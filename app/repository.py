"""Persistence for asst_switch, plus the audit and message-centre stubs' storage."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AsstSwitch, AuditLog
from app.switch_domain import REASON_MAX_LENGTH, SCOPE_REF_MAX_LENGTH, SwitchScope


def list_switch_rows(session: Session) -> list[AsstSwitch]:
    """All live switch rows; deleted_at is the platform's soft delete marker."""

    statement = select(AsstSwitch).where(AsstSwitch.deleted_at.is_(None))
    return list(session.scalars(statement))


def get_switch(session: Session, scope: str, scope_ref: str) -> AsstSwitch | None:
    statement = (
        select(AsstSwitch)
        .where(
            AsstSwitch.scope == scope,
            AsstSwitch.scope_ref == scope_ref,
            AsstSwitch.deleted_at.is_(None),
        )
        .limit(1)
    )
    return session.scalars(statement).first()


def validate_switch_payload(scope: str, scope_ref: str, enabled: bool, reason: str) -> None:
    """Reject bad input early; the CHECK constraints are the second line of defence."""

    if scope not in {item.value for item in SwitchScope}:
        raise ValueError(f"unknown scope: {scope}")
    if scope == SwitchScope.GLOBAL.value:
        if scope_ref:
            raise ValueError("global scope must not carry a scope_ref")
    else:
        if not scope_ref.strip():
            raise ValueError(f"scope_ref is required for scope {scope}")
        if len(scope_ref) > SCOPE_REF_MAX_LENGTH:
            raise ValueError(f"scope_ref must be at most {SCOPE_REF_MAX_LENGTH} characters")
    if enabled is False and not reason.strip():
        raise ValueError("reason is required when disabling")
    if len(reason) > REASON_MAX_LENGTH:
        raise ValueError(f"reason must be at most {REASON_MAX_LENGTH} characters")


def upsert_switch(
    session: Session,
    *,
    scope: str,
    scope_ref: str,
    enabled: bool,
    reason: str,
    actor: str,
    org_id: str = "",
    site_id: str = "",
) -> tuple[AsstSwitch, bool]:
    """Insert or update one switch; returns (row, created)."""

    validate_switch_payload(scope, scope_ref, enabled, reason)
    row = get_switch(session, scope, scope_ref)
    created = row is None
    now = datetime.now(UTC)
    if row is None:
        row = AsstSwitch(
            scope=scope,
            scope_ref=scope_ref,
            enabled=enabled,
            reason=reason,
            changed_by=actor,
            changed_at=now,
            org_id=org_id,
            site_id=site_id if scope != SwitchScope.GLOBAL.value else "",
            created_by=actor,
        )
        session.add(row)
    else:
        row.enabled = enabled
        row.reason = reason
        row.changed_by = actor
        row.changed_at = now
        if site_id and scope == SwitchScope.SITE.value:
            row.site_id = site_id
    session.flush()
    return row, created


def write_audit(
    session: Session,
    *,
    module: str,
    action: str,
    target: str,
    actor: str,
    detail: str = "",
    org_id: str = "",
) -> AuditLog:
    entry = AuditLog(
        module=module,
        action=action,
        target=target,
        actor=actor,
        detail=detail,
        org_id=org_id,
        created_by=actor,
    )
    session.add(entry)
    session.flush()
    return entry


def list_audit(session: Session, module: str | None = None) -> list[AuditLog]:
    statement = select(AuditLog).order_by(AuditLog.created_at.asc())
    if module:
        statement = statement.where(AuditLog.module == module)
    return list(session.scalars(statement))
