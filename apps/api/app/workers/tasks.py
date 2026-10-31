"""arq worker: mint indexing, rule evaluation, confirmation tracking.

Run: uv run arq app.workers.tasks.WorkerSettings
"""

from __future__ import annotations

from typing import Any

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select

from app.chains.registry import get_clients
from app.core.config import get_settings
from app.db.models import TransactionIntent
from app.db.session import sessionmaker
from app.execution.service import advance
from app.execution.types import ExecStatus
from app.services import mint as mint_svc
from app.services.monitoring import evaluate_all


async def index_mints(ctx: dict[str, Any]) -> dict[str, int]:
    async with sessionmaker()() as db:
        b = await mint_svc.index_base_mints(db)
        try:
            s = await mint_svc.index_solana_mints(db)
        except Exception:  # noqa: BLE001
            s = 0
    return {"base": b, "solana": s}


async def evaluate_rules(ctx: dict[str, Any]) -> int:
    async with sessionmaker()() as db:
        return await evaluate_all(db, get_clients())


async def track_confirmations(ctx: dict[str, Any]) -> int:
    n = 0
    async with sessionmaker()() as db:
        rows = (await db.execute(select(TransactionIntent).where(TransactionIntent.status == ExecStatus.submitted).limit(100))).scalars().all()
        for row in rows:
            await advance(db, get_clients(), row)
            n += 1
    return n


class WorkerSettings:
    functions = [index_mints, evaluate_rules, track_confirmations]
    cron_jobs = [
        cron(index_mints, second={0}),
        cron(evaluate_rules, second={30}),
        cron(track_confirmations, second={0, 15, 30, 45}),
    ]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 10
    job_timeout = 120
