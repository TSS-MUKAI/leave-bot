# leave-bot ローカル開発手順

本番 (`/home/mukai/docker/leave-bot/`) からこのリポジトリを開発マシンに `git clone` し、
Mattermost を立てずに申請フローを end-to-end で動かすための手順。

構成:
- **postgres** — dev 用。`leavebot` と pytest 専用の `leavebot_test` を同居
- **leave-bot** — `app/` マウント + uvicorn `--reload`。環境変数 `LEAVE_BOT_DEV_MODE=1` で
  `MattermostClient` が `DevNoopMattermost`(HTTP 送信せずログ出力のみ)に差し替わる

## 1. 初回起動

```powershell
cd leave-bot
copy .env.example .env    # macOS/Linux: cp .env.example .env
# .env を編集: ADMIN_PASSWORD に任意値。他は初期値でOK。

docker compose -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.dev.yml exec leave-bot alembic upgrade head
curl http://127.0.0.1:8088/health     # => {"status":"ok"}
```

## 2. テスト用ユーザを seed

```powershell
docker compose -f docker-compose.dev.yml exec leave-bot python scripts/seed_dev.py
# seeded: alice=... -> manager bob=..., hr carol=...
```

## 3. 有給申請フローを end-to-end で動かす(Mattermost 不要)

### 3-A. 自動スクリプト(bash / WSL / Git Bash)
```bash
./scripts/dev_apply.sh
```
申請 → 上長承認 → 管理部承認 までを curl で叩きます。最後に申請の URL が出ます。

### 3-B. PowerShell で手動
```powershell
# 申請フォーム送信(alice として)
curl.exe -X POST http://127.0.0.1:8088/interactive/dialog `
  -H "Content-Type: application/json" `
  -d '{\"callback_id\":\"leave_apply\",\"user_id\":\"alice0000000000000000000aa\",\"user_name\":\"alice\",\"submission\":{\"leave_type\":\"paid\",\"start_date\":\"2026-04-20\",\"end_date\":\"2026-04-20\",\"reason\":\"test\"}}'

# 申請 ID を確認(1 件目なら 1)
docker compose -f docker-compose.dev.yml exec leave-bot python -c "from app.db.session import SessionLocal; from app.db.models import LeaveRequest; db=SessionLocal(); r=db.query(LeaveRequest).order_by(LeaveRequest.id.desc()).first(); print(r.id if r else '(none)')"

# 上長 bob が承認(request_id=1 を適宜差し替え)
curl.exe -X POST http://127.0.0.1:8088/interactive/action `
  -H "Content-Type: application/json" `
  -d '{\"user_id\":\"bob00000000000000000000aaa\",\"user_name\":\"bob\",\"context\":{\"action\":\"approve\",\"request_id\":1}}'

# 管理部 carol が承認
curl.exe -X POST http://127.0.0.1:8088/interactive/action `
  -H "Content-Type: application/json" `
  -d '{\"user_id\":\"carol00000000000000000aaaa\",\"user_name\":\"carol\",\"context\":{\"action\":\"approve\",\"request_id\":1}}'
```

`docker compose -f docker-compose.dev.yml logs -f leave-bot` で `[dev-mm] DM to=...`
のログ行が見えれば、本来 Mattermost に送るはずの DM が捕捉できています。

## 4. 管理 Web UI で結果確認

`http://127.0.0.1:8088/admin/`
Basic 認証は `.env` の `ADMIN_USERNAME` / `ADMIN_PASSWORD`。
- ユーザ一覧・編集
- 申請一覧・詳細・監査ログ

## 5. pytest(自動テスト)

`tests/` 配下に Postgres 実 DB + fake Mattermost を使った統合テストがあります。

```powershell
# 初回のみ: dev 依存を leave-bot コンテナに入れる
docker compose -f docker-compose.dev.yml exec leave-bot pip install -e ".[dev]"

# 全テスト
docker compose -f docker-compose.dev.yml exec leave-bot pytest

# 特定ファイル
docker compose -f docker-compose.dev.yml exec leave-bot pytest tests/test_leave_service.py -v
```

テストは `leavebot_test` DB 側で動き、実 DB(`leavebot`)の seed データには影響しません。

## 6. よく使う compose コマンド

| 用途 | コマンド |
|---|---|
| ログ監視 | `docker compose -f docker-compose.dev.yml logs -f leave-bot` |
| leave-bot 再起動 | `docker compose -f docker-compose.dev.yml restart leave-bot` |
| 再ビルド | `docker compose -f docker-compose.dev.yml up -d --build leave-bot` |
| DB シェル | `docker compose -f docker-compose.dev.yml exec postgres psql -U leavebot -d leavebot` |
| 完全リセット(DB ごと) | `docker compose -f docker-compose.dev.yml down -v` |

## 7. 本番反映

開発マシンで commit & push → 本番サーバで:
```bash
cd /home/mukai/docker/leave-bot && git pull
cd /home/mukai/docker && docker compose \
  -f docker-compose.yml \
  -f docker-compose.without-nginx.yml \
  -f docker-compose.leave-bot.yml \
  up -d --build leave-bot
```
本番側は `LEAVE_BOT_DEV_MODE` を設定していないので通常の `MattermostClient` が使われます。
