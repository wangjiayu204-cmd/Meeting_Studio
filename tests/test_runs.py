import asyncio
import json
from time import monotonic, sleep

from fastapi.testclient import TestClient
import pytest

from app.agents.executor import ExecutorAgent
from app.agents.meeting import MeetingAgents
from app.core.config import Settings
from app.core.errors import AppError
from app.domain.schemas import AnalysisSubmission
from app.domain.states import RunStatus, RunType
from app.main import create_app
from app.repositories.events import EventRepository
from test_execution import ExecutorClient
from test_planning import FakeClient, meeting


class SlowClient(FakeClient):
    async def generate_structured(self, *args, **kwargs):
        await asyncio.sleep(0.03)
        return await super().generate_structured(*args, **kwargs)


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(_env_file=None, database_path=tmp_path / "runs.sqlite3",
                               test_artifacts_path=tmp_path / "artifacts",
                               sse_poll_interval=0.01, sse_heartbeat_seconds=0.02))


def configure(app, slow=False):
    fake = SlowClient() if slow else FakeClient()
    app.state.planning_service.agents = MeetingAgents(fake)
    app.state.execution_service.agent = ExecutorAgent(ExecutorClient())
    return fake


def create(client):
    response = client.post("/api/meetings", json={
        "title": "后台测试会议", "meeting_date": "2026-10-21", "text": meeting().text,
    })
    assert response.status_code == 201
    return response.json()["id"]


def wait_run(client, run_id):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        run = client.get("/api/runs/" + run_id).json()
        if run["status"] not in {"running", "queued"}:
            return run
        sleep(0.01)
    pytest.fail("后台运行未结束")


def parse_sse(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_202_background_analysis_and_persistent_sse(app):
    with TestClient(app) as client:
        configure(app, slow=True)
        meeting_id = create(client)
        response = client.post(f"/api/meetings/{meeting_id}/runs", json={"type": "analysis"})
        assert response.status_code == 202, response.text
        run = response.json()
        assert run["status"] == "queued"
        assert run["started_at"] is None
        stream = client.get(f"/api/runs/{run['id']}/events")
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(stream.text)
        assert [e["type"] for e in events[:2]] == ["run_queued", "run_started"]
        assert events[-1]["type"] == "run_completed"
        assert [e["agent"] for e in events if e["type"] == "agent_started"] == ["manager", "planner", "reviewer"]
        assert all("think" not in e["message"] for e in events)
        assert len({e["id"] for e in events}) == len(events)
        assert [e["id"] for e in events] == sorted(e["id"] for e in events)
        assert "heartbeat" in stream.text
        assert wait_run(client, run["id"])["status"] == "succeeded"
        assert client.get(f"/api/meetings/{meeting_id}/draft").json()["status"] == "ready"
        history = client.get(f"/api/meetings/{meeting_id}/runs").json()
        assert history[0]["id"] == run["id"]


def test_replay_after_and_header_no_duplicates_including_multiple_pages(app):
    with TestClient(app) as client:
        configure(app)
        meeting_id = create(client)
        # 手工创建活跃运行以测试大于单次查询 100 条的补读。
        run = app.state.planning_repository.start(meeting_id, RunType.analysis)
        for i in range(205):
            app.state.event_repository.append(run.id, "agent_completed", "步骤完成", "planner", {"index": i})
        app.state.planning_repository.finish(run.id, RunStatus.succeeded)
        full = parse_sse(client.get(f"/api/runs/{run.id}/events").text)
        assert len(full) == 207
        middle = full[100]["id"]
        replay = parse_sse(client.get(f"/api/runs/{run.id}/events?after={full[10]['id']}",
                                      headers={"Last-Event-ID": str(middle)}).text)
        assert replay == full[101:]
        empty = client.get(f"/api/runs/{run.id}/events?after={full[-1]['id']}")
        assert parse_sse(empty.text) == []
        for header in ("-1", "bad", "9" * 30):
            assert client.get(f"/api/runs/{run.id}/events", headers={"Last-Event-ID": header}).status_code == 422
        assert client.get(f"/api/runs/{run.id}/events?after=-1").status_code == 422
        assert client.get(f"/api/runs/{run.id}/events?after=999999999999999999999999").status_code == 422
        assert client.get("/api/runs/missing/events").status_code == 404


def test_background_execution_and_review(app):
    with TestClient(app) as client:
        configure(app)
        meeting_id = create(client)
        prefix = f"/api/meetings/{meeting_id}"
        run = client.post(prefix + "/runs", json={"type": "analysis"}).json()
        assert wait_run(client, run["id"])["status"] == "succeeded"
        draft = client.get(prefix + "/draft").json()
        edited = client.put(prefix + "/draft", json={"base_version": draft["version"], "tasks": draft["tasks"]}).json()
        review = client.post(prefix + "/runs", json={"type": "review", "draft_version": edited["version"]})
        assert review.status_code == 202
        assert wait_run(client, review.json()["id"])["status"] == "succeeded"
        draft = client.get(prefix + "/draft").json()
        payload = {"type": "execution", "draft_version": draft["version"], "confirm": True}
        execution = client.post(prefix + "/runs", json=payload)
        assert execution.status_code == 202
        assert wait_run(client, execution.json()["id"])["status"] == "succeeded"
        events = parse_sse(client.get(f"/api/runs/{execution.json()['id']}/events").text)
        assert events[-2]["type"] == "tool_result"
        assert events[-1]["type"] == "run_completed"
        assert sum(e["type"] == "run_completed" for e in events) == 1
        assert client.get("/api/tasks").json()["total"] == 1
        duplicate = client.post(prefix + "/runs", json=payload)
        assert duplicate.json()["id"] == execution.json()["id"]
        assert client.get("/api/tasks").json()["total"] == 1


def test_failed_analysis_retry_creates_new_run(app):
    with TestClient(app) as client:
        fake = configure(app)
        fake.fail = "reviewer"
        meeting_id = create(client)
        first = client.post(f"/api/meetings/{meeting_id}/runs", json={"type": "analysis"}).json()
        assert wait_run(client, first["id"])["status"] == "failed"
        events = parse_sse(client.get(f"/api/runs/{first['id']}/events").text)
        assert events[-1]["type"] == "run_failed"
        fake.fail = None
        retry = client.post(f"/api/runs/{first['id']}/retry", json={})
        assert retry.status_code == 202
        assert retry.json()["id"] != first["id"]
        assert wait_run(client, retry.json()["id"])["status"] == "succeeded"
        with app.state.database.connection() as connection:
            assert connection.execute("SELECT retry_of FROM run_requests WHERE run_id=?",
                                      (retry.json()["id"],)).fetchone()[0] == first["id"]
        assert client.post(f"/api/runs/{retry.json()['id']}/retry", json={}).status_code == 409


def test_execution_retry_requires_new_confirmation(app):
    with TestClient(app) as client:
        configure(app)
        meeting_id = create(client)
        prefix = f"/api/meetings/{meeting_id}"
        draft = client.post(prefix + "/analysis").json()
        executor = ExecutorClient()
        executor.fail = True
        app.state.execution_service.agent = ExecutorAgent(executor)
        failed = client.post(prefix + "/runs", json={
            "type": "execution", "draft_version": draft["version"], "confirm": True,
        }).json()
        assert wait_run(client, failed["id"])["status"] == "failed"
        assert client.post(f"/api/runs/{failed['id']}/retry", json={}).status_code == 422
        executor.fail = False
        retry = client.post(f"/api/runs/{failed['id']}/retry", json={"confirm": True, "acknowledgement": "再次确认"})
        assert retry.status_code == 202
        assert wait_run(client, retry.json()["id"])["status"] == "succeeded"
        assert client.get("/api/tasks").json()["total"] == 1


def test_scheduler_concurrency_queue_bound_and_same_meeting_lock(app):
    async def scenario():
        manager = app.state.run_manager
        manager.settings.max_pending_runs = 2
        ids = [app.state.meeting_service.repository.create(meeting()).id for _ in range(3)]
        gate = asyncio.Event()
        entered = []
        original = manager.planning.perform_prepared
        async def gated(run):
            entered.append(run.id)
            await gate.wait()
            return await original(run)
        manager.planning.perform_prepared = gated
        first = await manager.submit(ids[0], AnalysisSubmission())
        for _ in range(100):
            if entered:
                break
            await asyncio.sleep(0.005)
        with pytest.raises(AppError) as error:
            await manager.submit(ids[0], AnalysisSubmission())
        assert error.value.code == "meeting_busy"
        second = await manager.submit(ids[1], AnalysisSubmission())
        await asyncio.sleep(0.03)
        assert entered == [first.id]
        assert manager.repository.get_run(second.id).status == RunStatus.queued
        with pytest.raises(AppError) as error:
            await manager.submit(ids[2], AnalysisSubmission())
        assert error.value.code == "run_queue_full"
        gate.set()
        await asyncio.gather(*list(manager.tasks.values()))
        assert len(entered) == 2
    with TestClient(app) as client:
        configure(app)
        client.portal.call(scenario)


def test_shutdown_marks_running_and_queued_interrupted_with_replay(app):
    async def scenario():
        manager = app.state.run_manager
        async def slow(run):
            await asyncio.sleep(30)
        manager.planning.perform_prepared = slow
        ids = [app.state.meeting_service.repository.create(meeting()).id for _ in range(2)]
        runs = [await manager.submit(mid, AnalysisSubmission()) for mid in ids]
        await asyncio.sleep(0.03)
        return [run.id for run in runs]
    with TestClient(app) as client:
        configure(app)
        ids = client.portal.call(scenario)
    with TestClient(create_app(app.state.settings)) as restarted:
        configure(restarted.app)
        for run_id in ids:
            assert restarted.get("/api/runs/" + run_id).json()["status"] == "interrupted"
            events = parse_sse(restarted.get(f"/api/runs/{run_id}/events").text)
            assert events[-1]["type"] == "run_interrupted"
        new = restarted.post(f"/api/runs/{ids[0]}/retry", json={})
        assert new.status_code == 202
        assert wait_run(restarted, new.json()["id"])["status"] == "succeeded"


def test_stale_review_retry_does_not_review_new_version(app):
    with TestClient(app) as client:
        fake = configure(app)
        meeting_id = create(client)
        prefix = f"/api/meetings/{meeting_id}"
        draft = client.post(prefix + "/analysis").json()
        fake.fail = "reviewer"
        run = client.post(prefix + "/runs", json={"type": "review", "draft_version": draft["version"]}).json()
        assert wait_run(client, run["id"])["status"] == "failed"
        updated = client.put(prefix + "/draft", json={"tasks": draft["tasks"], "base_version": draft["version"]})
        assert updated.status_code == 200
        assert client.post(f"/api/runs/{run['id']}/retry", json={}).status_code == 409


def test_startup_reconciles_crashed_run_and_validates_input(app):
    with TestClient(app) as client:
        configure(app)
        meeting_id = create(client)
        prefix = f"/api/meetings/{meeting_id}"
        assert client.post(prefix + "/runs", json={"type": "review", "draft_version": 1}).status_code == 404
        assert client.post(prefix + "/runs", json={"type": "execution", "confirm": False, "draft_version": 1}).status_code == 422
        assert client.post(prefix + "/runs", json={"type": "invalid"}).status_code == 422
        assert client.post("/api/meetings/missing/runs", json={"type": "analysis"}).status_code == 404
        run = app.state.planning_repository.start(meeting_id, RunType.analysis, queued=True)
    with TestClient(create_app(app.state.settings)) as restarted:
        assert restarted.get("/api/runs/" + run.id).json()["status"] == "interrupted"
        stream = parse_sse(restarted.get(f"/api/runs/{run.id}/events").text)
        assert stream[-1]["type"] == "run_interrupted"


def test_v3_migration_keeps_existing_runs(app):
    with TestClient(app) as client:
        configure(app)
        mid = create(client)
        draft = client.post(f"/api/meetings/{mid}/analysis").json()
    database = app.state.database
    with database.connection() as connection:
        connection.execute("DROP TABLE run_requests")
        connection.execute("DROP TABLE events")
        connection.execute("PRAGMA user_version=3")
    database.initialize()
    assert database.check()
    assert app.state.planning_repository.get_run(draft["run_id"]).status == RunStatus.succeeded


def test_cancel_execution_waiting_for_model_marks_interrupted(app):
    with TestClient(app) as client:
        configure(app)
        mid = create(client)
        draft = client.post(f"/api/meetings/{mid}/analysis").json()
        async def slow(draft, tools):
            await asyncio.sleep(30)
        app.state.execution_service.agent.choose = slow
        submitted = client.post(f"/api/meetings/{mid}/runs", json={
            "type": "execution", "confirm": True, "draft_version": draft["version"],
        }).json()
        deadline = monotonic() + 2
        while monotonic() < deadline:
            if any(e.type.value == "agent_started" for e in app.state.event_repository.after(submitted["id"], 0)):
                break
            sleep(0.01)
        else:
            pytest.fail("Executor 未开始")
    with TestClient(create_app(app.state.settings)) as restarted:
        assert restarted.get("/api/runs/" + submitted["id"]).json()["status"] == "interrupted"
        events = parse_sse(restarted.get(f"/api/runs/{submitted['id']}/events").text)
        assert events[-1]["type"] == "run_interrupted"
        assert restarted.get("/api/tasks").json()["total"] == 0


def test_legacy_analysis_emits_events_without_background_submission(app):
    with TestClient(app) as client:
        configure(app)
        mid = create(client)
        response = client.post(f"/api/meetings/{mid}/analysis")
        assert response.status_code == 200
        run_id = response.json()["run_id"]
        events = parse_sse(client.get(f"/api/runs/{run_id}/events").text)
        assert events[0]["type"] == "run_started"
        assert events[-1]["type"] == "run_completed"
        assert len([e for e in events if e["type"] == "agent_started"]) == 3
