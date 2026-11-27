"""Platform message-centre stub (the real channel is a separate integration).

The switch service must announce every change to the administrator group. In
this delivery the transport is an in-process outbox that the tests inspect; the
production adapter posts the same envelope to the platform notification API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

ADMIN_GROUP = "group:platform-admins"
MODULE_ASSISTANT = "assistant"
MODULE_RECRUITMENT = "recruitment"


@dataclass(frozen=True)
class Notification:
    module: str
    group: str
    title: str
    body: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class NotificationOutbox:
    """Collects notifications in memory; one instance per app."""

    def __init__(self) -> None:
        self.messages: list[Notification] = []

    def clear(self) -> None:
        self.messages.clear()

    def send(self, notification: Notification) -> None:
        self.messages.append(notification)

    def latest(self, module: str | None = None) -> Notification | None:
        for item in reversed(self.messages):
            if module is None or item.module == module:
                return item
        return None
