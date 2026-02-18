import os
import sqlite3
import time
from datetime import datetime, time as dtime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# =======================
TOKEN = os.getenv("TOKEN")

MAIN_ADMIN_ID = 8377544927
ADMIN_USERNAME = "El8awy116"

DB_FILE = "lectures.db"

# Maintenance Mode Global
BOT_ENABLED = True

MAINTENANCE_MESSAGE = (
    "💡 عزيزي الطالب ❤️\n"
    "البوت تحت التحديث حاليًا علشان نقدملك حاجة تليق بيك\n"
    "ارجع قريبًا إن شاء الله ✨"
)

# Rate Limit
user_messages = {}
blocked_users = {}

# =======================
# LOG SYSTEM
def log_admin_action(user_id, action):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("admin_log.txt", "a", encoding="utf-8") as f:
        f.write(f"[{now}] Admin({user_id}) -> {action}\n")


# =======================
# DATABASE INIT
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS subjects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS lectures (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_id INTEGER,
        title TEXT,
        file_id TEXT,
        FOREIGN KEY(subject_id) REFERENCES subjects(id)
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER UNIQUE
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER UNIQUE
    )
    """)

    # Important Links Table
    c.execute("""
    CREATE TABLE IF NOT EXISTS important_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        url TEXT,
        position INTEGER DEFAULT 999
    )
    """)

    conn.commit()
    conn.close()

    # Ensure admin log file exists
    if not os.path.exists("admin_log.txt"):
        with open("admin_log.txt", "a", encoding="utf-8"):
            pass


def db():
    return sqlite3.connect(DB_FILE)


# =======================
# ADMINS
def get_all_admins():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT user_id FROM admins")
    admins = [MAIN_ADMIN_ID] + [x[0] for x in c.fetchall()]
    conn.close()
    return list(set(admins))


def is_admin(uid):
    return uid in get_all_admins()


# =======================
# USER REGISTER
async def register_user(update: Update):
    uid = update.effective_user.id
    conn = db()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (uid,))
    conn.commit()
    conn.close()


# =======================
# RATE LIMIT
async def check_rate_limit(update: Update):
    uid = update.effective_user.id

    # Admins are exempt from rate limiting
    if is_admin(uid):
        return True

    now = time.time()

    if uid in blocked_users:
        if now < blocked_users[uid]:
            await update.message.reply_text("🚫 استنى 10 ثواني قبل ما تبعت تاني.")
            return False
        else:
            del blocked_users[uid]

    msgs = user_messages.get(uid, [])
    msgs = [t for t in msgs if now - t < 10]
    msgs.append(now)
    user_messages[uid] = msgs

    if len(msgs) >= 5:
        blocked_users[uid] = now + 10
        await update.message.reply_text("🚫 استنى 10 ثواني قبل ما تبعت تاني.")
        return False

    return True


# =======================
# MENUS
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 الدخول للمواد", callback_data="go_subjects")],
        [InlineKeyboardButton("🔗 لينكات مهمة", callback_data="links")],
        [InlineKeyboardButton("🛠 الإبلاغ عن مشكلة / نقص ملفات", callback_data="reports")],
        [InlineKeyboardButton("👤 التواصل مع الأدمن", url=f"https://t.me/{ADMIN_USERNAME}")]
    ])


def home_button():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 رجوع للقائمة الرئيسية", callback_data="home")]
    ])


# =======================
# START
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BOT_ENABLED

    await register_user(update)

    uid = update.effective_user.id
    if not BOT_ENABLED and not is_admin(uid):
        await update.message.reply_text(MAINTENANCE_MESSAGE)
        return

    await update.message.reply_text(
        "✨ أهلاً بيك عزيزي الطالب\n"
        "أتمنالك تجربة ممتعة وموفقة بإذن الله ❤️📚",
        reply_markup=main_menu()
    )


# =======================
# SHOW SUBJECTS
async def show_subjects(message):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT id,name FROM subjects")
    subjects = c.fetchall()
    conn.close()

    if not subjects:
        await message.reply_text("📌 لا توجد مواد بعد.", reply_markup=home_button())
        return

    keyboard = []
    for sid, name in subjects:
        keyboard.append([InlineKeyboardButton(f"📚 {name}", callback_data=f"sub_{sid}")])

    keyboard.append([InlineKeyboardButton("🏠 رجوع", callback_data="home")])

    await message.reply_text("📚 اختر المادة:", reply_markup=InlineKeyboardMarkup(keyboard))


# =======================
# SHOW LECTURES
async def show_lectures(query, subject_id):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT id,title FROM lectures WHERE subject_id=?", (subject_id,))
    lectures = c.fetchall()
    conn.close()

    if not lectures:
        await query.message.reply_text("📌 لا توجد محاضرات بعد.", reply_markup=home_button())
        return

    keyboard = []
    for lid, title in lectures:
        keyboard.append([InlineKeyboardButton(f"📄 {title}", callback_data=f"lec_{lid}")])

    keyboard.append([InlineKeyboardButton("🔙 رجوع للمواد", callback_data="go_subjects")])
    keyboard.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="home")])

    await query.message.reply_text("📘 المحاضرات:", reply_markup=InlineKeyboardMarkup(keyboard))


# =======================
# IMPORTANT LINKS (Student)
async def show_links(query):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT id,title FROM important_links ORDER BY position ASC")
    links = c.fetchall()
    conn.close()

    if not links:
        await query.message.reply_text("📌 لا توجد لينكات بعد.", reply_markup=home_button())
        return

    keyboard = []
    for lid, title in links:
        keyboard.append([InlineKeyboardButton(f"🔗 {title}", callback_data=f"openLink_{lid}")])

    keyboard.append([InlineKeyboardButton("🏠 رجوع", callback_data="home")])

    await query.message.reply_text("📌 اختر لينك:", reply_markup=InlineKeyboardMarkup(keyboard))


# =======================
# IMPORTANT LINKS ORDER (Admin)
async def show_links_order_menu(message):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT id,title,position FROM important_links ORDER BY position ASC, id ASC")
    links = c.fetchall()
    conn.close()

    if not links:
        await message.reply_text("📌 لا توجد لينكات بعد.", reply_markup=home_button())
        return

    keyboard = []
    total = len(links)
    for index, (lid, title, pos) in enumerate(links):
        row_buttons = []
        if index > 0:
            row_buttons.append(
                InlineKeyboardButton(f"⬆️ {title}", callback_data=f"linkUp_{lid}")
            )
        if index < total - 1:
            row_buttons.append(
                InlineKeyboardButton(f"⬇️ {title}", callback_data=f"linkDown_{lid}")
            )
        if row_buttons:
            keyboard.append(row_buttons)

    keyboard.append([InlineKeyboardButton("✅ تم", callback_data="admin_cancel")])

    await message.reply_text(
        "🔃 استخدم الأسهم لترتيب اللينكات (الأعلى = الأهم).",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# =======================
# REPORTS MENU
async def reports_menu(query):
    keyboard = [
        [InlineKeyboardButton("🚨 الإبلاغ عن مشكلة", callback_data="report_problem")],
        [InlineKeyboardButton("📌 نقص في الملفات", callback_data="missing_files")],
        [InlineKeyboardButton("🏠 رجوع", callback_data="home")]
    ]
    await query.message.reply_text("🛠 اختر الخدمة:", reply_markup=InlineKeyboardMarkup(keyboard))


# =======================
# CALLBACK ROUTER (dispatch student/admin buttons)
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = (query.data or "").strip()

    # Admin-specific callbacks (by exact value)
    admin_exact = {
        "add_subject",
        "add_lecture",
        "delete_subject",
        "delete_lecture",
        "edit_subject",
        "edit_lecture",
        "manage_links",
        "add_link",
        "delete_link",
        "edit_link",
        "order_links",
        "backup",
        "bot_off",
        "bot_on",
        "broadcast",
        "admins",
        "add_admin_btn",
        "remove_admin_btn",
        "list_admins",
        "stats",
        "admin_cancel",
        "admin_panel",
    }

    # Admin-specific callbacks (by prefix)
    admin_prefixes = (
        "chooseSub_",
        "confirmDelSub_",
        "doDelSub_",
        "delLecSub_",
        "confirmDelLec_",
        "doDelLec_",
        "editSub_",
        "editLec_",
        "confirmDelLink_",
        "doDelLink_",
        "editLink_",
        "linkUp_",
        "linkDown_",
    )

    if data in admin_exact or any(data.startswith(p) for p in admin_prefixes):
        await admin_buttons(update, context)
    else:
        await button_handler(update, context)


# =======================
# BUTTON HANDLER (Student Part)
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    # Maintenance check
    if not BOT_ENABLED and not is_admin(uid):
        await query.message.reply_text(MAINTENANCE_MESSAGE)
        return

    # HOME
    if query.data == "home":
        await query.message.reply_text("اختر الخدمة المطلوبة:", reply_markup=main_menu())
        return

    # SUBJECTS
    if query.data == "go_subjects":
        await show_subjects(query.message)
        return

    # LINKS
    if query.data == "links":
        await show_links(query)
        return

    if query.data.startswith("openLink_"):
        link_id = int(query.data.replace("openLink_", ""))
        conn = db()
        c = conn.cursor()
        c.execute("SELECT title,url FROM important_links WHERE id=?", (link_id,))
        row = c.fetchone()
        conn.close()

        if row:
            title, url = row
            await query.message.reply_text(f"🔗 {title}\n\n{url}", reply_markup=home_button())
        return

    # REPORTS
    if query.data == "reports":
        await reports_menu(query)
        return

    # REPORT PROBLEM
    if query.data == "report_problem":
        context.user_data["reporting"] = True
        await query.message.reply_text("✍️ اكتب المشكلة وسيتم إرسالها للإدارة:", reply_markup=home_button())
        return

    # MISSING FILES
    if query.data == "missing_files":
        context.user_data["missing_step"] = "subject"
        await query.message.reply_text("📚 اكتب اسم المادة:", reply_markup=home_button())
        return

    # OPEN SUBJECT
    if query.data.startswith("sub_"):
        sid = int(query.data.replace("sub_", ""))
        context.user_data["last_subject"] = sid
        await show_lectures(query, sid)
        return

    # OPEN LECTURE
    if query.data.startswith("lec_"):
        lec_id = int(query.data.replace("lec_", ""))

        conn = db()
        c = conn.cursor()
        c.execute("SELECT file_id,title FROM lectures WHERE id=?", (lec_id,))
        row = c.fetchone()
        conn.close()

        if not row:
            return

        file_id, title = row
        await query.message.reply_document(file_id)

        last_subject = context.user_data.get("last_subject")
        keyboard = [
            [InlineKeyboardButton("🔙 رجوع للمحاضرات", callback_data=f"sub_{last_subject}")],
            [InlineKeyboardButton("🔙 رجوع للمواد", callback_data="go_subjects")],
            [InlineKeyboardButton("🏠 الرئيسية", callback_data="home")]
        ]
        await query.message.reply_text("اختر التالي 👇", reply_markup=InlineKeyboardMarkup(keyboard))
        return

   # =======================
# ADMIN PANEL
async def send_admin_panel(message, uid: int):
    if not is_admin(uid):
        return

    keyboard = [
        [InlineKeyboardButton("➕ إضافة مادة", callback_data="add_subject")],
        [InlineKeyboardButton("➕ إضافة محاضرة", callback_data="add_lecture")],

        [InlineKeyboardButton("🗑 حذف مادة", callback_data="delete_subject")],
        [InlineKeyboardButton("🗑 حذف محاضرة", callback_data="delete_lecture")],

        [InlineKeyboardButton("✏️ تعديل اسم مادة", callback_data="edit_subject")],
        [InlineKeyboardButton("✏️ تعديل عنوان محاضرة", callback_data="edit_lecture")],

        [InlineKeyboardButton("🔗 إدارة اللينكات المهمة", callback_data="manage_links")],

        [InlineKeyboardButton("📢 رسالة جماعية", callback_data="broadcast")],

        [InlineKeyboardButton("📊 إحصائيات", callback_data="stats")],

        [InlineKeyboardButton("📦 Backup Database", callback_data="backup")],
        [InlineKeyboardButton("⏸ إيقاف البوت", callback_data="bot_off")],
        [InlineKeyboardButton("▶️ تشغيل البوت", callback_data="bot_on")],

        [InlineKeyboardButton("👑 إدارة المشرفين", callback_data="admins")],

        [InlineKeyboardButton("🏠 رجوع للأدمن", callback_data="admin_panel")],
    ]

    await message.reply_text(
        "👑 لوحة تحكم الأدمن:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_admin_panel(update.message, update.effective_user.id)


# =======================
# ADMIN BUTTONS (Continue inside button_handler)

async def admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BOT_ENABLED

    query = update.callback_query
    uid = query.from_user.id

    if not is_admin(uid):
        return

    # =======================
    # RETURN TO ADMIN PANEL
    if query.data == "admin_panel":
        await send_admin_panel(query.message, uid)
        return

    # =======================
    # ADD SUBJECT
    if query.data == "add_subject":
        context.user_data["waiting_subject"] = True
        await query.message.reply_text("✍️ اكتب اسم المادة الجديدة:")
        return

    # =======================
    # ADD LECTURE
    if query.data == "add_lecture":
        conn = db()
        c = conn.cursor()
        c.execute("SELECT id,name FROM subjects")
        subs = c.fetchall()
        conn.close()

        keyboard = []
        for sid, name in subs:
            keyboard.append([InlineKeyboardButton(name, callback_data=f"chooseSub_{sid}")])

        await query.message.reply_text("📚 اختر المادة:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if query.data.startswith("chooseSub_"):
        sid = int(query.data.replace("chooseSub_", ""))
        context.user_data["add_lec_subject"] = sid
        await query.message.reply_text("✍️ اكتب عنوان المحاضرة:")
        return

    # =======================
    # DELETE SUBJECT (Confirm)
    if query.data == "delete_subject":
        conn = db()
        c = conn.cursor()
        c.execute("SELECT id,name FROM subjects")
        subs = c.fetchall()
        conn.close()

        keyboard = []
        for sid, name in subs:
            keyboard.append([InlineKeyboardButton(name, callback_data=f"confirmDelSub_{sid}")])

        await query.message.reply_text("اختر المادة للحذف:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if query.data.startswith("confirmDelSub_"):
        sid = int(query.data.replace("confirmDelSub_", ""))

        keyboard = [
            [InlineKeyboardButton("✅ تأكيد الحذف", callback_data=f"doDelSub_{sid}")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="admin_cancel")]
        ]
        await query.message.reply_text("⚠️ هل أنت متأكد؟", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if query.data.startswith("doDelSub_"):
        sid = int(query.data.replace("doDelSub_", ""))

        conn = db()
        c = conn.cursor()
        c.execute("DELETE FROM lectures WHERE subject_id=?", (sid,))
        c.execute("DELETE FROM subjects WHERE id=?", (sid,))
        conn.commit()
        conn.close()

        log_admin_action(uid, f"🗑 Deleted Subject ID {sid}")
        await query.message.reply_text("✅ تم حذف المادة ومحاضراتها.")
        return

    # =======================
    # DELETE LECTURE (Subject → Lecture → Confirm)
    if query.data == "delete_lecture":
        conn = db()
        c = conn.cursor()
        c.execute("SELECT id,name FROM subjects")
        subs = c.fetchall()
        conn.close()

        keyboard = []
        for sid, name in subs:
            keyboard.append([InlineKeyboardButton(name, callback_data=f"delLecSub_{sid}")])

        await query.message.reply_text("اختر المادة:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if query.data.startswith("delLecSub_"):
        sid = int(query.data.replace("delLecSub_", ""))

        conn = db()
        c = conn.cursor()
        c.execute("SELECT id,title FROM lectures WHERE subject_id=?", (sid,))
        lecs = c.fetchall()
        conn.close()

        keyboard = []
        for lid, title in lecs:
            keyboard.append([InlineKeyboardButton(title, callback_data=f"confirmDelLec_{lid}")])

        await query.message.reply_text("اختر المحاضرة للحذف:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if query.data.startswith("confirmDelLec_"):
        lid = int(query.data.replace("confirmDelLec_", ""))

        keyboard = [
            [InlineKeyboardButton("✅ تأكيد الحذف", callback_data=f"doDelLec_{lid}")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="admin_cancel")]
        ]
        await query.message.reply_text("⚠️ هل أنت متأكد؟", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if query.data.startswith("doDelLec_"):
        lid = int(query.data.replace("doDelLec_", ""))

        conn = db()
        c = conn.cursor()
        c.execute("DELETE FROM lectures WHERE id=?", (lid,))
        conn.commit()
        conn.close()

        log_admin_action(uid, f"🗑 Deleted Lecture ID {lid}")
        await query.message.reply_text("✅ تم حذف المحاضرة.")
        return

    # =======================
    # EDIT SUBJECT
    if query.data == "edit_subject":
        conn = db()
        c = conn.cursor()
        c.execute("SELECT id,name FROM subjects")
        subs = c.fetchall()
        conn.close()

        keyboard = []
        for sid, name in subs:
            keyboard.append([InlineKeyboardButton(name, callback_data=f"editSub_{sid}")])

        await query.message.reply_text("اختر المادة للتعديل:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if query.data.startswith("editSub_"):
        sid = int(query.data.replace("editSub_", ""))
        context.user_data["edit_subject_id"] = sid
        await query.message.reply_text("✍️ اكتب الاسم الجديد:")
        return

    # =======================
    # EDIT LECTURE
    if query.data == "edit_lecture":
        conn = db()
        c = conn.cursor()
        c.execute("SELECT id,title FROM lectures")
        lecs = c.fetchall()
        conn.close()

        keyboard = []
        for lid, title in lecs:
            keyboard.append([InlineKeyboardButton(title, callback_data=f"editLec_{lid}")])

        await query.message.reply_text("اختر المحاضرة للتعديل:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if query.data.startswith("editLec_"):
        lid = int(query.data.replace("editLec_", ""))
        context.user_data["edit_lecture_id"] = lid
        await query.message.reply_text("✍️ اكتب العنوان الجديد:")
        return

    # =======================
    # MANAGE LINKS
    if query.data == "manage_links":
        keyboard = [
            [InlineKeyboardButton("➕ إضافة لينك", callback_data="add_link")],
            [InlineKeyboardButton("🗑 حذف لينك", callback_data="delete_link")],
            [InlineKeyboardButton("✏️ تعديل لينك", callback_data="edit_link")],
            [InlineKeyboardButton("🔃 ترتيب اللينكات", callback_data="order_links")],
            [InlineKeyboardButton("🏠 رجوع", callback_data="home")]
        ]
        await query.message.reply_text("🔗 إدارة اللينكات:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if query.data == "add_link":
        context.user_data["add_link_step"] = "title"
        await query.message.reply_text("✍️ اكتب اسم اللينك:")
        return

    # DELETE LINK
    if query.data == "delete_link":
        conn = db()
        c = conn.cursor()
        c.execute("SELECT id,title FROM important_links ORDER BY position ASC, id ASC")
        links = c.fetchall()
        conn.close()

        if not links:
            await query.message.reply_text("📌 لا توجد لينكات لحذفها.")
            return

        keyboard = []
        for lid, title in links:
            keyboard.append(
                [InlineKeyboardButton(title, callback_data=f"confirmDelLink_{lid}")]
            )

        await query.message.reply_text(
            "اختر اللينك للحذف:", reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if query.data.startswith("confirmDelLink_"):
        lid = int(query.data.replace("confirmDelLink_", ""))

        keyboard = [
            [InlineKeyboardButton("✅ تأكيد الحذف", callback_data=f"doDelLink_{lid}")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="admin_cancel")],
        ]
        await query.message.reply_text(
            "⚠️ هل أنت متأكد من حذف هذا اللينك؟",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if query.data.startswith("doDelLink_"):
        lid = int(query.data.replace("doDelLink_", ""))

        conn = db()
        c = conn.cursor()
        c.execute("DELETE FROM important_links WHERE id=?", (lid,))
        conn.commit()
        conn.close()

        log_admin_action(uid, f"🗑 Deleted Link ID {lid}")
        await query.message.reply_text("✅ تم حذف اللينك.")
        return

    # EDIT LINK
    if query.data == "edit_link":
        conn = db()
        c = conn.cursor()
        c.execute("SELECT id,title FROM important_links ORDER BY position ASC, id ASC")
        links = c.fetchall()
        conn.close()

        if not links:
            await query.message.reply_text("📌 لا توجد لينكات لتعديلها.")
            return

        keyboard = []
        for lid, title in links:
            keyboard.append(
                [InlineKeyboardButton(title, callback_data=f"editLink_{lid}")]
            )

        await query.message.reply_text(
            "اختر اللينك للتعديل:", reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if query.data.startswith("editLink_"):
        lid = int(query.data.replace("editLink_", ""))
        context.user_data["edit_link_id"] = lid
        context.user_data["edit_link_step"] = "title"
        await query.message.reply_text("✏️ اكتب الاسم الجديد للينك:")
        return

    # ORDER LINKS
    if query.data == "order_links":
        await show_links_order_menu(query.message)
        return

    if query.data.startswith("linkUp_"):
        lid = int(query.data.replace("linkUp_", ""))

        conn = db()
        c = conn.cursor()
        c.execute(
            "SELECT id,position FROM important_links ORDER BY position ASC, id ASC"
        )
        links = c.fetchall()

        for index, (current_id, current_pos) in enumerate(links):
            if current_id == lid and index > 0:
                prev_id, prev_pos = links[index - 1]
                c.execute(
                    "UPDATE important_links SET position=? WHERE id=?",
                    (prev_pos, current_id),
                )
                c.execute(
                    "UPDATE important_links SET position=? WHERE id=?",
                    (current_pos, prev_id),
                )
                conn.commit()
                log_admin_action(uid, f"🔃 Moved Link ID {lid} Up")
                break

        conn.close()
        await show_links_order_menu(query.message)
        return

    if query.data.startswith("linkDown_"):
        lid = int(query.data.replace("linkDown_", ""))

        conn = db()
        c = conn.cursor()
        c.execute(
            "SELECT id,position FROM important_links ORDER BY position ASC, id ASC"
        )
        links = c.fetchall()

        for index, (current_id, current_pos) in enumerate(links):
            if current_id == lid and index < len(links) - 1:
                next_id, next_pos = links[index + 1]
                c.execute(
                    "UPDATE important_links SET position=? WHERE id=?",
                    (next_pos, current_id),
                )
                c.execute(
                    "UPDATE important_links SET position=? WHERE id=?",
                    (current_pos, next_id),
                )
                conn.commit()
                log_admin_action(uid, f"🔃 Moved Link ID {lid} Down")
                break

        conn.close()
        await show_links_order_menu(query.message)
        return

    # =======================
    # BACKUP
    if query.data == "backup":
        await query.message.reply_document(document=DB_FILE)
        await query.message.reply_document(document="admin_log.txt")
        log_admin_action(uid, "📦 Manual Backup Sent")
        await query.message.reply_text("✅ Backup تم إرساله.")
        return

    # =======================
    # BOT ON/OFF
    if query.data == "bot_off":
        BOT_ENABLED = False
        log_admin_action(uid, "⏸ Bot Disabled For Students")
        await query.message.reply_text("⏸ تم إيقاف البوت للطلاب.")
        return

    if query.data == "bot_on":
        BOT_ENABLED = True
        log_admin_action(uid, "▶️ Bot Enabled For Students")
        await query.message.reply_text("▶️ تم تشغيل البوت للطلاب.")
        return

    # =======================
    # BROADCAST
    if query.data == "broadcast":
        context.user_data["broadcast"] = True
        await query.message.reply_text("✍️ اكتب الرسالة لإرسالها لكل الطلاب:")
        return

    # =======================
    # ADMINS MANAGEMENT
    if query.data == "admins":
        keyboard = [
            [InlineKeyboardButton("➕ إضافة أدمن", callback_data="add_admin_btn")],
            [InlineKeyboardButton("➖ حذف أدمن", callback_data="remove_admin_btn")],
            [InlineKeyboardButton("📋 عرض الأدمن الحاليين", callback_data="list_admins")],
            [InlineKeyboardButton("🏠 رجوع", callback_data="home")],
        ]
        await query.message.reply_text(
            "👑 إدارة المشرفين:", reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if query.data == "add_admin_btn":
        if uid != MAIN_ADMIN_ID:
            await query.message.reply_text("❌ هذا الخيار متاح للمشرف الرئيسي فقط.")
            return
        context.user_data["adding_admin"] = True
        await query.message.reply_text("✍️ اكتب ID الأدمن الجديد (رقم):")
        return

    if query.data == "remove_admin_btn":
        if uid != MAIN_ADMIN_ID:
            await query.message.reply_text("❌ هذا الخيار متاح للمشرف الرئيسي فقط.")
            return
        context.user_data["removing_admin"] = True
        await query.message.reply_text("✍️ اكتب ID الأدمن المراد حذفه (رقم):")
        return

    if query.data == "list_admins":
        admins = get_all_admins()
        lines = []
        for admin_id in admins:
            if admin_id == MAIN_ADMIN_ID:
                lines.append(f"- {admin_id} (Main Admin)")
            else:
                lines.append(f"- {admin_id}")
        text = "📋 قائمة الأدمن:\n" + ("\n".join(lines) if lines else "لا يوجد أدمن بعد.")
        await query.message.reply_text(text)
        return

    # STATS
    if query.data == "stats":
        conn = db()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        users_count = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM subjects")
        subjects_count = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM lectures")
        lectures_count = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM important_links")
        links_count = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM admins")
        admins_count = c.fetchone()[0]
        conn.close()

        stats_text = (
            "📊 إحصائيات البوت:\n\n"
            f"👥 عدد الطلاب المسجلين: {users_count}\n"
            f"📚 عدد المواد: {subjects_count}\n"
            f"📄 عدد المحاضرات: {lectures_count}\n"
            f"🔗 عدد اللينكات المهمة: {links_count}\n"
            f"👑 عدد الأدمن (بدون المشرف الرئيسي): {admins_count}"
        )

        log_admin_action(uid, "📊 Viewed Stats")
        await query.message.reply_text(stats_text)
        return

    # =======================
    if query.data == "admin_cancel":
        context.user_data.clear()
        await query.message.reply_text("❌ تم الإلغاء.")
        return


# =======================
# HANDLE TEXT
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text

    # Maintenance check for students
    if not BOT_ENABLED and not is_admin(uid):
        await update.message.reply_text(MAINTENANCE_MESSAGE)
        return

    if not await check_rate_limit(update):
        return

    # REPORTING
    if context.user_data.get("reporting"):
        for admin in get_all_admins():
            await context.bot.send_message(admin, f"🚨 مشكلة من طالب:\n\n{text}")
        context.user_data.clear()
        return

    # MISSING FILES
    if context.user_data.get("missing_step") == "subject":
        context.user_data["missing_subject"] = text
        context.user_data["missing_step"] = "lecture"
        await update.message.reply_text("📄 اكتب اسم المحاضرة الناقصة:")
        return

    if context.user_data.get("missing_step") == "lecture":
        context.user_data["missing_lecture"] = text
        context.user_data["missing_step"] = "upload"
        await update.message.reply_text("📤 لو معاك الملف ارفعه الآن (اختياري) أو اكتب (تخطي)")
        return

    if context.user_data.get("missing_step") == "upload":
        subject = context.user_data["missing_subject"]
        lecture = context.user_data["missing_lecture"]

        for admin in get_all_admins():
            await context.bot.send_message(
                admin,
                f"📌 نقص ملفات:\n\n📚 المادة: {subject}\n📄 المحاضرة: {lecture}"
            )

        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال الطلب للإدارة.")
        return

    # ADMIN ONLY
    if not is_admin(uid):
        return

    # EDIT SUBJECT NAME
    if context.user_data.get("edit_subject_id"):
        sid = context.user_data["edit_subject_id"]

        conn = db()
        c = conn.cursor()
        c.execute("UPDATE subjects SET name=? WHERE id=?", (text, sid))
        conn.commit()
        conn.close()

        log_admin_action(uid, f"✏️ Edited Subject {sid} -> {text}")
        context.user_data.clear()
        await update.message.reply_text("✅ تم تعديل اسم المادة.")
        return

    # EDIT LECTURE TITLE
    if context.user_data.get("edit_lecture_id"):
        lid = context.user_data["edit_lecture_id"]

        conn = db()
        c = conn.cursor()
        c.execute("UPDATE lectures SET title=? WHERE id=?", (text, lid))
        conn.commit()
        conn.close()

        log_admin_action(uid, f"✏️ Edited Lecture {lid} -> {text}")
        context.user_data.clear()
        await update.message.reply_text("✅ تم تعديل عنوان المحاضرة.")
        return

    # ADD SUBJECT
    if context.user_data.get("waiting_subject"):
        conn = db()
        c = conn.cursor()
        c.execute("INSERT INTO subjects(name) VALUES(?)", (text,))
        conn.commit()
        conn.close()

        log_admin_action(uid, f"➕ Added Subject {text}")
        context.user_data.clear()
        await update.message.reply_text("✅ تم إضافة المادة.")
        return

    # ADD LECTURE TITLE
    if context.user_data.get("add_lec_subject"):
        context.user_data["lecture_title"] = text
        await update.message.reply_text("📤 الآن أرسل ملف PDF للمحاضرة")
        return

    # ADD LINK TITLE
    if context.user_data.get("add_link_step") == "title":
        context.user_data["link_title"] = text
        context.user_data["add_link_step"] = "url"
        await update.message.reply_text("🔗 ابعت الرابط الآن:")
        return

    if context.user_data.get("add_link_step") == "url":
        title = context.user_data["link_title"]
        url = text

        conn = db()
        c = conn.cursor()
        c.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM important_links")
        next_pos = c.fetchone()[0]
        c.execute(
            "INSERT INTO important_links(title,url,position) VALUES(?,?,?)",
            (title, url, next_pos),
        )
        conn.commit()
        conn.close()

        log_admin_action(uid, f"🔗 Added Link {title}")
        context.user_data.clear()
        await update.message.reply_text("✅ تم إضافة اللينك.")
        return

    # EDIT LINK (TITLE THEN URL)
    if context.user_data.get("edit_link_step") == "title":
        link_id = context.user_data.get("edit_link_id")
        new_title = text

        conn = db()
        c = conn.cursor()
        c.execute("UPDATE important_links SET title=? WHERE id=?", (new_title, link_id))
        conn.commit()
        conn.close()

        context.user_data["edit_link_step"] = "url"
        await update.message.reply_text("🔗 اكتب الرابط الجديد للينك:")
        return

    if context.user_data.get("edit_link_step") == "url":
        link_id = context.user_data.get("edit_link_id")
        new_url = text

        conn = db()
        c = conn.cursor()
        c.execute("UPDATE important_links SET url=? WHERE id=?", (new_url, link_id))
        conn.commit()
        conn.close()

        log_admin_action(uid, f"✏️ Edited Link ID {link_id}")
        context.user_data.clear()
        await update.message.reply_text("✅ تم تعديل اللينك.")
        return

    # BROADCAST
    if context.user_data.get("broadcast"):
        conn = db()
        c = conn.cursor()
        c.execute("SELECT user_id FROM users")
        users = c.fetchall()
        conn.close()

        for (u,) in users:
            try:
                await context.bot.send_message(u, f"📢 إعلان:\n\n{text}")
            except:
                pass

        log_admin_action(uid, "📢 Sent Broadcast")
        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال الرسالة.")
        return

    # ADD ADMIN (from admin panel)
    if context.user_data.get("adding_admin"):
        if uid != MAIN_ADMIN_ID:
            await update.message.reply_text("❌ هذا الخيار متاح للمشرف الرئيسي فقط.")
            context.user_data.clear()
            return
        try:
            new_admin_id = int(text)
        except ValueError:
            await update.message.reply_text("❌ من فضلك اكتب ID رقمي صحيح.")
            return

        conn = db()
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO admins(user_id) VALUES(?)", (new_admin_id,))
        conn.commit()
        conn.close()

        log_admin_action(uid, f"👑 Added Admin {new_admin_id}")
        context.user_data.clear()
        await update.message.reply_text("✅ تم إضافة الأدمن.")
        return

    # REMOVE ADMIN (from admin panel)
    if context.user_data.get("removing_admin"):
        if uid != MAIN_ADMIN_ID:
            await update.message.reply_text("❌ هذا الخيار متاح للمشرف الرئيسي فقط.")
            context.user_data.clear()
            return
        try:
            rem_admin_id = int(text)
        except ValueError:
            await update.message.reply_text("❌ من فضلك اكتب ID رقمي صحيح.")
            return

        if rem_admin_id == MAIN_ADMIN_ID:
            await update.message.reply_text("❌ لا يمكن حذف المشرف الرئيسي.")
            context.user_data.clear()
            return

        conn = db()
        c = conn.cursor()
        c.execute("DELETE FROM admins WHERE user_id=?", (rem_admin_id,))
        conn.commit()
        conn.close()

        log_admin_action(uid, f"👑 Removed Admin {rem_admin_id}")
        context.user_data.clear()
        await update.message.reply_text("✅ تم حذف الأدمن.")
        return


# =======================
# HANDLE PDF UPLOAD
async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    pdf = update.message.document

    # Maintenance check for students
    if not BOT_ENABLED and not is_admin(uid):
        await update.message.reply_text(MAINTENANCE_MESSAGE)
        return

    # Student sending PDF for missing files (optional attachment)
    if context.user_data.get("missing_step") == "upload":
        subject = context.user_data.get("missing_subject", "")
        lecture = context.user_data.get("missing_lecture", "")
        file_id = pdf.file_id

        for admin in get_all_admins():
            await context.bot.send_document(
                admin,
                document=file_id,
                caption=(
                    "📌 نقص ملفات مع مرفق:\n\n"
                    f"📚 المادة: {subject}\n"
                    f"📄 المحاضرة: {lecture}"
                ),
            )

        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال الطلب والمرفق للإدارة.")
        return

    # Admin uploading lecture PDF
    if not is_admin(uid):
        return

    if "lecture_title" not in context.user_data:
        return

    sid = context.user_data["add_lec_subject"]
    title = context.user_data["lecture_title"]
    file_id = pdf.file_id

    conn = db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO lectures(subject_id,title,file_id) VALUES(?,?,?)",
        (sid, title, file_id),
    )
    conn.commit()
    conn.close()

    log_admin_action(uid, f"📤 Uploaded Lecture {title}")
    context.user_data.clear()
    await update.message.reply_text("✅ تم إضافة المحاضرة بنجاح!")


# =======================
# AUTO BACKUP JOB
async def auto_backup(context: ContextTypes.DEFAULT_TYPE):
    for admin_id in get_all_admins():
        try:
            await context.bot.send_document(admin_id, document=DB_FILE)
            await context.bot.send_document(admin_id, document="admin_log.txt")
        except Exception:
            pass


# =======================
# ADMIN COMMANDS
async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != MAIN_ADMIN_ID:
        return

    admin_id = int(context.args[0])
    conn = db()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO admins(user_id) VALUES(?)", (admin_id,))
    conn.commit()
    conn.close()

    log_admin_action(update.effective_user.id, f"👑 Added Admin {admin_id} (Command)")
    await update.message.reply_text("✅ تم إضافة أدمن جديد.")


async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != MAIN_ADMIN_ID:
        return

    admin_id = int(context.args[0])

    if admin_id == MAIN_ADMIN_ID:
        await update.message.reply_text("❌ لا يمكن حذف المشرف الرئيسي.")
        return

    conn = db()
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE user_id=?", (admin_id,))
    conn.commit()
    conn.close()

    log_admin_action(update.effective_user.id, f"👑 Removed Admin {admin_id} (Command)")
    await update.message.reply_text("✅ تم حذف الأدمن.")


# =======================
# RUN BOT
def main():
    init_db()

    app = Application.builder().token(TOKEN).build()

    # Daily automatic backup at 00:00 (only if JobQueue is available)
    if app.job_queue is not None:
        app.job_queue.run_daily(auto_backup, time=dtime(hour=0, minute=0, second=0))
    else:
        print("⚠️ Auto backup disabled because JobQueue is not available. "
              "Install python-telegram-bot[job-queue] to enable it.")

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))

    app.add_handler(CommandHandler("addadmin", add_admin))
    app.add_handler(CommandHandler("removeadmin", remove_admin))

    # Single router for all callback queries to avoid handler conflicts
    app.add_handler(CallbackQueryHandler(callback_router))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))

    print("✅ البوت شغال...")
    app.run_polling()


if __name__ == "__main__":
    main()

