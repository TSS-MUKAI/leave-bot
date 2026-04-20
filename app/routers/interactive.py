from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from app import ui
from app.db.models import Approval, LeaveRequest, User
from app.db.session import get_db
from app.mattermost import get_mattermost
from app.services import leave as leave_svc
from app.services import users as user_svc

log = logging.getLogger(__name__)
router = APIRouter(prefix="/interactive")


# --- /interactive/dialog ----------------------------------------------
@router.post("/dialog")
def dialog_submission(
    body: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
) -> dict:
    if body.get("cancelled"):
        return {}
    callback_id = body.get("callback_id", "")
    submission = body.get("submission") or {}
    state = body.get("state", "")
    user_id = body.get("user_id", "")
    user_name = body.get("user_name", "")
    log.info("dialog submit: callback=%s user=%s", callback_id, user_name)

    if callback_id == "leave_apply":
        return _handle_apply(db, user_id, submission)
    if callback_id == "set_manager":
        return _handle_set_manager(db, user_id, submission)
    if callback_id == "leave_reject":
        return _handle_reject_dialog(db, user_id, state, submission)
    if callback_id == "leave_cancel":
        return _handle_cancel_dialog(db, user_id, state, submission)
    return {"error": f"unknown callback_id: {callback_id}"}


def _handle_apply(db: Session, user_id: str, submission: dict) -> dict:
    errors: dict[str, str] = {}

    leave_type = submission.get("leave_type", "")
    if leave_type not in leave_svc.LEAVE_TYPES:
        errors["leave_type"] = "休暇種別を選択してください"

    start_date = None
    try:
        start_date = leave_svc.parse_date(submission.get("start_date", ""))
    except Exception:
        errors["start_date"] = "開始日を選択してください"

    end_date = None
    try:
        end_date = leave_svc.parse_date(submission.get("end_date", ""))
    except Exception:
        errors["end_date"] = "終了日を選択してください"

    reason = (submission.get("reason") or "").strip()
    if not reason:
        errors["reason"] = "理由を入力してください"

    if errors:
        return {"errors": errors}

    if start_date > end_date:
        return {"errors": {"end_date": "終了日は開始日以降にしてください"}}

    if leave_type in ("half_am", "half_pm") and start_date != end_date:
        return {"errors": {"end_date": "半休は同日を指定してください"}}

    days = leave_svc.calc_business_days(leave_type, start_date, end_date)
    if days <= 0:
        return {
            "errors": {
                "start_date": "期間に営業日(平日)が含まれていません",
                "end_date": "期間に営業日(平日)が含まれていません",
            }
        }

    try:
        req = leave_svc.create_request(
            db,
            applicant_id=user_id,
            leave_type=leave_type,
            start_date=start_date,
            end_date=end_date,
            business_days=days,
            reason=reason,
        )
    except ValueError as e:
        return {"error": str(e)}

    _notify_stage_approvers(db, req)
    _notify_applicant_submitted(db, req)
    return {}


def _handle_set_manager(db: Session, user_id: str, submission: dict) -> dict:
    manager_id = (submission.get("manager_id") or "").strip()
    if not manager_id:
        return {"errors": {"manager_id": "上長を選択してください"}}
    if manager_id == user_id:
        return {"errors": {"manager_id": "自分自身は上長に設定できません"}}
    mm = get_mattermost()
    try:
        mgr = mm.get_user(manager_id)
    except Exception as e:
        log.exception("mattermost user lookup failed")
        return {"error": f"Mattermost ユーザ情報取得失敗: {e}"}
    user_svc.upsert_user(
        db,
        mm_user_id=mgr["id"],
        username=mgr.get("username", ""),
        display_name=ui.mm_display_name(mgr),
        email=mgr.get("email"),
    )
    user_svc.set_manager(db, user_id, mgr["id"])
    try:
        mm.send_dm(
            user_id,
            message=(
                f":white_check_mark: 上長を @{mgr['username']} "
                f"({ui.mm_display_name(mgr)}) に設定しました"
            ),
        )
    except Exception:
        log.exception("failed to DM applicant about manager change")
    return {}


def _handle_cancel_dialog(
    db: Session, user_id: str, state: str, submission: dict
) -> dict:
    try:
        request_id = int(state)
    except Exception:
        return {"error": "申請IDが不正です"}
    reason = (submission.get("reason") or "").strip() or None
    try:
        result = leave_svc.cancel_request(
            db, request_id=request_id, actor_id=user_id, reason=reason
        )
    except ValueError as e:
        return {"error": str(e)}
    after_cancel(db, result, actor_id=user_id)
    _send_refreshed_list_dm(db, user_id, request_id)
    return {}


def _send_refreshed_list_dm(db: Session, user_id: str, canceled_id: int) -> None:
    """After a successful cancel, send the applicant the latest history as a new DM
    so any stale cancel buttons in earlier DMs become obvious."""
    from sqlalchemy import select as _select

    reqs = list(
        db.scalars(
            _select(LeaveRequest)
            .where(LeaveRequest.user_id == user_id)
            .order_by(LeaveRequest.id.desc())
            .limit(10)
        )
    )
    if not reqs:
        return
    try:
        get_mattermost().send_dm(
            user_id,
            message=(
                f":white_check_mark: 申請 (ID: {canceled_id}) を取消しました\n"
                ":clipboard: **最新の申請履歴**(以前のリストにあるボタンは無効です)"
            ),
            props={"attachments": ui.list_attachments(reqs)},
        )
    except Exception:
        log.exception("refresh list DM failed after cancel")


def _handle_reject_dialog(
    db: Session, user_id: str, state: str, submission: dict
) -> dict:
    try:
        request_id = int(state)
    except Exception:
        return {"error": "申請 ID が不正です"}
    comment = (submission.get("comment") or "").strip()
    if not comment:
        return {"errors": {"comment": "却下理由を入力してください"}}
    try:
        result = leave_svc.decide(
            db,
            request_id=request_id,
            actor_id=user_id,
            decision="rejected",
            comment=comment,
        )
    except ValueError as e:
        return {"error": str(e)}
    after_decision(db, result, actor_id=user_id)
    return {}


# --- /interactive/action ----------------------------------------------
@router.post("/action")
def action(
    body: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
) -> dict:
    context = body.get("context") or {}
    name = context.get("action", "")
    user_id = body.get("user_id", "")
    user_name = body.get("user_name", "")
    trigger_id = body.get("trigger_id", "")
    channel_id = body.get("channel_id", "")
    log.info("action: %s user=%s", name, user_name)

    if user_id:
        user_svc.upsert_user(db, mm_user_id=user_id, username=user_name)

    if name == "apply":
        user = user_svc.get_user(db, user_id)
        if user is None or not user_svc.resolve_manager(db, user):
            return {
                "ephemeral_text": ":warning: 上長が登録されていません。最終承認者にご依頼ください。"
            }
        if not trigger_id:
            return {"ephemeral_text": ":x: フォームを開けませんでした"}
        try:
            get_mattermost().open_dialog(trigger_id, ui.dialog_url(), ui.apply_dialog())
        except Exception as e:
            log.exception("open_dialog failed")
            return {"ephemeral_text": f":x: フォーム表示失敗: {e}"}
        return {}

    if name == "set_manager":
        if not trigger_id:
            return {"ephemeral_text": ":x: フォームを開けませんでした"}
        try:
            get_mattermost().open_dialog(
                trigger_id, ui.dialog_url(), ui.set_manager_dialog()
            )
        except Exception as e:
            log.exception("open_dialog failed")
            return {"ephemeral_text": f":x: フォーム表示失敗: {e}"}
        return {}

    if name == "me":
        return {"ephemeral_text": ui.me_text(db, user_id)}

    if name == "help":
        return {"ephemeral_text": ui.help_text()}

    if name == "approve":
        return _handle_approve_action(db, user_id, context)
    if name == "reject":
        return _handle_reject_action(db, user_id, trigger_id, context)
    if name == "list":
        return _handle_list_action(db, user_id, channel_id)
    if name == "cancel":
        return _handle_cancel_action(db, user_id, trigger_id, context)

    return {"ephemeral_text": f":warning: 未実装のアクション: {name}"}


def _handle_list_action(db: Session, user_id: str, channel_id: str) -> dict:
    from sqlalchemy import select as _select

    reqs = list(
        db.scalars(
            _select(LeaveRequest)
            .where(LeaveRequest.user_id == user_id)
            .order_by(LeaveRequest.id.desc())
            .limit(10)
        )
    )
    if not reqs:
        return {"ephemeral_text": ":clipboard: あなたの申請履歴はまだありません"}

    # Attachments with interactive buttons need a persistent post so the cancel
    # button can update it. Send as DM from the bot (ephemeral slash responses
    # have no post id that MM can update later).
    try:
        get_mattermost().send_dm(
            user_id,
            message=":clipboard: **あなたの申請履歴(最新10件)**",
            props={"attachments": ui.list_attachments(reqs)},
        )
        return {
            "ephemeral_text": ":clipboard: Bot との DM に申請履歴を送りました。そちらで取消ボタン等を使用できます。"
        }
    except Exception:
        log.exception("DM list failed; returning plain text fallback")

    lines = [":clipboard: **あなたの申請履歴(最新10件)**"]
    for r in reqs:
        type_jp = ui.LEAVE_TYPE_JP.get(r.leave_type, r.leave_type)
        period = (
            str(r.start_date)
            if r.start_date == r.end_date
            else f"{r.start_date} 〜 {r.end_date}"
        )
        lines.append(
            f"- ID: **{r.id}** — {type_jp} — {period} ({r.business_days}日) — {r.status}"
        )
    return {"ephemeral_text": "\n".join(lines)}


def _handle_cancel_action(
    db: Session, user_id: str, trigger_id: str, context: dict
) -> dict:
    """Open a cancel-confirmation dialog for the applicant's pending request.
    Error messages are sent as fresh DMs because ephemeral_text doesn't render
    when returned from a DM-originated button click in Mattermost."""
    try:
        request_id = int(context.get("request_id", 0))
    except Exception:
        _dm_error(user_id, ":x: 申請IDが不正です")
        return {}
    req = db.get(LeaveRequest, request_id)
    if req is None:
        _dm_error(user_id, f":x: 申請 (ID: {request_id}) が見つかりません")
        return {}
    if req.user_id != user_id:
        _dm_error(user_id, ":x: ご自身の申請のみ取消できます")
        return {}
    if req.status != "pending":
        status_jp = {
            "approved": "承認済",
            "rejected": "却下済",
            "canceled": "取消済",
        }.get(req.status, req.status)
        _dm_error(
            user_id,
            f":x: 申請 (ID: {req.id}) は既に **{status_jp}** のため取消できません",
        )
        return {}
    if not trigger_id:
        _dm_error(user_id, ":x: フォームを開けませんでした(trigger_id 空)")
        return {}
    try:
        get_mattermost().open_dialog(
            trigger_id, ui.dialog_url(), ui.cancel_dialog(request_id)
        )
    except Exception as e:
        log.exception("open_dialog failed")
        _dm_error(user_id, f":x: ダイアログ表示失敗: {e}")
        return {}
    log.info("cancel dialog opened for request=%s user=%s", request_id, user_id)
    return {}


def _dm_error(user_id: str, message: str) -> None:
    try:
        get_mattermost().send_dm(user_id, message=message)
    except Exception:
        log.exception("dm_error send failed")


def _handle_approve_action(db: Session, user_id: str, context: dict) -> dict:
    try:
        request_id = int(context.get("request_id", 0))
    except Exception:
        return {"ephemeral_text": ":x: 申請 ID が不正です"}
    try:
        result = leave_svc.decide(
            db, request_id=request_id, actor_id=user_id, decision="approved"
        )
    except ValueError as e:
        return {"ephemeral_text": f":x: {e}"}
    after_decision(db, result, actor_id=user_id)

    req = result.request
    type_jp = ui.LEAVE_TYPE_JP.get(req.leave_type, req.leave_type)
    summary = _request_summary(db, req, type_jp)
    status_line = (
        ":white_check_mark: **承認済み(全段階完了)**"
        if result.finalized == "approved"
        else f":white_check_mark: **あなたが承認しました(次段階へ進行)**"
    )
    return {
        "update": {
            "message": f"{summary}\n\n{status_line}",
            "props": {"attachments": ui.decided_attachments(status_line, color="#28a745")},
        }
    }


def _handle_reject_action(
    db: Session, user_id: str, trigger_id: str, context: dict
) -> dict:
    try:
        request_id = int(context.get("request_id", 0))
    except Exception:
        return {"ephemeral_text": ":x: 申請 ID が不正です"}
    req = db.get(LeaveRequest, request_id)
    if req is None or req.status != "pending":
        return {"ephemeral_text": ":x: この申請は既に処理済みです"}
    allowed, _own = leave_svc._can_act_at_stage(db, req, req.current_stage, user_id)
    if not allowed:
        return {"ephemeral_text": ":x: あなたはこの段階の承認者ではありません"}
    if not trigger_id:
        return {"ephemeral_text": ":x: フォームを開けませんでした"}
    try:
        get_mattermost().open_dialog(
            trigger_id, ui.dialog_url(), ui.reject_dialog(request_id)
        )
    except Exception as e:
        log.exception("open_dialog failed")
        return {"ephemeral_text": f":x: フォーム表示失敗: {e}"}
    return {}


# --- DM notifications -------------------------------------------------
def _period_line(req: LeaveRequest) -> str:
    if req.start_date == req.end_date:
        return f"{req.start_date} ({req.business_days}日)"
    return f"{req.start_date} 〜 {req.end_date} ({req.business_days}日)"


def _request_summary(db: Session, req: LeaveRequest, type_jp: str) -> str:
    applicant = user_svc.get_user(db, req.user_id)
    applicant_txt = (
        f"@{applicant.username} ({applicant.display_name})" if applicant else req.user_id
    )
    return (
        f":envelope: **有給申請 (ID: {req.id})**\n"
        f"- 申請者: {applicant_txt}\n"
        f"- 種別: {type_jp}\n"
        f"- 期間: {_period_line(req)}\n"
        f"- 理由: {req.reason}"
    )


def _role_label(role: str) -> str:
    return {"manager": "上長承認", "proxy": "代理承認", "hr": "最終承認"}.get(
        role, role
    )


def _notify_stage_approvers(db: Session, req: LeaveRequest) -> None:
    """Send DM with approval buttons to every pending approver at the current stage."""
    rows = leave_svc.pending_approvals_at(db, req.id, req.current_stage)
    if not rows:
        return
    type_jp = ui.LEAVE_TYPE_JP.get(req.leave_type, req.leave_type)
    summary = _request_summary(db, req, type_jp)
    mm = get_mattermost()

    targets: list[tuple[Approval, str]] = []
    for row in rows:
        if row.approver_id:
            if row.approver_id == req.user_id:
                continue
            targets.append((row, row.approver_id))
        elif row.role == "hr":
            for hr in leave_svc.get_hr_users(db):
                if hr.mm_user_id == req.user_id:
                    continue
                targets.append((row, hr.mm_user_id))

    first_row_post: dict[str, str] = {}  # row.id -> post.id
    for row, target_user_id in targets:
        try:
            post = mm.send_dm(
                target_user_id,
                message=summary,
                props={
                    "attachments": ui.approval_attachments(
                        request_id=req.id,
                        summary=summary,
                        role_label=_role_label(row.role),
                    )
                },
            )
        except Exception:
            log.exception(
                "failed to DM approver user=%s request=%s", target_user_id, req.id
            )
            continue
        # Store the first post id per Approval row (HR has many recipients per row).
        if row.id not in first_row_post:
            first_row_post[row.id] = post.get("id", "")

    for row_id, post_id in first_row_post.items():
        if post_id:
            row = db.get(Approval, row_id)
            if row is not None:
                row.dm_post_id = post_id
    if first_row_post:
        db.commit()


def _notify_applicant_submitted(db: Session, req: LeaveRequest) -> None:
    mm = get_mattermost()
    type_jp = ui.LEAVE_TYPE_JP.get(req.leave_type, req.leave_type)
    applicant = user_svc.get_user(db, req.user_id)
    manager_line = ""
    if applicant:
        mgr_id = user_svc.resolve_manager(db, applicant)
        if mgr_id:
            m = user_svc.get_user(db, mgr_id)
            if m:
                manager_line = f"\n- 承認者: @{m.username} ({m.display_name})"
        proxy_ids = user_svc.resolve_proxies(db, applicant)
        proxy_names = []
        for pid in proxy_ids:
            p = user_svc.get_user(db, pid)
            if p:
                proxy_names.append(f"@{p.username} ({p.display_name})")
        if proxy_names:
            manager_line += f"\n- 代理承認者: {', '.join(proxy_names)}"
    try:
        mm.send_dm(
            req.user_id,
            message=(
                f":white_check_mark: **有給申請を受け付けました** (ID: {req.id})\n"
                f"- 種別: {type_jp}\n"
                f"- 期間: {_period_line(req)}\n"
                f"- 理由: {req.reason}"
                f"{manager_line}\n"
                f"\n上長の承認をお待ちください。"
            ),
        )
    except Exception:
        log.exception("failed to DM applicant for request %s", req.id)


def after_decision(
    db: Session,
    result: Any,
    actor_id: str | None,
    admin_label: str | None = None,
) -> None:
    """Post-decision side effects: clear buttons on every stage DM, advance stage
    notifications, final result notification to applicant.
    When invoked via admin override, pass `actor_id=None` and a human-readable
    `admin_label` (e.g. the admin UI username)."""
    req: LeaveRequest = result.request
    mm = get_mattermost()
    type_jp = ui.LEAVE_TYPE_JP.get(req.leave_type, req.leave_type)
    summary = _request_summary(db, req, type_jp)
    if admin_label:
        actor_txt = f"システム管理者 ({admin_label}) が代行"
    else:
        actor = user_svc.get_user(db, actor_id) if actor_id else None
        actor_txt = f"@{actor.username} ({actor.display_name})" if actor else (actor_id or "不明")

    decided_row = result.decided_row
    if decided_row.status == "approved":
        decided_banner = f":white_check_mark: {actor_txt} が承認しました"
        decided_color = "#28a745"
    else:
        reason = f"\n理由: {decided_row.comment}" if decided_row.comment else ""
        decided_banner = f":x: {actor_txt} が却下しました{reason}"
        decided_color = "#dc3545"
    sibling_banner = f":information_source: {actor_txt} が対応したため、他の承認者の操作は不要です"

    for row in [decided_row] + list(result.sibling_rows):
        if not row.dm_post_id:
            continue
        is_self = row.id == decided_row.id
        banner = decided_banner if is_self else sibling_banner
        color = decided_color if is_self else "#6c757d"
        try:
            mm.update_post(
                row.dm_post_id,
                message=f"{summary}\n\n{banner}",
                props={"attachments": ui.decided_attachments(banner, color=color)},
            )
        except Exception:
            log.exception("failed to update DM post %s", row.dm_post_id)

    if result.finalized is None and result.advanced_to is not None:
        _notify_stage_approvers(db, req)
        _dm_applicant_progress(db, req, actor_txt)
        return

    if result.finalized == "approved":
        _dm_applicant_final(db, req, approved=True, actor_txt=actor_txt, comment=None)
    elif result.finalized == "rejected":
        _dm_applicant_final(
            db,
            req,
            approved=False,
            actor_txt=actor_txt,
            comment=decided_row.comment,
        )


def after_cancel(
    db: Session,
    result: Any,
    actor_id: str | None,
    admin_label: str | None = None,
) -> None:
    req: LeaveRequest = result.request
    mm = get_mattermost()
    type_jp = ui.LEAVE_TYPE_JP.get(req.leave_type, req.leave_type)
    summary = _request_summary(db, req, type_jp)
    if admin_label:
        actor_txt = f"システム管理者 ({admin_label}) が代行"
    else:
        actor = user_svc.get_user(db, actor_id) if actor_id else None
        actor_txt = f"@{actor.username} ({actor.display_name})" if actor else "申請者"

    banner = f":arrows_counterclockwise: {actor_txt} により取消されました"
    if result.reason:
        banner += f"\n理由: {result.reason}"

    for row in result.pending_rows:
        if not row.dm_post_id:
            continue
        try:
            mm.update_post(
                row.dm_post_id,
                message=f"{summary}\n\n{banner}",
                props={"attachments": ui.decided_attachments(banner, color="#6c757d")},
            )
        except Exception:
            log.exception("failed to update DM post %s", row.dm_post_id)

    # Notify applicant unless they cancelled themselves.
    if admin_label or (actor_id and actor_id != req.user_id):
        try:
            mm.send_dm(
                req.user_id,
                message=(
                    f":arrows_counterclockwise: **申請 (ID: {req.id}) が取消されました**\n"
                    f"- 種別: {type_jp}\n"
                    f"- 期間: {_period_line(req)}\n"
                    + (f"- 取消: {actor_txt}\n" if admin_label else "")
                    + (f"- 理由: {result.reason}\n" if result.reason else "")
                ),
            )
        except Exception:
            log.exception("failed to DM applicant cancel")


def _dm_applicant_progress(db: Session, req: LeaveRequest, actor_txt: str) -> None:
    mm = get_mattermost()
    try:
        mm.send_dm(
            req.user_id,
            message=(
                f":arrow_forward: 申請 (ID: {req.id}) の承認が進みました\n"
                f"- {actor_txt} が承認しました\n"
                f"- 現在の段階: {req.current_stage}(次の承認者に通知済み)"
            ),
        )
    except Exception:
        log.exception("failed to DM applicant progress for request %s", req.id)


def _dm_applicant_final(
    db: Session,
    req: LeaveRequest,
    *,
    approved: bool,
    actor_txt: str,
    comment: str | None,
) -> None:
    mm = get_mattermost()
    type_jp = ui.LEAVE_TYPE_JP.get(req.leave_type, req.leave_type)
    if approved:
        header = f":white_check_mark: **有給申請が承認されました** (ID: {req.id})"
    else:
        header = f":x: **有給申請が却下されました** (ID: {req.id})"
    body = (
        f"- 種別: {type_jp}\n"
        f"- 期間: {_period_line(req)}\n"
        f"- 最終対応者: {actor_txt}"
    )
    if comment:
        body += f"\n- 理由(却下): {comment}"
    try:
        mm.send_dm(req.user_id, message=f"{header}\n{body}")
    except Exception:
        log.exception("failed to DM applicant final for request %s", req.id)
