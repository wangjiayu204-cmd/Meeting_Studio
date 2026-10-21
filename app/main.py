from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request

from app import __version__
from app.api.health import router as health_router
from app.core.config import Settings
from app.core.errors import ErrorResponse, error_response, register_error_handlers
from app.core.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()
    logger = configure_logging(config.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        logger.info("application_started phase=P1")
        yield
        logger.info("application_stopped")

    app = FastAPI(
        title=config.app_name,
        version=__version__,
        description="轻量多 Agent 会议与任务管理。P1：配置、领域契约、健康检查。",
        lifespan=lifespan,
        responses={
            422: {"model": ErrorResponse, "description": "参数校验失败"},
            500: {"model": ErrorResponse, "description": "内部错误"},
        },
    )
    app.state.settings = config
    register_error_handlers(app)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        # 服务端生成 ID，避免不受信任的请求头进入日志。
        request.state.request_id = uuid4().hex
        try:
            response = await call_next(request)
        except Exception as exc:
            # 不输出异常正文，它可能包含会议文本、上游响应或密钥。
            logger.error("unhandled_error request_id=%s type=%s",
                         request.state.request_id, type(exc).__name__)
            response = error_response(request, "internal_error", "服务内部错误", 500)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    app.include_router(health_router, prefix="/api")
    return app
