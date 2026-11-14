from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import ChannelVersions, Checkpoint, CheckpointMetadata
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel
from sqlalchemy.engine import make_url

from travelmind.config import Settings
from travelmind.runtime.workflow import ExpertExchange, ExpertRun
from travelmind.schemas import agents, api, domain, events
from travelmind.schemas.worker import LeaseLost, LeaseRef, WorkerStore


def checkpoint_dsn(settings: Settings) -> str:
    if settings.database_url is None:
        raise ValueError("DATABASE_URL is required")
    url = make_url(settings.database_url.get_secret_value()).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


def checkpoint_serializer() -> JsonPlusSerializer:
    types = [ExpertRun, ExpertExchange]
    for module in (agents, api, domain, events):
        types.extend(
            item
            for item in vars(module).values()
            if isinstance(item, type)
            and issubclass(item, BaseModel)
            and item.__module__ == module.__name__
        )
    allowlist = [(item.__module__, item.__name__) for item in types]
    return JsonPlusSerializer(allowed_msgpack_modules=allowlist)


class FencedPostgresSaver(AsyncPostgresSaver):
    """Bind both checkpoint write paths to the lease checked on their connection."""

    def __init__(self, pool: AsyncConnectionPool, store: WorkerStore, lease: LeaseRef):
        super().__init__(pool, serde=checkpoint_serializer())
        self.pool = pool
        self.store = store
        self.lease = lease

    @asynccontextmanager
    async def _writer(self, config: RunnableConfig) -> AsyncIterator[AsyncPostgresSaver]:
        if config["configurable"]["thread_id"] != str(self.lease.task_id):
            raise LeaseLost("checkpoint thread does not match the lease")
        async with self.pool.connection() as conn, conn.transaction():
            await self.store.check_lease(self.lease, transaction=conn)
            yield AsyncPostgresSaver(conn, serde=self.serde)

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        async with self._writer(config) as saver:
            return await saver.aput(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        async with self._writer(config) as saver:
            await saver.aput_writes(config, writes, task_id, task_path)


async def initialize_checkpoints(settings: Settings) -> None:
    async with AsyncPostgresSaver.from_conn_string(checkpoint_dsn(settings)) as saver:
        await saver.setup()
