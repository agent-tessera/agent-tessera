"""Runtime token endpoint."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from tessera_server.api.deps import get_session
from tessera_server.api.errors import ApiError
from tessera_server.services.delegation import exchange_token, is_exchange
from tessera_server.services.registry import grant_dev, grant_refresh, grant_registration
from tessera_server.services.workload import grant_workload

_SESSION = Depends(get_session)


class _TokenBody(BaseModel):
    """Body of `POST /v1/token`. The grant selects which fields are required."""

    model_config = ConfigDict(extra="forbid")

    grant_type: Literal[
        "registration",
        "refresh",
        "dev",
        "workload",
        "token-exchange",
        "urn:ietf:params:oauth:grant-type:token-exchange",
    ]
    registration_token: str | None = None
    assertion: str | None = None
    subject_token: str | None = None
    actor_token: str | None = None
    instance_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    host: str | None = None
    runtime: dict[str, str] | None = None
    agent: str | None = Field(default=None, pattern=r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
    environment: str | None = None
    bound_supervisor: str | None = Field(default=None, max_length=200)


def token_router() -> APIRouter:
    """Build the token route.

    Returns:
        A router serving `POST /v1/token`.
    """
    router = APIRouter(tags=["token"])

    @router.post("/v1/token")
    def post_token(
        body: _TokenBody,
        request: Request,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Exchange a registration token, a runtime JWT, a dev grant, or a workload assertion.

        Args:
            body: Grant and, for registration or dev, the instance description.
            request: Current request. Refresh reads the bearer token. The dev
                grant also reads the client address.
            session: Database session.

        Returns:
            The access token and the agent it represents.
        """
        settings = request.app.state.settings
        if is_exchange(body.grant_type):
            if body.subject_token is None or body.actor_token is None:
                raise ApiError(422, "invalid_request", "token exchange is incomplete")
            return exchange_token(
                session,
                settings,
                subject_token=body.subject_token,
                actor_token=body.actor_token,
            )
        if body.grant_type == "workload":
            if (
                body.assertion is None
                or body.agent is None
                or body.environment is None
                or body.instance_id is None
            ):
                raise ApiError(422, "invalid_request", "workload grant is incomplete")
            return grant_workload(
                session,
                settings,
                assertion=body.assertion,
                agent=body.agent,
                environment=body.environment,
                instance_id=body.instance_id,
                runtime=dict(body.runtime or {}),
                bound_supervisor=body.bound_supervisor,
            )
        if body.grant_type == "refresh":
            return grant_refresh(session, settings, _bearer(request))
        if body.grant_type == "dev":
            if (
                body.agent is None
                or body.environment is None
                or body.instance_id is None
                or body.host is None
            ):
                raise ApiError(422, "invalid_request", "dev grant is incomplete")
            client_host = "" if request.client is None else request.client.host
            return grant_dev(
                session,
                settings,
                agent=body.agent,
                environment=body.environment,
                instance_id=body.instance_id,
                host=body.host,
                runtime=dict(body.runtime or {}),
                client_host=client_host,
                bound_supervisor=body.bound_supervisor,
            )
        if body.registration_token is None or body.instance_id is None or body.host is None:
            raise ApiError(422, "invalid_request", "registration grant is incomplete")
        return grant_registration(
            session,
            settings,
            registration_token=body.registration_token,
            instance_id=body.instance_id,
            host=body.host,
            runtime=dict(body.runtime or {}),
            bound_supervisor=body.bound_supervisor,
        )

    return router


def _bearer(request: Request) -> str:
    """Return the bearer credential.

    Args:
        request: Current HTTP request.

    Returns:
        The credential after `Bearer`.

    Raises:
        ApiError: Status 401 when the header is missing or not a bearer token.
    """
    header = request.headers.get("authorization", "")
    scheme, _, credential = header.partition(" ")
    if scheme.lower() != "bearer" or credential == "":
        raise ApiError(401, "invalid_token", "runtime token is not valid")
    return credential


__all__ = ["token_router"]
