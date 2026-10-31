"""Hash-chained audit logging. Tampering with any row breaks every subsequent hash."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent

GENESIS = "0" * 64


def _hash(prev: str, action: str, user_id: str | None, subject: str | None, data: dict[str, Any]) -> str:
    payload = json.dumps(
        {"prev": prev, "action": action, "user": user_id, "subject": subject, "data": data},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


async def audit(
    db: AsyncSession,
    action: str,
    *,
    user_id: uuid.UUID | None = None,
    subject: str | None = None,
    data: dict[str, Any] | None = None,
) -> AuditEvent:
    data = data or {}
    # Serialize writers so the chain is linear.
    await db.execute(text("SELECT pg_advisory_xact_lock(9898)"))
    prev = (await db.execute(select(AuditEvent.hash).order_by(AuditEvent.id.desc()).limit(1))).scalar()
    prev = prev or GENESIS
    uid = str(user_id) if user_id else None
    ev = AuditEvent(
        user_id=user_id, action=action, subject=subject, data=data, prev_hash=prev,
        hash=_hash(prev, action, uid, subject, data),
    )
    db.add(ev)
    await db.flush()
    return ev


async def verify_chain(db: AsyncSession) -> tuple[bool, int | None]:
    prev = GENESIS
    rows = (await db.execute(select(AuditEvent).order_by(AuditEvent.id))).scalars()
    for ev in rows:
        expected = _hash(prev, ev.action, str(ev.user_id) if ev.user_id else None, ev.subject, ev.data)
        if ev.prev_hash != prev or ev.hash != expected:
            return False, ev.id
        prev = ev.hash
    return True, None
