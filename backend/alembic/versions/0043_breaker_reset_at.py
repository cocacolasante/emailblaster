"""campaigns.breaker_reset_at — manual Resume overrides the circuit breaker.

Additive + reversible.  Stamped by every manual Resume; the bounce/spam
circuit breaker only counts email events that occurred AFTER this instant,
so resuming a breaker-paused campaign can't be immediately re-tripped by
the stale bounces still inside the rolling window.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0043"
down_revision: Union[str, None] = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column("breaker_reset_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("campaigns", "breaker_reset_at")
