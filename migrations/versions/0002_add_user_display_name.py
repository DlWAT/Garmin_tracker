"""add user display name

Revision ID: 0002_add_user_display_name
Revises: 0001_users_and_garmin_accounts
Create Date: 2026-01-01

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "0002_add_user_display_name"
down_revision = "0001_users_and_garmin_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    cols = [c.get("name") for c in inspect(bind).get_columns("users")]
    if "display_name" not in cols:
        # SQLite cannot add a NOT NULL column without a default, and cannot ALTER COLUMN.
        if dialect == "sqlite":
            op.add_column("users", sa.Column("display_name", sa.String(length=80), nullable=True))
        else:
            op.add_column(
                "users",
                sa.Column("display_name", sa.String(length=80), nullable=False, server_default=""),
            )

    # Backfill existing rows.
    op.execute("UPDATE users SET display_name = user_id WHERE display_name IS NULL OR display_name = ''")

    # Enforce NOT NULL where supported.
    if dialect != "sqlite":
        op.alter_column("users", "display_name", server_default=None)
        op.alter_column("users", "display_name", existing_type=sa.String(length=80), nullable=False)


def downgrade() -> None:
    op.drop_column("users", "display_name")
