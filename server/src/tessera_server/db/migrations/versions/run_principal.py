"""Store the principal bound to a run.

The first revision creates every current table. This revision adds the
column only when a database was migrated before it existed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add `runs.principal` when the first revision did not."""
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("runs")}
    if "principal" in columns:
        return
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("principal", sa.String(length=320), nullable=True))


def downgrade() -> None:
    """Remove `runs.principal`."""
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("runs")}
    if "principal" not in columns:
        return
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("principal")
