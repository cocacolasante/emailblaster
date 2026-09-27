"""Ambient tenant + user context.

Two ContextVars carry "who is acting, in which workspace" for the
current request (set by ``app.database.get_db``) or worker unit of work
(set by ``app.tenancy.worker.run_in_tenant``).  Everything tenant-aware
reads from here: the ``TenantMixin`` insert default, the ORM scoping
listener, the RLS ``app.tenant_id`` setting, and the credential bundle.

Always set these via ``tenant_scope`` (or the helpers that wrap it) so
the previous value is restored on exit — a leaked value in a worker
loop would stamp the NEXT tenant's rows with the wrong id.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

current_tenant_id: ContextVar[uuid.UUID | None] = ContextVar(
    "current_tenant_id", default=None
)
current_user_id: ContextVar[uuid.UUID | None] = ContextVar(
    "current_user_id", default=None
)


def get_tenant_id() -> uuid.UUID | None:
    return current_tenant_id.get()


def require_tenant_id() -> uuid.UUID:
    tid = current_tenant_id.get()
    if tid is None:
        raise RuntimeError("No tenant in context — call inside tenant_scope()")
    return tid


def get_user_id() -> uuid.UUID | None:
    return current_user_id.get()


@contextmanager
def tenant_scope(
    tenant_id: uuid.UUID | None, user_id: uuid.UUID | None = None
) -> Iterator[None]:
    """Set tenant (+ optional user) for the duration of the block."""
    t1 = current_tenant_id.set(tenant_id)
    t2 = current_user_id.set(user_id)
    try:
        yield
    finally:
        current_user_id.reset(t2)
        current_tenant_id.reset(t1)
