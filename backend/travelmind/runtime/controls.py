from uuid import UUID, uuid5

from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import TypeAdapter

from travelmind.runtime.journal import ControlContext
from travelmind.runtime.state import ResearchState
from travelmind.schemas.api import ClarificationQuestion, PendingClarification
from travelmind.schemas.worker import AnswerClarification, ExecutionInstruction, ResumePause

GRAPH_VERSION = "durable-v1"
REFLECTION_GRAPH_VERSION = "durable-v2"
instruction_adapter = TypeAdapter(ExecutionInstruction)


async def pause_gate(_state: ResearchState, runtime: Runtime[ControlContext]) -> dict:
    if not await runtime.context.should_pause():
        return {}
    response = instruction_adapter.validate_python(
        interrupt(
            {
                "kind": "pause",
                "projection_index": runtime.context.projection_index,
            }
        )
    )
    if not isinstance(response, ResumePause):
        raise ValueError("pause requires a resume_pause instruction")
    return {"consumed": [response.instruction_id]}


def clarification_for(state: ResearchState, questions: list[str]) -> PendingClarification:
    round_id = uuid5(
        UUID(str(state["task_id"])), f"clarification:{len(state.get('clarification_history', []))}"
    )
    return PendingClarification(
        clarification_id=round_id,
        questions=[
            ClarificationQuestion(question_id=uuid5(round_id, str(i)), text=question)
            for i, question in enumerate(questions)
        ],
    )


async def clarification(state: ResearchState, runtime: Runtime[ControlContext]) -> dict:
    pending = state["pending_clarification"]
    response = instruction_adapter.validate_python(
        interrupt(
            {
                "kind": "clarification",
                "clarification_id": str(pending.clarification_id),
                "projection_index": runtime.context.projection_index,
            }
        )
    )
    if (
        not isinstance(response, AnswerClarification)
        or response.clarification_id != pending.clarification_id
    ):
        raise ValueError("answer does not match the clarification round")
    answers = {entry.question_id: entry.text for entry in response.response.answers}
    if set(answers) != {entry.question_id for entry in pending.questions}:
        raise ValueError("answer does not match the clarification questions")
    return {
        "status": "running",
        "consumed": [response.instruction_id],
        "clarification_history": [
            *state.get("clarification_history", []),
            *[
                {"question": entry.text, "answer": answers[entry.question_id]}
                for entry in pending.questions
            ],
        ],
    }
