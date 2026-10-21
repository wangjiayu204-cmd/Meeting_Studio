from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import PROJECT_ROOT, Settings


def test_environment_overrides_configuration(monkeypatch):
    monkeypatch.setenv("PM_LLM_BASE_URL", "http://dcu.internal:8000/v1/")
    monkeypatch.setenv("PM_LLM_MODEL", "remote-qwen")
    monkeypatch.setenv("PM_MAX_CONCURRENT_RUNS", "2")
    settings = Settings(_env_file=None)
    assert settings.llm_base_url == "http://dcu.internal:8000/v1"
    assert settings.llm_model == "remote-qwen"
    assert settings.max_concurrent_runs == 2


def test_env_file_and_secret_not_in_repr(tmp_path):
    config_file = tmp_path / ".env"
    config_file.write_text("PM_LLM_API_KEY=private-example-key\nPM_LLM_TIMEOUT=90\n", encoding="utf-8")
    settings = Settings(_env_file=config_file)
    assert settings.llm_timeout == 90
    assert settings.llm_api_key.get_secret_value() == "private-example-key"
    assert "private-example-key" not in repr(settings)
    assert "private-example-key" not in settings.model_dump_json()


def test_database_path_is_independent_of_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None, database_path=Path("data/pm.sqlite3"))
    assert settings.database_path == PROJECT_ROOT / "data/pm.sqlite3"
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("overrides", [
    {"llm_base_url": "file:///tmp/model"},
    {"llm_base_url": "http://user:password@localhost:8000/v1"},
    {"llm_base_url": "http://localhost:bad/v1"},
    {"llm_base_url": "http://localhost:8000/v1?key=secret"},
    {"llm_timeout": 0},
    {"llm_timeout": float("inf")},
    {"max_concurrent_runs": 0},
    {"max_revisions": -1},
    {"llm_total_timeout": 0},
    {"llm_max_retries": 4},
    {"timezone": "Invalid/Timezone"},
])
def test_invalid_configuration_fails_early(overrides):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


def test_artifact_path_resolves_from_project_root(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    value = Settings(_env_file=None, test_artifacts_path="test_artifacts")
    assert value.test_artifacts_path == PROJECT_ROOT / "test_artifacts"
