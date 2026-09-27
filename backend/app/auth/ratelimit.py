"""Tiny fixed-window rate limiter for auth endpoints (Redis INCR+EXPIRE).

Fails OPEN when Redis is unreachable — an outage must not lock every
user out; brute force is still bounded by argon2's cost.
"""
from __future__ import annotations

import logging

import redis.asyncio as aioredis
from fastapi import HTTPException

from app.config import settings

logger = logging.getLogger(__name__)


def _redis() -> aioredis.Redis:
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True,
                             socket_connect_timeout=0.5, socket_timeout=0.5)


async def hit(key: str, *, limit: int, window_seconds: int) -> None:
    """Count one attempt against ``key``; 429 once ``limit`` is exceeded."""
    client = _redis()
    try:
        count = await client.incr(f"authrl:{key}")
        if count == 1:
            await client.expire(f"authrl:{key}", window_seconds)
    except Exception as exc:  # noqa: BLE001 — fail open
        logger.debug("auth rate limiter unavailable: %s", exc)
        return
    finally:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            pass
    if count > limit:
        raise HTTPException(status_code=429, detail="too many attempts — try again later")
