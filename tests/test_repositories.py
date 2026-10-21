import sqlite3

import pytest

from app.documents.parser import split_paragraphs
from app.domain.schemas import Meeting, Paragraph
from app.repositories.database import Database
from app.repositories.meetings import MeetingRepository


def meeting(**overrides):
    text = "会议决定本周完成登录模块。\n张三负责联调。"
    return Meeting(**{"title": "例会", "meeting_date": "2026-10-21", "text": text,
                      "paragraphs": split_paragraphs(text), **overrides})


def test_restart_and_sqlite_constraints(tmp_path):
    path = tmp_path / "nested/pm.sqlite3"
    database = Database(path)
    database.initialize()
    repository = MeetingRepository(database)
    created = repository.create(meeting())
    restarted = Database(path)
    restarted.initialize()
    assert restarted.check()
    assert MeetingRepository(restarted).get(created.id) == created
    with restarted.connection() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO paragraphs VALUES (?, ?, ?, ?)", ("missing", "p0001", 0, "text"))


def test_partial_create_rolls_back(tmp_path):
    database = Database(tmp_path / "pm.sqlite3")
    database.initialize()
    repository = MeetingRepository(database)
    invalid = meeting(paragraphs=[Paragraph(id="p0001", text="第一段"),
                                  Paragraph(id="p0001", text="重复段落")])
    with pytest.raises(sqlite3.IntegrityError):
        repository.create(invalid)
    assert repository.get(invalid.id) is None
    assert repository.list(20, 0).total == 0
    with database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM paragraphs").fetchone()[0] == 0


def test_newer_database_version_is_not_overwritten(tmp_path):
    database = Database(tmp_path / "pm.sqlite3")
    database.initialize()
    with database.connection() as connection:
        connection.execute("PRAGMA user_version = 999")
    with pytest.raises(RuntimeError, match="数据库版本"):
        database.initialize()
    with database.connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 999
