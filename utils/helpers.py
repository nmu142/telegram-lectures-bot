"""Shared helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes


def title_from_document_filename(file_name: Optional[str]) -> str:
    if not file_name:
        return "محاضرة"
    stem = Path(file_name).stem
    return stem.strip() or "محاضرة"


def build_pagination_row(
    prefix: str,
    page: int,
    total_pages: int,
    extra: str = "",
) -> list[InlineKeyboardButton]:
    """prefix format: e.g. 'usub' -> callback usub|page|extra"""
    extra_part = f"|{extra}" if extra else ""
    buttons: list[InlineKeyboardButton] = []
    if total_pages <= 1:
        return buttons
    if page > 0:
        buttons.append(
            InlineKeyboardButton("◀️ السابق", callback_data=f"{prefix}|{page - 1}{extra_part}")
        )
    buttons.append(
        InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop")
    )
    if page < total_pages - 1:
        buttons.append(
            InlineKeyboardButton("التالي ▶️", callback_data=f"{prefix}|{page + 1}{extra_part}")
        )
    return buttons


async def safe_edit_text(
    query,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
    except Exception:
        await query.message.reply_text(text, reply_markup=reply_markup)


async def noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q:
        await q.answer()


def parse_callback(data: str, maxsplit: int = -1) -> list[str]:
    return data.split("|", maxsplit)


def escape_like(s: str) -> str:
    """Basic escape for LIKE patterns."""
    return s.replace("%", "\\%").replace("_", "\\_")
