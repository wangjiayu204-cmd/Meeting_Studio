"""真实 HTTP 冒烟：隔离数据库、临时端口，停止和重启服务后再次读取。

在 demo1 中执行 python scripts/smoke_meetings.py，不访问模型、不修改正式数据。
"""

from contextlib import contextmanager
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def server(database_path: Path, log_path: Path):
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        port = candidate.getsockname()[1]
    environment = {**os.environ, "PM_DATABASE_PATH": str(database_path)}
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
             "--host", "127.0.0.1", "--port", str(port), "--workers", "1"],
            cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5) as client:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("服务提前退出：" + log_path.read_text(encoding="utf-8"))
                    try:
                        if client.get("/api/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("服务启动超时")
                yield client
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main():
    with TemporaryDirectory(prefix="meeting-p2-") as directory:
        path = Path(directory)
        database = path / "pm.sqlite3"
        with server(database, path / "server.log") as client:
            response = client.post("/api/meetings", json={
                "title": "HTTP 冒烟会议", "meeting_date": "2026-10-21",
                "text": "张三负责完成登录模块，周五提交联调报告。",
            })
            assert response.status_code == 201, response.text
            saved = response.json()
            with (ROOT / "examples/meeting.txt").open("rb") as document:
                uploaded = client.post("/api/meetings/upload", files={
                    "file": ("meeting.txt", document, "text/plain"),
                }, data={"title": "上传冒烟会议", "meeting_date": "2026-10-21"})
            assert uploaded.status_code == 201, uploaded.text
            assert uploaded.json()["paragraphs"][0]["id"] == "p0001"
            assert client.get("/api/meetings?limit=1").json()["total"] == 2
            assert client.get("/docs").status_code == 200
            assert client.get("/openapi.json").status_code == 200
            missing = client.get("/api/meetings/not-found")
            assert missing.status_code == 404
            assert missing.json()["request_id"] == missing.headers["x-request-id"]
        with server(database, path / "restart.log") as client:
            assert client.get(f"/api/meetings/{saved['id']}").json() == saved
            assert client.get("/api/meetings").json()["total"] == 2
            assert client.get("/api/health").json()["database"] == "ok"
    print("P2 HTTP 冒烟通过：创建、上传、分页、错误、文档及重启持久化；临时服务和数据已清理。")


if __name__ == "__main__":
    main()
