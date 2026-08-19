import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError

from ..agent import AgentGuardrailError, AgentTimeoutError, stream_agent_response
from ..auth import get_current_user
from ..cache import get_redis
from ..config import get_settings
from ..metrics import CACHE_ERRORS, CACHE_HITS, CACHE_MISSES, CHAT_LATENCY, CHAT_REQUESTS
from ..models import User
from ..rate_limit import enforce_rate_limit
from ..schemas import ChatRequest

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])
logger = logging.getLogger(__name__)


def _cache_key(user_id: str, prompt: str) -> str:
    settings = get_settings()
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    model = hashlib.sha256(settings.openai_model.encode("utf-8")).hexdigest()[:12]
    return f"ai-platform:response:{settings.cache_key_version}:{model}:{user_id}:{prompt_hash}"


def _sse(event: str, payload: object) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


async def _stream_chat(prompt: str, user_id: str) -> AsyncIterator[str]:
    started = time.perf_counter()
    cache = get_redis()
    key = _cache_key(user_id, prompt)
    try:
        cached = await cache.get(key)
    except RedisError:
        CACHE_ERRORS.labels(operation="get").inc()
        logger.exception("Response cache read failed; continuing without cache")
        cached = None
    if cached:
        CACHE_HITS.inc()
        CHAT_REQUESTS.labels(status="cache_hit").inc()
        yield _sse("delta", cached)
        yield _sse("done", {"cached": True})
        CHAT_LATENCY.observe(time.perf_counter() - started)
        return

    CACHE_MISSES.inc()
    chunks: list[str] = []
    try:
        async for stream_event in stream_agent_response(prompt):
            if stream_event.kind == "delta":
                assert isinstance(stream_event.payload, str)
                chunks.append(stream_event.payload)
            yield _sse(stream_event.kind, stream_event.payload)
        response_text = "".join(chunks)
        try:
            if response_text:
                await cache.set(key, response_text, ex=get_settings().cache_ttl_seconds)
        except RedisError:
            CACHE_ERRORS.labels(operation="set").inc()
            logger.exception("Response cache write failed; returning uncached response")
        CHAT_REQUESTS.labels(status="success").inc()
        yield _sse("done", {"cached": False})
    except AgentGuardrailError as exc:
        CHAT_REQUESTS.labels(status="guardrail").inc()
        yield _sse(
            "error",
            {"message": exc.message, "guardrail": exc.guardrail},
        )
    except AgentTimeoutError:
        CHAT_REQUESTS.labels(status="timeout").inc()
        yield _sse(
            "error",
            {"message": AgentTimeoutError.message, "code": "agent_timeout"},
        )
    except Exception as exc:  # noqa: BLE001 - do not expose provider errors to clients
        logger.exception("AI stream failed: %s", exc)
        CHAT_REQUESTS.labels(status="error").inc()
        yield _sse("error", {"message": "The AI request could not be completed."})
    finally:
        CHAT_LATENCY.observe(time.perf_counter() - started)


@router.post("/stream")
async def stream_chat(
    payload: ChatRequest,
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    await enforce_rate_limit("chat-user", current_user.id)
    return StreamingResponse(
        _stream_chat(payload.prompt, current_user.id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
