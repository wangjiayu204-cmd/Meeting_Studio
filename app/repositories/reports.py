import json

from app.core.errors import AppError
from app.domain.reports import MeetingSnapshot
from app.domain.schemas import Analysis, Draft, Execution, Meeting, Task
from app.repositories.database import Database


class ReportRepository:
    def __init__(self, database: Database):
        self.database = database

    def snapshot(self, meeting_id: str) -> MeetingSnapshot:
        # 多表读取固定在一个 SQLite 快照内，不分页，避免看板更新时导出内容不一致。
        with self.database.connection() as connection:
            connection.execute("BEGIN")
            row = connection.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
            if row is None:
                raise AppError("meeting_not_found", "会议不存在", 404)
            data = dict(row)
            data["members"] = json.loads(data.pop("members_json"))
            data["paragraphs"] = [dict(p) for p in connection.execute(
                "SELECT id,text FROM paragraphs WHERE meeting_id=? ORDER BY position", (meeting_id,))]
            analysis = connection.execute("""SELECT a.body,a.run_id FROM analyses a
                JOIN runs r ON r.id=a.run_id WHERE r.meeting_id=?
                ORDER BY r.created_at DESC,r.id DESC LIMIT 1""", (meeting_id,)).fetchone()
            draft = connection.execute("SELECT body FROM drafts WHERE meeting_id=? ORDER BY version DESC LIMIT 1",
                                       (meeting_id,)).fetchone()
            execution = connection.execute("SELECT body FROM executions WHERE meeting_id=?", (meeting_id,)).fetchone()
            tasks = connection.execute("SELECT body FROM tasks WHERE meeting_id=? ORDER BY created_at,id",
                                       (meeting_id,)).fetchall()
            return MeetingSnapshot(
                meeting=Meeting.model_validate(data),
                analysis=Analysis.model_validate_json(analysis[0]) if analysis else None,
                analysis_run_id=analysis[1] if analysis else None,
                draft=Draft.model_validate_json(draft[0]) if draft else None,
                execution=Execution.model_validate_json(execution[0]) if execution else None,
                tasks=[Task.model_validate_json(t[0]) for t in tasks],
            )
