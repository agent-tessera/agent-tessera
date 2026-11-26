"""JSON routes that mirror classic UI behaviour for the console."""

from __future__ import annotations

import difflib
from typing import Literal

import yaml
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_tessera.errors import PolicyError
from agent_tessera.policy.effective import build_effective_policy
from agent_tessera.policy.evaluator import evaluate
from agent_tessera.policy.loader import parse_policy
from agent_tessera.policy.models import AgentPolicy, BaselinePolicy
from agent_tessera.types import Action, ActionKind
from tessera_server.api.deps import get_session, require_admin
from tessera_server.api.errors import ApiError
from tessera_server.db.models import AdminKey, Agent, Baseline, Instance
from tessera_server.services.policies import get_agent_policy
from tessera_server.services.policy_templates import (
    apply_policy_template,
    list_policy_templates,
)
from tessera_server.services.posture import posture_board
from tessera_server.services.secrets import create_admin_key, list_admin_keys, revoke_admin_key
from tessera_server.services.webhooks import ping_webhook

_ADMIN = Depends(require_admin)
_SESSION = Depends(get_session)
_LIMIT = Query(default=50, ge=1, le=500)


class DryRunBody(BaseModel):
    """Body for `POST /v1/agents/{id}/policy:dry-run`."""

    kind: Literal["model", "tool", "data", "agent"] = "tool"
    resource: str = Field(min_length=1)


class FromTemplateBody(BaseModel):
    """Body for `POST /v1/agents/{id}/policy:from-template`."""

    template_id: str = Field(min_length=1)


class AdminKeyCreateBody(BaseModel):
    """Body for `POST /v1/admin-keys`."""

    name: str = Field(min_length=1, max_length=200)


def console_parity_router() -> APIRouter:
    """Build console parity routes.

    Returns:
        A router with posture, instances, policy helpers, admin keys,
        webhook ping, and policy templates.
    """
    router = APIRouter()
    _add_posture(router)
    _add_instances(router)
    _add_policy_helpers(router)
    _add_admin_keys(router)
    _add_webhooks(router)
    _add_templates(router)
    return router


def _add_posture(router: APIRouter) -> None:
    """Register `GET /v1/posture`."""

    @router.get("/v1/posture", tags=["posture"], summary="Posture board")
    def read_posture_board(
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Return posture for every non-retired agent."""
        return {"items": posture_board(session)}


def _add_instances(router: APIRouter) -> None:
    """Register `GET /v1/agents/{id}/instances`."""

    @router.get(
        "/v1/agents/{agent_id}/instances",
        tags=["agents"],
        summary="List instances for an agent",
    )
    def read_instances(
        agent_id: str,
        *,
        limit: int = _LIMIT,
        cursor: str | None = None,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """List instances for one agent."""
        agent = session.get(Agent, agent_id)
        if agent is None:
            raise ApiError(404, "not_found", "agent was not found")
        query = select(Instance).where(Instance.agent_id == agent_id).order_by(Instance.id)
        if cursor is not None:
            query = query.where(Instance.id > cursor)
        rows = session.scalars(query.limit(limit + 1)).all()
        page = list(rows[:limit])
        next_cursor = page[-1].id if len(rows) > limit else None
        return {
            "items": [
                {
                    "id": row.id,
                    "host": row.host,
                    "runtime": row.runtime,
                    "started_at": row.started_at.isoformat().replace("+00:00", "Z"),
                    "last_heartbeat_at": row.last_heartbeat_at.isoformat().replace("+00:00", "Z"),
                    "revoked": row.revoked,
                }
                for row in page
            ],
            "next_cursor": next_cursor,
        }


def _add_policy_helpers(router: APIRouter) -> None:
    """Register validate and dry-run routes."""

    @router.post(
        "/v1/agents/{agent_id}/policy:validate",
        tags=["policies"],
        summary="Validate policy YAML without saving",
    )
    async def validate_policy(
        agent_id: str,
        request: Request,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Validate draft YAML and diff it against the stored policy."""
        draft = (await request.body()).decode("utf-8")
        try:
            stored = get_agent_policy(session, agent_id)
        except ApiError:
            stored = ""
        try:
            _parse_agent_policy(session, agent_id, draft)
        except PolicyError as err:
            return {"valid": False, "error": str(err), "diff": None}
        except ApiError as err:
            return {"valid": False, "error": err.message, "diff": None}
        diff = "".join(
            difflib.unified_diff(
                stored.splitlines(keepends=True),
                draft.splitlines(keepends=True),
                fromfile="stored",
                tofile="draft",
            )
        )
        return {"valid": True, "error": None, "diff": diff}

    @router.post(
        "/v1/agents/{agent_id}/policy:dry-run",
        tags=["policies"],
        summary="Evaluate one action against the stored policy",
    )
    def dry_run_policy(
        agent_id: str,
        body: DryRunBody,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Evaluate one action against the agent's effective policy."""
        yaml_text = get_agent_policy(session, agent_id)
        document = _load_agent_policy(yaml_text)
        baselines = _baselines(session)
        effective = build_effective_policy(document, baselines)
        kind = ActionKind(body.kind)
        decision = evaluate(effective, Action(kind=kind, resource=body.resource))
        return {
            "effect": decision.effect.value,
            "reason": decision.reason,
            "rule": decision.rule,
        }

    @router.post(
        "/v1/agents/{agent_id}/policy:from-template",
        tags=["policies"],
        summary="Apply a built-in policy template",
    )
    def from_template(
        agent_id: str,
        body: FromTemplateBody,
        admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Apply a built-in template as the agent's policy."""
        actor = f"admin:{admin.id}" if admin is not None else "dev"
        return apply_policy_template(session, agent_id, body.template_id, actor=actor)


def _add_admin_keys(router: APIRouter) -> None:
    """Register admin key routes."""

    @router.get("/v1/admin-keys", tags=["admin"], summary="List admin keys")
    def read_admin_keys(
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """List admin keys (secrets never included)."""
        return list_admin_keys(session)

    @router.post("/v1/admin-keys", tags=["admin"], summary="Create an admin key")
    def post_admin_key(
        body: AdminKeyCreateBody,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Create an admin key; the secret is returned once."""
        raw = create_admin_key(session, body.name)
        return {"token": raw, "name": body.name}

    @router.post(
        "/v1/admin-keys/{key_id}:revoke",
        tags=["admin"],
        summary="Revoke an admin key",
    )
    def post_revoke_admin_key(
        key_id: str,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Revoke an admin key and end its sessions."""
        return {**revoke_admin_key(session, key_id), "revoked": True}


def _add_webhooks(router: APIRouter) -> None:
    """Register webhook ping."""

    @router.post(
        "/v1/webhooks/{webhook_id}:ping",
        tags=["webhooks"],
        summary="Send a signed webhook.ping",
    )
    def post_webhook_ping(
        webhook_id: str,
        request: Request,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Deliver one signed ping to the webhook endpoint."""
        ping_webhook(session, request.app.state.settings, webhook_id)
        return {"ok": True}


def _add_templates(router: APIRouter) -> None:
    """Register policy template list."""

    @router.get(
        "/v1/policy-templates",
        tags=["policies"],
        summary="List built-in policy templates",
    )
    def read_templates(_admin: AdminKey | None = _ADMIN) -> dict[str, object]:
        """Return built-in onboarding templates."""
        return list_policy_templates()


def _parse_agent_policy(session: Session, agent_id: str, text: str) -> AgentPolicy:
    """Parse and check an agent policy draft.

    Args:
        session: Open database session.
        agent_id: Expected agent id.
        text: YAML draft.

    Returns:
        Parsed agent policy.

    Raises:
        ApiError: Status 404/422 when the draft is unusable.
        PolicyError: When the YAML is not a valid policy.
    """
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise ApiError(404, "not_found", "agent was not found")
    document = _load_agent_policy(text)
    if document.metadata.agent != agent.name:
        raise ApiError(422, "invalid_policy", "policy agent name does not match")
    if document.metadata.environment != agent.environment:
        raise ApiError(422, "invalid_policy", "policy environment does not match")
    build_effective_policy(document, _baselines(session))
    return document


def _load_agent_policy(text: str) -> AgentPolicy:
    """Parse YAML text into an agent policy.

    Args:
        text: YAML document.

    Returns:
        Parsed agent policy.

    Raises:
        PolicyError: When the YAML is not a policy.
        ApiError: Status 422 when the document is not an agent policy.
    """
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as err:
        raise PolicyError(str(err)) from err
    document = parse_policy(loaded)
    if not isinstance(document, AgentPolicy):
        raise ApiError(422, "invalid_policy", "agent policy is required")
    return document


def _baselines(session: Session) -> list[BaselinePolicy]:
    """Load stored baselines.

    Args:
        session: Open database session.

    Returns:
        Parsed baseline policies.
    """
    rows = session.scalars(select(Baseline).order_by(Baseline.name)).all()
    parsed: list[BaselinePolicy] = []
    for row in rows:
        try:
            loaded = yaml.safe_load(row.yaml)
            document = parse_policy(loaded)
        except (PolicyError, yaml.YAMLError):
            continue
        if isinstance(document, BaselinePolicy):
            parsed.append(document)
    return parsed


__all__ = ["console_parity_router"]
