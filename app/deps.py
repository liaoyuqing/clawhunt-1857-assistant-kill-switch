"""Request-scoped dependencies: authentication and the service container."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fastapi import Request

from app.cache import SwitchCache
from app.config import Settings
from app.db import Database
from app.errors import SWITCH_FORBIDDEN, UNAUTHENTICATED, ApiError
from app.iam import IamDirectory, Principal
from app.notify import NotificationOutbox

if TYPE_CHECKING:
    from app.asst.switch import AssistantSwitchService
    from app.sse import StreamReport


@dataclass
class Container:
    """Everything the routes need, assembled once in create_app()."""

    settings: Settings
    db: Database
    cache: SwitchCache
    iam: IamDirectory
    outbox: NotificationOutbox
    assistant: AssistantSwitchService | None = None
    stream_reports: list[StreamReport] = field(default_factory=list)


def get_container(request: Request) -> Container:
    container = getattr(request.app.state, "container", None)
    if container is None:  # pragma: no cover - create_app always sets it
        raise RuntimeError("application container is not initialised")
    return container


def bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise ApiError(401, UNAUTHENTICATED, "缺少 Bearer 令牌")
    return value.strip()


def current_principal(request: Request) -> Principal:
    container = get_container(request)
    principal = container.iam.authenticate(bearer_token(request))
    if principal is None:
        raise ApiError(401, UNAUTHENTICATED, "令牌无效或已过期")
    return principal


def require_system_admin(request: Request) -> Principal:
    principal = current_principal(request)
    if not principal.is_system_admin:
        raise ApiError(403, SWITCH_FORBIDDEN, "仅系统管理员可以管理助手开关")
    return principal


def optional_principal(request: Request) -> Principal | None:
    """Public status probing still needs identity when a token is supplied."""

    header = request.headers.get("authorization")
    if not header:
        return None
    return current_principal(request)
