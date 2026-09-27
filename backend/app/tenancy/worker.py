"""Tenant context for Celery tasks and webhook handlers.

Background work runs outside any request, so it establishes the tenant
context itself — BEFORE opening DB sessions, so the ORM scoping filter
and the RLS ``app.tenant_id`` setting (stamped at transaction begin) both
see it:

- ``tenant_context(tid)``   bind tenant + the workspace's credential
                            bundle for a block of work.
- ``for_each_tenant(fn)``   beat sweeps: run ``fn()`` once per ACTIVE
                            workspace, each in its own context, isolating
                            failures so one workspace can't stall others.
- ``tenant_of(kind, key)``  resolve the workspace that owns a record
                            (lead id, unipile account id, Brevo message
                            id, …) through the allowlisted SECURITY
                            DEFINER function ``app_tenant_of`` — the only
                            tenant-blind lookup the runtime role can do.
- ``@in_record_tenant(kind, arg)`` wraps a record task's async body so it
                            runs in that record's workspace.  Task
                            signatures are unchanged, so messages already
                            in the broker survive a deploy.

Each unit of work must open its OWN session inside the context: a
transaction begun under one tenant keeps that tenant's RLS setting.
"""
from __future__ import annotations

import functools
import inspect
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.tenancy.context import tenant_scope
from app.tenancy.worker_db import worker_engine

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Kinds app_tenant_of() understands (mirrors the SQL function's CASE list).
TENANT_OF_KINDS = (
    "tenant", "lead", "campaign", "connected_account", "linkedin_account",
    "linkedin_account_unipile", "brevo_message", "social_search", "social_post",
    "signal_watch", "opportunity",
)

# SQL for the SECURITY DEFINER resolver.  Used by migration 0049 and by
# the test schema (metadata after_create) so both agree.
TENANT_OF_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION app_tenant_of(kind text, key text) RETURNS uuid
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $fn$
BEGIN
  IF kind = 'tenant' THEN
    RETURN (SELECT id FROM tenants WHERE id = key::uuid AND status = 'active');
  ELSIF kind = 'lead' THEN
    RETURN (SELECT tenant_id FROM leads WHERE id = key::uuid);
  ELSIF kind = 'campaign' THEN
    RETURN (SELECT tenant_id FROM campaigns WHERE id = key::uuid);
  ELSIF kind = 'opportunity' THEN
    RETURN (SELECT tenant_id FROM crm_opportunities WHERE id = key::uuid);
  ELSIF kind = 'connected_account' THEN
    RETURN (SELECT tenant_id FROM connected_accounts WHERE id = key::uuid);
  ELSIF kind = 'linkedin_account' THEN
    RETURN (SELECT tenant_id FROM linkedin_accounts WHERE id = key::uuid);
  ELSIF kind = 'linkedin_account_unipile' THEN
    RETURN (SELECT tenant_id FROM linkedin_accounts WHERE unipile_account_id = key LIMIT 1);
  ELSIF kind = 'brevo_message' THEN
    RETURN COALESCE(
      (SELECT tenant_id FROM leads WHERE brevo_message_id = key LIMIT 1),
      (SELECT tenant_id FROM lead_step_executions WHERE external_id = key LIMIT 1)
    );
  ELSIF kind = 'social_search' THEN
    RETURN (SELECT tenant_id FROM social_listening_searches WHERE id = key::uuid);
  ELSIF kind = 'social_post' THEN
    RETURN (SELECT tenant_id FROM social_listening_posts WHERE id = key::uuid);
  ELSIF kind = 'signal_watch' THEN
    RETURN (SELECT tenant_id FROM signal_watches WHERE id = key::uuid);
  END IF;
  RAISE EXCEPTION 'app_tenant_of: unknown kind %', kind;
END
$fn$;
"""


def install_tenant_of_ddl(metadata) -> None:
    """Create ``app_tenant_of`` after ``metadata.create_all`` (test DB)."""
    from sqlalchemy import DDL, event

    # DDL() treats "%" as a format placeholder — escape the RAISE's %.
    event.listen(metadata, "after_create", DDL(TENANT_OF_FUNCTION_SQL.replace("%", "%%")))


async def tenant_of(kind: str, key: Any) -> uuid.UUID | None:
    """The workspace owning a record, or None when it doesn't exist."""
    if kind not in TENANT_OF_KINDS:
        raise ValueError(f"unknown tenant_of kind {kind!r}")
    if key is None or key == "":
        return None
    engine = worker_engine()
    try:
        async with engine.connect() as conn:
            return (
                await conn.execute(
                    text("SELECT app_tenant_of(:kind, :key)"), {"kind": kind, "key": str(key)},
                )
            ).scalar()
    except Exception as exc:  # noqa: BLE001 — malformed key (bad uuid) etc.
        logger.debug("tenant_of(%s, %r) failed: %s", kind, key, exc)
        return None
    finally:
        await engine.dispose()


@asynccontextmanager
async def tenant_context(
    tenant_id: uuid.UUID, *, user_id: uuid.UUID | None = None,
) -> AsyncIterator[None]:
    """Bind tenant (+ optional acting user) and its credential bundle."""
    from app.services import credentials

    with tenant_scope(tenant_id, user_id):
        engine = worker_engine()
        try:
            bundle = await credentials.load_bundle(
                async_sessionmaker(engine, expire_on_commit=False), tenant_id,
            )
        finally:
            await engine.dispose()
        with credentials.use_bundle(bundle):
            yield


async def active_tenant_ids() -> list[uuid.UUID]:
    engine = worker_engine()
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT id FROM tenants WHERE status = 'active' ORDER BY created_at")
            )
            return [r[0] for r in rows]
    finally:
        await engine.dispose()


async def for_each_tenant(
    fn: Callable[[], Awaitable[T]], *, label: str | None = None,
) -> dict[str, Any]:
    """Run ``fn()`` once per active workspace in that workspace's context.

    Returns ``{"tenants": n, "results": {tenant_id: result}, "errors": k}``.
    One workspace raising is logged and counted, never propagated — a bad
    key or a data bug in workspace A must not stop workspace B's sends.
    """
    name = label or getattr(fn, "__name__", "task")
    results: dict[str, Any] = {}
    errors = 0
    for tid in await active_tenant_ids():
        try:
            async with tenant_context(tid):
                results[str(tid)] = await fn()
        except Exception:  # noqa: BLE001 — isolate workspaces
            errors += 1
            logger.exception("%s failed for tenant %s", name, tid)
            results[str(tid)] = {"error": True}
    return {"tenants": len(results), "results": results, "errors": errors}


def merge_counts(summary: dict[str, Any]) -> dict[str, Any]:
    """Sum the numeric fields of each tenant's result dict (keeps the
    old single-tenant return shape for callers/tests that read counts)."""
    merged: dict[str, Any] = {}
    for res in summary.get("results", {}).values():
        if not isinstance(res, dict):
            continue
        for k, v in res.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            merged[k] = merged.get(k, 0) + v
    merged["tenants"] = summary.get("tenants", 0)
    if summary.get("errors"):
        merged["tenant_errors"] = summary["errors"]
    return merged


def in_record_tenant(kind: str, arg: str | int = 0):
    """Decorate a record task's async body: resolve the record's workspace
    and run the body inside it.  An unknown record runs the body without a
    tenant (it finds nothing and returns its own not-found result)."""

    def deco(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            bound = sig.bind_partial(*args, **kwargs)
            key = bound.arguments.get(arg) if isinstance(arg, str) else (
                args[arg] if len(args) > arg else None
            )
            tid = await tenant_of(kind, key)
            if tid is None:
                return await fn(*args, **kwargs)
            async with tenant_context(tid):
                return await fn(*args, **kwargs)

        return wrapper

    return deco


async def run_per_tenant(fn: Callable[[], Awaitable[Any]]) -> dict[str, Any]:
    """Beat entry point: ``for_each_tenant(fn)`` with the per-tenant count
    dicts summed (keeps the familiar single-tenant result shape in logs)."""
    return merge_counts(await for_each_tenant(fn))
