from fastapi import APIRouter, Request
from app.core.errors import AppError
from app.domain.schemas import Analysis, Draft, DraftEdit, DraftReviewRequest, Run

router = APIRouter(tags=["planning"])


@router.post("/meetings/{meeting_id}/analysis", response_model=Draft)
async def analyze(meeting_id: str, request: Request):
    """等待分析完成；后台执行与 SSE 使用 /runs 接口。"""
    return await request.app.state.planning_service.analyze(meeting_id)


@router.get("/meetings/{meeting_id}/analysis", response_model=Analysis)
def analysis(meeting_id: str, request: Request):
    request.app.state.meeting_service.get(meeting_id)
    result = request.app.state.planning_repository.get_analysis(meeting_id)
    if result is None:
        raise AppError("analysis_not_found", "尚无会议分析结果", 404)
    return result


@router.get("/meetings/{meeting_id}/draft", response_model=Draft)
def get_draft(meeting_id: str, request: Request):
    return request.app.state.planning_service.get_draft(meeting_id)


@router.get("/meetings/{meeting_id}/drafts", response_model=list[Draft])
def history(meeting_id: str, request: Request):
    return request.app.state.planning_service.history(meeting_id)


@router.put("/meetings/{meeting_id}/draft", response_model=Draft)
def edit(meeting_id: str, data: DraftEdit, request: Request):
    return request.app.state.planning_service.edit(meeting_id, data)


@router.post("/meetings/{meeting_id}/draft/review", response_model=Draft)
async def review(meeting_id: str, data: DraftReviewRequest, request: Request):
    return await request.app.state.planning_service.review(meeting_id, data.draft_version)


@router.get("/runs/{run_id}", response_model=Run)
def get_run(run_id: str, request: Request):
    return request.app.state.planning_repository.get_run(run_id)
