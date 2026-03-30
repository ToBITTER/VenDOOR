"""Revision ID: add_listing_quantity
Revises: add_accessory_subcategory
Create Date: 2026-03-30 19:10:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_listing_quantity"
down_revision = "add_accessory_subcategory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "listings",
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
    )
    op.execute("UPDATE listings SET quantity = 1 WHERE quantity IS NULL OR quantity < 1")
    op.alter_column("listings", "quantity", server_default=None)


def downgrade() -> None:
    op.drop_column("listings", "quantity")
