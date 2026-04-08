"""Add admin_users table for super/ops admin roles.

Revision ID: 014_add_admin_users_table
Revises: 013_payout_tracking
Create Date: 2026-04-08 16:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "014_add_admin_users_table"
down_revision = "013_payout_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.String(length=255), nullable=False),
        sa.Column("role", sa.Enum("SUPER_ADMIN", "OPS_ADMIN", name="adminrole"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_id"),
    )
    op.create_index("ix_admin_users_role", "admin_users", ["role"], unique=False)
    op.create_index("ix_admin_users_telegram_id", "admin_users", ["telegram_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_admin_users_telegram_id", table_name="admin_users")
    op.drop_index("ix_admin_users_role", table_name="admin_users")
    op.drop_table("admin_users")
    op.execute("DROP TYPE IF EXISTS adminrole")
