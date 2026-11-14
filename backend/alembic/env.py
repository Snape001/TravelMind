import asyncio

from alembic import context
from travelmind.config import get_settings
from travelmind.db.connection import create_database_engine
from travelmind.db.models import Base

CHECKPOINT_TABLES = {
    "checkpoint_blobs",
    "checkpoint_migrations",
    "checkpoint_writes",
    "checkpoints",
}


def include_name(name, object_type, _parent_names):
    # LangGraph upgrades these tables through the Worker CLI.
    return object_type != "table" or name not in CHECKPOINT_TABLES


def run_migrations(connection):
    context.configure(
        connection=connection, target_metadata=Base.metadata, include_name=include_name
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_online():
    engine = create_database_engine(get_settings())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(dialect_name="postgresql", target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_online())
