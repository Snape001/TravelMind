from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypedDict

from langgraph.runtime import Runtime

from travelmind.schemas.events import EventPayload


class JournalEvent(TypedDict):
    occurred_at: datetime
    payload: EventPayload


current_journal: ContextVar[list[JournalEvent] | None] = ContextVar("current_journal", default=None)


@dataclass(frozen=True)
class ControlContext:
    should_pause: Callable[[], Awaitable[bool]]
    projection_index: int


def journal_node(node: Callable[..., Awaitable[dict]]) -> Callable[..., Awaitable[dict]]:
    async def execute(state, runtime: Runtime[ControlContext]) -> dict:
        events: list[JournalEvent] = []
        token = current_journal.set(events)
        try:
            updates = await node(state)
            # Evidence contains HttpUrl, which the checkpoint MsgPack encoder cannot encode.
            for key in ("results", "evidence"):
                if key in updates:
                    updates[key] = {
                        str(record_id): item.model_dump(mode="json")
                        for record_id, item in updates[key].items()
                    }
            for key in ("assignment_runs", "challenge_runs"):
                if key in updates:
                    updates[key] = {
                        str(record_id): run_id for record_id, run_id in updates[key].items()
                    }
            return {
                **updates,
                "journal": events,
                "projection_indexes": [runtime.context.projection_index],
            }
        finally:
            current_journal.reset(token)

    return execute


def control_node(node: Callable[..., Awaitable[dict]]) -> Callable[..., Awaitable[dict]]:
    async def execute(state, runtime: Runtime[ControlContext]) -> dict:
        updates = await node(state, runtime)
        if updates:
            return {**updates, "projection_indexes": [runtime.context.projection_index]}
        return updates

    return execute


def record_event(payload: EventPayload) -> None:
    if (events := current_journal.get()) is not None:
        events.append({"occurred_at": datetime.now(UTC), "payload": payload})
