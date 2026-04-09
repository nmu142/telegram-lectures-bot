"""Admin panel handlers (Arabic)."""

from __future__ import annotations

import logging
import math
from typing import Any, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db
from config import (
    ADMIN_ID,
    PAGE_SIZE_LECTURES,
    PAGE_SIZE_SUBJECTS,
    BATCH_UPLOAD_PROGRESS_EVERY,
)
from handlers.common import track_user
from keyboards import admin as kb_admin
from keyboards import user as kb_user
from services import broadcast as broadcast_svc
from services import upload as upload_svc
from utils.helpers import title_from_document_filename

logger = logging.getLogger(__name__)


def _flow(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    return context.user_data.setdefault("admin_flow", {})


def _clear_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("admin_flow", None)


async def _log(admin_id: int, action: str, details: Optional[str] = None) -> None:
    await db.add_log(admin_id, action, details)


async def _admin_home_message(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    await message.reply_text(
        "لوحة الأدمن:",
        reply_markup=kb_admin.admin_main_reply(),
    )


def _subjects_pick_keyboard(
    subjects: list[dict], prefix: str, page: int, total_pages: int
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for s in subjects:
        rows.append(
            [
                InlineKeyboardButton(
                    s["name"][:40],
                    callback_data=f"{prefix}|pick|{s['id']}",
                )
            ]
        )
    nav = []
    if total_pages > 1:
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    "◀️", callback_data=f"{prefix}|page|{page - 1}"
                )
            )
        nav.append(
            InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop")
        )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    "▶️", callback_data=f"{prefix}|page|{page + 1}"
                )
            )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"{prefix}|cancel")])
    return InlineKeyboardMarkup(rows)


async def admin_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    if not u or not await db.is_admin(u.id):
        return
    await track_user(update)
    text = (update.effective_message.text or "").strip()
    if text == "⬅️ رجوع للمستخدم":
        _clear_flow(context)
        await update.effective_message.reply_text(
            "وضع المستخدم:",
            reply_markup=kb_user.main_menu_reply(),
        )
        return

    if text == "❌ إلغاء":
        _clear_flow(context)
        await _admin_home_message(update.effective_message, context)
        return

    flow = _flow(context)

    if text == "➕ إضافة مادة":
        flow.clear()
        flow["mode"] = "add_subject"
        await update.effective_message.reply_text(
            "اكتب اسم المادة الجديدة:",
            reply_markup=kb_admin.admin_cancel_reply(),
        )
        return

    if text == "✏️ تعديل مادة":
        flow.clear()
        flow["mode"] = "pick_edit_subject"
        total = await db.count_subjects()
        if total == 0:
            await update.effective_message.reply_text("لا توجد مواد.")
            return
        await _send_subject_pick(update.effective_message, context, "editsub", 0)
        return

    if text == "🗑 حذف مادة":
        flow.clear()
        flow["mode"] = "pick_del_subject"
        total = await db.count_subjects()
        if total == 0:
            await update.effective_message.reply_text("لا توجد مواد.")
            return
        await _send_subject_pick(update.effective_message, context, "delsub", 0)
        return

    if text == "🧹 حذف كل محاضرات مادة":
        flow.clear()
        flow["mode"] = "pick_clear_subject"
        total = await db.count_subjects()
        if total == 0:
            await update.effective_message.reply_text("لا توجد مواد.")
            return
        await _send_subject_pick(update.effective_message, context, "clrsub", 0)
        return

    if text == "➕ إضافة محاضرة":
        flow.clear()
        flow["mode"] = "pick_add_lec_subject"
        total = await db.count_subjects()
        if total == 0:
            await update.effective_message.reply_text("لا توجد مواد. أضف مادة أولًا.")
            return
        await _send_subject_pick(update.effective_message, context, "addlec", 0)
        return

    if text == "📥 رفع دفعة محاضرات":
        flow.clear()
        flow["mode"] = "pick_batch_subject"
        total = await db.count_subjects()
        if total == 0:
            await update.effective_message.reply_text("لا توجد مواد.")
            return
        await _send_subject_pick(update.effective_message, context, "batch", 0)
        return

    if text == "🗑 حذف محاضرة":
        flow.clear()
        flow["mode"] = "pick_del_lec_subject"
        total = await db.count_subjects()
        if total == 0:
            await update.effective_message.reply_text("لا توجد مواد.")
            return
        await _send_subject_pick(update.effective_message, context, "dellecsub", 0)
        return

    if text == "✏️ تعديل محاضرة":
        flow.clear()
        flow["mode"] = "pick_edit_lec_subject"
        total = await db.count_subjects()
        if total == 0:
            await update.effective_message.reply_text("لا توجد مواد.")
            return
        await _send_subject_pick(update.effective_message, context, "editlecsub", 0)
        return

    if text == "🔄 نقل محاضرة لمادة أخرى":
        flow.clear()
        flow["mode"] = "move_pick_subject"
        total = await db.count_subjects()
        if total == 0:
            await update.effective_message.reply_text("لا توجد مواد.")
            return
        await _send_subject_pick(update.effective_message, context, "movelecsub", 0)
        return

    if text == "📊 الإحصائيات":
        u = await db.count_users()
        s = await db.count_subjects()
        l = await db.count_lectures_total()
        r = await db.count_requests()
        await update.effective_message.reply_text(
            "📊 الإحصائيات\n\n"
            f"👥 المستخدمون: {u}\n"
            f"📚 المواد: {s}\n"
            f"📄 المحاضرات: {l}\n"
            f"📝 الطلبات: {r}\n"
        )
        return

    if text == "📢 رسالة جماعية":
        flow.clear()
        flow["mode"] = "broadcast"
        await update.effective_message.reply_text(
            "أرسل الرسالة التي تريد إيصالها للجميع (نص، صورة، فيديو، ملف، تصويت...):",
            reply_markup=kb_admin.admin_cancel_reply(),
        )
        return

    if text == "👮 إدارة الأدمنز":
        await _show_admins_menu(update.effective_message, context)
        return

    if text == "🔗 إدارة اللينكات":
        await _show_links_menu(update.effective_message, context)
        return

    if text == "📦 نسخ احتياطي":
        data = await db.export_db_blob()
        await update.effective_message.reply_document(
            document=data,
            filename="bot_backup.sqlite3",
            caption="📦 نسخة احتياطية من قاعدة البيانات",
        )
        await _log(update.effective_user.id, "backup", None)
        return

    if text == "▶️ تشغيل البوت":
        await db.set_setting("bot_running", "1")
        await update.effective_message.reply_text("✅ تم تشغيل البوت للمستخدمين.")
        await _log(update.effective_user.id, "bot_start", None)
        return

    if text == "⏹ إيقاف البوت":
        await db.set_setting("bot_running", "0")
        await update.effective_message.reply_text("⏹ تم إيقاف البوت للمستخدمين (الأدمن يعمل).")
        await _log(update.effective_user.id, "bot_stop", None)
        return

    if text == "📝 سجل الأدمن":
        logs = await db.recent_logs(40)
        if not logs:
            await update.effective_message.reply_text("لا توجد سجلات.")
            return
        lines = []
        for lg in logs:
            lines.append(
                f"[{lg['created_at']}] {lg['action']} — {lg.get('details') or ''}"
            )
        chunk = "\n".join(lines)
        if len(chunk) > 3500:
            chunk = chunk[:3500] + "\n..."
        await update.effective_message.reply_text(chunk)
        return

    if text == "🔢 ترتيب المواد":
        flow.clear()
        flow["mode"] = "reorder_subjects"
        ids = await db.list_all_subject_ids_ordered()
        if len(ids) < 2:
            await update.effective_message.reply_text("تحتاج مادتين على الأقل لترتيب المواد.")
            return
        await update.effective_message.reply_text(
            "اختر مادة لتحريكها:",
            reply_markup=_reorder_subjects_kb(ids),
        )
        return

    if text == "🔢 ترتيب المحاضرات":
        flow.clear()
        flow["mode"] = "pick_reorder_lec_subject"
        total = await db.count_subjects()
        if total == 0:
            await update.effective_message.reply_text("لا توجد مواد.")
            return
        await _send_subject_pick(update.effective_message, context, "reolecsub", 0)
        return

    mode = flow.get("mode")
    if mode == "add_subject":
        name = text
        ok, _ = await db.add_subject(name)
        if not ok:
            await update.effective_message.reply_text("❌ الاسم مكرر أو غير صالح.")
            return
        await update.effective_message.reply_text("✅ تمت إضافة المادة.")
        await _log(update.effective_user.id, "add_subject", name)
        flow.clear()
        await _admin_home_message(update.effective_message, context)
        return

    if mode == "edit_subject_name":
        sid = int(flow["subject_id"])
        ok = await db.update_subject_name(sid, text)
        if not ok:
            await update.effective_message.reply_text("❌ الاسم مكرر.")
            return
        await update.effective_message.reply_text("✅ تم التعديل.")
        await _log(update.effective_user.id, "edit_subject", str(sid))
        flow.clear()
        await _admin_home_message(update.effective_message, context)
        return

    if mode == "broadcast":
        await update.effective_message.reply_text("⏳ جاري الإرسال...")
        ok, fail = await broadcast_svc.send_broadcast_copy(
            context,
            update.effective_chat.id,
            update.effective_message.message_id,
        )
        await update.effective_message.reply_text(
            f"✅ اكتمل الإرسال.\nنجاح: {ok}\nفشل: {fail}",
            reply_markup=kb_admin.admin_main_reply(),
        )
        await _log(update.effective_user.id, "broadcast", f"ok={ok} fail={fail}")
        flow.clear()
        return

    if mode == "admin_add_id":
        try:
            new_id = int(text)
        except ValueError:
            await update.effective_message.reply_text("❌ المعرف يجب أن يكون رقمًا.")
            return
        if new_id == ADMIN_ID:
            await update.effective_message.reply_text("هذا الأدمن الرئيسي موجود بالفعل.")
            flow.clear()
            return
        added = await db.add_admin(new_id, update.effective_user.id)
        if not added:
            await update.effective_message.reply_text("❌ لم يتم الإضافة (قد يكون موجودًا).")
        else:
            await update.effective_message.reply_text("✅ تمت إضافة الأدمن.")
            await _log(update.effective_user.id, "add_admin", str(new_id))
        flow.clear()
        await _admin_home_message(update.effective_message, context)
        return

    if mode == "link_add_title":
        flow["link_title"] = text
        flow["mode"] = "link_add_url"
        await update.effective_message.reply_text("الآن أرسل رابط الURL:")
        return

    if mode == "link_add_url":
        title = flow.get("link_title", "")
        await db.add_link(title, text)
        await update.effective_message.reply_text("✅ تمت إضافة الرابط.")
        await _log(update.effective_user.id, "add_link", title)
        flow.clear()
        await _show_links_menu(update.effective_message, context)
        return

    if mode == "link_edit_title":
        lid = int(flow["link_id"])
        await db.update_link_title(lid, text)
        await update.effective_message.reply_text("✅ تم تعديل الاسم.")
        await _log(update.effective_user.id, "edit_link_title", str(lid))
        flow.clear()
        await _show_links_menu(update.effective_message, context)
        return

    if mode == "link_edit_url":
        lid = int(flow["link_id"])
        await db.update_link_url(lid, text)
        await update.effective_message.reply_text("✅ تم تعديل الرابط.")
        await _log(update.effective_user.id, "edit_link_url", str(lid))
        flow.clear()
        await _show_links_menu(update.effective_message, context)
        return

    if mode == "edit_lecture_name":
        lid = int(flow["lecture_id"])
        await db.update_lecture_title(lid, text)
        await update.effective_message.reply_text("✅ تم تعديل الاسم.")
        await _log(update.effective_user.id, "edit_lecture_title", str(lid))
        flow.clear()
        await _admin_home_message(update.effective_message, context)
        return

    return


async def _send_subject_pick(message, context: ContextTypes.DEFAULT_TYPE, prefix: str, page: int) -> None:
    total = await db.count_subjects()
    total_pages = max(1, math.ceil(total / PAGE_SIZE_SUBJECTS))
    page = max(0, min(page, total_pages - 1))
    offset = page * PAGE_SIZE_SUBJECTS
    rows = await db.list_subjects_page(offset, PAGE_SIZE_SUBJECTS)
    await message.reply_text(
        "اختر المادة:",
        reply_markup=_subjects_pick_keyboard(rows, prefix, page, total_pages),
    )


def _reorder_subjects_kb(ids: list[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for sid in ids:
        rows.append(
            [
                InlineKeyboardButton(
                    f"⬆️ {sid}",
                    callback_data=f"rsu|up|{sid}",
                ),
                InlineKeyboardButton(
                    f"⬇️ {sid}",
                    callback_data=f"rsu|dn|{sid}",
                ),
            ]
        )
    rows.append([InlineKeyboardButton("✅ تم", callback_data="rsu|done")])
    return InlineKeyboardMarkup(rows)


def _reorder_lectures_kb(ids: list[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for lid in ids:
        rows.append(
            [
                InlineKeyboardButton(
                    f"⬆️ {lid}",
                    callback_data=f"rle|up|{lid}",
                ),
                InlineKeyboardButton(
                    f"⬇️ {lid}",
                    callback_data=f"rle|dn|{lid}",
                ),
            ]
        )
    rows.append([InlineKeyboardButton("✅ تم", callback_data="rle|done")])
    return InlineKeyboardMarkup(rows)


async def _show_admins_menu(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await db.list_admins()
    lines = ["👮 الأدمنز الحاليون:"]
    for r in rows:
        uid = r["user_id"]
        mark = " (رئيسي)" if uid == ADMIN_ID else ""
        lines.append(f"• `{uid}`{mark}")
    text = "\n".join(lines)
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ إضافة أدمن", callback_data="adm|add")],
            [InlineKeyboardButton("🗑 حذف أدمن", callback_data="adm|del")],
        ]
    )
    await message.reply_text(text, reply_markup=kb, parse_mode="Markdown")


async def _show_links_menu(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    links = await db.list_links()
    if not links:
        await message.reply_text(
            "لا توجد لينكات.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("➕ إضافة رابط", callback_data="lnk|add")]]
            ),
        )
        return
    rows: list[list[InlineKeyboardButton]] = []
    for ln in links:
        rows.append(
            [
                InlineKeyboardButton(
                    f"✏️ {ln['title'][:25]}",
                    callback_data=f"lnk|edit|{ln['id']}",
                ),
                InlineKeyboardButton(
                    "🗑",
                    callback_data=f"lnk|del|{ln['id']}",
                ),
            ]
        )
    rows.append([InlineKeyboardButton("➕ إضافة رابط", callback_data="lnk|add")])
    await message.reply_text(
        "🔗 إدارة اللينكات — اختر إجراءًا:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def admin_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    u = update.effective_user
    if not u or not await db.is_admin(u.id):
        if q:
            await q.answer()
        return
    await track_user(update)
    if not q:
        return
    await q.answer()
    parts = (q.data or "").split("|")
    uid = q.from_user.id

    if parts[0] == "noop":
        return

    flow = _flow(context)

    # Subject pickers: {prefix}|pick|id OR {prefix}|page|n OR cancel
    prefixes = (
        "editsub",
        "delsub",
        "clrsub",
        "addlec",
        "batch",
        "dellecsub",
        "editlecsub",
        "movelecsub",
        "reolecsub",
    )
    if parts[0] in prefixes:
        prefix = parts[0]
        if parts[1] == "cancel":
            _clear_flow(context)
            await q.edit_message_text("تم الإلغاء.")
            return
        if parts[1] == "page":
            page = int(parts[2])
            total = await db.count_subjects()
            total_pages = max(1, math.ceil(total / PAGE_SIZE_SUBJECTS))
            page = max(0, min(page, total_pages - 1))
            offset = page * PAGE_SIZE_SUBJECTS
            rows = await db.list_subjects_page(offset, PAGE_SIZE_SUBJECTS)
            await q.edit_message_text(
                "اختر المادة:",
                reply_markup=_subjects_pick_keyboard(rows, prefix, page, total_pages),
            )
            return
        if parts[1] == "pick":
            sid = int(parts[2])
            if prefix == "editsub":
                flow.clear()
                flow["mode"] = "edit_subject_name"
                flow["subject_id"] = sid
                await q.edit_message_text("اكتب الاسم الجديد للمادة:")
                return
            if prefix == "delsub":
                n = await db.count_lectures_in_subject(sid)
                sub = await db.get_subject(sid)
                await q.edit_message_text(
                    f"🗑 حذف المادة «{sub['name']}»؟\nعدد المحاضرات: {n}",
                    reply_markup=kb_admin.confirm_delete_subject_keyboard(sid, n),
                )
                return
            if prefix == "clrsub":
                n = await db.count_lectures_in_subject(sid)
                sub = await db.get_subject(sid)
                await q.edit_message_text(
                    f"🧹 حذف كل محاضرات «{sub['name']}»؟ ({n} محاضرة)",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "✅ تأكيد",
                                    callback_data=f"clr|go|{sid}",
                                ),
                                InlineKeyboardButton("❌ إلغاء", callback_data="clr|x"),
                            ]
                        ]
                    ),
                )
                return
            if prefix == "addlec":
                flow.clear()
                flow["mode"] = "wait_add_lecture_file"
                flow["subject_id"] = sid
                await q.edit_message_text("أرسل ملف المحاضرة (كوثيقة):")
                return
            if prefix == "batch":
                flow.clear()
                flow["mode"] = "batch_collect"
                flow["subject_id"] = sid
                msg = await q.message.reply_text(
                    "📥 أرسل الملفات واحدًا تلو الآخر. عند الانتهاء اضغط «✅ إنهاء الرفع» أو «❌ إلغاء».",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "✅ إنهاء الرفع",
                                    callback_data=f"bat|done|{sid}",
                                ),
                                InlineKeyboardButton(
                                    "❌ إلغاء",
                                    callback_data=f"bat|cancel|{sid}",
                                ),
                            ]
                        ]
                    ),
                )
                upload_svc.start_batch(
                    context.user_data,
                    sid,
                    q.message.chat_id,
                    msg.message_id,
                )
                return
            if prefix == "dellecsub":
                flow.clear()
                flow["subject_id"] = sid
                await q.edit_message_text("اختر المحاضرة من الرسالة التالية:")
                await _send_lecture_pick(q.message, context, sid, "dellec", 0)
                return
            if prefix == "editlecsub":
                flow.clear()
                flow["subject_id"] = sid
                await q.edit_message_text("اختر المحاضرة من الرسالة التالية:")
                await _send_lecture_pick(q.message, context, sid, "editlec", 0)
                return
            if prefix == "movelecsub":
                flow.clear()
                flow["subject_id"] = sid
                await q.edit_message_text("اختر المحاضرة من الرسالة التالية:")
                await _send_lecture_pick(q.message, context, sid, "movelec", 0)
                return
            if prefix == "reolecsub":
                flow.clear()
                flow["mode"] = "reorder_lectures"
                flow["subject_id"] = sid
                ids = await db.list_lecture_ids_in_subject_ordered(sid)
                if len(ids) < 2:
                    await q.edit_message_text("لا يوجد ما يكفي من المحاضرات للترتيب.")
                    return
                await q.edit_message_text("تم اختيار المادة.")
                await q.message.reply_text(
                    "رتّب المحاضرات:",
                    reply_markup=_reorder_lectures_kb(ids),
                )
                return

    if parts[0] == "adel":
        if parts[1] == "sub_go":
            sid = int(parts[2])
            await db.delete_subject(sid)
            await q.edit_message_text("✅ تم حذف المادة.")
            await _log(uid, "delete_subject", str(sid))
            return
        if parts[1] == "sub_x":
            await q.edit_message_text("تم الإلغاء.")
            return
        if parts[1] == "lec_go":
            lid = int(parts[2])
            await db.delete_lecture(lid)
            await q.edit_message_text("✅ تم حذف المحاضرة.")
            await _log(uid, "delete_lecture", str(lid))
            return
        if parts[1] == "lec_x":
            await q.edit_message_text("تم الإلغاء.")
            return

    if parts[0] == "clr":
        if parts[1] == "go":
            sid = int(parts[2])
            n = await db.delete_all_lectures_in_subject(sid)
            await q.edit_message_text(f"✅ تم حذف {n} محاضرة.")
            await _log(uid, "clear_subject_lectures", str(sid))
        else:
            await q.edit_message_text("تم الإلغاء.")
        return

    if parts[0] == "dellec":
        await _handle_lecture_pick_callback(q, context, parts, mode="del")
        return
    if parts[0] == "editlec":
        await _handle_lecture_pick_callback(q, context, parts, mode="edit")
        return
    if parts[0] == "movelec":
        await _handle_lecture_pick_callback(q, context, parts, mode="move")
        return

    if parts[0] == "movet":
        flow = _flow(context)
        if parts[1] == "cancel":
            _clear_flow(context)
            await q.edit_message_text("تم الإلغاء.")
            return
        if parts[1] == "page":
            page = int(parts[2])
            total = await db.count_subjects()
            total_pages = max(1, math.ceil(total / PAGE_SIZE_SUBJECTS))
            page = max(0, min(page, total_pages - 1))
            offset = page * PAGE_SIZE_SUBJECTS
            rows = await db.list_subjects_page(offset, PAGE_SIZE_SUBJECTS)
            await q.edit_message_text(
                "اختر المادة الجديدة:",
                reply_markup=_subjects_pick_keyboard(rows, "movet", page, total_pages),
            )
            return
        if parts[1] == "pick":
            new_sid = int(parts[2])
            lid = int(flow.get("lecture_id", 0))
            old_sid = int(flow.get("from_subject_id", 0))
            if not lid or not old_sid:
                await q.answer("انتهت الجلسة. أعد المحاولة.", show_alert=True)
                return
            if new_sid == old_sid:
                await q.answer("اختر مادة مختلفة.", show_alert=True)
                return
            await db.move_lecture(lid, new_sid)
            await q.edit_message_text("✅ تم نقل المحاضرة.")
            await _log(uid, "move_lecture", f"{lid}->{new_sid}")
            _clear_flow(context)
            return

    if parts[0] == "eled":
        flow = _flow(context)
        if parts[1] == "name":
            lid = int(parts[2])
            flow.clear()
            flow["mode"] = "edit_lecture_name"
            flow["lecture_id"] = lid
            await q.edit_message_text("أرسل الاسم الجديد:")
            return
        if parts[1] == "file":
            lid = int(parts[2])
            flow.clear()
            flow["mode"] = "wait_replace_lecture_file"
            flow["lecture_id"] = lid
            await q.edit_message_text("أرسل الملف الجديد:")
            return

    if parts[0] == "bat":
        sid = int(parts[2])
        if parts[1] == "cancel":
            upload_svc.cancel_batch(context.user_data)
            _clear_flow(context)
            await q.edit_message_text("❌ تم إلغاء الرفع.")
            return
        if parts[1] == "done":
            await _finalize_batch_upload(q, context, sid)
            return

    if parts[0] == "rsu":
        ids = await db.list_all_subject_ids_ordered()
        if parts[1] == "done":
            _clear_flow(context)
            await q.edit_message_text("✅ تم حفظ ترتيب المواد.")
            return
        sid = int(parts[2])
        try:
            idx = ids.index(sid)
        except ValueError:
            idx = -1
        if parts[1] == "up" and idx > 0:
            ids[idx - 1], ids[idx] = ids[idx], ids[idx - 1]
        elif parts[1] == "dn" and idx >= 0 and idx < len(ids) - 1:
            ids[idx + 1], ids[idx] = ids[idx], ids[idx + 1]
        await db.reorder_subjects(ids)
        await q.edit_message_reply_markup(reply_markup=_reorder_subjects_kb(ids))
        return

    if parts[0] == "rle":
        flow = _flow(context)
        sid = int(flow.get("subject_id", 0))
        if not sid:
            await q.answer("خطأ في السياق.", show_alert=True)
            return
        ids = await db.list_lecture_ids_in_subject_ordered(sid)
        if parts[1] == "done":
            _clear_flow(context)
            await q.edit_message_text("✅ تم حفظ ترتيب المحاضرات.")
            return
        lid = int(parts[2])
        try:
            idx = ids.index(lid)
        except ValueError:
            idx = -1
        if parts[1] == "up" and idx > 0:
            ids[idx - 1], ids[idx] = ids[idx], ids[idx - 1]
        elif parts[1] == "dn" and idx >= 0 and idx < len(ids) - 1:
            ids[idx + 1], ids[idx] = ids[idx], ids[idx + 1]
        await db.reorder_lectures(sid, ids)
        await q.edit_message_reply_markup(reply_markup=_reorder_lectures_kb(ids))
        return

    if parts[0] == "adm":
        if parts[1] == "add":
            flow.clear()
            flow["mode"] = "admin_add_id"
            await q.message.reply_text(
                "أرسل معرف Telegram الرقمي للأدمن الجديد:",
                reply_markup=kb_admin.admin_cancel_reply(),
            )
            return
        if parts[1] == "del":
            rows = await db.list_admins()
            kb_rows = []
            for r in rows:
                if r["user_id"] == ADMIN_ID:
                    continue
                kb_rows.append(
                    [
                        InlineKeyboardButton(
                            str(r["user_id"]),
                            callback_data=f"adm|rm|{r['user_id']}",
                        )
                    ]
                )
            if not kb_rows:
                await q.answer("لا يوجد أدمنز للحذف.", show_alert=True)
                return
            await q.message.reply_text(
                "اختر أدمنًا للحذف:",
                reply_markup=InlineKeyboardMarkup(kb_rows),
            )
            return
        if parts[1] == "rm":
            target = int(parts[2])
            ok = await db.remove_admin(target)
            await q.edit_message_text("✅ تم الحذف." if ok else "❌ فشل الحذف.")
            return

    if parts[0] == "lnk":
        if parts[1] == "add":
            flow.clear()
            flow["mode"] = "link_add_title"
            await q.message.reply_text("اكتب عنوان الرابط:")
            return
        if parts[1] == "del":
            lid = int(parts[2])
            await db.delete_link(lid)
            await q.edit_message_text("✅ تم حذف الرابط.")
            await _log(uid, "delete_link", str(lid))
            return
        if parts[1] == "edit":
            lid = int(parts[2])
            flow.clear()
            flow["link_id"] = lid
            await q.message.reply_text(
                "ماذا تريد تعديله؟",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "✏️ الاسم",
                                callback_data=f"lnk|et|{lid}",
                            ),
                            InlineKeyboardButton(
                                "🔗 الرابط",
                                callback_data=f"lnk|eu|{lid}",
                            ),
                        ]
                    ]
                ),
            )
            return
        if parts[1] == "et":
            lid = int(parts[2])
            flow.clear()
            flow["mode"] = "link_edit_title"
            flow["link_id"] = lid
            await q.message.reply_text("اكتب الاسم الجديد:")
            return
        if parts[1] == "eu":
            lid = int(parts[2])
            flow.clear()
            flow["mode"] = "link_edit_url"
            flow["link_id"] = lid
            await q.message.reply_text("أرسل الرابط الجديد:")
            return


async def _send_lecture_pick(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    subject_id: int,
    prefix: str,
    page: int,
) -> None:
    total = await db.count_lectures_in_subject(subject_id)
    if total == 0:
        await message.reply_text("لا توجد محاضرات في هذه المادة.")
        return
    total_pages = max(1, math.ceil(total / PAGE_SIZE_LECTURES))
    page = max(0, min(page, total_pages - 1))
    offset = page * PAGE_SIZE_LECTURES
    rows = await db.list_lectures_page(subject_id, offset, PAGE_SIZE_LECTURES)
    kb_rows: list[list[InlineKeyboardButton]] = []
    for lec in rows:
        kb_rows.append(
            [
                InlineKeyboardButton(
                    lec["title"][:50],
                    callback_data=f"{prefix}|pick|{lec['id']}|{subject_id}",
                )
            ]
        )
    nav = []
    if total_pages > 1:
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    "◀️",
                    callback_data=f"{prefix}|page|{subject_id}|{page - 1}",
                )
            )
        nav.append(
            InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop")
        )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    "▶️",
                    callback_data=f"{prefix}|page|{subject_id}|{page + 1}",
                )
            )
    if nav:
        kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"{prefix}|cancel")])
    await message.reply_text(
        "اختر المحاضرة:",
        reply_markup=InlineKeyboardMarkup(kb_rows),
    )


async def _handle_lecture_pick_callback(
    q,
    context: ContextTypes.DEFAULT_TYPE,
    parts: list[str],
    mode: str,
) -> None:
    prefix = parts[0]
    flow = _flow(context)
    if parts[1] == "cancel":
        _clear_flow(context)
        await q.edit_message_text("تم الإلغاء.")
        return
    if parts[1] == "page":
        sid = int(parts[2])
        page = int(parts[3])
        total = await db.count_lectures_in_subject(sid)
        total_pages = max(1, math.ceil(total / PAGE_SIZE_LECTURES))
        page = max(0, min(page, total_pages - 1))
        offset = page * PAGE_SIZE_LECTURES
        rows = await db.list_lectures_page(sid, offset, PAGE_SIZE_LECTURES)
        kb_rows: list[list[InlineKeyboardButton]] = []
        for lec in rows:
            kb_rows.append(
                [
                    InlineKeyboardButton(
                        lec["title"][:50],
                        callback_data=f"{prefix}|pick|{lec['id']}|{sid}",
                    )
                ]
            )
        nav = []
        if total_pages > 1:
            if page > 0:
                nav.append(
                    InlineKeyboardButton(
                        "◀️",
                        callback_data=f"{prefix}|page|{sid}|{page - 1}",
                    )
                )
            nav.append(
                InlineKeyboardButton(
                    f"{page + 1}/{total_pages}", callback_data="noop"
                )
            )
            if page < total_pages - 1:
                nav.append(
                    InlineKeyboardButton(
                        "▶️",
                        callback_data=f"{prefix}|page|{sid}|{page + 1}",
                    )
                )
        if nav:
            kb_rows.append(nav)
        kb_rows.append(
            [InlineKeyboardButton("❌ إلغاء", callback_data=f"{prefix}|cancel")]
        )
        await q.edit_message_text(
            "اختر المحاضرة:",
            reply_markup=InlineKeyboardMarkup(kb_rows),
        )
        return
    if parts[1] == "pick":
        lid = int(parts[2])
        sid = int(parts[3])
        lec = await db.get_lecture(lid)
        sub = await db.get_subject(sid)
        if mode == "del":
            await q.edit_message_text(
                f"🗑 حذف المحاضرة؟\nالمادة: {sub['name']}\nالمحاضرة: {lec['title']}",
                reply_markup=kb_admin.confirm_delete_lecture_keyboard(lid, sid),
            )
        elif mode == "edit":
            flow.clear()
            flow["mode"] = "edit_lecture_choice"
            flow["lecture_id"] = lid
            flow["subject_id"] = sid
            await q.edit_message_text(
                "ماذا تريد تعديله؟",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "✏️ الاسم",
                                callback_data=f"eled|name|{lid}",
                            ),
                            InlineKeyboardButton(
                                "📎 استبدال الملف",
                                callback_data=f"eled|file|{lid}",
                            ),
                        ]
                    ]
                ),
            )
        elif mode == "move":
            flow.clear()
            flow["mode"] = "move_pick_target_subject"
            flow["lecture_id"] = lid
            flow["from_subject_id"] = sid
            total = await db.count_subjects()
            total_pages = max(1, math.ceil(total / PAGE_SIZE_SUBJECTS))
            rows = await db.list_subjects_page(0, PAGE_SIZE_SUBJECTS)
            await q.edit_message_text(
                "اختر المادة الجديدة:",
                reply_markup=_subjects_pick_keyboard(rows, "movet", 0, total_pages),
            )
        return


async def admin_document_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    if not u or not await db.is_admin(u.id):
        return
    await track_user(update)
    doc = update.effective_message.document
    if not doc:
        return
    flow = _flow(context)
    mode = flow.get("mode")
    if mode == "wait_add_lecture_file":
        sid = int(flow["subject_id"])
        title = title_from_document_filename(doc.file_name)
        await db.add_lecture(
            sid,
            title,
            doc.file_id,
            doc.file_unique_id,
            doc.file_name,
        )
        await update.effective_message.reply_text(
            f"✅ تمت إضافة المحاضرة: {title}",
            reply_markup=kb_admin.admin_main_reply(),
        )
        await _log(update.effective_user.id, "add_lecture", title)
        flow.clear()
        return
    if mode == "wait_replace_lecture_file":
        lid = int(flow["lecture_id"])
        title = title_from_document_filename(doc.file_name)
        await db.update_lecture_file(
            lid,
            doc.file_id,
            doc.file_unique_id,
            doc.file_name,
            title_from_file=title,
        )
        await update.effective_message.reply_text(
            "✅ تم استبدال الملف وتحديث الاسم من اسم الملف.",
            reply_markup=kb_admin.admin_main_reply(),
        )
        await _log(update.effective_user.id, "replace_lecture_file", str(lid))
        flow.clear()
        return
    if mode == "batch_collect":
        n = upload_svc.add_file_to_batch(
            context.user_data,
            doc.file_id,
            doc.file_unique_id,
            doc.file_name,
        )
        b = upload_svc.get_batch(context.user_data)
        if b and n % BATCH_UPLOAD_PROGRESS_EVERY == 0:
            try:
                await context.bot.edit_message_text(
                    chat_id=b["progress_chat_id"],
                    message_id=b["progress_message_id"],
                    text=f"📥 تم استلام {n} ملفًا...",
                )
            except Exception as e:
                logger.debug("progress edit: %s", e)


async def _finalize_batch_upload(q, context: ContextTypes.DEFAULT_TYPE, sid: int) -> None:
    b = upload_svc.get_batch(context.user_data)
    if not b:
        await q.edit_message_text("لا يوجد رفع نشط.")
        return
    files = b["files"]
    if upload_svc.is_batch_cancelled(context.user_data):
        upload_svc.clear_batch(context.user_data)
        await q.edit_message_text("تم الإلغاء سابقًا.")
        return
    await q.edit_message_text(f"⏳ جاري رفع {len(files)} ملفًا...")
    ok = 0
    for i, f in enumerate(files):
        if upload_svc.is_batch_cancelled(context.user_data):
            break
        try:
            title = title_from_document_filename(f.get("file_name"))
            await db.add_lecture(
                sid,
                title,
                f["file_id"],
                f.get("file_unique_id"),
                f.get("file_name"),
            )
            ok += 1
        except Exception as e:
            logger.warning("batch item failed: %s", e)
    upload_svc.clear_batch(context.user_data)
    _clear_flow(context)
    await q.message.reply_text(
        f"✅ اكتمل الرفع. تمت إضافة {ok} محاضرة.",
        reply_markup=kb_admin.admin_main_reply(),
    )
    await _log(q.from_user.id, "batch_upload", str(ok))
