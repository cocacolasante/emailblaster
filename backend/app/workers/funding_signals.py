"""Nonprofit funding discovery workers (feeds prospect_signals).

A discovery PATH into the existing signals review queue — the sibling
relationship social_listening has with signals.

  funding.poll_usaspending — daily: recent nonprofit grant awards.
  funding.poll_irs_bmf      — monthly: newly-ruled 501(c)(3)s.

Autonomy boundary (mirrors workers/signals._apply_signal_actions): a
discovery signal MAY stage a campaign-less Lead (campaign_id=None),
create a "Reach out" CRM task, and send ONE owner notification.  It MAY
NEVER set campaign_id or create a sequence enrollment — reaching out
stays one human click away on the Signals page.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.models import (
    CrmActivity,
    CrmActivityType,
    FundingSourceState,
    Lead,
    NotificationKind,
    ProspectSignal,
)
from app.services import agent_core, notifications
from app.services.funding_sources import enrichment, irs_bmf, usaspending
from app.services.funding_sources.base import DiscoveredOrg
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

USASPENDING_SOURCE = "usaspending"
IRS_BMF_SOURCE = "irs_bmf"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Manual stop — hard-kill an in-flight run + break the redelivery loop
# ---------------------------------------------------------------------------


def _purge_broker_messages(task_name: str) -> dict[str, int]:
    """Remove every queued + unacked broker message for ``task_name``.

    The IRS feed can run far longer than the broker ``visibility_timeout``
    (300s); when it does, Redis restores the message and another worker
    picks it up, stacking concurrent copies that each burn Anthropic
    enrichment tokens.  Terminating the running task isn't enough on its
    own — the unacked copy would just be redelivered.  This clears both
    the ready queue and the in-flight ``unacked`` set so it stays dead.
    """
    needle = task_name.encode()
    queue = unacked = 0
    try:
        import redis  # redis-py sync client (already a dependency)

        r = redis.Redis.from_url(celery_app.conf.broker_url)
        for raw in r.lrange("celery", 0, -1):
            if needle in raw:
                queue += r.lrem("celery", 0, raw)
        for field, val in r.hgetall("unacked").items():
            if needle in val:
                r.hdel("unacked", field)
                r.zrem(
                    "unacked_index",
                    field.decode() if isinstance(field, bytes) else field,
                )
                unacked += 1
    except Exception:  # noqa: BLE001 — best-effort; never raise out of a stop
        logger.exception("funding stop: broker purge failed for %s", task_name)
    return {"queue": queue, "unacked": unacked}


def stop_funding_run(source: str) -> dict[str, Any]:
    """Hard-stop any in-flight run of one feed.

    Revokes + SIGKILLs every active/reserved Celery task for the feed's
    task name across all workers, then purges queued + redelivered copies
    from the broker so the run can't be restored past the visibility
    timeout.  Safe no-op when nothing is running (zero counts).
    """
    task_name = f"funding.poll_{source}"
    ids: set[str] = set()
    try:
        insp = celery_app.control.inspect(timeout=2.0)
        for snapshot in (insp.active(), insp.reserved()):
            for worker_tasks in (snapshot or {}).values():
                for t in worker_tasks:
                    if t.get("name") == task_name and t.get("id"):
                        ids.add(t["id"])
    except Exception:  # noqa: BLE001
        logger.exception("funding stop: inspect failed for %s", source)

    for tid in ids:
        try:
            celery_app.control.revoke(tid, terminate=True, signal="SIGKILL")
        except Exception:  # noqa: BLE001
            logger.exception("funding stop: revoke failed for %s", tid)

    purged = _purge_broker_messages(task_name)
    return {
        "task": task_name,
        "terminated": sorted(ids),
        "purged_queued": purged["queue"],
        "purged_unacked": purged["unacked"],
    }


def _yyyymm(d: date, minus_months: int = 0) -> str:
    """``d`` shifted back ``minus_months`` calendar months, as YYYYMM."""
    total = d.year * 12 + (d.month - 1) - minus_months
    return f"{total // 12:04d}{total % 12 + 1:02d}"


def _env_enabled(source: str) -> bool:
    return (
        settings.USASPENDING_ENABLED if source == USASPENDING_SOURCE
        else settings.IRS_BMF_ENABLED
    )


def _env_config(source: str) -> dict[str, Any]:
    if source == USASPENDING_SOURCE:
        return {"lookback_days": settings.USASPENDING_LOOKBACK_DAYS}
    return {
        "ruling_lookback_months": settings.IRS_BMF_RULING_LOOKBACK_MONTHS,
        "states": list(settings.IRS_BMF_STATES),
    }


async def _get_or_create_state(session: AsyncSession, source: str) -> FundingSourceState:
    """Fetch the per-source state, seeding ``enabled``/``config`` from the
    env defaults when unset (first run, or NULL after the 0033 migration).
    The env vars are the SEED; the DB row is authoritative afterward, so
    the Settings → Discovery panel can override them live."""
    state = await session.get(FundingSourceState, source)
    if state is None:
        state = FundingSourceState(source=source, cursor={})
        session.add(state)
    if state.enabled is None:
        state.enabled = _env_enabled(source)
    if state.config is None:
        state.config = _env_config(source)
    await session.flush()
    return state


async def _stage_discovery_signal(
    session: AsyncSession, src: str, org: DiscoveredOrg,
) -> dict[str, Any]:
    """Dedup → enrich → (maybe) stage a campaign-less Lead + CRM task →
    ProspectSignal + one owner notification.  Never touches a campaign.
    Returns {"staged", "lead_created", "task"}."""
    exists = await session.scalar(
        select(ProspectSignal.id).where(
            ProspectSignal.dedup_key == org.dedup_key
        ).limit(1)
    )
    if exists is not None:
        return {"staged": False, "lead_created": False, "task": False}

    agent_settings = await agent_core.get_agent_settings(session)
    contact = await enrichment.resolve_contact(org)

    lead_id = None
    lead_created = False
    task_created = False
    if contact and contact.get("email"):
        email = contact["email"].strip().lower()
        existing = await session.scalar(
            select(Lead).where(Lead.email == email).limit(1)
        )
        if existing is not None:
            lead = existing
        else:
            lead = Lead(
                campaign_id=None,                      # NEVER a campaign
                email=email,
                first_name=contact.get("first_name"),
                last_name=contact.get("last_name"),
                company=org.org_name,
                company_website=org.website,
                job_title=contact.get("title"),
                research_data={
                    "ein": org.ein,
                    "ntee": org.ntee_code,
                    "source": src,
                    **(org.detail or {}),
                },
            )
            session.add(lead)
            await session.flush()
            lead_created = True
        lead_id = lead.id

        # Reach-out task on the staged lead.  reminder_sent_at=now so the
        # agent reminder sweeper doesn't double-notify — the signal
        # notification below is the ping.
        task = CrmActivity(
            lead_id=lead_id,
            activity_type=CrmActivityType.TASK,
            subject=f"Reach out — {org.summary}"[:300],
            body=(
                f"Discovered via {src} ({org.signal_type}). "
                "Review the signal and reach out while it's fresh."
            ),
            due_at=agent_core.next_business_day(_now()),
            is_agent_generated=True,
            reminder_sent_at=_now(),
        )
        session.add(task)
        await session.flush()
        task_created = True

    signal = ProspectSignal(
        watch_id=None,
        source=src,
        signal_type=org.signal_type,
        summary=org.summary,
        # Persist the org identity alongside the feed payload so a later
        # on-demand "Find contact" enrichment can rebuild the org without
        # re-parsing the summary.
        detail={
            **(org.detail or {}),
            "org_name": org.org_name,
            "website": org.website,
            "state": org.state,
            "ein": org.ein,
            "ntee_code": org.ntee_code,
        },
        dedup_key=org.dedup_key,
        lead_id=lead_id,
    )
    session.add(signal)
    await session.flush()

    await notifications.notify(
        session,
        agent_settings,
        kind=NotificationKind.PROSPECT_SIGNAL,
        title=org.summary[:300],
        body=(
            f"Source: {src} ({org.signal_type})\n"
            + (f"New CRM lead staged: {contact['email']}\n" if lead_created else "")
            + (f"State: {org.state}\n" if org.state else "")
            + "Open the Signals page to action or dismiss."
        ),
        dedup_key=f"prospect_signal:{org.dedup_key}",
        lead_id=lead_id,
    )
    return {"staged": True, "lead_created": lead_created, "task": task_created}


async def _stage_all(
    session: AsyncSession, src: str, orgs: list[DiscoveredOrg],
) -> dict[str, int]:
    """Stage each org in its OWN transaction so one bad org (failed
    enrichment, notification hiccup) can't roll back the whole batch and
    a dedup guard makes the whole run idempotent."""
    staged = leads = tasks = 0
    for org in orgs:
        try:
            r = await _stage_discovery_signal(session, src, org)
            await session.commit()
            staged += int(r["staged"])
            leads += int(r["lead_created"])
            tasks += int(r["task"])
        except Exception:  # noqa: BLE001 — one org must not abort the run
            await session.rollback()
            logger.exception("funding stage failed for %s", org.dedup_key)
    return {"staged": staged, "leads_created": leads, "tasks_created": tasks}


# ---------------------------------------------------------------------------
# USAspending — daily
# ---------------------------------------------------------------------------


async def _poll_usaspending_async() -> dict[str, Any]:
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            try:
                state = await _get_or_create_state(session, USASPENDING_SOURCE)
                if not state.enabled:
                    await session.commit()
                    return {"skipped": "disabled"}
                cfg = state.config or {}
                cur = dict(state.cursor or {})
                today = _now().date()
                # Always query a TRAILING window of `lookback_days`.  We do
                # NOT advance a since-cursor: USAspending action_date data
                # lags reporting by days-to-weeks, so an award only becomes
                # visible in the API after its action_date has already passed
                # — a cursor parked at the last run day would skip every
                # back-dated award forever (the "0 signals" bug).  Re-scanning
                # the full window each poll is safe and free: the dedup_key
                # guard in `_stage_discovery_signal` (+ the UNIQUE on
                # prospect_signals) makes an already-seen award a no-op, so no
                # duplicate signal/lead/Anthropic/Hunter work happens on overlap.
                lookback = max(
                    int(cfg.get("lookback_days") or settings.USASPENDING_LOOKBACK_DAYS),
                    1,
                )
                since = today - timedelta(days=lookback)
                state.last_run_at = _now()
                state.last_run_status = "running"
                await session.commit()  # release the state row before slow I/O

                orgs = await usaspending.fetch_recent_awards(since, today, limit=100)
                counts = await _stage_all(session, USASPENDING_SOURCE, orgs)

                state = await _get_or_create_state(session, USASPENDING_SOURCE)
                state.cursor = {
                    **cur,
                    "last_window_start": since.isoformat(),
                    "last_run_date": today.isoformat(),
                }
                state.last_run_at = _now()
                state.last_run_status = "done"
                await session.commit()
                return {"fetched": len(orgs), **counts}
            except Exception:
                await session.rollback()
                state = await _get_or_create_state(session, USASPENDING_SOURCE)
                state.last_run_at = _now()
                state.last_run_status = "error"
                await session.commit()
                raise
    finally:
        await engine.dispose()


@celery_app.task(name="funding.poll_usaspending")
def poll_usaspending() -> dict[str, Any]:
    return asyncio.run(_poll_usaspending_async())


# ---------------------------------------------------------------------------
# IRS EO BMF — monthly
# ---------------------------------------------------------------------------


async def _poll_irs_bmf_async() -> dict[str, Any]:
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            try:
                state = await _get_or_create_state(session, IRS_BMF_SOURCE)
                cfg = state.config or {}
                states = list(cfg.get("states") or [])
                if not state.enabled or not states:
                    await session.commit()
                    return {"skipped": "disabled"}
                cur = dict(state.cursor or {})
                today = _now().date()
                lookback_months = int(
                    cfg.get("ruling_lookback_months")
                    or settings.IRS_BMF_RULING_LOOKBACK_MONTHS
                )
                floor = _yyyymm(today, lookback_months)
                last = cur.get("last_file_month")
                # FIRST RUN GUARD: with no cursor, bound to the lookback so
                # we never blast the entire historical file.
                since_ruling = floor if last is None else max(last, floor)
                state.last_run_at = _now()
                state.last_run_status = "running"
                await session.commit()

                orgs = await irs_bmf.fetch_new_501c3(states, since_ruling)
                counts = await _stage_all(session, IRS_BMF_SOURCE, orgs)

                state = await _get_or_create_state(session, IRS_BMF_SOURCE)
                state.cursor = {**cur, "last_file_month": _yyyymm(today)}
                state.last_run_at = _now()
                state.last_run_status = "done"
                await session.commit()
                return {"fetched": len(orgs), "since_ruling": since_ruling, **counts}
            except Exception:
                await session.rollback()
                state = await _get_or_create_state(session, IRS_BMF_SOURCE)
                state.last_run_at = _now()
                state.last_run_status = "error"
                await session.commit()
                raise
    finally:
        await engine.dispose()


@celery_app.task(name="funding.poll_irs_bmf")
def poll_irs_bmf() -> dict[str, Any]:
    return asyncio.run(_poll_irs_bmf_async())
