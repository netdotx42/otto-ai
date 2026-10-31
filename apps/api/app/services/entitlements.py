"""Unified pass entitlements (Base + Solana) and transparent usage credits.

Entitlement truth is on-chain state read by the server:
  * Base:   Agent98Pass.balanceOf(wallet, PASS_ID) via eth_call
  * Solana: sum of pass-mint token-account balances owned by the wallet
Clients never self-report pass balances.

Credits: each pass grants `creditsPerPassPerPeriod` credits per period, counting at most
`maxPassesCountedForCredits` passes (fair use). Holding more passes is allowed (no supply
limit), but does not grant unlimited inference.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from solders.pubkey import Pubkey
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.chains.evm import RpcError, encode_call
from app.chains.registry import get_clients
from app.core.chains import pass_cfg
from app.core.config import get_settings
from app.core.errors import APIError
from app.db.models import PassEntitlement, UsageLedger, User, Wallet

ENTITLEMENT_TTL = timedelta(minutes=5)


def solana_pass_mint() -> str:
    pid = Pubkey.from_string(get_settings().solana_pass_program_id)
    return str(Pubkey.find_program_address([b"pass_mint"], pid)[0])


def solana_config_pda() -> str:
    pid = Pubkey.from_string(get_settings().solana_pass_program_id)
    return str(Pubkey.find_program_address([b"config"], pid)[0])


async def read_base_passes(address: str) -> tuple[int | None, str]:
    s = get_settings()
    if not s.base_pass_address:
        return None, "base pass contract not deployed/configured"
    evm = get_clients().evm
    data = encode_call("balanceOf(address,uint256)", ["address", "uint256"], [address, 1])
    try:
        out = await evm.call({"to": s.base_pass_address, "data": data})
        block = await evm.block_number()
    except (RpcError, APIError) as e:
        return None, f"base rpc error: {e}"
    return int(out, 16), f"base eth_call balanceOf @ block {block}"


async def read_solana_passes(address: str) -> tuple[int | None, str]:
    try:
        accounts, slot = await get_clients().solana.get_token_accounts_by_owner(address, mint=solana_pass_mint())
    except (RpcError, APIError) as e:
        return None, f"solana rpc error: {e}"
    total = 0
    for acc in accounts:
        info = acc["account"]["data"]["parsed"]["info"]
        total += int(info["tokenAmount"]["amount"])
    return total, f"solana getTokenAccountsByOwner @ slot {slot}"


@dataclass
class EntitlementView:
    passes: int
    by_wallet: list[dict[str, Any]] = field(default_factory=list)
    has_access: bool = False
    credits_granted: int = 0
    credits_used: int = 0
    credits_remaining: int = 0
    period: str = ""
    period_resets_at: str = ""
    errors: list[str] = field(default_factory=list)


def current_period(now: datetime | None = None) -> tuple[str, datetime]:
    now = now or datetime.now(UTC)
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    nxt = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=UTC)
    return start.strftime("%Y-%m"), nxt


async def refresh_wallet(db: AsyncSession, wallet: Wallet, force: bool = False) -> tuple[int | None, str]:
    row = await db.get(PassEntitlement, wallet.id)
    if row and not force and datetime.now(UTC) - row.checked_at < ENTITLEMENT_TTL:
        return row.passes, row.source
    if wallet.chain == "base":
        n, src = await read_base_passes(wallet.address)
    else:
        n, src = await read_solana_passes(wallet.address)
    if n is None:
        # Keep last verified value (if any) but surface the error; never invent a balance.
        return (row.passes if row else None), src
    stmt = (
        insert(PassEntitlement)
        .values(wallet_id=wallet.id, passes=n, source=src, checked_at=datetime.now(UTC))
        .on_conflict_do_update(
            index_elements=[PassEntitlement.wallet_id], set_={"passes": n, "source": src, "checked_at": datetime.now(UTC)}
        )
    )
    await db.execute(stmt)
    return n, src


async def entitlements(db: AsyncSession, user: User, force: bool = False) -> EntitlementView:
    view = EntitlementView(passes=0)
    for w in user.wallets:
        n, src = await refresh_wallet(db, w, force=force)
        if n is None:
            view.errors.append(f"{w.chain}:{w.address}: {src}")
        view.by_wallet.append({"chain": w.chain, "address": w.address, "passes": n, "source": src})
        view.passes += n or 0
    await db.commit()
    cfg = pass_cfg()
    counted = min(view.passes, int(cfg["maxPassesCountedForCredits"]))
    period, resets = current_period()
    view.period, view.period_resets_at = period, resets.isoformat()
    view.credits_granted = counted * int(cfg["creditsPerPassPerPeriod"])
    view.credits_used = await credits_used(db, user.id, period)
    view.credits_remaining = max(0, view.credits_granted - view.credits_used)
    view.has_access = view.passes > 0
    return view


async def credits_used(db: AsyncSession, user_id: uuid.UUID, period: str) -> int:
    used = (
        await db.execute(
            select(func.coalesce(func.sum(-UsageLedger.delta), 0)).where(
                UsageLedger.user_id == user_id, UsageLedger.period == period, UsageLedger.delta < 0
            )
        )
    ).scalar()
    return int(used or 0)


async def charge(db: AsyncSession, user: User, cost: int, module: str, ref: str | None = None) -> EntitlementView:
    """Atomically verify entitlement and debit credits. Raises 402/403 when not allowed."""
    view = await entitlements(db, user)
    if not view.has_access:
        raise APIError(
            403, "pass_required",
            "A verified AGENT//98 pass (Base or Solana) is required. Passes are 1.98 USDC.",
            {"errors": view.errors},
        )
    if cost <= 0:
        return view
    # Serialize debits per user to prevent double spend of credits under concurrency.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"credits:{user.id}"})
    used = await credits_used(db, user.id, view.period)
    if used + cost > view.credits_granted:
        await db.rollback()
        raise APIError(
            402, "credits_exhausted",
            f"Usage credits exhausted for {view.period} ({used}/{view.credits_granted}). Credits reset {view.period_resets_at}.",
        )
    db.add(UsageLedger(user_id=user.id, period=view.period, delta=-cost, reason="module_call", module=module, ref=ref))
    await db.commit()
    view.credits_used = used + cost
    view.credits_remaining = view.credits_granted - view.credits_used
    return view
