"""Send worker: scheduled, rate-limited delivery via Brevo.

Pre-send checks run in this order:
  1. Suppression list — hard fail
  2. Campaign paused — re-enqueue after 5 minutes
  3. Schedule window — re-enqueue at next valid datetime in the campaign's tz
  4. Redis rate limits (min_delay, hourly cap, daily cap) — re-enqueue
  5. Render + send via Brevo
  6. Save send state + bump Redis counters
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

import pytz
import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.models import (
    Campaign,
    CampaignStatus,
    Lead,
    SendStatus,
    Suppression,
)
from app.services import brevo
from app.services.email_template import render_html, render_text
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

def _new_redis() -> aioredis.Redis:
    """Create a fresh Redis client for one asyncio.run() call.

    A module-level singleton breaks here: each asyncio.run() creates then
    closes its own event loop, leaving any client bound to the old loop
    unusable in the next invocation.
    """
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


# --------------------------------------------------------------------------
# Scheduling
# --------------------------------------------------------------------------


def compute_next_send_window(
    campaign: Campaign, now: datetime | None = None
) -> datetime | None:
    """Return None when the current moment is inside the campaign's send window;
    otherwise the next datetime (tz-aware) when the window opens.
    """
    tz = pytz.timezone(campaign.schedule_timezone or "UTC")
    if now is None:
        now_tz = datetime.now(tz)
    elif now.tzinfo is None:
        now_tz = tz.localize(now)
    else:
        now_tz = now.astimezone(tz)

    days_allowed = set(campaign.schedule_days or [])
    if not days_allowed:
        days_allowed = set(range(7))  # empty list ⇒ all days

    start = campaign.schedule_time_start
    end = campaign.schedule_time_end
    today_dow = now_tz.weekday()

    if today_dow in days_allowed and start <= now_tz.time() <= end:
        return None

    # Search up to a week ahead for the next allowed day.
    for offset in range(8):
        candidate = now_tz + timedelta(days=offset)
        if candidate.weekday() not in days_allowed:
            continue
        if offset == 0:
            if now_tz.time() < start:
                return tz.localize(datetime.combine(candidate.date(), start))
            # After end — skip to next allowed day.
            continue
        return tz.localize(datetime.combine(candidate.date(), start))
    return None


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------


async def check_rate_limits(
    campaign: Campaign, redis_client: aioredis.Redis
) -> dict[str, Any]:
    """Return {"ok": True} when the lead may send right now, else a dict
    describing the deferral with either retry_in (seconds) or retry_at (ISO datetime).
    """
    cid = str(campaign.id)

    last_sent_raw = await redis_client.get(f"rate:{cid}:last_sent")
    if last_sent_raw:
        try:
            last_sent_ts = float(last_sent_raw)
        except (TypeError, ValueError):
            last_sent_ts = 0.0
        elapsed = time.time() - last_sent_ts
        if elapsed < campaign.min_delay_seconds:
            return {
                "ok": False,
                "reason": "min_delay",
                "retry_in": int(campaign.min_delay_seconds - elapsed) + 1,
            }

    if campaign.max_per_hour is not None:
        hour_raw = await redis_client.get(f"rate:{cid}:hour")
        hour_count = int(hour_raw) if hour_raw else 0
        if hour_count >= campaign.max_per_hour:
            return {"ok": False, "reason": "hourly_cap", "retry_in": 60}

    if campaign.max_per_day is not None:
        day_raw = await redis_client.get(f"rate:{cid}:day")
        day_count = int(day_raw) if day_raw else 0
        if day_count >= campaign.max_per_day:
            tz = pytz.timezone(campaign.schedule_timezone or "UTC")
            tomorrow = (
                datetime.now(tz) + timedelta(days=1)
            ).replace(hour=0, minute=0, second=0, microsecond=0)
            return {
                "ok": False,
                "reason": "daily_cap",
                "retry_at": tomorrow.isoformat(),
            }

    return {"ok": True}


async def increment_rate_counters(
    campaign: Campaign, redis_client: aioredis.Redis
) -> None:
    cid = str(campaign.id)
    pipe = redis_client.pipeline()
    pipe.incr(f"rate:{cid}:hour")
    pipe.expire(f"rate:{cid}:hour", 3600, nx=True)
    pipe.incr(f"rate:{cid}:day")
    pipe.expire(f"rate:{cid}:day", 86400, nx=True)
    pipe.set(
        f"rate:{cid}:last_sent",
        str(time.time()),
        ex=max(campaign.min_delay_seconds + 10, 60),
    )
    await pipe.execute()


# --------------------------------------------------------------------------
# Async core
# --------------------------------------------------------------------------


async def _mark_send_failed(lead_id: str) -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with AsyncSession(engine) as session:
            lead = await session.get(Lead, uuid.UUID(lead_id))
            if lead is not None:
                lead.send_status = SendStatus.FAILED
                await session.commit()
    finally:
        await engine.dispose()


async def send_lead_async(lead_id: str) -> dict[str, Any]:
    lid = uuid.UUID(str(lead_id))
    engine = create_async_engine(settings.DATABASE_URL)
    redis_client = _new_redis()

    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            lead = await session.get(Lead, lid)
            if lead is None:
                return {"status": "not_found"}
            campaign = await session.get(Campaign, lead.campaign_id)
            if campaign is None:
                return {"status": "not_found"}

            if lead.send_status == SendStatus.SENT:
                return {"status": "already_sent"}

            # 1. Suppression list
            sup = await session.scalar(
                select(Suppression).where(Suppression.email == lead.email)
            )
            if sup is not None:
                lead.send_status = SendStatus.FAILED
                await session.commit()
                return {"status": "suppressed"}

            # 2. Campaign paused
            if campaign.status == CampaignStatus.PAUSED:
                return {"status": "paused"}

            # 3. Schedule window
            eta = compute_next_send_window(campaign)
            if eta is not None:
                lead.send_status = SendStatus.SCHEDULED
                lead.scheduled_send_at = eta
                await session.commit()
                return {"status": "scheduled", "eta": eta.isoformat()}

            # Snapshot for use after the session closes.
            full_name = " ".join(
                filter(None, [lead.first_name, lead.last_name])
            ) or None
            ctx = {
                "to_email": lead.email,
                "to_name": full_name,
                "subject": lead.composed_subject or "",
                "body": lead.composed_body or "",
                "sender_name": campaign.sender_name,
                "sender_email": campaign.sender_email,
                "campaign_id": str(campaign.id),
                "lead_id": str(lead.id),
            }
            campaign_snap = campaign  # safe to use outside session for read-only attrs

        # 4. Rate limits
        rate = await check_rate_limits(campaign_snap, redis_client)
        if not rate.get("ok"):
            return {"status": "rate_limited", **rate}

        # 5. Render
        html_body = render_html(ctx["body"])
        text_body = render_text(ctx["body"])

        # 6. Send via Brevo
        message_id = await brevo.send_email(
            to_email=ctx["to_email"],
            to_name=ctx["to_name"],
            subject=ctx["subject"],
            html_body=html_body,
            text_body=text_body,
            sender_name=ctx["sender_name"],
            sender_email=ctx["sender_email"],
            campaign_id=ctx["campaign_id"],
            lead_id=ctx["lead_id"],
        )

        # 7. Persist sent state
        async with AsyncSession(engine, expire_on_commit=False) as session:
            lead = await session.get(Lead, lid)
            if lead is None:
                return {"status": "not_found"}
            lead.brevo_message_id = message_id
            lead.send_status = SendStatus.SENT
            await session.commit()

        # 8. Bump rate counters
        await increment_rate_counters(campaign_snap, redis_client)
    finally:
        await engine.dispose()
        await redis_client.aclose()

    return {"status": "sent", "message_id": message_id}


# --------------------------------------------------------------------------
# Celery wrapper
# --------------------------------------------------------------------------


@celery_app.task(bind=True, name="send.send_lead", max_retries=3)
def send_lead(self, lead_id: str) -> dict[str, Any]:  # noqa: D401
    try:
        result = asyncio.run(send_lead_async(lead_id))
    except Exception as exc:  # noqa: BLE001
        logger.exception("send_lead transient failure for %s", lead_id)
        try:
            raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))
        except self.MaxRetriesExceededError:
            asyncio.run(_mark_send_failed(lead_id))
            return {"status": "failed", "error": str(exc)}

    status = result.get("status")
    if status == "paused":
        send_lead.apply_async(args=[lead_id], countdown=300)
    elif status == "scheduled":
        eta_str = result.get("eta")
        if eta_str:
            send_lead.apply_async(args=[lead_id], eta=datetime.fromisoformat(eta_str))
    elif status == "rate_limited":
        retry_at = result.get("retry_at")
        if retry_at:
            send_lead.apply_async(args=[lead_id], eta=datetime.fromisoformat(retry_at))
        else:
            countdown = int(result.get("retry_in") or 60)
            send_lead.apply_async(args=[lead_id], countdown=countdown)

    return result
