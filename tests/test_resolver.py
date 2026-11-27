"""Resolver tests: environment first, then cache, then database, then fail closed."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.cache import CacheStats, InMemorySwitchCache, SwitchCache
from app.config import Settings
from app.db import Database, DatabaseStats
from app.repository import upsert_switch
from app.resolver import SwitchResolver
from app.switch_domain import CallerContext, DecisionSource

DEAD_URL = "postgresql+psycopg://nobody:nobody@127.0.0.1:59999/no_such_database"

CALLER = CallerContext(user_id="u-1003", roles=("operation",), site_ids=("site-hangzhou",))


@pytest.fixture()
def db(settings: Settings) -> Iterator[Database]:
    database = Database(settings, DatabaseStats())
    database.create_all()
    yield database
    database.dispose()


def _resolver(db: Database, cache: SwitchCache, environ: dict[str, str] | None = None) -> SwitchResolver:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", cache_ttl_seconds=5.0)
    return SwitchResolver(db, cache, settings, environ=environ or {})


def test_force_off_never_opens_a_connection(dead_db: Database) -> None:
    cache = InMemorySwitchCache("asst", CacheStats(), 5.0)
    resolver = _resolver(dead_db, cache, {"ASST_FORCE_OFF": "true"})
    decision = resolver.resolve(CALLER)
    assert decision.enabled is False
    assert decision.source is DecisionSource.FORCE_OFF
    assert dead_db.stats.total() == 0
    assert cache.stats.total() == 0


@pytest.fixture()
def dead_db() -> Iterator[Database]:
    settings = Settings(database_url=DEAD_URL, cache_ttl_seconds=5.0)
    database = Database(settings, DatabaseStats())
    yield database
    database.dispose()


def test_database_rows_are_read_once_then_cached(db: Database) -> None:
    with db.read() as session:
        upsert_switch(
            session,
            scope="site",
            scope_ref="site-hangzhou",
            enabled=False,
            reason="机房迁移",
            actor="u-1001",
            site_id="site-hangzhou",
        )
        session.commit()
    db.stats.reset()
    cache = InMemorySwitchCache("asst", CacheStats(), 5.0)
    resolver = _resolver(db, cache)

    first = resolver.resolve(CALLER)
    assert first.enabled is False
    statements_after_first = db.stats.statements
    assert statements_after_first >= 1

    second = resolver.resolve(CALLER)
    assert second.enabled is False
    assert db.stats.statements == statements_after_first, "the second read must hit the cache"


def test_invalidate_forces_the_next_read_to_see_the_change(db: Database) -> None:
    cache = InMemorySwitchCache("asst", CacheStats(), 5.0)
    resolver = _resolver(db, cache)
    assert resolver.resolve(CALLER).enabled is True

    with db.read() as session:
        upsert_switch(
            session,
            scope="global",
            scope_ref="",
            enabled=False,
            reason="全平台演练",
            actor="u-1001",
        )
        session.commit()
    resolver.invalidate()

    decision = resolver.resolve(CALLER)
    assert decision.enabled is False
    assert decision.source is DecisionSource.GLOBAL


def test_unreadable_state_fails_closed(dead_db: Database) -> None:
    cache = InMemorySwitchCache("asst", CacheStats(), 5.0)
    resolver = _resolver(dead_db, cache)
    decision = resolver.resolve(CALLER)
    assert decision.enabled is False
    assert decision.source is DecisionSource.STATE_UNAVAILABLE


def test_last_snapshot_is_reused_when_the_database_dies_mid_flight(db: Database) -> None:
    cache = InMemorySwitchCache("asst", CacheStats(), 5.0)
    resolver = _resolver(db, cache)
    snapshot = resolver.snapshot()
    assert snapshot is not None

    broken = _resolver(Database(Settings(database_url=DEAD_URL, cache_ttl_seconds=5.0), DatabaseStats()), cache)
    decision = broken.resolve(CALLER, allow_db=False, last_snapshot=snapshot)
    assert decision.enabled is True


def test_streaming_resolve_reuses_the_snapshot_between_frames(db: Database) -> None:
    cache = InMemorySwitchCache("asst", CacheStats(), 5.0)
    resolver = _resolver(db, cache)
    decision, snapshot = resolver.resolve_streaming(CALLER, None)
    assert decision.enabled is True
    assert snapshot is not None

    db.stats.reset()
    for _ in range(5):
        decision, snapshot = resolver.resolve_streaming(CALLER, snapshot)
        assert decision.enabled is True
    assert db.stats.statements == 0, "frame checks must not hit the database"


def test_streaming_resolve_sees_a_disable_after_invalidation(db: Database) -> None:
    cache = InMemorySwitchCache("asst", CacheStats(), 5.0)
    resolver = _resolver(db, cache)
    _, snapshot = resolver.resolve_streaming(CALLER, None)

    with db.read() as session:
        upsert_switch(
            session,
            scope="global",
            scope_ref="",
            enabled=False,
            reason="流中关闭",
            actor="u-1001",
        )
        session.commit()
    resolver.invalidate()

    decision, _ = resolver.resolve_streaming(CALLER, snapshot)
    assert decision.enabled is False
    assert decision.source is DecisionSource.GLOBAL
