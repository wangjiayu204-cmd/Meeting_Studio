from enum import Enum


class RunStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    interrupted = "interrupted"


class RunType(str, Enum):
    analysis = "analysis"
    review = "review"
    execution = "execution"


class DraftStatus(str, Enum):
    pending_review = "pending_review"
    needs_attention = "needs_attention"
    ready = "ready"
    confirmed = "confirmed"


class ExecutionStatus(str, Enum):
    pending = "pending"
    succeeded = "succeeded"
    failed = "failed"


class TaskStatus(str, Enum):
    todo = "todo"
    in_progress = "in_progress"
    done = "done"


class AgentRole(str, Enum):
    manager = "manager"
    planner = "planner"
    reviewer = "reviewer"
    executor = "executor"


class EventType(str, Enum):
    run_started = "run_started"
    agent_started = "agent_started"
    agent_completed = "agent_completed"
    review_feedback = "review_feedback"
    tool_result = "tool_result"
    run_failed = "run_failed"
    run_completed = "run_completed"
