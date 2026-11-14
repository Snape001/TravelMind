import asyncio
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request, Response
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from travelmind.schemas.api import DependencyStatus, Liveness, Readiness

router = APIRouter(prefix="/health", tags=["health"])


async def check_postgres(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT task_id FROM research_tasks LIMIT 0"))


async def check_redis(redis: Redis) -> None:
    await redis.ping()


async def check_dependency(
    check: Callable[[], Awaitable[None]] | None, timeout: float
) -> DependencyStatus:
    if check is None:
        return "not_configured"
    try:
        async with asyncio.timeout(timeout):
            await check()
    except (OSError, SQLAlchemyError, RedisError):
        return "unavailable"
    return "ok"


@router.get("/live")
async def live() -> Liveness:
    return Liveness()


@router.get("/ready", responses={503: {"model": Readiness}})
async def ready(request: Request, response: Response) -> Readiness:
    engine = request.app.state.engine
    redis = request.app.state.redis
    settings = request.app.state.settings
    timeout = settings.dependency_timeout_seconds
    postgres_status, redis_status = await asyncio.gather(
        check_dependency(None if engine is None else lambda: check_postgres(engine), timeout),
        check_dependency(None if redis is None else lambda: check_redis(redis), timeout),
    )
    is_ready = postgres_status == "ok"
    model_configured = bool(
        settings.deepseek_api_key and settings.deepseek_api_key.get_secret_value()
    )
    if not is_ready:
        response.status_code = 503
    return Readiness(
        status="ready" if is_ready else "unavailable",
        postgres=postgres_status,
        redis=redis_status,
        deepseek="configured" if model_configured else "not_configured",
    )
