import os
import sqlite3
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes
)

# =======================
TOKEN = os.getenv("TOKEN")
MAIN_ADMIN_ID = 8377544927
DB_FILE = "lectures.db"
# =======================


# =======================
# LOG SYSTEM
def log_admin_action(user_id, action):
    الوقت = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("admin_log.txt", "a", encoding="utf-8") as f:
        f.write(f"[{الوقت}] Admin({user_id}) -> {action}\n")


# =======================
# DATABASE
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

    conn.commit()
    conn.close()


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


def is_admin(user_id):
    return user_id in get_all_admins()


# =======================
# MENUS
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 الدخول للمواد", callback_data="go_subjects")],
        [InlineKeyboardButton("🛠 الإبلاغ عن مشكلة", callback_data="report_problem")],
        [InlineKeyboardButton("📌 نقص في الملفات", callback_data="missing_files")]
    ])


def home_button():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 رجوع للقائمة الرئيسية", callback_data="home")]
    ])


# =======================
# REGISTER USER
async def register_user(update: Update):
    uid = update.effective_user.id
    conn = db()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (uid,))
    conn.commit()
    conn.close()


# =======================
# START
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    await update.message.reply_text(
        "مرحبًا بك 👋\nاختر الخدمة المطلوبة:",
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

    keyboard.append([InlineKeyboardButton("🏠 رجوع للقائمة الرئيسية", callback_data="home")])

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

    keyboard.append([InlineKeyboardButton("🏠 رجوع للقائمة الرئيسية", callback_data="home")])

    await query.message.reply_text("📘 المحاضرات:", reply_markup=InlineKeyboardMarkup(keyboard))


# =======================
# ADMIN PANEL
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    keyboard = [
        [InlineKeyboardButton("➕ إضافة مادة", callback_data="add_subject")],
        [InlineKeyboardButton("➕ إضافة محاضرة", callback_data="add_lecture")],

        [InlineKeyboardButton("🗑 حذف مادة", callback_data="delete_subject")],
        [InlineKeyboardButton("🗑 حذف محاضرة", callback_data="delete_lecture")],

        [InlineKeyboardButton("✏️ تعديل اسم مادة", callback_data="edit_subject")],
        [InlineKeyboardButton("✏️ تعديل عنوان محاضرة", callback_data="edit_lecture")],

        [InlineKeyboardButton("📢 رسالة جماعية", callback_data="broadcast")],
        [InlineKeyboardButton("👥 إدارة الأدمن", callback_data="admins")],

        [InlineKeyboardButton("📊 إحصائيات", callback_data="stats")],
        [InlineKeyboardButton("🏠 رجوع للقائمة الرئيسية", callback_data="home")]
    ]

    await update.message.reply_text("👑 لوحة تحكم الأدمن:", reply_markup=InlineKeyboardMarkup(keyboard))


# =======================
# BUTTON HANDLER
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    # HOME
    if query.data == "home":
        await query.message.reply_text("اختر الخدمة المطلوبة:", reply_markup=main_menu())
        return

    # SUBJECTS
    if query.data == "go_subjects":
        await show_subjects(query.message)
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
        c.execute("SELECT file_id FROM lectures WHERE id=?", (lec_id,))
        file_id = c.fetchone()[0]
        conn.close()

        await query.message.reply_document(file_id)

        last_subject = context.user_data.get("last_subject")
        keyboard = [
            [InlineKeyboardButton("🔙 رجوع للمحاضرات", callback_data=f"sub_{last_subject}")],
            [InlineKeyboardButton("🏠 رجوع للمواد", callback_data="go_subjects")],
            [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="home")]
        ]
        await query.message.reply_text("اختر التالي 👇", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # REPORT PROBLEM
    if query.data == "report_problem":
        context.user_data["reporting"] = True
        await query.message.reply_text("✍️ اكتب المشكلة وسيتم إرسالها للإدارة:", reply_markup=home_button())
        return

    # MISSING FILES
    if query.data == "missing_files":
        context.user_data["missing_step"] = "subject"
        await query.message.reply_text("📚 اكتب اسم المقرر الذي يوجد به نقص:", reply_markup=home_button())
        return

    # =======================
    # ADMIN ONLY
    if not is_admin(uid):
        return

    # ADMINS BUTTON
    if query.data == "admins":
        await query.message.reply_text(
            "👥 إدارة الأدمن:\n\n"
            "➕ إضافة أدمن:\n/addadmin ID\n\n"
            "➖ حذف أدمن:\n/removeadmin ID"
        )
        return

    # ADD SUBJECT
    if query.data == "add_subject":
        context.user_data["waiting_subject"] = True
        await query.message.reply_text("✍️ اكتب اسم المادة الجديدة:")
        return

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

    # BROADCAST
    if query.data == "broadcast":
        context.user_data["broadcast"] = True
        await query.message.reply_text("✍️ اكتب الرسالة لإرسالها لكل الطلاب:")
        return

    # STATS
    if query.data == "stats":
        conn = db()
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM subjects")
        sub_count = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM lectures")
        lec_count = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM users")
        user_count = c.fetchone()[0]

        conn.close()

        await query.message.reply_text(
            f"📊 إحصائيات:\n\n📚 المواد: {sub_count}\n📄 المحاضرات: {lec_count}\n👥 الطلاب: {user_count}"
        )
        return


# =======================
# HANDLE TEXT
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text

    # REPORTING
    if context.user_data.get("reporting"):
        for admin in get_all_admins():
            await context.bot.send_message(admin, f"🚨 مشكلة من طالب:\n\n{text}")
        context.user_data.clear()
        return

    # ADMIN ONLY
    if not is_admin(uid):
        return

    # ADD SUBJECT TEXT
    if context.user_data.get("waiting_subject"):
        conn = db()
        c = conn.cursor()
        c.execute("INSERT INTO subjects(name) VALUES(?)", (text,))
        conn.commit()
        conn.close()

        log_admin_action(uid, f"➕ Added Subject: {text}")

        context.user_data.clear()
        await update.message.reply_text("✅ تم إضافة المادة.")
        return

    # ADD LECTURE TITLE
    if context.user_data.get("add_lec_subject"):
        context.user_data["lecture_title"] = text
        await update.message.reply_text("📤 الآن أرسل ملف PDF للمحاضرة")
        return

    # BROADCAST TEXT
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

        log_admin_action(uid, "📢 Sent Broadcast Message")

        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال الرسالة.")
        return


# =======================
# HANDLE PDF
async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    pdf = update.message.document

    if not is_admin(uid):
        return

    if "lecture_title" not in context.user_data:
        return

    sid = context.user_data["add_lec_subject"]
    title = context.user_data["lecture_title"]
    file_id = pdf.file_id

    conn = db()
    c = conn.cursor()
    c.execute("INSERT INTO lectures(subject_id,title,file_id) VALUES(?,?,?)",
              (sid, title, file_id))
    conn.commit()
    conn.close()

    log_admin_action(uid, f"📤 Uploaded Lecture: {title}")

    context.user_data.clear()
    await update.message.reply_text("✅ تم إضافة المحاضرة بنجاح!")


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

    log_admin_action(update.effective_user.id, f"👥 Added Admin: {admin_id}")
    await update.message.reply_text("✅ تم إضافة أدمن جديد.")


async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != MAIN_ADMIN_ID:
        return

    admin_id = int(context.args[0])

    conn = db()
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE user_id=?", (admin_id,))
    conn.commit()
    conn.close()

    log_admin_action(update.effective_user.id, f"👥 Removed Admin: {admin_id}")
    await update.message.reply_text("✅ تم حذف الأدمن.")


# =======================
# RUN BOT
def main():
    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))

    app.add_handler(CommandHandler("addadmin", add_admin))
    app.add_handler(CommandHandler("removeadmin", remove_admin))

    app.add_handler(CallbackQueryHandler(button_handler))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))

    print("✅ البوت شغال...")
    app.run_polling()


if __name__ == "__main__":
    main()
