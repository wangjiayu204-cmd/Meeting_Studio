import asyncio
from concurrent.futures import ThreadPoolExecutor
import sqlite3

from fastapi.testclient import TestClient
import pytest

from app.agents.executor import ExecutorAgent, ExecutorCommand
from app.agents.contracts import ReviewVerdict
from app.agents.meeting import MeetingAgents
from app.core.config import Settings
from app.core.errors import AppError
from app.domain.schemas import (AcceptIssuesRequest, DraftEdit, ExecuteRequest, Plan)
from app.domain.states import DraftStatus, ExecutionStatus, RunStatus, RunType, TaskStatus
from app.llm.client import ModelResult
from app.main import create_app
from app.repositories.database import Database
from app.repositories.meetings import MeetingRepository
from app.repositories.planning import PlanningRepository
from app.repositories.tasks import TaskRepository
from app.services.execution import ExecutionService
from app.services.meetings import MeetingService
from app.services.planning import PlanningService
from app.tools.registry import CreateTasksArguments, ToolContext, ToolRegistry
from test_planning import FakeClient, meeting, task


class ExecutorClient:
    def __init__(self):
        self.calls = 0
        self.fail = False

    async def generate_structured(self, messages, schema, *, label, prompt_version):
        self.calls += 1
        if self.fail:
            raise AppError("llm_connection_error", "连接失败", 502)
        assert label == "executor"
        return ModelResult(ExecutorCommand(tool_name="create_tasks", arguments={}), "exec-trace", 1, {})


@pytest.fixture
def bundle(tmp_path):
    config = Settings(_env_file=None, database_path=tmp_path / "pm.sqlite3",
                      test_artifacts_path=tmp_path / "artifacts")
    database = Database(config.database_path)
    database.initialize()
    meetings = MeetingRepository(database)
    sample = meetings.create(meeting())
    planning_repo = PlanningRepository(database)
    planner = FakeClient()
    planning = PlanningService(planning_repo, MeetingService(meetings, config), MeetingAgents(planner), config)
    draft = asyncio.run(planning.analyze(sample.id))
    tasks = TaskRepository(database)
    executor_client = ExecutorClient()
    service = ExecutionService(tasks, planning.meetings, ExecutorAgent(executor_client), config)
    return config, database, sample, planning, planner, draft, tasks, executor_client, service


def execute(bundle):
    *_, service = bundle
    return asyncio.run(service.execute(bundle[2].id, ExecuteRequest(draft_version=bundle[5].version, confirm=True)))


def test_execution_calls_real_tool_and_is_idempotent(bundle):
    config, db, sample, planning, _, draft, repo, client, service = bundle
    first = execute(bundle)
    assert first.status == ExecutionStatus.succeeded
    assert first.tool_name == "create_tasks"
    assert len(first.task_ids) == 1
    assert client.calls == 1
    assert execute(bundle) == first
    assert client.calls == 1
    assert repo.list(meeting_id=sample.id).total == 1
    assert planning.get_draft(sample.id).status == DraftStatus.confirmed
    assert planning.repository.get_run(first.run_id).status == RunStatus.succeeded
    with db.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 1
    with pytest.raises(AppError) as error:
        asyncio.run(planning.analyze(sample.id))
    assert error.value.code == "meeting_already_executed"


def test_stale_execution_version_is_rejected(bundle):
    _, _, sample, planning, _, draft, repo, client, service = bundle
    planning.edit(sample.id, DraftEdit(base_version=draft.version, tasks=draft.tasks))
    with pytest.raises(AppError) as error:
        execute(bundle)
    assert error.value.code == "draft_version_conflict"
    assert client.calls == 0
    assert repo.list().total == 0


def test_pending_review_cannot_execute(bundle):
    _, _, sample, planning, _, draft, repo, client, service = bundle
    edited = planning.edit(sample.id, DraftEdit(base_version=draft.version, tasks=draft.tasks))
    with pytest.raises(AppError) as error:
        asyncio.run(service.execute(sample.id, ExecuteRequest(draft_version=edited.version, confirm=True)))
    assert error.value.code == "draft_not_ready"
    assert repo.list().total == 0


def test_missing_fields_must_be_accepted_with_notes(bundle):
    _, _, sample, planning, planner, _, repo, client, service = bundle
    planner.plans = [Plan(tasks=[task(owner=None, owner_evidence=None, due_date=None, due_evidence=None)])]
    draft = asyncio.run(planning.analyze(sample.id))
    assert draft.status == DraftStatus.needs_attention
    with pytest.raises(AppError):
        asyncio.run(service.execute(sample.id, ExecuteRequest(draft_version=draft.version, confirm=True)))
    accepted = planning.accept_issues(sample.id, AcceptIssuesRequest(
        draft_version=draft.version,
        resolutions=[{"issue_id": issue.id, "note": "当前允许未分配负责人和时间，后续跟进"}
                     for issue in draft.review.issues],
    ))
    assert accepted.status == DraftStatus.ready
    assert all(i.status == "accepted" and i.resolution_note for i in accepted.review.issues)
    assert planning.history(sample.id)[-2].review.issues[0].status == "open"
    result = asyncio.run(service.execute(sample.id, ExecuteRequest(draft_version=accepted.version, confirm=True,
                                                                  acknowledgement="已确认允许空字段")))
    assert result.acknowledgement == "已确认允许空字段"
    saved = repo.list().items[0]
    assert saved.owner is None and saved.due_date is None


def test_program_hard_errors_cannot_be_accepted(bundle):
    _, _, sample, planning, planner, _, _, _, _ = bundle
    planner.plans = [Plan(tasks=[task(source_refs=["p9999"])])]
    draft = asyncio.run(planning.analyze(sample.id))
    with pytest.raises(AppError) as error:
        planning.accept_issues(sample.id, AcceptIssuesRequest(
            draft_version=draft.version,
            resolutions=[{"issue_id": draft.review.issues[0].id, "note": "强制接受"}],
        ))
    assert error.value.code == "hard_error_not_acceptable"


def test_semantic_issues_require_individual_auditable_acceptance(bundle):
    _, _, sample, planning, planner, _, _, _, service = bundle
    planner.verdicts = [ReviewVerdict(approved=False, summary="待确认", issues=[
        {"task_ref": "task_login", "severity": "warning", "message": "交付范围需核实"},
        {"task_ref": "task_login", "severity": "error", "message": "负责人安排需人工核实"},
    ])]
    draft = asyncio.run(planning.analyze(sample.id))
    first, second = draft.review.issues
    partially = planning.accept_issues(sample.id, AcceptIssuesRequest(
        draft_version=draft.version, resolutions=[{"issue_id": first.id, "note": "已核实范围"}]))
    assert partially.status == DraftStatus.needs_attention
    with pytest.raises(AppError):
        asyncio.run(service.execute(sample.id, ExecuteRequest(draft_version=partially.version, confirm=True)))
    accepted = planning.accept_issues(sample.id, AcceptIssuesRequest(
        draft_version=partially.version, resolutions=[{"issue_id": second.id, "note": "会后已核实安排"}]))
    assert accepted.status == DraftStatus.ready
    result = asyncio.run(service.execute(sample.id, ExecuteRequest(draft_version=accepted.version, confirm=True)))
    assert result.status == ExecutionStatus.succeeded


def test_acceptance_rejects_stale_unknown_and_duplicate_ids(bundle):
    _, _, sample, planning, planner, _, _, _, _ = bundle
    planner.plans = [Plan(tasks=[task(owner=None, owner_evidence=None)])]
    draft = asyncio.run(planning.analyze(sample.id))
    issue_id = draft.review.issues[0].id
    for version, resolutions, expected in [
        (1, [{"issue_id": issue_id, "note": "确认"}], "draft_version_conflict"),
        (draft.version, [{"issue_id": "unknown", "note": "确认"}], "review_issue_not_open"),
        (draft.version, [{"issue_id": issue_id, "note": "确认"}] * 2, "duplicate_issue_resolution"),
    ]:
        with pytest.raises(AppError) as error:
            planning.accept_issues(sample.id, AcceptIssuesRequest(draft_version=version, resolutions=resolutions))
        assert error.value.code == expected


def test_execute_rechecks_even_if_review_claims_approved(bundle):
    _, db, sample, planning, _, draft, repo, _, _ = bundle
    tampered = draft.model_copy(deep=True)
    tampered.tasks[0].source_refs = ["p9999"]
    with db.connection() as connection:
        connection.execute("UPDATE drafts SET body=? WHERE id=?", (tampered.model_dump_json(), draft.id))
    with pytest.raises(AppError) as error:
        execute(bundle)
    assert error.value.code == "plan_validation_failed"


def test_concurrent_claims_only_one_wins(bundle):
    _, _, sample, planning, _, draft, repo, _, _ = bundle
    request = ExecuteRequest(draft_version=draft.version, confirm=True)
    def claim(_):
        try:
            return repo.claim(sample, request)[0]
        except AppError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, range(2)))
    assert results.count("meeting_busy") == 1
    with pytest.raises(AppError) as error:
        planning.edit(sample.id, DraftEdit(base_version=draft.version, tasks=draft.tasks))
    assert error.value.code == "meeting_busy"


def test_model_failure_does_not_create_tasks_and_can_retry(bundle):
    _, _, sample, planning, _, draft, repo, client, _ = bundle
    client.fail = True
    with pytest.raises(AppError):
        execute(bundle)
    failed = repo.get_execution(sample.id)
    assert failed.status == ExecutionStatus.failed
    assert planning.repository.get_run(failed.run_id).status == RunStatus.failed
    assert repo.list().total == 0
    client.fail = False
    successful = execute(bundle)
    assert successful.id == failed.id
    assert successful.run_id != failed.run_id
    assert len(successful.task_ids) == 1


def test_task_insert_failure_rolls_back_everything(bundle, monkeypatch):
    _, db, sample, planning, planner, _, repo, client, service = bundle
    planner.plans = [Plan(tasks=[task(), task(local_id="task_test", title="执行回归测试")])]
    draft = asyncio.run(planning.analyze(sample.id))
    insert = repo._insert_task
    def fail_after_insert(connection, item):
        insert(connection, item)
        if item.local_id == "task_test":
            raise sqlite3.IntegrityError("injected failure")
    monkeypatch.setattr(repo, "_insert_task", fail_after_insert)
    with pytest.raises(AppError) as error:
        asyncio.run(service.execute(sample.id, ExecuteRequest(draft_version=draft.version, confirm=True)))
    assert error.value.code == "execution_failed"
    assert repo.list().total == 0
    assert planning.get_draft(sample.id).status == DraftStatus.ready
    with db.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 0
    monkeypatch.setattr(repo, "_insert_task", insert)
    result = asyncio.run(service.execute(sample.id, ExecuteRequest(draft_version=draft.version, confirm=True)))
    assert len(result.task_ids) == 2


def test_tool_registry_rejects_unknown_tools_and_extra_arguments(bundle):
    *_, service = bundle
    with pytest.raises(AppError) as error:
        service.tools.invoke("delete_everything", {}, ToolContext("x", "y"))
    assert error.value.code == "tool_not_allowed"
    with pytest.raises(AppError) as error:
        service.tools.invoke("create_tasks", {"tasks": [{"title": "篡改任务"}]}, ToolContext("x", "y"))
    assert error.value.code == "invalid_tool_arguments"


def test_repeated_tool_call_is_also_idempotent(bundle):
    _, db, _, _, _, _, repo, _, _ = bundle
    result = execute(bundle)
    assert repo.create_tasks(ToolContext(result.id, result.run_id), CreateTasksArguments()) == result
    assert repo.list().total == 1


def test_concurrent_tool_invocations_are_idempotent(bundle):
    _, _, sample, _, _, draft, repo, _, _ = bundle
    claimed, _, _ = repo.claim(sample, ExecuteRequest(draft_version=draft.version, confirm=True))
    def invoke(_):
        return repo.create_tasks(ToolContext(claimed.id, claimed.run_id), CreateTasksArguments())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, range(2)))
    assert results[0] == results[1]
    assert repo.list().total == 1


def test_trace_write_failure_after_commit_does_not_report_execution_failed(bundle, monkeypatch):
    from app.core.artifacts import ArtifactRecord
    original = ArtifactRecord.write
    def write(self, filename, value):
        if filename == "02_tool_result.json":
            raise OSError("diagnostic disk failure")
        return original(self, filename, value)
    monkeypatch.setattr(ArtifactRecord, "write", write)
    result = execute(bundle)
    assert result.status == ExecutionStatus.succeeded
    assert bundle[6].list().total == 1


def test_database_v2_upgrade_preserves_planning_data(bundle):
    _, database, sample, planning, _, draft, repo, _, _ = bundle
    with database.connection() as connection:
        for table in ("run_requests", "events", "tool_calls", "tasks", "executions"):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("PRAGMA user_version=2")
    database.initialize()
    assert planning.get_draft(sample.id) == draft
    assert database.check()
    assert execute(bundle).status == ExecutionStatus.succeeded


def test_empty_plan_executes_without_fabricated_tasks(bundle):
    _, _, sample, planning, planner, _, repo, _, service = bundle
    planner.plans = [Plan()]
    draft = asyncio.run(planning.analyze(sample.id))
    result = asyncio.run(service.execute(sample.id, ExecuteRequest(draft_version=draft.version, confirm=True)))
    assert result.status == ExecutionStatus.succeeded
    assert result.task_ids == []
    assert repo.list().total == 0


def test_board_filter_and_status_roundtrip(bundle):
    _, _, sample, _, _, _, repo, _, _ = bundle
    result = execute(bundle)
    saved = repo.update(result.task_ids[0], TaskStatus.in_progress)
    assert saved.updated_at >= saved.created_at
    assert repo.list(status=TaskStatus.todo).total == 0
    assert repo.list(owner="张三", meeting_id=sample.id, status=TaskStatus.in_progress).total == 1
    assert repo.update(saved.id, TaskStatus.done).status == TaskStatus.done
    assert repo.update(saved.id, TaskStatus.todo).status == TaskStatus.todo
    with pytest.raises(AppError) as error:
        repo.update("missing", TaskStatus.done)
    assert error.value.status_code == 404


def test_restart_retains_tasks_and_interrupts_pending_execution(bundle):
    _, database, sample, planning, _, draft, repo, _, _ = bundle
    execution, _, _ = repo.claim(sample, ExecuteRequest(draft_version=draft.version, confirm=True))
    planning.repository.interrupt_active()
    repo.interrupt_pending()
    assert repo.get_execution(sample.id).status == ExecutionStatus.failed
    assert planning.repository.get_run(execution.run_id).status == RunStatus.interrupted
    successful = execute(bundle)
    database.initialize()
    assert TaskRepository(database).list().items[0].id == successful.task_ids[0]


def test_execution_api_and_confirmation_contract(tmp_path):
    config = Settings(_env_file=None, database_path=tmp_path / "api.sqlite3",
                      test_artifacts_path=tmp_path / "artifacts")
    app = create_app(config)
    with TestClient(app) as client:
        app.state.planning_service.agents = MeetingAgents(FakeClient())
        executor_client = ExecutorClient()
        app.state.execution_service.agent = ExecutorAgent(executor_client)
        saved = client.post("/api/meetings", json={
            "title": "例会", "meeting_date": "2026-10-21", "text": meeting().text,
        }).json()
        prefix = f"/api/meetings/{saved['id']}"
        assert client.get(prefix + "/execution").status_code == 404
        draft = client.post(prefix + "/analysis").json()
        assert client.post(prefix + "/execute", json={"draft_version": draft["version"], "confirm": False}).status_code == 422
        request = {"draft_version": draft["version"], "confirm": True}
        first = client.post(prefix + "/execute", json=request)
        assert first.status_code == 200, first.text
        assert client.post(prefix + "/execute", json=request).json() == first.json()
        assert executor_client.calls == 1
        assert client.get(prefix + "/execution").json() == first.json()
        page = client.get("/api/tasks", params={"meeting_id": saved["id"]}).json()
        assert page["total"] == 1
        task_id = page["items"][0]["id"]
        assert client.patch("/api/tasks/" + task_id, json={"status": "done"}).json()["status"] == "done"
        assert client.get("/api/tasks?status=done").json()["total"] == 1
        assert client.patch("/api/tasks/" + task_id, json={"status": "invalid"}).status_code == 422
        assert client.get("/api/tasks?limit=101").status_code == 422
        assert client.post(prefix + "/analysis").status_code == 409
    with TestClient(create_app(config)) as restarted:
        assert restarted.get("/api/tasks?status=done").json()["total"] == 1
