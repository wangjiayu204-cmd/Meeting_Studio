from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool

from app import __version__
from app.api.health import router as health_router
from app.api.meetings import router as meetings_router
from app.api.body_limit import BodyLimitMiddleware
from app.api.model import router as model_router
from app.core.config import Settings
from app.core.errors import ErrorResponse, error_response, register_error_handlers
from app.core.logging import configure_logging
from app.repositories.database import Database
from app.repositories.meetings import MeetingRepository
from app.services.meetings import MeetingService
from app.llm.client import ModelClient
from app.agents.meeting import MeetingAgents
from app.api.planning import router as planning_router
from app.repositories.planning import PlanningRepository
from app.services.planning import PlanningService
from app.agents.executor import ExecutorAgent
from app.api.tasks import router as tasks_router
from app.repositories.tasks import TaskRepository
from app.services.execution import ExecutionService
from app.services.runs import RunManager
from app.repositories.events import EventRepository
from app.api.runs import router as runs_router


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()
    logger = configure_logging(config.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        await run_in_threadpool(application.state.database.initialize)
        application.state.model_client = ModelClient(config)
        await run_in_threadpool(application.state.planning_repository.interrupt_active)
        await run_in_threadpool(application.state.task_repository.interrupt_pending)
        application.state.planning_service = PlanningService(
            application.state.planning_repository, application.state.meeting_service,
            MeetingAgents(application.state.model_client), config,
        )
        application.state.execution_service = ExecutionService(
            application.state.task_repository, application.state.meeting_service,
            ExecutorAgent(application.state.model_client), config,
        )
        application.state.run_manager = RunManager(application.state.planning_service,
                                                   application.state.execution_service, config)
        logger.info("application_started phase=P6")
        try:
            yield
        finally:
            await application.state.run_manager.close()
            await application.state.model_client.aclose()
            logger.info("application_stopped")

    app = FastAPI(
        title=config.app_name,
        version=__version__,
        description="轻量多 Agent 会议与任务管理。P6：后台队列、持久化事件与 SSE。",
        lifespan=lifespan,
        responses={
            422: {"model": ErrorResponse, "description": "参数校验失败"},
            500: {"model": ErrorResponse, "description": "内部错误"},
        },
    )
    app.state.settings = config
    app.state.database = Database(config.database_path)
    app.state.planning_repository = PlanningRepository(app.state.database)
    app.state.task_repository = TaskRepository(app.state.database)
    app.state.event_repository = EventRepository(app.state.database)
    app.state.meeting_service = MeetingService(MeetingRepository(app.state.database), config)
    register_error_handlers(app)
    # 为 JSON 转义及 multipart 元数据预留空间；文件仍执行独立字节限制。
    app.add_middleware(BodyLimitMiddleware, max_bytes=max(
        config.max_upload_bytes + 262144, config.max_document_chars * 6 + 262144,
    ))

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
    app.include_router(meetings_router, prefix="/api")
    app.include_router(model_router, prefix="/api")
    app.include_router(planning_router, prefix="/api")
    app.include_router(tasks_router, prefix="/api")
    app.include_router(runs_router, prefix="/api")
    return app
