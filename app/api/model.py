from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/model", tags=["model"])


class ModelHealth(BaseModel):
    status: Literal["ok"]
    model: str
    latency_ms: float
    trace_id: str


@router.get("/health", response_model=ModelHealth)
async def model_health(request: Request):
    """仅检查模型列表及名称；实际推理由独立冒烟脚本验证。"""
    return await request.app.state.model_client.health()
