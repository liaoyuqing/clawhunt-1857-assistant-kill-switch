"""Cache behaviour: 5 second TTL, generation bump, and the Redis branch itself."""

from __future__ import annotations

import time

import fakeredis
import pytest

from app.cache import (
    CacheStats,
    InMemorySwitchCache,
    RedisSwitchCache,
    SwitchRow,
    SwitchSnapshot,
    cache_key_names,
)


def _snapshot(generation: int = 1, enabled: bool = False) -> SwitchSnapshot:
    snapshot = SwitchSnapshot(module="asst", generation=generation)
    snapshot.rows[("global", "")] = SwitchRow("global", "", enabled, "演练")
    return snapshot


def test_memory_cache_hits_and_misses_counter() -> None:
    stats = CacheStats()
    cache = InMemorySwitchCache("asst", stats, ttl_seconds=5.0)
    generation = cache.generation()
    assert cache.read(generation) is None
    cache.write(_snapshot(generation), 5.0)
    assert cache.read(generation) is not None
    assert stats.misses == 1
    assert stats.hits == 1


def test_memory_cache_expires_after_its_ttl() -> None:
    cache = InMemorySwitchCache("asst", CacheStats(), ttl_seconds=0.05)
    generation = cache.generation()
    cache.write(_snapshot(generation), 0.05)
    assert cache.read(generation) is not None
    time.sleep(0.1)
    assert cache.read(generation) is None


def test_bump_produces_a_new_generation_and_hides_the_old_snapshot() -> None:
    cache = InMemorySwitchCache("asst", CacheStats(), ttl_seconds=5.0)
    first = cache.generation()
    cache.write(_snapshot(first), 5.0)
    second = cache.bump()
    assert second != first
    assert cache.read(second) is None, "a bumped generation must not serve the old snapshot"


def test_redis_cache_round_trip_through_fakeredis() -> None:
    client = fakeredis.FakeStrictRedis(decode_responses=True)
    stats = CacheStats()
    cache = RedisSwitchCache(client, "asst", stats, ttl_seconds=5)
    generation = cache.generation()
    snapshot = _snapshot(generation)
    cache.write(snapshot, 5)
    restored = cache.read(generation)
    assert restored is not None
    assert restored.rows[("global", "")].enabled is False
    assert restored.rows[("global", "")].reason == "演练"


def test_redis_cache_uses_a_five_second_ttl_on_the_key() -> None:
    client = fakeredis.FakeStrictRedis(decode_responses=True)
    cache = RedisSwitchCache(client, "asst", ttl_seconds=5)
    generation = cache.generation()
    cache.write(_snapshot(generation), 5)
    ttl = client.ttl(f"asst:switch:data:{generation}")
    assert 0 < int(ttl) <= 5


def test_redis_bump_increments_the_shared_generation_key() -> None:
    client = fakeredis.FakeStrictRedis(decode_responses=True)
    cache = RedisSwitchCache(client, "asst", ttl_seconds=5)
    first = cache.generation()
    assert cache.bump() == first + 1
    assert cache.bump() == first + 2


def test_invalidation_converges_another_instance_immediately() -> None:
    """Two instances share Redis; a change on one is visible to the other at once."""

    client = fakeredis.FakeStrictRedis(decode_responses=True)
    writer = RedisSwitchCache(client, "asst", CacheStats(), ttl_seconds=5)
    reader = RedisSwitchCache(client, "asst", CacheStats(), ttl_seconds=5)

    generation = writer.generation()
    writer.write(_snapshot(generation, enabled=True), 5)
    assert reader.read(reader.generation()) is not None
    assert reader.read(reader.generation()).rows[("global", "")].enabled is True  # type: ignore[union-attr]

    fresh = writer.bump()
    writer.write(_snapshot(fresh, enabled=False), 5)
    snapshot = reader.read(reader.generation())
    assert snapshot is not None
    assert snapshot.rows[("global", "")].enabled is False


def test_worst_case_ttl_bounds_propagation_without_any_message() -> None:
    """Even if the invalidation message is lost, the TTL caps staleness at 5s."""

    client = fakeredis.FakeStrictRedis(decode_responses=True)
    cache = RedisSwitchCache(client, "asst", CacheStats(), ttl_seconds=5)
    generation = cache.generation()
    cache.write(_snapshot(generation, enabled=True), 5)
    assert client.ttl(f"asst:switch:data:{generation}") <= 5


def test_publish_targets_the_module_channel() -> None:
    client = fakeredis.FakeStrictRedis(decode_responses=True)
    pubsub = client.pubsub()
    pubsub.subscribe("asst:switch:invalidated")
    pubsub.get_message(timeout=0.1)
    cache = RedisSwitchCache(client, "asst", CacheStats(), ttl_seconds=5)
    cache.publish_invalidation()
    message = pubsub.get_message(timeout=1.0)
    assert message is not None
    assert message["channel"] == "asst:switch:invalidated"


def test_cache_key_names_helper_matches_the_real_keys() -> None:
    client = fakeredis.FakeStrictRedis(decode_responses=True)
    cache = RedisSwitchCache(client, "asst", CacheStats(), ttl_seconds=5)
    generation = cache.generation()
    names = cache_key_names("asst", generation)
    assert names["generation"] == "asst:switch:gen"
    assert names["data"] == f"asst:switch:data:{generation}"
    assert client.exists(names["generation"]) == 1


@pytest.mark.parametrize("module", ["asst", "recruit"])
def test_snapshot_json_round_trip(module: str) -> None:
    snapshot = SwitchSnapshot(module=module, generation=3)
    snapshot.rows[("site", "site-hangzhou")] = SwitchRow(
        "site", "site-hangzhou", False, "杭州站点变更"
    )
    restored = SwitchSnapshot.from_json(module, snapshot.to_json())
    assert restored.generation == 3
    row = restored.rows[("site", "site-hangzhou")]
    assert row.enabled is False
    assert row.reason == "杭州站点变更"
