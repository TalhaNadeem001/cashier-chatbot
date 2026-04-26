"""Per-session debounce buffer using Redis.

Incoming messages for a session are held in a Redis list for up to
_BUFFER_TIMER_TTL_MS milliseconds after the last arrival.  Once the timer
expires one coroutine (the "processor") flushes the list, concatenates all
messages, calls the orchestrator once, and publishes the result.  Every other
concurrent request for the same session (a "waiter") polls the result key and
returns the same response.
"""

import asyncio
import json

from src.cache import cache_delete, cache_get, cache_list_clear, cache_set, cache_set_nx, cache_set_pex
from src.chatbot.constants import (
    _BUFFER_LOCK_TTL_SECONDS,
    _BUFFER_MAX_WAIT_SECONDS,
    _BUFFER_POLL_INTERVAL_SECONDS,
    _BUFFER_POLL_TIMEOUT_SECONDS,
    _BUFFER_RESULT_TTL_SECONDS,
    _BUFFER_TIMER_TTL_MS,
)
from src.chatbot.orchestrator import Orchestrator
from src.chatbot.schema import ChatbotV2MessageRequest, ChatbotV2MessageResponse
from src.chatbot.utils import (
    _buffer_lock_redis_key,
    _buffer_messages_redis_key,
    _buffer_result_redis_key,
    _buffer_timer_redis_key,
)


async def _push_to_buffer(session_id: str, user_message: str) -> None:
    from src.cache import redis as _redis
    await _redis.rpush(_buffer_messages_redis_key(session_id), user_message)


async def _refresh_timer(session_id: str) -> None:
    await cache_set_pex(_buffer_timer_redis_key(session_id), "1", _BUFFER_TIMER_TTL_MS)


async def _wait_for_silence(session_id: str) -> None:
    """Poll until the debounce timer key disappears (max _BUFFER_MAX_WAIT_SECONDS)."""
    timer_key = _buffer_timer_redis_key(session_id)
    elapsed = 0.0
    while elapsed < _BUFFER_MAX_WAIT_SECONDS:
        await asyncio.sleep(_BUFFER_POLL_INTERVAL_SECONDS)
        elapsed += _BUFFER_POLL_INTERVAL_SECONDS
        val = await cache_get(timer_key)
        if val is None:
            print(f"[buffer] silence detected for session={session_id!r} after {elapsed:.1f}s")
            return
    print(f"[buffer] silence timeout reached for session={session_id!r}")


async def _flush_buffer(session_id: str) -> list[str]:
    """Atomically LRANGE + DEL the message buffer, returning all messages."""
    messages = await cache_list_clear(_buffer_messages_redis_key(session_id))
    print(f"[buffer] flushed {len(messages)} message(s) for session={session_id!r}")
    return messages


async def _run_as_processor(request: ChatbotV2MessageRequest) -> ChatbotV2MessageResponse:
    """Wait for silence, flush buffer, call orchestrator, publish result."""
    session_id = request.session_id
    lock_key = _buffer_lock_redis_key(session_id)
    result_key = _buffer_result_redis_key(session_id)
    try:
        await _wait_for_silence(session_id)
        messages = await _flush_buffer(session_id)

        if messages:
            combined = " \n".join(messages)
        else:
            combined = request.user_message

        merged_request = ChatbotV2MessageRequest(
            user_message=combined,
            session_id=session_id,
            merchant_id=request.merchant_id,
            phone_number=request.phone_number,
        )

        print(f"[buffer] processor calling orchestrator for session={session_id!r}")
        orchestrator = Orchestrator()
        response = await orchestrator.handle_message(merged_request)

        payload = json.dumps({"system_response": response.system_response, "session_id": response.session_id})
        await cache_set(result_key, payload, ttl=_BUFFER_RESULT_TTL_SECONDS)
        print(f"[buffer] result published for session={session_id!r}")
        return response
    finally:
        await cache_delete(lock_key)
        print(f"[buffer] lock released for session={session_id!r}")


async def _run_as_waiter(session_id: str) -> ChatbotV2MessageResponse:
    """Poll the result key until the processor publishes it (max _BUFFER_POLL_TIMEOUT_SECONDS)."""
    result_key = _buffer_result_redis_key(session_id)
    elapsed = 0.0
    while elapsed < _BUFFER_POLL_TIMEOUT_SECONDS:
        await asyncio.sleep(_BUFFER_POLL_INTERVAL_SECONDS)
        elapsed += _BUFFER_POLL_INTERVAL_SECONDS
        raw = await cache_get(result_key)
        if raw is not None:
            print(f"[buffer] waiter got result for session={session_id!r} after {elapsed:.1f}s")
            data = json.loads(raw)
            return ChatbotV2MessageResponse(
                system_response=data["system_response"],
                session_id=data["session_id"],
            )
    print(f"[buffer] waiter timed out for session={session_id!r}")
    raise TimeoutError(f"Buffer result not available after {_BUFFER_POLL_TIMEOUT_SECONDS}s for session {session_id!r}")


async def handle_with_buffer(request: ChatbotV2MessageRequest) -> ChatbotV2MessageResponse:
    """Entry point: buffer the incoming message then either process or wait.

    1. Push the message to the session buffer list.
    2. Reset the debounce timer (1.5 s PX TTL).
    3. Try to acquire the distributed lock (SET NX).
       - Acquired  → become the processor: wait for silence, flush, call orchestrator, publish result.
       - Not acquired → become a waiter: poll the result key until the processor publishes it.
    """
    session_id = request.session_id
    lock_key = _buffer_lock_redis_key(session_id)

    await _push_to_buffer(session_id, request.user_message)
    await _refresh_timer(session_id)

    acquired = await cache_set_nx(lock_key, "1", _BUFFER_LOCK_TTL_SECONDS)
    print(f"[buffer] session={session_id!r} lock_acquired={acquired}")

    if acquired:
        return await _run_as_processor(request)
    else:
        return await _run_as_waiter(session_id)
