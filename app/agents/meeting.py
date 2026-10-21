import json
from typing import Protocol

from pydantic import BaseModel

from app.agents.contracts import ReviewVerdict
from app.agents.prompts import MANAGER, PLANNER, PROMPT_VERSION, REVIEWER
from app.domain.schemas import Analysis, Meeting, Plan, Review
from app.llm.client import ModelResult


class StructuredClient(Protocol):
    async def generate_structured(self, messages: list[dict[str, str]], schema: type[BaseModel],
                                  *, label: str, prompt_version: str) -> ModelResult: ...


class MeetingAgents:
    def __init__(self, client: StructuredClient):
        self.client = client

    async def _call(self, role, instruction, schema, meeting, **context):
        result = await self.client.generate_structured([
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps({
                "meeting": meeting.model_dump(mode="json", exclude={"text"}), **context,
            }, ensure_ascii=False)},
        ], schema, label=role, prompt_version=PROMPT_VERSION)
        return result

    async def analyze(self, meeting: Meeting):
        return await self._call("manager", MANAGER, Analysis, meeting)

    async def plan(self, meeting: Meeting, analysis: Analysis, previous: Plan | None,
                   feedback: Review | None):
        return await self._call(
            "planner", PLANNER, Plan, meeting,
            analysis=analysis.model_dump(mode="json"),
            previous_plan=previous.model_dump(mode="json") if previous else None,
            feedback=feedback.model_dump(mode="json") if feedback else None,
        )

    async def review(self, meeting: Meeting, analysis: Analysis, plan: Plan, program_issues):
        return await self._call(
            "reviewer", REVIEWER, ReviewVerdict, meeting,
            analysis=analysis.model_dump(mode="json"), plan=plan.model_dump(mode="json"),
            program_issues=[issue.model_dump(mode="json") for issue in program_issues],
        )
