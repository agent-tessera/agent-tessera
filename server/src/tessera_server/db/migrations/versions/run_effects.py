"""Add the run effects table.

The first revision creates every current table. This revision adds run effects
only when a database was migrated before that table existed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the run effects table when the first revision did not."""
    bind = op.get_bind()
    names = set(inspect(bind).get_table_names())
    if "run_effects" in names:
        return
    op.create_table(
        "run_effects",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("agent_id", sa.String(26), nullable=False),
        sa.Column("ref", sa.String(500), nullable=False),
        sa.Column("reversible", sa.Boolean(), nullable=False),
        sa.Column("undo", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    """Drop the run effects table."""
    bind = op.get_bind()
    names = set(inspect(bind).get_table_names())
    if "run_effects" in names:
        op.drop_table("run_effects")
