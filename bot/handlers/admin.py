"""
Admin handlers for seller verification workflows.
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from bot.helpers.telegram import safe_answer_callback, safe_edit_text
from core.config import get_settings
from db.models import SellerProfile

router = Router()
settings = get_settings()


def _is_admin(telegram_user_id: int) -> bool:
    if not settings.admin_telegram_id:
        return False
    return str(telegram_user_id) == str(settings.admin_telegram_id)


def _pending_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Refresh", callback_data="admin_pending_refresh")],
            [InlineKeyboardButton(text="Back", callback_data="back_to_menu")],
        ]
    )


def _actions_keyboard(seller_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Approve", callback_data=f"admin_approve_{seller_id}"),
                InlineKeyboardButton(text="Reject", callback_data=f"admin_reject_{seller_id}"),
            ],
            [InlineKeyboardButton(text="Back to Pending", callback_data="admin_pending_refresh")],
        ]
    )


async def _render_pending_text(session: AsyncSession) -> str:
    result = await session.execute(
        select(SellerProfile)
        .options(joinedload(SellerProfile.user))
        .where(SellerProfile.verified.is_(False))
        .order_by(SellerProfile.created_at.asc())
    )
    pending = result.scalars().all()
    if not pending:
        return "<b>Pending Seller Verifications</b>\n\nNo pending applications."

    text = "<b>Pending Seller Verifications</b>\n\n"
    for seller in pending[:10]:
        user = seller.user
        name = f"{user.first_name} {user.last_name or ''}".strip() if user else "Unknown"
        username = f"@{user.username}" if user and user.username else "N/A"
        text += (
            f"<b>Seller ID:</b> {seller.id}\n"
            f"<b>Name:</b> {name}\n"
            f"<b>Username:</b> {username}\n"
        )
        text += (
            f"<b>Student:</b> {'Yes' if seller.is_student else 'No'}\n"
            f"<b>Email:</b> {seller.student_email or 'N/A'}\n"
            f"<b>Bank:</b> {seller.bank_code} / {seller.account_number}\n"
            f"<b>Account Name:</b> {seller.account_name}\n"
            f"<b>ID Doc:</b> {seller.id_document_url or 'N/A'}\n"
            f"<b>Submitted:</b> {seller.created_at.strftime('%Y-%m-%d %H:%M')}\n"
            f"/review_seller_{seller.id}\n\n"
        )
    return text


@router.message(Command("pending_sellers"))
async def pending_sellers(message: Message, session: AsyncSession):
    if not _is_admin(message.from_user.id):
        await message.reply("You are not authorized to use this command.")
        return

    text = await _render_pending_text(session)
    await message.answer(text, parse_mode="HTML", reply_markup=_pending_keyboard())


@router.message(F.text.regexp(r"^/review_seller_(\d+)$"))
async def review_seller(message: Message, session: AsyncSession):
    if not _is_admin(message.from_user.id):
        await message.reply("You are not authorized to use this command.")
        return

    seller_id = int(message.text.split("_")[-1])
    result = await session.execute(
        select(SellerProfile).options(joinedload(SellerProfile.user)).where(SellerProfile.id == seller_id)
    )
    seller = result.scalars().first()
    if not seller:
        await message.reply("Seller not found.")
        return

    user = seller.user
    name = f"{user.first_name} {user.last_name or ''}".strip() if user else "Unknown"
    username = f"@{user.username}" if user and user.username else "N/A"
    text = (
        "<b>Seller Verification Review</b>\n\n"
        f"<b>Seller ID:</b> {seller.id}\n"
        f"<b>Name:</b> {name}\n"
        f"<b>Username:</b> {username}\n"
        f"<b>Student:</b> {'Yes' if seller.is_student else 'No'}\n"
        f"<b>Student Email:</b> {seller.student_email or 'N/A'}\n"
        f"<b>Hall:</b> {seller.hall or 'N/A'}\n"
        f"<b>Room Number:</b> {seller.room_number or 'N/A'}\n"
        f"<b>Address:</b> {seller.address or 'N/A'}\n"
        f"<b>Bank Code:</b> {seller.bank_code}\n"
        f"<b>Account Number:</b> {seller.account_number}\n"
        f"<b>Account Name:</b> {seller.account_name}\n"
        f"<b>ID Document File:</b> {seller.id_document_url or 'N/A'}"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=_actions_keyboard(seller.id))


@router.callback_query(F.data == "admin_pending_refresh")
async def refresh_pending(callback: CallbackQuery, session: AsyncSession):
    if not _is_admin(callback.from_user.id):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    text = await _render_pending_text(session)
    await safe_edit_text(callback, text, parse_mode="HTML", reply_markup=_pending_keyboard())


@router.callback_query(F.data.startswith("admin_approve_"))
async def approve_seller(callback: CallbackQuery, session: AsyncSession):
    if not _is_admin(callback.from_user.id):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)

    seller_id = int(callback.data.replace("admin_approve_", ""))
    result = await session.execute(
        select(SellerProfile).options(joinedload(SellerProfile.user)).where(SellerProfile.id == seller_id)
    )
    seller = result.scalars().first()
    if not seller:
        await safe_edit_text(callback, "Seller not found.", reply_markup=_pending_keyboard())
        return

    seller.verified = True
    await session.commit()

    if seller.user and seller.user.telegram_id:
        try:
            await callback.bot.send_message(
                chat_id=int(seller.user.telegram_id),
                text="Your seller account has been verified. You can now create listings.",
            )
        except Exception:
            pass

    await safe_edit_text(
        callback,
        f"Seller {seller.id} approved successfully.",
        reply_markup=_pending_keyboard(),
    )


@router.callback_query(F.data.startswith("admin_reject_"))
async def reject_seller(callback: CallbackQuery, session: AsyncSession):
    if not _is_admin(callback.from_user.id):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)

    seller_id = int(callback.data.replace("admin_reject_", ""))
    result = await session.execute(
        select(SellerProfile).options(joinedload(SellerProfile.user)).where(SellerProfile.id == seller_id)
    )
    seller = result.scalars().first()
    if not seller:
        await safe_edit_text(callback, "Seller not found.", reply_markup=_pending_keyboard())
        return

    seller.verified = False
    await session.commit()

    if seller.user and seller.user.telegram_id:
        try:
            await callback.bot.send_message(
                chat_id=int(seller.user.telegram_id),
                text="Your seller verification was not approved yet. Please review and resubmit your details.",
            )
        except Exception:
            pass

    await safe_edit_text(
        callback,
        f"Seller {seller.id} marked as not approved.",
        reply_markup=_pending_keyboard(),
    )
