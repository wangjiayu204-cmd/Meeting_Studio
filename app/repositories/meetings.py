import json

from app.domain.schemas import Meeting, MeetingPage, MeetingSummary
from app.repositories.database import Database


class MeetingRepository:
    def __init__(self, database: Database):
        self.database = database

    def create(self, meeting: Meeting) -> Meeting:
        # 原文与全部段落在同一事务落库；任意约束失败都会回滚。
        with self.database.connection() as connection:
            connection.execute(
                """INSERT INTO meetings
                (id, title, meeting_date, timezone, text, members_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (meeting.id, meeting.title, meeting.meeting_date.isoformat(),
                 meeting.timezone, meeting.text,
                 json.dumps([member.model_dump() for member in meeting.members], ensure_ascii=False),
                 meeting.created_at.isoformat()),
            )
            connection.executemany(
                "INSERT INTO paragraphs (meeting_id, id, position, text) VALUES (?, ?, ?, ?)",
                [(meeting.id, paragraph.id, index, paragraph.text)
                 for index, paragraph in enumerate(meeting.paragraphs)],
            )
        return meeting

    def get(self, meeting_id: str) -> Meeting | None:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            if row is None:
                return None
            paragraphs = connection.execute(
                "SELECT id, text FROM paragraphs WHERE meeting_id = ? ORDER BY position",
                (meeting_id,),
            ).fetchall()
        data = dict(row)
        data["members"] = json.loads(data.pop("members_json"))
        data["paragraphs"] = [dict(paragraph) for paragraph in paragraphs]
        return Meeting.model_validate(data)

    def list(self, limit: int, offset: int) -> MeetingPage:
        with self.database.connection() as connection:
            # 一次读取事务确保总数与本页来自同一快照。
            connection.execute("BEGIN")
            total = connection.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
            rows = connection.execute(
                """SELECT id, title, meeting_date, timezone, created_at
                FROM meetings ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return MeetingPage(items=[MeetingSummary.model_validate(dict(row)) for row in rows],
                           total=total, limit=limit, offset=offset)
