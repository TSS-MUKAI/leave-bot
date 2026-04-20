from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, Form, HTTPException, status
from sqlalchemy.orm import Session

from app import ui
from app.config import get_settings
from app.db.session import get_db
from app.mattermost import get_mattermost
from app.services import users as user_svc

log = logging.getLogger(__name__)
router = APIRouter()


USERNAME_RE = re.compile(r"[A-Za-z0-9._-]+")

# Japanese → canonical subcommand.
ALIASES = {
    "申請": "apply",
    "有給申請": "apply",
    "ヘルプ": "help",
    "使い方": "help",
    "情報": "me",
    "マイページ": "me",
    "自分": "me",
    "上長": "set-manager",
    "上長登録": "set-manager",
    "上長変更": "set-manager",
    "メニュー": "menu",
    "有給": "menu",
    "有給申請": "apply",
    "休暇": "menu",
    "休暇申請": "apply",
    "残日数": "balance",
    "一覧": "list",
    "履歴": "list",
    "承認待ち": "pending",
    "取消": "cancel",
}


def _ephemeral(text: str, attachments: list | None = None) -> dict:
    resp: dict = {"response_type": "ephemeral", "text": text}
    if attachments:
        resp["attachments"] = attachments
    return resp


def _upsert_caller(db: Session, user_id: str, user_name: str) -> None:
    if not user_id:
        return
    current = user_svc.get_user(db, user_id)
    needs_enrich = current is None or current.display_name == current.username
    if needs_enrich:
        try:
            profile = get_mattermost().get_user(user_id)
            user_svc.upsert_user(
                db,
                mm_user_id=user_id,
                username=profile.get("username", user_name),
                display_name=ui.mm_display_name(profile),
                email=profile.get("email"),
            )
            return
        except Exception:
            log.exception("caller profile enrich failed")
    user_svc.upsert_user(db, mm_user_id=user_id, username=user_name)


@router.post("/slash/leave")
def leave_command(
    token: str = Form(""),
    team_id: str = Form(""),
    team_domain: str = Form(""),
    channel_id: str = Form(""),
    channel_name: str = Form(""),
    user_id: str = Form(""),
    user_name: str = Form(""),
    command: str = Form(""),
    text: str = Form(""),
    trigger_id: str = Form(""),
    response_url: str = Form(""),
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    valid = settings.slash_tokens
    if valid and token not in valid:
        log.warning("slash token rejected: user=%s command=%s", user_name, command)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")

    log.info(
        "slash in: command=%s text=%r user=%s trigger_id=%s",
        command, text, user_name, bool(trigger_id),
    )

    _upsert_caller(db, user_id, user_name)

    parts = (text or "").strip().split(maxsplit=1)
    raw_sub = parts[0] if parts else ""
    args = parts[1].strip() if len(parts) > 1 else ""
    cmd = command or "/yukyu"
    sub = ALIASES.get(raw_sub, raw_sub.lower())

    if not raw_sub or sub == "menu":
        return ui.menu_response()

    if sub == "ping":
        return _ephemeral(
            f":white_check_mark: pong — hello @{user_name} (user_id={user_id})"
        )

    if sub == "me":
        return _ephemeral(ui.me_text(db, user_id))

    if sub == "help":
        return _ephemeral(ui.help_text(cmd))

    if sub == "list":
        from sqlalchemy import select as _select
        from app.db.models import LeaveRequest

        reqs = list(
            db.scalars(
                _select(LeaveRequest)
                .where(LeaveRequest.user_id == user_id)
                .order_by(LeaveRequest.id.desc())
                .limit(10)
            )
        )
        if not reqs:
            return _ephemeral(":clipboard: あなたの申請履歴はまだありません")
        return _ephemeral(
            ":clipboard: **あなたの申請履歴(最新10件)**",
            attachments=ui.list_attachments(reqs),
        )

    if sub == "set-manager":
        if not args:
            if not trigger_id:
                return _ephemeral(
                    f":x: `{cmd} 上長 @ユーザー名` の形式で指定してください"
                )
            try:
                get_mattermost().open_dialog(
                    trigger_id, ui.dialog_url(), ui.set_manager_dialog()
                )
            except Exception as e:
                log.exception("open_dialog failed")
                return _ephemeral(f":x: フォーム表示失敗: {e}")
            return _ephemeral(":arrow_up: 上長を選ぶフォームを開きました")
        first = args.split()[0]
        target = first.lstrip("@")
        if not target or not USERNAME_RE.fullmatch(target):
            return _ephemeral(
                f"使い方: `{cmd} 上長 @ユーザー名`(または `{cmd}` からメニュー)"
            )
        mm = get_mattermost()
        try:
            mgr = mm.get_user_by_username(target)
        except Exception as e:
            log.exception("mattermost user lookup failed")
            return _ephemeral(f":x: Mattermost ユーザ検索失敗: {e}")
        if not mgr:
            return _ephemeral(f":x: ユーザ `@{target}` が見つかりません")
        if mgr["id"] == user_id:
            return _ephemeral(":x: 自分自身を上長に設定することはできません")
        user_svc.upsert_user(
            db,
            mm_user_id=mgr["id"],
            username=mgr.get("username", target),
            display_name=ui.mm_display_name(mgr),
            email=mgr.get("email"),
        )
        user_svc.set_manager(db, user_id, mgr["id"])
        return _ephemeral(
            f":white_check_mark: 上長を @{mgr['username']} ({ui.mm_display_name(mgr)}) に設定しました"
        )

    if sub == "apply":
        user = user_svc.get_user(db, user_id)
        if user is None or not user_svc.resolve_manager(db, user):
            return _ephemeral(
                ":warning: 上長が登録されていません。最終承認者にご依頼ください。"
            )
        if not trigger_id:
            return _ephemeral(":x: フォームを開けませんでした(trigger_id 空)")
        try:
            get_mattermost().open_dialog(
                trigger_id, ui.dialog_url(), ui.apply_dialog()
            )
        except Exception as e:
            log.exception("open_dialog failed")
            return _ephemeral(f":x: フォーム表示失敗: {e}")
        return _ephemeral(":arrow_up: 申請フォームを開きました")

    return _ephemeral(
        f":warning: 未実装のコマンド: `{raw_sub}`\n"
        f"`{cmd}` でメニュー、`{cmd} 使い方` で説明が出ます。"
    )
