"""Public types for bring-your-own LLM client adapters."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agent_tessera.client import Tessera


@runtime_checkable
class Adapter(Protocol):
    """How `wrap` recognizes and governs one kind of provider client.

    Implement this Protocol (or any object with the same methods) and call
    `register_adapter`. Prefer `governed_method` inside `bind` for standard
    chat-style clients; exotic clients may call `tessera.model_call` directly.

    Example:
        ```python
        from agent_tessera.adapters import (
            Adapter,
            governed_method,
            register_adapter,
        )


        class EchoAdapter:
            def matches(self, client: object) -> bool:
                return type(client).__name__ == "EchoClient"

            def paths(self, client: object) -> tuple[tuple[str, ...], ...]:
                return (("complete",),)

            def bind(self, tessera, client):
                def binder(_path, method):
                    return governed_method(
                        tessera,
                        method,
                        prefix="echo",
                        style="tokens",
                        asynchronous=False,
                    )

                return binder


        register_adapter(EchoAdapter())
        ```
    """

    def matches(self, client: object) -> bool:
        """Return whether this adapter governs `client`.

        Args:
            client: Object passed to `wrap`.

        Returns:
            `True` when this adapter should handle the client.
        """
        ...

    def paths(self, client: object) -> tuple[tuple[str, ...], ...]:
        """Return attribute paths to intercept on `client`.

        Args:
            client: Matched provider client.

        Returns:
            Paths such as `(("chat", "completions", "create"),)`.
        """
        ...

    def bind(
        self,
        tessera: Tessera,
        client: object,
    ) -> Callable[[tuple[str, ...], object], object]:
        """Build the binder that wraps each intercepted method.

        Args:
            tessera: Client that enforces model calls.
            client: Matched provider client.

        Returns:
            A callable `(path, method) -> governed_callable`.
        """
        ...


__all__ = ["Adapter"]
