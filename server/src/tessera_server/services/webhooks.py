"""Store webhook endpoints and deliver signed JSON events."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime
from http.client import HTTPMessage
from typing import IO, NoReturn

from sqlalchemy import select
from sqlalchemy.orm import Session

from tessera_server.api.errors import ApiError
from tessera_server.config import Settings
from tessera_server.db.models import WebhookEndpoint, utcnow
from tessera_server.ids import new_ulid
from tessera_server.services.audit import append_audit

log = logging.getLogger(__name__)

_ATTEMPTS = 3
_BACKOFF_SECONDS = (0.01, 0.02)
_HTTP_TIMEOUT = 5
_URL_LIMIT = 2000
_SECRET_LIMIT = 200
_EVENT_LIMIT = 64
_EVENT_COUNT = 20
_SUCCESS = range(200, 300)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to follow a webhook redirect."""

    def http_error_301(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
    ) -> urllib.request.Request | None:
        """Refuse a 301 redirect.

        Args:
            req: Original request.
            fp: Response body.
            code: Redirect status.
            msg: Reason phrase.
            headers: Response headers.

        Returns:
            Never. The method raises.

        Raises:
            urllib.error.HTTPError: Always, with the redirect status.
        """
        _refuse_redirect(req, fp, code, msg, headers)

    def http_error_302(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
    ) -> urllib.request.Request | None:
        """Refuse a 302 redirect.

        Args:
            req: Original request.
            fp: Response body.
            code: Redirect status.
            msg: Reason phrase.
            headers: Response headers.

        Returns:
            Never. The method raises.

        Raises:
            urllib.error.HTTPError: Always, with the redirect status.
        """
        _refuse_redirect(req, fp, code, msg, headers)

    def http_error_303(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
    ) -> urllib.request.Request | None:
        """Refuse a 303 redirect.

        Args:
            req: Original request.
            fp: Response body.
            code: Redirect status.
            msg: Reason phrase.
            headers: Response headers.

        Returns:
            Never. The method raises.

        Raises:
            urllib.error.HTTPError: Always, with the redirect status.
        """
        _refuse_redirect(req, fp, code, msg, headers)

    def http_error_307(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
    ) -> urllib.request.Request | None:
        """Refuse a 307 redirect.

        Args:
            req: Original request.
            fp: Response body.
            code: Redirect status.
            msg: Reason phrase.
            headers: Response headers.

        Returns:
            Never. The method raises.

        Raises:
            urllib.error.HTTPError: Always, with the redirect status.
        """
        _refuse_redirect(req, fp, code, msg, headers)

    def http_error_308(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
    ) -> urllib.request.Request | None:
        """Refuse a 308 redirect.

        Args:
            req: Original request.
            fp: Response body.
            code: Redirect status.
            msg: Reason phrase.
            headers: Response headers.

        Returns:
            Never. The method raises.

        Raises:
            urllib.error.HTTPError: Always, with the redirect status.
        """
        _refuse_redirect(req, fp, code, msg, headers)


def _refuse_redirect(
    req: urllib.request.Request,
    fp: IO[bytes],
    code: int,
    msg: str,
    headers: HTTPMessage,
) -> NoReturn:
    """Raise the redirect status so the body is not sent to another URL.

    Args:
        req: Original request.
        fp: Response body.
        code: Redirect status.
        msg: Reason phrase.
        headers: Response headers.

    Raises:
        urllib.error.HTTPError: Always, with the redirect status.
    """
    raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def ensure_catch_all(session: Session, settings: Settings) -> None:
    """Register the environment webhook when it is not already stored.

    Args:
        session: Open database session. A new row is committed.
        settings: `webhook_url` selects the endpoint. An empty value does
            nothing.

    Example:
        ```python
        ensure_catch_all(session, settings)
        ```
    """
    url = settings.webhook_url
    if not url:
        return
    checked = _url(url)
    found = session.scalars(select(WebhookEndpoint).where(WebhookEndpoint.url == checked)).first()
    if found is not None:
        return
    session.add(
        WebhookEndpoint(
            id=new_ulid(),
            url=checked,
            events=[],
            secret=None,
            active=True,
            created_at=utcnow(),
        )
    )
    session.commit()


def create_webhook(
    session: Session,
    *,
    url: str,
    events: list[str],
    secret: str | None,
    active: bool,
    now: datetime | None = None,
) -> dict[str, object]:
    """Store a webhook endpoint.

    Args:
        session: Open database session.
        url: HTTP or HTTPS URL.
        events: Event names. Empty delivers every event.
        secret: Signing secret, or `None` to use the server secret.
        active: Whether delivery is enabled.
        now: Clock for `created_at`.

    Returns:
        The endpoint document. The secret itself is not included.

    Raises:
        ApiError: Status 422 when the URL, events, or secret is not usable.

    Example:
        ```python
        stored = create_webhook(
            session, url="https://hooks.example/tessera", events=[], secret=None, active=True
        )
        assert stored["has_secret"] is False
        ```
    """
    row = WebhookEndpoint(
        id=new_ulid(),
        url=_url(url),
        events=_events(events),
        secret=_secret(secret),
        active=active,
        created_at=now or utcnow(),
    )
    session.add(row)
    session.commit()
    return _document(row)


def list_webhooks(session: Session) -> dict[str, object]:
    """List webhook endpoints.

    Args:
        session: Open database session.

    Returns:
        `items`, oldest first.

    Example:
        ```python
        page = list_webhooks(session)
        assert "items" in page
        ```
    """
    rows = session.scalars(select(WebhookEndpoint).order_by(WebhookEndpoint.created_at)).all()
    return {"items": [_document(row) for row in rows]}


def get_webhook(session: Session, webhook_id: str) -> dict[str, object]:
    """Return one webhook endpoint.

    Args:
        session: Open database session.
        webhook_id: Endpoint to read.

    Returns:
        The endpoint document.

    Raises:
        ApiError: Status 404 when it does not exist.

    Example:
        ```python
        document = get_webhook(session, webhook_id)
        assert document["id"] == webhook_id
        ```
    """
    return _document(_require(session, webhook_id))


def update_webhook(
    session: Session,
    webhook_id: str,
    *,
    url: str | None = None,
    events: list[str] | None = None,
    secret: str | None = None,
    active: bool | None = None,
    clear_secret: bool = False,
) -> dict[str, object]:
    """Change a webhook endpoint.

    Args:
        session: Open database session.
        webhook_id: Endpoint to change.
        url: New URL, or `None` to keep the current one.
        events: New event list, or `None` to keep the current one.
        secret: New signing secret. Ignored when `clear_secret` is set.
        active: New active flag, or `None` to keep the current one.
        clear_secret: Drop the endpoint secret so delivery uses the server secret.

    Returns:
        The updated document.

    Raises:
        ApiError: Status 404 when it does not exist, or 422 when a new value
            is not usable.

    Example:
        ```python
        updated = update_webhook(session, webhook_id, active=False)
        assert updated["active"] is False
        ```
    """
    row = _require(session, webhook_id)
    if url is not None:
        row.url = _url(url)
    if events is not None:
        row.events = _events(events)
    if clear_secret:
        row.secret = None
    elif secret is not None:
        row.secret = _secret(secret)
    if active is not None:
        row.active = active
    session.commit()
    return _document(row)


def delete_webhook(session: Session, webhook_id: str) -> dict[str, object]:
    """Delete a webhook endpoint.

    Args:
        session: Open database session.
        webhook_id: Endpoint to delete.

    Returns:
        The deleted id.

    Raises:
        ApiError: Status 404 when it does not exist.

    Example:
        ```python
        removed = delete_webhook(session, webhook_id)
        assert removed["id"] == webhook_id
        ```
    """
    row = _require(session, webhook_id)
    session.delete(row)
    session.commit()
    return {"id": webhook_id}


def deliver(
    session: Session,
    settings: Settings,
    event: str,
    payload: Mapping[str, object],
) -> None:
    """POST one event to every matching endpoint.

    Args:
        session: Open database session. A failed delivery is audited here.
        settings: Server webhook secret used when an endpoint has none.
        event: Event name, such as `approval.requested`.
        payload: JSON object. It is signed exactly as it is sent.

    A delivery tries three times. The wait before the second try is 0.01
    seconds and before the third is 0.02 seconds. A failure is audited and
    does not raise. The signature header is `X-Tessera-Signature: sha256=<hex>`.

    Example:
        ```python
        deliver(session, settings, "approval.requested", {"event": "approval.requested"})
        ```
    """
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    rows = session.scalars(select(WebhookEndpoint).order_by(WebhookEndpoint.created_at)).all()
    for row in rows:
        if not _matches(row, event):
            continue
        _deliver_one(session, settings, row, event, body)


def ping_webhook(
    session: Session,
    settings: Settings,
    webhook_id: str,
) -> dict[str, object]:
    """Deliver a signed `webhook.ping` to one endpoint.

    Args:
        session: Open database session.
        settings: Server webhook secret used when the endpoint has none.
        webhook_id: Endpoint to ping.

    Returns:
        The endpoint document.

    Raises:
        ApiError: Status 404 when the endpoint does not exist.

    Example:
        ```python
        ping_webhook(session, settings, webhook_id)
        ```
    """
    row = _require(session, webhook_id)
    payload: dict[str, object] = {"event": "webhook.ping", "webhook_id": row.id}
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    _deliver_one(session, settings, row, "webhook.ping", body)
    return _document(row)


def _deliver_one(
    session: Session,
    settings: Settings,
    row: WebhookEndpoint,
    event: str,
    body: bytes,
) -> None:
    """POST a signed body to one endpoint.

    Args:
        session: Open database session.
        settings: Server webhook secret fallback.
        row: Endpoint to call.
        event: Event name for failure audit rows.
        body: Exact JSON bytes that will be signed and posted.
    """
    secret = row.secret or settings.webhook_secret
    if not secret:
        _failed(session, row, event, "webhook secret is not configured")
        return
    signature = _sign(secret, body)
    status = _try_post(row.url, body, signature)
    if status is None or status not in _SUCCESS:
        detail = "unreachable" if status is None else f"status {status}"
        _failed(session, row, event, detail)


def _try_post(url: str, body: bytes, signature: str) -> int | None:
    """POST the body until it succeeds or the attempts are used.

    Args:
        url: Endpoint URL.
        body: Exact bytes that were signed.
        signature: `sha256=<hex>` header value.

    Returns:
        The last HTTP status, or `None` when the host could not be reached.
    """
    status: int | None = None
    for attempt in range(_ATTEMPTS):
        status = _post(url, body, signature)
        if status is not None and status in _SUCCESS:
            return status
        if attempt < _ATTEMPTS - 1:
            time.sleep(_BACKOFF_SECONDS[attempt])
    return status


def _post(url: str, body: bytes, signature: str) -> int | None:
    """Send one webhook POST.

    Args:
        url: Endpoint URL.
        body: JSON bytes.
        signature: Signature header value.

    Returns:
        The HTTP status, or `None` when the connection fails.
    """
    request = urllib.request.Request(  # noqa: S310
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Tessera-Signature": signature,
        },
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=_HTTP_TIMEOUT) as response:
            return int(response.status)
    except urllib.error.HTTPError as err:
        return int(err.code)
    except urllib.error.URLError:
        return None


def _sign(secret: str, body: bytes) -> str:
    """Sign a webhook body.

    Args:
        secret: HMAC key.
        body: Exact request bytes.

    Returns:
        `sha256=` plus the hex digest.
    """
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _matches(row: WebhookEndpoint, event: str) -> bool:
    """Return whether an endpoint wants this event.

    Args:
        row: Stored endpoint.
        event: Event name.

    Returns:
        `False` when the endpoint is inactive or lists other events. An empty
        list or `*` matches every event.
    """
    if not row.active:
        return False
    names = [item for item in row.events if isinstance(item, str)]
    if not names or "*" in names:
        return True
    return event in names


def _failed(session: Session, row: WebhookEndpoint, event: str, detail: str) -> None:
    """Audit a delivery that did not succeed.

    Args:
        session: Open database session.
        row: Endpoint that failed.
        event: Event that was not delivered.
        detail: Short reason, without the secret or the response body.
    """
    try:
        append_audit(
            session,
            actor="webhooks",
            entry_type="webhook_failed",
            payload={"id": row.id, "event": event, "error": detail},
        )
    except Exception:
        log.exception("webhook failure audit failed")


def _require(session: Session, webhook_id: str) -> WebhookEndpoint:
    """Load one endpoint.

    Args:
        session: Open database session.
        webhook_id: Endpoint id.

    Returns:
        The row.

    Raises:
        ApiError: Status 404 when it does not exist.
    """
    row = session.get(WebhookEndpoint, webhook_id)
    if row is None:
        raise ApiError(404, "not_found", "webhook not found")
    return row


def _document(row: WebhookEndpoint) -> dict[str, object]:
    """Return the API document for an endpoint.

    Args:
        row: Stored endpoint.

    Returns:
        The public fields. `has_secret` is true when this endpoint stores its
        own signing secret.
    """
    events = [item for item in row.events if isinstance(item, str)]
    return {
        "id": row.id,
        "url": row.url,
        "events": events,
        "active": row.active,
        "has_secret": bool(row.secret),
        "created_at": _iso(row.created_at),
    }


def _iso(moment: datetime) -> str:
    """Format a timestamp as UTC ISO-8601 with millisecond precision.

    Args:
        moment: Time to format.

    Returns:
        A string ending in `Z`.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _url(value: str) -> str:
    """Reject a URL that is not http or https.

    Args:
        value: Submitted URL.

    Returns:
        The same URL.

    Raises:
        ApiError: Status 422 when the scheme or host is missing, or the URL
            is longer than 2000 characters.
    """
    if len(value) > _URL_LIMIT:
        raise ApiError(422, "invalid_request", "webhook url is too long")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.netloc == "":
        raise ApiError(422, "invalid_request", "webhook url must be http or https")
    return value


def _events(values: list[str]) -> list[str]:
    """Reject an event name that is not a dotted token or `*`.

    Args:
        values: Submitted event names.

    Returns:
        The same names.

    Raises:
        ApiError: Status 422 when the list is too long or a name is invalid.
    """
    if len(values) > _EVENT_COUNT:
        raise ApiError(422, "invalid_request", "webhook lists too many events")
    for item in values:
        if item == "*":
            continue
        if len(item) > _EVENT_LIMIT or not _event_name(item):
            raise ApiError(422, "invalid_request", "webhook event name is invalid")
    return values


def _event_name(value: str) -> bool:
    """Return whether a name is lowercase tokens separated by dots.

    Args:
        value: Candidate event name.

    Returns:
        `True` for names such as `approval.requested`.
    """
    if value == "" or value.startswith(".") or value.endswith("."):
        return False
    for part in value.split("."):
        if part == "" or not part.isalnum() or not part.islower():
            return False
    return True


def _secret(value: str | None) -> str | None:
    """Reject a secret that is present but empty or too long.

    Args:
        value: Submitted secret. `None` and `""` store no override.

    Returns:
        The secret to store, or `None`.

    Raises:
        ApiError: Status 422 when the secret is longer than 200 characters.
    """
    if value is None or value == "":
        return None
    if len(value) > _SECRET_LIMIT:
        raise ApiError(422, "invalid_request", "webhook secret is too long")
    return value


__all__ = [
    "create_webhook",
    "delete_webhook",
    "deliver",
    "ensure_catch_all",
    "get_webhook",
    "list_webhooks",
    "ping_webhook",
    "update_webhook",
]
