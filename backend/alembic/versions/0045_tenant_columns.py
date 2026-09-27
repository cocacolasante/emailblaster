"""Tenant columns on every workspace-owned table + per-tenant uniqueness.
Multi-tenancy P2.

- Adds ``tenant_id`` (FK tenants ON DELETE CASCADE, indexed) to the 28
  tables that lacked it; adds the FK to the 13 that had the bare column.
- Backfills every existing row into the bootstrap workspace (0044).
- Re-keys global uniqueness to per-workspace: suppression, research
  cache (email PK → surrogate id), dedup keys, social posts, the default
  sender, agent_settings (int singleton → one row per tenant),
  funding_source_state (source PK → one row per tenant+source), and the
  webhook dedup table.

Column stays NULLABLE here; 0047 flips it NOT NULL once every writer
stamps a tenant.  Table lists are inlined (migrations never import app
code).
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045"
down_revision: Union[str, None] = "0044"
branch_labels = None
depends_on = None

BOOTSTRAP_TENANT_ID = "00000000-0000-0000-0000-000000000001"

EXISTING = [
    "leads", "crm_opportunities", "crm_activities", "pipelines", "opportunity_stages",
    "accounts", "contacts", "opportunity_stage_changes", "report_definitions",
    "orgs", "signals", "org_intent_scores", "icp_intent_profiles",
]
NEW = [
    "campaigns", "connected_accounts", "linkedin_accounts", "icp_profiles",
    "lookalike_candidates", "signal_watches", "prospect_signals",
    "social_listening_searches", "notifications", "agent_actions", "agent_settings",
    "suppression_list", "research_cache", "funding_source_state", "funding_enrichment_queue",
    "email_events", "style_corrections", "reply_outcomes", "campaign_copy_insights",
    "sequences", "sequence_nodes", "sequence_edges", "lead_sequence_states",
    "lead_step_executions", "crm_documents", "crm_opportunity_products",
    "social_listening_posts", "social_listening_opportunities",
]
ALL = EXISTING + NEW

DEDUP = [  # (table, column, new unique constraint name)
    ("notifications", "dedup_key", "uq_notifications_tenant_dedup"),
    ("prospect_signals", "dedup_key", "uq_prospect_signals_tenant_dedup"),
    ("lookalike_candidates", "dedup_key", "uq_lookalike_candidates_tenant_dedup"),
    ("funding_enrichment_queue", "dedup_key", "uq_funding_enrichment_queue_tenant_dedup"),
]


def _fk(table: str) -> None:
    op.create_foreign_key(
        f"{table}_tenant_id_fkey", table, "tenants", ["tenant_id"], ["id"], ondelete="CASCADE",
    )


def upgrade() -> None:
    bind = op.get_bind()

    # --- Columns + FKs + indexes -------------------------------------------
    for t in NEW:
        op.add_column(t, sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_index(f"ix_{t}_tenant_id", t, ["tenant_id"])
    op.execute("ALTER INDEX IF EXISTS ix_opp_stage_changes_tenant_id "
               "RENAME TO ix_opportunity_stage_changes_tenant_id")
    for t in ALL:
        _fk(t)

    # --- Backfill into the bootstrap workspace ------------------------------
    has_bootstrap = bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM tenants WHERE id = :t)"), {"t": BOOTSTRAP_TENANT_ID},
    ).scalar()
    if has_bootstrap:
        for t in ALL:
            bind.execute(sa.text(f"UPDATE {t} SET tenant_id = :t WHERE tenant_id IS NULL"),
                         {"t": BOOTSTRAP_TENANT_ID})

    # --- Per-tenant uniqueness ----------------------------------------------
    op.drop_constraint("uq_suppression_list_email", "suppression_list", type_="unique")
    op.create_unique_constraint("uq_suppression_list_tenant_email", "suppression_list",
                                ["tenant_id", "email"])

    for table, col, name in DEDUP:
        op.drop_index(f"ix_{table}_{col}", table_name=table)
        op.create_index(f"ix_{table}_{col}", table, [col])
        op.create_unique_constraint(name, table, ["tenant_id", col])

    op.drop_constraint("uq_signals_dedupe_key", "signals", type_="unique")
    op.execute("DROP INDEX IF EXISTS ix_signals_dedupe_key")
    op.create_index("ix_signals_dedupe_key", "signals", ["dedupe_key"])
    op.create_unique_constraint("uq_signals_tenant_dedupe", "signals", ["tenant_id", "dedupe_key"])

    op.drop_constraint("uq_social_posts_provider_url", "social_listening_posts", type_="unique")
    op.create_unique_constraint("uq_social_posts_provider_url", "social_listening_posts",
                                ["tenant_id", "provider", "post_url"])

    op.drop_index("ix_connected_accounts_single_default_sender", table_name="connected_accounts")
    op.create_index(
        "ix_connected_accounts_single_default_sender", "connected_accounts", ["tenant_id"],
        unique=True, postgresql_where=sa.text("is_default_sender IS TRUE"),
    )

    # research_cache: email PK → surrogate id + (tenant_id, email) unique.
    op.add_column("research_cache", sa.Column(
        "id", postgresql.UUID(as_uuid=True), nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    ))
    op.drop_constraint("research_cache_pkey", "research_cache", type_="primary")
    op.create_primary_key("research_cache_pkey", "research_cache", ["id"])
    op.create_unique_constraint("uq_research_cache_tenant_email", "research_cache",
                                ["tenant_id", "email"])

    # funding_source_state: source PK → surrogate id + (tenant_id, source).
    op.add_column("funding_source_state", sa.Column(
        "id", postgresql.UUID(as_uuid=True), nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    ))
    op.drop_constraint("funding_source_state_pkey", "funding_source_state", type_="primary")
    op.create_primary_key("funding_source_state_pkey", "funding_source_state", ["id"])
    op.create_unique_constraint("uq_funding_source_state_tenant_source", "funding_source_state",
                                ["tenant_id", "source"])

    # agent_settings: int singleton id → uuid, one row per tenant.
    op.add_column("agent_settings", sa.Column(
        "uid", postgresql.UUID(as_uuid=True), nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    ))
    op.drop_constraint("agent_settings_pkey", "agent_settings", type_="primary")
    op.drop_column("agent_settings", "id")
    op.alter_column("agent_settings", "uid", new_column_name="id")
    op.create_primary_key("agent_settings_pkey", "agent_settings", ["id"])
    op.create_unique_constraint("uq_agent_settings_tenant", "agent_settings", ["tenant_id"])

    # webhook_events: tenant tag (no RLS) + per-tenant dedup.
    op.add_column("webhook_events", sa.Column(
        "tenant_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE", name="webhook_events_tenant_id_fkey"),
        nullable=True,
    ))
    op.drop_constraint("uq_webhook_events_provider_event", "webhook_events", type_="unique")
    op.execute(
        "ALTER TABLE webhook_events ADD CONSTRAINT uq_webhook_events_provider_event "
        "UNIQUE NULLS NOT DISTINCT (provider, tenant_id, event_id)"
    )

    # --- Hot-path composite indexes -----------------------------------------
    op.create_index("ix_campaigns_tenant_status", "campaigns", ["tenant_id", "status"])
    op.create_index("ix_leads_tenant_campaign", "leads", ["tenant_id", "campaign_id"])
    op.create_index("ix_lead_sequence_states_tenant_status_next", "lead_sequence_states",
                    ["tenant_id", "status", "next_run_at"])


def downgrade() -> None:
    op.drop_index("ix_lead_sequence_states_tenant_status_next", table_name="lead_sequence_states")
    op.drop_index("ix_leads_tenant_campaign", table_name="leads")
    op.drop_index("ix_campaigns_tenant_status", table_name="campaigns")

    op.drop_constraint("uq_webhook_events_provider_event", "webhook_events", type_="unique")
    op.drop_column("webhook_events", "tenant_id")
    op.create_unique_constraint("uq_webhook_events_provider_event", "webhook_events",
                                ["provider", "event_id"])

    # agent_settings back to the int singleton (keeps the bootstrap row).
    op.drop_constraint("uq_agent_settings_tenant", "agent_settings", type_="unique")
    op.drop_constraint("agent_settings_pkey", "agent_settings", type_="primary")
    op.execute("DELETE FROM agent_settings WHERE ctid NOT IN "
               "(SELECT min(ctid) FROM agent_settings)")
    op.drop_column("agent_settings", "id")
    op.add_column("agent_settings", sa.Column("id", sa.Integer, nullable=False, server_default="1"))
    op.create_primary_key("agent_settings_pkey", "agent_settings", ["id"])

    op.drop_constraint("uq_funding_source_state_tenant_source", "funding_source_state", type_="unique")
    op.drop_constraint("funding_source_state_pkey", "funding_source_state", type_="primary")
    op.drop_column("funding_source_state", "id")
    op.create_primary_key("funding_source_state_pkey", "funding_source_state", ["source"])

    op.drop_constraint("uq_research_cache_tenant_email", "research_cache", type_="unique")
    op.drop_constraint("research_cache_pkey", "research_cache", type_="primary")
    op.drop_column("research_cache", "id")
    op.create_primary_key("research_cache_pkey", "research_cache", ["email"])

    op.drop_index("ix_connected_accounts_single_default_sender", table_name="connected_accounts")
    op.create_index(
        "ix_connected_accounts_single_default_sender", "connected_accounts", ["is_default_sender"],
        unique=True, postgresql_where=sa.text("is_default_sender IS TRUE"),
    )

    op.drop_constraint("uq_social_posts_provider_url", "social_listening_posts", type_="unique")
    op.create_unique_constraint("uq_social_posts_provider_url", "social_listening_posts",
                                ["provider", "post_url"])

    op.drop_constraint("uq_signals_tenant_dedupe", "signals", type_="unique")
    op.drop_index("ix_signals_dedupe_key", table_name="signals")
    op.create_index("ix_signals_dedupe_key", "signals", ["dedupe_key"], unique=True)
    op.create_unique_constraint("uq_signals_dedupe_key", "signals", ["dedupe_key"])

    for table, col, name in DEDUP:
        op.drop_constraint(name, table, type_="unique")
        op.drop_index(f"ix_{table}_{col}", table_name=table)
        op.create_index(f"ix_{table}_{col}", table, [col], unique=True)

    op.drop_constraint("uq_suppression_list_tenant_email", "suppression_list", type_="unique")
    op.create_unique_constraint("uq_suppression_list_email", "suppression_list", ["email"])

    for t in ALL:
        op.drop_constraint(f"{t}_tenant_id_fkey", t, type_="foreignkey")
    op.execute("ALTER INDEX IF EXISTS ix_opportunity_stage_changes_tenant_id "
               "RENAME TO ix_opp_stage_changes_tenant_id")
    for t in NEW:
        op.drop_index(f"ix_{t}_tenant_id", table_name=t)
        op.drop_column(t, "tenant_id")
