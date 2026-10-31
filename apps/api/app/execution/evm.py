"""Base (EVM) research, unsigned transaction construction and simulation."""

from __future__ import annotations

import time
from decimal import ROUND_DOWN, Decimal
from typing import Any

from eth_abi import decode

from app.chains.evm import RpcError, decode_revert, encode_call
from app.core.chains import base_cfg, base_chain_id, token_by_address, token_by_symbol
from app.core.errors import APIError
from app.execution.digest import evm_digest
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
from app.providers import uniswap_v3 as uni

GAS_PRICE_ORACLE = "0x420000000000000000000000000000000000000F"
SWAP_DEADLINE_SECONDS = 180


async def resolve_token(evm, ref: TokenRef) -> ResolvedToken:
    if ref.symbol and not ref.address:
        t = token_by_symbol("base", ref.symbol)
        if t is None:
            raise APIError(422, "unknown_token", f"{ref.symbol} is not allowlisted on Base; provide its address.")
        if t.native:
            return ResolvedToken(symbol="ETH", address="native", decimals=18, allowlisted=True, native=True)
        return ResolvedToken(symbol=t.symbol, address=t.address.lower(), decimals=t.decimals, allowlisted=True, stable=t.stable)
    addr = (ref.address or "").lower()
    known = token_by_address("base", addr)
    if known:
        return ResolvedToken(symbol=known.symbol, address=addr, decimals=known.decimals, allowlisted=True, stable=known.stable)
    code = await evm.get_code(addr)
    if not code or code == "0x":
        raise APIError(422, "not_a_contract", f"{addr} has no contract code on Base.")
    meta = await evm.erc20_metadata(addr)
    if meta.get("decimals") is None:
        raise APIError(422, "not_erc20", f"{addr} does not implement ERC-20 decimals().")
    from app.llm.sanitize import sanitize_untrusted

    return ResolvedToken(
        symbol=sanitize_untrusted(str(meta.get("symbol") or "UNKNOWN"), 16), address=addr,
        decimals=int(meta["decimals"]), allowlisted=False,
    )


def to_base_units(amount: Decimal, decimals: int) -> int:
    q = amount * (Decimal(10) ** decimals)
    if q != q.to_integral_value(rounding=ROUND_DOWN):
        raise APIError(422, "too_many_decimals", f"Amount has more precision than the token's {decimals} decimals.")
    return int(q)


async def balance_of(evm, wallet: str, tok: ResolvedToken) -> int:
    return await evm.get_balance(wallet) if tok.native else await evm.erc20_balance(tok.address, wallet)


async def research(evm, intent: TransactionIntent) -> dict[str, Any]:
    wallet = intent.wallet.lower()
    tin = await resolve_token(evm, intent.input_token)
    tout = await resolve_token(evm, intent.output_token) if intent.output_token else None
    if tout and tout.native:
        # SwapRouter02 output stays wrapped unless an unwrap call is added; keep it explicit.
        weth = token_by_symbol("base", "WETH")
        assert weth
        tout = ResolvedToken(symbol="WETH", address=weth.address.lower(), decimals=18, allowlisted=True)
    bal = await balance_of(evm, wallet, tin)
    if intent.amount_percent is not None:
        amount_in = bal * int(intent.amount_percent * 100) // 10_000
    else:
        amount_in = to_base_units(intent.amount or Decimal(0), tin.decimals)
    block = await evm.block_number()
    out: dict[str, Any] = {
        "chain": "base", "chain_id": base_chain_id(), "block": block, "wallet": wallet,
        "input_token": tin.model_dump(), "output_token": tout.model_dump() if tout else None,
        "amount_in": amount_in, "input_balance": bal, "notes": [],
        "evidence": [{"source": "base-rpc", "method": "eth_getBalance/balanceOf", "block": block}],
    }
    if intent.kind == IntentKind.transfer:
        dest = (intent.destination or "").lower()
        dcode = await evm.get_code(dest)
        out["destination"] = dest
        out["destination_is_contract"] = bool(dcode and dcode != "0x")
    else:
        assert tout is not None
        token_in_addr = base_cfg()["weth"].lower() if tin.native else tin.address
        q = await uni.quote(evm, token_in_addr, tout.address, amount_in, intent.slippage_bps)
        out["quote"] = q.model_dump()
        out["router"] = uni.router()
        if not tin.native:
            out["allowance"] = await evm.erc20_allowance(tin.address, wallet, uni.router())
        out["evidence"].append({"source": "uniswap-v3-quoter", "method": "eth_call quote", "block": block, "address": uni.quoter()})
    return out


def plan_steps(intent: TransactionIntent, r: dict[str, Any]) -> list[str]:
    if intent.kind == IntentKind.transfer:
        return ["transfer"]
    if r["input_token"]["native"] or r.get("allowance", 0) >= r["amount_in"]:
        return ["swap"]
    return ["approve", "swap"]


def _tx(wallet: str, to: str, data: str, value: int) -> dict[str, Any]:
    return {"chainId": base_chain_id(), "from": wallet, "to": to, "data": data, "value": str(value)}


def build_step(kind: str, intent: TransactionIntent, r: dict[str, Any], quote: Quote | None = None) -> BuiltStep:
    wallet = r["wallet"]
    tin = ResolvedToken(**r["input_token"])
    amount_in = int(r["amount_in"])
    if kind == "approve":
        tx = _tx(wallet, tin.address, uni.approve_calldata(uni.router(), amount_in), 0)
        decoded = {"type": "approve", "token": tin.address, **uni.decode_erc20_calldata(tx["data"])}
        return BuiltStep(kind="approve", tx=tx, digest=evm_digest(tx), decoded=decoded)
    if kind == "transfer":
        dest = r["destination"]
        if tin.native:
            tx = _tx(wallet, dest, "0x", amount_in)
            decoded = {"type": "native_transfer", "to": dest, "amount": amount_in}
        else:
            tx = _tx(wallet, tin.address, uni.transfer_calldata(dest, amount_in), 0)
            decoded = {"type": "erc20_transfer", "token": tin.address, **uni.decode_erc20_calldata(tx["data"])}
        return BuiltStep(kind="transfer", tx=tx, digest=evm_digest(tx), decoded=decoded)
    # swap
    q = quote or Quote(**r["quote"])
    deadline = int(time.time()) + SWAP_DEADLINE_SECONDS
    data = uni.build_swap_calldata(q, wallet, deadline)
    tx = _tx(wallet, uni.router(), data, amount_in if tin.native else 0)
    decoded = {"type": "uniswap_v3_swap", "router": uni.router(), **uni.decode_router_calldata(data)}
    return BuiltStep(kind="swap", tx=tx, digest=evm_digest(tx), decoded=decoded, quote=q, expires_at=min(q.expires_at, deadline))


async def simulate(evm, step: BuiltStep, r: dict[str, Any]) -> SimulationResult:
    tx = step.tx
    call = {"from": tx["from"], "to": tx["to"], "data": tx["data"], "value": hex(int(tx["value"]))}
    limitations = [
        "Simulated with eth_call against the latest block; state can change before inclusion.",
        "Gas is an eth_estimateGas estimate; the wallet may adjust it.",
    ]
    block = await evm.block_number()
    try:
        ret = await evm.call(call, hex(block))
    except RpcError as e:
        return SimulationResult(
            digest=step.digest, success=False, block_or_slot=block, gas_or_units=None,
            error=decode_revert(e.data if isinstance(e.data, str) else None) + f" ({e})", limitations=limitations,
            simulated_at=time.time(),
        )
    try:
        gas = await evm.estimate_gas(call)
    except RpcError as e:
        return SimulationResult(
            digest=step.digest, success=False, block_or_slot=block, gas_or_units=None,
            error=f"gas estimation failed: {e}", limitations=limitations, simulated_at=time.time(),
        )
    blk = await evm.get_block("latest")
    base_fee = int(blk.get("baseFeePerGas", "0x0"), 16)
    prio = await evm.max_priority_fee()
    fee: dict[str, Any] = {
        "gas": gas, "base_fee_wei": base_fee, "priority_fee_wei": prio,
        "l2_execution_fee_wei": gas * (base_fee + prio), "max_fee_per_gas_wei": 2 * base_fee + prio,
    }
    try:
        size = len(bytes.fromhex(tx["data"][2:])) + 120
        out = await evm.call({"to": GAS_PRICE_ORACLE, "data": encode_call("getL1FeeUpperBound(uint256)", ["uint256"], [size])})
        fee["l1_data_fee_upper_bound_wei"] = int(out, 16)
    except (RpcError, ValueError):
        limitations.append("Base L1 data fee could not be estimated (GasPriceOracle unavailable).")
    fee["total_estimate_wei"] = fee["l2_execution_fee_wei"] + fee.get("l1_data_fee_upper_bound_wei", 0)

    changes: list[BalanceChange] = []
    tin = r["input_token"]
    if step.kind == "swap":
        try:
            (results,) = decode(["bytes[]"], bytes.fromhex(ret[2:]))
            (amount_out,) = decode(["uint256"], results[0])
        except Exception:  # noqa: BLE001
            return SimulationResult(
                digest=step.digest, success=False, block_or_slot=block, gas_or_units=gas,
                error="Could not decode router return data; refusing to estimate outcome.",
                limitations=limitations, simulated_at=time.time(),
            )
        tout = r["output_token"]
        changes = [
            BalanceChange(token=tin["address"], symbol=tin["symbol"], delta=-int(r["amount_in"]), decimals=tin["decimals"]),
            BalanceChange(token=tout["address"], symbol=tout["symbol"], delta=int(amount_out), decimals=tout["decimals"]),
        ]
    elif step.kind == "transfer":
        changes = [BalanceChange(token=tin["address"], symbol=tin["symbol"], delta=-int(r["amount_in"]), decimals=tin["decimals"])]
    # Network fee (estimate) is always paid in ETH.
    changes.append(BalanceChange(token="native", symbol="ETH", delta=-int(fee["total_estimate_wei"]), decimals=18))
    return SimulationResult(
        digest=step.digest, success=True, block_or_slot=block, gas_or_units=gas, fee_estimate=fee,
        balance_changes=changes, return_data=ret[:2 + 512], limitations=limitations, simulated_at=time.time(),
    )
