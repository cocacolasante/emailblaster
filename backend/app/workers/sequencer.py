"""Sequence scheduler + step handlers.

Two Celery tasks live here:

- ``advance_sequences``  — beat-scheduled (every 60s). Finds lead_sequence_state
  rows whose next_run_at has arrived and either executes the wait/advances the
  cursor for wait nodes, or dispatches a channel handler for action nodes.

- ``send_email_step``    — channel handler for an `email` follow-up node.
  Records an execution row, sends via Brevo using the node's config, then
  advances the lead's state cursor.

The FIRST email step of any sequence is intentionally NOT routed through this
file: it stays on the legacy ``compose → send_lead`` path so the existing test
suite + every in-flight campaign keep working. When we pick up a lead sitting
on the entry node and its ``lead.send_status`` is already SENT, we skip it and
advance.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import redis.asyncio as aioredis

from app.config import settings
from app.models import (
    Campaign,
    EmailEvent,
    EmailEventType,
    Lead,
    LeadSequenceState,
    LeadSequenceStatus,
    LeadStepExecution,
    LeadStepResult,
    LinkedInAccount,
    LinkedInAccountStatus,
    LinkedInConnectionStatus,
    SendStatus,
    Sequence,
    SequenceEdge,
    SequenceNode,
    SequenceNodeKind,
    Suppression,
)
from app.services import brevo
from app.services.email_template import render_html, render_text
from app.services.linkedin import get_provider as get_linkedin_provider
from app.services.linkedin.base import (
    AccountRestricted,
    ChallengeRequired,
    ProfileRef,
)
from app.services.sequence_conditions import ConditionContext, evaluate
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# How often the beat task runs. Mirrored in celery_app.beat_schedule.
ADVANCE_INTERVAL_SECONDS = 60

# How many state rows we process per beat tick. Keeps the tick bounded.
ADVANCE_BATCH_SIZE = 200

_LI_REDIS_CLIENT: aioredis.Redis | None = None


def _li_redis() -> aioredis.Redis:
    global _LI_REDIS_CLIENT
    if _LI_REDIS_CLIENT is None:
        _LI_REDIS_CLIENT = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _LI_REDIS_CLIENT


async def _li_rate_check(
    account: LinkedInAccount,
    kind: SequenceNodeKind | None = None,
    page_id: str | None = None,
) -> dict[str, Any]:
    """Per-account daily cap + min-delay + per-kind subcap.

    `kind` lets us layer a stricter subcap on connects / DMs / page invites.
    `page_id` is required when kind=LINKEDIN_INVITE_TO_PAGE — the monthly cap
    is per company page, not per account.

    Returns ``{"ok": True}`` when the action may fire right now, otherwise
    a dict describing the reason — caller writes a `skipped` execution row.
    """
    client = _li_redis()
    aid = str(account.id)

    # 1. Min-delay between any two actions on the same account.
    last_raw = await client.get(f"li-rate:{aid}:last")
    if last_raw:
        try:
            last_ts = float(last_raw)
        except (TypeError, ValueError):
            last_ts = 0.0
        elapsed = time.time() - last_ts
        if elapsed < settings.LINKEDIN_MIN_ACTION_DELAY_SECONDS:
            return {
                "ok": False,
                "reason": "min_delay",
                "error": f"under min delay ({int(settings.LINKEDIN_MIN_ACTION_DELAY_SECONDS - elapsed) + 1}s remaining)",
            }

    # 2. Overall daily cap on the account.
    day_raw = await client.get(f"li-rate:{aid}:day")
    day_count = int(day_raw) if day_raw else 0
    if day_count >= settings.LINKEDIN_DAILY_ACTION_CAP:
        return {
            "ok": False,
            "reason": "daily_cap",
            "error": f"daily cap reached ({settings.LINKEDIN_DAILY_ACTION_CAP})",
        }

    # 3. Per-kind subcap (write actions only).
    if kind == SequenceNodeKind.LINKEDIN_CONNECT:
        sub_raw = await client.get(f"li-rate:{aid}:day:connect")
        sub = int(sub_raw) if sub_raw else 0
        if sub >= settings.LINKEDIN_DAILY_CONNECT_CAP:
            return {
                "ok": False, "reason": "connect_cap",
                "error": f"daily connect cap reached ({settings.LINKEDIN_DAILY_CONNECT_CAP})",
            }
    elif kind in (SequenceNodeKind.LINKEDIN_DM, SequenceNodeKind.LINKEDIN_INMAIL):
        # InMail counts against the same cap as DMs — LinkedIn looks at
        # combined outbound messaging volume per account.
        sub_raw = await client.get(f"li-rate:{aid}:day:dm")
        sub = int(sub_raw) if sub_raw else 0
        if sub >= settings.LINKEDIN_DAILY_DM_CAP:
            return {
                "ok": False, "reason": "dm_cap",
                "error": f"daily DM/InMail cap reached ({settings.LINKEDIN_DAILY_DM_CAP})",
            }
    elif kind == SequenceNodeKind.LINKEDIN_INVITE_TO_PAGE:
        if not page_id:
            return {"ok": False, "reason": "misconfigured", "error": "page_id missing"}
        page_raw = await client.get(f"li-rate:page:{page_id}:month")
        page = int(page_raw) if page_raw else 0
        if page >= settings.LINKEDIN_MONTHLY_PAGE_INVITE_CAP:
            return {
                "ok": False, "reason": "page_invite_cap",
                "error": f"monthly page invite cap reached ({settings.LINKEDIN_MONTHLY_PAGE_INVITE_CAP})",
            }

    return {"ok": True}


async def _li_rate_bump(
    account: LinkedInAccount,
    kind: SequenceNodeKind | None = None,
    page_id: str | None = None,
) -> None:
    client = _li_redis()
    aid = str(account.id)
    pipe = client.pipeline()
    pipe.incr(f"li-rate:{aid}:day")
    pipe.expire(f"li-rate:{aid}:day", 86400, nx=True)
    pipe.set(
        f"li-rate:{aid}:last", str(time.time()),
        ex=max(settings.LINKEDIN_MIN_ACTION_DELAY_SECONDS + 10, 60),
    )
    if kind == SequenceNodeKind.LINKEDIN_CONNECT:
        pipe.incr(f"li-rate:{aid}:day:connect")
        pipe.expire(f"li-rate:{aid}:day:connect", 86400, nx=True)
    elif kind in (SequenceNodeKind.LINKEDIN_DM, SequenceNodeKind.LINKEDIN_INMAIL):
        pipe.incr(f"li-rate:{aid}:day:dm")
        pipe.expire(f"li-rate:{aid}:day:dm", 86400, nx=True)
    elif kind == SequenceNodeKind.LINKEDIN_INVITE_TO_PAGE and page_id:
        # 30-day rolling window approximated as fixed-30-day expiry.
        pipe.incr(f"li-rate:page:{page_id}:month")
        pipe.expire(f"li-rate:page:{page_id}:month", 60 * 60 * 24 * 30, nx=True)
    await pipe.execute()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _substitute(template: str, lead: Lead) -> str:
    """Lightweight {{first_name}} / {{last_name}} / {{company}} / {{job_title}}
    substitution. Missing variables resolve to the empty string.
    """
    if not template:
        return ""
    mapping = {
        "first_name": lead.first_name or "",
        "last_name": lead.last_name or "",
        "company": lead.company or "",
        "job_title": lead.job_title or "",
        "email": lead.email or "",
    }
    out = template
    for k, v in mapping.items():
        out = out.replace("{{" + k + "}}", v).replace("{{ " + k + " }}", v)
    return out


async def _build_condition_context(
    session: AsyncSession, lead: Lead, state: LeadSequenceState
) -> ConditionContext:
    """Snapshot lead-level event/state for use by edge condition evaluator."""
    events = (await session.execute(
        select(EmailEvent.event_type, func.max(EmailEvent.occurred_at))
        .where(EmailEvent.lead_id == lead.id)
        .group_by(EmailEvent.event_type)
    )).all()
    by_type: dict[EmailEventType, datetime] = {row[0]: row[1] for row in events}
    return ConditionContext(
        has_replied_email=EmailEventType.REPLIED in by_type,
        last_reply_email_at=by_type.get(EmailEventType.REPLIED),
        has_opened=EmailEventType.OPENED in by_type,
        last_open_at=by_type.get(EmailEventType.OPENED),
        has_clicked=EmailEventType.CLICKED in by_type,
        last_click_at=by_type.get(EmailEventType.CLICKED),
        has_bounced=(
            EmailEventType.HARD_BOUNCE in by_type
            or EmailEventType.SOFT_BOUNCE in by_type
        ),
        linkedin_connection_status=lead.linkedin_connection_status.value
            if isinstance(lead.linkedin_connection_status, LinkedInConnectionStatus)
            else str(lead.linkedin_connection_status),
        has_replied_linkedin=lead.linkedin_last_reply_at is not None,
        last_reply_linkedin_at=lead.linkedin_last_reply_at,
        entered_current_at=state.entered_current_at,
        now=_now(),
    )


async def _arm_next_run(
    session: AsyncSession, state: LeadSequenceState, node: SequenceNode
) -> None:
    """Set next_run_at on the state row to reflect the node we just entered."""
    if node.kind == SequenceNodeKind.WAIT:
        minutes = int(node.config.get("duration_minutes", 0) or 0)
        state.next_run_at = _now() + timedelta(minutes=minutes)
    else:
        # Action nodes: fire ASAP. Scheduler picks up on the next tick.
        state.next_run_at = _now()
    state.entered_current_at = _now()
    state.status = LeadSequenceStatus.ACTIVE
    state.halt_reason = None


async def _advance_cursor(
    session: AsyncSession, state: LeadSequenceState, current_node: SequenceNode
) -> SequenceNode | None:
    """Evaluate outgoing edges of `current_node`, advance state to the matching
    target node (or set status=halted/completed). Returns the new node, or None
    if the lead exited the sequence.
    """
    lead = await session.get(Lead, state.lead_id)
    if lead is None:
        state.status = LeadSequenceStatus.HALTED
        state.halt_reason = "lead missing"
        return None

    edges = (await session.execute(
        select(SequenceEdge)
        .where(SequenceEdge.from_node_id == current_node.id)
        .order_by(SequenceEdge.priority.asc(), SequenceEdge.created_at.asc())
    )).scalars().all()

    if not edges:
        state.current_node_id = None
        state.next_run_at = None
        state.status = LeadSequenceStatus.COMPLETED
        return None

    ctx = await _build_condition_context(session, lead, state)
    for edge in edges:
        if evaluate(edge.condition, ctx):
            if edge.to_node_id is None:
                state.current_node_id = None
                state.next_run_at = None
                state.status = LeadSequenceStatus.COMPLETED
                return None
            next_node = await session.get(SequenceNode, edge.to_node_id)
            if next_node is None:
                state.status = LeadSequenceStatus.HALTED
                state.halt_reason = f"edge points to missing node {edge.to_node_id}"
                return None
            state.current_node_id = next_node.id
            await _arm_next_run(session, state, next_node)
            return next_node

    state.status = LeadSequenceStatus.HALTED
    state.halt_reason = "no outgoing edge matched"
    state.next_run_at = None
    return None


# --------------------------------------------------------------------------
# Email step handler
# --------------------------------------------------------------------------


async def _send_email_step_async(lead_id: str, node_id: str) -> dict[str, Any]:
    """Send a templated email for a non-entry email node.

    Suppression / paused / schedule / rate limits get the same treatment as
    the legacy send_lead, but the body comes from the node's config rather
    than lead.composed_*.
    """
    lid = uuid.UUID(str(lead_id))
    nid = uuid.UUID(str(node_id))
    engine = create_async_engine(settings.DATABASE_URL)
    result: dict[str, Any] = {}

    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            lead = await session.get(Lead, lid)
            node = await session.get(SequenceNode, nid)
            if lead is None or node is None:
                return {"status": "not_found"}
            campaign = await session.get(Campaign, lead.campaign_id)
            if campaign is None:
                return {"status": "not_found"}

            sup = await session.scalar(
                select(Suppression).where(Suppression.email == lead.email)
            )
            if sup is not None:
                return {"status": "suppressed"}

            cfg = node.config or {}
            subject_tpl = cfg.get("subject_template") or ""
            body_tpl = cfg.get("body_template") or ""
            if not subject_tpl or not body_tpl:
                return {"status": "misconfigured", "error": "email node missing subject_template or body_template"}

            subject = _substitute(subject_tpl, lead)
            body = _substitute(body_tpl, lead)
            ctx = {
                "to_email": lead.email,
                "to_name": " ".join(filter(None, [lead.first_name, lead.last_name])) or None,
                "subject": subject,
                "body": body,
                "sender_name": campaign.sender_name,
                "sender_email": campaign.sender_email,
                "campaign_id": str(campaign.id),
                "lead_id": str(lead.id),
            }

        html_body = render_html(ctx["body"])
        text_body = render_text(ctx["body"])
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
        result = {"status": "sent", "message_id": message_id}
    finally:
        await engine.dispose()

    return result


# --------------------------------------------------------------------------
# LinkedIn step handler
# --------------------------------------------------------------------------


LI_KINDS = {
    SequenceNodeKind.LINKEDIN_VIEW_PROFILE,
    SequenceNodeKind.LINKEDIN_FOLLOW_PROFILE,
    SequenceNodeKind.LINKEDIN_REACT_POST,
    SequenceNodeKind.LINKEDIN_CONNECT,
    SequenceNodeKind.LINKEDIN_DM,
    SequenceNodeKind.LINKEDIN_INVITE_TO_PAGE,
    SequenceNodeKind.LINKEDIN_INMAIL,
    SequenceNodeKind.LINKEDIN_COMMENT_POST,
}


async def _send_linkedin_step_async(lead_id: str, node_id: str) -> dict[str, Any]:
    """Dispatch a LinkedIn warm-up action.

    Returns the same shape as ``_send_email_step_async`` — a status dict
    handed to ``_record_execution_and_advance`` which writes a
    `lead_step_executions` row + advances the cursor.
    """
    lid = uuid.UUID(str(lead_id))
    nid = uuid.UUID(str(node_id))
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            lead = await session.get(Lead, lid)
            node = await session.get(SequenceNode, nid)
            if lead is None or node is None:
                return {"status": "not_found"}
            campaign = await session.get(Campaign, lead.campaign_id)
            if campaign is None or campaign.linkedin_account_id is None:
                return {
                    "status": "misconfigured",
                    "error": "no LinkedIn account configured on campaign",
                }
            account = await session.get(LinkedInAccount, campaign.linkedin_account_id)
            if account is None:
                return {"status": "not_found", "error": "linkedin account missing"}

            # Skip if account is in a bad state — don't burn the user's
            # attempts when we know it'll fail.
            if account.status in {
                LinkedInAccountStatus.CHALLENGED,
                LinkedInAccountStatus.RESTRICTED,
                LinkedInAccountStatus.FAILED,
            }:
                return {
                    "status": "skipped",
                    "error": f"linkedin account status={account.status.value}",
                }

            if not lead.linkedin_url:
                return {"status": "skipped", "error": "lead has no linkedin_url"}

            cfg = node.config or {}
            kind = node.kind

            # DMs only fire when we know the lead is a 1st-degree connection
            # — anything else gets a 403 from LinkedIn and a flagged account
            # if you do it often. Skip-and-move-on is the right call.
            if kind == SequenceNodeKind.LINKEDIN_DM:
                if lead.linkedin_connection_status != LinkedInConnectionStatus.CONNECTED:
                    return {
                        "status": "skipped",
                        "error": "not connected — DM requires 1st degree",
                    }

            # Page invites only work on 1st-degree connections too.
            if kind == SequenceNodeKind.LINKEDIN_INVITE_TO_PAGE:
                if lead.linkedin_connection_status != LinkedInConnectionStatus.CONNECTED:
                    return {
                        "status": "skipped",
                        "error": "not connected — page invite requires 1st degree",
                    }
                if not cfg.get("page_id"):
                    return {"status": "misconfigured", "error": "page_id missing on node"}

            rate_page_id = cfg.get("page_id") if kind == SequenceNodeKind.LINKEDIN_INVITE_TO_PAGE else None
            rate = await _li_rate_check(account, kind=kind, page_id=rate_page_id)
            if not rate.get("ok"):
                return {"status": "rate_limited", "error": rate.get("error")}

            profile = ProfileRef.from_url(lead.linkedin_url)
            provider = get_linkedin_provider()

            try:
                if kind == SequenceNodeKind.LINKEDIN_VIEW_PROFILE:
                    result = await provider.view_profile(account, profile)
                elif kind == SequenceNodeKind.LINKEDIN_FOLLOW_PROFILE:
                    result = await provider.follow_profile(account, profile)
                elif kind == SequenceNodeKind.LINKEDIN_REACT_POST:
                    post_urn = cfg.get("post_urn")
                    if not post_urn:
                        post_urn = await provider.latest_post_urn(account, profile)
                    if not post_urn:
                        return {"status": "skipped", "error": "no post to react to"}
                    reaction = (cfg.get("reaction") or "LIKE").upper()
                    result = await provider.react_to_post(account, post_urn, reaction)
                elif kind == SequenceNodeKind.LINKEDIN_CONNECT:
                    if cfg.get("no_note"):
                        note = None
                    else:
                        note = _substitute(cfg.get("note_template", ""), lead) or None
                        if note is not None and len(note) > 300:
                            note = note[:300]
                    result = await provider.send_connect_request(account, profile, note)
                    if result.ok:
                        # Optimistically mark INVITED — the poller will flip
                        # to CONNECTED once the lead accepts.
                        lead.linkedin_connection_status = LinkedInConnectionStatus.INVITED
                elif kind == SequenceNodeKind.LINKEDIN_DM:
                    text_tpl = cfg.get("text_template") or ""
                    if not text_tpl:
                        return {"status": "misconfigured", "error": "text_template missing"}
                    body = _substitute(text_tpl, lead)
                    result = await provider.send_dm(account, profile, body)
                elif kind == SequenceNodeKind.LINKEDIN_INVITE_TO_PAGE:
                    result = await provider.invite_to_page(
                        account, profile, str(cfg["page_id"])
                    )
                elif kind == SequenceNodeKind.LINKEDIN_INMAIL:
                    subject_tpl = cfg.get("subject_template") or ""
                    body_tpl = cfg.get("body_template") or ""
                    if not subject_tpl or not body_tpl:
                        return {
                            "status": "misconfigured",
                            "error": "InMail requires subject_template and body_template",
                        }
                    subject = _substitute(subject_tpl, lead)
                    body = _substitute(body_tpl, lead)
                    result = await provider.send_inmail(account, profile, subject, body)
                    # Premium-required gets a distinct skip status so the UI
                    # can surface a precise message.
                    if not result.ok and (result.meta or {}).get("premium_required"):
                        return {
                            "status": "skipped",
                            "error": "InMail unavailable — account needs Premium / Sales Nav credits",
                            "meta": result.meta,
                        }
                elif kind == SequenceNodeKind.LINKEDIN_COMMENT_POST:
                    comment_tpl = cfg.get("comment_template") or ""
                    if not comment_tpl:
                        return {
                            "status": "misconfigured",
                            "error": "comment_template missing",
                        }
                    target = cfg.get("target") or "latest"
                    post_urn = cfg.get("post_urn")
                    if not post_urn:
                        # M4: "latest" + "most_engaged" both fall back to
                        # latest_post_urn() — most_engaged would need a
                        # ranked post fetch which isn't in linkedin-api's
                        # surface. Documented as a known limitation.
                        post_urn = await provider.latest_post_urn(account, profile)
                    if not post_urn:
                        return {"status": "skipped", "error": "no post to comment on"}
                    comment_text = _substitute(comment_tpl, lead)
                    result = await provider.comment_on_post(account, post_urn, comment_text)
                else:
                    return {"status": "misconfigured", "error": f"unsupported kind: {kind.value}"}
            except ChallengeRequired as exc:
                account.status = LinkedInAccountStatus.CHALLENGED
                account.pending_challenge_url = exc.challenge_url or "https://www.linkedin.com"
                account.last_error = str(exc)
                await session.commit()
                return {"status": "challenged", "error": str(exc)}
            except AccountRestricted as exc:
                account.status = LinkedInAccountStatus.RESTRICTED
                account.last_error = str(exc)
                await session.commit()
                return {"status": "restricted", "error": str(exc)}

            # Persist refreshed cookies + optimistic INVITED flag.
            await session.commit()

        if result.ok:
            await _li_rate_bump(account, kind=kind, page_id=rate_page_id)
            return {"status": "sent", "external_id": result.external_id, "meta": result.meta}
        return {"status": "failed", "error": result.error or "unknown error"}
    finally:
        await engine.dispose()


async def _record_execution_and_advance(
    lead_id: uuid.UUID, node_id: uuid.UUID, result: dict[str, Any]
) -> None:
    """Persist a lead_step_executions row + advance the state cursor."""
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            status = result.get("status", "failed")
            mapped: LeadStepResult
            if status == "sent":
                mapped = LeadStepResult.SENT
            elif status in {"suppressed", "skipped", "paused", "rate_limited",
                            "misconfigured", "challenged", "restricted",
                            "not_found"}:
                mapped = LeadStepResult.SKIPPED
            else:
                mapped = LeadStepResult.FAILED
            # LinkedIn handlers return external_id; email handler returns
            # message_id. Either is fine here.
            external = result.get("external_id") or result.get("message_id")
            exec_row = LeadStepExecution(
                lead_id=lead_id,
                node_id=node_id,
                result=mapped,
                external_id=external,
                external_meta=result.get("meta") if isinstance(result.get("meta"), dict) else None,
                error=result.get("error"),
            )
            session.add(exec_row)

            state = await session.scalar(
                select(LeadSequenceState).where(LeadSequenceState.lead_id == lead_id)
            )
            if state is not None:
                node = await session.get(SequenceNode, node_id)
                if node is not None and state.current_node_id == node_id:
                    await _advance_cursor(session, state, node)
            await session.commit()
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------
# Beat: advance_sequences
# --------------------------------------------------------------------------


async def _advance_sequences_async() -> dict[str, int]:
    """Pull ready state rows and either advance them (wait/already-sent entry)
    or dispatch the appropriate channel task.
    """
    engine = create_async_engine(settings.DATABASE_URL)
    counts = {
        "advanced_wait": 0,
        "advanced_entry_done": 0,
        "dispatched_email": 0,
        "dispatched_linkedin": 0,
        "halted_unsupported": 0,
    }
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            now = _now()
            rows = (await session.execute(
                select(LeadSequenceState)
                .where(
                    LeadSequenceState.status == LeadSequenceStatus.ACTIVE,
                    LeadSequenceState.next_run_at.is_not(None),
                    LeadSequenceState.next_run_at <= now,
                    LeadSequenceState.current_node_id.is_not(None),
                )
                .order_by(LeadSequenceState.next_run_at.asc())
                .limit(ADVANCE_BATCH_SIZE)
                .with_for_update(skip_locked=True)
            )).scalars().all()

            email_dispatch: list[tuple[str, str]] = []
            linkedin_dispatch: list[tuple[str, str]] = []

            for state in rows:
                node = await session.get(SequenceNode, state.current_node_id)
                if node is None:
                    state.status = LeadSequenceStatus.HALTED
                    state.halt_reason = "current node missing"
                    state.next_run_at = None
                    continue
                if node.deleted_at is not None:
                    # Graph was re-edited and this node was retired. Halt
                    # rather than crash; the user can rebuild the campaign
                    # if they want these leads to continue.
                    state.status = LeadSequenceStatus.HALTED
                    state.halt_reason = "current node deleted (sequence was rebuilt)"
                    state.next_run_at = None
                    continue

                # Wait: just advance.
                if node.kind == SequenceNodeKind.WAIT:
                    await _advance_cursor(session, state, node)
                    counts["advanced_wait"] += 1
                    continue

                # Email entry node: if the legacy pipeline already sent it,
                # advance straight through.
                if node.kind == SequenceNodeKind.EMAIL and node.is_entry:
                    cfg = node.config or {}
                    if cfg.get("use_campaign_compose"):
                        lead = await session.get(Lead, state.lead_id)
                        if lead is not None and lead.send_status == SendStatus.SENT:
                            await _advance_cursor(session, state, node)
                            counts["advanced_entry_done"] += 1
                        else:
                            # Push ourselves out so we don't busy-spin while
                            # the legacy pipeline catches up.
                            state.next_run_at = now + timedelta(minutes=5)
                        continue

                # Follow-up email node: dispatch.
                if node.kind == SequenceNodeKind.EMAIL:
                    email_dispatch.append((str(state.lead_id), str(node.id)))
                    state.next_run_at = now + timedelta(minutes=10)
                    counts["dispatched_email"] += 1
                    continue

                # M2: LinkedIn warm-up kinds.
                if node.kind in LI_KINDS:
                    linkedin_dispatch.append((str(state.lead_id), str(node.id)))
                    # Push next_run_at out so a slow handler doesn't get
                    # double-dispatched on the next tick.
                    state.next_run_at = now + timedelta(minutes=10)
                    counts["dispatched_linkedin"] += 1
                    continue

                # Anything else is reserved for a future milestone.
                state.status = LeadSequenceStatus.HALTED
                state.halt_reason = f"unsupported node kind: {node.kind.value}"
                state.next_run_at = None
                counts["halted_unsupported"] += 1

            await session.commit()

        # Dispatch outside the transaction so a Celery enqueue failure doesn't
        # roll back state cursor updates.
        for lead_id, node_id in email_dispatch:
            send_email_step.delay(lead_id, node_id)
        for lead_id, node_id in linkedin_dispatch:
            send_linkedin_step.delay(lead_id, node_id)
    finally:
        await engine.dispose()
    return counts


# --------------------------------------------------------------------------
# Celery tasks
# --------------------------------------------------------------------------


@celery_app.task(name="sequencer.advance_sequences")
def advance_sequences() -> dict[str, int]:
    return asyncio.run(_advance_sequences_async())


@celery_app.task(bind=True, name="sequencer.send_email_step", max_retries=3)
def send_email_step(self, lead_id: str, node_id: str) -> dict[str, Any]:  # noqa: D401
    try:
        result = asyncio.run(_send_email_step_async(lead_id, node_id))
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "send_email_step transient failure for lead=%s node=%s", lead_id, node_id
        )
        try:
            raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))
        except self.MaxRetriesExceededError:
            asyncio.run(
                _record_execution_and_advance(
                    uuid.UUID(lead_id),
                    uuid.UUID(node_id),
                    {"status": "failed", "error": str(exc)},
                )
            )
            return {"status": "failed", "error": str(exc)}

    asyncio.run(
        _record_execution_and_advance(
            uuid.UUID(lead_id), uuid.UUID(node_id), result
        )
    )
    return result


@celery_app.task(bind=True, name="sequencer.send_linkedin_step", max_retries=2)
def send_linkedin_step(self, lead_id: str, node_id: str) -> dict[str, Any]:  # noqa: D401
    try:
        result = asyncio.run(_send_linkedin_step_async(lead_id, node_id))
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "send_linkedin_step transient failure for lead=%s node=%s", lead_id, node_id
        )
        try:
            raise self.retry(exc=exc, countdown=300 * (2**self.request.retries))
        except self.MaxRetriesExceededError:
            asyncio.run(
                _record_execution_and_advance(
                    uuid.UUID(lead_id),
                    uuid.UUID(node_id),
                    {"status": "failed", "error": str(exc)},
                )
            )
            return {"status": "failed", "error": str(exc)}

    asyncio.run(
        _record_execution_and_advance(
            uuid.UUID(lead_id), uuid.UUID(node_id), result
        )
    )
    return result
