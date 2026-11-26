"""Serve a non-dev Tessera instance with a known admin key for Playwright.

Writes the admin key to ``e2e/.admin-key.<port>`` (and legacy ``.admin-key``
on the default port). Seeds depend on ``TESSERA_E2E_SEED``:

* ``spike`` (default): palette-probe agent plus a denial-spike agent for
  the palette and overview tests, a two-agent chain run (waterfall,
  unparented events, effect ledger, ``trace_id``), a pending approval on
  that chain run's waiting span, a dedicated running run for console
  revoke, a retired agent with a live instance (lifecycle hidden + instance
  revoke), a stored policy on palette-probe that denies ``shell.exec`` for
  the policy dry-run test, and a background live-tail producer that inserts
  ``live.tail`` events into the spike agent's
  stream. Set ``TESSERA_SERVER_TRACE_URL`` for the external tracing-tool
  link.
* ``empty``: admin key only (0 agents) for Overview first-run. Runs with
  ``dev=True`` so Playwright can finish the welcome stepper via
  ``POST /v1/token`` ``grant_type=dev``.

Binds ``127.0.0.1:$TESSERA_E2E_PORT`` (default 18700) and uses a temporary
SQLite database. Requires a built console at
``server/src/tessera_server/web/dist``.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from sqlalchemy.orm import Session

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import upgrade_database
from tessera_server.db.models import (
    Agent,
    Approval,
    Event,
    Instance,
    Owner,
    Revocation,
    Run,
    RunEffect,
    RunParticipant,
    TokenCounter,
)
from tessera_server.db.session import make_engine, session_factory
from tessera_server.ids import new_ulid
from tessera_server.services.audit import append_audit
from tessera_server.services.policies import put_agent_policy
from tessera_server.services.secrets import create_admin_key, parse_secret

_HOST = "127.0.0.1"
# Dedicated e2e port so a local `tessera server dev` on :8700 is left alone.
_DEFAULT_PORT = 18700
_PORT = int((os.environ.get("TESSERA_E2E_PORT") or str(_DEFAULT_PORT)).strip())
_SEED_MODE = (os.environ.get("TESSERA_E2E_SEED") or "spike").strip().lower()
_E2E_DIR = Path(__file__).resolve().parent
_KEY_FILE = _E2E_DIR / f".admin-key.{_PORT}"
_SEED_FILE = _E2E_DIR / f".seed.{_PORT}.json"
_LEGACY_KEY = _E2E_DIR / ".admin-key"
_LEGACY_SEED = _E2E_DIR / ".seed.json"

_AGENT_NAME = "palette-probe"
_SPIKE_NAME = "denial-spike"
_BETA_NAME = "beta-worker"
_RETIRED_NAME = "retired-shell"
_RETIRED_INSTANCE_ID = "re" * 16
_POLICY_DENY_TOOL = "shell.exec"
_POLICY_DENY_RULE = "agent:tools.deny[0]"
_RUN_ID = "01JE2E2ESEED00000000000000"
_SPIKE_RUN_ID = "01JE2E2ESPIKE0000000000000"
_LIVE_RUN_ID = "01JE2E2ELIVE00000000000000"
_CHAIN_RUN_ID = "01JE2E2ECHAIN000000000000"
_REVOKE_RUN_ID = "01JE2E2EREVOKE00000000000"
_SPAN_ID = "abcdef0123456789"
_CHAIN_ROOT_SPAN = "1111111111111111"
_CHAIN_DENY_SPAN = "2222222222222222"
_CHAIN_APPROVAL_SPAN = "3333333333333333"
_CHAIN_ERROR_SPAN = "4444444444444444"
_CHAIN_TRACE_ID = "0af0af0af0af0af0af0af0af0af0af0a"
_PENDING_APPROVAL_ID = "01JE2E2EAPPROVE0000000001"
_PALETTE_NOW = datetime(2026, 11, 9, 19, 0, tzinfo=UTC)
# Docs screenshots need a recent run and an unexpired approval; e2e specs
# keep the fixed date.
_CHAIN_START = (
    datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=45)
    if os.environ.get("DOCS_SCREENSHOTS") == "1"
    else datetime(2026, 11, 9, 18, 0, tzinfo=UTC)
)
_LIVE_RESOURCE = "live.tail"
_LIVE_INTERVAL_S = 0.35


def main() -> int:
    """Migrate a temp DB, mint an admin key, seed fixtures, and serve."""
    if _SEED_MODE not in {"spike", "empty"}:
        print(
            f"unknown TESSERA_E2E_SEED={_SEED_MODE!r}; use spike|empty",
            file=sys.stderr,
        )
        return 1

    dist = (
        Path(__file__).resolve().parents[2]
        / "server"
        / "src"
        / "tessera_server"
        / "web"
        / "dist"
        / "index.html"
    )
    if not dist.is_file():
        print(
            "console build missing; run `npm --prefix web run build` first",
            file=sys.stderr,
        )
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="tessera-e2e-"))
    database_url = f"sqlite:///{tmp / 'server.db'}"
    kek = base64.b64encode(b"k" * 32).decode("ascii")
    trace_url = (os.environ.get("TESSERA_SERVER_TRACE_URL") or "").strip() or None
    # Empty seed enables dev grants for the welcome stepper test.
    settings = Settings(
        database_url=database_url,
        issuer=f"http://{_HOST}:{_PORT}",
        org="default",
        kek=base64.b64decode(kek),
        dev=_SEED_MODE == "empty",
        trace_url=trace_url,
    )
    upgrade_database(database_url)
    engine = make_engine(database_url)
    seed: dict[str, object] = {"mode": _SEED_MODE}
    try:
        with session_factory(engine)() as session:
            token = create_admin_key(session, "e2e")
            parsed = parse_secret(token, prefix="tsa")
            key_id = "" if parsed is None else parsed[0]
            append_audit(
                session,
                actor="e2e",
                entry_type="admin_key_created",
                payload={"id": key_id, "name": "e2e"},
            )
            if _SEED_MODE == "spike":
                seed.update(_seed_spike(session))
            session.commit()
    finally:
        engine.dispose()

    _KEY_FILE.write_text(token + "\n", encoding="utf-8")
    _KEY_FILE.chmod(0o600)
    _SEED_FILE.write_text(json.dumps(seed, indent=2) + "\n", encoding="utf-8")
    if _PORT == _DEFAULT_PORT:
        _LEGACY_KEY.write_text(token + "\n", encoding="utf-8")
        _LEGACY_KEY.chmod(0o600)
        _LEGACY_SEED.write_text(json.dumps(seed, indent=2) + "\n", encoding="utf-8")
    print(f"http://{_HOST}:{_PORT}", flush=True)
    print(f"admin key written to {_KEY_FILE}", flush=True)
    print(f"seed written to {_SEED_FILE} (mode={_SEED_MODE})", flush=True)

    spike_agent_id = seed.get("spike_agent_id")
    if isinstance(spike_agent_id, str) and spike_agent_id:
        _start_live_producer(database_url, spike_agent_id)

    app = create_app(settings)
    uvicorn.run(app, host=_HOST, port=_PORT, log_level="warning")
    return 0


def _start_live_producer(database_url: str, agent_id: str) -> None:
    """Insert ``live.tail`` events on a timer for the explorer live-tail test.

    Args:
        database_url: Same SQLite URL the app uses.
        agent_id: Spike agent that receives the synthetic events.
    """

    def _loop() -> None:
        engine = make_engine(database_url)
        counter = 0
        try:
            while True:
                time.sleep(_LIVE_INTERVAL_S)
                counter += 1
                stamp = datetime.now(UTC)
                # 32 hex chars; keep the live prefix unique from seed ids.
                event_id = f"f{counter:031x}"
                span_id = f"{counter & 0xFFFFFFFFFFFFFFFF:016x}"
                iso = stamp.isoformat().replace("+00:00", "Z")
                payload: dict[str, object] = {
                    "id": event_id,
                    "type": "decision",
                    "ts": iso,
                    "kind": "tool",
                    "resource": _LIVE_RESOURCE,
                    "effect": "allow",
                    "status": "ok",
                    "rule": "e2e:live.tail",
                    "run": _LIVE_RUN_ID,
                    "span_id": span_id,
                    "agent": _SPIKE_NAME,
                }
                try:
                    with session_factory(engine)() as session:
                        if session.get(Run, _LIVE_RUN_ID) is None:
                            session.add(
                                Run(
                                    id=_LIVE_RUN_ID,
                                    status="running",
                                    first_seen_at=stamp,
                                    last_seen_at=stamp,
                                )
                            )
                            session.add(
                                RunParticipant(
                                    run_id=_LIVE_RUN_ID,
                                    instance_id="ff" * 16,
                                    agent_id=agent_id,
                                )
                            )
                        else:
                            run = session.get(Run, _LIVE_RUN_ID)
                            if run is not None:
                                run.last_seen_at = stamp
                        session.add(
                            Event(
                                id=event_id,
                                agent_id=agent_id,
                                instance_id="ff" * 16,
                                event_type="decision",
                                ts=stamp,
                                task=_LIVE_RUN_ID,
                                run_id=_LIVE_RUN_ID,
                                span_id=span_id,
                                kind="tool",
                                resource=_LIVE_RESOURCE,
                                effect="allow",
                                status="ok",
                                rule="e2e:live.tail",
                                payload=payload,
                                received_at=stamp,
                            )
                        )
                        session.commit()
                except Exception as err:
                    print(f"live producer insert failed: {err}", flush=True)
        finally:
            engine.dispose()

    thread = threading.Thread(
        target=_loop,
        name="tessera-e2e-live-producer",
        daemon=True,
    )
    thread.start()
    print("live.tail background producer started", flush=True)


def _seed_spike(session: Session) -> dict[str, object]:
    """Insert palette-probe and a denial-spike agent with recent denies.

    Args:
        session: Open SQLAlchemy session.

    Returns:
        Seed metadata for Playwright.
    """
    owner = Owner(
        id=new_ulid(),
        owner_type="user",
        email="e2e@example.com",
        display_name="e2e",
    )
    session.add(owner)
    team = Owner(
        id=new_ulid(),
        owner_type="team",
        email="platform@teams.local",
        display_name="platform",
    )
    session.add(team)
    session.flush()
    agent = Agent(
        id=new_ulid(),
        name=_AGENT_NAME,
        environment="prod",
        status="active",
        owner_id=owner.id,
        description="palette accept fixture",
        last_seen_at=_PALETTE_NOW,
    )
    session.add(agent)
    session.flush()
    put_agent_policy(
        session,
        agent.id,
        _palette_policy(agent.name),
        actor="e2e",
    )
    session.add(
        TokenCounter(
            agent_id=agent.id,
            day=datetime.now(UTC).date(),
            used=1200,
            reserved=300,
        )
    )
    session.add(
        Revocation(
            kind="agent_activated",
            agent_id=agent.id,
            subject=agent.id,
            created_at=_PALETTE_NOW - timedelta(minutes=5),
        )
    )
    session.add(
        Revocation(
            kind="policy_updated",
            agent_id=agent.id,
            subject=agent.id,
            created_at=_PALETTE_NOW - timedelta(minutes=1),
        )
    )
    session.add(
        Run(
            id=_RUN_ID,
            status="completed",
            first_seen_at=_PALETTE_NOW,
            last_seen_at=_PALETTE_NOW,
        )
    )
    session.add(
        RunParticipant(
            run_id=_RUN_ID,
            instance_id="dd" * 16,
            agent_id=agent.id,
        )
    )
    session.add(
        Event(
            id="e" * 32,
            agent_id=agent.id,
            instance_id="dd" * 16,
            event_type="decision",
            ts=_PALETTE_NOW,
            task=_RUN_ID,
            run_id=_RUN_ID,
            span_id=_SPAN_ID,
            kind="tool",
            resource="files.read",
            effect="allow",
            status="ok",
            rule="agent:tools.allow[0]",
            payload={"id": "e" * 32, "span_id": _SPAN_ID, "effect": "allow"},
            received_at=_PALETTE_NOW,
        )
    )
    session.add(
        Event(
            id="f" * 32,
            agent_id=agent.id,
            instance_id="dd" * 16,
            event_type="decision",
            ts=_PALETTE_NOW - timedelta(minutes=2),
            task=_RUN_ID,
            run_id=_RUN_ID,
            span_id="deadbeefcafef00d",
            kind="tool",
            resource="files.delete",
            effect="deny",
            status="ok",
            rule="default",
            payload={
                "id": "f" * 32,
                "span_id": "deadbeefcafef00d",
                "effect": "deny",
            },
            received_at=_PALETTE_NOW - timedelta(minutes=2),
        )
    )

    spike_owner = Owner(
        id=new_ulid(),
        owner_type="user",
        email="spike@example.com",
        display_name="spike",
    )
    session.add(spike_owner)
    session.flush()
    now = datetime.now(UTC)
    spike = Agent(
        id=new_ulid(),
        name=_SPIKE_NAME,
        environment="prod",
        status="active",
        owner_id=spike_owner.id,
        description="overview denial-spike fixture",
        last_seen_at=now,
    )
    session.add(spike)
    session.flush()
    session.add(
        Run(
            id=_SPIKE_RUN_ID,
            status="running",
            first_seen_at=now - timedelta(minutes=20),
            last_seen_at=now,
        )
    )
    session.add(
        RunParticipant(
            run_id=_SPIKE_RUN_ID,
            instance_id="ee" * 16,
            agent_id=spike.id,
        )
    )
    # Several recent denials -> 1h count >> 3x (24h hourly mean).
    for index in range(6):
        stamp = now - timedelta(minutes=5 + index)
        event_id = f"{index + 1:032x}"
        session.add(
            Event(
                id=event_id,
                agent_id=spike.id,
                instance_id="ee" * 16,
                event_type="decision",
                ts=stamp,
                task=_SPIKE_RUN_ID,
                run_id=_SPIKE_RUN_ID,
                span_id=f"{index + 1:016x}",
                kind="tool",
                resource="shell.exec",
                effect="deny",
                status="ok",
                rule="agent:tools.deny[0]",
                payload={
                    "id": event_id,
                    "effect": "deny",
                    "rule": "agent:tools.deny[0]",
                    "span_id": f"{index + 1:016x}",
                },
                received_at=stamp,
            )
        )

    # Extra historical rows so the explorer table can scroll before live fills.
    for index in range(20):
        stamp = now - timedelta(minutes=40 + index)
        event_id = f"a{index + 1:031x}"
        session.add(
            Event(
                id=event_id,
                agent_id=spike.id,
                instance_id="ee" * 16,
                event_type="decision",
                ts=stamp,
                task=_SPIKE_RUN_ID,
                run_id=_SPIKE_RUN_ID,
                span_id=f"{(index + 100):016x}",
                kind="tool",
                resource="files.read",
                effect="allow",
                status="ok",
                rule="agent:tools.allow[0]",
                payload={
                    "id": event_id,
                    "effect": "allow",
                    "span_id": f"{(index + 100):016x}",
                },
                received_at=stamp,
            )
        )

    # Known spend + energy for the Insights test (totals match /v1/dashboards/spend).
    spend_total = 0.0
    for index, (minutes_ago, cost) in enumerate(
        ((90, 1.25), (30, 0.75), (10, 0.5)),
        start=1,
    ):
        stamp = now - timedelta(minutes=minutes_ago)
        event_id = f"b{index:031x}"
        spend_total += cost
        session.add(
            Event(
                id=event_id,
                agent_id=agent.id,
                instance_id="dd" * 16,
                event_type="usage",
                ts=stamp,
                task=_RUN_ID,
                run_id=_RUN_ID,
                span_id=f"{(index + 200):016x}",
                kind="model",
                resource="openai/gpt-4o-mini",
                effect=None,
                status="ok",
                rule=None,
                payload={
                    "id": event_id,
                    "type": "usage",
                    "model": "openai/gpt-4o-mini",
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "cost_usd": cost,
                    "span_id": f"{(index + 200):016x}",
                },
                received_at=stamp,
            )
        )

    chain = _seed_chain_run(session, owner_id=owner.id, alpha_id=agent.id)
    retired = _seed_retired_agent(session, owner_id=owner.id)

    return {
        "agent_name": agent.name,
        "agent_id": agent.id,
        "run_id": _RUN_ID,
        "span_id": _SPAN_ID,
        "spike_agent_name": spike.name,
        "spike_agent_id": spike.id,
        "spike_run_id": _SPIKE_RUN_ID,
        "policy_deny_tool": _POLICY_DENY_TOOL,
        "policy_deny_rule": _POLICY_DENY_RULE,
        "spend_total_usd": spend_total,
        **chain,
        **retired,
    }


def _palette_policy(agent_name: str) -> str:
    """Return a tight policy that denies a known tool for the dry-run test.

    Args:
        agent_name: Agent slug written into ``metadata.agent``.

    Returns:
        AgentPolicy YAML for the palette-probe fixture.
    """
    return (
        "apiVersion: tessera/v1\n"
        "kind: AgentPolicy\n"
        "metadata:\n"
        f"  agent: {agent_name}\n"
        "  environment: prod\n"
        "spec:\n"
        "  limits:\n"
        "    tokens_per_day: 10000\n"
        "  models:\n"
        '    allow: ["gpt-4o-mini"]\n'
        "  tools:\n"
        '    allow: ["files.read", "files.write"]\n'
        f'    deny: ["{_POLICY_DENY_TOOL}"]\n'
        "  data:\n"
        '    allow: ["ticket:*"]\n'
    )


def _seed_retired_agent(session: Session, *, owner_id: str) -> dict[str, object]:
    """Seed a retired agent with one active instance for the console tests.

    Args:
        session: Open SQLAlchemy session.
        owner_id: Owner shared with palette-probe.

    Returns:
        Seed metadata for Playwright (retired agent + instance ids).
    """
    retired = Agent(
        id=new_ulid(),
        name=_RETIRED_NAME,
        environment="prod",
        status="retired",
        owner_id=owner_id,
        description="retired agent for lifecycle/revoke accept",
        last_seen_at=_PALETTE_NOW,
    )
    session.add(retired)
    session.flush()
    session.add(
        Instance(
            id=_RETIRED_INSTANCE_ID,
            agent_id=retired.id,
            host="e2e-retired.local",
            runtime={"name": "python", "version": "3.12"},
            started_at=_PALETTE_NOW,
            last_heartbeat_at=_PALETTE_NOW,
            revoked=False,
        )
    )
    return {
        "retired_agent_name": retired.name,
        "retired_agent_id": retired.id,
        "retired_instance_id": _RETIRED_INSTANCE_ID,
    }


def _seed_chain_run(session: Session, *, owner_id: str, alpha_id: str) -> dict[str, object]:
    """Seed a completed two-agent run for waterfall screenshots.

    Args:
        session: Open SQLAlchemy session.
        owner_id: Owner id shared with palette-probe.
        alpha_id: Orchestrator agent id (palette-probe).

    Returns:
        Seed metadata for Playwright (chain run + beta agent + span ids).
    """
    beta = Agent(
        id=new_ulid(),
        name=_BETA_NAME,
        environment="prod",
        status="active",
        owner_id=owner_id,
        description="two-agent waterfall fixture",
        last_seen_at=_CHAIN_START + timedelta(seconds=30),
    )
    session.add(beta)
    session.flush()
    session.add(
        Run(
            id=_CHAIN_RUN_ID,
            status="completed",
            first_seen_at=_CHAIN_START,
            last_seen_at=_CHAIN_START + timedelta(seconds=8),
            supervisor="user:e2e",
        )
    )
    session.add_all(
        [
            RunParticipant(
                run_id=_CHAIN_RUN_ID,
                instance_id="aa" * 16,
                agent_id=alpha_id,
            ),
            RunParticipant(
                run_id=_CHAIN_RUN_ID,
                instance_id="bb" * 16,
                agent_id=beta.id,
            ),
        ]
    )

    def add_event(
        event_id: str,
        agent_id: str,
        stamp: datetime,
        event_type: str,
        *,
        span: str | None,
        parent: str | None,
        effect: str,
        status: str,
        resource: str,
        extra: dict[str, object] | None = None,
        run_id: str = _CHAIN_RUN_ID,
    ) -> None:
        payload: dict[str, object] = {
            "id": event_id,
            "type": event_type,
            "ts": stamp.isoformat().replace("+00:00", "Z"),
            "effect": effect,
            "status": status,
            "run": run_id,
            "action": {"kind": "tool", "resource": resource},
            **(extra or {}),
        }
        if span:
            payload["span_id"] = span
        if parent:
            payload["parent_span_id"] = parent
        session.add(
            Event(
                id=event_id,
                agent_id=agent_id,
                instance_id="cc" * 16,
                event_type=event_type,
                ts=stamp,
                task=run_id,
                run_id=run_id,
                span_id=span,
                parent_span_id=parent,
                kind="tool",
                resource=resource,
                effect=effect,
                status=status,
                rule="agent:tools.allow[0]",
                payload=payload,
                received_at=stamp,
            )
        )

    add_event(
        "c" * 31 + "1",
        alpha_id,
        _CHAIN_START,
        "decision",
        span=_CHAIN_ROOT_SPAN,
        parent=None,
        effect="allow",
        status="ok",
        resource="agent.call",
        extra={"trace_id": _CHAIN_TRACE_ID},
    )
    add_event(
        "c" * 31 + "2",
        alpha_id,
        _CHAIN_START + timedelta(seconds=2),
        "call",
        span=_CHAIN_ROOT_SPAN,
        parent=None,
        effect="allow",
        status="ok",
        resource="agent.call",
    )
    add_event(
        "c" * 31 + "3",
        beta.id,
        _CHAIN_START + timedelta(seconds=3),
        "decision",
        span=_CHAIN_DENY_SPAN,
        parent=_CHAIN_ROOT_SPAN,
        effect="deny",
        status="ok",
        resource="shell.exec",
    )
    add_event(
        "c" * 31 + "4",
        beta.id,
        _CHAIN_START + timedelta(seconds=4),
        "decision",
        span=_CHAIN_APPROVAL_SPAN,
        parent=_CHAIN_ROOT_SPAN,
        effect="require_approval",
        status="ok",
        resource="payments.refund",
    )
    add_event(
        "c" * 31 + "5",
        beta.id,
        _CHAIN_START + timedelta(seconds=5),
        "approval",
        span=_CHAIN_APPROVAL_SPAN,
        parent=_CHAIN_ROOT_SPAN,
        effect="require_approval",
        status="ok",
        resource="payments.refund",
        extra={"id": _PENDING_APPROVAL_ID},
    )
    session.add(
        Approval(
            id=_PENDING_APPROVAL_ID,
            agent_id=beta.id,
            instance_id="bb" * 16,
            run_id=_CHAIN_RUN_ID,
            action={
                "kind": "tool",
                "resource": "payments.refund",
                "args": {"amount": 42, "currency": "USD"},
                "span_id": _CHAIN_APPROVAL_SPAN,
            },
            decision={
                "effect": "require_approval",
                "reason": "approval_rule",
                "rule": "agent:tools.require_approval[0]",
            },
            route={
                "parties": ["user:e2e", "owner"],
                "forbid_self_approval": True,
                "timeout_seconds": 900,
            },
            route_index=0,
            status="pending",
            requested_by_principal="user:agent",
            resolved_by=None,
            expires_at=_CHAIN_START + timedelta(hours=2),
            created_at=_CHAIN_START + timedelta(seconds=4),
            resolved_at=None,
        )
    )
    add_event(
        "c" * 31 + "6",
        beta.id,
        _CHAIN_START + timedelta(seconds=6),
        "call",
        span=_CHAIN_APPROVAL_SPAN,
        parent=_CHAIN_ROOT_SPAN,
        effect="require_approval",
        status="ok",
        resource="payments.refund",
    )
    add_event(
        "c" * 31 + "7",
        alpha_id,
        _CHAIN_START + timedelta(seconds=6),
        "decision",
        span=_CHAIN_ERROR_SPAN,
        parent=_CHAIN_ROOT_SPAN,
        effect="allow",
        status="ok",
        resource="files.write",
    )
    add_event(
        "c" * 31 + "8",
        alpha_id,
        _CHAIN_START + timedelta(seconds=7),
        "call",
        span=_CHAIN_ERROR_SPAN,
        parent=_CHAIN_ROOT_SPAN,
        effect="allow",
        status="error",
        resource="files.write",
        extra={"error_type": "Timeout"},
    )
    # Pre-0.6 SDK event without a span id (unparented list under the waterfall).
    add_event(
        "c" * 31 + "9",
        alpha_id,
        _CHAIN_START + timedelta(seconds=7, milliseconds=500),
        "decision",
        span=None,
        parent=None,
        effect="allow",
        status="ok",
        resource="legacy.tool",
    )
    session.add(
        RunEffect(
            id="f" * 32,
            run_id=_CHAIN_RUN_ID,
            agent_id=alpha_id,
            ref="zendesk:ticket/42",
            reversible=True,
            undo="zendesk.reopen",
            created_at=_CHAIN_START + timedelta(seconds=5),
        )
    )
    session.add(
        Run(
            id=_REVOKE_RUN_ID,
            status="running",
            first_seen_at=_CHAIN_START,
            last_seen_at=_CHAIN_START + timedelta(seconds=1),
            supervisor="user:e2e",
        )
    )
    session.add(
        RunParticipant(
            run_id=_REVOKE_RUN_ID,
            instance_id="aa" * 16,
            agent_id=alpha_id,
        )
    )
    add_event(
        "d" * 32,
        alpha_id,
        _CHAIN_START,
        "decision",
        span="5555555555555555",
        parent=None,
        effect="allow",
        status="ok",
        resource="files.read",
        run_id=_REVOKE_RUN_ID,
    )

    return {
        "chain_run_id": _CHAIN_RUN_ID,
        "beta_agent_name": beta.name,
        "beta_agent_id": beta.id,
        "chain_root_span": _CHAIN_ROOT_SPAN,
        "chain_deny_span": _CHAIN_DENY_SPAN,
        "chain_approval_span": _CHAIN_APPROVAL_SPAN,
        "chain_trace_id": _CHAIN_TRACE_ID,
        "revoke_run_id": _REVOKE_RUN_ID,
        "pending_approval_id": _PENDING_APPROVAL_ID,
    }


if __name__ == "__main__":
    raise SystemExit(main())
