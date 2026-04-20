"""add departments + users.department_id

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-20

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "manager_mm_id",
            sa.String(length=26),
            sa.ForeignKey("users.mm_user_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "proxy_approver_mm_id",
            sa.String(length=26),
            sa.ForeignKey("users.mm_user_id", ondelete="SET NULL"),
        ),
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
        sa.UniqueConstraint("name", name="uq_departments_name"),
    )
    op.add_column(
        "users",
        sa.Column("department_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_department",
        "users",
        "departments",
        ["department_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_users_department_id", "users", ["department_id"])


def downgrade() -> None:
    op.drop_index("ix_users_department_id", table_name="users")
    op.drop_constraint("fk_users_department", "users", type_="foreignkey")
    op.drop_column("users", "department_id")
    op.drop_table("departments")
