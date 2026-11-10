"""One error envelope for every service:

{"error": {"code": "...", "message": "...", "details": [...], "correlation_id": "..."}}
"""

from __future__ import annotations

import logging
from typing import Any

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from .logging import get_correlation_id

log = logging.getLogger(__name__)


class AppError(Exception):
    status = 400
    code = "BAD_REQUEST"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status: int | None = None,
        details: list[Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.code
        self.status = status or self.status
        self.details = details or []
        self.headers = headers or {}


class ValidationFailed(AppError):
    status = 400
    code = "VALIDATION_ERROR"


class Unauthorized(AppError):
    status = 401
    code = "UNAUTHORIZED"


class Forbidden(AppError):
    status = 403
    code = "FORBIDDEN"


class NotFound(AppError):
    status = 404
    code = "NOT_FOUND"


class Conflict(AppError):
    status = 409
    code = "CONFLICT"


class Unprocessable(AppError):
    status = 422
    code = "UNPROCESSABLE"


class TooManyRequests(AppError):
    status = 429
    code = "RATE_LIMITED"

    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message, headers={"Retry-After": str(max(1, retry_after))})


def error_body(code: str, message: str, details: list[Any] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or [],
            "correlation_id": get_correlation_id(),
        }
    }


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(AppError)
    def _app_error(exc: AppError):
        return jsonify(error_body(exc.code, exc.message, exc.details)), exc.status, exc.headers

    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException):
        code = (exc.name or "HTTP_ERROR").upper().replace(" ", "_")
        return jsonify(error_body(code, exc.description or exc.name)), exc.code or 500

    @app.errorhandler(Exception)
    def _unhandled(exc: Exception):
        log.exception("unhandled error")
        return jsonify(error_body("INTERNAL_ERROR", "Internal server error")), 500
