"""Signed policy bundles exchanged with the control plane."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import Literal

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_tessera.errors import PolicyError
from agent_tessera.policy.effective import EffectivePolicy, build_effective_policy
from agent_tessera.policy.loader import parse_policy
from agent_tessera.policy.models import AgentPolicy, BaselinePolicy
from agent_tessera.types import ActionKind

BUNDLE_AUDIENCE = "tessera-bundle"
BUNDLE_SCHEMA = "tessera.bundle/v1"


class PeerAllowSet(BaseModel):
    """Allow globs for one other agent, carried in a policy bundle.

    Attributes:
        model: Model names this agent may call.
        tool: Tool names this agent may call.
        data: Data scopes this agent may use.
        agent: Agents this agent may invoke.

    Example:
        ```python
        grants = PeerAllowSet(tool=("zendesk.read",))
        assert grants.model == ()
        ```
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: tuple[str, ...] = ()
    tool: tuple[str, ...] = ()
    data: tuple[str, ...] = ()
    agent: tuple[str, ...] = ()


class SupervisorAllowSet(BaseModel):
    """Model and tool globs from one supervisor grant document.

    Attributes:
        model: Model allow globs.
        tool: Tool allow globs.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: tuple[str, ...] = ()
    tool: tuple[str, ...] = ()


class BundleClaims(BaseModel):
    """Claims carried inside a signed policy bundle.

    Attributes:
        iss: Server that signed the bundle.
        aud: Always `tessera-bundle`.
        iat: Issued-at time as a Unix timestamp.
        exp: Expiry time as a Unix timestamp.
        schema_: Bundle schema name. Serialized as `schema`.
        agent_id: Server id of the agent.
        agent: Agent slug.
        environment: Environment slug.
        status: Whether the agent is active or suspended.
        version: Effective-policy version, `sha256:` plus a hex digest.
        policy: Agent policy document.
        baselines: Baseline documents stored for this server, sorted by name.
        peers: Allow sets for other agents, keyed by agent name. Empty when
            this bundle does not carry chain grants.
        supervisor_grants: Allow sets keyed by supervisor id. Empty when this
            bundle does not cap calls to a supervisor.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    iss: str
    aud: Literal["tessera-bundle"]
    iat: int
    exp: int
    schema_: Literal["tessera.bundle/v1"] = Field(alias="schema")
    agent_id: str
    agent: str
    environment: str
    status: Literal["active", "suspended"]
    version: str
    policy: dict[str, object]
    baselines: list[dict[str, object]]
    peers: dict[str, PeerAllowSet] = Field(default_factory=dict)
    supervisor_grants: dict[str, SupervisorAllowSet] = Field(default_factory=dict)


def encode_bundle(claims: BundleClaims, key: Ed25519PrivateKey, kid: str) -> str:
    """Sign a policy bundle as a compact JWT.

    Args:
        claims: Bundle contents.
        key: Ed25519 private key.
        kid: Key id written into the JWT header.

    Returns:
        A compact JWS using EdDSA.
    """
    payload = claims.model_dump(mode="json", by_alias=True)
    token = jwt.encode(payload, key, algorithm="EdDSA", headers={"kid": kid})
    return token if isinstance(token, str) else token.decode("ascii")


def decode_bundle(
    token: str,
    jwks: Mapping[str, Ed25519PublicKey],
    *,
    issuer: str,
    now: int | None = None,
) -> BundleClaims:
    """Verify a policy bundle and return its claims.

    Args:
        token: Compact JWT.
        jwks: Public keys keyed by `kid`.
        issuer: Expected `iss` claim.
        now: Current Unix time. Defaults to the system clock.

    Returns:
        The verified claims. The embedded version matches the policy rebuilt
        from the payload.

    Raises:
        PolicyError: If the key is unknown, the signature or claims are wrong,
            the bundle is expired, or the version does not match the payload.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as err:
        raise PolicyError(f"invalid bundle: {err}") from err
    kid = header.get("kid")
    if not isinstance(kid, str) or kid not in jwks:
        raise PolicyError("invalid bundle: unknown kid")
    try:
        payload = jwt.decode(
            token,
            jwks[kid],
            algorithms=["EdDSA"],
            audience=BUNDLE_AUDIENCE,
            issuer=issuer,
            options={"verify_exp": False, "require": ["exp", "iat"]},
        )
    except jwt.InvalidSignatureError as err:
        raise PolicyError("invalid bundle: bad signature") from err
    except jwt.InvalidIssuerError as err:
        raise PolicyError("invalid bundle: wrong issuer") from err
    except jwt.InvalidAudienceError as err:
        raise PolicyError("invalid bundle: wrong audience") from err
    except jwt.InvalidTokenError as err:
        raise PolicyError(f"invalid bundle: {err}") from err
    current = int(time.time()) if now is None else now
    exp = payload.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, int) or current >= exp:
        raise PolicyError("invalid bundle: expired")
    try:
        claims = BundleClaims.model_validate(payload)
    except ValidationError as err:
        raise PolicyError("invalid bundle: schema mismatch") from err
    try:
        rebuilt = bundle_policy(claims)
    except PolicyError as err:
        raise PolicyError(f"invalid bundle: {err}") from err
    if rebuilt.version != claims.version:
        raise PolicyError("invalid bundle: version mismatch")
    return claims


def bundle_policy(claims: BundleClaims) -> EffectivePolicy:
    """Rebuild the effective policy from a bundle payload.

    Args:
        claims: Verified or locally constructed bundle claims.

    Returns:
        The effective policy produced from the embedded documents.

    Raises:
        PolicyError: If `policy` is not an agent policy or a baseline document
            is not a baseline.
    """
    agent = parse_policy(claims.policy)
    if not isinstance(agent, AgentPolicy):
        raise PolicyError("expected an AgentPolicy")
    baselines: list[BaselinePolicy] = []
    for document in claims.baselines:
        baseline = parse_policy(document)
        if not isinstance(baseline, BaselinePolicy):
            raise PolicyError("expected a BaselinePolicy")
        baselines.append(baseline)
    effective = build_effective_policy(agent, baselines)
    if not claims.peers and not claims.supervisor_grants:
        return effective
    peer_allows = _peer_allows(claims.peers) if claims.peers else effective.peer_allows
    grants = (
        _supervisor_grants(claims.supervisor_grants)
        if claims.supervisor_grants
        else effective.supervisor_grants
    )
    return replace(effective, peer_allows=peer_allows, supervisor_grants=grants)


def _peer_allows(
    peers: Mapping[str, PeerAllowSet],
) -> Mapping[str, Mapping[ActionKind, tuple[str, ...]]]:
    """Index bundle peer grants by action kind.

    Args:
        peers: Allow sets keyed by agent name.

    Returns:
        The same grants, keyed by agent name and then action kind.
    """
    indexed: dict[str, Mapping[ActionKind, tuple[str, ...]]] = {}
    for name, grants in peers.items():
        indexed[name] = MappingProxyType(
            {
                ActionKind.MODEL: grants.model,
                ActionKind.TOOL: grants.tool,
                ActionKind.DATA: grants.data,
                ActionKind.AGENT: grants.agent,
            }
        )
    return MappingProxyType(indexed)


def _supervisor_grants(
    grants: Mapping[str, SupervisorAllowSet],
) -> Mapping[str, Mapping[ActionKind, tuple[str, ...]]]:
    """Index supervisor grants by action kind.

    Args:
        grants: Allow sets keyed by supervisor id.

    Returns:
        The same grants, keyed by supervisor id and then action kind.
        Data and agent kinds stay empty.
    """
    indexed: dict[str, Mapping[ActionKind, tuple[str, ...]]] = {}
    for name, item in grants.items():
        indexed[name] = MappingProxyType(
            {
                ActionKind.MODEL: item.model,
                ActionKind.TOOL: item.tool,
                ActionKind.DATA: (),
                ActionKind.AGENT: (),
            }
        )
    return MappingProxyType(indexed)


__all__ = [
    "BUNDLE_AUDIENCE",
    "BUNDLE_SCHEMA",
    "BundleClaims",
    "PeerAllowSet",
    "SupervisorAllowSet",
    "bundle_policy",
    "decode_bundle",
    "encode_bundle",
]
