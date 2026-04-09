"""User-facing keyboards (Arabic)."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def main_menu_reply() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📚 المواد")],
            [KeyboardButton("⭐ المحاضرات المفضلة")],
            [KeyboardButton("🆕 أحدث المحاضرات")],
            [KeyboardButton("🔎 البحث عن محاضرة")],
            [KeyboardButton("📝 طلب محاضرة / نقص ملفات")],
            [KeyboardButton("🔗 لينكات مهمة")],
            [KeyboardButton("👤 التواصل مع الأدمن")],
        ],
        resize_keyboard=True,
    )


def back_home_reply() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("⬅️ رجوع"), KeyboardButton("🏠 الرئيسية")],
        ],
        resize_keyboard=True,
    )


def single_back_reply() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("⬅️ رجوع")]],
        resize_keyboard=True,
    )


def subjects_page_keyboard(
    subjects: list[dict],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for s in subjects:
        rows.append(
            [
                InlineKeyboardButton(
                    s["name"],
                    callback_data=f"usub|open|{s['id']}",
                )
            ]
        )
    nav = []
    if total_pages > 1:
        if page > 0:
            nav.append(
                InlineKeyboardButton("◀️ السابق", callback_data=f"usub|page|{page - 1}")
            )
        nav.append(
            InlineKeyboardButton(
                f"{page + 1}/{total_pages}", callback_data="noop"
            )
        )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton("التالي ▶️", callback_data=f"usub|page|{page + 1}")
            )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def lectures_page_keyboard(
    lectures: list[dict],
    subject_id: int,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for lec in lectures:
        lid = lec["id"]
        rows.append(
            [
                InlineKeyboardButton(
                    f"📄 {lec['title'][:60]}",
                    callback_data=f"ulec|open|{lid}|{subject_id}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    "⭐ للمفضلة" if not lec.get("_fav") else "إزالة من المفضلة",
                    callback_data=f"ulec|fav|{lid}|{subject_id}|{int(bool(lec.get('_fav')))}|{page}",
                )
            ]
        )
    nav = []
    if total_pages > 1:
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    "◀️ السابق",
                    callback_data=f"ulec|page|{subject_id}|{page - 1}",
                )
            )
        nav.append(
            InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop")
        )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    "التالي ▶️",
                    callback_data=f"ulec|page|{subject_id}|{page + 1}",
                )
            )
    if nav:
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton(
                "📥 تحميل كل المحاضرات",
                callback_data=f"ulec|all|{subject_id}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton("⬅️ للمواد", callback_data="usub|page|0"),
            InlineKeyboardButton("🏠 الرئيسية", callback_data="home"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def search_results_keyboard(
    items: list[dict],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for lec in items:
        lid = lec["id"]
        sid = lec["subject_id"]
        sub = lec.get("subject_name", "")
        label = f"📄 {lec['title'][:40]} — {sub[:20]}"
        rows.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=f"ulec|open|{lid}|{sid}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    "⭐ للمفضلة" if not lec.get("_fav") else "إزالة من المفضلة",
                    callback_data=f"usea|fav|{lid}|{sid}|{int(bool(lec.get('_fav')))}|{page}",
                )
            ]
        )
    nav = []
    if total_pages > 1:
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    "◀️ السابق", callback_data=f"usea|page|{page - 1}"
                )
            )
        nav.append(
            InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop")
        )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    "التالي ▶️", callback_data=f"usea|page|{page + 1}"
                )
            )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def favorites_keyboard(
    items: list[dict],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for lec in items:
        lid = lec["id"]
        sid = lec["subject_id"]
        rows.append(
            [
                InlineKeyboardButton(
                    f"📄 {lec['title'][:55]}",
                    callback_data=f"ulec|open|{lid}|{sid}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    "🗑 إزالة من المفضلة",
                    callback_data=f"ufav|rm|{lid}|{page}",
                )
            ]
        )
    nav = []
    if total_pages > 1:
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    "◀️ السابق", callback_data=f"ufav|page|{page - 1}"
                )
            )
        nav.append(
            InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop")
        )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    "التالي ▶️", callback_data=f"ufav|page|{page + 1}"
                )
            )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def latest_keyboard(
    items: list[dict],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for lec in items:
        lid = lec["id"]
        sid = lec["subject_id"]
        sub = lec.get("subject_name", "")
        rows.append(
            [
                InlineKeyboardButton(
                    f"📄 {lec['title'][:35]} — {sub[:25]}",
                    callback_data=f"ulec|open|{lid}|{sid}",
                )
            ]
        )
    nav = []
    if total_pages > 1:
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    "◀️ السابق", callback_data=f"ulate|page|{page - 1}"
                )
            )
        nav.append(
            InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop")
        )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    "التالي ▶️", callback_data=f"ulate|page|{page + 1}"
                )
            )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def links_keyboard(links: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for ln in links:
        rows.append(
            [
                InlineKeyboardButton(
                    ln["title"][:60],
                    url=ln["url"],
                )
            ]
        )
    rows.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="home")])
    return InlineKeyboardMarkup(rows)
