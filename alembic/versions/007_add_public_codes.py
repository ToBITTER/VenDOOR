"""Revision ID: add_public_codes
Revises: add_listing_quantity
Create Date: 2026-03-30 21:05:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import table, column
import uuid

# revision identifiers, used by Alembic.
revision = "add_public_codes"
down_revision = "add_listing_quantity"
branch_labels = None
depends_on = None


def _new_code(prefix: str, used: set[str]) -> str:
    while True:
        code = f"{prefix}-{uuid.uuid4().hex[:8].upper()}"
        if code not in used:
            used.add(code)
            return code


def upgrade() -> None:
    op.add_column("seller_profiles", sa.Column("seller_code", sa.String(length=20), nullable=True))
    op.add_column("listings", sa.Column("listing_code", sa.String(length=20), nullable=True))

    conn = op.get_bind()
    seller_profiles = table(
        "seller_profiles",
        column("id", sa.Integer),
        column("seller_code", sa.String),
    )
    listings = table(
        "listings",
        column("id", sa.Integer),
        column("listing_code", sa.String),
    )

    seller_rows = conn.execute(sa.select(seller_profiles.c.id)).fetchall()
    used_seller_codes: set[str] = set()
    for row in seller_rows:
        code = _new_code("SEL", used_seller_codes)
        conn.execute(
            seller_profiles.update()
            .where(seller_profiles.c.id == row.id)
            .values(seller_code=code)
        )

    listing_rows = conn.execute(sa.select(listings.c.id)).fetchall()
    used_listing_codes: set[str] = set()
    for row in listing_rows:
        code = _new_code("LST", used_listing_codes)
        conn.execute(
            listings.update()
            .where(listings.c.id == row.id)
            .values(listing_code=code)
        )

    op.alter_column("seller_profiles", "seller_code", nullable=False)
    op.alter_column("listings", "listing_code", nullable=False)
    op.create_index("ix_seller_profiles_seller_code", "seller_profiles", ["seller_code"], unique=True)
    op.create_index("ix_listings_listing_code", "listings", ["listing_code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_listings_listing_code", table_name="listings")
    op.drop_index("ix_seller_profiles_seller_code", table_name="seller_profiles")
    op.drop_column("listings", "listing_code")
    op.drop_column("seller_profiles", "seller_code")
