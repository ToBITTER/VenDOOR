"""Revision ID: delivery_agent_telegram_auth
Revises: add_cart_and_delivery
Create Date: 2026-03-31 12:45:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "delivery_agent_telegram_auth"
down_revision = "add_cart_and_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("delivery_agents", sa.Column("telegram_id", sa.String(length=255), nullable=True))
    op.create_index("ix_delivery_agents_telegram_id", "delivery_agents", ["telegram_id"], unique=True)
    op.alter_column("delivery_agents", "api_key_hash", existing_type=sa.String(length=128), nullable=True)


def downgrade() -> None:
    op.alter_column("delivery_agents", "api_key_hash", existing_type=sa.String(length=128), nullable=False)
    op.drop_index("ix_delivery_agents_telegram_id", table_name="delivery_agents")
    op.drop_column("delivery_agents", "telegram_id")
