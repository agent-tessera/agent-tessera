"""Command-line interface for Agent Tessera (the `tessera` command)."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn, cast

import httpx

from agent_tessera import __version__
from agent_tessera.audit import verify_audit_log
from agent_tessera.credentials import (
    remove_profile,
    resolve_admin_key,
    resolve_server,
    save_profile,
)
from agent_tessera.errors import ConfigError, PolicyError, RegistryError, TesseraError
from agent_tessera.footprint import Footprint, compute_footprint
from agent_tessera.policy.effective import build_effective_policy
from agent_tessera.policy.evaluator import RuntimeState, evaluate
from agent_tessera.policy.loader import (
    _read_document,
    load_agent_policy,
    load_baseline_policy,
    parse_policy,
)
from agent_tessera.policy.models import AgentPolicy, BaselinePolicy, SupervisorGrants
from agent_tessera.registry.apply import apply_plan, plan, render
from agent_tessera.registry.backends import (
    LocalBackend,
    RegistryBackend,
    ServerBackend,
    open_server_backend,
)
from agent_tessera.registry.models import AgentStatus, RegistryEntry, load_registry
from agent_tessera.types import Action, Effect, Principal


class _Parser(argparse.ArgumentParser):
    """Argument parser that reports usage errors with exit code 1."""

    def error(self, message: str) -> NoReturn:
        """Print a usage error to stderr and exit.

        Args:
            message: What was wrong with the arguments.
        """
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the `tessera` command.

    Returns:
        The configured top-level parser.
    """
    parser = _Parser(
        prog="tessera",
        description="Identity, permissions, and governance for AI agents.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")

    policy = commands.add_parser("policy", help="validate or evaluate a policy file")
    policy_commands = policy.add_subparsers(dest="policy_command")
    validate = policy_commands.add_parser("validate", help="check that policy files load")
    validate.add_argument("paths", nargs="+", metavar="PATH")
    check = policy_commands.add_parser("check", help="evaluate one action")
    check.add_argument("--policy", required=True, metavar="PATH")
    check.add_argument("--baseline", action="append", default=[], metavar="PATH")
    kind = check.add_mutually_exclusive_group(required=True)
    kind.add_argument("--model", metavar="NAME")
    kind.add_argument("--tool", metavar="NAME")
    kind.add_argument("--data", metavar="NAME")
    kind.add_argument("--agent", metavar="NAME")
    check.add_argument("--arg", action="append", default=[], metavar="KEY=VALUE")
    check.add_argument("--user", metavar="USER")
    check.add_argument("--group", action="append", default=[], metavar="GROUP")
    check.add_argument("--steps", type=int, metavar="N")
    check.add_argument("--guardrail", action="append", default=[], metavar="NAME")

    footprint = commands.add_parser("footprint", help="compare grants with an audit log")
    footprint.add_argument("--policy", required=True, metavar="PATH")
    footprint.add_argument("--baseline", action="append", default=[], metavar="PATH")
    footprint.add_argument("--audit", required=True, metavar="PATH")
    footprint.add_argument("--days", type=_positive_days, default=30, metavar="N")
    footprint.add_argument("--json", action="store_true")

    audit = commands.add_parser("audit", help="inspect an audit log")
    audit_commands = audit.add_subparsers(dest="audit_command")
    verify = audit_commands.add_parser("verify", help="check the hash chain")
    verify.add_argument("path", metavar="PATH")
    login = commands.add_parser("login", help="store an admin key for the control plane")
    login.add_argument("--server", required=True, metavar="URL")
    login.add_argument("--profile", default="default", metavar="NAME")
    logout = commands.add_parser("logout", help="remove a stored admin profile")
    logout.add_argument("--profile", default="default", metavar="NAME")
    status = commands.add_parser("status", help="show control-plane status")
    status.add_argument("--server", default=None, metavar="URL")
    status.add_argument("--profile", default="default", metavar="NAME")
    _add_approvals(commands)
    _add_events(commands)
    _add_agents(commands)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the `tessera` command.

    Args:
        argv: Command-line arguments, excluding the program name. Defaults to
            `sys.argv[1:]`.

    Returns:
        The process exit code.

    Example:
        ```python
        from agent_tessera.cli import main

        raise SystemExit(main(["--version"]))
        ```
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["server"]:
        return _delegate_server(arguments[1:])
    try:
        return _run(arguments)
    except TesseraError as err:
        _write(str(err), error=True)
        return 1


def _delegate_server(argv: Sequence[str]) -> int:
    """Forward `tessera server` to the server package.

    Args:
        argv: Arguments after `server`.

    Returns:
        The server command's exit code, or `1` when the package is not installed.
    """
    try:
        from tessera_server.cli import main as server_main  # noqa: PLC0415
    except ImportError:
        _write(
            "tessera server requires agent-tessera-server: pip install agent-tessera-server",
            error=True,
        )
        return 1
    return server_main(list(argv))


def _run(argv: Sequence[str] | None) -> int:
    """Parse arguments and dispatch a command.

    Args:
        argv: Arguments excluding the program name.

    Returns:
        The process exit code.
    """
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.command is None:
        parser.print_help(sys.stdout)
        return 0
    handlers = {
        "policy": _policy_command,
        "footprint": _footprint_command,
        "agents": _agents_command,
        "login": _login_command,
        "logout": _logout_command,
        "status": _status_command,
        "approvals": _approvals_command,
        "events": _events_command,
        "audit": _audit_command,
    }
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help(sys.stdout)
        return 0
    return handler(args)


def _policy_command(args: argparse.Namespace) -> int:
    """Run `policy validate` or `policy check`.

    Args:
        args: Parsed arguments.

    Returns:
        The command's exit code, or `1` when the policy verb is missing.
    """
    if args.policy_command == "validate":
        return _validate(args.paths)
    if args.policy_command == "check":
        return _check(args)
    _write("usage: tessera policy {validate,check}", error=True)
    return 1


def _audit_command(args: argparse.Namespace) -> int:
    """Run `audit verify`.

    Args:
        args: Parsed arguments.

    Returns:
        The command's exit code, or `1` when the audit verb is missing.
    """
    if args.audit_command == "verify":
        return _verify(args.path)
    _write("usage: tessera audit {verify}", error=True)
    return 1


def _write(text: str, *, error: bool = False) -> None:
    """Write one line of command output.

    Args:
        text: Line to write, without a trailing newline.
        error: Write to stderr when true, otherwise stdout.
    """
    stream = sys.stderr if error else sys.stdout
    stream.write(f"{text}\n")


def _login_command(args: argparse.Namespace) -> int:
    """Prompt for an admin key, verify it, and store the profile.

    Args:
        args: Parsed `login` arguments.

    Returns:
        `0` when the profile is written, otherwise `1`.
    """
    server = str(args.server).rstrip("/")
    try:
        admin_key = getpass.getpass("Admin key: ")
    except (EOFError, KeyboardInterrupt):
        _write("login cancelled", error=True)
        return 1
    if admin_key.strip() == "":
        _write("admin key is required", error=True)
        return 1
    try:
        _check_me(server, admin_key.strip())
    except TesseraError as err:
        _write(str(err), error=True)
        return 1
    path = save_profile(server=server, admin_key=admin_key.strip(), profile=args.profile)
    _write(f"logged in to {server} (profile {args.profile})")
    _write(f"credentials: {path}")
    return 0


def _logout_command(args: argparse.Namespace) -> int:
    """Remove a stored profile.

    Args:
        args: Parsed `logout` arguments.

    Returns:
        `0` when the profile is removed or was already absent.
    """
    removed = remove_profile(profile=args.profile)
    if removed:
        _write(f"logged out profile {args.profile}")
    else:
        _write(f"no stored profile {args.profile}")
    return 0


def _status_command(args: argparse.Namespace) -> int:
    """Print control-plane status for the resolved credentials.

    Args:
        args: Parsed `status` arguments.

    Returns:
        `0` when the server answers, otherwise `1`.
    """
    try:
        server = resolve_server(cli_server=args.server, profile=args.profile)
        admin_key = resolve_admin_key(profile=args.profile)
    except ConfigError as err:
        _write(str(err), error=True)
        return 1
    try:
        me = _check_me(server, admin_key)
        health = _get_json(server, "/healthz")
        agents = _get_json(server, "/v1/agents", headers={"Authorization": f"Bearer {admin_key}"})
        approvals = _get_json(
            server,
            "/v1/approvals",
            headers={"Authorization": f"Bearer {admin_key}"},
            params={"status": "pending", "limit": "500"},
        )
    except TesseraError as err:
        _write(str(err), error=True)
        return 1
    counts = _agent_status_counts(agents.get("items"))
    pending_items = approvals.get("items")
    pending = len(pending_items) if isinstance(pending_items, list) else 0
    pending_label = f"{pending}+" if approvals.get("next_cursor") else str(pending)
    _write(f"server:  {server}")
    _write(f"version: {health.get('version', '?')}")
    _write(f"dev:     {health.get('dev', '?')}")
    _write(f"actor:   {me.get('actor', '?')}")
    _write(
        "agents:  "
        + ", ".join(
            f"{name}={counts[name]}" for name in ("active", "suspended", "draft", "retired")
        )
    )
    _write(f"pending: {pending_label}")
    return 0


def _approvals_command(args: argparse.Namespace) -> int:
    """List or resolve approvals on the control plane.

    Args:
        args: Parsed `approvals` arguments.

    Returns:
        The command's exit code.
    """
    verb = args.approvals_command
    if verb is None:
        _write("usage: tessera approvals {ls,show,approve,deny}", error=True)
        return 1
    try:
        server, headers = _admin_session(args)
        if verb == "ls":
            params = {"status": str(args.status)} if args.status else {}
            page = _get_json(server, "/v1/approvals", headers=headers, params=params)
            for item in _mapping_items(page):
                _write(_approval_line(item))
            return 0
        if verb == "show":
            detail = _get_json(server, f"/v1/approvals/{args.approval_id}", headers=headers)
            _write(json.dumps(detail, indent=2, default=str))
            return 0
        body: dict[str, object] = {}
        if args.comment is not None:
            body["comment"] = args.comment
        result = _post_json(
            server,
            f"/v1/approvals/{args.approval_id}:{verb}",
            headers=headers,
            body=body,
        )
        _write(_approval_line(result))
        return 0
    except (ConfigError, TesseraError) as err:
        _write(str(err), error=True)
        return 1


def _events_command(args: argparse.Namespace) -> int:
    """Stream audit events from the control plane.

    Args:
        args: Parsed `events` arguments.

    Returns:
        The command's exit code.
    """
    if args.events_command != "tail":
        _write("usage: tessera events tail", error=True)
        return 1
    try:
        server, headers = _admin_session(args)
        return _tail_events(
            server,
            headers,
            agent_name=args.agent,
            event_type=args.event_type,
            follow=bool(args.follow),
        )
    except (ConfigError, TesseraError) as err:
        _write(str(err), error=True)
        return 1


def _admin_session(args: argparse.Namespace) -> tuple[str, dict[str, str]]:
    """Resolve server URL and admin Authorization header.

    Args:
        args: Parsed command with optional `--server` and `--profile`.

    Returns:
        Origin URL and headers.

    Raises:
        ConfigError: When credentials are missing.
    """
    server = resolve_server(cli_server=args.server, profile=args.profile)
    admin_key = resolve_admin_key(profile=args.profile)
    return server, {"Authorization": f"Bearer {admin_key}"}


def _tail_events(
    server: str,
    headers: Mapping[str, str],
    *,
    agent_name: str | None,
    event_type: str | None,
    follow: bool,
) -> int:
    """Print audit events, optionally following with a cursor.

    Args:
        server: Control-plane origin.
        headers: Authorization headers.
        agent_name: Optional agent name filter.
        event_type: Optional event type filter.
        follow: Poll every two seconds when true.

    Returns:
        `0` when the stream ends or is interrupted.
    """
    agent_id = _agent_id_by_name(server, headers, agent_name) if agent_name else None
    cursor: str | None = None
    seen: set[str] = set()
    try:
        while True:
            params: dict[str, str] = {"limit": "50"}
            if agent_id is not None:
                params["agent_id"] = agent_id
            if event_type:
                params["type"] = str(event_type)
            if cursor is not None:
                params["cursor"] = cursor
            page = _get_json(server, "/v1/audit", headers=dict(headers), params=params)
            for item in _mapping_items(page):
                event_id = item.get("id")
                if not isinstance(event_id, str) or event_id in seen:
                    continue
                seen.add(event_id)
                _write(json.dumps(item, default=str))
                cursor = event_id
            next_cursor = page.get("next_cursor")
            if isinstance(next_cursor, str) and next_cursor != "":
                cursor = next_cursor
            if not follow:
                return 0
            time.sleep(2)
    except KeyboardInterrupt:
        return 0


def _agent_id_by_name(
    server: str,
    headers: Mapping[str, str],
    name: str,
) -> str:
    """Resolve an agent name to its id.

    Args:
        server: Control-plane origin.
        headers: Authorization headers.
        name: Agent name.

    Returns:
        The agent id.

    Raises:
        TesseraError: When no agent matches the name.
    """
    page = _get_json(server, "/v1/agents", headers=dict(headers), params={"q": name})
    for item in _mapping_items(page):
        if item.get("name") == name:
            agent_id = item.get("id")
            if isinstance(agent_id, str):
                return agent_id
    raise TesseraError(f"agent not found: {name}")


def _approval_line(item: Mapping[str, object]) -> str:
    """Format one approval for list output.

    Args:
        item: Approval document.

    Returns:
        A single-line summary.
    """
    approval_id = item.get("id", "")
    status = item.get("status", "")
    agent = item.get("agent_id", "")
    action = item.get("action")
    resource = ""
    if isinstance(action, Mapping):
        resource = str(action.get("resource", ""))
    return f"{approval_id}  {status}  {agent}  {resource}"


def _mapping_items(page: Mapping[str, object]) -> list[dict[str, object]]:
    """Read `items` from a list response.

    Args:
        page: API page document.

    Returns:
        Object rows only.
    """
    raw = page.get("items")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _check_me(server: str, admin_key: str) -> dict[str, object]:
    """Call `GET /v1/me` with an admin key.

    Args:
        server: Control-plane origin.
        admin_key: Bearer credential.

    Returns:
        The identity document.

    Raises:
        TesseraError: When the server rejects the key or the body is invalid.
    """
    return _get_json(server, "/v1/me", headers={"Authorization": f"Bearer {admin_key}"})


def _get_json(
    server: str,
    path: str,
    *,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """GET a JSON object from the control plane.

    Args:
        server: Origin URL.
        path: Request path.
        headers: Optional headers.
        params: Optional query parameters.

    Returns:
        The response object.

    Raises:
        TesseraError: On transport or HTTP errors.
    """
    return _request_json("GET", server, path, headers=headers, params=params)


def _post_json(
    server: str,
    path: str,
    *,
    headers: Mapping[str, str] | None = None,
    body: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """POST a JSON object to the control plane.

    Args:
        server: Origin URL.
        path: Request path.
        headers: Optional headers.
        body: JSON body.

    Returns:
        The response object.

    Raises:
        TesseraError: On transport or HTTP errors.
    """
    return _request_json("POST", server, path, headers=headers, body=body)


def _request_json(
    method: str,
    server: str,
    path: str,
    *,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, str] | None = None,
    body: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Send a JSON request to the control plane.

    Args:
        method: HTTP method.
        server: Origin URL.
        path: Request path.
        headers: Optional headers.
        params: Optional query parameters.
        body: Optional JSON body.

    Returns:
        The response object.

    Raises:
        TesseraError: On transport or HTTP errors.
    """
    url = f"{server.rstrip('/')}{path}"
    request_headers = dict(headers or {})
    try:
        if method == "GET":
            response = httpx.get(
                url,
                headers=request_headers,
                params=dict(params or {}),
                timeout=10.0,
            )
        else:
            response = httpx.post(
                url,
                headers=request_headers,
                json=dict(body or {}),
                timeout=10.0,
            )
    except httpx.HTTPError as err:
        raise TesseraError(f"server unreachable: {err}") from err
    if response.status_code >= httpx.codes.BAD_REQUEST:
        detail = response.text.strip() or response.reason_phrase
        raise TesseraError(f"server error {response.status_code}: {detail}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise TesseraError("server returned a non-object JSON body")
    return cast("dict[str, object]", payload)


def _agent_status_counts(items: object) -> dict[str, int]:
    """Count agents by lifecycle status.

    Args:
        items: `GET /v1/agents` items list.

    Returns:
        Counts for active, suspended, draft, and retired.
    """
    counts = {"active": 0, "suspended": 0, "draft": 0, "retired": 0}
    if not isinstance(items, list):
        return counts
    for item in items:
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        if isinstance(status, str) and status in counts:
            counts[status] += 1
    return counts


def _positive_days(value: str) -> int:
    """Parse a positive day count.

    Args:
        value: Text from `--days`.

    Returns:
        The day count.

    Raises:
        ArgumentTypeError: When the value is not an integer of at least 1.
    """
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("--days must be a positive integer") from None
    if number < 1:
        raise argparse.ArgumentTypeError("--days must be a positive integer")
    return number


def _footprint_command(args: argparse.Namespace) -> int:
    """Compare a policy's grants with a local audit log.

    Args:
        args: Parsed `footprint` arguments.

    Returns:
        `0` after the report is printed.
    """
    policy = load_agent_policy(args.policy)
    baselines = tuple(load_baseline_policy(path) for path in args.baseline)
    since = datetime.now(UTC) - timedelta(days=args.days)
    report = compute_footprint(
        build_effective_policy(policy, baselines),
        _read_audit(Path(args.audit)),
        since=since,
    )
    if args.json:
        _write(json.dumps(report.as_dict(), indent=2))
    else:
        _write(_footprint_text(report).rstrip("\n"))
    return 0


def _read_audit(path: Path) -> list[Mapping[str, object]]:
    """Load JSON lines from an audit log.

    Args:
        path: Audit file.

    Returns:
        One mapping per non-empty line.

    Raises:
        PolicyError: If the file is missing, a line is not JSON, or a line is
            not an object.
    """
    if not path.is_file():
        raise PolicyError(f"{path}: file not found")
    events: list[Mapping[str, object]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip() == "":
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as err:
            raise PolicyError(f"{path}: line {number}: invalid json") from err
        if not isinstance(item, dict):
            raise PolicyError(f"{path}: line {number}: event must be an object")
        events.append(cast("dict[str, object]", item))
    return events


def _footprint_text(report: Footprint) -> str:
    """Render the four footprint sections.

    Args:
        report: Computed footprint.

    Returns:
        Text with sections `declared`, `observed`, `unused`, and `denied`.
    """
    lines = ["declared"]
    lines.extend(f"  {item.kind} {item.pattern} ({item.rule})" for item in report.declared)
    lines.append("observed")
    lines.extend(f"  {item.kind} {item.resource} {item.count}" for item in report.observed)
    lines.append("unused")
    lines.extend(f"  {item.kind} {item.pattern} ({item.rule})" for item in report.unused)
    lines.append("denied")
    lines.extend(f"  {item.kind} {item.resource} {item.count}" for item in report.denied)
    return "\n".join(lines) + "\n"


def _validate(paths: Sequence[str]) -> int:
    """Load each policy file and report the result.

    Args:
        paths: Policy files to load.

    Returns:
        `0` when every file loads, otherwise `1`.
    """
    failed = False
    for path in paths:
        try:
            label = _validated_label(path)
        except PolicyError as err:
            _write(f"ERROR {err}", error=True)
            failed = True
            continue
        _write(f"OK {path} ({label})")
    return 1 if failed else 0


def _validated_label(path: str) -> str:
    """Load a policy or a registry and return its display label.

    Args:
        path: File to load.

    Returns:
        `AgentPolicy <agent>`, `BaselinePolicy <name>`,
        `SupervisorGrants <id>`, or `AgentRegistry <n> agents`.

    Raises:
        PolicyError: If the file is not a policy or a registry.
    """
    document = _read_document(Path(path))
    if isinstance(document, Mapping) and document.get("kind") == "AgentRegistry":
        registry = load_registry(path)
        count = len(registry.agents)
        return f"AgentRegistry {count} agents"
    return _policy_label(parse_policy(document, path=Path(path)))


def _policy_label(policy: AgentPolicy | BaselinePolicy | SupervisorGrants) -> str:
    """Return the kind and name shown for a loaded policy.

    Args:
        policy: A policy that loaded successfully.

    Returns:
        `AgentPolicy <agent>`, `BaselinePolicy <name>`, or
        `SupervisorGrants <id>`.
    """
    if isinstance(policy, AgentPolicy):
        return f"AgentPolicy {policy.metadata.agent}"
    if isinstance(policy, SupervisorGrants):
        return f"SupervisorGrants {policy.metadata.supervisor}"
    return f"BaselinePolicy {policy.metadata.name}"


def _check(args: argparse.Namespace) -> int:
    """Evaluate one action and print the decision.

    Args:
        args: Parsed `policy check` arguments.

    Returns:
        `0` for allow, `2` for deny, `3` for approval, `1` for a usage error.
    """
    if args.group and args.user is None:
        _write("tessera: error: --group requires --user", error=True)
        return 1
    agent = load_agent_policy(args.policy)
    baselines = tuple(load_baseline_policy(path) for path in args.baseline)
    policy = build_effective_policy(agent, baselines)
    action = _action(args)
    state = RuntimeState(
        steps_in_task=args.steps,
        guardrails_ran=frozenset(args.guardrail),
    )
    decision = evaluate(policy, action, state)
    rule = "" if decision.rule is None else decision.rule
    _write(f"{'effect:':<9}{decision.effect.value}")
    _write(f"{'reason:':<9}{decision.reason.value}")
    _write(f"{'rule:':<9}{rule}")
    _write(f"{'policy:':<9}{decision.policy_version}")
    if decision.effect is Effect.ALLOW:
        return 0
    if decision.effect is Effect.DENY:
        return 2
    return 3


def _action(args: argparse.Namespace) -> Action:
    """Build the action described by a check command.

    Args:
        args: Parsed `policy check` arguments.

    Returns:
        The action, including a principal when `--user` was set.

    Raises:
        PolicyError: If an `--arg` value has no `=`.
    """
    arguments = _arguments(args.arg)
    if args.model is not None:
        action = Action.model(args.model)
    elif args.tool is not None:
        action = Action.tool(args.tool, arguments)
    elif args.data is not None:
        action = Action.data(args.data)
    else:
        action = Action.agent(args.agent)
    if args.user is not None:
        action = Action(
            action.kind,
            action.resource,
            action.args,
            Principal(args.user, frozenset(args.group)),
        )
    return action


def _arguments(items: Sequence[str]) -> dict[str, object]:
    """Parse `key=value` arguments, preferring JSON values.

    Args:
        items: Raw `--arg` values.

    Returns:
        Argument names mapped to JSON values, or the raw string when the
        value is not JSON.

    Raises:
        PolicyError: If an item does not contain `=`.
    """
    arguments: dict[str, object] = {}
    for item in items:
        key, separator, raw = item.partition("=")
        if separator == "":
            raise PolicyError(f"argument must be KEY=VALUE, got {item!r}")
        try:
            value: object = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        arguments[key] = value
    return arguments


def _verify(path: str) -> int:
    """Check an audit log and print the result.

    Args:
        path: Audit file to verify.

    Returns:
        `0` when the chain is intact, otherwise `1`.
    """
    result = verify_audit_log(path)
    if result.ok:
        _write(f"OK: {result.events} events")
        return 0
    if result.error_line is None:
        _write(f"FAILED: {result.error}", error=True)
    else:
        _write(f"FAILED at line {result.error_line}: {result.error}", error=True)
    return 1


def _add_approvals(commands: argparse._SubParsersAction[_Parser]) -> None:
    """Add the `approvals` command and its subcommands.

    Args:
        commands: Top-level subparser group.
    """
    approvals = commands.add_parser("approvals", help="list or resolve approvals")
    approvals.add_argument("--server", default=None, metavar="URL")
    approvals.add_argument("--profile", default="default", metavar="NAME")
    approval_commands = approvals.add_subparsers(dest="approvals_command")
    approval_ls = approval_commands.add_parser("ls", help="list approvals")
    approval_ls.add_argument("--status", default=None, metavar="STATUS")
    show = approval_commands.add_parser("show", help="show one approval")
    show.add_argument("approval_id", metavar="ID")
    for name, help_text in (
        ("approve", "approve a pending request"),
        ("deny", "deny a pending request"),
    ):
        command = approval_commands.add_parser(name, help=help_text)
        command.add_argument("approval_id", metavar="ID")
        command.add_argument("--comment", default=None, metavar="TEXT")


def _add_events(commands: argparse._SubParsersAction[_Parser]) -> None:
    """Add the `events` command and its subcommands.

    Args:
        commands: Top-level subparser group.
    """
    events = commands.add_parser("events", help="stream control-plane events")
    events.add_argument("--server", default=None, metavar="URL")
    events.add_argument("--profile", default="default", metavar="NAME")
    events_commands = events.add_subparsers(dest="events_command")
    tail = events_commands.add_parser("tail", help="print recent audit events")
    tail.add_argument("--agent", default=None, metavar="NAME")
    tail.add_argument("--type", dest="event_type", default=None, metavar="T")
    tail.add_argument("--follow", action="store_true")


def _add_agents(commands: argparse._SubParsersAction[_Parser]) -> None:
    """Add the `agents` command and its subcommands.

    Args:
        commands: Top-level subparser group.
    """
    agents = commands.add_parser("agents", help="register agents and change their lifecycle")
    subcommands = agents.add_subparsers(dest="agents_command")
    register = _agent_parser(subcommands, "register", "record an agent in the registry")
    register.add_argument("name", metavar="NAME")
    register.add_argument("--owner", required=True, metavar="OWNER")
    register.add_argument("--policy", metavar="PATH")
    register.add_argument("--description", default="", metavar="TEXT")
    register.add_argument("--risk-tier", default="low", choices=["low", "medium", "high"])
    register.add_argument("--label", action="append", default=[], metavar="KEY=VALUE")
    register.add_argument(
        "--autonomy",
        default="interactive",
        choices=["assistive", "interactive", "supervised", "autonomous"],
    )
    register.add_argument("--supervisor", action="append", default=[], metavar="S")
    register.add_argument("--activate", action="store_true")
    listing = _agent_parser(subcommands, "ls", "list registered agents", env_default=None)
    listing.add_argument("--status", default=None, metavar="S")
    listing.add_argument("--json", action="store_true")
    show = _agent_parser(subcommands, "show", "show one registered agent")
    show.add_argument("name", metavar="NAME")
    show.add_argument("--json", action="store_true")
    for name, help_text in (
        ("activate", "activate an agent"),
        ("suspend", "suspend an agent"),
        ("retire", "retire an agent"),
    ):
        command = _agent_parser(subcommands, name, help_text)
        command.add_argument("name", metavar="NAME")
    policy = _agent_parser(subcommands, "set-policy", "point an agent at a policy file")
    policy.add_argument("name", metavar="NAME")
    policy.add_argument("--policy", required=True, metavar="PATH")
    token = _agent_parser(subcommands, "token", "issue a registration token")
    token.add_argument("name", metavar="NAME")
    token.add_argument("--reusable", action="store_true")
    token.add_argument("--expires-in", type=int, metavar="SECONDS")
    apply = subcommands.add_parser("apply", help="make the registry match a file")
    apply.add_argument("--registry", metavar="PATH")
    apply.add_argument("--server", metavar="URL")
    apply.add_argument("-f", required=True, dest="file", metavar="PATH")
    apply.add_argument("--dry-run", action="store_true")


def _agent_parser(
    commands: argparse._SubParsersAction[_Parser],
    name: str,
    help_text: str,
    *,
    env_default: str | None = "dev",
) -> argparse.ArgumentParser:
    """Add one agents subcommand with the shared options.

    Args:
        commands: Agents subparser group.
        name: Subcommand name.
        help_text: Short help line.
        env_default: Value of `--env` when the flag is omitted. `ls` passes
            `None` so an omitted flag does not filter.

    Returns:
        The new parser.
    """
    parser = commands.add_parser(name, help=help_text)
    parser.add_argument("--registry", metavar="PATH")
    parser.add_argument("--server", metavar="URL")
    parser.add_argument("--env", default=env_default, metavar="ENV")
    return parser


def _agents_command(args: argparse.Namespace) -> int:
    """Run one `agents` subcommand.

    Args:
        args: Parsed arguments.

    Returns:
        The command's exit code.
    """
    if args.registry and args.server:
        _write("tessera: error: --registry and --server cannot be used together", error=True)
        return 1
    command = args.agents_command
    if command is None:
        _write(
            "usage: tessera agents "
            "{register,ls,show,activate,suspend,retire,set-policy,token,apply}",
            error=True,
        )
        return 1
    backend = _agents_backend(args)
    try:
        return _run_agents(args, backend)
    finally:
        closer = getattr(backend, "close", None)
        if callable(closer):
            closer()


def _run_agents(args: argparse.Namespace, backend: RegistryBackend) -> int:
    """Dispatch one agents subcommand.

    Args:
        args: Parsed arguments.
        backend: Open registry.

    Returns:
        The command's exit code.
    """
    command = args.agents_command
    if command == "apply":
        return _agents_apply(args, backend)
    if command == "token":
        return _agents_token(args, backend)
    status = {"activate": "active", "suspend": "suspended", "retire": "retired"}.get(command)
    if status is not None:
        return _agents_status(args, backend, cast(AgentStatus, status))
    handlers = {
        "register": _agents_register,
        "ls": _agents_ls,
        "show": _agents_show,
        "set-policy": _agents_set_policy,
    }
    return handlers[command](args, backend)


def _agents_backend(args: argparse.Namespace) -> RegistryBackend:
    """Open the local file or the control plane.

    Args:
        args: Parsed agents arguments.

    Returns:
        The backend selected by `--server`, `TESSERA_URL`, and `--registry`.
        An explicit `--registry` stays on the local file.
    """
    if args.server or (os.environ.get("TESSERA_URL") and not args.registry):
        url = args.server or os.environ.get("TESSERA_URL", "")
        return open_server_backend(url)
    configured = args.registry or os.environ.get("TESSERA_REGISTRY")
    path = configured if configured else Path(".tessera") / "registry.yaml"
    return LocalBackend(path)


def _agents_register(args: argparse.Namespace, backend: RegistryBackend) -> int:
    """Register an agent and optionally activate it.

    Args:
        args: Parsed `agents register` arguments.
        backend: Registry to change.

    Returns:
        `0` when the agent is stored.
    """
    entry = RegistryEntry(
        name=args.name,
        environment=args.env,
        owner=args.owner,
        policy=args.policy,
        description=args.description,
        risk_tier=args.risk_tier,
        labels=_labels(args.label),
        autonomy=args.autonomy,
        supervisors=tuple(args.supervisor),
    )
    stored = backend.register(entry)
    if args.activate:
        stored = backend.set_status(stored.name, stored.environment, "active")
    _write(f"registered {stored.name} ({stored.environment}) [{stored.status}]")
    return 0


def _agents_ls(args: argparse.Namespace, backend: RegistryBackend) -> int:
    """List agents.

    Args:
        args: Parsed `agents ls` arguments. `--env` filters only when passed;
            the parser default for this command is no filter.
        backend: Registry to read.

    Returns:
        `0`.
    """
    agents = [
        item
        for item in backend.list()
        if (args.env is None or item.environment == args.env)
        and (args.status is None or item.status == args.status)
    ]
    if args.json:
        _write(json.dumps([item.model_dump(mode="json") for item in agents], indent=2))
        return 0
    rows: list[tuple[str, str, str, str, str]] = [("NAME", "ENV", "STATUS", "OWNER", "POLICY")]
    rows.extend(_agent_row(item) for item in agents)
    _write(_columns(rows))
    return 0


def _agents_show(args: argparse.Namespace, backend: RegistryBackend) -> int:
    """Show one agent.

    Args:
        args: Parsed `agents show` arguments.
        backend: Registry to read.

    Returns:
        `0` when the agent is registered.
    """
    entry = backend.get(args.name, args.env)
    if args.json:
        _write(json.dumps(entry.model_dump(mode="json"), indent=2))
        return 0
    _write(_columns([("NAME", "ENV", "STATUS", "OWNER", "POLICY"), _agent_row(entry)]))
    return 0


def _agents_status(args: argparse.Namespace, backend: RegistryBackend, status: AgentStatus) -> int:
    """Move an agent to `status`.

    Args:
        args: Parsed status-command arguments.
        backend: Registry to change.
        status: Status named by the subcommand.

    Returns:
        `0` when the transition is stored.
    """
    current = backend.get(args.name, args.env)
    updated = backend.set_status(args.name, args.env, status)
    _write(f"{args.name} ({args.env}): {current.status} -> {updated.status}")
    return 0


def _agents_set_policy(args: argparse.Namespace, backend: RegistryBackend) -> int:
    """Replace an agent's policy path.

    Args:
        args: Parsed `agents set-policy` arguments.
        backend: Registry to change.

    Returns:
        `0` when the path is stored.
    """
    updated = backend.set_policy(args.name, args.env, Path(args.policy))
    _write(f"{args.name} ({args.env}): policy {updated.policy}")
    return 0


def _agents_apply(args: argparse.Namespace, backend: RegistryBackend) -> int:
    """Make a backend match a registry file.

    Args:
        args: Parsed `agents apply` arguments.
        backend: Registry to change.

    Returns:
        `1` when any entry could not be moved to its desired status.
    """
    path = Path(args.file)
    changes = plan(load_registry(path), path.parent, backend)
    if not args.dry_run:
        apply_plan(changes, backend)
    for change in changes:
        if change.action != "baseline":
            _write(render(change))
    return 1 if any(change.action == "error" for change in changes) else 0


def _agents_token(args: argparse.Namespace, backend: RegistryBackend) -> int:
    """Print a registration token, or reject the local backend.

    Args:
        args: Parsed `agents token` arguments.
        backend: Open registry.

    Returns:
        `0` when the token was printed.

    Raises:
        RegistryError: The local backend cannot issue registration tokens.
    """
    if not isinstance(backend, ServerBackend):
        raise RegistryError("registration tokens require a server (--server or TESSERA_URL)")
    _write(
        backend.issue_token(
            args.name,
            args.env,
            reusable=bool(args.reusable),
            expires_in=args.expires_in,
        )
    )
    return 0


def _labels(items: Sequence[str]) -> dict[str, str]:
    """Parse `KEY=VALUE` labels.

    Args:
        items: Raw `--label` values.

    Returns:
        Label names mapped to their values.

    Raises:
        RegistryError: If an item has no `=`.
    """
    labels: dict[str, str] = {}
    for item in items:
        key, separator, value = item.partition("=")
        if separator == "" or key == "":
            raise RegistryError(f"label must be KEY=VALUE, got {item!r}")
        labels[key] = value
    return labels


def _agent_row(entry: RegistryEntry) -> tuple[str, str, str, str, str]:
    """Return the text columns for one agent.

    Args:
        entry: Agent to format.

    Returns:
        Name, environment, status, owner, and policy path. A missing policy
        is `-`.
    """
    policy = "-" if entry.policy is None else entry.policy
    return (entry.name, entry.environment, entry.status, entry.owner, policy)


def _columns(rows: Sequence[Sequence[str]]) -> str:
    """Align rows with two spaces between columns.

    Args:
        rows: Header and data rows of equal length.

    Returns:
        One line per row.
    """
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    lines: list[str] = []
    for row in rows:
        cells = [cell.ljust(widths[index]) for index, cell in enumerate(row)]
        lines.append("  ".join(cells).rstrip())
    return "\n".join(lines)


__all__ = ["build_parser", "main"]
