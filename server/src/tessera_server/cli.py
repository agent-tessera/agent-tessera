"""Command-line interface for the control plane (`tessera server`)."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from typing import NoReturn

import uvicorn

from agent_tessera.errors import TesseraError
from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import database_revision, upgrade_database
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.audit import append_audit
from tessera_server.services.keys import rotate_keys
from tessera_server.services.secrets import create_admin_key, parse_secret, rotate_admin_key

_HEAD = "0013"


class _Parser(argparse.ArgumentParser):
    """Argument parser that reports usage errors with exit code 1."""

    def error(self, message: str) -> NoReturn:
        """Print a usage error to stderr and exit.

        Args:
            message: What was wrong with the arguments.
        """
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Run one `tessera server` command.

    Args:
        argv: Arguments after `server`. Defaults to `sys.argv[1:]`.

    Returns:
        The process exit code.

    Example:
        ```python
        from tessera_server.cli import main

        raise SystemExit(main(["migrate"]))
        ```
    """
    try:
        return _run(list(sys.argv[1:] if argv is None else argv))
    except TesseraError as err:
        _write(str(err), error=True)
        return 1


def _run(argv: list[str]) -> int:
    """Parse and dispatch a server command.

    Args:
        argv: Arguments excluding the program name.

    Returns:
        The process exit code.
    """
    parser = _Parser(prog="tessera server", description="Run the Agent Tessera control plane.")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("migrate", help="apply database migrations")
    dev = commands.add_parser("dev", help="run a local development server")
    dev.add_argument("--host", default="127.0.0.1")
    dev.add_argument("--port", type=int, default=8700)
    create = commands.add_parser("create-admin-key", help="create an admin key")
    create.add_argument("--name", required=True)
    rotate_admin = commands.add_parser(
        "rotate-admin-key",
        help="create an admin key and revoke earlier keys with the same name",
    )
    rotate_admin.add_argument("--name", required=True)
    commands.add_parser("rotate-keys", help="rotate the active signing key")
    serve = commands.add_parser("serve", help="serve the HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8700)
    args = parser.parse_args(argv)
    status = 0
    if args.command == "migrate":
        upgrade_database(Settings.from_env().database_url)
        _write("database migrated")
    elif args.command == "dev":
        status = _dev(args.host, args.port)
    elif args.command == "create-admin-key":
        status = _create_admin_key(args.name)
    elif args.command == "rotate-admin-key":
        status = _rotate_admin_key(args.name)
    elif args.command == "rotate-keys":
        status = _rotate()
    elif args.command == "serve":
        status = _serve(args.host, args.port)
    else:
        parser.print_help(sys.stdout)
    return status


def _create_admin_key(name: str) -> int:
    """Create an admin key and print it once.

    Args:
        name: Label for the key.

    Returns:
        `0` after the key is printed.
    """
    settings = Settings.from_env()
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            token = create_admin_key(session, name)
            parsed = parse_secret(token, prefix="tsa")
            key_id = "" if parsed is None else parsed[0]
            append_audit(
                session,
                actor="cli",
                entry_type="admin_key_created",
                payload={"id": key_id, "name": name},
            )
            _write(token)
    finally:
        engine.dispose()
    return 0


def _rotate_admin_key(name: str) -> int:
    """Create a new admin key, revoke earlier ones with the same name, print once.

    Args:
        name: Label for the new key and for keys to revoke.

    Returns:
        `0` after the key is printed.
    """
    settings = Settings.from_env()
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            token, previous_ids = rotate_admin_key(session, name)
            parsed = parse_secret(token, prefix="tsa")
            key_id = "" if parsed is None else parsed[0]
            append_audit(
                session,
                actor="cli",
                entry_type="admin_key_rotated",
                payload={
                    "id": key_id,
                    "name": name,
                    "previous_ids": list(previous_ids),
                },
            )
            _write(token)
    finally:
        engine.dispose()
    return 0


def _rotate() -> int:
    """Rotate the signing key and print the new key id.

    Returns:
        `0` after the new id is printed.
    """
    settings = Settings.from_env()
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            row = rotate_keys(session, settings)
            append_audit(
                session,
                actor="cli",
                entry_type="signing_key_rotated",
                payload={"kid": row.kid},
            )
            _write(row.kid)
    finally:
        engine.dispose()
    return 0


def _dev(host: str, port: int) -> int:
    """Migrate, print an admin key, and serve development mode on loopback.

    Args:
        host: Bind address. Only `127.0.0.1` is accepted.
        port: Bind port. When no issuer is configured, the issuer is this URL.

    Returns:
        `1` when the bind address is not loopback. Otherwise the server runs
        until it stops.
    """
    if host != "127.0.0.1":
        _write("dev mode binds 127.0.0.1 only", error=True)
        return 1
    settings = Settings.from_env(dev=True)
    if "TESSERA_SERVER_ISSUER" not in os.environ:
        settings = settings.model_copy(update={"issuer": f"http://127.0.0.1:{port}"})
    upgrade_database(settings.database_url)
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            token = create_admin_key(session, "dev")
            parsed = parse_secret(token, prefix="tsa")
            key_id = "" if parsed is None else parsed[0]
            append_audit(
                session,
                actor="dev",
                entry_type="admin_key_created",
                payload={"id": key_id, "name": "dev"},
            )
    finally:
        engine.dispose()
    _write(f"http://127.0.0.1:{port}")
    _write(token)
    app = create_app(settings)
    app.state.approval_poll = 1.0
    uvicorn.run(app, host="127.0.0.1", port=port)
    return 0


def _serve(host: str, port: int) -> int:
    """Start the API when the database is at the latest revision.

    Args:
        host: Bind address.
        port: Bind port.

    Returns:
        `1` when the database has not been migrated. Otherwise the server
        runs until it stops.
    """
    settings = Settings.from_env()
    if database_revision(settings.database_url) != _HEAD:
        _write("database is not migrated; run tessera server migrate", error=True)
        return 1
    app = create_app(settings)
    app.state.approval_poll = 1.0
    uvicorn.run(app, host=host, port=port)
    return 0


def _write(text: str, *, error: bool = False) -> None:
    """Write one line of command output and flush it.

    Flushing keeps the development URL and admin key visible when stdout is a
    pipe, before the server starts and writes its own logs.

    Args:
        text: Line to write, without a trailing newline.
        error: Write to stderr when true, otherwise stdout.
    """
    stream = sys.stderr if error else sys.stdout
    stream.write(f"{text}\n")
    stream.flush()


__all__ = ["main"]
