"""Service object that ties the decision function to cache, database and audit.

`AssistantSwitchService.decision_for()` is the single entry point used by the
status route, the chat route, the tool gate and the streaming loop, so all four
paths can never disagree about which level turned the assistant off.
"""

from __future__ import annotations

from dataclasses import replace

from sqlalchemy.orm import Session

from app.audit import (
    ACTION_SWITCH_DISABLE,
    ACTION_SWITCH_ENABLE,
    ACTION_SWITCH_SEED,
    ACTION_SWITCH_UPDATE,
    MODULE_ASSISTANT,
    action_for,
)
from app.cache import SwitchCache
from app.config import Settings, force_off_enabled
from app.db import Database
from app.models import AsstSwitch
from app.notify import ADMIN_GROUP, Notification, NotificationOutbox
from app.notify import MODULE_ASSISTANT as NOTIFY_ASSISTANT
from app.repository import list_switch_rows, upsert_switch, write_audit
from app.resolver import ASSISTANT_TOOLS, SwitchResolver, scope_label
from app.switch_domain import (
    CallerContext,
    Decision,
    DecisionSource,
    SwitchScope,
    evaluate,
)

__all__ = [
    "ASSISTANT_TOOLS",
    "AssistantSwitchService",
    "CallerContext",
    "Decision",
    "DecisionSource",
    "SwitchScope",
    "evaluate",
    "force_off_enabled",
]


class AssistantSwitchService:
    """One instance per application; owns the resolver and the write path."""

    def __init__(
        self,
        db: Database,
        cache: SwitchCache,
        settings: Settings,
        outbox: NotificationOutbox | None = None,
    ) -> None:
        self.db = db
        self.cache = cache
        self.settings = settings
        self.outbox = outbox or NotificationOutbox()
        self.resolver = SwitchResolver(db, cache, settings, tools=ASSISTANT_TOOLS)

    # -- read side ---------------------------------------------------------
    def decision_for(self, caller: CallerContext, tool: str | None = None) -> Decision:
        return self.resolver.resolve(caller, tool=tool)

    def status_payload(self, caller: CallerContext) -> dict[str, object]:
        decision = self.decision_for(caller)
        payload: dict[str, object] = {
            "enabled": decision.enabled,
            "source": decision.source.value,
            "message": decision.message,
            "reason": decision.reason,
            "tools": list(decision.tools),
            "blocked_tools": list(decision.blocked_tools),
            "cache_backend": self.cache.backend,
            "cache_ttl_seconds": self.settings.cache_ttl_seconds,
        }
        if not decision.enabled and decision.scope_ref and decision.source is not DecisionSource.GLOBAL:
            payload["scope_ref"] = decision.scope_ref
        return payload

    def list_switches(self) -> list[dict[str, object]]:
        with self.db.read() as session:
            rows = list_switch_rows(session)
        return [serialise(row) for row in rows]

    # -- write side --------------------------------------------------------
    def apply_switch(
        self,
        *,
        scope: str,
        scope_ref: str,
        enabled: bool,
        reason: str,
        actor: str,
        org_id: str = "",
        site_id: str = "",
    ) -> tuple[dict[str, object], bool]:
        with self.db.read() as session:
            row, created = upsert_switch(
                session,
                scope=scope,
                scope_ref=scope_ref,
                enabled=enabled,
                reason=reason,
                actor=actor,
                org_id=org_id,
                site_id=site_id,
            )
            action = action_for(scope, enabled, created)
            write_audit(
                session,
                module=MODULE_ASSISTANT,
                action=action,
                target=scope_label(scope, scope_ref),
                actor=actor,
                detail=f"enabled={enabled} reason={reason}",
                org_id=org_id,
            )
            session.commit()
            payload = serialise(row)
        self.invalidate()
        self.announce(scope, scope_ref, enabled, reason, actor)
        return payload, created

    def invalidate(self) -> int:
        return self.resolver.invalidate()

    def announce(self, scope: str, scope_ref: str, enabled: bool, reason: str, actor: str) -> None:
        verb = "恢复" if enabled else "关闭"
        self.outbox.send(
            Notification(
                module=NOTIFY_ASSISTANT,
                group=ADMIN_GROUP,
                title=f"员工 AI 助手已{verb}（{scope_label(scope, scope_ref)}）",
                body=f"操作人 {actor}；范围 {scope_label(scope, scope_ref)}；原因 {reason}",
            )
        )

    def refresh_cache(self) -> int:
        """Force a fresh snapshot from the database into the cache."""

        generation = self.cache.bump()
        snapshot = self.resolver.snapshot(allow_db=True)
        if snapshot is not None:
            snapshot = replace(snapshot, generation=generation)
            self.cache.write(snapshot, self.settings.cache_ttl_seconds)
        self.cache.publish_invalidation()
        return generation


def serialise(row: AsstSwitch) -> dict[str, object]:
    return {
        "id": str(row.id),
        "scope": row.scope,
        "scope_ref": row.scope_ref,
        "enabled": bool(row.enabled),
        "reason": row.reason,
        "changed_by": row.changed_by,
        "changed_at": row.changed_at.isoformat() if row.changed_at else None,
        "org_id": row.org_id,
        "site_id": row.site_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def is_disabled(decision: Decision) -> bool:
    return not decision.enabled


def audit_action_detail(session: Session) -> int:
    """Small helper kept for the audit tests."""

    from app.repository import list_audit

    return len(list_audit(session, MODULE_ASSISTANT))


__all__ += [
    "ACTION_SWITCH_DISABLE",
    "ACTION_SWITCH_ENABLE",
    "ACTION_SWITCH_SEED",
    "ACTION_SWITCH_UPDATE",
    "is_disabled",
]
