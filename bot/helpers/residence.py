"""
Shared residence picker helpers (hall, wing, floor, room).
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

HALLS = [
    "Daniel hall",
    "Joseph hall",
    "John hall",
    "Paul hall",
    "Peter hall",
    "Joshua hall",
    "Deborah hall",
    "Mary hall",
    "Dorcas hall",
    "Lydia hall",
]

WINGS = ["A", "B", "C", "D", "E", "F", "G", "H"]
FLOORS = [1, 2, 3, 4]


def hall_from_index(index: int) -> str | None:
    if index < 0 or index >= len(HALLS):
        return None
    return HALLS[index]


def build_hall_keyboard(prefix: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for idx, hall in enumerate(HALLS):
        rows.append([InlineKeyboardButton(text=hall, callback_data=f"{prefix}{idx}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_wing_keyboard(prefix: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text=wing, callback_data=f"{prefix}{wing}")
            for wing in WINGS[i:i + 4]
        ]
        for i in range(0, len(WINGS), 4)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_floor_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Floor {floor}", callback_data=f"{prefix}{floor}")]
            for floor in FLOORS
        ]
    )


def build_room_keyboard(prefix: str, floor: int) -> InlineKeyboardMarkup:
    # 11 rooms per floor: 101..111, 201..211, 301..311, 401..411
    room_numbers = [floor * 100 + room for room in range(1, 12)]
    rows = [
        [
            InlineKeyboardButton(text=str(room), callback_data=f"{prefix}{room}")
            for room in room_numbers[i:i + 3]
        ]
        for i in range(0, len(room_numbers), 3)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

