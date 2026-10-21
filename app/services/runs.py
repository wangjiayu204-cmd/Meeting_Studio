"""单进程后台队列，预先持久化排队运行，同会议互斥。"""

import asyncio
import json
import logging

from starlette.concurrency import run_in_threadpool

from app.core.errors import AppError
from app.domain.schemas import (ExecuteRequest, RunRetryRequest, RunSubmission)
from app.domain.states import RunStatus, RunType
from app.repositories.planning import PlanningRepository
from app.services.execution import ExecutionService
from app.services.planning import PlanningService

logger = logging.getLogger("meeting_pm")


class RunManager:
    def __init__(self, planning: PlanningService, execution: ExecutionService, settings):
        self.planning, self.execution = planning, execution
        self.repository: PlanningRepository = planning.repository
        self.settings = settings
        self.slots = asyncio.Semaphore(settings.max_concurrent_runs)
        self.lock = asyncio.Lock()
        self.tasks: dict[str, asyncio.Task] = {}
        self.closing = False

    async def submit(self, meeting_id: str, data: RunSubmission, *, retry_of=None):
        # 同步认领先于返回 202，避免重复点击创建多个同会议排队运行。
        async with self.lock:
            if self.closing:
                raise AppError("service_stopping", "服务正在停止，请稍后重试", 503)
            if len(self.tasks) >= self.settings.max_pending_runs:
                raise AppError("run_queue_full", "后台队列已满，请稍后重试", 503)
            meeting = await run_in_threadpool(self.planning.meetings.get, meeting_id)
            execution_context = None
            if data.type == "execution":
                request = ExecuteRequest(**data.model_dump(exclude={"type"}))
                execution, draft, reused = await run_in_threadpool(
                    self.execution.repository.claim, meeting, request, queued=True,
                )
                run = await run_in_threadpool(self.repository.get_run, execution.run_id)
                if reused:
                    return run
                execution_context = (execution, draft)
            else:
                run = await run_in_threadpool(
                    self.repository.start, meeting_id, RunType(data.type),
                    data.draft_version if data.type == "review" else None, queued=True,
                )
            try:
                await run_in_threadpool(self._save_request, run.id, data.model_dump(mode="json"), retry_of)
                task = asyncio.create_task(self._work(run, execution_context), name=f"run-{run.id}")
                self.tasks[run.id] = task
                task.add_done_callback(lambda finished, run_id=run.id: self._done(run_id, finished))
                return run
            except Exception:
                await self._fail(run, "run_submission_failed", "后台运行提交失败，请重试")
                raise

    def _save_request(self, run_id, body, retry_of):
        with self.repository.database.connection() as connection:
            connection.execute("INSERT INTO run_requests VALUES (?,?,?)",
                               (run_id, json.dumps(body, ensure_ascii=False), retry_of))

    def _done(self, run_id, task):
        self.tasks.pop(run_id, None)
        if not task.cancelled() and task.exception() is not None:
            logger.error("background_run_failed run_id=%s type=%s", run_id, type(task.exception()).__name__)

    async def _fail(self, run, code, message):
        if run.type == RunType.execution:
            execution = await run_in_threadpool(self.execution.repository.get_execution, run.meeting_id)
            await run_in_threadpool(self.execution.repository.fail, execution.id, run.id, code, message)
        else:
            await run_in_threadpool(self.repository.finish, run.id, RunStatus.failed, 0, code, message)

    async def _work(self, run, execution_context):
        try:
            async with self.slots:
                run = await run_in_threadpool(self.repository.mark_running, run.id)
                if execution_context:
                    await self.execution.perform_prepared(*execution_context)
                else:
                    await self.planning.perform_prepared(run)
        except asyncio.CancelledError:
            await run_in_threadpool(self.repository.finish, run.id, RunStatus.interrupted, 0,
                                    "service_stopping", "服务停止，运行已中断，可重试")
            raise
        except AppError as exc:
            await self._fail(run, exc.code, exc.message)
        except Exception:
            await self._fail(run, "background_run_failed", "后台运行失败，请查看测试记录后重试")

    async def retry(self, run_id: str, request: RunRetryRequest):
        from pydantic import TypeAdapter
        old = await run_in_threadpool(self.repository.get_run, run_id)
        if old.status not in {RunStatus.failed, RunStatus.interrupted}:
            raise AppError("run_not_retryable", "仅失败或中断的运行可重试", 409)

        def load():
            with self.repository.database.connection() as connection:
                row = connection.execute("SELECT body FROM run_requests WHERE run_id=?", (run_id,)).fetchone()
                return json.loads(row[0]) if row else None
        body = await run_in_threadpool(load)
        if body is None:
            raise AppError("run_request_missing", "该运行没有后台请求记录，请通过原入口重新提交", 409)
        if old.type == RunType.execution:
            if not request.confirm:
                raise AppError("execution_confirmation_required", "重试执行需要再次明确确认", 422)
            body["confirm"] = True
            body["acknowledgement"] = request.acknowledgement
        # 保留原草稿版本；被修改后拒绝旧复核 / 执行，不擅自应用新版本。
        return await self.submit(old.meeting_id, TypeAdapter(RunSubmission).validate_python(body), retry_of=run_id)

    async def close(self):
        self.closing = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # 处理尚未进入协程就被取消的任务，以及排队中的执行。
        await run_in_threadpool(self.repository.interrupt_active)
        await run_in_threadpool(self.execution.repository.interrupt_pending)
