import json

from app.core.errors import AppError
from app.domain.schemas import (Execution, ExecuteRequest, Meeting, Task, TaskPage,
                                new_id, utc_now)
from app.domain.states import DraftStatus, ExecutionStatus, RunStatus, RunType, TaskStatus
from app.domain.validation import require_executable
from app.repositories.database import Database
from app.repositories.meetings import MeetingRepository
from app.repositories.planning import PlanningRepository
from app.repositories.events import EventRepository


class TaskRepository:
    def __init__(self, database: Database):
        self.database = database
        self.planning = PlanningRepository(database)

    @staticmethod
    def _execution(connection, meeting_id):
        row = connection.execute("SELECT body FROM executions WHERE meeting_id=?", (meeting_id,)).fetchone()
        return Execution.model_validate_json(row[0]) if row else None

    def get_execution(self, meeting_id: str) -> Execution:
        with self.database.connection() as connection:
            result = self._execution(connection, meeting_id)
            if result is None:
                raise AppError("execution_not_found", "该会议尚无执行记录", 404)
            return result

    def claim(self, meeting: Meeting, request: ExecuteRequest, *, queued=False):
        from app.domain.schemas import Run
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            draft = self.planning._latest(connection, meeting.id)
            if not draft:
                raise AppError("draft_not_found", "尚无任务草稿", 404)
            if draft.version != request.draft_version:
                raise AppError("draft_version_conflict", "请确认最新草稿版本", 409,
                               [{"current_version": draft.version}])
            existing = self._execution(connection, meeting.id)
            if existing and existing.status == ExecutionStatus.succeeded:
                # 已执行重复请求不再调用模型或工具，返回原记录。
                return existing, draft, True
            self.planning._assert_idle(connection, meeting.id)
            require_executable(meeting, draft)
            run = Run(meeting_id=meeting.id, type=RunType.execution,
                      status=RunStatus.queued if queued else RunStatus.running,
                      started_at=None if queued else utc_now())
            connection.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?)", (
                run.id, meeting.id, run.status.value, run.created_at.isoformat(), run.model_dump_json()))
            EventRepository.insert(connection, run.id, "run_queued" if queued else "run_started",
                                   "执行已排队" if queued else "执行开始", payload={"type": "execution"})
            execution = Execution(
                id=existing.id if existing else new_id(), meeting_id=meeting.id,
                draft_id=draft.id, draft_version=draft.version, run_id=run.id,
                acknowledgement=request.acknowledgement,
            )
            connection.execute("""INSERT INTO executions VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(meeting_id) DO UPDATE SET draft_id=excluded.draft_id,
                run_id=excluded.run_id, status=excluded.status, body=excluded.body""", (
                execution.id, meeting.id, draft.id, run.id, execution.status.value, execution.model_dump_json()))
        return execution, draft, False

    def create_tasks(self, context, arguments) -> Execution:
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT body FROM executions WHERE id=?", (context.execution_id,)).fetchone()
            if not row:
                raise AppError("execution_not_found", "执行上下文不存在", 404)
            execution = Execution.model_validate_json(row[0])
            if execution.run_id != context.run_id:
                raise AppError("execution_context_conflict", "执行上下文已被新运行替换", 409)
            if execution.status == ExecutionStatus.succeeded:
                return execution
            run = self.planning._get_run(connection, context.run_id)
            if execution.status != ExecutionStatus.pending or run.status != RunStatus.running:
                raise AppError("execution_not_active", "执行已结束或中断", 409)
            draft = self.planning._latest(connection, execution.meeting_id)
            if draft.id != execution.draft_id or draft.version != execution.draft_version:
                raise AppError("draft_version_conflict", "执行前草稿发生变化", 409)
            # 在事务内重新验证，不能只依赖 claim 时的检查。
            meeting = MeetingRepository(self.database).get(execution.meeting_id)
            require_executable(meeting, draft)
            tasks = [Task(**task.model_dump(), meeting_id=execution.meeting_id, draft_id=draft.id)
                     for task in draft.tasks]
            for task in tasks:
                self._insert_task(connection, task)
            execution.status = ExecutionStatus.succeeded
            execution.task_ids = [task.id for task in tasks]
            execution.tool_name = "create_tasks"
            execution.finished_at = utc_now()
            connection.execute("UPDATE executions SET status=?, body=? WHERE id=?",
                               (execution.status.value, execution.model_dump_json(), execution.id))
            # 任务内容与审核不变，只推进当前版本的生命周期状态。
            confirmed = draft.model_copy(update={"status": DraftStatus.confirmed})
            connection.execute("UPDATE drafts SET body=? WHERE id=?", (confirmed.model_dump_json(), draft.id))
            run.status, run.finished_at = RunStatus.succeeded, utc_now()
            connection.execute("UPDATE runs SET status=?, body=? WHERE id=?",
                               (run.status.value, run.model_dump_json(), run.id))
            result = {"success": True, "execution_id": execution.id,
                      "task_ids": execution.task_ids, "count": len(tasks)}
            connection.execute("INSERT INTO tool_calls VALUES (?, ?, ?, ?, ?, ?)", (
                new_id(), execution.id, context.run_id, "create_tasks", utc_now().isoformat(),
                json.dumps(result, ensure_ascii=False)))
            EventRepository.insert(connection, run.id, "tool_result", "看板任务创建成功", "executor", result)
            EventRepository.insert(connection, run.id, "run_completed", "执行完成",
                                   payload={"execution_id": execution.id, "task_count": len(tasks)})
        return execution

    @staticmethod
    def _insert_task(connection, task):
        connection.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
            task.id, task.meeting_id, task.draft_id, task.local_id, task.status.value,
            task.owner, task.created_at.isoformat(), task.model_dump_json()))

    def fail(self, execution_id: str, run_id: str, code: str, message: str):
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT body FROM executions WHERE id=?", (execution_id,)).fetchone()
            if row is None:
                return
            execution = Execution.model_validate_json(row[0])
            if execution.run_id != run_id or execution.status != ExecutionStatus.pending:
                return
            execution.status = ExecutionStatus.failed
            execution.error_message, execution.finished_at = message, utc_now()
            connection.execute("UPDATE executions SET status=?, body=? WHERE id=?",
                               (execution.status.value, execution.model_dump_json(), execution.id))
            run = self.planning._get_run(connection, run_id)
            run.status = RunStatus.interrupted if code == "execution_cancelled" else RunStatus.failed
            run.finished_at = utc_now()
            run.error_code, run.error_message = code, message
            connection.execute("UPDATE runs SET status=?, body=? WHERE id=?",
                               (run.status.value, run.model_dump_json(), run.id))
            EventRepository.insert(connection, run.id,
                                   "run_interrupted" if run.status == RunStatus.interrupted else "run_failed", message,
                                   payload={"error_code": code, "execution_id": execution.id})

    def interrupt_pending(self):
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute("SELECT body FROM executions WHERE status='pending'").fetchall()
            for row in rows:
                execution = Execution.model_validate_json(row[0])
                execution.status, execution.finished_at = ExecutionStatus.failed, utc_now()
                execution.error_message = "应用重启中断执行，可再次确认重试"
                connection.execute("UPDATE executions SET status=?, body=? WHERE id=?",
                                   (execution.status.value, execution.model_dump_json(), execution.id))

    def list(self, *, meeting_id=None, status: TaskStatus | None = None, owner=None,
             limit=20, offset=0) -> TaskPage:
        conditions, values = [], []
        for column, value in (("meeting_id", meeting_id), ("status", status.value if status else None), ("owner", owner)):
            if value is not None:
                conditions.append(f"{column}=?")
                values.append(value)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.database.connection() as connection:
            connection.execute("BEGIN")
            total = connection.execute("SELECT COUNT(*) FROM tasks" + where, values).fetchone()[0]
            rows = connection.execute("SELECT body FROM tasks" + where +
                                      " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                                      [*values, limit, offset]).fetchall()
        return TaskPage(items=[Task.model_validate_json(row[0]) for row in rows],
                        total=total, limit=limit, offset=offset)

    def update(self, task_id: str, status: TaskStatus) -> Task:
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT body FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise AppError("task_not_found", "任务不存在", 404)
            task = Task.model_validate_json(row[0])
            task.status, task.updated_at = status, utc_now()
            connection.execute("UPDATE tasks SET status=?, body=? WHERE id=?",
                               (status.value, task.model_dump_json(), task_id))
        return task
