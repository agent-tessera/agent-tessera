from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agent_tessera.errors import PolicyError
from agent_tessera.policy import AgentPolicy
from agent_tessera.policy.loader import (
    load_agent_policy,
    load_baseline_policy,
    load_policy,
    parse_policy,
)

EXAMPLES = Path(__file__).resolve().parents[3] / "policies" / "examples"

AGENT_YAML = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: bot
"""

BASELINE_YAML = """\
apiVersion: tessera/v1
kind: BaselinePolicy
metadata:
  name: base
"""


def _write(tmp_path: Path, text: str, name: str = "policy.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "path",
    sorted(path for path in EXAMPLES.glob("*.yaml") if path.name != "registry.yaml"),
    ids=lambda path: path.name,
)
def test_example_policies_load(path: Path) -> None:
    loaded = load_policy(path)

    assert loaded.api_version == "tessera/v1"
    assert loaded.kind in {"AgentPolicy", "BaselinePolicy", "SupervisorGrants"}


def test_support_triage_example_fields() -> None:
    policy = load_agent_policy(EXAMPLES / "support-triage.yaml")

    assert policy.metadata.agent == "support-triage"
    assert policy.metadata.environment == "prod"
    assert policy.spec.limits.tokens_per_day == 2_000_000
    assert policy.spec.limits.cost_usd_per_day == 50
    assert policy.spec.tools.require_approval[0].name == "zendesk.refund"
    assert policy.spec.tools.require_approval[0].when[0].value == 100
    assert policy.spec.guardrails.on_missing == "block"


def test_prod_baseline_example_selects_the_support_agent() -> None:
    baseline = load_baseline_policy(EXAMPLES / "prod-baseline.yaml")
    agent = load_agent_policy(EXAMPLES / "support-triage.yaml")

    assert baseline.metadata.name == "prod-baseline"
    assert baseline.spec.require_owner is True
    assert baseline.spec.tools.deny == ("shell.*", "fs.delete*")
    assert baseline.spec.selector.matches(agent.metadata) is True


def test_quickstart_example_shape() -> None:
    policy = load_agent_policy(EXAMPLES / "quickstart.yaml")

    assert policy.metadata.agent == "quickstart"
    assert policy.metadata.environment == "dev"
    assert policy.spec.models.allow == (
        "openai/gpt-4.1-mini",
        "anthropic/claude-*",
        "local/*",
    )
    assert policy.spec.tools.allow == ("files.read", "files.list")
    assert policy.spec.tools.deny == ("shell.*",)
    assert policy.spec.tools.require_approval[0].name == "files.delete"
    assert policy.spec.tools.require_approval[0].when == ()
    assert policy.spec.limits.max_steps_per_task == 10


def test_parse_policy_accepts_an_agent_document() -> None:
    policy = parse_policy(
        {"apiVersion": "tessera/v1", "kind": "AgentPolicy", "metadata": {"agent": "bot"}}
    )

    assert isinstance(policy, AgentPolicy)
    assert policy.metadata.agent == "bot"


def test_load_policy_accepts_a_string_path(tmp_path: Path) -> None:
    path = _write(tmp_path, AGENT_YAML)

    policy = load_policy(str(path))

    assert isinstance(policy, AgentPolicy)


def test_missing_file_sets_path(tmp_path: Path) -> None:
    path = tmp_path / "missing.yaml"

    with pytest.raises(PolicyError) as exc_info:
        load_policy(path)

    assert exc_info.value.path == path
    assert isinstance(exc_info.value.__cause__, OSError)


def test_directory_is_unreadable(tmp_path: Path) -> None:
    with pytest.raises(PolicyError) as exc_info:
        load_policy(tmp_path)

    assert exc_info.value.path == tmp_path
    assert isinstance(exc_info.value.__cause__, OSError)


def test_invalid_utf8_sets_path(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_bytes(b"\xff")

    with pytest.raises(PolicyError) as exc_info:
        load_policy(path)

    assert exc_info.value.path == path
    assert isinstance(exc_info.value.__cause__, UnicodeError)


def test_invalid_yaml_sets_path(tmp_path: Path) -> None:
    path = _write(tmp_path, "kind: [\n")

    with pytest.raises(PolicyError) as exc_info:
        load_policy(path)

    assert exc_info.value.path == path
    assert isinstance(exc_info.value.__cause__, yaml.YAMLError)
    assert "invalid YAML" in str(exc_info.value)


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "")

    with pytest.raises(PolicyError) as exc_info:
        load_policy(path)

    assert exc_info.value.path == path
    assert "exactly one YAML document" in str(exc_info.value)


def test_multiple_documents_are_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, f"{AGENT_YAML}---\n{BASELINE_YAML}")

    with pytest.raises(PolicyError) as exc_info:
        load_policy(path)

    assert exc_info.value.path == path
    assert "exactly one YAML document" in str(exc_info.value)


@pytest.mark.parametrize("text", ["42\n", "- item\n", "null\n"])
def test_non_mapping_document_is_rejected(tmp_path: Path, text: str) -> None:
    path = _write(tmp_path, text)

    with pytest.raises(PolicyError) as exc_info:
        load_policy(path)

    assert exc_info.value.path == path
    assert "mapping" in str(exc_info.value)


def test_parse_policy_without_path_omits_the_prefix() -> None:
    with pytest.raises(PolicyError) as exc_info:
        parse_policy(["not", "a", "mapping"])

    assert exc_info.value.path is None
    assert str(exc_info.value) == "policy must be a mapping"


@pytest.mark.parametrize(
    "text",
    ["apiVersion: tessera/v1\n", "kind: Other\napiVersion: tessera/v1\n"],
)
def test_missing_or_unknown_kind_is_rejected(tmp_path: Path, text: str) -> None:
    path = _write(tmp_path, text)

    with pytest.raises(PolicyError) as exc_info:
        load_policy(path)

    assert exc_info.value.path == path
    assert "kind must be AgentPolicy, BaselinePolicy, or SupervisorGrants" in str(exc_info.value)


def test_validation_error_uses_dotted_locations(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: BAD
spec:
  tools:
    allow:
      - 1
""",
    )

    with pytest.raises(PolicyError) as exc_info:
        load_policy(path)

    message = str(exc_info.value)
    assert exc_info.value.path == path
    assert isinstance(exc_info.value.__cause__, ValidationError)
    assert "metadata.agent:" in message
    assert "spec.tools.allow.0: Input should be a valid string" in message
    assert "\n" in message.split(": ", 1)[1]


def test_baseline_validation_error_is_wrapped(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
apiVersion: tessera/v1
kind: BaselinePolicy
metadata:
  name: base
spec:
  tools:
    allow:
      - shell.exec
""",
    )

    with pytest.raises(PolicyError) as exc_info:
        load_baseline_policy(path)

    assert exc_info.value.path == path
    assert isinstance(exc_info.value.__cause__, ValidationError)
    assert "spec.tools.allow" in str(exc_info.value)


def test_load_agent_policy_rejects_a_baseline(tmp_path: Path) -> None:
    path = _write(tmp_path, BASELINE_YAML)

    with pytest.raises(PolicyError) as exc_info:
        load_agent_policy(str(path))

    assert exc_info.value.path == path
    assert str(exc_info.value) == f"{path}: expected an AgentPolicy"


def test_load_baseline_policy_rejects_an_agent(tmp_path: Path) -> None:
    path = _write(tmp_path, AGENT_YAML)

    with pytest.raises(PolicyError) as exc_info:
        load_baseline_policy(path)

    assert exc_info.value.path == path
    assert "expected a BaselinePolicy" in str(exc_info.value)
