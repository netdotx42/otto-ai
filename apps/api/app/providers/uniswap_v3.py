"""Uniswap V3 on Base: on-chain quotes (QuoterV2 via eth_call) and SwapRouter02 calldata.

This is the only Base swap adapter on the allowlist. The router/quoter addresses come from
packages/shared/chains.json — never from user input or model output.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from eth_abi import decode, encode

from app.chains.evm import RpcError, encode_call, selector
from app.core.chains import base_cfg
from app.core.config import get_settings
from app.core.errors import APIError
from app.execution.types import Quote

FEE_TIERS = (100, 500, 3000, 10000)
ADDRESS_THIS = "0x0000000000000000000000000000000000000002"

SIG_QUOTE_SINGLE = "quoteExactInputSingle((address,address,uint256,uint24,uint160))"
SIG_QUOTE_PATH = "quoteExactInput(bytes,uint256)"
SIG_EXACT_SINGLE = "exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))"
SIG_EXACT_PATH = "exactInput((bytes,address,uint256,uint256))"
SIG_MULTICALL_DEADLINE = "multicall(uint256,bytes[])"
SIG_APPROVE = "approve(address,uint256)"
SIG_TRANSFER = "transfer(address,uint256)"

SELECTORS = {
    "0x" + selector(SIG_EXACT_SINGLE).hex(): "exactInputSingle",
    "0x" + selector(SIG_EXACT_PATH).hex(): "exactInput",
    "0x" + selector(SIG_MULTICALL_DEADLINE).hex(): "multicall",
    "0x" + selector(SIG_APPROVE).hex(): "approve",
    "0x" + selector(SIG_TRANSFER).hex(): "transfer",
}


def router() -> str:
    return base_cfg()["uniswapV3"]["swapRouter02"].lower()


def quoter() -> str:
    return base_cfg()["uniswapV3"]["quoterV2"].lower()


def encode_path(tokens: list[str], fees: list[int]) -> bytes:
    out = b""
    for i, t in enumerate(tokens):
        out += bytes.fromhex(t[2:])
        if i < len(fees):
            out += fees[i].to_bytes(3, "big")
    return out


def decode_path(path: bytes) -> tuple[list[str], list[int]]:
    tokens, fees = [], []
    i = 0
    while True:
        tokens.append("0x" + path[i : i + 20].hex())
        i += 20
        if i >= len(path):
            break
        fees.append(int.from_bytes(path[i : i + 3], "big"))
        i += 3
    return tokens, fees


@dataclass
class Route:
    tokens: list[str]
    fees: list[int]
    amount_out: int
    gas_estimate: int


async def _quote_single(evm, token_in: str, token_out: str, amount: int, fee: int) -> Route | None:
    data = encode_call(SIG_QUOTE_SINGLE, ["(address,address,uint256,uint24,uint160)"], [(token_in, token_out, amount, fee, 0)])
    try:
        out = await evm.call({"to": quoter(), "data": data})
    except RpcError:
        return None  # no pool / no liquidity at this tier
    amount_out, _sqrt, _ticks, gas = decode(["uint256", "uint160", "uint32", "uint256"], bytes.fromhex(out[2:]))
    return Route([token_in, token_out], [fee], amount_out, gas)


async def _quote_path(evm, tokens: list[str], fees: list[int], amount: int) -> Route | None:
    data = encode_call(SIG_QUOTE_PATH, ["bytes", "uint256"], [encode_path(tokens, fees), amount])
    try:
        out = await evm.call({"to": quoter(), "data": data})
    except RpcError:
        return None
    amount_out, _s, _t, gas = decode(["uint256", "uint160[]", "uint32[]", "uint256"], bytes.fromhex(out[2:]))
    return Route(tokens, fees, amount_out, gas)


async def best_route(evm, token_in: str, token_out: str, amount: int) -> Route | None:
    weth = base_cfg()["weth"].lower()
    candidates: list[Route] = []
    for fee in FEE_TIERS:
        r = await _quote_single(evm, token_in, token_out, amount, fee)
        if r and r.amount_out > 0:
            candidates.append(r)
    if weth not in (token_in, token_out):
        for f1 in (500, 3000):
            for f2 in (500, 3000):
                r = await _quote_path(evm, [token_in, weth, token_out], [f1, f2], amount)
                if r and r.amount_out > 0:
                    candidates.append(r)
    return max(candidates, key=lambda r: r.amount_out) if candidates else None


async def quote(evm, token_in: str, token_out: str, amount_in: int, slippage_bps: int) -> Quote:
    route = await best_route(evm, token_in, token_out, amount_in)
    if route is None:
        raise APIError(422, "no_route", "No Uniswap V3 liquidity found for this pair on Base.")
    # Price impact estimate: compare execution rate to the rate for a 1/1000-size probe on the same route.
    probe_in = max(1, amount_in // 1000)
    if len(route.tokens) == 2:
        probe = await _quote_single(evm, token_in, token_out, probe_in, route.fees[0])
    else:
        probe = await _quote_path(evm, route.tokens, route.fees, probe_in)
    impact = None
    if probe and probe.amount_out > 0:
        spot = probe.amount_out / probe_in
        execd = route.amount_out / amount_in
        impact = max(0, int(round((1 - execd / spot) * 10_000)))
    now = time.time()
    min_out = route.amount_out * (10_000 - slippage_bps) // 10_000
    return Quote(
        provider="uniswap-v3 (QuoterV2 eth_call)",
        route=[f"{t}" for t in route.tokens],
        amount_in=amount_in,
        amount_out=route.amount_out,
        min_amount_out=min_out,
        price_impact_bps=impact,
        fetched_at=now,
        expires_at=now + get_settings().quote_ttl_seconds,
        raw={"fees": route.fees, "gas_estimate": route.gas_estimate, "quoter": quoter()},
    )


def build_swap_calldata(q: Quote, recipient: str, deadline: int) -> str:
    fees = q.raw["fees"]
    if len(q.route) == 2:
        inner = encode_call(
            SIG_EXACT_SINGLE,
            ["(address,address,uint24,address,uint256,uint256,uint160)"],
            [(q.route[0], q.route[1], fees[0], recipient, q.amount_in, q.min_amount_out, 0)],
        )
    else:
        inner = encode_call(
            SIG_EXACT_PATH, ["(bytes,address,uint256,uint256)"],
            [(encode_path(q.route, fees), recipient, q.amount_in, q.min_amount_out)],
        )
    # multicall(deadline, [...]) gives the transaction an on-chain expiry.
    return encode_call(SIG_MULTICALL_DEADLINE, ["uint256", "bytes[]"], [deadline, [bytes.fromhex(inner[2:])]])


def decode_router_calldata(data: str) -> dict:
    """Decode SwapRouter02 calldata produced above. Anything else is reported as unknown."""
    raw = bytes.fromhex(data[2:])
    sel = "0x" + raw[:4].hex()
    if SELECTORS.get(sel) != "multicall":
        return {"function": SELECTORS.get(sel, "unknown"), "selector": sel}
    deadline, calls = decode(["uint256", "bytes[]"], raw[4:])
    inner_decoded = []
    for c in calls:
        isel = "0x" + c[:4].hex()
        name = SELECTORS.get(isel, "unknown")
        if name == "exactInputSingle":
            (p,) = decode(["(address,address,uint24,address,uint256,uint256,uint160)"], c[4:])
            inner_decoded.append({
                "function": name, "tokenIn": p[0].lower(), "tokenOut": p[1].lower(), "fee": p[2],
                "recipient": p[3].lower(), "amountIn": p[4], "amountOutMinimum": p[5], "sqrtPriceLimitX96": p[6],
            })
        elif name == "exactInput":
            (p,) = decode(["(bytes,address,uint256,uint256)"], c[4:])
            toks, fees = decode_path(p[0])
            inner_decoded.append({
                "function": name, "tokenIn": toks[0].lower(), "tokenOut": toks[-1].lower(), "path": toks, "fees": fees,
                "recipient": p[1].lower(), "amountIn": p[2], "amountOutMinimum": p[3],
            })
        else:
            inner_decoded.append({"function": "unknown", "selector": isel})
    return {"function": "multicall", "deadline": deadline, "calls": inner_decoded}


def decode_erc20_calldata(data: str) -> dict:
    raw = bytes.fromhex(data[2:])
    sel = "0x" + raw[:4].hex()
    name = SELECTORS.get(sel)
    if name == "approve":
        spender, amount = decode(["address", "uint256"], raw[4:])
        return {"function": "approve", "spender": spender.lower(), "amount": amount}
    if name == "transfer":
        to, amount = decode(["address", "uint256"], raw[4:])
        return {"function": "transfer", "to": to.lower(), "amount": amount}
    return {"function": "unknown", "selector": sel}


def approve_calldata(spender: str, amount: int) -> str:
    return encode_call(SIG_APPROVE, ["address", "uint256"], [spender, amount])


def transfer_calldata(to: str, amount: int) -> str:
    return encode_call(SIG_TRANSFER, ["address", "uint256"], [to, amount])


_ = encode  # keep import for callers constructing custom payloads
