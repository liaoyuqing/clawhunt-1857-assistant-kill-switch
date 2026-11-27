"""Server-sent events for the assistant chat, with a switch check per frame.

The kill switch is honoured between frames, never "after the whole answer was
generated": the loop checks at every frame boundary, so a global disable lands
in the very next frame - tens of milliseconds, far inside the five second
promise. Waiting for the full generation would take tens of seconds and is the
documented anti-pattern in README.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from app.config import Settings
from app.resolver import ASSISTANT_TOOLS, SwitchResolver
from app.switch_domain import CallerContext, Decision

TOKEN_POOL: tuple[str, ...] = (
    "会员空间的",
    "资源对接",
    "本周新增",
    "三条线索，",
    "其中两条",
    "来自杭州站点；",
    "建议先安排",
    "一次线上沟通，",
    "再把资料",
    "同步到知识库。",
)


def sse_frame(event: str, payload: dict[str, object]) -> str:
    """One SSE frame: named event plus a JSON data line."""

    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@dataclass
class StreamReport:
    """Written by the generator so tests and logs can assert on real numbers."""

    tokens: int = 0
    finish_reason: str = ""
    disabled_source: str = ""
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float = 0.0
    checks: int = 0

    @property
    def elapsed_seconds(self) -> float:
        return (self.finished_at or time.monotonic()) - self.started_at


async def stream_chat(
    resolver: SwitchResolver,
    caller: CallerContext,
    prompt: str,
    settings: Settings,
    report: StreamReport | None = None,
) -> AsyncIterator[str]:
    """Yield SSE frames until the answer ends or the switch turns the assistant off.

    The caller must already have verified that the assistant is on; a stream is
    never opened for a request that arrives after the disable (that path answers
    503 directly, as the requirement asks).
    """

    report = report or StreamReport()
    snapshot = None
    yield sse_frame(
        "start",
        {
            "model": "member-space-assistant-v1",
            "tools": list(ASSISTANT_TOOLS),
            "prompt": prompt[:200],
            "started_at": round(report.started_at, 3),
        },
    )

    for index in range(settings.sse_max_frames):
        # The switch is re-evaluated at the frame boundary, before the frame is
        # written; allow_db=False keeps this to a cache read at worst.
        decision, snapshot = resolver.resolve_streaming(caller, snapshot)
        report.checks += 1
        if not decision.enabled:
            report.finish_reason = "disabled"
            report.disabled_source = decision.source.value
            report.finished_at = time.monotonic()
            yield sse_frame(
                "finish",
                {
                    "finish_reason": "disabled",
                    "source": decision.source.value,
                    "reason": decision.reason,
                    "tokens": report.tokens,
                    "checks": report.checks,
                    "elapsed_ms": round(report.elapsed_seconds * 1000, 1),
                },
            )
            return

        await asyncio.sleep(settings.sse_frame_interval_seconds)
        token = TOKEN_POOL[index % len(TOKEN_POOL)]
        report.tokens += 1
        yield sse_frame(
            "token",
            {"index": index, "text": token, "tokens": report.tokens},
        )

    report.finish_reason = "stop"
    report.finished_at = time.monotonic()
    yield sse_frame(
        "finish",
        {
            "finish_reason": "stop",
            "tokens": report.tokens,
            "checks": report.checks,
            "elapsed_ms": round(report.elapsed_seconds * 1000, 1),
        },
    )


def last_decision(decision: Decision) -> dict[str, object]:
    return {"enabled": decision.enabled, "source": decision.source.value}
