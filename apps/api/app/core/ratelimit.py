"""Fixed-window rate limiting backed by Redis, with an in-process fallback if Redis is down."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable

import redis.asyncio as aioredis
from fastapi import Request

from app.core.config import get_settings
from app.core.errors import APIError

_redis: aioredis.Redis | None = None
_memory: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


def set_redis(client: aioredis.Redis | None) -> None:
    global _redis
    _redis = client


async def hit(key: str, limit: int, window: int) -> tuple[bool, int]:
    """Returns (allowed, remaining)."""
    bucket = f"rl:{key}:{int(time.time() // window)}"
    try:
        r = get_redis()
        count = await r.incr(bucket)
        if count == 1:
            await r.expire(bucket, window + 1)
    except Exception:  # noqa: BLE001 - degrade to local limiter, never to "unlimited"
        c, _ = _memory[bucket]
        count = c + 1
        _memory[bucket] = (count, time.time())
    return count <= limit, max(0, limit - count)


def rate_limit(group: str) -> Callable:
    async def dep(request: Request) -> None:
        s = get_settings()
        limit = {
            "auth": s.rate_limit_auth,
            "ai": s.rate_limit_ai,
            "execution": s.rate_limit_execution,
        }.get(group, s.rate_limit_default)
        user = getattr(request.state, "user_id", None)
        ident = f"u:{user}" if user else f"ip:{request.client.host if request.client else 'unknown'}"
        ok, _remaining = await hit(f"{group}:{ident}", limit, s.rate_limit_window_seconds)
        if not ok:
            raise APIError(429, "rate_limited", f"Rate limit exceeded for {group}; retry shortly.")

    return dep
