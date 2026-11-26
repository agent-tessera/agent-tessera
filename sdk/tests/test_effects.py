"""Effect classification and the run ledger."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from agent_tessera import ConfigError, Tessera
from agent_tessera.effects import classify_effect
from agent_tessera.policy.effective import build_effective_policy
from agent_tessera.policy.models import AgentPolicy


def test_classify_effect_uses_patterns() -> None:
    loaded = AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "billing", "environment": "prod"},
            "spec": {
                "effects": {
                    "classify": {
                        "zendesk.read_*": ["read"],
                        "zendesk.refund": ["financial", "irreversible"],
                    }
                }
            },
        }
    )
    policy = build_effective_policy(loaded, ())
    assert classify_effect(policy, "zendesk.read_ticket") == ("read",)
    assert classify_effect(policy, "zendesk.refund") == ("financial", "irreversible")
    assert classify_effect(policy, "files.delete") == ("unknown",)
    assert classify_effect(None, "files.delete") == ("unknown",)


def test_record_effect_reaches_the_server(
    live_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("TESSERA_URL", raising=False)
    monkeypatch.delenv("TESSERA_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("TESSERA_POLICY", raising=False)
    with Tessera("billing", server_url=live_server.url, agent_token=live_server.token) as client:
        with pytest.raises(ConfigError):
            client.record_effect("")
        with client.task("run-effects"):
            client.record_effect("zendesk:ticket/123", reversible=True, undo="zendesk.reopen")
        assert client._effects.records[0].ref == "zendesk:ticket/123"
        assert client.flush(2)
    listed = httpx.get(
        f"{live_server.url}/v1/runs/run-effects/effects",
        headers=live_server.headers,
        timeout=5,
    )
    assert listed.status_code == 200, listed.text
    item = listed.json()["items"][0]
    assert item["ref"] == "zendesk:ticket/123"
    assert item["reversible"] is True
    assert item["undo"] == "zendesk.reopen"
    assert "content" not in item
