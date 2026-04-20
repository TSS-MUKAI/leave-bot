"""Seed three users for local manual testing:
  alice  (employee, applicant) -> manager: bob
  bob    (manager)
  carol  (hr)

Run inside the dev leave-bot container:
  docker compose -f docker-compose.dev.yml exec leave-bot python scripts/seed_dev.py
"""
from __future__ import annotations

from app.db.session import SessionLocal
from app.services import users as user_svc


ALICE = "alice0000000000000000000aa"
BOB = "bob00000000000000000000aaa"
CAROL = "carol00000000000000000aaaa"


def main() -> None:
    db = SessionLocal()
    try:
        user_svc.upsert_user(db, mm_user_id=ALICE, username="alice", display_name="Alice")
        user_svc.upsert_user(db, mm_user_id=BOB, username="bob", display_name="Bob")
        user_svc.upsert_user(db, mm_user_id=CAROL, username="carol", display_name="Carol")
        user_svc.update_user(db, mm_user_id=BOB, role="manager")
        user_svc.update_user(db, mm_user_id=CAROL, role="hr")
        user_svc.set_manager(db, ALICE, BOB)
        print(f"seeded: alice={ALICE} -> manager bob={BOB}, hr carol={CAROL}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
