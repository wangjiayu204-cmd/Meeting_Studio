from typing import Awaitable, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.meeting import MeetingAgents
from app.core.errors import AppError
from app.domain.schemas import Analysis, Meeting, Plan, Review
from app.domain.validation import merge_review, validate_analysis, validate_plan


class AnalysisState(TypedDict, total=False):
    meeting: Meeting
    analysis: Analysis
    plan: Plan
    review: Review
    revision: int


Observer = Callable[[str, AnalysisState, dict], Awaitable[None]]


def build_analysis_graph(agents: MeetingAgents, max_revisions: int, observer: Observer):
    async def manager(state):
        await observer("manager_started", state, {})
        result = await agents.analyze(state["meeting"])
        issues = validate_analysis(state["meeting"], result.value)
        if issues:
            raise AppError("analysis_invalid_references", "Manager 引用不存在，请重试分析", 502)
        update = {"analysis": result.value}
        await observer("manager", {**state, **update}, {"trace_id": result.trace_id})
        return update

    async def planner(state):
        await observer("planner_started", state, {})
        result = await agents.plan(state["meeting"], state["analysis"],
                                   state.get("plan"), state.get("review"))
        revision = state.get("revision", -1) + 1
        # 模型不得生成 human 来源；这是服务端人工编辑的能力。
        if any(e and e.source_type == "human" for task in result.value.tasks
               for e in (task.owner_evidence, task.due_evidence)):
            raise AppError("planner_forged_human_evidence", "Planner 不能创建人工确认依据", 502)
        update = {"plan": result.value, "revision": revision}
        await observer("planner", {**state, **update}, {"trace_id": result.trace_id})
        return update

    async def reviewer(state):
        await observer("reviewer_started", state, {})
        program = validate_plan(state["meeting"], state["plan"])
        result = await agents.review(state["meeting"], state["analysis"], state["plan"], program)
        review = merge_review(state["plan"], program, result.value)
        update = {"review": review}
        await observer("reviewer", {**state, **update}, {"trace_id": result.trace_id})
        return update

    def route(state):
        # 空字段需人工处理，不反复要求模型填造；有错误或语义反馈才返工。
        fixable = any(issue.severity == "error" or issue.origin == "reviewer"
                      for issue in state["review"].issues)
        return "planner" if fixable and state["revision"] < max_revisions else END

    graph = StateGraph(AnalysisState)
    graph.add_node("manager", manager)
    graph.add_node("planner", planner)
    graph.add_node("reviewer", reviewer)
    graph.add_edge(START, "manager")
    graph.add_edge("manager", "planner")
    graph.add_edge("planner", "reviewer")
    graph.add_conditional_edges("reviewer", route, {"planner": "planner", END: END})
    return graph.compile()
