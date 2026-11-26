"""Add alert rules and the record of when each rule fired.

The first revision creates every current table. This revision adds the alert
tables only when a database was migrated before they existed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the alert tables when the first revision did not."""
    bind = op.get_bind()
    names = set(inspect(bind).get_table_names())
    if "alert_rules" not in names:
        op.create_table(
            "alert_rules",
            sa.Column("id", sa.String(26), primary_key=True),
            sa.Column("name", sa.String(63), nullable=False, unique=True),
            sa.Column("metric", sa.String(32), nullable=False),
            sa.Column("threshold", sa.Float(), nullable=False),
            sa.Column("window_seconds", sa.Integer(), nullable=False),
            sa.Column("labels", sa.JSON(), nullable=False),
            sa.Column("autonomy", sa.String(32), nullable=True),
            sa.Column("action", sa.String(16), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "alert_firings" not in names:
        op.create_table(
            "alert_firings",
            sa.Column("id", sa.String(26), primary_key=True),
            sa.Column("rule_id", sa.String(26), sa.ForeignKey("alert_rules.id"), nullable=False),
            sa.Column("agent_id", sa.String(26), sa.ForeignKey("agents.id"), nullable=False),
            sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    """Drop the alert tables."""
    bind = op.get_bind()
    names = set(inspect(bind).get_table_names())
    if "alert_firings" in names:
        op.drop_table("alert_firings")
    if "alert_rules" in names:
        op.drop_table("alert_rules")
