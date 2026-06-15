"""Phase 43 — nonprofit funding discovery (USAspending + IRS EO BMF).

Feeds the existing prospect_signals review queue.  Covers feed parsing
(usaspending mapping/pagination/dedup; irs_bmf SUBSECTION+RULING
window), the staging autonomy boundary (campaign-less Lead + signal +
task + one notification, never a campaign/sequence), dedup, and cursor
advance.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.models import (
    CrmActivity,
    CrmActivityType,
    FundingSourceState,
    Lead,
    LeadSequenceState,
    Notification,
    ProspectSignal,
)
from app.services.funding_sources import irs_bmf, usaspending
from app.services.funding_sources.base import DiscoveredOrg
from app.workers import funding_signals
from app.workers.funding_signals import (
    _poll_irs_bmf_async,
    _poll_usaspending_async,
    _stage_discovery_signal,
    _yyyymm,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# httpx fakes
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, *, json_data=None, text=None):
        self._json = json_data
        self.text = text

    def raise_for_status(self):
        return None

    def json(self):
        return self._json


class _FakeClient:
    """Async-context-manager stand-in for httpx.AsyncClient."""
    def __init__(self, handler):
        self._handler = handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        return self._handler(url, json)

    async def get(self, url, params=None):
        return self._handler(url, params)


def _patch_client(monkeypatch, module, handler):
    monkeypatch.setattr(module, "httpx", _HttpxShim(handler))


class _HttpxShim:
    def __init__(self, handler):
        self._handler = handler

    def AsyncClient(self, *a, **k):  # noqa: N802 — mimic httpx API
        return _FakeClient(self._handler)


# ---------------------------------------------------------------------------
# usaspending feed
# ---------------------------------------------------------------------------


async def test_usaspending_mapping_pagination_dedup(monkeypatch):
    def handler(url, body):
        page = body.get("page", 1)
        if page == 1:
            return _FakeResp(json_data={
                "results": [
                    {"Award ID": "A1", "Recipient Name": "Helping Hands",
                     "Award Amount": 50000, "Awarding Agency": "HHS",
                     "Start Date": "2026-06-01",
                     "recipient_location_state_code": "PA"},
                    {"Award ID": "B2", "Recipient Name": "Food Bank Inc",
                     "Award Amount": 12000, "Awarding Agency": "USDA",
                     "Start Date": "2026-06-02"},
                ],
                "page_metadata": {"page": 1, "hasNext": True},
            })
        return _FakeResp(json_data={
            "results": [
                # B2 repeated across pages → must dedup.
                {"Award ID": "B2", "Recipient Name": "Food Bank Inc",
                 "Award Amount": 12000, "Awarding Agency": "USDA"},
                {"Award ID": "C3", "Recipient Name": "Youth Center",
                 "Award Amount": 9000, "Awarding Agency": "ED"},
            ],
            "page_metadata": {"page": 2, "hasNext": False},
        })

    _patch_client(monkeypatch, usaspending, handler)
    orgs = await usaspending.fetch_recent_awards(date(2026, 6, 1), date(2026, 6, 8))

    keys = [o.dedup_key for o in orgs]
    assert keys == ["grant_awarded:A1", "grant_awarded:B2", "grant_awarded:C3"]
    a1 = orgs[0]
    assert a1.signal_type == "grant_awarded"
    assert a1.org_name == "Helping Hands"
    assert a1.state == "PA"
    assert a1.detail["amount"] == 50000
    assert a1.detail["agency"] == "HHS"
    assert a1.detail["award_id"] == "A1"
    assert "Helping Hands won a federal grant" in a1.summary
    assert "$50,000" in a1.summary


async def test_usaspending_outage_returns_empty(monkeypatch):
    def handler(url, body):
        raise RuntimeError("usaspending down")
    _patch_client(monkeypatch, usaspending, handler)
    orgs = await usaspending.fetch_recent_awards(date(2026, 6, 1), date(2026, 6, 8))
    assert orgs == []


# ---------------------------------------------------------------------------
# irs_bmf feed
# ---------------------------------------------------------------------------


_BMF_HEADER = "EIN,NAME,STATE,SUBSECTION,RULING,NTEE_CD,CLASSIFICATION"
_BMF_CSV = "\n".join([
    _BMF_HEADER,
    "11,New Charity,PA,3,202605,P20,1000",       # 501c3, in window → keep
    "22,Old Charity,PA,3,202601,A20,1000",        # 501c3, before window → drop
    "33,Social Welfare,PA,4,202605,,1000",        # 501c4 → drop
    "44,No Ruling,PA,3,,B20,1000",                # blank ruling → drop
    "55,Arts Group,PA,3,202606,A60,1000",         # 501c3, in window → keep
])


async def test_irs_bmf_filters_subsection_and_ruling_window(monkeypatch):
    def handler(url, params):
        assert url.endswith("/eo_pa.csv")
        return _FakeResp(text=_BMF_CSV)
    _patch_client(monkeypatch, irs_bmf, handler)

    orgs = await irs_bmf.fetch_new_501c3(["PA"], since_ruling="202604")
    keys = sorted(o.dedup_key for o in orgs)
    assert keys == ["new_501c3:11", "new_501c3:55"]
    keep = next(o for o in orgs if o.ein == "11")
    assert keep.signal_type == "new_501c3"
    assert keep.org_name == "New Charity"
    assert keep.state == "PA"
    assert keep.ntee_code == "P20"
    assert keep.detail["ruling_date"] == "202605"
    assert keep.detail["classification"] == "1000"


async def test_irs_bmf_state_outage_skipped(monkeypatch):
    def handler(url, params):
        raise RuntimeError("404")
    _patch_client(monkeypatch, irs_bmf, handler)
    orgs = await irs_bmf.fetch_new_501c3(["PA", "NJ"], since_ruling="202601")
    assert orgs == []


# ---------------------------------------------------------------------------
# staging — autonomy boundary
# ---------------------------------------------------------------------------


def _org(dedup="grant_awarded:X1", **kw) -> DiscoveredOrg:
    defaults = dict(
        signal_type="grant_awarded",
        summary="Helping Hands won a federal grant ($50,000)",
        dedup_key=dedup,
        org_name="Helping Hands",
        state="PA",
        ein="99",
        ntee_code="P20",
        website="https://helpinghands.org",
        detail={"amount": 50000, "agency": "HHS", "award_id": "X1"},
    )
    defaults.update(kw)
    return DiscoveredOrg(**defaults)


async def test_staging_with_email_creates_lead_signal_task_notification(
    db_session, monkeypatch,
):
    monkeypatch.setattr(
        funding_signals.enrichment, "resolve_contact",
        AsyncMock(return_value={
            "email": "ed@helpinghands.org",
            "first_name": "Dana", "last_name": "Reed",
            "title": "Executive Director", "generic": False,
        }),
    )
    org = _org()
    result = await _stage_discovery_signal(db_session, "usaspending", org)
    await db_session.commit()
    assert result == {"staged": True, "lead_created": True, "task": True}

    # Campaign-less Lead.
    lead = await db_session.scalar(
        select(Lead).where(Lead.email == "ed@helpinghands.org")
    )
    assert lead is not None
    assert lead.campaign_id is None                  # NEVER a campaign
    assert lead.company == "Helping Hands"
    assert lead.job_title == "Executive Director"
    assert lead.research_data["ein"] == "99"
    assert lead.research_data["source"] == "usaspending"

    # ProspectSignal — discovery: source set, watch_id NULL.
    signal = await db_session.scalar(
        select(ProspectSignal).where(ProspectSignal.dedup_key == org.dedup_key)
    )
    assert signal is not None
    assert signal.source == "usaspending"
    assert signal.watch_id is None
    assert signal.signal_type == "grant_awarded"
    assert signal.lead_id == lead.id

    # CRM reach-out task.
    task = await db_session.scalar(
        select(CrmActivity).where(
            CrmActivity.lead_id == lead.id,
            CrmActivity.activity_type == CrmActivityType.TASK,
        )
    )
    assert task is not None
    assert task.subject.startswith("Reach out —")
    assert task.is_agent_generated is True
    assert task.reminder_sent_at is not None

    # One owner notification.
    notifs = (await db_session.execute(
        select(Notification).where(
            Notification.dedup_key == f"prospect_signal:{org.dedup_key}"
        )
    )).scalars().all()
    assert len(notifs) == 1

    # NEVER a sequence enrollment.
    enrollments = (await db_session.execute(
        select(LeadSequenceState).where(LeadSequenceState.lead_id == lead.id)
    )).scalars().all()
    assert enrollments == []


async def test_staging_without_email_is_notification_only(db_session, monkeypatch):
    monkeypatch.setattr(
        funding_signals.enrichment, "resolve_contact",
        AsyncMock(return_value=None),
    )
    org = _org(dedup="new_501c3:77", signal_type="new_501c3",
               summary="New 501(c)(3): Tiny Org (PA)")
    result = await _stage_discovery_signal(db_session, "irs_bmf", org)
    await db_session.commit()
    assert result == {"staged": True, "lead_created": False, "task": False}

    # Signal + notification exist; no Lead, no task.
    signal = await db_session.scalar(
        select(ProspectSignal).where(ProspectSignal.dedup_key == org.dedup_key)
    )
    assert signal is not None
    assert signal.source == "irs_bmf"
    assert signal.lead_id is None
    assert (await db_session.execute(select(Lead))).scalars().first() is None
    assert (await db_session.execute(select(CrmActivity))).scalars().first() is None
    notifs = (await db_session.execute(select(Notification))).scalars().all()
    assert len(notifs) == 1


async def test_staging_dedup_emits_once(db_session, monkeypatch):
    monkeypatch.setattr(
        funding_signals.enrichment, "resolve_contact",
        AsyncMock(return_value=None),
    )
    org = _org(dedup="grant_awarded:DUP")
    r1 = await _stage_discovery_signal(db_session, "usaspending", org)
    await db_session.commit()
    r2 = await _stage_discovery_signal(db_session, "usaspending", org)
    await db_session.commit()
    assert r1["staged"] is True
    assert r2["staged"] is False
    count = (await db_session.execute(
        select(func.count()).select_from(ProspectSignal)
        .where(ProspectSignal.dedup_key == "grant_awarded:DUP")
    )).scalar_one()
    assert count == 1


# ---------------------------------------------------------------------------
# poll tasks — disabled no-op, cursor advance, first-run guard
# ---------------------------------------------------------------------------


async def test_usaspending_poll_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(funding_signals.settings, "USASPENDING_ENABLED", False)
    assert await _poll_usaspending_async() == {"skipped": "disabled"}


async def test_irs_bmf_poll_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(funding_signals.settings, "IRS_BMF_ENABLED", False)
    assert await _poll_irs_bmf_async() == {"skipped": "disabled"}


async def test_usaspending_poll_advances_cursor(db_session, monkeypatch):
    monkeypatch.setattr(funding_signals.settings, "USASPENDING_ENABLED", True)
    monkeypatch.setattr(
        funding_signals.usaspending, "fetch_recent_awards",
        AsyncMock(return_value=[_org(dedup="grant_awarded:CUR1")]),
    )
    monkeypatch.setattr(
        funding_signals.enrichment, "resolve_contact",
        AsyncMock(return_value=None),
    )
    result = await _poll_usaspending_async()
    assert result["fetched"] == 1
    assert result["staged"] == 1

    # Cursor advanced to today, status done (worker uses its own engine →
    # query via the test session sees the committed row).
    state = await db_session.get(FundingSourceState, "usaspending")
    assert state is not None
    assert state.cursor["last_action_date"] == datetime.now(timezone.utc).date().isoformat()
    assert state.last_run_status == "done"


async def test_irs_bmf_first_run_guard_bounds_window(db_session, monkeypatch):
    monkeypatch.setattr(funding_signals.settings, "IRS_BMF_ENABLED", True)
    monkeypatch.setattr(funding_signals.settings, "IRS_BMF_STATES", ["PA"])
    monkeypatch.setattr(funding_signals.settings, "IRS_BMF_RULING_LOOKBACK_MONTHS", 2)

    captured = {}

    async def _fake_fetch(states, since_ruling):
        captured["since_ruling"] = since_ruling
        captured["states"] = states
        return []

    monkeypatch.setattr(funding_signals.irs_bmf, "fetch_new_501c3", _fake_fetch)
    result = await _poll_irs_bmf_async()

    today = datetime.now(timezone.utc).date()
    # First run with no cursor → bounded to the lookback floor, NOT the
    # whole historical file.
    assert captured["since_ruling"] == _yyyymm(today, 2)
    assert captured["states"] == ["PA"]
    assert result["since_ruling"] == _yyyymm(today, 2)

    state = await db_session.get(FundingSourceState, "irs_bmf")
    assert state.cursor["last_file_month"] == _yyyymm(today)
    assert state.last_run_status == "done"


async def test_yyyymm_helper():
    assert _yyyymm(date(2026, 6, 15)) == "202606"
    assert _yyyymm(date(2026, 6, 15), 2) == "202604"
    assert _yyyymm(date(2026, 1, 15), 2) == "202511"   # crosses year boundary


# ---------------------------------------------------------------------------
# API surface — source serializer + filter (discovery signals appear)
# ---------------------------------------------------------------------------


async def test_signals_list_exposes_source_and_filters(client, db_session):
    # A discovery signal (watch_id NULL, source set) + a watch signal.
    db_session.add_all([
        ProspectSignal(
            watch_id=None, source="usaspending", signal_type="grant_awarded",
            summary="Helping Hands won a federal grant",
            dedup_key="grant_awarded:API1", detail={"amount": 50000},
        ),
        ProspectSignal(
            watch_id=None, source="irs_bmf", signal_type="new_501c3",
            summary="New 501(c)(3): Tiny Org",
            dedup_key="new_501c3:API2", detail={},
        ),
        # watch-sourced (source NULL) — must still appear, no inner join.
        ProspectSignal(
            watch_id=None, source=None, signal_type="job_change",
            summary="Someone changed roles",
            dedup_key="job_change:API3", detail={},
        ),
    ])
    await db_session.commit()

    # No filter → all three, each carrying its source in the serializer.
    body = (await client.get("/signals")).json()
    assert body["total"] == 3
    by_key = {i["summary"]: i for i in body["items"]}
    assert by_key["Helping Hands won a federal grant"]["source"] == "usaspending"
    assert by_key["Someone changed roles"]["source"] is None

    # source=usaspending → just the grant.
    body = (await client.get("/signals?source=usaspending")).json()
    assert body["total"] == 1
    assert body["items"][0]["source"] == "usaspending"

    # source=watch → only the watch-sourced (source NULL) signal.
    body = (await client.get("/signals?source=watch")).json()
    assert body["total"] == 1
    assert body["items"][0]["source"] is None


# ---------------------------------------------------------------------------
# Settings → Discovery: DB-backed config + API
# ---------------------------------------------------------------------------


async def test_funding_sources_list_seeds_from_env(client, db_session, monkeypatch):
    monkeypatch.setattr(funding_signals.settings, "USASPENDING_ENABLED", True)
    monkeypatch.setattr(funding_signals.settings, "USASPENDING_LOOKBACK_DAYS", 7)
    monkeypatch.setattr(funding_signals.settings, "IRS_BMF_ENABLED", True)
    monkeypatch.setattr(funding_signals.settings, "IRS_BMF_STATES", ["PA", "NJ"])
    # router reads settings.HUNTER_API_KEY for the hunter_configured flag
    from app.routers import signals as signals_router
    monkeypatch.setattr(signals_router.settings, "HUNTER_API_KEY", "")

    resp = await client.get("/signals/funding/sources")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["hunter_configured"] is False
    by_src = {s["source"]: s for s in body["sources"]}
    assert set(by_src) == {"usaspending", "irs_bmf"}
    assert by_src["usaspending"]["enabled"] is True
    assert by_src["usaspending"]["label"] == "USASpending"
    assert by_src["usaspending"]["config"]["lookback_days"] == 7
    assert by_src["irs_bmf"]["config"]["states"] == ["PA", "NJ"]
    assert by_src["usaspending"]["signal_count"] == 0

    # Seeding persisted the rows.
    state = await db_session.get(FundingSourceState, "usaspending")
    assert state is not None and state.enabled is True


async def test_funding_source_patch_updates_config(client, db_session, monkeypatch):
    monkeypatch.setattr(funding_signals.settings, "IRS_BMF_ENABLED", False)
    monkeypatch.setattr(funding_signals.settings, "IRS_BMF_STATES", [])

    resp = await client.patch("/signals/funding/sources/irs_bmf", json={
        "enabled": True,
        "ruling_lookback_months": 3,
        "states": ["pa", " NJ ", "ny", "pa"],   # normalise + dedupe
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is True
    assert body["config"]["ruling_lookback_months"] == 3
    assert body["config"]["states"] == ["PA", "NJ", "NY"]

    state = await db_session.get(FundingSourceState, "irs_bmf")
    await db_session.refresh(state)
    assert state.enabled is True
    assert state.config["states"] == ["PA", "NJ", "NY"]


async def test_funding_source_patch_unknown_404(client):
    resp = await client.patch("/signals/funding/sources/nope", json={"enabled": True})
    assert resp.status_code == 404


async def test_funding_run_now_enqueues_when_enabled(client, db_session, monkeypatch):
    monkeypatch.setattr(funding_signals.settings, "USASPENDING_ENABLED", True)
    called = {}
    # Patch the lazily-imported celery task's .delay so no broker is hit.
    monkeypatch.setattr(
        funding_signals.poll_usaspending, "delay",
        lambda: called.setdefault("delay", True),
    )

    resp = await client.post("/signals/funding/sources/usaspending/run-now")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"enqueued": True}
    assert called.get("delay") is True


async def test_funding_run_now_409_when_disabled(client, db_session, monkeypatch):
    monkeypatch.setattr(funding_signals.settings, "USASPENDING_ENABLED", False)
    resp = await client.post("/signals/funding/sources/usaspending/run-now")
    assert resp.status_code == 409
    assert "disabled" in resp.json()["detail"]


async def test_funding_run_now_409_irs_without_states(client, db_session, monkeypatch):
    monkeypatch.setattr(funding_signals.settings, "IRS_BMF_ENABLED", True)
    monkeypatch.setattr(funding_signals.settings, "IRS_BMF_STATES", [])
    resp = await client.post("/signals/funding/sources/irs_bmf/run-now")
    assert resp.status_code == 409
    assert "state" in resp.json()["detail"].lower()


async def test_worker_honors_db_config_over_env(db_session, monkeypatch):
    """A UI-set DB row wins over env: env says disabled, DB says enabled
    with its own lookback → the poll runs with the DB lookback."""
    # Env default disabled — but a pre-existing DB row (UI-enabled) wins.
    monkeypatch.setattr(funding_signals.settings, "USASPENDING_ENABLED", False)
    db_session.add(FundingSourceState(
        source="usaspending", enabled=True,
        config={"lookback_days": 14}, cursor={},
    ))
    await db_session.commit()

    captured = {}

    async def _fake_fetch(since, until, *, limit=100):
        captured["since"] = since
        captured["until"] = until
        return []

    monkeypatch.setattr(funding_signals.usaspending, "fetch_recent_awards", _fake_fetch)
    result = await _poll_usaspending_async()
    assert result.get("fetched") == 0          # ran (not skipped)
    # 14-day lookback from the DB config, not the env default 7.
    assert (captured["until"] - captured["since"]).days == 14
