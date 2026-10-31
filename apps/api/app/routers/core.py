"""Health, entitlements, settings, mint data, modules, activity, admin."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import admin_user, current_user
from app.chains.registry import get_clients
from app.core.audit import audit, verify_chain
from app.core.chains import pass_cfg
from app.core.config import get_settings
from app.core.errors import APIError
from app.core.ratelimit import get_redis, rate_limit
from app.db.models import Alert, AuditEvent, SystemFlag, TokenScan, ToolCall, User
from app.db.models import TransactionIntent as IntentRow
from app.db.session import get_db
from app.modules.base import ModuleContext
from app.modules.registry import MODULES
from app.services import mint as mint_svc
from app.services.entitlements import charge, entitlements

router = APIRouter()


# ---------------------------------------------------------------------------- health
@router.get("/health", tags=["health"])
async def health() -> dict[str, Any]:
    return {"status": "ok", "time": time.time()}


@router.get("/health/ready", tags=["health"])
async def ready(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["database"] = f"error: {type(e).__name__}"
    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["redis"] = f"error: {type(e).__name__}"
    s = get_settings()
    checks["network_mode"] = s.network_mode
    checks["mainnet_execution_enabled"] = s.mainnet_execution_enabled
    checks["base_pass_configured"] = bool(s.base_pass_address)
    checks["llm_configured"] = bool(s.openai_api_key)
    ok = checks["database"] == "ok"
    if not ok:
        raise APIError(503, "not_ready", "Dependencies unavailable", checks)
    return {"status": "ready", "checks": checks}


# ---------------------------------------------------------------------------- entitlements / settings
@router.get("/me/entitlements", tags=["entitlements"])
async def my_entitlements(refresh: bool = False, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    v = await entitlements(db, user, force=refresh)
    return {**v.__dict__, "policy": {
        "credits_per_pass": pass_cfg()["creditsPerPassPerPeriod"], "max_passes_counted": pass_cfg()["maxPassesCountedForCredits"],
        "note": "Passes are unlimited in supply. Usage credits are capped per period under the fair-use policy; extra passes beyond the cap do not add credits.",
    }}


class UserSettingsIn(BaseModel):
    max_tx_usd: float | None = Field(default=None, gt=0)
    daily_limit_usd: float | None = Field(default=None, gt=0)
    max_slippage_bps: int | None = Field(default=None, ge=1, le=5000)
    allow_unlimited_approvals: bool | None = None


@router.get("/me/settings", tags=["settings"])
async def get_settings_(user: User = Depends(current_user)) -> dict[str, Any]:
    s = get_settings()
    return {"settings": user.settings or {}, "platform_limits": {"max_tx_usd": s.default_max_tx_usd, "daily_limit_usd": s.default_daily_limit_usd, "max_slippage_bps": s.max_slippage_bps}}


@router.put("/me/settings", tags=["settings"])
async def put_settings(body: UserSettingsIn, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    s = get_settings()
    cur = dict(user.settings or {})
    data = body.model_dump(exclude_none=True)
    # Users can only tighten platform limits.
    if "max_tx_usd" in data:
        data["max_tx_usd"] = min(data["max_tx_usd"], s.default_max_tx_usd)
    if "daily_limit_usd" in data:
        data["daily_limit_usd"] = min(data["daily_limit_usd"], s.default_daily_limit_usd)
    if "max_slippage_bps" in data:
        data["max_slippage_bps"] = min(data["max_slippage_bps"], s.max_slippage_bps)
    cur.update(data)
    user.settings = cur
    await audit(db, "settings.updated", user_id=user.id, data=data)
    await db.commit()
    return {"settings": cur}


# ---------------------------------------------------------------------------- mint
@router.get("/mint/supply", tags=["mint"])
async def supply() -> dict[str, Any]:
    base, sol = await mint_svc.base_supply(), await mint_svc.solana_supply()
    total = sum(x.get("total_minted", 0) for x in (base, sol) if x.get("available"))
    return {"price_usdc": pass_cfg()["priceDisplay"], "max_batch": pass_cfg()["maxBatch"], "aggregate_cap": None, "chains": [base, sol], "total_verified": total}


@router.get("/mint/config", tags=["mint"])
async def mint_config() -> dict[str, Any]:
    from app.services.entitlements import solana_config_pda, solana_pass_mint

    s = get_settings()
    return {
        "network_mode": s.network_mode, "price_base_units": pass_cfg()["priceUsdcBaseUnits"], "max_batch": pass_cfg()["maxBatch"],
        "base": {"pass_address": s.base_pass_address or None},
        "solana": {"program_id": s.solana_pass_program_id, "config_pda": solana_config_pda(), "pass_mint": solana_pass_mint()},
    }


@router.get("/mint/history", tags=["mint"])
async def history(
    chain: Literal["base", "solana"] | None = None, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    items = await mint_svc.mint_history(db, chain, [], limit, offset)
    return {"items": items, "limit": limit, "offset": offset}


@router.get("/me/mints", tags=["mint"])
async def my_mints(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    items = await mint_svc.mint_history(db, None, [w.address for w in user.wallets], limit, offset)
    return {"items": items, "limit": limit, "offset": offset}


# ---------------------------------------------------------------------------- modules
@router.get("/modules", tags=["modules"])
async def list_modules() -> dict[str, Any]:
    return {"modules": [m.describe() for m in MODULES.values()]}


@router.post("/modules/{name}/run", tags=["modules"], dependencies=[Depends(rate_limit("ai"))])
async def run_module(name: str, body: dict[str, Any], db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    mod = MODULES.get(name)
    if mod is None:
        raise APIError(404, "unknown_module", f"Unknown module {name}.")
    inp = mod.Input.model_validate(body)
    return await invoke_module(db, user, name, inp)


async def invoke_module(db: AsyncSession, user: User, name: str, inp: Any, conversation_id: uuid.UUID | None = None) -> dict[str, Any]:
    mod = MODULES[name]
    view = await charge(db, user, mod.credit_cost, name)
    t0 = time.time()
    call = ToolCall(user_id=user.id, conversation_id=conversation_id, module=name, tool="run", input=inp.model_dump(mode="json"), status="ok", credits=mod.credit_cost)
    try:
        out = await mod.run(ModuleContext(db=db, user=user, clients=get_clients(), conversation_id=conversation_id), inp)
    except APIError as e:
        call.status, call.error = "error", f"{e.code}: {e.message}"
        call.duration_ms = int((time.time() - t0) * 1000)
        db.add(call)
        await db.commit()
        raise
    call.output = {"summary": out.summary}
    call.evidence = [e.model_dump() for e in out.evidence]
    call.duration_ms = int((time.time() - t0) * 1000)
    db.add(call)
    await db.commit()
    return {**out.model_dump(mode="json"), "credits": {"charged": mod.credit_cost, "remaining": view.credits_remaining}}


@router.get("/scans", tags=["modules"])
async def scans(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    rows = (await db.execute(select(TokenScan).where(TokenScan.user_id == user.id).order_by(TokenScan.created_at.desc()).limit(limit).offset(offset))).scalars().all()
    return {"items": [{"id": str(r.id), "chain": r.chain, "token": r.token, "risk_level": r.risk_level, "created_at": r.created_at.isoformat()} for r in rows]}


# ---------------------------------------------------------------------------- activity
@router.get("/activity", tags=["activity"])
async def activity(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    rows = (await db.execute(select(IntentRow).where(IntentRow.user_id == user.id).order_by(IntentRow.created_at.desc()).limit(limit).offset(offset))).scalars().all()
    total = (await db.execute(select(func.count()).select_from(IntentRow).where(IntentRow.user_id == user.id))).scalar()
    from app.execution.service import serialize

    return {"items": [await serialize(db, r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/alerts", tags=["activity"])
async def alerts(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    rows = (await db.execute(select(Alert).where(Alert.user_id == user.id).order_by(Alert.created_at.desc()).limit(limit).offset(offset))).scalars().all()
    return {"items": [{"id": str(a.id), "severity": a.severity, "title": a.title, "body": a.body, "read": a.read_at is not None, "created_at": a.created_at.isoformat()} for a in rows]}


@router.get("/audit", tags=["activity"])
async def my_audit(limit: int = Query(50, ge=1, le=200), db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    rows = (await db.execute(select(AuditEvent).where(AuditEvent.user_id == user.id).order_by(AuditEvent.id.desc()).limit(limit))).scalars().all()
    return {"items": [{"id": r.id, "at": r.at.isoformat(), "action": r.action, "subject": r.subject, "hash": r.hash} for r in rows]}


# ---------------------------------------------------------------------------- admin / kill switch
class FlagIn(BaseModel):
    enabled: bool
    reason: str = Field(min_length=3, max_length=500)


@router.get("/flags", tags=["admin"])
async def flags(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = (await db.execute(select(SystemFlag))).scalars().all()
    return {"flags": {r.key: {"enabled": r.enabled, "reason": r.reason} for r in rows}}


@router.put("/admin/flags/{key}", tags=["admin"])
async def set_flag(key: Literal["execution_enabled", "mint_ui_enabled", "automations_enabled"], body: FlagIn, db: AsyncSession = Depends(get_db), user: User = Depends(admin_user)) -> dict[str, Any]:
    row = await db.get(SystemFlag, key)
    if row is None:
        row = SystemFlag(key=key, enabled=body.enabled, reason=body.reason)
        db.add(row)
    else:
        row.enabled, row.reason, row.updated_at = body.enabled, body.reason, datetime.now(UTC)
    await audit(db, "admin.flag", user_id=user.id, subject=key, data=body.model_dump())
    await db.commit()
    return {"key": key, "enabled": body.enabled}


@router.get("/admin/audit/verify", tags=["admin"])
async def audit_verify(db: AsyncSession = Depends(get_db), user: User = Depends(admin_user)) -> dict[str, Any]:
    ok, bad = await verify_chain(db)
    return {"intact": ok, "first_bad_id": bad}
