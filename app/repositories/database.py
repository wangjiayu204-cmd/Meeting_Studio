from contextlib import contextmanager
from pathlib import Path
import sqlite3


SCHEMA_VERSION = 2


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

    def check(self) -> bool:
        with self.connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            connection.execute("SELECT id FROM meetings LIMIT 1").fetchall()
            connection.execute("SELECT id FROM paragraphs LIMIT 1").fetchall()
            connection.execute("SELECT id FROM runs LIMIT 1").fetchall()
            connection.execute("SELECT id FROM drafts LIMIT 1").fetchall()
            return version == SCHEMA_VERSION
