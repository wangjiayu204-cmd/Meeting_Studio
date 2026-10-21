from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app import __version__
from app.core.errors import AppError

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    phase: Literal["P2"] = "P2"
    database: Literal["ok"] = "ok"
    model: Literal["not_checked"] = "not_checked"


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """检查应用与数据库，不调用模型；数据库异常返回 503。"""
    try:
        healthy = await run_in_threadpool(request.app.state.database.check)
    except Exception as exc:
        raise AppError("database_unavailable", "数据库健康检查失败", 503) from exc
    if not healthy:
        raise AppError("database_unavailable", "数据库版本不匹配", 503)
    return HealthResponse(version=__version__)
