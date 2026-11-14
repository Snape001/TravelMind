import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import TypeVar

from httpx import HTTPStatusError, RequestError
from langgraph.config import get_stream_writer
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import ValidationError

from travelmind.schemas.agents import AgentFailure, AgentInput, ErrorInfo, RunBudget
from travelmind.schemas.events import AgentFinishedEvent, AgentStartedEvent, EventPayload

logger = logging.getLogger(__name__)
Result = TypeVar("Result")


class InvalidOutput(ValueError):
    pass


class RunError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        self.info = ErrorInfo(code=code, message=message, retryable=retryable)
        super().__init__(code)


def classify_error(exc: Exception) -> RunError:
    if isinstance(exc, (TimeoutError, APITimeoutError)):
        return RunError("PROVIDER_TIMEOUT", "外部服务调用超时", retryable=True)
    if isinstance(exc, (APIStatusError, HTTPStatusError)):
        status = exc.response.status_code
        if status == 429:
            return RunError("PROVIDER_RATE_LIMIT", "外部服务请求过于频繁", retryable=True)
        if status in (408, 409) or 500 <= status <= 599:
            return RunError("PROVIDER_UNAVAILABLE", "外部服务暂不可用", retryable=True)
        return RunError("PROVIDER_REJECTED", "外部服务拒绝请求")
    if isinstance(exc, (APIConnectionError, RequestError, OSError)):
        return RunError("PROVIDER_UNAVAILABLE", "外部服务连接失败", retryable=True)
    if isinstance(exc, (ValidationError, InvalidOutput)):
        return RunError("INVALID_AGENT_OUTPUT", "Agent 输出未通过校验", retryable=True)
    return RunError("AGENT_EXECUTION_FAILED", "Agent 执行失败")


def emit(payload: EventPayload) -> None:
    get_stream_writer()(payload)


async def with_budget(operation: Callable[[], Awaitable[Result]], budget: RunBudget) -> Result:
    for attempt in range(1, budget.max_attempts + 1):
        try:
            async with asyncio.timeout(budget.timeout_seconds):
                return await operation()
        except RunError:
            raise
        except Exception as exc:
            error = classify_error(exc)
            if not error.info.retryable or attempt == budget.max_attempts:
                raise error from exc
            await asyncio.sleep(0.25 * 2 ** (attempt - 1))
    raise AssertionError("RunBudget guarantees at least one attempt")


async def run_agent(
    request: AgentInput, operation: Callable[[], Awaitable[Result]]
) -> Result | AgentFailure:
    agent = {"agent_id": request.agent_id, "run_id": request.run_id}
    emit(
        AgentStartedEvent(
            type="agent.started",
            **agent,
            assignment_id=request.assignment.assignment_id if request.assignment else None,
        )
    )
    started = perf_counter()
    error = None
    try:
        result = await operation()
    except RunError as exc:
        error = exc.info
        result = AgentFailure(kind="failed", **agent, error=error)
    status = "failed" if error else "completed"
    logger.info(
        "agent.finished task_id=%s trace_id=%s run_id=%s agent_id=%s status=%s "
        "error_code=%s duration_ms=%d",
        request.task_id,
        request.trace_id,
        request.run_id,
        request.agent_id,
        status,
        error.code if error else None,
        (perf_counter() - started) * 1000,
    )
    emit(AgentFinishedEvent(type="agent.finished", **agent, status=status, error=error))
    return result


def result_identity(request: AgentInput) -> dict:
    return {"agent_id": request.agent_id, "run_id": request.run_id}
