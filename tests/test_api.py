from __future__ import annotations


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_slash_ping(client):
    r = client.post(
        "/slash/leave",
        data={"token": "", "user_id": "u1", "user_name": "tester", "text": "ping"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["response_type"] == "ephemeral"
    assert "pong" in body["text"]


def test_slash_menu_when_no_args(client):
    r = client.post("/slash/leave", data={"user_id": "u1", "user_name": "tester"})
    assert r.status_code == 200
    assert r.json()["response_type"] == "ephemeral"


def test_apply_dialog_submission_creates_request(client, db, seeded, fake_mm):
    body = {
        "callback_id": "leave_apply",
        "user_id": seeded["alice"],
        "user_name": "alice",
        "submission": {
            "leave_type": "paid",
            "start_date": "2026-04-20",
            "end_date": "2026-04-20",
            "reason": "vacation",
        },
    }
    r = client.post("/interactive/dialog", json=body)
    assert r.status_code == 200
    assert r.json() == {}

    from app.db.models import LeaveRequest

    reqs = list(db.query(LeaveRequest).all())
    assert len(reqs) == 1
    assert reqs[0].status == "pending"

    # Bot should have DM'd the manager and the applicant.
    to_ids = [d["to"] for d in fake_mm.dms]
    assert seeded["bob"] in to_ids
    assert seeded["alice"] in to_ids


def test_apply_validation_errors(client, seeded):
    body = {
        "callback_id": "leave_apply",
        "user_id": seeded["alice"],
        "user_name": "alice",
        "submission": {
            "leave_type": "",
            "start_date": "",
            "end_date": "",
            "reason": "",
        },
    }
    r = client.post("/interactive/dialog", json=body)
    assert r.status_code == 200
    errors = r.json()["errors"]
    assert set(errors.keys()) == {"leave_type", "start_date", "end_date", "reason"}


def test_end_to_end_approve_via_action(client, db, seeded, fake_mm):
    # 1. Submit application
    r = client.post(
        "/interactive/dialog",
        json={
            "callback_id": "leave_apply",
            "user_id": seeded["alice"],
            "submission": {
                "leave_type": "paid",
                "start_date": "2026-04-20",
                "end_date": "2026-04-20",
                "reason": "vacation",
            },
        },
    )
    assert r.status_code == 200

    from app.db.models import LeaveRequest

    req = db.query(LeaveRequest).one()

    # 2. Manager clicks approve
    r = client.post(
        "/interactive/action",
        json={
            "user_id": seeded["bob"],
            "user_name": "bob",
            "context": {"action": "approve", "request_id": req.id},
        },
    )
    assert r.status_code == 200

    db.refresh(req)
    assert req.status == "pending"
    assert req.current_stage == 2

    # 3. HR approves
    r = client.post(
        "/interactive/action",
        json={
            "user_id": seeded["carol"],
            "user_name": "carol",
            "context": {"action": "approve", "request_id": req.id},
        },
    )
    assert r.status_code == 200
    db.refresh(req)
    assert req.status == "approved"


def test_reject_dialog_requires_comment(client, db, seeded):
    # seed a pending request
    from app.services import leave as leave_svc
    from datetime import date
    from decimal import Decimal

    req = leave_svc.create_request(
        db,
        applicant_id=seeded["alice"],
        leave_type="paid",
        start_date=date(2026, 4, 20),
        end_date=date(2026, 4, 20),
        business_days=Decimal("1"),
        reason="x",
    )

    # submit reject dialog with empty comment
    r = client.post(
        "/interactive/dialog",
        json={
            "callback_id": "leave_reject",
            "user_id": seeded["bob"],
            "state": str(req.id),
            "submission": {"comment": "  "},
        },
    )
    assert r.status_code == 200
    assert r.json()["errors"]["comment"]
