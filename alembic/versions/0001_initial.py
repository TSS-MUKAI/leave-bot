"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-20

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("mm_user_id", sa.String(length=26), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("email", sa.String(length=256)),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="employee"),
        sa.Column(
            "manager_mm_id",
            sa.String(length=26),
            sa.ForeignKey("users.mm_user_id", ondelete="SET NULL"),
        ),
        sa.Column("hire_date", sa.Date()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_users_role", "users", ["role"])
    op.create_index("ix_users_manager_mm_id", "users", ["manager_mm_id"])

    op.create_table(
        "leave_grants",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(length=26),
            sa.ForeignKey("users.mm_user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("granted_days", sa.Numeric(5, 1), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "fiscal_year", name="uq_leave_grants_user_fy"),
    )

    op.create_table(
        "leave_requests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(length=26),
            sa.ForeignKey("users.mm_user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("leave_type", sa.String(length=16), nullable=False, server_default="paid"),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("business_days", sa.Numeric(5, 1), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("current_stage", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_leave_requests_user_id", "leave_requests", ["user_id"])
    op.create_index("ix_leave_requests_status", "leave_requests", ["status"])
    op.create_index("ix_leave_requests_start_date", "leave_requests", ["start_date"])

    op.create_table(
        "approvals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "request_id",
            sa.Integer(),
            sa.ForeignKey("leave_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column(
            "approver_id",
            sa.String(length=26),
            sa.ForeignKey("users.mm_user_id", ondelete="SET NULL"),
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("comment", sa.Text()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("request_id", "stage", name="uq_approvals_request_stage"),
    )
    op.create_index("ix_approvals_request_id", "approvals", ["request_id"])
    op.create_index("ix_approvals_approver_id", "approvals", ["approver_id"])
    op.create_index("ix_approvals_status", "approvals", ["status"])

    op.create_table(
        "leave_audit",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "request_id",
            sa.Integer(),
            sa.ForeignKey("leave_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_id",
            sa.String(length=26),
            sa.ForeignKey("users.mm_user_id", ondelete="SET NULL"),
        ),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_leave_audit_request_id", "leave_audit", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_leave_audit_request_id", table_name="leave_audit")
    op.drop_table("leave_audit")
    op.drop_index("ix_approvals_status", table_name="approvals")
    op.drop_index("ix_approvals_approver_id", table_name="approvals")
    op.drop_index("ix_approvals_request_id", table_name="approvals")
    op.drop_table("approvals")
    op.drop_index("ix_leave_requests_start_date", table_name="leave_requests")
    op.drop_index("ix_leave_requests_status", table_name="leave_requests")
    op.drop_index("ix_leave_requests_user_id", table_name="leave_requests")
    op.drop_table("leave_requests")
    op.drop_table("leave_grants")
    op.drop_index("ix_users_manager_mm_id", table_name="users")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_table("users")
