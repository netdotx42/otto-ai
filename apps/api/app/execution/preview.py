"""Human-readable execution preview, derived only from decoded tx + simulation + policy."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.core.chains import base_cfg, solana_cfg
from app.execution.types import BuiltStep, CheckStatus, PolicyResult, SimulationResult, TransactionIntent


def fmt(amount: int, decimals: int) -> str:
    v = Decimal(amount) / (Decimal(10) ** decimals)
    s = f"{v:,.{min(decimals, 8)}f}".rstrip("0").rstrip(".")
    return s or "0"


def risk_level(policy: PolicyResult, token_risk: dict[str, Any] | None) -> str:
    if not policy.allowed:
        return "blocked"
    warns = [c for c in policy.checks if c.status == CheckStatus.warn]
    tr = (token_risk or {}).get("risk_level")
    if tr in ("high",) or len(warns) >= 3:
        return "high"
    if warns or tr == "medium":
        return "medium"
    return "low"


def build_preview(
    intent: TransactionIntent, r: dict[str, Any], step: BuiltStep, sim: SimulationResult | None,
    policy: PolicyResult, step_index: int, total_steps: int, token_risk: dict[str, Any] | None,
) -> dict[str, Any]:
    tin, tout = r["input_token"], r.get("output_token")
    network = base_cfg()["name"] if intent.chain.value == "base" else f"Solana {solana_cfg()['cluster']}"
    amt = int(r["amount_in"])
    p: dict[str, Any] = {
        "network": network, "chain": intent.chain.value, "type": step.kind,
        "step": {"index": step_index + 1, "of": total_steps, "kind": step.kind},
        "digest": step.digest, "sends": [], "receives": [], "approvals": [],
        "slippage": f"{intent.slippage_bps / 100:.2f}%",
    }
    if step.kind == "approve":
        p["action"] = f"Approve exactly {fmt(amt, tin['decimals'])} {tin['symbol']} for the Uniswap router"
        p["destination"] = {"address": step.tx["to"], "label": f"{tin['symbol']} token contract"}
        p["approvals"].append({
            "token": tin["address"], "symbol": tin["symbol"], "spender": step.decoded.get("spender"),
            "spender_label": "Uniswap V3 SwapRouter02 (allowlisted)", "amount": fmt(amt, tin["decimals"]), "unlimited": False,
        })
        p["next_steps"] = ["After this approval confirms, the swap is re-quoted, re-simulated and shown for confirmation."]
    elif step.kind == "swap":
        q = step.quote
        assert q and tout
        p["action"] = f"Swap {fmt(amt, tin['decimals'])} {tin['symbol']} → {tout['symbol']}"
        p["sends"].append({"symbol": tin["symbol"], "token": tin["address"], "amount": fmt(amt, tin["decimals"])})
        p["max_input"] = f"{fmt(amt, tin['decimals'])} {tin['symbol']}"
        p["receives"].append({
            "symbol": tout["symbol"], "token": tout["address"], "expected": fmt(q.amount_out, tout["decimals"]),
            "minimum": fmt(q.min_amount_out, tout["decimals"]),
        })
        p["min_output"] = f"{fmt(q.min_amount_out, tout['decimals'])} {tout['symbol']}"
        p["destination"] = (
            {"address": step.tx["to"], "label": "Uniswap V3 SwapRouter02"} if intent.chain.value == "base"
            else {"address": "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4", "label": "Jupiter Aggregator v6"}
        )
        p["quote"] = {
            "provider": q.provider, "route": q.route,
            "price_impact": None if q.price_impact_bps is None else f"{q.price_impact_bps / 100:.2f}%",
            "expires_at": q.expires_at, "fetched_at": q.fetched_at,
        }
    else:
        p["action"] = f"Transfer {fmt(amt, tin['decimals'])} {tin['symbol']} to {intent.destination}"
        p["sends"].append({"symbol": tin["symbol"], "token": tin["address"], "amount": fmt(amt, tin["decimals"])})
        p["max_input"] = f"{fmt(amt, tin['decimals'])} {tin['symbol']}"
        p["destination"] = {"address": intent.destination, "label": "Recipient (user-specified)"}
    if sim:
        p["simulation"] = {
            "status": "success" if sim.success else "failed", "block_or_slot": sim.block_or_slot, "error": sim.error,
            "gas_or_units": sim.gas_or_units, "limitations": sim.limitations,
            "balance_changes": [
                {"symbol": c.symbol, "token": c.token, "delta": fmt(c.delta, c.decimals) if c.delta >= 0 else "-" + fmt(-c.delta, c.decimals)}
                for c in sim.balance_changes
            ],
        }
        fe = sim.fee_estimate
        if intent.chain.value == "base" and fe:
            p["fees"] = {
                "estimated_total": f"{fmt(int(fe.get('total_estimate_wei', 0)), 18)} ETH",
                "gas": fe.get("gas"), "l1_data_fee_upper_bound": f"{fmt(int(fe.get('l1_data_fee_upper_bound_wei', 0)), 18)} ETH",
            }
        elif fe:
            p["fees"] = {"base_fee": f"{fmt(int(fe.get('base_fee_lamports', 5000)), 9)} SOL", "compute_units": fe.get("compute_units")}
    else:
        p["simulation"] = {"status": "not_run"}
    p["risk"] = {
        "level": risk_level(policy, token_risk),
        "warnings": [c.message for c in policy.checks if c.status != CheckStatus.passed],
        "token_scan": token_risk,
    }
    p["policy"] = policy.model_dump()
    p["usd_value"] = r.get("input_usd_value")
    p["decoded"] = step.decoded
    p["next"] = (
        "Review every field, then confirm to open your wallet for signature."
        if policy.allowed else "Blocked by the security policy. Nothing can be signed."
    )
    return p
