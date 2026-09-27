"""Background work runs per workspace, with that workspace's context."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models import Lead
from app.services import credentials
from app.tenancy.context import current_tenant_id, tenant_scope
from app.tenancy.worker import for_each_tenant, in_record_tenant, run_per_tenant, tenant_of
from tests.conftest import DEFAULT_TENANT_ID, create_test_user


async def test_for_each_tenant_visits_every_active_workspace(_engine):
    _, b, _ = await create_test_user(_engine)
    seen = []

    async def body():
        seen.append(current_tenant_id.get())
        return {"n": 1}

    summary = await for_each_tenant(body)
    assert set(seen) == {DEFAULT_TENANT_ID, b}
    assert summary["tenants"] == 2 and summary["errors"] == 0
    assert (await run_per_tenant(body))["n"] == 2


async def test_suspended_workspace_skipped(_engine):
    from sqlalchemy import text

    _, b, _ = await create_test_user(_engine)
    async with _engine.begin() as conn:
        await conn.execute(text("UPDATE tenants SET status = 'suspended' WHERE id = :t"), {"t": b})
    seen = []

    async def body():
        seen.append(current_tenant_id.get())

    await for_each_tenant(body)
    assert seen == [DEFAULT_TENANT_ID]


async def test_one_workspace_failure_does_not_stop_others(_engine):
    _, b, _ = await create_test_user(_engine)
    done = []

    async def body():
        if current_tenant_id.get() == DEFAULT_TENANT_ID:
            raise RuntimeError("boom")
        done.append(current_tenant_id.get())
        return {"ok": 1}

    summary = await for_each_tenant(body)
    assert done == [b] and summary["errors"] == 1


async def test_worker_queries_scoped_to_each_workspace(db_session, _engine):
    _, b, _ = await create_test_user(_engine)
    db_session.add(Lead(email="a@a.com"))
    with tenant_scope(b):
        db_session.add(Lead(email="b@b.com"))
    await db_session.commit()
    from app.tenancy.worker_db import worker_engine
    from sqlalchemy.ext.asyncio import AsyncSession

    found: dict = {}

    async def body():
        engine = worker_engine()
        try:
            async with AsyncSession(engine) as s:
                found[current_tenant_id.get()] = sorted(
                    (await s.execute(select(Lead.email))).scalars().all()
                )
        finally:
            await engine.dispose()

    await for_each_tenant(body)
    assert found == {DEFAULT_TENANT_ID: ["a@a.com"], b: ["b@b.com"]}


async def test_tenant_of_resolves_records(db_session, _engine):
    _, b, _ = await create_test_user(_engine)
    with tenant_scope(b):
        lead = Lead(email="x@b.com")
        db_session.add(lead)
        await db_session.commit()
    assert await tenant_of("lead", lead.id) == b
    assert await tenant_of("lead", uuid.uuid4()) is None
    assert await tenant_of("lead", "not-a-uuid") is None
    assert await tenant_of("tenant", b) == b


async def test_in_record_tenant_runs_body_in_records_workspace(db_session, _engine):
    _, b, _ = await create_test_user(_engine)
    with tenant_scope(b):
        lead = Lead(email="y@b.com")
        db_session.add(lead)
        await db_session.commit()

    @in_record_tenant("lead", "lead_id")
    async def task_body(lead_id):
        return current_tenant_id.get()

    assert await task_body(str(lead.id)) == b  # even though the test runs in A


async def test_credentials_bound_per_workspace(monkeypatch, _engine):
    """tenant_context binds the workspace's own credential bundle."""
    _, b, _ = await create_test_user(_engine)
    bundles = {
        DEFAULT_TENANT_ID: credentials.CredentialBundle(
            DEFAULT_TENANT_ID, {"hunter": credentials.HunterCreds("key-a")}),
        b: credentials.CredentialBundle(b, {"hunter": credentials.HunterCreds("key-b")}),
    }

    async def fake_load(factory, tid):
        return bundles[tid]

    monkeypatch.setattr(credentials, "load_bundle", fake_load)
    keys = {}

    async def body():
        c = credentials.get("hunter")
        keys[current_tenant_id.get()] = c.api_key if c else None

    await for_each_tenant(body)
    assert keys == {DEFAULT_TENANT_ID: "key-a", b: "key-b"}
