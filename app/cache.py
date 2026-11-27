"""Switch snapshot cache: Redis in production, in-memory stand-in for tests.

Two rules make the cache safe rather than merely fast:

* every entry has a 5 second TTL, so an instance that missed an invalidation
  message still converges within the promised window;
* every write bumps a generation counter, and the generation is part of the
  cache key. A reader that sees a new generation can never be served the old
  snapshot, so propagation is bounded by one round trip, not by the TTL.

Key names are always prefixed per module (asst / recruit), which is what keeps
two independent switches from colliding on a shared Redis instance.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config import DEFAULT_CACHE_TTL_SECONDS

SCOPE_KEYS: tuple[str, ...] = ("global", "site", "role", "tool")


@dataclass
class CacheStats:
    """Counters used by acceptance criterion 1 (Redis touched: 0)."""

    gets: int = 0
    sets: int = 0
    bumps: int = 0
    hits: int = 0
    misses: int = 0
    errors: int = 0

    def reset(self) -> None:
        self.gets = 0
        self.sets = 0
        self.bumps = 0
        self.hits = 0
        self.misses = 0
        self.errors = 0

    def total(self) -> int:
        return self.gets + self.sets + self.bumps

    def snapshot(self) -> dict[str, int]:
        return {
            "gets": self.gets,
            "sets": self.sets,
            "bumps": self.bumps,
            "hits": self.hits,
            "misses": self.misses,
            "errors": self.errors,
            "total": self.total(),
        }


@dataclass(frozen=True)
class SwitchRow:
    """Cache-friendly projection of one asst_switch row."""

    scope: str
    scope_ref: str
    enabled: bool
    reason: str = ""


@dataclass
class SwitchSnapshot:
    module: str
    generation: int
    rows: dict[tuple[str, str], SwitchRow] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "module": self.module,
                "generation": self.generation,
                "rows": [
                    {
                        "scope": row.scope,
                        "scope_ref": row.scope_ref,
                        "enabled": row.enabled,
                        "reason": row.reason,
                    }
                    for row in self.rows.values()
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, module: str, raw: str) -> SwitchSnapshot:
        data: dict[str, Any] = json.loads(raw)
        rows: dict[tuple[str, str], SwitchRow] = {}
        for item in data.get("rows", []):
            scope = str(item.get("scope", ""))
            ref = str(item.get("scope_ref", ""))
            if scope not in SCOPE_KEYS:
                continue
            rows[(scope, ref)] = SwitchRow(
                scope=scope,
                scope_ref=ref,
                enabled=bool(item.get("enabled", True)),
                reason=str(item.get("reason", "")),
            )
        return cls(module=module, generation=int(data.get("generation", 0)), rows=rows)


class SwitchCache(Protocol):
    """Minimal surface the resolver needs; both backends implement it."""

    stats: CacheStats

    @property
    def backend(self) -> str: ...

    def generation(self) -> int: ...

    def read(self, generation: int) -> SwitchSnapshot | None: ...

    def write(self, snapshot: SwitchSnapshot, ttl_seconds: float) -> None: ...

    def bump(self) -> int: ...

    def publish_invalidation(self) -> int: ...


class RedisLike(Protocol):
    def get(self, key: str) -> Any: ...

    def set(self, key: str, value: str, ex: int | None = None) -> Any: ...

    def incr(self, key: str) -> int: ...

    def publish(self, channel: str, message: str) -> Any: ...


class RedisSwitchCache:
    """Redis-backed cache keyed by ``<module>:switch:gen`` plus ``<module>:switch:data:<gen>``."""

    def __init__(
        self,
        client: RedisLike,
        module: str = "asst",
        stats: CacheStats | None = None,
        ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
    ) -> None:
        self.client = client
        self.module = module
        self.stats = stats or CacheStats()
        self.ttl_seconds = ttl_seconds

    @property
    def backend(self) -> str:
        return "redis"

    def generation(self) -> int:
        key = f"{self.module}:switch:gen"
        self.stats.gets += 1
        raw = self.client.get(key)
        if raw is None:
            self.stats.gets += 1
            self.client.set(key, "1", ex=None)
            return 1
        return int(raw)

    def read(self, generation: int) -> SwitchSnapshot | None:
        key = f"{self.module}:switch:data:{generation}"
        self.stats.gets += 1
        raw = self.client.get(key)
        if raw is None:
            self.stats.misses += 1
            return None
        self.stats.hits += 1
        # redis-py may hand back bytes depending on decode_responses.
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        return SwitchSnapshot.from_json(self.module, text)

    def write(self, snapshot: SwitchSnapshot, ttl_seconds: float | None = None) -> None:
        ttl = int(round(ttl_seconds if ttl_seconds is not None else self.ttl_seconds))
        ttl = max(1, ttl)
        key = f"{self.module}:switch:data:{snapshot.generation}"
        self.stats.sets += 1
        self.client.set(key, snapshot.to_json(), ex=ttl)

    def bump(self) -> int:
        self.stats.bumps += 1
        return int(self.client.incr(f"{self.module}:switch:gen"))

    def publish_invalidation(self) -> int:
        self.stats.bumps += 1
        return int(self.client.publish(f"{self.module}:switch:invalidated", self.module))


class InMemorySwitchCache:
    """Same contract without Redis; used when no Redis URL is configured."""

    def __init__(
        self,
        module: str = "asst",
        stats: CacheStats | None = None,
        ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
    ) -> None:
        self.module = module
        self.stats = stats or CacheStats()
        self.ttl_seconds = ttl_seconds
        self._generation = 1
        self._entries: dict[tuple[int, str], tuple[float, str]] = {}

    @property
    def backend(self) -> str:
        return "memory"

    def generation(self) -> int:
        self.stats.gets += 1
        return self._generation

    def read(self, generation: int) -> SwitchSnapshot | None:
        self.stats.gets += 1
        entry = self._entries.get((generation, self.module))
        if entry is None:
            self.stats.misses += 1
            return None
        expires_at, payload = entry
        if expires_at < time.monotonic():
            self.stats.misses += 1
            self._entries.pop((generation, self.module), None)
            return None
        self.stats.hits += 1
        return SwitchSnapshot.from_json(self.module, payload)

    def write(self, snapshot: SwitchSnapshot, ttl_seconds: float | None = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self.ttl_seconds
        self.stats.sets += 1
        self._entries[(snapshot.generation, self.module)] = (
            time.monotonic() + max(0.001, ttl),
            snapshot.to_json(),
        )

    def bump(self) -> int:
        self.stats.bumps += 1
        self._generation += 1
        return self._generation

    def publish_invalidation(self) -> int:
        return self.bump()


def cache_key_names(module: str, generation: int) -> Mapping[str, str]:
    """Exposed for the isolation test: the two modules never share a key."""

    return {
        "generation": f"{module}:switch:gen",
        "data": f"{module}:switch:data:{generation}",
        "channel": f"{module}:switch:invalidated",
    }
