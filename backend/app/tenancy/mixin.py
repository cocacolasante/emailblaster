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
from sqlalchemy.orm import Mapped, Session, declared_attr, mapped_column

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


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

OWNED_TABLES = (
    "leads", "campaigns", "crm_opportunities", "crm_activities",
    "accounts", "contacts", "report_definitions",
)


class OwnedMixin:
    """``owner_id`` — the workspace member accountable for the record.

    Visibility is workspace-wide; the owner is for accountability,
    assignment and routing (notifications, "My leads").  Enforced by a
    composite FK ``(tenant_id, owner_id) → memberships(tenant_id,
    user_id) ON DELETE SET NULL (owner_id)``: an owner is always a member
    of the record's workspace, and removing a member unassigns their
    records.  SQLAlchemy can't express the column-list SET NULL, so the FK
    is added by raw DDL (migration 0046 / ``owner_fk_ddl`` for create_all).
    """

    @declared_attr
    def owner_id(cls) -> Mapped[uuid.UUID | None]:  # noqa: N805
        return mapped_column(UUID(as_uuid=True), nullable=True, index=True)


@event.listens_for(OwnedMixin, "init", propagate=True)
def _default_owner(target, args, kwargs) -> None:
    # Omitted ⇒ the acting user owns it.  An explicit owner_id=None means
    # "create unassigned" and is left alone.
    if "owner_id" in kwargs:
        target._owner_explicit = True
    else:
        from app.tenancy.context import current_user_id

        uid = current_user_id.get()
        if uid is not None:
            kwargs["owner_id"] = uid


@event.listens_for(Session, "before_flush")
def _primary_owner_for_background_rows(session, flush_context, instances) -> None:
    """Rows created with no acting user (background jobs) and no explicit
    owner fall back to the workspace's primary owner, so they still show up
    in someone's "My …" views and route their notifications."""
    pending = [
        o for o in session.new
        if isinstance(o, OwnedMixin) and o.owner_id is None
        and not getattr(o, "_owner_explicit", False)
        and getattr(o, "tenant_id", None) is not None
    ]
    if not pending:
        return
    from sqlalchemy import select

    from app.models.identity import Membership, MembershipRole

    cache: dict = {}
    with session.no_autoflush:
        for obj in pending:
            tid = obj.tenant_id
            if tid not in cache:
                cache[tid] = session.execute(
                    select(Membership.user_id)
                    .where(Membership.tenant_id == tid)
                    .order_by((Membership.role == MembershipRole.OWNER).desc(), Membership.created_at)
                    .limit(1)
                ).scalar()
            obj.owner_id = cache[tid]


def owner_fk_ddl(table: str) -> str:
    return (
        f"ALTER TABLE {table} ADD CONSTRAINT {table}_owner_fkey "
        "FOREIGN KEY (tenant_id, owner_id) REFERENCES memberships (tenant_id, user_id) "
        "ON DELETE SET NULL (owner_id)"
    )


def install_owner_fk_ddl(metadata) -> None:
    """Add the composite owner FKs after ``metadata.create_all`` (test DB)."""
    from sqlalchemy import DDL

    for table in OWNED_TABLES:
        event.listen(metadata, "after_create", DDL(owner_fk_ddl(table)))
