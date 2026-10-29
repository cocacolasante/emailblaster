"""OAuth for MCP clients that can't take a pasted key (Claude, ChatGPT).

``oauth_clients`` holds dynamic client registrations (global — registered
before anyone signs in).  Access tokens are ordinary ``api_keys`` rows;
``api_keys.oauth_client_id`` marks the ones an OAuth grant issued.
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0052"
down_revision: Union[str, None] = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("client_id", sa.Text, primary_key=True),
        sa.Column("client_secret_hash", sa.Text, nullable=True),
        sa.Column("client_name", sa.Text, nullable=False),
        sa.Column("redirect_uris", postgresql.ARRAY(sa.Text), nullable=False),
        sa.Column("token_endpoint_auth_method", sa.Text, nullable=False, server_default="none"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column("api_keys", sa.Column(
        "oauth_client_id", sa.Text,
        sa.ForeignKey("oauth_clients.client_id", ondelete="SET NULL",
                      name="api_keys_oauth_client_id_fkey"),
        nullable=True,
    ))


def downgrade() -> None:
    op.drop_column("api_keys", "oauth_client_id")
    op.drop_table("oauth_clients")
