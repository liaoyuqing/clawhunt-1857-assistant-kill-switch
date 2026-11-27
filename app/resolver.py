"""The switch resolver: the only place that turns rows into a verdict.

Ordering is fixed and non-negotiable: ASST_FORCE_OFF -> global -> site -> role
-> tool. The first step never touches storage: the environment flag is read
from the process environment, and when it is on the resolver returns before any
cache or database call, which is exactly what acceptance criterion 1 asserts.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import replace

from app.cache import SwitchCache, SwitchRow, SwitchSnapshot
from app.config import Settings, force_off_enabled, force_off_reason
from app.db import Database
from app.models import AsstSwitch
from app.repository import list_switch_rows
from app.switch_domain import (
    STATE_UNAVAILABLE_MESSAGE,
    CallerContext,
    Decision,
    DecisionSource,
    SwitchScope,
    evaluate,
)

logger = logging.getLogger(__name__)

ASSISTANT_TOOLS: tuple[str, ...] = (
    "knowledge_search",
    "sop_draft",
    "member_profile_summary",
    "policy_qa",
)


class SwitchResolver:
    """Reads the environment, then the cache, then the database - in that order."""

    def __init__(
        self,
        db: Database,
        cache: SwitchCache,
        settings: Settings,
        tools: tuple[str, ...] = ASSISTANT_TOOLS,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.db = db
        self.cache = cache
        self.settings = settings
        self.tools = tools
        self.environ = environ

    # -- snapshot plumbing -------------------------------------------------
    def _load_rows(self) -> list[AsstSwitch]:
        with self.db.read() as session:
            return list_switch_rows(session)

    def snapshot(self, *, allow_db: bool = True) -> SwitchSnapshot | None:
        """Read the cached snapshot; on a miss load from the database once."""

        generation = self.cache.generation()
        cached = self.cache.read(generation)
        if cached is not None:
            return cached
        if not allow_db:
            return None
        snapshot = SwitchSnapshot(module="asst", generation=generation)
        for row in self._load_rows():
            snapshot.rows[(row.scope, row.scope_ref)] = SwitchRow(
                scope=row.scope,
                scope_ref=row.scope_ref,
                enabled=bool(row.enabled),
                reason=row.reason or "",
            )
        self.cache.write(snapshot, self.settings.cache_ttl_seconds)
        return snapshot

    def invalidate(self) -> int:
        """Bump the generation and publish; every instance converges at once."""

        generation = self.cache.bump()
        self.cache.publish_invalidation()
        return generation

    # -- public API --------------------------------------------------------
    def resolve(
        self,
        caller: CallerContext,
        *,
        tool: str | None = None,
        allow_db: bool = True,
        last_snapshot: SwitchSnapshot | None = None,
    ) -> Decision:
        """Evaluate the four levels for one caller.

        `allow_db=False` is used by the streaming loop, which must not open a
        database round trip per frame: it re-reads the cache generation (cheap)
        and reuses the last snapshot when the generation has not moved.
        """

        if force_off_enabled(self.environ):
            # Deliberate: no cache read, no database connection, no Redis call.
            return evaluate(
                caller,
                force_off=True,
                rows={},
                force_off_reason=force_off_reason(self.environ),
                all_tools=self.tools,
            )

        target = replace(caller, tool=tool) if tool is not None else caller
        snapshot: SwitchSnapshot | None = None
        try:
            snapshot = self.snapshot(allow_db=allow_db)
        except Exception:  # noqa: BLE001 - a cache/db outage must not turn 503 into 500
            logger.warning("switch snapshot unavailable, reusing last known state")
            snapshot = last_snapshot

        if snapshot is None:
            snapshot = last_snapshot
        if snapshot is None:
            # Nothing cached and the read failed. A kill switch must not fail
            # open: with no provable state the assistant is refused, and the
            # verdict is labelled so an operator can tell it from a real switch.
            return Decision(
                enabled=False,
                source=DecisionSource.STATE_UNAVAILABLE,
                message=STATE_UNAVAILABLE_MESSAGE,
                blocked_tools=self.tools,
            )

        return evaluate(
            target,
            force_off=False,
            rows=snapshot.rows,
            all_tools=self.tools,
        )

    def resolve_streaming(
        self,
        caller: CallerContext,
        last_snapshot: SwitchSnapshot | None = None,
    ) -> tuple[Decision, SwitchSnapshot | None]:
        """Frame-boundary check for the SSE loop.

        The generation counter is a cheap cache read; when it has not moved the
        previous snapshot is reused, so a 20 frame per second stream costs 20 key
        reads, not 20 database round trips. A moved generation triggers exactly
        one refresh, which is how a disable lands within one frame.
        """

        if force_off_enabled(self.environ):
            return (
                evaluate(
                    caller,
                    force_off=True,
                    rows={},
                    force_off_reason=force_off_reason(self.environ),
                    all_tools=self.tools,
                ),
                last_snapshot,
            )

        snapshot = last_snapshot
        try:
            generation = self.cache.generation()
            if snapshot is None or snapshot.generation != generation:
                cached = self.cache.read(generation)
                # Exactly one refresh per change: the generation moved, so the
                # cached snapshot is gone and the database is read once.
                snapshot = cached if cached is not None else self.snapshot(allow_db=True)
        except Exception:  # noqa: BLE001 - keep streaming on the last known state
            logger.warning("cache unavailable inside the stream, reusing last snapshot")

        if snapshot is None:
            return evaluate(caller, force_off=False, rows={}, all_tools=self.tools), snapshot

        return (
            evaluate(caller, force_off=False, rows=snapshot.rows, all_tools=self.tools),
            snapshot,
        )

    def tools_state(self, caller: CallerContext) -> dict[str, list[str]]:
        decision = self.resolve(caller)
        return {"tools": list(decision.tools), "blocked_tools": list(decision.blocked_tools)}


def decision_http_status(decision: Decision) -> int:
    """Both "off" verdicts are refusals; the code tells which level did it."""

    return 409 if decision.source is DecisionSource.TOOL else 503


def scope_label(scope: str, scope_ref: str) -> str:
    if scope == SwitchScope.GLOBAL.value:
        return "global"
    return f"{scope}:{scope_ref}"
