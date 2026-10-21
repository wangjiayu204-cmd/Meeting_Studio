from contextlib import contextmanager
from pathlib import Path
import sqlite3


SCHEMA_VERSION = 4


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("数据库版本高于应用支持版本，请使用兼容代码或恢复备份")
            if version < 1:
                connection.execute("""
                    CREATE TABLE meetings (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        meeting_date TEXT NOT NULL,
                        timezone TEXT NOT NULL,
                        text TEXT NOT NULL,
                        members_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)
                connection.execute("""
                    CREATE TABLE paragraphs (
                        meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                        id TEXT NOT NULL,
                        position INTEGER NOT NULL,
                        text TEXT NOT NULL,
                        PRIMARY KEY (meeting_id, id),
                        UNIQUE (meeting_id, position)
                    )
                """)
                connection.execute("CREATE INDEX meetings_created ON meetings(created_at DESC, id DESC)")
                connection.execute("PRAGMA user_version = 1")
            if version < 2:
                connection.execute("""CREATE TABLE runs (
                    id TEXT PRIMARY KEY,
                    meeting_id TEXT NOT NULL REFERENCES meetings(id),
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    body TEXT NOT NULL
                )""")
                connection.execute("""CREATE UNIQUE INDEX one_active_run ON runs(meeting_id)
                    WHERE status IN ('queued', 'running')""")
                connection.execute("""CREATE TABLE analyses (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id), body TEXT NOT NULL
                )""")
                connection.execute("""CREATE TABLE drafts (
                    id TEXT PRIMARY KEY,
                    meeting_id TEXT NOT NULL REFERENCES meetings(id),
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    version INTEGER NOT NULL CHECK(version > 0),
                    body TEXT NOT NULL,
                    UNIQUE(meeting_id, version)
                )""")
                connection.execute("PRAGMA user_version = 2")
            if version < 3:
                connection.execute("""CREATE TABLE executions (
                    id TEXT PRIMARY KEY,
                    meeting_id TEXT NOT NULL UNIQUE REFERENCES meetings(id),
                    draft_id TEXT NOT NULL REFERENCES drafts(id),
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    status TEXT NOT NULL CHECK(status IN ('pending','succeeded','failed')),
                    body TEXT NOT NULL
                )""")
                connection.execute("""CREATE TABLE tasks (
                    id TEXT PRIMARY KEY,
                    meeting_id TEXT NOT NULL REFERENCES meetings(id),
                    draft_id TEXT NOT NULL REFERENCES drafts(id),
                    local_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('todo','in_progress','done')),
                    owner TEXT,
                    created_at TEXT NOT NULL,
                    body TEXT NOT NULL,
                    UNIQUE(meeting_id, local_id)
                )""")
                connection.execute("CREATE INDEX tasks_board ON tasks(meeting_id,status,created_at DESC)")
                connection.execute("""CREATE TABLE tool_calls (
                    id TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL UNIQUE REFERENCES executions(id),
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    tool_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    body TEXT NOT NULL
                )""")
                connection.execute("PRAGMA user_version = 3")
            if version < 4:
                connection.execute("""CREATE TABLE events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    type TEXT NOT NULL,
                    agent TEXT,
                    message TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )""")
                connection.execute("CREATE INDEX events_run_id ON events(run_id,id)")
                connection.execute("""CREATE UNIQUE INDEX one_terminal_event ON events(run_id)
                    WHERE type IN ('run_completed','run_failed','run_interrupted')""")
                connection.execute("""CREATE TABLE run_requests (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id),
                    body TEXT NOT NULL,
                    retry_of TEXT REFERENCES runs(id)
                )""")
                connection.execute("PRAGMA user_version = 4")

    def check(self) -> bool:
        with self.connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            connection.execute("SELECT id FROM meetings LIMIT 1").fetchall()
            connection.execute("SELECT id FROM paragraphs LIMIT 1").fetchall()
            connection.execute("SELECT id FROM runs LIMIT 1").fetchall()
            connection.execute("SELECT id FROM drafts LIMIT 1").fetchall()
            connection.execute("SELECT id FROM executions LIMIT 1").fetchall()
            connection.execute("SELECT id FROM tasks LIMIT 1").fetchall()
            connection.execute("SELECT id FROM tool_calls LIMIT 1").fetchall()
            connection.execute("SELECT id FROM events LIMIT 1").fetchall()
            connection.execute("SELECT run_id FROM run_requests LIMIT 1").fetchall()
            return version == SCHEMA_VERSION
