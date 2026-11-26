"""Store the last supervisor acknowledgement on an agent.

The first revision creates every current column. This revision adds the
column only when a database was migrated before it existed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add `agents.supervisor_ack_at` when the first revision did not."""
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("agents")}
    if "supervisor_ack_at" in columns:
        return
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("supervisor_ack_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Remove `agents.supervisor_ack_at`."""
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("agents")}
    if "supervisor_ack_at" not in columns:
        return
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("supervisor_ack_at")
