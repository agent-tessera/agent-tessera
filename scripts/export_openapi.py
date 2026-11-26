#!/usr/bin/env python3
"""Write the control-plane OpenAPI document for the console type generator.

Builds a minimal FastAPI app offline (in-memory SQLite, no server listen) and
dumps ``create_app(...).openapi()`` as JSON. Invoked by ``npm run gen:api``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tessera_server.app import create_app
from tessera_server.config import Settings

_DEFAULT_OUT = Path(__file__).resolve().parents[1] / "web" / "openapi.json"


def export_openapi(out: Path) -> None:
    """Write the OpenAPI schema to ``out``.

    Args:
        out: Destination JSON path (created or overwritten).
    """
    settings = Settings(
        database_url="sqlite:///:memory:",
        issuer="http://127.0.0.1:8700",
        org="default",
        kek=b"k" * 32,
        dev=True,
    )
    app = create_app(settings)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Parse CLI args and export OpenAPI.

    Args:
        argv: Argument list without the program name. Defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (always ``0`` on success).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=_DEFAULT_OUT,
        help=f"JSON output path (default: {_DEFAULT_OUT})",
    )
    args = parser.parse_args(argv)
    export_openapi(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
