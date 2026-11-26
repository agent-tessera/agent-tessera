"""Store alert rules and fire them from footprint totals."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from tessera_server.api.errors import ApiError
from tessera_server.config import Settings
from tessera_server.db.models import Agent, AlertFiring, AlertRule, utcnow
from tessera_server.ids import new_ulid
from tessera_server.services.footprint import footprint_totals
from tessera_server.services.registry import transition_agent
from tessera_server.services.webhooks import deliver

_NAME = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_WINDOW = re.compile(r"^(\d+)([smh])$")
_NAME_LIMIT = 63
_HOUR = 3600
_MINUTE = 60
_WEEK = 7 * 24 * _HOUR
_METRICS = frozenset(
    {"tokens_in", "tokens_out", "cost_usd", "model_calls", "tool_calls", "denials"}
)
_ACTIONS = frozenset({"webhook", "suspend"})
_UNITS = {"s": 1, "m": _MINUTE, "h": _HOUR}
_ACTIVE = "active"


def create_alert(
    session: Session,
    *,
    name: str,
    metric: str,
    threshold: float,
    window: str,
    labels: Mapping[str, str],
    autonomy: str | None,
    action: str,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Store one alert rule.

    Args:
        session: Open database session.
        name: Unique slug.
        metric: Footprint field to compare.
        threshold: Value at or above which the rule fires.
        window: Lookback such as `30s`, `10m`, or `1h`.
        labels: Labels an agent must carry.
        autonomy: Autonomy an agent must have, or `None` for any.
        action: `webhook` or `suspend`.
        now: Clock for `created_at`.

    Returns:
        The stored rule.

    Raises:
        ApiError: Status 409 when the name is taken, or 422 when a field is
            unusable.

    Example:
        ```python
        rule = create_alert(
            session,
            name="denials",
            metric="denials",
            threshold=5,
            window="1h",
            labels={},
            autonomy=None,
            action="webhook",
        )
        assert rule["metric"] == "denials"
        ```
    """
    if not _NAME.fullmatch(name) or len(name) > _NAME_LIMIT:
        raise ApiError(422, "invalid_request", "name must be a lowercase slug")
    if metric not in _METRICS:
        raise ApiError(422, "invalid_request", "metric is not a footprint total")
    if threshold <= 0:
        raise ApiError(422, "invalid_request", "threshold must be positive")
    if action not in _ACTIONS:
        raise ApiError(422, "invalid_request", "action must be webhook or suspend")
    if session.scalar(select(AlertRule).where(AlertRule.name == name)) is not None:
        raise ApiError(409, "conflict", "alert name is already used")
    row = AlertRule(
        id=new_ulid(),
        name=name,
        metric=metric,
        threshold=threshold,
        window_seconds=_window_seconds(window),
        labels=dict(labels),
        autonomy=autonomy,
        action=action,
        created_at=(now or utcnow)(),
    )
    session.add(row)
    session.commit()
    return _rule(row)


def list_alerts(session: Session) -> dict[str, object]:
    """List alert rules, oldest first.

    Args:
        session: Open database session.

    Returns:
        A page with `items`.

    Example:
        ```python
        page = list_alerts(session)
        assert "items" in page
        ```
    """
    rows = session.scalars(select(AlertRule).order_by(AlertRule.created_at)).all()
    return {"items": [_rule(row) for row in rows]}


def delete_alert(session: Session, alert_id: str) -> dict[str, str]:
    """Delete a rule and its firing history.

    Args:
        session: Open database session.
        alert_id: Rule to delete.

    Returns:
        The deleted id.

    Raises:
        ApiError: Status 404 when the rule does not exist.

    Example:
        ```python
        deleted = delete_alert(session, rule_id)
        assert deleted["id"] == rule_id
        ```
    """
    row = session.get(AlertRule, alert_id)
    if row is None:
        raise ApiError(404, "not_found", "alert not found")
    firings = session.scalars(select(AlertFiring).where(AlertFiring.rule_id == row.id)).all()
    for firing in firings:
        session.delete(firing)
    session.delete(row)
    session.commit()
    return {"id": alert_id}


def evaluate_alerts(
    session: Session,
    settings: Settings,
    *,
    now: Callable[[], datetime] | None = None,
) -> int:
    """Fire rules whose footprint is at or above the threshold.

    A rule fires at most once per agent per window. `webhook` and `suspend`
    both emit `monitor.alert`. `suspend` also suspends an active agent.

    Args:
        session: Open database session.
        settings: Server webhook secret.
        now: Clock for the window and the firing row.

    Returns:
        How many alerts were sent.

    Example:
        ```python
        sent = evaluate_alerts(session, settings)
        assert sent >= 0
        ```
    """
    moment = (now or utcnow)()
    fired = 0
    rules = session.scalars(select(AlertRule).order_by(AlertRule.created_at)).all()
    agents = session.scalars(select(Agent).where(Agent.status == _ACTIVE)).all()
    for rule in rules:
        for agent in agents:
            if _matches(agent, rule) and _over(session, rule, agent.id, moment):
                if _recent(session, rule, agent.id, moment):
                    continue
                _fire(session, settings, rule, agent, moment)
                fired += 1
    return fired


def _fire(
    session: Session,
    settings: Settings,
    rule: AlertRule,
    agent: Agent,
    moment: datetime,
) -> None:
    """Record a firing, deliver it, and suspend when that is the action.

    Args:
        session: Open database session.
        settings: Server webhook secret.
        rule: Rule that crossed its threshold.
        agent: Agent the footprint belongs to.
        moment: Clock reading for the firing.
    """
    session.add(AlertFiring(id=new_ulid(), rule_id=rule.id, agent_id=agent.id, fired_at=moment))
    deliver(
        session,
        settings,
        "monitor.alert",
        {
            "event": "monitor.alert",
            "alert_id": rule.id,
            "name": rule.name,
            "agent_id": agent.id,
            "metric": rule.metric,
            "threshold": rule.threshold,
        },
    )
    if rule.action == "suspend":
        transition_agent(session, agent.id, "suspend", actor="alerts", now=lambda: moment)
    session.commit()


def _over(session: Session, rule: AlertRule, agent_id: str, moment: datetime) -> bool:
    """Return whether the agent's footprint is at or above the threshold.

    Args:
        session: Open database session.
        rule: Rule being evaluated.
        agent_id: Agent to measure.
        moment: End of the window.

    Returns:
        `True` when the metric is at least `threshold`.
    """
    start = moment - timedelta(seconds=rule.window_seconds)
    totals = footprint_totals(session, agent_id=agent_id, start=start, end=moment)
    raw = totals.get(rule.metric, 0)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return False
    return float(raw) >= rule.threshold


def _recent(session: Session, rule: AlertRule, agent_id: str, moment: datetime) -> bool:
    """Return whether this rule already fired for the agent inside the window.

    Args:
        session: Open database session.
        rule: Rule being evaluated.
        agent_id: Agent to check.
        moment: Current time.

    Returns:
        `True` when a firing is still inside the window.
    """
    cutoff = moment - timedelta(seconds=rule.window_seconds)
    found = session.scalar(
        select(AlertFiring.id)
        .where(
            AlertFiring.rule_id == rule.id,
            AlertFiring.agent_id == agent_id,
            AlertFiring.fired_at >= cutoff,
        )
        .limit(1)
    )
    return found is not None


def _matches(agent: Agent, rule: AlertRule) -> bool:
    """Return whether an agent is selected by a rule.

    Args:
        agent: Agent to test.
        rule: Rule selector.

    Returns:
        `True` when every label matches and the autonomy matches.
    """
    if rule.autonomy is not None and agent.autonomy != rule.autonomy:
        return False
    labels = agent.labels
    return all(str(labels.get(key, "")) == str(expected) for key, expected in rule.labels.items())


def _window_seconds(window: str) -> int:
    """Parse a lookback into seconds.

    Args:
        window: Text such as `30s`, `10m`, or `1h`.

    Returns:
        The lookback in seconds.

    Raises:
        ApiError: Status 422 when the text is not a duration, or it is longer
            than a week.
    """
    match = _WINDOW.fullmatch(window.strip())
    if match is None:
        raise ApiError(422, "invalid_request", "window must look like 30s, 10m, or 1h")
    seconds = int(match.group(1)) * _UNITS[match.group(2)]
    if seconds < 1 or seconds > _WEEK:
        raise ApiError(422, "invalid_request", "window must be from 1s to 168h")
    return seconds


def _rule(row: AlertRule) -> dict[str, object]:
    """Return the JSON document for a rule.

    Args:
        row: Stored rule.

    Returns:
        The rule fields. `window` is the duration text.
    """
    return {
        "id": row.id,
        "name": row.name,
        "metric": row.metric,
        "threshold": row.threshold,
        "window": _window_text(row.window_seconds),
        "selector": {"labels": dict(row.labels), "autonomy": row.autonomy},
        "action": row.action,
    }


def _window_text(seconds: int) -> str:
    """Format a lookback the way the API accepts it.

    Args:
        seconds: Lookback in seconds.

    Returns:
        Hours, minutes, or seconds.
    """
    if seconds % _HOUR == 0:
        return f"{seconds // _HOUR}h"
    if seconds % _MINUTE == 0:
        return f"{seconds // _MINUTE}m"
    return f"{seconds}s"


__all__ = ["create_alert", "delete_alert", "evaluate_alerts", "list_alerts"]
