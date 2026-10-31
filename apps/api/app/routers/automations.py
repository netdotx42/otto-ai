from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.core import idempotency
from app.core.audit import audit
from app.core.errors import APIError
from app.core.ratelimit import rate_limit
from app.db.models import MarketplaceTemplate, MonitoringRule, User, Workflow, WorkflowVersion
from app.db.session import get_db
from app.services import workflows as wf_svc
from app.services.entitlements import entitlements

router = APIRouter(tags=["automations"], dependencies=[Depends(rate_limit("default"))])


class WorkflowIn(BaseModel):
    definition: dict[str, Any]


class StatusIn(BaseModel):
    status: Literal["active", "paused", "revoked"]


async def _require_pass(db: AsyncSession, user: User) -> None:
    v = await entitlements(db, user)
    if not v.has_access:
        raise APIError(403, "pass_required", "Automations require a verified AGENT//98 pass.")


def _wf_out(w: Workflow) -> dict[str, Any]:
    return {"id": str(w.id), "name": w.name, "status": w.status, "version": w.current_version, "template_id": str(w.template_id) if w.template_id else None,
            "runs_today": w.runs_today, "last_run_at": w.last_run_at.isoformat() if w.last_run_at else None, "created_at": w.created_at.isoformat()}


@router.get("/workflows")
async def list_workflows(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    rows = (await db.execute(select(Workflow).where(Workflow.user_id == user.id).order_by(Workflow.created_at.desc()).limit(limit).offset(offset))).scalars().all()
    return {"items": [_wf_out(w) for w in rows]}


@router.post("/workflows/validate")
async def validate(body: WorkflowIn) -> dict[str, Any]:
    d, errs = wf_svc.validate_definition(body.definition)
    return {"valid": d is not None, "errors": errs, "normalized": d.model_dump(mode="json") if d else None}


@router.post("/workflows")
async def create(body: WorkflowIn, db: AsyncSession = Depends(get_db), user: User = Depends(current_user), idempotency_key: str | None = Header(default=None)) -> dict[str, Any]:
    replay = await idempotency.begin(db, user.id, idempotency_key, "POST /workflows", body.definition)
    if replay is not None:
        return replay
    await _require_pass(db, user)
    wf = await wf_svc.create_or_update(db, user, body.definition)
    out = _wf_out(wf)
    await idempotency.finish(db, user.id, idempotency_key, 200, out)
    return out


@router.get("/workflows/{wid}")
async def get_workflow(wid: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    wf = await db.get(Workflow, wid)
    if wf is None or wf.user_id != user.id:
        raise APIError(404, "not_found", "Workflow not found.")
    versions = (await db.execute(select(WorkflowVersion).where(WorkflowVersion.workflow_id == wid).order_by(WorkflowVersion.version.desc()))).scalars().all()
    rules = (await db.execute(select(MonitoringRule).where(MonitoringRule.workflow_id == wid))).scalars().all()
    return {**_wf_out(wf), "versions": [{"version": v.version, "hash": v.definition_hash, "definition": v.definition, "created_at": v.created_at.isoformat()} for v in versions],
            "rules": [{"id": str(r.id), "kind": r.kind, "enabled": r.enabled, "last_checked_at": r.last_checked_at.isoformat() if r.last_checked_at else None} for r in rules]}


@router.put("/workflows/{wid}")
async def update_workflow(wid: uuid.UUID, body: WorkflowIn, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    await _require_pass(db, user)
    return _wf_out(await wf_svc.create_or_update(db, user, body.definition, workflow_id=wid))


@router.post("/workflows/{wid}/status")
async def set_status(wid: uuid.UUID, body: StatusIn, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    return _wf_out(await wf_svc.set_status(db, user, wid, body.status))


# ---------------------------------------------------------------------------- marketplace
class TemplateIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]{3,80}$")
    name: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=10, max_length=2000)
    module: Literal["sentinel", "forensic", "whale", "alpha", "autopilot"]
    definition: dict[str, Any]


def _tpl_out(t: MarketplaceTemplate) -> dict[str, Any]:
    return {"id": str(t.id), "slug": t.slug, "name": t.name, "description": t.description, "module": t.module, "version": t.version,
            "hash": t.definition_hash, "installs": t.installs, "official": t.official, "status": t.status, "definition": t.definition}


@router.get("/marketplace/templates")
async def templates(module: str | None = None, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    q = select(MarketplaceTemplate).where(MarketplaceTemplate.status == "published")
    if module:
        q = q.where(MarketplaceTemplate.module == module)
    rows = (await db.execute(q.order_by(MarketplaceTemplate.official.desc(), MarketplaceTemplate.installs.desc()).limit(limit).offset(offset))).scalars().all()
    return {"items": [_tpl_out(t) for t in rows]}


@router.post("/marketplace/templates")
async def publish(body: TemplateIn, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    await _require_pass(db, user)
    # Templates are validated with placeholder addresses; installers bind their own.
    d, errs = wf_svc.validate_definition(body.definition)
    if d is None:
        raise APIError(422, "invalid_template", "Template definition invalid.", errs)
    if any(a.type == "prepare_transaction" for a in d.actions):
        raise APIError(422, "invalid_template", "Community templates may not include prepare_transaction actions.")
    h = hashlib.sha256(json.dumps(d.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
    t = MarketplaceTemplate(author_id=user.id, slug=body.slug, name=body.name, description=body.description, module=body.module, definition=d.model_dump(mode="json"), definition_hash=h, status="published")
    db.add(t)
    await audit(db, "marketplace.publish", user_id=user.id, subject=body.slug, data={"hash": h})
    await db.commit()
    return _tpl_out(t)


class InstallIn(BaseModel):
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$", description="Pins the exact template version the user reviewed")
    overrides: dict[str, Any] = Field(default_factory=dict, description="trigger fields to bind, e.g. wallet/token")
    name: str | None = None


@router.post("/marketplace/templates/{tid}/install")
async def install(tid: uuid.UUID, body: InstallIn, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    t = await db.get(MarketplaceTemplate, tid)
    if t is None or t.status != "published":
        raise APIError(404, "not_found", "Template not found.")
    if t.definition_hash != body.expected_hash:
        raise APIError(409, "template_changed", "Template changed since you reviewed it.")
    await _require_pass(db, user)
    definition = json.loads(json.dumps(t.definition))
    allowed = {"wallet", "token", "address", "min_amount", "threshold", "chain"}
    for k, v in body.overrides.items():
        if k not in allowed:
            raise APIError(422, "invalid_override", f"Field {k} cannot be overridden.")
        definition["trigger"][k] = v
    if body.name:
        definition["name"] = body.name
    wf = await wf_svc.create_or_update(db, user, definition, template_id=t.id)
    await db.execute(update(MarketplaceTemplate).where(MarketplaceTemplate.id == t.id).values(installs=MarketplaceTemplate.installs + 1))
    await db.commit()
    return _wf_out(wf)
