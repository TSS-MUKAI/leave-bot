# leave-bot ローカル開発手順

本番 (`mukai@<本番サーバ>:/home/mukai/docker/leave-bot/`) からこのリポジトリを
開発マシン (ローカル PC) に `git clone` して、本番とは完全に独立した
Postgres + leave-bot コンテナで開発するためのメモ。

## 前提
- Docker / Docker Compose v2 がローカル PC に入っていること
- 本番 Mattermost には直接つながない (Slash コマンドは curl で直接叩く)

## 起動
```bash
git clone <repo-url> leave-bot
cd leave-bot
cp .env.example .env
# .env の LEAVE_BOT_SLASH_TOKENS は空のままで OK (開発時はトークン検証スキップ)
# ADMIN_PASSWORD は適当な値を入れる

docker compose -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.dev.yml exec leave-bot alembic upgrade head
```

`http://127.0.0.1:8088/health` が `{"status":"ok"}` を返せば OK。

## 開発フロー
- `app/` 配下を編集すると uvicorn の `--reload` で即反映
- スキーマ変更時は `docker compose -f docker-compose.dev.yml exec leave-bot alembic revision --autogenerate -m "msg"`
- DB をまっさらに戻したい時:
  ```bash
  docker compose -f docker-compose.dev.yml down -v
  ```

## 本番デプロイ
ローカルで動作確認 → コミット → 本番サーバ側で `git pull` → 本番の compose を
再ビルドして再起動:
```bash
# 本番サーバ側
cd /home/mukai/docker
docker compose \
  -f docker-compose.yml \
  -f docker-compose.without-nginx.yml \
  -f docker-compose.leave-bot.yml \
  up -d --build leave-bot
```

## Slash コマンド疎通テスト (Mattermost なし)
```bash
curl -sS -X POST http://127.0.0.1:8088/slash/leave \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'token=dummy' \
  --data-urlencode 'user_id=test123' \
  --data-urlencode 'user_name=tester' \
  --data-urlencode 'text=ping'
```
