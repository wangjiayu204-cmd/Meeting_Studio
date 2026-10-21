from datetime import date, datetime, timezone

from fastapi.testclient import TestClient
import pytest

from app.agents.executor import ExecutorAgent
from app.agents.meeting import MeetingAgents
from app.core.config import Settings
from app.core.errors import AppError
from app.domain.reports import MeetingSnapshot
from app.domain.schemas import Analysis, Execution, ReviewIssue, Task
from app.domain.states import TaskStatus
from app.exports.calendar import escape_text, fold_line, render_calendar
from app.exports.followup import build_followup
from app.main import create_app
from app.repositories.reports import ReportRepository
from app.services.reports import MeetingToolContext, ReportService
from test_execution import ExecutorClient, bundle, execute
from test_planning import FakeClient, meeting, task


def snapshot(*items):
    sample = meeting()
    tasks = [Task(meeting_id=sample.id, draft_id="draft", **item) for item in items]
    return MeetingSnapshot(meeting=sample, tasks=tasks, execution=Execution(
        meeting_id=sample.id, draft_version=1, status="succeeded", task_ids=[t.id for t in tasks]))


def test_calendar_chinese_escaping_and_folding():
    title = "中文标题" * 40 + "\\;逗号,\r\nBEGIN:VEVENT\r\n恶意属性"
    source = snapshot(task(title=title[:240], description="第一行\n第二行;中文,\\").model_dump())
    result = render_calendar(source)
    raw = result.content
    assert raw.endswith(b"END:VCALENDAR\r\n")
    assert b"\n" not in raw.replace(b"\r\n", b"")
    assert all(len(line) <= 75 for line in raw.split(b"\r\n"))
    # 每一物理行都应可独立解码，防止 UTF-8 字符被拆断。
    for line in raw.split(b"\r\n"):
        line.decode("utf-8")
    unfolded = raw.decode().replace("\r\n ", "")
    assert unfolded.count("\r\nBEGIN:VEVENT\r\n") == 1
    assert "SUMMARY:" + escape_text(source.tasks[0].title) + "\r\n" in unfolded
    assert "DTSTART;VALUE=DATE:20261023\r\nDTEND;VALUE=DATE:20261024" in unfolded
    assert "STATUS:COMPLETED" not in unfolded
    assert len(result.exported_task_ids) == 1 and not result.skipped


@pytest.mark.parametrize("value", ["a" * 76, "中" * 60, "a" * 74 + "中" + "b" * 80, ""])
def test_folding_roundtrip(value):
    folded = fold_line(value)
    assert folded.replace("\r\n ", "") == value
    assert all(len(line.encode()) <= 75 for line in folded.split("\r\n"))


def test_calendar_skips_unknown_and_unrepresentable_dates():
    source = snapshot(task(due_date=None, due_evidence=None).model_dump(),
                      task(local_id="task_max", due_date=date.max).model_dump())
    result = render_calendar(source)
    assert not result.exported_task_ids
    assert set(result.skipped.values()) == {"missing_due_date", "exclusive_end_out_of_range"}
    assert b"BEGIN:VEVENT" not in result.content
    assert result.content.count(b"X-PM-SKIPPED-TASK:") == 2
    source.tasks = []
    assert not render_calendar(source).skipped


def test_calendar_stable_uids_and_updated_status():
    source = snapshot(task().model_dump())
    first = render_calendar(source).content
    source.tasks[0].status = TaskStatus.done
    second = render_calendar(source).content
    uid = f"UID:{source.tasks[0].id}@lightweight-pm.local".encode()
    assert uid in first and uid in second
    assert b"X-PM-TASK-STATUS:done" in second


@pytest.mark.parametrize("zone,instant,expected_date,overdue", [
    ("Asia/Shanghai", "2026-10-23T15:59:59+00:00", "2026-10-23", 0),
    ("Asia/Shanghai", "2026-10-23T16:00:00+00:00", "2026-10-24", 1),
    ("America/New_York", "2026-10-24T02:00:00+00:00", "2026-10-23", 0),
    ("America/New_York", "2026-11-01T05:30:00+00:00", "2026-11-01", 1),
    ("America/New_York", "2026-11-01T06:30:00+00:00", "2026-11-01", 1),
])
def test_followup_local_midnight_and_dst(zone, instant, expected_date, overdue):
    source = snapshot(task().model_dump())
    source.meeting.timezone = zone
    report = build_followup(source, now=datetime.fromisoformat(instant))
    assert report.as_of_date.isoformat() == expected_date
    assert report.counts.overdue == overdue
    assert report.counts.due_today == (1 if expected_date == "2026-10-23" else 0)


def test_followup_done_excluded_missing_fields_and_open_questions():
    source = snapshot({**task().model_dump(), "status": "done"},
                      task(local_id="task_unknown", due_date=None, due_evidence=None,
                           owner=None, owner_evidence=None, needs_confirmation=["确认范围"]).model_dump())
    source.analysis = Analysis(summary="会议", open_questions=["限流方案待定"])
    report = build_followup(source, now=datetime(2026, 11, 1, tzinfo=timezone.utc))
    assert report.counts.model_dump() == dict(total=2, completed=1, pending=1, overdue=0,
                                             due_today=0, missing_owner=1, missing_due_date=1)
    assert {q.message for q in report.clarifications} == {"限流方案待定", "确认范围", "负责人待确认", "截止日期待确认"}
    assert all(q.task_id == source.tasks[1].id for q in report.clarifications if q.origin == "task")
    with pytest.raises(ValueError):
        build_followup(source, now=datetime(2026, 11, 1))


def test_draft_not_counted_as_persisted_tasks(bundle):
    service = ReportService(ReportRepository(bundle[1]))
    source = service.repository.snapshot(bundle[2].id)
    source.draft.tasks[0].owner = None
    source.draft.tasks[0].needs_confirmation = ["确认负责人"]
    source.draft.review.issues = [ReviewIssue(message="范围需确认"),
                                  ReviewIssue(message="已核实", status="accepted", resolution_note="人工确认")]
    report = build_followup(source)
    assert report.counts.total == report.counts.pending == 0
    assert source.draft.tasks and not source.tasks
    assert any(q.origin == "draft" and q.task_ref == "task_login" for q in report.clarifications)
    assert any(q.origin == "review" and q.message == "范围需确认" for q in report.clarifications)
    assert not any(q.message == "已核实" for q in report.clarifications)
    assert report.review_issues[1].resolution_note == "人工确认"
    with pytest.raises(AppError) as error:
        service.calendar(bundle[2].id)
    assert error.value.code == "calendar_not_ready"


def test_snapshot_exports_every_task_and_status_updates(bundle):
    execute(bundle)
    database, sample = bundle[1], bundle[2]
    repository = ReportRepository(database)
    first = repository.snapshot(sample.id)
    # 数据层构造超出 API 单页上限的数据，验证导出不会复用分页列表。
    with database.connection() as connection:
        for index in range(1, 125):
            item = first.tasks[0].model_copy(update={"id": f"id{index}", "local_id": f"task_{index}"})
            bundle[6]._insert_task(connection, item)
    source = repository.snapshot(sample.id)
    assert len(source.tasks) == 125
    assert len(render_calendar(source).exported_task_ids) == 125
    bundle[6].update(first.tasks[0].id, TaskStatus.done)
    assert build_followup(repository.snapshot(sample.id)).counts.completed == 1
    assert source.tasks[0].status == TaskStatus.todo  # 已读取快照不会随更新变化。
    database.initialize()
    assert len(ReportRepository(database).snapshot(sample.id).tasks) == 125


def test_read_tools_bound_to_meeting_and_no_create_capability(bundle):
    service = ReportService(ReportRepository(bundle[1]))
    assert {t["name"] for t in service.tools.catalog()} == {"get_task_status", "export_calendar"}
    with pytest.raises(AppError) as error:
        service.tools.invoke("get_task_status", {"meeting_id": "other"}, MeetingToolContext(bundle[2].id))
    assert error.value.code == "invalid_tool_arguments"
    with pytest.raises(AppError):
        service.tools.invoke("create_tasks", {}, MeetingToolContext(bundle[2].id))


def test_download_and_followup_http_contract_and_restart(tmp_path):
    settings = Settings(_env_file=None, database_path=tmp_path / "api.sqlite3",
                        test_artifacts_path=tmp_path / "artifacts")
    with TestClient(create_app(settings)) as client:
        app = client.app
        app.state.planning_service.agents = MeetingAgents(FakeClient())
        app.state.execution_service.agent = ExecutorAgent(ExecutorClient())
        saved = client.post("/api/meetings", json={"title": "中文会议", "meeting_date": "2026-10-21",
                                                  "text": meeting().text}).json()
        prefix = f"/api/meetings/{saved['id']}"
        for suffix in ("/export", "/export?format=ics", "/followup"):
            response = client.get("/api/meetings/missing" + suffix)
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "meeting_not_found"
        before = client.get(prefix + "/export").json()
        assert before["draft"] is None and not before["tasks"]
        assert client.get(prefix + "/export?format=ics").status_code == 409
        assert client.get(prefix + "/export?format=pdf").status_code == 422
        draft = client.post(prefix + "/analysis").json()
        result = client.post(prefix + "/execute", json={"draft_version": draft["version"], "confirm": True})
        assert result.status_code == 200, result.text
        task_id = result.json()["task_ids"][0]
        client.patch(f"/api/tasks/{task_id}", json={"status": "done"})
        exported = client.get(prefix + "/export?format=json")
        assert exported.headers["cache-control"] == "no-store"
        assert exported.headers["x-pm-task-count"] == "1"
        assert "attachment;" in exported.headers["content-disposition"]
        assert exported.json()["tasks"][0]["status"] == "done"
        assert exported.json()["analysis_run_id"]
        calendar = client.get(prefix + "/export?format=ics")
        assert calendar.headers["content-type"].startswith("text/calendar")
        assert calendar.headers["x-pm-skipped-count"] == "0"
        assert task_id.encode() in calendar.content
        report = client.get(prefix + "/followup").json()
        assert report["counts"]["completed"] == 1 and report["counts"]["overdue"] == 0
    with TestClient(create_app(settings)) as client:
        assert client.get(prefix + "/followup").json()["counts"] == report["counts"]
        assert client.get(prefix + "/export").json()["tasks"] == exported.json()["tasks"]
