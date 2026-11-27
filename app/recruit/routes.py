"""Minimal recruitment AI task API used to prove module isolation."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.audit import MODULE_RECRUITMENT, RECRUIT_ACTION_SWITCH_UPDATE
from app.deps import Container, current_principal, get_container, require_system_admin
from app.errors import RECRUITMENT_AI_DISABLED, SWITCH_INVALID, ApiError
from app.models import RecruitSwitch
from app.repository import write_audit

router = APIRouter(prefix="/api/recruitment/ai", tags=["recruitment-ai"])

TASKS: tuple[dict[str, object], ...] = (
    {
        "id": "rt-2001",
        "title": "杭州会员企业智能制造专题招商线索复核",
        "site_id": "site-hangzhou",
        "status": "queued",
        "leads": 12,
    },
    {
        "id": "rt-2002",
        "title": "苏州会员企业供应链企业画像补全",
        "site_id": "site-suzhou",
        "status": "queued",
        "leads": 8,
    },
)


class RecruitSwitchRequest(BaseModel):
    enabled: bool
    reason: str = Field(default="", max_length=200)


def _container(request: Request) -> Container:
    return get_container(request)


def recruitment_ai_enabled(container: Container) -> tuple[bool, str]:
    """Recruitment AI reads its own switch only; ASST_FORCE_OFF never reaches it."""

    with container.db.read() as session:
        row = session.scalars(
            select(RecruitSwitch).where(RecruitSwitch.deleted_at.is_(None)).limit(1)
        ).first()
    if row is None:
        return True, ""
    return bool(row.enabled), row.reason


def _deny() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "code": RECRUITMENT_AI_DISABLED,
            "message": "招商 AI 已被管理员关闭，暂时无法使用。",
        },
        headers={"Retry-After": "5"},
    )


@router.get("/tasks")
def list_tasks(request: Request) -> JSONResponse:
    container = _container(request)
    current_principal(request)
    enabled, reason = recruitment_ai_enabled(container)
    if not enabled:
        return _deny()
    return JSONResponse(
        status_code=200,
        content={"items": list(TASKS), "count": len(TASKS), "module": "recruitment"},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/tasks/{task_id}/run")
def run_task(task_id: str, request: Request) -> JSONResponse:
    container = _container(request)
    principal = current_principal(request)
    enabled, reason = recruitment_ai_enabled(container)
    if not enabled:
        return _deny()
    task = next((item for item in TASKS if item["id"] == task_id), None)
    if task is None:
        raise ApiError(404, "TASK_NOT_FOUND", f"任务不存在: {task_id}")
    return JSONResponse(
        status_code=200,
        content={
            "task": task,
            "status": "running",
            "module": "recruitment",
            "requested_by": principal.user_id,
            "result": {"leads_scored": task["leads"], "note": "（虚构示例）线索评分已排队"},
        },
    )


@router.get("/admin/switch")
def get_switch(request: Request) -> JSONResponse:
    container = _container(request)
    require_system_admin(request)
    enabled, reason = recruitment_ai_enabled(container)
    return JSONResponse(status_code=200, content={"enabled": enabled, "reason": reason})


@router.put("/admin/switch")
def put_switch(request: Request, body: RecruitSwitchRequest) -> JSONResponse:
    container = _container(request)
    principal = require_system_admin(request)
    if not body.enabled and not body.reason.strip():
        raise ApiError(422, SWITCH_INVALID, "关闭时 reason 必填")
    with container.db.read() as session:
        row = session.scalars(select(RecruitSwitch).limit(1)).first()
        if row is None:
            row = RecruitSwitch(enabled=body.enabled, reason=body.reason, changed_by=principal.user_id,
                                org_id=principal.org_id, created_by=principal.user_id)
            session.add(row)
        else:
            row.enabled = body.enabled
            row.reason = body.reason
            row.changed_by = principal.user_id
        write_audit(
            session,
            module=MODULE_RECRUITMENT,
            action=RECRUIT_ACTION_SWITCH_UPDATE,
            target="recruitment-ai",
            actor=principal.user_id,
            detail=f"enabled={body.enabled} reason={body.reason}",
            org_id=principal.org_id,
        )
        session.commit()
    return JSONResponse(status_code=200, content={"enabled": body.enabled, "reason": body.reason})


@router.get("/admin/audit")
def admin_audit(request: Request) -> JSONResponse:
    container = _container(request)
    require_system_admin(request)
    from app.repository import list_audit

    with container.db.read() as session:
        entries = list_audit(session, MODULE_RECRUITMENT)
    return JSONResponse(
        status_code=200,
        content={"items": [{"action": item.action, "actor": item.actor, "detail": item.detail}
                           for item in entries]},
    )
