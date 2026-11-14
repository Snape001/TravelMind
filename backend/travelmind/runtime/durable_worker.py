import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4, uuid5

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from travelmind.runtime.controls import GRAPH_VERSION
from travelmind.runtime.experts import EvidenceRetriever
from travelmind.runtime.journal import ControlContext
from travelmind.runtime.model import StructuredModel
from travelmind.runtime.projection import batches
from travelmind.runtime.workflow import build_research_graph
from travelmind.schemas.agents import ErrorInfo
from travelmind.schemas.worker import (
    AnswerClarification,
    AttemptFailure,
    LeaseLost,
    LeaseRef,
    ProjectionConflict,
    ResumePause,
    StartResearch,
    TaskClaim,
    WorkerStore,
)

logger = logging.getLogger(__name__)


class DurableWorker:
    def __init__(
        self,
        store: WorkerStore,
        model: StructuredModel,
        saver: Callable[[LeaseRef], BaseCheckpointSaver],
        *,
        retrieve: Callable[[LeaseRef], EvidenceRetriever] | None = None,
        lease_seconds: int = 45,
        poll_seconds: float = 2,
        worker_id: UUID | None = None,
    ):
        if lease_seconds < 1 or not math.isfinite(poll_seconds) or poll_seconds <= 0:
            raise ValueError("lease and poll intervals must be positive")
        self.store = store
        self.model = model
        self.saver = saver
        self.retrieve = retrieve
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self.worker_id = worker_id or uuid4()

    async def _heartbeat(self, lease: LeaseRef) -> None:
        while True:
            await asyncio.sleep(self.lease_seconds / 3)
            await self.store.renew(lease, lease_seconds=self.lease_seconds)

    async def run_claim(self, claim: TaskClaim) -> None:
        execution = asyncio.create_task(self.execute(claim))
        heartbeat = asyncio.create_task(self._heartbeat(claim.lease))
        try:
            await asyncio.wait({execution, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
            # A heartbeat can see a released lease just after the terminal projection commits.
            if execution.done():
                await execution
            else:
                await heartbeat
        except LeaseLost:
            logger.info(
                "worker.lease_lost task_id=%s epoch=%s",
                claim.lease.task_id,
                claim.lease.lease_epoch,
            )
        except ProjectionConflict:
            logger.error("worker.projection_conflict task_id=%s", claim.lease.task_id)
            try:
                await self._failure(claim.lease, "CHECKPOINT_PROJECTION_CONFLICT", retryable=False)
            except LeaseLost:
                pass
        finally:
            execution.cancel()
            heartbeat.cancel()
            await asyncio.gather(execution, heartbeat, return_exceptions=True)

    async def serve(
        self, wait_for_work: Callable[[float], Awaitable[None]] = asyncio.sleep
    ) -> None:
        while True:
            try:
                claim = await self.store.claim(self.worker_id, lease_seconds=self.lease_seconds)
                if claim is not None:
                    await self.run_claim(claim)
                    continue
            except Exception as exc:
                logger.error(
                    "worker.attempt_interrupted worker_id=%s type=%s",
                    self.worker_id,
                    type(exc).__name__,
                )
            await wait_for_work(self.poll_seconds)

    async def execute(self, claim: TaskClaim) -> None:
        """Reconcile the last checkpoint before issuing any further graph work."""
        execution_permitted = getattr(claim, "execution_permitted", None)
        attempt_count = getattr(claim, "attempt_count", None)
        max_attempts = getattr(claim, "max_attempts", None)
        if (
            type(execution_permitted) is not bool
            or type(attempt_count) is not int
            or type(max_attempts) is not int
            or not 1 <= attempt_count <= max_attempts
            or (not execution_permitted and (not claim.reclaimed or attempt_count != max_attempts))
        ):
            raise ValueError("TaskClaim is missing a valid retry budget")

        lease = claim.lease
        saver = self.saver(lease)
        graph = build_research_graph(
            self.model,
            checkpointer=saver,
            retrieve=self.retrieve(lease) if execution_permitted and self.retrieve else None,
        )
        config = {"configurable": {"thread_id": str(lease.task_id), "graph_version": GRAPH_VERSION}}
        force_pause = False

        async def should_pause() -> bool:
            current = await self.store.load_instruction(lease)
            return force_pause or current.pause_requested_at is not None

        while True:
            # The durable cursor is authoritative after an ambiguous response or restart.
            current = await self.store.load_instruction(lease)
            checkpoint = await saver.aget_tuple(config)
            snapshot = None
            if checkpoint is not None:
                if checkpoint.metadata.get("graph_version") != GRAPH_VERSION:
                    await self._failure(lease, "CHECKPOINT_VERSION_MISMATCH", retryable=False)
                    return
                # Pin to the committed checkpoint. Latest/live state additionally applies
                # pending writes, which must not be projected until a superstep commits.
                snapshot = await graph.aget_state(checkpoint.config)
                applied = current.projected_index
                projections = batches(lease.task_id, snapshot)
                for batch in projections:
                    if batch.projection_index <= applied:
                        continue
                    if batch.projection_index != applied + 1:
                        raise ProjectionConflict("checkpoint projection is not contiguous")
                    await self.store.commit_projection(lease, batch)
                    applied = batch.projection_index
                    if batch.transition is not None:
                        return
                if applied != current.projected_index:
                    current = await self.store.load_instruction(lease)

            if not execution_permitted:
                await self._failure(
                    lease,
                    "ATTEMPT_BUDGET_EXHAUSTED",
                    message="任务执行次数已达上限",
                    retryable=False,
                )
                return

            if snapshot is None:
                if not isinstance(current.instruction, StartResearch):
                    await self._failure(lease, "CHECKPOINT_MISSING", retryable=False)
                    return
                inputs = {
                    "task_id": UUID(str(lease.task_id)),
                    "trace_id": UUID(str(claim.trace_id)),
                    "brief": current.instruction.brief,
                    "start_instruction_id": UUID(str(current.instruction.instruction_id)),
                }
            else:
                inputs = None
                if snapshot.interrupts:
                    [halt] = snapshot.interrupts
                    instruction = current.instruction
                    expected = ResumePause if halt.value["kind"] == "pause" else AnswerClarification
                    if not isinstance(instruction, expected) or instruction.interrupt_id != halt.id:
                        await self._failure(
                            lease, "CHECKPOINT_INSTRUCTION_MISMATCH", retryable=False
                        )
                        return
                    if isinstance(instruction, AnswerClarification):
                        if str(instruction.clarification_id) != halt.value["clarification_id"]:
                            await self._failure(
                                lease, "CHECKPOINT_INSTRUCTION_MISMATCH", retryable=False
                            )
                            return
                    inputs = Command(resume={halt.id: instruction.model_dump(mode="json")})
                    # A resumed pause re-enters the same gate even after the API cleared
                    # pause_requested_at, so LangGraph can deliver the saved resume value.
                    force_pause = isinstance(instruction, ResumePause)
                elif not snapshot.next:
                    await self._failure(lease, "CHECKPOINT_NO_TRANSITION", retryable=False)
                    return

            try:
                # Stop after each committed superstep. This bounds any unprojected work
                # to the latest checkpoint (plus an interrupt on that checkpoint).
                await graph.ainvoke(
                    inputs,
                    config,
                    context=ControlContext(should_pause, current.projected_index + 1),
                    interrupt_after="*",
                    durability="sync",
                )
            except (LeaseLost, ProjectionConflict):
                raise
            except Exception as exc:
                logger.warning(
                    "worker.graph_failed task_id=%s type=%s", lease.task_id, type(exc).__name__
                )
                # The graph's last node may have completed just before a persistence error.
                latest = await saver.aget_tuple(config)
                if latest is not None and latest.config != (
                    checkpoint.config if checkpoint else None
                ):
                    continue
                await self._failure(lease, "TASK_EXECUTION_FAILED", retryable=True)
                return
            finally:
                force_pause = False

    async def _failure(
        self, lease: LeaseRef, code: str, *, retryable: bool, message: str = "调研执行中断"
    ) -> None:
        await self.store.commit_projection(
            lease,
            AttemptFailure(
                failure_id=uuid5(lease.task_id, f"attempt:{lease.lease_epoch}"),
                error=ErrorInfo(code=code, message=message, retryable=retryable),
            ),
        )
