from typing import Literal

from pydantic import Field

from app.domain.schemas import Contract, NonEmpty, TaskRef


class SemanticIssue(Contract):
    task_ref: TaskRef | None = None
    severity: Literal["error", "warning"] = "error"
    message: NonEmpty = Field(max_length=1500)


class ReviewVerdict(Contract):
    approved: bool
    summary: str = Field(max_length=3000)
    issues: list[SemanticIssue] = Field(default_factory=list, max_length=100)
