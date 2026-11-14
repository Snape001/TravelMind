from typing import Literal

from travelmind.schemas.domain import TaskStatus

TaskTransition = Literal[
    "create",
    "claim",
    "request_pause",
    "pause_reached",
    "clarification_required",
    "resume",
    "answer",
    "retry",
    "complete",
    "fail",
]

_NEXT_STATUS: dict[tuple[TaskStatus | None, TaskTransition], TaskStatus] = {
    (None, "create"): "queued",
    ("queued", "claim"): "running",
    ("queued", "request_pause"): "paused",
    ("running", "request_pause"): "running",
    ("running", "pause_reached"): "paused",
    ("running", "clarification_required"): "needs_clarification",
    ("paused", "resume"): "queued",
    ("needs_clarification", "answer"): "queued",
    ("running", "retry"): "queued",
    ("running", "complete"): "completed",
    ("running", "fail"): "failed",
}


class InvalidTaskTransition(ValueError):
    pass


def next_task_status(current: TaskStatus | None, transition: TaskTransition) -> TaskStatus:
    """Evaluate the public transition. Callers enforce preconditions under the task lock."""
    try:
        return _NEXT_STATUS[current, transition]
    except KeyError as exc:
        raise InvalidTaskTransition(
            f"cannot apply {transition} to task in state {current}"
        ) from exc
