"""Proxy that governs selected methods of a provider client."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import TYPE_CHECKING, cast

from agent_tessera.context import _use_call_meta

if TYPE_CHECKING:
    from agent_tessera.client import Tessera


class GovernedProxy:
    """Forward attributes, wrapping the calls a provider policy cares about.

    An exact intercepted path returns a wrapper. A prefix of one returns
    another proxy. Every other attribute is the real object.

    Args:
        inner: Provider client, or the object the path is relative to.
        path: Attribute path already traversed.
        intercepted: Full paths that must be governed, such as
            `("chat", "completions", "create")`.
        bind: Builds the wrapper for an intercepted method.
    """

    def __init__(
        self,
        inner: object,
        path: tuple[str, ...],
        intercepted: Iterable[tuple[str, ...]],
        bind: Callable[[tuple[str, ...], object], object],
    ) -> None:
        """Store the client and the paths that should be governed.

        Args:
            inner: Object attribute lookup starts from.
            path: Path already traversed from the original client.
            intercepted: Full method paths to wrap.
            bind: Called with the path and the real method.
        """
        self._inner = inner
        self._path = path
        self._intercepted = frozenset(intercepted)
        self._bind = bind

    def __getattr__(self, name: str) -> object:
        """Resolve one attribute, wrapping intercepted calls.

        Args:
            name: Attribute name.

        Returns:
            A wrapper, a nested proxy, or the real attribute.
        """
        path = (*self._path, name)
        if path in self._intercepted:
            return self._bind(path, _resolve(self._inner, path))
        if _is_prefix(path, self._intercepted):
            return GovernedProxy(self._inner, path, self._intercepted, self._bind)
        parent = _resolve(self._inner, self._path)
        return getattr(parent, name)


def _resolve(inner: object, path: tuple[str, ...]) -> object:
    """Walk a path of attributes.

    Args:
        inner: Object to start from.
        path: Attribute names.

    Returns:
        The object at the end of the path.
    """
    current = inner
    for name in path:
        current = getattr(current, name)
    return current


def _is_prefix(path: tuple[str, ...], intercepted: Iterable[tuple[str, ...]]) -> bool:
    """Return whether any intercepted path continues past this one.

    Args:
        path: Path being accessed.
        intercepted: Full method paths.

    Returns:
        `True` when `path` is a strict prefix of an intercepted path.
    """
    return any(len(full) > len(path) and full[: len(path)] == path for full in intercepted)


def governed_method(
    tessera: Tessera,
    method: object,
    *,
    prefix: str,
    style: str,
    asynchronous: bool,
    model_key: str = "model",
) -> object:
    """Wrap a provider method with policy checks and usage recording.

    Args:
        tessera: Client that enforces the call.
        method: Real provider method.
        prefix: Resource prefix, such as `openai` or `anthropic`.
        style: `chat` for prompt/completion tokens, `gemini` for usage
            metadata, otherwise input/output.
        asynchronous: Whether to await the provider method.
        model_key: Keyword that carries the model name. Bedrock uses `modelId`.

    Returns:
        A sync or async callable with the same calling convention.
    """
    if asynchronous:
        return _async_method(tessera, method, prefix, style, model_key)
    return _sync_method(tessera, method, prefix, style, model_key)


def _sync_method(
    tessera: Tessera, method: object, prefix: str, style: str, model_key: str
) -> object:
    """Return a sync wrapper around a provider method.

    Args:
        tessera: Client that enforces the call.
        method: Real provider method.
        prefix: Resource prefix.
        style: Usage field style.
        model_key: Keyword that carries the model name.

    Returns:
        The governed callable.
    """
    call = cast(Callable[..., object], method)

    def governed(*args: object, **kwargs: object) -> object:
        resource = _resource(prefix, kwargs, model_key)

        def invoke() -> object:
            return call(*args, **kwargs)

        owned = tessera._open_model_hold(resource, (), kwargs)
        try:
            with _use_call_meta(streamed=bool(kwargs.get("stream"))):
                result = tessera.model_call(resource)(invoke)()
            if not kwargs.get("stream"):
                _record_usage(tessera, resource, result, style)
            return result
        finally:
            if owned:
                tessera._close_model_hold()

    return governed


def _async_method(
    tessera: Tessera, method: object, prefix: str, style: str, model_key: str
) -> object:
    """Return an async wrapper around a provider method.

    Args:
        tessera: Client that enforces the call.
        method: Real provider method.
        prefix: Resource prefix.
        style: Usage field style.
        model_key: Keyword that carries the model name.

    Returns:
        The governed coroutine function.
    """
    call = cast(Callable[..., object], method)

    async def governed(*args: object, **kwargs: object) -> object:
        resource = _resource(prefix, kwargs, model_key)

        async def invoke() -> object:
            return await cast(Awaitable[object], call(*args, **kwargs))

        owned = tessera._open_model_hold(resource, (), kwargs)
        try:
            governed_call = tessera.model_call(resource)(invoke)
            with _use_call_meta(streamed=bool(kwargs.get("stream"))):
                result = await cast(Awaitable[object], governed_call())
            if not kwargs.get("stream"):
                _record_usage(tessera, resource, result, style)
            return result
        finally:
            if owned:
                tessera._close_model_hold()

    return governed


def _resource(prefix: str, kwargs: dict[str, object], model_key: str) -> str:
    """Build the model resource name.

    Args:
        prefix: Provider prefix.
        kwargs: Arguments of the provider call.
        model_key: Keyword that carries the model name.

    Returns:
        `<prefix>/<model>`, or `<prefix>/unknown` when the model is missing.
    """
    value = kwargs.get(model_key, "unknown")
    if not isinstance(value, str) or value == "":
        return f"{prefix}/unknown"
    return f"{prefix}/{value}"


def _record_usage(tessera: Tessera, resource: str, result: object, style: str) -> None:
    """Record token usage when the provider result includes it.

    Args:
        tessera: Client that owns the token counter.
        resource: Model resource, including the provider prefix.
        result: Provider result object.
        style: `chat` for prompt/completion tokens, `gemini` for usage
            metadata, otherwise input/output.
    """
    if style == "gemini":
        usage = getattr(result, "usage_metadata", None)
        prompt_name = "prompt_token_count"
        completion_name = "candidates_token_count"
    else:
        usage = getattr(result, "usage", None)
        if style == "chat":
            prompt_name = "prompt_tokens"
            completion_name = "completion_tokens"
        else:
            prompt_name = "input_tokens"
            completion_name = "output_tokens"
    if usage is None:
        return
    prompt = getattr(usage, prompt_name, None)
    completion = getattr(usage, completion_name, None)
    if isinstance(prompt, bool) or isinstance(completion, bool):
        return
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return
    tessera.record_usage(resource, prompt, completion)


__all__ = ["GovernedProxy", "governed_method"]
