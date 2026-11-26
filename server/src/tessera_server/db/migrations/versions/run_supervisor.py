"""Store the supervisor bound to a run.

The first revision creates every current table. This revision adds the
column only when a database was migrated before it existed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add `runs.supervisor` when the first revision did not."""
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("runs")}
    if "supervisor" in columns:
        return
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("supervisor", sa.String(length=200), nullable=True))


def downgrade() -> None:
    """Remove `runs.supervisor`."""
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("runs")}
    if "supervisor" not in columns:
        return
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("supervisor")
