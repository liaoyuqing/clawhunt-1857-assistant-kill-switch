"""Fixtures: a disposable SQLite database, an in-process cache, a TestClient."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.cache import CacheStats, InMemorySwitchCache
from app.config import Settings
from app.db import Database, DatabaseStats
from app.main import create_app


@pytest.fixture()
def environ() -> dict[str, str]:
    """Process-like environment used by the resolver; ASST_FORCE_OFF is absent."""

    return {}


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    db_path = tmp_path / "asst.sqlite"
    return Settings(
        database_url="sqlite+pysqlite:///" + str(db_path).replace(os.sep, "/"),
        redis_url="",
        cache_ttl_seconds=5.0,
        sse_frame_interval_seconds=0.02,
        sse_max_frames=40,
    )


@pytest.fixture()
def db_stats() -> DatabaseStats:
    return DatabaseStats()


@pytest.fixture()
def db(settings: Settings, db_stats: DatabaseStats) -> Iterator[Database]:
    database = Database(settings, db_stats)
    database.create_all()
    yield database
    database.dispose()


@pytest.fixture()
def cache_stats() -> CacheStats:
    return CacheStats()


@pytest.fixture()
def cache(cache_stats: CacheStats) -> InMemorySwitchCache:
    return InMemorySwitchCache("asst", cache_stats, ttl_seconds=5.0)


@pytest.fixture()
def shared_redis() -> fakeredis.FakeStrictRedis:
    """One in-process Redis stand-in shared by both modules in the isolation test."""

    return fakeredis.FakeStrictRedis(decode_responses=True)


@pytest.fixture()
def app(
    settings: Settings, db: Database, cache: InMemorySwitchCache, environ: dict[str, str]
) -> FastAPI:
    return create_app(settings, database=db, cache=cache, environ=environ)


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer tok-admin-zhou"}


@pytest.fixture()
def hangzhou_headers() -> dict[str, str]:
    return {"Authorization": "Bearer tok-staff-hangzhou"}


@pytest.fixture()
def suzhou_headers() -> dict[str, str]:
    return {"Authorization": "Bearer tok-staff-suzhou"}


@pytest.fixture()
def manager_headers() -> dict[str, str]:
    return {"Authorization": "Bearer tok-manager-lin"}
