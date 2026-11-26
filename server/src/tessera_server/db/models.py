"""SQLAlchemy models shared by SQLite and Postgres."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Return the current time in UTC.

    Returns:
        A timezone-aware datetime.
    """
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Base class for every control-plane table."""


class Owner(Base):
    """A user or team that can own agents."""

    __tablename__ = "owners"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    owner_type: Mapped[str] = mapped_column("type", String(16))
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Agent(Base):
    """A registered agent in one environment."""

    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("name", "environment"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(String(63))
    environment: Mapped[str] = mapped_column(String(63))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    status: Mapped[str] = mapped_column(String(16), default="draft")
    risk_tier: Mapped[str] = mapped_column(String(16), default="low")
    labels: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    autonomy: Mapped[str] = mapped_column(String(32), default="interactive")
    supervisors: Mapped[list[object]] = mapped_column(JSON, default=list)
    policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("policies.id", use_alter=True, name="fk_agents_policy_id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    supervisor_ack_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Instance(Base):
    """One running copy of an agent."""

    __tablename__ = "instances"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    host: Mapped[str] = mapped_column(String(255), default="")
    runtime: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    bound_supervisor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Policy(Base):
    """One stored revision of an agent policy."""

    __tablename__ = "policies"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    yaml: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(71))
    author: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Baseline(Base):
    """A named baseline policy."""

    __tablename__ = "baselines"

    name: Mapped[str] = mapped_column(String(63), primary_key=True)
    yaml: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(71))
    author: Mapped[str] = mapped_column(String(200), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SigningKey(Base):
    """An Ed25519 key used to sign tokens and bundles."""

    __tablename__ = "signing_keys"

    kid: Mapped[str] = mapped_column(String(64), primary_key=True)
    public_key: Mapped[str] = mapped_column(Text)
    private_key_enc: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    retire_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProviderSecret(Base):
    """A provider API key encrypted with the server key-encryption key.

    Attributes:
        id: Row id.
        name: Unique label an agent asks for when a key is issued.
        provider: Provider the key belongs to, such as `openai`.
        ciphertext: Base64 nonce and AES-GCM ciphertext. The plaintext is not stored.
        created_at: When the row was stored.
        updated_at: When the row last changed.
    """

    __tablename__ = "provider_secrets"
    __table_args__ = (UniqueConstraint("name"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(64))
    ciphertext: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AdminKey(Base):
    """A hashed admin API key."""

    __tablename__ = "admin_keys"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    secret_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class AdminSession(Base):
    """A signed-in admin UI session. Only the hash of the cookie secret is stored."""

    __tablename__ = "admin_sessions"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    secret_sha256: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(300))
    admin_key_id: Mapped[str | None] = mapped_column(ForeignKey("admin_keys.id"), nullable=True)
    csrf: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RegistrationToken(Base):
    """A hashed token an agent uses to obtain its first runtime JWT."""

    __tablename__ = "registration_tokens"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    secret_sha256: Mapped[str] = mapped_column(String(64))
    reusable: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(200), default="")
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class IssuedToken(Base):
    """A runtime JWT that has been issued."""

    __tablename__ = "issued_tokens"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(26))
    instance_id: Mapped[str] = mapped_column(String(32))
    exp: Mapped[int] = mapped_column(Integer)


class Event(Base):
    """An event uploaded by an agent instance."""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_agent_ts", "agent_id", "ts"),
        Index("ix_events_task", "task"),
        Index("ix_events_run_ts", "run_id", "ts"),
        Index("ix_events_span_id", "span_id"),
        Index("ix_events_agent_type_ts", "agent_id", "type", "ts"),
        Index("ix_events_resource", "resource"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(26))
    instance_id: Mapped[str] = mapped_column(String(32))
    event_type: Mapped[str] = mapped_column("type", String(64))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    task: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    span_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    parent_span_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    resource: Mapped[str | None] = mapped_column(String(300), nullable=True)
    effect: Mapped[str | None] = mapped_column(String(24), nullable=True)
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rule: Mapped[str | None] = mapped_column(String(200), nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEntry(Base):
    """One row of the server's hash-chained audit log."""

    __tablename__ = "audit_log"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor: Mapped[str] = mapped_column(String(300))
    entry_type: Mapped[str] = mapped_column("type", String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    row_hash: Mapped[str] = mapped_column("hash", String(64))


class Run(Base):
    """A task id shared by one or more instances."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    supervisor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    principal: Mapped[str | None] = mapped_column(String(320), nullable=True)


class RunParticipant(Base):
    """An instance that has reported events for a run."""

    __tablename__ = "run_participants"

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(26))


class Revocation(Base):
    """A row the revocation stream delivers to agents."""

    __tablename__ = "revocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32))
    agent_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    subject: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TokenCounter(Base):
    """Tokens used and reserved for one agent on one UTC day."""

    __tablename__ = "token_counters"

    agent_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    used: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)


class TokenLease(Base):
    """A grant of daily tokens to one instance."""

    __tablename__ = "token_leases"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(26))
    instance_id: Mapped[str] = mapped_column(String(32))
    day: Mapped[date] = mapped_column(Date)
    granted: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed: Mapped[bool] = mapped_column(Boolean, default=False)


class QuotaCounter(Base):
    """Used and reserved units for one quota bucket.

    Attributes:
        scope_key: Who shares the counter, such as `agent:<id>` or
            `principal:<id>`.
        quota_name: Quota name from the effective policy.
        bucket: Window bucket, such as a UTC day or hour.
        used: Units already reported.
        reserved: Units held by open leases.
    """

    __tablename__ = "quota_counters"

    scope_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    quota_name: Mapped[str] = mapped_column(String(63), primary_key=True)
    bucket: Mapped[str] = mapped_column(String(64), primary_key=True)
    used: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)


class Approval(Base):
    """One approval request offered to a route.

    Attributes:
        id: Approval id.
        agent_id: Agent that asked.
        instance_id: Instance that asked.
        run_id: Run the action belongs to, when the caller named one.
        action: Action document from the SDK.
        decision: Decision document from the SDK.
        route: Resolved escalation route and the timeout, in seconds.
        route_index: Which party is currently offered the approval.
        status: `pending`, `approved`, `denied`, `timeout`, or `cancelled`.
        requested_by_principal: Principal who triggered the action, when known.
        resolved_by: Party who approved or denied it.
        expires_at: When the current party loses its turn.
        created_at: When the request was stored.
        resolved_at: When it reached a terminal status.
    """

    __tablename__ = "approvals"
    __table_args__ = (Index("ix_approvals_status_expires", "status", "expires_at"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(26))
    instance_id: Mapped[str] = mapped_column(String(32))
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[dict[str, object]] = mapped_column(JSON)
    decision: Mapped[dict[str, object]] = mapped_column(JSON)
    route: Mapped[dict[str, object]] = mapped_column(JSON)
    route_index: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    requested_by_principal: Mapped[str | None] = mapped_column(String(320), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QuotaLease(Base):
    """A grant of one named quota to one instance."""

    __tablename__ = "quota_leases"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(26))
    instance_id: Mapped[str] = mapped_column(String(32))
    scope_key: Mapped[str] = mapped_column(String(512))
    quota_name: Mapped[str] = mapped_column(String(63))
    bucket: Mapped[str] = mapped_column(String(64))
    granted: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed: Mapped[bool] = mapped_column(Boolean, default=False)


class RunEffect(Base):
    """One opaque change recorded for a run.

    Attributes:
        id: Event id that produced the row.
        run_id: Run the effect belongs to.
        agent_id: Agent that reported it.
        ref: Opaque reference. The ledger does not store content.
        reversible: Whether the caller says the change can be undone.
        undo: Undo tool name, or `None`.
        created_at: When the server stored the row.
    """

    __tablename__ = "run_effects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64))
    agent_id: Mapped[str] = mapped_column(String(26))
    ref: Mapped[str] = mapped_column(String(500))
    reversible: Mapped[bool] = mapped_column(Boolean)
    undo: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SupervisorGrant(Base):
    """YAML allow lists for one supervisor."""

    __tablename__ = "supervisor_grants"

    supervisor: Mapped[str] = mapped_column(String(200), primary_key=True)
    yaml: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertRule(Base):
    """A threshold that emits an alert or suspends the agent.

    Attributes:
        id: Rule id.
        name: Unique slug.
        metric: Footprint field compared with `threshold`.
        threshold: Value at or above which the rule fires.
        window_seconds: How far back the footprint is measured.
        labels: Labels an agent must carry. Empty matches every agent.
        autonomy: Autonomy an agent must have. `None` matches every level.
        action: `webhook` or `suspend`.
        created_at: When the rule was stored.
    """

    __tablename__ = "alert_rules"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(String(63), unique=True)
    metric: Mapped[str] = mapped_column(String(32))
    threshold: Mapped[float] = mapped_column(Float)
    window_seconds: Mapped[int] = mapped_column(Integer)
    labels: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    autonomy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertFiring(Base):
    """One time a rule fired for one agent.

    Attributes:
        id: Firing id.
        rule_id: Rule that fired.
        agent_id: Agent whose footprint crossed the threshold.
        fired_at: When the alert was sent.
    """

    __tablename__ = "alert_firings"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    rule_id: Mapped[str] = mapped_column(ForeignKey("alert_rules.id"))
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WebhookEndpoint(Base):
    """An outbound webhook the server POSTs when an event happens.

    Attributes:
        id: Endpoint id.
        url: HTTP or HTTPS URL that receives the JSON body.
        events: Event names to deliver. Empty means every event.
        secret: Signing secret for this endpoint. `None` uses the server secret.
        active: Whether delivery is enabled.
        created_at: When the endpoint was stored.
    """

    __tablename__ = "webhook_endpoints"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    url: Mapped[str] = mapped_column(String(2000))
    events: Mapped[list[str]] = mapped_column(JSON, default=list)
    secret: Mapped[str | None] = mapped_column(String(200), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


__all__ = [
    "AdminKey",
    "Agent",
    "AlertFiring",
    "AlertRule",
    "Approval",
    "AuditEntry",
    "Base",
    "Baseline",
    "Event",
    "Instance",
    "IssuedToken",
    "Owner",
    "Policy",
    "ProviderSecret",
    "QuotaCounter",
    "QuotaLease",
    "RegistrationToken",
    "Revocation",
    "Run",
    "RunEffect",
    "RunParticipant",
    "SigningKey",
    "SupervisorGrant",
    "TokenCounter",
    "TokenLease",
    "WebhookEndpoint",
    "utcnow",
]
