"""proxy approvers as join tables (many-to-many); drop single-value columns

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-20

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "user_proxy_approvers",
        sa.Column(
            "user_id",
            sa.String(length=26),
            sa.ForeignKey("users.mm_user_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "proxy_mm_id",
            sa.String(length=26),
            sa.ForeignKey("users.mm_user_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_user_proxy_approvers_proxy", "user_proxy_approvers", ["proxy_mm_id"]
    )

    op.create_table(
        "department_proxy_approvers",
        sa.Column(
            "department_id",
            sa.Integer(),
            sa.ForeignKey("departments.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "proxy_mm_id",
            sa.String(length=26),
            sa.ForeignKey("users.mm_user_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_department_proxy_approvers_proxy",
        "department_proxy_approvers",
        ["proxy_mm_id"],
    )

    # Copy existing single-value data to join tables before dropping columns.
    op.execute(
        "INSERT INTO user_proxy_approvers (user_id, proxy_mm_id) "
        "SELECT mm_user_id, proxy_approver_mm_id FROM users "
        "WHERE proxy_approver_mm_id IS NOT NULL"
    )
    op.execute(
        "INSERT INTO department_proxy_approvers (department_id, proxy_mm_id) "
        "SELECT id, proxy_approver_mm_id FROM departments "
        "WHERE proxy_approver_mm_id IS NOT NULL"
    )

    # Drop old single-value columns.
    op.drop_index("ix_users_proxy_approver_mm_id", table_name="users")
    op.drop_constraint("fk_users_proxy_approver", "users", type_="foreignkey")
    op.drop_column("users", "proxy_approver_mm_id")
    op.drop_column("departments", "proxy_approver_mm_id")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("proxy_approver_mm_id", sa.String(length=26), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_proxy_approver",
        "users",
        "users",
        ["proxy_approver_mm_id"],
        ["mm_user_id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_users_proxy_approver_mm_id", "users", ["proxy_approver_mm_id"]
    )
    op.add_column(
        "departments",
        sa.Column("proxy_approver_mm_id", sa.String(length=26), nullable=True),
    )

    op.drop_index(
        "ix_department_proxy_approvers_proxy", table_name="department_proxy_approvers"
    )
    op.drop_table("department_proxy_approvers")
    op.drop_index(
        "ix_user_proxy_approvers_proxy", table_name="user_proxy_approvers"
    )
    op.drop_table("user_proxy_approvers")
