"""Workspace API keys for agents (Muse over MCP).

Listing is open to any member (so everyone can see what can act on the
workspace); minting and revoking need a signed-in person — never a key.
A member can revoke their own keys; owners/admins can revoke any.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import Identity, get_identity, require_session
from app.config import settings
from app.database import get_db
from app.models.api_key import ApiKey
from app.models.identity import User
from app.services import api_keys

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


class KeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class KeyOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    created_by: uuid.UUID
    created_by_name: str | None = None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class KeyCreated(KeyOut):
    token: str  # the only time the token exists outside the caller's hands


class AgentAccessInfo(BaseModel):
    mcp_url: str
    public: bool


def mcp_url() -> str:
    return f"{settings.PUBLIC_ORIGIN.rstrip('/') or settings.WEBHOOK_BASE_URL.rstrip('/')}/mcp"


@router.get("/connection", response_model=AgentAccessInfo)
async def connection_info() -> AgentAccessInfo:
    """The MCP endpoint to paste into Muse."""
    url = mcp_url()
    host = url.split("://", 1)[-1].split("/", 1)[0].split(":")[0]
    return AgentAccessInfo(
        mcp_url=url,
        public=url.startswith("https://") and host not in ("localhost", "127.0.0.1"),
    )


@router.get("", response_model=list[KeyOut])
async def list_keys(db: AsyncSession = Depends(get_db)) -> list[KeyOut]:
    rows = (
        await db.execute(
            select(ApiKey, User.name, User.email)
            .join(User, User.id == ApiKey.created_by)
            .order_by(ApiKey.created_at.desc())
        )
    ).all()
    return [
        KeyOut(
            id=k.id, name=k.name, prefix=k.prefix, created_by=k.created_by,
            created_by_name=name or email, created_at=k.created_at,
            last_used_at=k.last_used_at, revoked_at=k.revoked_at,
        )
        for k, name, email in rows
    ]


@router.post("", response_model=KeyCreated, status_code=201)
async def create_key(
    body: KeyCreate,
    identity: Identity = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> KeyCreated:
    token, prefix, token_hash = api_keys.mint()
    key = ApiKey(name=body.name.strip(), prefix=prefix, token_hash=token_hash,
                 created_by=identity.user_id)
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return KeyCreated(
        id=key.id, name=key.name, prefix=key.prefix, created_by=key.created_by,
        created_by_name=identity.user.display_name, created_at=key.created_at,
        last_used_at=None, revoked_at=None, token=token,
    )


@router.post("/{key_id}/revoke", response_model=KeyOut)
async def revoke_key(
    key_id: uuid.UUID,
    identity: Identity = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> KeyOut:
    key = await db.get(ApiKey, key_id)
    if key is None or key.revoked_at is not None:
        raise HTTPException(status_code=404, detail="key not found or already revoked")
    if key.created_by != identity.user_id and not identity.is_manager:
        raise HTTPException(status_code=403, detail="only the key's creator or an owner/admin can revoke it")
    # Revoked, not deleted: last_used_at is the evidence of what a leaked key did.
    key.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    return KeyOut(
        id=key.id, name=key.name, prefix=key.prefix, created_by=key.created_by,
        created_at=key.created_at, last_used_at=key.last_used_at, revoked_at=key.revoked_at,
    )
