"""Stored admin credentials for the `tessera` CLI."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from agent_tessera.errors import ConfigError

_DEFAULT_PROFILE = "default"


def credentials_path() -> Path:
    """Return the path of the credentials file.

    Returns:
        `~/.config/tessera/credentials.yaml`.
    """
    return Path.home() / ".config" / "tessera" / "credentials.yaml"


def save_profile(
    *,
    server: str,
    admin_key: str,
    profile: str = _DEFAULT_PROFILE,
    path: Path | None = None,
) -> Path:
    """Write one profile into the credentials file.

    Args:
        server: Control-plane origin.
        admin_key: Admin API key.
        profile: Profile name.
        path: Override path (tests).

    Returns:
        The path written. The file mode is `0o600`.
    """
    target = path or credentials_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    document = _load_document(target)
    profiles = document.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
        document["profiles"] = profiles
    profiles[profile] = {"server": server.rstrip("/"), "admin_key": admin_key}
    if "current" not in document:
        document["current"] = profile
    _write_document(target, document)
    return target


def remove_profile(*, profile: str = _DEFAULT_PROFILE, path: Path | None = None) -> bool:
    """Remove one profile from the credentials file.

    Args:
        profile: Profile name.
        path: Override path (tests).

    Returns:
        `True` when the profile existed and was removed.
    """
    target = path or credentials_path()
    if not target.is_file():
        return False
    document = _load_document(target)
    profiles = document.get("profiles")
    if not isinstance(profiles, dict) or profile not in profiles:
        return False
    del profiles[profile]
    if document.get("current") == profile:
        document["current"] = next(iter(profiles), _DEFAULT_PROFILE)
    if profiles:
        _write_document(target, document)
    else:
        target.unlink(missing_ok=True)
    return True


def resolve_server(
    *,
    cli_server: str | None = None,
    profile: str = _DEFAULT_PROFILE,
    path: Path | None = None,
) -> str:
    """Resolve the control-plane URL.

    Args:
        cli_server: `--server` value when present.
        profile: Profile name for the credentials file.
        path: Override credentials path (tests).

    Returns:
        The origin URL.

    Raises:
        ConfigError: When no URL is configured.
    """
    env = os.environ.get("TESSERA_URL", "").strip()
    if env:
        return env.rstrip("/")
    if cli_server:
        return cli_server.rstrip("/")
    stored = _profile(profile, path=path)
    server = stored.get("server") if stored is not None else None
    if isinstance(server, str) and server.strip():
        return server.rstrip("/")
    raise ConfigError("set TESSERA_URL, pass --server, or run tessera login")


def resolve_admin_key(
    *,
    profile: str = _DEFAULT_PROFILE,
    path: Path | None = None,
) -> str:
    """Resolve the admin API key.

    Args:
        profile: Profile name for the credentials file.
        path: Override credentials path (tests).

    Returns:
        The admin key.

    Raises:
        ConfigError: When no key is configured.
    """
    env = os.environ.get("TESSERA_ADMIN_KEY", "").strip()
    if env:
        return env
    stored = _profile(profile, path=path)
    key = stored.get("admin_key") if stored is not None else None
    if isinstance(key, str) and key.strip():
        return key
    raise ConfigError("set TESSERA_ADMIN_KEY or run tessera login")


def _profile(name: str, *, path: Path | None) -> dict[str, Any] | None:
    """Load one named profile.

    Args:
        name: Profile name.
        path: Override credentials path.

    Returns:
        The profile mapping, or `None` when missing.
    """
    target = path or credentials_path()
    document = _load_document(target)
    profiles = document.get("profiles")
    if not isinstance(profiles, dict):
        return None
    raw = profiles.get(name)
    return raw if isinstance(raw, dict) else None


def _load_document(path: Path) -> dict[str, Any]:
    """Read the credentials YAML object.

    Args:
        path: Credentials file.

    Returns:
        A mapping. Missing files yield an empty document.
    """
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _write_document(path: Path, document: Mapping[str, Any]) -> None:
    """Write the credentials YAML with mode `0o600`.

    Args:
        path: Destination file.
        document: Mapping to serialize.
    """
    text = yaml.safe_dump(dict(document), sort_keys=False)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


__all__ = [
    "credentials_path",
    "remove_profile",
    "resolve_admin_key",
    "resolve_server",
    "save_profile",
]
