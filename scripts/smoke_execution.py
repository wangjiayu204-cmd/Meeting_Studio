"""P5 真实四角色 HTTP 闭环；保留请求、响应和执行工具记录。"""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.artifacts import ArtifactRecord
from app.core.config import Settings
from smoke_meetings import ROOT, server


def main():
    settings = Settings()
    record = ArtifactRecord(settings.test_artifacts_path / "api", "execution_smoke",
                            (settings.llm_api_key.get_secret_value(),))
    record.write("context.json", {"scope": "P5 real four-agent workflow", "model": settings.llm_model})
    try:
        with TemporaryDirectory(prefix="execution-p5-") as directory:
            database = Path(directory) / "pm.sqlite3"
            with server(database, record.path / "server.log", record=record,
                        request_timeout=settings.workflow_timeout + 30) as client:
                response = client.post("/api/meetings", json={
                    "title": "登录模块迭代例会", "meeting_date": "2026-10-21",
                    "text": (ROOT / "examples/meeting.txt").read_text(encoding="utf-8"),
                })
                assert response.status_code == 201, response.text
                meeting_id = response.json()["id"]
                prefix = f"/api/meetings/{meeting_id}"
                response = client.post(prefix + "/analysis")
                assert response.status_code == 200, response.text
                draft = response.json()
                record.write("01_reviewed_draft.json", draft)
                assert draft["status"] == "ready", "审核尚未通过，请查看工作流记录"
                assert len(draft["tasks"]) == 2
                assert {(t["owner"], t["due_date"]) for t in draft["tasks"]} == {
                    ("张三", "2026-10-23"), ("李四", "2026-10-26"),
                }
                stale = client.post(prefix + "/execute", json={"draft_version": draft["version"] - 1, "confirm": True})
                assert stale.status_code == 409
                request = {"draft_version": draft["version"], "confirm": True,
                           "acknowledgement": "已核对原文、负责人和截止日期，确认创建看板任务"}
                response = client.post(prefix + "/execute", json=request)
                assert response.status_code == 200, response.text
                execution = response.json()
                record.write("02_execution.json", execution)
                assert execution["status"] == "succeeded" and execution["tool_name"] == "create_tasks"
                assert len(execution["task_ids"]) == 2
                assert client.post(prefix + "/execute", json=request).json() == execution
                assert client.get(prefix + "/execution").json() == execution
                page = client.get("/api/tasks", params={"meeting_id": meeting_id}).json()
                assert page["total"] == 2
                assert {t["id"] for t in page["items"]} == set(execution["task_ids"])
                task_id = page["items"][0]["id"]
                changed = client.patch(f"/api/tasks/{task_id}", json={"status": "in_progress"})
                assert changed.status_code == 200, changed.text
                changed = client.patch(f"/api/tasks/{task_id}", json={"status": "done"})
                assert changed.json()["status"] == "done"
                assert client.get("/api/tasks", params={"meeting_id": meeting_id, "status": "done"}).json()["total"] == 1
                assert client.post(prefix + "/analysis").status_code == 409
                assert client.get(prefix + "/draft").json()["status"] == "confirmed"
                run = client.get("/api/runs/" + execution["run_id"]).json()
                assert run["type"] == "execution" and run["status"] == "succeeded"
                record.write("03_board.json", client.get("/api/tasks", params={"meeting_id": meeting_id}).json())
            with server(database, record.path / "restart.log", record=record) as client:
                page = client.get("/api/tasks", params={"meeting_id": meeting_id}).json()
                assert page["total"] == 2
                assert sum(t["status"] == "done" for t in page["items"]) == 1
                assert client.post(prefix + "/execute", json=request).json() == execution
                assert client.get("/api/tasks", params={"meeting_id": meeting_id}).json()["total"] == 2
            record.finish("succeeded", meeting_id=meeting_id, execution_id=execution["id"],
                          run_id=execution["run_id"], task_count=2, duplicate_count=0)
        print("P5 真实联调通过：四角色、工具落库、重复确认幂等、看板更新、重启保留。")
    except Exception as exc:
        record.finish("failed", error_type=type(exc).__name__, message=str(exc))
        raise
    finally:
        print(f"本次验证记录：{record.path}")


if __name__ == "__main__":
    main()
