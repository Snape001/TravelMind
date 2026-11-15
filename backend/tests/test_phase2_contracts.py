from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from travelmind.schemas.api import (
    ClarificationAnswers,
    IdempotencyKey,
    PendingClarification,
    TaskRead,
)
from travelmind.schemas.domain import TaskStatus
from travelmind.schemas.events import ClarificationEvent
from travelmind.schemas.task_control import InvalidTaskTransition, TaskTransition, next_task_status
from travelmind.schemas.worker import (
    ExecutionInstruction,
    LeaseRef,
    ProjectedEvent,
    ProjectionBatch,
    TaskClaim,
)

EXPECTED_TRANSITIONS = {
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


def test_status_machine_has_only_the_declared_transitions():
    statuses = TypeAdapter(TaskStatus).json_schema()["enum"]
    assert set(statuses) == {
        "queued",
        "running",
        "paused",
        "needs_clarification",
        "completed",
        "failed",
    }
    assert set(TypeAdapter(TaskTransition).json_schema()["enum"]) == {
        event for _, event in EXPECTED_TRANSITIONS
    }
    for status in [None, *statuses]:
        for event in {event for _, event in EXPECTED_TRANSITIONS}:
            if (status, event) in EXPECTED_TRANSITIONS:
                assert next_task_status(status, event) == EXPECTED_TRANSITIONS[status, event]
            else:
                with pytest.raises(InvalidTaskTransition):
                    next_task_status(status, event)


def test_task_read_accepts_legacy_records_and_structured_questions():
    now = datetime.now(UTC)
    data = {
        "task_id": uuid4(),
        "trace_id": uuid4(),
        "brief": {"destination": "泉州"},
        "status": "needs_clarification",
        "created_at": now,
        "updated_at": now,
    }
    legacy = TaskRead.model_validate(data)
    assert legacy.pending_clarification is legacy.pause_requested_at is None
    pending = PendingClarification(
        clarification_id=uuid4(), questions=[{"question_id": uuid4(), "text": " 哪座古城？ "}]
    )
    task = TaskRead.model_validate({**data, "pending_clarification": pending})
    assert task.pending_clarification.questions[0].text == "哪座古城？"
    event = ClarificationEvent(type="task.clarification", questions=["哪座古城？"])
    assert event.model_dump() == {"type": "task.clarification", "questions": ["哪座古城？"]}
    with pytest.raises(ValidationError):
        PendingClarification(clarification_id=uuid4(), questions=pending.questions * 2)


def test_answers_require_unique_questions_and_nonblank_bounded_text():
    question_id = uuid4()
    answer = {"question_id": question_id, "text": " 泉州 "}
    assert ClarificationAnswers(answers=[answer]).answers[0].text == "泉州"
    for answers in (
        [],
        [answer, answer],
        [answer | {"text": " "}],
        [answer | {"text": "x" * 4001}],
    ):
        with pytest.raises(ValidationError):
            ClarificationAnswers(answers=answers)


def test_idempotency_keys_enforce_visible_ascii_and_length():
    keys = TypeAdapter(IdempotencyKey)
    assert keys.validate_python("a-9!") == "a-9!"
    for key in ("", "with space", "ä", "a" * 129):
        with pytest.raises(ValidationError):
            keys.validate_python(key)


def test_worker_instruction_and_projection_keep_internal_identity_separate_from_events():
    lease = {"task_id": uuid4(), "worker_id": uuid4(), "lease_epoch": 1}
    with pytest.raises(ValidationError):
        LeaseRef.model_validate(lease | {"lease_epoch": 0})
    instructions = TypeAdapter(ExecutionInstruction)
    instruction = instructions.validate_python(
        {
            "instruction_id": uuid4(),
            "kind": "answer_clarification",
            "interrupt_id": "graph-interrupt",
            "clarification_id": uuid4(),
            "response": {"answers": [{"question_id": uuid4(), "text": "泉州"}]},
        }
    )
    assert instructions.validate_json(instruction.model_dump_json()) == instruction
    event = ProjectedEvent(
        projection_key=uuid4(),
        payload={
            "type": "agent.message",
            "agent_id": "director",
            "run_id": uuid4(),
            "message": "整理简报",
        },
    )
    with pytest.raises(ValidationError):
        ProjectionBatch(checkpoint_id="checkpoint", projection_index=1, events=[event, event])
    with pytest.raises(ValidationError):
        ProjectionBatch(checkpoint_id="checkpoint", projection_index=1, transition="retry")
    with pytest.raises(ValidationError):
        ProjectionBatch(
            checkpoint_id="checkpoint",
            projection_index=1,
            events=[
                {
                    "projection_key": uuid4(),
                    "payload": {"type": "task.clarification", "questions": ["哪里？"]},
                }
            ],
        )


def test_claim_requires_explicit_execution_permission_and_budget():
    fields = {
        "lease": {"task_id": uuid4(), "worker_id": uuid4(), "lease_epoch": 1},
        "trace_id": uuid4(),
        "lease_until": datetime.now(UTC),
        "reclaimed": False,
        "attempt_count": 1,
        "max_attempts": 1,
        "execution_permitted": True,
    }
    assert TaskClaim.model_validate(fields).execution_permitted
    with pytest.raises(ValidationError):
        TaskClaim.model_validate(fields | {"attempt_count": 2})
    with pytest.raises(ValidationError):
        TaskClaim.model_validate(fields | {"execution_permitted": False})
    assert not TaskClaim.model_validate(
        fields | {"reclaimed": True, "execution_permitted": False}
    ).execution_permitted
