"""Safe classification for agent failures without logging exception messages."""

import asyncio
import socket
from dataclasses import dataclass
from typing import Literal

import httpx2
from pydantic import ValidationError

AgentFailureCategory = Literal["timeout", "connection", "http", "validation", "numbers_guard", "other"]
_MIN_HTTP_STATUS_CODE = 100
_MAX_HTTP_STATUS_CODE = 599


class AgentNumbersGuardError(ValueError):
    """The model response contained a number absent from the approved evidence."""


@dataclass(frozen=True)
class AgentFailureDiagnostic:
    """Exception metadata safe to include in operational logs."""

    category: AgentFailureCategory
    root_exception: str
    http_status: int | None


def diagnose_agent_failure(error: BaseException) -> AgentFailureDiagnostic:
    """Extract only fixed categories, exception class names, and HTTP status."""
    exception_chain = _exception_chain(error)
    http_status = _http_status(exception_chain)
    category = _failure_category(error, exception_chain, http_status)
    return AgentFailureDiagnostic(
        category=category,
        root_exception=type(exception_chain[-1]).__name__,
        http_status=http_status,
    )


def _exception_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _http_status(exception_chain: list[BaseException]) -> int | None:
    for error in exception_chain:
        status_code = getattr(error, "status_code", None)
        if (
            isinstance(status_code, int)
            and not isinstance(status_code, bool)
            and _MIN_HTTP_STATUS_CODE <= status_code <= _MAX_HTTP_STATUS_CODE
        ):
            return status_code
        response = getattr(error, "response", None)
        response_status = getattr(response, "status_code", None)
        if (
            isinstance(response_status, int)
            and not isinstance(response_status, bool)
            and _MIN_HTTP_STATUS_CODE <= response_status <= _MAX_HTTP_STATUS_CODE
        ):
            return response_status
    return None


def _failure_category(
    error: BaseException,
    exception_chain: list[BaseException],
    http_status: int | None,
) -> AgentFailureCategory:
    exception_names = {type(item).__name__ for item in exception_chain}
    if isinstance(error, AgentNumbersGuardError):
        return "numbers_guard"
    if (
        any(isinstance(item, (TimeoutError, asyncio.TimeoutError, httpx2.TimeoutException)) for item in exception_chain)
        or "APITimeoutError" in exception_names
    ):
        return "timeout"
    if http_status is not None or exception_names.intersection({"ModelHTTPError", "APIStatusError", "HTTPStatusError"}):
        return "http"
    if (
        any(isinstance(item, (socket.gaierror, ConnectionError, httpx2.TransportError)) for item in exception_chain)
        or "APIConnectionError" in exception_names
    ):
        return "connection"
    if any(isinstance(item, (ValidationError, ValueError, TypeError)) for item in exception_chain):
        return "validation"
    return "other"
