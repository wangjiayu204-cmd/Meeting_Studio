from datetime import date, datetime, timezone
from typing import Annotated, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (AwareDatetime, BaseModel, ConfigDict, Field, JsonValue,
                      StringConstraints, field_validator, model_validator)

from app.domain.states import (AgentRole, DraftStatus, EventType, ExecutionStatus,
                               RunStatus, RunType, TaskStatus)

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
TaskRef = Annotated[str, StringConstraints(pattern=r"^task_[A-Za-z0-9_-]+$", max_length=80)]
ParagraphRef = Annotated[str, StringConstraints(pattern=r"^p[0-9]{4,}$", max_length=30)]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid4().hex


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Member(Contract):
    name: NonEmpty = Field(max_length=60)
    role: str = Field(default="", max_length=200)


class Paragraph(Contract):
    id: ParagraphRef
    text: NonEmpty


class MeetingCreate(Contract):
    title: NonEmpty = Field(max_length=160)
    meeting_date: date
    timezone: str = "Asia/Shanghai"
    text: NonEmpty = Field(min_length=10)
    members: list[Member] = Field(default_factory=list, max_length=100)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("无效 IANA 时区") from exc
        return value


class Meeting(MeetingCreate):
    id: str = Field(default_factory=new_id)
    paragraphs: list[Paragraph]
    created_at: AwareDatetime = Field(default_factory=utc_now)


class Decision(Contract):
    text: NonEmpty = Field(max_length=1500)
    source_refs: list[ParagraphRef] = Field(min_length=1)


class Analysis(Contract):
    summary: str = Field(max_length=5000)
    topics: list[str] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class FieldEvidence(Contract):
    source_type: Literal["document", "human"] = "document"
    source_refs: list[ParagraphRef] = Field(default_factory=list)
    confirmation: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def require_origin(self):
        if self.source_type == "human" and not self.confirmation:
            raise ValueError("人工补充必须填写确认说明")
        if self.source_type == "document" and not self.source_refs:
            raise ValueError("文档来源必须包含段落引用")
        return self


class DraftTask(Contract):
    local_id: TaskRef
    title: NonEmpty = Field(max_length=240)
    description: str = Field(default="", max_length=4000)
    deliverable: str = Field(default="", max_length=2000)
    owner: NonEmpty | None = Field(default=None, max_length=100)
    owner_evidence: FieldEvidence | None = None
    due_date: date | None = None
    due_evidence: FieldEvidence | None = None
    priority: Literal["low", "medium", "high"] = "medium"
    dependencies: list[TaskRef] = Field(default_factory=list)
    source_refs: list[ParagraphRef] = Field(min_length=1)
    needs_confirmation: list[str] = Field(default_factory=list)


class Plan(Contract):
    tasks: list[DraftTask] = Field(default_factory=list, max_length=100)


class ReviewIssue(Contract):
    id: str = Field(default_factory=new_id)
    task_ref: TaskRef | None = None
    severity: Literal["error", "warning"] = "error"
    origin: Literal["program", "reviewer"] = "reviewer"
    message: NonEmpty = Field(max_length=2000)
    status: Literal["open", "resolved", "accepted"] = "open"
    resolution_note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def require_resolution_note(self):
        if self.status != "open" and not self.resolution_note:
            raise ValueError("关闭或接受问题必须填写说明")
        return self


class Review(Contract):
    draft_version: int = Field(ge=1)
    approved: bool
    summary: str = Field(max_length=4000)
    issues: list[ReviewIssue] = Field(default_factory=list, max_length=100)
    created_at: AwareDatetime = Field(default_factory=utc_now)


class Draft(Plan):
    id: str = Field(default_factory=new_id)
    meeting_id: NonEmpty
    run_id: NonEmpty
    version: int = Field(ge=1)
    status: DraftStatus = DraftStatus.pending_review
    review: Review | None = None
    created_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def matching_review_version(self):
        if self.review is not None and self.review.draft_version != self.version:
            raise ValueError("审核结果必须对应当前草稿版本")
        return self


class Run(Contract):
    id: str = Field(default_factory=new_id)
    meeting_id: NonEmpty
    type: RunType = RunType.analysis
    status: RunStatus = RunStatus.queued
    revision_count: int = Field(default=0, ge=0)
    error_code: str | None = None
    error_message: str | None = None
    created_at: AwareDatetime = Field(default_factory=utc_now)
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None


class Task(DraftTask):
    id: str = Field(default_factory=new_id)
    meeting_id: NonEmpty
    draft_id: NonEmpty
    status: TaskStatus = TaskStatus.todo
    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)


class Execution(Contract):
    id: str = Field(default_factory=new_id)
    meeting_id: NonEmpty
    draft_version: int = Field(ge=1)
    status: ExecutionStatus = ExecutionStatus.pending
    task_ids: list[str] = Field(default_factory=list)
    error_message: str | None = None
    created_at: AwareDatetime = Field(default_factory=utc_now)


class Event(Contract):
    id: int = Field(ge=1)
    run_id: NonEmpty
    agent: AgentRole | None = None
    type: EventType
    message: str = Field(max_length=4000)
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: AwareDatetime = Field(default_factory=utc_now)


class DraftEdit(Plan):
    base_version: int = Field(ge=1)
    resolution_note: str = Field(default="", max_length=4000)


class ExecuteRequest(Contract):
    draft_version: int = Field(ge=1)
    confirm: bool = Field(strict=True)
    acknowledgement: str = Field(default="", max_length=4000)

    @field_validator("confirm")
    @classmethod
    def explicit_confirmation(cls, value: bool) -> bool:
        if not value:
            raise ValueError("必须明确确认后才能执行")
        return value


class TaskUpdate(Contract):
    status: TaskStatus
