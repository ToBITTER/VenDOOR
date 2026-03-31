"""Revision ID: add_cart_and_delivery
Revises: add_public_codes
Create Date: 2026-03-31 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "add_cart_and_delivery"
down_revision = "add_public_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    delivery_status_enum = postgresql.ENUM(
        "PENDING_ASSIGNMENT",
        "ASSIGNED",
        "PICKED_UP",
        "IN_TRANSIT",
        "DELIVERED",
        name="deliverystatus",
        create_type=False,
    )
    delivery_event_type_enum = postgresql.ENUM(
        "ASSIGNED",
        "PICKED_UP",
        "IN_TRANSIT",
        "LOCATION_UPDATE",
        "DELIVERED",
        name="deliveryeventtype",
        create_type=False,
    )
    delivery_status_enum.create(op.get_bind(), checkfirst=True)
    delivery_event_type_enum.create(op.get_bind(), checkfirst=True)

    op.add_column("orders", sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("orders", sa.Column("delivery_eta_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "orders",
        sa.Column("delivery_confirm_deadline_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_orders_delivery_eta_at", "orders", ["delivery_eta_at"], unique=False)
    op.create_index(
        "ix_orders_delivery_confirm_deadline_at",
        "orders",
        ["delivery_confirm_deadline_at"],
        unique=False,
    )
    op.alter_column("orders", "quantity", server_default=None)

    op.create_table(
        "cart_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("buyer_id", sa.Integer(), nullable=False),
        sa.Column("listing_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["buyer_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("buyer_id", "listing_id", name="uq_cart_item_buyer_listing"),
    )
    op.create_index("ix_cart_items_buyer_id", "cart_items", ["buyer_id"], unique=False)
    op.create_index("ix_cart_items_listing_id", "cart_items", ["listing_id"], unique=False)
    op.create_index("ix_cart_items_buyer_id_created_at", "cart_items", ["buyer_id", "created_at"], unique=False)
    op.alter_column("cart_items", "quantity", server_default=None)

    op.create_table(
        "delivery_agents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("vehicle_type", sa.String(length=100), nullable=True),
        sa.Column("api_key_hash", sa.String(length=128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("api_key_hash"),
    )
    op.create_index("ix_delivery_agents_api_key_hash", "delivery_agents", ["api_key_hash"], unique=True)
    op.create_index("ix_delivery_agents_is_active", "delivery_agents", ["is_active"], unique=False)
    op.alter_column("delivery_agents", "is_active", server_default=None)

    op.create_table(
        "deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=True),
        sa.Column("status", delivery_status_enum, nullable=False, server_default="PENDING_ASSIGNMENT"),
        sa.Column("current_latitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("current_longitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("current_location_note", sa.String(length=255), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("picked_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("in_transit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["delivery_agents.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id"),
    )
    op.create_index("ix_deliveries_order_id", "deliveries", ["order_id"], unique=True)
    op.create_index("ix_deliveries_agent_id", "deliveries", ["agent_id"], unique=False)
    op.create_index("ix_deliveries_status", "deliveries", ["status"], unique=False)
    op.create_index("ix_deliveries_status_created_at", "deliveries", ["status", "created_at"], unique=False)
    op.alter_column("deliveries", "status", server_default=None)

    op.create_table(
        "delivery_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("delivery_id", sa.Integer(), nullable=False),
        sa.Column("event_type", delivery_event_type_enum, nullable=False),
        sa.Column("actor", sa.String(length=32), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("latitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("longitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["delivery_id"], ["deliveries.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_delivery_events_delivery_id", "delivery_events", ["delivery_id"], unique=False)
    op.create_index("ix_delivery_events_event_type", "delivery_events", ["event_type"], unique=False)
    op.create_index(
        "ix_delivery_events_delivery_id_created_at",
        "delivery_events",
        ["delivery_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_delivery_events_delivery_id_created_at", table_name="delivery_events")
    op.drop_index("ix_delivery_events_event_type", table_name="delivery_events")
    op.drop_index("ix_delivery_events_delivery_id", table_name="delivery_events")
    op.drop_table("delivery_events")

    op.drop_index("ix_deliveries_status_created_at", table_name="deliveries")
    op.drop_index("ix_deliveries_status", table_name="deliveries")
    op.drop_index("ix_deliveries_agent_id", table_name="deliveries")
    op.drop_index("ix_deliveries_order_id", table_name="deliveries")
    op.drop_table("deliveries")

    op.drop_index("ix_delivery_agents_is_active", table_name="delivery_agents")
    op.drop_index("ix_delivery_agents_api_key_hash", table_name="delivery_agents")
    op.drop_table("delivery_agents")

    op.drop_index("ix_cart_items_buyer_id_created_at", table_name="cart_items")
    op.drop_index("ix_cart_items_listing_id", table_name="cart_items")
    op.drop_index("ix_cart_items_buyer_id", table_name="cart_items")
    op.drop_table("cart_items")

    op.drop_index("ix_orders_delivery_confirm_deadline_at", table_name="orders")
    op.drop_index("ix_orders_delivery_eta_at", table_name="orders")
    op.drop_column("orders", "delivery_confirm_deadline_at")
    op.drop_column("orders", "delivered_at")
    op.drop_column("orders", "delivery_eta_at")
    op.drop_column("orders", "quantity")

    postgresql.ENUM(name="deliveryeventtype").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="deliverystatus").drop(op.get_bind(), checkfirst=True)
