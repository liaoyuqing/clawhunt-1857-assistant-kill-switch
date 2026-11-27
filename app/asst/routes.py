"""HTTP surface of the assistant kill switch: status, chat, tools, admin."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app import sse
from app.deps import Container, current_principal, get_container, require_system_admin
from app.errors import (
    ASSISTANT_DISABLED,
    SWITCH_INVALID,
    TOOL_UNAVAILABLE,
    ApiError,
)
from app.repository import validate_switch_payload
from app.resolver import ASSISTANT_TOOLS, decision_http_status
from app.switch_domain import CallerContext, Decision, SwitchScope

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

UNAVAILABLE_HEADERS = {"Retry-After": "5", "Cache-Control": "no-store"}
SSE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


class ChatRequest(BaseModel):
    prompt: str = Field(default="", max_length=4000)
    stream: bool = False
    tool: str | None = Field(default=None, max_length=64)


class ToolInvokeRequest(BaseModel):
    arguments: dict[str, object] = Field(default_factory=dict)


class SwitchRequest(BaseModel):
    scope: Literal["global", "site", "role", "tool"]
    scope_ref: str = Field(default="", max_length=64)
    enabled: bool
    reason: str = Field(default="", max_length=200)


def _container(request: Request) -> Container:
    return get_container(request)


def _deny(decision: Decision) -> JSONResponse:
    """Uniform refusal body for the two "off" verdicts."""

    if decision.source.value == "tool":
        return JSONResponse(
            status_code=409,
            content={
                "code": TOOL_UNAVAILABLE,
                "message": decision.message,
                "tool": decision.scope_ref,
                "reason": decision.reason,
            },
            headers=UNAVAILABLE_HEADERS,
        )
    return JSONResponse(
        status_code=decision_http_status(decision),
        content={
            "code": ASSISTANT_DISABLED,
            "message": decision.message,
            "source": decision.source.value,
            "scope_ref": decision.scope_ref,
            "reason": decision.reason,
        },
        headers=UNAVAILABLE_HEADERS,
    )


@router.get("/status")
def assistant_status(request: Request) -> JSONResponse:
    container = _container(request)
    principal = current_principal(request)
    assert container.assistant is not None
    payload = container.assistant.status_payload(principal.caller())
    payload["user_id"] = principal.user_id
    payload["site_ids"] = list(principal.site_ids)
    return JSONResponse(status_code=200, content=payload, headers={"Cache-Control": "no-store"})


@router.post("/chat")
def assistant_chat(request: Request, body: ChatRequest) -> Response:
    container = _container(request)
    principal = current_principal(request)
    caller: CallerContext = principal.caller(body.tool)

    if body.tool and body.tool not in ASSISTANT_TOOLS:
        raise ApiError(404, TOOL_UNAVAILABLE, f"未知工具: {body.tool}")

    assert container.assistant is not None
    decision = container.assistant.decision_for(caller, body.tool)
    if not decision.enabled:
        return _deny(decision)

    if body.stream:
        report = sse.StreamReport()
        container.stream_reports.append(report)
        generator = sse.stream_chat(
            container.assistant.resolver,
            principal.caller(),
            body.prompt,
            container.settings,
            report,
        )
        return StreamingResponse(generator, media_type="text/event-stream", headers=SSE_HEADERS)

    return JSONResponse(
        status_code=200,
        content={
            "reply": f"（虚构示例回复）已收到关于“{body.prompt[:40]}”的提问，建议先查阅知识库中的会员服务 SOP。",
            "model": "member-space-assistant-v1",
            "tools": list(decision.tools),
            "enabled": True,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/tools")
def assistant_tools(request: Request) -> JSONResponse:
    container = _container(request)
    principal = current_principal(request)
    assert container.assistant is not None
    state = container.assistant.resolver.tools_state(principal.caller())
    decision = container.assistant.decision_for(principal.caller())
    return JSONResponse(
        status_code=200,
        content={
            "enabled": decision.enabled,
            "tools": state["tools"],
            "blocked_tools": state["blocked_tools"],
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/tools/{tool_name}/invoke")
def assistant_tool_invoke(tool_name: str, request: Request, body: ToolInvokeRequest) -> JSONResponse:
    container = _container(request)
    principal = current_principal(request)
    if tool_name not in ASSISTANT_TOOLS:
        raise ApiError(404, TOOL_UNAVAILABLE, f"未知工具: {tool_name}")

    assert container.assistant is not None
    decision = container.assistant.decision_for(principal.caller(), tool_name)
    if not decision.enabled:
        return _deny(decision)

    return JSONResponse(
        status_code=200,
        content={
            "tool": tool_name,
            "status": "ok",
            "result": {"note": "（虚构示例）工具执行结果", "arguments": body.arguments},
        },
    )


@router.get("/admin/switches")
def admin_list_switches(request: Request) -> JSONResponse:
    container = _container(request)
    require_system_admin(request)
    assert container.assistant is not None
    switches = container.assistant.list_switches()
    return JSONResponse(
        status_code=200,
        content={"items": switches, "count": len(switches)},
        headers={"Cache-Control": "no-store"},
    )


@router.put("/admin/switches")
def admin_put_switch(request: Request, body: SwitchRequest) -> JSONResponse:
    container = _container(request)
    principal = require_system_admin(request)
    try:
        validate_switch_payload(body.scope, body.scope_ref, body.enabled, body.reason)
    except ValueError as exc:
        raise ApiError(422, SWITCH_INVALID, str(exc)) from exc

    site_id = body.scope_ref if body.scope == SwitchScope.SITE.value else ""
    assert container.assistant is not None
    payload, created = container.assistant.apply_switch(
        scope=body.scope,
        scope_ref=body.scope_ref,
        enabled=body.enabled,
        reason=body.reason,
        actor=principal.user_id,
        org_id=principal.org_id,
        site_id=site_id,
    )
    payload["created"] = created
    return JSONResponse(
        status_code=201 if created else 200,
        content=payload,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/admin/audit")
def admin_audit(request: Request) -> JSONResponse:
    container = _container(request)
    require_system_admin(request)
    from app.repository import list_audit

    with container.db.read() as session:
        entries = list_audit(session, "assistant")
    return JSONResponse(
        status_code=200,
        content={
            "items": [
                {
                    "action": item.action,
                    "target": item.target,
                    "actor": item.actor,
                    "detail": item.detail,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                }
                for item in entries
            ]
        },
    )


@router.get("/admin/notifications")
def admin_notifications(request: Request) -> JSONResponse:
    container = _container(request)
    require_system_admin(request)
    messages = [
        {"module": m.module, "group": m.group, "title": m.title, "body": m.body}
        for m in container.outbox.messages
    ]
    return JSONResponse(status_code=200, content={"items": messages, "count": len(messages)})


@router.get("/admin/cache-keys")
def admin_cache_keys(request: Request) -> JSONResponse:
    """Operators debugging a stuck instance can see exactly which keys exist."""

    container = _container(request)
    require_system_admin(request)
    return JSONResponse(
        status_code=200,
        content={
            "backend": container.cache.backend,
            "generation": container.cache.generation(),
            "ttl_seconds": container.settings.cache_ttl_seconds,
        },
    )