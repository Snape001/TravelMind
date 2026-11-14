import logging
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.exc import OperationalError, SQLAlchemyError, TimeoutError
from sqlalchemy.ext.asyncio import async_sessionmaker

from travelmind.api import health, tasks
from travelmind.config import Settings, get_settings
from travelmind.db.connection import create_database_engine
from travelmind.schemas.api import ErrorInfo, ErrorResponse

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with AsyncExitStack() as resources:
            app.state.settings = config
            app.state.engine = None
            app.state.sessions = None
            app.state.redis = None
            if config.database_url is not None:
                engine = create_database_engine(config)
                resources.push_async_callback(engine.dispose)
                app.state.engine = engine
                app.state.sessions = async_sessionmaker(engine, expire_on_commit=False)
            if config.redis_url is not None:
                redis = Redis.from_url(
                    config.redis_url.get_secret_value(),
                    socket_connect_timeout=config.dependency_timeout_seconds,
                    socket_timeout=config.dependency_timeout_seconds,
                )
                resources.push_async_callback(redis.aclose)
                app.state.redis = redis
            yield

    app = FastAPI(title="TravelMind API", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(tasks.router)

    @app.exception_handler(SQLAlchemyError)
    @app.exception_handler(OSError)
    async def database_error(request: Request, exc: Exception) -> JSONResponse:
        unavailable = isinstance(exc, (OperationalError, OSError, TimeoutError))
        logger.error("database.error type=%s", type(exc).__name__)
        error = ErrorInfo(
            code="DATABASE_UNAVAILABLE" if unavailable else "DATABASE_ERROR",
            message="任务数据库不可用" if unavailable else "任务数据库操作失败",
            retryable=unavailable,
        )
        return JSONResponse(
            ErrorResponse(detail=error).model_dump(), status_code=503 if unavailable else 500
        )

    return app


app = create_app()
