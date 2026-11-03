"""Index leads by workspace + lower(email).

Every send now asks "has this address replied?" across the workspace
(services/reply_stop.py), and the sequencer re-checks deferred follow-ups often.
"""
from typing import Union

from alembic import op

revision: str = "0054"
down_revision: Union[str, None] = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX ix_leads_tenant_lower_email ON leads (tenant_id, lower(email))")


def downgrade() -> None:
    op.execute("DROP INDEX ix_leads_tenant_lower_email")
