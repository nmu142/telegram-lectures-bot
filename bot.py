"""Telegram Lectures Bot — entry point (polling)."""

from __future__ import annotations

import logging

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from config import BOT_TOKEN, LOG_LEVEL
from db import close_db, init_db
from handlers import admin as admin_handlers
from handlers import user as user_handlers
from handlers.common import error_handler

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
)
logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    await init_db()
    logger.info("Database ready.")


async def post_shutdown(application: Application) -> None:
    await close_db()
    logger.info("Database closed.")


def main() -> None:
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_error_handler(error_handler)

    application.add_handler(CommandHandler("start", user_handlers.cmd_start), group=0)
    application.add_handler(CommandHandler("help", user_handlers.cmd_help), group=0)
    application.add_handler(CommandHandler("subjects", user_handlers.cmd_subjects), group=0)
    application.add_handler(CommandHandler("admin", user_handlers.cmd_admin), group=0)

    application.add_handler(user_handlers.build_user_conversation(), group=1)

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            admin_handlers.admin_text_router,
        ),
        group=2,
    )
    application.add_handler(
        MessageHandler(
            filters.Document.ALL & filters.ChatType.PRIVATE,
            admin_handlers.admin_document_router,
        ),
        group=2,
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            user_handlers.user_text_router,
        ),
        group=3,
    )

    application.add_handler(
        CallbackQueryHandler(
            user_handlers.user_callback,
            pattern=r"^(usub|ulec|usea|ufav|ulate|home|noop)",
        ),
        group=4,
    )
    application.add_handler(CallbackQueryHandler(admin_handlers.admin_callback_router), group=5)

    logger.info("Starting polling...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
