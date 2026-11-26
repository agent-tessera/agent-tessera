"""Owner and agent registry routes."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from tessera_server.api.deps import get_session, require_admin
from tessera_server.db.models import AdminKey
from tessera_server.services.registry import (
    create_agent,
    create_owner,
    get_agent,
    issue_registration_token,
    list_agents,
    list_owners,
    revoke_instance,
    transition_agent,
    update_agent,
    update_owner,
)


class _OwnerBody(BaseModel):
    """Request body for creating an owner."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["user", "team"]
    email: str = Field(min_length=1, max_length=320)
    display_name: str = Field(min_length=1, max_length=200)


class _OwnerPatch(BaseModel):
    """Request body for changing an owner."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    active: bool | None = None


class _AgentBody(BaseModel):
    """Request body for registering an agent."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$", max_length=63)
    environment: str = Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$", max_length=63)
    owner_id: str
    description: str = ""
    risk_tier: Literal["low", "medium", "high"] = "low"
    labels: dict[str, str] = Field(default_factory=dict)
    autonomy: Literal["assistive", "interactive", "supervised", "autonomous"] = "interactive"
    supervisors: list[str] = Field(default_factory=list)


class _AgentPatch(BaseModel):
    """Request body for changing agent metadata."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    owner_id: str | None = None
    risk_tier: Literal["low", "medium", "high"] | None = None
    labels: dict[str, str] | None = None
    autonomy: Literal["assistive", "interactive", "supervised", "autonomous"] | None = None
    supervisors: list[str] | None = None


class _TokenBody(BaseModel):
    """Request body for creating a registration token."""

    model_config = ConfigDict(extra="forbid")

    reusable: bool = False
    expires_in: int | None = Field(default=None, gt=0)


class _ReasonBody(BaseModel):
    """Optional operator reason for lifecycle and instance revoke."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


_EMPTY_REASON = _ReasonBody()

_ADMIN = Depends(require_admin)
_SESSION = Depends(get_session)
_LIMIT = Query(default=50, ge=1, le=500)


def _optional_reason(body: _ReasonBody) -> str | None:
    """Normalize an optional reason body field.

    Args:
        body: Request body with an optional `reason`.

    Returns:
        Trimmed reason, or `None` when blank.
    """
    if body.reason is None:
        return None
    trimmed = body.reason.strip()
    return trimmed or None


def registry_router() -> APIRouter:
    """Build the owner, agent, and instance routes.

    Returns:
        A router mounted at `/v1`.
    """
    router = APIRouter()

    @router.post("/v1/owners", tags=["owners"], summary="Create an owner")
    def post_owner(
        body: _OwnerBody,
        admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Create an owner.

        Args:
            body: Owner fields.
            admin: Authenticated admin key, if one was presented.
            session: Database session.

        Returns:
            The owner document.
        """
        return create_owner(
            session,
            owner_type=body.type,
            email=body.email,
            display_name=body.display_name,
            actor=_actor(admin),
        )

    @router.get("/v1/owners", tags=["owners"], summary="List owners")
    def get_owners(
        limit: int = _LIMIT,
        cursor: str | None = None,
        email: str | None = None,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """List owners.

        Args:
            limit: Page size.
            cursor: Id of the last owner already seen.
            email: Exact email address to match.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            A page of owners.
        """
        return list_owners(session, limit=limit, cursor=cursor, email=email)

    @router.patch("/v1/owners/{owner_id}", tags=["owners"], summary="Update an owner")
    def patch_owner(
        owner_id: str,
        body: _OwnerPatch,
        admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Change an owner's name or active flag.

        Args:
            owner_id: Owner to update.
            body: Fields to change.
            admin: Authenticated admin key.
            session: Database session.

        Returns:
            The updated owner.
        """
        return update_owner(
            session,
            owner_id,
            display_name=body.display_name,
            active=body.active,
            actor=_actor(admin),
        )

    @router.post("/v1/agents", tags=["agents"], summary="Register an agent")
    def post_agent(
        body: _AgentBody,
        admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Register an agent.

        Args:
            body: Agent fields.
            admin: Authenticated admin key.
            session: Database session.

        Returns:
            The agent document, with status `draft`.
        """
        return create_agent(
            session,
            name=body.name,
            environment=body.environment,
            owner_id=body.owner_id,
            description=body.description,
            risk_tier=body.risk_tier,
            labels=body.labels,
            autonomy=body.autonomy,
            supervisors=body.supervisors,
            actor=_actor(admin),
        )

    @router.get("/v1/agents", tags=["agents"], summary="List agents")
    def get_agents(
        *,
        environment: str | None = None,
        status: str | None = None,
        owner_id: str | None = None,
        orphaned: bool | None = None,
        limit: int = _LIMIT,
        cursor: str | None = None,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """List agents.

        Args:
            environment: Filter by environment.
            status: Filter by lifecycle status.
            owner_id: Filter by owner.
            orphaned: Filter by an inactive owner.
            limit: Page size.
            cursor: Id of the last agent already seen.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            A page of agents.
        """
        return list_agents(
            session,
            environment=environment,
            status=status,
            owner_id=owner_id,
            orphaned=orphaned,
            limit=limit,
            cursor=cursor,
        )

    @router.get("/v1/agents/{agent_id}", tags=["agents"], summary="Get one agent")
    def get_one_agent(
        agent_id: str,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Return one agent.

        Args:
            agent_id: Agent to load.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            The agent, its orphaned flag, policy version, and recent instances.
        """
        return get_agent(session, agent_id)

    @router.patch("/v1/agents/{agent_id}", tags=["agents"], summary="Update an agent")
    def patch_agent(
        agent_id: str,
        body: _AgentPatch,
        admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Change agent metadata.

        Args:
            agent_id: Agent to update.
            body: Fields to change.
            admin: Authenticated admin key.
            session: Database session.

        Returns:
            The updated agent.
        """
        return update_agent(
            session,
            agent_id,
            description=body.description,
            owner_id=body.owner_id,
            risk_tier=body.risk_tier,
            labels=body.labels,
            autonomy=body.autonomy,
            supervisors=body.supervisors,
            actor=_actor(admin),
        )

    @router.post("/v1/agents/{agent_id}:activate", tags=["agents"], summary="Activate an agent")
    def activate(
        agent_id: str,
        body: _ReasonBody = _EMPTY_REASON,
        admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Activate an agent.

        Args:
            agent_id: Agent to activate.
            body: Optional JSON with an operator `reason` (max 500 chars).
            admin: Authenticated admin key.
            session: Database session.

        Returns:
            The updated agent.
        """
        return transition_agent(
            session,
            agent_id,
            "activate",
            actor=_actor(admin),
            reason=_optional_reason(body),
        )

    @router.post("/v1/agents/{agent_id}:suspend", tags=["agents"], summary="Suspend an agent")
    def suspend(
        agent_id: str,
        body: _ReasonBody = _EMPTY_REASON,
        admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Suspend an agent.

        Args:
            agent_id: Agent to suspend.
            body: Optional JSON with an operator `reason` (max 500 chars).
            admin: Authenticated admin key.
            session: Database session.

        Returns:
            The updated agent.
        """
        return transition_agent(
            session,
            agent_id,
            "suspend",
            actor=_actor(admin),
            reason=_optional_reason(body),
        )

    @router.post("/v1/agents/{agent_id}:retire", tags=["agents"], summary="Retire an agent")
    def retire(
        agent_id: str,
        body: _ReasonBody = _EMPTY_REASON,
        admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Retire an agent.

        Args:
            agent_id: Agent to retire.
            body: Optional JSON with an operator `reason` (max 500 chars).
            admin: Authenticated admin key.
            session: Database session.

        Returns:
            The updated agent.
        """
        return transition_agent(
            session,
            agent_id,
            "retire",
            actor=_actor(admin),
            reason=_optional_reason(body),
        )

    @router.post(
        "/v1/agents/{agent_id}/registration-tokens",
        tags=["agents"],
        summary="Create a registration token",
    )
    def post_token(
        agent_id: str,
        body: _TokenBody,
        admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Create a registration token.

        Args:
            agent_id: Agent the token registers.
            body: Reuse and expiry options.
            admin: Authenticated admin key.
            session: Database session.

        Returns:
            The token id and the full token, shown once.
        """
        return issue_registration_token(
            session,
            agent_id,
            reusable=body.reusable,
            expires_in=body.expires_in,
            actor=_actor(admin),
        )

    @router.post(
        "/v1/instances/{instance_id}:revoke",
        tags=["agents"],
        summary="Revoke an instance",
    )
    def post_revoke(
        instance_id: str,
        body: _ReasonBody = _EMPTY_REASON,
        admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Revoke an instance.

        Args:
            instance_id: Instance to revoke.
            body: Optional JSON with an operator `reason` (max 500 chars).
            admin: Authenticated admin key.
            session: Database session.

        Returns:
            The revocation row.
        """
        return revoke_instance(
            session,
            instance_id,
            actor=_actor(admin),
            reason=_optional_reason(body),
        )

    return router


def _actor(admin: AdminKey | None) -> str:
    """Name the admin key for the audit log.

    Args:
        admin: Authenticated key, or `None` in development mode.

    Returns:
        `admin:<id>` or `dev`.
    """
    if admin is None:
        return "dev"
    return f"admin:{admin.id}"


__all__ = ["registry_router"]
