"""In-process client that evaluates policy and writes the audit log."""

from __future__ import annotations

import inspect
import json
import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from functools import wraps
from importlib.metadata import version
from pathlib import Path
from typing import Literal, Self, TypeVar, cast
from uuid import uuid4

import httpx

from agent_tessera.adapters import wrap as wrap_client
from agent_tessera.audit import AuditLog
from agent_tessera.bypass import install as install_bypass
from agent_tessera.context import (
    Task,
    _begin_span,
    _clear_pending_span,
    _current_call_meta,
    _current_chain,
    _current_span_id,
    _peek_pending_span,
    _PendingSpan,
    _take_pending_span,
    _use_call_meta,
    _use_chain,
    _use_inbound_parent_span,
    _use_span,
    active_tasks,
    clear_guardrails,
    current_principal,
    current_task,
    guardrails_ran,
    mark_guardrail_ran,
    use_principal,
    use_task,
)
from agent_tessera.delegation import chain_headers, parent_span_from_headers
from agent_tessera.effects import EffectLedger, classify_effect
from agent_tessera.errors import ApprovalRequired, ConfigError, Denied, ServerError, TesseraError
from agent_tessera.limits import LimitTracker
from agent_tessera.otel import CallSpan, configure, set_attributes, start_call_span, trace_span
from agent_tessera.policy.effective import EffectivePolicy, QuotaLimit, build_effective_policy
from agent_tessera.policy.evaluator import RuntimeState, evaluate
from agent_tessera.policy.loader import load_agent_policy, load_baseline_policy
from agent_tessera.policy.models import AgentPolicy, BaselinePolicy
from agent_tessera.pricing import (
    PriceTable,
    claim_unknown_model,
    estimate_cost,
    load_price_table,
    model_price,
)
from agent_tessera.quotas import (
    LocalQuotas,
    count_amount,
    model_reservation,
    quota_applies,
    reserved_input_tokens,
    server_leased,
    text_chars,
    usage_amount,
)
from agent_tessera.registry.store import LocalRegistry
from agent_tessera.remote.connection import ServerConnection, ServerSettings, resolve_server
from agent_tessera.remote.custody import (
    HeldKey,
    inject_provider_key,
    issue_provider_key,
    restore_provider_keys,
)
from agent_tessera.remote.events import EventUploader, remote_event
from agent_tessera.remote.http import RemoteError
from agent_tessera.remote.leases import LeaseKeeper, QuotaKeeper
from agent_tessera.remote.stream import RevocationListener
from agent_tessera.supervision import resolve_supervisor
from agent_tessera.types import Action, ActionKind, Decision, Effect, Principal, Reason

_model_hold: ContextVar[_ModelHold | None] = ContextVar("tessera_model_hold", default=None)


@dataclass
class _ModelHold:
    """Tokens held for one in-flight model call."""

    legacy: int
    parts: tuple[tuple[str, float], ...]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    principal: str | None
    run_id: str | None
    resource: str
    reconciled: bool = False


logger = logging.getLogger("agent_tessera")

F = TypeVar("F", bound=Callable[..., object])
T = TypeVar("T")
ApprovalHandler = Callable[[Decision], bool]
_ApprovalMode = Literal["handler", "server", "raise"]
_APPROVAL_POLL = 0.05
_APPROVAL_GRACE = 1.0
_STATUS_OK = 200
_STATUS_UNPROCESSABLE = 422
_approval_wait_ms: ContextVar[int | None] = ContextVar("tessera_approval_wait_ms", default=None)


class Tessera:
    """Govern one agent from a local policy and an append-only audit log.

    The client evaluates actions in process. `check` reports a decision.
    `enforce` raises when the action is denied or still needs approval.

    Args:
        agent: Agent name. It must match the loaded policy.
        policy: Agent policy object or file. Falls back to `TESSERA_POLICY`.
        baselines: Baseline policies or files. Falls back to `TESSERA_BASELINES`.
        audit_path: Audit log file. Falls back to `TESSERA_AUDIT_PATH`, then
            `.tessera/audit.jsonl` in the current directory.
        suspended: Start suspended when `True`. Falls back to
            `TESSERA_SUSPENDED` being `1`.
        approval_handler: Called with the decision when approval is required.
            `True` allows the action. `None` leaves approval unresolved.
        approval_mode: `handler` calls `approval_handler`, `server` asks the
            control plane, and `raise` raises `ApprovalRequired`. The default
            is `server` in server mode, otherwise `handler` when a handler is
            set, otherwise `raise`.
        approval_wait: When `True` in server mode, block until the approval
            is decided or the policy timeout passes. When `False`, raise
            `ApprovalRequired` with `approval_id`.
        server_url: Control-plane URL. Falls back to `TESSERA_URL`. Unset
            keeps the client in offline mode.
        agent_token: Registration token. Falls back to `TESSERA_AGENT_TOKEN`.
        fail_mode: `open` or `closed` when the server is unreachable. Falls
            back to `TESSERA_FAIL_MODE`, then `open` when `TESSERA_ENV` is
            `dev`.
        detect_bypass: When `True`, or when `TESSERA_DETECT_BYPASS` is `1`,
            record provider HTTP calls that did not pass through an adapter.
            The call is not blocked.

    Example:
        ```python
        from agent_tessera import AgentPolicy, Tessera
        from agent_tessera.types import Action

        policy = AgentPolicy.model_validate(
            {
                "apiVersion": "tessera/v1",
                "kind": "AgentPolicy",
                "metadata": {"agent": "bot"},
                "spec": {"tools": {"allow": ["files.read"]}},
            }
        )
        with Tessera("bot", policy=policy, audit_path="audit.jsonl") as client:
            client.enforce(Action.tool("files.read"))
        ```
    """

    _bound_supervisor: str | None = None
    _registry_supervisors: tuple[str, ...] = ()
    _wrapped_clients: list[object] | None = None

    def __init__(
        self,
        agent: str,
        *,
        policy: str | Path | AgentPolicy | None = None,
        baselines: Sequence[str | Path | BaselinePolicy] | None = None,
        audit_path: str | Path | None = None,
        suspended: bool | None = None,
        approval_handler: ApprovalHandler | None = None,
        approval_mode: _ApprovalMode | None = None,
        approval_wait: bool = True,
        server_url: str | None = None,
        agent_token: str | None = None,
        fail_mode: Literal["closed", "open"] | None = None,
        price_table: str | Path | None = None,
        detect_bypass: bool = False,
    ) -> None:
        """Load policy, open the audit log, and record a start event.

        Args:
            agent: Agent name. It must match the loaded policy.
            policy: Agent policy object or file. Falls back to `TESSERA_POLICY`.
            baselines: Baseline policies or files. Falls back to
                `TESSERA_BASELINES`.
            audit_path: Audit log file. Falls back to `TESSERA_AUDIT_PATH`,
                then `.tessera/audit.jsonl`.
            suspended: Start suspended when `True`. Falls back to
                `TESSERA_SUSPENDED` being `1`.
            approval_handler: Called when an action requires approval.
            approval_mode: `handler`, `server`, or `raise`. `None` selects
                `server` in server mode, `handler` when a handler is set, and
                `raise` otherwise.
            approval_wait: Block for a server approval when `True`. When
                `False`, raise `ApprovalRequired` carrying `approval_id`.
            server_url: Control-plane URL. Falls back to `TESSERA_URL`.
            agent_token: Registration token. Falls back to `TESSERA_AGENT_TOKEN`.
            fail_mode: `open` or `closed`. Falls back to `TESSERA_FAIL_MODE`.
            price_table: JSON price table. Falls back to `TESSERA_PRICE_TABLE`,
                then the built-in table.
            detect_bypass: Record ungoverned provider HTTP calls. Also enabled
                when `TESSERA_DETECT_BYPASS` is `1`. The call is not blocked.

        Raises:
            ConfigError: If no policy is configured, the agent name does not
                match, server mode is combined with a local policy, the price
                table cannot be read, or `approval_mode` is `server` without
                a control plane.
            ServerError: If server mode cannot obtain a policy and fail mode
                is closed.
        """
        self._agent = agent
        self._instance_id = uuid4().hex
        configure()
        self._approval_handler = approval_handler
        self._approval_wait = approval_wait
        self._approval_mode: _ApprovalMode = "raise"
        self._suspended = _initial_suspended(suspended)
        self._refresh_rejected = False
        self._bundle_suspended = False
        self._bundle_unusable = False
        self._environment = ""
        self._closed = False
        self._mode: Literal["offline", "server"] = "offline"
        self._fail_mode = "closed"
        self._policy: EffectivePolicy | None = None
        self._tracker: LimitTracker | None = None
        self._audit: AuditLog | None = None
        self._connection: ServerConnection | None = None
        self._uploader: EventUploader | None = None
        self._listener: RevocationListener | None = None
        self._leases: LeaseKeeper | None = None
        self._named: QuotaKeeper | None = None
        self._local = LocalQuotas()
        self._effects = EffectLedger()
        self._server_suspended = False
        self._registry: LocalRegistry | None = None
        self._registry_suspended = False
        self._registry_stamp: tuple[int, int] | None = None
        self._registry_checked = 0.0
        self._revoked_runs: set[str] = set()
        self._revoked_lock = threading.Lock()
        self._denial_times: list[datetime] = []
        self._denial_lock = threading.Lock()
        self._in_flight_model = 0
        self._in_flight_tool = 0
        self._flight_lock = threading.Lock()
        self._price_table = _load_price_table(price_table)
        self._bypass_detach: Callable[[], None] | None = None
        self._detect_bypass = detect_bypass
        settings = resolve_server(server_url, agent_token, fail_mode)
        self._approval_mode = _select_approval_mode(
            "offline" if settings is None else "server",
            approval_handler,
            approval_mode,
        )
        if self._approval_mode == "server" and settings is None:
            raise ConfigError("approval_mode server requires a control plane")
        if settings is None:
            self._init_offline(policy, baselines, audit_path)
        else:
            self._init_server(policy, baselines, audit_path, settings)
        self._arm_bypass()

    def _init_offline(
        self,
        policy: str | Path | AgentPolicy | None,
        baselines: Sequence[str | Path | BaselinePolicy] | None,
        audit_path: str | Path | None,
    ) -> None:
        """Load a local policy and open the audit log.

        Args:
            policy: Agent policy object or file.
            baselines: Baseline policies or files.
            audit_path: Audit log file.

        Raises:
            ConfigError: If no policy is configured, the agent name does not
                match the policy, or a local registry does not allow the agent.
        """
        registry_path = _registry_path()
        if registry_path is not None:
            policy, baselines = self._from_registry(policy, baselines, registry_path)
        loaded = _load_agent(policy)
        if loaded.metadata.agent != self._agent:
            raise ConfigError(
                f"agent {self._agent} does not match policy agent {loaded.metadata.agent}"
            )
        effective = build_effective_policy(loaded, _load_baselines(baselines))
        self._policy = effective
        self._environment = effective.environment
        self._tracker = LimitTracker(effective)
        self._audit = AuditLog(_resolve_audit_path(audit_path))
        _warn_unenforced(effective)
        self._lifecycle("start")

    def _from_registry(
        self,
        policy: str | Path | AgentPolicy | None,
        baselines: Sequence[str | Path | BaselinePolicy] | None,
        path: Path,
    ) -> tuple[str | Path | AgentPolicy | None, Sequence[str | Path | BaselinePolicy] | None]:
        """Require a registered agent and fill in its policy when needed.

        Args:
            policy: Explicit policy, which wins over the registry.
            baselines: Explicit baselines, which win over the registry.
            path: Registry file.

        Returns:
            The policy and baselines to load.

        Raises:
            ConfigError: If the agent is missing, draft, or retired.
        """
        registry = LocalRegistry(path)
        environment = _registry_environment(policy)
        entry = registry.find(self._agent, environment)
        if entry is None:
            raise ConfigError(
                f"agent {self._agent} ({environment}) is not registered; "
                f"run: tessera agents register {self._agent} ..."
            )
        if entry.status in {"draft", "retired"}:
            raise ConfigError(f"agent {self._agent} ({environment}) is {entry.status}")
        chosen_policy = policy
        if policy is None and not os.environ.get("TESSERA_POLICY"):
            chosen_policy = registry.resolve_policy(entry)
        chosen_baselines: Sequence[str | Path | BaselinePolicy] | None = baselines
        if baselines is None and not os.environ.get("TESSERA_BASELINES"):
            chosen_baselines = registry.resolve_baselines()
        self._registry = registry
        self._registry_supervisors = entry.supervisors
        self._registry_suspended = entry.status == "suspended"
        self._remember_registry()
        return chosen_policy, chosen_baselines

    def _remember_registry(self) -> None:
        """Record the registry file's stamp and the time it was read."""
        registry = self._registry
        if registry is None:
            return
        self._registry_checked = time.monotonic()
        try:
            stat = registry.path.stat()
        except OSError:
            self._registry_stamp = None
            return
        self._registry_stamp = (stat.st_mtime_ns, stat.st_size)

    def _watch_registry(self) -> None:
        """Reload the registry at most once a second when its file changes."""
        registry = self._registry
        if registry is None:
            return
        moment = time.monotonic()
        if moment - self._registry_checked < 1.0:
            return
        self._registry_checked = moment
        try:
            stat = registry.path.stat()
        except OSError as err:
            self._fail_registry(f"registry file cannot be read: {err}")
            return
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp == self._registry_stamp:
            return
        self._registry_stamp = stamp
        try:
            loaded = registry.load()
        except TesseraError as err:
            self._fail_registry(str(err))
            return
        entry = next(
            (
                item
                for item in loaded.agents
                if item.name == self._agent and item.environment == self._environment
            ),
            None,
        )
        if entry is None or entry.status != "active":
            detail = "removed from the registry" if entry is None else entry.status
            self._fail_registry(f"agent {self._agent} ({self._environment}) is {detail}")
            return
        self._registry_supervisors = entry.supervisors
        self._resume_registry()

    def _fail_registry(self, message: str) -> None:
        """Suspend the client because the registry no longer allows it.

        Args:
            message: Warning logged once for this transition.
        """
        if self._registry_suspended:
            return
        self._registry_suspended = True
        logger.warning("%s", message)
        self._lifecycle("registry_suspend")

    def _resume_registry(self) -> None:
        """Clear a registry suspension after the entry is active again."""
        if not self._registry_suspended:
            return
        self._registry_suspended = False
        self._lifecycle("registry_resume")

    def _init_server(
        self,
        policy: str | Path | AgentPolicy | None,
        baselines: Sequence[str | Path | BaselinePolicy] | None,
        audit_path: str | Path | None,
        settings: ServerSettings,
    ) -> None:
        """Register with the control plane and start refresh and bundle polling.

        Args:
            policy: Local policy, which server mode rejects.
            baselines: Local baselines, which server mode rejects.
            audit_path: Audit log file. The file is opened only when this or
                `TESSERA_AUDIT_PATH` is set.
            settings: Resolved server settings.

        Raises:
            ConfigError: If a local policy is also configured, or the server
                names a different agent.
            ServerError: If no usable bundle is available and fail mode is
                closed.
        """
        if policy is not None or baselines is not None or os.environ.get("TESSERA_POLICY"):
            raise ConfigError("policy comes from the server in server mode")
        self._mode = "server"
        self._fail_mode = settings.fail_mode
        self._bound_supervisor = settings.bound_supervisor
        explicit_audit = audit_path is not None or bool(os.environ.get("TESSERA_AUDIT_PATH"))
        self._audit = AuditLog(_resolve_audit_path(audit_path)) if explicit_audit else None
        self._connection = ServerConnection(
            self._agent,
            self._instance_id,
            settings,
            on_policy=self._accept_policy,
            on_unusable=self._mark_unusable,
            on_rejected=self._mark_rejected,
        )
        try:
            self._connection.start()
        except Exception:
            self._connection.close()
            self._connection = None
            raise
        self._uploader = EventUploader(
            self._connection._http,
            self._connection.access_token,
            self._agent,
            self._instance_id,
            settings.spool_dir,
        )
        self._uploader.start()
        self._listener = RevocationListener(
            self._connection._http,
            self._connection.access_token,
            self._on_revocation,
        )
        self._listener.start()
        if self._policy is not None:
            _warn_unenforced(self._policy)
        self._lifecycle("start")

    def _accept_policy(self, policy: EffectivePolicy, status: str) -> None:
        """Swap in a bundle policy and reset counters when the version changes.

        Args:
            policy: Effective policy from the bundle.
            status: Bundle status, `active` or `suspended`.
        """
        changed = self._policy is not None and self._policy.version != policy.version
        self._policy = policy
        self._environment = policy.environment
        self._bundle_unusable = False
        self._bundle_suspended = status == "suspended"
        if self._tracker is None or changed:
            self._tracker = LimitTracker(policy)
        self._sync_lease_limit(policy)
        if changed and not self._closed:
            self._lifecycle("policy_update")

    def _mark_unusable(self) -> None:
        """Stop using a bundle that expired before a replacement arrived."""
        self._bundle_unusable = True

    def _mark_rejected(self) -> None:
        """Deny every action after the server rejects a credential refresh."""
        self._refresh_rejected = True

    @property
    def agent(self) -> str:
        """Return the agent name.

        Returns:
            The agent name passed to the constructor.
        """
        return self._agent

    @property
    def instance_id(self) -> str:
        """Return this client's id.

        Returns:
            A random 32-character hex id, stable for the life of the client.
        """
        return self._instance_id

    @property
    def policy(self) -> EffectivePolicy | None:
        """Return the effective policy.

        Returns:
            The agent policy after applicable baselines were merged. `None`
            only while server mode is fail-open and no bundle is available.
        """
        return self._policy

    @property
    def mode(self) -> Literal["offline", "server"]:
        """Return whether decisions use a local policy or the control plane.

        Returns:
            `offline` or `server`.
        """
        return self._mode

    @property
    def suspended(self) -> bool:
        """Return whether the client is suspended.

        Returns:
            `True` when the local flag, the registry, the bundle, the
            revocation stream, or a rejected refresh says so. `resume` clears
            only the local flag.
        """
        return (
            self._suspended
            or self._registry_suspended
            or self._bundle_suspended
            or self._server_suspended
            or self._refresh_rejected
        )

    def flush(self, timeout: float = 5.0) -> bool:
        """Upload events buffered for the control plane.

        Args:
            timeout: How long to wait, in seconds.

        Returns:
            `True` when nothing remains buffered. Offline mode has no buffer.
        """
        if self._uploader is None:
            return True
        return self._uploader.flush(timeout)

    def check(self, action: Action) -> Decision:
        """Evaluate an action without changing counters.

        Args:
            action: Action to evaluate. The active principal is attached when
                the action does not already name one.

        Returns:
            The decision. A denial is returned, not raised.

        Example:
            ```python
            decision = client.check(Action.tool("files.read"))
            assert decision.effect in {"allow", "deny", "require_approval"}
            ```
        """
        self._watch_registry()
        prepared = _with_principal(action)
        special = self._server_decision(prepared)
        if special is not None:
            self._write_decision(special, capture_content=False)
            return special
        if self._policy is None:
            raise ConfigError("policy is required")
        with trace_span("tessera.evaluate", self._trace_fields()) as span:
            decision = evaluate(self._policy, prepared, self._runtime_state(prepared))
            set_attributes(span, _decision_attributes(decision))
        self._remember_denial(decision)
        self._write_decision(decision, capture_content=self._policy.capture_content)
        self._apply_breaker(decision)
        return decision

    def enforce(self, action: Action) -> Decision:
        """Evaluate an action and commit it when it is allowed.

        Args:
            action: Action to evaluate and, when allowed, count.

        Returns:
            The allowing decision. Approval that is granted is returned as
            `allow` with reason `approval_granted`.

        Raises:
            Denied: If the action is denied, an approval handler rejects it,
                or a server approval is denied or times out.
            ApprovalRequired: If approval is required and the client is not
                waiting on the server. In server mode with `approval_wait`
                false, `approval_id` is set.

        Example:
            ```python
            client.enforce(Action.tool("files.read"))
            ```
        """
        with trace_span("tessera.enforce", self._trace_fields()) as span:
            decision = self.check(action)
            set_attributes(span, _decision_attributes(decision))
        if decision.effect is Effect.DENY:
            raise Denied(decision)
        if decision.effect is Effect.REQUIRE_APPROVAL:
            return self._resolve_approval(decision)
        self._commit(decision.action)
        return decision

    def suspend(self) -> None:
        """Suspend the client so every later action is denied.

        Example:
            ```python
            client.suspend()
            client.resume()
            ```
        """
        self._suspended = True
        self._lifecycle("suspend")

    def resume(self) -> None:
        """Clear the suspended flag.

        Example:
            ```python
            client.resume()
            ```
        """
        self._suspended = False
        self._lifecycle("resume")

    def task(
        self, task_id: str | None = None, *, supervisor: str | None = None
    ) -> AbstractContextManager[Task]:
        """Activate a task for this client until the block exits.

        Args:
            task_id: Id to store on the task. A random id is used when omitted.
            supervisor: Supervisor for this run. The first value is the run
                binding. A nested task can name its own.

        Returns:
            A context manager that yields the task. Nested tasks are allowed.

        Example:
            ```python
            with client.task() as task:
                client.enforce(Action.tool("files.read"))
                assert task.steps == 1
            ```
        """
        return use_task(Task(task_id or uuid4().hex, self._instance_id, supervisor=supervisor))

    def http_client(self) -> httpx.Client:
        """Return an HTTP client that attaches this agent's chain headers.

        Each request gets `Authorization: Bearer <runtime JWT>` when this
        client is in server mode, `Tessera-Run` when a task is active, and
        `X-Tessera-Parent-Span` when a governed call is on the stack.

        Returns:
            A sync `httpx.Client`. Callers own it and must close it.

        Example:
            ```python
            with client.task("run-1"):
                with client.http_client() as http:
                    http.get("https://worker.internal/work")
            ```
        """
        http = httpx.Client()

        def _attach(request: httpx.Request) -> None:
            token: str | None = None
            if self._connection is not None:
                token = self._connection.access_token()
            task = current_task(self._instance_id)
            run_id = None if task is None else task.id
            for key, value in chain_headers(
                access_token=token,
                run_id=run_id,
                parent_span_id=_current_span_id(),
            ).items():
                request.headers[key] = value

        http.event_hooks["request"].append(_attach)
        return http

    @contextmanager
    def handle_call(self, headers: Mapping[str, str]) -> Iterator[None]:
        """Bind an inbound parent span from request headers.

        Args:
            headers: Incoming HTTP headers. A malformed
                `X-Tessera-Parent-Span` is ignored.

        Yields:
            Nothing. Decisions inside the block parent to the inbound span
            when no local call is already on the stack.

        Example:
            ```python
            with worker.handle_call(request.headers):
                worker.enforce(Action.tool("files.read"))
            ```
        """
        with _use_inbound_parent_span(parent_span_from_headers(headers)):
            yield

    def receive_handoff(self, caller: Tessera) -> AbstractContextManager[Task | None]:
        """Join the caller's run and principal for an in-process hop.

        The process-wide principal stays active. When the caller has a task,
        this client opens a task with the same id so both agents share the
        run for the duration of the block. The caller's active span becomes
        the inbound parent for decisions in this block when the call stack
        does not already supply one.

        Args:
            caller: Client that is handing work to this one.

        Returns:
            A context manager that yields the joined task, or `None` when the
            caller has no active task.

        Example:
            ```python
            with orchestrator.task("run-1"):
                with worker.receive_handoff(orchestrator) as run:
                    assert run is not None and run.id == "run-1"
            ```
        """
        return self._receive_handoff(caller)

    @contextmanager
    def _receive_handoff(self, caller: Tessera) -> Iterator[Task | None]:
        """Activate the caller's run id on this client.

        Args:
            caller: Client that is handing work to this one.

        Yields:
            The joined task, or `None` when the caller has no active task.
        """
        parent = _current_span_id()
        with (
            _use_inbound_parent_span(parent),
            _use_chain((*_current_chain(), caller.agent)),
        ):
            caller_task = current_task(caller.instance_id)
            principal = current_principal()
            if caller_task is None:
                yield None
                return
            joined = Task(caller_task.id, self._instance_id, supervisor=caller_task.supervisor)
            if principal is None:
                with use_task(joined) as task:
                    yield task
                return
            with use_principal(principal), use_task(joined) as task:
                yield task

    def record_effect(self, ref: str, *, reversible: bool = False, undo: str | None = None) -> None:
        """Remember an opaque change made by the current run.

        Args:
            ref: Caller-supplied id, such as `zendesk:ticket/123`. Content
                does not belong here.
            reversible: Whether the caller says the change can be undone.
            undo: Name of a tool that would undo the change. Tessera stores
                the name and does not call it.

        Raises:
            ConfigError: If `ref` or `undo` is not usable.

        Example:
            ```python
            with client.task("run-1"):
                client.record_effect("zendesk:ticket/123", reversible=True, undo="zendesk.reopen")
            ```
        """
        task = current_task(self._instance_id)
        run_id = None if task is None else task.id
        record = self._effects.append(ref, reversible=reversible, undo=undo, run_id=run_id)
        fields: dict[str, object] = {
            "ref": record.ref,
            "reversible": record.reversible,
            "undo": record.undo,
        }
        span_id = _current_span_id()
        if span_id is not None:
            fields["span_id"] = span_id
        self._write("effect", fields)

    def on_behalf_of(
        self,
        user: str,
        groups: Iterable[str] = (),
        *,
        user_token: str | None = None,
    ) -> AbstractContextManager[Principal]:
        """Act for a user until the block exits.

        Offline, and in server mode without `user_token`, `user` is trusted.
        In server mode, `user_token` is exchanged for a delegated runtime JWT
        that is used until the block exits.

        Args:
            user: User identifier.
            groups: Groups the user belongs to.
            user_token: User access or ID token. Keyword-only. Server mode
                exchanges it. Offline mode ignores it.

        Returns:
            A context manager that yields the principal. The principal is
            shared by every client in the process.

        Raises:
            ServerError: In server mode, if the exchange is rejected or there
                is no runtime JWT.

        Example:
            ```python
            with client.on_behalf_of("ada", ["support"]):
                client.check(Action.tool("files.read"))
            ```
        """
        if user_token is not None and self._connection is not None:
            return self._exchange_principal(user, groups, user_token)
        return use_principal(Principal(user, frozenset(groups)))

    @contextmanager
    def _exchange_principal(
        self,
        user: str,
        groups: Iterable[str],
        user_token: str,
    ) -> Iterator[Principal]:
        """Exchange `user_token` and activate the trusted principal.

        Args:
            user: User identifier.
            groups: Groups the user belongs to.
            user_token: User access or ID token.

        Yields:
            The principal for the block.

        Raises:
            ServerError: If the connection is gone or the exchange is rejected.
        """
        connection = self._connection
        if connection is None:
            raise ServerError("runtime token is missing")
        with (
            connection._use_user_token(user_token),
            use_principal(Principal(user, frozenset(groups))) as principal,
        ):
            yield principal

    def tool(self, name: str) -> Callable[[F], F]:
        """Wrap a function so each call is enforced as a tool action.

        Args:
            name: Tool name recorded in the decision and the call event.

        Returns:
            A decorator for a sync or async function. `self` and `cls` are
            omitted from the audited arguments.

        Example:
            ```python
            @client.tool("files.read")
            def read(path: str) -> str:
                return path
            ```
        """

        def decorate(fn: F) -> F:
            if inspect.iscoroutinefunction(fn):

                @wraps(fn)
                async def async_tool(*args: object, **kwargs: object) -> object:
                    arguments = _bound_arguments(fn, args, kwargs)
                    self.enforce(Action.tool(name, arguments))
                    with _use_call_meta(arguments=arguments):
                        return await _await_call(self, fn, args, kwargs, kind="tool", resource=name)

                return cast(F, async_tool)

            @wraps(fn)
            def sync_tool(*args: object, **kwargs: object) -> object:
                arguments = _bound_arguments(fn, args, kwargs)
                self.enforce(Action.tool(name, arguments))
                with _use_call_meta(arguments=arguments):
                    return _call(self, "tool", name, lambda: fn(*args, **kwargs))

            return cast(F, sync_tool)

        return decorate

    def guardrail(self, name: str) -> Callable[[F], F]:
        """Mark a guardrail as ran when the wrapped function returns.

        Args:
            name: Guardrail name, such as `input.prompt_injection`.

        Returns:
            A decorator for a sync or async function. An exception leaves the
            guardrail unmarked.

        Example:
            ```python
            @client.guardrail("input.prompt_injection")
            def scan(prompt: str) -> None:
                return None
            ```
        """

        def decorate(fn: F) -> F:
            if inspect.iscoroutinefunction(fn):

                @wraps(fn)
                async def async_guard(*args: object, **kwargs: object) -> object:
                    result = await fn(*args, **kwargs)
                    self._mark_guardrail(name)
                    return result

                return cast(F, async_guard)

            @wraps(fn)
            def sync_guard(*args: object, **kwargs: object) -> object:
                result = fn(*args, **kwargs)
                self._mark_guardrail(name)
                return result

            return cast(F, sync_guard)

        return decorate

    def model_call(self, name: str) -> Callable[[F], F]:
        """Wrap a function as a governed model call.

        Args:
            name: Model resource, such as `ollama/llama3`.

        Returns:
            A decorator for a sync or async function. Missing `warn` and
            `flag` input guardrails are recorded, then the call is enforced.
            Guardrails are cleared after the function returns.

        Example:
            ```python
            @client.model_call("local/echo")
            def complete(prompt: str) -> str:
                return prompt
            ```
        """

        def decorate(fn: F) -> F:
            if inspect.iscoroutinefunction(fn):

                @wraps(fn)
                async def async_model(*args: object, **kwargs: object) -> object:
                    owned = self._open_model_hold(name, args, kwargs)
                    held: list[HeldKey] = []
                    try:
                        self._prepare_model(name)
                        held = self._open_custody(name)
                        return await _await_call(
                            self,
                            fn,
                            args,
                            kwargs,
                            kind="model",
                            resource=name,
                            clear=True,
                        )
                    finally:
                        restore_provider_keys(held)
                        if owned:
                            self._close_model_hold()

                return cast(F, async_model)

            @wraps(fn)
            def sync_model(*args: object, **kwargs: object) -> object:
                owned = self._open_model_hold(name, args, kwargs)
                held: list[HeldKey] = []
                try:
                    self._prepare_model(name)
                    held = self._open_custody(name)
                    return _call(self, "model", name, lambda: fn(*args, **kwargs), clear=True)
                finally:
                    restore_provider_keys(held)
                    if owned:
                        self._close_model_hold()

            return cast(F, sync_model)

        return decorate

    def record_usage(self, model: str, input_tokens: int, output_tokens: int) -> None:
        """Add token usage and estimated cost to today's total and the audit log.

        An unknown model is priced at zero and emits one `cost.unknown_model`
        event for this process.

        Args:
            model: Model name the tokens belong to.
            input_tokens: Prompt tokens.
            output_tokens: Completion tokens.

        Example:
            ```python
            client.record_usage("local/echo", 3, 4)
            ```
        """
        count = input_tokens + output_tokens
        if self._tracker is not None:
            self._tracker.record_tokens(count)
        cost = estimate_cost(model, input_tokens, output_tokens, self._price_table)
        self._add_run_cost(float(cost))
        hold = _model_hold.get()
        if hold is not None and not hold.reconciled:
            self._reconcile_hold(hold, model, input_tokens, output_tokens, float(cost))
            hold.reconciled = True
        else:
            if self._leases is not None:
                self._leases.record(count)
            self._charge_model(model, input_tokens, output_tokens, float(cost))
        if model_price(model, self._price_table) is None and claim_unknown_model(model):
            self._write("cost.unknown_model", {"model": model})
        self._write(
            "usage",
            {
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": format(cost, "f"),
            },
        )

    def wrap(self, client: T) -> T:
        """Return a governed view of a provider client.

        Args:
            client: OpenAI, Azure OpenAI, or Anthropic client.

        Returns:
            A proxy with the same interface. Model calls are enforced.

        Raises:
            ConfigError: If the client type is not supported.

        Example:
            ```python
            governed = client.wrap(openai_client)
            governed.chat.completions.create(model="gpt-4.1-mini")
            ```
        """
        self._remember_wrapped(client)
        return wrap_client(self, client)

    def _remember_wrapped(self, client: object) -> None:
        """Remember a provider client so a custody key can be injected.

        Args:
            client: Provider client passed to `wrap`.
        """
        clients = self._wrapped_clients
        if clients is None:
            clients = []
            self._wrapped_clients = clients
        clients.append(client)

    def _open_custody(self, resource: str) -> list[HeldKey]:
        """Issue a provider key and set it on matching wrapped clients.

        Offline mode and a client that did not set `TESSERA_KEY_CUSTODY=1`
        skip the request. The key is not written to the audit log.

        Args:
            resource: Model resource, such as `openai/gpt-4o`.

        Returns:
            Clients whose `api_key` was replaced. Empty when custody is off.

        Raises:
            ServerError: When custody is on and the control plane does not
                return a key.
        """
        if os.environ.get("TESSERA_KEY_CUSTODY") != "1" or self._mode != "server":
            return []
        connection = self._connection
        if connection is None:
            return []
        provider = resource.split("/", 1)[0]
        api_key = issue_provider_key(connection, provider=provider)
        return inject_provider_key(self._wrapped_clients or [], provider, api_key)

    def close(self) -> None:
        """Stop background work, record a stop event, and upload it.

        A second call does nothing. Offline mode only closes the local audit log.

        Example:
            ```python
            client.close()
            client.close()
            ```
        """
        if self._closed:
            return
        self._closed = True
        if self._bypass_detach is not None:
            self._bypass_detach()
            self._bypass_detach = None
        if self._connection is not None:
            self._connection.stop()
        if self._uploader is not None:
            self._uploader.stop()
        if self._listener is not None:
            self._listener.stop()
        self._lifecycle("stop")
        self.flush(5.0)
        if self._leases is not None:
            self._leases.settle()
        if self._named is not None:
            self._named.settle()
        if self._connection is not None:
            self._connection.close()
        if self._listener is not None:
            self._listener.join()
        if self._audit is not None:
            self._audit.close()

    def __enter__(self) -> Self:
        """Return this client.

        Returns:
            The same client.
        """
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the client when the block exits.

        Args:
            exc_info: Exception information supplied by the `with` statement.
        """
        self.close()

    def _resolve_approval(self, decision: Decision) -> Decision:
        """Ask the approval handler and commit the action when it grants.

        Args:
            decision: Decision that required approval.

        Returns:
            An allow decision when the handler grants the action.

        Raises:
            ApprovalRequired: If no handler is configured.
            Denied: If the handler rejects the action.
        """
        if self._approval_mode == "server":
            return self._server_approval(decision)
        if self._approval_mode == "raise":
            raise ApprovalRequired(decision)
        handler = self._approval_handler
        if handler is None:
            raise ApprovalRequired(decision)
        granted = handler(decision)
        self._write("approval", _approval_fields(decision, granted=granted))
        if granted:
            allowed = Decision(
                Effect.ALLOW,
                Reason.APPROVAL_GRANTED,
                decision.action,
                decision.policy_version,
                decision.rule,
            )
            self._commit(allowed.action)
            return allowed
        raise Denied(
            Decision(
                Effect.DENY,
                Reason.APPROVAL_REJECTED,
                decision.action,
                decision.policy_version,
                decision.rule,
            )
        )

    def _approvals_available(self) -> bool:
        """Return whether this client can ask someone to approve an action.

        Returns:
            `True` in server mode, and in handler mode when a handler is set.
        """
        if self._approval_mode == "server":
            return True
        return self._approval_mode == "handler" and self._approval_handler is not None

    def _server_approval(self, decision: Decision) -> Decision:
        """Create a server approval and wait, or raise its id.

        Args:
            decision: Decision that required approval.

        Returns:
            An allow decision when the server records an approval.

        Raises:
            ApprovalRequired: When `approval_wait` is false. `approval_id` is set.
            Denied: When nobody can be asked, the request is denied, or it
                times out.
            ServerError: When the control plane cannot be used.
        """
        created = self._post_approval(decision)
        approval_id = str(created["id"])
        if not self._approval_wait:
            raise ApprovalRequired(decision, approval_id=approval_id)
        return self._wait_for_approval(decision, approval_id, created.get("expires_at"))

    def _post_approval(self, decision: Decision) -> dict[str, object]:
        """Store an approval on the server.

        Args:
            decision: Decision that required approval.

        Returns:
            The create response.

        Raises:
            Denied: When the server has nobody to ask.
            ServerError: When the control plane cannot be used.
        """
        connection = self._connection
        if connection is None:
            raise ServerError("approval requires a control plane")
        task = current_task(self._instance_id)
        principal = decision.action.principal
        body: dict[str, object] = {
            "action": {
                "kind": decision.action.kind.value,
                "resource": decision.action.resource,
            },
            "decision": {
                "effect": decision.effect.value,
                "reason": decision.reason.value,
                "rule": decision.rule,
            },
            "run_id": None if task is None else task.id,
            "principal": None if principal is None else principal.user,
        }
        try:
            response = connection.send("POST", "/v1/approvals", body)
        except RemoteError as err:
            raise ServerError("approval request failed") from err
        document = _json_object(response)
        if response.status_code == _STATUS_OK:
            return document
        if (
            response.status_code == _STATUS_UNPROCESSABLE
            and _error_code(document) == "approval_unavailable"
        ):
            raise Denied(
                Decision(
                    Effect.DENY,
                    Reason.APPROVAL_UNAVAILABLE,
                    decision.action,
                    decision.policy_version,
                    decision.rule,
                )
            )
        raise ServerError("approval request failed")

    def _wait_for_approval(
        self, decision: Decision, approval_id: str, expires_at: object
    ) -> Decision:
        """Poll until the approval is decided or its deadline passes.

        Args:
            decision: Decision that required approval.
            approval_id: Server approval id.
            expires_at: First `expires_at` from the create response.

        Returns:
            An allow decision when the status is `approved`.

        Raises:
            Denied: When the status is denied, timed out, cancelled, or the
                deadline passes while it is still pending.
            ServerError: When a poll fails.
        """
        started = time.monotonic()
        deadline = _expiry(expires_at)
        while True:
            document = self._read_approval(approval_id)
            status = document.get("status")
            later = _expiry(document.get("expires_at"))
            deadline = max(deadline, later)
            if status == "approved":
                return self._approval_granted(decision, approval_id, started)
            if status in {"denied", "timeout", "cancelled"}:
                reason = Reason.APPROVAL_REJECTED if status == "denied" else Reason.APPROVAL_TIMEOUT
                self._approval_settled(decision, approval_id, granted=False)
                raise Denied(
                    Decision(
                        Effect.DENY,
                        reason,
                        decision.action,
                        decision.policy_version,
                        decision.rule,
                    )
                )
            if datetime.now(UTC) > deadline + timedelta(seconds=_APPROVAL_GRACE):
                self._approval_settled(decision, approval_id, granted=False)
                raise Denied(
                    Decision(
                        Effect.DENY,
                        Reason.APPROVAL_TIMEOUT,
                        decision.action,
                        decision.policy_version,
                        decision.rule,
                    )
                )
            time.sleep(_APPROVAL_POLL)

    def _read_approval(self, approval_id: str) -> dict[str, object]:
        """Read one approval from the server.

        Args:
            approval_id: Server approval id.

        Returns:
            The approval document.

        Raises:
            ServerError: When the control plane cannot be used.
        """
        connection = self._connection
        if connection is None:
            raise ServerError("approval requires a control plane")
        try:
            response = connection.send("GET", f"/v1/approvals/{approval_id}")
        except RemoteError as err:
            raise ServerError("approval request failed") from err
        if response.status_code != _STATUS_OK:
            raise ServerError("approval request failed")
        return _json_object(response)

    def _approval_granted(self, decision: Decision, approval_id: str, started: float) -> Decision:
        """Record a granted server approval and commit the action.

        Args:
            decision: Decision that required approval.
            approval_id: Server approval id.
            started: Monotonic time when waiting began.

        Returns:
            An allow decision with reason `approval_granted`.
        """
        waited = round((time.monotonic() - started) * 1000)
        _approval_wait_ms.set(waited)
        self._approval_settled(decision, approval_id, granted=True)
        allowed = Decision(
            Effect.ALLOW,
            Reason.APPROVAL_GRANTED,
            decision.action,
            decision.policy_version,
            decision.rule,
        )
        self._commit(allowed.action)
        return allowed

    def _approval_settled(self, decision: Decision, approval_id: str, *, granted: bool) -> None:
        """Write an approval audit event without the action arguments.

        Args:
            decision: Decision that required approval.
            approval_id: Server approval id.
            granted: Whether the server approved the action.
        """
        fields = _approval_fields(decision, granted=granted)
        fields["approval_id"] = approval_id
        self._write("approval", fields)

    def _prepare_model(self, name: str) -> None:
        """Record missing input guardrails, then enforce the model call.

        Args:
            name: Model resource name.
        """
        if self._policy is None:
            self.enforce(Action.model(name))
            return
        ran = guardrails_ran(self._instance_id)
        for guardrail in self._policy.required_guardrails:
            if not guardrail.name.startswith("input."):
                continue
            if guardrail.on_missing not in {"warn", "flag"} or guardrail.name in ran:
                continue
            self._write("guardrail", {"name": guardrail.name, "status": "missing"})
            if guardrail.on_missing == "warn":
                logger.warning("guardrail %s did not run", guardrail.name)
        self.enforce(Action.model(name))

    def _mark_guardrail(self, name: str) -> None:
        """Record a guardrail that returned without raising.

        Args:
            name: Guardrail name.
        """
        mark_guardrail_ran(self._instance_id, name)
        self._write("guardrail", {"name": name, "status": "ran"})

    def _commit(self, action: Action) -> None:
        """Count an allowed model or tool call.

        Args:
            action: Action that was allowed.
        """
        task = current_task(self._instance_id)
        if task is not None and action.kind in {ActionKind.MODEL, ActionKind.TOOL}:
            task.steps += 1
        if self._tracker is not None:
            self._tracker.record_call(action)
        self._charge_count(action)

    def _runtime_state(self, action: Action | None = None) -> RuntimeState:
        """Snapshot counters for one evaluation.

        Args:
            action: Action being evaluated. Principal-scoped quotas use its
                principal. `None` reads only instance and agent counters.

        Returns:
            The runtime state for this client.
        """
        task = current_task(self._instance_id)
        tracker = self._tracker
        tokens = 0 if tracker is None else tracker.tokens_today()
        if self._leases is not None:
            leased = self._leases.tokens_today()
            if leased is not None:
                tokens = leased
        run_id = None if task is None else task.id
        hold = _model_hold.get()
        with self._flight_lock:
            models = self._in_flight_model
            tools = self._in_flight_tool
        return RuntimeState(
            suspended=self.suspended,
            run_revoked=self._task_revoked(task),
            guardrails_ran=guardrails_ran(self._instance_id),
            steps_in_task=None if task is None else task.steps,
            tokens_today=tokens,
            calls_last_minute={} if tracker is None else tracker.calls_last_minute(),
            approvals_available=self._approvals_available(),
            input_tokens=None if hold is None else hold.input_tokens,
            output_tokens=None if hold is None else hold.output_tokens,
            cost_usd=None if hold is None else hold.cost_usd,
            run_steps=0 if task is None else task.steps,
            run_cost_usd=0.0 if task is None else task.cost_usd,
            run_elapsed_seconds=None if task is None else _run_elapsed(task),
            run_id=run_id,
            in_flight_model_calls=models,
            in_flight_tool_calls=tools,
            concurrent_runs=max(0, len(active_tasks(self._instance_id)) - 1),
            quota_usage=self._quota_usage(action, run_id),
            recent_denials=self._recent_denials(),
            chain=_current_chain(),
            supervisor=self._supervisor_id(),
        )

    def _supervisor_id(self) -> str | None:
        """Resolve the supervisor for the active task.

        Returns:
            The run binding, then the instance binding, then the first static
            supervisor. `None` when none of those is set.
        """
        task = current_task(self._instance_id)
        run = None if task is None else task.supervisor
        return resolve_supervisor(run, self._bound_supervisor, self._static_supervisors())

    def _static_supervisors(self) -> tuple[str, ...]:
        """Return supervisors remembered from the last readable registry.

        Returns:
            The registry list, or empty when this client has no registry.
            A registry that can no longer be read keeps the previous list.
        """
        return self._registry_supervisors

    def _recent_denials(self) -> int:
        """Count denials still inside the circuit-breaker window.

        Returns:
            The number of remembered denials. Zero when the policy has no
            circuit breaker.
        """
        window = self._breaker_window()
        if window is None:
            return 0
        cutoff = datetime.now(UTC) - timedelta(seconds=window)
        with self._denial_lock:
            self._denial_times = [moment for moment in self._denial_times if moment >= cutoff]
            return len(self._denial_times)

    def _remember_denial(self, decision: Decision) -> None:
        """Remember a denial for the offline circuit breaker.

        Args:
            decision: Decision just produced. Circuit-open denials are not
                counted again.
        """
        if self._breaker_window() is None or decision.effect is not Effect.DENY:
            return
        if decision.reason is Reason.CIRCUIT_OPEN:
            return
        with self._denial_lock:
            self._denial_times.append(datetime.now(UTC))

    def _apply_breaker(self, decision: Decision) -> None:
        """Carry out the circuit-breaker action after it opens.

        Args:
            decision: Decision just produced. Any other reason is ignored.
                The returned decision stays `circuit_open`; the action changes
                the next check.
        """
        if decision.reason is not Reason.CIRCUIT_OPEN or self._policy is None:
            return
        breaker = self._policy.controls.circuit_breaker
        if breaker is None:
            return
        if breaker.action == "suspend":
            if not self._suspended:
                self.suspend()
            return
        task = current_task(self._instance_id)
        if task is None:
            return
        with self._revoked_lock:
            self._revoked_runs.add(task.id)

    def _add_run_cost(self, cost: float) -> None:
        """Add estimated spend to the active run.

        Args:
            cost: Dollars to add. A call with no active task is ignored.
        """
        task = current_task(self._instance_id)
        if task is None:
            return
        task.cost_usd += cost

    @contextmanager
    def _in_flight(self, kind: str) -> Iterator[None]:
        """Count a model or tool call until it returns.

        Args:
            kind: `model` or `tool`. Any other kind is not counted.

        Yields:
            Nothing.
        """
        tracked = kind in {"model", "tool"}
        if tracked:
            with self._flight_lock:
                if kind == "model":
                    self._in_flight_model += 1
                else:
                    self._in_flight_tool += 1
        try:
            yield
        finally:
            if tracked:
                with self._flight_lock:
                    if kind == "model":
                        self._in_flight_model -= 1
                    else:
                        self._in_flight_tool -= 1

    def _breaker_window(self) -> int | None:
        """Return the circuit-breaker window, in seconds.

        Returns:
            The merged window, or `None` when the policy sets no breaker.
        """
        if self._policy is None or self._policy.controls.circuit_breaker is None:
            return None
        return self._policy.controls.circuit_breaker.window_seconds

    def _sync_lease_limit(self, policy: EffectivePolicy) -> None:
        """Point the lease keeper at the limit in a new policy.

        Args:
            policy: Policy that was just installed.
        """
        limit = _daily_limit(policy)
        if self._leases is not None:
            self._leases.set_limit(limit)
        elif limit is not None and not self._closed and self._connection is not None:
            self._leases = LeaseKeeper(self._connection._http, self._connection.access_token, limit)
            self._leases.start()
        self._sync_named_leases(policy)

    def _sync_named_leases(self, policy: EffectivePolicy) -> None:
        """Start or retarget leases for named quotas.

        Args:
            policy: Policy that was just installed.
        """
        if self._closed or self._connection is None:
            return
        quotas = tuple(quota for quota in policy.controls.quotas if server_leased(quota))
        if self._named is None:
            if not quotas:
                return
            self._named = QuotaKeeper(self._connection._http, self._connection.access_token)
            self._named.replace(quotas)
            self._named.start()
            return
        self._named.replace(quotas)

    def _quota_usage(self, action: Action | None, run_id: str | None) -> dict[str, float]:
        """Read every stored quota counter.

        Args:
            action: Action being evaluated, or `None`.
            run_id: Active run id.

        Returns:
            Usage keyed by quota name. Call windows are left out; the evaluator
            measures those from this call.
        """
        policy = self._policy
        if policy is None:
            return {}
        principal = None if action is None or action.principal is None else action.principal.user
        moment = datetime.now(UTC)
        usage: dict[str, float] = {}
        for quota in policy.controls.quotas:
            if quota.window == "call":
                continue
            if self._named is not None and server_leased(quota):
                usage[quota.name] = self._named.usage(
                    quota, principal=principal, run_id=run_id, supervisor=self._supervisor_id()
                )
            else:
                usage[quota.name] = self._local.used(quota.name, quota.window, run_id, moment)
        return usage

    def _charge_count(self, action: Action) -> None:
        """Add one allowed call to the count quotas that include it.

        Args:
            action: Action that was allowed.
        """
        policy = self._policy
        if policy is None:
            return
        task = current_task(self._instance_id)
        run_id = None if task is None else task.id
        principal = None if action.principal is None else action.principal.user
        moment = datetime.now(UTC)
        for quota in policy.controls.quotas:
            amount = count_amount(quota.metric, action.kind)
            if amount <= 0 or not quota_applies(quota, action):
                continue
            self._add_quota(
                quota,
                amount,
                principal=principal,
                run_id=run_id,
                resource=action.resource,
                moment=moment,
            )

    def _charge_model(self, model: str, input_tokens: int, output_tokens: int, cost: float) -> None:
        """Add measured model usage to token and cost quotas.

        Args:
            model: Model resource.
            input_tokens: Prompt tokens.
            output_tokens: Completion tokens.
            cost: Estimated cost in dollars.
        """
        policy = self._policy
        if policy is None:
            return
        action = _with_principal(Action.model(model))
        task = current_task(self._instance_id)
        run_id = None if task is None else task.id
        principal = None if action.principal is None else action.principal.user
        moment = datetime.now(UTC)
        for quota in policy.controls.quotas:
            amount = usage_amount(
                quota.metric,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
            )
            if amount <= 0 or not quota_applies(quota, action):
                continue
            self._add_quota(
                quota, amount, principal=principal, run_id=run_id, resource=model, moment=moment
            )

    def _add_quota(
        self,
        quota: QuotaLimit,
        amount: float,
        *,
        principal: str | None,
        run_id: str | None,
        resource: str,
        moment: datetime,
    ) -> None:
        """Record usage on the local book or the server lease.

        Args:
            quota: Quota being charged.
            amount: Units in the quota's own scale.
            principal: Principal id.
            run_id: Active run id.
            resource: Resource name to send with a server lease.
            moment: Time that selects a local bucket.
        """
        if self._named is not None and server_leased(quota):
            self._named.consume(
                quota,
                amount,
                principal=principal,
                run_id=run_id,
                supervisor=self._supervisor_id(),
                resource=resource,
            )
            return
        self._local.consume(quota.name, quota.window, run_id, amount, moment)

    def _open_model_hold(
        self, name: str, args: tuple[object, ...], kwargs: Mapping[str, object]
    ) -> bool:
        """Hold the model reservation, or deny when it does not fit.

        Args:
            name: Model resource.
            args: Positional arguments of the model call.
            kwargs: Keyword arguments of the model call.

        Returns:
            `True` when this call created the hold. `False` when a caller
            already holds one.

        Raises:
            Denied: If a token or cost quota cannot cover the reservation.
        """
        if _model_hold.get() is not None:
            return False
        policy = self._policy
        cap = 0
        if policy is not None and policy.controls.max_output_tokens is not None:
            cap = int(policy.controls.max_output_tokens.value)
        chars = text_chars((args, kwargs))
        incoming = reserved_input_tokens(chars)
        reserved = model_reservation(chars, cap)
        cost = float(estimate_cost(name, incoming, cap, self._price_table))
        action = _with_principal(Action.model(name))
        task = current_task(self._instance_id)
        run_id = None if task is None else task.id
        principal = None if action.principal is None else action.principal.user
        legacy = 0
        if self._leases is not None:
            if not self._leases.try_reserve(reserved):
                self._deny_reservation(action, _daily_rule(policy))
            legacy = reserved
        parts: list[tuple[str, float]] = []
        if policy is not None:
            for quota in policy.controls.quotas:
                amount = _reservation_amount(quota, action, incoming, cap, cost)
                if amount <= 0:
                    continue
                if not self._reserve_quota(quota, amount, principal, run_id, name):
                    self._release_reservation(legacy, parts, principal, run_id)
                    self._deny_reservation(action, quota.rule)
                parts.append((quota.name, amount))
        _model_hold.set(
            _ModelHold(
                legacy,
                tuple(parts),
                incoming,
                cap,
                cost,
                principal,
                run_id,
                name,
            )
        )
        return True

    def _close_model_hold(self) -> None:
        """Release a reservation that usage recording did not reconcile."""
        hold = _model_hold.get()
        _model_hold.set(None)
        if hold is None or hold.reconciled:
            return
        self._release_reservation(hold.legacy, list(hold.parts), hold.principal, hold.run_id)

    def _reconcile_hold(
        self,
        hold: _ModelHold,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
    ) -> None:
        """Replace a model hold with measured usage.

        Args:
            hold: Hold taken before the call.
            model: Model resource.
            input_tokens: Prompt tokens.
            output_tokens: Completion tokens.
            cost: Estimated cost in dollars.
        """
        if self._leases is not None and hold.legacy:
            self._leases.reconcile(hold.legacy, input_tokens + output_tokens)
        policy = self._policy
        if policy is None:
            return
        by_name = {quota.name: quota for quota in policy.controls.quotas}
        for name, reserved in hold.parts:
            quota = by_name.get(name)
            if quota is None:
                continue
            actual = usage_amount(
                quota.metric,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
            )
            if self._named is not None and server_leased(quota):
                self._named.reconcile(
                    quota,
                    reserved,
                    actual,
                    principal=hold.principal,
                    run_id=hold.run_id,
                    supervisor=self._supervisor_id(),
                    resource=model,
                )
            else:
                self._local.reconcile(
                    quota.name,
                    quota.window,
                    hold.run_id,
                    reserved=reserved,
                    actual=actual,
                    moment=datetime.now(UTC),
                )

    def _reserve_quota(
        self,
        quota: QuotaLimit,
        amount: float,
        principal: str | None,
        run_id: str | None,
        resource: str,
    ) -> bool:
        """Hold units of one quota.

        Args:
            quota: Quota to hold.
            amount: Units in the quota's own scale.
            principal: Principal id.
            run_id: Active run id.
            resource: Model resource.

        Returns:
            `True` when the hold fits.
        """
        if self._named is not None and server_leased(quota):
            return self._named.try_reserve(
                quota,
                amount,
                principal=principal,
                run_id=run_id,
                supervisor=self._supervisor_id(),
                resource=resource,
            )
        return self._local.try_reserve(
            quota.name,
            quota.window,
            run_id,
            amount=amount,
            maximum=quota.max,
            moment=datetime.now(UTC),
        )

    def _release_reservation(
        self,
        legacy: int,
        parts: list[tuple[str, float]],
        principal: str | None,
        run_id: str | None,
    ) -> None:
        """Give back holds taken for a reservation that will not run.

        Args:
            legacy: Tokens held on the daily lease.
            parts: Named quota holds, as `(name, amount)` pairs.
            principal: Principal id.
            run_id: Active run id.
        """
        if self._leases is not None and legacy:
            self._leases.release(legacy)
        policy = self._policy
        if policy is None:
            return
        by_name = {quota.name: quota for quota in policy.controls.quotas}
        moment = datetime.now(UTC)
        for name, amount in parts:
            quota = by_name.get(name)
            if quota is None:
                continue
            if self._named is not None and server_leased(quota):
                self._named.release(
                    quota,
                    amount,
                    principal=principal,
                    run_id=run_id,
                    supervisor=self._supervisor_id(),
                )
            else:
                self._local.release(quota.name, quota.window, run_id, amount, moment)

    def _deny_reservation(self, action: Action, rule: str | None) -> None:
        """Deny a model call whose reservation does not fit.

        Args:
            action: Model action.
            rule: Quota or daily-limit rule.

        Raises:
            Denied: Always.
        """
        version = "" if self._policy is None else self._policy.version
        decision = Decision(Effect.DENY, Reason.LIMIT, action, version, rule)
        self._remember_denial(decision)
        self._write_decision(decision, capture_content=False)
        raise Denied(decision)

    def _task_revoked(self, task: Task | None) -> bool:
        """Return whether the active task's run was revoked.

        Args:
            task: Innermost task for this client, or `None`.

        Returns:
            `True` when that task id was revoked over the revocation stream.
        """
        if task is None:
            return False
        with self._revoked_lock:
            return task.id in self._revoked_runs

    def _on_revocation(self, kind: str, subject: str) -> None:
        """Apply one revocation event.

        Args:
            kind: Event name from the stream.
            subject: Agent, instance, or run the event names.
        """
        if kind == "instance_revoked" and subject != self._instance_id:
            return
        if kind in {"agent_suspended", "instance_revoked"}:
            self._server_suspended = True
            return
        if kind == "agent_activated":
            self._server_suspended = False
            self._bundle_suspended = False
            self._refresh_rejected = False
            return
        if kind == "run_revoked":
            with self._revoked_lock:
                self._revoked_runs.add(subject)
            return
        if kind == "policy_updated" and self._connection is not None:
            self._connection.refresh_bundle()

    def _lifecycle(self, event: str) -> None:
        """Write a lifecycle event.

        Args:
            event: Lifecycle name, such as `start`, `stop`, or `policy_update`.
        """
        self._write(
            "lifecycle",
            {
                "event": event,
                "policy_version": "none" if self._policy is None else self._policy.version,
                "sdk_version": _sdk_version(),
            },
        )

    def _arm_bypass(self) -> None:
        """Wrap provider HTTP calls when detection is requested.

        `detect_bypass=True` or `TESSERA_DETECT_BYPASS=1` enables it. The
        wrapper is removed when this client closes and no other client still
        wants it.
        """
        if not self._detect_bypass and os.environ.get("TESSERA_DETECT_BYPASS") != "1":
            return
        self._bypass_detach = install_bypass(self._report_ungoverned)

    def _report_ungoverned(self, host: str, path: str) -> None:
        """Record one provider call that bypassed an adapter.

        Args:
            host: Request host.
            path: Request path, without a query string.
        """
        self._write("ungoverned_call", {"host": host, "path": path})

    def _trace_fields(self) -> dict[str, str]:
        """Return the identity attributes for a span.

        Returns:
            Agent, instance, and policy version. The version is empty until a
            policy is loaded.
        """
        version = "" if self._policy is None else self._policy.version
        return {
            "tessera.agent": self._agent,
            "tessera.instance": self._instance_id,
            "tessera.policy_version": version,
        }

    def _write_decision(self, decision: Decision, *, capture_content: bool) -> None:
        """Write a decision event with a fresh span id.

        Args:
            decision: Decision that was just made.
            capture_content: Whether call arguments are stored.

        Notes:
            Allow and require-approval leave the span pending for the matching
            call (and any approval events). Deny clears the pending span.
            When OpenTelemetry is available, `tessera.call` starts here so the
            event `span_id` matches the exported span.
        """
        otel: CallSpan | None = None
        span_id: str | None = None
        trace_id: str | None = None
        if decision.effect is not Effect.DENY:
            otel = start_call_span(
                {
                    "tessera.agent": self._agent,
                    "tessera.instance": self._instance_id,
                    "tessera.decision": decision.effect.value,
                    "tessera.rule": "" if decision.rule is None else decision.rule,
                    "tessera.policy_version": decision.policy_version,
                }
            )
            if otel is not None:
                span_id = otel.span_id
                trace_id = otel.trace_id
        pending = _begin_span(span_id=span_id, trace_id=trace_id, otel=otel)
        fields = _decision_fields(decision, capture_content)
        fields["span_id"] = pending.span_id
        fields["parent_span_id"] = pending.parent_span_id
        if pending.trace_id is not None:
            fields["trace_id"] = pending.trace_id
        self._write("decision", fields)
        if decision.effect is Effect.DENY:
            _clear_pending_span()

    def _write(self, event_type: str, fields: Mapping[str, object]) -> None:
        """Append an event with this client's identity fields.

        Args:
            event_type: Audit event type.
            fields: Type-specific fields.
        """
        task = current_task(self._instance_id)
        agent = self._agent if self._policy is None else self._policy.agent
        environment = self._environment if self._policy is None else self._policy.environment
        payload: dict[str, object] = {
            "agent": agent,
            "environment": environment,
            "instance": self._instance_id,
            "task": None if task is None else task.id,
        }
        if task is not None and task.supervisor:
            payload["supervisor"] = task.supervisor
        payload.update(fields)
        record = None if self._audit is None else self._audit.write(event_type, payload)
        if self._uploader is not None:
            self._uploader.enqueue(remote_event(event_type, payload, record))

    def _server_decision(self, action: Action) -> Decision | None:
        """Return a decision that does not use the local evaluator.

        Args:
            action: Action being checked.

        Returns:
            A suspension, fail-open, or policy-unavailable decision, or `None`
            when the effective policy should be evaluated.
        """
        if self._mode != "server":
            return None
        if self._policy is None or self._bundle_unusable:
            if self.suspended:
                return Decision(Effect.DENY, Reason.SUSPENDED, action, "none")
            if self._fail_mode == "open":
                return Decision(Effect.ALLOW, Reason.FAIL_OPEN, action, "none")
            return Decision(Effect.DENY, Reason.POLICY_UNAVAILABLE, action, "none")
        return None


def _registry_path() -> Path | None:
    """Return the registry file when registry mode is on.

    Returns:
        `TESSERA_REGISTRY`, or `.tessera/registry.yaml` when that file exists.
        `None` when neither is set, so offline clients keep their previous
        behaviour.
    """
    configured = os.environ.get("TESSERA_REGISTRY", "")
    if configured:
        return Path(configured)
    default = Path(".tessera") / "registry.yaml"
    if default.is_file():
        return default
    return None


def _registry_environment(policy: str | Path | AgentPolicy | None) -> str:
    """Return the environment used to look an agent up.

    Args:
        policy: Explicit policy, whose environment wins. `TESSERA_POLICY` is
            used when the argument is omitted.

    Returns:
        The policy environment, or `TESSERA_ENV`, or `dev`.
    """
    source = os.environ.get("TESSERA_POLICY") if policy is None else policy
    if isinstance(source, AgentPolicy):
        return source.metadata.environment
    if isinstance(source, str | Path) and str(source):
        return load_agent_policy(source).metadata.environment
    return os.environ.get("TESSERA_ENV") or "dev"


def _load_agent(policy: str | Path | AgentPolicy | None) -> AgentPolicy:
    """Resolve the agent policy from the argument or the environment.

    Args:
        policy: Policy object, file, or `None` to read `TESSERA_POLICY`.

    Returns:
        The loaded agent policy.

    Raises:
        ConfigError: If neither the argument nor the environment sets a policy.
    """
    source = os.environ.get("TESSERA_POLICY") if policy is None else policy
    if source is None or source == "":
        raise ConfigError("policy is required")
    if isinstance(source, AgentPolicy):
        return source
    return load_agent_policy(source)


def _load_baselines(
    baselines: Sequence[str | Path | BaselinePolicy] | None,
) -> tuple[BaselinePolicy, ...]:
    """Resolve baselines from the argument or `TESSERA_BASELINES`.

    Args:
        baselines: Baseline objects or files, or `None` to read the environment.

    Returns:
        Loaded baselines. Empty when none are configured.
    """
    if baselines is None:
        raw = os.environ.get("TESSERA_BASELINES", "")
        items: Sequence[str | Path | BaselinePolicy] = [
            part for part in raw.split(os.pathsep) if part
        ]
    else:
        items = baselines
    loaded: list[BaselinePolicy] = []
    for item in items:
        if isinstance(item, BaselinePolicy):
            loaded.append(item)
        else:
            loaded.append(load_baseline_policy(item))
    return tuple(loaded)


def _resolve_audit_path(audit_path: str | Path | None) -> Path:
    """Resolve the audit file from the argument, environment, or default.

    Args:
        audit_path: Explicit path, or `None` to read `TESSERA_AUDIT_PATH`.

    Returns:
        The path to open.
    """
    if audit_path is not None:
        return Path(audit_path)
    configured = os.environ.get("TESSERA_AUDIT_PATH")
    if configured:
        return Path(configured)
    return Path(".tessera") / "audit.jsonl"


def _initial_suspended(suspended: bool | None) -> bool:
    """Resolve the starting kill switch.

    Args:
        suspended: Explicit flag, or `None` to read `TESSERA_SUSPENDED`.

    Returns:
        `True` when the client should start suspended.
    """
    if suspended is not None:
        return suspended
    return os.environ.get("TESSERA_SUSPENDED") == "1"


def _reservation_amount(
    quota: QuotaLimit, action: Action, incoming: int, cap: int, cost: float
) -> float:
    """Return the units to hold for one quota before a model call.

    Args:
        quota: Quota that might count the call.
        action: Model action.
        incoming: Reserved input tokens, already scaled by 1.1.
        cap: Policy output cap.
        cost: Estimated cost of that reservation, in dollars.

    Returns:
        The hold, or `0` when this quota is not a token or cost quota for the
        action.
    """
    if not quota_applies(quota, action):
        return 0
    if quota.metric == "tokens":
        return float(incoming + cap)
    if quota.metric == "input_tokens":
        return float(incoming)
    if quota.metric == "output_tokens":
        return float(cap)
    if quota.metric == "cost_usd":
        return cost
    return 0


def _daily_rule(policy: EffectivePolicy | None) -> str | None:
    """Return the daily token shorthand rule.

    Args:
        policy: Effective policy, or `None`.

    Returns:
        The rule id, or `None` when the shorthand is unset.
    """
    if policy is None or policy.tokens_per_day is None:
        return None
    return policy.tokens_per_day.rule


def _daily_limit(policy: EffectivePolicy | None) -> int | None:
    """Return the policy's daily token limit.

    Args:
        policy: Effective policy, or `None` when none is loaded.

    Returns:
        The limit, or `None` when the policy does not set one.
    """
    if policy is None or policy.tokens_per_day is None:
        return None
    return int(policy.tokens_per_day.value)


def _warn_unenforced(policy: EffectivePolicy) -> None:
    """Warn about limits and guardrails this version does not enforce.

    Args:
        policy: Effective policy just built.
    """
    if policy.cost_usd_per_day is not None:
        logger.warning("cost_usd_per_day is not enforced in this version")
    for guardrail in policy.required_guardrails:
        if guardrail.on_missing == "block" and not guardrail.name.startswith("input."):
            logger.warning(
                "only input guardrails can block; treated as warn: %s",
                guardrail.name,
            )


def _with_principal(action: Action) -> Action:
    """Attach the active principal when the action has none.

    Args:
        action: Action passed by the caller.

    Returns:
        The action, or a copy carrying the active principal.
    """
    if action.principal is not None or current_principal() is None:
        return action
    return replace(action, principal=current_principal())


def _decision_fields(decision: Decision, capture_content: bool) -> dict[str, object]:
    """Build the audit fields for a decision.

    Args:
        decision: Decision that was just made.
        capture_content: Whether call arguments are stored.

    Returns:
        Fields for a `decision` event, without the identity fields.
    """
    action: dict[str, object] = {
        "kind": decision.action.kind.value,
        "resource": decision.action.resource,
    }
    if capture_content:
        action["args"] = dict(decision.action.args)
    principal = decision.action.principal
    recorded: dict[str, object] | None = None
    if principal is not None:
        recorded = {"user": principal.user, "groups": sorted(principal.groups)}
    return {
        "action": action,
        "principal": recorded,
        "effect": decision.effect.value,
        "reason": decision.reason.value,
        "rule": decision.rule,
        "policy_version": decision.policy_version,
    }


def _approval_fields(decision: Decision, *, granted: bool) -> dict[str, object]:
    """Build approval audit fields, including the pending call's span id.

    Args:
        decision: Decision that required approval.
        granted: Whether the action was approved.

    Returns:
        Fields for an `approval` event.
    """
    fields: dict[str, object] = {
        "resource": decision.action.resource,
        "rule": decision.rule,
        "granted": granted,
    }
    pending = _peek_pending_span()
    if pending is not None:
        fields["span_id"] = pending.span_id
    return fields


def _bound_arguments(
    fn: Callable[..., object],
    args: tuple[object, ...],
    kwargs: Mapping[str, object],
) -> dict[str, object]:
    """Bind a tool call and drop an instance or class parameter.

    Args:
        fn: Wrapped function.
        args: Positional arguments, including `self` for a method.
        kwargs: Keyword arguments.

    Returns:
        Arguments after defaults are applied, without `self` or `cls`.
    """
    signature = inspect.signature(fn)
    bound = signature.bind(*args, **kwargs)
    bound.apply_defaults()
    arguments = dict(bound.arguments)
    parameters = list(signature.parameters)
    if parameters and parameters[0] in {"self", "cls"}:
        arguments.pop(parameters[0], None)
    return arguments


def _run_elapsed(task: Task) -> float:
    """Return how long a task has been open, in seconds.

    Args:
        task: Active task. A naive `started_at` is treated as UTC.

    Returns:
        Seconds since the task started, never negative.
    """
    started = task.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    aware = started if started.tzinfo is UTC else started.astimezone(UTC)
    return max(0.0, (datetime.now(UTC) - aware).total_seconds())


def _call(
    client: Tessera,
    kind: str,
    resource: str,
    invoke: Callable[[], object],
    *,
    clear: bool = False,
) -> object:
    """Run a governed call and write its outcome.

    Args:
        client: Client that allowed the call.
        kind: `tool` or `model`.
        resource: Resource name.
        invoke: Zero-argument call to time.
        clear: Clear this client's guardrails after the call returns.

    Returns:
        Whatever `invoke` returns.

    Raises:
        Exception: Re-raises whatever `invoke` raised, after the call event.
    """
    started = time.monotonic()
    caught: BaseException | None = None
    returned = False
    result: object = None
    pending = _take_pending_span()
    try:
        with _span_scope(pending):
            with client._in_flight(kind):
                result = invoke()
            returned = True
            return result
    except BaseException as exc:
        caught = exc
        raise
    finally:
        _finish_call_otel(pending)
        _write_call(
            client,
            kind,
            resource,
            caught,
            started,
            clear=clear,
            result=result,
            returned=returned,
            pending=pending,
        )


async def _await_call(
    client: Tessera,
    fn: Callable[..., object],
    args: tuple[object, ...],
    kwargs: Mapping[str, object],
    *,
    kind: str,
    resource: str,
    clear: bool = False,
) -> object:
    """Await a governed call and write its outcome.

    Args:
        client: Client that allowed the call.
        fn: Async function to call.
        args: Positional arguments.
        kwargs: Keyword arguments.
        kind: `tool` or `model`.
        resource: Resource name.
        clear: Clear this client's guardrails after the call returns.

    Returns:
        The awaited result.

    Raises:
        Exception: Re-raises whatever the function raised, after the call event.
    """
    started = time.monotonic()
    caught: BaseException | None = None
    returned = False
    result: object = None
    pending = _take_pending_span()
    try:
        with _span_scope(pending):
            with client._in_flight(kind):
                result = await cast(Awaitable[object], fn(*args, **kwargs))
            returned = True
            return result
    except BaseException as exc:
        caught = exc
        raise
    finally:
        _finish_call_otel(pending)
        _write_call(
            client,
            kind,
            resource,
            caught,
            started,
            clear=clear,
            result=result,
            returned=returned,
            pending=pending,
        )


def _decision_attributes(decision: Decision) -> dict[str, str]:
    """Return the span attributes for a decision.

    Args:
        decision: Decision that was just made.

    Returns:
        `tessera.decision`, `tessera.rule`, and `tessera.policy_version`.
    """
    return {
        "tessera.decision": decision.effect.value,
        "tessera.rule": decision.rule or "",
        "tessera.policy_version": decision.policy_version,
    }


def _finish_reason(result: object) -> str | None:
    """Read a provider finish reason from a model result.

    Args:
        result: Value returned by the model call.

    Returns:
        Anthropic `stop_reason`, or the first OpenAI choice's
        `finish_reason`. `None` when the result has neither.
    """
    if isinstance(result, Mapping):
        stop = result.get("stop_reason")
        choices = result.get("choices")
    else:
        stop = getattr(result, "stop_reason", None)
        choices = getattr(result, "choices", None)
    if isinstance(stop, str):
        return stop
    if isinstance(choices, str | bytes) or not isinstance(choices, Sequence) or not choices:
        return None
    first = choices[0]
    if isinstance(first, Mapping):
        reason = first.get("finish_reason")
    else:
        reason = getattr(first, "finish_reason", None)
    if isinstance(reason, str):
        return reason
    return None


def _effect_names(policy: EffectivePolicy | None, resource: str) -> list[str]:
    """Return the effect tags a tool resource carries.

    Args:
        policy: Effective policy, or `None` when none is loaded.
        resource: Tool name.

    Returns:
        Sorted tags from every matching classification. `unknown` when no
        pattern matches.
    """
    return list(classify_effect(policy, resource))


def _utf8_size(value: object) -> int:
    """Return the UTF-8 size of a value without storing it.

    Args:
        value: Tool arguments or a tool result.

    Returns:
        Byte length of the compact JSON form. A string or `bytes` value is
        measured directly. A value JSON cannot encode uses its type name.
    """
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, bytes):
        return len(value)
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=_json_fallback)
    except (TypeError, ValueError):
        encoded = type(value).__name__
    return len(encoded.encode("utf-8"))


def _json_fallback(value: object) -> str:
    """Replace a value JSON cannot encode with its type name.

    Args:
        value: Value `json.dumps` could not encode.

    Returns:
        The type name.
    """
    return type(value).__name__


def _select_approval_mode(
    mode: Literal["offline", "server"],
    handler: ApprovalHandler | None,
    requested: _ApprovalMode | None,
) -> _ApprovalMode:
    """Choose how a client resolves `require_approval`.

    Args:
        mode: `server` when a control plane is configured.
        handler: Approval handler, or `None`.
        requested: Explicit mode. `None` selects the default.

    Returns:
        `server` in server mode, `handler` when a handler is set, otherwise
        `raise`. An explicit mode wins.

    Raises:
        ConfigError: If `requested` is not a known mode.
    """
    if requested is not None and requested not in {"handler", "server", "raise"}:
        raise ConfigError("approval_mode must be handler, server, or raise")
    if requested is not None:
        return requested
    if mode == "server":
        return "server"
    if handler is not None:
        return "handler"
    return "raise"


def _json_object(response: httpx.Response) -> dict[str, object]:
    """Read a JSON object from an HTTP response.

    Args:
        response: HTTP response.

    Returns:
        The object.

    Raises:
        ServerError: If the body is not a JSON object.
    """
    try:
        body = response.json()
    except ValueError as err:
        raise ServerError("approval response was not JSON") from err
    if not isinstance(body, dict):
        raise ServerError("approval response was not an object")
    return {str(key): value for key, value in body.items()}


def _error_code(document: Mapping[str, object]) -> str | None:
    """Return the API error code, when the body has one.

    Args:
        document: Parsed response.

    Returns:
        The code, or `None`.
    """
    error = document.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    if not isinstance(code, str):
        return None
    return code


def _expiry(value: object) -> datetime:
    """Parse an approval expiry.

    Args:
        value: ISO-8601 timestamp from the server.

    Returns:
        An aware datetime.

    Raises:
        ServerError: If the value is missing or not a timestamp.
    """
    if not isinstance(value, str) or value == "":
        raise ServerError("approval expiry is missing")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise ServerError("approval expiry is missing") from err
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _load_price_table(path: str | Path | None) -> PriceTable:
    """Load the price table named by the caller or the environment.

    Args:
        path: Explicit table path. `None` falls back to `TESSERA_PRICE_TABLE`,
            then the built-in table.

    Returns:
        The table used to price later usage.

    Raises:
        ConfigError: If a named file cannot be read.
    """
    chosen = path
    if chosen is None:
        env = os.environ.get("TESSERA_PRICE_TABLE", "")
        chosen = env if env else None
    if chosen is None:
        return load_price_table()
    return load_price_table(Path(chosen))


@contextmanager
def _span_scope(pending: _PendingSpan | None) -> Iterator[None]:
    """Push the pending span while a governed call runs.

    Args:
        pending: Span paired with the decision, or `None`.

    Yields:
        Nothing.
    """
    if pending is None:
        yield
        return
    otel = pending.otel
    call_span = otel if isinstance(otel, CallSpan) else None
    if call_span is not None:
        call_span.activate()
    try:
        with _use_span(pending.span_id):
            yield
    finally:
        if call_span is not None:
            call_span.deactivate()


def _finish_call_otel(pending: _PendingSpan | None) -> None:
    """End the OpenTelemetry call span started at decision time.

    Args:
        pending: Span taken for this call, or `None`.
    """
    if pending is None:
        return
    otel = pending.otel
    if isinstance(otel, CallSpan):
        otel.end()


def _write_call(
    client: Tessera,
    kind: str,
    resource: str,
    caught: BaseException | None,
    started: float,
    *,
    clear: bool,
    result: object,
    returned: bool,
    pending: _PendingSpan | None,
) -> None:
    """Write the call event, clearing guardrails first when requested.

    Args:
        client: Client that allowed the call.
        kind: `tool` or `model`.
        resource: Resource name.
        caught: Exception raised by the call (including cancellation), or
            `None` on success.
        started: Monotonic time when the call began.
        clear: Clear this client's guardrails before writing.
        result: Value the call returned. Ignored when `returned` is false.
        returned: Whether the call returned a value. A successful `None` is
            still a return.
        pending: Span from the matching decision, or `None`.
    """
    if clear:
        clear_guardrails(client.instance_id)
    error_type = None
    if caught is not None:
        error_type = f"{type(caught).__module__}.{type(caught).__qualname__}"
    task = current_task(client.instance_id)
    principal = current_principal()
    recorded: dict[str, object] | None = None
    if principal is not None:
        recorded = {"user": principal.user, "groups": sorted(principal.groups)}
    fields: dict[str, object] = {
        "kind": kind,
        "resource": resource,
        "status": "error" if caught is not None else "ok",
        "error_type": error_type,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "run": None if task is None else task.id,
        "chain": list(_current_chain()),
        "principal": recorded,
    }
    if pending is not None:
        fields["span_id"] = pending.span_id
        fields["parent_span_id"] = pending.parent_span_id
        if pending.trace_id is not None:
            fields["trace_id"] = pending.trace_id
    waited = _approval_wait_ms.get()
    if waited is not None:
        fields["approval_wait_ms"] = waited
        _approval_wait_ms.set(None)
    meta = _current_call_meta()
    if kind == "model":
        streamed = False if meta is None or meta.streamed is None else meta.streamed
        fields["streamed"] = streamed
        fields["finish_reason"] = _finish_reason(result) if returned else None
    elif kind == "tool":
        arguments = None if meta is None else meta.arguments
        fields["effects"] = _effect_names(client._policy, resource)
        fields["args_bytes"] = None if arguments is None else _utf8_size(arguments)
        fields["result_bytes"] = _utf8_size(result) if returned else None
    client._write("call", fields)


def _sdk_version() -> str:
    """Return the installed SDK version.

    Returns:
        The package version string.
    """
    return version("agent-tessera")


__all__ = ["Tessera"]
