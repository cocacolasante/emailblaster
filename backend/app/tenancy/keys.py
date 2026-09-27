"""Redis key helpers for workspace-scoped counters and flags.

Keys already namespaced by a record UUID (``rate:{campaign_id}:*``,
``li-rate:{account_id}:*``, ``seq:emailsent:{lead}:{node}``) are unique per
workspace by construction.  Keys namespaced by something two workspaces
can share — a sending domain, a LinkedIn company page, a feed source —
must carry the tenant id, or one workspace's usage would throttle (or
stop) another's.
"""
from __future__ import annotations

from app.tenancy.context import require_tenant_id


def domain_rate_key(domain: str, window: str) -> str:
    return f"rate:domain:{require_tenant_id()}:{domain}:{window}"


def li_page_month_key(page_id: str) -> str:
    return f"li-rate:page:{require_tenant_id()}:{page_id}:month"


def funding_stop_key(source: str) -> str:
    return f"funding:stop:{require_tenant_id()}:{source}"


def brevo_watermark_key() -> str:
    return f"brevo:events:{require_tenant_id()}:last_polled_at"
