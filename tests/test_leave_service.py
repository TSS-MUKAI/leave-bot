from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services import leave as leave_svc


def _create(db, applicant, **kw):
    defaults = dict(
        leave_type="paid",
        start_date=date(2026, 4, 20),
        end_date=date(2026, 4, 20),
        business_days=Decimal("1"),
        reason="test",
    )
    defaults.update(kw)
    return leave_svc.create_request(db, applicant_id=applicant, **defaults)


def test_calc_business_days_half_day():
    d = date(2026, 4, 20)
    assert leave_svc.calc_business_days("half_am", d, d) == Decimal("0.5")
    assert leave_svc.calc_business_days("half_pm", d, d) == Decimal("0.5")


def test_calc_business_days_skips_weekend():
    # Mon 4/20 .. Sun 4/26 -> Mon-Fri = 5
    assert (
        leave_svc.calc_business_days("paid", date(2026, 4, 20), date(2026, 4, 26))
        == Decimal("5")
    )


def test_calc_business_days_single_weekend_day_is_zero():
    assert (
        leave_svc.calc_business_days("paid", date(2026, 4, 25), date(2026, 4, 25))
        == Decimal("0")
    )


def test_create_request_builds_stage1_and_stage2(db, seeded):
    req = _create(db, seeded["alice"])
    assert req.status == "pending"
    assert req.current_stage == 1

    stage1 = leave_svc.pending_approvals_at(db, req.id, 1)
    assert [r.role for r in stage1] == ["manager"]
    assert stage1[0].approver_id == seeded["bob"]

    stage2 = leave_svc.pending_approvals_at(db, req.id, 2)
    assert [r.role for r in stage2] == ["hr"]


def test_create_request_rejects_unknown_applicant(db):
    with pytest.raises(ValueError):
        leave_svc.create_request(
            db,
            applicant_id="unknown00000000000000000aa",
            leave_type="paid",
            start_date=date(2026, 4, 20),
            end_date=date(2026, 4, 20),
            business_days=Decimal("1"),
            reason="x",
        )


def test_create_request_rejects_no_manager(db):
    from app.services import users as user_svc

    orphan = "orphan000000000000000aaaaa"
    user_svc.upsert_user(db, mm_user_id=orphan, username="orphan")
    with pytest.raises(ValueError):
        _create(db, orphan)


def test_two_stage_approval_finalizes(db, seeded):
    req = _create(db, seeded["alice"])
    r1 = leave_svc.decide(
        db, request_id=req.id, actor_id=seeded["bob"], decision="approved"
    )
    assert r1.finalized is None
    assert r1.advanced_to == 2

    r2 = leave_svc.decide(
        db, request_id=req.id, actor_id=seeded["carol"], decision="approved"
    )
    assert r2.finalized == "approved"
    assert r2.request.status == "approved"


def test_reject_finalizes_immediately(db, seeded):
    req = _create(db, seeded["alice"])
    r = leave_svc.decide(
        db,
        request_id=req.id,
        actor_id=seeded["bob"],
        decision="rejected",
        comment="no",
    )
    assert r.finalized == "rejected"
    assert r.request.status == "rejected"


def test_self_approval_blocked(db, seeded):
    req = _create(db, seeded["alice"])
    with pytest.raises(ValueError):
        leave_svc.decide(
            db,
            request_id=req.id,
            actor_id=seeded["alice"],
            decision="approved",
        )


def test_non_approver_blocked(db, seeded):
    from app.services import users as user_svc

    intruder = "intru00000000000000000aaaa"
    user_svc.upsert_user(db, mm_user_id=intruder, username="intruder")

    req = _create(db, seeded["alice"])
    with pytest.raises(ValueError):
        leave_svc.decide(
            db, request_id=req.id, actor_id=intruder, decision="approved"
        )


def test_cancel_by_owner(db, seeded):
    req = _create(db, seeded["alice"])
    r = leave_svc.cancel_request(
        db, request_id=req.id, actor_id=seeded["alice"], reason="changed mind"
    )
    assert r.request.status == "canceled"
    # Pending approvals should be skipped
    assert all(row.status == "skipped" for row in r.pending_rows)


def test_cancel_by_other_fails(db, seeded):
    req = _create(db, seeded["alice"])
    with pytest.raises(ValueError):
        leave_svc.cancel_request(
            db, request_id=req.id, actor_id=seeded["bob"], reason=None
        )


def test_admin_override_cancel_after_approval(db, seeded):
    req = _create(db, seeded["alice"])
    leave_svc.decide(
        db, request_id=req.id, actor_id=seeded["bob"], decision="approved"
    )
    leave_svc.decide(
        db, request_id=req.id, actor_id=seeded["carol"], decision="approved"
    )
    r = leave_svc.cancel_request(
        db,
        request_id=req.id,
        actor_id=None,
        reason="mistake",
        admin_override=True,
        admin_label="admin",
    )
    assert r.request.status == "canceled"


def test_half_day_requires_same_day(db, seeded):
    with pytest.raises(ValueError):
        _create(
            db,
            seeded["alice"],
            leave_type="half_am",
            start_date=date(2026, 4, 20),
            end_date=date(2026, 4, 21),
            business_days=Decimal("0.5"),
        )
