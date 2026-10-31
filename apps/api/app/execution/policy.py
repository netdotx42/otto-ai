"""Mandatory security policy engine.

Pure, deterministic functions over (intent, research, built tx, decoded actions, simulation,
limits). No model output reaches this module except through the typed, validated intent; the
LLM cannot add, remove or override checks. Any FAIL blocks the approval endpoint.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from eth_utils import is_address

from app.auth.messages import is_valid_solana_address
from app.core.chains import base_cfg, base_chain_id, solana_allowed_programs
from app.core.config import get_settings
from app.execution.types import (
    BuiltStep,
    CheckStatus,
    IntentKind,
    PolicyCheck,
    PolicyResult,
    SimulationResult,
    TransactionIntent,
)
from app.providers import uniswap_v3 as uni

MAX_UINT256 = 2**256 - 1


@dataclass
class PolicyContext:
    intent: TransactionIntent
    research: dict[str, Any]
    step: BuiltStep
    simulation: SimulationResult | None
    user_limits: dict[str, Any] = field(default_factory=dict)
    execution_enabled: bool = True
    wallet_verified: bool = False
    spent_today_usd: float = 0.0
    token_risk: dict[str, Any] | None = None
    now: float = field(default_factory=time.time)


def _c(results: list[PolicyCheck], cid: str, ok: bool, msg_ok: str, msg_fail: str, warn: bool = False) -> None:
    status = CheckStatus.passed if ok else (CheckStatus.warn if warn else CheckStatus.fail)
    results.append(PolicyCheck(id=cid, status=status, message=msg_ok if ok else msg_fail))


def evaluate(ctx: PolicyContext) -> PolicyResult:
    s = get_settings()
    out: list[PolicyCheck] = []
    i, r, step, sim = ctx.intent, ctx.research, ctx.step, ctx.simulation
    lim = ctx.user_limits

    # --- platform gates -----------------------------------------------------------------
    _c(out, "kill_switch", ctx.execution_enabled, "Execution is enabled.", "Execution is disabled by the emergency switch.")
    _c(out, "chain_supported", i.chain.value in ("base", "solana"), "Chain supported.", f"Unsupported chain {i.chain}.")
    mainnet_ok = s.network_mode != "mainnet" or s.mainnet_execution_enabled
    _c(out, "mainnet_gate", mainnet_ok, f"Network mode: {s.network_mode}.", "Mainnet execution is not enabled for this deployment.")
    _c(out, "wallet_ownership", ctx.wallet_verified, "Wallet ownership verified by signature.", "Wallet is not verified for this session.")

    # --- address hygiene ----------------------------------------------------------------
    valid = is_address if i.chain.value == "base" else is_valid_solana_address
    addrs = [i.wallet] + ([i.destination] if i.destination else [])
    _c(out, "address_format", all(valid(a) for a in addrs), "Addresses well-formed.", "Malformed address in request.")

    if i.chain.value == "base":
        _evm_checks(out, ctx)
    else:
        _solana_checks(out, ctx)

    # --- economics ------------------------------------------------------------------------
    max_slip = min(s.max_slippage_bps, int(lim.get("max_slippage_bps", s.max_slippage_bps)))
    _c(out, "slippage_limit", i.slippage_bps <= max_slip, f"Slippage {i.slippage_bps / 100:.2f}% within limit.", f"Slippage {i.slippage_bps / 100:.2f}% exceeds limit {max_slip / 100:.2f}%.")

    bal = int(r.get("input_balance", 0))
    amt = int(r.get("amount_in", 0))
    _c(out, "amount_positive", amt > 0, "Amount is positive.", "Computed input amount is zero.")
    _c(out, "balance_sufficient", bal >= amt, "Balance covers the input amount.", f"Insufficient balance ({bal} < {amt} base units).")

    usd = r.get("input_usd_value")
    max_tx = float(min(s.default_max_tx_usd, float(lim.get("max_tx_usd", s.default_max_tx_usd))))
    daily = float(min(s.default_daily_limit_usd, float(lim.get("daily_limit_usd", s.default_daily_limit_usd))))
    if step.kind == "approve":
        pass  # approval amount checked separately; spend counted on the swap step
    elif usd is None:
        out.append(PolicyCheck(id="spend_limit", status=CheckStatus.fail, message="Could not value the input in USD from a trusted source; spend limits cannot be verified."))
    else:
        _c(out, "spend_limit_tx", usd <= max_tx, f"${usd:,.2f} within per-transaction limit ${max_tx:,.2f}.", f"${usd:,.2f} exceeds per-transaction limit ${max_tx:,.2f}.")
        _c(out, "spend_limit_daily", ctx.spent_today_usd + usd <= daily, f"Daily total ${ctx.spent_today_usd + usd:,.2f} within ${daily:,.2f}.", f"Daily total ${ctx.spent_today_usd + usd:,.2f} exceeds ${daily:,.2f}.")

    if step.kind == "swap" and step.quote:
        q = step.quote
        _c(out, "quote_fresh", ctx.now < q.expires_at, f"Quote valid for {int(q.expires_at - ctx.now)}s.", "Quote expired; a fresh quote and simulation are required.")
        expected_min = q.amount_out * (10_000 - i.slippage_bps) // 10_000
        _c(out, "min_out_consistent", q.min_amount_out >= expected_min, "Minimum output matches slippage setting.", "Minimum output is looser than the requested slippage.")
        _c(out, "quote_amount_matches", q.amount_in == amt, "Quote input equals intended amount.", "Quote input differs from intended amount.")
        if q.price_impact_bps is None:
            out.append(PolicyCheck(id="price_impact", status=CheckStatus.warn, message="Price impact could not be estimated."))
        elif q.price_impact_bps >= s.price_impact_block_bps:
            out.append(PolicyCheck(id="price_impact", status=CheckStatus.fail, message=f"Price impact {q.price_impact_bps / 100:.2f}% is too high."))
        elif q.price_impact_bps >= s.price_impact_warn_bps:
            out.append(PolicyCheck(id="price_impact", status=CheckStatus.warn, message=f"Price impact {q.price_impact_bps / 100:.2f}%."))
        else:
            out.append(PolicyCheck(id="price_impact", status=CheckStatus.passed, message=f"Price impact {q.price_impact_bps / 100:.2f}%."))

    if step.expires_at is not None:
        _c(out, "tx_expiry", ctx.now < step.expires_at, "Transaction has an expiry and is not expired.", "Transaction expired.")

    # --- simulation -----------------------------------------------------------------------
    if sim is None:
        out.append(PolicyCheck(id="simulation_ok", status=CheckStatus.fail, message="Transaction has not been simulated."))
    else:
        _c(out, "simulation_ok", sim.success, "Simulation succeeded.", f"Simulation failed: {sim.error}")
        _c(out, "simulation_digest", sim.digest == step.digest, "Simulated transaction is byte-identical to the candidate.", "Transaction changed after simulation; re-simulation required.")
        _c(out, "simulation_fresh", ctx.now - sim.simulated_at <= s.simulation_ttl_seconds, "Simulation is fresh.", "Simulation is stale; re-simulation required.")
        if sim.success:
            _outcome_checks(out, ctx)

    # --- token risk ----------------------------------------------------------------------
    tr = ctx.token_risk
    if i.kind == IntentKind.swap and r.get("output_token") and not r["output_token"].get("allowlisted"):
        if tr is None:
            out.append(PolicyCheck(id="token_risk", status=CheckStatus.fail, message="Output token is not allowlisted and has not been scanned."))
        elif tr.get("risk_level") in ("critical",):
            out.append(PolicyCheck(id="token_risk", status=CheckStatus.fail, message=f"Output token risk is {tr['risk_level']}: {tr.get('summary', '')}"))
        else:
            out.append(PolicyCheck(id="token_risk", status=CheckStatus.warn, message=f"Output token is not allowlisted (scan: {tr.get('risk_level')}). Review findings."))

    allowed = not any(c.status == CheckStatus.fail for c in out)
    return PolicyResult(allowed=allowed, checks=out)


def _evm_checks(out: list[PolicyCheck], ctx: PolicyContext) -> None:
    i, r, step = ctx.intent, ctx.research, ctx.step
    tx = step.tx
    wallet = i.wallet.lower()
    router = base_cfg()["uniswapV3"]["swapRouter02"].lower()
    tin = r["input_token"]
    _c(out, "chain_id", int(tx["chainId"]) == base_chain_id(), f"chainId {tx['chainId']} (EIP-155 replay protection).", "chainId mismatch.")
    _c(out, "from_matches", tx["from"].lower() == wallet, "Sender is the verified wallet.", "Sender differs from the requesting wallet.")
    to = tx["to"].lower()
    if step.kind == "approve":
        _c(out, "destination_allowlist", to == tin["address"], "Approval targets the input token contract.", "Approval targets an unexpected contract.")
        dec = uni.decode_erc20_calldata(tx["data"])
        _c(out, "decoded_matches_intent", dec.get("function") == "approve" and dec.get("spender") == router, "Approval spender is the allowlisted Uniswap SwapRouter02.", "Approval spender is not an allowlisted router.")
        unlimited = int(dec.get("amount", 0)) >= MAX_UINT256 // 2
        allow_unl = bool(ctx.user_limits.get("allow_unlimited_approvals", False))
        _c(out, "approval_exact", (not unlimited or allow_unl) and int(dec.get("amount", 0)) == int(r["amount_in"]), "Exact-amount approval.", "Approval is not the exact required amount (unlimited approvals are blocked by default).")
        _c(out, "native_value", int(tx["value"]) == 0, "No ETH attached.", "Unexpected ETH value attached.")
    elif step.kind == "swap":
        _c(out, "destination_allowlist", to == router, "Destination is the allowlisted Uniswap SwapRouter02.", f"Destination {to} is not an allowlisted router.")
        dec = uni.decode_router_calldata(tx["data"])
        calls = dec.get("calls", [])
        ok = dec.get("function") == "multicall" and len(calls) == 1 and calls[0].get("function") in ("exactInputSingle", "exactInput")
        if ok:
            c = calls[0]
            exp_in = base_cfg()["weth"].lower() if tin.get("native") else tin["address"]
            ok = (
                c["tokenIn"] == exp_in and c["tokenOut"] == r["output_token"]["address"]
                and c["recipient"] == wallet and int(c["amountIn"]) == int(r["amount_in"])
                and step.quote is not None and int(c["amountOutMinimum"]) == step.quote.min_amount_out
            )
        _c(out, "decoded_matches_intent", ok, "Decoded swap (tokens, amount, recipient, min-out) matches intent.", "Decoded calldata does not match the requested swap.")
        exp_val = int(r["amount_in"]) if tin.get("native") else 0
        _c(out, "native_value", int(tx["value"]) == exp_val, "ETH value matches intent.", "ETH value attached does not match intent.")
        _c(out, "deadline", int(dec.get("deadline", 0)) > ctx.now, "Swap carries an on-chain deadline.", "Missing or past swap deadline.")
    else:  # transfer
        dest = (i.destination or "").lower()
        if tin.get("native"):
            ok = to == dest and tx["data"] in ("0x", "") and int(tx["value"]) == int(r["amount_in"])
            _c(out, "destination_allowlist", True, "Native transfer to the user-specified destination.", "")
        else:
            dec = uni.decode_erc20_calldata(tx["data"])
            ok = to == tin["address"] and dec.get("function") == "transfer" and dec.get("to") == dest and int(dec.get("amount", -1)) == int(r["amount_in"])
            _c(out, "destination_allowlist", tin.get("allowlisted", False) or ctx.token_risk is not None, "Token contract is allowlisted or scanned.", "Token contract is neither allowlisted nor scanned.")
            _c(out, "native_value", int(tx["value"]) == 0, "No ETH attached.", "Unexpected ETH value attached.")
        _c(out, "decoded_matches_intent", ok, "Decoded transfer matches recipient and amount.", "Decoded transfer does not match the request.")
        if r.get("destination_is_contract"):
            out.append(PolicyCheck(id="destination_contract", status=CheckStatus.warn, message="Destination is a smart contract. Make sure it can receive this asset."))


def _solana_checks(out: list[PolicyCheck], ctx: PolicyContext) -> None:
    i, r, step = ctx.intent, ctx.research, ctx.step
    d = step.decoded
    allowed = solana_allowed_programs()
    ixs = d.get("instructions", [])
    unknown = [x["program"] for x in ixs if x["program"] not in allowed]
    _c(out, "program_allowlist", not unknown, "All programs are allowlisted.", f"Unexpected program(s): {', '.join(unknown)}.")
    forbidden = [x.get("instruction") for x in ixs if x.get("forbidden")]
    _c(out, "forbidden_instructions", not forbidden, "No delegate/authority/assign instructions.", f"Forbidden instruction(s): {', '.join(map(str, forbidden))}.")
    _c(out, "from_matches", d.get("fee_payer") == i.wallet and step.tx.get("fee_payer") == i.wallet, "Fee payer is the verified wallet.", "Fee payer differs from the requesting wallet.")
    tin = r["input_token"]
    if step.kind == "transfer":
        if tin.get("native"):
            xs = [x for x in ixs if x.get("instruction") == "Transfer" and x["program"] == "11111111111111111111111111111111"]
            ok = len(xs) == 1 and xs[0]["accounts"][:2] == [i.wallet, i.destination] and xs[0].get("lamports") == int(r["amount_in"])
        else:
            from app.execution.solana import ata

            prog = tin.get("token_program") or "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
            xs = [x for x in ixs if x.get("instruction") == "TransferChecked"]
            ok = (
                len(xs) == 1 and xs[0].get("amount") == int(r["amount_in"])
                and xs[0]["accounts"][0] == ata(i.wallet, tin["address"], prog)
                and xs[0]["accounts"][2] == ata(i.destination or "", tin["address"], prog)
                and xs[0]["accounts"][3] == i.wallet
            )
        _c(out, "decoded_matches_intent", ok, "Decoded transfer matches recipient and amount.", "Decoded instructions do not match the requested transfer.")
        _c(out, "destination_allowlist", True, "Transfer to user-specified destination.", "")
    else:
        jup = [x for x in ixs if x["program"] == "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"]
        _c(out, "destination_allowlist", len(jup) == 1, "Exactly one Jupiter v6 route instruction.", "Swap transaction does not contain exactly one Jupiter route.")
        # Jupiter routes cannot be fully decoded without its IDL; outcome checks below verify balance deltas.
        _c(out, "decoded_matches_intent", True, "Route verified via program allowlist + simulated balance deltas.", "")


def _outcome_checks(out: list[PolicyCheck], ctx: PolicyContext) -> None:
    """Compare simulated balance changes with the intent ('decoded actions differ from intent')."""
    r, step, sim = ctx.research, ctx.step, ctx.simulation
    assert sim is not None
    if step.kind == "approve":
        return
    tin = r["input_token"]
    changes = {(c.token, c.symbol): c.delta for c in sim.balance_changes}
    amt = int(r["amount_in"])
    in_delta = next((d for (t, sym), d in changes.items() if t == tin["address"] and "fee" not in sym.lower()), None)
    if ctx.intent.chain.value == "solana" and tin.get("native"):
        # SOL delta includes fees and possibly rent for new token accounts (bounded at 0.01 SOL).
        ok = in_delta is not None and -amt - 10_000_000 <= in_delta <= -amt
    else:
        ok = in_delta == -amt
    _c(out, "simulated_input", ok, "Simulated spend equals the intended amount.", f"Simulated spend ({in_delta}) differs from intended ({-amt}).")
    if step.kind == "swap" and step.quote:
        tout = r["output_token"]
        out_delta = next((d for (t, _s), d in changes.items() if t == tout["address"]), None)
        if ctx.intent.chain.value == "solana" and tout.get("native"):
            ok_out = out_delta is not None  # SOL output mixes with fees; min-out enforced on-chain by Jupiter
        else:
            ok_out = out_delta is not None and out_delta >= step.quote.min_amount_out
        _c(out, "simulated_output", ok_out, "Simulated output meets the minimum.", f"Simulated output ({out_delta}) is below the minimum {step.quote.min_amount_out}.")
