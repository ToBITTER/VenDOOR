"""
Main menu keyboards for VenDOOR Bot.
"""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

MENU_BROWSE = "Browse Catalog"
MENU_CART = "My Cart"
MENU_ORDERS = "My Orders"
MENU_SELLER = "Become Seller"
MENU_DELIVERY = "Delivery Hub"
MENU_LISTINGS = "My Listings"
MENU_COMPLAINTS = "Complaints"
MENU_HELP = "Help"
MENU_TERMS = "Terms & Conditions"


def get_main_menu_inline() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=MENU_BROWSE, callback_data="browse_catalog"),
                InlineKeyboardButton(text=MENU_CART, callback_data="my_cart"),
            ],
            [
                InlineKeyboardButton(text=MENU_ORDERS, callback_data="my_orders"),
                InlineKeyboardButton(text=MENU_SELLER, callback_data="seller_register"),
            ],
            [InlineKeyboardButton(text=MENU_DELIVERY, callback_data="delivery_hub")],
            [
                InlineKeyboardButton(text=MENU_LISTINGS, callback_data="seller_listings"),
                InlineKeyboardButton(text=MENU_COMPLAINTS, callback_data="complaints"),
                InlineKeyboardButton(text=MENU_HELP, callback_data="help"),
            ],
            [InlineKeyboardButton(text=MENU_TERMS, callback_data="terms_conditions")],
        ]
    )
    return keyboard


def get_main_menu_reply() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MENU_BROWSE), KeyboardButton(text=MENU_CART)],
            [KeyboardButton(text=MENU_ORDERS), KeyboardButton(text=MENU_SELLER)],
            [KeyboardButton(text=MENU_DELIVERY), KeyboardButton(text=MENU_LISTINGS)],
            [KeyboardButton(text=MENU_COMPLAINTS), KeyboardButton(text=MENU_HELP)],
            [KeyboardButton(text=MENU_TERMS)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Please select an action",
    )


def get_catalog_categories() -> InlineKeyboardMarkup:
    categories = [
        ("iPads", "browse_cat_IPADS"),
        ("iPods", "browse_cat_IPODS"),
        ("Accessories", "browse_cat_JEWELRY"),
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
        + [[InlineKeyboardButton(text="Search Listings", callback_data="catalog_search_start")]]
        + [[InlineKeyboardButton(text="Back", callback_data="back_to_menu")]]
    )
    return keyboard


def get_order_actions(order_id: int) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Track Delivery", callback_data=f"order_track_{order_id}")],
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
