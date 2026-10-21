"""P7 真实 HTTP / vLLM：导出文件、跟进统计与重启一致性。"""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.artifacts import ArtifactRecord
from app.core.config import Settings
from smoke_meetings import ROOT, server


def main():
    settings = Settings()
    record = ArtifactRecord(settings.test_artifacts_path / "api", "exports_smoke",
                            (settings.llm_api_key.get_secret_value(),))
    record.write("context.json", {"scope": "P7 real JSON/ICS/followup", "model": settings.llm_model})
    try:
        with TemporaryDirectory(prefix="exports-p7-") as directory:
            database = Path(directory) / "pm.sqlite3"
            with server(database, record.path / "server.log", record=record,
                        request_timeout=settings.workflow_timeout + 30) as client:
                response = client.post("/api/meetings", json={
                    "title": "登录模块迭代例会", "meeting_date": "2026-10-21", "timezone": "Asia/Shanghai",
                    "text": (ROOT / "examples/meeting.txt").read_text(encoding="utf-8"),
                })
                assert response.status_code == 201, response.text
                meeting_id = response.json()["id"]
                prefix = f"/api/meetings/{meeting_id}"
                assert client.get(prefix + "/export?format=ics").status_code == 409
                response = client.post(prefix + "/analysis")
                assert response.status_code == 200, response.text
                draft = response.json()
                record.write("01_reviewed_draft.json", draft)
                assert draft["status"] == "ready", "草稿尚未就绪，请查看模型与工作流记录"
                assert len(draft["tasks"]) == 2
                response = client.post(prefix + "/execute", json={
                    "draft_version": draft["version"], "confirm": True,
                    "acknowledgement": "已核对原文、负责人和日期，确认创建并验证导出",
                })
                assert response.status_code == 200, response.text
                execution = response.json()
                assert execution["status"] == "succeeded"
                record.write("02_execution.json", execution)
                response = client.patch("/api/tasks/" + execution["task_ids"][0], json={"status": "done"})
                assert response.status_code == 200, response.text
                response = client.get(prefix + "/export?format=json")
                assert response.status_code == 200, response.text
                exported = response.json()
                assert len(exported["tasks"]) == 2
                assert {t["id"] for t in exported["tasks"]} == set(execution["task_ids"])
                record.write("03_meeting_export.json", exported)
                response = client.get(prefix + "/export?format=ics")
                assert response.status_code == 200, response.text
                calendar = response.content
                record.write("04_tasks.ics", calendar.decode("utf-8"))
                assert response.headers["x-pm-task-count"] == "2"
                assert response.headers["x-pm-skipped-count"] == "0"
                assert calendar.count(b"BEGIN:VEVENT\r\n") == 2
                assert all(len(line) <= 75 for line in calendar.split(b"\r\n"))
                unfolded = calendar.decode().replace("\r\n ", "")
                for item in exported["tasks"]:
                    assert f"UID:{item['id']}@lightweight-pm.local" in unfolded
                    assert "DTSTART;VALUE=DATE:" + item["due_date"].replace("-", "") in unfolded
                response = client.get(prefix + "/followup")
                assert response.status_code == 200, response.text
                followup = response.json()
                record.write("05_followup.json", followup)
                assert followup["timezone"] == "Asia/Shanghai"
                assert followup["counts"]["total"] == 2
                assert followup["counts"]["completed"] == followup["counts"]["pending"] == 1
                assert followup["counts"]["missing_owner"] == followup["counts"]["missing_due_date"] == 0
                assert any(q["origin"] == "analysis" for q in followup["clarifications"]), "未决议题应保留"
            with server(database, record.path / "restart.log", record=record) as client:
                assert client.get(prefix + "/export").json()["tasks"] == exported["tasks"]
                assert client.get(prefix + "/followup").json()["counts"] == followup["counts"]
                response = client.get(prefix + "/export?format=ics")
                assert response.status_code == 200 and response.content.count(b"BEGIN:VEVENT\r\n") == 2
                record.write("06_restart_followup.json", client.get(prefix + "/followup").json())
            record.finish("succeeded", meeting_id=meeting_id, execution_id=execution["id"],
                          task_count=2, calendar_event_count=2, counts=followup["counts"],
                          clarification_count=len(followup["clarifications"]), restart_verified=True)
        print("P7 真实联调通过：JSON / ICS 下载、跟进统计、未决议题及重启保留。")
    except Exception as exc:
        record.finish("failed", error_type=type(exc).__name__, message=str(exc))
        raise
    finally:
        print(f"本次验证记录：{record.path}")


if __name__ == "__main__":
    main()
