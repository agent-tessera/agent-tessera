"""Approval request endpoints."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from tessera_server.api.deps import get_session, require_admin
from tessera_server.api.errors import ApiError
from tessera_server.db.models import AdminKey
from tessera_server.services.approvals import (
    Approver,
    create_approval,
    get_approval,
    list_approvals,
    read_approver,
    resolve_approval,
)
from tessera_server.services.secrets import parse_secret, secret_matches

_ADMIN = Depends(require_admin)
_SESSION = Depends(get_session)
_LIMIT = Query(default=50, ge=1, le=500)


class _CreateBody(BaseModel):
    """Body of `POST /v1/approvals`."""

    model_config = ConfigDict(extra="forbid")

    action: dict[str, object]
    decision: dict[str, object]
    run_id: str | None = Field(default=None, max_length=64)
    principal: str | None = Field(default=None, max_length=320)


class _Comment(BaseModel):
    """Optional note on an approve or deny."""

    model_config = ConfigDict(extra="forbid")

    comment: str | None = Field(default=None, max_length=1000)


def approvals_router() -> APIRouter:
    """Build the approval routes.

    Returns:
        A router for creating, reading, listing, and resolving approvals.
    """
    router = APIRouter(tags=["approvals"])

    @router.post("/v1/approvals")
    def post_approval(
        body: _CreateBody, request: Request, session: Session = _SESSION
    ) -> dict[str, object]:
        """Create an approval from an agent instance.

        Args:
            body: Action, decision, and optional run and principal.
            request: Request whose bearer token is a runtime JWT.
            session: Database session.

        Returns:
            The new id, status, and expiry.
        """
        return create_approval(
            session,
            request.app.state.settings,
            _bearer(request),
            action=body.action,
            decision=body.decision,
            run_id=body.run_id,
            principal=body.principal,
        )

    @router.get("/v1/approvals")
    def read_approvals(
        *,
        status: str | None = None,
        agent_id: str | None = None,
        limit: int = _LIMIT,
        cursor: str | None = None,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """List approvals.

        Args:
            status: Keep only this status.
            agent_id: Keep only this agent.
            limit: Page size.
            cursor: Id of the last approval already seen.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            A page of approvals, newest first, and `next_cursor`.
        """
        return list_approvals(session, status=status, agent_id=agent_id, limit=limit, cursor=cursor)

    @router.get("/v1/approvals/{approval_id}")
    def read_approval(
        approval_id: str, request: Request, session: Session = _SESSION
    ) -> dict[str, object]:
        """Return one approval to its agent or to an admin.

        Args:
            approval_id: Approval to read.
            request: Bearer admin/runtime credential, or an admin session cookie.
            session: Database session.

        Returns:
            The approval document.
        """
        credential = _optional_bearer(request)
        if credential is not None:
            admin = _admin_token(session, credential)
            return get_approval(
                session,
                request.app.state.settings,
                None if admin else credential,
                approval_id,
                admin=admin,
            )
        require_admin(request)
        return get_approval(
            session,
            request.app.state.settings,
            None,
            approval_id,
            admin=True,
        )

    @router.post("/v1/approvals/{approval_id}:approve")
    async def post_approve(
        approval_id: str, request: Request, session: Session = _SESSION
    ) -> dict[str, object]:
        """Approve a pending request.

        Args:
            approval_id: Approval to approve.
            request: Bearer admin/runtime credential, or an admin session cookie.
                The body may be empty or `{"comment": "..."}`.
            session: Database session.

        Returns:
            The updated approval.
        """
        return _resolve(request, session, approval_id, allow=True, comment=await _comment(request))

    @router.post("/v1/approvals/{approval_id}:deny")
    async def post_deny(
        approval_id: str, request: Request, session: Session = _SESSION
    ) -> dict[str, object]:
        """Deny a pending request.

        Args:
            approval_id: Approval to deny.
            request: Bearer admin/runtime credential, or an admin session cookie.
                The body may be empty or `{"comment": "..."}`.
            session: Database session.

        Returns:
            The updated approval.
        """
        return _resolve(request, session, approval_id, allow=False, comment=await _comment(request))

    return router


def _resolve(
    request: Request,
    session: Session,
    approval_id: str,
    *,
    allow: bool,
    comment: str | None,
) -> dict[str, object]:
    """Resolve an approval as the caller.

    Args:
        request: Bearer admin/runtime credential, or an admin session cookie.
        session: Database session.
        approval_id: Approval to resolve.
        allow: Whether the decision is an approval.
        comment: Optional note.

    Returns:
        The updated approval.
    """
    return resolve_approval(
        session,
        approval_id,
        _approver(request, session),
        request.app.state.settings,
        allow=allow,
        comment=comment,
    )


def _approver(request: Request, session: Session) -> Approver:
    """Identify the caller as an admin session, admin key, or runtime party.

    Args:
        request: Current HTTP request.
        session: Open database session.

    Returns:
        The approver used for resolve checks and audit.

    Raises:
        ApiError: Status 401 when no usable credential is present.
    """
    credential = _optional_bearer(request)
    if credential is not None:
        return read_approver(session, request.app.state.settings, credential)
    admin_key = require_admin(request)
    actor = str(getattr(request.state, "admin_actor", "admin") or "admin")
    if admin_key is not None:
        return Approver("admin", admin_key.name, None, actor)
    identity = actor
    if actor.startswith("admin:"):
        identity = actor.removeprefix("admin:") or actor
    return Approver("admin", identity, None, actor)


def _admin_token(session: Session, token: str) -> bool:
    """Return whether a bearer credential is a live admin key.

    Args:
        session: Open database session.
        token: Bearer credential.

    Returns:
        `True` when the credential matches an admin key that is not revoked.
    """
    parsed = parse_secret(token, prefix="tsa")
    if parsed is None:
        return False
    key_id, secret = parsed
    row = session.get(AdminKey, key_id)
    return row is not None and not row.revoked and secret_matches(row.secret_sha256, secret)


async def _comment(request: Request) -> str | None:
    """Read an optional comment from a resolve body.

    Args:
        request: Request whose body is empty or a comment object.

    Returns:
        The comment, or `None` when the body is empty.

    Raises:
        ApiError: Status 422 when the body is not a comment object.
    """
    raw = await request.body()
    if raw == b"":
        return None
    try:
        loaded = json.loads(raw)
        return _Comment.model_validate(loaded).comment
    except (json.JSONDecodeError, ValidationError) as err:
        raise ApiError(422, "invalid_request", "comment body is invalid") from err


def _optional_bearer(request: Request) -> str | None:
    """Return a bearer credential when the Authorization header presents one.

    Args:
        request: Current HTTP request.

    Returns:
        The credential after `Bearer`, or `None` when absent.
    """
    header = request.headers.get("authorization", "")
    scheme, _, credential = header.partition(" ")
    if scheme.lower() != "bearer" or credential == "":
        return None
    return credential


def _bearer(request: Request) -> str:
    """Return the bearer credential.

    Args:
        request: Current HTTP request.

    Returns:
        The credential after `Bearer`.

    Raises:
        ApiError: Status 401 when the header is missing or not a bearer token.
    """
    credential = _optional_bearer(request)
    if credential is None:
        raise ApiError(401, "invalid_token", "credential is required")
    return credential


__all__ = ["approvals_router"]
