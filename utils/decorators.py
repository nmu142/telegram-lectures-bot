"""Handler decorators."""

from __future__ import annotations

import functools
import logging
from typing import Awaitable, Callable, TypeVar

from telegram import Update
from telegram.ext import ContextTypes

import db

logger = logging.getLogger(__name__)

T = TypeVar("T")


def admin_only(
    handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[T]],
) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]:
    """Allow only admins (including main ADMIN_ID)."""

    @functools.wraps(handler)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user:
            return
        if not await db.is_admin(user.id):
            msg = "❌ غير مسموح لك بالوصول إلى لوحة الأدمن"
            if update.callback_query:
                await update.callback_query.answer(msg, show_alert=True)
            elif update.message:
                await update.message.reply_text(msg)
            return
        await handler(update, context)

    return wrapped


def private_chat_only(
    handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[T]],
) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]:
    """Ignore non-private chats."""

    @functools.wraps(handler)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat and update.effective_chat.type != "private":
            return
        await handler(update, context)

    return wrapped
