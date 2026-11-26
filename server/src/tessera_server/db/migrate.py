"""Apply and roll back the Alembic revision chain."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from tessera_server.db.session import make_engine

_SCRIPT = Path(__file__).resolve().parent / "migrations"


def upgrade_database(url: str) -> None:
    """Migrate a database to the latest revision.

    Args:
        url: SQLAlchemy database URL.
    """
    command.upgrade(_config(url), "head")


def downgrade_database(url: str) -> None:
    """Roll a database back to an empty schema.

    Args:
        url: SQLAlchemy database URL.
    """
    command.downgrade(_config(url), "base")


def database_revision(url: str) -> str | None:
    """Return the current Alembic revision.

    Args:
        url: SQLAlchemy database URL.

    Returns:
        The revision id, or `None` when the database has not been migrated.
    """
    engine = make_engine(url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            revision = context.get_current_revision()
    finally:
        engine.dispose()
    return revision


def table_names(url: str) -> set[str]:
    """List tables in a database.

    Args:
        url: SQLAlchemy database URL.

    Returns:
        Table names, excluding Alembic's version table.
    """
    engine = make_engine(url)
    try:
        names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    names.discard("alembic_version")
    return names


def _config(url: str) -> Config:
    """Build an Alembic config for a URL.

    Args:
        url: SQLAlchemy database URL. Percent signs are escaped for ConfigParser.

    Returns:
        A config whose script location is this package's migration directory.
    """
    config = Config()
    config.set_main_option("script_location", str(_SCRIPT))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


__all__ = ["database_revision", "downgrade_database", "table_names", "upgrade_database"]
