from sqlalchemy.ext.asyncio import create_async_engine

from travelmind.config import Settings


def create_database_engine(settings: Settings):
    if settings.database_url is None:
        raise ValueError("DATABASE_URL is required")
    return create_async_engine(
        settings.database_url.get_secret_value(),
        hide_parameters=True,
        pool_pre_ping=True,
        pool_timeout=settings.dependency_timeout_seconds,
        connect_args={"timeout": settings.dependency_timeout_seconds},
    )
