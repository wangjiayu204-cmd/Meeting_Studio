from dataclasses import dataclass

from app.domain.reports import CalendarExport, FollowupReport
from app.exports.calendar import render_calendar
from app.exports.followup import build_followup
from app.repositories.reports import ReportRepository
from app.tools.registry import CreateTasksArguments, ToolRegistry


@dataclass(frozen=True)
class MeetingToolContext:
    meeting_id: str


class ReportService:
    def __init__(self, repository: ReportRepository):
        self.repository = repository
        # 查询工具与创建工具分别注册，执行创建任务时不会误选导出工具。
        # 参数为空，会议始终由服务端上下文绑定，模型无法传入任意会议 ID。
        self.tools = ToolRegistry[MeetingToolContext, CalendarExport | FollowupReport]()
        self.tools.register("export_calendar", CreateTasksArguments,
                            lambda context, _: render_calendar(repository.snapshot(context.meeting_id)))
        self.tools.register("get_task_status", CreateTasksArguments,
                            lambda context, _: build_followup(repository.snapshot(context.meeting_id)))

    def calendar(self, meeting_id: str) -> CalendarExport:
        return self.tools.invoke("export_calendar", {}, MeetingToolContext(meeting_id))

    def followup(self, meeting_id: str) -> FollowupReport:
        return self.tools.invoke("get_task_status", {}, MeetingToolContext(meeting_id))
