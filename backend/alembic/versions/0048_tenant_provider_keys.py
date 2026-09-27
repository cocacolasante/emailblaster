"""Per-workspace provider credentials.  Multi-tenancy P5.

Creates ``tenant_provider_keys`` and SEEDS the bootstrap workspace from
the process environment (the single-tenant era's .env keys), encrypted
with ENCRYPTION_KEY.  After this, the app never reads provider keys from
the environment again — each workspace manages its own in
Settings → Integrations.

Run inside the backend container (it has the env vars).  Idempotent:
existing (tenant, provider) rows are left untouched.
"""
import json
import os
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0048"
down_revision: Union[str, None] = "0047"
branch_labels = None
depends_on = None

BOOTSTRAP_TENANT_ID = "00000000-0000-0000-0000-000000000001"

# provider → {field: env var}; first field listed is the "primary" secret.
SEED = {
    "anthropic": {"api_key": "ANTHROPIC_API_KEY"},
    "brevo": {
        "api_key": "BREVO_API_KEY",
        "sender_email": "BREVO_SENDER_EMAIL",
        "sender_name": "BREVO_SENDER_NAME",
        "webhook_secret": "BREVO_WEBHOOK_SECRET",
    },
    "hunter": {"api_key": "HUNTER_API_KEY"},
    "apollo": {"api_key": "APOLLO_API_KEY"},
    "unipile": {
        "api_key": "UNIPILE_API_KEY",
        "dsn": "UNIPILE_DSN",
        "webhook_secret": "UNIPILE_WEBHOOK_SECRET",
    },
    "adzuna": {"app_key": "ADZUNA_APP_KEY", "app_id": "ADZUNA_APP_ID"},
}
REQUIRED = {
    "anthropic": ("api_key",), "brevo": ("api_key", "sender_email"),
    "hunter": ("api_key",), "apollo": ("api_key",),
    "unipile": ("api_key", "dsn"), "adzuna": ("app_id", "app_key"),
}


def _mask(v: str) -> str:
    return ("••••" + v[-4:]) if len(v) > 4 else "••••"


def upgrade() -> None:
    op.create_table(
        "tenant_provider_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE",
                                name="tenant_provider_keys_tenant_id_fkey"),
                  nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("encrypted_credentials", sa.Text, nullable=False),
        sa.Column("preview", sa.Text, nullable=True),
        sa.Column("last_test_status", sa.Text, nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_error", sa.Text, nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL",
                                name="tenant_provider_keys_updated_by_fkey"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "provider", name="uq_tenant_provider_keys_tenant_provider"),
    )
    op.create_index("ix_tenant_provider_keys_tenant_id", "tenant_provider_keys", ["tenant_id"])

    bind = op.get_bind()
    has_bootstrap = bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM tenants WHERE id = :t)"), {"t": BOOTSTRAP_TENANT_ID},
    ).scalar()
    key = os.environ.get("ENCRYPTION_KEY", "")
    if not has_bootstrap or not key:
        return
    from cryptography.fernet import Fernet

    fernet = Fernet(key.encode())
    for provider, fields in SEED.items():
        data = {f: (os.environ.get(env) or "").strip() for f, env in fields.items()}
        if any(not data.get(f) for f in REQUIRED[provider]):
            continue
        primary = next(iter(fields))
        preview = _mask(data[primary])
        if provider == "brevo":
            preview += f" · {data['sender_email']}"
        elif provider == "unipile":
            preview += f" · {data['dsn']}"
        token = fernet.encrypt(json.dumps(data, sort_keys=True).encode()).decode()
        bind.execute(
            sa.text(
                "INSERT INTO tenant_provider_keys (tenant_id, provider, encrypted_credentials, preview) "
                "VALUES (:t, :p, :c, :v) ON CONFLICT (tenant_id, provider) DO NOTHING"
            ),
            {"t": BOOTSTRAP_TENANT_ID, "p": provider, "c": token, "v": preview},
        )


def downgrade() -> None:
    op.drop_index("ix_tenant_provider_keys_tenant_id", table_name="tenant_provider_keys")
    op.drop_table("tenant_provider_keys")
