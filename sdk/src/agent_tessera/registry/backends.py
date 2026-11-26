"""Backends the `tessera agents` command uses to read and change a registry."""

from __future__ import annotations

import builtins
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

import httpx

from agent_tessera.errors import PolicyError, RegistryError
from agent_tessera.policy.loader import load_baseline_policy
from agent_tessera.registry.models import AgentStatus, RegistryEntry
from agent_tessera.registry.store import LocalRegistry
from agent_tessera.remote.http import _new_http_client


class RegistryBackend(Protocol):
    """Operations the agents command needs from a local file or the server.

    Example:
        ```python
        from agent_tessera.registry.backends import LocalBackend

        backend = LocalBackend(".tessera/registry.yaml")
        assert backend.list() == ()
        ```
    """

    def list(self) -> tuple[RegistryEntry, ...]:
        """Return every registered agent.

        Returns:
            The agents, in the backend's order.
        """
        ...

    def get(self, name: str, environment: str) -> RegistryEntry:
        """Return one agent.

        Args:
            name: Agent name.
            environment: Environment name.

        Returns:
            The stored entry.

        Raises:
            RegistryError: If the agent is not registered.
        """
        ...

    def register(self, entry: RegistryEntry) -> RegistryEntry:
        """Add an agent.

        Args:
            entry: Agent to add. A policy path may be relative to the process
                or to the registry file.

        Returns:
            The stored entry.
        """
        ...

    def update(self, name: str, environment: str, **changes: object) -> RegistryEntry:
        """Replace metadata fields.

        Args:
            name: Agent name.
            environment: Environment name.
            **changes: Fields to replace.

        Returns:
            The stored entry.
        """
        ...

    def set_policy(self, name: str, environment: str, policy: Path) -> RegistryEntry:
        """Point an agent at a policy file.

        Args:
            name: Agent name.
            environment: Environment name.
            policy: Policy file to store.

        Returns:
            The stored entry.
        """
        ...

    def set_status(self, name: str, environment: str, status: AgentStatus) -> RegistryEntry:
        """Move an agent to another lifecycle status.

        Args:
            name: Agent name.
            environment: Environment name.
            status: Status to store.

        Returns:
            The stored entry.
        """
        ...

    def put_baseline(self, name: str, path: Path) -> None:
        """Record a baseline.

        Args:
            name: Baseline name. It must match the file.
            path: Baseline file.
        """
        ...


class LocalBackend:
    """Registry stored in one YAML file.

    Args:
        path: Registry file. A missing file is an empty registry.

    Example:
        ```python
        from agent_tessera.registry.backends import LocalBackend
        from agent_tessera.registry import RegistryEntry

        backend = LocalBackend(".tessera/registry.yaml")
        backend.register(RegistryEntry(name="helper", owner="dev@localhost"))
        ```
    """

    def __init__(self, path: str | Path) -> None:
        """Open the file-backed registry.

        Args:
            path: Registry file.
        """
        self._registry = LocalRegistry(path)

    @property
    def path(self) -> Path:
        """Return the registry file path.

        Returns:
            The path passed to the constructor.
        """
        return self._registry.path

    def list(self) -> tuple[RegistryEntry, ...]:
        """Return every agent in the file.

        Returns:
            Agents sorted by environment, then name.
        """
        return self._registry.load().agents

    def get(self, name: str, environment: str) -> RegistryEntry:
        """Return one agent.

        Args:
            name: Agent name.
            environment: Environment name.

        Returns:
            The stored entry.

        Raises:
            RegistryError: If the agent is not registered.
        """
        found = self._registry.find(name, environment)
        if found is None:
            raise RegistryError(f"agent {name} ({environment}) is not registered")
        return found

    def register(self, entry: RegistryEntry) -> RegistryEntry:
        """Add an agent, storing its policy path relative to the file.

        Args:
            entry: Agent to add.

        Returns:
            The stored entry.
        """
        policy = entry.policy
        if policy is not None:
            entry = entry.model_copy(update={"policy": self._relative_policy(policy)})
        return self._registry.register(entry)

    def update(self, name: str, environment: str, **changes: object) -> RegistryEntry:
        """Replace metadata fields.

        Args:
            name: Agent name.
            environment: Environment name.
            **changes: Fields to replace. A `policy` string is made relative.

        Returns:
            The stored entry.
        """
        if isinstance(changes.get("policy"), str):
            changes["policy"] = self._relative_policy(str(changes["policy"]))
        return self._registry.update(name, environment, **changes)

    def set_policy(self, name: str, environment: str, policy: Path) -> RegistryEntry:
        """Point an agent at a policy file.

        Args:
            name: Agent name.
            environment: Environment name.
            policy: Policy file. The stored path is relative to the registry.

        Returns:
            The stored entry.
        """
        return self.update(name, environment, policy=self._relative_policy(str(policy)))

    def set_status(self, name: str, environment: str, status: AgentStatus) -> RegistryEntry:
        """Move an agent to another lifecycle status.

        Args:
            name: Agent name.
            environment: Environment name.
            status: Status to store.

        Returns:
            The stored entry.
        """
        return self._registry.set_status(name, environment, status)

    def put_baseline(self, name: str, path: Path) -> None:
        """Record a baseline path when it is not already listed.

        Args:
            name: Baseline name. It must equal the file's `metadata.name`.
            path: Baseline file. The stored path is relative to the registry.

        Raises:
            RegistryError: If the file is not a baseline of that name.
        """
        try:
            loaded = load_baseline_policy(path)
        except PolicyError as err:
            raise RegistryError(str(err)) from err
        if loaded.metadata.name != name:
            raise RegistryError(f"baseline {path} is named {loaded.metadata.name}")
        relative = _relative(self._registry.path.parent, path)
        with self._registry._exclusive():
            current = self._registry.load()
            if relative in current.baselines:
                return
            updated = current.model_copy(update={"baselines": (*current.baselines, relative)})
            self._registry._write(updated)

    def baselines(self) -> tuple[str, ...]:
        """Return the baseline paths stored in the file.

        Returns:
            Paths relative to the registry file.
        """
        return self._registry.load().baselines

    def stored_path(self, source: Path) -> str:
        """Return the path this backend would store for a file.

        Args:
            source: Policy or baseline file.

        Returns:
            A posix path relative to the registry file.
        """
        return _relative(self._registry.path.parent, source)

    def _relative_policy(self, policy: str) -> str:
        """Return a policy path relative to the registry file.

        Args:
            policy: Path given by the caller.

        Returns:
            A posix path relative to the registry directory when the file
            exists, otherwise the original text.
        """
        given = Path(policy)
        candidate = given if given.is_file() else self._registry.path.parent / given
        if not candidate.is_file():
            return policy
        return _relative(self._registry.path.parent, candidate)


class ServerBackend:
    """Registry stored by a control plane.

    Args:
        url: Server origin.
        admin_key: Admin API key sent as a bearer token.

    Example:
        ```python
        backend = ServerBackend("http://127.0.0.1:8700", "tsa_example")
        backend.close()
        ```
    """

    def __init__(self, url: str, admin_key: str) -> None:
        """Open an admin client.

        Args:
            url: Server origin.
            admin_key: Admin API key.
        """
        self._client = _new_http_client(url, timeout=10)
        self._client.headers["Authorization"] = f"Bearer {admin_key}"
        self._ids: dict[tuple[str, str], str] = {}

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def list(self) -> tuple[RegistryEntry, ...]:
        """Return every agent.

        Returns:
            Agents in the server's id order.
        """
        owners = self._owner_emails()
        entries: builtins.list[RegistryEntry] = []
        self._ids.clear()
        for item in self._items("/v1/agents"):
            agent_id = str(item["id"])
            detail = self._object("GET", f"/v1/agents/{agent_id}")
            entry = _server_entry(detail, owners)
            self._ids[(entry.name, entry.environment)] = agent_id
            entries.append(entry)
        return tuple(entries)

    def get(self, name: str, environment: str) -> RegistryEntry:
        """Return one agent.

        Args:
            name: Agent name.
            environment: Environment name.

        Returns:
            The stored entry.

        Raises:
            RegistryError: If the agent is not registered.
        """
        agent_id = self._id(name, environment)
        detail = self._object("GET", f"/v1/agents/{agent_id}")
        return _server_entry(detail, self._owner_emails())

    def register(self, entry: RegistryEntry) -> RegistryEntry:
        """Create an agent and upload its policy when one is set.

        Args:
            entry: Agent to add. `owner` is an email or a team id.

        Returns:
            The stored entry.
        """
        created = self._object(
            "POST",
            "/v1/agents",
            json_body={
                "name": entry.name,
                "environment": entry.environment,
                "owner_id": self._owner_id(entry.owner),
                "description": entry.description,
                "risk_tier": entry.risk_tier,
                "labels": dict(entry.labels),
                "autonomy": entry.autonomy,
                "supervisors": list(entry.supervisors),
            },
        )
        self._ids[(entry.name, entry.environment)] = str(created["id"])
        if entry.policy is not None:
            self.set_policy(entry.name, entry.environment, Path(entry.policy))
        return self.get(entry.name, entry.environment)

    def update(self, name: str, environment: str, **changes: object) -> RegistryEntry:
        """Replace metadata fields.

        Args:
            name: Agent name.
            environment: Environment name.
            **changes: Fields to replace. `owner` is an email or a team id.

        Returns:
            The stored entry.
        """
        body: dict[str, object] = {}
        if "owner" in changes:
            body["owner_id"] = self._owner_id(str(changes["owner"]))
        for field in ("description", "risk_tier", "labels", "autonomy"):
            if field in changes:
                body[field] = changes[field]
        if "supervisors" in changes:
            value = changes["supervisors"]
            body["supervisors"] = list(value) if isinstance(value, list | tuple) else value
        if body:
            self._object("PATCH", f"/v1/agents/{self._id(name, environment)}", json_body=body)
        if isinstance(changes.get("policy"), str):
            self.set_policy(name, environment, Path(str(changes["policy"])))
        return self.get(name, environment)

    def set_policy(self, name: str, environment: str, policy: Path) -> RegistryEntry:
        """Upload a policy file.

        Args:
            name: Agent name.
            environment: Environment name.
            policy: Policy file to upload.

        Returns:
            The stored entry.
        """
        text = policy.read_text(encoding="utf-8")
        self._send(
            "PUT",
            f"/v1/agents/{self._id(name, environment)}/policy",
            content=text.encode(),
            content_type="application/yaml",
        )
        return self.get(name, environment)

    def set_status(self, name: str, environment: str, status: AgentStatus) -> RegistryEntry:
        """Call the lifecycle endpoint for `status`.

        Args:
            name: Agent name.
            environment: Environment name.
            status: Status to store.

        Returns:
            The stored entry.
        """
        action = {"active": "activate", "suspended": "suspend", "retired": "retire"}[status]
        self._object("POST", f"/v1/agents/{self._id(name, environment)}:{action}")
        return self.get(name, environment)

    def put_baseline(self, name: str, path: Path) -> None:
        """Upload a baseline when the stored YAML differs.

        Args:
            name: Baseline name.
            path: Baseline file.
        """
        text = path.read_text(encoding="utf-8")
        current = self._send("GET", f"/v1/baselines/{name}", allow_not_found=True)
        if current.status_code != httpx.codes.NOT_FOUND and current.text == text:
            return
        self._send(
            "PUT",
            f"/v1/baselines/{name}",
            content=text.encode(),
            content_type="application/yaml",
        )

    def policy_bytes(self, name: str, environment: str) -> bytes | None:
        """Return the YAML stored for an agent.

        Args:
            name: Agent name.
            environment: Environment name.

        Returns:
            The policy body, or `None` when the agent has no policy.
        """
        response = self._send(
            "GET",
            f"/v1/agents/{self._id(name, environment)}/policy",
            allow_not_found=True,
        )
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        return response.content

    def baseline_bytes(self, name: str) -> bytes | None:
        """Return the YAML stored for a baseline.

        Args:
            name: Baseline name.

        Returns:
            The baseline body, or `None` when it is not stored.
        """
        response = self._send("GET", f"/v1/baselines/{name}", allow_not_found=True)
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        return response.content

    def issue_token(
        self,
        name: str,
        environment: str,
        *,
        reusable: bool,
        expires_in: int | None,
    ) -> str:
        """Create a registration token.

        Args:
            name: Agent name.
            environment: Environment name.
            reusable: Whether the token can be used more than once.
            expires_in: Lifetime in seconds, or `None` for no expiry.

        Returns:
            The token, shown once.
        """
        body: dict[str, object] = {"reusable": reusable}
        if expires_in is not None:
            body["expires_in"] = expires_in
        created = self._object(
            "POST",
            f"/v1/agents/{self._id(name, environment)}/registration-tokens",
            json_body=body,
        )
        return str(created["token"])

    def _owner_id(self, owner: str) -> str:
        """Return the id of an owner, creating it when the email is new.

        Args:
            owner: Email, or a team id with no `@`.

        Returns:
            The owner id.
        """
        email = owner if "@" in owner else f"{owner}@teams.local"
        found = self._items("/v1/owners", {"email": email})
        if found:
            return str(found[0]["id"])
        kind = "user" if "@" in owner else "team"
        created = self._object(
            "POST",
            "/v1/owners",
            json_body={"type": kind, "email": email, "display_name": owner},
        )
        return str(created["id"])

    def _owner_emails(self) -> dict[str, str]:
        """Return owner ids mapped to emails.

        Returns:
            Every owner's id and email.
        """
        return {str(item["id"]): str(item["email"]) for item in self._items("/v1/owners")}

    def _id(self, name: str, environment: str) -> str:
        """Return the server id of one agent.

        Args:
            name: Agent name.
            environment: Environment name.

        Returns:
            The agent id.

        Raises:
            RegistryError: If the agent is not registered.
        """
        key = (name, environment)
        if key not in self._ids:
            self.list()
        try:
            return self._ids[key]
        except KeyError:
            raise RegistryError(f"agent {name} ({environment}) is not registered") from None

    def _items(
        self, path: str, params: Mapping[str, str] | None = None
    ) -> builtins.list[dict[str, object]]:
        """Read every page of a list endpoint.

        Args:
            path: Request path.
            params: Extra query parameters.

        Returns:
            The documents in `items`.
        """
        items: builtins.list[dict[str, object]] = []
        cursor: str | None = None
        while True:
            query = {"limit": "500", **dict(params or {})}
            if cursor is not None:
                query["cursor"] = cursor
            page = self._object("GET", path, params=query)
            raw = page.get("items")
            if isinstance(raw, list):
                items.extend(item for item in raw if isinstance(item, dict))
            nxt = page.get("next_cursor")
            if not isinstance(nxt, str) or nxt == "":
                return items
            cursor = nxt

    def _object(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Send a JSON request and return the object body.

        Args:
            method: HTTP method.
            path: Request path.
            params: Query parameters.
            json_body: JSON body.

        Returns:
            The response object.
        """
        response = self._send(method, path, params=params, json_body=json_body)
        body = response.json()
        if not isinstance(body, dict):
            raise RegistryError(f"server error {response.status_code}: error: expected an object")
        return cast(dict[str, object], body)

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: Mapping[str, object] | None = None,
        content: bytes | None = None,
        content_type: str | None = None,
        allow_not_found: bool = False,
    ) -> httpx.Response:
        """Send one request.

        Args:
            method: HTTP method.
            path: Request path.
            params: Query parameters.
            json_body: JSON body.
            content: Raw body.
            content_type: Content type for a raw body.
            allow_not_found: Return a 404 response instead of raising.

        Returns:
            The response.

        Raises:
            RegistryError: If the server cannot be reached or rejects the call.
        """
        headers = {"Content-Type": content_type} if content_type is not None else None
        try:
            response = self._client.request(
                method,
                path,
                params=params,
                json=json_body,
                content=content,
                headers=headers,
            )
        except httpx.HTTPError as err:
            raise RegistryError("server error 0: unreachable: server is unreachable") from err
        if response.status_code == httpx.codes.NOT_FOUND and allow_not_found:
            return response
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise _server_error(response) from None
        return response


def open_server_backend(url: str) -> RegistryBackend:
    """Open the control-plane backend.

    Args:
        url: Server origin.

    Returns:
        A backend that calls the admin API.

    Raises:
        ConfigError: If no admin key is available from the environment or a
            saved `tessera login` profile.
    """
    from agent_tessera.credentials import resolve_admin_key  # noqa: PLC0415

    return ServerBackend(url, resolve_admin_key())


def _server_entry(detail: Mapping[str, object], owners: Mapping[str, str]) -> RegistryEntry:
    """Build a registry entry from an agent detail document.

    Args:
        detail: `GET /v1/agents/{id}` body.
        owners: Owner ids mapped to emails.

    Returns:
        The entry. `policy` is `sha256:` plus the first 12 hex digits of the
        effective version. `registered_at` is `created_at`.
    """
    version = detail.get("policy_version")
    policy = None
    if isinstance(version, str) and version:
        policy = f"sha256:{version.removeprefix('sha256:')[:12]}"
    owner_id = str(detail.get("owner_id", ""))
    labels = detail.get("labels")
    supervisors = detail.get("supervisors")
    return RegistryEntry.model_validate(
        {
            "name": detail["name"],
            "environment": detail["environment"],
            "owner": owners.get(owner_id, owner_id),
            "status": detail["status"],
            "policy": policy,
            "description": detail.get("description") or "",
            "risk_tier": detail.get("risk_tier") or "low",
            "labels": labels if isinstance(labels, dict) else {},
            "autonomy": detail.get("autonomy") or "interactive",
            "supervisors": list(supervisors) if isinstance(supervisors, list) else [],
            "registered_at": detail.get("created_at"),
            "updated_at": detail.get("last_seen_at"),
        }
    )


def _server_error(response: httpx.Response) -> RegistryError:
    """Build the error for a rejected response.

    Args:
        response: Response whose status is 400 or higher.

    Returns:
        `server error <status>: <code>: <message>`.
    """
    code = "error"
    message = response.text.strip() or "request failed"
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = str(error.get("code", code))
            message = str(error.get("message", message))
    return RegistryError(f"server error {response.status_code}: {code}: {message}")


def _relative(base: Path, path: Path) -> str:
    """Return `path` relative to `base` using forward slashes.

    Args:
        base: Directory the result is relative to.
        path: File to name.

    Returns:
        A posix relative path.
    """
    return Path(os.path.relpath(path, base)).as_posix()


__all__ = ["LocalBackend", "RegistryBackend", "ServerBackend", "open_server_backend"]
