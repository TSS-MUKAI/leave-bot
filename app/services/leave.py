from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Approval, LeaveAudit, LeaveRequest, User
from app.services import users as user_svc

log = logging.getLogger(__name__)

LEAVE_TYPES: set[str] = {"paid", "half_am", "half_pm", "special"}


def calc_business_days(leave_type: str, start: date, end: date) -> Decimal:
    """Count weekdays (Mon-Fri) inclusive. Half-day types always return 0.5.
    Holidays are not considered in this revision."""
    if leave_type in ("half_am", "half_pm"):
        return Decimal("0.5")
    total = Decimal(0)
    d = start
    while d <= end:
        if d.weekday() < 5:
            total += Decimal("1")
        d += timedelta(days=1)
    return total


def parse_date(s: str) -> date:
    return date.fromisoformat((s or "").strip())


def parse_business_days(s: str) -> Decimal:
    v = Decimal((s or "").strip())
    if v <= 0:
        raise ValueError("0より大きい値で入力してください")
    if (v * 2) != (v * 2).to_integral_value():
        raise ValueError("0.5単位で入力してください")
    return v


def create_request(
    db: Session,
    *,
    applicant_id: str,
    leave_type: str,
    start_date: date,
    end_date: date,
    business_days: Decimal,
    reason: str,
) -> LeaveRequest:
    if leave_type not in LEAVE_TYPES:
        raise ValueError("休暇種別の値が不正です")
    if start_date > end_date:
        raise ValueError("終了日が開始日より前です")
    if leave_type in ("half_am", "half_pm"):
        if start_date != end_date or business_days != Decimal("0.5"):
            raise ValueError("半休は同日・0.5日で入力してください")

    applicant = db.get(User, applicant_id)
    if applicant is None:
        raise ValueError("申請者情報が未登録です")

    manager_id = user_svc.resolve_manager(db, applicant)
    proxy_ids = user_svc.resolve_proxies(db, applicant)
    if not manager_id and not proxy_ids:
        raise ValueError(
            "承認者が未設定です。管理部に上長または代理承認者を設定してもらってください"
        )

    # Build stage 1 approvers, excluding the applicant (self) and dedup.
    stage1_approvers: list[tuple[str, str]] = []
    seen: set[str] = {applicant_id}
    if manager_id and manager_id != applicant_id:
        stage1_approvers.append(("manager", manager_id))
        seen.add(manager_id)
    for pid in proxy_ids or []:
        if not pid or pid in seen:
            continue
        seen.add(pid)
        stage1_approvers.append(("proxy", pid))

    if not stage1_approvers:
        raise ValueError(
            "あなた以外の承認者が設定されていません。"
            "管理部に上長または代理承認者を追加してもらってください"
        )

    req = LeaveRequest(
        user_id=applicant_id,
        leave_type=leave_type,
        start_date=start_date,
        end_date=end_date,
        business_days=business_days,
        reason=reason,
        status="pending",
        current_stage=1,
    )
    db.add(req)
    db.flush()

    for role, approver_id in stage1_approvers:
        db.add(
            Approval(
                request_id=req.id,
                stage=1,
                role=role,
                approver_id=approver_id,
                status="pending",
            )
        )
    db.add(
        Approval(
            request_id=req.id,
            stage=2,
            role="hr",
            approver_id=None,
            status="pending",
        )
    )
    db.add(
        LeaveAudit(
            request_id=req.id,
            actor_id=applicant_id,
            action="create",
            payload={
                "leave_type": leave_type,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "business_days": str(business_days),
                "reason": reason,
            },
        )
    )

    db.commit()
    db.refresh(req)
    return req


def get_hr_users(db: Session) -> list[User]:
    return list(
        db.scalars(
            select(User).where(User.role == "hr", User.is_active.is_(True))
        )
    )


def pending_approvals_at(db: Session, request_id: int, stage: int) -> list[Approval]:
    return list(
        db.scalars(
            select(Approval).where(
                Approval.request_id == request_id,
                Approval.stage == stage,
                Approval.status == "pending",
            )
        )
    )


def _audit(
    db: Session,
    *,
    request_id: int,
    actor_id: str,
    action: str,
    payload: dict | None = None,
) -> None:
    db.add(
        LeaveAudit(
            request_id=request_id,
            actor_id=actor_id,
            action=action,
            payload=payload or {},
        )
    )


def _can_act_at_stage(
    db: Session, req: LeaveRequest, stage: int, user_id: str
) -> tuple[bool, Approval | None]:
    """Return (allowed, own_row). own_row is the Approval row the user should fill.
    Rules:
      - Self-approval blocked: the applicant cannot act on their own request.
      - Stage 1: eligible if user is listed as approver in any pending row at stage 1.
      - Stage 2: eligible if user has role=hr in users table (excluding applicant).
    """
    if user_id == req.user_id:
        return (False, None)
    rows = pending_approvals_at(db, req.id, stage)
    if not rows:
        return (False, None)
    for row in rows:
        if row.approver_id == user_id:
            return (True, row)
    if stage == 2:
        u = db.get(User, user_id)
        if u and u.role == "hr":
            hr_row = next((r for r in rows if r.role == "hr"), rows[0])
            return (True, hr_row)
    return (False, None)


class CancelResult:
    __slots__ = ("request", "pending_rows", "reason")

    def __init__(
        self,
        request: LeaveRequest,
        pending_rows: list[Approval],
        reason: str | None,
    ):
        self.request = request
        self.pending_rows = pending_rows
        self.reason = reason


def cancel_request(
    db: Session,
    *,
    request_id: int,
    actor_id: str | None,
    reason: str | None = None,
    admin_override: bool = False,
    admin_label: str | None = None,
) -> CancelResult:
    req = db.get(LeaveRequest, request_id)
    if req is None:
        raise ValueError("申請が見つかりません")
    if not admin_override:
        if req.user_id != actor_id:
            raise ValueError("ご自身の申請のみ取消できます")
        if req.status != "pending":
            raise ValueError(
                f"この申請は既に {req.status} のため取消できません"
            )
    else:
        if req.status not in ("pending", "approved"):
            raise ValueError(
                f"この申請は既に {req.status} のため取消できません"
            )

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    effective_reason = reason
    if admin_override:
        prefix = f"[管理者取消 by {admin_label or 'admin'}]"
        effective_reason = f"{prefix} {reason}" if reason else prefix

    req.status = "canceled"
    req.decided_at = now

    pending_rows = list(
        db.scalars(
            select(Approval).where(
                Approval.request_id == req.id, Approval.status == "pending"
            )
        )
    )
    for row in pending_rows:
        row.status = "skipped"
        row.decided_at = now

    action = "admin_override_canceled" if admin_override else "canceled"
    _audit(
        db,
        request_id=req.id,
        actor_id=None if admin_override else actor_id,
        action=action,
        payload={
            "reason": effective_reason or "",
            "admin_label": admin_label or "",
        },
    )

    db.commit()
    db.refresh(req)
    for r in pending_rows:
        db.refresh(r)

    return CancelResult(
        request=req, pending_rows=pending_rows, reason=effective_reason
    )


class DecisionResult:
    __slots__ = ("request", "decided_row", "sibling_rows", "advanced_to", "finalized")

    def __init__(
        self,
        request: LeaveRequest,
        decided_row: Approval,
        sibling_rows: list[Approval],
        advanced_to: int | None,
        finalized: str | None,
    ):
        self.request = request
        self.decided_row = decided_row
        self.sibling_rows = sibling_rows
        self.advanced_to = advanced_to  # next stage, or None
        self.finalized = finalized  # "approved"/"rejected" when terminal


def decide(
    db: Session,
    *,
    request_id: int,
    actor_id: str | None,
    decision: str,
    comment: str | None = None,
    admin_override: bool = False,
    admin_label: str | None = None,
) -> DecisionResult:
    """Apply an approve/reject decision on the current stage.

    Normal path: `actor_id` must be an eligible approver for the current stage.
    Admin override path: set `admin_override=True`; the caller isn't required to
    be an approver. `admin_label` identifies the admin user for the audit trail.
    """
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be approved or rejected")
    req = db.get(LeaveRequest, request_id)
    if req is None:
        raise ValueError("申請が見つかりません")
    if req.status != "pending":
        raise ValueError(f"この申請は既に {req.status} です")

    if admin_override:
        rows = pending_approvals_at(db, req.id, req.current_stage)
        if not rows:
            raise ValueError("対応中の承認レコードがありません")
        own_row = rows[0]
    else:
        allowed, own_row = _can_act_at_stage(db, req, req.current_stage, actor_id)
        if not allowed or own_row is None:
            raise ValueError("あなたはこの承認段階の承認者ではありません")

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    effective_comment = comment
    if admin_override:
        prefix = f"[管理者代行 by {admin_label or 'admin'}]"
        effective_comment = f"{prefix} {comment}" if comment else prefix

    own_row.status = decision
    if not admin_override:
        own_row.approver_id = actor_id
    own_row.comment = effective_comment
    own_row.decided_at = now

    siblings = [
        r for r in pending_approvals_at(db, req.id, req.current_stage) if r.id != own_row.id
    ]
    for s in siblings:
        s.status = "skipped"
        s.decided_at = now

    action = f"admin_override_{decision}" if admin_override else decision
    _audit(
        db,
        request_id=req.id,
        actor_id=None if admin_override else actor_id,
        action=action,
        payload={
            "stage": req.current_stage,
            "role": own_row.role,
            "comment": effective_comment or "",
            "admin_label": admin_label or "",
        },
    )

    advanced_to: int | None = None
    finalized: str | None = None

    if decision == "rejected":
        req.status = "rejected"
        req.decided_at = now
        finalized = "rejected"
    else:
        # Approved at current stage. Advance or finalize.
        next_stage = req.current_stage + 1
        next_rows = list(
            db.scalars(
                select(Approval).where(
                    Approval.request_id == req.id,
                    Approval.stage == next_stage,
                )
            )
        )
        if next_rows:
            req.current_stage = next_stage
            advanced_to = next_stage
        else:
            req.status = "approved"
            req.decided_at = now
            finalized = "approved"

    db.commit()
    db.refresh(req)
    db.refresh(own_row)
    for s in siblings:
        db.refresh(s)

    return DecisionResult(
        request=req,
        decided_row=own_row,
        sibling_rows=siblings,
        advanced_to=advanced_to,
        finalized=finalized,
    )
