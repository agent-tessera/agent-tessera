"""Startup, credential refresh, and bundle polling for server mode."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx

from agent_tessera.errors import ConfigError, ServerError
from agent_tessera.policy.effective import EffectivePolicy
from agent_tessera.remote.bundles import (
    CachedBundle,
    fetch_bundle,
    fetch_jwks,
    read_cache,
    write_cache,
)
from agent_tessera.remote.http import RemoteError, _new_http_client, request
from agent_tessera.remote.identity import RuntimeCredential, bootstrap, refresh, server_is_dev
from agent_tessera.remote.oidc_user import exchange_for_user

logger = logging.getLogger("agent_tessera")

POLL_INTERVAL = 30.0
REFRESH_FRACTION = 0.7


@dataclass(frozen=True)
class ServerSettings:
    """Resolved server-mode settings. An argument wins over the environment.

    Attributes:
        url: Control-plane origin, without a trailing slash.
        agent_token: Registration token, or `None` when dev mode may issue one.
        fail_mode: `open` or `closed`.
        cache_dir: Directory for the bundle and JWKS cache.
        timeout: Connect and read timeout in seconds.
        spool_dir: Directory for events that could not be uploaded.
        bound_supervisor: Supervisor sent on the token request, or `None`.
    """

    url: str
    agent_token: str | None
    fail_mode: str
    cache_dir: Path
    timeout: float
    spool_dir: Path
    bound_supervisor: str | None = None


def resolve_server(
    server_url: str | None, agent_token: str | None, fail_mode: str | None
) -> ServerSettings | None:
    """Resolve server settings from arguments and the environment.

    Args:
        server_url: Explicit URL, or `None` to read `TESSERA_URL`.
        agent_token: Explicit registration token, or `None` to read
            `TESSERA_AGENT_TOKEN`.
        fail_mode: Explicit fail mode, or `None` to read `TESSERA_FAIL_MODE`.

    Returns:
        The settings, or `None` when no server URL is configured.
    """
    url = _text(server_url, "TESSERA_URL")
    if url is None:
        return None
    token = _text(agent_token, "TESSERA_AGENT_TOKEN")
    return ServerSettings(
        url.rstrip("/"),
        token,
        _fail_mode(fail_mode),
        _cache_dir(),
        _timeout(),
        _spool_dir(),
    )


class ServerConnection:
    """Keep a runtime credential and a verified policy bundle.

    Args:
        agent: Agent slug the caller asked to govern.
        instance_id: This process's instance id.
        settings: Resolved server settings.
        on_policy: Called with a new effective policy and the bundle status.
        on_unusable: Called when the bundle expires and cannot be replaced.
        on_rejected: Called when a refresh is rejected.
    """

    def __init__(
        self,
        agent: str,
        instance_id: str,
        settings: ServerSettings,
        *,
        on_policy: Callable[[EffectivePolicy, str], None],
        on_unusable: Callable[[], None],
        on_rejected: Callable[[], None],
    ) -> None:
        """Store the settings. `start` contacts the server.

        Args:
            agent: Agent slug.
            instance_id: This process's instance id.
            settings: Resolved server settings.
            on_policy: Called when a bundle is installed.
            on_unusable: Called when an expired bundle cannot be replaced.
            on_rejected: Called when refresh is rejected.
        """
        self.agent = agent
        self._instance_id = instance_id
        self.settings = settings
        self._on_policy = on_policy
        self._on_unusable = on_unusable
        self._on_rejected = on_rejected
        self.environment = ""
        self._http = _new_http_client(settings.url, settings.timeout)
        self._credential: RuntimeCredential | None = None
        self._bundle: CachedBundle | None = None
        self._jwks = ""
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._delegated = 0

    def start(self) -> EffectivePolicy | None:
        """Fetch a credential and a bundle, using the cache when the server is down.

        Returns:
            The effective policy, or `None` when fail-open starts without one.

        Raises:
            ConfigError: If the token names another agent, or a token is
                required and the server is not in dev mode.
            ServerError: If the server rejects the client, or fail-closed has
                no usable bundle.
        """
        try:
            self._credential = self._bootstrap()
            if self._credential.agent != self.agent:
                raise ConfigError(
                    f"agent {self.agent} does not match server agent {self._credential.agent}"
                )
            self.environment = self._credential.environment
            self._jwks = fetch_jwks(self._http)
            bundle = fetch_bundle(
                self._http,
                access_token=self._credential.access_token,
                agent_id=self._credential.agent_id,
                jwks_text=self._jwks,
                issuer=self.settings.url,
            )
            if bundle is None:
                raise ServerError("server returned no bundle")
            self._install(bundle)
        except RemoteError:
            cached = read_cache(self.settings.cache_dir, self.agent, self.settings.url)
            if cached is None:
                self._without_bundle()
            else:
                self._install(cached)
        self._spawn(self._refresh_loop)
        self._spawn(self._poll_loop)
        return None if self._bundle is None else self._bundle.policy

    def access_token(self) -> str | None:
        """Return the current runtime JWT.

        Returns:
            The bearer token, or `None` when the client has not registered.
        """
        with self._lock:
            credential = self._credential
        if credential is None:
            return None
        return credential.access_token

    @contextmanager
    def _use_user_token(self, user_token: str) -> Iterator[None]:
        """Exchange a user token and use the result until the block exits.

        Refresh does not replace the credential while the block is active.
        The previous runtime JWT is restored on exit.

        Args:
            user_token: User access or ID token. It is the subject of the
                exchange, not the actor.

        Yields:
            Control while the delegated credential is current.

        Raises:
            ServerError: If there is no runtime JWT or the server rejects the
                exchange.
        """
        with self._lock:
            self._delegated += 1
            current = self._credential
        actor = None if current is None else current.access_token
        try:
            if actor is None:
                raise ServerError("runtime token is missing")
            issued = exchange_for_user(self._http, user_token=user_token, actor_token=actor)
            with self._lock:
                previous = self._credential
                self._credential = issued
            try:
                yield
            finally:
                with self._lock:
                    self._credential = previous
        finally:
            with self._lock:
                self._delegated -= 1

    def send(self, method: str, path: str, body: object | None = None) -> httpx.Response:
        """Send one request with the current runtime token.

        Args:
            method: HTTP method.
            path: Path on the control plane.
            body: JSON body, or `None` for no body.

        Returns:
            The response, including a 4xx response.

        Raises:
            RemoteError: If there is no runtime token, the server cannot be
                reached, or the server returns 5xx.

        Example:
            ```python
            response = connection.send("GET", "/v1/approvals/01H")
            assert response.status_code in {200, 404}
            ```
        """
        token = self.access_token()
        if token is None:
            raise RemoteError(None, "runtime token is missing")
        return request(
            self._http,
            method,
            path,
            json_body=body,
            headers={"Authorization": f"Bearer {token}"},
        )

    def refresh_bundle(self) -> None:
        """Fetch the current bundle without waiting for the next poll."""
        self._poll_once()

    def stop(self) -> None:
        """Stop the refresh and bundle threads. The HTTP client stays open."""
        self._stop.set()
        threads = self._threads
        self._threads = []
        for thread in threads:
            thread.join(timeout=1)

    def close(self) -> None:
        """Stop the background threads and close the HTTP client."""
        self.stop()
        self._http.close()

    def _bootstrap(self) -> RuntimeCredential:
        """Obtain the first runtime credential.

        Returns:
            The issued credential.

        Raises:
            ConfigError: If no registration token was configured and the server
                is not in dev mode.
            ServerError: If the server rejects the grant.
            RemoteError: If the server cannot be reached.
        """
        if self.settings.agent_token is None and not server_is_dev(self._http):
            raise ConfigError("agent_token is required")
        return bootstrap(
            self._http,
            agent=self.agent,
            instance_id=self._instance_id,
            agent_token=self.settings.agent_token,
            bound_supervisor=self.settings.bound_supervisor,
        )

    def _install(self, bundle: CachedBundle) -> None:
        """Remember a bundle, cache it, and publish the policy.

        Args:
            bundle: Verified bundle.
        """
        with self._lock:
            self._bundle = bundle
            if not self._jwks:
                self._jwks = bundle.jwks_text
        write_cache(self.settings.cache_dir, self.agent, bundle)
        self._on_policy(bundle.policy, bundle.claims.status)

    def _without_bundle(self) -> None:
        """Start fail-open, or raise when the client fails closed.

        Returns:
            `None` after logging the fail-open warning.

        Raises:
            ServerError: When the fail mode is closed.
        """
        if self.settings.fail_mode == "closed":
            raise ServerError("server is unreachable")
        logger.warning("server is unreachable; allowing actions until a policy is available")

    def _refresh_loop(self) -> None:
        """Refresh the runtime credential until the connection closes."""
        while not self._stop.is_set():
            delay = self._refresh_delay()
            if self._stop.wait(delay):
                return
            self._refresh_once()

    def _poll_loop(self) -> None:
        """Poll for a new bundle until the connection closes."""
        while not self._stop.wait(POLL_INTERVAL):
            self._poll_once()

    def _refresh_once(self) -> None:
        """Refresh once. A rejection suspends the client.

        A delegated credential is left in place until `on_behalf_of` exits.
        """
        with self._lock:
            if self._delegated:
                return
            credential = self._credential
        if credential is None:
            return
        try:
            renewed = refresh(self._http, credential.access_token)
        except ServerError:
            self._on_rejected()
            self._stop.set()
            return
        except RemoteError:
            return
        self._store_refresh(renewed)

    def _store_refresh(self, renewed: RuntimeCredential) -> None:
        """Keep a refresh unless a delegation started while it was in flight.

        Args:
            renewed: Credential returned by the refresh grant.
        """
        with self._lock:
            if self._delegated:
                return
            self._credential = renewed

    def _poll_once(self) -> None:
        """Fetch a bundle when the server has a newer one."""
        if self._credential is None:
            try:
                self._credential = self._bootstrap()
            except (ConfigError, RemoteError, ServerError):
                self._mark_if_expired()
                return
        credential = self._credential
        bundle = self._bundle
        if credential is None or not self._jwks:
            self._mark_if_expired()
            return
        etag = None if bundle is None or self._expired(bundle) else bundle.etag
        try:
            fresh = fetch_bundle(
                self._http,
                access_token=credential.access_token,
                agent_id=credential.agent_id,
                jwks_text=self._jwks,
                issuer=self.settings.url,
                etag=etag,
            )
        except (ServerError, RemoteError):
            self._mark_if_expired()
            return
        if fresh is not None:
            self._install(fresh)

    def _mark_if_expired(self) -> None:
        """Report that the current bundle can no longer be used."""
        bundle = self._bundle
        if bundle is not None and self._expired(bundle):
            self._on_unusable()

    def _expired(self, bundle: CachedBundle) -> bool:
        """Return whether a bundle's expiry has passed.

        Args:
            bundle: Cached bundle.

        Returns:
            `True` when `exp` is in the past.
        """
        return int(self._clock()) >= bundle.claims.exp

    def _clock(self) -> float:
        """Return the current Unix time.

        Returns:
            Seconds since the epoch.
        """
        return time.time()

    def _refresh_delay(self) -> float:
        """Return how long to wait before the next refresh.

        Returns:
            70% of the credential lifetime, or one second when it is unknown.
        """
        credential = self._credential
        if credential is None:
            return 1.0
        return max(0.05, credential.expires_in * REFRESH_FRACTION)

    def _spawn(self, target: Callable[[], None]) -> None:
        """Start one daemon thread.

        Args:
            target: Loop to run.
        """
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        self._threads.append(thread)


def _text(argument: str | None, env_name: str) -> str | None:
    """Prefer an argument over an environment variable.

    Args:
        argument: Explicit value.
        env_name: Environment variable.

    Returns:
        The value, or `None` when neither is set.
    """
    if argument is not None:
        return argument
    value = os.environ.get(env_name, "")
    return value or None


def _fail_mode(argument: str | None) -> str:
    """Resolve `open` or `closed`.

    Args:
        argument: Explicit mode.

    Returns:
        The fail mode. An unknown environment value uses the default, which is
        `open` when `TESSERA_ENV` is `dev`.
    """
    if argument is not None:
        return argument
    value = os.environ.get("TESSERA_FAIL_MODE", "")
    if value in {"open", "closed"}:
        return value
    if os.environ.get("TESSERA_ENV") == "dev":
        return "open"
    return "closed"


def _cache_dir() -> Path:
    """Resolve the bundle cache directory.

    Returns:
        `TESSERA_CACHE_DIR` or `.tessera/cache`.
    """
    return Path(os.environ.get("TESSERA_CACHE_DIR") or ".tessera/cache")


def _spool_dir() -> Path:
    """Resolve the event spool directory.

    Returns:
        `TESSERA_SPOOL_DIR` or `.tessera/spool`.
    """
    return Path(os.environ.get("TESSERA_SPOOL_DIR") or ".tessera/spool")


def _timeout() -> float:
    """Resolve the connect timeout.

    Returns:
        `TESSERA_CONNECT_TIMEOUT` in seconds, or 10.
    """
    raw = os.environ.get("TESSERA_CONNECT_TIMEOUT", "")
    if not raw:
        return 10.0
    try:
        return float(raw)
    except ValueError:
        return 10.0


__all__ = [
    "POLL_INTERVAL",
    "REFRESH_FRACTION",
    "ServerConnection",
    "ServerSettings",
    "resolve_server",
]
