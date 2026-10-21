"""RFC 5545 全天截止日期事件，使用稳定 UID 支持后续重新导入。"""
from datetime import date, timedelta, timezone

from app.core.errors import AppError
from app.domain.reports import CalendarExport, MeetingSnapshot


def escape_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def fold_line(value: str) -> str:
    """每物理行至多 75 UTF-8 字节；续行空格占 1 字节，不拆开字符。"""
    lines, current, size = [], "", 0
    for char in value:
        length = len(char.encode("utf-8"))
        if size + length > 75:
            lines.append(current)
            current, size = " ", 1
        current += char
        size += length
    lines.append(current)
    return "\r\n".join(lines)


def stamp(value):
    value = value.astimezone(timezone.utc)
    return f"{value.year:04d}{value.month:02d}{value.day:02d}T{value.hour:02d}{value.minute:02d}{value.second:02d}Z"


def day(value):
    return f"{value.year:04d}{value.month:02d}{value.day:02d}"


def render_calendar(snapshot: MeetingSnapshot) -> CalendarExport:
    if snapshot.execution is None or snapshot.execution.status != "succeeded":
        raise AppError("calendar_not_ready", "确认执行成功后才能导出日历", 409)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Lightweight Multi-Agent PM//ZH",
             "CALSCALE:GREGORIAN", "X-WR-CALNAME:" + escape_text(snapshot.meeting.title),
             "X-WR-TIMEZONE:" + escape_text(snapshot.meeting.timezone)]
    exported, skipped = [], {}
    for task in snapshot.tasks:
        if task.due_date is None:
            skipped[task.id] = "missing_due_date"
            continue
        if task.due_date == date.max:
            skipped[task.id] = "exclusive_end_out_of_range"
            continue
        description = "\n".join([
            task.description, f"预期产出：{task.deliverable}", f"负责人：{task.owner or '待确认'}",
            f"看板状态：{task.status.value}", f"优先级：{task.priority}",
            "原文依据：" + ", ".join(task.source_refs), "依赖：" + ", ".join(task.dependencies),
            "待确认：" + "; ".join(task.needs_confirmation),
        ])
        lines.extend([
            "BEGIN:VEVENT", f"UID:{task.id}@lightweight-pm.local",
            "DTSTAMP:" + stamp(snapshot.generated_at), "LAST-MODIFIED:" + stamp(task.updated_at),
            "DTSTART;VALUE=DATE:" + day(task.due_date),
            "DTEND;VALUE=DATE:" + day(task.due_date + timedelta(days=1)),
            "SUMMARY:" + escape_text(task.title), "DESCRIPTION:" + escape_text(description),
            "STATUS:CONFIRMED", "TRANSP:TRANSPARENT", "X-PM-TASK-STATUS:" + task.status.value,
            "END:VEVENT",
        ])
        exported.append(task.id)
    lines.extend("X-PM-SKIPPED-TASK:" + escape_text(f"{task_id}:{reason}")
                 for task_id, reason in skipped.items())
    lines.append("END:VCALENDAR")
    return CalendarExport(content=("\r\n".join(fold_line(line) for line in lines) + "\r\n").encode("utf-8"),
                          exported_task_ids=exported, skipped=skipped)
