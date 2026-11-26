"""Seed a running Tessera server for console Playwright via the public API.

Uses admin HTTP endpoints plus a registration-token exchange and
``POST /v1/events`` shaped with ``agent_tessera.remote.events.remote_event``
(the same path the SDK uses offline). No LLM keys.

Example::

    tessera server dev   # prints an admin key
    uv run python web/tests/e2e/seed.py \\
      --base-url http://127.0.0.1:8700 \\
      --admin-key "$TESSERA_ADMIN_KEY" \\
      --out web/e2e/.seed.json

Playwright's default harness still boots ``web/e2e/serve.py`` (temp DB +
rich spike fixtures). This script seeds an already-running server for local
``tessera server`` workflows and CI documentation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from agent_tessera.remote.events import remote_event

_AGENT_NAME = "console-seed"
_ENVIRONMENT = "prod"
_INSTANCE_ID = "cs" * 16
_RUN_ID = "01JE2E2EAPISEED00000000000"
_SPAN_ID = "c0n501e5eed00001"
_POLICY = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: console-seed
  environment: prod
spec:
  limits:
    tokens_per_day: 10000
  models:
    allow: ["gpt-4o-mini"]
  tools:
    allow: ["files.read", "files.write"]
    deny: ["shell.exec"]
"""


def _ensure_owner(
    client: httpx.Client,
    headers: dict[str, str],
    *,
    owner_type: str,
    email: str,
    display_name: str,
) -> str:
    """Create an owner or return the existing id for that email.

    Args:
        client: HTTP client bound to the control plane.
        headers: Admin authorization headers.
        owner_type: ``user`` or ``team``.
        email: Owner email.
        display_name: Display name.

    Returns:
        Owner id.

    Raises:
        httpx.HTTPError: When an API call fails.
        RuntimeError: When a conflict cannot be resolved by email lookup.
    """
    created = client.post(
        "/v1/owners",
        headers=headers,
        json={
            "type": owner_type,
            "email": email,
            "display_name": display_name,
        },
    )
    if created.status_code == 409:
        listed = client.get(
            "/v1/owners",
            headers=headers,
            params={"email": email},
        )
        listed.raise_for_status()
        items = listed.json().get("items") or []
        if not items:
            raise RuntimeError("owner conflict but email lookup was empty")
        return str(items[0]["id"])
    created.raise_for_status()
    return str(created.json()["id"])


def seed_server(
    base_url: str,
    admin_key: str,
    *,
    agent_name: str = _AGENT_NAME,
    environment: str = _ENVIRONMENT,
) -> dict[str, Any]:
    """Provision an active agent and ingest one decision event.

    Args:
        base_url: Control-plane origin (for example ``http://127.0.0.1:8700``).
        admin_key: Admin bearer key (``tsa_...``).
        agent_name: Agent name to create.
        environment: Agent environment.

    Returns:
        Seed metadata suitable for Playwright (ids and names).

    Raises:
        httpx.HTTPError: When an API call fails.
        RuntimeError: When a response is missing required fields.
    """
    origin = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {admin_key}"}
    with httpx.Client(base_url=origin, timeout=30.0) as client:
        me = client.get("/v1/me", headers=headers)
        me.raise_for_status()

        owner_id = _ensure_owner(
            client,
            headers,
            owner_type="user",
            email="console-seed@example.com",
            display_name="console-seed",
        )
        _ensure_owner(
            client,
            headers,
            owner_type="team",
            email="console-seed-team@teams.local",
            display_name="console-seed-team",
        )

        created = client.post(
            "/v1/agents",
            headers=headers,
            json={
                "name": agent_name,
                "environment": environment,
                "owner_id": owner_id,
            },
        )
        if created.status_code == 409:
            agents = client.get(
                "/v1/agents",
                headers=headers,
                params={"environment": environment, "limit": 100},
            )
            agents.raise_for_status()
            match = next(
                (
                    item
                    for item in (agents.json().get("items") or [])
                    if item.get("name") == agent_name and item.get("environment") == environment
                ),
                None,
            )
            if match is None:
                raise RuntimeError(f"agent conflict but {agent_name!r} not found")
            agent_id = str(match["id"])
        else:
            created.raise_for_status()
            agent_id = str(created.json()["id"])

        policy = client.put(
            f"/v1/agents/{agent_id}/policy",
            headers={**headers, "Content-Type": "application/yaml"},
            content=_POLICY.replace("console-seed", agent_name, 1),
        )
        policy.raise_for_status()

        activate = client.post(f"/v1/agents/{agent_id}:activate", headers=headers)
        if activate.status_code not in {200, 409}:
            activate.raise_for_status()

        token_row = client.post(
            f"/v1/agents/{agent_id}/registration-tokens",
            headers=headers,
            json={"reusable": True},
        )
        token_row.raise_for_status()
        registration_token = str(token_row.json()["token"])

        exchanged = client.post(
            "/v1/token",
            json={
                "grant_type": "registration",
                "registration_token": registration_token,
                "instance_id": _INSTANCE_ID,
                "host": "e2e-seed",
                "runtime": {"sdk": "0.6.1", "source": "web/tests/e2e/seed.py"},
            },
        )
        exchanged.raise_for_status()
        access_token = str(exchanged.json()["access_token"])

        allow_event = remote_event(
            "decision",
            {
                "task": _RUN_ID,
                "run": _RUN_ID,
                "span_id": _SPAN_ID,
                "kind": "tool",
                "resource": "files.read",
                "effect": "allow",
                "status": "ok",
                "rule": "agent:tools.allow[0]",
                "agent": agent_name,
            },
            None,
        )
        # Stable id for Playwright deep links.
        allow_event["id"] = "c0" * 16
        deny_event = remote_event(
            "decision",
            {
                "task": _RUN_ID,
                "run": _RUN_ID,
                "span_id": "d1" * 8,
                "kind": "tool",
                "resource": "files.delete",
                "effect": "deny",
                "status": "ok",
                "rule": "default",
                "agent": agent_name,
            },
            None,
        )
        deny_event["id"] = "d1" * 16
        uploaded = client.post(
            "/v1/events",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"events": [allow_event, deny_event]},
        )
        uploaded.raise_for_status()

        # Fleet activity: suspend then re-activate.
        suspend = client.post(f"/v1/agents/{agent_id}:suspend", headers=headers)
        if suspend.status_code not in {200, 409}:
            suspend.raise_for_status()
        resume = client.post(f"/v1/agents/{agent_id}:activate", headers=headers)
        if resume.status_code not in {200, 409}:
            resume.raise_for_status()

    return {
        "mode": "api",
        "agent_name": agent_name,
        "agent_id": agent_id,
        "owner_id": owner_id,
        "environment": environment,
        "run_id": _RUN_ID,
        "span_id": _SPAN_ID,
        "event_id": str(allow_event["id"]),
        "instance_id": _INSTANCE_ID,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry: seed a running server and write a JSON manifest."""
    parser = argparse.ArgumentParser(
        description="Seed a Tessera server for console e2e via the API.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("TESSERA_URL") or "http://127.0.0.1:8700",
        help="Control-plane origin (default: TESSERA_URL or http://127.0.0.1:8700).",
    )
    parser.add_argument(
        "--admin-key",
        default=os.environ.get("TESSERA_ADMIN_KEY") or "",
        help="Admin key (default: TESSERA_ADMIN_KEY).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("web/e2e/.seed.api.json"),
        help="Where to write the seed manifest JSON.",
    )
    parser.add_argument(
        "--agent-name",
        default=_AGENT_NAME,
        help=f"Agent name to create (default: {_AGENT_NAME}).",
    )
    parser.add_argument(
        "--environment",
        default=_ENVIRONMENT,
        help=f"Agent environment (default: {_ENVIRONMENT}).",
    )
    args = parser.parse_args(argv)
    if not args.admin_key:
        print(
            "admin key required (--admin-key or TESSERA_ADMIN_KEY)",
            file=sys.stderr,
        )
        return 2

    try:
        seed = seed_server(
            args.base_url,
            args.admin_key,
            agent_name=args.agent_name,
            environment=args.environment,
        )
    except (httpx.HTTPError, RuntimeError, OSError, ValueError, KeyError) as err:
        print(f"seed failed: {err}", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(seed, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(seed, indent=2))
    print(f"wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
