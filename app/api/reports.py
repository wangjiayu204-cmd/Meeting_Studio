import json
from typing import Literal

from fastapi import APIRouter, Request, Response

from app.domain.reports import FollowupReport

router = APIRouter(tags=["exports and followup"])


@router.get("/meetings/{meeting_id}/export", response_class=Response,
            responses={200: {"content": {"application/json": {}, "text/calendar": {}}}})
def export(meeting_id: str, request: Request, format: Literal["json", "ics"] = "json"):
    service = request.app.state.report_service
    if format == "json":
        snapshot = service.repository.snapshot(meeting_id)
        content = json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2).encode("utf-8")
        media_type = "application/json"
        headers = {"X-PM-Task-Count": str(len(snapshot.tasks))}
        safe_id = snapshot.meeting.id
    else:
        calendar = service.calendar(meeting_id)
        content, media_type = calendar.content, "text/calendar"
        headers = {"X-PM-Task-Count": str(len(calendar.exported_task_ids)),
                   "X-PM-Skipped-Count": str(len(calendar.skipped))}
        # 只使用服务器生成的 ID，避免标题进入下载文件名或响应头。
        safe_id = meeting_id
    headers.update({"Content-Disposition": f'attachment; filename="meeting-{safe_id}.{format}"',
                    "Cache-Control": "no-store"})
    return Response(content, media_type=media_type, headers=headers)


@router.get("/meetings/{meeting_id}/followup", response_model=FollowupReport)
def followup(meeting_id: str, request: Request):
    return request.app.state.report_service.followup(meeting_id)
