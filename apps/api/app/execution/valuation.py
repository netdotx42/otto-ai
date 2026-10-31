"""USD valuation of an input amount from trusted sources only (never model output)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.chains.evm import RpcError
from app.core.chains import base_cfg, token_by_symbol
from app.core.config import get_settings
from app.core.errors import APIError
from app.providers import uniswap_v3 as uni


async def input_usd_value(evm, http, research: dict[str, Any]) -> tuple[float | None, str]:
    tin = research["input_token"]
    amt = int(research["amount_in"])
    if tin.get("stable"):
        return float(Decimal(amt) / (Decimal(10) ** tin["decimals"])), "stablecoin at $1.00 (par assumption)"
    out = research.get("output_token")
    q = research.get("quote")
    if out and out.get("stable") and q:
        return float(Decimal(int(q["amount_out"])) / (Decimal(10) ** out["decimals"])), f"quote output ({q['provider']})"
    if research["chain"] == "base":
        usdc = token_by_symbol("base", "USDC")
        assert usdc
        token_in = base_cfg()["weth"].lower() if tin.get("native") else tin["address"]
        try:
            vq = await uni.quote(evm, token_in, usdc.address.lower(), amt, 50)
        except (APIError, RpcError):
            return None, "no on-chain USDC route to value input"
        return float(Decimal(vq.amount_out) / Decimal(10**6)), "Uniswap V3 QuoterV2 → USDC (on-chain)"
    mint = "So11111111111111111111111111111111111111112" if tin.get("native") else tin["address"]
    try:
        r = await http.get(f"{get_settings().jupiter_base()}/price/v3", params={"ids": mint}, headers=get_settings().jupiter_headers())
        price = r.json().get(mint, {}).get("usdPrice") if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        price = None
    if price is None:
        return None, "Jupiter price unavailable"
    return float(Decimal(str(price)) * Decimal(amt) / (Decimal(10) ** tin["decimals"])), "Jupiter Price API v3"
