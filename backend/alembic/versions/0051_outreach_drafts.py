"""Outreach drafts for the Muse research-a-lead flow (RLS like every
workspace table).  Draft → confirm (one-time code) → approved send."""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0051"
down_revision: Union[str, None] = "0050"
branch_labels = None
depends_on = None

POLICY = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
channel = postgresql.ENUM("email", "linkedin_dm", "linkedin_connect", name="outreach_channel", create_type=False)
status = postgresql.ENUM("draft", "ready", "sent", "failed", "discarded",
                         name="outreach_draft_status", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM("email", "linkedin_dm", "linkedin_connect", name="outreach_channel").create(bind, checkfirst=True)
    postgresql.ENUM("draft", "ready", "sent", "failed", "discarded",
                    name="outreach_draft_status").create(bind, checkfirst=True)
    uid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "outreach_drafts",
        sa.Column("id", uid, primary_key=True),
        sa.Column("tenant_id", uid, sa.ForeignKey("tenants.id", ondelete="CASCADE",
                  name="outreach_drafts_tenant_id_fkey"), nullable=False),
        sa.Column("created_by", uid, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("linkedin_url", sa.Text, nullable=False),
        sa.Column("channel", channel, nullable=False),
        sa.Column("status", status, nullable=False, server_default="draft"),
        sa.Column("goal", sa.Text, nullable=False),
        sa.Column("tone", sa.Text, nullable=False),
        sa.Column("research_mode", sa.Text, nullable=False),
        sa.Column("char_limit", sa.Integer, nullable=False),
        sa.Column("sender_name", sa.Text, nullable=False),
        sa.Column("research", postgresql.JSONB, nullable=False),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("to_email", sa.Text, nullable=True),
        sa.Column("to_name", sa.Text, nullable=True),
        sa.Column("sender_email", sa.Text, nullable=True),
        sa.Column("linkedin_account_id", uid, sa.ForeignKey("linkedin_accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("confirm_hash", sa.Text, nullable=True),
        sa.Column("confirmed_version", sa.Integer, nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_external_id", sa.Text, nullable=True),
        sa.Column("send_error", sa.Text, nullable=True),
        sa.Column("crm_lead_id", uid, sa.ForeignKey("leads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_outreach_drafts_tenant_id", "outreach_drafts", ["tenant_id"])
    op.execute("ALTER TABLE outreach_drafts ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON outreach_drafts USING ({POLICY}) WITH CHECK ({POLICY})")


def downgrade() -> None:
    op.drop_table("outreach_drafts")
    bind = op.get_bind()
    postgresql.ENUM(name="outreach_draft_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="outreach_channel").drop(bind, checkfirst=True)
