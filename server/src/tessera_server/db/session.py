"""Database engines, sessions, and the Postgres advisory lock."""

from __future__ import annotations

from typing import Protocol, cast

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool


class _DBAPICursor(Protocol):
    """The small cursor surface used to enable SQLite foreign keys."""

    def execute(self, statement: str) -> object:
        """Run one statement."""

    def close(self) -> None:
        """Close the cursor."""


class _DBAPIConnection(Protocol):
    """A DB-API connection that can open a cursor."""

    def cursor(self) -> _DBAPICursor:
        """Return a new cursor."""


def make_engine(url: str) -> Engine:
    """Create an engine and enable SQLite foreign keys.

    SQLite uses a pool that closes each connection on the thread that opened
    it. Postgres keeps SQLAlchemy's default pool.

    Args:
        url: SQLAlchemy database URL.

    Returns:
        A new engine.
    """
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"timeout": 30}, poolclass=NullPool)
    else:
        engine = create_engine(url)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
            cursor = cast(_DBAPIConnection, dbapi_connection).cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a session factory for an engine.

    Args:
        engine: Engine the sessions will use.

    Returns:
        A sessionmaker that keeps attributes available after commit.
    """
    return sessionmaker(engine, expire_on_commit=False)


def advisory_xact_lock(session: Session, key: int = 7401) -> None:
    """Serialize a transaction on Postgres.

    Args:
        session: Transaction that should hold the lock until commit.
        key: Advisory-lock key. Audit appends use `7401`.

    SQLite has one writer, so the call does nothing there.
    """
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


__all__ = ["advisory_xact_lock", "make_engine", "session_factory"]
