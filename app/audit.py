"""S2-AUDIT client boundary.

The real audit service is another ticket; this module is the seam. It records
into the local audit_log table and exposes the exact call the production
adapter would make, so swapping in the HTTP client later touches one file.
"""

from __future__ import annotations

from dataclasses import dataclass

MODULE_ASSISTANT = "assistant"
MODULE_RECRUITMENT = "recruitment"

ACTION_SWITCH_SEED = "assistant.switch.seed"
ACTION_SWITCH_ENABLE = "assistant.switch.enable"
ACTION_SWITCH_DISABLE = "assistant.switch.disable"
ACTION_SWITCH_UPDATE = "assistant.switch.update"
ACTION_CHAT_DENIED = "assistant.chat.denied"
ACTION_TOOL_DENIED = "assistant.tool.denied"

RECRUIT_ACTION_SWITCH_UPDATE = "recruitment.switch.update"
RECRUIT_ACTION_TASK_RUN = "recruitment.task.run"


@dataclass(frozen=True)
class AuditEvent:
    """What the platform audit service would receive."""

    module: str
    action: str
    target: str
    actor: str
    detail: str = ""
    org_id: str = ""


def action_for(scope: str, enabled: bool, created: bool) -> str:
    """Audit verbs are stable strings; the module lives in the prefix."""

    if created:
        return ACTION_SWITCH_SEED
    return ACTION_SWITCH_ENABLE if enabled else ACTION_SWITCH_DISABLE
