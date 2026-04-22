"""Revision ID: add_wigs_category
Revises: 014_add_admin_users_table
Create Date: 2026-04-22
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "add_wigs_category"
down_revision = "014_add_admin_users_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE category ADD VALUE IF NOT EXISTS 'WIGS'")


def downgrade() -> None:
    # PostgreSQL enum value removal is intentionally not automated.
    pass

