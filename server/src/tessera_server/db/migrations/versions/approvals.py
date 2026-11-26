"""Add the approvals table.

The first revision creates every current table. This revision adds approvals
only when a database was migrated before that table existed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the approvals table when the first revision did not."""
    bind = op.get_bind()
    names = set(inspect(bind).get_table_names())
    if "approvals" in names:
        return
    op.create_table(
        "approvals",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("agent_id", sa.String(26), nullable=False),
        sa.Column("instance_id", sa.String(32), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("action", sa.JSON(), nullable=False),
        sa.Column("decision", sa.JSON(), nullable=False),
        sa.Column("route", sa.JSON(), nullable=False),
        sa.Column("route_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("requested_by_principal", sa.String(320), nullable=True),
        sa.Column("resolved_by", sa.String(200), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_approvals_status_expires", "approvals", ["status", "expires_at"])


def downgrade() -> None:
    """Drop the approvals table."""
    bind = op.get_bind()
    names = set(inspect(bind).get_table_names())
    if "approvals" not in names:
        return
    op.drop_index("ix_approvals_status_expires", table_name="approvals")
    op.drop_table("approvals")
