"""Add indexed columns on `events` for console query and traces.

Fresh installs create these columns via `Base.metadata.create_all` in 0001.
This revision adds them when upgrading a 0.5 database, then backfills from
`payload` in batches of 1,000.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

from tessera_server.services.event_index import index_fields

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH = 1000
_COLUMNS: tuple[tuple[str, int], ...] = (
    ("run_id", 64),
    ("span_id", 16),
    ("parent_span_id", 16),
    ("kind", 16),
    ("resource", 300),
    ("effect", 24),
    ("status", 16),
    ("rule", 200),
)
_INDEXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ix_events_run_ts", ("run_id", "ts")),
    ("ix_events_span_id", ("span_id",)),
    ("ix_events_agent_type_ts", ("agent_id", "type", "ts")),
    ("ix_events_resource", ("resource",)),
)


def upgrade() -> None:
    """Add event index columns, indexes, and backfill from payload."""
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("events")}
    for name, length in _COLUMNS:
        if name in columns:
            continue
        op.add_column("events", sa.Column(name, sa.String(length), nullable=True))
    existing = {index["name"] for index in inspect(bind).get_indexes("events")}
    for name, cols in _INDEXES:
        if name in existing:
            continue
        op.create_index(name, "events", list(cols))
    _backfill(bind)


def downgrade() -> None:
    """Drop event index columns and their indexes."""
    bind = op.get_bind()
    existing = {index["name"] for index in inspect(bind).get_indexes("events")}
    for name, _cols in _INDEXES:
        if name in existing:
            op.drop_index(name, table_name="events")
    columns = {column["name"] for column in inspect(bind).get_columns("events")}
    for name, _length in _COLUMNS:
        if name in columns:
            op.drop_column("events", name)


def _backfill(bind: sa.Connection) -> None:
    """Fill index columns from `payload` in id-ordered batches.

    Args:
        bind: Live database connection.
    """
    last_id = ""
    while True:
        rows = (
            bind.execute(
                sa.text("SELECT id, payload FROM events WHERE id > :last ORDER BY id LIMIT :limit"),
                {"last": last_id, "limit": _BATCH},
            )
            .mappings()
            .all()
        )
        if not rows:
            return
        for row in rows:
            payload = _payload(row["payload"])
            fields = index_fields(payload)
            bind.execute(
                sa.text(
                    "UPDATE events SET run_id = :run_id, span_id = :span_id, "
                    "parent_span_id = :parent_span_id, kind = :kind, resource = :resource, "
                    "effect = :effect, status = :status, rule = :rule WHERE id = :id"
                ),
                {"id": row["id"], **fields},
            )
        last_id = str(rows[-1]["id"])


def _payload(value: object) -> Mapping[str, object]:
    """Normalize a JSON payload column to a mapping.

    Args:
        value: Driver value for `events.payload`.

    Returns:
        A mapping. Empty when the value is not an object.
    """
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str | bytes | bytearray):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(loaded, Mapping):
            return dict(loaded)
    return {}
