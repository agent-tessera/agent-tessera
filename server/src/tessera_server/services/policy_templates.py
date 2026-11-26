"""Built-in onboarding policy templates."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from tessera_server.api.errors import ApiError
from tessera_server.db.models import Agent
from tessera_server.services.policies import put_agent_policy

_TEMPLATES = Path(__file__).resolve().parents[1] / "policy_templates"

TEMPLATE_META: tuple[dict[str, str], ...] = (
    {
        "id": "read-only",
        "title": "Read-only",
        "blurb": "Models and read tools only; shell and writes denied.",
        "file": "read-only.yaml",
    },
    {
        "id": "support-with-approvals",
        "title": "Support with approvals",
        "blurb": "Ticket tools with refunds gated by approval.",
        "file": "support-with-approvals.yaml",
    },
    {
        "id": "dev-permissive",
        "title": "Dev permissive",
        "blurb": "Allow everything. Fine for local demos, not for production.",
        "file": "dev-permissive.yaml",
    },
)


def list_policy_templates() -> dict[str, object]:
    """Return built-in template metadata.

    Returns:
        `items` with id, title, blurb, and `yaml` (placeholders `AGENT` /
        `ENV` still unsubstituted for console preview).
    """
    items: list[dict[str, str]] = []
    for item in TEMPLATE_META:
        path = _TEMPLATES / item["file"]
        yaml_text = path.read_text(encoding="utf-8") if path.is_file() else ""
        items.append(
            {
                "id": item["id"],
                "title": item["title"],
                "blurb": item["blurb"],
                "yaml": yaml_text,
            }
        )
    return {"items": items}


def apply_policy_template(
    session: Session,
    agent_id: str,
    template_id: str,
    *,
    actor: str,
) -> dict[str, object]:
    """Apply a built-in template as the agent's policy.

    Args:
        session: Open database session.
        agent_id: Target agent.
        template_id: Template id from `TEMPLATE_META`.
        actor: Audit actor.

    Returns:
        The stored policy document summary from `put_agent_policy`.

    Raises:
        ApiError: Status 404 when the template is unknown.
    """
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise ApiError(404, "not_found", "agent was not found")
    meta = next((item for item in TEMPLATE_META if item["id"] == template_id), None)
    if meta is None:
        raise ApiError(404, "not_found", "policy template not found")
    path = _TEMPLATES / meta["file"]
    if not path.is_file():
        raise ApiError(404, "not_found", "policy template not found")
    text = (
        path.read_text(encoding="utf-8")
        .replace("agent: AGENT", f"agent: {agent.name}", 1)
        .replace("environment: ENV", f"environment: {agent.environment}", 1)
    )
    return put_agent_policy(session, agent_id, text, actor=actor)


__all__ = ["TEMPLATE_META", "apply_policy_template", "list_policy_templates"]
