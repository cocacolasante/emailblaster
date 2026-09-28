"""Research a lead from a LinkedIn URL → draft → confirm → approve → send.

The Muse flow for one-off prospect outreach (the same research + compose
the GUI's "Research a client" page uses), by email or LinkedIn.

Nothing sends without a human in the loop, enforced in three steps:

1. ``POST /outreach-drafts`` researches + drafts.  Returns the draft, the
   suggested recipient(s) and the sender options.  ``/redraft`` and
   ``PATCH`` revise it.
2. ``POST /{id}/confirm`` pins the EXACT recipient, sender and message
   version and returns a summary plus a one-time confirmation code.
3. ``POST /{id}/send`` needs that code, ``user_approved: true``, and an
   unchanged draft.  Any redraft/edit after confirming voids the code.

A draft sends at most once (row lock + status check).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import Identity, get_identity
from app.database import get_db
from app.models import (
    ConnectedAccount,
    CrmActivity,
    CrmActivityDirection,
    CrmActivityType,
    Lead,
    LinkedInAccount,
    LinkedInAccountStatus,
    OutreachChannel,
    OutreachDraft,
    OutreachDraftStatus,
    Suppression,
)
from app.routers.research_client import profile_from_research, research_and_compose
from app.services import credentials, outreach, research_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/outreach-drafts", tags=["outreach-drafts"])

_EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# LinkedIn caps connection-request notes at 200 chars on free accounts.
CONNECT_NOTE_LIMIT = 200
DEFAULT_CHAR_LIMIT = {
    OutreachChannel.EMAIL: 600,
    OutreachChannel.LINKEDIN_DM: 300,
    OutreachChannel.LINKEDIN_CONNECT: CONNECT_NOTE_LIMIT,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


# --- schemas -----------------------------------------------------------------


class DraftCreate(BaseModel):
    linkedin_url: str = Field(min_length=10, max_length=500)
    goal: str = Field(min_length=3, max_length=2000)
    channel: OutreachChannel = OutreachChannel.EMAIL
    tone: str = Field(default="professional", max_length=120)
    research_mode: Literal["fast", "deep"] = "fast"
    char_limit: int | None = Field(default=None, ge=50, le=5000)
    sender_name: str | None = Field(default=None, max_length=120)


class Redraft(BaseModel):
    feedback: str | None = Field(default=None, max_length=2000,
                                 description="What to change, e.g. 'shorter, mention their podcast'")
    goal: str | None = Field(default=None, max_length=2000)
    tone: str | None = Field(default=None, max_length=120)
    channel: OutreachChannel | None = None
    char_limit: int | None = Field(default=None, ge=50, le=5000)


class DraftEdit(BaseModel):
    subject: str | None = Field(default=None, max_length=998)
    body: str | None = Field(default=None, max_length=50_000)
    to_email: str | None = Field(default=None, max_length=320)
    to_name: str | None = Field(default=None, max_length=200)
    sender_email: str | None = Field(default=None, max_length=320)
    sender_name: str | None = Field(default=None, max_length=120)
    linkedin_account_id: uuid.UUID | None = None


class Confirm(BaseModel):
    to_email: str | None = Field(default=None, max_length=320)
    to_name: str | None = Field(default=None, max_length=200)
    sender_email: str | None = Field(default=None, max_length=320)
    linkedin_account_id: uuid.UUID | None = None


class Send(BaseModel):
    confirmation_code: str = Field(min_length=4, max_length=32)
    user_approved: bool


class DraftOut(BaseModel):
    id: uuid.UUID
    status: str
    channel: str
    version: int
    linkedin_url: str
    goal: str
    tone: str
    profile: dict[str, Any]
    research_highlights: dict[str, Any]
    subject: str
    body: str
    char_count: int
    to_email: str | None
    to_name: str | None
    sender_email: str | None
    sender_name: str
    linkedin_account_id: uuid.UUID | None
    recipient_suggestions: list[dict[str, Any]] = []
    sender_options: list[dict[str, Any]] = []
    confirmation: dict[str, Any] | None = None
    sent_at: datetime | None = None
    sent_external_id: str | None = None
    send_error: str | None = None
    crm_lead_id: uuid.UUID | None = None
    next_step: str


# --- helpers -----------------------------------------------------------------


def _output_kind(channel: OutreachChannel) -> str:
    return "email" if channel == OutreachChannel.EMAIL else "linkedin_dm"


def _highlights(research: dict[str, Any]) -> dict[str, Any]:
    keys = ("headline", "summary", "person_news", "company_news", "recent_updates",
            "company_description", "industry", "location", "quality", "from_cache")
    return {k: research[k] for k in keys if research.get(k)}


def _slug(url: str) -> str | None:
    slug, _ = research_client.parse_linkedin_url(url)
    return slug


async def _get(db: AsyncSession, draft_id: uuid.UUID, *, lock: bool = False) -> OutreachDraft:
    q = select(OutreachDraft).where(OutreachDraft.id == draft_id)
    if lock:
        q = q.with_for_update()
    draft = (await db.execute(q)).scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=404, detail="draft not found")
    return draft


def _editable(draft: OutreachDraft) -> None:
    if draft.status in (OutreachDraftStatus.SENT, OutreachDraftStatus.DISCARDED):
        raise HTTPException(status_code=409, detail=f"this draft was already {draft.status.value}")


def _invalidate(draft: OutreachDraft) -> None:
    """Any change voids a pending confirmation — the approval was for
    different words or a different recipient."""
    draft.version += 1
    draft.status = OutreachDraftStatus.DRAFT
    draft.confirm_hash = None
    draft.confirmed_version = None
    draft.confirmed_at = None


async def _recipient_suggestions(db: AsyncSession, draft: OutreachDraft) -> list[dict[str, Any]]:
    if draft.channel != OutreachChannel.EMAIL:
        return []
    out: list[dict[str, Any]] = []
    slug = _slug(draft.linkedin_url)
    if slug:
        rows = (await db.execute(
            select(Lead.email, Lead.first_name, Lead.last_name)
            .where(Lead.linkedin_url.ilike(f"%/in/{slug}%"), Lead.email.is_not(None))
            .order_by(Lead.updated_at.desc()).limit(3)
        )).all()
        for email, first, last in rows:
            out.append({"email": email, "name": " ".join(x for x in (first, last) if x) or None,
                        "source": "existing CRM lead"})
    profile = draft.research
    name = " ".join(x for x in (profile.get("first_name"), profile.get("last_name")) if x)
    website = (profile.get("company_website") or "").strip()
    domain = re.sub(r"^https?://", "", website).split("/")[0].removeprefix("www.")
    if name and domain and credentials.is_configured("hunter"):
        from app.services import hunter

        found = await hunter.find_email_hunter(domain, full_name=name)
        if found and found.get("email") and all(s["email"] != found["email"] for s in out):
            out.append({"email": found["email"], "name": name, "source": "Hunter",
                        "confidence": found.get("score")})
    return out


async def _sender_options(db: AsyncSession, draft: OutreachDraft) -> list[dict[str, Any]]:
    if draft.channel == OutreachChannel.EMAIL:
        opts: list[dict[str, Any]] = []
        name, email = credentials.default_sender()
        rows = (await db.execute(
            select(ConnectedAccount.email_address, ConnectedAccount.label, ConnectedAccount.is_default_sender)
        )).all()
        for addr, label, is_default in rows:
            opts.append({"sender_email": addr, "label": label, "default": bool(is_default)})
        if email and all(o["sender_email"] != email for o in opts):
            opts.append({"sender_email": email, "label": f"{name or 'Workspace'} (Brevo default)",
                         "default": not any(o["default"] for o in opts)})
        return opts
    rows = (await db.execute(
        select(LinkedInAccount.id, LinkedInAccount.label)
        .where(LinkedInAccount.status == LinkedInAccountStatus.OK,
               LinkedInAccount.unipile_account_id.is_not(None))
    )).all()
    return [{"linkedin_account_id": str(i), "label": label, "default": n == 0} for n, (i, label) in enumerate(rows)]


def _next_step(draft: OutreachDraft) -> str:
    if draft.status == OutreachDraftStatus.DRAFT:
        who = "recipient email and sender" if draft.channel == OutreachChannel.EMAIL else "LinkedIn account to send from"
        return (f"Show the user this draft and ask them to confirm the {who}, redraft, or edit. "
                "When they're happy, call confirm_outreach. Do NOT send without their approval.")
    if draft.status == OutreachDraftStatus.READY:
        return ("Show the user the confirmation summary (recipient, sender, final message) and ask "
                "'Send it?'. Only if they explicitly approve, call send_outreach with the "
                "confirmation code and user_approved=true. If they want changes, redraft or edit.")
    if draft.status == OutreachDraftStatus.FAILED:
        return "The send failed (see send_error). Fix it, then confirm again."
    return "Done."


async def _out(db: AsyncSession, draft: OutreachDraft, *, code: str | None = None,
               suggestions: bool = True) -> DraftOut:
    confirmation = None
    if draft.status == OutreachDraftStatus.READY:
        confirmation = {
            "to": draft.to_email if draft.channel == OutreachChannel.EMAIL else draft.linkedin_url,
            "to_name": draft.to_name,
            "from": draft.sender_email if draft.channel == OutreachChannel.EMAIL
            else str(draft.linkedin_account_id),
            "channel": draft.channel.value,
            "subject": draft.subject if draft.channel == OutreachChannel.EMAIL else None,
            "body": draft.body,
            "version": draft.version,
        }
        if code is not None:
            confirmation["confirmation_code"] = code
    return DraftOut(
        id=draft.id, status=draft.status.value, channel=draft.channel.value, version=draft.version,
        linkedin_url=draft.linkedin_url, goal=draft.goal, tone=draft.tone,
        profile=profile_from_research(draft.research).model_dump(),
        research_highlights=_highlights(draft.research),
        subject=draft.subject, body=draft.body, char_count=len(draft.body),
        to_email=draft.to_email, to_name=draft.to_name, sender_email=draft.sender_email,
        sender_name=draft.sender_name, linkedin_account_id=draft.linkedin_account_id,
        recipient_suggestions=await _recipient_suggestions(db, draft) if suggestions else [],
        sender_options=await _sender_options(db, draft) if suggestions else [],
        confirmation=confirmation, sent_at=draft.sent_at, sent_external_id=draft.sent_external_id,
        send_error=draft.send_error, crm_lead_id=draft.crm_lead_id, next_step=_next_step(draft),
    )


def _requirements(channel: OutreachChannel) -> None:
    credentials.require("anthropic")
    if channel == OutreachChannel.EMAIL:
        credentials.require("brevo")
    else:
        credentials.require("unipile")


# --- endpoints ---------------------------------------------------------------


@router.post("", response_model=DraftOut, status_code=201)
async def create_draft(
    body: DraftCreate,
    identity: Identity = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
) -> DraftOut:
    """Research the LinkedIn profile and draft the outreach.  Sends nothing."""
    _requirements(body.channel)
    sender_name = (body.sender_name or "").strip() or identity.user.display_name
    char_limit = body.char_limit or DEFAULT_CHAR_LIMIT[body.channel]
    if body.channel == OutreachChannel.LINKEDIN_CONNECT:
        char_limit = min(char_limit, CONNECT_NOTE_LIMIT)
    research, composed = await research_and_compose(
        db, linkedin_url=body.linkedin_url, goal=body.goal, tone=body.tone,
        sender_name=sender_name, research_mode=body.research_mode,
        output_kind=_output_kind(body.channel), char_limit=char_limit,
    )
    draft = OutreachDraft(
        created_by=identity.user_id, linkedin_url=body.linkedin_url.strip(), channel=body.channel,
        goal=body.goal, tone=body.tone, research_mode=body.research_mode, char_limit=char_limit,
        sender_name=sender_name, research=research, subject=composed.get("subject") or "",
        body=composed["body"], version=1,
        to_name=" ".join(x for x in (research.get("first_name"), research.get("last_name")) if x) or None,
    )
    db.add(draft)
    await db.flush()
    # Preselect the defaults so a plain "yes, send it" has something concrete to confirm.
    opts = await _sender_options(db, draft)
    default = next((o for o in opts if o.get("default")), opts[0] if opts else None)
    if default and draft.channel == OutreachChannel.EMAIL:
        draft.sender_email = default["sender_email"]
    elif default:
        draft.linkedin_account_id = uuid.UUID(default["linkedin_account_id"])
    suggestions = await _recipient_suggestions(db, draft)
    if suggestions and draft.channel == OutreachChannel.EMAIL:
        draft.to_email = suggestions[0]["email"]
    await db.commit()
    await db.refresh(draft)
    return await _out(db, draft)


@router.get("", response_model=list[DraftOut])
async def list_drafts(db: AsyncSession = Depends(get_db)) -> list[DraftOut]:
    rows = (await db.execute(
        select(OutreachDraft).order_by(OutreachDraft.updated_at.desc()).limit(20)
    )).scalars().all()
    return [await _out(db, d, suggestions=False) for d in rows]


@router.get("/{draft_id}", response_model=DraftOut)
async def get_draft(draft_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> DraftOut:
    return await _out(db, await _get(db, draft_id))


@router.post("/{draft_id}/redraft", response_model=DraftOut)
async def redraft(draft_id: uuid.UUID, body: Redraft, db: AsyncSession = Depends(get_db)) -> DraftOut:
    """Rewrite the message from the SAME research (no new research cost)."""
    draft = await _get(db, draft_id, lock=True)
    _editable(draft)
    if body.channel is not None and body.channel != draft.channel:
        _requirements(body.channel)
        draft.channel = body.channel
        draft.char_limit = body.char_limit or DEFAULT_CHAR_LIMIT[body.channel]
        draft.to_email = draft.to_email if body.channel == OutreachChannel.EMAIL else None
    if body.goal:
        draft.goal = body.goal
    if body.tone:
        draft.tone = body.tone
    if body.char_limit:
        draft.char_limit = body.char_limit
    if draft.channel == OutreachChannel.LINKEDIN_CONNECT:
        draft.char_limit = min(draft.char_limit, CONNECT_NOTE_LIMIT)
    goal = draft.goal
    if body.feedback:
        goal = (f"{draft.goal}\n\nRevise the previous draft per this feedback: {body.feedback}\n"
                f"Previous draft:\n{draft.body}")
    _research, composed = await research_and_compose(
        db, linkedin_url=draft.linkedin_url, goal=goal, tone=draft.tone,
        sender_name=draft.sender_name, research_mode=draft.research_mode,
        output_kind=_output_kind(draft.channel), char_limit=draft.char_limit,
        research=draft.research,
    )
    draft.subject = composed.get("subject") or draft.subject
    draft.body = composed["body"]
    _invalidate(draft)
    await db.commit()
    await db.refresh(draft)
    return await _out(db, draft)


@router.patch("/{draft_id}", response_model=DraftOut)
async def edit_draft(draft_id: uuid.UUID, body: DraftEdit, db: AsyncSession = Depends(get_db)) -> DraftOut:
    draft = await _get(db, draft_id, lock=True)
    _editable(draft)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="nothing to change")
    if "to_email" in changes and changes["to_email"]:
        changes["to_email"] = changes["to_email"].strip().lower()
    for k, v in changes.items():
        setattr(draft, k, v)
    _invalidate(draft)
    await db.commit()
    await db.refresh(draft)
    return await _out(db, draft)


@router.post("/{draft_id}/confirm", response_model=DraftOut)
async def confirm_draft(draft_id: uuid.UUID, body: Confirm, db: AsyncSession = Depends(get_db)) -> DraftOut:
    """Pin recipient + sender + this exact message and issue a one-time
    confirmation code.  Still sends nothing."""
    draft = await _get(db, draft_id, lock=True)
    _editable(draft)
    _requirements(draft.channel)
    if draft.channel == OutreachChannel.EMAIL:
        to_email = (body.to_email or draft.to_email or "").strip().lower()
        if not _EMAIL_RX.match(to_email):
            raise HTTPException(status_code=422, detail="a valid recipient email is required")
        if (await db.execute(select(Suppression.id).where(Suppression.email == to_email))).first():
            raise HTTPException(status_code=409,
                                detail=f"{to_email} is on this workspace's ignore list — not sending")
        sender = (body.sender_email or draft.sender_email or "").strip().lower()
        allowed = {o["sender_email"].lower() for o in await _sender_options(db, draft)}
        if sender not in allowed:
            raise HTTPException(status_code=422,
                                detail=f"sender must be one of: {', '.join(sorted(allowed)) or 'none configured'}")
        if not draft.subject.strip():
            raise HTTPException(status_code=422, detail="the email needs a subject")
        draft.to_email, draft.sender_email = to_email, sender
        if body.to_name is not None:
            draft.to_name = body.to_name
    else:
        account_id = body.linkedin_account_id or draft.linkedin_account_id
        account = await db.get(LinkedInAccount, account_id) if account_id else None
        if account is None or account.status != LinkedInAccountStatus.OK or not account.unipile_account_id:
            raise HTTPException(status_code=422, detail="choose a connected LinkedIn account to send from")
        if draft.channel == OutreachChannel.LINKEDIN_CONNECT and len(draft.body) > CONNECT_NOTE_LIMIT:
            raise HTTPException(status_code=422,
                                detail=f"connection notes are limited to {CONNECT_NOTE_LIMIT} characters "
                                       f"(this one is {len(draft.body)}) — redraft it shorter")
        draft.linkedin_account_id = account.id
    code = secrets.token_hex(3).upper()
    draft.status = OutreachDraftStatus.READY
    draft.confirm_hash = _hash(code)
    draft.confirmed_version = draft.version
    draft.confirmed_at = _now()
    draft.send_error = None
    await db.commit()
    await db.refresh(draft)
    return await _out(db, draft, code=code, suggestions=False)


@router.post("/{draft_id}/send", response_model=DraftOut)
async def send_draft(
    draft_id: uuid.UUID,
    body: Send,
    identity: Identity = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
) -> DraftOut:
    """Send a CONFIRMED draft.  Requires the confirmation code and an
    explicit user approval; sends at most once."""
    if body.user_approved is not True:
        raise HTTPException(status_code=409, detail="not sent — the user has not approved this send")
    draft = await _get(db, draft_id, lock=True)
    if draft.status == OutreachDraftStatus.SENT:
        raise HTTPException(status_code=409, detail="this draft was already sent")
    if draft.status != OutreachDraftStatus.READY or draft.confirm_hash is None:
        raise HTTPException(status_code=409, detail="confirm the draft first (confirm_outreach)")
    if draft.confirmed_version != draft.version:
        raise HTTPException(status_code=409, detail="the draft changed after it was confirmed — confirm again")
    if not hmac.compare_digest(draft.confirm_hash, _hash(body.confirmation_code)):
        raise HTTPException(status_code=403, detail="confirmation code doesn't match this draft")

    try:
        if draft.channel == OutreachChannel.EMAIL:
            result = await outreach.send_and_track(
                db, to_email=draft.to_email, to_name=draft.to_name, subject=draft.subject,
                body=draft.body, sender_name=draft.sender_name, sender_email=draft.sender_email,
                campaign_tag="muse-outreach",
            )
            draft = await _get(db, draft_id)  # send_and_track commits; reload
            draft.sent_external_id = result.message_id
            draft.crm_lead_id = uuid.UUID(result.crm_lead_id) if result.crm_lead_id else None
            await _enrich_lead(db, draft)
        else:
            draft.sent_external_id = await _send_linkedin(db, draft)
            draft.crm_lead_id = await _log_linkedin(db, draft)
    except outreach.OutreachSendError as exc:
        return await _fail(db, draft_id, exc.detail)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — surface, don't 500
        logger.exception("muse outreach send failed")
        return await _fail(db, draft_id, str(exc))

    draft.status = OutreachDraftStatus.SENT
    draft.sent_at = _now()
    draft.confirm_hash = None
    await db.commit()
    await db.refresh(draft)
    return await _out(db, draft, suggestions=False)


@router.post("/{draft_id}/discard", response_model=DraftOut)
async def discard_draft(draft_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> DraftOut:
    draft = await _get(db, draft_id, lock=True)
    _editable(draft)
    draft.status = OutreachDraftStatus.DISCARDED
    draft.confirm_hash = None
    await db.commit()
    await db.refresh(draft)
    return await _out(db, draft, suggestions=False)


# --- send internals ----------------------------------------------------------


async def _fail(db: AsyncSession, draft_id: uuid.UUID, error: str) -> DraftOut:
    await db.rollback()
    draft = await _get(db, draft_id)
    draft.status = OutreachDraftStatus.FAILED
    draft.send_error = error[:1000]
    draft.confirm_hash = None
    await db.commit()
    await db.refresh(draft)
    return await _out(db, draft, suggestions=False)


async def claim_linkedin_slot(account: LinkedInAccount, channel: OutreachChannel) -> dict[str, Any]:
    """Share the sequencer's per-account LinkedIn caps so one-off sends and
    campaigns draw from the same daily budget."""
    from app.models import SequenceNodeKind
    from app.workers import sequencer

    kind = (SequenceNodeKind.LINKEDIN_CONNECT if channel == OutreachChannel.LINKEDIN_CONNECT
            else SequenceNodeKind.LINKEDIN_DM)
    sequencer._LI_REDIS_CLIENT = None  # fresh client for this event loop
    return await sequencer._li_rate_acquire(account, kind)


async def _send_linkedin(db: AsyncSession, draft: OutreachDraft) -> str | None:
    from app.services.linkedin import ProfileRef, get_provider

    account = await db.get(LinkedInAccount, draft.linkedin_account_id)
    if account is None:
        raise HTTPException(status_code=409, detail="the LinkedIn account was removed — confirm again")
    slot = await claim_linkedin_slot(account, draft.channel)
    if not slot.get("ok"):
        raise HTTPException(status_code=429,
                            detail=f"LinkedIn limit reached for this account ({slot.get('reason')}) — try later")
    provider = get_provider()
    profile = ProfileRef.from_url(draft.linkedin_url)
    if draft.channel == OutreachChannel.LINKEDIN_CONNECT:
        res = await provider.send_connect_request(account, profile, note=draft.body)
    else:
        res = await provider.send_dm(account, profile, draft.body)
    if not res.ok:
        hint = ""
        if draft.channel == OutreachChannel.LINKEDIN_DM:
            hint = (" If you're not connected with them yet, redraft with channel "
                    "'linkedin_connect' to send a connection request with a note instead.")
        raise RuntimeError(f"LinkedIn didn't accept it: {res.error}.{hint}")
    return res.external_id


def _name_parts(draft: OutreachDraft) -> tuple[str | None, str | None]:
    r = draft.research
    return (r.get("first_name") or None), (r.get("last_name") or None)


async def _enrich_lead(db: AsyncSession, draft: OutreachDraft) -> None:
    """Fill the CRM lead the email send created/matched with what research
    learned (only blanks)."""
    if draft.crm_lead_id is None:
        return
    lead = await db.get(Lead, draft.crm_lead_id)
    if lead is None:
        return
    r = draft.research
    first, last = _name_parts(draft)
    lead.first_name = lead.first_name or first
    lead.last_name = lead.last_name or last
    lead.company = lead.company or (r.get("company") or None)
    lead.job_title = lead.job_title or (r.get("job_title") or None)
    lead.linkedin_url = lead.linkedin_url or draft.linkedin_url


async def _log_linkedin(db: AsyncSession, draft: OutreachDraft) -> uuid.UUID:
    """Find-or-create the CRM lead by LinkedIn profile and log the touch."""
    slug = _slug(draft.linkedin_url)
    lead = None
    if slug:
        lead = (await db.execute(
            select(Lead).where(Lead.linkedin_url.ilike(f"%/in/{slug}%"))
            .order_by(Lead.updated_at.desc()).limit(1)
        )).scalar_one_or_none()
    if lead is None:
        first, last = _name_parts(draft)
        r = draft.research
        lead = Lead(campaign_id=None, email=None, first_name=first, last_name=last,
                    company=r.get("company") or None, job_title=r.get("job_title") or None,
                    linkedin_url=draft.linkedin_url)
        db.add(lead)
        await db.flush()
    label = ("LinkedIn connection request sent" if draft.channel == OutreachChannel.LINKEDIN_CONNECT
             else "LinkedIn message sent")
    db.add(CrmActivity(
        lead_id=lead.id, activity_type=CrmActivityType.NOTE, direction=CrmActivityDirection.OUTBOUND,
        subject=label, body=draft.body[:1000],
    ))
    await db.flush()
    return lead.id
