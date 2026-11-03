from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class ApplicationError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "application_error",
        status_code: int = status.HTTP_400_BAD_REQUEST,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


def _error_payload(request: Request, code: str, message: str) -> dict[str, Any]:
    return {
        "error": {"code": code, "message": message},
        "request_id": getattr(request.state, "request_id", None),
    }


async def application_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, ApplicationError):
        raise exc
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(request, exc.code, exc.message),
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    structlog.get_logger(__name__).exception("unhandled_exception", error_type=type(exc).__name__)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_payload(request, "internal_error", "An unexpected error occurred."),
    )


def register_exception_handlers(application: FastAPI) -> None:
    application.add_exception_handler(ApplicationError, application_error_handler)
    application.add_exception_handler(Exception, unexpected_error_handler)

