"""Fetch, verify, and cache signed policy bundles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import httpx

from agent_tessera.bundle import BundleClaims, bundle_policy, decode_bundle
from agent_tessera.crypto import load_jwks
from agent_tessera.errors import PolicyError, ServerError
from agent_tessera.policy.effective import EffectivePolicy
from agent_tessera.remote.http import request

_MODE = 0o600


@dataclass(frozen=True)
class CachedBundle:
    """A verified bundle and the policy rebuilt from it.

    Attributes:
        token: Compact JWT.
        claims: Verified claims.
        policy: Effective policy.
        etag: Validator to send as `If-None-Match`.
        jwks_text: JWKS document that verified the bundle.
    """

    token: str
    claims: BundleClaims
    policy: EffectivePolicy
    etag: str
    jwks_text: str


def fetch_jwks(client: httpx.Client) -> str:
    """Download the server's JWKS document.

    Args:
        client: Open HTTP client.

    Returns:
        The raw JSON document.

    Raises:
        ServerError: If the server rejects the request.
        RemoteError: If the server cannot be reached.
    """
    response = request(client, "GET", "/.well-known/jwks.json")
    if response.status_code != httpx.codes.OK:
        raise ServerError(f"server returned {response.status_code}")
    return response.text


def fetch_bundle(
    client: httpx.Client,
    *,
    access_token: str,
    agent_id: str,
    jwks_text: str,
    issuer: str,
    etag: str | None = None,
) -> CachedBundle | None:
    """Download and verify the agent's bundle.

    Args:
        client: Open HTTP client.
        access_token: Runtime JWT.
        agent_id: Server id of the agent.
        jwks_text: JWKS document used to verify the signature.
        issuer: Expected bundle issuer.
        etag: Current version. A matching `If-None-Match` returns `None`.

    Returns:
        The verified bundle, or `None` when the server reports no change.

    Raises:
        ServerError: If the server rejects the request or the bundle is invalid.
        RemoteError: If the server cannot be reached.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    if etag is not None:
        headers["If-None-Match"] = etag
    response = request(client, "GET", f"/v1/bundles/{agent_id}", headers=headers)
    if response.status_code == httpx.codes.NOT_MODIFIED:
        return None
    if response.status_code != httpx.codes.OK:
        raise ServerError(f"server returned {response.status_code}")
    validator = response.headers.get("etag", "").strip('"')
    return _verify(response.text, jwks_text, issuer, validator or None)


def read_cache(cache_dir: Path, agent: str, issuer: str) -> CachedBundle | None:
    """Load a cached bundle when it still verifies.

    Args:
        cache_dir: Directory written by `write_cache`.
        agent: Agent slug.
        issuer: Expected bundle issuer.

    Returns:
        The cached bundle, or `None` when it is missing, tampered, or expired.
    """
    bundle_path = cache_dir / f"{agent}.bundle.jwt"
    jwks_path = cache_dir / "jwks.json"
    if not bundle_path.is_file() or not jwks_path.is_file():
        return None
    try:
        return _verify(
            bundle_path.read_text(encoding="utf-8"),
            jwks_path.read_text(encoding="utf-8"),
            issuer,
            None,
        )
    except (OSError, PolicyError, ServerError, ValueError, json.JSONDecodeError):
        return None


def write_cache(cache_dir: Path, agent: str, bundle: CachedBundle) -> None:
    """Store a bundle and the JWKS that verified it.

    Args:
        cache_dir: Cache directory. It is created if needed.
        agent: Agent slug.
        bundle: Verified bundle.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    _write(cache_dir / f"{agent}.bundle.jwt", bundle.token)
    _write(cache_dir / "jwks.json", bundle.jwks_text)


def _verify(token: str, jwks_text: str, issuer: str, etag: str | None) -> CachedBundle:
    """Verify a bundle against a JWKS document.

    Args:
        token: Compact JWT.
        jwks_text: JWKS JSON.
        issuer: Expected issuer.
        etag: Version validator. Defaults to the bundle version.

    Returns:
        The verified bundle.

    Raises:
        ServerError: If the JWKS or the bundle is invalid.
    """
    try:
        document = json.loads(jwks_text)
        if not isinstance(document, dict):
            raise ServerError("server returned an invalid JWKS")
        keys = load_jwks(document)
        claims = decode_bundle(token, keys, issuer=issuer)
    except (PolicyError, ValueError, json.JSONDecodeError) as err:
        raise ServerError("server returned an invalid bundle") from err
    return CachedBundle(token, claims, bundle_policy(claims), etag or claims.version, jwks_text)


def _write(path: Path, text: str) -> None:
    """Write a cache file that only the owner can read.

    Args:
        path: Destination.
        text: File contents.
    """
    path.write_text(text, encoding="utf-8")
    path.chmod(_MODE)


__all__ = ["CachedBundle", "fetch_bundle", "fetch_jwks", "read_cache", "write_cache"]
