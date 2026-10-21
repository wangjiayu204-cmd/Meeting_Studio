"""无需浏览器，使用现有 Node + demo1 联调前端模块与真实后端。"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.artifacts import ArtifactRecord
from app.core.config import Settings
from smoke_meetings import ROOT, server


def main():
    executable = shutil.which("node")
    if executable is None:
        raise SystemExit("本脚本需要 Node 18+，页面运行不需要 Node。可在已有 Node 的机器验证，不自动安装。")
    settings = Settings()
    record = ArtifactRecord(settings.test_artifacts_path / "api", "interface_smoke",
                            (settings.llm_api_key.get_secret_value(),))
    record.write("context.json", {"scope": "P8 JavaScript modules and HTTP/vLLM",
                                  "browser_available": False, "model": settings.llm_model})
    try:
        with TemporaryDirectory(prefix="interface-p8-") as directory:
            database = Path(directory) / "pm.sqlite3"
            with server(database, record.path / "server.log") as client:
                process = subprocess.Popen(
                    [executable, str(ROOT / "scripts/smoke_interface.mjs"), str(client.base_url),
                     str(record.path), str(settings.workflow_timeout + 30)],
                    cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                )
                try:
                    output, _ = process.communicate(timeout=3 * settings.workflow_timeout + 120)
                except subprocess.TimeoutExpired:
                    process.kill(); output, _ = process.communicate()
                    record.write("node.log", output)
                    raise RuntimeError("前端模块联调超时，已终止临时进程")
                record.write("node.log", output)
                assert process.returncode == 0, output
            result = json.loads((record.path / "result.json").read_text(encoding="utf-8"))
            with server(database, record.path / "restart.log", record=record) as client:
                prefix = f"/api/meetings/{result['meeting_id']}"
                assert client.get("/").status_code == 200
                assert client.get(prefix + "/followup").json()["counts"] == result["counts"]
                assert len(client.get(prefix + "/export").json()["tasks"]) == 2
            record.finish("succeeded", **result, restart_verified=True)
        print("P8 服务器联调通过；未执行浏览器布局与点击测试。")
    except Exception as exc:
        record.finish("failed", error_type=type(exc).__name__, message=str(exc), browser_tested=False)
        raise
    finally:
        print(f"本次验证记录：{record.path}")


if __name__ == "__main__":
    main()
