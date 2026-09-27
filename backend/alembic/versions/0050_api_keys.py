"""Workspace API keys for agents (Muse over MCP).

Hashed bearer keys, RLS like every workspace table, plus the SECURITY
DEFINER ``api_key_verify(prefix, hash)`` — the key identifies the
workspace, so verification can't run inside the tenant boundary.  It
matches ONE live key (creator still a member, workspace active), stamps
``last_used_at``, and returns only ids.
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0050"
down_revision: Union[str, None] = "0049"
branch_labels = None
depends_on = None

POLICY = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"

VERIFY_SQL = """
CREATE OR REPLACE FUNCTION api_key_verify(p_prefix text, p_hash text)
RETURNS TABLE (key_id uuid, tenant_id uuid, user_id uuid)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $fn$
  UPDATE api_keys k
     SET last_used_at = now()
    FROM memberships m, tenants t
   WHERE k.prefix = p_prefix
     AND k.token_hash = p_hash
     AND k.revoked_at IS NULL
     AND m.tenant_id = k.tenant_id AND m.user_id = k.created_by
     AND t.id = k.tenant_id AND t.status = 'active'
  RETURNING k.id, k.tenant_id, k.created_by;
$fn$;
"""


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE", name="api_keys_tenant_id_fkey"),
                  nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("prefix", sa.Text, nullable=False, unique=True),
        sa.Column("token_hash", sa.Text, nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE", name="api_keys_created_by_fkey"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_tenant_id", "api_keys", ["tenant_id"])
    op.execute("ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON api_keys USING ({POLICY}) WITH CHECK ({POLICY})")
    op.execute(VERIFY_SQL)
    op.execute("GRANT EXECUTE ON FUNCTION api_key_verify(text, text) TO PUBLIC")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS api_key_verify(text, text)")
    op.drop_index("ix_api_keys_tenant_id", table_name="api_keys")
    op.drop_table("api_keys")
