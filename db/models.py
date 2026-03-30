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

    def __repr__(self) -> str:
        return f"<User {self.telegram_id} - {self.first_name}>"


class SellerProfile(Base):
    """
    Seller profile - extended info for users who are sellers.
    """
    __tablename__ = "seller_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
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
    seller_id: Mapped[int] = mapped_column(ForeignKey("seller_profiles.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), nullable=False)
    category: Mapped[Category] = mapped_column(Enum(Category), nullable=False, index=True)
    accessory_subcategory: Mapped[Optional[AccessorySubcategory]] = mapped_column(
        Enum(AccessorySubcategory), nullable=True, index=True
    )
    base_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    buyer_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)  # base_price * 1.05 (5% fee)
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
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)  # buyer_price at purchase time
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.PENDING, nullable=False, index=True)
    
    # Payment info
    transaction_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)  # Korapay reference
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Delivery info
    buyer_address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    buyer_delivery_details: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    
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
