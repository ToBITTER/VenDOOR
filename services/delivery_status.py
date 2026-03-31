"""Delivery status update service for shared logic between API and bot handlers."""

from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from db.models import Delivery, DeliveryEvent, DeliveryStatus, DeliveryEventType, DeliveryOrder, Order


async def update_delivery_status(
    delivery_id: int,
    new_status: DeliveryStatus,
    actor: str,
    note: str | None,
    session: AsyncSession,
    latitude: Decimal | None = None,
    longitude: Decimal | None = None,
) -> Delivery | None:
    """
    Update delivery status and create immutable DeliveryEvent record.
    
    Args:
        delivery_id: ID of delivery to update
        new_status: New DeliveryStatus enum value
        actor: "AGENT" or "ADMIN"
        note: Optional note about the status change
        session: AsyncSession for database operations
        latitude: Optional location latitude
        longitude: Optional location longitude
    
    Returns:
        Updated Delivery object or None if not found
    """
    delivery = await session.get(Delivery, delivery_id)
    if not delivery:
        return None

    # Update delivery status and timestamps
    delivery.status = new_status
    
    if new_status == DeliveryStatus.ASSIGNED:
        delivery.assigned_at = datetime.now(timezone.utc)
    elif new_status == DeliveryStatus.PICKED_UP:
        delivery.picked_up_at = datetime.now(timezone.utc)
    elif new_status == DeliveryStatus.IN_TRANSIT:
        delivery.in_transit_at = datetime.now(timezone.utc)
    elif new_status == DeliveryStatus.DELIVERED:
        delivery.delivered_at = datetime.now(timezone.utc)

    # Update location if provided
    if latitude is not None and longitude is not None:
        delivery.current_latitude = latitude
        delivery.current_longitude = longitude

    if note:
        delivery.current_location_note = note

    # Create immutable DeliveryEvent record for audit trail
    event_type_map = {
        DeliveryStatus.ASSIGNED: DeliveryEventType.ASSIGNED,
        DeliveryStatus.PICKED_UP: DeliveryEventType.PICKED_UP,
        DeliveryStatus.IN_TRANSIT: DeliveryEventType.IN_TRANSIT,
        DeliveryStatus.DELIVERED: DeliveryEventType.DELIVERED,
    }

    event_type = event_type_map.get(new_status, DeliveryEventType.LOCATION_UPDATE)
    event = DeliveryEvent(
        delivery_id=delivery_id,
        event_type=event_type,
        actor=actor,
        note=note,
        latitude=latitude,
        longitude=longitude,
    )

    session.add(delivery)
    session.add(event)

    return delivery


async def update_delivery_order_status(
    delivery_order_id: int,
    session: AsyncSession,
    note: str | None = None,
) -> DeliveryOrder | None:
    """
    Mark a single order within a multi-seller delivery as picked up.
    
    This is used during sequential pickup workflow to track which
    individual orders have been collected.
    
    Args:
        delivery_order_id: ID of DeliveryOrder join record
        session: AsyncSession for database operations
        note: Optional note (e.g., photo file_id)
    
    Returns:
        Updated DeliveryOrder object or None if not found
    """
    delivery_order = await session.get(DeliveryOrder, delivery_order_id)
    if not delivery_order:
        return None

    delivery_order.picked_up_at = datetime.now(timezone.utc)
    session.add(delivery_order)

    # Also create event on the main delivery
    event = DeliveryEvent(
        delivery_id=delivery_order.delivery_id,
        event_type=DeliveryEventType.PICKED_UP,
        actor="AGENT",
        note=f"Order {delivery_order.order_id} collected. {note or ''}",
    )
    session.add(event)

    return delivery_order
