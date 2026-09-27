"""Workspace team management: members, roles, invitations, workspace name.

All endpoints act on the caller's ACTIVE workspace (``identity.tenant``).
Identity tables carry no RLS, so every query here filters on
``tenant_id`` explicitly.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import Identity, get_identity, hash_token, new_token, require_manager, require_session
from app.config import settings
from app.database import get_db
from app.models.identity import (
    Invitation,
    Membership,
    MembershipRole,
    Tenant,
    User,
    UserSession,
)
from app.routers.auth import _EMAIL_RX, canonical_email
from app.services import platform_email

router = APIRouter(prefix="/team", tags=["team"])

INVITE_TTL_DAYS = 7


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    name: str | None
    display_name: str
    role: str
    joined_at: datetime


class InviteOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    expires_at: datetime
    created_at: datetime
    invite_url: str | None = None
    emailed: bool | None = None


class InviteCreate(BaseModel):
    email: str = Field(max_length=320)
    role: MembershipRole = MembershipRole.MEMBER

    @field_validator("email")
    @classmethod
    def _valid(cls, v: str) -> str:
        v = canonical_email(v)
        if not _EMAIL_RX.match(v):
            raise ValueError("invalid email address")
        return v


class RoleUpdate(BaseModel):
    role: MembershipRole


class WorkspaceUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class WorkspaceOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str


def _member_out(m: Membership, u: User) -> MemberOut:
    return MemberOut(
        user_id=u.id, email=u.email, name=u.name, display_name=u.display_name,
        role=m.role.value, joined_at=m.created_at,
    )


async def _owner_count(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            select(func.count()).select_from(Membership).where(
                Membership.tenant_id == tenant_id, Membership.role == MembershipRole.OWNER,
            )
        ) or 0
    )


async def _get_member(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Membership:
    m = (
        await db.execute(
            select(Membership).where(Membership.tenant_id == tenant_id, Membership.user_id == user_id)
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=404, detail="not a member of this workspace")
    return m


@router.get("/members", response_model=list[MemberOut])
async def list_members(
    identity: Identity = Depends(get_identity), db: AsyncSession = Depends(get_db),
) -> list[MemberOut]:
    rows = (
        await db.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.tenant_id == identity.tenant_id)
            .order_by(Membership.created_at)
        )
    ).all()
    return [_member_out(m, u) for m, u in rows]


@router.patch("/members/{user_id}", response_model=MemberOut)
async def update_member_role(
    user_id: uuid.UUID,
    body: RoleUpdate,
    identity: Identity = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> MemberOut:
    target = await _get_member(db, identity.tenant_id, user_id)
    touches_owner = MembershipRole.OWNER in (target.role, body.role)
    if touches_owner and identity.role != MembershipRole.OWNER:
        raise HTTPException(status_code=403, detail="only an owner can grant or change the owner role")
    if (
        target.role == MembershipRole.OWNER
        and body.role != MembershipRole.OWNER
        and await _owner_count(db, identity.tenant_id) <= 1
    ):
        raise HTTPException(status_code=409, detail="a workspace needs at least one owner")
    target.role = body.role
    await db.commit()
    user = await db.get(User, user_id)
    return _member_out(target, user)


@router.delete("/members/{user_id}", status_code=204, response_model=None)
async def remove_member(
    user_id: uuid.UUID,
    reassign_to: uuid.UUID | None = Query(default=None),
    identity: Identity = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a member (managers), or leave the workspace (self).

    Their owned records are reassigned to ``reassign_to`` when given;
    otherwise they become unassigned (the owner FK is ON DELETE SET NULL).
    """
    is_self = user_id == identity.user_id
    if not is_self and not identity.is_manager:
        raise HTTPException(status_code=403, detail="owner or admin role required")
    target = await _get_member(db, identity.tenant_id, user_id)
    if target.role == MembershipRole.OWNER:
        if identity.role != MembershipRole.OWNER:
            raise HTTPException(status_code=403, detail="only an owner can remove an owner")
        if await _owner_count(db, identity.tenant_id) <= 1:
            raise HTTPException(status_code=409, detail="cannot remove the last owner")
    if reassign_to is not None:
        if reassign_to == user_id:
            raise HTTPException(status_code=422, detail="cannot reassign to the member being removed")
        await _get_member(db, identity.tenant_id, reassign_to)
        from app.services import ownership

        await ownership.reassign_all(db, from_user=user_id, to_user=reassign_to)
    await db.execute(
        update(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.tenant_id == identity.tenant_id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=_now())
    )
    await db.delete(target)
    await db.commit()


@router.get("/invites", response_model=list[InviteOut])
async def list_invites(
    identity: Identity = Depends(require_manager), db: AsyncSession = Depends(get_db),
) -> list[InviteOut]:
    rows = (
        await db.execute(
            select(Invitation)
            .where(
                Invitation.tenant_id == identity.tenant_id,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
                Invitation.expires_at > _now(),
            )
            .order_by(Invitation.created_at.desc())
        )
    ).scalars().all()
    return [
        InviteOut(id=i.id, email=i.email, role=i.role.value,
                  expires_at=i.expires_at, created_at=i.created_at)
        for i in rows
    ]


@router.post("/invites", response_model=InviteOut, status_code=201)
async def create_invite(
    body: InviteCreate,
    identity: Identity = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> InviteOut:
    """Invite by email.  Returns the invite link so an admin can share it
    directly when platform mail isn't configured."""
    if body.role == MembershipRole.OWNER and identity.role != MembershipRole.OWNER:
        raise HTTPException(status_code=403, detail="only an owner can invite another owner")
    already = (
        await db.execute(
            select(Membership.id)
            .join(User, User.id == Membership.user_id)
            .where(Membership.tenant_id == identity.tenant_id, User.email == body.email)
        )
    ).first()
    if already:
        raise HTTPException(status_code=409, detail="already a member of this workspace")
    # One live invite per email: re-inviting revokes the previous link.
    await db.execute(
        update(Invitation)
        .where(
            Invitation.tenant_id == identity.tenant_id,
            Invitation.email == body.email,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .values(revoked_at=_now())
    )
    raw = new_token()
    inv = Invitation(
        tenant_id=identity.tenant_id,
        email=body.email,
        role=body.role,
        token_hash=hash_token(raw),
        invited_by=identity.user_id,
        expires_at=_now() + timedelta(days=INVITE_TTL_DAYS),
    )
    db.add(inv)
    await db.commit()
    await db.refresh(inv)

    url = f"{settings.FRONTEND_URL.rstrip('/')}/accept-invite?token={raw}"
    ws = identity.tenant.name
    inviter = identity.user.display_name
    emailed = await platform_email.send_platform_email(
        to_email=body.email,
        subject=f"{inviter} invited you to {ws} on Email Blaster",
        html_body=(
            f"<p>{inviter} invited you to join the <b>{ws}</b> workspace.</p>"
            f'<p><a href="{url}">Accept the invitation</a> '
            f"(expires in {INVITE_TTL_DAYS} days).</p>"
        ),
        text_body=f"{inviter} invited you to join {ws}. Accept (expires in {INVITE_TTL_DAYS} days): {url}",
    )
    return InviteOut(
        id=inv.id, email=inv.email, role=inv.role.value, expires_at=inv.expires_at,
        created_at=inv.created_at, invite_url=url, emailed=emailed,
    )


@router.delete("/invites/{invite_id}", status_code=204, response_model=None)
async def revoke_invite(
    invite_id: uuid.UUID,
    identity: Identity = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> None:
    inv = await db.get(Invitation, invite_id)
    if inv is None or inv.tenant_id != identity.tenant_id:
        raise HTTPException(status_code=404, detail="invite not found")
    inv.revoked_at = _now()
    await db.commit()


@router.patch("/workspace", response_model=WorkspaceOut)
async def rename_workspace(
    body: WorkspaceUpdate,
    identity: Identity = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceOut:
    tenant = await db.get(Tenant, identity.tenant_id)
    tenant.name = body.name.strip()
    await db.commit()
    return WorkspaceOut(id=tenant.id, name=tenant.name, slug=tenant.slug)
