"""HTTP error body used by every JSON endpoint."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """Raise to return the standard error document.

    Args:
        status: HTTP status code.
        code: Short snake_case identifier.
        message: Human-readable explanation.
    """

    def __init__(self, status: int, code: str, message: str) -> None:
        """Store the status, code, and message.

        Args:
            status: HTTP status code.
            code: Short snake_case identifier.
            message: Human-readable explanation.
        """
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def install_error_handlers(app: FastAPI) -> None:
    """Register the handler that renders `ApiError`.

    Args:
        app: Application that should speak the error format.
    """

    @app.exception_handler(ApiError)
    async def render_api_error(_request: Request, exc: ApiError) -> JSONResponse:
        body = {"error": {"code": exc.code, "message": exc.message}}
        return JSONResponse(status_code=exc.status, content=body)

    @app.exception_handler(RequestValidationError)
    async def render_validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        body = {
            "error": {
                "code": "invalid_request",
                "message": "request validation failed",
                "details": _validation_details(exc.errors()),
            }
        }
        return JSONResponse(status_code=422, content=body)


def _validation_details(errors: Sequence[Any]) -> list[dict[str, str]]:
    """Turn pydantic errors into field/reason pairs without input values.

    Args:
        errors: `RequestValidationError.errors()` entries.

    Returns:
        Objects with `field` (location joined by `.`) and `reason`. Secrets in
        the request body are never copied into the response.
    """
    details: list[dict[str, str]] = []
    for entry in errors:
        if not isinstance(entry, dict):
            continue
        location = entry.get("loc", ())
        parts = [str(part) for part in location if part not in {"body", "query", "path", "header"}]
        field = ".".join(parts) if parts else ".".join(str(part) for part in location)
        message = entry.get("msg", "invalid")
        reason = message if isinstance(message, str) else "invalid"
        details.append({"field": field, "reason": reason})
    return details


__all__ = ["ApiError", "install_error_handlers"]
