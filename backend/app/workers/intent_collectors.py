"""Celery tasks for the Signals & Intent Engine v2 collectors.

``intent.backfill_orgs``  — seed/refresh the monitored-org set from existing
                            EIN-bearing data (daily; cheap + idempotent).
``intent.collect_propublica_rev_delta`` — ProPublica 990 grant-revenue-delta
                            collector → Tier-2 ``rev_drop`` signals (weekly;
                            990 data moves slowly).

``acks_late=False`` so a long run is never redelivered into a duplicate
(collectors are idempotent via the unique ``dedupe_key`` regardless, and a
run lost to a crash simply re-runs next tick).  Grants.gov + USASpending
collectors land in this module in the next sub-phase, after the gate.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.services.intent import collect_propublica, orgs
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _backfill_async() -> dict[str, int]:
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await orgs.backfill_orgs_from_existing(session)
    finally:
        await engine.dispose()


@celery_app.task(name="intent.backfill_orgs", acks_late=False)
def backfill_orgs() -> dict[str, int]:
    return asyncio.run(_backfill_async())


async def _propublica_async() -> dict[str, int]:
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await collect_propublica.collect_propublica_rev_delta(session)
    finally:
        await engine.dispose()


@celery_app.task(name="intent.collect_propublica_rev_delta", acks_late=False)
def collect_propublica_rev_delta() -> dict[str, int]:
    return asyncio.run(_propublica_async())
