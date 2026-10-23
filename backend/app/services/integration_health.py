"""Background detection of a workspace integration whose key stopped working.

A provider that rejects the workspace's credentials (Brevo 401 "unrecognised
IP address" after the home IP changed, a revoked Unipile token, ...) used to
look like a transient error: tasks retried forever, nothing was recorded,
and the workspace silently stopped sending and pulling events for days.

``mark_broken`` is called by the worker that hit the rejection.  It:

- sets a Redis flag ``integration:broken:{tenant}:{provider}`` with a
  ``BROKEN_TTL_SECONDS`` lifetime — ``check_send_gates`` defers sends while
  it's set, so leads wait in place (no FAILED rows, no burned retries) and
  only one probe per window reaches the provider;
- stamps the Integrations row ``last_test_status="failed"`` with the
  provider's message (drives the app-wide banner);
- adds one workspace-wide notification per provider per day.

``mark_ok`` (a successful Brevo events poll, or a passing Integrations
"Test") clears both, so sending resumes on its own once the key works.
Everything here is best-effort and never raises into the caller.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import NotificationKind, TenantProviderKey
from app.tenancy.keys import integration_broken_key
from app.tenancy.worker_db import worker_engine

logger = logging.getLogger(__name__)

# How long a rejection holds sends back before one more attempt is allowed
# through.  Matches the Brevo events poll cadence, which also probes the key.
BROKEN_TTL_SECONDS = 600

_LABELS = {"brevo": "Brevo", "unipile": "Unipile"}
_IMPACT = {
    "brevo": "Emails are on hold and opens, clicks and bounces aren't being "
             "recorded. Sending resumes automatically once the key works again.",
    "unipile": "LinkedIn steps and LinkedIn reply tracking aren't working.",
}


def _redis() -> aioredis.Redis:
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


async def broken_ttl(redis_client: aioredis.Redis, provider: str) -> int | None:
    """Seconds left on the provider's broken flag, or None when healthy."""
    try:
        ttl = await redis_client.ttl(integration_broken_key(provider))
    except Exception:  # noqa: BLE001 — a Redis hiccup must not block sends
        return None
    return int(ttl) if ttl and ttl > 0 else None


async def mark_broken(provider: str, error: str) -> None:
    """Record that ``provider`` rejected the current workspace's credentials."""
    error = (error or "credentials rejected")[:500]
    label = _LABELS.get(provider, provider)
    logger.warning("%s rejected this workspace's credentials: %s", label, error)
    redis_client = _redis()
    try:
        await redis_client.set(integration_broken_key(provider), error, ex=BROKEN_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        logger.exception("could not set broken flag for %s", provider)
    finally:
        await redis_client.aclose()

    engine = worker_engine()
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            row = await session.scalar(
                select(TenantProviderKey).where(TenantProviderKey.provider == provider)
            )
            if row is not None:
                row.last_test_status = "failed"
                row.last_test_error = error
                row.last_tested_at = datetime.now(timezone.utc)
            from app.services.notifications import create_notification

            await create_notification(
                session,
                kind=NotificationKind.AGENT_ERROR,
                title=f"{label} is rejecting this workspace's API key",
                body=f"{label} said: {error}\n\n{_IMPACT.get(provider, '')}\n\n"
                     "Fix it in Settings → Integrations.",
                dedup_key=f"integration_broken:{provider}:{datetime.now(timezone.utc).date()}",
                route_to_owner=False,
            )
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("could not record broken %s integration", provider)
    finally:
        await engine.dispose()


async def clear_flag(provider: str) -> None:
    """Drop the Redis hold so sends resume immediately."""
    redis_client = _redis()
    try:
        await redis_client.delete(integration_broken_key(provider))
    except Exception:  # noqa: BLE001
        logger.exception("could not clear broken flag for %s", provider)
    finally:
        await redis_client.aclose()


async def mark_ok(provider: str) -> None:
    """The provider accepted the credentials again: lift the hold and flip
    a ``failed`` Integrations row back to ``ok``."""
    await clear_flag(provider)
    engine = worker_engine()
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            row = await session.scalar(
                select(TenantProviderKey).where(TenantProviderKey.provider == provider)
            )
            if row is not None and row.last_test_status == "failed":
                row.last_test_status = "ok"
                row.last_test_error = None
                row.last_tested_at = datetime.now(timezone.utc)
                await session.commit()
                logger.info("%s accepted this workspace's credentials again", provider)
    except Exception:  # noqa: BLE001
        logger.exception("could not record recovered %s integration", provider)
    finally:
        await engine.dispose()
