"""Favorites helpers."""

from __future__ import annotations

import math

import db
from config import PAGE_SIZE_FAVORITES


async def favorites_page(user_id: int, page: int) -> tuple[list[dict], int, int]:
    total = await db.count_user_favorites(user_id)
    if total == 0:
        return [], 0, 0
    total_pages = max(1, math.ceil(total / PAGE_SIZE_FAVORITES))
    page = max(0, min(page, total_pages - 1))
    offset = page * PAGE_SIZE_FAVORITES
    rows = await db.list_favorites_page(user_id, offset, PAGE_SIZE_FAVORITES)
    return rows, total, total_pages
