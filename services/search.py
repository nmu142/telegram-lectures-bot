"""Lecture search with pagination."""

from __future__ import annotations

import math

import db
from config import PAGE_SIZE_SEARCH


async def search_page(query: str, page: int) -> tuple[list[dict], int, int]:
    """Return (rows with subject_name, total_count, total_pages)."""
    if not query or not query.strip():
        return [], 0, 0
    total = await db.count_search_lectures(query)
    if total == 0:
        return [], 0, 0
    total_pages = max(1, math.ceil(total / PAGE_SIZE_SEARCH))
    page = max(0, min(page, total_pages - 1))
    offset = page * PAGE_SIZE_SEARCH
    rows = await db.search_lectures_page(query, offset, PAGE_SIZE_SEARCH)
    return rows, total, total_pages
