"""Tests for Ed25519 keys and JWKS loading."""

import hashlib

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from agent_tessera.crypto import generate_signing_key, load_jwks, public_jwk, signing_key_id


def test_jwk_round_trip() -> None:
    private = generate_signing_key()
    public = private.public_key()
    kid = signing_key_id(public)
    assert len(kid) == 16
    document = {"keys": [public_jwk(public, kid), {"kty": "RSA", "kid": "other"}]}
    loaded = load_jwks(document)
    assert set(loaded) == {kid}
    assert loaded[kid].public_bytes_raw() == public.public_bytes_raw()


def test_signing_key_id_is_sha256_prefix() -> None:
    public = generate_signing_key().public_key()
    digest = hashlib.sha256(public.public_bytes_raw()).hexdigest()
    assert signing_key_id(public) == digest[:16]


def test_load_jwks_rejects_malformed_documents() -> None:
    with pytest.raises(ValueError, match="keys must be a list"):
        load_jwks({})
    with pytest.raises(ValueError, match="must be an object"):
        load_jwks({"keys": ["nope"]})
    with pytest.raises(ValueError, match="requires kid and x"):
        load_jwks({"keys": [{"kty": "OKP", "crv": "Ed25519", "x": "abc"}]})
    with pytest.raises(ValueError, match="not base64url"):
        load_jwks({"keys": [{"kty": "OKP", "crv": "Ed25519", "kid": "a", "x": "****"}]})
    short = public_jwk(generate_signing_key().public_key(), "a")
    short["x"] = "AAAA"
    with pytest.raises(ValueError, match="32 bytes"):
        load_jwks({"keys": [short]})


def test_load_jwks_rejects_duplicate_kids() -> None:
    jwk = public_jwk(generate_signing_key().public_key(), "same")
    with pytest.raises(ValueError, match="duplicate kid"):
        load_jwks({"keys": [jwk, jwk]})


def test_loaded_key_can_verify_a_signature() -> None:
    private = generate_signing_key()
    kid = signing_key_id(private.public_key())
    loaded = load_jwks({"keys": [public_jwk(private.public_key(), kid)]})
    signature = private.sign(b"bundle")
    loaded[kid].verify(signature, b"bundle")
    with pytest.raises(InvalidSignature):
        loaded[kid].verify(signature, b"other")


def test_public_key_type_is_ed25519() -> None:
    assert isinstance(generate_signing_key().public_key(), Ed25519PublicKey)
