"""`tessera agents apply` against a file and a control plane."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_tessera.cli import main
from agent_tessera.policy.effective import build_effective_policy
from agent_tessera.policy.loader import load_agent_policy, load_baseline_policy
from agent_tessera.registry import LocalRegistry, RegistryEntry
from agent_tessera.registry.backends import LocalBackend

_EXAMPLES = Path(__file__).resolve().parents[2] / "policies" / "examples"

_POLICY = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: {name}
  environment: dev
spec:
  tools:
    allow: ["files.read"]
"""

_BASELINE = """\
apiVersion: tessera/v1
kind: BaselinePolicy
metadata:
  name: base
spec:
  tools:
    deny: ["shell.exec"]
"""


def test_apply_prints_each_change_and_is_idempotent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet(monkeypatch)
    _policy(tmp_path, "bot")
    _policy(tmp_path, "note")
    (tmp_path / "base.yaml").write_text(_BASELINE, encoding="utf-8")
    desired = tmp_path / "wanted.yaml"
    target = tmp_path / "registry.yaml"
    _write_desired(desired, status="active", description="first")
    dry = main(["agents", "apply", "-f", str(desired), "--registry", str(target), "--dry-run"])
    assert dry == 0
    assert "+ register" in capsys.readouterr().out
    assert not target.exists()
    applied = main(["agents", "apply", "-f", str(desired), "--registry", str(target)])
    output = capsys.readouterr().out
    assert applied == 0
    assert "bot (dev) + register" in output
    assert "bot (dev) ~ policy" in output
    assert "bot (dev) ~ status draft -> active" in output
    assert "note (dev) + register" in output
    assert LocalRegistry(target).load().baselines == ("base.yaml",)
    before = target.read_bytes()
    again = main(["agents", "apply", "-f", str(desired), "--registry", str(target)])
    assert again == 0
    assert target.read_bytes() == before
    second = capsys.readouterr().out
    assert "bot (dev) = unchanged" in second
    assert "note (dev) = unchanged" in second
    _write_desired(desired, status="suspended", description="renamed")
    updated = main(["agents", "apply", "-f", str(desired), "--registry", str(target)])
    changed = capsys.readouterr().out
    assert updated == 0
    assert "bot (dev) ~ update description" in changed
    assert "bot (dev) ~ status active -> suspended" in changed
    assert "note (dev) ~ update description" in changed


def test_forbidden_status_reports_an_error_and_continues(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet(monkeypatch)
    _policy(tmp_path, "bot")
    _policy(tmp_path, "note")
    (tmp_path / "base.yaml").write_text(_BASELINE, encoding="utf-8")
    desired = tmp_path / "wanted.yaml"
    target = tmp_path / "registry.yaml"
    _write_desired(desired, status="active", description="first")
    assert main(["agents", "apply", "-f", str(desired), "--registry", str(target)]) == 0
    capsys.readouterr()
    store = LocalRegistry(target)
    store.set_status("bot", "dev", "suspended")
    store.set_status("bot", "dev", "retired")
    store.register(RegistryEntry(name="extra", owner="dev@localhost"))
    _write_desired(desired, status="active", description="renamed")
    code = main(["agents", "apply", "-f", str(desired), "--registry", str(target)])
    output = capsys.readouterr().out
    assert code == 1
    assert "bot (dev) ! error bot: cannot change status from retired to active" in output
    assert "note (dev) ~ update description" in output
    assert "extra (dev) ? unmanaged" in output
    retired = store.find("bot", "dev")
    renamed = store.find("note", "dev")
    assert retired is not None
    assert retired.status == "retired"
    assert renamed is not None
    assert renamed.description == "renamed"
    assert store.find("extra", "dev") is not None


def test_apply_onto_another_file_rewrites_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet(monkeypatch)
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _policy(source_dir, "bot")
    (source_dir / "base.yaml").write_text(_BASELINE, encoding="utf-8")
    source = source_dir / "registry.yaml"
    store = LocalRegistry(source)
    store.register(
        RegistryEntry(name="bot", owner="dev@localhost", policy="bot.yaml", status="active")
    )
    LocalBackend(source).put_baseline("base", source_dir / "base.yaml")
    copied = tmp_path / "elsewhere" / "registry.yaml"
    assert main(["agents", "apply", "-f", str(source), "--registry", str(copied)]) == 0
    capsys.readouterr()
    entry = LocalRegistry(copied).find("bot", "dev")
    assert entry is not None
    assert entry.status == "active"
    assert entry.policy is not None
    assert (copied.parent / entry.policy).is_file()
    assert LocalRegistry(copied).load().baselines
    assert (copied.parent / LocalRegistry(copied).load().baselines[0]).is_file()
    before = copied.read_bytes()
    assert main(["agents", "apply", "-f", str(copied), "--registry", str(copied)]) == 0
    assert copied.read_bytes() == before
    assert "= unchanged" in capsys.readouterr().out


def test_example_registry_migrates_to_the_server(
    live_server, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet(monkeypatch)
    key = live_server.headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("TESSERA_ADMIN_KEY", key)
    monkeypatch.setenv("TESSERA_URL", live_server.url)
    example = _EXAMPLES / "registry.yaml"
    assert main(["agents", "apply", "-f", str(example), "--dry-run"]) == 0
    assert "+ register" in capsys.readouterr().out
    import httpx  # noqa: PLC0415

    billing_policy = (
        "apiVersion: tessera/v1\n"
        "kind: AgentPolicy\n"
        "metadata:\n"
        "  agent: billing\n"
        "  environment: prod\n"
        "  owner: a@example.com\n"
        "spec:\n"
        "  tools:\n"
        '    allow: ["*"]\n'
    )
    with httpx.Client(base_url=live_server.url, headers=live_server.headers, timeout=5) as client:
        before = client.get("/v1/agents")
        assert [item["name"] for item in before.json()["items"]] == ["billing"]
        billing_id = before.json()["items"][0]["id"]
        stored = client.put(
            f"/v1/agents/{billing_id}/policy",
            headers={"Content-Type": "application/yaml"},
            content=billing_policy,
        )
        assert stored.status_code == 200
    assert main(["agents", "apply", "-f", str(example)]) == 0
    output = capsys.readouterr().out
    assert "quickstart (dev) + register" in output
    assert "support-triage (prod) + register" in output
    assert "billing (prod) ? unmanaged" in output
    baselines = [load_baseline_policy(_EXAMPLES / "prod-baseline.yaml")]
    expected = {
        "quickstart": build_effective_policy(
            load_agent_policy(_EXAMPLES / "quickstart.yaml"), baselines
        ).version,
        "support-triage": build_effective_policy(
            load_agent_policy(_EXAMPLES / "support-triage.yaml"), baselines
        ).version,
    }
    with httpx.Client(base_url=live_server.url, headers=live_server.headers, timeout=5) as client:
        assert client.get("/v1/baselines/prod-baseline").status_code == 200
        listed = client.get("/v1/agents").json()["items"]
        names = {item["name"] for item in listed}
        assert {"billing", "quickstart", "support-triage"} <= names
        for item in listed:
            if item["name"] not in expected:
                continue
            detail = client.get(f"/v1/agents/{item['id']}").json()
            assert detail["status"] == "active"
            assert detail["policy_version"] == expected[item["name"]]
        billing = next(item for item in listed if item["name"] == "billing")
        assert client.get(f"/v1/agents/{billing['id']}").json()["status"] == "active"


def _policy(directory: Path, name: str) -> None:
    """Write an agent policy.

    Args:
        directory: Directory to write in.
        name: Agent name.
    """
    (directory / f"{name}.yaml").write_text(
        _POLICY.format(name=name),
        encoding="utf-8",
    )


def _write_desired(path: Path, *, status: str, description: str) -> None:
    """Write a two-agent registry file.

    Args:
        path: File to write.
        status: Status for `bot`.
        description: Description for both agents.
    """
    path.write_text(
        "apiVersion: tessera/v1\n"
        "kind: AgentRegistry\n"
        "baselines:\n"
        "  - base.yaml\n"
        "agents:\n"
        "  - name: bot\n"
        "    owner: dev@localhost\n"
        f"    status: {status}\n"
        "    policy: bot.yaml\n"
        f"    description: {description}\n"
        "  - name: note\n"
        "    owner: dev@localhost\n"
        "    policy: note.yaml\n"
        f"    description: {description}\n",
        encoding="utf-8",
    )


def _quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide a developer's server settings.

    Args:
        monkeypatch: Environment patcher.
    """
    monkeypatch.delenv("TESSERA_URL", raising=False)
    monkeypatch.delenv("TESSERA_REGISTRY", raising=False)
    monkeypatch.delenv("TESSERA_ADMIN_KEY", raising=False)
