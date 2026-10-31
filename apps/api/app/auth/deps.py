from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import APIError
from app.core.security import hash_token
from app.db.models import Session, User
from app.db.session import get_db


def _token_from(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(get_settings().session_cookie_name)


async def optional_user(request: Request, db: AsyncSession = Depends(get_db)) -> User | None:
    token = _token_from(request)
    if not token:
        return None
    row = (
        await db.execute(
            select(Session, User)
            .join(User, User.id == Session.user_id)
            .where(Session.token_hash == hash_token(token))
        )
    ).first()
    if not row:
        return None
    sess, user = row
    if sess.revoked_at is not None or sess.expires_at < datetime.now(UTC):
        return None
    request.state.user_id = str(user.id)
    request.state.session_id = str(sess.id)
    return user


async def current_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise APIError(401, "unauthenticated", "Sign in with a wallet to continue.")
    return user


async def admin_user(user: User = Depends(current_user)) -> User:
    admins = {a.lower() for a in get_settings().admin_wallets}
    if not (user.is_admin or any(w.address.lower() in admins for w in user.wallets)):
        raise APIError(403, "forbidden", "Admin only.")
    return user


def user_wallet(user: User, chain: str, address: str) -> bool:
    a = address.lower() if chain == "base" else address
    return any(w.chain == chain and w.address == a for w in user.wallets)
