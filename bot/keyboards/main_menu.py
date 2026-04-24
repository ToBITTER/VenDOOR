"""
Main menu keyboards for VenDOOR Bot.
"""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

MENU_BROWSE = "Browse Marketplace"
MENU_CART = "Cart"
MENU_ORDERS = "Orders"
MENU_SELLER = "Start Selling"
MENU_DELIVERY = "Delivery Tracking"
MENU_LISTINGS = "Seller Dashboard"
MENU_COMPLAINTS = "Report an Issue"
MENU_HELP = "Support"
MENU_TERMS = "Terms & Policies"


def get_main_menu_inline(seller_first: bool = False) -> InlineKeyboardMarkup:
    if seller_first:
        rows = [
            [
                InlineKeyboardButton(text=MENU_LISTINGS, callback_data="seller_listings"),
                InlineKeyboardButton(text=MENU_DELIVERY, callback_data="delivery_hub"),
            ],
            [
                InlineKeyboardButton(text=MENU_BROWSE, callback_data="browse_catalog"),
                InlineKeyboardButton(text=MENU_ORDERS, callback_data="my_orders"),
            ],
            [
                InlineKeyboardButton(text=MENU_CART, callback_data="my_cart"),
                InlineKeyboardButton(text=MENU_SELLER, callback_data="seller_register"),
            ],
            [
                InlineKeyboardButton(text=MENU_COMPLAINTS, callback_data="complaints"),
                InlineKeyboardButton(text=MENU_HELP, callback_data="support_hub"),
            ],
            [InlineKeyboardButton(text=MENU_TERMS, callback_data="terms_conditions")],
        ]
    else:
        rows = [
            [
                InlineKeyboardButton(text=MENU_BROWSE, callback_data="browse_catalog"),
                InlineKeyboardButton(text=MENU_CART, callback_data="my_cart"),
            ],
            [
                InlineKeyboardButton(text=MENU_ORDERS, callback_data="my_orders"),
                InlineKeyboardButton(text=MENU_DELIVERY, callback_data="delivery_hub"),
            ],
            [
                InlineKeyboardButton(text=MENU_SELLER, callback_data="seller_register"),
                InlineKeyboardButton(text=MENU_LISTINGS, callback_data="seller_listings"),
            ],
            [
                InlineKeyboardButton(text=MENU_COMPLAINTS, callback_data="complaints"),
                InlineKeyboardButton(text=MENU_HELP, callback_data="support_hub"),
            ],
            [InlineKeyboardButton(text=MENU_TERMS, callback_data="terms_conditions")],
        ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_main_menu_reply(seller_first: bool = False) -> ReplyKeyboardMarkup:
    if seller_first:
        rows = [
            [KeyboardButton(text=MENU_LISTINGS), KeyboardButton(text=MENU_DELIVERY)],
            [KeyboardButton(text=MENU_BROWSE), KeyboardButton(text=MENU_ORDERS)],
            [KeyboardButton(text=MENU_CART), KeyboardButton(text=MENU_SELLER)],
            [KeyboardButton(text=MENU_COMPLAINTS), KeyboardButton(text=MENU_HELP)],
            [KeyboardButton(text=MENU_TERMS)],
        ]
    else:
        rows = [
            [KeyboardButton(text=MENU_BROWSE), KeyboardButton(text=MENU_CART)],
            [KeyboardButton(text=MENU_ORDERS), KeyboardButton(text=MENU_DELIVERY)],
            [KeyboardButton(text=MENU_SELLER), KeyboardButton(text=MENU_LISTINGS)],
            [KeyboardButton(text=MENU_COMPLAINTS), KeyboardButton(text=MENU_HELP)],
            [KeyboardButton(text=MENU_TERMS)],
        ]
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="",
    )


def get_support_hub_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="How VenDOOR Works", callback_data="support_how_it_works")],
            [InlineKeyboardButton(text="Contact Support", callback_data="support_contact")],
            [InlineKeyboardButton(text="Report an Issue", callback_data="complaints")],
            [InlineKeyboardButton(text="Back to Main Menu", callback_data="back_to_menu")],
        ]
    )


def get_catalog_categories() -> InlineKeyboardMarkup:
    categories = [
        ("iPads", "browse_cat_IPADS"),
        ("iPods", "browse_cat_IPODS"),
        ("Wigs", "browse_cat_WIGS"),
        ("Other Gadgets", "browse_cat_OTHERGADGETS"),
        ("Accessories", "browse_cat_JEWELRY"),
        ("Clothes", "browse_cat_CLOTHES"),
        ("Laptop", "browse_cat_ELECTRONICS"),
        ("Skincare & Perfumes", "browse_cat_SKINCARE"),
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
        + [[InlineKeyboardButton(text="Back to Main Menu", callback_data="back_to_menu")]]
    )
    return keyboard


def get_order_actions(order_id: int) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Track Delivery", callback_data=f"order_track_{order_id}")],
            [InlineKeyboardButton(text="Confirm Receipt", callback_data=f"order_confirm_{order_id}")],
            [InlineKeyboardButton(text="Report Issue", callback_data=f"order_dispute_{order_id}")],
            [InlineKeyboardButton(text="Back to Orders", callback_data="my_orders")],
        ]
    )
    return keyboard


def get_seller_actions() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Create Listing", callback_data="seller_create_listing")],
            [InlineKeyboardButton(text="Seller Dashboard", callback_data="seller_listings")],
            [InlineKeyboardButton(text="Sales Analytics", callback_data="seller_stats")],
            [InlineKeyboardButton(text="Back to Main Menu", callback_data="back_to_menu")],
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
