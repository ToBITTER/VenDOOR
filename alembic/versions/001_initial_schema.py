"""Revision ID: initial_schema
Revises: 
Create Date: 2026-03-28 00:00:00.000000

This is the initial schema migration that creates all base tables.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic
revision = 'initial_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create initial database schema."""
    
    # Create ENUM types
    orderstatustype = postgresql.ENUM(
        'PENDING', 'PAID', 'COMPLETED', 'DISPUTED', 'CANCELLED', 'REFUNDED',
        name='orderstatus',
        create_type=False,
    )
    orderstatustype.create(op.get_bind(), checkfirst=True)
    
    disputestatustype = postgresql.ENUM(
        'OPEN', 'INVESTIGATING', 'RESOLVED', 'CLOSED',
        name='disputestatus',
        create_type=False,
    )
    disputestatustype.create(op.get_bind(), checkfirst=True)
    
    categorytype = postgresql.ENUM(
        'IPADS', 'IPODS', 'JEWELRY', 'CLOTHES', 'ELECTRONICS', 'BOOKS', 'SHOES', 'OTHERS',
        name='category',
        create_type=False,
    )
    categorytype.create(op.get_bind(), checkfirst=True)
    
    # Create users table
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('telegram_id', sa.String(255), nullable=False),
        sa.Column('username', sa.String(255), nullable=True),
        sa.Column('first_name', sa.String(255), nullable=False),
        sa.Column('last_name', sa.String(255), nullable=True),
        sa.Column('phone', sa.String(20), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_users'),
    )
    op.create_index('ix_users_telegram_id', 'users', ['telegram_id'], unique=True)
    
    # Create seller_profiles table
    op.create_table(
        'seller_profiles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('is_student', sa.Boolean(), nullable=False),
        sa.Column('student_email', sa.String(255), nullable=True),
        sa.Column('id_document_url', sa.String(1024), nullable=True),
        sa.Column('verified', sa.Boolean(), nullable=False),
        sa.Column('bank_code', sa.String(10), nullable=False),
        sa.Column('account_number', sa.String(20), nullable=False),
        sa.Column('account_name', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_seller_profiles_user_id'),
        sa.PrimaryKeyConstraint('id', name='pk_seller_profiles'),
        sa.UniqueConstraint('user_id', name='uq_seller_profiles_user_id'),
    )
    op.create_index('ix_seller_profiles_user_id', 'seller_profiles', ['user_id'])
    
    # Create listings table
    op.create_table(
        'listings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('seller_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('description', sa.String(2000), nullable=False),
        sa.Column('category', categorytype, nullable=False),
        sa.Column('base_price', sa.Numeric(12, 2), nullable=False),
        sa.Column('buyer_price', sa.Numeric(12, 2), nullable=False),
        sa.Column('image_url', sa.String(1024), nullable=True),
        sa.Column('available', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['seller_id'], ['seller_profiles.id'], name='fk_listings_seller_id'),
        sa.PrimaryKeyConstraint('id', name='pk_listings'),
    )
    op.create_index('ix_listings_seller_id', 'listings', ['seller_id'])
    op.create_index('ix_listings_available', 'listings', ['available'])
    op.create_index('ix_listings_category', 'listings', ['category'])
    op.create_index('ix_listings_created_at', 'listings', ['created_at'])
    op.create_index('ix_listings_seller_id_created_at', 'listings', ['seller_id', 'created_at'])
    op.create_index('ix_listings_category_available', 'listings', ['category', 'available'])
    
    # Create orders table
    op.create_table(
        'orders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('buyer_id', sa.Integer(), nullable=False),
        sa.Column('seller_id', sa.Integer(), nullable=False),
        sa.Column('listing_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('status', orderstatustype, nullable=False),
        sa.Column('transaction_ref', sa.String(255), nullable=True),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('buyer_address', sa.String(500), nullable=True),
        sa.Column('buyer_delivery_details', sa.String(500), nullable=True),
        sa.Column('escrow_released_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('auto_release_scheduled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['buyer_id'], ['users.id'], name='fk_orders_buyer_id'),
        sa.ForeignKeyConstraint(['seller_id'], ['seller_profiles.id'], name='fk_orders_seller_id'),
        sa.ForeignKeyConstraint(['listing_id'], ['listings.id'], name='fk_orders_listing_id'),
        sa.PrimaryKeyConstraint('id', name='pk_orders'),
    )
    op.create_index('ix_orders_buyer_id', 'orders', ['buyer_id'])
    op.create_index('ix_orders_seller_id', 'orders', ['seller_id'])
    op.create_index('ix_orders_listing_id', 'orders', ['listing_id'])
    op.create_index('ix_orders_status', 'orders', ['status'])
    op.create_index('ix_orders_created_at', 'orders', ['created_at'])
    op.create_index('ix_orders_buyer_id_status', 'orders', ['buyer_id', 'status'])
    op.create_index('ix_orders_seller_id_status', 'orders', ['seller_id', 'status'])
    op.create_index('uq_orders_transaction_ref', 'orders', ['transaction_ref'], unique=True)
    
    # Create complaints table
    op.create_table(
        'complaints',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('complainant_id', sa.Integer(), nullable=False),
        sa.Column('subject', sa.String(255), nullable=False),
        sa.Column('description', sa.String(2000), nullable=False),
        sa.Column('status', disputestatustype, nullable=False),
        sa.Column('evidence_url', sa.String(1024), nullable=True),
        sa.Column('resolution', sa.String(2000), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name='fk_complaints_order_id'),
        sa.ForeignKeyConstraint(['complainant_id'], ['users.id'], name='fk_complaints_user_id'),
        sa.PrimaryKeyConstraint('id', name='pk_complaints'),
        sa.UniqueConstraint('order_id', name='uq_complaints_order_id'),
    )
    op.create_index('ix_complaints_order_id', 'complaints', ['order_id'])
    op.create_index('ix_complaints_complainant_id', 'complaints', ['complainant_id'])
    op.create_index('ix_complaints_status', 'complaints', ['status'])
    op.create_index('ix_complaints_created_at', 'complaints', ['created_at'])
    op.create_index('ix_complaints_status_created_at', 'complaints', ['status', 'created_at'])


def downgrade() -> None:
    """Drop all tables and ENUM types."""
    
    op.drop_index('ix_complaints_status_created_at', table_name='complaints')
    op.drop_index('ix_complaints_created_at', table_name='complaints')
    op.drop_index('ix_complaints_status', table_name='complaints')
    op.drop_index('ix_complaints_complainant_id', table_name='complaints')
    op.drop_index('ix_complaints_order_id', table_name='complaints')
    op.drop_table('complaints')
    
    op.drop_index('uq_orders_transaction_ref', table_name='orders')
    op.drop_index('ix_orders_seller_id_status', table_name='orders')
    op.drop_index('ix_orders_buyer_id_status', table_name='orders')
    op.drop_index('ix_orders_created_at', table_name='orders')
    op.drop_index('ix_orders_status', table_name='orders')
    op.drop_index('ix_orders_listing_id', table_name='orders')
    op.drop_index('ix_orders_seller_id', table_name='orders')
    op.drop_index('ix_orders_buyer_id', table_name='orders')
    op.drop_table('orders')
    
    op.drop_index('ix_listings_category_available', table_name='listings')
    op.drop_index('ix_listings_seller_id_created_at', table_name='listings')
    op.drop_index('ix_listings_created_at', table_name='listings')
    op.drop_index('ix_listings_category', table_name='listings')
    op.drop_index('ix_listings_available', table_name='listings')
    op.drop_index('ix_listings_seller_id', table_name='listings')
    op.drop_table('listings')
    
    op.drop_index('ix_seller_profiles_user_id', table_name='seller_profiles')
    op.drop_table('seller_profiles')
    
    op.drop_index('ix_users_telegram_id', table_name='users')
    op.drop_table('users')
    
    # Drop ENUM types
    postgresql.ENUM(name='category').drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name='disputestatus').drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name='orderstatus').drop(op.get_bind(), checkfirst=True)
