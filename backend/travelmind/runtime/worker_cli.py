import argparse
import asyncio
import logging
import sys
from contextlib import AsyncExitStack
from datetime import datetime
from uuid import UUID

import httpx
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import async_sessionmaker

from travelmind.config import WAKE_CHANNEL, Settings, get_settings
from travelmind.db.connection import create_database_engine
from travelmind.db.worker import PostgresWorkerStore
from travelmind.research.amap_client import AmapClient
from travelmind.research.amap_poi import AmapPoi
from travelmind.research.engine import ResearchEngine
from travelmind.research.quality import EvidenceQualityScorer
from travelmind.runtime.checkpoints import (
    FencedPostgresSaver,
    checkpoint_dsn,
    initialize_checkpoints,
)
from travelmind.runtime.durable_worker import DurableWorker
from travelmind.runtime.intake.worker import IntakeWorker
from travelmind.runtime.model import StructuredModel, open_structured_model
from travelmind.runtime.reflection import ReflectionConfig
from travelmind.runtime.retrieval import ResearchRetriever
from travelmind.schemas.agents import AgentInput
from travelmind.schemas.domain import Evidence
from travelmind.schemas.research_quality import EvidenceQuality

logger = logging.getLogger(__name__)


def reflection_for(model: StructuredModel, model_id: str) -> ReflectionConfig:
    scorer = EvidenceQualityScorer(model=model, model_id=model_id)
    screen = EvidenceQualityScorer()

    async def rank(
        request: AgentInput, *, as_of: datetime, max_age_days: int | None
    ) -> list[Evidence]:
        ranked = await screen.assess(
            request, request.evidence, as_of=as_of, max_age_days=max_age_days
        )
        return [
            item.evidence for item in sorted(ranked, key=lambda item: (item.grade, -item.score))
        ]

    async def score(
        request: AgentInput, *, as_of: datetime, max_age_days: int | None
    ) -> list[EvidenceQuality]:
        # Reflection sends at most one semantic batch and owns its retry allowance.
        budget = request.budget.model_copy(update={"max_attempts": 1})
        items = await scorer.assess(
            request.model_copy(update={"budget": budget}),
            request.evidence,
            as_of=as_of,
            max_age_days=max_age_days,
            require_contract_cards=True,
        )
        return [item.quality for item in items if item.quality is not None]

    return ReflectionConfig(model_id=model_id, score=score, rank=rank)


async def listen(redis: Redis, wake: asyncio.Event) -> None:
    while True:
        try:
            async with redis.pubsub() as subscriber:
                await subscriber.subscribe(WAKE_CHANNEL)
                async for message in subscriber.listen():
                    if message["type"] == "message":
                        wake.set()
        except (RedisError, OSError) as exc:
            logger.warning("worker.redis_unavailable type=%s", type(exc).__name__)
            await asyncio.sleep(5)


async def log_liveness(worker_id: UUID) -> None:
    while True:
        logger.info("worker.alive worker_id=%s", worker_id)
        await asyncio.sleep(15)


async def serve(settings: Settings, lease_seconds: int, poll_seconds: float) -> None:
    async with AsyncExitStack() as resources:
        model = await resources.enter_async_context(open_structured_model(settings))
        engine = create_database_engine(settings)
        resources.push_async_callback(engine.dispose)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = PostgresWorkerStore(sessions)
        client = await resources.enter_async_context(httpx.AsyncClient())
        research = ResearchEngine(client, settings)
        pool = await resources.enter_async_context(
            AsyncConnectionPool(
                checkpoint_dsn(settings),
                min_size=1,
                max_size=6,
                open=False,
                kwargs={"autocommit": True, "row_factory": dict_row},
                timeout=settings.dependency_timeout_seconds,
            )
        )
        worker = DurableWorker(
            store,
            model,
            lambda lease: FencedPostgresSaver(pool, store, lease),
            retrieve=lambda lease: ResearchRetriever(research, sessions, store=store, lease=lease),
            reflection=reflection_for(model, settings.deepseek_model),
            intake=IntakeWorker(
                store, AmapPoi(AmapClient(client, settings.amap_web_api_key)), model
            ),
            lease_seconds=lease_seconds,
            poll_seconds=poll_seconds,
        )
        wake = asyncio.Event()

        async def wait_for_work(seconds: float) -> None:
            try:
                await asyncio.wait_for(wake.wait(), seconds)
            except TimeoutError:
                pass
            wake.clear()

        async with asyncio.TaskGroup() as tasks:
            heartbeat = tasks.create_task(log_liveness(worker.worker_id))
            subscriber = None
            if settings.redis_url is not None:
                redis = Redis.from_url(
                    settings.redis_url.get_secret_value(),
                    socket_connect_timeout=settings.dependency_timeout_seconds,
                )
                resources.push_async_callback(redis.aclose)
                subscriber = tasks.create_task(listen(redis, wake))
            try:
                await worker.serve(wait_for_work)
            finally:
                heartbeat.cancel()
                if subscriber is not None:
                    subscriber.cancel()


def main() -> int:
    parser = argparse.ArgumentParser(description="TravelMind durable research worker")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("init-checkpoints", help="Initialize or upgrade Checkpoint tables")
    run = subcommands.add_parser("run", help="Poll PostgreSQL and execute queued research")
    run.add_argument("--lease-seconds", type=int, default=45)
    run.add_argument("--poll-seconds", type=float, default=2)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    # Transport INFO logs include request URLs. Provider loggers supply safe metrics.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    if sys.platform == "win32":
        # Psycopg does not support Windows' default Proactor loop.
        loop_factory = asyncio.SelectorEventLoop
    else:
        loop_factory = asyncio.new_event_loop
    try:
        settings = get_settings()
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            if args.command == "init-checkpoints":
                runner.run(initialize_checkpoints(settings))
            else:
                runner.run(serve(settings, args.lease_seconds, args.poll_seconds))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logger.error("worker.startup_failed type=%s", type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
