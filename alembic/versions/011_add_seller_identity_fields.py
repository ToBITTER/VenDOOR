"""Add seller full_name and level fields.

Revision ID: add_seller_identity_fields
Revises: add_delivery_order_join_table
Create Date: 2026-04-06
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "add_seller_identity_fields"
down_revision = "add_delivery_order_join_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("seller_profiles", sa.Column("full_name", sa.String(length=255), nullable=True))
    op.add_column("seller_profiles", sa.Column("level", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("seller_profiles", "level")
    op.drop_column("seller_profiles", "full_name")
