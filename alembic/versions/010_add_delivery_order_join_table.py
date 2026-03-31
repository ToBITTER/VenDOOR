"""Add delivery_orders join table for multi-seller deliveries.

Revision ID: 010
Revises: 009
Create Date: 2026-03-31

This migration adds a many-to-many join table to link multiple orders
to a single delivery job, supporting consolidated multi-seller deliveries.
Existing 1:1 delivery-order relationships remain functional via this table.

"""

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    # Create delivery_orders join table
    op.create_table(
        'delivery_orders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('delivery_id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('picked_up_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['delivery_id'], ['deliveries.id'], ),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('delivery_id', 'order_id', name='uq_delivery_order')
    )
    
    # Create indices for query efficiency
    op.create_index('ix_delivery_orders_delivery_id', 'delivery_orders', ['delivery_id'])
    op.create_index('ix_delivery_orders_order_id', 'delivery_orders', ['order_id'])
    op.create_index('ix_delivery_orders_sequence', 'delivery_orders', ['sequence'])


def downgrade() -> None:
    # Drop indices first
    op.drop_index('ix_delivery_orders_sequence', table_name='delivery_orders')
    op.drop_index('ix_delivery_orders_order_id', table_name='delivery_orders')
    op.drop_index('ix_delivery_orders_delivery_id', table_name='delivery_orders')
    
    # Drop table
    op.drop_table('delivery_orders')
