from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services import users as user_svc


LEAVE_TYPE_JP: dict[str, str] = {
    "paid": "全日(有給)",
    "half_am": "午前半休",
    "half_pm": "午後半休",
    "special": "特別休暇",
}

_WEEKDAY_JP = ["月", "火", "水", "木", "金", "土", "日"]


def date_select_options(
    days_ahead: int = 90, start: date | None = None
) -> list[dict]:
    """Return Mattermost select options covering today..today+days_ahead-1.
    Labels use Japanese format like '2026年4月20日(月)'."""
    base = start or date.today()
    options: list[dict] = []
    for i in range(days_ahead):
        d = base + timedelta(days=i)
        label = f"{d.year}年{d.month}月{d.day}日({_WEEKDAY_JP[d.weekday()]})"
        options.append({"text": label, "value": d.isoformat()})
    return options


# --- URLs -------------------------------------------------------------
def dialog_url() -> str:
    return f"{get_settings().leave_bot_url}/interactive/dialog"


def action_url() -> str:
    return f"{get_settings().leave_bot_url}/interactive/action"


# --- Display helpers --------------------------------------------------
def mm_display_name(mm_user: dict) -> str:
    """Format a Mattermost user record as '姓 名' using this org's convention
    (first_name=姓, last_name=名). Falls back to nickname or username."""
    nick = (mm_user.get("nickname") or "").strip()
    if nick:
        return nick
    first = (mm_user.get("first_name") or "").strip()
    last = (mm_user.get("last_name") or "").strip()
    full = f"{first} {last}".strip()
    return full or mm_user.get("username", "")


# --- Dialogs ----------------------------------------------------------
def apply_dialog() -> dict:
    today_iso = date.today().isoformat()
    date_options = date_select_options(90)
    return {
        "callback_id": "leave_apply",
        "title": "有給申請",
        "submit_label": "申請する",
        "notify_on_cancel": False,
        "elements": [
            {
                "display_name": "休暇種別",
                "name": "leave_type",
                "type": "select",
                "default": "paid",
                "options": [
                    {"text": label, "value": value}
                    for value, label in LEAVE_TYPE_JP.items()
                ],
            },
            {
                "display_name": "開始日",
                "name": "start_date",
                "type": "select",
                "default": today_iso,
                "help_text": "今日から約3ヶ月先まで選べます",
                "options": date_options,
            },
            {
                "display_name": "終了日",
                "name": "end_date",
                "type": "select",
                "default": today_iso,
                "help_text": "半休・1日休暇の場合は開始日と同じ日付を選んでください",
                "options": date_options,
            },
            {
                "display_name": "理由",
                "name": "reason",
                "type": "textarea",
                "placeholder": "私用のため",
                "max_length": 500,
            },
        ],
    }


def cancel_dialog(request_id: int) -> dict:
    return {
        "callback_id": "leave_cancel",
        "title": f"申請の取消 (ID: {request_id})",
        "submit_label": "取消する",
        "notify_on_cancel": False,
        "state": str(request_id),
        "elements": [
            {
                "display_name": "取消理由(任意)",
                "name": "reason",
                "type": "textarea",
                "optional": True,
                "placeholder": "例: 予定変更のため",
                "max_length": 500,
            }
        ],
    }


def list_attachments(reqs: list) -> list[dict]:
    """Build a single attachment containing all requests and a cancel button per
    pending request. Using one attachment avoids Mattermost action-routing
    collisions that occur when multiple attachments each default to id=0."""
    status_jp = {
        "pending": ":hourglass: 承認待ち",
        "approved": ":white_check_mark: 承認済",
        "rejected": ":x: 却下",
        "canceled": ":arrows_counterclockwise: 取消",
    }
    lines: list[str] = []
    actions: list[dict] = []
    for r in reqs:
        type_jp = LEAVE_TYPE_JP.get(r.leave_type, r.leave_type)
        period = (
            str(r.start_date)
            if r.start_date == r.end_date
            else f"{r.start_date} 〜 {r.end_date}"
        )
        lines.append(
            f"**ID: {r.id}** — {type_jp} — {period} ({r.business_days}日) — "
            f"{status_jp.get(r.status, r.status)}\n"
            f"理由: {r.reason}"
        )
        if r.status == "pending":
            actions.append(
                {
                    "id": f"cancel{r.id}",
                    "name": f":wastebasket: ID {r.id} を取消",
                    "type": "button",
                    "style": "danger",
                    "integration": {
                        "url": action_url(),
                        "context": {"action": "cancel", "request_id": r.id},
                    },
                }
            )
    return [
        {
            "text": "\n\n".join(lines),
            "color": "#6c757d",
            "actions": actions,
        }
    ]


def reject_dialog(request_id: int) -> dict:
    return {
        "callback_id": "leave_reject",
        "title": f"却下コメント (ID: {request_id})",
        "submit_label": "却下する",
        "notify_on_cancel": False,
        "state": str(request_id),
        "elements": [
            {
                "display_name": "却下理由",
                "name": "comment",
                "type": "textarea",
                "placeholder": "申請者へ届く却下理由を入力してください",
                "min_length": 1,
                "max_length": 500,
            }
        ],
    }


def set_manager_dialog() -> dict:
    return {
        "callback_id": "set_manager",
        "title": "上長を設定",
        "submit_label": "登録する",
        "notify_on_cancel": False,
        "elements": [
            {
                "display_name": "上長",
                "name": "manager_id",
                "type": "select",
                "data_source": "users",
                "help_text": "承認者となる方を選択してください",
            },
        ],
    }


# --- Buttons / Menu ---------------------------------------------------
def approval_attachments(request_id: int, summary: str, role_label: str = "承認者") -> list[dict]:
    return [
        {
            "color": "#6c757d",
            "text": summary,
            "actions": [
                {
                    "id": "approve",
                    "name": ":white_check_mark: 承認",
                    "type": "button",
                    "style": "good",
                    "integration": {
                        "url": action_url(),
                        "context": {"action": "approve", "request_id": request_id},
                    },
                },
                {
                    "id": "reject",
                    "name": ":x: 却下",
                    "type": "button",
                    "style": "danger",
                    "integration": {
                        "url": action_url(),
                        "context": {"action": "reject", "request_id": request_id},
                    },
                },
            ],
            "footer": role_label,
        }
    ]


def decided_attachments(status_text: str, color: str = "#28a745") -> list[dict]:
    return [{"color": color, "text": status_text, "actions": []}]


def menu_attachments() -> list[dict]:
    return [
        {
            "text": "操作を選んでください",
            "actions": [
                {
                    "id": "apply",
                    "name": ":memo: 有給を申請する",
                    "type": "button",
                    "style": "primary",
                    "integration": {
                        "url": action_url(),
                        "context": {"action": "apply"},
                    },
                },
                {
                    "id": "list",
                    "name": ":clipboard: 申請履歴",
                    "type": "button",
                    "integration": {
                        "url": action_url(),
                        "context": {"action": "list"},
                    },
                },
                {
                    "id": "me",
                    "name": ":information_source: 自分の情報",
                    "type": "button",
                    "integration": {
                        "url": action_url(),
                        "context": {"action": "me"},
                    },
                },
                {
                    "id": "help",
                    "name": ":question: 使い方",
                    "type": "button",
                    "integration": {
                        "url": action_url(),
                        "context": {"action": "help"},
                    },
                },
            ],
        }
    ]


def menu_response() -> dict:
    return {
        "response_type": "ephemeral",
        "text": (
            ":leaves: **有給申請 Bot**\n"
            "下のボタンから操作できます。\n"
            "※ボタンが表示されない場合は直接コマンドでも操作可:\n"
            "`/yukyu 申請` / `/yukyu 情報` / `/yukyu 使い方`"
        ),
        "attachments": menu_attachments(),
    }


# --- Text builders ----------------------------------------------------
def me_text(db: Session, user_id: str) -> str:
    user = user_svc.get_user(db, user_id)
    if user is None:
        return "ユーザ情報が未登録です。`/yukyu` を一度実行してください"
    if user.manager_mm_id:
        m = user_svc.get_user(db, user.manager_mm_id)
        manager_txt = f"@{m.username} ({m.display_name})" if m else user.manager_mm_id
    else:
        manager_txt = "未設定 — 最終承認者にご連絡ください"
    return (
        "**あなたの登録情報**\n"
        f"- ユーザ: @{user.username} ({user.display_name})\n"
        f"- ロール: `{user.role}`\n"
        f"- 上長: {manager_txt}\n"
        "\n_上長の変更は最終承認者にご依頼ください_"
    )


def help_text(cmd: str = "/yukyu") -> str:
    return (
        ":leaves: **有給申請 Bot の使い方**\n"
        f"`{cmd}` と打つだけでメニューが出ます。ボタンで操作してください。\n\n"
        "**できること**\n"
        "- :memo: 有給を申請する — フォームから申請\n"
        "- :clipboard: 申請履歴 — 自分の申請を確認・pending は取消可\n"
        "- :information_source: 自分の情報 — 登録内容を確認\n"
        "\n**直接コマンドでも使えます**\n"
        f"- `{cmd}` だけ — メニュー\n"
        f"- `{cmd} 申請` — 申請フォーム\n"
        f"- `{cmd} 履歴` — 申請履歴(取消もここから)\n"
        f"- `{cmd} 情報` — 自分の情報\n"
        "\n_上長の変更は最終承認者にご依頼ください_"
    )
