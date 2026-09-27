"""Record ownership: defaults, validation, (bulk) assignment, reassignment.

Visibility is workspace-wide; ``owner_id`` is accountability — who works
the record, whose "My leads" it shows in, who gets its notifications.

Defaults (in precedence order):
1. an explicit ``owner_id=`` at construction (``None`` = unassigned);
2. the parent's owner, where code passes it (``inherit_owner``) — leads
   uploaded into a campaign get the campaign's owner, an opportunity
   converted from a lead gets the lead's owner, agent-created tasks get
   their deal's owner;
3. the acting user (``OwnedMixin`` init default, request context);
4. the workspace's primary owner (``before_flush`` fallback in
   ``app.tenancy.mixin``) for rows created by background jobs with no
   acting user.
"""
from __future__ import annotations

import uuid
from typing import Any, Iterable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import Membership, MembershipRole, User
from app.tenancy.context import require_tenant_id
from app.tenancy.mixin import OwnedMixin

MAX_BULK_ASSIGN = 5000


class NotAMember(ValueError):
    """The proposed owner isn't a member of the current workspace."""


def inherit_owner(parent: Any) -> dict[str, Any]:
    """``Model(**inherit_owner(parent), ...)`` — kwargs that give a new
    record its parent's owner (empty dict when the parent has none, so the
    normal defaults apply)."""
    owner = getattr(parent, "owner_id", None)
    return {"owner_id": owner} if owner is not None else {}


async def primary_owner_id(db: AsyncSession, tenant_id: uuid.UUID | None = None) -> uuid.UUID | None:
    tid = tenant_id or require_tenant_id()
    return await db.scalar(
        select(Membership.user_id)
        .where(Membership.tenant_id == tid)
        .order_by((Membership.role == MembershipRole.OWNER).desc(), Membership.created_at)
        .limit(1)
    )


async def is_member(db: AsyncSession, user_id: uuid.UUID, tenant_id: uuid.UUID | None = None) -> bool:
    tid = tenant_id or require_tenant_id()
    return (
        await db.scalar(
            select(Membership.id).where(Membership.tenant_id == tid, Membership.user_id == user_id)
        )
    ) is not None


async def validate_owner(db: AsyncSession, owner_id: uuid.UUID | None) -> None:
    if owner_id is not None and not await is_member(db, owner_id):
        raise NotAMember("owner must be a member of this workspace")


async def assign(
    db: AsyncSession,
    model: type,
    ids: Iterable[uuid.UUID],
    owner_id: uuid.UUID | None,
    *,
    actor_id: uuid.UUID | None = None,
    notify: bool = True,
) -> int:
    from app.tenancy.context import current_user_id

    actor_id = actor_id or current_user_id.get()
    """Set ``owner_id`` on the given records of ``model`` in the current
    workspace.  Ids from other workspaces are silently not matched.
    Notifies the new owner once per batch.  Caller commits."""
    if not issubclass(model, OwnedMixin):
        raise TypeError(f"{model.__name__} has no owner")
    ids = list(dict.fromkeys(ids))
    if len(ids) > MAX_BULK_ASSIGN:
        raise ValueError(f"at most {MAX_BULK_ASSIGN} records per request")
    await validate_owner(db, owner_id)
    if not ids:
        return 0
    result = await db.execute(
        update(model)
        .where(model.id.in_(ids), model.tenant_id == require_tenant_id())
        .values(owner_id=owner_id)
        .execution_options(synchronize_session="fetch")
    )
    count = result.rowcount or 0
    if notify and count and owner_id is not None and owner_id != actor_id:
        await _notify_assigned(db, model, ids, count, owner_id, actor_id)
    return count


async def _notify_assigned(db, model, ids, count, owner_id, actor_id) -> None:
    from app.models import NotificationKind
    from app.services import agent_core, notifications

    label = _LABELS.get(model.__tablename__, "records")
    actor = await db.get(User, actor_id) if actor_id else None
    who = actor.display_name if actor else "Someone"
    one = count == 1
    title = (
        f"{who} assigned you a {label[:-1]}" if one else f"{who} assigned you {count} {label}"
    )
    extra: dict[str, Any] = {}
    if one and model.__tablename__ == "leads":
        extra["lead_id"] = ids[0]
    elif one and model.__tablename__ == "crm_opportunities":
        extra["opportunity_id"] = ids[0]
    elif one and model.__tablename__ == "crm_activities":
        extra["activity_id"] = ids[0]
    settings_row = await agent_core.get_agent_settings(db)
    await notifications.notify(
        db, settings_row,
        kind=NotificationKind.ASSIGNED,
        title=title,
        dedup_key=f"assigned:{model.__tablename__}:{owner_id}:{uuid.uuid4()}",
        recipient_user_id=owner_id,
        **extra,
    )


_LABELS = {
    "leads": "leads",
    "campaigns": "campaigns",
    "crm_opportunities": "opportunities",
    "crm_activities": "tasks",
    "accounts": "accounts",
    "contacts": "contacts",
    "report_definitions": "reports",
}


async def set_owner(db: AsyncSession, obj: Any, owner_id: uuid.UUID | None) -> None:
    """Single-record owner change from a PATCH: validate, set, notify the
    new owner (unless they made the change themselves).  Caller commits."""
    from app.tenancy.context import current_user_id

    await validate_owner(db, owner_id)
    if obj.owner_id == owner_id:
        return
    obj.owner_id = owner_id
    actor = current_user_id.get()
    if owner_id is not None and owner_id != actor:
        await _notify_assigned(db, type(obj), [obj.id], 1, owner_id, actor)


def owned_models() -> list[type]:
    from app.database import Base

    return [m.class_ for m in Base.registry.mappers if issubclass(m.class_, OwnedMixin)]


async def reassign_all(db: AsyncSession, *, from_user: uuid.UUID, to_user: uuid.UUID) -> int:
    """Move every record owned by ``from_user`` in the current workspace to
    ``to_user`` (used when removing a member).  Caller commits."""
    await validate_owner(db, to_user)
    tid = require_tenant_id()
    total = 0
    for model in owned_models():
        result = await db.execute(
            update(model)
            .where(model.owner_id == from_user, model.tenant_id == tid)
            .values(owner_id=to_user)
            .execution_options(synchronize_session=False)
        )
        total += result.rowcount or 0
    return total


def owner_filter(model: type, owner: str | None):
    """SQL filter for a list endpoint's ``?owner=`` param:
    ``me`` | ``unassigned`` | ``<user uuid>``.  None/empty → no filter."""
    from app.tenancy.context import current_user_id

    if not owner:
        return None
    if owner == "me":
        return model.owner_id == current_user_id.get()
    if owner == "unassigned":
        return model.owner_id.is_(None)
    try:
        return model.owner_id == uuid.UUID(owner)
    except ValueError as exc:
        raise NotAMember("owner must be 'me', 'unassigned', or a member id") from exc
