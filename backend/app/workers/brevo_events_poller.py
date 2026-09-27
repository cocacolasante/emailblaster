"""Poll Brevo's transactional events API in lieu of the inbound webhook.

Brevo's outbound webhook would push events at us in real time, but it
needs a public tunnel and (on some plans) the Inbound Parsing add-on.
Polling the events endpoint outbound is free with the regular API key
and works behind the localhost-only port binding we use everywhere else.

Cadence: ``BREVO_EVENTS_POLL_INTERVAL_MINUTES`` (default 10).  Lag is
therefore up to 10 min from a real-world event (delivered, bounced,
opened, etc.) to the database row.  Acceptable for cold outreach where
hourly decisions are the norm.

Every poll re-scans the full ``LOOKBACK_FLOOR_HOURS`` (24h) window and
relies on ``process_event``'s per-event dedup (terminal types per lead;
opens/clicks per ``(lead, type, occurred_at)``) to make the re-scan
idempotent.  There is deliberately NO event-date watermark filter:
Brevo exposes ``opened`` events in its statistics feed LATE, so a
"skip anything older than the newest event seen" watermark silently
dropped nearly every open once a steady send stream kept the newest
event date pinned to "now" (2026-07-29: 184 opens at Brevo, 2 ingested).
Events older than 24h won't be back-filled — that's a deliberate floor
to keep the API query bounded; ``scripts/backfill_brevo_events.py``
covers historical recovery.  ``brevo:events:last_polled_at`` in Redis is
now purely an observability stamp of the last successful poll.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.tenancy.worker_db import worker_engine
from app.services import brevo
from app.services.brevo_events import process_event
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

WATERMARK_KEY = "brevo:events:last_polled_at"
LOOKBACK_FLOOR_HOURS = 24


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_redis() -> aioredis.Redis:
    """Fresh per-task client — Celery prefork tasks each get their own
    event loop, and a module-level client carries connection state bound
    to a dead loop.
    """
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


def _parse_event_date(raw: Any) -> datetime | None:
    """Brevo emits dates like ``2026-05-18T14:23:00.123Z`` or
    ``2026-05-18T14:23:00+02:00``.  Be tolerant of trailing Z."""
    if not raw:
        return None
    s = str(raw).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


async def _poll_async() -> dict[str, int]:
    counts = {"fetched": 0, "processed": 0, "deduped_or_unmatched": 0}
    redis_client = _new_redis()
    engine = worker_engine()
    try:
        now = _now()
        floor = now - timedelta(hours=LOOKBACK_FLOOR_HOURS)

        try:
            events = await brevo.fetch_events(
                start_date=floor.date().isoformat(),
                end_date=now.date().isoformat(),
            )
        except Exception:  # noqa: BLE001
            logger.exception("brevo.fetch_events failed")
            return counts

        counts["fetched"] = len(events)

        async with AsyncSession(engine, expire_on_commit=False) as session:
            for ev in events:
                # Undated events can't be deduped on re-scan — skip them
                # rather than duplicating one per poll tick.
                if _parse_event_date(ev.get("date")) is None:
                    continue
                try:
                    recorded = await process_event(session, ev)
                except Exception:  # noqa: BLE001
                    logger.exception("brevo process_event failed for %r", ev)
                    continue
                if recorded:
                    counts["processed"] += 1
                else:
                    counts["deduped_or_unmatched"] += 1
            await session.commit()

        # Observability stamp only — nothing filters on this anymore.
        await redis_client.set(WATERMARK_KEY, now.isoformat())
    finally:
        await engine.dispose()
        await redis_client.aclose()
    return counts


@celery_app.task(name="brevo_events_poller.poll")
def poll_brevo_events() -> dict[str, int]:
    return asyncio.run(_poll_async())
