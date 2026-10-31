"""Loads the shared chain registry / protocol allowlist (packages/shared/chains.json)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import get_settings

_DEFAULT = Path(__file__).resolve().parents[4] / "packages" / "shared" / "chains.json"


@lru_cache
def registry() -> dict[str, Any]:
    path = Path(os.environ.get("CHAINS_JSON_PATH", _DEFAULT))
    return json.loads(path.read_text())


@dataclass(frozen=True)
class TokenInfo:
    symbol: str
    address: str
    decimals: int
    stable: bool = False
    native: bool = False


def base_cfg() -> dict[str, Any]:
    return registry()["base"][get_settings().network_mode]


def solana_cfg() -> dict[str, Any]:
    return registry()["solana"][get_settings().network_mode]


def solana_allowed_programs() -> dict[str, str]:
    return dict(registry()["solana"]["allowedPrograms"])


def pass_cfg() -> dict[str, Any]:
    return registry()["pass"]


def tokens(chain: str) -> list[TokenInfo]:
    cfg = base_cfg() if chain == "base" else solana_cfg()
    return [
        TokenInfo(
            symbol=t["symbol"],
            address=t["address"],
            decimals=int(t["decimals"]),
            stable=bool(t.get("stable", False)),
            native=bool(t.get("native", False)),
        )
        for t in cfg["tokens"]
    ]


def token_by_symbol(chain: str, symbol: str) -> TokenInfo | None:
    s = symbol.upper()
    if chain == "base" and s == "ETH":
        return TokenInfo("ETH", "native", 18, native=True)
    for t in tokens(chain):
        if t.symbol.upper() == s:
            return t
    return None


def token_by_address(chain: str, address: str) -> TokenInfo | None:
    for t in tokens(chain):
        if (t.address.lower() == address.lower()) if chain == "base" else (t.address == address):
            return t
    return None


def base_chain_id() -> int:
    return int(base_cfg()["chainId"])


def explorer_tx_url(chain: str, tx: str) -> str:
    if chain == "base":
        return f"{base_cfg()['explorer']}/tx/{tx}"
    cfg = solana_cfg()
    return f"{cfg['explorer']}/tx/{tx}{cfg.get('explorerSuffix', '')}"
