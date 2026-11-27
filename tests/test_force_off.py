"""Acceptance criterion 1: ASST_FORCE_OFF=true with a dead database still answers 503.

The database is not merely mocked away: the engine points at a closed port, so
any attempt to use it would raise a connection error. The assertion is on the
counters - zero connects, zero statements, zero sessions, zero Redis calls -
which proves the strong branch short-circuits before any storage access.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.cache import CacheStats, InMemorySwitchCache
from app.config import Settings
from app.db import Database, DatabaseStats
from app.main import create_app

DEAD_URL = "postgresql+psycopg://nobody:nobody@127.0.0.1:59999/no_such_database"


@pytest.fixture()
def dead_db() -> Iterator[Database]:
    settings = Settings(database_url=DEAD_URL, redis_url="", cache_ttl_seconds=5.0)
    database = Database(settings, DatabaseStats())
    yield database
    database.dispose()


def test_force_off_survives_a_dead_database(dead_db: Database) -> None:
    stats = dead_db.stats
    cache_stats = CacheStats()
    cache = InMemorySwitchCache("asst", cache_stats, ttl_seconds=5.0)
    settings = Settings(database_url=DEAD_URL, redis_url="", cache_ttl_seconds=5.0)
    app = create_app(settings, database=dead_db, cache=cache, environ={"ASST_FORCE_OFF": "true"})

    with TestClient(app) as client:
        response = client.post(
            "/api/assistant/chat",
            json={"prompt": "本周会员企业有哪些新增线索？"},
            headers={"Authorization": "Bearer tok-staff-hangzhou"},
        )

    assert response.status_code == 503
    assert response.json()["code"] == "ASSISTANT_DISABLED"
    assert response.json()["source"] == "force_off"
    assert stats.snapshot() == {"connects": 0, "statements": 0, "sessions": 0}
    assert cache_stats.total() == 0


def test_force_off_status_is_still_readable(dead_db: Database) -> None:
    settings = Settings(database_url=DEAD_URL, redis_url="", cache_ttl_seconds=5.0)
    cache = InMemorySwitchCache("asst", CacheStats(), ttl_seconds=5.0)
    app = create_app(settings, database=dead_db, cache=cache, environ={"ASST_FORCE_OFF": "true"})

    with TestClient(app) as client:
        response = client.get(
            "/api/assistant/status",
            headers={"Authorization": "Bearer tok-staff-hangzhou"},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["enabled"] is False
    assert body["source"] == "force_off"
    assert body["message"]
    assert dead_db.stats.total() == 0


def test_force_off_false_is_not_the_brake(dead_db: Database) -> None:
    """The literal string "false" must not disable anything (parsing edge case)."""

    settings = Settings(database_url=DEAD_URL, redis_url="", cache_ttl_seconds=5.0)
    cache = InMemorySwitchCache("asst", CacheStats(), ttl_seconds=5.0)
    app = create_app(settings, database=dead_db, cache=cache, environ={"ASST_FORCE_OFF": "false"})

    with TestClient(app) as client:
        response = client.get(
            "/api/assistant/status",
            headers={"Authorization": "Bearer tok-staff-hangzhou"},
        )

    body = response.json()
    assert response.status_code == 200
    # The brake is off, but the switch state cannot be read: the service must
    # fail closed rather than pretend the assistant is available.
    assert body["enabled"] is False
    assert body["source"] == "state_unavailable"
