"""Helpers for Nigerian bank selection in Telegram flows."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


NIGERIAN_BANKS: list[tuple[str, str]] = [
    ("044", "Access Bank"),
    ("014", "Afribank"),
    ("023", "Citibank"),
    ("050", "Ecobank"),
    ("011", "First Bank"),
    ("214", "First City Monument Bank"),
    ("070", "Fidelity Bank"),
    ("058", "Guaranty Trust Bank"),
    ("030", "Heritage Bank"),
    ("301", "Jaiz Bank"),
    ("082", "Keystone Bank"),
    ("076", "Polaris Bank"),
    ("101", "Providus Bank"),
    ("221", "Stanbic IBTC Bank"),
    ("068", "Standard Chartered Bank"),
    ("232", "Sterling Bank"),
    ("100", "Suntrust Bank"),
    ("032", "Union Bank"),
    ("033", "United Bank For Africa"),
    ("215", "Unity Bank"),
    ("035", "Wema Bank"),
    ("057", "Zenith Bank"),
    ("090267", "Kuda Bank"),
    ("999991", "PalmPay"),
    ("999992", "Opay"),
]


def bank_name_from_code(code: str) -> str | None:
    cleaned = (code or "").strip()
    for bank_code, bank_name in NIGERIAN_BANKS:
        if bank_code == cleaned:
            return bank_name
    return None


def build_bank_picker_keyboard(prefix: str = "seller_bank_") -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for bank_code, bank_name in NIGERIAN_BANKS:
        rows.append(
            [
                InlineKeyboardButton(
                    text=bank_name,
                    callback_data=f"{prefix}{bank_code}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)

