"""Owners, agents, lifecycle, and registration tokens."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol, TypeVar, cast

import jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tessera_server.api.errors import ApiError
from tessera_server.config import Settings
from tessera_server.db.models import (
    Agent,
    Instance,
    IssuedToken,
    Owner,
    Policy,
    RegistrationToken,
    Revocation,
    utcnow,
)
from tessera_server.ids import new_ulid
from tessera_server.services.audit import append_audit
from tessera_server.services.keys import issue_runtime_token, verify_runtime_token
from tessera_server.services.policies import dev_policy_yaml, put_agent_policy
from tessera_server.services.secrets import (
    create_registration_token,
    parse_secret,
    secret_matches,
)

_PAGE_DEFAULT = 50
_DEV_EMAIL = "dev@localhost"
_LOOPBACK = frozenset({"127.0.0.1", "::1"})
_SUPERVISOR_LIMIT = 200
_SUPERVISOR = re.compile(r"^(?:user|group):[^\s]+$")
_ACTIVATE_FROM = frozenset({"draft", "suspended"})
_SUSPEND_FROM = frozenset({"active"})
_RETIRE_FROM = frozenset({"active", "suspended"})
_TRANSITIONS = {"activate": _ACTIVATE_FROM, "suspend": _SUSPEND_FROM, "retire": _RETIRE_FROM}


def create_owner(
    session: Session,
    *,
    owner_type: str,
    email: str,
    display_name: str,
    actor: str,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Create an owner.

    Args:
        session: Open database session.
        owner_type: `user` or `team`.
        email: Unique email address.
        display_name: Name shown in the registry.
        actor: Audit actor.
        now: Clock for `created_at`.

    Returns:
        The owner document.

    Raises:
        ApiError: Status 409 when the email is already registered.
    """
    row = Owner(
        id=new_ulid(),
        owner_type=owner_type,
        email=email,
        display_name=display_name,
        active=True,
        created_at=(now or utcnow)(),
    )
    _add(session, row, "an owner with this email already exists")
    append_audit(
        session,
        actor=actor,
        entry_type="owner_created",
        payload={"id": row.id, "email": row.email, "type": row.owner_type},
        now=now,
    )
    return _owner(row)


def list_owners(
    session: Session,
    *,
    limit: int = _PAGE_DEFAULT,
    cursor: str | None = None,
    email: str | None = None,
) -> dict[str, object]:
    """List owners.

    Args:
        session: Open database session.
        limit: Maximum number of rows to return.
        cursor: Return rows whose id is greater than this value.
        email: Return only the owner with this exact email.

    Returns:
        `items` and `next_cursor`.
    """
    query = select(Owner).order_by(Owner.id)
    if email is not None:
        query = query.where(Owner.email == email)
    if cursor is not None:
        query = query.where(Owner.id > cursor)
    rows = session.scalars(query.limit(limit + 1)).all()
    page, next_cursor = _page(rows, limit)
    return {"items": [_owner(row) for row in page], "next_cursor": next_cursor}


def update_owner(
    session: Session,
    owner_id: str,
    *,
    display_name: str | None,
    active: bool | None,
    actor: str,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Change an owner's name or active flag.

    Args:
        session: Open database session.
        owner_id: Owner to update.
        display_name: New name, or `None` to leave it.
        active: New active flag, or `None` to leave it.
        actor: Audit actor.
        now: Clock for the audit row.

    Returns:
        The updated owner document.

    Raises:
        ApiError: Status 404 when the owner does not exist.
    """
    row = session.get(Owner, owner_id)
    if row is None:
        raise ApiError(404, "not_found", "owner not found")
    if display_name is not None:
        row.display_name = display_name
    if active is not None:
        row.active = active
    append_audit(
        session,
        actor=actor,
        entry_type="owner_updated",
        payload={"id": row.id, "display_name": row.display_name, "active": row.active},
        now=now,
    )
    return _owner(row)


def create_agent(
    session: Session,
    *,
    name: str,
    environment: str,
    owner_id: str,
    description: str,
    risk_tier: str,
    labels: dict[str, str],
    autonomy: str,
    supervisors: list[str],
    actor: str,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Register an agent in `draft`.

    Args:
        session: Open database session.
        name: Slug, unique within the environment.
        environment: Environment slug.
        owner_id: Existing owner.
        description: Free-text description.
        risk_tier: `low`, `medium`, or `high`.
        labels: String labels.
        autonomy: Autonomy level.
        supervisors: Supervisor identifiers.
        actor: Audit actor.
        now: Clock for `created_at`.

    Returns:
        The agent document.

    Raises:
        ApiError: Status 404 when the owner does not exist, or 409 when the
            name is already used in the environment.
    """
    if session.get(Owner, owner_id) is None:
        raise ApiError(404, "not_found", "owner not found")
    row = Agent(
        id=new_ulid(),
        name=name,
        environment=environment,
        description=description,
        owner_id=owner_id,
        status="draft",
        risk_tier=risk_tier,
        labels=dict(labels),
        autonomy=autonomy,
        supervisors=list(supervisors),
        created_at=(now or utcnow)(),
    )
    _add(session, row, "an agent with this name already exists in the environment")
    append_audit(
        session,
        actor=actor,
        entry_type="agent_created",
        payload={"id": row.id, "name": row.name, "environment": row.environment},
        now=now,
    )
    return _agent(session, row, detail=False)


def list_agents(
    session: Session,
    *,
    environment: str | None = None,
    status: str | None = None,
    owner_id: str | None = None,
    orphaned: bool | None = None,
    limit: int = _PAGE_DEFAULT,
    cursor: str | None = None,
    q: str | None = None,
) -> dict[str, object]:
    """List agents.

    Args:
        session: Open database session.
        environment: Filter by environment.
        status: Filter by lifecycle status.
        owner_id: Filter by owner.
        orphaned: Filter by whether the owner is inactive.
        limit: Maximum number of rows to return.
        cursor: Return rows whose id is greater than this value.
        q: Case-sensitive substring of the agent name.

    Returns:
        `items` and `next_cursor`.
    """
    query = select(Agent).order_by(Agent.id)
    if environment is not None:
        query = query.where(Agent.environment == environment)
    if status is not None:
        query = query.where(Agent.status == status)
    if owner_id is not None:
        query = query.where(Agent.owner_id == owner_id)
    if q:
        query = query.where(Agent.name.contains(q, autoescape=True))
    if cursor is not None:
        query = query.where(Agent.id > cursor)
    if orphaned is None:
        matched = list(session.scalars(query.limit(limit + 1)).all())
    else:
        matched = [
            row for row in session.scalars(query).all() if _orphaned(session, row) is orphaned
        ]
    page, next_cursor = _page(matched, limit)
    items = [_agent(session, row, detail=False) for row in page]
    return {"items": items, "next_cursor": next_cursor}


def get_agent(session: Session, agent_id: str) -> dict[str, object]:
    """Return one agent, including instances and the linked policy version.

    Args:
        session: Open database session.
        agent_id: Agent to load.

    Returns:
        The agent document.

    Raises:
        ApiError: Status 404 when the agent does not exist.
    """
    return _agent(session, _require_agent(session, agent_id), detail=True)


def update_agent(
    session: Session,
    agent_id: str,
    *,
    description: str | None,
    owner_id: str | None,
    risk_tier: str | None,
    labels: dict[str, str] | None,
    autonomy: str | None,
    supervisors: list[str] | None,
    actor: str,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Change agent metadata.

    Args:
        session: Open database session.
        agent_id: Agent to update.
        description: New description, or `None` to leave it.
        owner_id: New owner, or `None` to leave it.
        risk_tier: New risk tier, or `None` to leave it.
        labels: New labels, or `None` to leave them.
        autonomy: New autonomy, or `None` to leave it.
        supervisors: New supervisors, or `None` to leave them.
        actor: Audit actor.
        now: Clock for the audit row.

    Returns:
        The updated agent document.

    Raises:
        ApiError: Status 404 when the agent or the new owner does not exist.
    """
    row = _require_agent(session, agent_id)
    if owner_id is not None and session.get(Owner, owner_id) is None:
        raise ApiError(404, "not_found", "owner not found")
    if description is not None:
        row.description = description
    if owner_id is not None:
        row.owner_id = owner_id
    if risk_tier is not None:
        row.risk_tier = risk_tier
    if labels is not None:
        row.labels = dict(labels)
    if autonomy is not None:
        row.autonomy = autonomy
    if supervisors is not None:
        row.supervisors = list(supervisors)
    append_audit(
        session,
        actor=actor,
        entry_type="agent_updated",
        payload={"id": row.id, "owner_id": row.owner_id, "status": row.status},
        now=now,
    )
    return _agent(session, row, detail=True)


def transition_agent(
    session: Session,
    agent_id: str,
    action: str,
    *,
    actor: str,
    reason: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Move an agent along the lifecycle.

    Args:
        session: Open database session.
        agent_id: Agent to change.
        action: `activate`, `suspend`, or `retire`.
        actor: Audit actor.
        reason: Optional operator note stored on the audit row.
        now: Clock for the revocation and audit rows.

    Returns:
        The updated agent document.

    Raises:
        ApiError: Status 404 when the agent does not exist, or 409 when the
            transition is illegal or activation has no linked policy.
    """
    row = _require_agent(session, agent_id)
    allowed = _TRANSITIONS[action]
    if row.status not in allowed:
        raise ApiError(409, "invalid_transition", f"cannot {action} an agent that is {row.status}")
    if action == "activate" and row.policy_id is None:
        raise ApiError(409, "no_policy", "agent has no policy")
    row.status = {"activate": "active", "suspend": "suspended", "retire": "retired"}[action]
    moment = (now or utcnow)()
    if action != "retire":
        session.add(
            Revocation(
                kind="agent_activated" if action == "activate" else "agent_suspended",
                agent_id=row.id,
                subject=row.id,
                created_at=moment,
            )
        )
    payload: dict[str, object] = {"id": row.id, "status": row.status}
    if reason:
        payload["reason"] = reason
    append_audit(
        session,
        actor=actor,
        entry_type={
            "activate": "agent_activated",
            "suspend": "agent_suspended",
            "retire": "agent_retired",
        }[action],
        payload=payload,
        now=now,
    )
    return _agent(session, row, detail=True)


def issue_registration_token(
    session: Session,
    agent_id: str,
    *,
    reusable: bool,
    expires_in: int | None,
    actor: str,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Create a registration token and return it once.

    Args:
        session: Open database session.
        agent_id: Agent the token registers.
        reusable: Whether more than one instance may use it.
        expires_in: Lifetime in seconds. Required when `reusable` is true.
        actor: Audit actor stored as `created_by`.
        now: Clock for the expiry.

    Returns:
        `id` and the full `token`.

    Raises:
        ApiError: Status 404 when the agent does not exist, or 422 when a
            reusable token has no expiry.
    """
    _require_agent(session, agent_id)
    if reusable and expires_in is None:
        raise ApiError(422, "invalid_request", "reusable tokens require expires_in")
    moment = (now or utcnow)()
    expires_at = None if expires_in is None else moment + timedelta(seconds=expires_in)
    try:
        token = create_registration_token(
            session,
            agent_id,
            reusable=reusable,
            expires_at=expires_at,
            created_by=actor,
        )
    except ValueError as err:
        raise ApiError(422, "invalid_request", str(err)) from err
    parsed = parse_secret(token, prefix="tsr")
    token_id = "" if parsed is None else parsed[0]
    append_audit(
        session,
        actor=actor,
        entry_type="registration_token_created",
        payload={"id": token_id, "agent_id": agent_id, "reusable": reusable},
        now=now,
    )
    return {"id": token_id, "token": token}


def revoke_instance(
    session: Session,
    instance_id: str,
    *,
    actor: str,
    reason: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Revoke one instance.

    Args:
        session: Open database session.
        instance_id: Instance to revoke.
        actor: Audit actor.
        reason: Optional operator note stored on the audit row.
        now: Clock for the revocation row.

    Returns:
        The revocation document.

    Raises:
        ApiError: Status 404 when the instance does not exist.
    """
    row = session.get(Instance, instance_id)
    if row is None:
        raise ApiError(404, "not_found", "instance not found")
    row.revoked = True
    moment = (now or utcnow)()
    revocation = Revocation(
        kind="instance_revoked",
        agent_id=row.agent_id,
        subject=row.id,
        created_at=moment,
    )
    session.add(revocation)
    session.flush()
    payload: dict[str, object] = {
        "id": row.id,
        "agent_id": row.agent_id,
        "revocation_id": revocation.id,
    }
    if reason:
        payload["reason"] = reason
    append_audit(
        session,
        actor=actor,
        entry_type="instance_revoked",
        payload=payload,
        now=now,
    )
    return {
        "id": revocation.id,
        "kind": revocation.kind,
        "agent_id": revocation.agent_id,
        "subject": revocation.subject,
    }


def grant_registration(
    session: Session,
    settings: Settings,
    *,
    registration_token: str,
    instance_id: str,
    host: str,
    runtime: dict[str, object],
    bound_supervisor: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Bootstrap an instance with a registration token.

    Args:
        session: Open database session.
        settings: Issuer and token lifetime.
        registration_token: `tsr_<id>_<secret>` token.
        instance_id: 32 hex characters chosen by the instance.
        host: Host name reported by the instance.
        runtime: Runtime description stored on the instance.
        bound_supervisor: Supervisor bound to this instance, `user:` or
            `group:`. `None` leaves the stored value unchanged.
        now: Clock for expiry, heartbeats, and the JWT.

    Returns:
        The token response document.

    Raises:
        ApiError: Status 401 when the token cannot be used, 403 when the agent
            is not active or the instance is revoked, or 422 when
            `bound_supervisor` is not a user or group id.
    """
    moment = (now or utcnow)()
    supervisor = _check_supervisor(bound_supervisor)
    row = _registration(session, registration_token, moment)
    agent = _require_agent(session, row.agent_id)
    _require_active(agent)
    _record_instance(
        session,
        agent,
        instance_id,
        host,
        runtime=runtime,
        moment=moment,
        bound_supervisor=supervisor,
    )
    if not row.reusable:
        row.used_at = moment
    return _access_token(session, settings, agent, instance_id, now)


def grant_dev(
    session: Session,
    settings: Settings,
    *,
    agent: str,
    environment: str,
    instance_id: str,
    host: str,
    runtime: dict[str, object],
    client_host: str,
    bound_supervisor: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Issue a runtime JWT for a development agent, creating it when needed.

    Args:
        session: Open database session.
        settings: Issuer and token lifetime. `dev` must be true.
        agent: Agent slug.
        environment: Must be `dev`.
        instance_id: 32 hex characters chosen by the instance.
        host: Host name reported by the instance.
        runtime: Runtime description stored on the instance.
        client_host: Address of the caller. Only loopback is accepted.
        bound_supervisor: Supervisor bound to this instance, or `None`.
        now: Clock for expiry, heartbeats, and the JWT.

    Returns:
        The token response document.

    Raises:
        ApiError: Status 403 when development mode is off, the caller is not
            on loopback, or the agent cannot take a token. Status 422 when
            the environment is not `dev`.
    """
    if not settings.dev or client_host not in _LOOPBACK:
        raise ApiError(403, "dev_disabled", "dev grant is not available")
    if environment != "dev":
        raise ApiError(422, "invalid_request", "dev grant requires environment dev")
    moment = (now or utcnow)()
    supervisor = _check_supervisor(bound_supervisor)
    row = _ensure_dev_agent(session, agent, now=lambda: moment)
    _require_active(row)
    _record_instance(
        session,
        row,
        instance_id,
        host,
        runtime=runtime,
        moment=moment,
        bound_supervisor=supervisor,
    )
    return _access_token(session, settings, row, instance_id, now)


def grant_refresh(
    session: Session,
    settings: Settings,
    token: str,
    *,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Issue a new runtime JWT for a still-valid one.

    Args:
        session: Open database session.
        settings: Issuer and token lifetime.
        token: Current runtime JWT.
        now: Clock for expiry and the new JWT.

    Returns:
        The token response document.

    Raises:
        ApiError: Status 401 when the token is missing or expired, 403 when
            the agent is not active or the instance is revoked.
    """
    moment = (now or utcnow)()
    try:
        claims = verify_runtime_token(session, settings, token, now=lambda: moment)
    except ValueError as err:
        raise ApiError(401, "invalid_token", "runtime token is not valid") from err
    agent_id = claims.get("agent_id")
    instance_id = claims.get("inst")
    if not isinstance(agent_id, str) or not isinstance(instance_id, str):
        raise ApiError(401, "invalid_token", "runtime token is not valid")
    agent = session.get(Agent, agent_id)
    instance = session.get(Instance, instance_id)
    if agent is None or instance is None or instance.agent_id != agent.id:
        raise ApiError(401, "invalid_token", "runtime token is not valid")
    _require_active(agent)
    if instance.revoked:
        raise ApiError(403, "instance_revoked", "instance is revoked")
    instance.last_heartbeat_at = moment
    agent.last_seen_at = moment
    return _access_token(session, settings, agent, instance.id, now)


def _registration(session: Session, token: str, moment: datetime) -> RegistrationToken:
    """Load a usable registration token.

    Args:
        session: Open database session.
        token: Full `tsr_` token.
        moment: Current time.

    Returns:
        The token row.

    Raises:
        ApiError: Status 401 when the token is unknown, revoked, used, or expired.
    """
    parsed = parse_secret(token, prefix="tsr")
    row = None if parsed is None else session.get(RegistrationToken, parsed[0])
    if parsed is None or row is None or row.revoked:
        raise ApiError(401, "invalid_token", "registration token is not valid")
    if not secret_matches(row.secret_sha256, parsed[1]):
        raise ApiError(401, "invalid_token", "registration token is not valid")
    if row.expires_at is not None and _as_utc(row.expires_at) <= moment:
        raise ApiError(401, "invalid_token", "registration token is not valid")
    if not row.reusable and row.used_at is not None:
        raise ApiError(401, "invalid_token", "registration token is not valid")
    return row


def _ensure_dev_agent(session: Session, name: str, *, now: Callable[[], datetime]) -> Agent:
    """Create the development owner, agent, and policy when they are absent.

    Args:
        session: Open database session.
        name: Agent slug in the `dev` environment.
        now: Clock for the new rows.

    Returns:
        The agent row. A missing policy is installed and a draft agent is
        activated. An agent that is already past `draft` is left as it is.
    """
    owner = session.scalar(select(Owner).where(Owner.email == _DEV_EMAIL))
    if owner is None:
        created = create_owner(
            session,
            owner_type="user",
            email=_DEV_EMAIL,
            display_name="dev",
            actor="dev",
            now=now,
        )
        owner_id = str(created["id"])
    else:
        owner_id = owner.id
    row = session.scalar(select(Agent).where(Agent.name == name, Agent.environment == "dev"))
    if row is None:
        created_agent = create_agent(
            session,
            name=name,
            environment="dev",
            owner_id=owner_id,
            description="",
            risk_tier="low",
            labels={},
            autonomy="interactive",
            supervisors=[],
            actor="dev",
            now=now,
        )
        row = _require_agent(session, str(created_agent["id"]))
    if row.policy_id is None:
        put_agent_policy(session, row.id, dev_policy_yaml(name), actor="dev", now=now)
    if row.status == "draft":
        transition_agent(session, row.id, "activate", actor="dev", now=now)
    return row


def _check_supervisor(value: str | None) -> str | None:
    """Accept a user or group supervisor id.

    Args:
        value: Requested binding, or `None`.

    Returns:
        The same id.

    Raises:
        ApiError: Status 422 when the value is not `user:` or `group:`.
    """
    if value is None:
        return None
    if _SUPERVISOR.fullmatch(value) is None or len(value) > _SUPERVISOR_LIMIT:
        raise ApiError(422, "invalid_request", "supervisor must be user: or group:")
    return value


def _record_instance(
    session: Session,
    agent: Agent,
    instance_id: str,
    host: str,
    *,
    runtime: dict[str, object],
    moment: datetime,
    bound_supervisor: str | None = None,
) -> None:
    """Create or refresh an instance and stamp the agent as seen.

    Args:
        session: Open database session.
        agent: Agent the instance belongs to.
        instance_id: Instance id.
        host: Reported host name.
        runtime: Runtime description.
        moment: Current time.
        bound_supervisor: Supervisor to store. `None` leaves the current
            value in place.

    Raises:
        ApiError: Status 401 when the instance belongs to another agent, or
            403 when it is revoked.
    """
    instance = session.get(Instance, instance_id)
    if instance is not None and instance.agent_id != agent.id:
        raise ApiError(401, "invalid_token", "registration token is not valid")
    if instance is not None and instance.revoked:
        raise ApiError(403, "instance_revoked", "instance is revoked")
    if instance is None:
        session.add(
            Instance(
                id=instance_id,
                agent_id=agent.id,
                host=host,
                runtime=runtime,
                bound_supervisor=bound_supervisor,
                started_at=moment,
                last_heartbeat_at=moment,
                revoked=False,
            )
        )
    else:
        instance.host = host
        instance.runtime = runtime
        instance.last_heartbeat_at = moment
        if bound_supervisor is not None:
            instance.bound_supervisor = bound_supervisor
    agent.last_seen_at = moment


def _require_active(agent: Agent) -> None:
    """Reject an agent that cannot receive a runtime token.

    Args:
        agent: Agent being registered or refreshed.

    Raises:
        ApiError: Status 403 `agent_<status>` when the agent is not active.
    """
    if agent.status != "active":
        raise ApiError(403, f"agent_{agent.status}", f"agent is {agent.status}")


def _access_token(
    session: Session,
    settings: Settings,
    agent: Agent,
    instance_id: str,
    now: Callable[[], datetime] | None,
    *,
    subject: str | None = None,
    subject_issuer: str | None = None,
    act: dict[str, object] | None = None,
) -> dict[str, object]:
    """Sign a runtime JWT and remember its `jti`.

    Args:
        session: Open database session.
        settings: Issuer and token lifetime.
        agent: Agent the token represents.
        instance_id: Instance id placed in the `inst` claim.
        now: Clock forwarded to the signer.
        subject: Principal `sub`. `None` uses the agent URI.
        subject_issuer: `sub_iss` for a principal from another issuer.
        act: Nested actor. `None` means the agent acts alone.

    Returns:
        The token response document.
    """
    policy = None if agent.policy_id is None else session.get(Policy, agent.policy_id)
    token = issue_runtime_token(
        session,
        settings,
        agent_id=agent.id,
        name=agent.name,
        environment=agent.environment,
        instance_id=instance_id,
        policy_version=None if policy is None else policy.sha256,
        now=now,
        subject=subject,
        subject_issuer=subject_issuer,
        act=act,
    )
    claims = cast("dict[str, object]", jwt.decode(token, options={"verify_signature": False}))
    jti = claims.get("jti")
    exp = claims.get("exp")
    if not isinstance(jti, str) or isinstance(exp, bool) or not isinstance(exp, int):
        raise ApiError(401, "invalid_token", "runtime token is not valid")
    session.add(IssuedToken(jti=jti, agent_id=agent.id, instance_id=instance_id, exp=exp))
    session.commit()
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": settings.token_ttl,
        "agent_id": agent.id,
        "agent": agent.name,
        "environment": agent.environment,
    }


def _as_utc(moment: datetime) -> datetime:
    """Treat a naive timestamp as UTC.

    Args:
        moment: Stored or supplied time.

    Returns:
        A timezone-aware UTC datetime.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _add(session: Session, row: Owner | Agent, conflict: str) -> None:
    """Insert a row or raise a conflict error.

    Args:
        session: Open database session.
        row: Pending owner or agent.
        conflict: Message used when a uniqueness constraint fails.

    Raises:
        ApiError: Status 409 when the database rejects the row.
    """
    try:
        session.add(row)
        session.flush()
    except IntegrityError as err:
        session.rollback()
        raise ApiError(409, "conflict", conflict) from err


def _require_agent(session: Session, agent_id: str) -> Agent:
    """Load an agent or raise not-found.

    Args:
        session: Open database session.
        agent_id: Agent to load.

    Returns:
        The agent row.

    Raises:
        ApiError: Status 404 when the agent does not exist.
    """
    row = session.get(Agent, agent_id)
    if row is None:
        raise ApiError(404, "not_found", "agent not found")
    return row


def _owner(row: Owner) -> dict[str, object]:
    """Serialize an owner.

    Args:
        row: Owner row.

    Returns:
        The owner document.
    """
    return {
        "id": row.id,
        "type": row.owner_type,
        "email": row.email,
        "display_name": row.display_name,
        "active": row.active,
        "created_at": _iso(row.created_at),
    }


def _agent(session: Session, row: Agent, *, detail: bool) -> dict[str, object]:
    """Serialize an agent.

    Args:
        session: Open database session, used for the owner and instances.
        row: Agent row.
        detail: Include the policy version and the latest instances.

    Returns:
        The agent document.
    """
    body: dict[str, object] = {
        "id": row.id,
        "name": row.name,
        "environment": row.environment,
        "description": row.description,
        "owner_id": row.owner_id,
        "status": row.status,
        "risk_tier": row.risk_tier,
        "labels": row.labels,
        "autonomy": row.autonomy,
        "supervisors": row.supervisors,
        "created_at": _iso(row.created_at),
        "last_seen_at": _iso(row.last_seen_at),
        "orphaned": _orphaned(session, row),
    }
    if detail:
        policy = None if row.policy_id is None else session.get(Policy, row.policy_id)
        instances = session.scalars(
            select(Instance)
            .where(Instance.agent_id == row.id)
            .order_by(Instance.last_heartbeat_at.desc())
            .limit(20)
        ).all()
        body["policy_version"] = None if policy is None else policy.sha256
        body["instances"] = [_instance(item) for item in instances]
    return body


def _instance(row: Instance) -> dict[str, object]:
    """Serialize an instance.

    Args:
        row: Instance row.

    Returns:
        The instance document.
    """
    return {
        "id": row.id,
        "host": row.host,
        "runtime": row.runtime,
        "bound_supervisor": row.bound_supervisor,
        "started_at": _iso(row.started_at),
        "last_heartbeat_at": _iso(row.last_heartbeat_at),
        "revoked": row.revoked,
    }


def _orphaned(session: Session, row: Agent) -> bool:
    """Report whether the agent's owner is inactive.

    Args:
        session: Open database session.
        row: Agent row.

    Returns:
        `True` when the owner is missing or inactive.
    """
    owner = session.get(Owner, row.owner_id)
    return owner is None or not owner.active


class _HasId(Protocol):
    """A row whose cursor is its string id."""

    id: str


_RowT = TypeVar("_RowT", bound=_HasId)


def _page(rows: Sequence[_RowT], limit: int) -> tuple[list[_RowT], str | None]:
    """Split a limit-plus-one result into a page and a cursor.

    Args:
        rows: Rows ordered by id, possibly one past the limit.
        limit: Page size.

    Returns:
        The page and the last included id when another page exists.
    """
    if len(rows) <= limit:
        return list(rows), None
    page = list(rows[:limit])
    return page, page[-1].id


def _iso(moment: datetime | None) -> str | None:
    """Format a timestamp as UTC.

    Args:
        moment: Stored time. Naive values are treated as UTC.

    Returns:
        An ISO-8601 string ending in `Z`, or `None`.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")
