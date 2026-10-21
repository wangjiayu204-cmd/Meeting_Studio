from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    phase: Literal["P1"] = "P1"
    database: Literal["not_configured"] = "not_configured"
    model: Literal["not_checked"] = "not_checked"


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """P1 应用存活检查；不创建数据库、不调用模型。"""
    return HealthResponse(version=__version__)
