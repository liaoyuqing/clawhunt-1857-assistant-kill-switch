"""Pure decision tests: ordering, boundaries and the force-off short circuit."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.cache import SwitchRow
from app.config import force_off_enabled, force_off_reason
from app.switch_domain import (
    CallerContext,
    DecisionSource,
    evaluate,
)


def rows(*items: tuple[str, str, bool, str]) -> dict[tuple[str, str], SwitchRow]:
    return {
        (scope, ref): SwitchRow(scope=scope, scope_ref=ref, enabled=enabled, reason=reason)
        for scope, ref, enabled, reason in items
    }


CALLER = CallerContext(
    user_id="u-1003", org_id="org-xinglan", roles=("operation",), site_ids=("site-hangzhou",)
)
TOOLS = ("knowledge_search", "sop_draft")


def test_force_off_wins_over_every_scope() -> None:
    decision = evaluate(
        CALLER,
        force_off=True,
        rows=rows(("site", "site-hangzhou", False, "站点维护")),
        force_off_reason="紧急止血",
        all_tools=TOOLS,
    )
    assert decision.enabled is False
    assert decision.source is DecisionSource.FORCE_OFF
    assert decision.reason == "紧急止血"


def test_force_off_ignores_rows_entirely() -> None:
    """The strong branch must not consult anything: give it a mapping that raises."""

    class Exploding(dict):
        def get(self, key: object, default: object = None) -> object:
            raise AssertionError("force off must not read switches")

    decision = evaluate(CALLER, force_off=True, rows=Exploding(), all_tools=TOOLS)
    assert decision.enabled is False


def test_no_rows_means_usable() -> None:
    decision = evaluate(CALLER, force_off=False, rows={}, all_tools=TOOLS)
    assert decision.enabled is True
    assert decision.source is DecisionSource.NONE
    assert decision.tools == TOOLS


def test_global_off_beats_site_role_and_tool() -> None:
    decision = evaluate(
        CALLER,
        force_off=False,
        rows=rows(
            ("global", "", False, "全员停用"),
            ("site", "site-hangzhou", False, "站点停用"),
            ("tool", "sop_draft", False, "工具停用"),
        ),
        all_tools=TOOLS,
    )
    assert decision.source is DecisionSource.GLOBAL
    assert decision.reason == "全员停用"


def test_site_off_beats_role_and_tool() -> None:
    decision = evaluate(
        CALLER,
        force_off=False,
        rows=rows(
            ("site", "site-hangzhou", False, "站点停用"),
            ("role", "operation", False, "角色停用"),
            ("tool", "sop_draft", False, "工具停用"),
        ),
        all_tools=TOOLS,
    )
    assert decision.source is DecisionSource.SITE
    assert decision.scope_ref == "site-hangzhou"


def test_role_off_beats_tool() -> None:
    decision = evaluate(
        CALLER,
        force_off=False,
        rows=rows(
            ("role", "operation", False, "角色停用"),
            ("tool", "sop_draft", False, "工具停用"),
        ),
        all_tools=TOOLS,
    )
    assert decision.source is DecisionSource.ROLE
    assert decision.scope_ref == "operation"


def test_tool_off_only_blocks_that_tool() -> None:
    base = rows(("tool", "sop_draft", False, "工具停用"))
    wants_blocked = replace(CALLER, tool="sop_draft")
    wants_allowed = replace(CALLER, tool="knowledge_search")
    blocked = evaluate(wants_blocked, force_off=False, rows=base, all_tools=TOOLS)
    allowed = evaluate(wants_allowed, force_off=False, rows=base, all_tools=TOOLS)
    assert blocked.enabled is False
    assert blocked.source is DecisionSource.TOOL
    assert blocked.tools == ("knowledge_search",)
    assert allowed.enabled is True
    assert allowed.blocked_tools == ("sop_draft",)


def test_enabled_true_row_is_not_a_block() -> None:
    decision = evaluate(
        CALLER,
        force_off=False,
        rows=rows(("site", "site-hangzhou", True, "临时恢复")),
        all_tools=TOOLS,
    )
    assert decision.enabled is True


def test_dual_site_user_is_refused_when_any_site_is_off() -> None:
    dual = CallerContext(
        user_id="u-1005",
        roles=("operation",),
        site_ids=("site-hangzhou", "site-suzhou"),
    )
    decision = evaluate(
        dual,
        force_off=False,
        rows=rows(("site", "site-suzhou", False, "苏州站点维护")),
        all_tools=TOOLS,
    )
    assert decision.enabled is False
    assert decision.scope_ref == "site-suzhou"


@pytest.mark.parametrize(("value", "expected"), [
    ("true", True), ("TRUE", True), ("1", True), ("yes", True), ("on", True),
    ("false", False), ("0", False), ("", False), ("off", False),
])
def test_force_off_parsing(value: str, expected: bool) -> None:
    assert force_off_enabled({"ASST_FORCE_OFF": value}) is expected


def test_force_off_reason_is_capped_at_200() -> None:
    assert len(force_off_reason({"ASST_FORCE_OFF_REASON": "x" * 500})) == 200
