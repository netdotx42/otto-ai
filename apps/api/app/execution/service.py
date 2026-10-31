"""HTTP-facing operations over the execution graph and its persisted records."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.config import get_settings
from app.core.errors import APIError
from app.db.models import (
    ApprovalRecord,
    ConfirmedReceipt,
    ExecutionStep,
    SimulationRecord,
    TransactionSubmission,
    User,
)
from app.db.models import TransactionIntent as IntentRow
from app.execution import graph
from app.execution.graph import ExecState, check_submission, execution_enabled
from app.execution.types import ExecStatus

TERMINAL = {ExecStatus.confirmed, ExecStatus.failed, ExecStatus.rejected, ExecStatus.cancelled, ExecStatus.needs_clarification}


async def _owned(db: AsyncSession, user: User, execution_id: uuid.UUID) -> IntentRow:
    row = await db.get(IntentRow, execution_id)
    if row is None or row.user_id != user.id:
        raise APIError(404, "not_found", "Execution not found.")
    return row


async def start(
    db: AsyncSession, user: User, clients: Any, *, text: str, chain: str, wallet: str,
    conversation_id: uuid.UUID | None = None, workflow_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    if not await execution_enabled(db):
        raise APIError(503, "execution_disabled", "Assisted execution is temporarily disabled by the operators.")
    w = wallet.lower() if chain == "base" else wallet
    row = IntentRow(
        user_id=user.id, conversation_id=conversation_id, workflow_id=workflow_id, chain=chain, wallet=w,
        kind="unknown", raw_text=text[:2000], intent={}, status=ExecStatus.preparing,
    )
    db.add(row)
    await db.flush()
    await audit(db, "execution.request", user_id=user.id, subject=str(row.id), data={"chain": chain, "text": text[:500]})
    await db.commit()
    state: ExecState = {"phase": "prepare", "execution_id": str(row.id), "text": text, "chain": chain, "wallet": w, "step_index": 0, "trace": []}
    out = await graph.run(state, db, user, clients)
    await db.refresh(row)
    return await serialize(db, row, trace=out.get("trace"), questions=out.get("questions"), error=out.get("error"))


async def start_stream(
    db: AsyncSession, user: User, clients: Any, *, text: str, chain: str, wallet: str, conversation_id: uuid.UUID | None = None,
):
    """Like `start`, but yields each LangGraph node update as it completes (for the terminal)."""
    if not await execution_enabled(db):
        raise APIError(503, "execution_disabled", "Assisted execution is temporarily disabled by the operators.")
    w = wallet.lower() if chain == "base" else wallet
    row = IntentRow(user_id=user.id, conversation_id=conversation_id, chain=chain, wallet=w, kind="unknown", raw_text=text[:2000], intent={}, status=ExecStatus.preparing)
    db.add(row)
    await db.flush()
    await audit(db, "execution.request", user_id=user.id, subject=str(row.id), data={"chain": chain, "text": text[:500]})
    await db.commit()
    state: ExecState = {"phase": "prepare", "execution_id": str(row.id), "text": text, "chain": chain, "wallet": w, "step_index": 0, "trace": []}
    final: dict[str, Any] = dict(state)
    async for update_ in graph.GRAPH.astream(state, config={"configurable": {"db": db, "user": user, "clients": clients}}, stream_mode="updates"):
        for node, delta in update_.items():
            delta = delta or {}
            final.update(delta)
            last = (delta.get("trace") or [{}])[-1]
            yield {"type": "node", "node": node, "detail": last.get("detail", "")}
    await db.refresh(row)
    yield {"type": "result", "execution": await serialize(db, row, trace=final.get("trace"), questions=final.get("questions"), error=final.get("error"))}


async def refresh(db: AsyncSession, user: User, clients: Any, execution_id: uuid.UUID) -> dict[str, Any]:
    """Re-quote, rebuild and re-simulate the current step. Any prior approval is invalidated."""
    row = await _owned(db, user, execution_id)
    if row.status not in (ExecStatus.awaiting_approval, ExecStatus.blocked, ExecStatus.expired):
        raise APIError(409, "not_refreshable", f"Execution is {row.status}.")
    step = next((s for s in row.steps if s.index == row.current_step), None)
    if step:
        await db.execute(update(ApprovalRecord).where(ApprovalRecord.step_id == step.id, ApprovalRecord.used_at.is_(None)).values(invalidated_at=datetime.now(UTC)))
        await db.commit()
    state: ExecState = {
        "phase": "prepare", "execution_id": str(row.id), "intent": row.intent, "chain": row.chain, "wallet": row.wallet,
        "step_index": row.current_step, "text": row.raw_text, "trace": [], "steps_plan": _plan_from(row),
    }
    out = await graph.run(state, db, user, clients)
    await db.refresh(row)
    return await serialize(db, row, trace=out.get("trace"), error=out.get("error"))


async def approve(db: AsyncSession, user: User, step_id: uuid.UUID, digest: str) -> dict[str, Any]:
    """Explicit user confirmation. Bound to the digest the user's client computed and displayed."""
    s = get_settings()
    step = await db.get(ExecutionStep, step_id)
    if step is None:
        raise APIError(404, "not_found", "Step not found.")
    row = await _owned(db, user, step.intent_id)
    if not await execution_enabled(db):
        raise APIError(503, "execution_disabled", "Execution disabled by emergency switch.")
    if row.status != ExecStatus.awaiting_approval or step.status != ExecStatus.awaiting_approval or row.current_step != step.index:
        raise APIError(409, "not_awaiting_approval", f"Step is {step.status}.")
    if not step.policy.get("allowed"):
        raise APIError(403, "policy_blocked", "The security policy blocked this transaction.")
    if digest.lower() != (step.digest or "").lower():
        await audit(db, "execution.approval_digest_mismatch", user_id=user.id, subject=str(step.id), data={"client": digest, "server": step.digest})
        await db.commit()
        raise APIError(409, "digest_mismatch", "The transaction you reviewed differs from the current candidate. Refresh and review again.")
    sim = (await db.execute(select(SimulationRecord).where(SimulationRecord.step_id == step.id).order_by(SimulationRecord.created_at.desc()).limit(1))).scalar_one_or_none()
    now = datetime.now(UTC)
    if sim is None or sim.digest != step.digest or not sim.success:
        raise APIError(409, "simulation_mismatch", "No successful simulation for this exact transaction.")
    if (now - sim.created_at).total_seconds() > s.simulation_ttl_seconds:
        raise APIError(409, "simulation_stale", "Simulation is stale. Refresh to re-simulate against current state.")
    expiry = now + timedelta(seconds=s.approval_ttl_seconds)
    if step.quote and step.quote.get("expires_at"):
        q_exp = datetime.fromtimestamp(float(step.quote["expires_at"]), UTC)
        if q_exp <= now:
            raise APIError(409, "quote_expired", "Quote expired. Refresh for a new quote.")
        expiry = min(expiry, q_exp + timedelta(seconds=30))
    await db.execute(update(ApprovalRecord).where(ApprovalRecord.step_id == step.id, ApprovalRecord.used_at.is_(None)).values(invalidated_at=now))
    appr = ApprovalRecord(step_id=step.id, user_id=user.id, digest=step.digest or "", simulation_id=sim.id, expires_at=expiry)
    db.add(appr)
    step.status = ExecStatus.approved
    row.status = ExecStatus.approved
    await db.flush()
    await audit(db, "execution.approved", user_id=user.id, subject=str(step.id), data={"digest": step.digest, "approval": str(appr.id)})
    await db.commit()
    return {"approval_id": str(appr.id), "digest": step.digest, "expires_at": expiry.isoformat(), "chain": row.chain, "unsigned_tx": step.unsigned_tx}


async def settle(db: AsyncSession, user: User, clients: Any, approval_id: uuid.UUID, signed: dict[str, Any]) -> dict[str, Any]:
    appr = await db.get(ApprovalRecord, approval_id)
    if appr is None or appr.user_id != user.id:
        raise APIError(404, "not_found", "Approval not found.")
    step = await db.get(ExecutionStep, appr.step_id)
    assert step is not None
    row = await _owned(db, user, step.intent_id)
    state: ExecState = {"phase": "settle", "approval_id": str(approval_id), "signed": signed, "chain": row.chain, "execution_id": str(row.id), "trace": []}
    out = await graph.run(state, db, user, clients)
    await db.refresh(row)
    if out.get("status") in (ExecStatus.failed, ExecStatus.rejected, ExecStatus.expired):
        step.status = out["status"]
        await db.commit()
    else:
        await advance(db, clients, row)
        await db.refresh(row)
    return await serialize(db, row, trace=out.get("trace"), error=out.get("error"))


async def poll(db: AsyncSession, user: User | None, clients: Any, execution_id: uuid.UUID) -> dict[str, Any]:
    row = await db.get(IntentRow, execution_id)
    if row is None or (user is not None and row.user_id != user.id):
        raise APIError(404, "not_found", "Execution not found.")
    await advance(db, clients, row)
    await db.refresh(row)
    return await serialize(db, row)


async def advance(db: AsyncSession, clients: Any, row: IntentRow) -> None:
    """Check outstanding submissions; after a confirmed approve step, prepare the next step."""
    if row.status != ExecStatus.submitted:
        return
    step = next((s for s in row.steps if s.index == row.current_step), None)
    if step is None:
        return
    sub = (await db.execute(select(TransactionSubmission).where(TransactionSubmission.step_id == step.id))).scalar_one_or_none()
    if sub is None:
        return
    status = await check_submission(db, clients, sub)
    if status in ("submitted", "unverified"):
        await db.commit()
        return
    if status == "confirmed":
        step.status = ExecStatus.confirmed
        plan = _plan_from(row)
        if step.index + 1 < len(plan):
            row.current_step = step.index + 1
            row.status = ExecStatus.preparing
            await db.commit()
            user = await db.get(User, row.user_id)
            assert user is not None
            state: ExecState = {
                "phase": "prepare", "execution_id": str(row.id), "intent": row.intent, "chain": row.chain, "wallet": row.wallet,
                "step_index": step.index + 1, "text": row.raw_text, "trace": [], "steps_plan": plan,
            }
            await graph.run(state, db, user, clients)
            return
        row.status = ExecStatus.confirmed
    else:
        step.status = ExecStatus.failed
        row.status = ExecStatus.failed
        row.status_reason = {"failed": "Transaction reverted on-chain.", "dropped": "Transaction expired before inclusion.", "mismatch": "Broadcast transaction did not match the approval."}.get(status, status)
    await audit(db, "execution.final", user_id=row.user_id, subject=str(row.id), data={"status": row.status, "tx": sub.tx_id})
    await db.commit()


def _plan_from(row: IntentRow) -> list[str]:
    return list((row.research or {}).get("_plan") or [row.kind])


async def cancel(db: AsyncSession, user: User, execution_id: uuid.UUID) -> dict[str, Any]:
    row = await _owned(db, user, execution_id)
    if row.status in (ExecStatus.submitted, ExecStatus.confirmed):
        raise APIError(409, "cannot_cancel", "A submitted transaction cannot be cancelled by the platform.")
    row.status = ExecStatus.cancelled
    for s in row.steps:
        await db.execute(update(ApprovalRecord).where(ApprovalRecord.step_id == s.id, ApprovalRecord.used_at.is_(None)).values(invalidated_at=datetime.now(UTC)))
    await audit(db, "execution.cancelled", user_id=user.id, subject=str(row.id))
    await db.commit()
    return await serialize(db, row)


async def serialize(db: AsyncSession, row: IntentRow, *, trace=None, questions=None, error=None) -> dict[str, Any]:
    steps = []
    for s in sorted(row.steps, key=lambda x: x.index):
        subs = (await db.execute(select(TransactionSubmission).where(TransactionSubmission.step_id == s.id))).scalars().all()
        sub_out = []
        for sub in subs:
            rc = (await db.execute(select(ConfirmedReceipt).where(ConfirmedReceipt.submission_id == sub.id))).scalar_one_or_none()
            sub_out.append({
                "tx_id": sub.tx_id, "status": sub.status, "explorer_url": sub.explorer_url,
                "receipt": None if rc is None else {"success": rc.success, "block_or_slot": rc.block_or_slot, "confirmation": rc.confirmation, "fee_paid": rc.fee_paid},
            })
        steps.append({
            "id": str(s.id), "index": s.index, "kind": s.kind, "status": s.status, "digest": s.digest,
            "preview": s.preview, "unsigned_tx": s.unsigned_tx if s.status == ExecStatus.awaiting_approval else None,
            "submissions": sub_out,
        })
    return {
        "id": str(row.id), "status": row.status, "status_reason": row.status_reason, "chain": row.chain, "wallet": row.wallet,
        "kind": row.kind, "raw_text": row.raw_text, "intent": row.intent, "current_step": row.current_step,
        "created_at": row.created_at.isoformat() if row.created_at else None, "steps": steps,
        "questions": questions or [], "error": error, "trace": trace or [], "server_time": time.time(),
    }
