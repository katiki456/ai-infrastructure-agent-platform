import hashlib
import logging

from fastapi import HTTPException, status
from redis.exceptions import RedisError

from .cache import get_redis
from .config import get_settings
from .metrics import RATE_LIMIT_EXCEEDED

logger = logging.getLogger(__name__)

_FIXED_WINDOW_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


def _rate_limit_key(scope: str, subject: str) -> str:
    subject_hash = hashlib.sha256(subject.encode("utf-8")).hexdigest()
    return f"ai-platform:rate-limit:v1:{scope}:{subject_hash}"


async def enforce_rate_limit(scope: str, subject: str) -> None:
    settings = get_settings()
    key = _rate_limit_key(scope, subject)
    try:
        count = int(
            await get_redis().eval(
                _FIXED_WINDOW_SCRIPT,
                1,
                key,
                settings.rate_limit_window_seconds,
            )
        )
    except RedisError:
        # Availability of the AI API should not depend on Redis, but the failure is
        # visible through logs and metrics so the limiter can be repaired promptly.
        logger.exception("Rate limiter unavailable; allowing request scope=%s", scope)
        return

    if count > settings.rate_limit_requests:
        RATE_LIMIT_EXCEEDED.labels(scope=scope).inc()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded; retry later.",
            headers={"Retry-After": str(settings.rate_limit_window_seconds)},
        )
