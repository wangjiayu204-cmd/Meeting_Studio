"""P4 真实 HTTP + vLLM 分析、草稿编辑、复核和重启验证。"""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.artifacts import ArtifactRecord
from app.core.config import Settings
from smoke_meetings import ROOT, server


def main():
    settings = Settings()
    record = ArtifactRecord(settings.test_artifacts_path / "api", "planning_smoke",
                            (settings.llm_api_key.get_secret_value(),))
    record.write("context.json", {"scope": "P4 real HTTP workflow", "model": settings.llm_model})
    try:
        with TemporaryDirectory(prefix="planning-p4-") as directory:
            database = Path(directory) / "pm.sqlite3"
            with server(database, record.path / "server.log", record=record,
                        request_timeout=settings.workflow_timeout + 30) as client:
                created = client.post("/api/meetings", json={
                    "title": "登录模块迭代例会", "meeting_date": "2026-10-21",
                    "timezone": "Asia/Shanghai", "text": (ROOT / "examples/meeting.txt").read_text(encoding="utf-8"),
                })
                assert created.status_code == 201, created.text
                meeting = created.json()
                prefix = f"/api/meetings/{meeting['id']}"
                response = client.post(prefix + "/analysis")
                assert response.status_code == 200, response.text
                draft = response.json()
                record.write("initial_draft.json", draft)
                assert len(draft["tasks"]) == 2, "预期两个明确行动项"
                actual = {(task["owner"], task["due_date"]) for task in draft["tasks"]}
                assert actual == {("张三", "2026-10-23"), ("李四", "2026-10-26")}, "负责人 / 日期不符"
                assert draft["status"] == "ready", "样例审核未通过，请检查 workflow 记录"
                analysis = client.get(prefix + "/analysis").json()
                record.write("analysis.json", analysis)
                assert analysis["open_questions"], "应保留验证码限流未决问题"
                run = client.get("/api/runs/" + draft["run_id"]).json()
                assert run["status"] == "succeeded"
                edited_tasks = draft["tasks"]
                edited_tasks[0]["deliverable"] += "（补充归档说明）"
                edited = client.put(prefix + "/draft", json={
                    "base_version": draft["version"], "tasks": edited_tasks,
                    "resolution_note": "会后补充交付物归档说明",
                })
                assert edited.status_code == 200, edited.text
                assert edited.json()["status"] == "pending_review"
                stale = client.put(prefix + "/draft", json={"base_version": draft["version"], "tasks": edited_tasks})
                assert stale.status_code == 409
                reviewed = client.post(prefix + "/draft/review", json={"draft_version": edited.json()["version"]})
                assert reviewed.status_code == 200, reviewed.text
                final = reviewed.json()
                assert final["status"] == "ready", "编辑后复核未通过"
                record.write("final_draft.json", final)
                versions = client.get(prefix + "/drafts").json()
                assert len(versions) >= 4
                assert [d["version"] for d in versions] == list(range(1, len(versions) + 1))
            with server(database, record.path / "restart.log", record=record) as client:
                assert client.get(prefix + "/draft").json() == final
                assert client.get("/api/runs/" + final["run_id"]).json()["status"] == "succeeded"
            record.finish("succeeded", meeting_id=meeting["id"], run_id=run["id"],
                          final_version=final["version"], versions=len(versions),
                          workflow_records="test_artifacts/workflows（按 run_id 查找 context.json）")
        print("P4 真实联调通过：三角色分析审核、草稿编辑、旧版冲突、复核与重启持久化。")
    except Exception as exc:
        record.finish("failed", error_type=type(exc).__name__, message=str(exc))
        raise
    finally:
        print(f"本次验证记录：{record.path}")


if __name__ == "__main__":
    main()
