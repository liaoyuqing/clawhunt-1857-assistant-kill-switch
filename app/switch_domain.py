"""Pure decision logic for the four-level assistant kill switch.

No I/O happens in this module: it takes the environment flag and the rows that
were already loaded, and returns the decision. That keeps the ordering rule
(force_off -> global -> site -> role -> tool) testable without a database.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class SwitchScope(StrEnum):
    GLOBAL = "global"
    SITE = "site"
    ROLE = "role"
    TOOL = "tool"


SCOPE_ORDER: tuple[SwitchScope, ...] = (
    SwitchScope.GLOBAL,
    SwitchScope.SITE,
    SwitchScope.ROLE,
    SwitchScope.TOOL,
)

# Scope references are site ids, role codes and tool names; "global" has none.
SCOPE_REF_MAX_LENGTH = 64
REASON_MAX_LENGTH = 200


class DecisionSource(StrEnum):
    """Where the "off" verdict came from; mirrored into the API answer."""

    FORCE_OFF = "force_off"
    GLOBAL = "global"
    SITE = "site"
    ROLE = "role"
    TOOL = "tool"
    STATE_UNAVAILABLE = "state_unavailable"
    NONE = "none"


@dataclass(frozen=True)
class CallerContext:
    """Who is asking: a user, its roles, its sites and the tool it wants to call."""

    user_id: str
    org_id: str = ""
    roles: tuple[str, ...] = ()
    site_ids: tuple[str, ...] = ()
    tool: str | None = None


@dataclass(frozen=True)
class Decision:
    """Result of the evaluation; `source` names the level that turned it off."""

    enabled: bool
    source: DecisionSource
    scope_ref: str = ""
    reason: str = ""
    message: str = ""
    tools: tuple[str, ...] = ()
    blocked_tools: tuple[str, ...] = ()
    details: Mapping[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "source": self.source.value,
            "scope_ref": self.scope_ref,
            "reason": self.reason,
            "message": self.message,
            "tools": list(self.tools),
            "blocked_tools": list(self.blocked_tools),
        }


DEFAULT_OFF_MESSAGE = "员工 AI 助手已被管理员关闭，暂时无法使用；如需恢复请联系系统管理员。"
SITE_OFF_MESSAGE = "您所在的站点已关闭员工 AI 助手，暂时无法使用。"
ROLE_OFF_MESSAGE = "您所属的角色已被停用员工 AI 助手，暂时无法使用。"
TOOL_OFF_MESSAGE = "该工具已被管理员停用，暂时无法使用。"
STATE_UNAVAILABLE_MESSAGE = "助手开关状态暂时无法读取（数据库与缓存均不可用），为安全起见助手暂停服务，请稍后重试。"
TOOL_BLOCKED_MESSAGE = "工具 {tool} 已被管理员停用，无法调用。"


def is_switch_off(row: object) -> bool:
    """A row turns the scope off when it exists and is explicitly disabled."""

    return getattr(row, "enabled", True) is False


def evaluate(
    caller: CallerContext,
    *,
    force_off: bool,
    rows: Mapping[tuple[str, str], object],
    force_off_reason: str = "",
    all_tools: tuple[str, ...] = (),
) -> Decision:
    """Apply the fixed order and stop at the first level that is off.

    `rows` maps (scope, scope_ref) to a record carrying `enabled` and `reason`.
    Only rows already present in that mapping are consulted, so the caller can
    decide how much to load - and the force-off path never gets here at all.
    """

    if force_off:
        return Decision(
            enabled=False,
            source=DecisionSource.FORCE_OFF,
            reason=force_off_reason,
            message=DEFAULT_OFF_MESSAGE,
            blocked_tools=tuple(all_tools),
        )

    global_row = rows.get((SwitchScope.GLOBAL.value, ""))
    if global_row is not None and is_switch_off(global_row):
        return Decision(
            enabled=False,
            source=DecisionSource.GLOBAL,
            scope_ref="",
            reason=str(getattr(global_row, "reason", "")),
            message=DEFAULT_OFF_MESSAGE,
            blocked_tools=tuple(all_tools),
        )

    for site in caller.site_ids:
        row = rows.get((SwitchScope.SITE.value, site))
        if row is not None and is_switch_off(row):
            return Decision(
                enabled=False,
                source=DecisionSource.SITE,
                scope_ref=site,
                reason=str(getattr(row, "reason", "")),
                message=SITE_OFF_MESSAGE,
                blocked_tools=tuple(all_tools),
            )

    for role in caller.roles:
        row = rows.get((SwitchScope.ROLE.value, role))
        if row is not None and is_switch_off(row):
            return Decision(
                enabled=False,
                source=DecisionSource.ROLE,
                scope_ref=role,
                reason=str(getattr(row, "reason", "")),
                message=ROLE_OFF_MESSAGE,
                blocked_tools=tuple(all_tools),
            )

    blocked: list[str] = []
    for tool in all_tools:
        row = rows.get((SwitchScope.TOOL.value, tool))
        if row is not None and is_switch_off(row):
            blocked.append(tool)

    if caller.tool and caller.tool in blocked:
        return Decision(
            enabled=False,
            source=DecisionSource.TOOL,
            scope_ref=caller.tool,
            reason=str(getattr(rows[(SwitchScope.TOOL.value, caller.tool)], "reason", "")),
            message=TOOL_BLOCKED_MESSAGE.format(tool=caller.tool),
            tools=tuple(t for t in all_tools if t not in blocked),
            blocked_tools=tuple(blocked),
        )

    return Decision(
        enabled=True,
        source=DecisionSource.NONE,
        message="员工 AI 助手可用。",
        tools=tuple(t for t in all_tools if t not in blocked),
        blocked_tools=tuple(blocked),
    )
