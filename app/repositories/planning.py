from app.core.errors import AppError
from app.domain.schemas import Analysis, Draft, Run, utc_now
from app.domain.states import RunStatus, RunType
from app.repositories.database import Database
from app.repositories.events import EventRepository


class PlanningRepository:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _latest(connection, meeting_id):
        row = connection.execute(
            "SELECT body FROM drafts WHERE meeting_id = ? ORDER BY version DESC LIMIT 1",
            (meeting_id,),
        ).fetchone()
        return Draft.model_validate_json(row[0]) if row else None

    @staticmethod
    def _assert_idle(connection, meeting_id):
        row = connection.execute(
            "SELECT id FROM runs WHERE meeting_id = ? AND status IN ('queued','running')",
            (meeting_id,),
        ).fetchone()
        if row:
            raise AppError("meeting_busy", "该会议已有运行，请等待结束", 409,
                           [{"run_id": row[0]}])

    @staticmethod
    def _version(latest, expected):
        current = latest.version if latest else 0
        if current != expected:
            raise AppError("draft_version_conflict", "草稿版本已更新，请刷新", 409,
                           [{"current_version": current}])
        if latest and latest.status.value == "confirmed":
            raise AppError("meeting_already_executed", "会议任务已经确认执行，首版不支持重新规划", 409)

    def start(self, meeting_id: str, run_type: RunType,
              expected_version: int | None = None, *, queued=False) -> Run:
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute("SELECT id FROM meetings WHERE id=?", (meeting_id,)).fetchone():
                raise AppError("meeting_not_found", "会议不存在", 404)
            self._assert_idle(connection, meeting_id)
            latest = self._latest(connection, meeting_id)
            if run_type == RunType.review and latest is None:
                raise AppError("draft_not_found", "尚无草稿可供复核", 404)
            self._version(latest, expected_version if expected_version is not None else (latest.version if latest else 0))
            run = Run(meeting_id=meeting_id, type=run_type,
                      status=RunStatus.queued if queued else RunStatus.running,
                      started_at=None if queued else utc_now())
            connection.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?)", (
                run.id, meeting_id, run.status.value, run.created_at.isoformat(), run.model_dump_json()))
            EventRepository.insert(connection, run.id, "run_queued" if queued else "run_started",
                                   "运行已排队" if queued else "运行开始", payload={"type": run_type.value})
        return run

    def finish(self, run_id: str, status: RunStatus, revision_count=0,
               error_code=None, error_message=None):
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = self._get_run(connection, run_id)
            if run.status not in {RunStatus.queued, RunStatus.running}:
                return run
            run.status = status
            run.finished_at = utc_now()
            run.revision_count = revision_count
            run.error_code, run.error_message = error_code, error_message
            connection.execute("UPDATE runs SET status=?, body=? WHERE id=?",
                               (status.value, run.model_dump_json(), run.id))
            event_type = {RunStatus.succeeded: "run_completed", RunStatus.failed: "run_failed",
                          RunStatus.interrupted: "run_interrupted"}[status]
            EventRepository.insert(connection, run.id, event_type,
                                   "运行完成" if status == RunStatus.succeeded else (error_message or "运行中断"),
                                   payload={"status": status.value, "error_code": error_code})
        return run

    @staticmethod
    def _get_run(connection, run_id):
        row = connection.execute("SELECT body FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise AppError("run_not_found", "运行不存在", 404)
        return Run.model_validate_json(row[0])

    def get_run(self, run_id: str) -> Run:
        with self.database.connection() as connection:
            return self._get_run(connection, run_id)

    def save_analysis(self, run_id: str, analysis: Analysis):
        with self.database.connection() as connection:
            connection.execute("INSERT INTO analyses VALUES (?, ?)", (run_id, analysis.model_dump_json()))

    def get_analysis(self, meeting_id: str) -> Analysis | None:
        with self.database.connection() as connection:
            row = connection.execute("""SELECT a.body FROM analyses a JOIN runs r ON r.id=a.run_id
                WHERE r.meeting_id=? ORDER BY r.created_at DESC, r.id DESC LIMIT 1""",
                                     (meeting_id,)).fetchone()
            return Analysis.model_validate_json(row[0]) if row else None

    def save_draft(self, draft: Draft, *, expected_version: int | None = None,
                   require_idle: bool = False) -> Draft:
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if require_idle:
                self._assert_idle(connection, draft.meeting_id)
            latest = self._latest(connection, draft.meeting_id)
            current = latest.version if latest else 0
            self._version(latest, current if expected_version is None else expected_version)
            version = current + 1
            # 保存时绑定真实版本，绝不信任模型给出的版本。
            review = draft.review.model_copy(update={"draft_version": version}) if draft.review else None
            saved = draft.model_copy(update={"version": version, "review": review})
            connection.execute("INSERT INTO drafts VALUES (?, ?, ?, ?, ?)", (
                saved.id, saved.meeting_id, saved.run_id, saved.version, saved.model_dump_json()))
        return saved

    def current_draft(self, meeting_id: str) -> Draft | None:
        with self.database.connection() as connection:
            return self._latest(connection, meeting_id)

    def drafts(self, meeting_id: str) -> list[Draft]:
        with self.database.connection() as connection:
            rows = connection.execute("SELECT body FROM drafts WHERE meeting_id=? ORDER BY version",
                                      (meeting_id,)).fetchall()
            return [Draft.model_validate_json(row[0]) for row in rows]

    def interrupt_active(self):
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute("SELECT body FROM runs WHERE status IN ('running','queued')").fetchall()
            for row in rows:
                run = Run.model_validate_json(row[0])
                run.status = RunStatus.interrupted
                run.finished_at = utc_now()
                run.error_code = "application_restarted"
                run.error_message = "运行被应用重启中断，请重新发起"
                connection.execute("UPDATE runs SET status=?, body=? WHERE id=?",
                                   (run.status.value, run.model_dump_json(), run.id))
                EventRepository.insert(connection, run.id, "run_interrupted", run.error_message,
                                       payload={"error_code": run.error_code})

    def mark_running(self, run_id: str) -> Run:
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = self._get_run(connection, run_id)
            if run.status != RunStatus.queued:
                raise AppError("run_not_queued", "运行已经开始或结束", 409)
            run.status, run.started_at = RunStatus.running, utc_now()
            connection.execute("UPDATE runs SET status=?,body=? WHERE id=?",
                               (run.status.value, run.model_dump_json(), run_id))
            EventRepository.insert(connection, run_id, "run_started", "后台运行开始",
                                   payload={"type": run.type.value})
            return run
