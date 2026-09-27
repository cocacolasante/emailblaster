"""Postgres row-level security on every workspace-owned table.
Multi-tenancy P6.

Each tenanted table gets ``ENABLE ROW LEVEL SECURITY`` and one policy:

    tenant_isolation USING / WITH CHECK
        (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)

The app stamps ``app.tenant_id`` at the start of every transaction
(``app.tenancy.scoping``).  No setting → zero rows (fail closed), and
inserts/updates can't write another workspace's tenant_id.

RLS binds only non-owner roles: the runtime connects as the app role
(``APP_DATABASE_URL``, created by ``app.scripts.bootstrap_db``), while
Alembic and one-off scripts keep the owner role.  Plain ENABLE (not
FORCE) keeps owner-run migrations/backfills working.

Also creates ``app_tenant_of(kind, key)`` — the allowlisted SECURITY
DEFINER resolver background jobs and webhooks use to find which
workspace a record belongs to before a tenant context exists.

Identity tables (tenants, users, memberships, sessions, tokens,
invitations), ``webhook_events`` and ``linkedin_profile_cache`` are
deliberately not covered (read before a tenant is known / not tenant
data).  tests/test_rls_isolation.py asserts every table with a
``tenant_id`` column that should be covered is.
"""
from typing import Union

from alembic import op

revision: str = "0049"
down_revision: Union[str, None] = "0048"
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
    "tenant_provider_keys",
]

POLICY = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"

TENANT_OF_SQL = """
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


def upgrade() -> None:
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"CREATE POLICY tenant_isolation ON {t} USING ({POLICY}) WITH CHECK ({POLICY})")
    op.execute(TENANT_OF_SQL)
    # Callable by the runtime role (granted to PUBLIC; it only ever returns
    # a tenant id, never row data).
    op.execute("GRANT EXECUTE ON FUNCTION app_tenant_of(text, text) TO PUBLIC")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS app_tenant_of(text, text)")
    for t in TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
