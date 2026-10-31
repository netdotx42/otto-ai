"""Solana research, unsigned transaction construction (solders / Jupiter) and simulation."""

from __future__ import annotations

import base64
import struct
import time
from decimal import Decimal
from typing import Any

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import MessageV0, to_bytes_versioned
from solders.null_signer import NullSigner
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from app.chains.evm import RpcError
from app.chains.solana import TOKEN_2022_PROGRAM, TOKEN_PROGRAM
from app.core.chains import solana_allowed_programs, solana_cfg, token_by_address, token_by_symbol
from app.core.config import get_settings
from app.core.errors import APIError
from app.execution.digest import solana_digest
from app.execution.evm import to_base_units
from app.execution.types import (
    BalanceChange,
    BuiltStep,
    IntentKind,
    Quote,
    ResolvedToken,
    SimulationResult,
    TokenRef,
    TransactionIntent,
)

WSOL = "So11111111111111111111111111111111111111112"
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
SYSTEM_PROGRAM = Pubkey.from_string("11111111111111111111111111111111")
COMPUTE_BUDGET = Pubkey.from_string("ComputeBudget111111111111111111111111111111")
JUPITER_V6 = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"

# Token-program instruction tags that must never appear in an assisted transaction.
FORBIDDEN_TOKEN_IX = {4: "Approve (delegate)", 6: "SetAuthority", 13: "ApproveChecked (delegate)"}
FORBIDDEN_SYSTEM_IX = {1: "Assign", 8: "Allocate", 10: "AssignWithSeed"}


def ata(owner: str, mint: str, token_program: str = TOKEN_PROGRAM) -> str:
    return str(
        Pubkey.find_program_address(
            [bytes(Pubkey.from_string(owner)), bytes(Pubkey.from_string(token_program)), bytes(Pubkey.from_string(mint))],
            ATA_PROGRAM,
        )[0]
    )


async def resolve_token(sol, ref: TokenRef) -> ResolvedToken:
    if ref.symbol and not ref.address:
        t = token_by_symbol("solana", ref.symbol)
        if t is None:
            raise APIError(422, "unknown_token", f"{ref.symbol} is not allowlisted on Solana; provide its mint address.")
        if t.native:
            return ResolvedToken(symbol="SOL", address="native", decimals=9, allowlisted=True, native=True)
        addr = t.address
    else:
        addr = ref.address or ""
        if addr == WSOL:
            return ResolvedToken(symbol="SOL", address="native", decimals=9, allowlisted=True, native=True)
    acc, _slot = await sol.get_account_info(addr)
    if acc is None:
        raise APIError(422, "mint_not_found", f"Mint {addr} not found on this cluster.")
    owner = acc.get("owner")
    if owner not in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
        raise APIError(422, "not_a_mint", f"{addr} is not an SPL token mint.")
    info = acc["data"]["parsed"]["info"]
    known = token_by_address("solana", addr)
    from app.llm.sanitize import sanitize_untrusted

    return ResolvedToken(
        symbol=known.symbol if known else sanitize_untrusted(addr[:4] + "…", 16), address=addr,
        decimals=int(info["decimals"]), allowlisted=known is not None, stable=bool(known and known.stable),
        token_program=owner,
    )


async def token_balance(sol, wallet: str, tok: ResolvedToken) -> int:
    if tok.native:
        lamports, _ = await sol.get_balance(wallet)
        return lamports
    accs, _ = await sol.get_token_accounts_by_owner(wallet, mint=tok.address)
    return sum(int(a["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"]) for a in accs)


async def research(sol, http, intent: TransactionIntent) -> dict[str, Any]:
    if intent.kind == IntentKind.swap and not solana_cfg().get("jupiterAvailable"):
        raise APIError(
            422, "swap_unavailable_on_cluster",
            f"Jupiter routing is not available on {solana_cfg()['cluster']}. Solana swaps require mainnet "
            "(MAINNET mode + explicit enablement). Transfers work on devnet.",
        )
    tin = await resolve_token(sol, intent.input_token)
    tout = await resolve_token(sol, intent.output_token) if intent.output_token else None
    bal = await token_balance(sol, intent.wallet, tin)
    amount_in = (
        bal * int(intent.amount_percent * 100) // 10_000
        if intent.amount_percent is not None
        else to_base_units(intent.amount or Decimal(0), tin.decimals)
    )
    slot = await sol.get_slot()
    out: dict[str, Any] = {
        "chain": "solana", "cluster": solana_cfg()["cluster"], "slot": slot, "wallet": intent.wallet,
        "input_token": tin.model_dump(), "output_token": tout.model_dump() if tout else None,
        "amount_in": amount_in, "input_balance": bal, "notes": [],
        "evidence": [{"source": "solana-rpc", "method": "getBalance/getTokenAccountsByOwner", "slot": slot}],
    }
    if intent.kind == IntentKind.transfer:
        out["destination"] = intent.destination
        dacc, _ = await sol.get_account_info(intent.destination or "")
        out["destination_owner_program"] = dacc.get("owner") if dacc else None
        if dacc and dacc.get("owner") in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
            out["notes"].append("Destination is a token account, not a wallet. Transfers expect a wallet address.")
    else:
        assert tout is not None
        q = await jupiter_quote(http, tin, tout, amount_in, intent.slippage_bps)
        out["quote"] = q.model_dump()
        out["evidence"].append({"source": "jupiter", "method": "GET /swap/v1/quote", "fetched_at": q.fetched_at})
    return out


async def jupiter_quote(http, tin: ResolvedToken, tout: ResolvedToken, amount: int, slippage_bps: int) -> Quote:
    st = get_settings()
    base = st.jupiter_base()
    params = {
        "inputMint": WSOL if tin.native else tin.address,
        "outputMint": WSOL if tout.native else tout.address,
        "amount": str(amount), "slippageBps": str(slippage_bps),
        "restrictIntermediateTokens": "true", "onlyDirectRoutes": "false",
    }
    r = await http.get(f"{base}/swap/v1/quote", params=params, headers=st.jupiter_headers())
    if r.status_code != 200:
        raise APIError(502, "quote_failed", f"Jupiter quote failed ({r.status_code}).")
    data = r.json()
    now = time.time()
    impact = data.get("priceImpactPct")
    return Quote(
        provider="jupiter v6 (api.jup.ag)" if st.jupiter_api_key else "jupiter v6 (lite-api)",
        route=[step["swapInfo"].get("label", "?") for step in data.get("routePlan", [])],
        amount_in=int(data["inAmount"]), amount_out=int(data["outAmount"]),
        min_amount_out=int(data["otherAmountThreshold"]),
        price_impact_bps=int(Decimal(str(impact)) * 10_000) if impact is not None else None,
        fetched_at=now, expires_at=now + get_settings().quote_ttl_seconds, raw=data,
    )


def plan_steps(intent: TransactionIntent, r: dict[str, Any]) -> list[str]:
    return ["transfer"] if intent.kind == IntentKind.transfer else ["swap"]


def _compile(payer: str, ixs: list[Instruction], blockhash: str, lvbh: int) -> dict[str, Any]:
    payer_pk = Pubkey.from_string(payer)
    msg = MessageV0.try_compile(payer_pk, ixs, [], Hash.from_string(blockhash))
    tx = VersionedTransaction(msg, [NullSigner(payer_pk)])
    msg_b64 = base64.b64encode(to_bytes_versioned(msg)).decode()
    return {
        "transaction": base64.b64encode(bytes(tx)).decode(), "message_b64": msg_b64, "fee_payer": payer,
        "recent_blockhash": blockhash, "last_valid_block_height": lvbh,
    }


def _transfer_checked_ix(src: str, mint: str, dst: str, owner: str, amount: int, decimals: int, program: str) -> Instruction:
    data = bytes([12]) + struct.pack("<Q", amount) + bytes([decimals])
    return Instruction(
        Pubkey.from_string(program), data,
        [
            AccountMeta(Pubkey.from_string(src), False, True),
            AccountMeta(Pubkey.from_string(mint), False, False),
            AccountMeta(Pubkey.from_string(dst), False, True),
            AccountMeta(Pubkey.from_string(owner), True, False),
        ],
    )


def _create_ata_idempotent_ix(payer: str, owner: str, mint: str, program: str) -> Instruction:
    return Instruction(
        ATA_PROGRAM, bytes([1]),
        [
            AccountMeta(Pubkey.from_string(payer), True, True),
            AccountMeta(Pubkey.from_string(ata(owner, mint, program)), False, True),
            AccountMeta(Pubkey.from_string(owner), False, False),
            AccountMeta(Pubkey.from_string(mint), False, False),
            AccountMeta(SYSTEM_PROGRAM, False, False),
            AccountMeta(Pubkey.from_string(program), False, False),
        ],
    )


async def build_step(sol, http, kind: str, intent: TransactionIntent, r: dict[str, Any]) -> BuiltStep:
    wallet = intent.wallet
    tin = ResolvedToken(**r["input_token"])
    amount = int(r["amount_in"])
    if kind == "transfer":
        blockhash, lvbh, _ = await sol.get_latest_blockhash()
        dest = r["destination"]
        if tin.native:
            ixs = [transfer(TransferParams(from_pubkey=Pubkey.from_string(wallet), to_pubkey=Pubkey.from_string(dest), lamports=amount))]
        else:
            prog = tin.token_program or TOKEN_PROGRAM
            ixs = [
                _create_ata_idempotent_ix(wallet, dest, tin.address, prog),
                _transfer_checked_ix(ata(wallet, tin.address, prog), tin.address, ata(dest, tin.address, prog), wallet, amount, tin.decimals, prog),
            ]
        tx = _compile(wallet, ixs, blockhash, lvbh)
        decoded = decode_message(tx["message_b64"])
        return BuiltStep(kind="transfer", tx=tx, digest=solana_digest(tx["message_b64"]), decoded=decoded, expires_at=None)
    # swap via Jupiter
    q = Quote(**r["quote"])
    st = get_settings()
    resp = await http.post(
        f"{st.jupiter_base()}/swap/v1/swap",
        headers=st.jupiter_headers(),
        json={"quoteResponse": q.raw, "userPublicKey": wallet, "wrapAndUnwrapSol": True, "dynamicComputeUnitLimit": True},
    )
    if resp.status_code != 200:
        raise APIError(502, "swap_build_failed", f"Jupiter swap build failed ({resp.status_code}).")
    body = resp.json()
    raw_tx = VersionedTransaction.from_bytes(base64.b64decode(body["swapTransaction"]))
    msg_b64 = base64.b64encode(to_bytes_versioned(raw_tx.message)).decode()
    tx = {
        "transaction": body["swapTransaction"], "message_b64": msg_b64, "fee_payer": str(raw_tx.message.account_keys[0]),
        "recent_blockhash": str(raw_tx.message.recent_blockhash), "last_valid_block_height": int(body.get("lastValidBlockHeight", 0)),
    }
    decoded = decode_message(msg_b64)
    return BuiltStep(kind="swap", tx=tx, digest=solana_digest(msg_b64), decoded=decoded, quote=q, expires_at=q.expires_at)


def decode_message(message_b64: str) -> dict[str, Any]:
    """User-visible decoding of every top-level instruction."""
    from solders.message import from_bytes_versioned

    msg = from_bytes_versioned(base64.b64decode(message_b64))
    keys = [str(k) for k in msg.account_keys]
    allowed = solana_allowed_programs()
    ixs = []
    for ix in msg.instructions:
        pid = keys[ix.program_id_index]
        data = bytes(ix.data)
        entry: dict[str, Any] = {
            "program": pid, "program_name": allowed.get(pid, "UNKNOWN PROGRAM"), "allowlisted": pid in allowed,
            "accounts": [keys[i] if i < len(keys) else f"lookup[{i}]" for i in ix.accounts], "data_len": len(data),
        }
        if pid == "11111111111111111111111111111111" and len(data) >= 4:
            tag = struct.unpack_from("<I", data)[0]
            entry["instruction"] = {2: "Transfer"}.get(tag, FORBIDDEN_SYSTEM_IX.get(tag, f"system#{tag}"))
            if tag == 2:
                entry["lamports"] = struct.unpack_from("<Q", data, 4)[0]
            entry["forbidden"] = tag in FORBIDDEN_SYSTEM_IX
        elif pid in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM) and data:
            tag = data[0]
            entry["instruction"] = {3: "Transfer", 9: "CloseAccount", 12: "TransferChecked", 17: "SyncNative"}.get(tag, FORBIDDEN_TOKEN_IX.get(tag, f"token#{tag}"))
            if tag == 12:
                entry["amount"] = struct.unpack_from("<Q", data, 1)[0]
                entry["decimals"] = data[9]
            entry["forbidden"] = tag in FORBIDDEN_TOKEN_IX
        elif pid == "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL":
            entry["instruction"] = "CreateIdempotent" if data[:1] == b"\x01" else "Create"
        elif pid == "ComputeBudget111111111111111111111111111111":
            entry["instruction"] = {2: "SetComputeUnitLimit", 3: "SetComputeUnitPrice"}.get(data[0] if data else -1, "compute")
        elif pid == JUPITER_V6:
            entry["instruction"] = "Jupiter route"
        ixs.append(entry)
    lookups = [str(a.account_key) for a in getattr(msg, "address_table_lookups", [])]
    return {"fee_payer": keys[0], "instructions": ixs, "address_lookup_tables": lookups, "recent_blockhash": str(msg.recent_blockhash)}


async def simulate(sol, step: BuiltStep, r: dict[str, Any]) -> SimulationResult:
    wallet = r["wallet"]
    tin = ResolvedToken(**r["input_token"])
    tout = ResolvedToken(**r["output_token"]) if r.get("output_token") else None
    watch: list[tuple[str, ResolvedToken | None]] = [(wallet, None)]
    for tok in (tin, tout):
        if tok and not tok.native:
            watch.append((ata(wallet, tok.address, tok.token_program or TOKEN_PROGRAM), tok))
    pre_lamports, _ = await sol.get_balance(wallet)
    pre: dict[str, int] = {}
    for addr, _tok in watch[1:]:
        acc, _ = await sol.get_account_info(addr)
        pre[addr] = int(acc["data"]["parsed"]["info"]["tokenAmount"]["amount"]) if acc else 0
    limitations = [
        "simulateTransaction runs against the RPC node's current bank; state can change before inclusion.",
        "Signature verification is skipped during simulation (the wallet has not signed yet).",
        f"Transaction expires when block height exceeds {step.tx.get('last_valid_block_height')}.",
    ]
    try:
        res = await sol.simulate_transaction(step.tx["transaction"], [a for a, _ in watch])
    except RpcError as e:
        return SimulationResult(digest=step.digest, success=False, block_or_slot=None, gas_or_units=None, error=str(e), limitations=limitations, simulated_at=time.time())
    val = res["value"]
    slot = int(res["context"]["slot"])
    logs = val.get("logs") or []
    if val.get("err") is not None:
        return SimulationResult(
            digest=step.digest, success=False, block_or_slot=slot, gas_or_units=val.get("unitsConsumed"),
            error=f"program error: {val['err']}", logs=logs[-30:], limitations=limitations, simulated_at=time.time(),
        )
    accounts = val.get("accounts") or []
    changes: list[BalanceChange] = []
    post_wallet = accounts[0]["lamports"] if accounts and accounts[0] else pre_lamports
    changes.append(BalanceChange(token="native", symbol="SOL", delta=int(post_wallet) - int(pre_lamports), decimals=9))
    for (addr, tok), acc in zip(watch[1:], accounts[1:], strict=False):
        assert tok is not None
        post = 0
        if acc:
            try:
                post = int(acc["data"]["parsed"]["info"]["tokenAmount"]["amount"])
            except (KeyError, TypeError):
                post = pre.get(addr, 0)
        changes.append(BalanceChange(token=tok.address, symbol=tok.symbol, delta=post - pre.get(addr, 0), decimals=tok.decimals))
    return SimulationResult(
        digest=step.digest, success=True, block_or_slot=slot, gas_or_units=val.get("unitsConsumed"),
        fee_estimate={"base_fee_lamports": 5000, "compute_units": val.get("unitsConsumed")},
        balance_changes=changes, logs=logs[-30:], limitations=limitations, simulated_at=time.time(),
    )
