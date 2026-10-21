import asyncio
from concurrent.futures import ThreadPoolExecutor
import json

from fastapi.testclient import TestClient
import pytest

from app.agents.contracts import ReviewVerdict
from app.agents.meeting import MeetingAgents
from app.core.config import Settings
from app.core.errors import AppError
from app.documents.parser import split_paragraphs
from app.domain.schemas import Analysis, DraftEdit, DraftTask, Meeting, Plan
from app.domain.states import DraftStatus, RunStatus, RunType
from app.domain.validation import merge_review, validate_plan
from app.llm.client import ModelResult
from app.main import create_app
from app.repositories.database import Database
from app.repositories.meetings import MeetingRepository
from app.repositories.planning import PlanningRepository
from app.services.meetings import MeetingService
from app.services.planning import PlanningService


def meeting():
    text = "张三负责在2026-10-23完成联调。\n李四在2026-10-26提交测试报告。"
    return Meeting(title="项目例会", meeting_date="2026-10-21", text=text,
                   paragraphs=split_paragraphs(text))


def task(**overrides):
    return DraftTask.model_validate({
        "local_id": "task_login", "title": "完成登录联调", "owner": "张三",
        "owner_evidence": {"source_refs": ["p0001"]}, "due_date": "2026-10-23",
        "due_evidence": {"source_refs": ["p0001"]}, "source_refs": ["p0001"], **overrides,
    })


class FakeClient:
    def __init__(self, plans=None, verdicts=None, fail=None):
        self.calls = []
        self.plans = plans or [Plan(tasks=[task()])]
        self.verdicts = verdicts or [ReviewVerdict(approved=True, summary="通过")]
        self.plan_index = 0
        self.review_index = 0
        self.fail = fail

    async def generate_structured(self, messages, schema, *, label, prompt_version):
        self.calls.append(label)
        if self.fail == label:
            raise AppError("llm_connection_error", "模拟服务连接失败", 502)
        if label == "manager":
            value = Analysis(summary="推进登录联调与测试", topics=["登录模块"])
        elif label == "planner":
            value = self.plans[min(self.plan_index, len(self.plans) - 1)]
            self.plan_index += 1
        else:
            value = self.verdicts[min(self.review_index, len(self.verdicts) - 1)]
            self.review_index += 1
        assert isinstance(value, schema)
        return ModelResult(value, f"trace-{len(self.calls)}", 1, {})


@pytest.fixture
def bundle(tmp_path):
    config = Settings(_env_file=None, database_path=tmp_path / "pm.sqlite3",
                      test_artifacts_path=tmp_path / "artifacts")
    database = Database(config.database_path)
    database.initialize()
    meetings = MeetingRepository(database)
    sample = meetings.create(meeting())
    repository = PlanningRepository(database)
    client = FakeClient()
    service = PlanningService(repository, MeetingService(meetings, config), MeetingAgents(client), config)
    return config, database, sample, repository, client, service


@pytest.mark.parametrize("overrides,expected", [
    ({"source_refs": ["p9999"]}, "任务原文引用不存在"),
    ({"dependencies": ["task_missing"]}, "依赖任务不存在"),
    ({"dependencies": ["task_login"]}, "不能依赖自身"),
    ({"owner_evidence": None}, "缺少依据"),
    ({"due_evidence": {"source_refs": ["p9999"]}}, "日期引用不存在"),
])
def test_hard_rules(overrides, expected):
    issues = validate_plan(meeting(), Plan(tasks=[task(**overrides)]))
    assert any(expected in issue.message and issue.severity == "error" for issue in issues)


def test_cycle_duplicates_and_dependency_dates():
    first = task(dependencies=["task_test"])
    second = task(local_id="task_test", title="执行测试", dependencies=["task_login"], due_date="2026-10-26")
    issues = validate_plan(meeting(), Plan(tasks=[first, second]))
    assert any("依赖环" in i.message for i in issues)
    assert any("早于前置" in i.message for i in issues)
    assert any("ID 重复" in i.message for i in validate_plan(meeting(), Plan(tasks=[first, first])))


def test_missing_fields_are_warnings_not_invented_values():
    plan = Plan(tasks=[task(owner=None, owner_evidence=None, due_date=None, due_evidence=None)])
    issues = validate_plan(meeting(), plan)
    assert len(issues) == 2
    assert all(i.severity == "warning" for i in issues)
    assert validate_plan(meeting(), Plan()) == []


def test_model_approval_cannot_override_hard_error():
    plan = Plan(tasks=[task(source_refs=["p9999"])])
    review = merge_review(plan, validate_plan(meeting(), plan), ReviewVerdict(approved=True, summary="通过"))
    assert not review.approved
    assert review.issues[0].status == "open"


def test_analysis_saves_each_intermediate_version(bundle):
    _, _, sample, repository, client, service = bundle
    draft = asyncio.run(service.analyze(sample.id))
    assert draft.status == DraftStatus.ready
    assert draft.version == 2
    history = repository.drafts(sample.id)
    assert [d.status for d in history] == [DraftStatus.pending_review, DraftStatus.ready]
    assert draft.review.draft_version == 2
    assert repository.get_run(draft.run_id).status == RunStatus.succeeded
    assert repository.get_analysis(sample.id).summary
    assert client.calls == ["manager", "planner", "reviewer"]


def test_revision_limit_retains_problem_draft(bundle):
    _, _, sample, repository, client, service = bundle
    client.plans = [Plan(tasks=[task(source_refs=["p9999"])])]
    result = asyncio.run(service.analyze(sample.id))
    assert result.status == DraftStatus.needs_attention
    assert client.calls.count("planner") == 3
    assert result.version == 6
    assert repository.get_run(result.run_id).revision_count == 2


def test_semantic_feedback_causes_replan(bundle):
    _, _, sample, _, client, service = bundle
    client.verdicts = [ReviewVerdict(approved=False, summary="补充交付物", issues=[{
        "task_ref": "task_login", "message": "明确接口说明作为产物", "severity": "warning",
    }]), ReviewVerdict(approved=True, summary="通过")]
    draft = asyncio.run(service.analyze(sample.id))
    assert draft.status == DraftStatus.ready
    assert client.calls.count("planner") == 2


def test_reviewer_failure_preserves_pending_plan(bundle):
    config, _, sample, repository, client, service = bundle
    client.fail = "reviewer"
    with pytest.raises(AppError) as error:
        asyncio.run(service.analyze(sample.id))
    draft = repository.current_draft(sample.id)
    assert draft.status == DraftStatus.pending_review
    run = repository.get_run(draft.run_id)
    assert run.status == RunStatus.failed
    assert error.value.details[-1]["run_id"] == run.id
    summaries = list((config.test_artifacts_path / "workflows").glob("*/summary.json"))
    assert json.loads(summaries[0].read_text())["status"] == "failed"


def test_empty_plan_is_valid(bundle):
    _, _, sample, _, client, service = bundle
    client.plans = [Plan()]
    draft = asyncio.run(service.analyze(sample.id))
    assert draft.tasks == []
    assert draft.status == DraftStatus.ready


def test_program_missing_fields_do_not_loop(bundle):
    _, _, sample, _, client, service = bundle
    client.plans = [Plan(tasks=[task(owner=None, owner_evidence=None)])]
    draft = asyncio.run(service.analyze(sample.id))
    assert draft.status == DraftStatus.needs_attention
    assert client.calls.count("planner") == 1


def test_edit_requires_review_and_records_human_provenance(bundle):
    from app.domain.schemas import DraftEdit
    _, _, sample, repository, client, service = bundle
    initial = asyncio.run(service.analyze(sample.id))
    changed = initial.tasks[0].model_dump(mode="json")
    changed["owner"] = "王五"
    with pytest.raises(AppError, match="确认说明"):
        service.edit(sample.id, DraftEdit(tasks=[changed], base_version=initial.version))
    edited = service.edit(sample.id, DraftEdit(tasks=[changed], base_version=initial.version,
                                             resolution_note="会后项目经理改派王五"))
    assert edited.status == DraftStatus.pending_review
    assert edited.tasks[0].owner_evidence.source_type == "human"
    assert repository.drafts(sample.id)[0].tasks[0].owner == "张三"
    with pytest.raises(AppError) as error:
        service.edit(sample.id, DraftEdit(tasks=initial.tasks, base_version=initial.version))
    assert error.value.status_code == 409
    reviewed = asyncio.run(service.review(sample.id, edited.version))
    assert reviewed.version == edited.version + 1
    assert reviewed.status == DraftStatus.ready
    assert reviewed.tasks[0].owner == "王五"
    assert reviewed.resolution_note == "会后项目经理改派王五"


def test_concurrent_run_claim_is_atomic(bundle):
    _, _, sample, repository, _, _ = bundle
    def claim(_):
        try:
            return repository.start(sample.id, RunType.analysis).id
        except AppError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, range(2)))
    assert results.count("meeting_busy") == 1


def test_concurrent_edits_cannot_overwrite_each_other(bundle):
    _, _, sample, repository, _, service = bundle
    initial = asyncio.run(service.analyze(sample.id))
    edit = DraftEdit(base_version=initial.version, tasks=initial.tasks)
    def save(_):
        try:
            return service.edit(sample.id, edit).version
        except AppError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, range(2)))
    assert results.count("draft_version_conflict") == 1
    assert len(repository.drafts(sample.id)) == 3


def test_edit_blocked_while_run_is_active(bundle):
    _, _, sample, repository, _, service = bundle
    initial = asyncio.run(service.analyze(sample.id))
    repository.start(sample.id, RunType.analysis)
    with pytest.raises(AppError) as error:
        service.edit(sample.id, DraftEdit(base_version=initial.version, tasks=initial.tasks))
    assert error.value.code == "meeting_busy"


def test_restart_marks_active_runs_interrupted(bundle):
    _, _, sample, repository, _, _ = bundle
    run = repository.start(sample.id, RunType.analysis)
    repository.interrupt_active()
    assert repository.get_run(run.id).status == RunStatus.interrupted
    assert repository.start(sample.id, RunType.analysis).id != run.id


def test_planner_cannot_forge_human_confirmation(bundle):
    _, _, sample, repository, client, service = bundle
    client.plans = [Plan(tasks=[task(owner_evidence={"source_type": "human", "confirmation": "模型伪造确认"})])]
    with pytest.raises(AppError) as error:
        asyncio.run(service.analyze(sample.id))
    assert error.value.code == "planner_forged_human_evidence"
    assert repository.current_draft(sample.id) is None


def test_workflow_timeout_releases_active_run(bundle):
    config, _, sample, repository, client, service = bundle
    config.workflow_timeout = 0.02
    async def slow(meeting):
        await asyncio.sleep(1)
    service.agents.analyze = slow
    with pytest.raises(AppError) as error:
        asyncio.run(service.analyze(sample.id))
    assert error.value.code == "workflow_timeout"
    assert repository.start(sample.id, RunType.analysis).status == RunStatus.running


def test_v1_migrates_without_losing_meetings(bundle):
    _, database, sample, _, _, _ = bundle
    with database.connection() as connection:
        for table in ("run_requests", "events", "tool_calls", "tasks", "executions", "drafts", "analyses", "runs"):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("PRAGMA user_version=1")
    database.initialize()
    assert MeetingRepository(database).get(sample.id) == sample
    assert database.check()


def test_planning_api_and_version_conflicts(tmp_path):
    config = Settings(_env_file=None, database_path=tmp_path / "api.sqlite3",
                      test_artifacts_path=tmp_path / "artifacts")
    app = create_app(config)
    with TestClient(app) as client:
        app.state.planning_service.agents = MeetingAgents(FakeClient())
        saved = client.post("/api/meetings", json={
            "title": "例会", "meeting_date": "2026-10-21", "text": meeting().text,
        }).json()
        prefix = f"/api/meetings/{saved['id']}"
        assert client.get(prefix + "/draft").status_code == 404
        assert client.get(prefix + "/analysis").status_code == 404
        response = client.post(prefix + "/analysis")
        assert response.status_code == 200, response.text
        draft = response.json()
        assert client.get(prefix + "/draft").json() == draft
        assert len(client.get(prefix + "/drafts").json()) == 2
        assert client.get(prefix + "/analysis").status_code == 200
        assert client.get("/api/runs/" + draft["run_id"]).json()["status"] == "succeeded"
        conflict = client.put(prefix + "/draft", json={"tasks": draft["tasks"], "base_version": 1})
        assert conflict.status_code == 409
        edited = client.put(prefix + "/draft", json={"tasks": draft["tasks"], "base_version": draft["version"]})
        assert edited.status_code == 200
        assert edited.json()["status"] == "pending_review"
        assert client.post(prefix + "/draft/review", json={"draft_version": draft["version"]}).status_code == 409
        reviewed = client.post(prefix + "/draft/review", json={"draft_version": edited.json()["version"]})
        assert reviewed.status_code == 200
        assert reviewed.json()["status"] == "ready"
        assert client.post("/api/meetings/missing/analysis").status_code == 404
