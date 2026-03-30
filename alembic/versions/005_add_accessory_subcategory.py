"""Revision ID: add_accessory_subcategory
Revises: add_vendor_privileges
Create Date: 2026-03-30 18:20:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "add_accessory_subcategory"
down_revision = "add_vendor_privileges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    accessorysubcategory = postgresql.ENUM(
        "BAGS",
        "JEWELRY",
        "WATCHES",
        name="accessorysubcategory",
        create_type=False,
    )
    accessorysubcategory.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "listings",
        sa.Column("accessory_subcategory", accessorysubcategory, nullable=True),
    )
    op.create_index(
        "ix_listings_accessory_subcategory",
        "listings",
        ["accessory_subcategory"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_listings_accessory_subcategory", table_name="listings")
    op.drop_column("listings", "accessory_subcategory")
    postgresql.ENUM(name="accessorysubcategory").drop(op.get_bind(), checkfirst=True)
