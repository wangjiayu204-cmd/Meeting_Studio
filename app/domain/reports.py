"""导出与跟进报告的数据契约；草稿和正式任务始终分开。"""
from datetime import date
from typing import Literal

from pydantic import AwareDatetime, Field

from app.domain.schemas import (Analysis, Contract, Draft, Execution, Meeting,
                                ReviewIssue, Task, utc_now)


class MeetingSnapshot(Contract):
    schema_version: Literal[1] = 1
    generated_at: AwareDatetime = Field(default_factory=utc_now)
    meeting: Meeting
    analysis: Analysis | None = None
    analysis_run_id: str | None = None
    draft: Draft | None = None
    execution: Execution | None = None
    tasks: list[Task] = Field(default_factory=list)


class Clarification(Contract):
    origin: Literal["analysis", "review", "task", "draft"]
    message: str
    task_ref: str | None = None
    task_id: str | None = None
    source_refs: list[str] = Field(default_factory=list)


class FollowupCounts(Contract):
    total: int
    completed: int
    pending: int
    overdue: int
    due_today: int
    missing_owner: int
    missing_due_date: int


class FollowupReport(Contract):
    meeting_id: str
    generated_at: AwareDatetime
    timezone: str
    as_of_date: date
    execution_status: str | None
    draft_version: int | None
    counts: FollowupCounts
    completed: list[Task]
    pending: list[Task]
    overdue: list[Task]
    due_today: list[Task]
    clarifications: list[Clarification]
    review_issues: list[ReviewIssue]


class CalendarExport(Contract):
    content: bytes
    exported_task_ids: list[str]
    skipped: dict[str, str]
