"""Broadcast messages to all users."""

from __future__ import annotations

import logging

from telegram.ext import ContextTypes

import db

logger = logging.getLogger(__name__)


async def iter_all_user_ids() -> list[int]:
    """Fetch all user ids from DB."""
    conn = await db.get_db()
    cur = await conn.execute("SELECT user_id FROM users")
    rows = await cur.fetchall()
    return [int(r[0]) for r in rows]


async def send_broadcast_copy(
    context: ContextTypes.DEFAULT_TYPE,
    from_chat_id: int,
    message_id: int,
) -> tuple[int, int]:
    """Copy message to every user. Returns (success_count, fail_count)."""
    user_ids = await iter_all_user_ids()
    ok = 0
    fail = 0
    for uid in user_ids:
        try:
            await context.bot.copy_message(
                chat_id=uid,
                from_chat_id=from_chat_id,
                message_id=message_id,
            )
            ok += 1
        except Exception as e:
            logger.warning("Broadcast copy failed for %s: %s", uid, e)
            fail += 1
    return ok, fail


async def send_broadcast_text(
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> tuple[int, int]:
    user_ids = await iter_all_user_ids()
    ok = 0
    fail = 0
    for uid in user_ids:
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            ok += 1
        except Exception as e:
            logger.warning("Broadcast text failed for %s: %s", uid, e)
            fail += 1
    return ok, fail
