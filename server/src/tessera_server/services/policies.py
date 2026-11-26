"""Stored agent policies, baselines, and signed bundles."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Literal, cast

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_tessera.bundle import BundleClaims, PeerAllowSet, SupervisorAllowSet, encode_bundle
from agent_tessera.errors import PolicyError
from agent_tessera.policy.effective import PatternRule, build_effective_policy
from agent_tessera.policy.loader import parse_policy
from agent_tessera.policy.models import (
    AgentPolicy,
    AgentPolicyMetadata,
    AutonomyLevel,
    AutonomySettings,
    BaselinePolicy,
    RiskTier,
)
from agent_tessera.types import ActionKind
from tessera_server.api.errors import ApiError
from tessera_server.config import Settings
from tessera_server.db.models import Agent, Baseline, Policy, Revocation, utcnow
from tessera_server.ids import new_ulid
from tessera_server.services.audit import append_audit
from tessera_server.services.keys import ensure_active_key, load_private_key
from tessera_server.services.supervision import supervisor_allow_sets

_BundleStatus = Literal["active", "suspended"]


def put_agent_policy(
    session: Session,
    agent_id: str,
    text: str,
    *,
    actor: str,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Store an agent policy when it matches the agent and the baselines.

    Args:
        session: Open database session.
        agent_id: Agent the document belongs to.
        text: YAML document.
        actor: Audit actor.
        now: Clock for the row and any revocation.

    Returns:
        The effective policy version.

    Raises:
        ApiError: Status 404 when the agent does not exist, or 422 when the
            document is not that agent's policy or the baselines reject it.
    """
    agent = _require_agent(session, agent_id)
    document = _agent_document(text)
    if document.metadata.agent != agent.name or document.metadata.environment != agent.environment:
        raise ApiError(
            422,
            "invalid_policy",
            "metadata.agent and metadata.environment must match the agent",
        )
    baselines = _baseline_models(session)
    try:
        effective = build_effective_policy(document, baselines)
    except PolicyError as err:
        raise ApiError(422, "invalid_policy", str(err)) from err
    previous = _current_version(session, agent)
    moment = (now or utcnow)()
    row = Policy(
        id=new_ulid(),
        agent_id=agent.id,
        yaml=text,
        sha256=effective.version,
        author=actor,
        created_at=moment,
    )
    session.add(row)
    session.flush()
    agent.policy_id = row.id
    _note_version(session, agent, previous, effective.version, moment)
    append_audit(
        session,
        actor=actor,
        entry_type="policy_updated",
        payload={"agent_id": agent.id, "version": effective.version},
        now=now,
    )
    return {"version": effective.version}


def dev_policy_yaml(agent: str) -> str:
    """Return the permissive policy used for a development agent.

    Args:
        agent: Agent slug written into `metadata.agent`.

    Returns:
        YAML that allows every model, tool, and data scope, flags a missing
        guardrail, and caps a task at 200 steps.
    """
    return (
        "apiVersion: tessera/v1\n"
        "kind: AgentPolicy\n"
        "metadata:\n"
        f"  agent: {agent}\n"
        "  environment: dev\n"
        "spec:\n"
        "  models:\n"
        '    allow: ["*"]\n'
        "  tools:\n"
        '    allow: ["*"]\n'
        "  data:\n"
        '    allow: ["*"]\n'
        "  guardrails:\n"
        "    on_missing: flag\n"
        "  limits:\n"
        "    max_steps_per_task: 200\n"
    )


def get_agent_policy(session: Session, agent_id: str) -> str:
    """Return the YAML stored for an agent.

    Args:
        session: Open database session.
        agent_id: Agent to read.

    Returns:
        The stored YAML text.

    Raises:
        ApiError: Status 404 when the agent or its policy does not exist.
    """
    agent = _require_agent(session, agent_id)
    policy = None if agent.policy_id is None else session.get(Policy, agent.policy_id)
    if policy is None:
        raise ApiError(404, "no_policy", "agent has no policy")
    return policy.yaml


def put_baseline(
    session: Session,
    name: str,
    text: str,
    *,
    actor: str,
    now: Callable[[], datetime] | None = None,
) -> str:
    """Store a baseline when every current agent policy still builds.

    Args:
        session: Open database session.
        name: Baseline name from the URL.
        text: YAML document.
        actor: Audit actor.
        now: Clock for the row and revocations.

    Returns:
        The stored YAML.

    Raises:
        ApiError: Status 422 when the document is not that baseline or it
            makes an existing agent policy invalid.
    """
    document = _baseline_document(text, name)
    pending = [item for item in _baseline_models(session) if item.metadata.name != name]
    pending.append(document)
    versions = _versions(session, pending)
    moment = (now or utcnow)()
    row = session.get(Baseline, name)
    if row is None:
        row = Baseline(
            name=name,
            yaml=text,
            sha256=_digest(document),
            author=actor,
            updated_at=moment,
        )
        session.add(row)
    else:
        row.yaml = text
        row.sha256 = _digest(document)
        row.author = actor
        row.updated_at = moment
    _apply_versions(session, versions, moment)
    append_audit(
        session,
        actor=actor,
        entry_type="baseline_updated",
        payload={"name": name},
        now=now,
    )
    return text


def get_baseline(session: Session, name: str) -> str:
    """Return one baseline's YAML.

    Args:
        session: Open database session.
        name: Baseline name.

    Returns:
        The stored YAML text.

    Raises:
        ApiError: Status 404 when the baseline does not exist.
    """
    row = session.get(Baseline, name)
    if row is None:
        raise ApiError(404, "not_found", "baseline not found")
    return row.yaml


def delete_baseline(
    session: Session,
    name: str,
    *,
    actor: str,
    now: Callable[[], datetime] | None = None,
) -> None:
    """Delete a baseline and refresh agent versions.

    Args:
        session: Open database session.
        name: Baseline to delete.
        actor: Audit actor.
        now: Clock for revocations.

    Raises:
        ApiError: Status 404 when the baseline does not exist, or 422 when
            removing it makes an agent policy invalid.
    """
    row = session.get(Baseline, name)
    if row is None:
        raise ApiError(404, "not_found", "baseline not found")
    remaining = [item for item in _baseline_models(session) if item.metadata.name != name]
    versions = _versions(session, remaining)
    moment = (now or utcnow)()
    session.delete(row)
    _apply_versions(session, versions, moment)
    append_audit(
        session,
        actor=actor,
        entry_type="baseline_deleted",
        payload={"name": name},
        now=now,
    )


def list_baselines(
    session: Session, *, limit: int = 50, cursor: str | None = None
) -> dict[str, object]:
    """List baseline names.

    Args:
        session: Open database session.
        limit: Maximum number of rows to return.
        cursor: Return names greater than this value.

    Returns:
        `items` and `next_cursor`.
    """
    query = select(Baseline).order_by(Baseline.name)
    if cursor is not None:
        query = query.where(Baseline.name > cursor)
    rows = session.scalars(query.limit(limit + 1)).all()
    page = list(rows[:limit])
    next_cursor = page[-1].name if len(rows) > limit else None
    items = [{"name": row.name, "sha256": row.sha256} for row in page]
    return {"items": items, "next_cursor": next_cursor}


def list_baseline_agents(session: Session, name: str) -> dict[str, object]:
    """List agents whose registry metadata matches a baseline selector.

    Args:
        session: Open database session.
        name: Baseline name.

    Returns:
        `items` of matching agents (id, name, environment, status).

    Raises:
        ApiError: Status 404 when the baseline does not exist, or 422 when
            stored YAML is not a baseline.
    """
    text = get_baseline(session, name)
    document = _baseline_document(text, name)
    agents = session.scalars(select(Agent).order_by(Agent.name, Agent.environment)).all()
    items = [
        {
            "id": agent.id,
            "name": agent.name,
            "environment": agent.environment,
            "status": agent.status,
        }
        for agent in agents
        if document.spec.selector.matches(_agent_metadata(agent))
    ]
    return {"items": items}


def validate_baseline(session: Session, name: str, text: str) -> dict[str, object]:
    """Parse a baseline YAML body without writing.

    Args:
        session: Open database session (unused; kept for API symmetry).
        name: Baseline name required of ``metadata.name``.
        text: YAML document.

    Returns:
        ``ok``, ``name``, and content digest.

    Raises:
        ApiError: Status 422 when the document is invalid.
    """
    del session
    document = _baseline_document(text, name)
    return {"ok": True, "name": name, "sha256": _digest(document)}


def dry_run_baseline(session: Session, name: str, text: str) -> dict[str, object]:
    """Preview baseline replace impact without writing.

    Args:
        session: Open database session.
        name: Baseline name.
        text: Candidate YAML document.

    Returns:
        Matching agent count and per-policy-agent impact rows
        (``would_change``, ``ok``, optional ``error`` / versions).

    Raises:
        ApiError: Status 422 when the candidate YAML is not that baseline.
    """
    document = _baseline_document(text, name)
    pending = [item for item in _baseline_models(session) if item.metadata.name != name]
    pending.append(document)
    matching = 0
    for agent in session.scalars(select(Agent)).all():
        if document.spec.selector.matches(_agent_metadata(agent)):
            matching += 1
    impacts: list[dict[str, object]] = []
    agents = session.scalars(select(Agent).where(Agent.policy_id.is_not(None))).all()
    for agent in agents:
        policy = None if agent.policy_id is None else session.get(Policy, agent.policy_id)
        if policy is None:
            continue
        try:
            agent_doc = _agent_document(policy.yaml)
            effective = build_effective_policy(agent_doc, pending)
        except (PolicyError, ApiError) as err:
            impacts.append(
                {
                    "agent_id": agent.id,
                    "name": agent.name,
                    "environment": agent.environment,
                    "ok": False,
                    "would_change": False,
                    "current_version": policy.sha256,
                    "new_version": None,
                    "error": str(err),
                }
            )
            continue
        impacts.append(
            {
                "agent_id": agent.id,
                "name": agent.name,
                "environment": agent.environment,
                "ok": True,
                "would_change": policy.sha256 != effective.version,
                "current_version": policy.sha256,
                "new_version": effective.version,
                "error": None,
            }
        )
    return {"matching_agents": matching, "items": impacts}


def issue_bundle(
    session: Session,
    settings: Settings,
    agent_id: str,
    *,
    if_none_match: str | None,
    now: Callable[[], datetime] | None = None,
) -> tuple[int, str, str | None]:
    """Sign the agent's current effective policy.

    Args:
        session: Open database session.
        settings: Issuer, bundle lifetime, and key-encryption key.
        agent_id: Agent the bundle is for.
        if_none_match: `If-None-Match` header, when the client sent one.
        now: Clock for issuance and expiry.

    Returns:
        The status, the quoted ETag, and the JWT. The JWT is `None` for 304.

    Raises:
        ApiError: Status 404 when the agent has no policy, or 409 when the
            agent is not active or suspended.
    """
    agent = _require_agent(session, agent_id)
    policy = None if agent.policy_id is None else session.get(Policy, agent.policy_id)
    if policy is None:
        raise ApiError(404, "no_policy", "agent has no policy")
    if agent.status not in {"active", "suspended"}:
        message = f"cannot issue a bundle for an agent that is {agent.status}"
        raise ApiError(409, "invalid_status", message)
    document = _agent_document(policy.yaml)
    baselines = _baseline_models(session)
    try:
        effective = build_effective_policy(document, baselines)
    except PolicyError as err:
        raise ApiError(422, "invalid_policy", str(err)) from err
    grants = supervisor_allow_sets(session, agent.environment)
    validator = _bundle_validator(effective.version, grants)
    quoted = f'"{validator}"'
    if if_none_match is not None and if_none_match.strip() in {quoted, validator}:
        return 304, quoted, None
    moment = (now or utcnow)()
    issued = int(moment.timestamp())
    key = ensure_active_key(session, settings, now=lambda: moment)
    peers = _peer_allow_sets(session, agent.environment, baselines)
    claims = BundleClaims(
        iss=settings.issuer,
        aud="tessera-bundle",
        iat=issued,
        exp=issued + settings.bundle_ttl,
        schema_="tessera.bundle/v1",
        agent_id=agent.id,
        agent=agent.name,
        environment=agent.environment,
        status=cast("_BundleStatus", agent.status),
        version=effective.version,
        policy=document.model_dump(mode="json", by_alias=True),
        baselines=[item.model_dump(mode="json", by_alias=True) for item in baselines],
        peers=peers,
        supervisor_grants=grants,
    )
    token = encode_bundle(claims, load_private_key(key, settings), key.kid)
    return 200, quoted, token


def _bundle_validator(version: str, grants: dict[str, SupervisorAllowSet]) -> str:
    """Return the bundle validator, including grants when they are embedded.

    Args:
        version: Effective policy version.
        grants: Supervisor allow sets embedded in the bundle. Empty when the
            validator is the policy version alone.

    Returns:
        The validator stored as the ETag.
    """
    if not grants:
        return version
    payload = {
        name: {"model": list(grants[name].model), "tool": list(grants[name].tool)}
        for name in sorted(grants)
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return f"{version}+{digest[:16]}"


def _peer_allow_sets(
    session: Session,
    environment: str,
    baselines: list[BaselinePolicy],
) -> dict[str, PeerAllowSet]:
    """Collect allow globs for agents that share an environment.

    The set is empty unless some agent in that environment uses
    `caller_constraint: intersect`. Offline mode has no bundle, so this
    embedding does not apply there.

    Args:
        session: Open database session.
        environment: Environment of the agent whose bundle is being signed.
        baselines: Baselines already loaded for the server.

    Returns:
        Allow sets keyed by agent name. Empty when nobody intersects.

    Raises:
        ApiError: Status 422 when a stored peer policy is no longer valid.
    """
    rows = session.scalars(select(Agent).where(Agent.environment == environment)).all()
    documents: list[tuple[str, AgentPolicy]] = []
    for row in rows:
        if row.policy_id is None:
            continue
        stored = session.get(Policy, row.policy_id)
        if stored is None:
            continue
        documents.append((row.name, _agent_document(stored.yaml)))
    if not any(_uses_intersect(document) for _, document in documents):
        return {}
    peers: dict[str, PeerAllowSet] = {}
    for name, document in sorted(documents, key=lambda item: item[0]):
        try:
            effective = build_effective_policy(document, baselines)
        except PolicyError as err:
            raise ApiError(422, "invalid_policy", str(err)) from err
        peers[name] = PeerAllowSet(
            model=_globs(effective.allow[ActionKind.MODEL]),
            tool=_globs(effective.allow[ActionKind.TOOL]),
            data=_globs(effective.allow[ActionKind.DATA]),
            agent=_globs(effective.allow[ActionKind.AGENT]),
        )
    return peers


def _uses_intersect(document: AgentPolicy) -> bool:
    """Return whether an agent policy asks for chain intersection.

    Args:
        document: Stored agent policy.

    Returns:
        `True` when the scalar constraint or any per-caller rule is
        `intersect`.
    """
    constraint = document.spec.agents.caller_constraint
    if constraint == "intersect":
        return True
    if isinstance(constraint, str):
        return False
    return any(rule.constraint == "intersect" for rule in constraint)


def _globs(rules: tuple[PatternRule, ...]) -> tuple[str, ...]:
    """Return the glob text of pattern rules.

    Args:
        rules: Allow rules for one action kind.

    Returns:
        The patterns, in policy order.
    """
    return tuple(rule.pattern for rule in rules)


_AUTONOMY_LEVELS = frozenset({"assistive", "interactive", "supervised", "autonomous"})


def _agent_metadata(agent: Agent) -> AgentPolicyMetadata:
    """Build policy metadata from a registry agent row.

    Args:
        agent: Stored agent.

    Returns:
        Metadata suitable for baseline selector matching.
    """
    labels = {str(key): str(value) for key, value in dict(agent.labels or {}).items()}
    level = cast(
        AutonomyLevel,
        agent.autonomy if agent.autonomy in _AUTONOMY_LEVELS else "interactive",
    )
    risk = cast(
        RiskTier,
        agent.risk_tier if agent.risk_tier in {"low", "medium", "high"} else "low",
    )
    return AgentPolicyMetadata(
        agent=agent.name,
        environment=agent.environment,
        risk_tier=risk,
        labels=labels,
        autonomy=AutonomySettings(level=level),
    )


def _agent_document(text: str) -> AgentPolicy:
    """Parse YAML into an agent policy.

    Args:
        text: YAML document.

    Returns:
        The agent policy.

    Raises:
        ApiError: Status 422 when the text is not an agent policy.
    """
    try:
        document = parse_policy(_one_document(text))
    except PolicyError as err:
        raise ApiError(422, "invalid_policy", str(err)) from err
    if not isinstance(document, AgentPolicy):
        raise ApiError(422, "invalid_policy", "expected an AgentPolicy")
    return document


def _baseline_document(text: str, name: str) -> BaselinePolicy:
    """Parse YAML into the named baseline.

    Args:
        text: YAML document.
        name: Name required of `metadata.name`.

    Returns:
        The baseline policy.

    Raises:
        ApiError: Status 422 when the text is not that baseline.
    """
    try:
        document = parse_policy(_one_document(text))
    except PolicyError as err:
        raise ApiError(422, "invalid_policy", str(err)) from err
    if not isinstance(document, BaselinePolicy):
        raise ApiError(422, "invalid_policy", "expected a BaselinePolicy")
    if document.metadata.name != name:
        raise ApiError(422, "invalid_policy", "metadata.name must equal the baseline name")
    return document


def _one_document(text: str) -> object:
    """Load the only YAML document in a request body.

    Args:
        text: Request body.

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


def _baseline_models(session: Session) -> list[BaselinePolicy]:
    """Load every stored baseline in name order.

    Args:
        session: Open database session.

    Returns:
        Parsed baseline policies.
    """
    rows = session.scalars(select(Baseline).order_by(Baseline.name)).all()
    parsed: list[BaselinePolicy] = []
    for row in rows:
        document = parse_policy(_one_document(row.yaml))
        if isinstance(document, BaselinePolicy):
            parsed.append(document)
    return parsed


def _versions(session: Session, baselines: list[BaselinePolicy]) -> list[tuple[Agent, Policy, str]]:
    """Rebuild every stored agent policy against a baseline set.

    Args:
        session: Open database session.
        baselines: Baselines to apply.

    Returns:
        Each agent, its current policy row, and the new version.

    Raises:
        ApiError: Status 422 when a stored policy no longer builds.
    """
    found: list[tuple[Agent, Policy, str]] = []
    agents = session.scalars(select(Agent).where(Agent.policy_id.is_not(None))).all()
    for agent in agents:
        policy = None if agent.policy_id is None else session.get(Policy, agent.policy_id)
        if policy is None:
            continue
        document = _agent_document(policy.yaml)
        try:
            effective = build_effective_policy(document, baselines)
        except PolicyError as err:
            raise ApiError(422, "invalid_policy", str(err)) from err
        found.append((agent, policy, effective.version))
    return found


def _apply_versions(
    session: Session, versions: list[tuple[Agent, Policy, str]], moment: datetime
) -> None:
    """Store new versions and notify active agents whose version changed.

    Args:
        session: Open database session.
        versions: Rebuilt versions.
        moment: Revocation timestamp.
    """
    for agent, policy, version in versions:
        _note_version(session, agent, policy.sha256, version, moment)
        policy.sha256 = version


def _note_version(
    session: Session, agent: Agent, previous: str | None, version: str, moment: datetime
) -> None:
    """Record a policy change for an active agent.

    Args:
        session: Open database session.
        agent: Agent whose version may have changed.
        previous: Version before the change, or `None`.
        version: Version after the change.
        moment: Revocation timestamp.
    """
    if agent.status == "active" and previous != version:
        session.add(
            Revocation(
                kind="policy_updated",
                agent_id=agent.id,
                subject=agent.id,
                created_at=moment,
            )
        )


def _current_version(session: Session, agent: Agent) -> str | None:
    """Return the agent's current effective version.

    Args:
        session: Open database session.
        agent: Agent to read.

    Returns:
        The stored version, or `None` when the agent has no policy.
    """
    if agent.policy_id is None:
        return None
    policy = session.get(Policy, agent.policy_id)
    if policy is None:
        return None
    return policy.sha256


def _digest(document: BaselinePolicy) -> str:
    """Hash a baseline document.

    Args:
        document: Baseline to hash.

    Returns:
        `sha256:` plus the hex digest of the canonical JSON.
    """
    payload = json.dumps(
        document.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


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


__all__ = [
    "delete_baseline",
    "dev_policy_yaml",
    "get_agent_policy",
    "get_baseline",
    "issue_bundle",
    "list_baselines",
    "put_agent_policy",
    "put_baseline",
]
