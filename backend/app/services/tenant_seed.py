"""Per-workspace defaults created when a workspace is registered.

Mirrors what migrations seeded app-wide in the single-tenant era.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession


async def seed_tenant_defaults(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Create the workspace's default CRM pipeline + agent settings.

    Populated once those tables are tenant-scoped (tenancy P2)."""
    return None
