"""Minimal, typed EVM JSON-RPC client (Base). All reads carry provenance for evidence."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any

import httpx
from eth_abi import decode, encode
from eth_utils import keccak, to_checksum_address

from app.core.errors import APIError


class RpcError(Exception):
    def __init__(self, message: str, data: Any = None, code: int | None = None) -> None:
        super().__init__(message)
        self.data = data
        self.code = code


def selector(signature: str) -> bytes:
    return keccak(text=signature)[:4]


def encode_call(signature: str, types: list[str], args: list[Any]) -> str:
    return "0x" + (selector(signature) + encode(types, args)).hex()


def decode_revert(data: str | None) -> str:
    """Decode Error(string) / Panic(uint256) revert payloads; otherwise return raw hex."""
    if not data or data == "0x":
        return "execution reverted (no reason)"
    raw = bytes.fromhex(data[2:] if data.startswith("0x") else data)
    if raw[:4] == selector("Error(string)"):
        try:
            return f"reverted: {decode(['string'], raw[4:])[0]}"
        except Exception:  # noqa: BLE001
            pass
    if raw[:4] == selector("Panic(uint256)"):
        return f"panic: 0x{decode(['uint256'], raw[4:])[0]:x}"
    return f"reverted with data 0x{raw.hex()[:200]}"


@dataclass
class CallResult:
    data: str
    block: int


class EvmRpc:
    def __init__(self, url: str, timeout: float = 15.0) -> None:
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
            raise APIError(502, "rpc_unavailable", f"Base RPC request failed: {type(e).__name__}") from e
        if "error" in body:
            err = body["error"]
            raise RpcError(err.get("message", "rpc error"), err.get("data"), err.get("code"))
        return body["result"]

    async def chain_id(self) -> int:
        return int(await self.request("eth_chainId", []), 16)

    async def block_number(self) -> int:
        return int(await self.request("eth_blockNumber", []), 16)

    async def get_balance(self, address: str, block: str = "latest") -> int:
        return int(await self.request("eth_getBalance", [address, block]), 16)

    async def get_code(self, address: str, block: str = "latest") -> str:
        return await self.request("eth_getCode", [address, block])

    async def get_storage_at(self, address: str, slot: str, block: str = "latest") -> str:
        return await self.request("eth_getStorageAt", [address, slot, block])

    async def call(self, tx: dict[str, Any], block: str = "latest") -> str:
        return await self.request("eth_call", [tx, block])

    async def estimate_gas(self, tx: dict[str, Any]) -> int:
        return int(await self.request("eth_estimateGas", [tx]), 16)

    async def gas_price(self) -> int:
        return int(await self.request("eth_gasPrice", []), 16)

    async def max_priority_fee(self) -> int:
        try:
            return int(await self.request("eth_maxPriorityFeePerGas", []), 16)
        except RpcError:
            return 0

    async def get_logs(self, flt: dict[str, Any]) -> list[dict[str, Any]]:
        return await self.request("eth_getLogs", [flt])

    async def get_transaction(self, tx_hash: str) -> dict[str, Any] | None:
        return await self.request("eth_getTransactionByHash", [tx_hash])

    async def get_receipt(self, tx_hash: str) -> dict[str, Any] | None:
        return await self.request("eth_getTransactionReceipt", [tx_hash])

    async def get_block(self, number: str = "latest") -> dict[str, Any]:
        return await self.request("eth_getBlockByNumber", [number, False])

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- typed helpers -------------------------------------------------------------------

    async def erc20_balance(self, token: str, owner: str) -> int:
        out = await self.call({"to": token, "data": encode_call("balanceOf(address)", ["address"], [owner])})
        return int(out, 16) if out and out != "0x" else 0

    async def erc20_allowance(self, token: str, owner: str, spender: str) -> int:
        out = await self.call(
            {"to": token, "data": encode_call("allowance(address,address)", ["address", "address"], [owner, spender])}
        )
        return int(out, 16) if out and out != "0x" else 0

    async def erc20_metadata(self, token: str) -> dict[str, Any]:
        meta: dict[str, Any] = {}
        for field, sig, typ in (
            ("name", "name()", "string"),
            ("symbol", "symbol()", "string"),
            ("decimals", "decimals()", "uint8"),
            ("totalSupply", "totalSupply()", "uint256"),
        ):
            try:
                out = await self.call({"to": token, "data": "0x" + selector(sig).hex()})
                raw = bytes.fromhex(out[2:])
                if typ == "string":
                    try:
                        meta[field] = decode(["string"], raw)[0]
                    except Exception:  # noqa: BLE001 - bytes32-style names
                        meta[field] = raw.rstrip(b"\x00").decode("utf-8", "replace")
                else:
                    meta[field] = decode([typ], raw)[0]
            except (RpcError, ValueError, Exception):  # noqa: BLE001
                meta[field] = None
        return meta


def checksum(address: str) -> str:
    return to_checksum_address(address)
