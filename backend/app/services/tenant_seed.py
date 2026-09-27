"""Per-workspace defaults created when a workspace is registered.

Mirrors what migrations seeded app-wide in the single-tenant era: the
"Default" CRM pipeline with the six stages matching the legacy
``OpportunityStage`` enum (migration 0038), and the agent settings row.
Idempotent — safe to call on an existing workspace.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentSettings, Pipeline, PipelineStage

# (key, name, sort, probability, is_won, is_lost)
DEFAULT_STAGES = [
    ("prospecting", "Prospecting", 0, 10, False, False),
    ("qualification", "Qualification", 1, 25, False, False),
    ("proposal", "Proposal", 2, 50, False, False),
    ("negotiation", "Negotiation", 3, 75, False, False),
    ("closed_won", "Closed Won", 4, 100, True, False),
    ("closed_lost", "Closed Lost", 5, 0, False, True),
]


async def seed_tenant_defaults(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Create the workspace's default pipeline + agent settings (caller commits).

    Runs during signup, before any tenant context exists, so it stamps the
    transaction's RLS setting itself (the row-level-security WITH CHECK
    would otherwise reject these inserts under the runtime role)."""
    await db.execute(
        text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)}
    )
    has_pipeline = await db.scalar(
        select(Pipeline.id).where(Pipeline.tenant_id == tenant_id, Pipeline.is_default.is_(True))
        .execution_options(skip_tenant_filter=True)
    )
    if has_pipeline is None:
        pipeline = Pipeline(name="Default", is_default=True, tenant_id=tenant_id)
        db.add(pipeline)
        await db.flush()
        for key, name, order, prob, won, lost in DEFAULT_STAGES:
            db.add(PipelineStage(
                tenant_id=tenant_id, pipeline_id=pipeline.id, key=key, name=name,
                sort_order=order, default_probability=prob, is_won=won, is_lost=lost,
            ))
    has_settings = await db.scalar(
        select(AgentSettings.id).where(AgentSettings.tenant_id == tenant_id)
        .execution_options(skip_tenant_filter=True)
    )
    if has_settings is None:
        db.add(AgentSettings(tenant_id=tenant_id))
    await db.flush()
