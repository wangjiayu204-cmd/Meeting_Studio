from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("请提供有效的 IANA 时区，例如 Asia/Shanghai") from exc
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PM_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        str_strip_whitespace=True,
        validate_default=True,
    )

    app_name: str = Field(default="智能会议与任务闭环管理", min_length=1)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    llm_base_url: str = "http://127.0.0.1:8000/v1"
    llm_model: str = Field(default="qwen3-8b", min_length=1)
    llm_api_key: SecretStr = SecretStr("EMPTY")
    llm_timeout: float = Field(default=180, gt=0, allow_inf_nan=False)
    llm_max_tokens: int = Field(default=6000, gt=0)
    llm_total_timeout: float = Field(default=240, gt=0, allow_inf_nan=False)
    llm_max_retries: int = Field(default=2, ge=0, le=3)
    llm_trace_enabled: bool = True
    test_artifacts_path: Path = Path("test_artifacts")
    max_document_chars: int = Field(default=24000, ge=10)
    max_upload_bytes: int = Field(default=10485760, gt=0)
    max_pdf_pages: int = Field(default=100, gt=0)
    max_revisions: int = Field(default=2, ge=0, le=10)
    max_concurrent_runs: int = Field(default=1, ge=1, le=16)
    database_path: Path = Path("data/pm.sqlite3")
    timezone: str = "Asia/Shanghai"

    @field_validator("llm_base_url")
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("模型接口端口无效") from exc
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or port == 0):
            raise ValueError("模型地址必须是无内嵌凭证、查询参数的 HTTP(S) 地址")
        return value.rstrip("/")

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        return validate_timezone(value)

    @field_validator("database_path", "test_artifacts_path")
    @classmethod
    def resolve_database_path(cls, value: Path) -> Path:
        return (value if value.is_absolute() else PROJECT_ROOT / value).resolve()
