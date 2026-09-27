"""Engine factory for Celery tasks.

Each Celery task body runs under its own ``asyncio.run`` loop, and
asyncpg pools are loop-bound, so tasks build a short-lived engine per
invocation and dispose it at the end.  Centralised here so the runtime
DB role (``APP_DATABASE_URL``, the non-owner role that RLS applies to)
is switched in exactly one place.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import settings


def runtime_database_url() -> str:
    """URL the running app connects with (non-owner role once RLS is on)."""
    return getattr(settings, "APP_DATABASE_URL", "") or settings.DATABASE_URL


def worker_engine() -> AsyncEngine:
    return create_async_engine(runtime_database_url())
