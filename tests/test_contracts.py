import pytest
from pydantic import ValidationError

from app.domain.schemas import (
    Draft, DraftTask, Event, ExecuteRequest, FieldEvidence, MeetingCreate,
    Plan, Review, ReviewIssue, Run, TaskUpdate,
)
from app.domain.states import DraftStatus, RunStatus


def meeting_data(**overrides):
    return {"title": "项目例会", "meeting_date": "2026-10-21",
            "text": "会议决定本周完成登录模块联调。", **overrides}


def task_data(**overrides):
    return {"local_id": "task_login", "title": "完成登录模块联调",
            "source_refs": ["p0001"], **overrides}


def test_meeting_date_and_timezone_are_explicit():
    value = MeetingCreate(**meeting_data(timezone="Asia/Shanghai"))
    assert value.meeting_date.isoformat() == "2026-10-21"
    assert value.timezone == "Asia/Shanghai"
    assert value.members == []


@pytest.mark.parametrize("overrides", [
    {"title": "  "}, {"text": "   "}, {"meeting_date": "2026-02-30"},
    {"timezone": "Unknown/Zone"}, {"unrecognized": "ignored by mistake"},
])
def test_invalid_meeting_input_is_rejected(overrides):
    with pytest.raises(ValidationError):
        MeetingCreate(**meeting_data(**overrides))


def test_unknown_owner_and_date_remain_null_and_empty_plan_is_valid():
    task = DraftTask(**task_data())
    assert task.owner is None
    assert task.due_date is None
    assert task.owner_evidence is None
    assert Plan().tasks == []
    assert Plan.model_json_schema()["additionalProperties"] is False


@pytest.mark.parametrize("overrides", [
    {"local_id": "invalid id"}, {"source_refs": []},
    {"source_refs": ["paragraph-does-not-match-contract"]},
    {"due_date": "tomorrow"}, {"priority": "urgent"}, {"owner": " "},
])
def test_invalid_task_contracts_are_rejected(overrides):
    with pytest.raises(ValidationError):
        DraftTask(**task_data(**overrides))


def test_human_evidence_requires_a_note():
    with pytest.raises(ValidationError):
        FieldEvidence(source_type="human", confirmation=" ")
    human = FieldEvidence(source_type="human", confirmation="会后由项目负责人确认")
    assert human.source_refs == []
    with pytest.raises(ValidationError):
        FieldEvidence(source_type="document")


def test_draft_review_must_match_version_and_states_are_separate():
    review = Review(draft_version=1, approved=False, summary="缺少负责人")
    draft = Draft(meeting_id="meeting1", run_id="run1", version=1, review=review)
    run = Run(meeting_id="meeting1", status=RunStatus.succeeded)
    assert draft.status == DraftStatus.pending_review
    assert run.status == RunStatus.succeeded
    with pytest.raises(ValidationError):
        Draft(meeting_id="meeting1", run_id="run1", version=2, review=review)


def test_issue_acceptance_is_auditable():
    with pytest.raises(ValidationError):
        ReviewIssue(message="缺少截止日期", status="accepted")
    issue = ReviewIssue(message="缺少截止日期", status="accepted", resolution_note="允许暂不设日期")
    assert issue.status == "accepted"


@pytest.mark.parametrize("confirmation", [False, "true", 1, None])
def test_execution_requires_explicit_boolean_confirmation(confirmation):
    with pytest.raises(ValidationError):
        ExecuteRequest(draft_version=1, confirm=confirmation)


def test_valid_execution_request_and_json_roundtrip():
    assert ExecuteRequest(draft_version=1, confirm=True).confirm is True
    event = Event(id=1, run_id="run1", type="run_started", message="开始分析",
                  payload={"revision": 0})
    assert Event.model_validate_json(event.model_dump_json()) == event
    assert event.created_at.tzinfo is not None
    with pytest.raises(ValidationError):
        TaskUpdate(status="finished")
