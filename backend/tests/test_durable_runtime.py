import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_runtime import Model, source

from travelmind.runtime.checkpoints import checkpoint_serializer
from travelmind.runtime.durable_worker import DurableWorker
from travelmind.schemas.api import ClarificationAnswers
from travelmind.schemas.domain import ResearchBrief
from travelmind.schemas.worker import (
    AnswerClarification,
    ContinueCheckpoint,
    LeaseLost,
    LeaseRef,
    ProjectionBatch,
    ResumePause,
    StartResearch,
    TaskClaim,
    WorkerInstruction,
)


class Store:
    def __init__(self, destination):
        self.task_id = uuid4()
        self.trace_id = uuid4()
        self.instruction = StartResearch(
            kind="start", instruction_id=uuid4(), brief=ResearchBrief(destination=destination)
        )
        self.batches = []
        self.paused_at = None

    async def load_instruction(self, _):
        return WorkerInstruction(
            instruction=self.instruction,
            projected_index=len(self.batches),
            pause_requested_at=self.paused_at,
        )

    async def commit_projection(self, _, batch):
        assert isinstance(batch, ProjectionBatch)
        assert batch.projection_index == len(self.batches) + 1
        self.batches.append(batch)
        if batch.consumed_instruction_id is not None:
            assert batch.consumed_instruction_id == self.instruction.instruction_id
            self.instruction = ContinueCheckpoint(
                kind="continue_checkpoint", instruction_id=uuid4()
            )
        return "applied"

    def claim(self, epoch=1):
        return TaskClaim(
            lease=LeaseRef(task_id=self.task_id, worker_id=uuid4(), lease_epoch=epoch),
            trace_id=self.trace_id,
            lease_until=datetime.now(UTC),
            reclaimed=epoch > 1,
        )


def test_clarification_resume_survives_worker_restart():
    async def run():
        store = Store("古城")
        saver = InMemorySaver(serde=checkpoint_serializer())
        calls = []

        async def model(request, instructions, response, /):
            calls.append(request.agent_id)
            assert request.agent_id == "research_director"
            if "泉州" in instructions:
                return response(questions=["最关注什么？"])
            return response(questions=["哪座古城？"])

        await DurableWorker(store, model, lambda _: saver).execute(store.claim())
        pending = store.batches[-1]
        assert pending.transition == "clarification_required"
        data = pending.clarification
        answer = AnswerClarification(
            kind="answer_clarification",
            instruction_id=uuid4(),
            interrupt_id=pending.interrupt_id,
            clarification_id=data.clarification_id,
            response=ClarificationAnswers(
                answers=[{"question_id": data.questions[0].question_id, "text": "泉州"}]
            ),
        )
        store.instruction = answer
        await DurableWorker(store, model, lambda _: saver).execute(store.claim(2))
        assert calls == ["research_director", "research_director"]
        assert [batch.consumed_instruction_id for batch in store.batches].count(
            answer.instruction_id
        ) == 1
        assert store.batches[-1].clarification.questions[0].text == "最关注什么？"
        assert store.batches[-1].interrupt_id != pending.interrupt_id

    asyncio.run(run())


def test_pause_after_director_and_resume_without_repeating_it():
    async def run():
        evidence = source()
        store = Store("泉州")
        saver = InMemorySaver(serde=checkpoint_serializer())

        async def model_reply(request, _):
            if request.agent_id == "research_director":
                store.paused_at = datetime.now(UTC)

        model = Model(evidence, reply=model_reply)

        async def retrieve(_):
            return [evidence]

        def worker():
            return DurableWorker(store, model, lambda _: saver, retrieve=lambda _: retrieve)

        await worker().execute(store.claim())
        assert store.batches[-1].transition == "pause_reached"
        assert [request.agent_id for request, _ in model.calls] == ["research_director"]
        store.instruction = ResumePause(
            kind="resume_pause", interrupt_id=store.batches[-1].interrupt_id, instruction_id=uuid4()
        )
        store.paused_at = None
        await worker().execute(store.claim(2))
        assert store.batches[-1].transition == "complete"
        assert [request.agent_id for request, _ in model.calls].count("research_director") == 1
        assert len(store.batches[-1].reports) == 1
        assert {event.payload.type for event in store.batches[-1].events} >= {
            "report.ready",
            "report.reviewed",
        }

    asyncio.run(run())


@pytest.mark.parametrize("crash_at", ["initialize", "plan.ready", "complete"])
def test_missing_projection_is_replayed_before_the_next_node(crash_at):
    class StopWorker(Exception):
        pass

    class CrashStore(Store):
        dropped = None

        async def commit_projection(self, lease, batch):
            types = {entry.payload.type for entry in batch.events}
            crash = (
                crash_at == batch.transition
                or crash_at in types
                or (crash_at == "initialize" and batch.projection_index == 1)
            )
            if crash and self.dropped is None:
                self.dropped = batch
                raise StopWorker
            return await super().commit_projection(lease, batch)

    async def run():
        evidence = source()
        store = CrashStore("泉州")
        saver = InMemorySaver(serde=checkpoint_serializer())
        model = Model(evidence)

        async def retrieve(_):
            return [evidence]

        def worker():
            return DurableWorker(store, model, lambda _: saver, retrieve=lambda _: retrieve)

        with pytest.raises(StopWorker):
            await worker().execute(store.claim())
        assert store.dropped is not None
        await worker().execute(store.claim(2))
        assert store.batches[store.dropped.projection_index - 1] == store.dropped
        assert store.batches[-1].transition == "complete"
        assert [request.agent_id for request, _ in model.calls].count("planner") == 1
        assert [request.agent_id for request, _ in model.calls].count("reviewer") == 1

    asyncio.run(run())


def test_heartbeat_loss_cancels_the_inflight_model():
    class LostStore(Store):
        async def renew(self, lease, *, lease_seconds):
            await model_started.wait()
            raise LeaseLost

    async def model(*args):
        model_started.set()
        try:
            await asyncio.Future()
        finally:
            model_cancelled.set()

    async def run():
        store = LostStore("泉州")
        saver = InMemorySaver(serde=checkpoint_serializer())
        worker = DurableWorker(store, model, lambda _: saver, lease_seconds=1)
        await asyncio.wait_for(worker.run_claim(store.claim()), 5)
        assert model_cancelled.is_set()
        assert len(store.batches) == 1

    model_started, model_cancelled = asyncio.Event(), asyncio.Event()
    asyncio.run(run())


def test_crash_after_consuming_answer_does_not_submit_it_to_a_later_round():
    class SimulatedCrash(Exception):
        pass

    class CrashStore(Store):
        failed = False

        async def commit_projection(self, lease, batch):
            if isinstance(self.instruction, AnswerClarification) and not self.failed:
                assert batch.consumed_instruction_id == self.instruction.instruction_id
                self.failed = True
                raise SimulatedCrash
            return await super().commit_projection(lease, batch)

    async def run():
        store = CrashStore("古城")
        saver = InMemorySaver(serde=checkpoint_serializer())
        calls = 0

        async def model(_request, instructions, response, /):
            nonlocal calls
            calls += 1
            return response(questions=["最关注什么？" if "泉州" in instructions else "哪座古城？"])

        await DurableWorker(store, model, lambda _: saver).execute(store.claim())
        first = store.batches[-1]
        answer = AnswerClarification(
            kind="answer_clarification",
            instruction_id=uuid4(),
            interrupt_id=first.interrupt_id,
            clarification_id=first.clarification.clarification_id,
            response=ClarificationAnswers(
                answers=[
                    {"question_id": first.clarification.questions[0].question_id, "text": "泉州"},
                ]
            ),
        )
        store.instruction = answer
        with pytest.raises(SimulatedCrash):
            await DurableWorker(store, model, lambda _: saver).execute(store.claim(2))
        assert calls == 1
        await DurableWorker(store, model, lambda _: saver).execute(store.claim(3))
        assert calls == 2
        assert (
            sum(batch.consumed_instruction_id == answer.instruction_id for batch in store.batches)
            == 1
        )
        assert store.batches[-1].clarification.questions[0].text == "最关注什么？"

    asyncio.run(run())
