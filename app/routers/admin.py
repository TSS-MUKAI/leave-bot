from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ui
from app.auth import require_admin
from app.db.models import Approval, Department, LeaveAudit, LeaveRequest, User
from app.db.session import get_db
from app.mattermost import get_mattermost
from app.services import departments as dept_svc
from app.services import leave as leave_svc
from app.services import users as user_svc

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

templates = Jinja2Templates(directory="app/templates")


def _jst_filter(dt: Any) -> str:
    return _fmt(dt) if dt is not None else ""


templates.env.filters["jst"] = _jst_filter


ROLES = [
    {"value": "employee", "label": "申請者"},
    {"value": "manager", "label": "承認者"},
    {"value": "hr", "label": "最終承認者"},
    {"value": "admin", "label": "admin"},
]

STATUS_JP = {
    "pending": "承認待ち",
    "approved": "承認済",
    "rejected": "却下",
    "canceled": "取消",
}


def _render(
    request: Request, name: str, ctx: dict[str, Any] | None = None
) -> HTMLResponse:
    data: dict[str, Any] = {"flash": None, "nav_active": None}
    if ctx:
        data.update(ctx)
    return templates.TemplateResponse(request, name, data)


def _user_with_refs(db: Session, user: User) -> dict[str, Any]:
    resolved_mgr_id = user_svc.resolve_manager(db, user)
    individual_proxies = user_svc.list_user_proxies(db, user.mm_user_id)
    resolved_proxy_ids = user_svc.resolve_proxies(db, user)
    proxies = [db.get(User, pid) for pid in resolved_proxy_ids]
    proxies = [p for p in proxies if p is not None]
    return {
        "mm_user_id": user.mm_user_id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
        "manager_mm_id": user.manager_mm_id,
        "manager": db.get(User, resolved_mgr_id) if resolved_mgr_id else None,
        "proxies": proxies,
        "department": db.get(Department, user.department_id) if user.department_id else None,
        "manager_is_individual": bool(user.manager_mm_id),
        "proxy_is_individual": bool(individual_proxies),
    }


# --- Routes -----------------------------------------------------------
@router.get("/", name="admin_index", response_class=HTMLResponse)
def index() -> RedirectResponse:
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)


@router.get("/users", name="admin_users", response_class=HTMLResponse)
def users_list(
    request: Request,
    synced: str = "",
    added: int = 0,
    updated: int = 0,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    users = [_user_with_refs(db, u) for u in user_svc.list_all(db)]
    flash = None
    if synced:
        flash = {
            "level": "success",
            "message": f"Mattermost 同期完了: 追加 {added} / 更新 {updated}",
        }
    return _render(
        request,
        "admin/users.html",
        {"nav_active": "users", "users": users, "flash": flash},
    )


@router.post("/users/sync", name="admin_users_sync")
def users_sync(db: Session = Depends(get_db)) -> RedirectResponse:
    mm = get_mattermost()
    try:
        mm_users = mm.list_active_users()
    except Exception:
        log.exception("mattermost users sync failed")
        raise HTTPException(status_code=502, detail="Mattermost ユーザ取得に失敗しました")
    added = updated = 0
    for u in mm_users:
        existing = db.get(User, u["id"])
        user_svc.upsert_user(
            db,
            mm_user_id=u["id"],
            username=u.get("username", ""),
            display_name=ui.mm_display_name(u),
            email=u.get("email"),
        )
        if existing is None:
            added += 1
        else:
            updated += 1
    log.info("users sync: added=%s updated=%s", added, updated)
    return RedirectResponse(
        url=f"/admin/users?synced=1&added={added}&updated={updated}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/users/add", name="admin_add_user", response_class=HTMLResponse)
def add_user_form(request: Request) -> HTMLResponse:
    return _render(request, "admin/add_user.html", {"nav_active": "users"})


@router.post("/users/add", name="admin_add_user_submit")
def add_user_submit(
    request: Request,
    username: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    uname = username.strip().lstrip("@")
    if not uname:
        return _render(
            request,
            "admin/add_user.html",
            {"nav_active": "users", "flash": {"level": "danger", "message": "ユーザ名を入力してください"}},
        )
    mm = get_mattermost()
    try:
        mmu = mm.get_user_by_username(uname)
    except Exception as e:
        log.exception("mattermost lookup failed")
        return _render(
            request,
            "admin/add_user.html",
            {"nav_active": "users", "flash": {"level": "danger", "message": f"Mattermost 検索失敗: {e}"}},
        )
    if not mmu:
        return _render(
            request,
            "admin/add_user.html",
            {"nav_active": "users", "flash": {"level": "warning", "message": f"ユーザ @{uname} が見つかりません"}},
        )
    user_svc.upsert_user(
        db,
        mm_user_id=mmu["id"],
        username=mmu.get("username", uname),
        display_name=ui.mm_display_name(mmu),
        email=mmu.get("email"),
    )
    return RedirectResponse(
        url=f"/admin/users/{mmu['id']}/edit", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/users/{user_id}/edit", name="admin_user_edit", response_class=HTMLResponse)
def user_edit(
    user_id: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    candidates = [
        c for c in user_svc.list_all(db) if c.mm_user_id != user_id and c.is_active
    ]
    resolved_mgr_id = user_svc.resolve_manager(db, user)
    individual_proxy_ids = user_svc.list_user_proxies(db, user_id)
    resolved_proxy_ids = user_svc.resolve_proxies(db, user)
    resolved_proxies = [db.get(User, pid) for pid in resolved_proxy_ids]
    resolved_proxies = [p for p in resolved_proxies if p is not None]
    return _render(
        request,
        "admin/user_edit.html",
        {
            "nav_active": "users",
            "user": user,
            "candidates": candidates,
            "roles": ROLES,
            "departments": dept_svc.list_all(db),
            "resolved_manager": db.get(User, resolved_mgr_id) if resolved_mgr_id else None,
            "individual_proxy_ids": individual_proxy_ids,
            "resolved_proxies": resolved_proxies,
        },
    )


@router.post("/users/{user_id}/edit", name="admin_user_update")
def user_update(
    user_id: str,
    request: Request,
    role: str = Form("employee"),
    department_id: str = Form(""),
    manager_mm_id: str = Form(""),
    proxy_mm_ids: list[str] = Form(default_factory=list),
    is_active: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    valid_roles = {r["value"] for r in ROLES}
    if role not in valid_roles:
        raise HTTPException(status_code=400, detail="invalid role")
    if manager_mm_id and db.get(User, manager_mm_id) is None:
        raise HTTPException(status_code=400, detail="manager not found")
    if manager_mm_id == user_id:
        raise HTTPException(status_code=400, detail="cannot self-assign manager")
    cleaned_proxies: list[str] = []
    for pid in proxy_mm_ids:
        pid = pid.strip()
        if not pid:
            continue
        if pid == user_id:
            raise HTTPException(status_code=400, detail="cannot set self as proxy")
        if db.get(User, pid) is None:
            raise HTTPException(status_code=400, detail=f"proxy user {pid} not found")
        cleaned_proxies.append(pid)
    dept_id_int: int | None = None
    if department_id:
        try:
            dept_id_int = int(department_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid department_id")
        if dept_svc.get(db, dept_id_int) is None:
            raise HTTPException(status_code=400, detail="department not found")
    user_svc.update_user(
        db,
        mm_user_id=user_id,
        role=role,
        manager_mm_id=manager_mm_id,
        department_id=dept_id_int,
        is_active=bool(is_active),
    )
    user_svc.set_user_proxies(db, user_id, cleaned_proxies)
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/departments", name="admin_depts", response_class=HTMLResponse)
def depts_list(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    depts = dept_svc.list_all(db)
    rows = []
    for d in depts:
        proxy_ids = dept_svc.list_proxies(db, d.id)
        proxies = [db.get(User, pid) for pid in proxy_ids]
        proxies = [p for p in proxies if p is not None]
        rows.append(
            {
                "id": d.id,
                "name": d.name,
                "manager": db.get(User, d.manager_mm_id) if d.manager_mm_id else None,
                "proxies": proxies,
                "member_count": dept_svc.member_count(db, d.id),
            }
        )
    return _render(
        request, "admin/departments.html", {"nav_active": "depts", "rows": rows}
    )


@router.get("/departments/add", name="admin_dept_add", response_class=HTMLResponse)
def dept_add_form(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return _render(
        request,
        "admin/department_edit.html",
        {
            "nav_active": "depts",
            "dept": None,
            "candidates": user_svc.list_all(db),
            "proxy_ids": [],
            "form_action": "/admin/departments/add",
        },
    )


@router.post("/departments/add")
def dept_add_submit(
    request: Request,
    name: str = Form(...),
    manager_mm_id: str = Form(""),
    proxy_mm_ids: list[str] = Form(default_factory=list),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="部署名は必須です")
    if dept_svc.get_by_name(db, name) is not None:
        return _render(
            request,
            "admin/department_edit.html",
            {
                "nav_active": "depts",
                "dept": None,
                "candidates": user_svc.list_all(db),
                "proxy_ids": proxy_mm_ids,
                "form_action": "/admin/departments/add",
                "flash": {"level": "danger", "message": f"部署名「{name}」は既に存在します"},
            },
        )
    d = dept_svc.create(
        db,
        name=name,
        manager_mm_id=manager_mm_id or None,
    )
    cleaned = [p for p in proxy_mm_ids if p and db.get(User, p) is not None]
    dept_svc.set_proxies(db, d.id, cleaned)
    return RedirectResponse(url="/admin/departments", status_code=status.HTTP_303_SEE_OTHER)


@router.get(
    "/departments/{department_id}/edit",
    name="admin_dept_edit",
    response_class=HTMLResponse,
)
def dept_edit_form(
    department_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    dept = dept_svc.get(db, department_id)
    if dept is None:
        raise HTTPException(status_code=404, detail="department not found")
    return _render(
        request,
        "admin/department_edit.html",
        {
            "nav_active": "depts",
            "dept": dept,
            "candidates": user_svc.list_all(db),
            "proxy_ids": dept_svc.list_proxies(db, department_id),
            "form_action": f"/admin/departments/{department_id}/edit",
        },
    )


@router.post("/departments/{department_id}/edit")
def dept_edit_submit(
    department_id: int,
    name: str = Form(...),
    manager_mm_id: str = Form(""),
    proxy_mm_ids: list[str] = Form(default_factory=list),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="部署名は必須です")
    existing = dept_svc.get_by_name(db, name)
    if existing and existing.id != department_id:
        raise HTTPException(status_code=400, detail=f"部署名「{name}」は既に存在します")
    dept_svc.update(
        db,
        id=department_id,
        name=name,
        manager_mm_id=manager_mm_id,
    )
    cleaned = [p for p in proxy_mm_ids if p and db.get(User, p) is not None]
    dept_svc.set_proxies(db, department_id, cleaned)
    return RedirectResponse(
        url="/admin/departments", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/departments/{department_id}/delete", name="admin_dept_delete")
def dept_delete(
    department_id: int, db: Session = Depends(get_db)
) -> RedirectResponse:
    dept_svc.delete(db, department_id)
    return RedirectResponse(
        url="/admin/departments", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/requests", name="admin_requests", response_class=HTMLResponse)
def requests_list(
    request: Request,
    status: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    stmt = select(LeaveRequest).order_by(LeaveRequest.id.desc())
    if status:
        stmt = stmt.where(LeaveRequest.status == status)
    reqs = list(db.scalars(stmt))
    rows = []
    for r in reqs:
        applicant = db.get(User, r.user_id)
        period = (
            f"{r.start_date}"
            if r.start_date == r.end_date
            else f"{r.start_date} 〜 {r.end_date}"
        )
        rows.append(
            {
                "id": r.id,
                "applicant": applicant,
                "type_jp": ui.LEAVE_TYPE_JP.get(r.leave_type, r.leave_type),
                "period": period,
                "business_days": r.business_days,
                "status": r.status,
                "status_jp": STATUS_JP.get(r.status, r.status),
                "current_stage": r.current_stage,
                "created_at": _fmt(r.created_at),
            }
        )
    return _render(
        request,
        "admin/requests.html",
        {"nav_active": "requests", "rows": rows, "status": status},
    )


@router.get(
    "/requests/{request_id}",
    name="admin_request_detail",
    response_class=HTMLResponse,
)
def request_detail(
    request_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    req = db.get(LeaveRequest, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="request not found")
    applicant = db.get(User, req.user_id)
    approvals = list(
        db.scalars(
            select(Approval)
            .where(Approval.request_id == request_id)
            .order_by(Approval.id)
        )
    )
    audit = list(
        db.scalars(
            select(LeaveAudit)
            .where(LeaveAudit.request_id == request_id)
            .order_by(LeaveAudit.id)
        )
    )
    approvals_ctx = [
        {
            "stage": a.stage,
            "role": a.role,
            "status": a.status,
            "comment": a.comment,
            "decided_at": _fmt(a.decided_at) if a.decided_at else None,
            "approver": db.get(User, a.approver_id) if a.approver_id else None,
        }
        for a in approvals
    ]
    audit_ctx = [
        {
            "created_at": _fmt(e.created_at),
            "action": e.action,
            "payload": e.payload,
            "actor": db.get(User, e.actor_id) if e.actor_id else None,
        }
        for e in audit
    ]
    return _render(
        request,
        "admin/request_detail.html",
        {
            "nav_active": "requests",
            "req": req,
            "applicant": applicant,
            "type_jp": ui.LEAVE_TYPE_JP.get(req.leave_type, req.leave_type),
            "status_jp": STATUS_JP.get(req.status, req.status),
            "approvals": approvals_ctx,
            "audit": audit_ctx,
        },
    )


@router.post(
    "/requests/{request_id}/override/approve",
    name="admin_request_override_approve",
)
def request_override_approve(
    request_id: int,
    admin_user: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    try:
        result = leave_svc.decide(
            db,
            request_id=request_id,
            actor_id=None,
            decision="approved",
            admin_override=True,
            admin_label=admin_user,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    from app.routers.interactive import after_decision

    after_decision(db, result, actor_id=None, admin_label=admin_user)
    return RedirectResponse(
        url=f"/admin/requests/{request_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post(
    "/requests/{request_id}/override/cancel",
    name="admin_request_override_cancel",
)
def request_override_cancel(
    request_id: int,
    reason: str = Form(""),
    admin_user: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    try:
        result = leave_svc.cancel_request(
            db,
            request_id=request_id,
            actor_id=None,
            reason=reason.strip() or None,
            admin_override=True,
            admin_label=admin_user,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    from app.routers.interactive import after_cancel

    after_cancel(db, result, actor_id=None, admin_label=admin_user)
    return RedirectResponse(
        url=f"/admin/requests/{request_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post(
    "/requests/{request_id}/override/reject",
    name="admin_request_override_reject",
)
def request_override_reject(
    request_id: int,
    comment: str = Form(...),
    admin_user: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if not comment.strip():
        raise HTTPException(status_code=400, detail="却下理由は必須です")
    try:
        result = leave_svc.decide(
            db,
            request_id=request_id,
            actor_id=None,
            decision="rejected",
            comment=comment.strip(),
            admin_override=True,
            admin_label=admin_user,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    from app.routers.interactive import after_decision

    after_decision(db, result, actor_id=None, admin_label=admin_user)
    return RedirectResponse(
        url=f"/admin/requests/{request_id}", status_code=status.HTTP_303_SEE_OTHER
    )


from datetime import timezone as _tz
from zoneinfo import ZoneInfo as _ZoneInfo

_JST = _ZoneInfo("Asia/Tokyo")


def _fmt(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz.utc)
    return dt.astimezone(_JST).strftime("%Y-%m-%d %H:%M")
