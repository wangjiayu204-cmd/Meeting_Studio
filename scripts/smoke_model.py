"""真实 vLLM 结构化输出验证，保留每次调用及总体验证记录。"""

import asyncio
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel, ConfigDict, Field

from app.core.artifacts import ArtifactRecord
from app.core.config import Settings
from app.core.errors import AppError
from app.documents.parser import split_paragraphs
from app.llm.client import ModelClient
from smoke_meetings import server


class SmokeTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    owner: str | None
    due_date: str | None
    source_refs: list[str] = Field(min_length=1)


class SmokePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks: list[SmokeTask]


async def main():
    settings = Settings()
    record = ArtifactRecord(settings.test_artifacts_path / "api", "model_smoke",
                            (settings.llm_api_key.get_secret_value(),))
    record.write("context.json", {"model": settings.llm_model, "base_url": settings.llm_base_url})
    client = ModelClient(settings)
    try:
        # 真实 HTTP 检查应用的 /api/model/health；隔离数据库，日志与响应保留。
        with TemporaryDirectory(prefix="model-p3-") as directory:
            with server(Path(directory) / "pm.sqlite3", record.path / "model_api.log",
                        record=record, request_model_health=True):
                pass
        health = await client.health()
        record.write("01_health.json", health)
        text = (Path(__file__).resolve().parents[1] / "examples/meeting.txt").read_text(encoding="utf-8")
        messages = [{"role": "user", "content":
                     "会议日期 2026-10-21，时区 Asia/Shanghai。只提取明确分配的行动项，"
                     "未决议题不要当任务；日期 YYYY-MM-DD，引用段落 ID。\n"
                     + "\n".join(f"[{p.id}] {p.text}" for p in split_paragraphs(text))}]
        record.write("02_input.json", messages)
        result = await client.generate_structured(messages, SmokePlan,
                                                  label="meeting_smoke", prompt_version="smoke-v1")
        record.write("03_parsed_plan.json", result.value.model_dump(mode="json"))
        expected = {("张三", "2026-10-23"), ("李四", "2026-10-26")}
        actual = {(task.owner, task.due_date) for task in result.value.tasks}
        assert actual == expected and len(result.value.tasks) == 2, "行动项负责人 / 日期与样例预期不符"
        valid_refs = {p.id for p in split_paragraphs(text)}
        assert all(set(task.source_refs) <= valid_refs for task in result.value.tasks), "原文引用不正确"
        record.finish("succeeded", health_trace_id=health["trace_id"],
                      generation_trace_id=result.trace_id, attempts=result.attempts, usage=result.usage)
        print("真实模型验证通过：2 个行动项，负责人、日期和引用校验通过。")
    except Exception as exc:
        record.finish("failed", error_code=exc.code if isinstance(exc, AppError) else type(exc).__name__,
                      message=str(exc))
        raise
    finally:
        await client.aclose()
        print(f"本次验证记录：{record.path}")


if __name__ == "__main__":
    asyncio.run(main())
