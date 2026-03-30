"""Revision ID: add_seller_residence_fields
Revises: add_skincare_category
Create Date: 2026-03-30 13:10:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_seller_residence_fields"
down_revision = "add_skincare_category"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("seller_profiles", sa.Column("hall", sa.String(length=255), nullable=True))
    op.add_column("seller_profiles", sa.Column("room_number", sa.String(length=50), nullable=True))
    op.add_column("seller_profiles", sa.Column("address", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("seller_profiles", "address")
    op.drop_column("seller_profiles", "room_number")
    op.drop_column("seller_profiles", "hall")
