"""add coaches and coach-athletes mapping

Revision ID: 0003_add_coaches_and_coach_athletes
Revises: 0002_add_user_display_name
Create Date: 2026-01-26

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "0003_add_coaches_and_coach_athletes"
down_revision = "0002_add_user_display_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # --- users.is_coach
    cols = [c.get("name") for c in inspect(bind).get_columns("users")]
    if "is_coach" not in cols:
        # SQLite cannot reliably add a NOT NULL column without a default + alter.
        if dialect == "sqlite":
            op.add_column("users", sa.Column("is_coach", sa.Boolean(), nullable=True))
        else:
            op.add_column(
                "users",
                sa.Column("is_coach", sa.Boolean(), nullable=False, server_default=sa.false()),
            )

    # Backfill
    op.execute("UPDATE users SET is_coach = 0 WHERE is_coach IS NULL")

    # Enforce NOT NULL where supported.
    if dialect != "sqlite":
        op.alter_column("users", "is_coach", server_default=None)
        op.alter_column("users", "is_coach", existing_type=sa.Boolean(), nullable=False)

    # --- coach_athletes table
    tables = set(inspect(bind).get_table_names())
    if "coach_athletes" not in tables:
        op.create_table(
            "coach_athletes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("coach_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("athlete_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("coach_user_id", "athlete_user_id", name="uq_coach_athletes_pair"),
        )
        op.create_index("ix_coach_athletes_coach_user_id", "coach_athletes", ["coach_user_id"], unique=False)
        op.create_index("ix_coach_athletes_athlete_user_id", "coach_athletes", ["athlete_user_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    tables = set(inspector.get_table_names())
    if "coach_athletes" in tables:
        op.drop_index("ix_coach_athletes_athlete_user_id", table_name="coach_athletes")
        op.drop_index("ix_coach_athletes_coach_user_id", table_name="coach_athletes")
        op.drop_table("coach_athletes")

    cols = [c.get("name") for c in inspector.get_columns("users")]
    if "is_coach" in cols:
        op.drop_column("users", "is_coach")
