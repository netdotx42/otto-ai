"""Level 3 assisted execution endpoints. Every mutating route supports Idempotency-Key."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.chains.registry import get_clients
from app.core import idempotency
from app.core.ratelimit import rate_limit
from app.db.models import User
from app.db.session import get_db
from app.execution import service
from app.services.entitlements import charge

router = APIRouter(prefix="/executions", tags=["executions"], dependencies=[Depends(rate_limit("execution"))])


class StartIn(BaseModel):
    request: str = Field(min_length=3, max_length=1000)
    chain: Literal["base", "solana"]
    wallet: str = Field(min_length=20, max_length=64)
    conversation_id: uuid.UUID | None = None


class ApproveIn(BaseModel):
    digest: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    confirm: Literal[True] = Field(description="Must be literally true: explicit user confirmation.")


class SubmitIn(BaseModel):
    approval_id: uuid.UUID
    tx_hash: str | None = Field(default=None, pattern=r"^0x[0-9a-fA-F]{64}$")
    signed_tx: str | None = Field(default=None, max_length=8000)

    @model_validator(mode="after")
    def one_of(self) -> SubmitIn:
        if (self.tx_hash is None) == (self.signed_tx is None):
            raise ValueError("Provide exactly one of tx_hash (Base) or signed_tx (Solana).")
        return self


async def _idem(db, user, key, route, body, fn):
    replay = await idempotency.begin(db, user.id, key, route, body)
    if replay is not None:
        return replay
    out = await fn()
    await idempotency.finish(db, user.id, key, 200, out)
    return out


@router.post("")
async def start(body: StartIn, db: AsyncSession = Depends(get_db), user: User = Depends(current_user), idempotency_key: str | None = Header(default=None)) -> dict[str, Any]:
    async def fn():
        await charge(db, user, 5, "simulator")
        return await service.start(db, user, get_clients(), text=body.request, chain=body.chain, wallet=body.wallet, conversation_id=body.conversation_id)

    return await _idem(db, user, idempotency_key, "POST /executions", body.model_dump(mode="json"), fn)


@router.get("/{execution_id}")
async def get(execution_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    return await service.poll(db, user, get_clients(), execution_id)


@router.post("/{execution_id}/refresh")
async def refresh(execution_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    return await service.refresh(db, user, get_clients(), execution_id)


@router.post("/steps/{step_id}/approve")
async def approve(step_id: uuid.UUID, body: ApproveIn, db: AsyncSession = Depends(get_db), user: User = Depends(current_user), idempotency_key: str | None = Header(default=None)) -> dict[str, Any]:
    return await _idem(db, user, idempotency_key, f"POST /executions/steps/{step_id}/approve", body.model_dump(mode="json"), lambda: service.approve(db, user, step_id, body.digest))


@router.post("/submit")
async def submit(body: SubmitIn, db: AsyncSession = Depends(get_db), user: User = Depends(current_user), idempotency_key: str | None = Header(default=None)) -> dict[str, Any]:
    signed = {"tx_hash": body.tx_hash} if body.tx_hash else {"signed_tx": body.signed_tx}
    return await _idem(db, user, idempotency_key, "POST /executions/submit", body.model_dump(mode="json"), lambda: service.settle(db, user, get_clients(), body.approval_id, signed))


@router.post("/{execution_id}/cancel")
async def cancel(execution_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    return await service.cancel(db, user, execution_id)
