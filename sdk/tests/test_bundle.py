"""Tests for signed policy bundles."""

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from agent_tessera.bundle import (
    BundleClaims,
    PeerAllowSet,
    bundle_policy,
    decode_bundle,
    encode_bundle,
)
from agent_tessera.crypto import generate_signing_key, load_jwks, public_jwk, signing_key_id
from agent_tessera.errors import PolicyError
from agent_tessera.policy.effective import build_effective_policy
from agent_tessera.policy.models import AgentPolicy, BaselinePolicy
from agent_tessera.types import ActionKind

ISSUER = "http://127.0.0.1:8700"


def _agent() -> AgentPolicy:
    return AgentPolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "AgentPolicy",
            "metadata": {"agent": "bot", "environment": "prod", "owner": "team"},
            "spec": {"tools": {"allow": ["files.read"]}},
        }
    )


def _baseline() -> BaselinePolicy:
    return BaselinePolicy.model_validate(
        {
            "apiVersion": "tessera/v1",
            "kind": "BaselinePolicy",
            "metadata": {"name": "floor"},
            "spec": {"selector": {"environment": "prod"}, "tools": {"deny": ["shell.*"]}},
        }
    )


def _claims(
    private: Ed25519PrivateKey | None = None,
    *,
    version: str | None = None,
    exp: int = 2_000,
) -> tuple[BundleClaims, Ed25519PrivateKey, str]:
    key = private or generate_signing_key()
    agent = _agent()
    baseline = _baseline()
    effective = build_effective_policy(agent, [baseline])
    payload = {
        "iss": ISSUER,
        "aud": "tessera-bundle",
        "iat": 1_000,
        "exp": exp,
        "schema": "tessera.bundle/v1",
        "agent_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "agent": agent.metadata.agent,
        "environment": agent.metadata.environment,
        "status": "active",
        "version": effective.version if version is None else version,
        "policy": agent.model_dump(mode="json", by_alias=True),
        "baselines": [baseline.model_dump(mode="json", by_alias=True)],
    }
    claims = BundleClaims.model_validate(payload)
    return claims, key, signing_key_id(key.public_key())


def _jwks(key: Ed25519PrivateKey) -> dict[str, Ed25519PublicKey]:
    public = key.public_key()
    kid = signing_key_id(public)
    return load_jwks({"keys": [public_jwk(public, kid)]})


def _resign(claims: BundleClaims, key: Ed25519PrivateKey, kid: str, **changes: object) -> str:
    payload = claims.model_dump(mode="json", by_alias=True)
    payload.update(changes)
    token = jwt.encode(payload, key, algorithm="EdDSA", headers={"kid": kid})
    return token if isinstance(token, str) else token.decode("ascii")


def test_bundle_round_trip_rebuilds_the_same_version() -> None:
    claims, key, kid = _claims()
    token = encode_bundle(claims, key, kid)
    decoded = decode_bundle(token, _jwks(key), issuer=ISSUER, now=1_500)
    assert decoded == claims
    rebuilt = bundle_policy(decoded)
    assert rebuilt.version == claims.version
    assert rebuilt.agent == "bot"
    assert rebuilt.applied_baselines == ("floor",)


def test_decode_rejects_each_failure() -> None:
    claims, key, kid = _claims()
    token = encode_bundle(claims, key, kid)
    keys = _jwks(key)

    other = generate_signing_key()
    other_kid = signing_key_id(other.public_key())
    foreign = encode_bundle(claims, other, other_kid)
    with pytest.raises(PolicyError, match="unknown kid"):
        decode_bundle(foreign, keys, issuer=ISSUER, now=1_500)

    resigned = encode_bundle(claims, other, kid)
    with pytest.raises(PolicyError, match="bad signature"):
        decode_bundle(resigned, keys, issuer=ISSUER, now=1_500)

    with pytest.raises(PolicyError, match="wrong issuer"):
        decode_bundle(token, keys, issuer="http://other", now=1_500)

    with pytest.raises(PolicyError, match="wrong audience"):
        decode_bundle(_resign(claims, key, kid, aud="other"), keys, issuer=ISSUER, now=1_500)

    with pytest.raises(PolicyError, match="expired"):
        decode_bundle(token, keys, issuer=ISSUER, now=2_000)

    with pytest.raises(PolicyError, match="schema mismatch"):
        decode_bundle(
            _resign(claims, key, kid, schema="tessera.bundle/v0"),
            keys,
            issuer=ISSUER,
            now=1_500,
        )

    mismatched, mismatch_key, mismatch_kid = _claims(version="sha256:" + "ab" * 32)
    with pytest.raises(PolicyError, match="version mismatch"):
        decode_bundle(
            encode_bundle(mismatched, mismatch_key, mismatch_kid),
            _jwks(mismatch_key),
            issuer=ISSUER,
            now=1_500,
        )

    with pytest.raises(PolicyError, match="invalid bundle"):
        decode_bundle("not-a-jwt", keys, issuer=ISSUER, now=1_500)


def test_decode_uses_the_system_clock_and_wraps_payload_errors() -> None:
    claims, key, kid = _claims(exp=4_102_444_800)
    token = encode_bundle(claims, key, kid)
    assert decode_bundle(token, _jwks(key), issuer=ISSUER).agent == "bot"

    missing_exp = claims.model_dump(mode="json", by_alias=True)
    del missing_exp["exp"]
    unsigned = jwt.encode(missing_exp, key, algorithm="EdDSA", headers={"kid": kid})
    with pytest.raises(PolicyError, match="invalid bundle"):
        decode_bundle(unsigned, _jwks(key), issuer=ISSUER, now=1_500)

    with pytest.raises(PolicyError, match="expected an AgentPolicy"):
        decode_bundle(
            _resign(claims, key, kid, policy=claims.baselines[0]),
            _jwks(key),
            issuer=ISSUER,
            now=1_500,
        )


def test_bundle_policy_rejects_a_baseline_in_the_policy_slot() -> None:
    claims, _, _ = _claims()
    swapped = claims.model_copy(
        update={"policy": claims.baselines[0], "baselines": [claims.policy]}
    )
    with pytest.raises(PolicyError, match="expected an AgentPolicy"):
        bundle_policy(swapped)
    agent_as_baseline = claims.model_copy(update={"baselines": [claims.policy]})
    with pytest.raises(PolicyError, match="expected a BaselinePolicy"):
        bundle_policy(agent_as_baseline)


def test_bundle_peers_are_grants_on_the_effective_policy() -> None:
    claims, key, kid = _claims()
    issued = claims.model_copy(
        update={"peers": {"orchestrator": PeerAllowSet(tool=("zendesk.read",))}}
    )
    decoded = decode_bundle(
        encode_bundle(issued, key, kid),
        _jwks(key),
        issuer=ISSUER,
        now=1_500,
    )

    policy = bundle_policy(decoded)

    assert policy.version == claims.version
    assert policy.peer_allows["orchestrator"][ActionKind.TOOL] == ("zendesk.read",)
    assert bundle_policy(claims).peer_allows == {}


def test_pyjwt_header_uses_eddsa() -> None:
    claims, key, kid = _claims()
    header = jwt.get_unverified_header(encode_bundle(claims, key, kid))
    assert header["alg"] == "EdDSA"
    assert header["kid"] == kid
