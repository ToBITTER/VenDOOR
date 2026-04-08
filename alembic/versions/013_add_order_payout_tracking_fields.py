"""Add seller payout tracking fields on orders.

Revision ID: 013_add_order_payout_tracking_fields
Revises: 012_add_webhook_receipts_table
Create Date: 2026-04-08 13:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "013_add_order_payout_tracking_fields"
down_revision = "012_add_webhook_receipts_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("seller_payout_ref", sa.String(length=255), nullable=True))
    op.add_column("orders", sa.Column("seller_payout_status", sa.String(length=50), nullable=True))
    op.add_column("orders", sa.Column("seller_payout_attempted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_orders_seller_payout_status", "orders", ["seller_payout_status"], unique=False)
    op.create_unique_constraint("uq_orders_seller_payout_ref", "orders", ["seller_payout_ref"])


def downgrade() -> None:
    op.drop_constraint("uq_orders_seller_payout_ref", "orders", type_="unique")
    op.drop_index("ix_orders_seller_payout_status", table_name="orders")
    op.drop_column("orders", "seller_payout_attempted_at")
    op.drop_column("orders", "seller_payout_status")
    op.drop_column("orders", "seller_payout_ref")
