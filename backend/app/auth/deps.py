"""Cookie sessions + request identity.

Session model: the browser holds a random 256-bit token in an httpOnly
``eb_session`` cookie; ``user_sessions.token_hash`` stores only its
SHA-256.  ``authenticate_request`` resolves the cookie on its OWN short
DB session (identity tables carry no RLS and must be readable before a
tenant is known), returning an ``Identity``.  ``bind_identity`` then sets
the tenant/user context and the workspace credential bundle for the
rest of the request.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.database import get_identity
from app.models.identity import (
    Membership,
    MembershipRole,
    Tenant,
    TenantStatus,
    User,
    UserSession,
)
from app.tenancy.context import current_tenant_id, current_user_id

SESSION_COOKIE_NAME = "eb_session"
# Sliding "last seen" is written at most this often (avoids a write per request).
_LAST_SEEN_RESOLUTION = timedelta(minutes=5)


@dataclass(frozen=True)
class Identity:
    user: User
    tenant: Tenant
    membership: Membership
    session_id: uuid.UUID | None = None

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.tenant.id

    @property
    def role(self) -> MembershipRole:
        return self.membership.role

    @property
    def is_manager(self) -> bool:
        return self.membership.is_manager


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def new_token() -> str:
    """256-bit URL-safe random token."""
    return secrets.token_urlsafe(32)


def session_expiry() -> datetime:
    return _now() + timedelta(days=settings.SESSION_TTL_DAYS)


def set_session_cookie(response, raw_token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        raw_token,
        max_age=settings.SESSION_TTL_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.SESSION_COOKIE_SECURE,
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


async def create_session(
    db: AsyncSession,
    *,
    user: User,
    tenant_id: uuid.UUID,
    request: Request | None = None,
) -> str:
    """Insert a session row and return the raw cookie token (caller commits
    and sets the cookie)."""
    raw = new_token()
    db.add(UserSession(
        token_hash=hash_token(raw),
        user_id=user.id,
        tenant_id=tenant_id,
        expires_at=session_expiry(),
        last_seen_at=_now(),
        ip=(request.client.host if request is not None and request.client else None),
        user_agent=(request.headers.get("user-agent") if request is not None else None),
    ))
    return raw


async def load_live_session(db: AsyncSession, raw_token: str) -> tuple[UserSession, User] | None:
    row = (
        await db.execute(
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(
                UserSession.token_hash == hash_token(raw_token),
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > _now(),
            )
        )
    ).first()
    return (row[0], row[1]) if row else None


async def authenticate_request(
    request: Request, factory: async_sessionmaker[AsyncSession],
) -> Identity:
    """Resolve the session cookie to an Identity.  401 without a live
    session / membership; 403 when the workspace is suspended."""
    raw = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw:
        raise HTTPException(status_code=401, detail="not authenticated")
    async with factory() as db:
        pair = await load_live_session(db, raw)
        if pair is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess, user = pair
        tenant = await db.get(Tenant, sess.tenant_id)
        membership = (
            await db.execute(
                select(Membership).where(
                    Membership.tenant_id == sess.tenant_id,
                    Membership.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if tenant is None or membership is None:
            # Removed from the workspace after the session was issued.
            raise HTTPException(status_code=401, detail="not authenticated")
        if tenant.status != TenantStatus.ACTIVE:
            raise HTTPException(status_code=403, detail=f"workspace {tenant.status.value}")
        now = _now()
        if sess.last_seen_at is None or now - sess.last_seen_at > _LAST_SEEN_RESOLUTION:
            sess.last_seen_at = now
            await db.commit()
    identity = Identity(user=user, tenant=tenant, membership=membership, session_id=sess.id)
    request.state.identity = identity
    return identity


@asynccontextmanager
async def bind_identity(
    identity: Identity, factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    """Bind tenant + user context and the workspace credential bundle."""
    from app.services import credentials

    t1 = current_tenant_id.set(identity.tenant_id)
    t2 = current_user_id.set(identity.user_id)
    try:
        bundle = await credentials.load_bundle(factory, identity.tenant_id)
        with credentials.use_bundle(bundle):
            yield
    finally:
        # FastAPI may finalise yield-dependencies in a copied context; a
        # token from another context can't be reset — the request's
        # context dies with it anyway.
        for var, tok in ((current_user_id, t2), (current_tenant_id, t1)):
            try:
                var.reset(tok)
            except ValueError:
                pass


def require_manager(identity: Identity = Depends(get_identity)) -> Identity:
    """Owner or admin of the active workspace."""
    if not identity.is_manager:
        raise HTTPException(status_code=403, detail="owner or admin role required")
    return identity


__all__ = [
    "Identity",
    "SESSION_COOKIE_NAME",
    "authenticate_request",
    "bind_identity",
    "clear_session_cookie",
    "create_session",
    "get_identity",
    "hash_token",
    "load_live_session",
    "new_token",
    "require_manager",
    "session_expiry",
    "set_session_cookie",
]
