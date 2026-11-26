"""Store admin UI sessions server-side.

The first revision creates every current table. This revision adds the
table only when a database was migrated before it existed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create `admin_sessions` when the first revision did not."""
    bind = op.get_bind()
    names = set(inspect(bind).get_table_names())
    if "admin_sessions" in names:
        return
    op.create_table(
        "admin_sessions",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("secret_sha256", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(300), nullable=False),
        sa.Column(
            "admin_key_id",
            sa.String(26),
            sa.ForeignKey("admin_keys.id"),
            nullable=True,
        ),
        sa.Column("csrf", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Drop `admin_sessions`."""
    bind = op.get_bind()
    names = set(inspect(bind).get_table_names())
    if "admin_sessions" in names:
        op.drop_table("admin_sessions")
