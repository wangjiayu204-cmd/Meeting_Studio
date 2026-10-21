import asyncio

from starlette.concurrency import run_in_threadpool

from app.agents.meeting import MeetingAgents
from app.core.artifacts import ArtifactRecord
from app.core.config import Settings
from app.core.errors import AppError
from app.domain.schemas import Analysis, Draft, DraftEdit, FieldEvidence, Plan, Review
from app.domain.states import DraftStatus, RunStatus, RunType
from app.domain.validation import merge_review, validate_plan
from app.repositories.planning import PlanningRepository
from app.services.meetings import MeetingService
from app.workflows.analysis import build_analysis_graph


class PlanningService:
    def __init__(self, repository: PlanningRepository, meetings: MeetingService,
                 agents: MeetingAgents, settings: Settings):
        self.repository, self.meetings = repository, meetings
        self.agents, self.settings = agents, settings

    async def analyze(self, meeting_id: str) -> Draft:
        meeting = await run_in_threadpool(self.meetings.get, meeting_id)
        run = await run_in_threadpool(self.repository.start, meeting_id, RunType.analysis)
        return await self._perform(run, meeting)

    async def review(self, meeting_id: str, version: int) -> Draft:
        meeting = await run_in_threadpool(self.meetings.get, meeting_id)
        run = await run_in_threadpool(self.repository.start, meeting_id, RunType.review, version)
        return await self._perform(run, meeting, review_only=True)

    async def _perform(self, run, meeting, review_only=False):
        record = None
        latest = None
        revision = 0
        step = 0
        try:
            record = ArtifactRecord(
                self.settings.test_artifacts_path / "workflows" if self.settings.llm_trace_enabled else None,
                "review" if review_only else "analysis", (self.settings.llm_api_key.get_secret_value(),),
            )
            record.write("context.json", {"run": run.model_dump(mode="json"),
                                           "meeting": meeting.model_dump(mode="json")})

            async def observe(role, state, metadata):
                nonlocal latest, revision, step
                step += 1
                revision = state.get("revision", 0)
                record.write(f"{step:02d}_{role}.json", {
                    **metadata, "revision": revision,
                    "analysis": state["analysis"].model_dump(mode="json"),
                    "plan": state["plan"].model_dump(mode="json") if "plan" in state else None,
                    "review": state["review"].model_dump(mode="json") if role == "reviewer" else None,
                })
                if role == "manager":
                    await run_in_threadpool(self.repository.save_analysis, run.id, state["analysis"])
                else:
                    review = state.get("review") if role == "reviewer" else None
                    status = (DraftStatus.ready if review.approved else DraftStatus.needs_attention) if review else DraftStatus.pending_review
                    candidate = Draft(meeting_id=meeting.id, run_id=run.id, version=1,
                                      tasks=state["plan"].tasks, review=review, status=status,
                                      resolution_note=state.get("resolution_note", ""))
                    latest = await run_in_threadpool(self.repository.save_draft, candidate)
                    record.write(f"{step:02d}_draft.json", latest.model_dump(mode="json"))

            async def perform():
                if review_only:
                    current = await run_in_threadpool(self.repository.current_draft, meeting.id)
                    analysis = await run_in_threadpool(self.repository.get_analysis, meeting.id)
                    analysis = analysis or Analysis(summary="用户编辑草稿复核")
                    plan = Plan(tasks=current.tasks)
                    program = validate_plan(meeting, plan)
                    result = await self.agents.review(meeting, analysis, plan, program)
                    review = merge_review(plan, program, result.value)
                    await observe("reviewer", {"analysis": analysis, "plan": plan, "review": review,
                                              "resolution_note": current.resolution_note},
                                  {"trace_id": result.trace_id})
                else:
                    graph = build_analysis_graph(self.agents, self.settings.max_revisions, observe)
                    await graph.ainvoke({"meeting": meeting}, config={"recursion_limit": 20 + self.settings.max_revisions * 3})

            await asyncio.wait_for(perform(), timeout=self.settings.workflow_timeout)
            await run_in_threadpool(self.repository.finish, run.id, RunStatus.succeeded, revision)
            record.write("result.json", latest.model_dump(mode="json"))
            record.finish("succeeded", run_id=run.id, draft_version=latest.version, revision_count=revision)
            return latest
        except (Exception, asyncio.CancelledError) as exc:
            if isinstance(exc, asyncio.CancelledError):
                status, error = RunStatus.interrupted, AppError("workflow_cancelled", "分析被中断，请重试", 503)
            elif isinstance(exc, asyncio.TimeoutError):
                status, error = RunStatus.failed, AppError("workflow_timeout", "工作流超过时间预算，已保留中间结果", 504)
            elif isinstance(exc, AppError):
                status, error = RunStatus.failed, exc
            else:
                status, error = RunStatus.failed, AppError("workflow_failed", "工作流失败，已保留中间结果", 500)
            await run_in_threadpool(self.repository.finish, run.id, status, revision, error.code, error.message)
            if record:
                record.finish("failed", run_id=run.id, error_code=error.code,
                              message=error.message, last_draft_version=latest.version if latest else None)
            if isinstance(exc, asyncio.CancelledError):
                raise
            error.details.append({"run_id": run.id, "workflow_trace_id": record.id if record else None})
            if error is exc:
                raise
            raise error from exc

    def get_draft(self, meeting_id: str) -> Draft:
        self.meetings.get(meeting_id)
        draft = self.repository.current_draft(meeting_id)
        if draft is None:
            raise AppError("draft_not_found", "尚未生成任务草稿", 404)
        return draft

    def history(self, meeting_id: str) -> list[Draft]:
        self.meetings.get(meeting_id)
        return self.repository.drafts(meeting_id)

    def edit(self, meeting_id: str, edit: DraftEdit) -> Draft:
        current = self.get_draft(meeting_id)
        if current.version != edit.base_version:
            raise AppError("draft_version_conflict", "草稿版本已更新，请刷新", 409,
                           [{"current_version": current.version}])
        meeting = self.meetings.get(meeting_id)
        old = {task.local_id: task for task in current.tasks}
        tasks = [task.model_copy(deep=True) for task in edit.tasks]
        for task in tasks:
            previous = old.get(task.local_id)
            for field, evidence_field in (("owner", "owner_evidence"), ("due_date", "due_evidence")):
                value = getattr(task, field)
                evidence = getattr(task, evidence_field)
                changed = previous is None or value != getattr(previous, field) or evidence != getattr(previous, evidence_field)
                if value is None:
                    setattr(task, evidence_field, None)
                elif changed:
                    note = edit.resolution_note or (evidence.confirmation if evidence and evidence.source_type == "human" else "")
                    if not note:
                        raise AppError("human_confirmation_required", "人工修改负责人或日期必须填写确认说明", 422)
                    setattr(task, evidence_field, FieldEvidence(source_type="human", confirmation=note))
        issues = validate_plan(meeting, Plan(tasks=tasks))
        review = Review(draft_version=1, approved=False, summary="人工编辑后待 Reviewer 复核", issues=issues)
        draft = Draft(meeting_id=meeting_id, run_id=current.run_id, version=1, tasks=tasks,
                      status=DraftStatus.pending_review, review=review, resolution_note=edit.resolution_note)
        return self.repository.save_draft(draft, expected_version=edit.base_version, require_idle=True)
