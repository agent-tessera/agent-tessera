"""Optional OpenTelemetry spans for evaluation, enforcement, and calls.

Importing `agent_tessera` does not import OpenTelemetry. Spans are no-ops when
the `otel` extra is not installed.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Protocol, cast

_ENDPOINT = "TESSERA_OTEL_ENDPOINT"


class _State:
    """Process-wide tracer cache. Attributes are mutated in place."""

    tracer: _Tracer | None = None
    missing: bool = False
    configured: bool = False


_state = _State()


class _Tracer(Protocol):
    """The slice of a tracer this module uses."""

    def start_as_current_span(self, name: str) -> _SpanContext:
        """Start a span and make it current."""
        ...

    def start_span(self, name: str) -> _Span:
        """Start a span without making it current."""
        ...


class _SpanContext(Protocol):
    """A span context manager."""

    def __enter__(self) -> _Span:
        """Enter the span."""
        ...

    def __exit__(self, *exc: object) -> bool | None:
        """Leave the span."""
        ...


class _Span(Protocol):
    """A span that accepts string attributes."""

    def set_attribute(self, key: str, value: str) -> None:
        """Store one attribute."""
        ...

    def end(self, *, end_time: int | None = None) -> None:
        """Finish the span."""
        ...

    def get_span_context(self) -> _SpanContextIds:
        """Return the span's context ids."""
        ...


class _SpanContextIds(Protocol):
    """Numeric trace and span ids from OpenTelemetry."""

    @property
    def trace_id(self) -> int:
        """Trace id as an integer."""
        ...

    @property
    def span_id(self) -> int:
        """Span id as an integer."""
        ...

    @property
    def is_valid(self) -> bool:
        """Whether the context has usable ids."""
        ...


class CallSpan:
    """An OpenTelemetry call span held from decision until the call ends.

    Attributes:
        span_id: 16 lowercase hex characters.
        trace_id: 32 lowercase hex characters.
    """

    def __init__(self, span: _Span, span_id: str, trace_id: str) -> None:
        """Store the live span and its formatted ids.

        Args:
            span: OpenTelemetry span started for this call.
            span_id: Formatted span id.
            trace_id: Formatted trace id.
        """
        self._span = span
        self.span_id = span_id
        self.trace_id = trace_id
        self._token: object | None = None

    def activate(self) -> None:
        """Make this span the current OpenTelemetry span."""
        if self._token is not None:
            return
        from opentelemetry import context, trace  # noqa: PLC0415
        from opentelemetry.trace import Span  # noqa: PLC0415

        self._token = context.attach(trace.set_span_in_context(cast(Span, self._span)))

    def deactivate(self) -> None:
        """Restore the previous OpenTelemetry context."""
        if self._token is None:
            return
        from opentelemetry import context  # noqa: PLC0415

        detach = cast("Any", context.detach)
        detach(self._token)
        self._token = None

    def end(self) -> None:
        """Deactivate and finish the span."""
        self.deactivate()
        self._span.end()


@contextmanager
def trace_span(name: str, attributes: Mapping[str, str] | None = None) -> Iterator[_Span | None]:
    """Start a span, or yield `None` when OpenTelemetry is not installed.

    Args:
        name: Span name, such as `tessera.evaluate`.
        attributes: Initial string attributes.

    Yields:
        The span, or `None`.

    Example:
        ```python
        with trace_span("tessera.evaluate", {"tessera.agent": "bot"}):
            pass
        ```
    """
    tracer = _load_tracer()
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(name) as span:
        _apply(span, attributes or {})
        yield span


def start_call_span(attributes: Mapping[str, str] | None = None) -> CallSpan | None:
    """Start a `tessera.call` span and return its ids, or `None` when OTel is off.

    The span is not ended here. Callers must `activate` it while the governed
    call runs and `end` it afterwards.

    Args:
        attributes: Initial string attributes.

    Returns:
        A live call span, or `None` when the tracer is unavailable.
    """
    tracer = _load_tracer()
    if tracer is None:
        return None
    try:
        from opentelemetry.trace import format_span_id, format_trace_id  # noqa: PLC0415
    except ImportError:
        return None
    span = tracer.start_span("tessera.call")
    _apply(span, attributes or {})
    ctx = span.get_span_context()
    if not ctx.is_valid:
        span.end()
        return None
    return CallSpan(
        span,
        format_span_id(ctx.span_id),
        format_trace_id(ctx.trace_id),
    )


def set_attributes(span: _Span | None, attributes: Mapping[str, str]) -> None:
    """Add attributes to a span opened by `trace_span`.

    Args:
        span: Current span, or `None` when tracing is off.
        attributes: Names and values.
    """
    if span is None:
        return
    _apply(span, attributes)


def configure(endpoint: str | None = None) -> bool:
    """Export spans to an OTLP HTTP endpoint.

    Args:
        endpoint: Collector URL. `None` reads `TESSERA_OTEL_ENDPOINT`.

    Returns:
        `True` when a provider was installed. `False` when no endpoint is
        configured, the extra is missing, or a provider is already installed.

    Example:
        ```python
        assert configure() is False
        ```
    """
    if _state.configured:
        return True
    chosen = endpoint if endpoint is not None else os.environ.get(_ENDPOINT, "")
    chosen = chosen.strip()
    if chosen == "":
        return False
    try:
        from opentelemetry import trace  # noqa: PLC0415
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: PLC0415
    except ImportError:
        return False
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=chosen)))
    trace.set_tracer_provider(provider)
    _state.configured = True
    return True


def _load_tracer() -> _Tracer | None:
    """Return the agent tracer, or `None` when the extra is missing.

    Returns:
        A tracer. The first successful load is reused.
    """
    if _state.missing:
        return None
    if _state.tracer is not None:
        return _state.tracer
    try:
        from opentelemetry import trace  # noqa: PLC0415
    except ImportError:
        _state.missing = True
        return None
    _state.tracer = cast("_Tracer", trace.get_tracer("agent_tessera"))
    return _state.tracer


def _apply(span: _Span, attributes: Mapping[str, str]) -> None:
    """Set string attributes on a span.

    Args:
        span: Current span.
        attributes: Names and values.
    """
    for key, value in attributes.items():
        span.set_attribute(key, value)


__all__ = ["CallSpan", "configure", "set_attributes", "start_call_span", "trace_span"]
