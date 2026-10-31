"""AI Terminal: saved conversations + streaming (SSE) responses with tool activity and evidence."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.auth.deps import current_user, user_wallet
from app.chains.registry import get_clients
from app.core.errors import APIError
from app.core.ratelimit import rate_limit
from app.db.models import Conversation, Message, User
from app.db.session import get_db
from app.execution import service as exec_service
from app.execution.intent import TX_HINTS, detect_addresses
from app.modules.registry import MODULES
from app.routers.core import invoke_module
from app.services.entitlements import charge

router = APIRouter(prefix="/terminal", tags=["terminal"])


class ConversationIn(BaseModel):
    title: str = Field(default="New session", max_length=160)
    chain: Literal["base", "solana"] = "base"


class PromptIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    chain: Literal["base", "solana"] = "base"
    wallet: str | None = None


@router.post("/conversations")
async def create_conversation(body: ConversationIn, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    c = Conversation(user_id=user.id, title=body.title, chain=body.chain)
    db.add(c)
    await db.commit()
    return {"id": str(c.id), "title": c.title, "chain": c.chain}


@router.get("/conversations")
async def list_conversations(limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    rows = (await db.execute(select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.created_at.desc()).limit(limit).offset(offset))).scalars().all()
    return {"items": [{"id": str(c.id), "title": c.title, "chain": c.chain, "created_at": c.created_at.isoformat()} for c in rows]}


@router.get("/conversations/{cid}/messages")
async def messages(cid: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    c = await db.get(Conversation, cid)
    if c is None or c.user_id != user.id:
        raise APIError(404, "not_found", "Conversation not found.")
    rows = (await db.execute(select(Message).where(Message.conversation_id == cid).order_by(Message.created_at))).scalars().all()
    return {"items": [{"id": str(m.id), "role": m.role, "content": m.content, "payload": m.payload, "created_at": m.created_at.isoformat()} for m in rows]}


def _ev(kind: str, data: dict[str, Any]) -> dict[str, str]:
    return {"event": kind, "data": json.dumps(data, default=str)}


def format_execution(ex: dict[str, Any]) -> str:
    step = ex["steps"][-1] if ex["steps"] else None
    p = (step or {}).get("preview") or {}
    status = ex["status"]
    lines = [
        f"ACTION: {p.get('action') or ex.get('raw_text', '')[:80]}",
        f"NETWORK: {p.get('network') or ex['chain'].capitalize()}",
        f"STATUS: {status.replace('_', ' ').title()}",
        f"RISK: {(p.get('risk') or {}).get('level', 'Not yet assessed').upper() if p else 'Not yet assessed'}",
    ]
    if status == "needs_clarification":
        lines.append("NEXT: " + " ".join(ex.get("questions") or [ex.get("status_reason") or "Clarify the request."]))
    elif status == "awaiting_approval":
        lines.append("NEXT: Review the preview card, then confirm to open your wallet.")
    elif status == "blocked":
        lines.append("NEXT: Blocked by policy — " + (ex.get("status_reason") or ""))
    elif status == "rejected":
        lines.append("NEXT: " + (ex.get("error") or ex.get("status_reason") or "Request rejected."))
    return "\n".join(lines)


@router.post("/conversations/{cid}/stream", dependencies=[Depends(rate_limit("ai"))])
async def stream(cid: uuid.UUID, body: PromptIn, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> EventSourceResponse:
    c = await db.get(Conversation, cid)
    if c is None or c.user_id != user.id:
        raise APIError(404, "not_found", "Conversation not found.")
    db.add(Message(conversation_id=cid, role="user", content=body.prompt, payload={"chain": body.chain}))
    await db.commit()

    async def gen() -> AsyncIterator[dict[str, str]]:
        addrs = detect_addresses(body.prompt)
        if addrs["base"] or addrs["solana"]:
            yield _ev("detected", {"addresses": addrs})
        payload: dict[str, Any] = {}
        text = ""
        try:
            if TX_HINTS.search(body.prompt):
                wallet = body.wallet or next((w.address for w in user.wallets if w.chain == body.chain), None)
                if not wallet or not user_wallet(user, body.chain, wallet):
                    raise APIError(400, "wallet_required", f"Connect and verify a {body.chain} wallet to prepare transactions.")
                await charge(db, user, MODULES["simulator"].credit_cost, "simulator")
                yield _ev("tool_start", {"module": "SIMULATOR", "tool": "execution_graph"})
                async for item in exec_service.start_stream(db, user, get_clients(), text=body.prompt, chain=body.chain, wallet=wallet, conversation_id=cid):
                    if item["type"] == "node":
                        yield _ev("tool", {"module": "SIMULATOR", "node": item["node"], "detail": item["detail"]})
                    else:
                        ex = item["execution"]
                        payload = {"execution_id": ex["id"], "execution": ex}
                        yield _ev("execution", ex)
                yield _ev("tool_end", {"module": "SIMULATOR"})
                text = format_execution(payload["execution"])
            else:
                yield _ev("tool_start", {"module": "INTEL", "tool": "route"})
                out = await invoke_module(db, user, "intel", MODULES["intel"].Input.model_validate({"question": body.prompt, "chain": body.chain}), conversation_id=cid)
                for r in out["data"].get("results", []):
                    yield _ev("tool", {"module": r["tool"].upper(), "node": "run", "detail": r["summary"][:200]})
                yield _ev("evidence", {"evidence": out["evidence"]})
                yield _ev("tool_end", {"module": "INTEL"})
                payload = {"module": "intel", "results": out["data"].get("results", []), "evidence": out["evidence"], "limitations": out.get("limitations", [])}
                text = out["summary"]
        except APIError as e:
            text = f"STATUS: Error\nREASON: {e.message}"
            payload = {"error": {"code": e.code, "message": e.message}}
            yield _ev("error", {"code": e.code, "message": e.message})
        # Stream the (real) response text in chunks.
        for i in range(0, len(text), 48):
            yield _ev("token", {"text": text[i : i + 48]})
        db.add(Message(conversation_id=cid, role="assistant", content=text, payload=payload))
        await db.commit()
        yield _ev("done", {"ok": True})

    return EventSourceResponse(gen(), ping=15)
