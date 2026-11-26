"""Process settings for the control plane."""

from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from agent_tessera.errors import ConfigError
from agent_tessera.policy.models import parse_duration

_KEK_PATH = Path(".tessera") / "server.kek"
_SESSION_IDLE = 28_800
_SESSION_MAX = 86_400
_DEV_DATABASE_URL = "sqlite:///.tessera/server.db"
_DEV_ISSUER = "http://127.0.0.1:8700"
_KEK_BYTES = 32


class WorkloadBinding(BaseModel):
    """One workload-identity assertion that may bootstrap an agent.

    Attributes:
        issuer: Identity provider the assertion must come from.
        audience: Audience the assertion must carry.
        agent: Agent slug this binding may start.
        environment: Environment that agent runs in.
        claim: Claim that must be a non-empty string. Defaults to `sub`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    issuer: str
    audience: str
    agent: str
    environment: str
    claim: str = "sub"


class Settings(BaseModel):
    """Configuration the server reads once at startup.

    Attributes:
        database_url: SQLAlchemy URL for SQLite or Postgres.
        issuer: Public base URL of this server, without a trailing slash.
        org: Organization slug used in agent URIs.
        kek: 32-byte key that encrypts signing keys at rest.
        dev: Whether loopback requests may skip admin authentication.
        token_ttl: Runtime token lifetime in seconds.
        bundle_ttl: Policy bundle lifetime in seconds.
        lease_ttl: Token-lease lifetime in seconds.
        webhook_url: Catch-all webhook registered at startup. `None` skips it.
        webhook_secret: HMAC secret for webhooks that do not set their own.
        oidc_issuer: Identity provider issuer for admin SSO. `None` leaves SSO off.
        oidc_client_id: OAuth client id for the admin login.
        oidc_client_secret: OAuth client secret for the admin login.
        oidc_redirect_url: Callback URL. `None` uses `{issuer}/oidc/callback`.
        oidc_required: When true, the admin-key form is rejected.
        oidc_admin_group: Optional group claim required for admin SSO.
        workload_bindings: Assertions that may bootstrap an agent without a
            registration token. The first match wins.
        session_idle: Admin UI session idle timeout in seconds.
        session_max: Admin UI session absolute lifetime in seconds.
        trace_url: Optional OTel/backend URL template with `{trace_id}` and
            `{span_id}` placeholders for the console span drawer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    database_url: str
    issuer: str
    org: str = "default"
    kek: bytes
    dev: bool = False
    token_ttl: int = 900
    bundle_ttl: int = 86_400
    lease_ttl: int = 300
    webhook_url: str | None = None
    webhook_secret: str | None = None
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_url: str | None = None
    oidc_required: bool = False
    oidc_admin_group: str | None = None
    workload_bindings: tuple[WorkloadBinding, ...] = ()
    session_idle: int = _SESSION_IDLE
    session_max: int = _SESSION_MAX
    trace_url: str | None = None

    @classmethod
    def from_env(cls, *, dev: bool = False) -> Settings:
        """Build settings from the environment.

        Args:
            dev: Use development defaults and a key file under `.tessera/`.

        Returns:
            Frozen settings. The development key file is created mode `0600`
            when it does not already exist.

        Raises:
            ConfigError: If a required variable is missing, the key is not
                32 bytes of base64, or a session lifetime is not a duration.
                The message names the variable.

        Example:
            ```python
            settings = Settings.from_env(dev=True)
            assert settings.dev is True
            assert len(settings.kek) == 32
            ```
        """
        database_url = _setting("TESSERA_SERVER_DATABASE_URL", _DEV_DATABASE_URL, dev=dev)
        issuer = _setting("TESSERA_SERVER_ISSUER", _DEV_ISSUER, dev=dev).rstrip("/")
        org = os.environ.get("TESSERA_SERVER_ORG") or "default"
        webhook_url = os.environ.get("TESSERA_WEBHOOK_URL", "").strip() or None
        webhook_secret = os.environ.get("TESSERA_WEBHOOK_SECRET", "").strip() or None
        return cls(
            database_url=database_url,
            issuer=issuer,
            org=org,
            kek=_kek(dev=dev),
            dev=dev,
            webhook_url=webhook_url,
            webhook_secret=webhook_secret,
            oidc_issuer=_trimmed("TESSERA_OIDC_ISSUER"),
            oidc_client_id=_optional("TESSERA_OIDC_CLIENT_ID"),
            oidc_client_secret=_optional("TESSERA_OIDC_CLIENT_SECRET"),
            oidc_redirect_url=_trimmed("TESSERA_OIDC_REDIRECT_URL"),
            oidc_required=os.environ.get("TESSERA_OIDC_REQUIRED", "").strip() == "1",
            oidc_admin_group=_optional("TESSERA_OIDC_ADMIN_GROUP"),
            workload_bindings=_workload_bindings(os.environ.get("TESSERA_WORKLOAD_BINDINGS", "")),
            session_idle=_duration("TESSERA_SERVER_SESSION_IDLE", _SESSION_IDLE),
            session_max=_duration("TESSERA_SERVER_SESSION_MAX", _SESSION_MAX),
            trace_url=_optional("TESSERA_SERVER_TRACE_URL"),
        )


def _duration(name: str, default: int) -> int:
    """Read one optional duration setting.

    Args:
        name: Environment variable name.
        default: Seconds used when the variable is blank.

    Returns:
        The duration in seconds.

    Raises:
        ConfigError: If the value is not `Nh`, `Nm`, `Ns`, or a number of seconds.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return parse_duration(value)
    except ValueError as err:
        raise ConfigError(f"{name} must be a duration such as 8h, 30m, or 3600") from err


def _workload_bindings(raw: str) -> tuple[WorkloadBinding, ...]:
    """Parse the workload-identity allowlist.

    Args:
        raw: JSON list from `TESSERA_WORKLOAD_BINDINGS`. Blank means none.

    Returns:
        Bindings in list order. Issuers have no trailing slash.

    Raises:
        ConfigError: If the text is not a list of binding objects.
    """
    text = raw.strip()
    if not text:
        return ()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as err:
        raise ConfigError("TESSERA_WORKLOAD_BINDINGS must be a JSON list") from err
    if not isinstance(parsed, list):
        raise ConfigError("TESSERA_WORKLOAD_BINDINGS must be a JSON list")
    bindings: list[WorkloadBinding] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise ConfigError("TESSERA_WORKLOAD_BINDINGS must be a JSON list")
        try:
            binding = WorkloadBinding.model_validate(item)
        except ValidationError as err:
            raise ConfigError("TESSERA_WORKLOAD_BINDINGS must be a JSON list") from err
        bindings.append(binding.model_copy(update={"issuer": binding.issuer.rstrip("/")}))
    return tuple(bindings)


def _optional(name: str) -> str | None:
    """Read one optional string setting.

    Args:
        name: Environment variable name.

    Returns:
        The stripped value, or `None` when it is missing or blank.
    """
    value = os.environ.get(name, "").strip()
    return value or None


def _trimmed(name: str) -> str | None:
    """Read an optional URL and drop a trailing slash.

    Args:
        name: Environment variable name.

    Returns:
        The URL without a trailing slash, or `None` when it is blank.
    """
    value = _optional(name)
    if value is None:
        return None
    return value.rstrip("/")


def _setting(name: str, dev_default: str, *, dev: bool) -> str:
    """Read one required string setting.

    Args:
        name: Environment variable name.
        dev_default: Value used when `dev` is set and the variable is empty.
        dev: Whether development defaults apply.

    Returns:
        The configured value.

    Raises:
        ConfigError: If the variable is empty and development defaults are off.
    """
    value = os.environ.get(name, "").strip()
    if value:
        return value
    if dev:
        return dev_default
    raise ConfigError(f"{name} is required")


def _kek(*, dev: bool) -> bytes:
    """Load the 32-byte key-encryption key.

    Args:
        dev: Read or create `.tessera/server.kek` when the variable is unset.

    Returns:
        32 raw key bytes.

    Raises:
        ConfigError: If `TESSERA_SERVER_KEK` is missing or not valid.
    """
    raw = os.environ.get("TESSERA_SERVER_KEK", "").strip()
    if raw:
        return _decode_kek(raw)
    if not dev:
        raise ConfigError("TESSERA_SERVER_KEK is required")
    if _KEK_PATH.is_file():
        return _decode_kek(_KEK_PATH.read_text(encoding="utf-8"))
    key = secrets.token_bytes(_KEK_BYTES)
    _KEK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _KEK_PATH.write_text(base64.b64encode(key).decode("ascii"), encoding="utf-8")
    _KEK_PATH.chmod(0o600)
    return key


def _decode_kek(value: str) -> bytes:
    """Decode a base64 key-encryption key.

    Args:
        value: Base64 text, as stored in the environment or the key file.

    Returns:
        32 raw key bytes.

    Raises:
        ConfigError: If the text is not base64 of exactly 32 bytes.
    """
    try:
        key = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as err:
        raise ConfigError("TESSERA_SERVER_KEK must be base64 of 32 bytes") from err
    if len(key) != _KEK_BYTES:
        raise ConfigError("TESSERA_SERVER_KEK must be base64 of 32 bytes")
    return key


__all__ = ["Settings", "WorkloadBinding"]
