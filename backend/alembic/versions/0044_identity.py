"""Identity: tenants (workspaces), users, memberships, sessions, auth
tokens, invitations.  Multi-tenancy P1.

Bootstrap: when the database already holds data (single-tenant era),
create the "bootstrap" workspace with a FIXED id so later migrations
(0045 backfill / server default, 0048 key seed) can reference it, plus
an owner user from ``BOOTSTRAP_OWNER_EMAIL`` (falling back to
``OWNER_NOTIFY_EMAIL``).  The owner has NO password yet — set one with
``python -m app.scripts.set_password <email>`` (keeps passwords out of
env files and migration history).
"""
import os
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044"
down_revision: Union[str, None] = "0043"
branch_labels = None
depends_on = None

BOOTSTRAP_TENANT_ID = "00000000-0000-0000-0000-000000000001"

tenant_status = postgresql.ENUM("active", "suspended", name="tenant_status", create_type=False)
membership_role = postgresql.ENUM("owner", "admin", "member", name="membership_role", create_type=False)
auth_token_purpose = postgresql.ENUM(
    "password_reset", "email_verify", name="auth_token_purpose", create_type=False,
)


def _uuid_pk():
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                     server_default=sa.text("gen_random_uuid()"))


def _ts(name, nullable=True, default=False):
    kw = {"server_default": sa.func.now()} if default else {}
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, **kw)


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM("active", "suspended", name="tenant_status").create(bind, checkfirst=True)
    postgresql.ENUM("owner", "admin", "member", name="membership_role").create(bind, checkfirst=True)
    postgresql.ENUM("password_reset", "email_verify", name="auth_token_purpose").create(bind, checkfirst=True)

    op.create_table(
        "tenants",
        _uuid_pk(),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("slug", sa.Text, nullable=False, unique=True),
        sa.Column("status", tenant_status, nullable=False, server_default="active"),
        _ts("created_at", False, True),
        _ts("updated_at", False, True),
    )
    op.create_table(
        "users",
        _uuid_pk(),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=True),
        sa.Column("password_hash", sa.Text, nullable=True),
        sa.Column("auth_provider", sa.Text, nullable=True),
        sa.Column("external_id", sa.Text, nullable=True),
        _ts("email_verified_at"),
        _ts("created_at", False, True),
        _ts("updated_at", False, True),
        sa.UniqueConstraint("auth_provider", "external_id", name="uq_users_auth_provider_external_id"),
    )
    op.create_table(
        "memberships",
        _uuid_pk(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("role", membership_role, nullable=False, server_default="member"),
        sa.Column("notify_email_enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("digest_enabled", sa.Boolean, nullable=False, server_default="true"),
        _ts("created_at", False, True),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_user"),
    )
    op.create_table(
        "user_sessions",
        _uuid_pk(),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        _ts("expires_at", False),
        _ts("revoked_at"),
        _ts("last_seen_at"),
        sa.Column("ip", sa.Text, nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        _ts("created_at", False, True),
    )
    op.create_table(
        "auth_tokens",
        _uuid_pk(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("purpose", auth_token_purpose, nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        _ts("expires_at", False),
        _ts("used_at"),
        _ts("created_at", False, True),
    )
    op.create_table(
        "invitations",
        _uuid_pk(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("role", membership_role, nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("invited_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        _ts("expires_at", False),
        _ts("accepted_at"),
        _ts("revoked_at"),
        _ts("created_at", False, True),
    )

    # --- Bootstrap workspace for pre-existing single-tenant data ---------
    has_data = bind.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM campaigns) OR EXISTS (SELECT 1 FROM leads)"
    )).scalar()
    owner_email = (
        os.environ.get("BOOTSTRAP_OWNER_EMAIL") or os.environ.get("OWNER_NOTIFY_EMAIL") or ""
    ).strip().lower()
    if not (has_data or owner_email):
        return
    name = os.environ.get("BOOTSTRAP_TENANT_NAME") or "My Workspace"
    bind.execute(
        sa.text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, 'workspace')"),
        {"id": BOOTSTRAP_TENANT_ID, "name": name},
    )
    if owner_email:
        user_id = bind.execute(
            sa.text("INSERT INTO users (email, name) VALUES (:e, :n) RETURNING id"),
            # "Operator" was the old placeholder default — not a real name.
            {"e": owner_email,
             "n": (os.environ.get("OWNER_NOTIFY_NAME") or "").strip() not in ("", "Operator")
                  and os.environ["OWNER_NOTIFY_NAME"].strip() or None},
        ).scalar()
        bind.execute(
            sa.text(
                "INSERT INTO memberships (tenant_id, user_id, role) "
                "VALUES (:t, :u, 'owner')"
            ),
            {"t": BOOTSTRAP_TENANT_ID, "u": user_id},
        )


def downgrade() -> None:
    for t in ("invitations", "auth_tokens", "user_sessions", "memberships", "users", "tenants"):
        op.drop_table(t)
    bind = op.get_bind()
    for e in ("auth_token_purpose", "membership_role", "tenant_status"):
        postgresql.ENUM(name=e).drop(bind, checkfirst=True)
