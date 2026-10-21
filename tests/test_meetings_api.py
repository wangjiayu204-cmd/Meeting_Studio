import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from test_documents import make_pdf


@pytest.fixture
def config(tmp_path):
    return Settings(_env_file=None, database_path=tmp_path / "pm.sqlite3",
                    max_upload_bytes=4096, max_document_chars=200)


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as client:
        yield client


def data(**overrides):
    return {"title": "项目例会", "meeting_date": "2026-10-21",
            "timezone": "Asia/Shanghai", "text": "张三负责完成登录模块。\n周五提交联调报告。", **overrides}


def upload(client, content=None, **overrides):
    fields = {"title": "项目例会", "meeting_date": "2026-10-21", **overrides}
    return client.post("/api/meetings/upload", data=fields,
                       files={"file": ("会议.txt", content or "张三负责本周完成登录模块联调。".encode(), "text/plain")})


def test_create_query_and_restart(config):
    with TestClient(create_app(config)) as client:
        response = client.post("/api/meetings", json=data())
        assert response.status_code == 201
        created = response.json()
        assert [p["id"] for p in created["paragraphs"]] == ["p0001", "p0002"]
        assert client.get(f"/api/meetings/{created['id']}").json() == created
    with TestClient(create_app(config)) as restarted:
        assert restarted.get(f"/api/meetings/{created['id']}").json() == created
        assert restarted.get("/api/health").json()["database"] == "ok"


def test_list_pagination_and_not_found(client):
    empty = client.get("/api/meetings").json()
    assert empty["total"] == 0
    first = client.post("/api/meetings", json=data(title="第一份")).json()
    second = client.post("/api/meetings", json=data(title="第二份")).json()
    page = client.get("/api/meetings?limit=1&offset=0").json()
    assert page["total"] == 2
    assert page["items"][0]["id"] == second["id"]
    assert "text" not in page["items"][0]
    assert client.get("/api/meetings?limit=1&offset=1").json()["items"][0]["id"] == first["id"]
    assert client.get("/api/meetings?offset=5").json()["items"] == []
    assert client.get("/api/meetings?limit=101").status_code == 422
    assert client.get("/api/meetings?offset=-1").status_code == 422
    response = client.get("/api/meetings/missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "meeting_not_found"


def test_upload_with_metadata_and_no_filename_write(client, tmp_path):
    response = upload(client, members=json.dumps([{"name": "张三", "role": "研发"}]))
    assert response.status_code == 201
    assert response.json()["members"][0]["name"] == "张三"
    assert response.json()["timezone"] == "Asia/Shanghai"
    assert not (tmp_path / "会议.txt").exists()


def test_pdf_upload_is_saved_with_extracted_paragraphs(client):
    response = client.post("/api/meetings/upload", data={
        "title": "PDF 例会", "meeting_date": "2026-10-21",
    }, files={"file": ("meeting.pdf", make_pdf(), "application/pdf")})
    assert response.status_code == 201
    assert "integration report" in response.json()["paragraphs"][0]["text"]
    meeting_id = response.json()["id"]
    assert client.get(f"/api/meetings/{meeting_id}").json() == response.json()


def test_default_timezone_is_shared_by_json_and_upload(config):
    config.timezone = "UTC"
    with TestClient(create_app(config)) as client:
        payload = data()
        del payload["timezone"]
        assert client.post("/api/meetings", json=payload).json()["timezone"] == "UTC"
        assert upload(client).json()["timezone"] == "UTC"
        assert client.post("/api/meetings", json=data()).json()["timezone"] == "Asia/Shanghai"


@pytest.mark.parametrize("fields", [
    {"timezone": "Unknown/Zone"}, {"members": "bad json"},
    {"members": '[{"name":""}]'}, {"title": " "}, {"meeting_date": "yesterday"},
])
def test_invalid_upload_metadata_does_not_create_meeting(client, fields):
    response = upload(client, **fields)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert client.get("/api/meetings").json()["total"] == 0


def test_size_limits_and_parse_failure_do_not_persist(client):
    assert client.post("/api/meetings", json=data(text="字" * 201)).status_code == 413
    assert upload(client, b"a" * 4097).status_code == 413
    malformed = client.post("/api/meetings/upload", data={"title": "会议", "meeting_date": "2026-10-21"},
                            files={"file": ("bad.pdf", b"not a pdf", "application/pdf")})
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "invalid_pdf"
    assert client.get("/api/meetings").json()["total"] == 0


def test_total_body_limit_including_chunked_transfer(client):
    response = client.post("/api/meetings", content=b"x" * 270000)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    def chunks():
        for _ in range(3):
            yield b"x" * 100000
    response = client.post("/api/meetings", content=chunks())
    assert response.status_code == 413
    assert response.json()["request_id"] == response.headers["x-request-id"]


def test_database_health_failure_is_explicit(client, config):
    with client.app.state.database.connection() as connection:
        connection.execute("PRAGMA user_version = 999")
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
