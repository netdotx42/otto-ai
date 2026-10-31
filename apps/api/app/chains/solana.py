"""Minimal, typed Solana JSON-RPC client."""

from __future__ import annotations

import itertools
from typing import Any

import httpx

from app.chains.evm import RpcError
from app.core.errors import APIError

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


class SolanaRpc:
    def __init__(self, url: str, timeout: float = 20.0) -> None:
        self.url = url
        self._ids = itertools.count(1)
        self._client = httpx.AsyncClient(timeout=timeout)

    async def request(self, method: str, params: list[Any]) -> Any:
        try:
            r = await self._client.post(
                self.url, json={"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params}
            )
            r.raise_for_status()
            body = r.json()
        except httpx.HTTPError as e:
            raise APIError(502, "rpc_unavailable", f"Solana RPC request failed: {type(e).__name__}") from e
        if "error" in body:
            err = body["error"]
            raise RpcError(err.get("message", "rpc error"), err.get("data"), err.get("code"))
        return body["result"]

    async def get_slot(self, commitment: str = "confirmed") -> int:
        return int(await self.request("getSlot", [{"commitment": commitment}]))

    async def get_balance(self, address: str) -> tuple[int, int]:
        res = await self.request("getBalance", [address, {"commitment": "confirmed"}])
        return int(res["value"]), int(res["context"]["slot"])

    async def get_account_info(self, address: str, encoding: str = "jsonParsed") -> tuple[dict[str, Any] | None, int]:
        res = await self.request("getAccountInfo", [address, {"encoding": encoding, "commitment": "confirmed"}])
        return res["value"], int(res["context"]["slot"])

    async def get_token_accounts_by_owner(self, owner: str, *, mint: str | None = None, program: str | None = None) -> tuple[list[dict[str, Any]], int]:
        flt = {"mint": mint} if mint else {"programId": program or TOKEN_PROGRAM}
        res = await self.request(
            "getTokenAccountsByOwner", [owner, flt, {"encoding": "jsonParsed", "commitment": "confirmed"}]
        )
        return res["value"], int(res["context"]["slot"])

    async def get_latest_blockhash(self) -> tuple[str, int, int]:
        res = await self.request("getLatestBlockhash", [{"commitment": "confirmed"}])
        return res["value"]["blockhash"], int(res["value"]["lastValidBlockHeight"]), int(res["context"]["slot"])

    async def get_block_height(self) -> int:
        return int(await self.request("getBlockHeight", [{"commitment": "confirmed"}]))

    async def simulate_transaction(self, tx_b64: str, account_addresses: list[str] | None = None) -> dict[str, Any]:
        cfg: dict[str, Any] = {
            "encoding": "base64",
            "sigVerify": False,
            # Simulate the EXACT candidate: keep its blockhash so expiry/staleness is real.
            "replaceRecentBlockhash": False,
            "commitment": "confirmed",
            "innerInstructions": True,
        }
        if account_addresses:
            cfg["accounts"] = {"encoding": "jsonParsed", "addresses": account_addresses}
        return await self.request("simulateTransaction", [tx_b64, cfg])

    async def send_transaction(self, tx_b64: str) -> str:
        return await self.request(
            "sendTransaction",
            [tx_b64, {"encoding": "base64", "skipPreflight": False, "preflightCommitment": "confirmed", "maxRetries": 3}],
        )

    async def get_signature_statuses(self, sigs: list[str]) -> list[dict[str, Any] | None]:
        res = await self.request("getSignatureStatuses", [sigs, {"searchTransactionHistory": True}])
        return res["value"]

    async def get_transaction(self, sig: str) -> dict[str, Any] | None:
        return await self.request(
            "getTransaction",
            [sig, {"encoding": "jsonParsed", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}],
        )

    async def get_signatures_for_address(self, address: str, *, limit: int = 100, until: str | None = None, before: str | None = None) -> list[dict[str, Any]]:
        cfg: dict[str, Any] = {"limit": limit, "commitment": "confirmed"}
        if until:
            cfg["until"] = until
        if before:
            cfg["before"] = before
        return await self.request("getSignaturesForAddress", [address, cfg])

    async def aclose(self) -> None:
        await self._client.aclose()
