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
    ConnectedAccount,
    EmailEvent,
    EmailEventType,
    Lead,
    SendStatus,
    Suppression,
    canonical_email,
)
from app.services import brevo
from app.services.email_template import render_html, render_text  # noqa: F401 — render_* kept for callers/tests that patch here
from app.services.signature import render_email_with_signature, resolve_campaign_signature
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
# Send-time optimization
# --------------------------------------------------------------------------

# Default optimal window when no engagement data exists: weekday mornings.
DEFAULT_OPTIMAL_HOURS = (9, 10, 11)
DEFAULT_OPTIMAL_DAYS = (1, 2, 3)  # Tue-Thu (Monday=0)
# Minimum engagement events before we trust a derived hour profile.
_LEAD_ENGAGEMENT_MIN_EVENTS = 2
_CAMPAIGN_ENGAGEMENT_MIN_EVENTS = 10
_OPTIMAL_SEARCH_HOURS = 7 * 24  # give up past a week — send normally


async def _engagement_hours(
    session: AsyncSession,
    tz: pytz.BaseTzInfo,
    *,
    lead_id: uuid.UUID | None = None,
    campaign_id: uuid.UUID | None = None,
) -> list[int]:
    """Top open/click hours (in ``tz``) for one lead or a whole campaign.
    Empty list when the sample is too thin to trust."""
    q = select(EmailEvent.occurred_at).where(
        EmailEvent.event_type.in_([EmailEventType.OPENED, EmailEventType.CLICKED])
    )
    min_events = _CAMPAIGN_ENGAGEMENT_MIN_EVENTS
    if lead_id is not None:
        q = q.where(EmailEvent.lead_id == lead_id)
        min_events = _LEAD_ENGAGEMENT_MIN_EVENTS
    elif campaign_id is not None:
        q = q.where(EmailEvent.campaign_id == campaign_id)
    rows = (await session.execute(q.limit(500))).scalars().all()
    if len(rows) < min_events:
        return []
    counts: dict[int, int] = {}
    for ts in rows:
        local = ts.astimezone(tz) if ts.tzinfo else pytz.UTC.localize(ts).astimezone(tz)
        counts[local.hour] = counts.get(local.hour, 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
    return [h for h, _ in top]


async def compute_optimal_send_eta(
    session: AsyncSession,
    lead: Lead,
    campaign: Campaign,
    now: datetime | None = None,
) -> datetime | None:
    """Next datetime landing in both the recipient's optimal local hour AND
    the campaign's send window.  ``None`` when "now" is already optimal (or
    no qualifying slot exists within a week — send normally rather than
    stall).  Only called when ``campaign.send_time_optimization`` is on.

    Hour selection, best-first: this lead's own open/click hours →
    campaign-wide aggregate → the 9-11am Tue-Thu default.  The Tue-Thu
    day preference only applies on the default profile — engagement-
    derived hours fire on any campaign-allowed day.
    """
    try:
        tz = pytz.timezone(lead.timezone or campaign.schedule_timezone or "UTC")
    except Exception:  # noqa: BLE001 — bad tz string from enrichment
        tz = pytz.timezone(campaign.schedule_timezone or "UTC")

    hours = await _engagement_hours(session, tz, lead_id=lead.id)
    days: set[int] | None = None
    if not hours:
        hours = await _engagement_hours(session, tz, campaign_id=campaign.id)
    if not hours:
        hours = list(DEFAULT_OPTIMAL_HOURS)
        days = set(DEFAULT_OPTIMAL_DAYS)
    hour_set = set(hours)

    now_utc = now or datetime.now(pytz.UTC)
    if now_utc.tzinfo is None:
        now_utc = pytz.UTC.localize(now_utc)
    now_local = now_utc.astimezone(tz)

    def _qualifies(dt_local: datetime) -> bool:
        if dt_local.hour not in hour_set:
            return False
        if days is not None and dt_local.weekday() not in days:
            return False
        # Must also be inside the campaign's own send window.
        return compute_next_send_window(campaign, now=dt_local) is None

    if _qualifies(now_local):
        return None

    candidate = now_local.replace(minute=0, second=0, microsecond=0)
    for _ in range(_OPTIMAL_SEARCH_HOURS):
        candidate += timedelta(hours=1)
        if _qualifies(candidate):
            # Land a few minutes past the hour so it doesn't look robotic.
            return candidate.replace(minute=7)
    return None  # nothing qualifying within a week — don't stall the send


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------


async def resolve_sending_domain(
    session: AsyncSession, campaign: Campaign
) -> str | None:
    """The campaign's sending domain for per-domain throttling: the domain
    of its ConnectedAccount.email_address, falling back to the campaign's
    sender_email domain when no account is bound."""
    email = None
    if campaign.connected_account_id is not None:
        email = await session.scalar(
            select(ConnectedAccount.email_address).where(
                ConnectedAccount.id == campaign.connected_account_id
            )
        )
    email = email or campaign.sender_email or ""
    if "@" not in email:
        return None
    return email.rsplit("@", 1)[1].strip().lower() or None


async def check_rate_limits(
    campaign: Campaign,
    redis_client: aioredis.Redis,
    domain: str | None = None,
) -> dict[str, Any]:
    """Return {"ok": True} when the lead may send right now, else a dict
    describing the deferral with either retry_in (seconds) or retry_at (ISO datetime).

    The min-delay check ATOMICALLY claims the window via ``SET NX EX`` — this
    is the single serialization point that stops a burst of concurrent send
    tasks (Celery prefork runs several at once) from all reading a stale
    ``last_sent`` and firing together (the "bulk of N" the user saw).  Only
    one task per ``min_delay_seconds`` claims the gate; the rest defer and
    reschedule.  ``SET NX EX`` is atomic on both real Redis and fakeredis (no
    Lua needed).
    """
    cid = str(campaign.id)

    # Hard caps first (read-only).  Safe under the min-gate serialization
    # below — only one send claims the gate per window, so caps aren't raced.
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

    # Per-SENDING-DOMAIN caps — shared across every campaign sending from
    # this domain, so parallel campaigns can't jointly burn its reputation.
    # Same read-only check pattern as the per-campaign caps; the min-gate
    # below serializes, so they aren't raced any worse than those.
    if domain:
        dom_hour_raw = await redis_client.get(f"rate:domain:{domain}:hour")
        if int(dom_hour_raw or 0) >= settings.DOMAIN_MAX_PER_HOUR:
            return {"ok": False, "reason": "domain_hourly_cap", "retry_in": 300}
        dom_day_raw = await redis_client.get(f"rate:domain:{domain}:day")
        if int(dom_day_raw or 0) >= settings.DOMAIN_MAX_PER_DAY:
            tz = pytz.timezone(campaign.schedule_timezone or "UTC")
            tomorrow = (
                datetime.now(tz) + timedelta(days=1)
            ).replace(hour=0, minute=0, second=0, microsecond=0)
            return {
                "ok": False,
                "reason": "domain_daily_cap",
                "retry_at": tomorrow.isoformat(),
            }

    # Atomic min-delay claim.  Whoever sets the gate key first owns this
    # window; everyone else gets the key's TTL back as their retry interval.
    min_delay = campaign.min_delay_seconds or 0
    if min_delay > 0:
        gate_key = f"rate:{cid}:min_gate"
        claimed = await redis_client.set(
            gate_key, str(time.time()), nx=True, ex=min_delay
        )
        if not claimed:
            ttl = await redis_client.ttl(gate_key)
            return {
                "ok": False,
                "reason": "min_delay",
                "retry_in": max(int(ttl), 1),
            }

    return {"ok": True}


def _seconds_until_local_midnight(tz_name: str | None) -> int:
    """Seconds until the next local midnight in ``tz_name`` (the campaign's
    timezone), so the DAILY cap resets on the calendar-day boundary rather
    than as a rolling 24h window anchored to the day's first send.  Falls
    back to UTC on a missing/invalid timezone.
    """
    try:
        tz = pytz.timezone(tz_name or "UTC")
    except Exception:  # noqa: BLE001 — bad tz string → safe default
        tz = pytz.UTC
    now_local = datetime.now(tz)
    tomorrow = (now_local + timedelta(days=1)).date()
    next_midnight = tz.localize(
        datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0)
    )
    secs = int((next_midnight - now_local).total_seconds())
    return secs if 0 < secs <= 86400 else 86400


async def increment_rate_counters(
    campaign: Campaign,
    redis_client: aioredis.Redis,
    domain: str | None = None,
) -> None:
    """Count a successful send against the hourly/daily caps (campaign AND
    sending-domain when ``domain`` is provided).

    The min-delay window is claimed atomically in ``check_rate_limits`` (the
    ``min_gate`` key), so it is NOT set here — this only bumps the caps.  The
    daily counter expires at the next local midnight (calendar-day reset),
    matching the ``daily_cap`` retry_at; the hourly counter stays a rolling
    60-minute window.
    """
    cid = str(campaign.id)
    day_ttl = _seconds_until_local_midnight(campaign.schedule_timezone)
    pipe = redis_client.pipeline()
    pipe.incr(f"rate:{cid}:hour")
    pipe.expire(f"rate:{cid}:hour", 3600, nx=True)
    pipe.incr(f"rate:{cid}:day")
    pipe.expire(f"rate:{cid}:day", day_ttl, nx=True)
    if domain:
        pipe.incr(f"rate:domain:{domain}:hour")
        pipe.expire(f"rate:domain:{domain}:hour", 3600, nx=True)
        pipe.incr(f"rate:domain:{domain}:day")
        pipe.expire(f"rate:domain:{domain}:day", day_ttl, nx=True)
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


async def check_send_gates(
    session: AsyncSession,
    lead: Lead,
    campaign: Campaign,
    redis_client: aioredis.Redis,
) -> dict[str, Any]:
    """Shared pre-send checks for both the legacy first-email path and the
    sequencer's follow-up-email step.

    Returns one of:
      ``{"ok": True}`` — green light, caller may render + send.
      ``{"ok": False, "reason": "suppressed"}`` — permanent skip.
      ``{"ok": False, "reason": "paused"}`` — defer indefinitely while
          the campaign is paused.
      ``{"ok": False, "reason": "scheduled", "retry_at": <ISO>}`` —
          defer until the next valid send window.
      ``{"ok": False, "reason": "min_delay" | "hourly_cap" | "daily_cap",
          "retry_in": <int seconds>}`` or ``"retry_at": <ISO>`` — defer
          per Brevo / per-campaign rate-limit policy.
    """
    sup = await session.scalar(
        select(Suppression).where(Suppression.email == canonical_email(lead.email))
    )
    if sup is not None:
        return {"ok": False, "reason": "suppressed"}

    if campaign.status == CampaignStatus.PAUSED:
        return {"ok": False, "reason": "paused"}

    eta = compute_next_send_window(campaign)
    if eta is not None:
        return {"ok": False, "reason": "scheduled", "retry_at": eta.isoformat()}

    # Send-time optimization: defer to the recipient's optimal local hour.
    # Checked AFTER paused/window (those always win) and BEFORE the rate
    # claim (an optimization deferral must not burn a min-delay slot).  The
    # deferred lead re-enters every gate at its ETA, so caps still apply.
    if campaign.send_time_optimization:
        opt_eta = await compute_optimal_send_eta(session, lead, campaign)
        if opt_eta is not None:
            return {
                "ok": False,
                "reason": "scheduled",
                "retry_at": opt_eta.isoformat(),
                "optimized": True,
            }

    domain = await resolve_sending_domain(session, campaign)
    rate = await check_rate_limits(campaign, redis_client, domain=domain)
    if not rate.get("ok"):
        return {"ok": False, **rate}

    # Hand the resolved domain back so the caller can bump the domain
    # counters in increment_rate_counters without re-resolving.
    return {"ok": True, "domain": domain}


async def send_lead_async(lead_id: str) -> dict[str, Any]:
    lid = uuid.UUID(str(lead_id))
    engine = create_async_engine(settings.DATABASE_URL)
    redis_client = _new_redis()

    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            # Row-level lock prevents two concurrent invocations (e.g. a
            # rate-limit retry colliding with a late beat-dispatched run)
            # from both reading PENDING and double-POSTing to Brevo.  We
            # acquire the lock on the Lead row only — the Campaign read
            # below is a snapshot read.
            lead = await session.scalar(
                select(Lead).where(Lead.id == lid).with_for_update()
            )
            if lead is None:
                return {"status": "not_found"}
            campaign = await session.get(Campaign, lead.campaign_id)
            if campaign is None:
                return {"status": "not_found"}

            if lead.send_status == SendStatus.SENT:
                return {"status": "already_sent"}

            # Defensive: a campaign whose first step isn't an email has no
            # composed body (compose skipped it).  Never send an empty email
            # even if a stray dispatch reaches here.
            if not (lead.composed_body or "").strip():
                return {"status": "skipped_no_body"}

            gates = await check_send_gates(session, lead, campaign, redis_client)
            sending_domain = gates.get("domain")
            if not gates.get("ok"):
                reason = gates["reason"]
                if reason == "suppressed":
                    # Deliberate ignore / unsubscribe / bounce — a
                    # TERMINAL state distinct from FAILED so the lead
                    # doesn't surface in the campaign error list or get
                    # re-enqueued by retry-failed in a deterministic
                    # fail loop.
                    lead.send_status = SendStatus.SUPPRESSED
                    await session.commit()
                    return {"status": "suppressed"}
                if reason == "paused":
                    return {"status": "paused"}
                if reason == "scheduled":
                    lead.send_status = SendStatus.SCHEDULED
                    # ``compute_next_send_window`` returned a tz-aware dt;
                    # round-trip via ISO so the gate dict + the model
                    # value match exactly.
                    lead.scheduled_send_at = datetime.fromisoformat(gates["retry_at"])
                    await session.commit()
                    return {"status": "scheduled", "eta": gates["retry_at"]}
                # rate-limit family (min_delay / hourly_cap / daily_cap)
                return {"status": "rate_limited", **{k: v for k, v in gates.items() if k != "ok"}}

            # Snapshot for the Brevo call (avoid holding ORM-detached refs).
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
            campaign_snap = campaign

            # Render + Brevo POST happen INSIDE the locked session.  A
            # concurrent send_lead_async for the same lead is blocked on
            # the row lock; when it unblocks (after our commit below) it
            # reads SendStatus.SENT and short-circuits, preventing the
            # double-send the audit flagged.
            #
            # render_email_with_signature is the SAME renderer the
            # research-client one-off send uses: an HTML signature
            # (toolbar-inserted <a>/<img>) renders as real HTML here too,
            # instead of being escaped into visible angle brackets.  The
            # composed_body already carries the signature text (merged by
            # apply_signature at compose time) — the helper strips that
            # tail idempotently before re-rendering, so nothing doubles.
            #
            # Use the EFFECTIVE signature (campaign override → connected
            # account's Settings signature), NOT campaign.signature.  When a
            # campaign inherits its account's signature, campaign.signature
            # is None — passing that here would strip the sign-off and append
            # nothing, sending an UNSIGNED email (the bug this fixes).
            effective_signature = await resolve_campaign_signature(session, campaign)
            html_body, text_body = render_email_with_signature(
                ctx["body"], effective_signature,
            )
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

            lead.brevo_message_id = message_id
            lead.send_status = SendStatus.SENT
            await session.commit()

        # Bump rate counters AFTER the row lock is released (commit at
        # session-exit).  Counter bump errors must not roll back the
        # persisted SENT state.
        await increment_rate_counters(campaign_snap, redis_client, domain=sending_domain)
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
        # Hard stop: pause means stop the queue.  We don't self-re-enqueue
        # (the old behaviour was a 5-min loop that kept the queue churning
        # for every paused lead) — `resume_campaign` re-enqueues every
        # composed PENDING+SCHEDULED lead, so the resume IS the trigger.
        # Lead's send_status is whatever the gate left it (PENDING for the
        # legacy first-email, possibly SCHEDULED if a prior pass deferred
        # it on the schedule window); either way resume picks it up.
        return result
    if status == "scheduled":
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
