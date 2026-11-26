"""Alembic environment for the control-plane schema."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from tessera_server.db.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    url = config.get_main_option("sqlalchemy.url")
    if url is None:
        raise RuntimeError("sqlalchemy.url is required")
    connectable = create_engine(url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


run_migrations_online()
