# leave-bot

Mattermost 有給申請 Bot (Python 3.12 + FastAPI + PostgreSQL).

## セットアップ手順

### 1. DB 初期化(初回のみ)

既存の PostgreSQL コンテナに専用 DB とユーザを作成します。

```bash
cd /home/mukai/docker
docker exec -i docker-postgres-1 psql -U mmuser -d mattermost \
  < leave-bot/scripts/init_db.sql
```

本番運用する場合は `scripts/init_db.sql` のパスワードを変更し、同じ値を `leave-bot/.env` の `DATABASE_URL` に反映してください。

### 2. Bot コンテナのビルドと起動

既存スタックに `docker-compose.leave-bot.yml` を追加する形で起動します。

```bash
cd /home/mukai/docker
docker compose \
  -f docker-compose.yml \
  -f docker-compose.without-nginx.yml \
  -f docker-compose.leave-bot.yml \
  up -d --build leave-bot
```

### 3. マイグレーション適用

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.without-nginx.yml \
  -f docker-compose.leave-bot.yml \
  exec leave-bot alembic upgrade head
```

### 4. ヘルスチェック

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.without-nginx.yml \
  -f docker-compose.leave-bot.yml \
  exec leave-bot curl -fsS http://127.0.0.1:8080/health
# => {"status":"ok"}
```

### 5. Mattermost 側で Slash Command を登録

管理者権限で System Console は不要(チーム設定で登録可能)。

> `/leave` は Mattermost 組み込みコマンド(チャンネル退出)と競合するため使用不可。`/yukyu` / `/有給` / `/休暇` の3つを同一エンドポイントに登録し、ユーザが覚えやすいコマンドで呼び出せるようにする。

1. Mattermost 右上メニュー → **Integrations** → **Slash Commands** → **Add Slash Command**
2. 以下の3つを登録(全て同じ URL を指す)

   | 項目 | `/yukyu` | `/有給` | `/休暇` |
   |---|---|---|---|
   | Command Trigger Word | `yukyu` | `有給` | `休暇` |
   | Request URL | `http://leave-bot:8080/slash/leave` | 同左 | 同左 |
   | Request Method | `POST` | `POST` | `POST` |
   | Autocomplete | ON | ON | ON |
   | Autocomplete Hint | `(引数なしでメニュー)` | 同左 | 同左 |
   | Autocomplete Description | 有給申請 Bot | 同左 | 同左 |

3. 発行された **Token** をすべて `.env` の `LEAVE_BOT_SLASH_TOKENS` にカンマ区切りで貼り付け

   ```env
   LEAVE_BOT_SLASH_TOKENS=token_yukyu,token_yuukyuu,token_kyuuka
   ```

4. 設定反映のため再起動

   ```bash
   docker compose \
     -f docker-compose.yml \
     -f docker-compose.without-nginx.yml \
     -f docker-compose.leave-bot.yml \
     up -d leave-bot
   ```

### 6. 動作確認

Mattermost 任意のチャンネルで:

```
/yukyu ping
```

→ `:white_check_mark: pong — hello @yourname (user_id=...)` がエフェメラル(自分にだけ見える)で返れば疎通 OK。

`/有給申請 ping` も同じ応答。

## コマンド一覧(実装予定含む)

| コマンド | 説明 | 状態 |
|---|---|---|
| `/yukyu ping` | 疎通確認 | ✅ 実装済み |
| `/yukyu help` | ヘルプ表示 | ✅ 実装済み |
| `/yukyu apply` | 申請ダイアログを開く | ⏳ 未実装 |
| `/yukyu balance` | 自分の残日数 | ⏳ 未実装 |
| `/yukyu list` | 自分の申請履歴 | ⏳ 未実装 |
| `/yukyu pending` | 承認待ち一覧(上長/管理部) | ⏳ 未実装 |
| `/yukyu cancel <id>` | 申請取消 | ⏳ 未実装 |

## ローカル疎通テスト(Mattermost を介さず直接 POST)

Bot 単体で応答を確認する場合:

```bash
# ホストから直接叩くには docker-compose.leave-bot.yml の ports: を有効化し、
# 127.0.0.1:8088 -> 8080 を公開してから以下を実行。
curl -sS -X POST http://127.0.0.1:8088/slash/leave \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'token=dummy' \
  --data-urlencode 'user_id=test123' \
  --data-urlencode 'user_name=tester' \
  --data-urlencode 'text=ping'
# => {"response_type":"ephemeral","text":":white_check_mark: pong — hello @tester (user_id=test123)"}
```

`.env` の `LEAVE_BOT_SLASH_TOKENS` が空の間はトークン検証をスキップします(開発用)。本運用前に必ずトークンを設定してください。

## 管理 Web UI

管理部(HR)向けのブラウザ画面で、ユーザの上長/代理承認者/ロール設定、申請一覧・詳細・監査ログの閲覧が可能。

### 起動方法
`.env` に `ADMIN_PASSWORD` を設定し、`docker-compose.leave-bot.yml` で `8088:8080` をホストに公開している前提で:

```
http://<ホストIP>:8088/admin/
```

ブラウザがダイアログでユーザ名とパスワードを求めます(Basic 認証)。`.env` の `ADMIN_USERNAME`(既定 `admin`)と `ADMIN_PASSWORD` を入力。

### 画面
| パス | 説明 |
|---|---|
| `/admin/users` | ユーザ一覧 + 追加 |
| `/admin/users/<id>/edit` | ロール(社員/上長/管理部/admin)・上長・代理承認者・在籍状態を編集 |
| `/admin/requests` | 申請一覧(状態で絞込可) |
| `/admin/requests/<id>` | 申請詳細 + 承認レコード + 監査ログ |

### セキュリティ
- `.env` の `ADMIN_PASSWORD` が空だと `/admin/*` は 503 で無効化
- 8088 ポートは社内 LAN 限定の想定(公開 IP に直接バインドしない)
- Basic 認証は簡易。監査要件が上がったら `Mattermost OAuth 2.0` に差し替え可能

## アーキテクチャ

- **承認フロー**: 2 段階(上長 → 管理部)。`approvals` テーブルで汎用化しており、段数追加はデータ変更のみで可能
- **ロール**: `employee` / `manager` / `hr` / `admin`
- **DB**: 既存 PostgreSQL 18 コンテナに `leavebot` DB を相乗り
- **Mattermost 連携**: Slash Command(受信)+ Bot REST API(DM 投稿、Interactive ボタン)

### 休暇種別 (`leave_requests.leave_type`)

| 値 | 説明 | 制約 |
|---|---|---|
| `paid` | 全日有給 | `business_days` は 0.5 刻みの正数 |
| `half_am` | 午前半休 | `start_date = end_date` かつ `business_days = 0.5` |
| `half_pm` | 午後半休 | 同上 |
| `special` | 特別休暇(慶弔等) | 全日扱い |

上記は DB 側 CHECK 制約 (`ck_leave_requests_*`) で強制されます。時間単位有給は現時点では非対応。
