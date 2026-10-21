"""真实后台分析 / 执行、SSE 断线重连与重启补读，保留全过程。"""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.artifacts import ArtifactRecord
from app.core.config import Settings
from smoke_meetings import ROOT, server


def consume(base_url, run_id, record, label, after=0, stop_after=None):
    collected = []
    cursor = after
    # 不使用普通请求的 response hook：该 hook 会读完整响应，妨碍真实流式检查。
    with httpx.Client(base_url=base_url, trust_env=False, timeout=950) as client:
        with client.stream("GET", f"/api/runs/{run_id}/events",
                           headers={"Last-Event-ID": str(after)}) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            for line in response.iter_lines():
                if line.startswith("data: "):
                    event = json.loads(line[6:])
                    assert event["id"] > cursor
                    cursor = event["id"]
                    collected.append(event)
                    record.write(f"{label}_events.json", collected)
                    if stop_after and len(collected) >= stop_after:
                        break  # 主动断开，后台任务必须继续。
    record.write(f"{label}_stream.json", {"run_id": run_id, "after": after,
                                             "last_id": cursor, "count": len(collected)})
    return collected, cursor


def main():
    settings = Settings()
    record = ArtifactRecord(settings.test_artifacts_path / "api", "runs_smoke",
                            (settings.llm_api_key.get_secret_value(),))
    record.write("context.json", {"scope": "P6 background and real SSE reconnect", "model": settings.llm_model})
    try:
        with TemporaryDirectory(prefix="runs-p6-") as directory:
            database = Path(directory) / "pm.sqlite3"
            with server(database, record.path / "server.log", record=record,
                        request_timeout=settings.workflow_timeout + 30) as client:
                created = client.post("/api/meetings", json={
                    "title": "后台链路例会", "meeting_date": "2026-10-21",
                    "text": (ROOT / "examples/meeting.txt").read_text(encoding="utf-8"),
                })
                assert created.status_code == 201
                mid = created.json()["id"]
                prefix = f"/api/meetings/{mid}"
                submitted = client.post(prefix + "/runs", json={"type": "analysis"})
                assert submitted.status_code == 202, submitted.text
                analysis_run = submitted.json()
                assert analysis_run["status"] == "queued"
                first, cursor = consume(str(client.base_url), analysis_run["id"], record, "01_disconnected", stop_after=2)
                rest, last_id = consume(str(client.base_url), analysis_run["id"], record, "02_reconnected", after=cursor)
                assert rest and rest[-1]["type"] == "run_completed"
                assert set(e["id"] for e in first).isdisjoint(e["id"] for e in rest)
                roles = {e["agent"] for e in first + rest if e["type"] == "agent_completed"}
                assert roles == {"manager", "planner", "reviewer"}
                draft = client.get(prefix + "/draft").json()
                record.write("03_draft.json", draft)
                assert draft["status"] == "ready", "模型审核未通过，请查看对应工作流记录"
                assert len(draft["tasks"]) == 2
                execution_request = {"type": "execution", "draft_version": draft["version"], "confirm": True,
                                     "acknowledgement": "确认后台创建任务"}
                execution = client.post(prefix + "/runs", json=execution_request)
                assert execution.status_code == 202, execution.text
                execution_run = execution.json()
                events, execution_last = consume(str(client.base_url), execution_run["id"], record, "04_execution")
                assert events[-1]["type"] == "run_completed"
                assert events[-2]["type"] == "tool_result"
                assert events[-2]["payload"]["count"] == 2
                assert client.get("/api/tasks", params={"meeting_id": mid}).json()["total"] == 2
                assert client.post(prefix + "/runs", json=execution_request).json()["id"] == execution_run["id"]
                full, _ = consume(str(client.base_url), analysis_run["id"], record, "05_full_replay")
                assert [e["id"] for e in full] == [e["id"] for e in first + rest]
            with server(database, record.path / "restart.log", record=record) as client:
                replay, _ = consume(str(client.base_url), execution_run["id"], record, "06_restart_replay")
                assert replay == events
                empty, _ = consume(str(client.base_url), execution_run["id"], record, "07_caught_up", after=execution_last)
                assert empty == []
                assert client.get("/api/tasks", params={"meeting_id": mid}).json()["total"] == 2
            record.finish("succeeded", analysis_run_id=analysis_run["id"], execution_run_id=execution_run["id"],
                          analysis_events=len(full), execution_events=len(events), reconnect_duplicates=0,
                          task_count=2)
        print("P6 真实联调通过：202 后台提交、SSE 主动断线重连、工具执行、重启后补读与零重复任务。")
    except Exception as exc:
        record.finish("failed", error_type=type(exc).__name__, message=str(exc))
        raise
    finally:
        print(f"本次验证记录：{record.path}")


if __name__ == "__main__":
    main()
