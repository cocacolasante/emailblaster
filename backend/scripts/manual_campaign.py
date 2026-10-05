"""Load a campaign whose FIRST emails are written outside the app (e.g. by
Claude Code on a subscription) — no research or compose API calls.

Usage (inside the backend container):

    python scripts/manual_campaign.py create <template-campaign-id> "<name>"
        Clone the template's settings + sequence (nodes/edges, published)
        into a new DRAFT campaign.  research_mode = none.

    python scripts/manual_campaign.py load <campaign-id> <csv> <briefs-out.jsonl>
        Insert leads (dedupe; skip suppressed, already-emailed-anywhere and
        non-VERIFIED Prospeo emails), research_status=SKIPPED,
        compose_status=PENDING, enroll on the sequence, mark samples.  Writes
        one compact brief per lead for the writer.  Nothing is queued.

    python scripts/manual_campaign.py apply <campaign-id> <emails.jsonl>
        Each line {"email", "subject", "body"} → strip long dashes, apply the
        campaign signature, compose_status=DONE.  Only PENDING leads are
        touched (an already-composed/sent lead is never overwritten).

    python scripts/manual_campaign.py preview <campaign-id>
        DRAFT → PREVIEWING once every sample is composed (approve in the UI).

    python scripts/manual_campaign.py status <campaign-id>

Nothing here calls Anthropic, Hunter, Apollo or the web.  Note for the
campaign afterwards: editing its Goal or clicking "Retry failed" WOULD
re-compose through the API.
"""
import asyncio
import csv
import json
import sys
import uuid

sys.path.insert(0, ".")

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.models import (  # noqa: E402
    Campaign, CampaignStatus, ComposeStatus, Lead, ResearchMode, ResearchStatus,
    SendStatus, Sequence, SequenceEdge, SequenceNode, Suppression,
)
from app.services.ownership import inherit_owner  # noqa: E402
from app.services.sequence_service import enroll_leads  # noqa: E402
from app.services.signature import apply_signature, resolve_campaign_signature  # noqa: E402
from app.tenancy.worker import tenant_context  # noqa: E402
from app.tenancy.worker_db import worker_engine  # noqa: E402
from app.workers.compose import _strip_long_dashes  # noqa: E402

TENANT = uuid.UUID("00000000-0000-0000-0000-000000000001")

# Copied from the template campaign; everything else is reset.
_SKIP_COLS = {"id", "name", "status", "research_mode", "created_at", "updated_at",
              "auto_paused_at", "auto_pause_reason", "auto_paused_until", "goal_updated_at",
              "tenant_id"}


def _clip(v: str | None, n: int) -> str:
    v = " ".join((v or "").split())
    return v if len(v) <= n else v[: n - 1].rstrip() + "…"


def _research_from_row(r: dict) -> dict:
    """Compact research_data from the Prospeo row — what the app's AI
    follow-ups read for personalization (no research API ran)."""
    return {
        "quality": "medium",
        "source": "prospeo_csv",
        "person": {
            "headline": _clip(r.get("Person headline"), 240),
            "title": r.get("Job title") or "",
            "job_start_year": r.get("Job start year") or "",
            "location": ", ".join(x for x in (r.get("Person city"), r.get("Person state")) if x),
        },
        "company": {
            "name": r.get("Company name") or "",
            "one_liner": _clip(r.get("AI One-Liner"), 240),
            "description": _clip(r.get("Company description") or r.get("AI Description"), 600),
            "keywords": _clip(r.get("Company keywords"), 200),
            "size": r.get("Company employee range") or "",
            "website": r.get("Company website") or "",
        },
    }


def _brief(r: dict, email: str) -> dict:
    return {
        "email": email,
        "first": r.get("First name") or "",
        "title": _clip(r.get("Job title"), 80),
        "company": r.get("Company name") or "",
        "headline": _clip(r.get("Person headline"), 160),
        "since": r.get("Job start year") or "",
        "loc": ", ".join(x for x in (r.get("Person city"), r.get("Person state")) if x),
        "size": r.get("Company employee range") or "",
        "about": _clip(r.get("AI One-Liner") or r.get("Company description"), 260),
        "kw": _clip(r.get("Company keywords"), 120),
    }


async def create(template_id: str, name: str) -> None:
    eng = worker_engine()
    async with AsyncSession(eng, expire_on_commit=False) as s:
        tpl = await s.get(Campaign, uuid.UUID(template_id))
        data = {c.key: getattr(tpl, c.key) for c in Campaign.__table__.columns if c.key not in _SKIP_COLS}
        camp = Campaign(**data, name=name, status=CampaignStatus.DRAFT, research_mode=ResearchMode.NONE)
        s.add(camp)
        await s.flush()

        tseq = await s.scalar(select(Sequence).where(Sequence.campaign_id == tpl.id))
        seq = Sequence(campaign_id=camp.id, is_published=True)
        s.add(seq)
        await s.flush()
        nodes = (await s.execute(select(SequenceNode).where(
            SequenceNode.sequence_id == tseq.id, SequenceNode.deleted_at.is_(None)))).scalars().all()
        id_map = {}
        for n in nodes:
            new = SequenceNode(sequence_id=seq.id, kind=n.kind, config=dict(n.config or {}),
                               is_entry=n.is_entry)
            s.add(new)
            await s.flush()
            id_map[n.id] = new.id
        edges = (await s.execute(select(SequenceEdge).where(SequenceEdge.sequence_id == tseq.id))).scalars().all()
        for e in edges:
            if e.from_node_id not in id_map or (e.to_node_id and e.to_node_id not in id_map):
                continue
            s.add(SequenceEdge(sequence_id=seq.id, from_node_id=id_map[e.from_node_id],
                               to_node_id=id_map.get(e.to_node_id), condition=e.condition,
                               priority=e.priority))
        await s.commit()
        print(f"created campaign {camp.id} ({name}) with {len(id_map)} nodes, status=draft")
    await eng.dispose()


async def load(campaign_id: str, csv_path: str, briefs_out: str) -> None:
    rows = list(csv.DictReader(open(csv_path, newline="", encoding="utf-8-sig")))
    eng = worker_engine()
    async with AsyncSession(eng, expire_on_commit=False) as s:
        camp = await s.get(Campaign, uuid.UUID(campaign_id))
        suppressed = {e.lower() for e in (await s.execute(select(Suppression.email))).scalars()}
        emailed = {e for e in (await s.execute(
            select(func.lower(Lead.email)).where(Lead.send_status == SendStatus.SENT))).scalars()}
        existing = {e for e in (await s.execute(
            select(func.lower(Lead.email)).where(Lead.campaign_id == camp.id))).scalars()}

        skipped = {"suppressed": 0, "already_emailed": 0, "unverified": 0, "duplicate": 0, "no_email": 0}
        seen, leads, briefs = set(existing), [], []
        for r in rows:
            email = (r.get("Email") or "").strip().lower()
            if not email:
                skipped["no_email"] += 1
            elif email in seen:
                skipped["duplicate"] += 1
            elif email in suppressed:
                skipped["suppressed"] += 1
            elif email in emailed:
                skipped["already_emailed"] += 1
            elif (r.get("Email status") or "").upper() != "VERIFIED":
                skipped["unverified"] += 1
            else:
                seen.add(email)
                leads.append(Lead(
                    campaign_id=camp.id, email=email, raw_csv_row=r,
                    first_name=(r.get("First name") or "").strip() or None,
                    last_name=(r.get("Last name") or "").strip() or None,
                    company=(r.get("Company name") or "").strip() or None,
                    company_website=(r.get("Company website") or "").strip() or None,
                    job_title=(r.get("Job title") or "").strip() or None,
                    linkedin_url=(r.get("Person LinkedIn URL") or "").strip() or None,
                    research_data=_research_from_row(r),
                    research_status=ResearchStatus.SKIPPED,
                    compose_status=ComposeStatus.PENDING,
                    **inherit_owner(camp),
                ))
                briefs.append(_brief(r, email))

        # Samples: the first `sample_count` leads, so they're composed first.
        for lead in leads[: camp.sample_count]:
            lead.is_sample = True
        s.add_all(leads)
        await s.flush()
        await enroll_leads(s, camp.id, [lead.id for lead in leads])
        await s.commit()
    await eng.dispose()

    with open(briefs_out, "w") as f:
        for b in briefs:
            f.write(json.dumps(b, ensure_ascii=False) + "\n")
    print(f"loaded {len(leads)} leads; skipped {skipped}; briefs → {briefs_out}")


async def apply(campaign_id: str, emails_path: str) -> None:
    items = [json.loads(line) for line in open(emails_path) if line.strip()]
    eng = worker_engine()
    done = missing = not_pending = 0
    async with AsyncSession(eng, expire_on_commit=False) as s:
        camp = await s.get(Campaign, uuid.UUID(campaign_id))
        signature = await resolve_campaign_signature(s, camp)
        for it in items:
            lead = await s.scalar(select(Lead).where(
                Lead.campaign_id == camp.id, func.lower(Lead.email) == it["email"].strip().lower()))
            if lead is None:
                missing += 1
                continue
            if lead.compose_status != ComposeStatus.PENDING or lead.send_status != SendStatus.PENDING:
                not_pending += 1
                continue
            lead.composed_subject = _strip_long_dashes(it["subject"].strip())
            lead.composed_body = apply_signature(_strip_long_dashes(it["body"].strip()), signature)
            lead.compose_status = ComposeStatus.DONE
            done += 1
        await s.commit()
    await eng.dispose()
    print(f"applied {done}; not found {missing}; skipped (already composed/sent) {not_pending}")


async def preview(campaign_id: str) -> None:
    eng = worker_engine()
    async with AsyncSession(eng, expire_on_commit=False) as s:
        camp = await s.get(Campaign, uuid.UUID(campaign_id))
        pending = await s.scalar(select(func.count()).select_from(Lead).where(
            Lead.campaign_id == camp.id, Lead.is_sample.is_(True),
            Lead.compose_status != ComposeStatus.DONE))
        if camp.status != CampaignStatus.DRAFT:
            print(f"campaign is {camp.status.value}, not draft — unchanged")
        elif pending:
            print(f"{pending} samples not composed yet — unchanged")
        else:
            camp.status = CampaignStatus.PREVIEWING
            await s.commit()
            print("campaign → previewing (review + approve in the UI)")
    await eng.dispose()


async def status(campaign_id: str) -> None:
    eng = worker_engine()
    async with AsyncSession(eng) as s:
        cid = uuid.UUID(campaign_id)
        camp = await s.get(Campaign, cid)
        rows = (await s.execute(select(Lead.compose_status, Lead.send_status, func.count())
                                .where(Lead.campaign_id == cid)
                                .group_by(Lead.compose_status, Lead.send_status))).all()
        print(camp.name, camp.status.value)
        for c, snd, n in rows:
            print(f"  compose={c.value:8} send={snd.value:10} {n}")
    await eng.dispose()


async def main() -> None:
    cmd, *args = sys.argv[1:]
    fn = {"create": create, "load": load, "apply": apply, "preview": preview, "status": status}[cmd]
    async with tenant_context(TENANT, user_id=uuid.UUID("11d331b0-06a4-4bc4-a774-4fd3c1551eea")):
        await fn(*args)


if __name__ == "__main__":
    asyncio.run(main())
