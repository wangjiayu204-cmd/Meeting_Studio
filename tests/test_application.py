import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.schemas import MeetingCreate
from app.main import create_app


@pytest.fixture
def app(tmp_path):
    application = create_app(Settings(
        _env_file=None, database_path=tmp_path / "data/pm.sqlite3",
        llm_base_url="http://127.0.0.1:1/v1",
    ))
    # 仅测试应用挂载契约探针，不在产品中增加占位业务 API。
    probe = APIRouter()

    @probe.post("/contract")
    async def contract(body: MeetingCreate):
        return body

    @probe.get("/conflict")
    async def conflict():
        raise AppError("draft_version_conflict", "草稿已更新", 409,
                       [{"current_version": 2}])

    @probe.get("/unexpected")
    async def unexpected():
        raise RuntimeError("private-example-key confidential-meeting")

    application.include_router(probe, prefix="/_test")
    return application


def test_health_checks_database_without_requiring_model(app):
    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok", "version": "0.1.0", "phase": "P6",
            "database": "ok", "model": "not_checked",
        }
        assert len(response.headers["x-request-id"]) == 32
        assert app.state.settings.database_path.exists()


def test_docs_and_openapi_are_available(app):
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 200
        schema = client.get("/openapi.json").json()
        assert "/api/health" in schema["paths"]
        assert "ErrorResponse" in schema["components"]["schemas"]


def test_validation_response_is_consistent_without_echoing_input(app):
    with TestClient(app) as client:
        response = client.post("/_test/contract", json={
            "title": "项目例会", "meeting_date": "private-example-key",
            "text": "私密会议内容不得在错误信息中回显。",
        })
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "validation_error"
        assert body["request_id"] == response.headers["x-request-id"]
        assert "private-example-key" not in response.text
        assert "私密会议内容" not in response.text
        assert "input" not in body["error"]["details"][0]


def test_business_errors_and_http_errors_use_same_envelope(app):
    with TestClient(app) as client:
        response = client.get("/_test/conflict")
        assert response.status_code == 409
        assert response.json()["error"]["details"] == [{"current_version": 2}]
        missing = client.get("/missing")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not_found"
        wrong_method = client.post("/api/health")
        assert wrong_method.status_code == 405
        assert "GET" in wrong_method.headers["allow"]


def test_unexpected_error_is_sanitized_and_process_stays_available(app):
    with TestClient(app) as client:
        failed = client.get("/_test/unexpected")
        assert failed.status_code == 500
        assert failed.json()["error"]["code"] == "internal_error"
        assert "private-example-key" not in failed.text
        assert "confidential-meeting" not in failed.text
        assert failed.json()["request_id"] == failed.headers["x-request-id"]
        assert client.get("/api/health").status_code == 200


def test_application_factory_instances_do_not_share_settings():
    first = create_app(Settings(_env_file=None, app_name="first"))
    second = create_app(Settings(_env_file=None, app_name="second"))
    assert first.title == "first"
    assert second.title == "second"
    assert first.state.settings is not second.state.settings
