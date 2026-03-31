"""
SQLAlchemy 2.0 async database models for VenDOOR Marketplace.
All models use async-compatible patterns.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import (
    String, Integer, Boolean, DateTime, Numeric, ForeignKey, Enum,
    UniqueConstraint, Index, func
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from core.id_codes import generate_listing_code, generate_seller_code


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


class Category(str, PyEnum):
    """Product category enum."""
    IPADS = "IPADS"
    IPODS = "IPODS"
    JEWELRY = "JEWELRY"
    CLOTHES = "CLOTHES"
    ELECTRONICS = "ELECTRONICS"
    SKINCARE = "SKINCARE"
    BOOKS = "BOOKS"
    SHOES = "SHOES"
    OTHERS = "OTHERS"


class AccessorySubcategory(str, PyEnum):
    """Accessory subcategory enum."""
    BAGS = "BAGS"
    JEWELRY = "JEWELRY"
    WATCHES = "WATCHES"


class OrderStatus(str, PyEnum):
    """Order status enum."""
    PENDING = "PENDING"           # Awaiting payment
    PAID = "PAID"                 # Payment received, in escrow
    COMPLETED = "COMPLETED"       # Delivery confirmed
    DISPUTED = "DISPUTED"         # Dispute raised
    CANCELLED = "CANCELLED"       # Order cancelled
    REFUNDED = "REFUNDED"         # Refund issued


class DisputeStatus(str, PyEnum):
    """Complaint/dispute status enum."""
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class DeliveryStatus(str, PyEnum):
    """Delivery lifecycle status enum."""
    PENDING_ASSIGNMENT = "PENDING_ASSIGNMENT"
    ASSIGNED = "ASSIGNED"
    PICKED_UP = "PICKED_UP"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"


class DeliveryEventType(str, PyEnum):
    """Delivery timeline event enum."""
    ASSIGNED = "ASSIGNED"
    PICKED_UP = "PICKED_UP"
    IN_TRANSIT = "IN_TRANSIT"
    LOCATION_UPDATE = "LOCATION_UPDATE"
    DELIVERED = "DELIVERED"


class User(Base):
    """
    User model - represents both buyers and sellers.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str] = mapped_column(String(255), nullable=False)
    last_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    # Relationships
    seller_profile: Mapped[Optional["SellerProfile"]] = relationship(
        "SellerProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan"
    )
    orders_as_buyer: Mapped[list["Order"]] = relationship(
        "Order",
        back_populates="buyer",
        foreign_keys="Order.buyer_id",
        cascade="all, delete-orphan"
    )
    complaints: Mapped[list["Complaint"]] = relationship(
        "Complaint",
        back_populates="complainant",
        cascade="all, delete-orphan"
    )
    cart_items: Mapped[list["CartItem"]] = relationship(
        "CartItem",
        back_populates="buyer",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<User {self.telegram_id} - {self.first_name}>"


class SellerProfile(Base):
    """
    Seller profile - extended info for users who are sellers.
    """
    __tablename__ = "seller_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    seller_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True, default=generate_seller_code)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False, index=True)
    is_student: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    student_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    hall: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    room_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    id_document_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)  # Telegram file_id
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    priority_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bank_code: Mapped[str] = mapped_column(String(10), nullable=False)
    account_number: Mapped[str] = mapped_column(String(20), nullable=False)
    account_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="seller_profile")
    listings: Mapped[list["Listing"]] = relationship(
        "Listing",
        back_populates="seller",
        cascade="all, delete-orphan"
    )
    orders_as_seller: Mapped[list["Order"]] = relationship(
        "Order",
        back_populates="seller",
        foreign_keys="Order.seller_id",
    )

    __table_args__ = (
        Index("ix_seller_profiles_user_id", "user_id"),
    )

    def __repr__(self) -> str:
        return f"<SellerProfile user_id={self.user_id}>"


class Listing(Base):
    """
    Product listing created by a seller.
    """
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_code: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True, default=generate_listing_code
    )
    seller_id: Mapped[int] = mapped_column(ForeignKey("seller_profiles.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), nullable=False)
    category: Mapped[Category] = mapped_column(Enum(Category), nullable=False, index=True)
    accessory_subcategory: Mapped[Optional[AccessorySubcategory]] = mapped_column(
        Enum(AccessorySubcategory), nullable=True, index=True
    )
    base_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    buyer_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)  # base_price * 1.05 (5% fee)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)  # Telegram file_id
    available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True
    )

    # Relationships
    seller: Mapped["SellerProfile"] = relationship("SellerProfile", back_populates="listings")
    orders: Mapped[list["Order"]] = relationship(
        "Order",
        back_populates="listing",
        cascade="all, delete-orphan"
    )
    cart_items: Mapped[list["CartItem"]] = relationship(
        "CartItem",
        back_populates="listing",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_listings_seller_id_created_at", "seller_id", "created_at"),
        Index("ix_listings_category_available", "category", "available"),
    )

    def __repr__(self) -> str:
        return f"<Listing {self.id} - {self.title}>"


class Order(Base):
    """
    Order model - represents a purchase transaction with escrow.
    """
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("seller_profiles.id"), nullable=False, index=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), nullable=False, index=True)
    
    # Transaction details
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)  # buyer_price at purchase time
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.PENDING, nullable=False, index=True)
    
    # Payment info
    transaction_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)  # Korapay reference
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Delivery info
    buyer_address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    buyer_delivery_details: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    delivery_eta_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_confirm_deadline_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    
    # Escrow & Release
    escrow_released_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_release_scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    # Relationships
    buyer: Mapped["User"] = relationship("User", back_populates="orders_as_buyer", foreign_keys=[buyer_id])
    seller: Mapped["SellerProfile"] = relationship("SellerProfile", back_populates="orders_as_seller", foreign_keys=[seller_id])
    listing: Mapped["Listing"] = relationship("Listing", back_populates="orders")
    complaint: Mapped[Optional["Complaint"]] = relationship(
        "Complaint",
        back_populates="order",
        uselist=False,
        cascade="all, delete-orphan"
    )
    delivery: Mapped[Optional["Delivery"]] = relationship(
        "Delivery",
        back_populates="order",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_orders_buyer_id_status", "buyer_id", "status"),
        Index("ix_orders_seller_id_status", "seller_id", "status"),
        Index("ix_orders_created_at", "created_at"),
        UniqueConstraint("transaction_ref", name="uq_orders_transaction_ref"),
    )

    def __repr__(self) -> str:
        return f"<Order {self.id} - {self.status}>"


class Complaint(Base):
    """
    Complaint/Dispute model - raised by buyers or sellers for order disputes.
    """
    __tablename__ = "complaints"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), unique=True, nullable=False, index=True)
    complainant_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), nullable=False)
    status: Mapped[DisputeStatus] = mapped_column(Enum(DisputeStatus), default=DisputeStatus.OPEN, nullable=False, index=True)
    
    evidence_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)  # Telegram file_id
    resolution: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    # Relationships
    order: Mapped["Order"] = relationship("Order", back_populates="complaint")
    complainant: Mapped["User"] = relationship("User", back_populates="complaints")

    __table_args__ = (
        Index("ix_complaints_status_created_at", "status", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Complaint {self.id} - {self.status}>"


class CartItem(Base):
    """Shopping cart item belonging to a buyer."""
    __tablename__ = "cart_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    buyer: Mapped["User"] = relationship("User", back_populates="cart_items")
    listing: Mapped["Listing"] = relationship("Listing", back_populates="cart_items")

    __table_args__ = (
        UniqueConstraint("buyer_id", "listing_id", name="uq_cart_item_buyer_listing"),
        Index("ix_cart_items_buyer_id_created_at", "buyer_id", "created_at"),
    )


class DeliveryAgent(Base):
    """Delivery rider/agent account managed by admin."""
    __tablename__ = "delivery_agents"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    telegram_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    vehicle_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    api_key_hash: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    deliveries: Mapped[list["Delivery"]] = relationship("Delivery", back_populates="agent")


class Delivery(Base):
    """Delivery record linked to a paid order."""
    __tablename__ = "deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False, unique=True, index=True)
    agent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("delivery_agents.id"), nullable=True, index=True)
    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus),
        default=DeliveryStatus.PENDING_ASSIGNMENT,
        nullable=False,
        index=True,
    )
    current_latitude: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7), nullable=True)
    current_longitude: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7), nullable=True)
    current_location_note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    assigned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    picked_up_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    in_transit_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    order: Mapped["Order"] = relationship("Order", back_populates="delivery")
    agent: Mapped[Optional["DeliveryAgent"]] = relationship("DeliveryAgent", back_populates="deliveries")
    events: Mapped[list["DeliveryEvent"]] = relationship(
        "DeliveryEvent", back_populates="delivery", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_deliveries_status_created_at", "status", "created_at"),
    )


class DeliveryEvent(Base):
    """Immutable timeline entries for delivery tracking."""
    __tablename__ = "delivery_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey("deliveries.id"), nullable=False, index=True)
    event_type: Mapped[DeliveryEventType] = mapped_column(Enum(DeliveryEventType), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    latitude: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    delivery: Mapped["Delivery"] = relationship("Delivery", back_populates="events")

    __table_args__ = (
        Index("ix_delivery_events_delivery_id_created_at", "delivery_id", "created_at"),
    )
