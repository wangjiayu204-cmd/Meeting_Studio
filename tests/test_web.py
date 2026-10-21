from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest

from app.core.config import Settings
from app.main import create_app


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets, self.ids = [], []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"])
        if tag in {"script", "link", "img"}:
            value = values.get("src") or values.get("href")
            if value:
                self.assets.append(value)


def test_home_assets_and_config_are_self_contained_and_do_not_expose_data(tmp_path):
    config = Settings(_env_file=None, database_path=tmp_path / "pm.sqlite3", llm_api_key="private-key",
                      llm_base_url="http://127.0.0.1:1234/v1", timezone="America/New_York")
    with TestClient(create_app(config)) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["cache-control"] == "no-store"
        assert "script-src 'self'" in response.headers["content-security-policy"]
        page = Page(); page.feed(response.text)
        assert len(page.ids) == len(set(page.ids))
        assert len(page.assets) >= 4
        for asset in page.assets:
            assert asset.startswith("/static/")
            assert client.get(asset).status_code == 200
        settings = client.get("/api/ui-config")
        assert settings.json()["timezone"] == "America/New_York"
        assert len(settings.json()["example"]["text"]) > 10
        for private in ("private-key", "llm_base_url", str(tmp_path), "llm_api_key"):
            assert private not in settings.text
        # 静态文件只发布 web 目录，不能通过路径读取业务库、环境或源码。
        for path in ("/static/.env", "/static/data/pm.sqlite3", "/static/main.py",
                     "/static/%2e%2e/core/config.py", "/test_artifacts/README.md"):
            assert client.get(path).status_code == 404
        assert client.get("/api/health").json()["phase"] == "P8"


def test_every_frontend_module_can_be_served_with_javascript_mime(tmp_path):
    app = create_app(Settings(_env_file=None, database_path=tmp_path / "pm.sqlite3"))
    root = Path(__file__).resolve().parents[1]
    with TestClient(app) as client:
        for file in (root / "app/web/js").glob("*.mjs"):
            response = client.get("/static/js/" + file.name)
            assert response.status_code == 200
            assert "javascript" in response.headers["content-type"]
            assert ".innerHTML" not in response.text
            for module in re.findall(r'from "(\./[^\"]+)"', response.text):
                assert (file.parent / module).is_file(), module
            page = Page(); page.feed(client.get("/").text)
            for element_id in re.findall(r'\$\("([^\"]+)"\)', response.text):
                assert element_id in page.ids, f"缺少交互节点：{file.name} / {element_id}"


def test_frontend_behavior_with_existing_node_runtime():
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("未提供 Node；可在有 Node 18+ 的机器执行 tests/frontend/*.test.mjs")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([executable, "--test", *map(str, sorted((root / "tests/frontend").glob("*.test.mjs")))],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
