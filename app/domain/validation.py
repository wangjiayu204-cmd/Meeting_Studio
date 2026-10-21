"""确定性审核；程序问题不能由模型修改严重程度或关闭。"""

from collections import Counter
import re

from app.domain.schemas import Analysis, Meeting, Plan, Review, ReviewIssue


def validate_analysis(meeting: Meeting, analysis: Analysis) -> list[ReviewIssue]:
    refs = {p.id for p in meeting.paragraphs}
    return [ReviewIssue(origin="program", message=f"会议决议引用不存在：{ref}")
            for decision in analysis.decisions for ref in decision.source_refs if ref not in refs]


def validate_plan(meeting: Meeting, plan: Plan) -> list[ReviewIssue]:
    issues = []
    refs = {paragraph.id for paragraph in meeting.paragraphs}
    ids = {task.local_id for task in plan.tasks}
    by_id = {task.local_id: task for task in plan.tasks}

    def add(task_id, message, severity="error"):
        issues.append(ReviewIssue(task_ref=task_id, origin="program",
                                  severity=severity, message=message))

    for task_id, count in Counter(t.local_id for t in plan.tasks).items():
        if count > 1:
            add(task_id, "草稿任务 ID 重复")
    titles = Counter(re.sub(r"\s+", "", t.title).casefold() for t in plan.tasks)
    for task in plan.tasks:
        for ref in task.source_refs:
            if ref not in refs:
                add(task.local_id, f"任务原文引用不存在：{ref}")
        for field, evidence in (("负责人", task.owner_evidence), ("日期", task.due_evidence)):
            value = task.owner if field == "负责人" else task.due_date
            if value is not None and evidence is None:
                add(task.local_id, f"{field}有值但缺少依据或人工确认")
            if value is None:
                if evidence is not None:
                    add(task.local_id, f"{field}为空但仍填写了依据")
                add(task.local_id, f"{field}未明确，执行前需确认接受空值或补充", "warning")
            if evidence:
                for ref in evidence.source_refs:
                    if ref not in refs:
                        add(task.local_id, f"{field}引用不存在：{ref}")
        if titles[re.sub(r"\s+", "", task.title).casefold()] > 1:
            add(task.local_id, "任务标题重复，请确认是否应合并", "warning")
        if task.due_date and task.due_date < meeting.meeting_date:
            add(task.local_id, "截止日期早于会议日期，请确认是否为历史或逾期事项", "warning")
        for note in task.needs_confirmation:
            add(task.local_id, f"待确认：{note}", "warning")
        for dependency in task.dependencies:
            if dependency not in ids:
                add(task.local_id, f"依赖任务不存在：{dependency}")
            elif dependency == task.local_id:
                add(task.local_id, "任务不能依赖自身")
            elif task.due_date and by_id[dependency].due_date and task.due_date < by_id[dependency].due_date:
                add(task.local_id, f"截止日期早于前置任务 {dependency} 的截止日期")

    visiting, visited = set(), set()
    cycle_nodes = set()
    stack = []

    def visit(task_id):
        if task_id in visiting:
            cycle_nodes.update(stack[stack.index(task_id):])
            return
        if task_id in visited:
            return
        visiting.add(task_id)
        stack.append(task_id)
        for dependency in by_id[task_id].dependencies:
            if dependency in by_id and dependency != task_id:
                visit(dependency)
        visiting.remove(task_id)
        stack.pop()
        visited.add(task_id)

    for task_id in by_id:
        visit(task_id)
    if cycle_nodes:
        add(None, "任务存在依赖环，请拆解循环依赖：" + ", ".join(sorted(cycle_nodes)))
    return issues


def merge_review(plan: Plan, program_issues: list[ReviewIssue], verdict) -> Review:
    # 模型仅负责语义意见，不能声称程序错误已经修复，也不能接受问题。
    issues = list(program_issues)
    ids = {task.local_id for task in plan.tasks}
    for item in verdict.issues:
        task_ref = item.task_ref if item.task_ref in ids else None
        issues.append(ReviewIssue(task_ref=task_ref, message=item.message,
                                  severity=item.severity, origin="reviewer"))
    if not verdict.approved and not verdict.issues:
        issues.append(ReviewIssue(message="Reviewer 未通过：" + verdict.summary, origin="reviewer"))
    return Review(draft_version=1, approved=verdict.approved and not issues,
                  summary=verdict.summary, issues=issues)
