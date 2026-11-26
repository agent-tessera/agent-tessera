"""Store supervisor grant documents.

The first revision creates every current table. This revision adds the
table only when a database was migrated before it existed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create `supervisor_grants` when the first revision did not."""
    bind = op.get_bind()
    names = set(inspect(bind).get_table_names())
    if "supervisor_grants" in names:
        return
    op.create_table(
        "supervisor_grants",
        sa.Column("supervisor", sa.String(200), primary_key=True),
        sa.Column("yaml", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    """Drop `supervisor_grants`."""
    bind = op.get_bind()
    names = set(inspect(bind).get_table_names())
    if "supervisor_grants" in names:
        op.drop_table("supervisor_grants")
