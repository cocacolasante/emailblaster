"""Mint / hash / verify workspace API keys (see models/api_key.py)."""
from __future__ import annotations

import hashlib
import re
import secrets
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import KEY_PREFIX, PREFIX_LENGTH

_BEARER = re.compile(r"^Bearer\s+(\S+)$", re.IGNORECASE)


def mint() -> tuple[str, str, str]:
    """``(token, prefix, token_hash)``.  The token is shown once, never stored."""
    token = KEY_PREFIX + secrets.token_urlsafe(32)
    return token, token[:PREFIX_LENGTH], hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def parse_bearer(header: str | None) -> str | None:
    """The key from an Authorization header (``Bearer eb_…`` or bare)."""
    if not header:
        return None
    m = _BEARER.match(header.strip())
    token = m.group(1) if m else header.strip()
    return token if token.startswith(KEY_PREFIX) and len(token) > PREFIX_LENGTH else None


async def verify(db: AsyncSession, token: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID] | None:
    """``(key_id, tenant_id, user_id)`` for a live key, else None.  Runs the
    SECURITY DEFINER verifier (caller commits to persist last_used_at)."""
    row = (
        await db.execute(
            text("SELECT key_id, tenant_id, user_id FROM api_key_verify(:p, :h)"),
            {"p": token[:PREFIX_LENGTH], "h": hash_token(token)},
        )
    ).first()
    return (row[0], row[1], row[2]) if row else None
