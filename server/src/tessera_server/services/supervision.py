"""Resolve supervisors and store their grant documents."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import yaml
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_tessera.bundle import SupervisorAllowSet
from agent_tessera.errors import PolicyError
from agent_tessera.policy.effective import build_effective_policy
from agent_tessera.policy.loader import parse_policy
from agent_tessera.policy.models import AgentPolicy, BaselinePolicy, SupervisorGrants
from agent_tessera.supervision import resolve_supervisor
from tessera_server.api.errors import ApiError
from tessera_server.config import Settings
from tessera_server.db.models import Agent, Baseline, Instance, Policy, Run, SupervisorGrant, utcnow
from tessera_server.services.webhooks import deliver

_SUPERVISOR = re.compile(r"^(?:user|group):\S+$")
_SUPERVISOR_LIMIT = 200


def resolved_supervisor(
    session: Session,
    *,
    agent_id: str,
    instance_id: str | None,
    run_id: str | None,
) -> str | None:
    """Resolve a run's supervisor from stored rows.

    A run binding wins, then the instance binding, then the first static
    supervisor on the agent.

    Args:
        session: Open database session.
        agent_id: Agent whose static supervisors are the fallback.
        instance_id: Instance that may carry `bound_supervisor`, or `None`.
        run_id: Run that may carry a supervisor, or `None`.

    Returns:
        The chosen supervisor, or `None` when every source is empty.

    Example:
        ```python
        chosen = resolved_supervisor(
            session,
            agent_id=agent.id,
            instance_id=instance.id,
            run_id=run.id,
        )
        ```
    """
    agent = session.get(Agent, agent_id)
    instance = session.get(Instance, instance_id) if instance_id else None
    run = session.get(Run, run_id) if run_id else None
    static = agent.supervisors if agent is not None and isinstance(agent.supervisors, list) else []
    names = [item for item in static if isinstance(item, str)]
    bound = instance.bound_supervisor if instance is not None else None
    stored = run.supervisor if run is not None else None
    return resolve_supervisor(stored, bound, names)


def put_supervisor_grants(session: Session, supervisor_id: str, text: str) -> None:
    """Replace the YAML allow lists for one supervisor.

    Args:
        session: Open database session. The row is committed.
        supervisor_id: `user:` or `group:` id from the request path.
        text: YAML document. Its metadata supervisor must match the path.

    Raises:
        ApiError: Status 422 when the id or document is not usable.
    """
    _require_id(supervisor_id)
    document = _parse_grants(text)
    if document.metadata.supervisor != supervisor_id:
        raise ApiError(422, "invalid_request", "grant supervisor does not match the path")
    row = session.get(SupervisorGrant, supervisor_id)
    moment = utcnow()
    if row is None:
        session.add(SupervisorGrant(supervisor=supervisor_id, yaml=text, updated_at=moment))
    else:
        row.yaml = text
        row.updated_at = moment
    session.commit()


def get_supervisor_grants(session: Session, supervisor_id: str) -> str:
    """Return the stored grant YAML for one supervisor.

    Args:
        session: Open database session.
        supervisor_id: `user:` or `group:` id.

    Returns:
        The stored YAML.

    Raises:
        ApiError: Status 422 when the id is not a supervisor, or 404 when
            nothing is stored.
    """
    _require_id(supervisor_id)
    row = session.get(SupervisorGrant, supervisor_id)
    if row is None:
        raise ApiError(404, "not_found", "supervisor grants are not stored")
    return row.yaml


def supervisor_allow_sets(session: Session, environment: str) -> dict[str, SupervisorAllowSet]:
    """Collect grant globs when an environment caps calls to a supervisor.

    The set is empty unless some agent in that environment sets
    `supervision.cap_to_supervisor`. Offline mode has no bundle, so this
    embedding does not apply there. The flag is read from the agent policy,
    not from a baseline.

    Args:
        session: Open database session.
        environment: Environment of the agent whose bundle is being signed.

    Returns:
        Allow sets keyed by supervisor id. Empty when nobody caps.

    Raises:
        ApiError: Status 422 when a stored policy or grant document is no
            longer valid.
    """
    if not _environment_caps(session, environment):
        return {}
    rows = session.scalars(select(SupervisorGrant).order_by(SupervisorGrant.supervisor)).all()
    grants: dict[str, SupervisorAllowSet] = {}
    for row in rows:
        try:
            document = _parse_grants(row.yaml)
        except ApiError as err:
            raise ApiError(422, "invalid_policy", err.message) from err
        grants[row.supervisor] = SupervisorAllowSet(
            model=document.spec.models.allow,
            tool=document.spec.tools.allow,
        )
    return grants


def _environment_caps(session: Session, environment: str) -> bool:
    """Return whether any agent in an environment caps to its supervisor.

    Args:
        session: Open database session.
        environment: Environment to scan.

    Returns:
        `True` when a stored policy sets `cap_to_supervisor`.

    Raises:
        ApiError: Status 422 when a stored policy is no longer valid.
    """
    rows = session.scalars(select(Agent).where(Agent.environment == environment)).all()
    for row in rows:
        if row.policy_id is None:
            continue
        stored = session.get(Policy, row.policy_id)
        if stored is None:
            continue
        try:
            document = _agent_policy(stored.yaml)
        except PolicyError as err:
            raise ApiError(422, "invalid_policy", str(err)) from err
        if document.spec.supervision.cap_to_supervisor:
            return True
    return False


def _agent_policy(text: str) -> AgentPolicy:
    """Parse stored policy YAML.

    Args:
        text: Stored document.

    Returns:
        The agent policy.

    Raises:
        PolicyError: If the text is not an agent policy.
    """
    loaded = _one_document(text)
    document = parse_policy(loaded)
    if not isinstance(document, AgentPolicy):
        raise PolicyError("expected an AgentPolicy")
    return document


def _parse_grants(text: str) -> SupervisorGrants:
    """Parse a supervisor grant document.

    Args:
        text: YAML body.

    Returns:
        The grant document.

    Raises:
        ApiError: Status 422 when the YAML is not a grant document.
    """
    try:
        loaded = _one_document(text)
        return SupervisorGrants.model_validate(loaded)
    except (PolicyError, ValidationError) as err:
        raise ApiError(422, "invalid_request", "supervisor grants are not valid") from err


def _one_document(text: str) -> object:
    """Load the only YAML document in a body.

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


def acknowledge_supervisor(
    session: Session,
    agent_id: str,
    *,
    now: Callable[[], datetime] | None = None,
) -> dict[str, str]:
    """Record that a supervisor acknowledged an agent.

    Args:
        session: Open database session. The row is committed.
        agent_id: Agent being acknowledged.
        now: Clock for the acknowledgement.

    Returns:
        The agent id and the acknowledgement time.

    Raises:
        ApiError: Status 404 when the agent does not exist.
    """
    row = session.get(Agent, agent_id)
    if row is None:
        raise ApiError(404, "not_found", "agent was not found")
    moment = (now or utcnow)()
    row.supervisor_ack_at = moment
    session.commit()
    return {"id": row.id, "supervisor_ack_at": moment.isoformat()}


def advance_dead_men(
    session: Session,
    settings: Settings,
    *,
    now: Callable[[], datetime] | None = None,
) -> int:
    """Suspend active agents whose supervisor acknowledgement is overdue.

    Args:
        session: Open database session.
        settings: Used when a digest webhook is configured.
        now: Clock for the comparison and the revocation.

    Returns:
        How many agents were suspended.
    """
    moment = (now or utcnow)()
    rows = session.scalars(select(Agent).where(Agent.status == "active")).all()
    # Registry imports this module, so the lifecycle helper stays local.
    from tessera_server.services.registry import transition_agent  # noqa: PLC0415

    suspended = 0
    for row in rows:
        limit = _ack_limit(session, row)
        if limit is None:
            continue
        started = _aware(row.supervisor_ack_at or row.created_at)
        if moment - started < timedelta(seconds=limit):
            continue
        transition_agent(session, row.id, "suspend", actor="dead_man", now=lambda: moment)
        deliver(
            session,
            settings,
            "supervisor.digest",
            {"event": "supervisor.digest", "agent_id": row.id, "reason": "dead_man"},
        )
        suspended += 1
    return suspended


def _aware(moment: datetime) -> datetime:
    """Return a UTC timestamp.

    SQLite returns naive values. They are stored as UTC.

    Args:
        moment: Timestamp from the database or the clock.

    Returns:
        The same moment with a UTC offset.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _ack_limit(session: Session, agent: Agent) -> int | None:
    """Return the dead-man duration for one agent.

    Args:
        session: Open database session.
        agent: Agent whose policy to read.

    Returns:
        Seconds, or `None` when the policy sets no duration, is not
        autonomous, or cannot be read.
    """
    if agent.policy_id is None:
        return None
    stored = session.get(Policy, agent.policy_id)
    if stored is None:
        return None
    try:
        document = _agent_policy(stored.yaml)
        effective = build_effective_policy(document, _stored_baselines(session))
    except (PolicyError, ApiError):
        return None
    if document.metadata.autonomy.level != "autonomous":
        return None
    return effective.max_unacknowledged_seconds


def _stored_baselines(session: Session) -> list[BaselinePolicy]:
    """Load every stored baseline in name order.

    Args:
        session: Open database session.

    Returns:
        Parsed baseline policies.

    Raises:
        PolicyError: When a stored document is not valid YAML or not a baseline.
        ApiError: When a stored document is not exactly one YAML document.
    """
    rows = session.scalars(select(Baseline).order_by(Baseline.name)).all()
    parsed: list[BaselinePolicy] = []
    for row in rows:
        document = parse_policy(_one_document(row.yaml))
        if isinstance(document, BaselinePolicy):
            parsed.append(document)
    return parsed


def _require_id(supervisor_id: str) -> None:
    """Reject a path id that is not a user or group.

    Args:
        supervisor_id: Path parameter.

    Raises:
        ApiError: Status 422 when the id is not `user:` or `group:`.
    """
    if _SUPERVISOR.fullmatch(supervisor_id) is None or len(supervisor_id) > _SUPERVISOR_LIMIT:
        raise ApiError(422, "invalid_request", "supervisor must be user: or group:")


__all__ = [
    "acknowledge_supervisor",
    "advance_dead_men",
    "get_supervisor_grants",
    "put_supervisor_grants",
    "resolved_supervisor",
    "supervisor_allow_sets",
]
