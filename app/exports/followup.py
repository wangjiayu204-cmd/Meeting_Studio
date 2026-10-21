from datetime import datetime
from zoneinfo import ZoneInfo

from app.domain.reports import Clarification, FollowupCounts, FollowupReport, MeetingSnapshot


def build_followup(snapshot: MeetingSnapshot, *, now: datetime | None = None) -> FollowupReport:
    instant = now if now is not None else snapshot.generated_at
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("跟进统计必须使用带时区的时间")
    today = instant.astimezone(ZoneInfo(snapshot.meeting.timezone)).date()
    completed = [t for t in snapshot.tasks if t.status == "done"]
    pending = [t for t in snapshot.tasks if t.status != "done"]
    overdue = [t for t in pending if t.due_date is not None and t.due_date < today]
    due_today = [t for t in pending if t.due_date == today]
    issues = snapshot.draft.review.issues if snapshot.draft and snapshot.draft.review else []
    questions = [Clarification(origin="analysis", message=q)
                 for q in (snapshot.analysis.open_questions if snapshot.analysis else [])]
    questions.extend(Clarification(origin="review", task_ref=i.task_ref, message=i.message)
                     for i in issues if i.status == "open")
    # 未执行时检查草稿，但不将草稿计入正式待办或逾期统计。
    source = snapshot.tasks if snapshot.execution and snapshot.execution.status == "succeeded" else (
        snapshot.draft.tasks if snapshot.draft else [])
    origin = "task" if snapshot.execution and snapshot.execution.status == "succeeded" else "draft"
    for task in source:
        messages = list(task.needs_confirmation)
        if task.owner is None:
            messages.append("负责人待确认")
        if task.due_date is None:
            messages.append("截止日期待确认")
        for message in dict.fromkeys(messages):
            questions.append(Clarification(origin=origin, message=message, task_ref=task.local_id,
                                            task_id=getattr(task, "id", None), source_refs=task.source_refs))
    return FollowupReport(
        meeting_id=snapshot.meeting.id, generated_at=instant, timezone=snapshot.meeting.timezone,
        as_of_date=today, execution_status=snapshot.execution.status if snapshot.execution else None,
        draft_version=snapshot.draft.version if snapshot.draft else None,
        counts=FollowupCounts(total=len(snapshot.tasks), completed=len(completed), pending=len(pending),
                              overdue=len(overdue), due_today=len(due_today),
                              missing_owner=sum(t.owner is None for t in pending),
                              missing_due_date=sum(t.due_date is None for t in pending)),
        completed=completed, pending=pending, overdue=overdue, due_today=due_today,
        clarifications=questions, review_issues=issues,
    )
