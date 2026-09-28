"""Pytest configuration: set test env vars BEFORE app modules load."""
import asyncio
import os

from cryptography.fernet import Fernet


def _force_env(key: str, value: str) -> None:
    """Set env var, overriding empty values injected by docker-compose."""
    if not os.environ.get(key):
        os.environ[key] = value


# Test database lives on the same postgres instance as dev, in a separate DB.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://emailblaster:emailblaster@postgres:5432/emailblaster_test",
)
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_DATABASE_URL",
    "postgresql+asyncpg://emailblaster:emailblaster@postgres:5432/postgres",
)
TEST_DB_NAME = "emailblaster_test"

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# NEVER inherit the compose runtime-role URL: it points at the real DB.
# Tests run on the owner role (app-level scoping); RLS is exercised by
# test_rls_isolation.py against its own database + role.
os.environ["APP_DATABASE_URL"] = ""
_force_env("REDIS_URL", "redis://localhost:6379/15")
_force_env("ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ["SECRET_KEY"] = "test-secret"
_force_env("FRONTEND_URL", "http://localhost:5173")
_force_env("WEBHOOK_BASE_URL", "http://localhost:8000")
# Force-blank UNCONDITIONALLY (docker compose injects the real .env value):
# a configured owner address would make notification tests attempt REAL
# Brevo sends to the operator's inbox.  Tests that need it set monkeypatch
# notifications.settings.OWNER_NOTIFY_EMAIL explicitly.
os.environ["OWNER_NOTIFY_EMAIL"] = ""

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402


# --------------------------------------------------------------------------
# One-time test database + schema initialization (synchronous, before tests).
# pytest-asyncio creates a new event loop per test, and asyncpg pools are
# loop-bound, so each per-test fixture below builds its own engine.
# --------------------------------------------------------------------------


async def _initialize_test_db() -> None:
    admin = create_async_engine(ADMIN_DATABASE_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"'))
        await conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    await admin.dispose()

    import app.models  # noqa: F401 — register Base.metadata
    from app.database import Base

    schema_engine = create_async_engine(TEST_DATABASE_URL)
    async with schema_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await schema_engine.dispose()


asyncio.run(_initialize_test_db())


# --------------------------------------------------------------------------
# Per-test fixtures
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Default identity: every test runs as DEFAULT_USER (owner) in
# DEFAULT_TENANT, authenticated through the real session-cookie path.
# --------------------------------------------------------------------------

import hashlib  # noqa: E402
import uuid  # noqa: E402

import pytest  # noqa: E402

DEFAULT_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
DEFAULT_USER_EMAIL = "owner@test.local"
DEFAULT_SESSION_TOKEN = "test-session-token-default-user"


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _seed_default_identity(conn) -> None:
    await conn.execute(text(
        "INSERT INTO tenants (id, name, slug) VALUES (:t, 'Test Workspace', 'test-workspace')"
    ), {"t": DEFAULT_TENANT_ID})
    await conn.execute(text(
        "INSERT INTO users (id, email, name) VALUES (:u, :e, 'Test Owner')"
    ), {"u": DEFAULT_USER_ID, "e": DEFAULT_USER_EMAIL})
    await conn.execute(text(
        "INSERT INTO memberships (tenant_id, user_id, role) VALUES (:t, :u, 'owner')"
    ), {"t": DEFAULT_TENANT_ID, "u": DEFAULT_USER_ID})
    await conn.execute(text(
        "INSERT INTO user_sessions (token_hash, user_id, tenant_id, expires_at) "
        "VALUES (:h, :u, :t, now() + interval '1 day')"
    ), {"h": _token_hash(DEFAULT_SESSION_TOKEN), "u": DEFAULT_USER_ID, "t": DEFAULT_TENANT_ID})


async def create_test_user(
    engine, *, tenant_id=None, email=None, role="member", name=None,
    new_tenant_name=None,
):
    """Insert a user (+ optional new workspace) with a live session.

    Returns ``(user_id, tenant_id, session_token)``.
    """
    user_id = uuid.uuid4()
    email = email or f"user-{user_id.hex[:8]}@test.local"
    token = f"test-session-{user_id.hex}"
    async with engine.begin() as conn:
        if tenant_id is None:
            tenant_id = uuid.uuid4()
            await conn.execute(text(
                "INSERT INTO tenants (id, name, slug) VALUES (:t, :n, :s)"
            ), {"t": tenant_id, "n": new_tenant_name or "Other Workspace",
                "s": f"ws-{tenant_id.hex[:10]}"})
        await conn.execute(text(
            "INSERT INTO users (id, email, name) VALUES (:u, :e, :n)"
        ), {"u": user_id, "e": email, "n": name})
        await conn.execute(text(
            "INSERT INTO memberships (tenant_id, user_id, role) "
            "VALUES (:t, :u, CAST(:r AS membership_role))"
        ), {"t": tenant_id, "u": user_id, "r": role})
        await conn.execute(text(
            "INSERT INTO user_sessions (token_hash, user_id, tenant_id, expires_at) "
            "VALUES (:h, :u, :t, now() + interval '1 day')"
        ), {"h": _token_hash(token), "u": user_id, "t": tenant_id})
    return user_id, tenant_id, token


@pytest.fixture(autouse=True)
def _default_tenant_context():
    """Every test runs inside DEFAULT_TENANT's context (and DEFAULT_USER's),
    so rows a test inserts directly via ``db_session`` are stamped by the
    TenantMixin default — just as a request would stamp them.

    Deliberately a SYNC fixture: pytest-asyncio runs async fixtures in
    their own task, and ContextVars set there never reach the test.
    """
    from app.tenancy.context import current_tenant_id, current_user_id

    t1 = current_tenant_id.set(DEFAULT_TENANT_ID)
    t2 = current_user_id.set(DEFAULT_USER_ID)
    yield
    current_user_id.reset(t2)
    current_tenant_id.reset(t1)


@pytest.fixture(autouse=True)
def _isolated_auth_ratelimit(monkeypatch):
    """Auth rate-limit counters go to a per-test fakeredis, never the real
    Redis the test container can reach (counters would leak across runs)."""
    import fakeredis.aioredis

    from app.auth import ratelimit

    server = fakeredis.FakeServer()
    monkeypatch.setattr(
        ratelimit, "_redis",
        lambda: fakeredis.aioredis.FakeRedis(server=server, decode_responses=True),
    )


@pytest_asyncio.fixture
async def _engine():
    """Per-test async engine bound to the current event loop. Truncates first."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text(
            "TRUNCATE TABLE lookalike_candidates, icp_profiles, "
            "org_intent_scores, signals, orgs, icp_intent_profiles, "
            "funding_enrichment_queue, "
            "funding_source_state, prospect_signals, signal_watches, "
            "reply_outcomes, campaign_copy_insights, "
            "agent_actions, notifications, agent_settings, "
            "report_definitions, opportunity_stage_changes, "
            "crm_documents, crm_opportunity_products, "
            "crm_activities, crm_opportunities, "
            "contacts, accounts, opportunity_stages, pipelines, "
            "lead_step_executions, lead_sequence_states, "
            "sequence_edges, sequence_nodes, sequences, "
            "email_events, leads, style_corrections, "
            "suppression_list, campaigns, connected_accounts, "
            "linkedin_accounts, linkedin_profile_cache, "
            "webhook_events, research_cache, "
            "social_listening_opportunities, social_listening_posts, "
            "social_listening_searches, "
            "tenant_provider_keys, api_keys, outreach_drafts, "
            "invitations, auth_tokens, user_sessions, memberships, users, tenants "
            "RESTART IDENTITY CASCADE"
        ))
        await _seed_default_identity(conn)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(_engine):
    async with AsyncSession(_engine, expire_on_commit=False) as session:
        yield session


def _make_client(app, token: str | None) -> AsyncClient:
    c = AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )
    if token:
        c.cookies.set("eb_session", token)
    return c


@pytest_asyncio.fixture
async def client_factory(_engine):
    """Build extra clients: ``client_factory(token)`` (or ``None`` for an
    anonymous client).  All share the test DB via the factory override."""
    from app.database import get_session_factory
    from app.main import app

    SessionLocal = async_sessionmaker(_engine, expire_on_commit=False)
    app.dependency_overrides[get_session_factory] = lambda: SessionLocal
    made: list[AsyncClient] = []

    def _factory(token: str | None = DEFAULT_SESSION_TOKEN) -> AsyncClient:
        c = _make_client(app, token)
        made.append(c)
        return c

    try:
        yield _factory
    finally:
        for c in made:
            await c.aclose()
        app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(client_factory):
    """AsyncClient authenticated as DEFAULT_USER (owner of DEFAULT_TENANT),
    through the real cookie-session → get_db path against the test DB.

    raise_app_exceptions=False so the global exception handler can return a
    500 response in tests instead of httpx re-raising the original exception.
    """
    yield client_factory()


@pytest_asyncio.fixture
async def anon_client(client_factory):
    """Unauthenticated AsyncClient."""
    yield client_factory(None)


# --------------------------------------------------------------------------
# Workspace credentials
# --------------------------------------------------------------------------

import pytest  # noqa: E402

_CRED_DEFAULTS = {
    "brevo": {"sender_email": "sender@test.local", "sender_name": "Test Sender"},
    "unipile": {"dsn": "api.test.unipile.local:443"},
}


@pytest.fixture
def set_creds(monkeypatch):
    """Override the current workspace's provider credentials for one test.

        set_creds("brevo", api_key="k")      # merge fields (defaults fill
                                             #  sender_email / dsn)
        set_creds("hunter", api_key="")      # → provider reads as missing
        set_creds("anthropic", None)         # → explicitly missing

    Patches ``credentials.get`` so every consumer (require / is_configured
    / default_sender / get_client / get_provider) sees the override.
    """
    from app.services import credentials

    raw: dict[str, dict | None] = {}
    real_get = credentials.get

    def fake_get(provider):
        if provider in raw:
            data = raw[provider]
            return credentials.build_creds(provider, data) if data else None
        return real_get(provider)

    monkeypatch.setattr(credentials, "get", fake_get)

    def _set(provider, _missing=..., **fields):
        if _missing is None:
            raw[provider] = None
            return
        cur = raw.get(provider) or dict(_CRED_DEFAULTS.get(provider, {}))
        cur.update(fields)
        raw[provider] = cur

    return _set
