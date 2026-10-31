"""Mint supply (from verified contract/program state) and mint-event indexing."""

from __future__ import annotations

import base64
import hashlib
import struct
from datetime import UTC, datetime
from typing import Any

from eth_abi import decode
from eth_utils import keccak
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.chains.evm import RpcError, encode_call
from app.chains.registry import get_clients
from app.core.chains import explorer_tx_url
from app.core.config import get_settings
from app.core.errors import APIError
from app.db.models import IndexerCursor, MintEvent
from app.services.entitlements import solana_config_pda

PASS_MINTED_TOPIC = "0x" + keccak(text="PassMinted(address,uint256,uint256,uint256)").hex()
ANCHOR_PASS_MINTED_DISC = hashlib.sha256(b"event:PassMinted").digest()[:8]
LOG_CHUNK = 5_000


async def base_supply() -> dict[str, Any]:
    s = get_settings()
    if not s.base_pass_address:
        return {"chain": "base", "available": False, "reason": "Base pass contract address not configured"}
    evm = get_clients().evm
    try:
        out = await evm.call({"to": s.base_pass_address, "data": encode_call("totalMinted()", [], [])})
        block = await evm.block_number()
    except (RpcError, APIError) as e:
        return {"chain": "base", "available": False, "reason": f"RPC error: {e}"}
    return {
        "chain": "base", "available": True, "total_minted": int(out, 16),
        "source": f"Agent98Pass.totalMinted() @ block {block}", "contract": s.base_pass_address,
    }


def decode_solana_config(raw: bytes) -> dict[str, Any]:
    # 8-byte discriminator, then 5 pubkeys (32 bytes each), u64 total_minted, bool paused, u8, u8
    off = 8 + 32 * 5
    total = struct.unpack_from("<Q", raw, off)[0]
    paused = raw[off + 8] == 1
    return {"total_minted": total, "paused": paused}


async def solana_supply() -> dict[str, Any]:
    try:
        acc, slot = await get_clients().solana.get_account_info(solana_config_pda(), encoding="base64")
    except (RpcError, APIError) as e:
        return {"chain": "solana", "available": False, "reason": f"RPC error: {e}"}
    if acc is None:
        return {"chain": "solana", "available": False, "reason": "Solana pass program not initialized on this cluster"}
    if acc.get("owner") != get_settings().solana_pass_program_id:
        return {"chain": "solana", "available": False, "reason": "Config account owner mismatch"}
    data = base64.b64decode(acc["data"][0])
    cfg = decode_solana_config(data)
    return {
        "chain": "solana", "available": True, "total_minted": cfg["total_minted"], "paused": cfg["paused"],
        "source": f"config PDA {solana_config_pda()} @ slot {slot}", "program": get_settings().solana_pass_program_id,
    }


async def _cursor(db: AsyncSession, key: str) -> str | None:
    row = await db.get(IndexerCursor, key)
    return row.value if row else None


async def _set_cursor(db: AsyncSession, key: str, value: str) -> None:
    await db.execute(
        insert(IndexerCursor).values(key=key, value=value).on_conflict_do_update(
            index_elements=[IndexerCursor.key], set_={"value": value, "updated_at": datetime.now(UTC)}
        )
    )


async def index_base_mints(db: AsyncSession, max_chunks: int = 20) -> int:
    s = get_settings()
    if not s.base_pass_address:
        return 0
    evm = get_clients().evm
    start = int(await _cursor(db, "base_mints") or s.base_pass_deploy_block)
    head = await evm.block_number() - 1  # small reorg margin
    added = 0
    for _ in range(max_chunks):
        if start > head:
            break
        end = min(head, start + LOG_CHUNK - 1)
        logs = await evm.get_logs(
            {"address": s.base_pass_address, "topics": [PASS_MINTED_TOPIC], "fromBlock": hex(start), "toBlock": hex(end)}
        )
        for lg in logs:
            if lg.get("removed"):
                continue
            buyer = "0x" + lg["topics"][1][-40:]
            qty, paid, total = decode(["uint256", "uint256", "uint256"], bytes.fromhex(lg["data"][2:]))
            res = await db.execute(
                insert(MintEvent).values(
                    chain="base", tx_id=lg["transactionHash"], log_index=int(lg["logIndex"], 16), buyer=buyer.lower(),
                    quantity=qty, paid_base_units=paid, total_minted_after=total, block_or_slot=int(lg["blockNumber"], 16),
                ).on_conflict_do_nothing(constraint="uq_mint_event")
            )
            added += res.rowcount or 0
        start = end + 1
        await _set_cursor(db, "base_mints", str(start))
        await db.commit()
    return added


def parse_anchor_pass_minted(logs: list[str]) -> list[dict[str, Any]]:
    """Extract PassMinted events from 'Program data:' log lines (Anchor emit!)."""
    out = []
    for line in logs:
        if not line.startswith("Program data: "):
            continue
        try:
            raw = base64.b64decode(line[len("Program data: "):])
        except ValueError:
            continue
        if raw[:8] != ANCHOR_PASS_MINTED_DISC or len(raw) < 8 + 32 + 24:
            continue
        from solders.pubkey import Pubkey

        buyer = str(Pubkey.from_bytes(raw[8:40]))
        qty, paid, total = struct.unpack_from("<QQQ", raw, 40)
        out.append({"buyer": buyer, "quantity": qty, "paid": paid, "total_minted": total})
    return out


async def index_solana_mints(db: AsyncSession, limit: int = 200) -> int:
    sol = get_clients().solana
    program = get_settings().solana_pass_program_id
    until = await _cursor(db, "solana_mints")
    sigs = await sol.get_signatures_for_address(program, limit=limit, until=until)
    added = 0
    for entry in reversed(sigs):  # oldest first
        if entry.get("err") is not None:
            continue
        tx = await sol.get_transaction(entry["signature"])
        if not tx or tx.get("meta", {}).get("err") is not None:
            continue
        # Only trust events emitted by OUR program: ensure it is invoked in this tx.
        logs = tx["meta"].get("logMessages") or []
        if not any(line.startswith(f"Program {program} invoke") for line in logs):
            continue
        for i, ev in enumerate(parse_anchor_pass_minted(logs)):
            res = await db.execute(
                insert(MintEvent).values(
                    chain="solana", tx_id=entry["signature"], log_index=i, buyer=ev["buyer"], quantity=ev["quantity"],
                    paid_base_units=ev["paid"], total_minted_after=ev["total_minted"], block_or_slot=int(tx["slot"]),
                    block_time=datetime.fromtimestamp(tx["blockTime"], UTC) if tx.get("blockTime") else None,
                ).on_conflict_do_nothing(constraint="uq_mint_event")
            )
            added += res.rowcount or 0
    if sigs:
        await _set_cursor(db, "solana_mints", sigs[0]["signature"])
    await db.commit()
    return added


async def mint_history(db: AsyncSession, chain: str | None, buyers: list[str], limit: int, offset: int) -> list[dict[str, Any]]:
    q = select(MintEvent).order_by(MintEvent.block_or_slot.desc(), MintEvent.log_index.desc())
    if chain:
        q = q.where(MintEvent.chain == chain)
    if buyers:
        q = q.where(MintEvent.buyer.in_(buyers))
    rows = (await db.execute(q.limit(limit).offset(offset))).scalars().all()
    return [
        {
            "chain": r.chain, "tx_id": r.tx_id, "buyer": r.buyer, "quantity": int(r.quantity),
            "paid_usdc": f"{int(r.paid_base_units) / 1_000_000:.2f}", "total_minted_after": int(r.total_minted_after),
            "block_or_slot": int(r.block_or_slot), "explorer_url": explorer_tx_url(r.chain, r.tx_id),
        }
        for r in rows
    ]
