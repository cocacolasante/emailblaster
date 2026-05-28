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
_force_env("REDIS_URL", "redis://localhost:6379/15")
_force_env("ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ["SECRET_KEY"] = "test-secret"
_force_env("FRONTEND_URL", "http://localhost:5173")
_force_env("WEBHOOK_BASE_URL", "http://localhost:8000")

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


@pytest_asyncio.fixture
async def _engine():
    """Per-test async engine bound to the current event loop. Truncates first."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text(
            "TRUNCATE TABLE lead_step_executions, lead_sequence_states, "
            "sequence_edges, sequence_nodes, sequences, "
            "email_events, leads, style_corrections, "
            "suppression_list, campaigns, connected_accounts, "
            "linkedin_accounts, webhook_events, research_cache "
            "RESTART IDENTITY CASCADE"
        ))
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(_engine):
    async with AsyncSession(_engine, expire_on_commit=False) as session:
        yield session


@pytest_asyncio.fixture
async def client(_engine):
    """AsyncClient against the FastAPI app with get_db pointing at the test engine.

    raise_app_exceptions=False so the global exception handler can return a
    500 response in tests instead of httpx re-raising the original exception.
    """
    from app.database import get_db
    from app.main import app

    SessionLocal = async_sessionmaker(_engine, expire_on_commit=False)

    async def _override_get_db():
        async with SessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
