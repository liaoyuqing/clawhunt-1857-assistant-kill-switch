"""Runtime settings. Everything is read from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

TRUE_VALUES = frozenset({"true", "1", "yes", "on"})
FORCE_OFF_ENV = "ASST_FORCE_OFF"
FORCE_OFF_REASON_ENV = "ASST_FORCE_OFF_REASON"
DEFAULT_FORCE_OFF_REASON = "紧急止血：ASST_FORCE_OFF=true，助手已由运维无条件关闭，解除后自动恢复"
DEFAULT_CACHE_TTL_SECONDS = 5.0


@dataclass(frozen=True)
class Settings:
    """Immutable settings snapshot, one per application instance."""

    database_url: str = "sqlite+pysqlite:///./var/asst.db"
    redis_url: str = ""
    cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS
    sse_frame_interval_seconds: float = 0.05
    sse_max_frames: int = 600
    env: str = "dev"

    @property
    def cache_backend(self) -> str:
        return "redis" if self.redis_url.strip() else "memory"


def _as_float(raw: str, default: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _as_int(raw: str, default: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def force_off_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """True only for explicit truthy values; "false", "0" and empty mean untouched."""

    source = os.environ if environ is None else environ
    return str(source.get(FORCE_OFF_ENV, "")).strip().lower() in TRUE_VALUES


def force_off_reason(environ: Mapping[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    raw = str(source.get(FORCE_OFF_REASON_ENV, "")).strip()
    return raw[:200] if raw else DEFAULT_FORCE_OFF_REASON


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    source = os.environ if environ is None else environ

    def raw(name: str, default: str = "") -> str:
        value = str(source.get(name, "")).strip()
        return value if value else default

    return Settings(
        database_url=raw("ASST_DATABASE_URL", "sqlite+pysqlite:///./var/asst.db"),
        redis_url=raw("ASST_REDIS_URL"),
        cache_ttl_seconds=_as_float(raw("ASST_CACHE_TTL_SECONDS"), DEFAULT_CACHE_TTL_SECONDS),
        sse_frame_interval_seconds=_as_float(raw("ASST_SSE_FRAME_INTERVAL_SECONDS"), 0.05),
        sse_max_frames=_as_int(raw("ASST_SSE_MAX_FRAMES"), 600),
        env=raw("ASST_ENV", "dev"),
    )
