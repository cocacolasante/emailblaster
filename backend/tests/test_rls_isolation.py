"""Postgres row-level security: the database-level tenant safety net.

Builds a dedicated database with ``alembic upgrade head`` (so the real
migrations — policies, the SECURITY DEFINER resolver — are what's
tested), creates the non-owner runtime role through the real
``app.scripts.bootstrap_db``, seeds two workspaces as the owner, then
connects AS THE RUNTIME ROLE and proves:

- no ``app.tenant_id`` setting → zero rows (fail closed);
- a workspace sees only its own rows, even with the ORM filter skipped;
- updates/deletes can't touch another workspace's rows;
- inserts can't write another workspace's tenant_id (WITH CHECK);
- every table with a tenant_id is covered by a tenant_isolation policy;
- ``app_tenant_of`` resolves across workspaces and rejects unknown kinds;
- the runtime role is not the owner and can't bypass RLS.
"""
from __future__ import annotations

import os
import subprocess
import uuid

import asyncpg
import pytest

RLS_DB = "emailblaster_rls_test"
OWNER = "postgresql://emailblaster:emailblaster@postgres:5432"
APP_ROLE, APP_PW = "eb_rls_test_app", "rls-test-password"
OWNER_DSN = f"{OWNER}/{RLS_DB}"
APP_DSN = f"postgresql://{APP_ROLE}:{APP_PW}@postgres:5432/{RLS_DB}"

# Tables that carry tenant_id but are deliberately outside RLS.
NOT_RLS = {"webhook_events", "memberships", "user_sessions", "invitations"}

A = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
B = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000001")


async def _admin(sql: str) -> None:
    conn = await asyncpg.connect(f"{OWNER}/postgres")
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


def _run(*cmd: str, **env: str) -> None:
    result = subprocess.run(
        list(cmd), cwd="/app", env={**os.environ, **env}, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"{cmd} failed:\n{result.stdout}\n{result.stderr}"


@pytest.fixture(scope="module")
def rls_db():
    import asyncio

    asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{RLS_DB}" WITH (FORCE)'))
    asyncio.run(_admin(f'CREATE DATABASE "{RLS_DB}"'))
    owner_url = f"postgresql+asyncpg://emailblaster:emailblaster@postgres:5432/{RLS_DB}"
    app_url = f"postgresql+asyncpg://{APP_ROLE}:{APP_PW}@postgres:5432/{RLS_DB}"
    _run("alembic", "upgrade", "head", DATABASE_URL=owner_url, BOOTSTRAP_OWNER_EMAIL="",
         OWNER_NOTIFY_EMAIL="")
    _run("python", "-m", "app.scripts.bootstrap_db", DATABASE_URL=owner_url, APP_DATABASE_URL=app_url)
    asyncio.run(_seed())
    yield app_url
    asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{RLS_DB}" WITH (FORCE)'))


async def _seed() -> None:
    conn = await asyncpg.connect(OWNER_DSN)
    try:
        for tid, name in ((A, "A"), (B, "B")):
            await conn.execute("INSERT INTO tenants (id, name, slug) VALUES ($1, $2, $3)",
                               tid, f"Workspace {name}", f"ws-{name.lower()}")
            uid = await conn.fetchval("INSERT INTO users (email) VALUES ($1) RETURNING id",
                                      f"owner@{name.lower()}.com")
            await conn.execute("INSERT INTO memberships (tenant_id, user_id, role) VALUES ($1, $2, 'owner')",
                               tid, uid)
            cid = await conn.fetchval(
                "INSERT INTO campaigns (id, tenant_id, owner_id, name, goal, tone, sender_name, "
                "sender_email, schedule_days, schedule_time_start, schedule_time_end) "
                "VALUES (gen_random_uuid(), $1, $2, $3, 'g', 't', 's', 's@x.com', '{}', '09:00', '17:00') "
                "RETURNING id", tid, uid, f"{name} campaign")
            await conn.execute(
                "INSERT INTO leads (id, tenant_id, owner_id, campaign_id, email) "
                "VALUES (gen_random_uuid(), $1, $2, $3, $4)", tid, uid, cid, f"lead@{name.lower()}.com")
            await conn.execute(
                "INSERT INTO suppression_list (id, tenant_id, email, reason) "
                "VALUES (gen_random_uuid(), $1, 'same@x.com', 'manual')", tid)
            await conn.execute(
                "INSERT INTO tenant_provider_keys (tenant_id, provider, encrypted_credentials) "
                "VALUES ($1, 'hunter', $2)", tid, f"secret-of-{name}")
    finally:
        await conn.close()


async def _app_conn(tenant: uuid.UUID | None = None) -> asyncpg.Connection:
    conn = await asyncpg.connect(APP_DSN)
    if tenant is not None:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant))
    return conn


async def test_no_setting_sees_nothing(rls_db):
    conn = await _app_conn()
    try:
        for t in ("leads", "campaigns", "suppression_list", "tenant_provider_keys"):
            assert await conn.fetchval(f"SELECT count(*) FROM {t}") == 0, t
    finally:
        await conn.close()


async def test_workspace_sees_only_its_rows(rls_db):
    conn = await _app_conn(A)
    try:
        assert [r["email"] for r in await conn.fetch("SELECT email FROM leads")] == ["lead@a.com"]
        keys = [r["encrypted_credentials"] for r in await conn.fetch("SELECT * FROM tenant_provider_keys")]
        assert keys == ["secret-of-A"]
        # A WHERE naming the other workspace still returns nothing.
        assert await conn.fetchval("SELECT count(*) FROM leads WHERE tenant_id = $1", B) == 0
    finally:
        await conn.close()


async def test_cannot_modify_other_workspace(rls_db):
    conn = await _app_conn(A)
    try:
        assert await conn.execute("UPDATE leads SET first_name = 'x' WHERE tenant_id = $1", B) == "UPDATE 0"
        assert await conn.execute("DELETE FROM campaigns WHERE tenant_id = $1", B) == "DELETE 0"
        # Moving your own row into another workspace is rejected too.
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute("UPDATE leads SET tenant_id = $1", B)
    finally:
        await conn.close()
    owner = await asyncpg.connect(OWNER_DSN)
    try:
        assert await owner.fetchval("SELECT count(*) FROM campaigns WHERE tenant_id = $1", B) == 1
    finally:
        await owner.close()


async def test_insert_with_foreign_tenant_rejected(rls_db):
    conn = await _app_conn(A)
    try:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO suppression_list (id, tenant_id, email, reason) "
                "VALUES (gen_random_uuid(), $1, 'evil@x.com', 'manual')", B)
    finally:
        await conn.close()


async def test_every_tenant_table_has_a_policy(rls_db):
    owner = await asyncpg.connect(OWNER_DSN)
    try:
        with_col = {r["table_name"] for r in await owner.fetch(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name = 'tenant_id'")}
        covered = {r["tablename"] for r in await owner.fetch(
            "SELECT c.relname AS tablename FROM pg_class c "
            "JOIN pg_policies p ON p.tablename = c.relname AND p.policyname = 'tenant_isolation' "
            "WHERE c.relrowsecurity AND c.relnamespace = 'public'::regnamespace")}
    finally:
        await owner.close()
    assert with_col - NOT_RLS == covered, (
        f"missing RLS: {sorted(with_col - NOT_RLS - covered)}; unexpected: {sorted(covered - with_col)}"
    )


async def test_metadata_tenanted_tables_match_rls(rls_db):
    """Every TenantMixin model is covered (catches a new model added without
    extending the RLS migration)."""
    import app.models  # noqa: F401
    from app.tenancy.mixin import tenanted_models

    owner = await asyncpg.connect(OWNER_DSN)
    try:
        covered = {r["tablename"] for r in await owner.fetch(
            "SELECT tablename FROM pg_policies WHERE policyname = 'tenant_isolation'")}
    finally:
        await owner.close()
    assert {m.__tablename__ for m in tenanted_models()} == covered


async def test_tenant_of_resolver(rls_db):
    owner = await asyncpg.connect(OWNER_DSN)
    try:
        b_lead = await owner.fetchval("SELECT id FROM leads WHERE tenant_id = $1", B)
    finally:
        await owner.close()
    conn = await _app_conn()  # no tenant context at all
    try:
        assert await conn.fetchval("SELECT app_tenant_of('lead', $1)", str(b_lead)) == B
        assert await conn.fetchval("SELECT app_tenant_of('tenant', $1)", str(A)) == A
        assert await conn.fetchval("SELECT app_tenant_of('lead', $1)", str(uuid.uuid4())) is None
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await conn.fetchval("SELECT app_tenant_of('users', 'x')")
    finally:
        await conn.close()


async def test_runtime_role_cannot_bypass(rls_db):
    conn = await _app_conn()
    try:
        row = await conn.fetchrow(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        assert row["rolsuper"] is False and row["rolbypassrls"] is False
        owner = await conn.fetchval("SELECT tableowner FROM pg_tables WHERE tablename = 'leads'")
        assert owner != APP_ROLE
        # Identity tables stay readable (auth runs before a tenant is known).
        assert await conn.fetchval("SELECT count(*) FROM tenants") == 2
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.fetchval("SELECT count(*) FROM alembic_version")
    finally:
        await conn.close()


async def test_orm_path_enforced_even_when_app_filter_skipped(rls_db):
    """Through SQLAlchemy as the runtime role: the after_begin hook stamps
    app.tenant_id, and RLS holds even with the ORM tenant filter off."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.models import Lead
    from app.tenancy.context import tenant_scope

    engine = create_async_engine(rls_db)
    try:
        with tenant_scope(B):
            async with AsyncSession(engine) as s:
                emails = (await s.execute(
                    select(Lead.email).execution_options(skip_tenant_filter=True)
                )).scalars().all()
        assert emails == ["lead@b.com"]
        with tenant_scope(None):
            async with AsyncSession(engine) as s:
                assert (await s.execute(select(Lead.email))).scalars().all() == []
    finally:
        await engine.dispose()
