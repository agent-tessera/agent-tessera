"""Coverage and posture score for one agent."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_tessera.errors import PolicyError
from agent_tessera.policy.effective import build_effective_policy
from agent_tessera.policy.loader import parse_policy
from agent_tessera.policy.models import AgentPolicy, BaselinePolicy
from tessera_server.api.errors import ApiError
from tessera_server.db.models import Agent, Baseline, Event, Policy, utcnow
from tessera_server.services.policies import get_agent_policy
from tessera_server.services.registry import _orphaned
from tessera_server.services.supervision import _one_document

_WINDOW = timedelta(days=7)
_OWNER_POINTS = 20
_DECLARED_POINTS = 20
_COVERAGE_POINTS = 30
_DENIAL_POINTS = 15
_UNGOVERNED_POINTS = 15
_DENIAL_FULL = 0.05
_DENIAL_ZERO = 0.25
_SCORE_MAX = 100


def agent_posture(
    session: Session,
    agent_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Score one agent over the last seven days.

    Args:
        session: Open database session.
        agent_id: Agent to score.
        now: Clock for the window. Defaults to the current time.

    Returns:
        `score`, `coverage`, and `components`.

    Raises:
        ApiError: Status 404 when the agent does not exist.

    Example:
        ```python
        body = agent_posture(session, agent_id)
        assert 0 <= int(body["score"]) <= 100
        ```
    """
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise ApiError(404, "not_found", "agent was not found")
    moment = now or utcnow()
    required, declared, inputs = _policy_guardrails(session, agent)
    ran, decisions, denials, ungoverned = _observations(session, agent_id, moment)
    return score_posture(
        owner_ok=not _orphaned(session, agent),
        has_policy=agent.policy_id is not None and required is not None,
        required_names=required or (),
        declared_names=declared,
        input_required=inputs,
        ran=ran,
        decisions=decisions,
        denials=denials,
        ungoverned=ungoverned,
    )


def score_posture(
    *,
    owner_ok: bool,
    has_policy: bool,
    required_names: tuple[str, ...],
    declared_names: frozenset[str],
    input_required: tuple[str, ...],
    ran: frozenset[str],
    decisions: int,
    denials: int,
    ungoverned: int,
) -> dict[str, object]:
    """Combine the posture components into a 0-100 score.

    Args:
        owner_ok: Whether the agent has an active owner.
        has_policy: Whether a readable policy is stored.
        required_names: Effective required guardrail names.
        declared_names: Names listed on the agent policy itself.
        input_required: Required guardrails whose names start with `input.`.
        ran: Guardrail names observed with status `ran` in the window.
        decisions: Decision events in the window.
        denials: Decision events whose effect is `deny`.
        ungoverned: `ungoverned_call` events in the window.

    Returns:
        `score`, `coverage`, and `components`. Coverage is 1 when a policy
        requires no input guardrails, and 0 when no policy is stored.
        Denial points are 15 at or below 5 percent, 0 above 25 percent, and
        linear between those rates. Zero decisions score the full 15.
    """
    coverage = _coverage(has_policy, input_required, ran)
    owner = _OWNER_POINTS if owner_ok else 0
    declared = _DECLARED_POINTS if _all_declared(has_policy, required_names, declared_names) else 0
    coverage_points = _COVERAGE_POINTS * coverage
    denial_points = _denial_points(decisions, denials)
    clean = _UNGOVERNED_POINTS if ungoverned == 0 else 0
    total = owner + declared + coverage_points + denial_points + clean
    return {
        "score": _whole_score(total),
        "coverage": coverage,
        "components": {
            "owner": owner,
            "guardrails_declared": declared,
            "coverage": coverage_points,
            "denial_rate": denial_points,
            "ungoverned": clean,
            "probes": 0,
        },
    }


def _policy_guardrails(
    session: Session, agent: Agent
) -> tuple[tuple[str, ...] | None, frozenset[str], tuple[str, ...]]:
    """Read required and declared guardrail names.

    Args:
        session: Open database session.
        agent: Agent whose policy to read.

    Returns:
        Effective required names, names declared on the agent, and the
        required input names. The first item is `None` when no readable
        policy is stored.
    """
    if agent.policy_id is None:
        return None, frozenset(), ()
    stored = session.get(Policy, agent.policy_id)
    if stored is None:
        return None, frozenset(), ()
    try:
        document = parse_policy(_one_document(stored.yaml))
        if not isinstance(document, AgentPolicy):
            return None, frozenset(), ()
        effective = build_effective_policy(document, _baselines(session))
    except (PolicyError, ApiError):
        return None, frozenset(), ()
    required = tuple(item.name for item in effective.required_guardrails)
    declared = frozenset(document.spec.guardrails.required)
    inputs = tuple(name for name in required if name.startswith("input."))
    return required, declared, inputs


def _baselines(session: Session) -> list[BaselinePolicy]:
    """Load stored baselines in name order.

    Args:
        session: Open database session.

    Returns:
        Parsed baseline policies. An unreadable baseline is skipped so one
        bad document does not hide the agent's own guardrails.
    """
    rows = session.scalars(select(Baseline).order_by(Baseline.name)).all()
    parsed: list[BaselinePolicy] = []
    for row in rows:
        try:
            document = parse_policy(_one_document(row.yaml))
        except (PolicyError, ApiError):
            continue
        if isinstance(document, BaselinePolicy):
            parsed.append(document)
    return parsed


def _observations(
    session: Session, agent_id: str, moment: datetime
) -> tuple[frozenset[str], int, int, int]:
    """Count guardrail, decision, and ungoverned events in the window.

    Args:
        session: Open database session.
        agent_id: Agent whose events to read.
        moment: End of the window.

    Returns:
        Names that ran, decision count, denial count, and ungoverned count.
    """
    start = _aware(moment) - _WINDOW
    rows = session.scalars(select(Event).where(Event.agent_id == agent_id)).all()
    ran: set[str] = set()
    decisions = 0
    denials = 0
    ungoverned = 0
    for row in rows:
        if _aware(row.ts) < start:
            continue
        payload = row.payload
        if row.event_type == "guardrail" and payload.get("status") == "ran":
            name = payload.get("name")
            if isinstance(name, str):
                ran.add(name)
        elif row.event_type == "decision":
            decisions += 1
            if payload.get("effect") == "deny":
                denials += 1
        elif row.event_type == "ungoverned_call":
            ungoverned += 1
    return frozenset(ran), decisions, denials, ungoverned


def _coverage(has_policy: bool, required: tuple[str, ...], ran: frozenset[str]) -> float:
    """Return the fraction of required input guardrails that ran.

    Args:
        has_policy: Whether a readable policy is stored.
        required: Required input guardrail names.
        ran: Names observed in the window.

    Returns:
        A fraction from 0 to 1.
    """
    if not has_policy:
        return 0.0
    if not required:
        return 1.0
    found = sum(1 for name in required if name in ran)
    return found / len(required)


def _all_declared(has_policy: bool, required: tuple[str, ...], declared: frozenset[str]) -> bool:
    """Return whether the agent policy lists every required guardrail.

    Args:
        has_policy: Whether a readable policy is stored.
        required: Effective required names.
        declared: Names on the agent policy.

    Returns:
        `True` when a policy is stored and lists every required name.
    """
    return has_policy and all(name in declared for name in required)


def _denial_points(decisions: int, denials: int) -> float:
    """Return denial-rate points.

    Args:
        decisions: Decision events in the window.
        denials: Denied decisions in the window.

    Returns:
        15 at or below 5 percent, 0 above 25 percent, otherwise the linear
        value between them. Zero decisions score 15.
    """
    if decisions <= 0:
        return float(_DENIAL_POINTS)
    rate = denials / decisions
    if rate <= _DENIAL_FULL:
        return float(_DENIAL_POINTS)
    if rate >= _DENIAL_ZERO:
        return 0.0
    span = _DENIAL_ZERO - _DENIAL_FULL
    return _DENIAL_POINTS * (_DENIAL_ZERO - rate) / span


def _whole_score(total: float) -> int:
    """Round a positive total half up and clamp it to 0-100.

    Args:
        total: Sum of the component points.

    Returns:
        An integer score.
    """
    rounded = int(total + 0.5)
    return min(_SCORE_MAX, max(0, rounded))


def _aware(moment: datetime) -> datetime:
    """Return a UTC timestamp.

    Args:
        moment: Timestamp from the database or the clock.

    Returns:
        The same moment with a UTC offset.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def posture_board(session: Session) -> list[dict[str, object]]:
    """Score every non-retired agent, lowest score first.

    Args:
        session: Open database session.

    Returns:
        Rows with posture fields plus `name` and `permissive`.
    """
    rows = session.scalars(
        select(Agent).where(Agent.status != "retired").order_by(Agent.name, Agent.id)
    ).all()
    board: list[dict[str, object]] = []
    for row in rows:
        scored = agent_posture(session, row.id)
        try:
            yaml_text = get_agent_policy(session, row.id)
        except ApiError:
            yaml_text = ""
        board.append(
            {
                **scored,
                "id": row.id,
                "name": row.name,
                "permissive": _is_permissive(yaml_text),
            }
        )
    board.sort(
        key=lambda item: (
            float(item["score"]) if isinstance(item["score"], (int, float)) else 0.0,
            str(item["name"]),
            str(item["id"]),
        )
    )
    return board


def _is_permissive(policy_yaml: str) -> bool:
    """Return whether any allow list contains a literal `*`.

    Args:
        policy_yaml: Stored agent policy text.

    Returns:
        True when models, tools, or data allow `*`.
    """
    if policy_yaml.strip() == "":
        return False
    try:
        loaded = yaml.safe_load(policy_yaml)
    except yaml.YAMLError:
        return False
    if not isinstance(loaded, dict):
        return False
    spec = loaded.get("spec")
    if not isinstance(spec, dict):
        return False
    for kind in ("models", "tools", "data"):
        block = spec.get(kind)
        if not isinstance(block, dict):
            continue
        allow = block.get("allow")
        if isinstance(allow, list) and "*" in allow:
            return True
    return False


__all__ = ["agent_posture", "posture_board", "score_posture"]
