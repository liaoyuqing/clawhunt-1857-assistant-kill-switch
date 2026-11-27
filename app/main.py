"""Application factory: wires settings, database, cache, identity and routes."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.asst.routes import router as asst_router
from app.asst.switch import AssistantSwitchService
from app.cache import (
    CacheStats,
    InMemorySwitchCache,
    RedisSwitchCache,
    SwitchCache,
)
from app.config import Settings, load_settings
from app.console_routes import router as console_router
from app.db import Database
from app.deps import Container
from app.errors import ApiError
from app.iam import default_directory
from app.notify import NotificationOutbox
from app.recruit.routes import router as recruit_router

logger = logging.getLogger(__name__)


def build_cache(settings: Settings, stats: CacheStats | None = None) -> SwitchCache:
    """Redis when a URL is configured, otherwise the in-process stand-in.

    The stand-in is what makes the suite runnable with no Redis server; the
    requirement's production wiring is the Redis branch, exercised separately
    with fakeredis so the same code path is covered in tests.
    """

    stats = stats or CacheStats()
    if settings.redis_url.strip():
        import redis

        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        return RedisSwitchCache(client, "asst", stats, settings.cache_ttl_seconds)
    return InMemorySwitchCache("asst", stats, settings.cache_ttl_seconds)


def create_app(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    cache: SwitchCache | None = None,
    environ: Mapping[str, str] | None = None,
) -> FastAPI:
    settings = settings or load_settings(environ)
    db = database or Database(settings)
    cache = cache or build_cache(settings)
    container = Container(
        settings=settings,
        db=db,
        cache=cache,
        iam=default_directory(),
        outbox=NotificationOutbox(),
    )
    container.assistant = AssistantSwitchService(db, cache, settings, container.outbox)
    if environ is not None:
        container.assistant.resolver.environ = environ

    app = FastAPI(title="会员空间平台 · 员工 AI 助手关停开关", version="1.0.0")
    app.state.container = container

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.payload())

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        return {
            "status": "ok",
            "cache_backend": container.cache.backend,
            "cache_ttl_seconds": settings.cache_ttl_seconds,
        }

    app.include_router(asst_router)
    app.include_router(recruit_router)
    app.include_router(console_router)
    return app


app = create_app()
