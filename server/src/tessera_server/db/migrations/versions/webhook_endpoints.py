"""Add the webhook endpoints table.

The first revision creates every current table. This revision adds webhook
endpoints only when a database was migrated before that table existed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the webhook endpoints table when the first revision did not."""
    bind = op.get_bind()
    names = set(inspect(bind).get_table_names())
    if "webhook_endpoints" in names:
        return
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("url", sa.String(2000), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("secret", sa.String(200), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    """Drop the webhook endpoints table."""
    bind = op.get_bind()
    names = set(inspect(bind).get_table_names())
    if "webhook_endpoints" in names:
        op.drop_table("webhook_endpoints")
