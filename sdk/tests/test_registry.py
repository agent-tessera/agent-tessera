"""Local registry storage and offline enforcement."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_tessera import ConfigError, RegistryError, Tessera
from agent_tessera.audit import verify_audit_log
from agent_tessera.cli import main
from agent_tessera.errors import PolicyError
from agent_tessera.registry import AgentRegistry, LocalRegistry, RegistryEntry, load_registry
from agent_tessera.registry import store as store_module
from agent_tessera.types import Action, Reason

_POLICY = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: {name}
  environment: {environment}
spec:
  tools:
    allow: ["files.read"]
"""

_EXAMPLES = Path(__file__).resolve().parents[2] / "policies" / "examples"


def test_example_registry_validates(capsys: pytest.CaptureFixture[str]) -> None:
    path = _EXAMPLES / "registry.yaml"
    assert main(["policy", "validate", str(path)]) == 0
    assert f"OK {path} (AgentRegistry 2 agents)" in capsys.readouterr().out
    loaded = load_registry(path)
    assert [item.name for item in loaded.agents] == ["quickstart", "support-triage"]


def test_duplicate_entries_are_rejected(tmp_path: Path) -> None:
    document = {
        "apiVersion": "tessera/v1",
        "kind": "AgentRegistry",
        "agents": [
            {"name": "bot", "owner": "dev@localhost"},
            {"name": "bot", "environment": "dev", "owner": "other@localhost"},
        ],
    }
    with pytest.raises(ValidationError, match="duplicate agent bot \\(dev\\)"):
        AgentRegistry.model_validate(document)
    path = tmp_path / "registry.yaml"
    path.write_text(
        "apiVersion: tessera/v1\nkind: AgentRegistry\nagents:\n"
        "  - {name: bot, owner: dev@localhost}\n"
        "  - {name: bot, owner: other@localhost}\n",
        encoding="utf-8",
    )
    with pytest.raises(PolicyError, match="duplicate agent bot \\(dev\\)"):
        load_registry(path)


@pytest.mark.parametrize(
    ("start", "target", "allowed"),
    [
        ("draft", "active", True),
        ("suspended", "active", True),
        ("active", "suspended", True),
        ("active", "retired", True),
        ("suspended", "retired", True),
        ("draft", "suspended", False),
        ("draft", "retired", False),
        ("draft", "draft", False),
        ("active", "active", False),
        ("active", "draft", False),
        ("retired", "active", False),
        ("retired", "suspended", False),
    ],
)
def test_lifecycle_transitions(tmp_path: Path, start: str, target: str, allowed: bool) -> None:
    store, _policy = _store(tmp_path, status="draft")
    if start != "draft":
        store.set_status("bot", "dev", "active")
        if start != "active":
            store.set_status("bot", "dev", start)  # type: ignore[arg-type]
    if allowed:
        stored = store.set_status("bot", "dev", target)  # type: ignore[arg-type]
        assert stored.status == target
        return
    with pytest.raises(RegistryError, match="cannot change status"):
        store.set_status("bot", "dev", target)  # type: ignore[arg-type]


def test_activation_without_a_policy_is_rejected(tmp_path: Path) -> None:
    store = LocalRegistry(tmp_path / "registry.yaml")
    store.register(RegistryEntry(name="bot", owner="dev@localhost"))
    with pytest.raises(RegistryError, match="bot: activation needs a policy"):
        store.set_status("bot", "dev", "active")
    with pytest.raises(RegistryError, match="other: activation needs a policy"):
        store.register(RegistryEntry(name="other", owner="dev@localhost", status="active"))


def test_duplicate_registration_and_unknown_fields_are_rejected(tmp_path: Path) -> None:
    store, _policy = _store(tmp_path)
    with pytest.raises(RegistryError, match="already registered"):
        store.register(RegistryEntry(name="bot", owner="dev@localhost"))
    with pytest.raises(RegistryError, match="unknown field status"):
        store.update("bot", "dev", status="active")
    with pytest.raises(RegistryError, match="not registered"):
        store.update("missing", "dev", description="nope")


def test_policy_must_name_the_same_agent(tmp_path: Path) -> None:
    policy = tmp_path / "bot.yaml"
    policy.write_text(_POLICY.format(name="other", environment="dev"), encoding="utf-8")
    store = LocalRegistry(tmp_path / "registry.yaml")
    with pytest.raises(RegistryError, match="does not match"):
        store.register(RegistryEntry(name="bot", owner="dev@localhost", policy="bot.yaml"))
    policy.write_text("kind: nope\n", encoding="utf-8")
    with pytest.raises(RegistryError, match="kind"):
        store.register(RegistryEntry(name="bot", owner="dev@localhost", policy="bot.yaml"))


def test_lock_serializes_writes_and_a_stale_lock_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "registry.yaml"
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker(name: str) -> None:
        try:
            barrier.wait(timeout=2)
            LocalRegistry(path).register(RegistryEntry(name=name, owner="dev@localhost"))
        except BaseException as err:
            errors.append(err)

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("one", "two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert errors == []
    assert LocalRegistry(path).find("one", "dev") is not None
    assert LocalRegistry(path).find("two", "dev") is not None
    lock = Path(str(path) + ".lock")
    lock.write_text("", encoding="utf-8")
    os.utime(lock, (time.time() - 31, time.time() - 31))
    LocalRegistry(path).register(RegistryEntry(name="three", owner="dev@localhost"))
    assert not lock.exists()
    lock.write_text("", encoding="utf-8")
    monkeypatch.setattr(store_module, "_LOCK_WAIT", 0.0)
    with pytest.raises(RegistryError, match="registry is locked"):
        LocalRegistry(path).register(RegistryEntry(name="four", owner="dev@localhost"))


def test_written_yaml_is_deterministic(tmp_path: Path) -> None:
    store, _policy = _store(tmp_path)
    store.register(RegistryEntry(name="aaa", owner="dev@localhost", environment="prod"))
    store._write(store.load())
    first = store.path.read_bytes()
    store._write(store.load())
    assert store.path.read_bytes() == first
    text = first.decode()
    assert text.index("environment: dev") < text.index("environment: prod")


def test_registry_audit_records_old_and_new(tmp_path: Path) -> None:
    store, _policy = _store(tmp_path)
    store.update("bot", "dev", description="billing")
    audit = tmp_path / "registry-audit.jsonl"
    assert verify_audit_log(audit).ok
    text = audit.read_text(encoding="utf-8")
    assert '"description":[null,""]' in text
    assert '"description":["","billing"]' in text


def test_unregistered_draft_and_retired_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, policy = _store(tmp_path)
    monkeypatch.setenv("TESSERA_REGISTRY", str(store.path))
    monkeypatch.delenv("TESSERA_POLICY", raising=False)
    with pytest.raises(ConfigError, match="agent missing \\(dev\\) is not registered"):
        Tessera("missing", audit_path=tmp_path / "audit.jsonl")
    with pytest.raises(ConfigError, match="agent bot \\(dev\\) is draft"):
        Tessera("bot", audit_path=tmp_path / "audit.jsonl")
    store.set_status("bot", "dev", "active")
    store.set_status("bot", "dev", "retired")
    with pytest.raises(ConfigError, match="agent bot \\(dev\\) is retired"):
        Tessera("bot", policy=policy, audit_path=tmp_path / "audit.jsonl")


def test_client_uses_the_registry_policy_and_follows_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _policy = _store(tmp_path, status="active")
    baseline = tmp_path / "base.yaml"
    baseline.write_text(
        "apiVersion: tessera/v1\nkind: BaselinePolicy\nmetadata:\n  name: base\n"
        "spec:\n  tools:\n    deny: ['shell.exec']\n",
        encoding="utf-8",
    )
    store._write(store.load().model_copy(update={"baselines": ("base.yaml",)}))
    monkeypatch.setenv("TESSERA_REGISTRY", str(store.path))
    monkeypatch.delenv("TESSERA_POLICY", raising=False)
    monkeypatch.delenv("TESSERA_BASELINES", raising=False)
    clock = {"t": 50.0}
    monkeypatch.setattr("agent_tessera.client.time.monotonic", lambda: clock["t"])
    with Tessera("bot", audit_path=tmp_path / "audit.jsonl") as client:
        assert client.policy is not None
        assert client.check(Action.tool("files.read")).allowed is True
        assert client.check(Action.tool("shell.exec")).reason is Reason.DENY_RULE
        store.set_status("bot", "dev", "suspended")
        clock["t"] = 51.0
        assert client.check(Action.tool("files.read")).reason is Reason.SUSPENDED
        store.set_status("bot", "dev", "active")
        clock["t"] = 52.0
        assert client.check(Action.tool("files.read")).allowed is True
        store.path.write_text("kind: nope\n", encoding="utf-8")
        clock["t"] = 53.0
        assert client.check(Action.tool("files.read")).reason is Reason.SUSPENDED
    text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert text.count("registry_suspend") == 2
    assert "registry_resume" in text


def test_missing_registry_keeps_offline_behaviour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TESSERA_REGISTRY", raising=False)
    monkeypatch.chdir(tmp_path)
    policy = tmp_path / "bot.yaml"
    policy.write_text(_POLICY.format(name="bot", environment="dev"), encoding="utf-8")
    with Tessera("bot", policy=policy, audit_path=tmp_path / "audit.jsonl") as client:
        assert client.mode == "offline"
        assert client.check(Action.tool("files.read")).allowed is True


def test_server_mode_ignores_the_registry(
    live_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TESSERA_REGISTRY", str(tmp_path / "empty.yaml"))
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    with Tessera(
        "billing",
        server_url=live_server.url,
        agent_token=live_server.token,
        audit_path=tmp_path / "audit.jsonl",
    ) as client:
        assert client.mode == "server"
        assert client.check(Action.tool("files.read")).allowed is True


def _store(tmp_path: Path, *, status: str = "draft") -> tuple[LocalRegistry, Path]:
    """Create a registry and a matching policy file.

    Args:
        tmp_path: Test directory.
        status: Status stored on the entry.

    Returns:
        The store and the policy path.
    """
    policy = tmp_path / "bot.yaml"
    policy.write_text(_POLICY.format(name="bot", environment="dev"), encoding="utf-8")
    store = LocalRegistry(tmp_path / "registry.yaml")
    store.register(
        RegistryEntry(
            name="bot",
            owner="dev@localhost",
            policy="bot.yaml",
            status=status,  # type: ignore[arg-type]
        )
    )
    return store, policy
