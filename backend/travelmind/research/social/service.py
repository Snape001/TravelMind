import asyncio
import os
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing
from datetime import UTC, datetime
from hmac import compare_digest
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from travelmind.research.social.cache import evidence_page
from travelmind.research.social.platforms import ImportStopped, load_export
from travelmind.research.social.schemas import (
    MAX_POSTS,
    ImportRequest,
    Keyword,
    Platform,
    SocialEvidenceRecord,
    SocialPage,
    keyword_key,
)
from travelmind.schemas.domain import Contract


class JobStatus(Contract):
    batch_id: UUID
    platform: Platform
    status: Literal["running", "completed", "partial", "cancelled", "failed"]
    started_at: datetime
    duration_ms: int = 0
    post_count: int = 0
    comment_count: int = 0
    rejected_rows: int = 0
    error_code: str | None = None


def create_social_app(root: Path, token: str) -> FastAPI:
    root = root.resolve()
    database = root / "social.sqlite3"
    active: tuple[UUID, Event, asyncio.Task] | None = None
    if not token:
        raise ValueError("the local social service requires a bearer token")
    expected = f"Bearer {token}".encode()

    def connect() -> sqlite3.Connection:
        return sqlite3.connect(database)

    def get_job(key: UUID) -> tuple[ImportRequest, JobStatus] | None:
        with closing(connect()) as db:
            row = db.execute(
                "SELECT request, job FROM jobs WHERE batch_id = ?", (str(key),)
            ).fetchone()
        if row is None:
            return None
        return ImportRequest.model_validate_json(row[0]), JobStatus.model_validate_json(row[1])

    def store_job(request: ImportRequest, job: JobStatus, items=()) -> None:
        with closing(connect()) as db, db:
            db.execute(
                """INSERT INTO jobs VALUES (?, ?, ?)
                   ON CONFLICT(batch_id) DO UPDATE SET job=excluded.job""",
                (str(request.batch_id), request.model_dump_json(), job.model_dump_json()),
            )
            db.executemany(
                """INSERT INTO evidence VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(platform, keyword, post_id) DO UPDATE
                   SET collected=excluded.collected, record=excluded.record
                   WHERE excluded.collected >= evidence.collected""",
                (
                    (
                        item.platform,
                        keyword_key(item.keyword),
                        item.post_id,
                        item.collected_at.timestamp(),
                        item.model_dump_json(),
                    )
                    for item in items
                ),
            )

    async def import_batch(request: ImportRequest, job: JobStatus, stop: Event) -> None:
        started = monotonic()
        items: list[SocialEvidenceRecord] = []
        try:
            async with asyncio.timeout(15):
                staged, rejected = await asyncio.to_thread(
                    load_export, root / "inbox" / str(request.batch_id), request, stop
                )
            job.rejected_rows = rejected
            if stop.is_set():
                raise ImportStopped()
            if rejected and not staged:
                job.status, job.error_code = "failed", "INVALID_ROWS"
            else:
                items = staged
                job.status = (
                    "partial"
                    if rejected or any(item.collection_status == "partial" for item in items)
                    else "completed"
                )
                job.error_code = "INVALID_ROWS" if rejected else None
        except ImportStopped:
            job.status = "cancelled"
        except TimeoutError:
            stop.set()
            job.status, job.error_code = "failed", "IMPORT_TIMEOUT"
        except (OSError, ValueError):
            job.status, job.error_code = "failed", "IMPORT_FILE"
        job.post_count = len(items)
        job.comment_count = sum(len(item.comments) for item in items)
        job.duration_ms = round((monotonic() - started) * 1000)
        store_job(request, job, items)

    @asynccontextmanager
    async def lifecycle(_: FastAPI) -> AsyncIterator[None]:
        root.mkdir(parents=True, exist_ok=True)
        with closing(connect()) as db, db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs(
                  batch_id TEXT PRIMARY KEY, request TEXT NOT NULL, job TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence(
                  platform TEXT, keyword TEXT, post_id TEXT, collected REAL, record TEXT,
                  PRIMARY KEY(platform, keyword, post_id)
                );
                """
            )
        with closing(connect()) as db:
            interrupted = db.execute(
                "SELECT request, job FROM jobs WHERE json_extract(job, '$.status')='running'"
            ).fetchall()
        for raw, status in interrupted:
            request = ImportRequest.model_validate_json(raw)
            job = JobStatus.model_validate_json(status)
            job.status, job.error_code = "failed", "INTERRUPTED"
            store_job(request, job)
        try:
            yield
        finally:
            if active:
                active[1].set()
                await active[2]

    async def authorize(authorization: Annotated[str, Header()] = "") -> None:
        if not compare_digest(authorization.encode(), expected):
            raise HTTPException(401, "local social service authentication required")

    app = FastAPI(
        lifespan=lifecycle,
        dependencies=[Depends(authorize)],
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1"])

    @app.get("/v1/status")
    async def service_status() -> dict:
        return {"status": "ready", "mode": "local_export", "import_running": running()}

    def running() -> bool:
        return active is not None and not active[2].done()

    @app.post("/v1/jobs", status_code=202)
    async def start(request: ImportRequest, response: Response) -> JobStatus:
        nonlocal active
        response.headers["Location"] = f"/v1/jobs/{request.batch_id}"
        prior = get_job(request.batch_id)
        if prior:
            if prior[0] != request:
                raise HTTPException(409, "batch ID already used with different metadata")
            response.status_code = 200
            return prior[1]
        if running():
            raise HTTPException(409, "another import is running")
        job = JobStatus(
            batch_id=request.batch_id,
            platform=request.platform,
            status="running",
            started_at=datetime.now(UTC),
        )
        store_job(request, job)
        stop = Event()
        active = request.batch_id, stop, asyncio.create_task(import_batch(request, job, stop))
        return job

    @app.get("/v1/jobs/{batch_id}")
    async def status(batch_id: UUID) -> JobStatus:
        saved = get_job(batch_id)
        if saved is None:
            raise HTTPException(404, "unknown import job")
        return saved[1]

    @app.delete("/v1/jobs/{batch_id}")
    async def stop_job(batch_id: UUID) -> JobStatus:
        if active and active[0] == batch_id and running():
            active[1].set()
            await active[2]
        return await status(batch_id)

    @app.get("/v1/evidence", response_model=SocialPage)
    async def evidence(
        platform: Platform,
        keyword: Annotated[list[Keyword], Query(min_length=1, max_length=2)],
        limit: Annotated[int, Query(ge=1, le=MAX_POSTS)] = MAX_POSTS,
        offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
        format: Literal["json", "jsonl"] = "json",
        destination: Keyword | None = None,
        comments_only: bool = False,
    ):
        with closing(connect()) as db:
            total, records = evidence_page(
                db, platform, keyword, destination=destination, limit=limit, offset=offset,
                comments_only=comments_only,
            )
        if format == "jsonl":
            return Response(
                "".join(f"{line}\n" for line in records), media_type="application/x-ndjson"
            )
        return SocialPage(
            total=total,
            items=[SocialEvidenceRecord.model_validate_json(line) for line in records],
        )

    return app


def create_app_from_environment() -> FastAPI:
    return create_social_app(
        Path(os.environ["SOCIAL_DATA_PATH"]), os.environ["SOCIAL_RESEARCH_TOKEN"]
    )
