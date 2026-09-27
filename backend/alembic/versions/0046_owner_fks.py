"""Record owners.  Multi-tenancy P3.

- ``owner_id`` on leads + campaigns (the CRM tables already had it).
- Clears owner ids that don't point at a member of the row's workspace
  (pre-tenancy junk), then assigns every unowned record to its
  workspace's first owner.
- Composite FK ``(tenant_id, owner_id) → memberships(tenant_id, user_id)
  ON DELETE SET NULL (owner_id)`` (PG15 column-list SET NULL): owners are
  always members of the record's workspace; removing a member unassigns.
- ``opportunity_stage_changes.changed_by`` → users (SET NULL).
- ``notifications.user_id`` recipient (NULL = whole workspace) and the
  ``assigned`` notification kind.
- ``agent_settings.notify_from_email/name`` (seeded from the retired
  ``OWNER_NOTIFY_FROM_*`` env vars).
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046"
down_revision: Union[str, None] = "0045"
branch_labels = None
depends_on = None

OWNED = ["leads", "campaigns", "crm_opportunities", "crm_activities",
         "accounts", "contacts", "report_definitions"]


def upgrade() -> None:
    for t in ("leads", "campaigns"):
        op.add_column(t, sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_index(f"ix_{t}_owner_id", t, ["owner_id"])

    for t in OWNED:
        op.execute(
            f"UPDATE {t} r SET owner_id = NULL WHERE owner_id IS NOT NULL AND NOT EXISTS ("
            " SELECT 1 FROM memberships m WHERE m.tenant_id = r.tenant_id AND m.user_id = r.owner_id)"
        )
        op.execute(
            f"UPDATE {t} r SET owner_id = ("
            " SELECT m.user_id FROM memberships m WHERE m.tenant_id = r.tenant_id"
            " ORDER BY (m.role = 'owner') DESC, m.created_at LIMIT 1)"
            " WHERE r.owner_id IS NULL AND r.tenant_id IS NOT NULL"
        )
        op.execute(
            f"ALTER TABLE {t} ADD CONSTRAINT {t}_owner_fkey "
            "FOREIGN KEY (tenant_id, owner_id) REFERENCES memberships (tenant_id, user_id) "
            "ON DELETE SET NULL (owner_id)"
        )

    op.execute(
        "UPDATE opportunity_stage_changes c SET changed_by = NULL WHERE changed_by IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = c.changed_by)"
    )
    op.create_foreign_key(
        "opportunity_stage_changes_changed_by_fkey", "opportunity_stage_changes", "users",
        ["changed_by"], ["id"], ondelete="SET NULL",
    )

    op.add_column("notifications", sa.Column(
        "user_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE", name="notifications_user_id_fkey"),
        nullable=True,
    ))
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.execute("ALTER TYPE notification_kind ADD VALUE IF NOT EXISTS 'assigned'")

    # Alert sender override per workspace (replaces OWNER_NOTIFY_FROM_* env).
    op.add_column("agent_settings", sa.Column("notify_from_email", sa.Text, nullable=True))
    op.add_column("agent_settings", sa.Column("notify_from_name", sa.Text, nullable=True))
    import os

    from_email = os.environ.get("OWNER_NOTIFY_FROM_EMAIL") or None
    from_name = os.environ.get("OWNER_NOTIFY_FROM_NAME") or None
    if from_email or from_name:
        op.get_bind().execute(
            sa.text("UPDATE agent_settings SET notify_from_email = :e, notify_from_name = :n"),
            {"e": from_email, "n": from_name},
        )


def downgrade() -> None:
    op.drop_column("agent_settings", "notify_from_name")
    op.drop_column("agent_settings", "notify_from_email")
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_column("notifications", "user_id")
    op.drop_constraint("opportunity_stage_changes_changed_by_fkey", "opportunity_stage_changes",
                       type_="foreignkey")
    for t in OWNED:
        op.drop_constraint(f"{t}_owner_fkey", t, type_="foreignkey")
    for t in ("leads", "campaigns"):
        op.drop_index(f"ix_{t}_owner_id", table_name=t)
        op.drop_column(t, "owner_id")
