#!/usr/bin/env bash
# Simulate the full leave-apply -> approve flow against a running dev leave-bot,
# bypassing Mattermost entirely (DevNoopMattermost logs outbound calls).
#
# Prerequisites:
#   docker compose -f docker-compose.dev.yml up -d
#   docker compose -f docker-compose.dev.yml exec leave-bot alembic upgrade head
#   docker compose -f docker-compose.dev.yml exec leave-bot python scripts/seed_dev.py
#
# Usage:  ./scripts/dev_apply.sh
#
# Windows users: run the equivalent curl.exe commands from DEV.md, or run this
# script under WSL / Git Bash.
set -euo pipefail

BOT="${BOT:-http://127.0.0.1:8088}"
ALICE="alice0000000000000000000aa"
BOB="bob00000000000000000000aaa"
CAROL="carol00000000000000000aaaa"

echo "[1/3] submit apply dialog as alice"
curl -sS -X POST "$BOT/interactive/dialog" \
  -H 'Content-Type: application/json' \
  -d "{
    \"callback_id\": \"leave_apply\",
    \"user_id\": \"$ALICE\",
    \"user_name\": \"alice\",
    \"submission\": {
      \"leave_type\": \"paid\",
      \"start_date\": \"2026-04-20\",
      \"end_date\": \"2026-04-20\",
      \"reason\": \"local dev smoke test\"
    }
  }"
echo

REQ_ID=$(curl -sS "$BOT/admin/requests.json" 2>/dev/null | head -c 0 || true)
# Fallback: fetch newest id from the API (admin endpoints need basic auth so we
# use a simple log-inspection approach instead).
echo "[2/3] resolve newest request id from container log"
REQ_ID=$(docker compose -f docker-compose.dev.yml exec -T leave-bot \
  python -c "from app.db.session import SessionLocal; from app.db.models import LeaveRequest
db = SessionLocal()
try:
    r = db.query(LeaveRequest).order_by(LeaveRequest.id.desc()).first()
    print(r.id if r else '')
finally:
    db.close()")
echo "  request_id=$REQ_ID"

echo "[3/3] manager approves, then hr approves"
curl -sS -X POST "$BOT/interactive/action" \
  -H 'Content-Type: application/json' \
  -d "{\"user_id\": \"$BOB\", \"user_name\": \"bob\",
       \"context\": {\"action\": \"approve\", \"request_id\": $REQ_ID}}"
echo
curl -sS -X POST "$BOT/interactive/action" \
  -H 'Content-Type: application/json' \
  -d "{\"user_id\": \"$CAROL\", \"user_name\": \"carol\",
       \"context\": {\"action\": \"approve\", \"request_id\": $REQ_ID}}"
echo
echo "done. check status in admin UI: http://127.0.0.1:8088/admin/requests/$REQ_ID"
