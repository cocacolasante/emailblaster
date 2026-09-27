"""Record ownership: defaults, assignment, and reassignment."""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession


async def reassign_all(db: AsyncSession, *, from_user: uuid.UUID, to_user: uuid.UUID) -> int:
    """Move every record owned by ``from_user`` in the current workspace to
    ``to_user``.  Owner columns land in tenancy P3."""
    return 0
