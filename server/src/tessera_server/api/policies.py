"""Policy, baseline, and bundle routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from tessera_server.api.deps import get_session, require_admin
from tessera_server.api.errors import ApiError
from tessera_server.db.models import AdminKey
from tessera_server.services.keys import verify_runtime_token
from tessera_server.services.policies import (
    delete_baseline,
    dry_run_baseline,
    get_agent_policy,
    get_baseline,
    issue_bundle,
    list_baseline_agents,
    list_baselines,
    put_agent_policy,
    put_baseline,
    validate_baseline,
)
from tessera_server.services.secrets import parse_secret

_ADMIN = Depends(require_admin)
_SESSION = Depends(get_session)
_LIMIT = Query(default=50, ge=1, le=500)
_LOOPBACK = frozenset({"127.0.0.1", "::1"})


def policies_router() -> APIRouter:
    """Build the policy and bundle routes.

    Returns:
        A router for agent policies, baselines, and signed bundles.
    """
    router = APIRouter(tags=["policies"])

    @router.put("/v1/agents/{agent_id}/policy")
    async def put_policy(
        agent_id: str,
        request: Request,
        admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Replace an agent's policy.

        Args:
            agent_id: Agent the document belongs to.
            request: Request whose body is YAML.
            admin: Authenticated admin key.
            session: Database session.

        Returns:
            The effective policy version.
        """
        text = (await request.body()).decode("utf-8")
        return put_agent_policy(session, agent_id, text, actor=_actor(admin))

    @router.get("/v1/agents/{agent_id}/policy")
    def read_policy(
        agent_id: str,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> Response:
        """Return an agent's policy YAML.

        Args:
            agent_id: Agent to read.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            The stored YAML document.
        """
        return Response(content=get_agent_policy(session, agent_id), media_type="application/yaml")

    @router.put("/v1/baselines/{name}")
    async def put_one_baseline(
        name: str,
        request: Request,
        admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> Response:
        """Replace a baseline.

        Args:
            name: Baseline name.
            request: Request whose body is YAML.
            admin: Authenticated admin key.
            session: Database session.

        Returns:
            The stored YAML document.
        """
        text = (await request.body()).decode("utf-8")
        stored = put_baseline(session, name, text, actor=_actor(admin))
        return Response(content=stored, media_type="application/yaml")

    @router.get("/v1/baselines/{name}")
    def read_baseline(
        name: str,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> Response:
        """Return a baseline's YAML.

        Args:
            name: Baseline name.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            The stored YAML document.
        """
        return Response(content=get_baseline(session, name), media_type="application/yaml")

    @router.delete("/v1/baselines/{name}")
    def remove_baseline(
        name: str,
        admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> Response:
        """Delete a baseline.

        Args:
            name: Baseline name.
            admin: Authenticated admin key.
            session: Database session.

        Returns:
            An empty response.
        """
        delete_baseline(session, name, actor=_actor(admin))
        return Response(status_code=204)

    @router.get("/v1/baselines")
    def read_baselines(
        *,
        limit: int = _LIMIT,
        cursor: str | None = None,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """List baselines.

        Args:
            limit: Page size.
            cursor: Name of the last baseline already seen.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            A page of baseline names.
        """
        return list_baselines(session, limit=limit, cursor=cursor)

    @router.get("/v1/baselines/{name}/agents")
    def read_baseline_agents(
        name: str,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """List agents that inherit a baseline via its selector.

        Args:
            name: Baseline name.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            Matching agents.
        """
        return list_baseline_agents(session, name)

    @router.post("/v1/baselines/{name}:validate")
    async def validate_one_baseline(
        name: str,
        request: Request,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Validate baseline YAML without writing.

        Args:
            name: Baseline name.
            request: Request whose body is YAML.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            Validation result with digest.
        """
        text = (await request.body()).decode("utf-8")
        return validate_baseline(session, name, text)

    @router.post("/v1/baselines/{name}:dry-run")
    async def dry_run_one_baseline(
        name: str,
        request: Request,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Preview baseline replace impact without writing.

        Args:
            name: Baseline name.
            request: Request whose body is YAML.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            Matching count and per-agent impact rows.
        """
        text = (await request.body()).decode("utf-8")
        return dry_run_baseline(session, name, text)

    @router.get("/v1/bundles/{agent_id}")
    def read_bundle(
        agent_id: str,
        request: Request,
        session: Session = _SESSION,
    ) -> Response:
        """Return the agent's signed policy bundle.

        Args:
            agent_id: Agent the bundle is for.
            request: Request carrying an admin key or that agent's runtime JWT.
            session: Database session.

        Returns:
            A JWT, or 304 when `If-None-Match` is the current version.
        """
        _authorize_bundle(request, session, agent_id)
        status, etag, token = issue_bundle(
            session,
            request.app.state.settings,
            agent_id,
            if_none_match=request.headers.get("if-none-match"),
        )
        if token is None:
            return Response(status_code=status, headers={"ETag": etag})
        return Response(
            content=token,
            status_code=status,
            media_type="application/jwt",
            headers={"ETag": etag},
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


def _authorize_bundle(request: Request, session: Session, agent_id: str) -> None:
    """Allow an admin or the agent the bundle belongs to.

    Args:
        request: Current HTTP request.
        session: Database session used to verify a runtime JWT.
        agent_id: Agent named in the URL.

    Raises:
        ApiError: Status 401 when the credential is missing or invalid, or 403
            when a runtime token belongs to another agent.
    """
    client = request.client.host if request.client is not None else ""
    if request.app.state.settings.dev and client in _LOOPBACK:
        return
    header = request.headers.get("authorization", "")
    scheme, _, credential = header.partition(" ")
    if scheme.lower() != "bearer" or credential == "" or parse_secret(credential, prefix="tsa"):
        require_admin(request)
        return
    try:
        claims = verify_runtime_token(session, request.app.state.settings, credential)
    except ValueError as err:
        raise ApiError(401, "invalid_token", "runtime token is not valid") from err
    if claims.get("agent_id") != agent_id:
        raise ApiError(403, "forbidden", "token is for another agent")


__all__ = ["policies_router"]
