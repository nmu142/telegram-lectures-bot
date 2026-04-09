"""Shared handler utilities, conversation states, and global error handler."""

from __future__ import annotations

import logging
import time
import traceback
from telegram import Update
from telegram.ext import ContextTypes

import db

logger = logging.getLogger(__name__)

DEBOUNCE_CALLBACK_S = 0.5
DEBOUNCE_COMMAND_S = 0.5


def should_ignore_duplicate_callback(bot_data: dict, user_id: int, data: str) -> bool:
    """True if the same callback_data was handled for this user within DEBOUNCE_CALLBACK_S."""
    key = ("cbdeb", user_id, data)
    now = time.monotonic()
    last = bot_data.get(key)
    if last is not None and (now - last) < DEBOUNCE_CALLBACK_S:
        return True
    bot_data[key] = now
    return False


def should_ignore_duplicate_command(bot_data: dict, user_id: int, command: str) -> bool:
    """True if the same command was run for this user within DEBOUNCE_COMMAND_S."""
    key = ("cmddeb", user_id, command)
    now = time.monotonic()
    last = bot_data.get(key)
    if last is not None and (now - last) < DEBOUNCE_COMMAND_S:
        return True
    bot_data[key] = now
    return False


# Conversation states (single range for all handlers)
(
    ST_REQ_SUBJECT,
    ST_REQ_LECTURE,
    ST_CONTACT,
    ST_SEARCH,
    ST_BROADCAST,
    ST_ADD_SUBJECT,
    ST_ADD_LEC_SUBJECT,
    ST_ADD_LEC_FILE,
    ST_BATCH_SUBJECT,
    ST_BATCH_FILES,
    ST_DEL_SUB_PICK,
    ST_DEL_LEC_SUB,
    ST_DEL_LEC_PICK,
    ST_EDIT_SUB_PICK,
    ST_EDIT_SUB_NAME,
    ST_EDIT_LEC_SUB,
    ST_EDIT_LEC_PICK,
    ST_EDIT_LEC_CHOICE,
    ST_EDIT_LEC_NAME,
    ST_EDIT_LEC_FILE,
    ST_MOVE_LEC_SUB,
    ST_MOVE_LEC_PICK,
    ST_MOVE_LEC_TARGET,
    ST_CLR_SUB_PICK,
    ST_LINK_ADD_TITLE,
    ST_LINK_ADD_URL,
    ST_LINK_EDIT_PICK,
    ST_LINK_EDIT_TITLE,
    ST_LINK_EDIT_URL,
    ST_ADMIN_ADD_ID,
    ST_ADMIN_DEL_PICK,
    ST_REORDER_SUB,
    ST_REORDER_LEC_SUB,
    ST_REORDER_LEC,
) = range(34)


async def track_user(update: Update) -> None:
    u = update.effective_user
    if not u:
        return
    await db.upsert_user(u.id, u.username, u.first_name)


async def user_bot_accessible(update: Update) -> bool:
    """False if bot is stopped and user is not admin."""
    u = update.effective_user
    if not u:
        return False
    await track_user(update)
    if await db.is_bot_running():
        return True
    if await db.is_admin(u.id):
        return True
    return False


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    exc = context.error
    if exc:
        logger.error(
            "Unhandled error: %s | update=%s",
            exc,
            update,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
    else:
        logger.error("Error without exception | update=%s", update)
    err_text = "حدث خطأ غير متوقع. حاول مرة أخرى لاحقًا."
    if isinstance(update, Update):
        try:
            if update.effective_message:
                await update.effective_message.reply_text(err_text)
            elif update.callback_query and update.callback_query.message:
                await update.callback_query.message.reply_text(err_text)
        except Exception:
            logger.debug("Could not notify user about error: %s", traceback.format_exc())
