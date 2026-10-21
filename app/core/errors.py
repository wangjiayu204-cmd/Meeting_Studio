from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, JsonValue
from starlette.exceptions import HTTPException


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[JsonValue] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorBody
    request_id: str


class AppError(Exception):
    """业务错误只携带可公开的信息，不传入原始模型响应或密钥。"""

    def __init__(self, code: str, message: str, status_code: int = 400,
                 details: list[Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or []


def error_response(request: Request, code: str, message: str,
                   status_code: int, details: list | None = None,
                   headers: dict | None = None) -> JSONResponse:
    request_id = request.state.request_id
    body = ErrorResponse(
        error=ErrorBody(code=code, message=message, details=details or []),
        request_id=request_id,
    )
    return JSONResponse(
        status_code=status_code, content=body.model_dump(mode="json"),
        headers={**(headers or {}), "X-Request-ID": request_id},
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError):
        return error_response(request, exc.code, exc.message,
                              exc.status_code, exc.details)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError):
        # 不返回 input / ctx，避免回显会议原文、凭证和不可序列化对象。
        details = [
            {"location": list(item["loc"]), "type": item["type"],
             "message": item["msg"]}
            for item in exc.errors()
        ]
        return error_response(request, "validation_error", "请求参数不合法", 422, details)

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exc: HTTPException):
        codes = {404: "not_found", 405: "method_not_allowed"}
        message = exc.detail if isinstance(exc.detail, str) else "请求失败"
        return error_response(request, codes.get(exc.status_code, "http_error"),
                              message, exc.status_code, headers=exc.headers)
