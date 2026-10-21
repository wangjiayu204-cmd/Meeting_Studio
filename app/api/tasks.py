from fastapi import APIRouter, Query, Request

from app.domain.schemas import (AcceptIssuesRequest, Draft, ExecuteRequest, Execution,
                                Task, TaskPage, TaskUpdate)
from app.domain.states import TaskStatus

router = APIRouter(tags=["execution and tasks"])


@router.post("/meetings/{meeting_id}/draft/accept-issues", response_model=Draft)
def accept_issues(meeting_id: str, data: AcceptIssuesRequest, request: Request):
    return request.app.state.planning_service.accept_issues(meeting_id, data)


@router.post("/meetings/{meeting_id}/execute", response_model=Execution)
async def execute(meeting_id: str, data: ExecuteRequest, request: Request):
    return await request.app.state.execution_service.execute(meeting_id, data)


@router.get("/meetings/{meeting_id}/execution", response_model=Execution)
def execution(meeting_id: str, request: Request):
    request.app.state.meeting_service.get(meeting_id)
    return request.app.state.task_repository.get_execution(meeting_id)


@router.get("/tasks", response_model=TaskPage)
def list_tasks(request: Request, meeting_id: str | None = None, status: TaskStatus | None = None,
               owner: str | None = None, limit: int = Query(default=20, ge=1, le=100),
               offset: int = Query(default=0, ge=0)):
    return request.app.state.task_repository.list(meeting_id=meeting_id, status=status, owner=owner,
                                                  limit=limit, offset=offset)


@router.patch("/tasks/{task_id}", response_model=Task)
def update_task(task_id: str, data: TaskUpdate, request: Request):
    return request.app.state.task_repository.update(task_id, data.status)
