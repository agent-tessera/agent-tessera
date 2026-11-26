"""Load one agent or baseline policy from YAML."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import ValidationError

from agent_tessera.errors import PolicyError
from agent_tessera.policy.models import AgentPolicy, BaselinePolicy, SupervisorGrants


def _format_validation_error(err: ValidationError) -> str:
    """Render a validation error as one `<location>: <message>` line per problem.

    Args:
        err: The error raised by the policy models.

    Returns:
        A multiline message. Locations use dotted fields, such as
        `spec.tools.allow.0`.
    """
    lines: list[str] = []
    for item in err.errors():
        location = ".".join(str(part) for part in item["loc"])
        lines.append(f"{location}: {item['msg']}")
    return "\n".join(lines)


def _read_document(path: Path) -> object:
    """Read exactly one YAML document from a file.

    Args:
        path: Policy file to read.

    Returns:
        The single document, which may be any YAML value.

    Raises:
        PolicyError: If the file cannot be read, the YAML is invalid, or the
            file does not contain exactly one document.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as err:
        raise PolicyError(f"unable to read file: {err}", path=path) from err
    try:
        documents = list(yaml.safe_load_all(text))
    except yaml.YAMLError as err:
        raise PolicyError(f"invalid YAML: {err}", path=path) from err
    if len(documents) != 1:
        found = len(documents)
        raise PolicyError(f"expected exactly one YAML document, found {found}", path=path)
    return documents[0]


def parse_policy(
    data: object, *, path: Path | None = None
) -> AgentPolicy | BaselinePolicy | SupervisorGrants:
    """Validate one already parsed policy document.

    Args:
        data: A mapping with `kind` of `AgentPolicy`, `BaselinePolicy`, or
            `SupervisorGrants`.
        path: File the document came from. Included in errors when set.

    Returns:
        The agent policy, baseline, or supervisor grant document.

    Raises:
        PolicyError: If `data` is not a policy document. Validation failures
            list each problem as `<dotted.location>: <message>`.

    Example:
        ```python
        from agent_tessera.policy import parse_policy

        policy = parse_policy(
            {
                "apiVersion": "tessera/v1",
                "kind": "AgentPolicy",
                "metadata": {"agent": "bot"},
            }
        )
        assert policy.metadata.agent == "bot"
        ```
    """
    if not isinstance(data, Mapping):
        raise PolicyError("policy must be a mapping", path=path)
    payload = dict(data)
    kind = payload.get("kind")
    if kind == "AgentPolicy":
        model: type[AgentPolicy | BaselinePolicy | SupervisorGrants] = AgentPolicy
    elif kind == "BaselinePolicy":
        model = BaselinePolicy
    elif kind == "SupervisorGrants":
        model = SupervisorGrants
    else:
        raise PolicyError(
            "kind must be AgentPolicy, BaselinePolicy, or SupervisorGrants",
            path=path,
        )
    try:
        return model.model_validate(payload)
    except ValidationError as err:
        raise PolicyError(_format_validation_error(err), path=path) from err


def load_policy(path: str | Path) -> AgentPolicy | BaselinePolicy | SupervisorGrants:
    """Read one YAML policy document from disk.

    Args:
        path: Path of the policy file.

    Returns:
        The agent policy, baseline, or supervisor grant document.

    Raises:
        PolicyError: If the file cannot be read, the YAML is invalid, or the
            document is not a single policy. The error's `path` is this file.
    """
    file_path = Path(path)
    return parse_policy(_read_document(file_path), path=file_path)


def load_agent_policy(path: str | Path) -> AgentPolicy:
    """Read an agent policy from disk.

    Args:
        path: Path of the policy file.

    Returns:
        The agent policy.

    Raises:
        PolicyError: If the file is not a readable `AgentPolicy` document.
    """
    policy = load_policy(path)
    if isinstance(policy, AgentPolicy):
        return policy
    raise PolicyError("expected an AgentPolicy", path=Path(path))


def load_baseline_policy(path: str | Path) -> BaselinePolicy:
    """Read a baseline policy from disk.

    Args:
        path: Path of the policy file.

    Returns:
        The baseline policy.

    Raises:
        PolicyError: If the file is not a readable `BaselinePolicy` document.
    """
    policy = load_policy(path)
    if isinstance(policy, BaselinePolicy):
        return policy
    raise PolicyError("expected a BaselinePolicy", path=Path(path))


__all__ = [
    "load_agent_policy",
    "load_baseline_policy",
    "load_policy",
    "parse_policy",
]
