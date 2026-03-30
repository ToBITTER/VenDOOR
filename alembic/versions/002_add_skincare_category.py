"""Revision ID: add_skincare_category
Revises: initial_schema
Create Date: 2026-03-30 12:40:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "add_skincare_category"
down_revision = "initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE category ADD VALUE IF NOT EXISTS 'SKINCARE'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely in-place.
    pass
