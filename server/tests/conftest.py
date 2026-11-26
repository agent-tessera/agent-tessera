"""Database fixtures for server tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


def _backends() -> list[str]:
    names = ["sqlite"]
    if os.environ.get("TESSERA_TEST_DATABASE_URL"):
        names.append("postgres")
    return names


@pytest.fixture(params=_backends())
def database_url(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[str]:
    """Yield a SQLite URL, and Postgres when `TESSERA_TEST_DATABASE_URL` is set."""
    if request.param == "sqlite":
        yield f"sqlite:///{tmp_path / 'server.db'}"
        return
    yield os.environ["TESSERA_TEST_DATABASE_URL"]


@pytest.fixture
def built_console(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Serve a stub console `index.html` so tests do not need `npm run build`."""
    index = tmp_path / "console-index.html"
    index.write_text("<!doctype html><title>Tessera</title>", encoding="utf-8")
    monkeypatch.setattr("tessera_server.web._INDEX", index)
    return index
