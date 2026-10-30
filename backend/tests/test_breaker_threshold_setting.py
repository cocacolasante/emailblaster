"""Each workspace can set its own bounce rate for the deliverability
breaker (Settings → Workspace); unset means the deployment default."""
from __future__ import annotations

from decimal import Decimal

from app.config import settings
from app.models import CampaignStatus
from app.services import agent_core, deliverability
from tests.conftest import DEFAULT_TENANT_ID, create_test_user
from tests.test_phase37_deliverability import _make_campaign, _seed_events


async def _set_threshold(db_session, value):
    row = await agent_core.get_agent_settings(db_session)
    row.breaker_bounce_rate = None if value is None else Decimal(str(value))
    await db_session.commit()


async def test_default_threshold_applies_when_unset(db_session):
    assert await deliverability.bounce_threshold(db_session) == settings.CIRCUIT_BREAKER_BOUNCE_RATE


async def test_higher_workspace_threshold_keeps_campaign_running(db_session):
    campaign = await _make_campaign(db_session)
    # 20 outcomes, 2 hard bounces = 10%: trips the 5% default...
    await _seed_events(db_session, campaign, delivered=18, hard_bounce=2)
    assert (await deliverability.evaluate_campaign_health(db_session, campaign.id)).trip is True

    # ...but not a workspace that allows 12%.
    await _set_threshold(db_session, 0.12)
    assert await deliverability.check_and_trip(db_session, campaign.id) is False
    assert campaign.status is CampaignStatus.RUNNING


async def test_lower_workspace_threshold_trips_sooner(db_session):
    campaign = await _make_campaign(db_session)
    # 25 outcomes, 1 bounce = 4%: under the 5% default, over a 3% setting.
    await _seed_events(db_session, campaign, delivered=24, hard_bounce=1)
    assert (await deliverability.evaluate_campaign_health(db_session, campaign.id)).trip is False
    await _set_threshold(db_session, 0.03)
    verdict = await deliverability.evaluate_campaign_health(db_session, campaign.id)
    assert verdict.trip is True and "3% threshold" in verdict.reason


async def test_settings_endpoint_round_trip(client):
    got = (await client.get("/settings/deliverability")).json()
    assert got["bounce_threshold"] == settings.CIRCUIT_BREAKER_BOUNCE_RATE and got["custom"] is False

    put = await client.put("/settings/deliverability", json={"bounce_threshold": 0.06})
    assert put.status_code == 200, put.text
    assert put.json()["bounce_threshold"] == 0.06 and put.json()["custom"] is True
    assert (await client.get("/settings/deliverability")).json()["bounce_threshold"] == 0.06

    reset = await client.put("/settings/deliverability", json={"bounce_threshold": None})
    assert reset.json()["custom"] is False
    assert reset.json()["bounce_threshold"] == settings.CIRCUIT_BREAKER_BOUNCE_RATE


async def test_settings_endpoint_validates_range(client):
    for bad in (0.001, 0.5, -1):
        assert (await client.put("/settings/deliverability", json={"bounce_threshold": bad})).status_code == 422


async def test_only_managers_can_change_it(client_factory, _engine):
    _, _, token = await create_test_user(_engine, tenant_id=DEFAULT_TENANT_ID, role="member")
    member = client_factory(token)
    assert (await member.get("/settings/deliverability")).status_code == 200
    assert (await member.put("/settings/deliverability", json={"bounce_threshold": 0.1})).status_code == 403


async def test_threshold_is_per_workspace(client, client_factory, _engine):
    await client.put("/settings/deliverability", json={"bounce_threshold": 0.15})
    _, _, token = await create_test_user(_engine, role="owner", new_tenant_name="Other")
    other = client_factory(token)
    assert (await other.get("/settings/deliverability")).json()["custom"] is False
