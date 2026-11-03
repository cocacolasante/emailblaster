"""Per-campaign override for the "skip anyone already emailed" rule.

Off by default, so every campaign skips people the workspace has already
emailed.  Existing retarget campaigns exist to re-contact people, so they start
with it on.
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0055"
down_revision: Union[str, None] = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column(
        "allow_previously_emailed", sa.Boolean(), nullable=False, server_default="false",
    ))
    op.execute("UPDATE campaigns SET allow_previously_emailed = true WHERE name LIKE 'Retarget — %'")


def downgrade() -> None:
    op.drop_column("campaigns", "allow_previously_emailed")
