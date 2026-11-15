import logging
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.exc import OperationalError, SQLAlchemyError, TimeoutError
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from travelmind.api import account, amap, auth, experts, health, intake, tasks
from travelmind.api.oidc import AuthSettings, OidcClient
from travelmind.config import Settings, get_settings
from travelmind.db.connection import create_database_engine
from travelmind.schemas.api import ErrorInfo, ErrorResponse
from travelmind.server_logging import access_log_filter, amap_log_filter

logger = logging.getLogger(__name__)


class AccountResponseHeaders:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith(("/api/v1/auth/", "/api/v1/account/")):
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                if path.startswith("/api/v1/auth/"):
                    headers["Referrer-Policy"] = "no-referrer"
            await send(message)

        await self.app(scope, receive, send_with_headers)


def create_app(
    settings: Settings | None = None, *, auth_settings: AuthSettings | None = None
) -> FastAPI:
    config = settings if settings is not None else get_settings()
    identity = auth_settings if auth_settings is not None else AuthSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.getLogger("uvicorn.access").addFilter(access_log_filter)
        logging.getLogger("httpx").addFilter(amap_log_filter)
        async with AsyncExitStack() as resources:
            app.state.settings = config
            app.state.engine = None
            app.state.sessions = None
            app.state.redis = None
            app.state.auth_settings = identity
            app.state.oidc = None
            app.state.amap_http = await resources.enter_async_context(
                httpx.AsyncClient(timeout=8, follow_redirects=False)
            )
            if identity.issuer is not None:
                client = await resources.enter_async_context(httpx.AsyncClient(timeout=10))
                app.state.oidc = OidcClient(client, identity)
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
    app.add_middleware(AccountResponseHeaders)
    app.include_router(amap.router)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(account.router)
    app.include_router(experts.router)
    app.include_router(intake.router)
    app.include_router(tasks.router)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        route = request.scope.get("route")
        if getattr(route, "name", None) == "submit_questionnaire" and any(
            item["loc"][0] == "body" for item in exc.errors()
        ):
            error = ErrorInfo(
                code="INVALID_QUESTIONNAIRE_ANSWERS",
                message="答复格式无效，请核对当前问卷",
                retryable=False,
            )
            return JSONResponse(ErrorResponse(detail=error).model_dump(), status_code=422)
        if getattr(route, "name", None) in {
            "pause_task",
            "resume_task",
            "answer_task",
            "submit_destination",
            "submit_questionnaire",
            "account_reports",
        }:
            error = ErrorInfo(code="VALIDATION_ERROR", message="请求参数无效", retryable=False)
            return JSONResponse(ErrorResponse(detail=error).model_dump(), status_code=422)
        return await request_validation_exception_handler(request, exc)

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
