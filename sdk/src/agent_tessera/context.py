"""Per-client task and guardrail state, with a shared principal."""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType

from agent_tessera.types import Principal

_EMPTY_GUARDRAILS: Mapping[str, frozenset[str]] = MappingProxyType({})
_SPAN_ID = re.compile(r"^[0-9a-f]{16}$")
_tasks: ContextVar[tuple[Task, ...]] = ContextVar("agent_tessera_tasks", default=())
_principal: ContextVar[Principal | None] = ContextVar("agent_tessera_principal", default=None)
_chain: ContextVar[tuple[str, ...]] = ContextVar("agent_tessera_chain", default=())


@dataclass(frozen=True, slots=True)
class _PendingSpan:
    """Span ids for a decision waiting to be paired with a call.

    Attributes:
        span_id: 16 lowercase hex characters identifying this governed call.
        parent_span_id: Enclosing span, or `None` for a root call.
        trace_id: 32 hex OpenTelemetry trace id when OTel started the span.
        otel: Optional live OpenTelemetry call span (`CallSpan`), ended when
            the call finishes or the pending span is discarded.
    """

    span_id: str
    parent_span_id: str | None
    trace_id: str | None = None
    otel: object | None = None


_span_stack: ContextVar[tuple[str, ...]] = ContextVar("agent_tessera_spans", default=())
_pending_span: ContextVar[_PendingSpan | None] = ContextVar(
    "agent_tessera_pending_span", default=None
)
_inbound_parent_span: ContextVar[str | None] = ContextVar(
    "agent_tessera_inbound_parent_span", default=None
)


@dataclass(frozen=True, slots=True)
class _CallMeta:
    """Details the caller knows before a governed call returns.

    Attributes:
        streamed: Whether the provider call asked for a stream. `None` when
            the caller did not say.
        arguments: Tool arguments used to measure size. `None` for model calls.
    """

    streamed: bool | None = None
    arguments: Mapping[str, object] | None = None


_call_meta: ContextVar[_CallMeta | None] = ContextVar("agent_tessera_call_meta", default=None)
_outside: ContextVar[Mapping[str, frozenset[str]]] = ContextVar(
    "agent_tessera_outside_guardrails",
    default=_EMPTY_GUARDRAILS,
)


@dataclass(slots=True)
class Task:
    """One unit of work whose steps and guardrails are tracked.

    Attributes:
        id: Task identifier. Callers pass `uuid4().hex` when they have no id.
        client_id: Instance id of the client that created the task.
        steps: Governed model and tool calls already allowed in this task.
        guardrails_ran: Guardrail names that have run while this task is active.
        started_at: When the task began. Run duration is measured from here.
        cost_usd: Estimated model spend recorded while this task is active.
        supervisor: Supervisor bound to this run, or `None`.
    """

    id: str
    client_id: str
    steps: int = 0
    guardrails_ran: set[str] = field(default_factory=set)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    cost_usd: float = 0.0
    supervisor: str | None = None


def current_task(client_id: str) -> Task | None:
    """Return the innermost active task for a client.

    Args:
        client_id: Client whose task to find. Other clients' tasks are ignored.

    Returns:
        The matching task, or `None` when that client has none active.
    """
    for task in reversed(_tasks.get()):
        if task.client_id == client_id:
            return task
    return None


def active_tasks(client_id: str) -> tuple[Task, ...]:
    """Return every active task for a client, outermost first.

    Args:
        client_id: Client whose tasks to collect. Other clients' tasks are
            ignored.

    Returns:
        The tasks currently on the stack for that client. Empty when none
        are active.

    Example:
        ```python
        from agent_tessera.context import Task, active_tasks, use_task

        with use_task(Task("outer", "client-a")):
            with use_task(Task("inner", "client-a")):
                assert [task.id for task in active_tasks("client-a")] == ["outer", "inner"]
        ```
    """
    return tuple(task for task in _tasks.get() if task.client_id == client_id)


@contextmanager
def _use_call_meta(
    *,
    streamed: bool | None = None,
    arguments: Mapping[str, object] | None = None,
) -> Iterator[None]:
    """Remember call details until the governed call finishes.

    Nested blocks keep a field that the inner block does not set.

    Args:
        streamed: Whether this provider call asked for a stream.
        arguments: Tool arguments. `None` leaves the outer value in place.

    Yields:
        Nothing.
    """
    current = _call_meta.get()
    if current is not None:
        if streamed is None:
            streamed = current.streamed
        if arguments is None:
            arguments = current.arguments
    token = _call_meta.set(_CallMeta(streamed, arguments))
    try:
        yield
    finally:
        _call_meta.reset(token)


def _current_call_meta() -> _CallMeta | None:
    """Return the call details for the active context.

    Returns:
        The details, or `None` when no governed call set them.
    """
    return _call_meta.get()


def _current_chain() -> tuple[str, ...]:
    """Return the agents a request has already passed through.

    Returns:
        Agent names, root first. Empty when this context has no handoff.
    """
    return _chain.get()


@contextmanager
def _use_chain(agents: tuple[str, ...]) -> Iterator[tuple[str, ...]]:
    """Remember a call chain until the block exits.

    Args:
        agents: Agent names, root first.

    Yields:
        The same chain.
    """
    token = _chain.set(agents)
    try:
        yield agents
    finally:
        _chain.reset(token)


def current_principal() -> Principal | None:
    """Return the principal shared by every client in this context.

    Returns:
        The active principal, or `None`.
    """
    return _principal.get()


def guardrails_ran(client_id: str) -> frozenset[str]:
    """Return guardrails that have run for a client.

    Args:
        client_id: Client to read. When that client has an active task, the
            task's set is returned. Otherwise the set recorded outside a task
            is returned.

    Returns:
        Guardrail names. Empty when none have run.
    """
    task = current_task(client_id)
    if task is not None:
        return frozenset(task.guardrails_ran)
    return _outside.get().get(client_id, frozenset())


def mark_guardrail_ran(client_id: str, name: str) -> None:
    """Record that a guardrail ran for a client.

    Args:
        client_id: Client the guardrail belongs to.
        name: Guardrail name, such as `input.prompt_injection`.
    """
    task = current_task(client_id)
    if task is not None:
        task.guardrails_ran.add(name)
        return
    current = dict(_outside.get())
    names = set(current.get(client_id, ()))
    names.add(name)
    current[client_id] = frozenset(names)
    _outside.set(MappingProxyType(current))


def clear_guardrails(client_id: str) -> None:
    """Forget guardrails that have run for a client.

    Args:
        client_id: Client whose set to clear. The active task is cleared when
            that client has one; otherwise the outside-task set is cleared.
    """
    task = current_task(client_id)
    if task is not None:
        task.guardrails_ran.clear()
        return
    current = dict(_outside.get())
    current[client_id] = frozenset()
    _outside.set(MappingProxyType(current))


@contextmanager
def use_task(task: Task) -> Iterator[Task]:
    """Push a task and pop it when the block exits.

    Args:
        task: Task to make active. Nested blocks keep the outer tasks.

    Yields:
        The same task.

    Example:
        ```python
        from agent_tessera.context import Task, current_task, use_task

        task = Task("task-1", "client-a")
        with use_task(task):
            assert current_task("client-a") is task
        assert current_task("client-a") is None
        ```
    """
    token = _tasks.set((*_tasks.get(), task))
    try:
        yield task
    finally:
        _tasks.reset(token)


@contextmanager
def use_principal(principal: Principal) -> Iterator[Principal]:
    """Set the shared principal and restore the previous one on exit.

    Args:
        principal: Person the enclosed calls are for.

    Yields:
        The same principal.

    Example:
        ```python
        from agent_tessera.context import current_principal, use_principal
        from agent_tessera.types import Principal

        person = Principal("ada")
        with use_principal(person):
            assert current_principal() is person
        ```
    """
    token = _principal.set(principal)
    try:
        yield principal
    finally:
        _principal.reset(token)


def _normalize_span_id(value: object) -> str | None:
    """Return a valid span id, or `None` when the value is not 16 hex digits.

    Args:
        value: Candidate span id, usually from a header.

    Returns:
        Lowercase hex id, or `None`.
    """
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if _SPAN_ID.fullmatch(text) is None:
        return None
    return text


def _current_span_id() -> str | None:
    """Return the innermost active call span, if any.

    Returns:
        The span id, or `None` when no governed call is on the stack.
    """
    stack = _span_stack.get()
    if not stack:
        return None
    return stack[-1]


def _parent_for_new_span() -> str | None:
    """Choose the parent span for a new decision.

    Returns:
        The active call span, else a validated inbound parent, else `None`.
    """
    current = _current_span_id()
    if current is not None:
        return current
    return _inbound_parent_span.get()


def _begin_span(
    *,
    span_id: str | None = None,
    trace_id: str | None = None,
    otel: object | None = None,
) -> _PendingSpan:
    """Allocate a span for a decision and remember it as pending.

    Args:
        span_id: Existing id (for example from OpenTelemetry). A random id is
            used when omitted.
        trace_id: Optional OpenTelemetry trace id.
        otel: Optional live OpenTelemetry call span to end later.

    Returns:
        The new pending span. A later call event should take it with
        `_take_pending_span`.
    """
    previous = _pending_span.get()
    if previous is not None:
        _end_pending_otel(previous)
    pending = _PendingSpan(
        span_id=secrets.token_hex(8) if span_id is None else span_id,
        parent_span_id=_parent_for_new_span(),
        trace_id=trace_id,
        otel=otel,
    )
    _pending_span.set(pending)
    return pending


def _peek_pending_span() -> _PendingSpan | None:
    """Return the pending decision span without clearing it.

    Returns:
        The pending span, or `None`.
    """
    return _pending_span.get()


def _take_pending_span() -> _PendingSpan | None:
    """Return and clear the pending decision span.

    Returns:
        The pending span, or `None` when none is waiting. The caller owns
        ending any attached OpenTelemetry span.
    """
    pending = _pending_span.get()
    _pending_span.set(None)
    return pending


def _clear_pending_span() -> None:
    """Drop a pending span that will not get a call event."""
    pending = _pending_span.get()
    _pending_span.set(None)
    if pending is not None:
        _end_pending_otel(pending)


def _end_pending_otel(pending: _PendingSpan) -> None:
    """End an OpenTelemetry call span stored on a pending decision.

    Args:
        pending: Pending span that may carry an `otel` handle.
    """
    handle = pending.otel
    if handle is None:
        return
    end = getattr(handle, "end", None)
    if callable(end):
        end()


@contextmanager
def _use_span(span_id: str) -> Iterator[str]:
    """Push a call span so nested decisions parent to it.

    Args:
        span_id: Span of the call that is about to run.

    Yields:
        The same span id.
    """
    token = _span_stack.set((*_span_stack.get(), span_id))
    try:
        yield span_id
    finally:
        _span_stack.reset(token)


@contextmanager
def _use_inbound_parent_span(span_id: str | None) -> Iterator[str | None]:
    """Bind an inbound parent span until the block exits.

    Args:
        span_id: Validated 16-hex id, or `None` to clear.

    Yields:
        The bound parent, or `None`.
    """
    token = _inbound_parent_span.set(span_id)
    try:
        yield span_id
    finally:
        _inbound_parent_span.reset(token)


__all__ = [
    "Task",
    "active_tasks",
    "clear_guardrails",
    "current_principal",
    "current_task",
    "guardrails_ran",
    "mark_guardrail_ran",
    "use_principal",
    "use_task",
]
