"""Automatic tenant scoping for every ORM query + the RLS session setting.

Two Session-level listeners, registered once at import (``app.models``
imports this module):

1. ``do_orm_execute`` — while a tenant is in context, every ORM SELECT /
   UPDATE / DELETE gets ``with_loader_criteria(TenantMixin, tenant_id ==
   <current>)``.  That covers the base entity, joined entities, aliases
   and relationship loads, so the 18 routers and the workers need no
   hand-written tenant filters.  Opt out (rare, audited) with
   ``.execution_options(skip_tenant_filter=True)`` — a hardening test
   allowlists the call sites.

2. ``after_begin`` — stamps the transaction-local Postgres setting
   ``app.tenant_id`` that the row-level-security policies check.  This is
   the database-level safety net for anything the ORM filter can't see
   (Core inserts, raw SQL, a forgotten filter): under the non-owner
   runtime role a query without the setting returns zero rows.

Outside a tenant context (auth lookups, the tenant-iterating beat loop)
neither listener does anything; identity tables aren't tenant-scoped.
"""
from __future__ import annotations

from sqlalchemy import event, text
from sqlalchemy.orm import Session, with_loader_criteria

from app.tenancy.context import current_tenant_id
from app.tenancy.mixin import TenantMixin


@event.listens_for(Session, "do_orm_execute")
def _scope_to_tenant(state) -> None:
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.is_column_load:
        return  # refreshing attributes of an already-scoped instance
    if state.execution_options.get("skip_tenant_filter", False):
        return
    tid = current_tenant_id.get()
    if tid is None:
        return
    state.statement = state.statement.options(
        with_loader_criteria(
            TenantMixin,
            lambda cls: cls.tenant_id == tid,
            include_aliases=True,
        )
    )


@event.listens_for(Session, "after_begin")
def _set_rls_tenant(session, transaction, connection) -> None:
    tid = current_tenant_id.get()
    if tid is None:
        return
    connection.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tid)}
    )
