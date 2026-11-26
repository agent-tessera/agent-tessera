"""Local `tessera agents` commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_tessera import Tessera
from agent_tessera.cli import main
from agent_tessera.errors import RegistryError
from agent_tessera.registry import LocalRegistry, RegistryEntry
from agent_tessera.registry.backends import LocalBackend
from agent_tessera.types import Action

_POLICY = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: bot
  environment: dev
spec:
  tools:
    allow: ["files.read"]
"""


def test_register_list_show_and_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "registry.yaml"
    _quiet_env(monkeypatch)
    policy = tmp_path / "bot.yaml"
    policy.write_text(_POLICY, encoding="utf-8")
    code = main(
        [
            "agents",
            "register",
            "bot",
            "--owner",
            "dev@localhost",
            "--policy",
            str(policy),
            "--description",
            "billing",
            "--label",
            "team=support",
            "--registry",
            str(registry),
        ]
    )
    assert code == 0
    assert capsys.readouterr().out == "registered bot (dev) [draft]\n"
    code = main(["agents", "ls", "--registry", str(registry)])
    assert code == 0
    assert capsys.readouterr().out == (
        "NAME  ENV  STATUS  OWNER          POLICY\nbot   dev  draft   dev@localhost  bot.yaml\n"
    )
    code = main(["agents", "ls", "--json", "--registry", str(registry)])
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["name"] == "bot"
    assert listed[0]["labels"] == {"team": "support"}
    assert listed[0]["description"] == "billing"
    code = main(["agents", "show", "bot", "--json", "--registry", str(registry)])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["policy"] == "bot.yaml"


def test_registry_flag_and_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet_env(monkeypatch)
    chosen = tmp_path / "chosen.yaml"
    other = tmp_path / "other.yaml"
    monkeypatch.setenv("TESSERA_REGISTRY", str(other))
    registered = main(
        ["agents", "register", "bot", "--owner", "dev@localhost", "--registry", str(chosen)]
    )
    assert registered == 0
    capsys.readouterr()
    assert LocalRegistry(chosen).find("bot", "dev") is not None
    assert not other.exists()
    monkeypatch.setenv("TESSERA_REGISTRY", str(other))
    assert main(["agents", "register", "bot", "--owner", "dev@localhost"]) == 0
    assert LocalRegistry(other).find("bot", "dev") is not None


def test_registry_and_server_together_is_a_usage_error(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet_env(monkeypatch)
    code = main(["agents", "ls", "--registry", "registry.yaml", "--server", "http://127.0.0.1:9"])
    assert code == 1
    assert "cannot be used together" in capsys.readouterr().err


def test_activate_without_a_policy_leaves_the_agent_draft(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet_env(monkeypatch)
    registry = tmp_path / "registry.yaml"
    code = main(
        [
            "agents",
            "register",
            "bot",
            "--owner",
            "dev@localhost",
            "--activate",
            "--registry",
            str(registry),
        ]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert "activation needs a policy" in captured.err
    assert "Traceback" not in captured.err
    stored = LocalRegistry(registry).find("bot", "dev")
    assert stored is not None
    assert stored.status == "draft"


def test_lifecycle_and_set_policy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet_env(monkeypatch)
    registry = tmp_path / "registry.yaml"
    policy = tmp_path / "bot.yaml"
    policy.write_text(_POLICY, encoding="utf-8")
    assert (
        main(
            [
                "agents",
                "register",
                "bot",
                "--owner",
                "dev@localhost",
                "--policy",
                str(policy),
                "--activate",
                "--registry",
                str(registry),
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == "registered bot (dev) [active]\n"
    assert main(["agents", "suspend", "bot", "--registry", str(registry)]) == 0
    assert capsys.readouterr().out == "bot (dev): active -> suspended\n"
    assert main(["agents", "activate", "bot", "--registry", str(registry)]) == 0
    assert capsys.readouterr().out == "bot (dev): suspended -> active\n"
    replacement = tmp_path / "next.yaml"
    replacement.write_text(_POLICY, encoding="utf-8")
    assert (
        main(
            [
                "agents",
                "set-policy",
                "bot",
                "--policy",
                str(replacement),
                "--registry",
                str(registry),
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == "bot (dev): policy next.yaml\n"
    assert main(["agents", "retire", "bot", "--registry", str(registry)]) == 0
    assert capsys.readouterr().out == "bot (dev): active -> retired\n"


def test_unknown_agent_and_local_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet_env(monkeypatch)
    registry = tmp_path / "registry.yaml"
    LocalRegistry(registry).register(RegistryEntry(name="bot", owner="dev@localhost"))
    code = main(["agents", "show", "missing", "--registry", str(registry)])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.err == "agent missing (dev) is not registered\n"
    assert "Traceback" not in captured.err
    code = main(["agents", "token", "bot", "--registry", str(registry), "--reusable"])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.err == "registration tokens require a server (--server or TESSERA_URL)\n"
    assert "Traceback" not in captured.err


def test_ls_filters_environment_and_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet_env(monkeypatch)
    registry = tmp_path / "registry.yaml"
    registered = main(
        ["agents", "register", "bot", "--owner", "dev@localhost", "--registry", str(registry)]
    )
    assert registered == 0
    assert (
        main(
            [
                "agents",
                "register",
                "bot",
                "--env",
                "prod",
                "--owner",
                "ops@localhost",
                "--registry",
                str(registry),
            ]
        )
        == 0
    )
    capsys.readouterr()
    listed = main(
        ["agents", "ls", "--env", "prod", "--status", "draft", "--registry", str(registry)]
    )
    assert listed == 0
    assert capsys.readouterr().out == (
        "NAME  ENV   STATUS  OWNER          POLICY\nbot   prod  draft   ops@localhost  -\n"
    )


def test_server_without_an_admin_key_is_rejected(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet_env(monkeypatch)
    monkeypatch.setenv("TESSERA_URL", "http://127.0.0.1:9")
    code = main(["agents", "ls"])
    assert code == 1
    assert capsys.readouterr().err == "set TESSERA_ADMIN_KEY or run tessera login\n"


def test_server_agents_register_list_and_bootstrap(
    live_server, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet_env(monkeypatch)
    key = live_server.headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("TESSERA_ADMIN_KEY", key)
    monkeypatch.setenv("TESSERA_URL", live_server.url)
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    helper = tmp_path / "helper.yaml"
    helper.write_text(_agent_policy("helper"), encoding="utf-8")
    other = tmp_path / "other.yaml"
    other.write_text(_agent_policy("other"), encoding="utf-8")
    registered = main(
        [
            "agents",
            "register",
            "helper",
            "--owner",
            "ada@example.com",
            "--policy",
            str(helper),
            "--activate",
            "--description",
            "desk",
        ]
    )
    assert registered == 0
    assert capsys.readouterr().out == "registered helper (dev) [active]\n"
    assert (
        main(
            [
                "agents",
                "register",
                "other",
                "--owner",
                "ada@example.com",
                "--policy",
                str(other),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["agents", "register", "crew", "--owner", "support", "--env", "prod"]) == 0
    capsys.readouterr()
    import httpx  # noqa: PLC0415

    with httpx.Client(base_url=live_server.url, headers=live_server.headers, timeout=5) as client:
        owners = client.get("/v1/owners", params={"email": "ada@example.com"})
        assert [item["email"] for item in owners.json()["items"]] == ["ada@example.com"]
        team = client.get("/v1/owners", params={"email": "support@teams.local"})
        assert team.json()["items"][0]["type"] == "team"
        listed = client.get("/v1/agents", params={"environment": "dev"})
        agent_id = next(item["id"] for item in listed.json()["items"] if item["name"] == "helper")
        detail = client.get(f"/v1/agents/{agent_id}").json()
        assert detail["status"] == "active"
        assert str(detail["policy_version"]).startswith("sha256:")
    assert main(["agents", "ls", "--env", "dev", "--status", "active"]) == 0
    table = capsys.readouterr().out
    assert table.splitlines()[0].split() == ["NAME", "ENV", "STATUS", "OWNER", "POLICY"]
    assert "helper" in table
    assert "ada@example.com" in table
    assert "sha256:" in table
    assert main(["agents", "show", "helper", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["status"] == "active"
    assert shown["owner"] == "ada@example.com"
    assert shown["policy"].startswith("sha256:")
    assert len(shown["policy"]) == len("sha256:") + 12
    duplicate = main(["agents", "register", "helper", "--owner", "ada@example.com"])
    captured = capsys.readouterr()
    assert duplicate == 1
    assert captured.err.startswith("server error 409: conflict:")
    assert "Traceback" not in captured.err
    assert main(["agents", "token", "helper"]) == 0
    token = capsys.readouterr().out.strip()
    assert token.startswith("tsr_")
    assert " " not in token
    with Tessera(
        "helper",
        server_url=live_server.url,
        agent_token=token,
        audit_path=tmp_path / "audit.jsonl",
    ) as agent:
        assert agent.check(Action.tool("files.read")).allowed is True


def _agent_policy(name: str) -> str:
    """Return a small policy for one agent.

    Args:
        name: Agent name.

    Returns:
        YAML text.
    """
    return (
        "apiVersion: tessera/v1\n"
        "kind: AgentPolicy\n"
        "metadata:\n"
        f"  agent: {name}\n"
        "  environment: dev\n"
        "spec:\n"
        "  tools:\n"
        '    allow: ["files.read"]\n'
    )


def test_local_baseline_path_is_recorded_once(tmp_path: Path) -> None:
    baseline = tmp_path / "base.yaml"
    baseline.write_text(
        "apiVersion: tessera/v1\nkind: BaselinePolicy\nmetadata:\n  name: base\n",
        encoding="utf-8",
    )
    backend = LocalBackend(tmp_path / "registry.yaml")
    backend.put_baseline("base", baseline)
    backend.put_baseline("base", baseline)
    assert LocalRegistry(backend.path).load().baselines == ("base.yaml",)
    with pytest.raises(RegistryError, match="is named base"):
        backend.put_baseline("other", baseline)


def _quiet_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide a developer's server and registry settings.

    Args:
        monkeypatch: Environment patcher.
    """
    monkeypatch.delenv("TESSERA_URL", raising=False)
    monkeypatch.delenv("TESSERA_REGISTRY", raising=False)
    monkeypatch.delenv("TESSERA_ADMIN_KEY", raising=False)
