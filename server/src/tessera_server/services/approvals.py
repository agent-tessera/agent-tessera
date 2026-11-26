"""Create, resolve, and escalate approval requests."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_tessera.errors import PolicyError
from agent_tessera.policy.loader import parse_policy
from agent_tessera.policy.models import AgentPolicy, parse_duration
from agent_tessera.supervision import resolve_supervisor
from tessera_server.api.errors import ApiError
from tessera_server.config import Settings
from tessera_server.db.models import (
    AdminKey,
    Agent,
    Approval,
    Instance,
    Owner,
    Policy,
    Run,
    RunParticipant,
    utcnow,
)
from tessera_server.ids import new_ulid
from tessera_server.services.audit import append_audit, exclusive_audit, record_audit
from tessera_server.services.keys import verify_runtime_token
from tessera_server.services.secrets import parse_secret, secret_matches
from tessera_server.services.webhooks import deliver

log = logging.getLogger(__name__)

_STATUSES = frozenset({"pending", "approved", "denied", "timeout", "cancelled"})
_PAGE = 50
_DEFAULT_TIMEOUT = 900


@dataclass(frozen=True, slots=True)
class Approver:
    """Who is resolving an approval.

    Attributes:
        kind: `admin` for an admin key, or `agent` for a runtime token.
        identity: Admin key name, or `agent:<name>`.
        agent_id: Approving agent. `None` for an admin key.
        actor: Audit actor, such as `admin:<id>` or the token subject.

    Example:
        ```python
        person = Approver("admin", "lead@example.com", None, "admin:01H")
        assert person.kind == "admin"
        ```
    """

    kind: Literal["admin", "agent"]
    identity: str
    agent_id: str | None
    actor: str


def create_approval(
    session: Session,
    settings: Settings,
    token: str,
    *,
    action: Mapping[str, object],
    decision: Mapping[str, object],
    run_id: str | None,
    principal: str | None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Store an approval and offer it to the first resolved party.

    Args:
        session: Open database session.
        settings: Issuer used to verify the runtime token.
        token: Runtime JWT for the requesting instance.
        action: Action the SDK wants approved.
        decision: Decision that required approval.
        run_id: Run id, or `None`.
        principal: Principal who triggered the action, or `None`.
        now: Clock for `created_at` and `expires_at`.

    Returns:
        `id`, `status`, and `expires_at`.

    Raises:
        ApiError: Status 401 when the token is invalid, 403 when the instance
            is revoked, or 422 when the policy has nobody to ask.

    Example:
        ```python
        created = create_approval(
            session,
            settings,
            token,
            action={"kind": "tool", "resource": "files.delete"},
            decision={"effect": "require_approval"},
            run_id=None,
            principal="ada",
        )
        assert created["status"] == "pending"
        ```
    """
    moment = (now or utcnow)()
    claims = _claims(session, settings, token, moment)
    agent = session.get(Agent, claims["agent_id"])
    instance = session.get(Instance, claims["instance_id"])
    if agent is None or instance is None or instance.agent_id != agent.id:
        raise ApiError(401, "invalid_token", "runtime token is not valid")
    if instance.revoked:
        raise ApiError(403, "instance_revoked", "instance is revoked")
    parties, timeout, forbid = _route(
        session,
        agent,
        instance,
        principal,
        _run_supervisor(session, run_id),
    )
    if not parties:
        raise ApiError(422, "approval_unavailable", "approval has nobody to route to")
    row = Approval(
        id=new_ulid(),
        agent_id=agent.id,
        instance_id=instance.id,
        run_id=run_id or None,
        action=dict(action),
        decision=dict(decision),
        route={
            "parties": parties,
            "forbid_self_approval": forbid,
            "timeout_seconds": timeout,
        },
        route_index=0,
        status="pending",
        requested_by_principal=principal or None,
        resolved_by=None,
        expires_at=moment + timedelta(seconds=timeout),
        created_at=moment,
        resolved_at=None,
    )
    session.add(row)
    append_audit(
        session,
        actor=claims["sub"],
        entry_type="approval_created",
        payload={"id": row.id, "agent_id": agent.id},
        now=lambda: moment,
    )
    publish_approval_event(session, settings, "created", row.id)
    return {"id": row.id, "status": row.status, "expires_at": _iso(row.expires_at)}


def read_approver(
    session: Session,
    settings: Settings,
    token: str,
    *,
    now: Callable[[], datetime] | None = None,
) -> Approver:
    """Identify an admin key or a runtime token.

    Args:
        session: Open database session.
        settings: Issuer used for a runtime token.
        token: Admin key or runtime JWT.
        now: Clock for token verification.

    Returns:
        The caller. An admin key uses its name as the identity.

    Raises:
        ApiError: Status 401 when the credential is not a live admin key or
            a valid runtime token.
    """
    parsed = parse_secret(token, prefix="tsa")
    if parsed is not None:
        key_id, secret = parsed
        row = session.get(AdminKey, key_id)
        if row is None or row.revoked or not secret_matches(row.secret_sha256, secret):
            raise ApiError(401, "invalid_token", "admin key is required")
        return Approver("admin", row.name, None, f"admin:{row.id}")
    claims = _claims(session, settings, token, (now or utcnow)())
    agent = session.get(Agent, claims["agent_id"])
    if agent is None:
        raise ApiError(401, "invalid_token", "runtime token is not valid")
    return Approver("agent", f"agent:{agent.name}", agent.id, claims["sub"])


def get_approval(
    session: Session,
    settings: Settings,
    token: str | None,
    approval_id: str,
    *,
    admin: bool,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Return one approval to its agent or to an admin.

    Args:
        session: Open database session.
        settings: Issuer used when the caller is an agent.
        token: Runtime JWT. Ignored when `admin` is true.
        approval_id: Approval to read.
        admin: Whether the caller presented an admin key.
        now: Clock for token verification.

    Returns:
        The approval document.

    Raises:
        ApiError: Status 401 when an agent token is invalid, or 404 when the
            approval does not exist or belongs to another agent.
    """
    row = session.get(Approval, approval_id)
    if row is None:
        raise ApiError(404, "not_found", "approval not found")
    if admin:
        return _document(row)
    if token is None:
        raise ApiError(401, "invalid_token", "runtime token is not valid")
    claims = _claims(session, settings, token, (now or utcnow)())
    if claims["agent_id"] != row.agent_id:
        raise ApiError(404, "not_found", "approval not found")
    return _document(row)


def list_approvals(
    session: Session,
    *,
    status: str | None = None,
    agent_id: str | None = None,
    limit: int = _PAGE,
    cursor: str | None = None,
) -> dict[str, object]:
    """List approvals for an admin.

    Args:
        session: Open database session.
        status: Keep only this status.
        agent_id: Keep only this agent.
        limit: Maximum number of rows to return.
        cursor: Id of the last approval already seen (continue with older ones).

    Returns:
        `items` (newest first) and `next_cursor`.

    Raises:
        ApiError: Status 422 when `status` is not a known approval status.
    """
    if status is not None and status not in _STATUSES:
        raise ApiError(422, "invalid_request", "status is not a known approval status")
    query = select(Approval).order_by(Approval.created_at.desc(), Approval.id.desc())
    if status is not None:
        query = query.where(Approval.status == status)
    if agent_id is not None:
        query = query.where(Approval.agent_id == agent_id)
    if cursor is not None:
        marker = session.get(Approval, cursor)
        if marker is None:
            query = query.where(Approval.id < cursor)
        else:
            query = query.where(
                (Approval.created_at < marker.created_at)
                | ((Approval.created_at == marker.created_at) & (Approval.id < marker.id))
            )
    rows = session.scalars(query.limit(limit + 1)).all()
    page = list(rows[:limit])
    next_cursor = page[-1].id if len(rows) > limit else None
    return {"items": [_document(row) for row in page], "next_cursor": next_cursor}


def resolve_approval(
    session: Session,
    approval_id: str,
    approver: Approver,
    settings: Settings,
    *,
    allow: bool,
    comment: str | None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Approve or deny a pending approval.

    Args:
        session: Open database session.
        approval_id: Approval to resolve.
        approver: Admin key, or the agent currently offered the request.
        settings: Used to sign the resolution webhook.
        allow: `True` to approve and `False` to deny.
        comment: Optional note stored on the audit row.
        now: Clock for `resolved_at`.

    Returns:
        The updated approval document.

    Raises:
        ApiError: Status 403 when the caller may not resolve it, 404 when it
            does not exist, or 409 when it is no longer pending.

    Example:
        ```python
        person = Approver("admin", "lead@example.com", None, "admin:01H")
        resolved = resolve_approval(
            session, approval_id, person, settings, allow=True, comment=None
        )
        assert resolved["status"] == "approved"
        ```
    """
    moment = (now or utcnow)()
    status = "approved" if allow else "denied"
    with exclusive_audit(session):
        row = session.get(Approval, approval_id)
        if row is None:
            raise ApiError(404, "not_found", "approval not found")
        if row.status != "pending":
            raise ApiError(409, "conflict", f"approval is {row.status}")
        _check_approver(session, row, approver)
        row.status = status
        row.resolved_by = approver.identity
        row.resolved_at = moment
        payload: dict[str, object] = {"id": row.id, "status": status}
        if comment is not None:
            payload["comment"] = comment
        record_audit(
            session,
            actor=approver.actor,
            entry_type="approval_resolved",
            payload=payload,
            now=lambda: moment,
        )
        document = _document(row)
    publish_approval_event(session, settings, status, approval_id)
    return document


def advance_approvals(
    session: Session,
    settings: Settings,
    *,
    now: Callable[[], datetime] | None = None,
) -> int:
    """Move expired approvals to the next party, or time them out.

    Args:
        session: Open database session.
        settings: Used to sign escalation and timeout webhooks.
        now: Clock compared with `expires_at`.

    Returns:
        How many pending approvals were escalated or timed out.

    Example:
        ```python
        changed = advance_approvals(session, settings)
        assert changed >= 0
        ```
    """
    moment = (now or utcnow)()
    rows: list[Approval] = []
    with exclusive_audit(session):
        rows = list(
            session.scalars(
                select(Approval).where(Approval.status == "pending", Approval.expires_at <= moment)
            ).all()
        )
        for row in rows:
            _advance(session, row, moment)
    for row in rows:
        kind = "timeout" if row.status == "timeout" else "escalated"
        publish_approval_event(session, settings, kind, row.id)
    return len(rows)


def publish_approval_event(
    session: Session, settings: Settings, kind: str, approval_id: str
) -> None:
    """Deliver the webhook for an approval change.

    Args:
        session: Open database session. The approval is already stored.
        settings: Signing secret for endpoints that do not set their own.
        kind: `created`, `escalated`, `approved`, `denied`, or `timeout`.
        approval_id: Approval that changed.

    `created` and `escalated` send `approval.requested`. The others send
    `approval.resolved`. A delivery failure is audited and does not raise.
    """
    event = "approval.requested" if kind in {"created", "escalated"} else "approval.resolved"
    row = session.get(Approval, approval_id)
    payload: dict[str, object] = {"event": event, "approval_id": approval_id}
    if row is not None:
        payload["status"] = row.status
        payload["agent_id"] = row.agent_id
    try:
        deliver(session, settings, event, payload)
    except Exception:
        log.exception("webhook delivery failed")


def _advance(session: Session, row: Approval, moment: datetime) -> None:
    """Offer the next party, or mark the approval timed out.

    Args:
        session: Open session inside `exclusive_audit`.
        row: Pending approval whose `expires_at` has passed.
        moment: Current time.
    """
    route = _stored(row)
    parties = _strings(route.get("parties"))
    timeout = _seconds(route.get("timeout_seconds"))
    nxt = row.route_index + 1
    if nxt < len(parties):
        row.route_index = nxt
        row.expires_at = moment + timedelta(seconds=timeout)
        entry_type = "approval_escalated"
        payload: dict[str, object] = {"id": row.id, "route_index": nxt}
    else:
        row.status = "timeout"
        row.resolved_at = moment
        entry_type = "approval_timeout"
        payload = {"id": row.id}
    record_audit(
        session,
        actor="approvals",
        entry_type=entry_type,
        payload=payload,
        now=lambda: moment,
    )


def _check_approver(session: Session, row: Approval, approver: Approver) -> None:
    """Reject a caller who may not resolve this approval.

    Args:
        session: Open database session.
        row: Pending approval.
        approver: Caller.

    Raises:
        ApiError: Status 403 when the caller is the requesting principal, is
            not the party currently offered the request, is the requesting
            agent, or takes part in the run.
    """
    route = _stored(row)
    if route.get("forbid_self_approval") is not False and _same_person(
        row.requested_by_principal, approver.identity
    ):
        raise ApiError(403, "self_approval", "the requesting principal cannot approve this action")
    if approver.kind == "admin":
        return
    parties = _strings(route.get("parties"))
    current = parties[row.route_index] if row.route_index < len(parties) else ""
    agent_id = approver.agent_id
    if current != approver.identity or agent_id is None or agent_id == row.agent_id:
        raise ApiError(403, "not_approver", "caller is not the current approver")
    if _participant(session, row.run_id, agent_id):
        raise ApiError(403, "not_approver", "caller is not the current approver")


def _route(
    session: Session,
    agent: Agent,
    instance: Instance,
    principal: str | None,
    run_supervisor: str | None,
) -> tuple[list[str], int, bool]:
    """Resolve the policy route to parties that can be offered the request.

    Args:
        session: Open database session.
        agent: Requesting agent.
        instance: Requesting instance.
        principal: Principal from the request, or `None`.
        run_supervisor: Supervisor stored on the run, or `None`.

    Returns:
        Resolved parties, the per-step timeout in seconds, and whether the
        requesting principal is forbidden from approving.

    Raises:
        ApiError: Status 422 when the stored policy cannot be read.
    """
    policy = _policy(session, agent)
    spec = policy.spec.approvals
    parties: list[str] = []
    for entry in spec.route_to:
        party = _party(session, agent, instance, principal, entry, run_supervisor=run_supervisor)
        if party is None or party == f"agent:{agent.name}":
            continue
        parties.append(party)
    return parties, parse_duration(spec.timeout), spec.forbid_self_approval


def _party(
    session: Session,
    agent: Agent,
    instance: Instance,
    principal: str | None,
    entry: str,
    *,
    run_supervisor: str | None,
) -> str | None:
    """Resolve one route entry.

    Args:
        session: Open database session.
        agent: Requesting agent.
        instance: Requesting instance.
        principal: Principal from the request, or `None`.
        entry: `principal`, `supervisor`, `owner`, or a `user:`, `group:`,
            or `agent:` name.
        run_supervisor: Supervisor stored on the run, or `None`.

    Returns:
        The party id, or `None` when that entry cannot be offered.
    """
    if entry == "principal":
        return _named(principal)
    if entry == "supervisor":
        return _supervisor(agent, instance, run_supervisor)
    if entry == "owner":
        owner = session.get(Owner, agent.owner_id)
        return None if owner is None else _named(owner.email)
    if agent.risk_tier == "high" and entry.startswith("agent:"):
        return None
    if entry.startswith(("user:", "group:", "agent:")):
        return entry
    return None


def _supervisor(agent: Agent, instance: Instance, run_supervisor: str | None) -> str | None:
    """Resolve the supervisor for an action.

    A run binding wins, then the instance binding, then the first static
    supervisor.

    Args:
        agent: Agent whose static supervisors are the fallback.
        instance: Instance that may carry a bound supervisor.
        run_supervisor: Supervisor stored on the run, or `None`.

    Returns:
        The chosen supervisor, or `None` when none is set.
    """
    names = [item for item in agent.supervisors if isinstance(item, str)]
    chosen = resolve_supervisor(run_supervisor, instance.bound_supervisor, names)
    return _named(chosen)


def _run_supervisor(session: Session, run_id: str | None) -> str | None:
    """Read the supervisor stored on a run.

    Args:
        session: Open database session.
        run_id: Run id, or `None` when the action has no task.

    Returns:
        The stored supervisor, or `None`.
    """
    if not run_id:
        return None
    run = session.get(Run, run_id)
    return run.supervisor if run is not None else None


def _named(value: str | None) -> str | None:
    """Prefix a bare name as `user:`.

    Args:
        value: Party text.

    Returns:
        The party id, or `None` when the text is empty.
    """
    if value is None or value == "":
        return None
    if ":" in value:
        return value
    return f"user:{value}"


def _policy(session: Session, agent: Agent) -> AgentPolicy:
    """Load the agent's stored policy.

    Args:
        session: Open database session.
        agent: Agent whose policy to read.

    Returns:
        The agent policy.

    Raises:
        ApiError: Status 422 when the agent has no usable policy.
    """
    if agent.policy_id is None:
        raise ApiError(422, "invalid_policy", "agent has no policy")
    stored = session.get(Policy, agent.policy_id)
    if stored is None:
        raise ApiError(422, "invalid_policy", "agent has no policy")
    try:
        document = parse_policy(_one_document(stored.yaml))
    except PolicyError as err:
        raise ApiError(422, "invalid_policy", str(err)) from err
    if not isinstance(document, AgentPolicy):
        raise ApiError(422, "invalid_policy", "expected an AgentPolicy")
    return document


def _participant(session: Session, run_id: str | None, agent_id: str) -> bool:
    """Return whether an agent takes part in a run.

    Args:
        session: Open database session.
        run_id: Run to search. `None` matches nothing.
        agent_id: Agent to look for.

    Returns:
        `True` when that agent has a participant row on the run.
    """
    if run_id is None:
        return False
    found = session.scalars(
        select(RunParticipant).where(
            RunParticipant.run_id == run_id,
            RunParticipant.agent_id == agent_id,
        )
    ).first()
    return found is not None


def _claims(session: Session, settings: Settings, token: str, moment: datetime) -> dict[str, str]:
    """Verify a runtime token and read its identity claims.

    Args:
        session: Open database session.
        settings: Expected issuer.
        token: Compact JWT.
        moment: Current time.

    Returns:
        `agent_id`, `instance_id`, and `sub`.

    Raises:
        ApiError: Status 401 when the token or its claims are not usable.
    """
    try:
        claims = verify_runtime_token(session, settings, token, now=lambda: moment)
    except ValueError as err:
        raise ApiError(401, "invalid_token", "runtime token is not valid") from err
    agent_id = claims.get("agent_id")
    instance_id = claims.get("inst")
    subject = claims.get("sub")
    if (
        not isinstance(agent_id, str)
        or not isinstance(instance_id, str)
        or not isinstance(subject, str)
    ):
        raise ApiError(401, "invalid_token", "runtime token is not valid")
    return {"agent_id": agent_id, "instance_id": instance_id, "sub": subject}


def _same_person(principal: str | None, identity: str) -> bool:
    """Return whether an identity is the requesting principal.

    Args:
        principal: Stored principal. A `user:` prefix is ignored.
        identity: Approver identity. A `user:` prefix is ignored.

    Returns:
        `True` when both name the same person.
    """
    if principal is None or principal == "":
        return False
    return principal.removeprefix("user:") == identity.removeprefix("user:")


def _stored(row: Approval) -> Mapping[str, object]:
    """Return the route document.

    Args:
        row: Approval row.

    Returns:
        The route mapping stored on the approval.
    """
    return row.route


def _strings(value: object) -> list[str]:
    """Return the string items of a JSON list.

    Args:
        value: Stored list.

    Returns:
        The string items. Empty when the value is not a list.
    """
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _seconds(value: object) -> int:
    """Return a positive timeout stored on the route.

    Args:
        value: Stored timeout.

    Returns:
        The timeout in seconds, or 900 when it is missing.
    """
    if isinstance(value, int) and value > 0:
        return value
    return _DEFAULT_TIMEOUT


def _document(row: Approval) -> dict[str, object]:
    """Return the API document for an approval.

    Args:
        row: Stored approval.

    Returns:
        The approval fields, with timestamps in UTC ISO-8601.
    """
    return {
        "id": row.id,
        "agent_id": row.agent_id,
        "instance_id": row.instance_id,
        "run_id": row.run_id,
        "action": dict(row.action),
        "decision": dict(row.decision),
        "route": dict(row.route),
        "route_index": row.route_index,
        "status": row.status,
        "requested_by_principal": row.requested_by_principal,
        "resolved_by": row.resolved_by,
        "expires_at": _iso(row.expires_at),
        "created_at": _iso(row.created_at),
        "resolved_at": None if row.resolved_at is None else _iso(row.resolved_at),
    }


def _one_document(text: str) -> object:
    """Load the only YAML document in a stored policy.

    Args:
        text: YAML text.

    Returns:
        The document.

    Raises:
        PolicyError: If the YAML is invalid or is not exactly one document.
    """
    try:
        documents = list(yaml.safe_load_all(text))
    except yaml.YAMLError as err:
        raise PolicyError(f"invalid YAML: {err}") from err
    if len(documents) != 1:
        found = len(documents)
        raise PolicyError(f"expected exactly one YAML document, found {found}")
    return documents[0]


def _iso(moment: datetime) -> str:
    """Format a timestamp as UTC ISO-8601 with millisecond precision.

    Args:
        moment: Time to format.

    Returns:
        A string ending in `Z`.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = [
    "Approver",
    "advance_approvals",
    "create_approval",
    "get_approval",
    "list_approvals",
    "publish_approval_event",
    "read_approver",
    "resolve_approval",
]
