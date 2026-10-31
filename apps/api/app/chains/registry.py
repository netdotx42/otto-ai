"""Process-wide chain clients. Tests swap these for deterministic fakes via `set_clients`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.chains.evm import EvmRpc
from app.chains.solana import SolanaRpc
from app.core.config import get_settings


@dataclass
class ChainClients:
    evm: Any  # EvmRpc-compatible
    solana: Any  # SolanaRpc-compatible
    http: httpx.AsyncClient  # for quote providers (Jupiter)


_clients: ChainClients | None = None


def get_clients() -> ChainClients:
    global _clients
    if _clients is None:
        s = get_settings()
        _clients = ChainClients(
            evm=EvmRpc(s.base_rpc_url), solana=SolanaRpc(s.solana_rpc_url), http=httpx.AsyncClient(timeout=15)
        )
    return _clients


def set_clients(clients: ChainClients | None) -> None:
    global _clients
    _clients = clients
