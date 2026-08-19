import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from redis.exceptions import RedisError

from platform_app import agent as agent_module
from platform_app import rate_limit
from platform_app.agent import AgentStreamEvent, AgentTimeoutError
from platform_app.routes import chat as chat_route


class FakeRedis:
    def __init__(self, value: str | None = None, fail_get: bool = False) -> None:
        self.value = value
        self.fail_get = fail_get
        self.set_values: list[tuple[str, str, int]] = []

    async def get(self, key: str) -> str | None:
        if self.fail_get:
            raise RedisError("redis unavailable")
        return self.value

    async def set(self, key: str, value: str, ex: int) -> None:
        self.set_values.append((key, value, ex))


async def _collect(stream) -> list[str]:
    return [chunk async for chunk in stream]


def test_rate_limiter_rejects_after_distributed_window_limit(monkeypatch) -> None:
    class LimiterRedis:
        def __init__(self) -> None:
            self.count = 0

        async def eval(self, script, key_count, key, window) -> int:
            del script, key_count, key, window
            self.count += 1
            return self.count

    redis = LimiterRedis()
    monkeypatch.setattr(rate_limit, "get_redis", lambda: redis)
    monkeypatch.setattr(
        rate_limit,
        "get_settings",
        lambda: SimpleNamespace(rate_limit_requests=2, rate_limit_window_seconds=60),
    )

    asyncio.run(rate_limit.enforce_rate_limit("chat-user", "user-1"))
    asyncio.run(rate_limit.enforce_rate_limit("chat-user", "user-1"))
    with pytest.raises(HTTPException) as error:
        asyncio.run(rate_limit.enforce_rate_limit("chat-user", "user-1"))

    assert error.value.status_code == 429
    assert error.value.headers["Retry-After"] == "60"


def test_rate_limiter_fails_open_when_redis_is_unavailable(monkeypatch) -> None:
    class BrokenRedis:
        async def eval(self, *args) -> int:
            del args
            raise RedisError("redis unavailable")

    monkeypatch.setattr(rate_limit, "get_redis", lambda: BrokenRedis())
    monkeypatch.setattr(
        rate_limit,
        "get_settings",
        lambda: SimpleNamespace(rate_limit_requests=2, rate_limit_window_seconds=60),
    )

    asyncio.run(rate_limit.enforce_rate_limit("chat-user", "user-1"))


def test_cache_key_contains_workflow_and_model_version() -> None:
    key = chat_route._cache_key("user-1", "check cluster health")
    settings = chat_route.get_settings()

    assert settings.cache_key_version in key
    assert settings.openai_model not in key
    assert key.count(":") == 5


def test_cache_hit_replays_exact_text(monkeypatch) -> None:
    cached_text = "first line\n\nsecond  line"
    cache = FakeRedis(value=cached_text)
    monkeypatch.setattr(chat_route, "get_redis", lambda: cache)

    events = asyncio.run(_collect(chat_route._stream_chat("prompt", "user-1")))
    data_lines = [
        line
        for event in events
        for line in event.splitlines()
        if line.startswith("data: ")
    ]

    assert cached_text == json.loads(data_lines[0][6:])
    assert '"cached": true' in events[-1]


def test_cache_read_failure_does_not_fail_ai_response(monkeypatch) -> None:
    cache = FakeRedis(fail_get=True)
    monkeypatch.setattr(chat_route, "get_redis", lambda: cache)

    async def fake_agent_stream(prompt: str):
        del prompt
        yield AgentStreamEvent("delta", "fresh response")

    monkeypatch.setattr(chat_route, "stream_agent_response", fake_agent_stream)
    events = asyncio.run(_collect(chat_route._stream_chat("prompt", "user-1")))

    assert any('"fresh response"' in event for event in events)
    assert '"cached": false' in events[-1]


def test_agent_stream_has_explicit_timeout(monkeypatch) -> None:
    class SlowStream:
        async def stream_events(self):
            await asyncio.sleep(0.05)
            if False:
                yield None

    monkeypatch.setattr(
        agent_module,
        "get_settings",
        lambda: SimpleNamespace(
            agent_max_turns=3,
            agent_timeout_seconds=0.001,
            openai_model="test-model",
        ),
    )
    monkeypatch.setattr(
        agent_module.Runner,
        "run_streamed",
        lambda *args, **kwargs: SlowStream(),
    )

    async def run() -> None:
        with pytest.raises(AgentTimeoutError):
            await _collect(agent_module.stream_agent_response("check cluster health"))

    asyncio.run(run())
