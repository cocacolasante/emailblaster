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
    # "session" (browser cookie) or "api_key" (agent bearer key).
    via: str = "session"
    api_key_id: uuid.UUID | None = None

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


def bearer_api_key(request: Request) -> str | None:
    """An ``Authorization: Bearer eb_…`` workspace API key, if presented."""
    from app.services.api_keys import parse_bearer

    return parse_bearer(request.headers.get("authorization"))


async def authenticate_api_key(
    token: str, request: Request, factory: async_sessionmaker[AsyncSession],
) -> Identity:
    """Resolve an agent's bearer key.  The key acts as the member who
    created it, in that member's workspace."""
    from app.services.api_keys import verify

    async with factory() as db:
        found = await verify(db, token)
        if found is None:
            raise HTTPException(
                status_code=401, detail="invalid or revoked API key",
                headers={"WWW-Authenticate": 'Bearer realm="emailblaster"'},
            )
        key_id, tenant_id, user_id = found
        user = await db.get(User, user_id)
        tenant = await db.get(Tenant, tenant_id)
        membership = (
            await db.execute(
                select(Membership).where(
                    Membership.tenant_id == tenant_id, Membership.user_id == user_id,
                )
            )
        ).scalar_one()
        await db.commit()  # persists last_used_at stamped by the verifier
    identity = Identity(user=user, tenant=tenant, membership=membership,
                        via="api_key", api_key_id=key_id)
    request.state.identity = identity
    return identity


async def authenticate_request(
    request: Request, factory: async_sessionmaker[AsyncSession],
) -> Identity:
    """Resolve the session cookie (or an agent's bearer API key) to an
    Identity.  401 without a live session / membership / key; 403 when the
    workspace is suspended."""
    token = bearer_api_key(request)
    if token is not None:
        return await authenticate_api_key(token, request, factory)
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


def require_session(identity: Identity = Depends(get_identity)) -> Identity:
    """A signed-in person, not an agent key.  Guards anything that manages
    credentials, the team, or the workspace itself."""
    if identity.via != "session":
        raise HTTPException(status_code=403, detail="this action needs a signed-in person, not an API key")
    return identity


def require_manager(identity: Identity = Depends(get_identity)) -> Identity:
    """Owner or admin of the active workspace."""
    if identity.via != "session":
        raise HTTPException(status_code=403, detail="this action needs a signed-in person, not an API key")
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
    "require_session",
    "session_expiry",
    "set_session_cookie",
]
