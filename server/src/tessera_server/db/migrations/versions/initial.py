"""Create the control-plane tables."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from tessera_server.db.models import Base

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create every control-plane table."""
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    """Drop every control-plane table."""
    Base.metadata.drop_all(op.get_bind())
