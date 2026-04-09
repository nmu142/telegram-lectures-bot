"""Admin keyboards (Arabic)."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def admin_main_reply() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("➕ إضافة مادة")],
            [KeyboardButton("➕ إضافة محاضرة")],
            [KeyboardButton("📥 رفع دفعة محاضرات")],
            [KeyboardButton("🗑 حذف مادة")],
            [KeyboardButton("🗑 حذف محاضرة")],
            [KeyboardButton("✏️ تعديل مادة")],
            [KeyboardButton("✏️ تعديل محاضرة")],
            [KeyboardButton("🔄 نقل محاضرة لمادة أخرى")],
            [KeyboardButton("🧹 حذف كل محاضرات مادة")],
            [KeyboardButton("📊 الإحصائيات")],
            [KeyboardButton("📢 رسالة جماعية")],
            [KeyboardButton("👮 إدارة الأدمنز")],
            [KeyboardButton("🔗 إدارة اللينكات")],
            [KeyboardButton("📦 نسخ احتياطي")],
            [KeyboardButton("▶️ تشغيل البوت")],
            [KeyboardButton("⏹ إيقاف البوت")],
            [KeyboardButton("📝 سجل الأدمن")],
            [KeyboardButton("🔢 ترتيب المواد")],
            [KeyboardButton("🔢 ترتيب المحاضرات")],
            [KeyboardButton("⬅️ رجوع للمستخدم")],
        ],
        resize_keyboard=True,
    )


def admin_cancel_reply() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("❌ إلغاء")]],
        resize_keyboard=True,
    )


def confirm_delete_lecture_keyboard(lecture_id: int, subject_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ تأكيد الحذف",
                    callback_data=f"adel|lec_go|{lecture_id}|{subject_id}",
                ),
                InlineKeyboardButton(
                    "❌ إلغاء",
                    callback_data=f"adel|lec_x|{lecture_id}",
                ),
            ]
        ]
    )


def confirm_delete_subject_keyboard(subject_id: int, lecture_count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ تأكيد حذف المادة",
                    callback_data=f"adel|sub_go|{subject_id}",
                ),
                InlineKeyboardButton(
                    "❌ إلغاء",
                    callback_data=f"adel|sub_x|{subject_id}",
                ),
            ]
        ]
    )
