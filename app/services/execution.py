import asyncio
import logging

from starlette.concurrency import run_in_threadpool

from app.agents.executor import ExecutorAgent
from app.core.artifacts import ArtifactRecord
from app.core.config import Settings
from app.core.errors import AppError
from app.domain.schemas import ExecuteRequest, Execution
from app.repositories.tasks import TaskRepository
from app.services.meetings import MeetingService
from app.tools.registry import CreateTasksArguments, ToolContext, ToolRegistry

logger = logging.getLogger("meeting_pm")


class ExecutionService:
    def __init__(self, repository: TaskRepository, meetings: MeetingService,
                 agent: ExecutorAgent, settings: Settings):
        self.repository, self.meetings, self.agent, self.settings = repository, meetings, agent, settings
        self.tools = ToolRegistry()
        self.tools.register("create_tasks", CreateTasksArguments, repository.create_tasks)

    async def execute(self, meeting_id: str, request: ExecuteRequest) -> Execution:
        meeting = await run_in_threadpool(self.meetings.get, meeting_id)
        execution, draft, reused = await run_in_threadpool(self.repository.claim, meeting, request)
        if reused:
            return execution
        record = None
        committed = None
        try:
            record = ArtifactRecord(
                self.settings.test_artifacts_path / "workflows" if self.settings.llm_trace_enabled else None,
                "execution", (self.settings.llm_api_key.get_secret_value(),),
            )
            record.write("context.json", {"execution": execution.model_dump(mode="json"),
                                           "confirmed_draft": draft.model_dump(mode="json")})
            result = await asyncio.wait_for(self.agent.choose(draft, self.tools.catalog()),
                                            timeout=self.settings.workflow_timeout)
            record.write("01_executor.json", {"trace_id": result.trace_id,
                                               "command": result.value.model_dump(mode="json")})
            # 事务执行不放在可取消的 wait_for 内，避免已提交却向客户端报告未执行。
            committed = await run_in_threadpool(
                self.tools.invoke, result.value.tool_name, result.value.arguments.model_dump(),
                ToolContext(execution.id, execution.run_id),
            )
            record.write("02_tool_result.json", committed.model_dump(mode="json"))
            record.finish("succeeded", execution_id=committed.id, run_id=committed.run_id,
                          count=len(committed.task_ids))
            return committed
        except (Exception, asyncio.CancelledError) as exc:
            # 日志写入失败不撤销或误报已经提交的任务。
            if committed is not None:
                logger.error("execution_trace_failed execution_id=%s", committed.id)
                return committed
            if isinstance(exc, asyncio.CancelledError):
                error = AppError("execution_cancelled", "执行被中断，可重新确认", 503)
            elif isinstance(exc, asyncio.TimeoutError):
                error = AppError("execution_timeout", "Executor 超时，可重新确认", 504)
            elif isinstance(exc, AppError):
                error = exc
            else:
                error = AppError("execution_failed", "工具执行失败，任务事务已回滚，可重试", 500)
            await run_in_threadpool(self.repository.fail, execution.id, execution.run_id, error.code, error.message)
            if record:
                record.finish("failed", execution_id=execution.id, run_id=execution.run_id,
                              error_code=error.code, message=error.message)
            if isinstance(exc, asyncio.CancelledError):
                raise
            error.details.append({"execution_id": execution.id, "run_id": execution.run_id})
            if error is exc:
                raise
            raise error from exc
