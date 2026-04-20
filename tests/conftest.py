from __future__ import annotations

import os
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker


TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://leavebot:leavebot@postgres:5432/leavebot_test",
)


@pytest.fixture(scope="session")
def _test_engine():
    from app.db.models import Base

    eng = create_engine(TEST_DB_URL, future=True, pool_pre_ping=True)
    # Reset via schema-level DROP to avoid SQLAlchemy's table-sort failure on
    # the users<->departments FK cycle (drop_all can't topologically order them).
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(_test_engine) -> Iterator[Session]:
    from app.db.models import Base

    SessionLocal = sessionmaker(
        bind=_test_engine, autoflush=False, autocommit=False, future=True
    )
    sess = SessionLocal()
    try:
        yield sess
    finally:
        sess.close()
        # users<->departments have a FK cycle, so truncate all tables in one
        # CASCADE statement instead of per-table (which needs a topological sort).
        names = ", ".join(f'"{t.name}"' for t in Base.metadata.tables.values())
        with _test_engine.begin() as c:
            c.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))


class FakeMattermost:
    """In-memory stand-in for the real MattermostClient used throughout the app."""

    def __init__(self) -> None:
        self.dms: list[dict] = []
        self.updates: list[dict] = []
        self.dialogs: list[dict] = []
        self._post_counter = 0

    def send_dm(self, to_user_id: str, message: str, props: dict | None = None) -> dict:
        self._post_counter += 1
        post_id = f"post{self._post_counter:022d}"
        self.dms.append({"to": to_user_id, "message": message, "props": props, "id": post_id})
        return {"id": post_id}

    def update_post(self, post_id: str, message: str, props: dict | None = None) -> dict:
        self.updates.append({"id": post_id, "message": message, "props": props})
        return {}

    def open_dialog(self, trigger_id: str, url: str, dialog: dict) -> None:
        self.dialogs.append({"trigger_id": trigger_id, "url": url, "dialog": dialog})

    def get_user(self, user_id: str) -> dict:
        return {
            "id": user_id,
            "username": f"u_{user_id[:6]}",
            "first_name": "",
            "last_name": "",
            "email": f"{user_id}@example.com",
        }

    def get_user_by_username(self, username: str) -> dict | None:
        return {
            "id": username.ljust(26, "0")[:26],
            "username": username,
            "first_name": "",
            "last_name": "",
            "email": f"{username}@example.com",
        }


@pytest.fixture
def fake_mm(monkeypatch) -> FakeMattermost:
    fake = FakeMattermost()

    def _get() -> FakeMattermost:
        return fake

    import app.mattermost as mm_mod
    import app.routers.interactive as interactive_mod
    import app.routers.slash as slash_mod

    monkeypatch.setattr(mm_mod, "get_mattermost", _get)
    monkeypatch.setattr(interactive_mod, "get_mattermost", _get)
    monkeypatch.setattr(slash_mod, "get_mattermost", _get)
    return fake


@pytest.fixture
def client(db, fake_mm) -> Iterator[TestClient]:
    from app.db.session import get_db
    from app.main import app

    def _override_db() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def seeded(db):
    """Seed three users: alice (applicant) -> bob (manager), carol (hr)."""
    from app.services import users as user_svc

    alice = "alice0000000000000000000aa"
    bob = "bob00000000000000000000aaa"
    carol = "carol00000000000000000aaaa"
    assert len(alice) == len(bob) == len(carol) == 26

    user_svc.upsert_user(db, mm_user_id=alice, username="alice", display_name="Alice")
    user_svc.upsert_user(db, mm_user_id=bob, username="bob", display_name="Bob")
    user_svc.upsert_user(db, mm_user_id=carol, username="carol", display_name="Carol")
    user_svc.update_user(db, mm_user_id=bob, role="manager")
    user_svc.update_user(db, mm_user_id=carol, role="hr")
    user_svc.set_manager(db, alice, bob)
    return {"alice": alice, "bob": bob, "carol": carol}
