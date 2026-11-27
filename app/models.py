"""ORM models: the switch table plus the shared platform columns.

Every business table of the member-space platform carries id(UUID), org_id,
site_id, created_at, created_by, updated_at, deleted_at; asst_switch is no
exception. The three extra columns of the switch itself are changed_by and
changed_at (the operator who flipped it and when) next to enabled/reason.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db import Base
from app.switch_domain import SwitchScope

SCOPE_VALUES = tuple(scope.value for scope in SwitchScope)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SharedColumns:
    """Columns shared by every business table of the platform."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    site_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AsstSwitch(SharedColumns, Base):
    """One row per scope/scope_ref pair; absence means "not configured, use the default"."""

    __tablename__ = "asst_switch"

    scope: Mapped[str] = mapped_column(
        Enum(*SCOPE_VALUES, name="asst_switch_scope", native_enum=False, length=16),
        nullable=False,
    )
    scope_ref: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        CheckConstraint("scope in ('global','site','role','tool')", name="ck_asst_switch_scope"),
        CheckConstraint("length(reason) <= 200", name="ck_asst_switch_reason_len"),
        Index("ux_asst_switch_scope_ref", "scope", "scope_ref", unique=True),
        Index("ix_asst_switch_site", "site_id"),
    )


class AuditLog(SharedColumns, Base):
    """Stand-in for S2-AUDIT; the real service is called through the same interface."""

    __tablename__ = "audit_log"

    module: Mapped[str] = mapped_column(String(32), nullable=False, default="assistant")
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")

class RecruitSwitch(SharedColumns, Base):
    """Recruitment AI keeps its own row, table and cache namespace (AC4)."""

    __tablename__ = "recruit_switch"

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    changed_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
