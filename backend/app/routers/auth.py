"""First-party auth: register / login / logout / me / workspace switch /
password reset / invite preview + accept.

These endpoints run before (or independent of) a workspace context, so
they use ``get_public_db`` and query identity tables explicitly — the
only feature router allowed to.
"""
from __future__ import annotations

import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import passwords, ratelimit
from app.auth.deps import (
    SESSION_COOKIE_NAME,
    Identity,
    clear_session_cookie,
    create_session,
    get_identity,
    hash_token,
    load_live_session,
    new_token,
    set_session_cookie,
)
from app.config import settings
from app.database import get_public_db
from app.models.identity import (
    AuthToken,
    AuthTokenPurpose,
    Invitation,
    Membership,
    MembershipRole,
    Tenant,
    TenantStatus,
    User,
    UserSession,
)
from app.services.platform_email import send_platform_email
from app.services.tenant_seed import seed_tenant_defaults

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

RESET_TOKEN_TTL_MINUTES = 30
_EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_email(email: str) -> str:
    return email.strip().lower()


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:48] or "workspace"


async def unique_slug(db: AsyncSession, name: str) -> str:
    base = _slugify(name)
    slug = base
    while (await db.execute(select(Tenant.id).where(Tenant.slug == slug))).first():
        slug = f"{base}-{secrets.token_hex(3)}"
    return slug


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# --- Schemas ---------------------------------------------------------------


class _EmailBody(BaseModel):
    email: str = Field(max_length=320)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = canonical_email(v)
        if not _EMAIL_RX.match(v):
            raise ValueError("invalid email address")
        return v


class RegisterRequest(_EmailBody):
    password: str = Field(min_length=passwords.MIN_PASSWORD_LENGTH, max_length=200)
    name: str | None = Field(default=None, max_length=200)
    workspace_name: str | None = Field(default=None, max_length=200)


class LoginRequest(_EmailBody):
    password: str = Field(max_length=200)


class ForgotRequest(_EmailBody):
    pass


class ResetRequest(BaseModel):
    token: str = Field(min_length=10, max_length=500)
    password: str = Field(min_length=passwords.MIN_PASSWORD_LENGTH, max_length=200)


class SwitchWorkspaceRequest(BaseModel):
    tenant_id: uuid.UUID


class UpdateMeRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    current_password: str | None = Field(default=None, max_length=200)
    new_password: str | None = Field(
        default=None, min_length=passwords.MIN_PASSWORD_LENGTH, max_length=200,
    )
    notify_email_enabled: bool | None = None
    digest_enabled: bool | None = None


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=10, max_length=500)
    password: str = Field(min_length=1, max_length=200)
    name: str | None = Field(default=None, max_length=200)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str | None
    display_name: str


class WorkspaceOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str


class MembershipOut(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    role: str


class MeResponse(BaseModel):
    user: UserOut
    workspace: WorkspaceOut
    role: str
    notify_email_enabled: bool
    digest_enabled: bool
    memberships: list[MembershipOut]
    allow_signup: bool


class AuthConfigResponse(BaseModel):
    allow_signup: bool
    platform_email: bool


class InvitePreview(BaseModel):
    workspace_name: str
    email: str
    role: str
    user_exists: bool


# --- Helpers ---------------------------------------------------------------


def _user_out(user: User) -> UserOut:
    return UserOut(id=user.id, email=user.email, name=user.name, display_name=user.display_name)


async def build_me(db: AsyncSession, user: User, tenant: Tenant, membership: Membership) -> MeResponse:
    rows = (
        await db.execute(
            select(Membership, Tenant)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .where(Membership.user_id == user.id, Tenant.status == TenantStatus.ACTIVE)
            .order_by(Membership.created_at)
        )
    ).all()
    return MeResponse(
        user=_user_out(user),
        workspace=WorkspaceOut(id=tenant.id, name=tenant.name, slug=tenant.slug),
        role=membership.role.value,
        notify_email_enabled=membership.notify_email_enabled,
        digest_enabled=membership.digest_enabled,
        memberships=[
            MembershipOut(tenant_id=t.id, tenant_name=t.name, role=m.role.value)
            for m, t in rows
        ],
        allow_signup=settings.ALLOW_SIGNUP,
    )


async def _login_into(
    db: AsyncSession, request: Request, response: Response,
    user: User, tenant: Tenant, membership: Membership,
) -> MeResponse:
    raw = await create_session(db, user=user, tenant_id=tenant.id, request=request)
    await db.commit()
    set_session_cookie(response, raw)
    return await build_me(db, user, tenant, membership)


async def _first_active_membership(db: AsyncSession, user_id: uuid.UUID):
    return (
        await db.execute(
            select(Membership, Tenant)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .where(Membership.user_id == user_id, Tenant.status == TenantStatus.ACTIVE)
            .order_by(Membership.created_at)
            .limit(1)
        )
    ).first()


async def _load_invite(db: AsyncSession, raw_token: str) -> Invitation | None:
    return (
        await db.execute(
            select(Invitation).where(
                Invitation.token_hash == hash_token(raw_token),
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
                Invitation.expires_at > _now(),
            )
        )
    ).scalar_one_or_none()


# --- Endpoints -------------------------------------------------------------


@router.get("/config", response_model=AuthConfigResponse)
async def auth_config() -> AuthConfigResponse:
    """Public: lets the login page decide whether to show "Sign up"."""
    from app.services import platform_email

    return AuthConfigResponse(
        allow_signup=settings.ALLOW_SIGNUP, platform_email=platform_email.is_configured(),
    )


@router.post("/register", response_model=MeResponse, status_code=201)
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_public_db),
) -> MeResponse:
    """Self-serve signup: creates a workspace with the caller as owner."""
    if not settings.ALLOW_SIGNUP:
        raise HTTPException(status_code=403, detail="signup is disabled")
    await ratelimit.hit(f"register:{_client_ip(request)}", limit=10, window_seconds=3600)
    if (await db.execute(select(User.id).where(User.email == body.email))).first():
        raise HTTPException(status_code=409, detail="an account with this email already exists")

    ws_name = (body.workspace_name or "").strip() or f"{(body.name or body.email.split('@')[0]).strip()}'s workspace"
    tenant = Tenant(name=ws_name, slug=await unique_slug(db, ws_name))
    user = User(
        email=body.email,
        name=(body.name or "").strip() or None,
        password_hash=passwords.hash_password(body.password),
    )
    db.add_all([tenant, user])
    await db.flush()
    membership = Membership(tenant_id=tenant.id, user_id=user.id, role=MembershipRole.OWNER)
    db.add(membership)
    await db.flush()
    await seed_tenant_defaults(db, tenant.id)
    return await _login_into(db, request, response, user, tenant, membership)


@router.post("/login", response_model=MeResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_public_db),
) -> MeResponse:
    await ratelimit.hit(f"login:{_client_ip(request)}:{body.email}", limit=10, window_seconds=900)
    user = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    # One generic 401 for unknown email and bad password (no enumeration);
    # the burner verify keeps both paths equally slow.
    if user is None or not user.password_hash:
        passwords.burn_verify(body.password)
        raise HTTPException(status_code=401, detail="invalid email or password")
    if not passwords.verify_password(user.password_hash, body.password):
        raise HTTPException(status_code=401, detail="invalid email or password")
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash_password(body.password)
    row = await _first_active_membership(db, user.id)
    if row is None:
        raise HTTPException(status_code=403, detail="you are not a member of any active workspace")
    membership, tenant = row
    return await _login_into(db, request, response, user, tenant, membership)


@router.post("/logout")
async def logout(
    request: Request, response: Response, db: AsyncSession = Depends(get_public_db),
) -> dict:
    """Revoke the current session (idempotent; always clears the cookie)."""
    raw = request.cookies.get(SESSION_COOKIE_NAME)
    if raw:
        pair = await load_live_session(db, raw)
        if pair is not None:
            pair[0].revoked_at = _now()
            await db.commit()
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
async def me(
    identity: Identity = Depends(get_identity), db: AsyncSession = Depends(get_public_db),
) -> MeResponse:
    return await build_me(db, identity.user, identity.tenant, identity.membership)


@router.patch("/me", response_model=MeResponse)
async def update_me(
    body: UpdateMeRequest,
    identity: Identity = Depends(get_identity),
    db: AsyncSession = Depends(get_public_db),
) -> MeResponse:
    user = await db.get(User, identity.user_id)
    membership = await db.get(Membership, identity.membership.id)
    if body.name is not None:
        user.name = body.name.strip() or None
    if body.new_password is not None:
        if not user.password_hash or not passwords.verify_password(
            user.password_hash, body.current_password or "",
        ):
            raise HTTPException(status_code=400, detail="current password is incorrect")
        user.password_hash = passwords.hash_password(body.new_password)
        # Other sessions die with the old password; keep this one.
        await db.execute(
            update(UserSession)
            .where(
                UserSession.user_id == user.id,
                UserSession.revoked_at.is_(None),
                UserSession.id != identity.session_id,
            )
            .values(revoked_at=_now())
        )
    if body.notify_email_enabled is not None:
        membership.notify_email_enabled = body.notify_email_enabled
    if body.digest_enabled is not None:
        membership.digest_enabled = body.digest_enabled
    await db.commit()
    return await build_me(db, user, identity.tenant, membership)


@router.post("/switch-workspace", response_model=MeResponse)
async def switch_workspace(
    body: SwitchWorkspaceRequest,
    identity: Identity = Depends(get_identity),
    db: AsyncSession = Depends(get_public_db),
) -> MeResponse:
    row = (
        await db.execute(
            select(Membership, Tenant)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .where(
                Membership.user_id == identity.user_id,
                Membership.tenant_id == body.tenant_id,
                Tenant.status == TenantStatus.ACTIVE,
            )
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    membership, tenant = row
    sess = await db.get(UserSession, identity.session_id)
    sess.tenant_id = tenant.id
    await db.commit()
    return await build_me(db, identity.user, tenant, membership)


@router.post("/forgot")
async def forgot(
    body: ForgotRequest, request: Request, db: AsyncSession = Depends(get_public_db),
) -> dict:
    """Always 200 — never reveals whether the email has an account."""
    await ratelimit.hit(f"forgot:{_client_ip(request)}", limit=10, window_seconds=3600)
    user = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if user is not None:
        raw = new_token()
        db.add(AuthToken(
            user_id=user.id,
            purpose=AuthTokenPurpose.PASSWORD_RESET,
            token_hash=hash_token(raw),
            expires_at=_now() + timedelta(minutes=RESET_TOKEN_TTL_MINUTES),
        ))
        await db.commit()
        reset_url = f"{settings.FRONTEND_URL.rstrip('/')}/reset-password?token={raw}"
        sent = await send_platform_email(
            to_email=user.email,
            subject="Reset your password",
            html_body=(
                "<p>Someone requested a password reset for this address.</p>"
                f'<p><a href="{reset_url}">Reset your password</a> '
                f"(expires in {RESET_TOKEN_TTL_MINUTES} minutes).</p>"
                "<p>If this wasn't you, ignore this email.</p>"
            ),
            text_body=(
                f"Reset your password (expires in {RESET_TOKEN_TTL_MINUTES} minutes): "
                f"{reset_url}\nIf this wasn't you, ignore this email."
            ),
        )
        if not sent:
            # Dev fallback: platform mail isn't configured.  Server log only —
            # never returned by the API.
            logger.warning("Password reset link for %s (platform mail not configured): %s",
                           user.email, reset_url)
    return {"ok": True}


@router.post("/reset")
async def reset(body: ResetRequest, db: AsyncSession = Depends(get_public_db)) -> dict:
    token_row = (
        await db.execute(
            select(AuthToken).where(
                AuthToken.token_hash == hash_token(body.token),
                AuthToken.purpose == AuthTokenPurpose.PASSWORD_RESET,
                AuthToken.used_at.is_(None),
                AuthToken.expires_at > _now(),
            )
        )
    ).scalar_one_or_none()
    user = await db.get(User, token_row.user_id) if token_row else None
    if token_row is None or user is None:
        raise HTTPException(status_code=400, detail="invalid or expired reset link")
    user.password_hash = passwords.hash_password(body.password)
    token_row.used_at = _now()
    # A reset kills every live session for the user.
    await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    await db.commit()
    return {"ok": True}


@router.get("/invite/{token}", response_model=InvitePreview)
async def preview_invite(token: str, db: AsyncSession = Depends(get_public_db)) -> InvitePreview:
    inv = await _load_invite(db, token)
    tenant = await db.get(Tenant, inv.tenant_id) if inv else None
    if inv is None or tenant is None or tenant.status != TenantStatus.ACTIVE:
        raise HTTPException(status_code=404, detail="invalid or expired invite")
    exists = (await db.execute(select(User.id).where(User.email == inv.email))).first() is not None
    return InvitePreview(
        workspace_name=tenant.name, email=inv.email, role=inv.role.value, user_exists=exists,
    )


@router.post("/invite/accept", response_model=MeResponse)
async def accept_invite(
    body: AcceptInviteRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_public_db),
) -> MeResponse:
    """New email → creates the account with ``password``.  Existing account
    → ``password`` must be that account's password (proves it's them)."""
    await ratelimit.hit(f"invite:{_client_ip(request)}", limit=20, window_seconds=900)
    inv = await _load_invite(db, body.token)
    tenant = await db.get(Tenant, inv.tenant_id) if inv else None
    if inv is None or tenant is None or tenant.status != TenantStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="invalid or expired invite")

    user = (await db.execute(select(User).where(User.email == inv.email))).scalar_one_or_none()
    if user is None:
        if len(body.password) < passwords.MIN_PASSWORD_LENGTH:
            raise HTTPException(
                status_code=422,
                detail=f"password must be at least {passwords.MIN_PASSWORD_LENGTH} characters",
            )
        user = User(
            email=inv.email,
            name=(body.name or "").strip() or None,
            password_hash=passwords.hash_password(body.password),
            email_verified_at=_now(),  # they received the invite at this address
        )
        db.add(user)
        await db.flush()
    else:
        if not user.password_hash or not passwords.verify_password(user.password_hash, body.password):
            raise HTTPException(status_code=401, detail="incorrect password for this account")

    membership = (
        await db.execute(
            select(Membership).where(
                Membership.tenant_id == tenant.id, Membership.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        membership = Membership(tenant_id=tenant.id, user_id=user.id, role=inv.role)
        db.add(membership)
    inv.accepted_at = _now()
    await db.flush()
    return await _login_into(db, request, response, user, tenant, membership)
