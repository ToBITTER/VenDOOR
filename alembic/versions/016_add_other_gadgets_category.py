"""Revision ID: add_other_gadgets_category
Revises: add_wigs_category
Create Date: 2026-04-22
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "add_other_gadgets_category"
down_revision = "add_wigs_category"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE category ADD VALUE IF NOT EXISTS 'OTHERGADGETS'")


def downgrade() -> None:
    # PostgreSQL enum value removal is intentionally not automated.
    pass

