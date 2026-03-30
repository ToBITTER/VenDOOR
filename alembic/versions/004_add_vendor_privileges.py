"""Revision ID: add_vendor_privileges
Revises: add_seller_residence_fields
Create Date: 2026-03-30 17:35:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_vendor_privileges"
down_revision = "add_seller_residence_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "seller_profiles",
        sa.Column("is_featured", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "seller_profiles",
        sa.Column("priority_score", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("seller_profiles", "is_featured", server_default=None)
    op.alter_column("seller_profiles", "priority_score", server_default=None)


def downgrade() -> None:
    op.drop_column("seller_profiles", "priority_score")
    op.drop_column("seller_profiles", "is_featured")
