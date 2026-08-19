import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..cache import ping_redis
from ..db import ping_db

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness() -> JSONResponse:
    try:
        database_ok, redis_ok = await asyncio.gather(ping_db(), ping_redis())
    except Exception as exc:  # noqa: BLE001 - readiness must fail closed
        logger.warning("Readiness check failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "database": False, "redis": False},
        )
    return JSONResponse(
        status_code=200,
        content={"status": "ready", "database": database_ok, "redis": redis_ok},
    )
