"""users + garmin_accounts

Revision ID: 0001_users_and_garmin_accounts
Revises: 
Create Date: 2026-01-01

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_users_and_garmin_accounts"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=80), nullable=False),
        sa.Column("pin_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.UniqueConstraint("user_id", name="uq_users_user_id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_user_id", "users", ["user_id"], unique=True)

    op.create_table(
        "garmin_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("garmin_email", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_garmin_accounts_user_id"),
    )
    op.create_index("ix_garmin_accounts_user_id", "garmin_accounts", ["user_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_garmin_accounts_user_id", table_name="garmin_accounts")
    op.drop_table("garmin_accounts")
    op.drop_index("ix_users_user_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
