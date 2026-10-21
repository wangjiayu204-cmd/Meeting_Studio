from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import TypeAdapter, ValidationError
from starlette.concurrency import run_in_threadpool

from app.core.errors import AppError
from app.domain.schemas import Meeting, MeetingCreate, MeetingPage, Member
from app.services.meetings import MeetingService

router = APIRouter(prefix="/meetings", tags=["meetings"])


def get_meeting_service(request: Request) -> MeetingService:
    return request.app.state.meeting_service


Service = Annotated[MeetingService, Depends(get_meeting_service)]


@router.post("", response_model=Meeting, status_code=201)
def create_meeting(data: MeetingCreate, service: Service):
    return service.create(data)


@router.post("/upload", response_model=Meeting, status_code=201)
async def upload_meeting(
    service: Service,
    file: Annotated[UploadFile, File(description="TXT 或文本型 PDF")],
    title: Annotated[str, Form(min_length=1, max_length=160)],
    meeting_date: Annotated[date, Form()],
    timezone: Annotated[str | None, Form()] = None,
    members: Annotated[str, Form(description='JSON 数组，如 [{"name":"张三","role":"研发"}]')] = "[]",
):
    try:
        content = await file.read(service.settings.max_upload_bytes + 1)
        if len(content) > service.settings.max_upload_bytes:
            raise AppError("upload_too_large", "上传文件超过大小限制", 413)
        text = await run_in_threadpool(service.parse_upload, file.filename or "", content)
        try:
            people = TypeAdapter(list[Member]).validate_json(members)
            data = MeetingCreate(
                title=title, meeting_date=meeting_date,
                timezone=timezone if timezone is not None else service.settings.timezone,
                members=people, text=text,
            )
        except ValidationError as exc:
            # 与 JSON 请求共用校验错误结构，不回显表单原文。
            raise RequestValidationError(exc.errors()) from exc
        return await run_in_threadpool(service.create, data)
    finally:
        await file.close()


@router.get("", response_model=MeetingPage)
def list_meetings(service: Service, limit: int = Query(default=20, ge=1, le=100),
                  offset: int = Query(default=0, ge=0)):
    return service.list(limit, offset)


@router.get("/{meeting_id}", response_model=Meeting)
def get_meeting(meeting_id: str, service: Service):
    return service.get(meeting_id)
