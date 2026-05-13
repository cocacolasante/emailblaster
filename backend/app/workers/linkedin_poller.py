"""Periodic LinkedIn inbox poller.

Runs every ``LINKEDIN_POLL_INTERVAL_MINUTES`` (default 5). For each OK
LinkedIn account, asks the provider for events newer than
``account.last_polled_at`` (or 1 hour ago for first-time polls) and
translates them into lead-state updates:

  - message_received    → Lead.linkedin_last_reply_at
  - connection_accepted → Lead.linkedin_connection_status = 'connected'

Lead matching is by ``lead.linkedin_url`` slug (public_id). Leads whose
URL doesn't match the inbound sender are ignored — most LinkedIn DMs we
get aren't from leads we're tracking.

The poller never raises into the Celery worker — provider errors flip
``account.last_error`` and move on.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.models import (
    Lead,
    LinkedInAccount,
    LinkedInAccountStatus,
    LinkedInConnectionStatus,
)
from app.services.linkedin import get_provider
from app.services.linkedin.base import (
    AccountRestricted,
    ChallengeRequired,
    InboundEvent,
)
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _slug_from_url(url: str | None) -> str | None:
    if not url:
        return None
    s = url.rstrip("/").split("/")[-1].split("?")[0]
    return s.lower() or None


async def _poll_account(session: AsyncSession, account: LinkedInAccount) -> dict[str, int]:
    """Pull recent events for one account; apply them to leads.

    Returns a per-event-kind counter for logging.
    """
    counts = {"messages_matched": 0, "messages_total": 0, "connections": 0}
    provider = get_provider()
    since = account.last_polled_at or datetime.now(timezone.utc) - timedelta(hours=1)

    try:
        events: list[InboundEvent] = await provider.inbox_recent_events(account, since)
    except ChallengeRequired as exc:
        account.status = LinkedInAccountStatus.CHALLENGED
        account.last_error = str(exc)
        account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
        return counts
    except AccountRestricted as exc:
        account.status = LinkedInAccountStatus.RESTRICTED
        account.last_error = str(exc)
        return counts
    except Exception as exc:  # noqa: BLE001
        logger.warning("LinkedIn poll failed for account %s: %s", account.id, exc)
        account.last_error = str(exc)
        return counts

    account.last_polled_at = datetime.now(timezone.utc)
    account.last_error = None

    if not events:
        return counts

    # Build a slug index of leads for the campaigns owned by THIS account.
    lead_rows = (await session.execute(
        select(Lead)
        .join(Lead.campaign)
        .where(Lead.campaign.has(linkedin_account_id=account.id))
    )).scalars().all()
    by_slug: dict[str, Lead] = {}
    for l in lead_rows:
        slug = _slug_from_url(l.linkedin_url)
        if slug:
            by_slug[slug] = l

    for ev in events:
        if ev.kind == "message_received":
            counts["messages_total"] += 1
            slug = (ev.from_public_id or "").lower() or None
            lead = by_slug.get(slug) if slug else None
            if lead is None:
                continue
            counts["messages_matched"] += 1
            lead.linkedin_last_reply_at = ev.occurred_at
            # First DM is a strong signal they accepted the connect at some
            # point; promote status if we don't already have it.
            if lead.linkedin_connection_status != LinkedInConnectionStatus.CONNECTED:
                lead.linkedin_connection_status = LinkedInConnectionStatus.CONNECTED
        elif ev.kind == "connection_accepted":
            counts["connections"] += 1
            slug = (ev.from_public_id or "").lower() or None
            lead = by_slug.get(slug) if slug else None
            if lead is not None:
                lead.linkedin_connection_status = LinkedInConnectionStatus.CONNECTED

    return counts


async def _poll_all_async() -> dict[str, int]:
    totals = {"accounts": 0, "messages_matched": 0, "messages_total": 0, "connections": 0}
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            accounts = (await session.execute(
                select(LinkedInAccount).where(
                    LinkedInAccount.status == LinkedInAccountStatus.OK
                )
            )).scalars().all()
            for acc in accounts:
                totals["accounts"] += 1
                c = await _poll_account(session, acc)
                totals["messages_matched"] += c["messages_matched"]
                totals["messages_total"] += c["messages_total"]
                totals["connections"] += c["connections"]
            await session.commit()
    finally:
        await engine.dispose()
    return totals


@celery_app.task(name="linkedin_poller.poll_all")
def poll_all() -> dict[str, int]:
    return asyncio.run(_poll_all_async())
