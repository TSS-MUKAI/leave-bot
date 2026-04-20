from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# Role values: employee / manager / hr / admin
# leave_type: paid / half_am / half_pm / special
# request status: pending / approved / rejected / canceled
# approval status: pending / approved / rejected / skipped


class User(Base):
    __tablename__ = "users"

    mm_user_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    email: Mapped[str | None] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="employee")
    manager_mm_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("users.mm_user_id", ondelete="SET NULL")
    )
    department_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("departments.id", ondelete="SET NULL")
    )
    hire_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    manager_mm_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("users.mm_user_id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (UniqueConstraint("name", name="uq_departments_name"),)


class UserProxyApprover(Base):
    __tablename__ = "user_proxy_approvers"

    user_id: Mapped[str] = mapped_column(
        String(26),
        ForeignKey("users.mm_user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    proxy_mm_id: Mapped[str] = mapped_column(
        String(26),
        ForeignKey("users.mm_user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DepartmentProxyApprover(Base):
    __tablename__ = "department_proxy_approvers"

    department_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("departments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    proxy_mm_id: Mapped[str] = mapped_column(
        String(26),
        ForeignKey("users.mm_user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LeaveGrant(Base):
    __tablename__ = "leave_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("users.mm_user_id", ondelete="CASCADE"), nullable=False
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    granted_days: Mapped[float] = mapped_column(Numeric(5, 1), nullable=False)
    expires_on: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "fiscal_year", name="uq_leave_grants_user_fy"),
    )


class LeaveRequest(Base):
    __tablename__ = "leave_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("users.mm_user_id", ondelete="RESTRICT"), nullable=False
    )
    leave_type: Mapped[str] = mapped_column(String(16), nullable=False, default="paid")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    business_days: Mapped[float] = mapped_column(Numeric(5, 1), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    current_stage: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "leave_type IN ('paid', 'half_am', 'half_pm', 'special')",
            name="ck_leave_requests_leave_type",
        ),
        CheckConstraint(
            "(leave_type NOT IN ('half_am', 'half_pm')) "
            "OR (start_date = end_date AND business_days = 0.5)",
            name="ck_leave_requests_half_day_shape",
        ),
        CheckConstraint(
            "business_days > 0 AND (business_days * 2) = floor(business_days * 2)",
            name="ck_leave_requests_business_days_positive",
        ),
        CheckConstraint(
            "start_date <= end_date",
            name="ck_leave_requests_date_order",
        ),
    )


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("leave_requests.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # manager / hr
    approver_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("users.mm_user_id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    dm_post_id: Mapped[str | None] = mapped_column(String(26))


class LeaveAudit(Base):
    __tablename__ = "leave_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("leave_requests.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("users.mm_user_id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
