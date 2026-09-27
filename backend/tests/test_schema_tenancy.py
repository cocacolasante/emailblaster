"""Schema-level tenancy invariants over ``Base.metadata``."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import app.models  # noqa: F401
from app.database import Base
from app.models import AgentSettings, Pipeline, PipelineStage, Suppression, SuppressionReason
from app.services import tenant_seed
from app.tenancy.context import tenant_scope
from app.tenancy.mixin import TenantMixin, tenanted_models

# Tables that are deliberately NOT workspace-scoped.
GLOBAL_TABLES = {
    # identity (read before a tenant is known)
    "tenants", "users", "memberships", "user_sessions", "auth_tokens", "invitations",
    # public LinkedIn slug → URN map; no customer data
    "linkedin_profile_cache",
    # webhook dedup: tagged with tenant_id, but inserted before tenant resolution
    "webhook_events",
}


def test_every_table_is_tenanted_or_explicitly_global():
    tenanted = {m.__tablename__ for m in tenanted_models()}
    for name, table in Base.metadata.tables.items():
        if name in GLOBAL_TABLES:
            continue
        assert name in tenanted, (
            f"table {name!r} has no TenantMixin — add it (and to the RLS migration), "
            "or list it in GLOBAL_TABLES with a reason"
        )
        assert "tenant_id" in table.c


def test_tenant_fk_cascades():
    for model in tenanted_models():
        fks = list(model.__table__.c.tenant_id.foreign_keys)
        assert fks and fks[0].column.table.name == "tenants"
        assert fks[0].ondelete == "CASCADE"


async def test_suppression_unique_per_tenant(db_session, _engine):
    from tests.conftest import create_test_user

    _, other_tid, _ = await create_test_user(_engine)
    db_session.add(Suppression(email="dup@x.com", reason=SuppressionReason.MANUAL))
    with tenant_scope(other_tid):
        db_session.add(Suppression(email="dup@x.com", reason=SuppressionReason.MANUAL))
        await db_session.commit()  # same email, different workspace: fine
    db_session.add(Suppression(email="dup@x.com", reason=SuppressionReason.SPAM))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_seed_tenant_defaults_is_idempotent(db_session, _engine):
    from tests.conftest import create_test_user

    _, tid, _ = await create_test_user(_engine)
    await tenant_seed.seed_tenant_defaults(db_session, tid)
    await tenant_seed.seed_tenant_defaults(db_session, tid)
    await db_session.commit()
    with tenant_scope(tid):
        pipelines = (await db_session.execute(select(Pipeline))).scalars().all()
        stages = (await db_session.execute(select(PipelineStage))).scalars().all()
        settings_rows = (await db_session.execute(select(AgentSettings))).scalars().all()
    assert len(pipelines) == 1 and pipelines[0].is_default
    assert [s.key for s in sorted(stages, key=lambda s: s.sort_order)][-2:] == ["closed_won", "closed_lost"]
    assert len(settings_rows) == 1
