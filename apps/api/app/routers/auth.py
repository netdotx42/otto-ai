from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from eth_abi import encode
from eth_account.messages import _hash_eip191_message, encode_defunct
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user, optional_user
from app.auth.messages import (
    EIP1271_MAGIC,
    build_siwe,
    build_siws,
    normalize_address,
    recover_evm_signer,
    verify_solana_signature,
)
from app.chains.evm import RpcError, selector
from app.chains.registry import get_clients
from app.core.audit import audit
from app.core.chains import base_chain_id, solana_cfg
from app.core.config import get_settings
from app.core.errors import APIError
from app.core.ratelimit import rate_limit
from app.core.security import hash_token, new_nonce, new_token
from app.db.models import AuthNonce, Session, User, Wallet
from app.db.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


class NonceRequest(BaseModel):
    chain: Literal["base", "solana"]
    address: str = Field(min_length=20, max_length=64)


class NonceResponse(BaseModel):
    nonce: str
    message: str
    expires_at: datetime


class VerifyRequest(BaseModel):
    chain: Literal["base", "solana"]
    address: str
    nonce: str = Field(min_length=8, max_length=64)
    signature: str = Field(min_length=10, max_length=4096)


class WalletOut(BaseModel):
    chain: str
    address: str
    verified_at: datetime


class SessionOut(BaseModel):
    user_id: str
    wallets: list[WalletOut]
    expires_at: datetime | None = None
    token: str | None = None  # returned once for non-browser API clients


@router.post("/nonce", response_model=NonceResponse, dependencies=[Depends(rate_limit("auth"))])
async def nonce(body: NonceRequest, db: AsyncSession = Depends(get_db)) -> NonceResponse:
    s = get_settings()
    try:
        addr = normalize_address(body.chain, body.address)
    except ValueError as e:
        raise APIError(400, "invalid_address", str(e)) from e
    now = datetime.now(UTC).replace(microsecond=0)
    exp = now + timedelta(seconds=s.nonce_ttl_seconds)
    n = new_nonce()
    if body.chain == "base":
        msg = build_siwe(s.app_domain, s.app_origin, addr, base_chain_id(), n, now, exp)
    else:
        msg = build_siws(s.app_domain, s.app_origin, addr, solana_cfg()["cluster"], n, now, exp)
    db.add(AuthNonce(nonce=n, chain=body.chain, address=addr, message=msg, expires_at=exp))
    await db.commit()
    return NonceResponse(nonce=n, message=msg, expires_at=exp)


async def _verify_eip1271(address: str, message: str, signature: str) -> bool:
    """Smart-contract wallets (e.g. Coinbase Smart Wallet once deployed) via EIP-1271."""
    digest = _hash_eip191_message(encode_defunct(text=message))
    sig = bytes.fromhex(signature[2:] if signature.startswith("0x") else signature)
    data = "0x" + (selector("isValidSignature(bytes32,bytes)") + encode(["bytes32", "bytes"], [digest, sig])).hex()
    try:
        out = await get_clients().evm.call({"to": address, "data": data})
    except (RpcError, APIError):
        return False
    return isinstance(out, str) and out[:10].lower() == EIP1271_MAGIC


@router.post("/verify", response_model=SessionOut, dependencies=[Depends(rate_limit("auth"))])
async def verify(
    body: VerifyRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    existing: User | None = Depends(optional_user),
) -> SessionOut:
    s = get_settings()
    try:
        addr = normalize_address(body.chain, body.address)
    except ValueError as e:
        raise APIError(400, "invalid_address", str(e)) from e

    now = datetime.now(UTC)
    # Atomically consume the nonce (single use, unexpired, bound to chain+address).
    consumed = (
        await db.execute(
            update(AuthNonce)
            .where(
                AuthNonce.nonce == body.nonce,
                AuthNonce.chain == body.chain,
                AuthNonce.address == addr,
                AuthNonce.used_at.is_(None),
                AuthNonce.expires_at > now,
            )
            .values(used_at=now)
            .returning(AuthNonce.message)
        )
    ).scalar()
    if consumed is None:
        await db.rollback()
        raise APIError(401, "invalid_nonce", "Nonce is invalid, expired or already used.")

    ok = False
    if body.chain == "base":
        signer = recover_evm_signer(consumed, body.signature)
        ok = signer == addr or await _verify_eip1271(addr, consumed, body.signature)
    else:
        ok = verify_solana_signature(consumed, addr, body.signature)
    if not ok:
        await db.commit()  # nonce stays burned
        raise APIError(401, "invalid_signature", "Signature does not match the wallet.")

    wallet = (
        await db.execute(select(Wallet).where(Wallet.chain == body.chain, Wallet.address == addr))
    ).scalar_one_or_none()
    if wallet is None:
        user = existing or User(settings={})
        if existing is None:
            db.add(user)
            await db.flush()
        wallet = Wallet(user_id=user.id, chain=body.chain, address=addr)
        db.add(wallet)
    else:
        if existing is not None and wallet.user_id != existing.id:
            raise APIError(409, "wallet_linked_elsewhere", "This wallet is linked to a different account.")
        wallet.verified_at = now
    await db.flush()
    user_id = wallet.user_id

    token = new_token()
    exp = now + timedelta(hours=s.session_ttl_hours)
    db.add(
        Session(
            user_id=user_id, token_hash=hash_token(token), expires_at=exp,
            user_agent=(request.headers.get("user-agent") or "")[:256],
        )
    )
    await audit(db, "auth.login", user_id=user_id, subject=f"{body.chain}:{addr}")
    await db.commit()

    response.set_cookie(
        s.session_cookie_name, token, httponly=True, secure=s.cookie_secure, samesite="lax",
        max_age=s.session_ttl_hours * 3600, path="/",
    )
    wallets = (await db.execute(select(Wallet).where(Wallet.user_id == user_id))).scalars().all()
    return SessionOut(
        user_id=str(user_id),
        wallets=[WalletOut(chain=w.chain, address=w.address, verified_at=w.verified_at) for w in wallets],
        expires_at=exp,
        token=token,
    )


@router.get("/session", response_model=SessionOut)
async def session(user: User = Depends(current_user)) -> SessionOut:
    return SessionOut(
        user_id=str(user.id),
        wallets=[WalletOut(chain=w.chain, address=w.address, verified_at=w.verified_at) for w in user.wallets],
    )


@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> dict[str, bool]:
    sid = getattr(request.state, "session_id", None)
    if sid:
        await db.execute(update(Session).where(Session.id == sid).values(revoked_at=datetime.now(UTC)))
        await audit(db, "auth.logout", user_id=user.id)
        await db.commit()
    response.delete_cookie(get_settings().session_cookie_name, path="/")
    return {"ok": True}
