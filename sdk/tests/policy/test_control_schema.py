"""Load tests for v0.3 quotas, effects, approvals, and operating windows."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from agent_tessera.errors import PolicyError
from agent_tessera.policy.loader import load_policy, parse_policy
from agent_tessera.policy.models import AgentPolicyMetadata, Selector, parse_duration

EXAMPLES = Path(__file__).resolve().parents[3] / "policies" / "examples"

_AGENT: dict[str, object] = {
    "apiVersion": "tessera/v1",
    "kind": "AgentPolicy",
    "metadata": {"agent": "spend-guard", "environment": "prod", "risk_tier": "medium"},
    "spec": {
        "limits": {
            "per_call": {"max_output_tokens": 100},
            "quotas": [
                {
                    "name": "daily-tokens",
                    "metric": "tokens",
                    "window": "day",
                    "scope": "agent",
                    "max": 10,
                }
            ],
        }
    },
}


def _agent(**metadata: object) -> dict[str, object]:
    document = copy.deepcopy(_AGENT)
    meta = document["metadata"]
    assert isinstance(meta, dict)
    meta.update(metadata)
    return document


def _without_output_cap(document: dict[str, object]) -> dict[str, object]:
    spec = document["spec"]
    assert isinstance(spec, dict)
    limits = spec["limits"]
    assert isinstance(limits, dict)
    limits.pop("per_call", None)
    return document


def _with_route(
    document: dict[str, object],
    route: list[str],
    risk_tier: str = "medium",
) -> dict[str, object]:
    meta = document["metadata"]
    assert isinstance(meta, dict)
    meta["risk_tier"] = risk_tier
    spec = document["spec"]
    assert isinstance(spec, dict)
    spec["approvals"] = {"route_to": route}
    return document


def _quota(**fields: object) -> dict[str, object]:
    document = _agent()
    spec = document["spec"]
    assert isinstance(spec, dict)
    limits = spec["limits"]
    assert isinstance(limits, dict)
    quotas = limits["quotas"]
    assert isinstance(quotas, list)
    quota = quotas[0]
    assert isinstance(quota, dict)
    quota.update(fields)
    return document


def test_control_examples_load() -> None:
    agent = load_policy(EXAMPLES / "spend-guard.yaml")
    baseline = load_policy(EXAMPLES / "spend-baseline.yaml")

    assert agent.kind == "AgentPolicy"
    assert agent.metadata.autonomy.level == "supervised"
    hours = agent.metadata.autonomy.active_hours
    assert hours is not None
    assert hours.tz == "Europe/Madrid"
    assert hours.days == ("mon-fri",)
    assert hours.from_ == "08:00"
    assert agent.spec.limits.per_call.max_output_tokens == 4096
    assert agent.spec.limits.quotas[0].warn_at == 0.8
    assert agent.spec.limits.quotas[1].on_exhausted == "suspend"
    assert agent.spec.limits.run.max_duration == "2h"
    assert agent.spec.circuit_breaker is not None
    assert agent.spec.circuit_breaker.window == "10m"
    assert agent.spec.approvals.route_to == ("supervisor", "owner")
    assert agent.spec.effects.classify["zendesk.refund"] == ("financial", "irreversible")
    assert agent.spec.effects.deny == ("delete", "credential")

    assert baseline.kind == "BaselinePolicy"
    assert baseline.spec.limits.quotas[0].scope == "org"
    assert baseline.spec.selector.autonomy == ("supervised", "autonomous")
    assert baseline.spec.circuit_breaker is not None
    assert baseline.spec.circuit_breaker.action == "revoke_run"
    assert baseline.spec.effects.require_approval == ("financial",)
    guarded = AgentPolicyMetadata(
        agent="spend-guard",
        environment="prod",
        autonomy="supervised",
    )
    assert baseline.spec.selector.matches(guarded) is True
    interactive = AgentPolicyMetadata(agent="spend-guard", environment="prod")
    assert baseline.spec.selector.matches(interactive) is False


def test_shorthand_limits_do_not_require_an_output_cap() -> None:
    loaded = load_policy(EXAMPLES / "support-triage.yaml")

    assert loaded.spec.limits.cost_usd_per_day == 50
    assert loaded.spec.limits.per_call.max_output_tokens is None
    assert loaded.spec.limits.quotas == ()
    assert loaded.metadata.autonomy.level == "interactive"


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("2h", 7200), ("30m", 1800), ("90s", 90), ("90", 90), (90, 90)],
)
def test_duration_accepts_hours_minutes_and_seconds(value: object, seconds: int) -> None:
    if isinstance(value, str):
        assert parse_duration(value) == seconds
    document = _agent()
    spec = document["spec"]
    assert isinstance(spec, dict)
    limits = spec["limits"]
    assert isinstance(limits, dict)
    limits["run"] = {"max_duration": value}
    loaded = parse_policy(document)
    assert loaded.spec.limits.run.max_duration == str(value)


@pytest.mark.parametrize(
    "document",
    [
        _without_output_cap(_quota(kinds=["tool"])),
        _without_output_cap(_quota(metric="tool_calls")),
    ],
)
def test_quotas_that_do_not_reserve_model_output_load(document: dict[str, object]) -> None:
    loaded = parse_policy(document)

    assert loaded.spec.limits.per_call.max_output_tokens is None
    assert loaded.spec.limits.quotas


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (_without_output_cap(_agent()), "max_output_tokens is required"),
        (_without_output_cap(_quota(kinds=["model"])), "max_output_tokens is required"),
        (_without_output_cap(_quota(metric="cost_usd", max=1.5)), "max_output_tokens is required"),
        (_quota(scope="org"), "scope org is only valid on a baseline"),
        (
            _with_route(_agent(), ["agent:reviewer"], risk_tier="high"),
            "agent reviewers are rejected when risk_tier is high",
        ),
        (_with_route(_agent(), ["nobody"]), "route_to entries must be"),
        (_with_route(_agent(), ["user:"]), "route_to entries must be"),
    ],
)
def test_control_schema_rejects(document: dict[str, object], message: str) -> None:
    with pytest.raises(PolicyError, match=message):
        parse_policy(document)


def test_medium_risk_may_name_an_agent_reviewer() -> None:
    loaded = parse_policy(_with_route(_agent(), ["agent:reviewer"], risk_tier="medium"))

    assert loaded.spec.approvals.route_to == ("agent:reviewer",)


def test_baseline_may_use_org_scope() -> None:
    loaded = parse_policy(
        {
            "apiVersion": "tessera/v1",
            "kind": "BaselinePolicy",
            "metadata": {"name": "ceiling"},
            "spec": {
                "limits": {
                    "per_call": {"max_output_tokens": 10},
                    "quotas": [
                        {
                            "name": "org-daily-cost",
                            "metric": "cost_usd",
                            "window": "day",
                            "scope": "org",
                            "max": 5,
                        }
                    ],
                }
            },
        }
    )

    assert loaded.spec.limits.quotas[0].scope == "org"


def test_active_hours_reject_bad_clock_zone_and_days() -> None:
    hours = {
        "tz": "Europe/Madrid",
        "days": ["mon-fri"],
        "from": "08:00",
        "to": "20:00",
    }
    for broken in (
        {**hours, "tz": "Not/AZone"},
        {**hours, "days": ["fri-mon"]},
        {**hours, "from": "25:00"},
        {**hours, "days": []},
    ):
        document = _agent()
        meta = document["metadata"]
        assert isinstance(meta, dict)
        meta["autonomy"] = {"level": "supervised", "active_hours": broken}
        with pytest.raises(PolicyError):
            parse_policy(document)


def test_selector_autonomy_is_optional() -> None:
    selector = Selector()
    agent = AgentPolicyMetadata(agent="bot")

    assert selector.matches(agent) is True
