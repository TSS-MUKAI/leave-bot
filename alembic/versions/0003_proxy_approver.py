"""add users.proxy_approver_mm_id; drop approvals unique(stage) to allow manager+proxy co-approvers

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-20

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
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
    op.drop_constraint(
        "uq_approvals_request_stage", "approvals", type_="unique"
    )
    op.add_column(
        "approvals",
        sa.Column("dm_post_id", sa.String(length=26), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("approvals", "dm_post_id")
    op.create_unique_constraint(
        "uq_approvals_request_stage", "approvals", ["request_id", "stage"]
    )
    op.drop_index("ix_users_proxy_approver_mm_id", table_name="users")
    op.drop_constraint("fk_users_proxy_approver", "users", type_="foreignkey")
    op.drop_column("users", "proxy_approver_mm_id")
