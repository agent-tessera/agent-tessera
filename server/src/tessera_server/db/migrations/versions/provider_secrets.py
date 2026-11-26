"""Store provider API keys encrypted with the server key.

The first revision creates every current table. This revision adds the
table only when a database was migrated before it existed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create `provider_secrets` when the first revision did not."""
    bind = op.get_bind()
    names = set(inspect(bind).get_table_names())
    if "provider_secrets" in names:
        return
    op.create_table(
        "provider_secrets",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name"),
    )


def downgrade() -> None:
    """Drop `provider_secrets`."""
    bind = op.get_bind()
    names = set(inspect(bind).get_table_names())
    if "provider_secrets" in names:
        op.drop_table("provider_secrets")
