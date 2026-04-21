import asyncio
from unittest.mock import AsyncMock, patch

from src.chatbot.exceptions import AIServiceError
from src.chatbot.orchestrator import (
    _GEMINI_429_MAX_ATTEMPTS,
    _GEMINI_503_MAX_ATTEMPTS,
    _gemini_service_call_with_retries,
    _is_gemini_http_429,
    _is_gemini_http_503,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Detection helpers — _is_gemini_http_503
# ---------------------------------------------------------------------------


def test_is_gemini_http_503_direct_code():
    exc = AIServiceError("service unavailable")
    exc.code = 503  # type: ignore[attr-defined]
    assert _is_gemini_http_503(exc) is True


def test_is_gemini_http_503_via_cause():
    cause = Exception("raw http error")
    cause.status_code = 503  # type: ignore[attr-defined]
    exc = AIServiceError("wrapped")
    exc.__cause__ = cause
    assert _is_gemini_http_503(exc) is True


def test_is_gemini_http_503_false_for_429():
    exc = AIServiceError("rate limited")
    exc.code = 429  # type: ignore[attr-defined]
    assert _is_gemini_http_503(exc) is False


# ---------------------------------------------------------------------------
# Detection helpers — _is_gemini_http_429
# ---------------------------------------------------------------------------


def test_is_gemini_http_429_direct_code():
    exc = AIServiceError("rate limited")
    exc.code = 429  # type: ignore[attr-defined]
    assert _is_gemini_http_429(exc) is True


def test_is_gemini_http_429_via_cause():
    cause = Exception("raw http error")
    cause.status_code = 429  # type: ignore[attr-defined]
    exc = AIServiceError("wrapped")
    exc.__cause__ = cause
    assert _is_gemini_http_429(exc) is True


def test_is_gemini_http_429_false_for_503():
    exc = AIServiceError("service unavailable")
    exc.code = 503  # type: ignore[attr-defined]
    assert _is_gemini_http_429(exc) is False


# ---------------------------------------------------------------------------
# Retry loop — successful retries
# ---------------------------------------------------------------------------


def _make_503_error() -> AIServiceError:
    exc = AIServiceError("service unavailable")
    exc.code = 503  # type: ignore[attr-defined]
    return exc


def _make_429_error() -> AIServiceError:
    exc = AIServiceError("rate limited")
    exc.code = 429  # type: ignore[attr-defined]
    return exc


def test_retry_succeeds_after_503():
    call_count = 0

    async def _flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise _make_503_error()
        return "ok"

    async def _test():
        with patch("asyncio.sleep", new_callable=AsyncMock):
            return await _gemini_service_call_with_retries(
                log_label="[Test]",
                extra_fields="",
                call=_flaky,
            )

    result = _run(_test())
    assert result == "ok"
    assert call_count == 3


def test_retry_succeeds_after_429():
    call_count = 0

    async def _flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise _make_429_error()
        return "ok"

    async def _test():
        with patch("asyncio.sleep", new_callable=AsyncMock):
            return await _gemini_service_call_with_retries(
                log_label="[Test]",
                extra_fields="",
                call=_flaky,
            )

    result = _run(_test())
    assert result == "ok"
    assert call_count == 3


# ---------------------------------------------------------------------------
# Retry loop — exhausted retries
# ---------------------------------------------------------------------------


def test_retry_raises_after_max_503_attempts():
    call_count = 0

    async def _always_503():
        nonlocal call_count
        call_count += 1
        raise _make_503_error()

    async def _test():
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await _gemini_service_call_with_retries(
                log_label="[Test]",
                extra_fields="",
                call=_always_503,
            )

    try:
        _run(_test())
        assert False, "expected AIServiceError"
    except AIServiceError:
        pass
    assert call_count == _GEMINI_503_MAX_ATTEMPTS


def test_retry_raises_after_max_429_attempts():
    call_count = 0

    async def _always_429():
        nonlocal call_count
        call_count += 1
        raise _make_429_error()

    async def _test():
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await _gemini_service_call_with_retries(
                log_label="[Test]",
                extra_fields="",
                call=_always_429,
            )

    try:
        _run(_test())
        assert False, "expected AIServiceError"
    except AIServiceError:
        pass
    assert call_count == _GEMINI_429_MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# Retry loop — non-retryable error raises immediately
# ---------------------------------------------------------------------------


def test_retry_raises_immediately_on_non_retryable_error():
    call_count = 0

    async def _bad_call():
        nonlocal call_count
        call_count += 1
        exc = AIServiceError("unknown error")
        exc.code = 500  # type: ignore[attr-defined]
        raise exc

    async def _test():
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await _gemini_service_call_with_retries(
                log_label="[Test]",
                extra_fields="",
                call=_bad_call,
            )

    try:
        _run(_test())
        assert False, "expected AIServiceError"
    except AIServiceError:
        pass
    assert call_count == 1
