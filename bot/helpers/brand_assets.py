"""
Helpers for loading optional brand image assets from assets/brand.
"""

from pathlib import Path
from typing import Optional

from aiogram.types import FSInputFile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BRAND_ROOT = PROJECT_ROOT / "assets" / "brand"


def _file_input(path: Path) -> Optional[FSInputFile]:
    if path.exists() and path.is_file():
        return FSInputFile(path=str(path))
    return None


def get_welcome_banner() -> Optional[FSInputFile]:
    return _file_input(BRAND_ROOT / "welcome_banner.png")


def get_main_menu_banner() -> Optional[FSInputFile]:
    banner = _file_input(BRAND_ROOT / "main_menu_banner.png")
    if banner:
        return banner
    return get_welcome_banner()


def get_help_banner() -> Optional[FSInputFile]:
    return _file_input(BRAND_ROOT / "help_banner.png")


def get_empty_state(asset_name: str) -> Optional[FSInputFile]:
    return _file_input(BRAND_ROOT / "empty" / f"{asset_name}.png")


def get_category_hero(category_name: str, accessory_subcategory_name: str | None = None) -> Optional[FSInputFile]:
    category_name = category_name.upper()
    if category_name == "JEWELRY":
        categories_root = BRAND_ROOT / "categories"
        if accessory_subcategory_name:
            token = accessory_subcategory_name.lower()
            accessory_candidates = {
                "bags": ["accessories_bags.png", "bags.png"],
                "watches": ["accessories_watches.png", "watches.png"],
                "jewelry": ["accessories_jewelry.png", "jewelry.png", "jewellery.png", "jewelry.png.png"],
            }
            for filename in accessory_candidates.get(token, [f"accessories_{token}.png"]):
                candidate = _file_input(categories_root / filename)
                if candidate:
                    return candidate
            return None

        for filename in [
            "accessories_jewelry.png",
            "jewelry.png",
            "jewellery.png",
            "jewelry.png.png",
            "bags.png",
            "watches.png",
        ]:
            candidate = _file_input(categories_root / filename)
            if candidate:
                return candidate
        return None

    mapping = {
        "IPADS": "ipads.png",
        "IPODS": "ipods.png",
        "CLOTHES": "clothes.png",
        "ELECTRONICS": "laptop.png",
        "SKINCARE": "skincare.png",
        "BOOKS": "books.png",
        "SHOES": "shoes.png",
        "OTHERS": "others.png",
    }
    filename = mapping.get(category_name)
    if not filename:
        return None
    return _file_input(BRAND_ROOT / "categories" / filename)
