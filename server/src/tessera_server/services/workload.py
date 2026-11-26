"""Bootstrap a runtime JWT from a workload-identity assertion."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import jwt
from jwt.exceptions import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.orm import Session

from tessera_server.api.errors import ApiError
from tessera_server.config import Settings
from tessera_server.db.models import Agent, utcnow
from tessera_server.services.oidc import OidcLoginError, open_client, verify_provider_token
from tessera_server.services.registry import (
    _access_token,
    _check_supervisor,
    _record_instance,
    _require_active,
)

_MESSAGE = "workload assertion is not valid"


def grant_workload(
    session: Session,
    settings: Settings,
    *,
    assertion: str,
    agent: str,
    environment: str,
    instance_id: str,
    runtime: dict[str, object],
    bound_supervisor: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Issue the same runtime JWT registration would issue.

    The first binding with this issuer, agent, and environment is the match.
    Its audience and claim must accept the assertion.

    Args:
        session: Open database session.
        settings: Workload bindings, issuer, and token lifetime.
        assertion: OIDC JWT from the workload.
        agent: Agent slug the caller asked to start.
        environment: Environment the caller asked to start.
        instance_id: 32 hex characters chosen by the instance.
        runtime: Runtime description stored on the instance.
        bound_supervisor: Supervisor bound to this instance, or `None`.
        now: Clock for the heartbeat and the runtime JWT.

    Returns:
        The token response document.

    Raises:
        ApiError: Status 401 when no binding accepts the assertion, or 403
            when the agent is not active or the instance is revoked.

    Example:
        ```python
        document = grant_workload(
            session,
            settings,
            assertion=token,
            agent="billing",
            environment="prod",
            instance_id="ab" * 16,
            runtime={},
        )
        assert "access_token" in document
        ```
    """
    moment = (now or utcnow)()
    supervisor = _check_supervisor(bound_supervisor)
    _accept(settings, assertion, agent, environment)
    row = session.scalar(select(Agent).where(Agent.name == agent, Agent.environment == environment))
    if row is None:
        raise ApiError(401, "invalid_token", _MESSAGE)
    _require_active(row)
    _record_instance(
        session,
        row,
        instance_id,
        "",
        runtime=runtime,
        moment=moment,
        bound_supervisor=supervisor,
    )
    return _access_token(session, settings, row, instance_id, now)


def _accept(settings: Settings, assertion: str, agent: str, environment: str) -> None:
    """Verify the assertion against the first matching binding.

    Args:
        settings: Configured bindings.
        assertion: Compact JWT.
        agent: Requested agent slug.
        environment: Requested environment.

    Raises:
        ApiError: Status 401 when the issuer, audience, or claim does not match.
    """
    issuer = _issuer(assertion)
    binding = next(
        (
            item
            for item in settings.workload_bindings
            if item.issuer == issuer and item.agent == agent and item.environment == environment
        ),
        None,
    )
    if binding is None:
        raise ApiError(401, "invalid_token", _MESSAGE)
    try:
        with open_client() as client:
            claims = verify_provider_token(binding.issuer, binding.audience, assertion, client)
    except OidcLoginError as err:
        raise ApiError(401, "invalid_token", _MESSAGE) from err
    value = claims.get(binding.claim)
    if not isinstance(value, str) or not value:
        raise ApiError(401, "invalid_token", _MESSAGE)


def _issuer(assertion: str) -> str:
    """Read the unverified issuer. The signature check confirms it.

    Args:
        assertion: Compact JWT.

    Returns:
        The issuer with no trailing slash.

    Raises:
        ApiError: Status 401 when the token has no issuer.
    """
    try:
        claims = jwt.decode(assertion, options={"verify_signature": False})
    except InvalidTokenError as err:
        raise ApiError(401, "invalid_token", _MESSAGE) from err
    issuer = claims.get("iss") if isinstance(claims, dict) else None
    if not isinstance(issuer, str) or not issuer:
        raise ApiError(401, "invalid_token", _MESSAGE)
    return issuer.rstrip("/")


__all__ = ["grant_workload"]
