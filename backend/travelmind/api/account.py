import base64
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from travelmind.api.dependencies import Sessions, SignedIn, api_error
from travelmind.db.account import published_reports
from travelmind.schemas.api import AccountReports, ErrorResponse, ReportSummary

router = APIRouter(prefix="/api/v1/account", tags=["account"])


def encode_cursor(report: ReportSummary) -> str:
    value = f"{report.published_at.astimezone(UTC).isoformat()}|{report.report_id}"
    return base64.urlsafe_b64encode(value.encode("ascii")).decode("ascii").rstrip("=")


def decode_cursor(value: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        published, report_id = raw.decode("ascii").split("|")
        date = datetime.fromisoformat(published)
        identity = UUID(report_id)
        if date.tzinfo is not None and date.utcoffset() == UTC.utcoffset(date):
            return date, identity
    except ValueError:
        pass
    raise api_error(422, "VALIDATION_ERROR", "报告分页参数无效")


@router.get(
    "/reports",
    responses={code: {"model": ErrorResponse} for code in (401, 422, 500, 503)},
)
async def account_reports(
    user: SignedIn,
    sessions: Sessions,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
) -> AccountReports:
    before = decode_cursor(cursor) if cursor is not None else None
    async with sessions() as session:
        items = await published_reports(session, user.user_id, limit=limit + 1, before=before)
    return AccountReports(
        items=items[:limit],
        next_cursor=encode_cursor(items[limit - 1]) if len(items) > limit else None,
    )
