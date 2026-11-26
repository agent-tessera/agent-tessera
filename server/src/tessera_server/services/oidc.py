"""Admin OpenID Connect login."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlencode

import httpx
import jwt
from jwt.exceptions import InvalidTokenError

from agent_tessera.errors import TesseraError
from tessera_server.config import Settings

_HTTP_TIMEOUT = 10.0
_OK = 200
_SUB_LIMIT = 240
_JWT_DOTS = 2
_SINGLE_KEY = 1
_ALGORITHMS = ["RS256", "ES256", "EdDSA"]


class OidcLoginError(TesseraError):
    """The admin SSO exchange did not complete."""


class OidcGroupError(OidcLoginError):
    """The account is not in the configured admin group."""


@dataclass(frozen=True, slots=True)
class OidcIdentity:
    """Verified administrator from an identity provider.

    Attributes:
        issuer: Issuer URL of the provider.
        sub: Subject claim. This becomes the admin session.
    """

    issuer: str
    sub: str


def oidc_configured(settings: Settings) -> bool:
    """Report whether admin SSO has an issuer, client id, and secret.

    Args:
        settings: Process settings.

    Returns:
        `True` when the three values are set.
    """
    return bool(settings.oidc_issuer and settings.oidc_client_id and settings.oidc_client_secret)


def callback_url(settings: Settings) -> str:
    """Return the redirect URL registered with the identity provider.

    Args:
        settings: Process settings. A blank redirect uses the server issuer.

    Returns:
        The callback URL, without a trailing slash on the default.
    """
    if settings.oidc_redirect_url:
        return settings.oidc_redirect_url
    return f"{settings.issuer}/oidc/callback"


def open_client() -> httpx.Client:
    """Open the HTTP client used to talk to the identity provider.

    Returns:
        A client that does not follow redirects. Tests replace this function.
    """
    return httpx.Client(timeout=_HTTP_TIMEOUT)


def authorization_redirect(settings: Settings, state: str, client: httpx.Client) -> str:
    """Build the provider authorization URL.

    Args:
        settings: OIDC client settings.
        state: Unpredictable value stored in the browser and checked later.
        client: HTTP client for discovery.

    Returns:
        The URL the browser should open.

    Raises:
        OidcLoginError: If SSO is not configured or discovery is not usable.

    Example:
        ```python
        url = authorization_redirect(settings, "state", client)
        assert "response_type=code" in url
        ```
    """
    _require(settings)
    endpoint = _text(_discovery(settings, client), "authorization_endpoint")
    query = urlencode(
        {
            "response_type": "code",
            "client_id": _client_id(settings),
            "redirect_uri": callback_url(settings),
            "scope": "openid",
            "state": state,
        }
    )
    return f"{endpoint}?{query}"


def complete_login(settings: Settings, code: str, client: httpx.Client) -> OidcIdentity:
    """Exchange an authorization code and verify the ID token.

    Args:
        settings: OIDC client settings.
        code: Authorization code from the callback.
        client: HTTP client for the token and key endpoints.

    Returns:
        The issuer and subject from the verified token.

    Raises:
        OidcLoginError: If the code, signature, issuer, audience, or subject
            is not acceptable. The message does not include the token.
    """
    _require(settings)
    document = _discovery(settings, client)
    token = _id_token(settings, code, client, _text(document, "token_endpoint"))
    claims = _verify(
        _issuer(settings),
        _client_id(settings),
        token,
        client,
        _text(document, "jwks_uri"),
    )
    _require_group(settings, claims)
    return OidcIdentity(issuer=_issuer(settings), sub=_subject(claims))


def verify_provider_token(
    issuer: str, audience: str, token: str, client: httpx.Client
) -> Mapping[str, object]:
    """Verify a JWT against an issuer's published keys.

    Args:
        issuer: Expected issuer, without a trailing slash.
        audience: Expected audience.
        token: Compact JWT.
        client: HTTP client for discovery and the key set.

    Returns:
        The verified claims.

    Raises:
        OidcLoginError: If discovery or the token is not acceptable.

    Example:
        ```python
        claims = verify_provider_token(issuer, audience, token, client)
        assert claims["sub"]
        ```
    """
    normalized = issuer.rstrip("/")
    document = _json(client, f"{normalized}/.well-known/openid-configuration")
    stated = document.get("issuer")
    if isinstance(stated, str) and stated.rstrip("/") != normalized:
        raise OidcLoginError("identity provider metadata is incomplete")
    return _verify(normalized, audience, token, client, _text(document, "jwks_uri"))


def _require(settings: Settings) -> None:
    """Reject a login when SSO settings are incomplete.

    Args:
        settings: Process settings.

    Raises:
        OidcLoginError: If the issuer, client id, or secret is missing.
    """
    if not oidc_configured(settings):
        raise OidcLoginError("SSO is not configured")


def _discovery(settings: Settings, client: httpx.Client) -> Mapping[str, object]:
    """Load the OpenID discovery document.

    Args:
        settings: Provider issuer.
        client: HTTP client.

    Returns:
        The discovery document.

    Raises:
        OidcLoginError: If the document cannot be read.
    """
    url = f"{_issuer(settings)}/.well-known/openid-configuration"
    return _json(client, url)


def _id_token(settings: Settings, code: str, client: httpx.Client, endpoint: str) -> str:
    """Exchange the code for an ID token.

    Args:
        settings: Client id, secret, and redirect URL.
        code: Authorization code.
        client: HTTP client.
        endpoint: Token endpoint from discovery.

    Returns:
        The compact ID token.

    Raises:
        OidcLoginError: If the provider does not return an ID token.
    """
    try:
        response = client.post(
            endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": callback_url(settings),
                "client_id": _client_id(settings),
                "client_secret": settings.oidc_client_secret or "",
            },
            follow_redirects=False,
        )
    except httpx.HTTPError as err:
        raise OidcLoginError("identity provider is unreachable") from err
    if response.status_code != _OK:
        raise OidcLoginError("identity provider rejected the code")
    body = _object(response)
    token = body.get("id_token")
    if not isinstance(token, str) or token.count(".") != _JWT_DOTS:
        raise OidcLoginError("identity provider did not return an ID token")
    return token


def _verify(
    issuer: str, audience: str, token: str, client: httpx.Client, jwks_uri: str
) -> Mapping[str, object]:
    """Check a JWT signature, issuer, audience, and expiry.

    Args:
        issuer: Expected issuer.
        audience: Expected audience.
        token: Compact JWT.
        client: HTTP client for the key set.
        jwks_uri: Provider key URL.

    Returns:
        The verified claims.

    Raises:
        OidcLoginError: If the token is not valid for this audience.
    """
    header = _header(token)
    signing_key = _key(header, _json(client, jwks_uri))
    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=_ALGORITHMS,
            audience=audience,
            issuer=issuer,
        )
    except InvalidTokenError as err:
        raise OidcLoginError("ID token was rejected") from err
    if not isinstance(claims, dict):
        raise OidcLoginError("ID token was rejected")
    return cast("Mapping[str, object]", claims)


def _header(token: str) -> Mapping[str, object]:
    """Read the unverified JWT header.

    Args:
        token: Compact JWT.

    Returns:
        Header fields.

    Raises:
        OidcLoginError: If the header is not an object.
    """
    try:
        header = jwt.get_unverified_header(token)
    except InvalidTokenError as err:
        raise OidcLoginError("ID token was rejected") from err
    if not isinstance(header, dict):
        raise OidcLoginError("ID token was rejected")
    return cast("Mapping[str, object]", header)


def _key(header: Mapping[str, object], document: Mapping[str, object]) -> jwt.PyJWK:
    """Select the public key named by the token.

    Args:
        header: JWT header. `kid` selects the key when the set has several.
        document: JWKS document.

    Returns:
        The JWK PyJWT can use to check the signature.

    Raises:
        OidcLoginError: If the key is missing.
    """
    try:
        keys = jwt.PyJWKSet.from_dict(dict(document))
    except (InvalidTokenError, ValueError, KeyError) as err:
        raise OidcLoginError("signing key is missing") from err
    kid = header.get("kid")
    try:
        if isinstance(kid, str) and kid:
            return keys[kid]
        if len(keys.keys) == _SINGLE_KEY:
            return keys.keys[0]
    except KeyError as err:
        raise OidcLoginError("signing key is missing") from err
    raise OidcLoginError("signing key is missing")


def _require_group(settings: Settings, claims: Mapping[str, object]) -> None:
    """Require the configured admin group when one is set.

    Args:
        settings: Optional `oidc_admin_group`. An empty value skips the check.
        claims: Verified ID token claims. Membership is read from `groups`.

    Raises:
        OidcGroupError: If `groups` does not contain the configured value.
    """
    required = settings.oidc_admin_group
    if not required:
        return
    groups = claims.get("groups")
    if isinstance(groups, str) and groups == required:
        return
    if isinstance(groups, list) and any(
        item == required for item in groups if isinstance(item, str)
    ):
        return
    raise OidcGroupError("admin group is required")


def _subject(claims: Mapping[str, object]) -> str:
    """Read a subject that is safe to store in a cookie.

    Args:
        claims: Verified ID token claims.

    Returns:
        The subject.

    Raises:
        OidcLoginError: If `sub` is missing or contains cookie delimiters.
    """
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub or len(sub) > _SUB_LIMIT:
        raise OidcLoginError("ID token was rejected")
    if any(character.isspace() or character in ";," for character in sub):
        raise OidcLoginError("ID token was rejected")
    return sub


def _json(client: httpx.Client, url: str) -> Mapping[str, object]:
    """GET a JSON object and refuse redirects.

    Args:
        client: HTTP client.
        url: Absolute URL.

    Returns:
        The JSON object.

    Raises:
        OidcLoginError: If the response is not a JSON object.
    """
    try:
        response = client.get(url, follow_redirects=False)
    except httpx.HTTPError as err:
        raise OidcLoginError("identity provider is unreachable") from err
    if response.status_code != _OK:
        raise OidcLoginError("identity provider metadata is incomplete")
    return _object(response)


def _object(response: httpx.Response) -> Mapping[str, object]:
    """Parse a response body as a JSON object.

    Args:
        response: HTTP response.

    Returns:
        The object.

    Raises:
        OidcLoginError: If the body is not a JSON object.
    """
    try:
        body = response.json()
    except ValueError as err:
        raise OidcLoginError("identity provider metadata is incomplete") from err
    if not isinstance(body, dict):
        raise OidcLoginError("identity provider metadata is incomplete")
    return cast("Mapping[str, object]", body)


def _text(document: Mapping[str, object], field: str) -> str:
    """Read one required string field.

    Args:
        document: JSON object.
        field: Field name.

    Returns:
        The string value.

    Raises:
        OidcLoginError: If the field is missing or blank.
    """
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise OidcLoginError("identity provider metadata is incomplete")
    return value


def _issuer(settings: Settings) -> str:
    """Return the configured issuer.

    Args:
        settings: Process settings. The caller has already checked SSO.

    Returns:
        The issuer URL.
    """
    return settings.oidc_issuer or ""


def _client_id(settings: Settings) -> str:
    """Return the configured client id.

    Args:
        settings: Process settings. The caller has already checked SSO.

    Returns:
        The client id.
    """
    return settings.oidc_client_id or ""


__all__ = [
    "OidcGroupError",
    "OidcIdentity",
    "OidcLoginError",
    "authorization_redirect",
    "callback_url",
    "complete_login",
    "oidc_configured",
    "open_client",
    "verify_provider_token",
]
