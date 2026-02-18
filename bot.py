import os
import sqlite3
import time
from datetime import datetime

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

BOT_ENABLED = True

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
# REGISTER USER
async def register_user(update: Update):
    uid = update.effective_user.id
    conn = db()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (uid,))
    conn.commit()
    conn.close()


# =======================
# RATE LIMIT CHECK
async def check_rate_limit(update: Update):
    uid = update.effective_user.id
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
        await update.message.reply_text(
            "💡 عزيزي الطالب ❤️\n"
            "البوت تحت التحديث حاليًا علشان نقدملك حاجة تليق بيك\n"
            "ارجع قريبًا إن شاء الله ✨"
        )
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
# IMPORTANT LINKS
async def show_links(query):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT id,title,url FROM important_links ORDER BY position ASC")
    links = c.fetchall()
    conn.close()

    if not links:
        await query.message.reply_text("📌 لا توجد لينكات بعد.", reply_markup=home_button())
        return

    keyboard = []
    for lid, title, url in links:
        keyboard.append([InlineKeyboardButton(f"🔗 {title}", callback_data=f"openLink_{lid}")])

    keyboard.append([InlineKeyboardButton("🏠 رجوع", callback_data="home")])

    await query.message.reply_text("📌 اختر لينك:", reply_markup=InlineKeyboardMarkup(keyboard))


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
# BUTTON HANDLER (Students + Admin Redirect)
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    # =======================
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

    if query.data == "report_problem":
        context.user_data["reporting"] = True
        await query.message.reply_text("✍️ اكتب المشكلة وسيتم إرسالها للإدارة:")
        return

    if query.data == "missing_files":
        context.user_data["missing_step"] = "subject"
        await query.message.reply_text("📚 اكتب اسم المادة:")
        return

    # OPEN SUBJECT
    if query.data.startswith("sub_"):
        sid = int(query.data.replace("sub_", ""))
        context.user_data["last_subject"] = sid
        await show_lectures(query, sid)
        return

    # OPEN LECTURE PDF
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
    # ADMIN BUTTONS REDIRECT
    if is_admin(uid):
        await admin_buttons(update, context)
        return
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

        [InlineKeyboardButton("🔗 إدارة اللينكات المهمة", callback_data="manage_links")],

        [InlineKeyboardButton("📢 رسالة جماعية", callback_data="broadcast")],
        [InlineKeyboardButton("📊 إحصائيات", callback_data="stats")],

        [InlineKeyboardButton("📦 Backup Database", callback_data="backup")],

        [InlineKeyboardButton("⏸ إيقاف البوت", callback_data="bot_off")],
        [InlineKeyboardButton("▶️ تشغيل البوت", callback_data="bot_on")],

        [InlineKeyboardButton("🏠 الرئيسية", callback_data="home")]
    ]

    await update.message.reply_text("👑 لوحة تحكم الأدمن:", reply_markup=InlineKeyboardMarkup(keyboard))


# =======================
# ADMIN BUTTONS
async def admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BOT_ENABLED

    query = update.callback_query
    uid = query.from_user.id

    if not is_admin(uid):
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
    # DELETE SUBJECT
    if query.data == "delete_subject":
        conn = db()
        c = conn.cursor()
        c.execute("SELECT id,name FROM subjects")
        subs = c.fetchall()
        conn.close()

        keyboard = []
        for sid, name in subs:
            keyboard.append([InlineKeyboardButton(name, callback_data=f"confirmDelSub_{sid}")])

        await query.message.reply_text("🗑 اختر المادة للحذف:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if query.data.startswith("confirmDelSub_"):
        sid = int(query.data.replace("confirmDelSub_", ""))

        keyboard = [
            [InlineKeyboardButton("✅ تأكيد الحذف", callback_data=f"doDelSub_{sid}")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="admin_cancel")]
        ]
        await query.message.reply_text("⚠️ هل أنت متأكد من حذف المادة؟", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if query.data.startswith("doDelSub_"):
        sid = int(query.data.replace("doDelSub_", ""))

        conn = db()
        c = conn.cursor()
        c.execute("DELETE FROM lectures WHERE subject_id=?", (sid,))
        c.execute("DELETE FROM subjects WHERE id=?", (sid,))
        conn.commit()
        conn.close()

        log_admin_action(uid, f"🗑 Deleted Subject {sid}")
        await query.message.reply_text("✅ تم حذف المادة ومحاضراتها.")
        return

    # =======================
    # DELETE LECTURE
    if query.data == "delete_lecture":
        conn = db()
        c = conn.cursor()
        c.execute("SELECT id,name FROM subjects")
        subs = c.fetchall()
        conn.close()

        keyboard = []
        for sid, name in subs:
            keyboard.append([InlineKeyboardButton(name, callback_data=f"delLecSub_{sid}")])

        await query.message.reply_text("📚 اختر المادة:", reply_markup=InlineKeyboardMarkup(keyboard))
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

        await query.message.reply_text("🗑 اختر المحاضرة للحذف:", reply_markup=InlineKeyboardMarkup(keyboard))
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

        log_admin_action(uid, f"🗑 Deleted Lecture {lid}")
        await query.message.reply_text("✅ تم حذف المحاضرة.")
        return

    # =======================
    # CANCEL
    if query.data == "admin_cancel":
        context.user_data.clear()
        await query.message.reply_text("❌ تم الإلغاء.")
        return
# =======================
# HANDLE TEXT
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_rate_limit(update):
        return

    uid = update.effective_user.id
    text = update.message.text

    # =======================
    # REPORT PROBLEM
    if context.user_data.get("reporting"):
        for admin in get_all_admins():
            await context.bot.send_message(admin, f"🚨 مشكلة من طالب:\n\n{text}")
        context.user_data.clear()
        await update.message.reply_text("✅ تم إرسال المشكلة للإدارة.")
        return

    # =======================
    # MISSING FILES
    if context.user_data.get("missing_step") == "subject":
        context.user_data["missing_subject"] = text
        context.user_data["missing_step"] = "lecture"
        await update.message.reply_text("📄 اكتب اسم المحاضرة الناقصة:")
        return

    if context.user_data.get("missing_step") == "lecture":
        context.user_data["missing_lecture"] = text
        context.user_data.clear()

        for admin in get_all_admins():
            await context.bot.send_message(
                admin,
                f"📌 نقص ملفات:\n\n📚 المادة: {context.user_data.get('missing_subject')}\n📄 المحاضرة: {text}"
            )

        await update.message.reply_text("✅ تم إرسال طلب النقص للإدارة.")
        return

    # =======================
    # ADMIN ONLY
    if not is_admin(uid):
        return

    # =======================
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

    # =======================
    # ADD LECTURE TITLE
    if context.user_data.get("add_lec_subject"):
        context.user_data["lecture_title"] = text
        await update.message.reply_text("📤 الآن أرسل ملف PDF للمحاضرة")
        return

    # =======================
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


# =======================
# HANDLE PDF UPLOAD
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
    c.execute(
        "INSERT INTO lectures(subject_id,title,file_id) VALUES(?,?,?)",
        (sid, title, file_id)
    )
    conn.commit()
    conn.close()

    log_admin_action(uid, f"📤 Uploaded Lecture {title}")
    context.user_data.clear()
    await update.message.reply_text("✅ تم إضافة المحاضرة بنجاح!")


# =======================
# EXTRA ADMIN BUTTONS
async def admin_buttons_extra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BOT_ENABLED
    query = update.callback_query
    uid = query.from_user.id

    if not is_admin(uid):
        return

    # BACKUP
    if query.data == "backup":
        await query.message.reply_document(open(DB_FILE, "rb"))
        await query.message.reply_text("✅ تم إرسال نسخة Backup.")
        return

    # BOT OFF
    if query.data == "bot_off":
        BOT_ENABLED = False
        await query.message.reply_text("⏸ تم إيقاف البوت للطلاب.")
        return

    # BOT ON
    if query.data == "bot_on":
        BOT_ENABLED = True
        await query.message.reply_text("▶️ تم تشغيل البوت للطلاب.")
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
            f"📊 الإحصائيات:\n\n"
            f"📚 المواد: {sub_count}\n"
            f"📄 المحاضرات: {lec_count}\n"
            f"👥 الطلاب: {user_count}"
        )
        return


# =======================
# RUN BOT
def main():
    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))

    app.add_handler(CallbackQueryHandler(button_handler))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))

    print("✅ البوت شغال...")
    app.run_polling()


if __name__ == "__main__":
    main()
