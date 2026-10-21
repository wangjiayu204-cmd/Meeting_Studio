import json

from app.domain.schemas import Event, utc_now
from app.domain.states import EventType
from app.repositories.database import Database

TERMINAL_EVENTS = {"run_completed", "run_failed", "run_interrupted"}


class EventRepository:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def insert(connection, run_id, event_type, message, agent=None, payload=None):
        # 终态事件与运行状态同事务，SSE 不会看到“运行结束但最后事件尚未落库”。
        if event_type in TERMINAL_EVENTS and connection.execute(
            "SELECT id FROM events WHERE run_id=? AND type IN ('run_completed','run_failed','run_interrupted')",
            (run_id,),
        ).fetchone():
            return
        connection.execute("INSERT INTO events (run_id,type,agent,message,payload,created_at) VALUES (?,?,?,?,?,?)", (
            run_id, event_type, agent, message, json.dumps(payload or {}, ensure_ascii=False), utc_now().isoformat(),
        ))

    def append(self, run_id, event_type, message, agent=None, payload=None):
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.insert(connection, run_id, event_type, message, agent, payload)

    def after(self, run_id: str, after: int, limit=100) -> list[Event]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT ?", (run_id, after, limit),
            ).fetchall()
        return [Event(id=row["id"], run_id=row["run_id"], type=row["type"], agent=row["agent"],
                      message=row["message"], payload=json.loads(row["payload"]), created_at=row["created_at"])
                for row in rows]
