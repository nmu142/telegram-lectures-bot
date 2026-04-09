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
BACKUP_FILE = "backup.db"

# =======================
# AUTO SAVE / RESTORE DATABASE

def save_db():
    try:
        if os.path.exists(DB_FILE):
            import shutil
            shutil.copy(DB_FILE, BACKUP_FILE)
    except:
        pass


def restore_db():
    try:
        if not os.path.exists(DB_FILE) and os.path.exists(BACKUP_FILE):
            import shutil
            shutil.copy(BACKUP_FILE, DB_FILE)
    except:
        pass


# =======================
# Maintenance Mode Global
BOT_ENABLED = True

MAINTENANCE_MESSAGE = (
    "💡 عزيزي الطالب ❤️\n"
    "البوت تحت التحديث حاليًا علشان نقدملك حاجة تليق بيك\n"
    "ارجع قريبًا إن شاء الله ✨"
)

# Rate Limit
RATE_LIMIT_WINDOW = 10
RATE_LIMIT_MAX_MESSAGES = 5
RATE_LIMIT_BLOCK_SECONDS = 10

RATE_LIMIT_MESSAGE = (
    "🚫 برجاء الانتظار 10 ثواني قبل إرسال رسائل جديدة "
    "حتى لا يتعطل البوت."
)

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
    restore_db()

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

    save_db()

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
    save_db()


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
    await register_user(update)

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
        keyboard.append([InlineKeyboardButton(name, callback_data=f"sub_{sid}")])

    await message.reply_text(
        "📚 اختر المادة:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


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
        keyboard.append(
            [InlineKeyboardButton(title, callback_data=f"lec_{lid}")]
        )

    await query.message.reply_text(
        "📘 المحاضرات:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =======================
# CALLBACK
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "go_subjects":
        await show_subjects(query.message)
        return

    if query.data.startswith("sub_"):
        sid = int(query.data.split("_")[1])
        await show_lectures(query, sid)
        return

    if query.data.startswith("lec_"):
        lid = int(query.data.split("_")[1])

        conn = db()
        c = conn.cursor()
        c.execute("SELECT file_id FROM lectures WHERE id=?", (lid,))
        row = c.fetchone()
        conn.close()

        if row:
            await query.message.reply_document(row[0])

# =======================
# ADD SUBJECT
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text

    if not is_admin(uid):
        return

    # add subject
    if context.user_data.get("waiting_subject"):
        conn = db()
        c = conn.cursor()
        c.execute("INSERT INTO subjects(name) VALUES(?)", (text,))
        conn.commit()
        conn.close()
        save_db()

        context.user_data.clear()
        await update.message.reply_text("✅ تم إضافة المادة.")
        return

    # add lecture title
    if context.user_data.get("add_lec_subject"):
        context.user_data["lecture_title"] = text
        await update.message.reply_text("📤 ارسل ملف PDF")
        return


# =======================
# HANDLE PDF
async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not is_admin(uid):
        return

    if "lecture_title" not in context.user_data:
        return

    sid = context.user_data["add_lec_subject"]
    title = context.user_data["lecture_title"]
    file_id = update.message.document.file_id

    conn = db()
    c = conn.cursor()

    c.execute(
        "INSERT INTO lectures(subject_id,title,file_id) VALUES(?,?,?)",
        (sid, title, file_id)
    )

    conn.commit()
    conn.close()
    save_db()

    context.user_data.clear()
    await update.message.reply_text("✅ تم إضافة المحاضرة")


# =======================
# ADMIN COMMAND
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    keyboard = [
        [InlineKeyboardButton("➕ إضافة مادة", callback_data="add_subject")],
        [InlineKeyboardButton("➕ إضافة محاضرة", callback_data="add_lecture")]
    ]

    await update.message.reply_text(
        "لوحة التحكم",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =======================
async def admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id

    if not is_admin(uid):
        return

    if query.data == "add_subject":
        context.user_data["waiting_subject"] = True
        await query.message.reply_text("اكتب اسم المادة")
        return

    if query.data == "add_lecture":
        conn = db()
        c = conn.cursor()
        c.execute("SELECT id,name FROM subjects")
        subs = c.fetchall()
        conn.close()

        keyboard = []
        for sid, name in subs:
            keyboard.append(
                [InlineKeyboardButton(name, callback_data=f"choose_{sid}")]
            )

        await query.message.reply_text(
            "اختر المادة",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if query.data.startswith("choose_"):
        sid = int(query.data.split("_")[1])
        context.user_data["add_lec_subject"] = sid
        await query.message.reply_text("اكتب عنوان المحاضرة")
        return


# =======================
def main():
    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))

    app.add_handler(CallbackQueryHandler(admin_buttons, pattern="^(add_|choose_)"))
    app.add_handler(CallbackQueryHandler(button_handler))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))

    print("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
