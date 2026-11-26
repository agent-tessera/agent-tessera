"""Ed25519 signing keys and their public JWK form."""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

_RAW_LENGTH = 32


def generate_signing_key() -> Ed25519PrivateKey:
    """Create a new Ed25519 signing key.

    Returns:
        A freshly generated private key. The matching public key is available
        from `key.public_key()`.

    Example:
        ```python
        from agent_tessera.crypto import generate_signing_key, public_jwk

        key = generate_signing_key()
        jwk = public_jwk(key.public_key(), "kid")
        assert jwk["kty"] == "OKP"
        ```
    """
    return Ed25519PrivateKey.generate()


def public_jwk(key: Ed25519PublicKey, kid: str) -> dict[str, str]:
    """Describe an Ed25519 public key as a JWK.

    Args:
        key: Public key to publish.
        kid: Key id placed in the JWK and in JWT headers.

    Returns:
        An OKP JWK with the raw public key in unpadded base64url.
    """
    raw = key.public_bytes_raw()
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "x": _b64url(raw),
        "kid": kid,
        "alg": "EdDSA",
        "use": "sig",
    }


def load_jwks(document: Mapping[str, object]) -> dict[str, Ed25519PublicKey]:
    """Load Ed25519 public keys from a JWKS document.

    Args:
        document: A JWKS object with a `keys` list.

    Returns:
        Public keys keyed by `kid`. Entries that are not OKP Ed25519 keys are
        skipped.

    Raises:
        ValueError: If the document or an Ed25519 entry is malformed.
    """
    keys = document.get("keys")
    if not isinstance(keys, list):
        raise ValueError("jwks keys must be a list")
    loaded: dict[str, Ed25519PublicKey] = {}
    for entry in keys:
        if not isinstance(entry, Mapping):
            raise ValueError("jwks key must be an object")
        if entry.get("kty") != "OKP" or entry.get("crv") != "Ed25519":
            continue
        kid = entry.get("kid")
        raw_x = entry.get("x")
        if not isinstance(kid, str) or kid == "" or not isinstance(raw_x, str):
            raise ValueError("jwks ed25519 key requires kid and x")
        if kid in loaded:
            raise ValueError(f"duplicate kid {kid}")
        try:
            raw = _b64url_decode(raw_x)
        except ValueError as err:
            raise ValueError("jwks x is not base64url") from err
        if len(raw) != _RAW_LENGTH:
            raise ValueError("jwks x must be 32 bytes")
        loaded[kid] = Ed25519PublicKey.from_public_bytes(raw)
    return loaded


def signing_key_id(public_key: Ed25519PublicKey) -> str:
    """Return the key id for an Ed25519 public key.

    Args:
        public_key: Public half of a signing key.

    Returns:
        The first 16 hex characters of the SHA-256 digest of the raw public
        key bytes.
    """
    raw = public_key.public_bytes_raw()
    return hashlib.sha256(raw).hexdigest()[:16]


def _b64url(raw: bytes) -> str:
    """Encode bytes as unpadded base64url.

    Args:
        raw: Bytes to encode.

    Returns:
        An ASCII base64url string without padding.
    """
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    """Decode unpadded base64url.

    Args:
        value: Base64url text, with or without padding.

    Returns:
        The decoded bytes.

    Raises:
        ValueError: If `value` is not valid base64url.
    """
    padding = "=" * (-len(value) % 4)
    try:
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as err:
        raise ValueError("invalid base64url") from err


__all__ = ["generate_signing_key", "load_jwks", "public_jwk", "signing_key_id"]
