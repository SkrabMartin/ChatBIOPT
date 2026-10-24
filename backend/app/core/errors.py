"""Uniform error envelope: every non-2xx JSON response is `{"error": {code, message, trace_id, details?}}`."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import trace_id_var

logger = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class UpstreamError(AppError):
    """An external data provider failed. Distinct from "no results": callers must never present it as empty."""

    status_code = 502
    code = "upstream_unavailable"


class UpstreamTimeoutError(UpstreamError):
    status_code = 504
    code = "upstream_timeout"


class UpstreamInvalidResponseError(UpstreamError):
    code = "upstream_invalid_response"


_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


def error_response(status_code: int, code: str, message: str, details: Any = None) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message, "trace_id": trace_id_var.get()}
    if details is not None:
        error["details"] = details
    return JSONResponse(status_code=status_code, content={"error": jsonable_encoder(error)})


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Echoing `input` back could reflect large or sensitive payloads; location + reason is enough.
        details = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return error_response(422, "validation_error", "Request validation failed", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        response = error_response(exc.status_code, code, str(exc.detail))
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", exc_info=exc)
        return error_response(500, "internal_error", "Internal server error")
