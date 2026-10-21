import asyncio
import json
from time import monotonic

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.core.errors import AppError
from app.domain.schemas import Run, RunRetryRequest, RunSubmission
from app.domain.states import RunStatus

router = APIRouter(tags=["background runs"])
TERMINAL = {RunStatus.succeeded, RunStatus.failed, RunStatus.interrupted}


@router.post("/meetings/{meeting_id}/runs", response_model=Run, status_code=202)
async def submit(meeting_id: str, data: RunSubmission, request: Request):
    return await request.app.state.run_manager.submit(meeting_id, data)


@router.post("/runs/{run_id}/retry", response_model=Run, status_code=202)
async def retry(run_id: str, data: RunRetryRequest, request: Request):
    return await request.app.state.run_manager.retry(run_id, data)


@router.get("/meetings/{meeting_id}/runs", response_model=list[Run])
def history(meeting_id: str, request: Request,
            limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    request.app.state.meeting_service.get(meeting_id)
    with request.app.state.database.connection() as connection:
        rows = connection.execute("SELECT body FROM runs WHERE meeting_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                                  (meeting_id, limit, offset)).fetchall()
    return [Run.model_validate_json(row[0]) for row in rows]


@router.get("/runs/{run_id}/events")
async def events(run_id: str, request: Request, after: int = Query(default=0, ge=0),
                 last_event_id: str | None = Header(default=None, alias="Last-Event-ID")):
    repository = request.app.state.planning_repository
    event_repository = request.app.state.event_repository
    await run_in_threadpool(repository.get_run, run_id)  # 在流开始前返回正常 404。
    if last_event_id is not None:
        if not last_event_id.isascii() or not last_event_id.isdigit() or len(last_event_id) > 18:
            raise AppError("invalid_event_cursor", "Last-Event-ID 必须是非负整数", 422)
        after = max(after, int(last_event_id))
    if after > 9223372036854775807:
        raise AppError("invalid_event_cursor", "事件游标超出范围", 422)

    async def stream():
        cursor = after
        heartbeat = monotonic()
        yield "retry: 1000\n\n"
        while not await request.is_disconnected():
            batch = await run_in_threadpool(event_repository.after, run_id, cursor)
            for event in batch:
                cursor = event.id
                data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
                yield f"id: {event.id}\nevent: {event.type.value}\ndata: {data}\n\n"
            if batch:
                heartbeat = monotonic()
                continue  # 终态前先补读所有分页事件，避免只读前 100 条。
            run = await run_in_threadpool(repository.get_run, run_id)
            if run.status in TERMINAL:
                # 再读一次覆盖本轮空读取与终态提交之间的竞态。
                if not await run_in_threadpool(event_repository.after, run_id, cursor, 1):
                    break
                continue
            if monotonic() - heartbeat >= request.app.state.settings.sse_heartbeat_seconds:
                yield ": heartbeat\n\n"
                heartbeat = monotonic()
            await asyncio.sleep(request.app.state.settings.sse_poll_interval)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })
