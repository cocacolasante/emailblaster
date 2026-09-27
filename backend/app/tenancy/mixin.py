"""``TenantMixin`` — the ``tenant_id`` column every workspace-owned table
carries.

- FK to ``tenants.id`` (CASCADE: deleting a workspace deletes its data).
- Stamped with the ambient tenant (``current_tenant_id``) when the ORM
  object is CONSTRUCTED (``init`` event) — not at flush — so an object
  belongs to the workspace it was created in even if a later flush runs
  under another context.  The column default covers Core inserts
  (``pg_insert(Model).values(...)``) the same way.
- Mixing it in is also what opts a model into the ORM tenant filter
  (``app.tenancy.scoping``) and — via the RLS migration's table list —
  the Postgres row-level-security policy.
"""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, event
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.tenancy.context import current_tenant_id


def _ambient_tenant() -> uuid.UUID | None:
    return current_tenant_id.get()


class TenantMixin:
    @declared_attr
    def tenant_id(cls) -> Mapped[uuid.UUID | None]:  # noqa: N805
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
            default=_ambient_tenant,
        )


@event.listens_for(TenantMixin, "init", propagate=True)
def _stamp_on_construct(target, args, kwargs) -> None:
    if kwargs.get("tenant_id") is None:
        tid = current_tenant_id.get()
        if tid is not None:
            kwargs["tenant_id"] = tid


def tenanted_models():
    """Every mapped class that carries ``TenantMixin``."""
    from app.database import Base

    return [m.class_ for m in Base.registry.mappers if issubclass(m.class_, TenantMixin)]
