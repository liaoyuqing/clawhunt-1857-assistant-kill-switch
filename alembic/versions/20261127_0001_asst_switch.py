"""asst_switch, recruit_switch and audit_log

Revision ID: 20261127_0001
Revises:
Create Date: 2026-11-27

Creates the switch table with the four scopes, the mandatory reason column
(<= 200 characters, enforced by a CHECK constraint as well as by the API), the
shared platform columns, and a unique index on (scope, scope_ref) so one scope
reference can never carry two contradictory rows.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261127_0001"
down_revision = None
branch_labels = None
depends_on = None

SHARED_COLUMNS = [
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("org_id", sa.String(64), nullable=False, server_default=""),
    sa.Column("site_id", sa.String(64), nullable=False, server_default=""),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("created_by", sa.String(64), nullable=False, server_default=""),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
]


def upgrade() -> None:
    op.create_table(
        "asst_switch",
        *SHARED_COLUMNS,
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("scope_ref", sa.String(64), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("changed_by", sa.String(64), nullable=False, server_default=""),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("scope in ('global','site','role','tool')", name="ck_asst_switch_scope"),
        sa.CheckConstraint("char_length(reason) <= 200", name="ck_asst_switch_reason_len"),
        sa.CheckConstraint(
            "(scope = 'global' and scope_ref = '') or (scope <> 'global' and scope_ref <> '')",
            name="ck_asst_switch_scope_ref",
        ),
    )
    op.create_index("ux_asst_switch_scope_ref", "asst_switch", ["scope", "scope_ref"], unique=True)
    op.create_index("ix_asst_switch_site", "asst_switch", ["site_id"])

    op.create_table(
        "recruit_switch",
        *SHARED_COLUMNS,
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("changed_by", sa.String(64), nullable=False, server_default=""),
    )

    op.create_table(
        "audit_log",
        *SHARED_COLUMNS,
        sa.Column("module", sa.String(32), nullable=False, server_default="assistant"),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target", sa.String(160), nullable=False, server_default=""),
        sa.Column("actor", sa.String(64), nullable=False, server_default=""),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_audit_log_module", "audit_log", ["module"])

    # Seed one disabled row per scope so the four-level ordering is visible on a
    # fresh install; the API keeps the same shapes, so the seed is idempotent.
    op.execute(
        """
        insert into asst_switch (id, scope, scope_ref, enabled, reason, changed_by, changed_at, org_id, site_id, created_by)
        values
          (gen_random_uuid(), 'global', '', true,  '初始安装：全局开关默认开启', 'system', now(), '', '', 'system'),
          (gen_random_uuid(), 'site', 'site-hangzhou', true, '初始安装：杭州站点默认开启', 'system', now(), '', 'site-hangzhou', 'system'),
          (gen_random_uuid(), 'site', 'site-suzhou',   true, '初始安装：苏州站点默认开启', 'system', now(), '', 'site-suzhou', 'system'),
          (gen_random_uuid(), 'role', 'operation',     true, '初始安装：运营角色默认开启', 'system', now(), '', '', 'system'),
          (gen_random_uuid(), 'tool', 'sop_draft',     true, '初始安装：SOP 工具默认开启', 'system', now(), '', '', 'system')
        """
    )


def downgrade() -> None:
    op.drop_index("ix_audit_log_module", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("recruit_switch")
    op.drop_index("ix_asst_switch_site", table_name="asst_switch")
    op.drop_index("ux_asst_switch_scope_ref", table_name="asst_switch")
    op.drop_table("asst_switch")
