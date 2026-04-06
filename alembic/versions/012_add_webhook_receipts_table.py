"""Add webhook_receipts table for webhook idempotency.

Revision ID: add_webhook_receipts_table
Revises: add_seller_identity_fields
Create Date: 2026-04-06
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "add_webhook_receipts_table"
down_revision = "add_seller_identity_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=True),
        sa.Column("reference", sa.String(length=255), nullable=True),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "event_type",
            "reference",
            name="uq_webhook_receipt_provider_event_reference",
        ),
    )
    op.create_index("ix_webhook_receipts_created_at", "webhook_receipts", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_webhook_receipts_created_at", table_name="webhook_receipts")
    op.drop_table("webhook_receipts")
