"""Tests for the governed attribute proxy."""

from types import SimpleNamespace

from agent_tessera.adapters._proxy import GovernedProxy


def test_exact_paths_are_wrapped_and_other_attributes_pass_through() -> None:
    calls: list[tuple[str, ...]] = []

    def create(**kwargs: object) -> dict[str, object]:
        return dict(kwargs)

    inner = SimpleNamespace(
        models="real-models",
        chat=SimpleNamespace(
            side="chat-side",
            completions=SimpleNamespace(create=create, extra="extra"),
        ),
        responses=SimpleNamespace(create=create),
    )

    def bind(path: tuple[str, ...], method: object) -> object:
        calls.append(path)

        def wrapped(**kwargs: object) -> object:
            assert callable(method)
            return method(**kwargs)

        return wrapped

    proxy = GovernedProxy(
        inner,
        (),
        (("chat", "completions", "create"), ("responses", "create")),
        bind,
    )
    assert proxy.models == "real-models"
    assert proxy.chat.side == "chat-side"
    assert proxy.chat.completions.extra == "extra"
    assert proxy.chat.completions.create(model="gpt") == {"model": "gpt"}
    assert proxy.responses.create(model="gpt") == {"model": "gpt"}
    assert calls == [
        ("chat", "completions", "create"),
        ("responses", "create"),
    ]
