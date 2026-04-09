"""User-facing handlers (Arabic UI)."""

from __future__ import annotations

import logging
import math
from typing import Optional

from telegram import Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import db
from config import (
    ADMIN_ID,
    PAGE_SIZE_LECTURES,
    PAGE_SIZE_SEARCH,
    PAGE_SIZE_SUBJECTS,
)
from handlers.common import (
    ST_CONTACT,
    ST_REQ_LECTURE,
    ST_REQ_SUBJECT,
    ST_SEARCH,
    should_ignore_duplicate_callback,
    should_ignore_duplicate_command,
    track_user,
    user_bot_accessible,
)
from keyboards import admin as kb_admin
from keyboards import user as kb_user
from services import search as search_svc

logger = logging.getLogger(__name__)


async def _send_main_menu(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    await message.reply_text(
        "القائمة الرئيسية — اختر خيارًا:",
        reply_markup=kb_user.main_menu_reply(),
    )


async def render_subjects(
    context: ContextTypes.DEFAULT_TYPE,
    page: int,
    *,
    edit_query=None,
    reply_message=None,
) -> None:
    """Single entry point for subjects list (commands, text, callbacks)."""
    total = await db.count_subjects()
    if total == 0:
        text = "لا توجد مواد بعد."
        kb = kb_user.main_menu_reply()
        if edit_query:
            await edit_query.edit_message_text(text, reply_markup=kb)
        elif reply_message:
            await reply_message.reply_text(text, reply_markup=kb)
        return
    total_pages = max(1, math.ceil(total / PAGE_SIZE_SUBJECTS))
    page = max(0, min(page, total_pages - 1))
    offset = page * PAGE_SIZE_SUBJECTS
    rows = await db.list_subjects_page(offset, PAGE_SIZE_SUBJECTS)
    text = "📚 المواد — اختر المادة:"
    markup = kb_user.subjects_page_keyboard(rows, page, total_pages)
    if edit_query:
        await edit_query.edit_message_text(text, reply_markup=markup)
    elif reply_message:
        await reply_message.reply_text(text, reply_markup=markup)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    u = update.effective_user
    if u and should_ignore_duplicate_command(context.application.bot_data, u.id, "start"):
        return ConversationHandler.END
    await track_user(update)
    if not await user_bot_accessible(update):
        await update.effective_message.reply_text(
            "⏹ البوت متوقف حاليًا. حاول لاحقًا."
        )
        return ConversationHandler.END
    await _send_main_menu(update.effective_message, context)
    return ConversationHandler.END


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    if u and should_ignore_duplicate_command(context.application.bot_data, u.id, "help"):
        return
    await track_user(update)
    if not await user_bot_accessible(update):
        await update.effective_message.reply_text("⏹ البوت متوقف حاليًا.")
        return
    await update.effective_message.reply_text(
        "ℹ️ المساعدة\n\n"
        "/start — القائمة الرئيسية\n"
        "/subjects — المواد\n"
        "/search — بحث عن محاضرة\n"
        "/help — هذه الرسالة\n"
        "/admin — لوحة الأدمن (للمصرح لهم فقط)\n",
        reply_markup=kb_user.main_menu_reply(),
    )


async def cmd_subjects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    if u and should_ignore_duplicate_command(context.application.bot_data, u.id, "subjects"):
        return
    await track_user(update)
    if not await user_bot_accessible(update):
        await update.effective_message.reply_text("⏹ البوت متوقف حاليًا.")
        return
    await render_subjects(context, 0, reply_message=update.effective_message)


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    u = update.effective_user
    if u and should_ignore_duplicate_command(context.application.bot_data, u.id, "search"):
        return ConversationHandler.END
    await track_user(update)
    if not await user_bot_accessible(update):
        await update.effective_message.reply_text("⏹ البوت متوقف حاليًا.")
        return ConversationHandler.END
    await update.effective_message.reply_text(
        "🔎 اكتب كلمة للبحث (جزئي، يدعم العربية):",
        reply_markup=kb_user.back_home_reply(),
    )
    return ST_SEARCH


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    if u and should_ignore_duplicate_command(context.application.bot_data, u.id, "admin"):
        return
    await track_user(update)
    if not u:
        return
    if await db.is_admin(u.id):
        await update.effective_message.reply_text(
            "لوحة الأدمن:",
            reply_markup=kb_admin.admin_main_reply(),
        )
    else:
        await update.effective_message.reply_text(
            "❌ غير مسموح لك بالوصول إلى لوحة الأدمن"
        )


async def show_lectures_for_subject(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    subject_id: int,
    page: int,
) -> None:
    sub = await db.get_subject(subject_id)
    if not sub:
        await query.edit_message_text(
            "المادة غير موجودة.",
            reply_markup=kb_user.main_menu_reply(),
        )
        return
    total = await db.count_lectures_in_subject(subject_id)
    if total == 0:
        await query.edit_message_text(
            f"📂 {sub['name']}\n\nلا توجد محاضرات بعد.",
            reply_markup=kb_user.lectures_page_keyboard([], subject_id, 0, 1),
        )
        return
    total_pages = max(1, math.ceil(total / PAGE_SIZE_LECTURES))
    page = max(0, min(page, total_pages - 1))
    offset = page * PAGE_SIZE_LECTURES
    lectures = await db.list_lectures_page(subject_id, offset, PAGE_SIZE_LECTURES)
    text = f"📂 {sub['name']}\n\nاختر المحاضرة:"
    await query.edit_message_text(
        text,
        reply_markup=kb_user.lectures_page_keyboard(
            lectures, subject_id, page, total_pages
        ),
    )


async def user_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    await track_user(update)
    if not await user_bot_accessible(update):
        await update.effective_message.reply_text("⏹ البوت متوقف حاليًا.")
        return None
    text = (update.effective_message.text or "").strip()
    if text == "🏠 الرئيسية":
        await _send_main_menu(update.effective_message, context)
        return ConversationHandler.END
    if text == "⬅️ رجوع":
        await _send_main_menu(update.effective_message, context)
        return ConversationHandler.END

    if text == "📚 المواد":
        await render_subjects(context, 0, reply_message=update.effective_message)
        return ConversationHandler.END
    if text == "🆕 أحدث المحاضرات":
        await show_latest_page(update.effective_message, context, page=0, new_msg=True)
        return ConversationHandler.END
    if text == "🔗 لينكات مهمة":
        links = await db.list_links()
        if not links:
            await update.effective_message.reply_text(
                "لا توجد لينكات بعد.",
                reply_markup=kb_user.main_menu_reply(),
            )
        else:
            await update.effective_message.reply_text(
                "🔗 لينكات مهمة — اضغط للفتح:",
                reply_markup=kb_user.links_keyboard(links),
            )
        return ConversationHandler.END

    return None


async def show_latest_page(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    page: int,
    new_msg: bool,
) -> None:
    total = await db.count_latest_lectures()
    if total == 0:
        text = "لا توجد محاضرات بعد."
        if new_msg:
            await message.reply_text(text, reply_markup=kb_user.main_menu_reply())
        else:
            await message.edit_text(text, reply_markup=kb_user.main_menu_reply())
        return
    total_pages = max(1, math.ceil(total / PAGE_SIZE_SEARCH))
    page = max(0, min(page, total_pages - 1))
    offset = page * PAGE_SIZE_SEARCH
    rows = await db.latest_lectures_page(offset, PAGE_SIZE_SEARCH)
    text = "🆕 أحدث المحاضرات:"
    markup = kb_user.latest_keyboard(rows, page, total_pages)
    if new_msg:
        await message.reply_text(text, reply_markup=markup)
    else:
        await message.edit_text(text, reply_markup=markup)


async def conv_search_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await track_user(update)
    if not await user_bot_accessible(update):
        return ConversationHandler.END
    q = (update.effective_message.text or "").strip()
    if q in ("⬅️ رجوع", "🏠 الرئيسية"):
        await _send_main_menu(update.effective_message, context)
        return ConversationHandler.END
    rows, total, total_pages = await search_svc.search_page(q, 0)
    if total == 0:
        await update.effective_message.reply_text(
            "لا توجد نتائج.",
            reply_markup=kb_user.main_menu_reply(),
        )
        return ConversationHandler.END
    context.user_data["last_search_query"] = q
    await update.effective_message.reply_text(
        f"🔎 نتائج البحث عن: {q}",
        reply_markup=kb_user.search_results_keyboard(rows, 0, total_pages),
    )
    return ConversationHandler.END


async def conv_request_subject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await track_user(update)
    if not await user_bot_accessible(update):
        return ConversationHandler.END
    t = (update.effective_message.text or "").strip()
    if t in ("⬅️ رجوع", "🏠 الرئيسية"):
        await _send_main_menu(update.effective_message, context)
        return ConversationHandler.END
    context.user_data["req_subject"] = t
    await update.effective_message.reply_text(
        "الآن اكتب اسم المحاضرة المطلوبة:",
        reply_markup=kb_user.back_home_reply(),
    )
    return ST_REQ_LECTURE


async def conv_request_lecture(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await track_user(update)
    if not await user_bot_accessible(update):
        return ConversationHandler.END
    t = (update.effective_message.text or "").strip()
    if t in ("⬅️ رجوع", "🏠 الرئيسية"):
        await _send_main_menu(update.effective_message, context)
        return ConversationHandler.END
    subj = context.user_data.get("req_subject", "")
    rid = await db.add_request(update.effective_user.id, subj, t)
    await update.effective_message.reply_text(
        "✅ تم إرسال طلبك للأدمن. شكرًا لك!",
        reply_markup=kb_user.main_menu_reply(),
    )
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"📝 طلب محاضرة جديد (#{rid})\n"
            f"المستخدم: {update.effective_user.id}\n"
            f"المادة: {subj}\n"
            f"المحاضرة: {t}",
        )
    except Exception as e:
        logger.warning("Notify admin request failed: %s", e)
    context.user_data.pop("req_subject", None)
    return ConversationHandler.END


async def conv_contact_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await track_user(update)
    if not await user_bot_accessible(update):
        return ConversationHandler.END
    if (update.effective_message.text or "").strip() in ("⬅️ رجوع", "🏠 الرئيسية"):
        await _send_main_menu(update.effective_message, context)
        return ConversationHandler.END
    u = update.effective_user
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"📩 رسالة من مستخدم\n"
            f"المعرف: {u.id}\n"
            f"الاسم: {u.full_name}\n\n"
            f"{update.effective_message.text}",
        )
    except Exception as e:
        logger.warning("Forward contact to admin failed: %s", e)
    await update.effective_message.reply_text(
        "✅ تم إرسال رسالتك للأدمن.",
        reply_markup=kb_user.main_menu_reply(),
    )
    return ConversationHandler.END


async def user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_user(update)
    q = update.callback_query
    if not q:
        return
    data = q.data or ""
    uid = q.from_user.id
    if should_ignore_duplicate_callback(context.application.bot_data, uid, data):
        await q.answer()
        return

    if not await user_bot_accessible(update):
        await q.answer()
        await q.message.reply_text("⏹ البوت متوقف حاليًا.")
        return

    parts = data.split("|")
    if data == "noop":
        await q.answer()
        return
    if data == "home":
        await q.answer()
        await q.message.reply_text(
            "القائمة الرئيسية:",
            reply_markup=kb_user.main_menu_reply(),
        )
        return

    await q.answer()

    if parts[0] == "usub":
        if parts[1] == "page":
            page = int(parts[2])
            await render_subjects(context, page, edit_query=q)
        elif parts[1] == "open":
            sid = int(parts[2])
            await show_lectures_for_subject(q, context, sid, 0)

    elif parts[0] == "ulec":
        if parts[1] == "page":
            sid = int(parts[2])
            page = int(parts[3])
            await show_lectures_for_subject(q, context, sid, page)
        elif parts[1] == "open":
            lid = int(parts[2])
            sid = int(parts[3])
            lec = await db.get_lecture(lid)
            if not lec or lec["subject_id"] != sid:
                await q.message.reply_text("المحاضرة غير موجودة.")
                return
            try:
                await context.bot.send_document(
                    chat_id=q.message.chat_id,
                    document=lec["file_id"],
                    caption=f"📄 {lec['title']}",
                )
            except Exception as e:
                logger.warning("send_document failed: %s", e)
                await q.message.reply_text("تعذر إرسال الملف.")
        elif parts[1] == "all":
            sid = int(parts[2])
            lecs = await db.list_all_lectures_in_subject(sid)
            if not lecs:
                await q.message.reply_text("لا توجد ملفات.")
                return
            await q.message.reply_text(f"📥 جاري إرسال {len(lecs)} ملفًا...")
            for lec in lecs:
                try:
                    await context.bot.send_document(
                        chat_id=q.message.chat_id,
                        document=lec["file_id"],
                        caption=f"📄 {lec['title']}",
                    )
                except Exception as e:
                    logger.warning("bulk send failed: %s", e)

    elif parts[0] == "usea":
        query_text = context.user_data.get("last_search_query")
        if not query_text:
            await q.message.reply_text("ابحث من جديد من القائمة.")
            return
        if parts[1] == "page":
            page = int(parts[2])
            rows, total, total_pages = await search_svc.search_page(query_text, page)
            await q.edit_message_text(
                f"🔎 نتائج البحث عن: {query_text}",
                reply_markup=kb_user.search_results_keyboard(rows, page, total_pages),
            )

    elif parts[0] == "ulate":
        page = int(parts[2])
        await show_latest_page(q.message, context, page, new_msg=False)


async def entry_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await track_user(update)
    if not await user_bot_accessible(update):
        return ConversationHandler.END
    await update.effective_message.reply_text(
        "📝 اكتب اسم المادة في رسالة واحدة:",
        reply_markup=kb_user.back_home_reply(),
    )
    return ST_REQ_SUBJECT


async def entry_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await track_user(update)
    if not await user_bot_accessible(update):
        return ConversationHandler.END
    await update.effective_message.reply_text(
        "👤 اكتب رسالتك للأدمن في الرسالة التالية:",
        reply_markup=kb_user.back_home_reply(),
    )
    return ST_CONTACT


async def fallback_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await cmd_start(update, context)


def build_user_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("search", cmd_search),
            MessageHandler(
                filters.Regex("^🔎 البحث عن محاضرة$") & filters.ChatType.PRIVATE,
                cmd_search,
            ),
            MessageHandler(
                filters.Regex("^📝 طلب محاضرة / نقص ملفات$") & filters.ChatType.PRIVATE,
                entry_request,
            ),
            MessageHandler(
                filters.Regex("^👤 التواصل مع الأدمن$") & filters.ChatType.PRIVATE,
                entry_contact,
            ),
        ],
        states={
            ST_SEARCH: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, conv_search_message),
            ],
            ST_REQ_SUBJECT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, conv_request_subject),
            ],
            ST_REQ_LECTURE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, conv_request_lecture),
            ],
            ST_CONTACT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, conv_contact_message),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_start),
            CommandHandler("start", cmd_start),
            MessageHandler(filters.Regex("^(🏠 الرئيسية|⬅️ رجوع)$"), fallback_home),
        ],
        name="user_conv",
        persistent=False,
    )
