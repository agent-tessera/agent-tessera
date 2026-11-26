"""Add counters and leases for named quotas.

The first revision creates every current table. This revision adds the named
quota tables only when a database was migrated before those tables existed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create named-quota tables that the first revision did not create."""
    bind = op.get_bind()
    names = set(inspect(bind).get_table_names())
    if "quota_counters" not in names:
        op.create_table(
            "quota_counters",
            sa.Column("scope_key", sa.String(512), primary_key=True),
            sa.Column("quota_name", sa.String(63), primary_key=True),
            sa.Column("bucket", sa.String(64), primary_key=True),
            sa.Column("used", sa.Integer(), nullable=False),
            sa.Column("reserved", sa.Integer(), nullable=False),
        )
    if "quota_leases" not in names:
        op.create_table(
            "quota_leases",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("agent_id", sa.String(26), nullable=False),
            sa.Column("instance_id", sa.String(32), nullable=False),
            sa.Column("scope_key", sa.String(512), nullable=False),
            sa.Column("quota_name", sa.String(63), nullable=False),
            sa.Column("bucket", sa.String(64), nullable=False),
            sa.Column("granted", sa.Integer(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("closed", sa.Boolean(), nullable=False),
        )


def downgrade() -> None:
    """Drop the named-quota tables."""
    bind = op.get_bind()
    names = set(inspect(bind).get_table_names())
    if "quota_leases" in names:
        op.drop_table("quota_leases")
    if "quota_counters" in names:
        op.drop_table("quota_counters")
