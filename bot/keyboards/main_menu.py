"""
Main menu keyboards for VenDOOR Bot.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_main_menu_inline() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Browse Catalog", callback_data="browse_catalog"),
                InlineKeyboardButton(text="My Orders", callback_data="my_orders"),
            ],
            [
                InlineKeyboardButton(text="Become Seller", callback_data="seller_register"),
                InlineKeyboardButton(text="My Listings", callback_data="seller_listings"),
            ],
            [
                InlineKeyboardButton(text="Complaints", callback_data="complaints"),
                InlineKeyboardButton(text="Help", callback_data="help"),
            ],
        ]
    )
    return keyboard


def get_catalog_categories() -> InlineKeyboardMarkup:
    categories = [
        ("iPads", "browse_cat_IPADS"),
        ("iPods", "browse_cat_IPODS"),
        ("Jewelry", "browse_cat_JEWELRY"),
        ("Clothes", "browse_cat_CLOTHES"),
        ("Laptop", "browse_cat_ELECTRONICS"),
        ("Skin Care", "browse_cat_SKINCARE"),
        ("Books", "browse_cat_BOOKS"),
        ("Shoes", "browse_cat_SHOES"),
        ("Others", "browse_cat_OTHERS"),
    ]

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=callback)]
            for label, callback in categories
        ]
        + [[InlineKeyboardButton(text="Back", callback_data="back_to_menu")]]
    )
    return keyboard


def get_order_actions(order_id: int) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Confirm Receipt", callback_data=f"order_confirm_{order_id}")],
            [InlineKeyboardButton(text="Raise Dispute", callback_data=f"order_dispute_{order_id}")],
            [InlineKeyboardButton(text="Back to Orders", callback_data="my_orders")],
        ]
    )
    return keyboard


def get_seller_actions() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Create Listing", callback_data="seller_create_listing")],
            [InlineKeyboardButton(text="View My Listings", callback_data="seller_listings")],
            [InlineKeyboardButton(text="Sales Stats", callback_data="seller_stats")],
            [InlineKeyboardButton(text="Back", callback_data="back_to_menu")],
        ]
    )
    return keyboard


def get_confirmation_keyboard() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Yes", callback_data="confirm_yes"),
                InlineKeyboardButton(text="No", callback_data="confirm_no"),
            ]
        ]
    )
    return keyboard
