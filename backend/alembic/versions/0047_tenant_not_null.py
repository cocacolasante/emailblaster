"""tenant_id NOT NULL on every workspace-owned table.  Multi-tenancy P4.

Gated: refuses to run while any row still has a NULL tenant_id (a writer
somewhere isn't stamping a tenant) and names the offending tables.
Must precede 0049 (RLS), which would otherwise hide NULL-tenant rows
from everyone.
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: Union[str, None] = "0046"
branch_labels = None
depends_on = None

TABLES = [
    "leads", "crm_opportunities", "crm_activities", "pipelines", "opportunity_stages",
    "accounts", "contacts", "opportunity_stage_changes", "report_definitions",
    "orgs", "signals", "org_intent_scores", "icp_intent_profiles",
    "campaigns", "connected_accounts", "linkedin_accounts", "icp_profiles",
    "lookalike_candidates", "signal_watches", "prospect_signals",
    "social_listening_searches", "notifications", "agent_actions", "agent_settings",
    "suppression_list", "research_cache", "funding_source_state", "funding_enrichment_queue",
    "email_events", "style_corrections", "reply_outcomes", "campaign_copy_insights",
    "sequences", "sequence_nodes", "sequence_edges", "lead_sequence_states",
    "lead_step_executions", "crm_documents", "crm_opportunity_products",
    "social_listening_posts", "social_listening_opportunities",
]


def upgrade() -> None:
    bind = op.get_bind()
    offenders = []
    for t in TABLES:
        n = bind.execute(sa.text(f"SELECT count(*) FROM {t} WHERE tenant_id IS NULL")).scalar()
        if n:
            offenders.append(f"{t} ({n})")
    if offenders:
        raise RuntimeError(
            "Refusing to set tenant_id NOT NULL — rows without a workspace: "
            + ", ".join(offenders)
            + ".  Backfill them (0045 assigns the bootstrap workspace) and re-run."
        )
    for t in TABLES:
        op.alter_column(t, "tenant_id", nullable=False)


def downgrade() -> None:
    for t in TABLES:
        op.alter_column(t, "tenant_id", nullable=True)
