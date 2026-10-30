"""Per-workspace bounce-rate threshold for the deliverability breaker.

``agent_settings.breaker_bounce_rate`` (a fraction, e.g. 0.06); NULL means
the deployment default ``CIRCUIT_BREAKER_BOUNCE_RATE``.
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0053"
down_revision: Union[str, None] = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_settings", sa.Column("breaker_bounce_rate", sa.Numeric(5, 4), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_settings", "breaker_bounce_rate")
