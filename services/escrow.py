"""
Escrow service for managing payment holds and releases.
Handles state transitions for orders in escrow (PAID → COMPLETED → REFUNDED/RELEASED).
"""

from datetime import datetime
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Order, OrderStatus


class EscrowService:
    """
    Service for managing escrow transactions.
    """
    
    @staticmethod
    async def release_escrow(order_id: int, session: AsyncSession) -> bool:
        """
        Release escrow funds to seller after order completion.
        
        Args:
            order_id: Order ID to release funds for
            session: Database session
        
        Returns:
            True if released successfully, False otherwise
        """
        try:
            result = await session.execute(
                select(Order).where(Order.id == order_id)
            )
            order = result.scalars().first()
            
            if not order:
                print(f"Order {order_id} not found")
                return False
            
            if order.status not in (OrderStatus.PAID, OrderStatus.COMPLETED):
                print(f"Cannot release escrow for order {order_id} with status {order.status}")
                return False
            
            # Update order status
            order.status = OrderStatus.COMPLETED
            order.escrow_released_at = datetime.utcnow()
            
            await session.commit()
            
            # TODO: Transfer funds to seller's bank account via Korapay
            # await transfer_to_seller_account(order)
            
            print(f"Escrow released for order {order_id}")
            return True
        
        except Exception as e:
            await session.rollback()
            print(f"Escrow release error: {e}")
            return False
    
    @staticmethod
    async def auto_release_escrow(order_id: int, session: AsyncSession) -> bool:
        """
        Automatically release escrow after 48 hours if not disputed.
        
        Args:
            order_id: Order ID to auto-release
            session: Database session
        
        Returns:
            True if auto-released, False otherwise
        """
        try:
            result = await session.execute(
                select(Order).where(Order.id == order_id)
            )
            order = result.scalars().first()
            
            if not order:
                return False
            
            # Only auto-release if no dispute and status is still PAID
            if order.status == OrderStatus.PAID:
                order.status = OrderStatus.COMPLETED
                order.escrow_released_at = datetime.utcnow()
                await session.commit()
                print(f"Auto-released escrow for order {order_id}")
                return True
            
            return False
        
        except Exception as e:
            await session.rollback()
            print(f"Auto-release escrow error: {e}")
            return False
    
    @staticmethod
    async def refund_escrow(order_id: int, session: AsyncSession) -> bool:
        """
        Refund escrow funds back to buyer (in case of dispute resolution).
        
        Args:
            order_id: Order ID to refund
            session: Database session
        
        Returns:
            True if refunded, False otherwise
        """
        try:
            result = await session.execute(
                select(Order).where(Order.id == order_id)
            )
            order = result.scalars().first()
            
            if not order:
                return False
            
            # Only refund if in PAID or DISPUTED status
            if order.status in (OrderStatus.PAID, OrderStatus.DISPUTED):
                order.status = OrderStatus.REFUNDED
                await session.commit()
                
                # TODO: Initiate refund via Korapay
                # await initiate_refund(order)
                
                print(f"Refunded escrow for order {order_id}")
                return True
            
            return False
        
        except Exception as e:
            await session.rollback()
            print(f"Escrow refund error: {e}")
            return False


# Singleton instance
_escrow_service = None


def get_escrow_service() -> EscrowService:
    """Get escrow service singleton."""
    global _escrow_service
    if _escrow_service is None:
        _escrow_service = EscrowService()
    return _escrow_service
