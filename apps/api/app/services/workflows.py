"""No-code workflow DSL (BUILDER), versioning, compilation to monitoring rules, and evaluation.

Workflows can only use allowlisted triggers and actions. The strongest action,
`prepare_transaction`, creates an execution that STOPS at awaiting_approval; workflows have no
path to signing or broadcasting. There is no autonomous fund movement.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.messages import normalize_address
from app.core.audit import audit
from app.core.errors import APIError
from app.db.models import Alert, MonitoringRule, User, Workflow, WorkflowVersion


class WalletLargeTransfer(BaseModel):
    type: Literal["wallet_large_transfer"]
    chain: Literal["base", "solana"]
    wallet: str
    token: str | None = Field(default=None, description="Base: ERC-20 address (required). Solana: optional mint filter.")
    min_amount: Decimal = Field(gt=0)


class BalanceBelow(BaseModel):
    type: Literal["balance_below"]
    chain: Literal["base", "solana"]
    wallet: str
    token: str = "native"
    threshold: Decimal = Field(ge=0)


class ContractCodeChange(BaseModel):
    type: Literal["contract_code_change"]
    chain: Literal["base"] = "base"
    address: str


Trigger = Annotated[WalletLargeTransfer | BalanceBelow | ContractCodeChange, Field(discriminator="type")]


class AlertAction(BaseModel):
    type: Literal["alert"]
    severity: Literal["info", "warning", "critical"] = "info"


class ForensicAction(BaseModel):
    type: Literal["forensic_scan"]


class PrepareTxAction(BaseModel):
    """Prepares (never signs) a transaction. Requires explicit user approval in the app."""

    type: Literal["prepare_transaction"]
    request: str = Field(max_length=300)
    wallet: str


Action = Annotated[AlertAction | ForensicAction | PrepareTxAction, Field(discriminator="type")]


class Limits(BaseModel):
    max_runs_per_day: int = Field(default=24, ge=1, le=288)


class WorkflowDefinition(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    trigger: Trigger
    actions: list[Action] = Field(min_length=1, max_length=5)
    limits: Limits = Field(default_factory=Limits)


ALLOWED_ACTIONS = {"alert", "forensic_scan", "prepare_transaction"}
TRIGGER_TO_RULE = {"wallet_large_transfer": "wallet_large_transfer", "balance_below": "balance_below", "contract_code_change": "contract_code_change"}


def validate_definition(raw: dict[str, Any]) -> tuple[WorkflowDefinition | None, list[str]]:
    try:
        d = WorkflowDefinition.model_validate(raw)
    except ValidationError as e:
        return None, [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]
    errs: list[str] = []
    t = d.trigger
    try:
        if isinstance(t, ContractCodeChange):
            normalize_address("base", t.address)
        else:
            normalize_address(t.chain, t.wallet)
            if isinstance(t, WalletLargeTransfer) and t.chain == "base" and not t.token:
                errs.append("trigger.token: Base large-transfer triggers require an ERC-20 token address.")
    except ValueError as e:
        errs.append(f"trigger: {e}")
    for a in d.actions:
        if isinstance(a, PrepareTxAction):
            chain = getattr(t, "chain", "base")
            try:
                normalize_address(chain, a.wallet)
            except ValueError as e:
                errs.append(f"actions.prepare_transaction.wallet: {e}")
    return (d if not errs else None), errs


def definition_hash(d: WorkflowDefinition) -> str:
    return hashlib.sha256(json.dumps(d.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


def _rule_from(d: WorkflowDefinition) -> dict[str, Any]:
    t = d.trigger
    if isinstance(t, ContractCodeChange):
        return {"kind": "contract_code_change", "chain": "base", "target": normalize_address("base", t.address), "params": {}}
    target = normalize_address(t.chain, t.wallet)
    if isinstance(t, WalletLargeTransfer):
        return {"kind": "wallet_large_transfer", "chain": t.chain, "target": target, "params": {"token": t.token, "min_amount": str(t.min_amount)}}
    return {"kind": "balance_below", "chain": t.chain, "target": target, "params": {"token": t.token, "threshold": str(t.threshold)}}


async def create_or_update(db: AsyncSession, user: User, raw: dict[str, Any], workflow_id: uuid.UUID | None = None, template_id: uuid.UUID | None = None) -> Workflow:
    d, errs = validate_definition(raw)
    if d is None:
        raise APIError(422, "invalid_workflow", "Workflow definition is invalid.", errs)
    for a in d.actions:
        if isinstance(a, PrepareTxAction):
            if not any(w.address == normalize_address(getattr(d.trigger, "chain", "base"), a.wallet) for w in user.wallets):
                raise APIError(403, "wallet_not_verified", "prepare_transaction must target one of your verified wallets.")
    if workflow_id:
        wf = await db.get(Workflow, workflow_id)
        if wf is None or wf.user_id != user.id:
            raise APIError(404, "not_found", "Workflow not found.")
        if wf.status == "revoked":
            raise APIError(409, "revoked", "Revoked workflows cannot be edited.")
        wf.current_version += 1
        wf.name = d.name
    else:
        wf = Workflow(user_id=user.id, name=d.name, current_version=1, template_id=template_id)
        db.add(wf)
        await db.flush()
    db.add(WorkflowVersion(workflow_id=wf.id, version=wf.current_version, definition=d.model_dump(mode="json"), definition_hash=definition_hash(d)))
    # Replace compiled rules for this workflow.
    old = (await db.execute(select(MonitoringRule).where(MonitoringRule.workflow_id == wf.id))).scalars().all()
    for r in old:
        r.enabled = False
    spec = _rule_from(d)
    db.add(MonitoringRule(user_id=user.id, workflow_id=wf.id, enabled=wf.status == "active", **spec))
    await audit(db, "workflow.saved", user_id=user.id, subject=str(wf.id), data={"version": wf.current_version, "hash": definition_hash(d)})
    await db.commit()
    await db.refresh(wf)
    return wf


async def set_status(db: AsyncSession, user: User, workflow_id: uuid.UUID, status: Literal["active", "paused", "revoked"]) -> Workflow:
    wf = await db.get(Workflow, workflow_id)
    if wf is None or wf.user_id != user.id:
        raise APIError(404, "not_found", "Workflow not found.")
    if wf.status == "revoked":
        raise APIError(409, "revoked", "Workflow is revoked.")
    wf.status = status
    rules = (await db.execute(select(MonitoringRule).where(MonitoringRule.workflow_id == wf.id))).scalars().all()
    latest = max(rules, key=lambda r: r.created_at, default=None)
    for r in rules:
        r.enabled = status == "active" and r is latest
    await audit(db, f"workflow.{status}", user_id=user.id, subject=str(wf.id))
    await db.commit()
    return wf


async def run_actions(db: AsyncSession, user: User, clients: Any, wf: Workflow, rule: MonitoringRule, event: dict[str, Any]) -> list[dict[str, Any]]:
    """Execute a workflow's actions for one trigger event (bounded by limits + entitlement)."""
    from app.execution import service as exec_service
    from app.services.entitlements import charge
    from app.services.token_risk import scan_base_token, scan_solana_mint

    ver = (await db.execute(select(WorkflowVersion).where(WorkflowVersion.workflow_id == wf.id, WorkflowVersion.version == wf.current_version))).scalar_one()
    d = WorkflowDefinition.model_validate(ver.definition)
    today = datetime.now(UTC).date()
    if wf.last_run_at and wf.last_run_at.date() != today:
        wf.runs_today = 0
    if wf.runs_today >= d.limits.max_runs_per_day:
        return [{"action": "limit", "status": "skipped", "reason": "max_runs_per_day reached"}]
    try:
        await charge(db, user, 2, "autopilot", ref=str(wf.id))
    except APIError as e:
        db.add(Alert(user_id=user.id, rule_id=rule.id, severity="warning", title=f"Workflow '{wf.name}' paused: {e.code}", body={"message": e.message}))
        wf.status = "paused"
        await db.commit()
        return [{"action": "entitlement", "status": "denied", "reason": e.code}]
    wf.runs_today += 1
    wf.last_run_at = datetime.now(UTC)
    results: list[dict[str, Any]] = []
    body: dict[str, Any] = {"event": event, "workflow": str(wf.id), "version": wf.current_version}
    for a in d.actions:
        if a.type not in ALLOWED_ACTIONS:  # defense in depth
            continue
        if isinstance(a, ForensicAction) and event.get("token"):
            tok = event["token"]
            rep = await (scan_base_token(clients.evm, tok) if rule.chain == "base" else scan_solana_mint(clients.solana, tok))
            body["forensic"] = {"risk_level": rep["risk_level"], "summary": rep["summary"], "findings": rep["findings"][:10]}
            results.append({"action": "forensic_scan", "status": "ok", "risk_level": rep["risk_level"]})
        elif isinstance(a, PrepareTxAction):
            ex = await exec_service.start(db, user, clients, text=a.request, chain=rule.chain, wallet=a.wallet, workflow_id=wf.id)
            body["execution"] = {"id": ex["id"], "status": ex["status"]}
            results.append({"action": "prepare_transaction", "status": ex["status"], "execution_id": ex["id"]})
    sev = next((a.severity for a in d.actions if isinstance(a, AlertAction)), "info")
    title = f"{wf.name}: {event.get('title', rule.kind)}"
    if "execution" in body:
        title += " — transaction prepared, awaiting your approval"
    db.add(Alert(user_id=user.id, rule_id=rule.id, severity=sev, title=title[:200], body=body))
    await audit(db, "workflow.run", user_id=user.id, subject=str(wf.id), data={"results": results})
    await db.commit()
    return results
