"""Idempotency-Key support for mutating endpoints.

Same key + same body  -> replay the stored response.
Same key + other body -> 409 conflict.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIError
from app.db.models import IdempotencyKey


def request_hash(body: Any) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


async def begin(db: AsyncSession, user_id: uuid.UUID, key: str | None, route: str, body: Any) -> dict[str, Any] | None:
    """Returns a stored response to replay, or None if the caller should proceed."""
    if not key:
        return None
    if len(key) > 128:
        raise APIError(400, "bad_idempotency_key", "Idempotency-Key too long")
    h = request_hash(body)
    stmt = (
        insert(IdempotencyKey)
        .values(user_id=user_id, key=key, route=route, request_hash=h)
        .on_conflict_do_nothing(constraint="uq_idem_user_key")
        .returning(IdempotencyKey.id)
    )
    inserted = (await db.execute(stmt)).scalar()
    await db.commit()
    if inserted:
        return None
    row = (
        await db.execute(select(IdempotencyKey).where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key))
    ).scalar_one()
    if row.route != route or row.request_hash != h:
        raise APIError(409, "idempotency_conflict", "Idempotency-Key reused with a different request.")
    if row.response is None:
        raise APIError(409, "idempotency_in_progress", "A request with this Idempotency-Key is in progress.")
    return row.response


async def finish(db: AsyncSession, user_id: uuid.UUID, key: str | None, status: int, response: dict[str, Any]) -> None:
    if not key:
        return
    row = (
        await db.execute(select(IdempotencyKey).where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key))
    ).scalar_one_or_none()
    if row:
        row.status_code = status
        row.response = json.loads(json.dumps(response, default=str))
        await db.commit()
