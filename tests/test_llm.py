import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.core.errors import AppError
from app.llm.client import ModelClient
from app.llm.parsing import OutputFormatError, clean_content, parse_json
from app.main import create_app


class Sample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    count: int = Field(ge=0)


def completion(content='{"title":"例会","count":2}', finish="stop"):
    return httpx.Response(200, json={
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    })


def settings(tmp_path, **overrides):
    return Settings(_env_file=None, test_artifacts_path=tmp_path / "artifacts",
                    database_path=tmp_path / "pm.sqlite3", **overrides)


def run_generation(config, handler):
    async def run():
        client = ModelClient(config, transport=httpx.MockTransport(handler))
        try:
            return await client.generate_structured(
                [{"role": "user", "content": "测试会议"}], Sample, label="unit_test",
            )
        finally:
            await client.aclose()
    return asyncio.run(run())


def trace_dir(tmp_path):
    return next((tmp_path / "artifacts/llm").iterdir())


def test_think_and_fences():
    text = '<think>私密思考</think>\n```json\n{"title":"例会","count":2}\n```'
    assert parse_json(clean_content(text)) == {"title": "例会", "count": 2}
    with pytest.raises(OutputFormatError):
        clean_content("<think>未结束")


@pytest.mark.parametrize("content", ["", "not json", '{"a":NaN}', '{"a":1,"a":2}',
                                         '前缀 {"a":1}'])
def test_invalid_json_not_silently_salvaged(content):
    with pytest.raises(OutputFormatError):
        parse_json(content)


def test_success_and_redacted_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://unavailable.invalid:1")
    config = settings(tmp_path, llm_api_key="private-secret")
    def handler(request):
        assert str(request.url) == "http://127.0.0.1:8000/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer private-secret"
        assert json.loads(request.content)["response_format"]["type"] == "json_schema"
        return completion('<think>private-secret</think>\n```json\n{"title":"例会","count":2}\n```')
    result = run_generation(config, handler)
    assert result.value.count == 2
    assert result.attempts == 1
    directory = trace_dir(tmp_path)
    assert json.loads((directory / "summary.json").read_text())["status"] == "succeeded"
    assert "think" not in (directory / "01_content.txt").read_text()
    for file in directory.iterdir():
        assert "private-secret" not in file.read_text()
        assert "Authorization" not in file.read_text()


def test_unsupported_schema_fallback_is_bounded(tmp_path):
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return httpx.Response(400, json={"error": "response_format json_schema not supported"})
        assert "response_format" not in body
        return completion()
    result = run_generation(settings(tmp_path), handler)
    assert result.attempts == 2
    assert (trace_dir(tmp_path) / "01_fallback.json").exists()


@pytest.mark.parametrize("first", ['bad json', '{"title":"x","count":-1}', '<think>unfinished'])
def test_single_format_repair(tmp_path, first):
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        if count == 1:
            return completion(first)
        body = json.loads(request.content)
        assert "请修正" in body["messages"][-1]["content"]
        assert "<think>" not in body["messages"][-2]["content"]
        return completion()
    assert run_generation(settings(tmp_path), handler).attempts == 2
    assert (trace_dir(tmp_path) / "01_validation.json").exists()


def test_second_format_failure_stops(tmp_path):
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        return completion("invalid")
    with pytest.raises(AppError, match="单次修复") as error:
        run_generation(settings(tmp_path), handler)
    assert calls == 2
    assert error.value.code == "llm_invalid_json"
    summary = json.loads((trace_dir(tmp_path) / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert error.value.details[0]["trace_id"] == summary["trace_id"]


@pytest.mark.parametrize("response,code,status", [
    (httpx.Response(401, text="private-secret"), "llm_auth_error", 502),
    (httpx.Response(403), "llm_auth_error", 502),
    (httpx.Response(404), "llm_not_found", 502),
    (httpx.Response(400, text="maximum context length exceeded"), "llm_context_exceeded", 422),
    (httpx.Response(400, text="invalid parameter"), "llm_upstream_error", 502),
    (completion("{}", finish="length"), "llm_output_truncated", 502),
    (completion(None), "llm_empty_output", 502),
    (completion("{}", finish="content_filter"), "llm_unexpected_finish", 502),
    (httpx.Response(200, json={"choices": []}), "llm_invalid_response", 502),
    (httpx.Response(200, json=[]), "llm_invalid_response", 502),
    (httpx.Response(200, json={"choices": ["invalid"]}), "llm_invalid_response", 502),
    (httpx.Response(200, text="not envelope JSON"), "llm_invalid_response", 502),
])
def test_nonretryable_failures(tmp_path, response, code, status):
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        return response
    with pytest.raises(AppError) as error:
        run_generation(settings(tmp_path), handler)
    assert error.value.code == code
    assert error.value.status_code == status
    assert calls == 1
    assert "private-secret" not in error.value.message


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_status_retried(tmp_path, status):
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(status) if calls == 1 else completion()
    assert run_generation(settings(tmp_path, llm_max_retries=1), handler).attempts == 2


def test_retry_budget_exhausted(tmp_path):
    with pytest.raises(AppError) as error:
        run_generation(settings(tmp_path, llm_max_retries=1), lambda r: httpx.Response(429))
    assert error.value.code == "llm_rate_limited"
    assert json.loads((trace_dir(tmp_path) / "summary.json").read_text())["attempts"] == 2


@pytest.mark.parametrize("exception,code", [(httpx.ConnectError, "llm_connection_error"),
                                            (httpx.ReadTimeout, "llm_timeout")])
def test_transport_failure(tmp_path, exception, code):
    def handler(request):
        raise exception("do not leak upstream", request=request)
    with pytest.raises(AppError) as error:
        run_generation(settings(tmp_path, llm_max_retries=0), handler)
    assert error.value.code == code


def test_total_timeout_covers_wait(tmp_path):
    async def handler(request):
        await asyncio.sleep(1)
        return completion()
    with pytest.raises(AppError) as error:
        run_generation(settings(tmp_path, llm_total_timeout=0.02), handler)
    assert error.value.code == "llm_total_timeout"
    assert json.loads((trace_dir(tmp_path) / "summary.json").read_text())["status"] == "failed"


def test_concurrency_limit_and_inputs_are_not_mutated(tmp_path):
    async def run():
        active = 0
        peak = 0
        async def handler(request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return completion()
        client = ModelClient(settings(tmp_path), transport=httpx.MockTransport(handler))
        messages = [{"role": "user", "content": "会议记录"}]
        try:
            await asyncio.gather(*(client.generate_structured(messages, Sample) for _ in range(3)))
            assert peak == 1
            assert messages == [{"role": "user", "content": "会议记录"}]
        finally:
            await client.aclose()
    asyncio.run(run())


def test_trace_can_be_disabled(tmp_path):
    run_generation(settings(tmp_path, llm_trace_enabled=False), lambda r: completion())
    assert not (tmp_path / "artifacts").exists()


@pytest.mark.parametrize("models,status,code", [
    ([{"id": "qwen3-8b"}], 200, None),
    ([{"id": "another-model"}], 502, "llm_model_missing"),
    ("wrong shape", 502, "llm_invalid_response"),
])
def test_model_health_route(tmp_path, models, status, code):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        original = app.state.model_client
        async def replace():
            await original.aclose()
            app.state.model_client = ModelClient(settings(tmp_path), transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"data": models})))
        client.portal.call(replace)
        response = client.get("/api/model/health")
        assert response.status_code == status
        if code:
            assert response.json()["error"]["code"] == code
        else:
            assert response.json()["model"] == "qwen3-8b"
        assert client.get("/api/health").status_code == 200
