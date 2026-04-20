"""half-day leave integrity constraints

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-20

"""
from __future__ import annotations

from alembic import op


revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_leave_requests_leave_type",
        "leave_requests",
        "leave_type IN ('paid', 'half_am', 'half_pm', 'special')",
    )
    op.create_check_constraint(
        "ck_leave_requests_half_day_shape",
        "leave_requests",
        "(leave_type NOT IN ('half_am', 'half_pm')) "
        "OR (start_date = end_date AND business_days = 0.5)",
    )
    op.create_check_constraint(
        "ck_leave_requests_business_days_positive",
        "leave_requests",
        "business_days > 0 AND (business_days * 2) = floor(business_days * 2)",
    )
    op.create_check_constraint(
        "ck_leave_requests_date_order",
        "leave_requests",
        "start_date <= end_date",
    )


def downgrade() -> None:
    op.drop_constraint("ck_leave_requests_date_order", "leave_requests", type_="check")
    op.drop_constraint(
        "ck_leave_requests_business_days_positive", "leave_requests", type_="check"
    )
    op.drop_constraint(
        "ck_leave_requests_half_day_shape", "leave_requests", type_="check"
    )
    op.drop_constraint("ck_leave_requests_leave_type", "leave_requests", type_="check")
