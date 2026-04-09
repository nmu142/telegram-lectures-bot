from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import aiosqlite
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import RetryAfter, TimedOut, NetworkError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =======================
# CONFIG (keeps legacy defaults)

TOKEN = os.getenv("TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError("TOKEN env var is required")

MAIN_ADMIN_ID = int(os.getenv("MAIN_ADMIN_ID", "8377544927"))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "El8awy116")

DB_FILE = os.getenv("DB_FILE", "lectures.db")
BACKUP_FILE = os.getenv("BACKUP_FILE", "backup.db")

BOT_ENABLED = True

MAINTENANCE_MESSAGE = os.getenv(
    "MAINTENANCE_MESSAGE",
    "💡 عزيزي الطالب ❤️\n"
    "البوت تحت التحديث حاليًا علشان نقدملك حاجة تليق بيك\n"
    "ارجع قريبًا إن شاء الله ✨",
)

# Rate Limit (legacy constants kept)
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "10"))
RATE_LIMIT_MAX_MESSAGES = int(os.getenv("RATE_LIMIT_MAX_MESSAGES", "5"))
RATE_LIMIT_BLOCK_SECONDS = int(os.getenv("RATE_LIMIT_BLOCK_SECONDS", "10"))

RATE_LIMIT_MESSAGE = os.getenv(
    "RATE_LIMIT_MESSAGE",
    "🚫 برجاء الانتظار 10 ثواني قبل إرسال رسائل جديدة حتى لا يتعطل البوت.",
)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# UX / performance tuning
UPLOAD_SESSION_TIMEOUT_S = int(os.getenv("UPLOAD_SESSION_TIMEOUT_S", str(20 * 60)))
UPLOAD_PROGRESS_EDIT_EVERY_S = float(os.getenv("UPLOAD_PROGRESS_EDIT_EVERY_S", "2.5"))
LIST_LOADING_EDIT = True

# =======================
# LOG SYSTEM

LOG = logging.getLogger("lectures_bot")


def log_admin_action(user_id: int, action: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open("admin_log.txt", "a", encoding="utf-8") as f:
            f.write(f"[{now}] Admin({user_id}) -> {action}\n")
    except Exception:
        pass


# =======================
# BACKUP / RESTORE (best-effort, legacy-compatible)


def save_db() -> None:
    try:
        if os.path.exists(DB_FILE):
            shutil.copy(DB_FILE, BACKUP_FILE)
    except Exception:
        pass


def restore_db() -> None:
    try:
        if not os.path.exists(DB_FILE) and os.path.exists(BACKUP_FILE):
            shutil.copy(BACKUP_FILE, DB_FILE)
    except Exception:
        pass


# =======================
# DB (async, additive migrations only)


async def db_connect() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(DB_FILE)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = aiosqlite.Row
    return conn


async def _add_column_if_missing(conn: aiosqlite.Connection, table: str, column: str, col_type: str) -> None:
    async with conn.execute(f"PRAGMA table_info({table})") as cur:
        rows = await cur.fetchall()
    existing = {r[1] for r in rows}
    if column in existing:
        return
    await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


async def init_db() -> None:
    restore_db()
    async with db_connect() as conn:
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS subjects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE
            );

            CREATE TABLE IF NOT EXISTS lectures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject_id INTEGER,
                title TEXT,
                file_id TEXT,
                FOREIGN KEY(subject_id) REFERENCES subjects(id)
            );

            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER UNIQUE
            );

            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER UNIQUE
            );

            CREATE TABLE IF NOT EXISTS important_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                url TEXT,
                position INTEGER DEFAULT 999
            );
            """
        )

        # Additive columns
        await _add_column_if_missing(conn, "subjects", "created_at", "INTEGER")
        await _add_column_if_missing(conn, "subjects", "manual_order", "INTEGER")
        await _add_column_if_missing(conn, "lectures", "created_at", "INTEGER")
        await _add_column_if_missing(conn, "lectures", "manual_order", "INTEGER")
        await _add_column_if_missing(conn, "users", "username", "TEXT")
        await _add_column_if_missing(conn, "users", "first_name", "TEXT")
        await _add_column_if_missing(conn, "users", "last_name", "TEXT")
        await _add_column_if_missing(conn, "users", "updated_at", "INTEGER")

        # Extensions
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS favorites (
                user_id INTEGER NOT NULL,
                lecture_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(user_id, lecture_id)
            );

            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                status TEXT DEFAULT 'new'
            );

            CREATE TABLE IF NOT EXISTS uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL,
                lecture_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            );
            """
        )

        await conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_lectures_subject ON lectures(subject_id);
            CREATE INDEX IF NOT EXISTS idx_lectures_title ON lectures(title);
            CREATE INDEX IF NOT EXISTS idx_subjects_name ON subjects(name);
            CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
            CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id);
            CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
            CREATE INDEX IF NOT EXISTS idx_links_position ON important_links(position, id);
            """
        )

        # Optional: FTS for fast partial search (additive)
        await conn.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS lectures_fts USING fts5(
                title,
                content='lectures',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            );
            """
        )
        # Triggers are safe to (re)create via IF NOT EXISTS pattern by checking sqlite_master
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name IN "
            "('lectures_ai','lectures_ad','lectures_au')"
        ) as cur_tr:
            existing_tr = {r[0] for r in await cur_tr.fetchall()}
        if "lectures_ai" not in existing_tr:
            await conn.execute(
                """
                CREATE TRIGGER lectures_ai AFTER INSERT ON lectures BEGIN
                    INSERT INTO lectures_fts(rowid, title) VALUES (new.id, new.title);
                END;
                """
            )
        if "lectures_ad" not in existing_tr:
            await conn.execute(
                """
                CREATE TRIGGER lectures_ad AFTER DELETE ON lectures BEGIN
                    INSERT INTO lectures_fts(lectures_fts, rowid, title) VALUES('delete', old.id, old.title);
                END;
                """
            )
        if "lectures_au" not in existing_tr:
            await conn.execute(
                """
                CREATE TRIGGER lectures_au AFTER UPDATE OF title ON lectures BEGIN
                    INSERT INTO lectures_fts(lectures_fts, rowid, title) VALUES('delete', old.id, old.title);
                    INSERT INTO lectures_fts(rowid, title) VALUES (new.id, new.title);
                END;
                """
            )
        # Backfill FTS if empty
        async with conn.execute("SELECT COUNT(*) AS c FROM lectures_fts") as cur_fts:
            fts_count = int((await cur_fts.fetchone())["c"])
        if fts_count == 0:
            await conn.execute("INSERT INTO lectures_fts(rowid, title) SELECT id, title FROM lectures")

        now = int(time.time())
        await conn.execute(
            "UPDATE subjects SET created_at=COALESCE(created_at, ?) WHERE created_at IS NULL",
            (now,),
        )
        await conn.execute(
            "UPDATE lectures SET created_at=COALESCE(created_at, ?) WHERE created_at IS NULL",
            (now,),
        )
        await conn.commit()

    # legacy behavior
    save_db()
    if not os.path.exists("admin_log.txt"):
        try:
            with open("admin_log.txt", "a", encoding="utf-8"):
                pass
        except Exception:
            pass


# =======================
# SECURITY: admin + rate limit


class RateLimiter:
    def __init__(self) -> None:
        self.user_messages: dict[int, list[float]] = {}
        self.blocked_until: dict[int, float] = {}

    def check(self, user_id: int) -> bool:
        now = time.time()
        until = self.blocked_until.get(user_id)
        if until and now < until:
            return False
        if until and now >= until:
            self.blocked_until.pop(user_id, None)

        msgs = self.user_messages.setdefault(user_id, [])
        cutoff = now - RATE_LIMIT_WINDOW
        while msgs and msgs[0] < cutoff:
            msgs.pop(0)
        msgs.append(now)
        if len(msgs) > RATE_LIMIT_MAX_MESSAGES:
            self.blocked_until[user_id] = now + RATE_LIMIT_BLOCK_SECONDS
            self.user_messages[user_id] = []
            return False
        return True


async def is_admin(uid: int) -> bool:
    if uid == MAIN_ADMIN_ID:
        return True
    async with db_connect() as conn:
        async with conn.execute("SELECT 1 FROM admins WHERE user_id=?", (uid,)) as cur:
            row = await cur.fetchone()
            return row is not None


# =======================
# NAV / STATE


def _nav_stack(context: ContextTypes.DEFAULT_TYPE) -> list[str]:
    st = context.user_data.setdefault("nav_stack", [])
    if isinstance(st, list):
        return st
    context.user_data["nav_stack"] = []
    return context.user_data["nav_stack"]


def nav_push(context: ContextTypes.DEFAULT_TYPE, cb: str) -> None:
    _nav_stack(context).append(cb)


def nav_pop(context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    st = _nav_stack(context)
    if not st:
        return None
    return st.pop()


def nav_clear(context: ContextTypes.DEFAULT_TYPE) -> None:
    _nav_stack(context).clear()


# Upload sessions (multi-upload) will be fully implemented in later todos.
@dataclass
class UploadSession:
    mode: str  # add_lecture/import_folder/import_batch
    subject_id: Optional[int] = None
    created_at: int = 0
    last_activity: int = 0
    progress_chat_id: Optional[int] = None
    progress_message_id: Optional[int] = None
    last_progress_edit_ts: float = 0.0


def get_upload_session(context: ContextTypes.DEFAULT_TYPE) -> Optional[UploadSession]:
    s = context.user_data.get("upload_session")
    if not isinstance(s, UploadSession):
        return None
    now = int(time.time())
    last = s.last_activity or s.created_at or now
    if UPLOAD_SESSION_TIMEOUT_S > 0 and (now - last) > UPLOAD_SESSION_TIMEOUT_S:
        # expire silently; UX will prompt admin to restart flow
        context.user_data.pop("upload_session", None)
        context.user_data.pop("upload_counters", None)
        return None
    return s


def set_upload_session(context: ContextTypes.DEFAULT_TYPE, session: Optional[UploadSession]) -> None:
    if session is None:
        context.user_data.pop("upload_session", None)
        context.user_data.pop("upload_counters", None)
    else:
        now = int(time.time())
        if not session.created_at:
            session.created_at = now
        session.last_activity = now
        context.user_data["upload_session"] = session


def cleanup_expired_upload_session(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns True if a session was expired/cleared."""
    s = context.user_data.get("upload_session")
    if not isinstance(s, UploadSession):
        return False
    now = int(time.time())
    last = s.last_activity or s.created_at or now
    if UPLOAD_SESSION_TIMEOUT_S > 0 and (now - last) > UPLOAD_SESSION_TIMEOUT_S:
        context.user_data.pop("upload_session", None)
        context.user_data.pop("upload_counters", None)
        context.user_data.pop("admin_mode", None)
        return True
    return False


# =======================
# MENUS / UI


def btn(text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=cb)


def url_btn(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, url=url)


def markup(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows)


def grid_2col(buttons: list[InlineKeyboardButton]) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i : i + 2])
    return rows


def nav_row(*, show_back: bool = True, show_home: bool = True) -> list[InlineKeyboardButton]:
    row: list[InlineKeyboardButton] = []
    if show_back:
        row.append(btn("🔙 رجوع", "nav:back"))
    if show_home:
        row.append(btn("🏠 الرئيسية", "u:home"))
    return row


def admin_nav_row(*, show_back: bool = True, show_home: bool = True) -> list[InlineKeyboardButton]:
    row: list[InlineKeyboardButton] = []
    if show_back:
        row.append(btn("🔙 رجوع", "a:panel"))
    if show_home:
        row.append(btn("🏠 الرئيسية", "u:home"))
    return row


def pagination_row(*, base_cb: str, page: int, has_prev: bool, has_next: bool) -> list[InlineKeyboardButton]:
    row: list[InlineKeyboardButton] = []
    if has_prev:
        row.append(btn("⬅️ السابق", f"{base_cb}:p={page-1}"))
    row.append(btn(f"📄 {page+1}", "noop"))
    if has_next:
        row.append(btn("➡️ التالي", f"{base_cb}:p={page+1}"))
    return row


def main_menu() -> InlineKeyboardMarkup:
    return markup(
        [
            [btn("📚 المواد", "u:subjects:p=0")],
            [btn("🔗 لينكات مهمة", "u:links:p=0")],
            [btn("📝 طلب محاضرة / إبلاغ عن نقص", "u:request")],
            [btn("🔎 البحث عن محاضرة", "u:search")],
            [btn("🆕 أحدث المحاضرات", "u:latest")],
            [btn("⭐ المفضلة", "u:favorites:p=0")],
            [url_btn("👤 التواصل مع الأدمن", f"https://t.me/{ADMIN_USERNAME}")],
        ]
    )


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        btn("➕📚 Add subject", "a:add_subject"),
        btn("➕📄 Add lecture", "a:add_lecture"),
        btn("📥📁 Import folder", "a:import_folder"),
        btn("📥🗂 Import batch", "a:import_batch"),
        btn("🗑️📚 Delete subject", "a:delete_subject"),
        btn("🗑️📄 Delete lecture", "a:delete_lecture"),
        btn("✏️📚 Edit subject", "a:edit_subject"),
        btn("✏️📄 Edit lecture", "a:edit_lecture"),
        btn("↕️📚 Sort subjects", "a:sort_subjects"),
        btn("🔗 Manage links", "a:manage_links"),
        btn("📢 Broadcast", "a:broadcast"),
        btn("📊 Statistics", "a:stats"),
        btn("💾 Backup DB", "a:backup"),
        btn("🧹 Clean data", "a:clean"),
        btn("⛔ Stop bot", "a:stop"),
        btn("✅ Start bot", "a:start"),
        btn("👮 Manage admins", "a:manage_admins"),
        btn("⬅️ Back to admin", "a:panel"),
    ]
    rows = grid_2col(buttons)
    rows.append(nav_row(show_back=False, show_home=True))
    return markup(rows)


# =======================
# HELPERS


def _parse_int_param(data: str, key: str) -> Optional[int]:
    token = f"{key}="
    if token not in data:
        return None
    try:
        after = data.split(token, 1)[1]
        num = after.split(":", 1)[0]
        return int(num)
    except Exception:
        return None


def _parse_page(data: str) -> int:
    p = _parse_int_param(data, "p")
    return p if p is not None and p >= 0 else 0


async def register_user(update: Update) -> None:
    if not update.effective_user:
        return
    u = update.effective_user
    now = int(time.time())
    async with db_connect() as conn:
        await conn.execute(
            """
            INSERT INTO users(user_id, username, first_name, last_name, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                updated_at=excluded.updated_at
            """,
            (u.id, u.username, u.first_name, u.last_name, now),
        )
        await conn.commit()
    save_db()


# =======================
# USER HANDLERS


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    limiter: RateLimiter = context.application.bot_data["limiter"]
    uid = update.effective_user.id if update.effective_user else 0
    if uid and not limiter.check(uid):
        await update.message.reply_text(RATE_LIMIT_MESSAGE)
        return

    if not BOT_ENABLED and uid and not await is_admin(uid):
        await update.message.reply_text(MAINTENANCE_MESSAGE)
        return

    await register_user(update)
    await update.message.reply_text(
        "✨ أهلاً بيك عزيزي الطالب\nأتمنالك تجربة ممتعة وموفقة بإذن الله ❤️📚",
        reply_markup=main_menu(),
    )


async def user_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return
    await query.answer()

    limiter: RateLimiter = context.application.bot_data["limiter"]
    uid = query.from_user.id
    if not limiter.check(uid):
        await query.message.reply_text(RATE_LIMIT_MESSAGE)
        return

    if not BOT_ENABLED and not await is_admin(uid):
        await query.message.reply_text(MAINTENANCE_MESSAGE)
        return

    data = query.data or ""

    if data == "u:home":
        nav_clear(context)
        await query.message.reply_text("🏠 القائمة الرئيسية", reply_markup=main_menu())
        return

    if data == "nav:back":
        prev = nav_pop(context)
        if not prev:
            await query.message.reply_text("🏠 القائمة الرئيسية", reply_markup=main_menu())
            return
        query.data = prev
        await user_callbacks(update, context)
        return

    if data.startswith("u:subjects"):
        nav_push(context, "u:home")
        await show_subjects(query, page=_parse_page(data))
        return

    if data.startswith("u:lectures"):
        sid = _parse_int_param(data, "s")
        if sid is None:
            return
        nav_push(context, "u:subjects:p=0")
        await show_lectures(query, sid, page=_parse_page(data))
        return

    if data.startswith("u:lec:"):
        lid = _parse_int_param(data, "id")
        if lid is None:
            return
        async with db_connect() as conn:
            async with conn.execute("SELECT file_id, title FROM lectures WHERE id=?", (lid,)) as cur:
                row = await cur.fetchone()
            if not row:
                await query.message.reply_text("⚠️ المحاضرة غير موجودة.", reply_markup=markup([nav_row()]))
                return
            async with conn.execute(
                "SELECT 1 FROM favorites WHERE user_id=? AND lecture_id=?",
                (uid, lid),
            ) as cur2:
                fav = await cur2.fetchone()
        fav_text = "⭐ إزالة من المفضلة" if fav else "⭐ حفظ في المفضلة"
        await query.message.reply_chat_action(ChatAction.UPLOAD_DOCUMENT)
        await query.message.reply_document(
            row["file_id"],
            caption=f"📄 {row['title']}",
            reply_markup=markup([[btn(fav_text, f"u:fav_toggle:id={lid}")], nav_row()]),
        )
        return

    if data.startswith("u:fav_toggle:"):
        lid = _parse_int_param(data, "id")
        if lid is None:
            return
        now = int(time.time())
        async with db_connect() as conn:
            async with conn.execute(
                "SELECT 1 FROM favorites WHERE user_id=? AND lecture_id=?",
                (uid, lid),
            ) as cur:
                exists = await cur.fetchone()
            if exists:
                await conn.execute("DELETE FROM favorites WHERE user_id=? AND lecture_id=?", (uid, lid))
                await conn.commit()
                save_db()
                await query.message.reply_text("✅ تم الإزالة من المفضلة.", reply_markup=markup([nav_row()]))
            else:
                await conn.execute(
                    "INSERT OR IGNORE INTO favorites(user_id, lecture_id, created_at) VALUES(?,?,?)",
                    (uid, lid, now),
                )
                await conn.commit()
                save_db()
                await query.message.reply_text("✅ تم الحفظ في المفضلة.", reply_markup=markup([nav_row()]))
        return

    if data.startswith("u:links"):
        nav_push(context, "u:home")
        await show_links(query, page=_parse_page(data))
        return

    if data == "u:request":
        nav_push(context, "u:home")
        context.user_data["waiting_request_text"] = True
        await query.message.reply_text("📝 اكتب طلبك وسيتم إرساله للأدمن.", reply_markup=markup([nav_row()]))
        return

    if data == "u:latest":
        nav_push(context, "u:home")
        await show_latest(query)
        return

    if data.startswith("u:favorites"):
        nav_push(context, "u:home")
        await show_favorites(query, user_id=uid, page=_parse_page(data))
        return

    if data == "u:search":
        nav_push(context, "u:home")
        context.user_data["waiting_search_query"] = True
        await query.message.reply_text("🔎 اكتب جزء من اسم المحاضرة للبحث.", reply_markup=markup([nav_row()]))
        return

    if data.startswith("u:search_results"):
        q = context.user_data.get("last_search_query") or ""
        page = _parse_page(data)
        await show_search_results(query, query_text=str(q), page=page)
        return


async def user_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    limiter: RateLimiter = context.application.bot_data["limiter"]
    uid = update.effective_user.id
    if not limiter.check(uid):
        await update.message.reply_text(RATE_LIMIT_MESSAGE)
        return

    if not BOT_ENABLED and not await is_admin(uid):
        await update.message.reply_text(MAINTENANCE_MESSAGE)
        return

    if context.user_data.get("waiting_request_text"):
        text = (update.message.text or "").strip()
        if not text:
            return
        context.user_data.pop("waiting_request_text", None)
        now = int(time.time())
        async with db_connect() as conn:
            await conn.execute(
                "INSERT INTO requests(user_id, text, created_at, status) VALUES(?,?,?,?)",
                (uid, text, now, "new"),
            )
            await conn.commit()
        save_db()
        try:
            await context.bot.send_message(chat_id=MAIN_ADMIN_ID, text=f"📝 طلب جديد من المستخدم {uid}:\n\n{text}")
        except Exception:
            pass
        await update.message.reply_text("✅ تم إرسال طلبك للأدمن.", reply_markup=main_menu())
        return

    if context.user_data.get("waiting_search_query"):
        q = (update.message.text or "").strip()
        if not q:
            return
        context.user_data["waiting_search_query"] = False
        context.user_data["last_search_query"] = q
        await show_search_results(update.message, query_text=q, page=0, is_message=True)
        return


async def show_subjects(query, page: int) -> None:
    PAGE_SIZE = 12
    offset = page * PAGE_SIZE
    loading_msg = None
    try:
        if LIST_LOADING_EDIT:
            loading_msg = await query.message.reply_text("⏳ جاري تحميل المواد...")
    except Exception:
        loading_msg = None
    async with db_connect() as conn:
        async with conn.execute(
            """
            SELECT id, name
            FROM subjects
            ORDER BY COALESCE(manual_order, 999999999), created_at, id
            LIMIT ? OFFSET ?
            """,
            (PAGE_SIZE + 1, offset),
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        if loading_msg:
            try:
                await loading_msg.edit_text("📌 لا توجد مواد بعد.")
            except Exception:
                await query.message.reply_text("📌 لا توجد مواد بعد.", reply_markup=markup([nav_row()]))
        else:
            await query.message.reply_text("📌 لا توجد مواد بعد.", reply_markup=markup([nav_row()]))
        return

    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    buttons = [btn(f"📚 {r['name']}", f"u:lectures:s={r['id']}:p=0") for r in rows]
    keyboard: list[list[InlineKeyboardButton]] = []
    keyboard.extend(grid_2col(buttons))
    keyboard.append(pagination_row(base_cb="u:subjects", page=page, has_prev=page > 0, has_next=has_next))
    keyboard.append(nav_row())
    if loading_msg:
        try:
            await loading_msg.edit_text("📚 اختر المادة:", reply_markup=markup(keyboard))
        except Exception:
            await query.message.reply_text("📚 اختر المادة:", reply_markup=markup(keyboard))
    else:
        await query.message.reply_text("📚 اختر المادة:", reply_markup=markup(keyboard))


async def show_lectures(query, subject_id: int, page: int) -> None:
    PAGE_SIZE = 12
    offset = page * PAGE_SIZE
    loading_msg = None
    try:
        if LIST_LOADING_EDIT:
            loading_msg = await query.message.reply_text("⏳ جاري تحميل المحاضرات...")
    except Exception:
        loading_msg = None
    async with db_connect() as conn:
        async with conn.execute(
            """
            SELECT id, title
            FROM lectures
            WHERE subject_id=?
            ORDER BY COALESCE(manual_order, 999999999), created_at, id
            LIMIT ? OFFSET ?
            """,
            (subject_id, PAGE_SIZE + 1, offset),
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        if loading_msg:
            try:
                await loading_msg.edit_text("📌 لا توجد محاضرات بعد.", reply_markup=markup([nav_row()]))
            except Exception:
                await query.message.reply_text("📌 لا توجد محاضرات بعد.", reply_markup=markup([nav_row()]))
        else:
            await query.message.reply_text("📌 لا توجد محاضرات بعد.", reply_markup=markup([nav_row()]))
        return

    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    buttons = [btn(f"📄 {r['title']}", f"u:lec:id={r['id']}") for r in rows]
    keyboard: list[list[InlineKeyboardButton]] = []
    keyboard.extend(grid_2col(buttons))
    keyboard.append(
        pagination_row(
            base_cb=f"u:lectures:s={subject_id}",
            page=page,
            has_prev=page > 0,
            has_next=has_next,
        )
    )
    keyboard.append(nav_row())
    if loading_msg:
        try:
            await loading_msg.edit_text("📘 المحاضرات:", reply_markup=markup(keyboard))
        except Exception:
            await query.message.reply_text("📘 المحاضرات:", reply_markup=markup(keyboard))
    else:
        await query.message.reply_text("📘 المحاضرات:", reply_markup=markup(keyboard))


async def show_links(query, page: int) -> None:
    PAGE_SIZE = 12
    offset = page * PAGE_SIZE
    async with db_connect() as conn:
        async with conn.execute(
            """
            SELECT id, title, url
            FROM important_links
            ORDER BY position, id
            LIMIT ? OFFSET ?
            """,
            (PAGE_SIZE + 1, offset),
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        await query.message.reply_text("📌 لا توجد لينكات بعد.", reply_markup=markup([nav_row()]))
        return

    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    keyboard: list[list[InlineKeyboardButton]] = []
    for r in rows:
        keyboard.append([url_btn(f"🔗 {r['title']}", r["url"])])
    keyboard.append(pagination_row(base_cb="u:links", page=page, has_prev=page > 0, has_next=has_next))
    keyboard.append(nav_row())
    await query.message.reply_text("🔗 لينكات مهمة:", reply_markup=markup(keyboard))


async def show_latest(query) -> None:
    async with db_connect() as conn:
        async with conn.execute(
            "SELECT id, title FROM lectures ORDER BY created_at DESC, id DESC LIMIT 10"
        ) as cur:
            rows = await cur.fetchall()
    if not rows:
        await query.message.reply_text("📌 لا توجد محاضرات بعد.", reply_markup=markup([nav_row()]))
        return
    buttons = [btn(f"🆕 {r['title']}", f"u:lec:id={r['id']}") for r in rows]
    keyboard: list[list[InlineKeyboardButton]] = []
    keyboard.extend(grid_2col(buttons))
    keyboard.append(nav_row())
    await query.message.reply_text("🆕 أحدث 10 محاضرات:", reply_markup=markup(keyboard))


async def show_search_results(target, query_text: str, page: int, is_message: bool = False) -> None:
    PAGE_SIZE = 10
    offset = page * PAGE_SIZE
    q = query_text.strip()
    if not q:
        if is_message:
            await target.reply_text("⚠️ اكتب كلمة للبحث.", reply_markup=markup([nav_row()]))
        else:
            await target.message.reply_text("⚠️ اكتب كلمة للبحث.", reply_markup=markup([nav_row()]))
        return
    async with db_connect() as conn:
        # Prefer FTS when available; fallback to LIKE
        try:
            fts_q = " ".join([p + "*" for p in q.split() if p])
            if not fts_q:
                raise ValueError("empty fts query")
            async with conn.execute(
                """
                SELECT l.id, l.title
                FROM lectures_fts f
                JOIN lectures l ON l.id = f.rowid
                WHERE lectures_fts MATCH ?
                ORDER BY l.created_at DESC, l.id DESC
                LIMIT ? OFFSET ?
                """,
                (fts_q, PAGE_SIZE + 1, offset),
            ) as cur:
                rows = await cur.fetchall()
        except Exception:
            like = f"%{q}%"
            async with conn.execute(
                """
                SELECT id, title
                FROM lectures
                WHERE title LIKE ? COLLATE NOCASE
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (like, PAGE_SIZE + 1, offset),
            ) as cur:
                rows = await cur.fetchall()
    if not rows:
        text = f"🔎 لا توجد نتائج لـ: {q}"
        if is_message:
            await target.reply_text(text, reply_markup=markup([nav_row()]))
        else:
            await target.message.reply_text(text, reply_markup=markup([nav_row()]))
        return
    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    buttons = [btn(f"🔎 {r['title']}", f"u:lec:id={int(r['id'])}") for r in rows]
    keyboard: list[list[InlineKeyboardButton]] = []
    keyboard.extend(grid_2col(buttons))
    keyboard.append(pagination_row(base_cb="u:search_results", page=page, has_prev=page > 0, has_next=has_next))
    keyboard.append(nav_row())
    text = f"🔎 نتائج البحث: {q}"
    if is_message:
        await target.reply_text(text, reply_markup=markup(keyboard))
    else:
        await target.message.reply_text(text, reply_markup=markup(keyboard))


async def show_favorites(query, user_id: int, page: int) -> None:
    PAGE_SIZE = 12
    offset = page * PAGE_SIZE
    async with db_connect() as conn:
        async with conn.execute(
            """
            SELECT l.id, l.title
            FROM favorites f
            JOIN lectures l ON l.id = f.lecture_id
            WHERE f.user_id=?
            ORDER BY f.created_at DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, PAGE_SIZE + 1, offset),
        ) as cur:
            rows = await cur.fetchall()
    if not rows:
        await query.message.reply_text("⭐ لا توجد محاضرات في المفضلة بعد.", reply_markup=markup([nav_row()]))
        return
    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    buttons = [btn(f"⭐ {r['title']}", f"u:lec:id={r['id']}") for r in rows]
    keyboard: list[list[InlineKeyboardButton]] = []
    keyboard.extend(grid_2col(buttons))
    keyboard.append(pagination_row(base_cb="u:favorites", page=page, has_prev=page > 0, has_next=has_next))
    keyboard.append(nav_row())
    await query.message.reply_text("⭐ المفضلة:", reply_markup=markup(keyboard))


# =======================
# ADMIN HANDLERS (skeleton wired; full flows to be completed in remaining todos)

SORT_PAGE_SIZE = 8


async def _ensure_manual_order_subjects() -> None:
    async with db_connect() as conn:
        async with conn.execute(
            """
            SELECT id
            FROM subjects
            ORDER BY COALESCE(manual_order, 999999999), created_at, id
            """
        ) as cur:
            rows = await cur.fetchall()
        # If all already have manual_order, keep them as-is
        if rows:
            async with conn.execute("SELECT COUNT(*) AS c FROM subjects WHERE manual_order IS NULL") as cur2:
                c = await cur2.fetchone()
            if c and int(c["c"]) == 0:
                return
        for i, r in enumerate(rows, start=1):
            await conn.execute("UPDATE subjects SET manual_order=COALESCE(manual_order, ?) WHERE id=?", (i, r["id"]))
        await conn.commit()


async def _ensure_manual_order_lectures(subject_id: int) -> None:
    async with db_connect() as conn:
        async with conn.execute(
            """
            SELECT id
            FROM lectures
            WHERE subject_id=?
            ORDER BY COALESCE(manual_order, 999999999), created_at, id
            """,
            (subject_id,),
        ) as cur:
            rows = await cur.fetchall()
        if rows:
            async with conn.execute(
                "SELECT COUNT(*) AS c FROM lectures WHERE subject_id=? AND manual_order IS NULL",
                (subject_id,),
            ) as cur2:
                c = await cur2.fetchone()
            if c and int(c["c"]) == 0:
                return
        for i, r in enumerate(rows, start=1):
            await conn.execute(
                "UPDATE lectures SET manual_order=COALESCE(manual_order, ?) WHERE id=? AND subject_id=?",
                (i, r["id"], subject_id),
            )
        await conn.commit()


async def _swap_subject_order(subject_id: int, direction: str) -> None:
    await _ensure_manual_order_subjects()
    async with db_connect() as conn:
        async with conn.execute(
            "SELECT id, manual_order FROM subjects ORDER BY manual_order, id"
        ) as cur:
            rows = await cur.fetchall()
        ids = [int(r["id"]) for r in rows]
        if subject_id not in ids:
            return
        idx = ids.index(subject_id)
        if direction == "up" and idx == 0:
            return
        if direction == "down" and idx == len(ids) - 1:
            return
        swap_idx = idx - 1 if direction == "up" else idx + 1
        a_id = ids[idx]
        b_id = ids[swap_idx]
        a_ord = int(rows[idx]["manual_order"] or idx + 1)
        b_ord = int(rows[swap_idx]["manual_order"] or swap_idx + 1)
        await conn.execute("BEGIN")
        await conn.execute("UPDATE subjects SET manual_order=? WHERE id=?", (b_ord, a_id))
        await conn.execute("UPDATE subjects SET manual_order=? WHERE id=?", (a_ord, b_id))
        await conn.commit()


async def _swap_lecture_order(subject_id: int, lecture_id: int, direction: str) -> None:
    await _ensure_manual_order_lectures(subject_id)
    async with db_connect() as conn:
        async with conn.execute(
            "SELECT id, manual_order FROM lectures WHERE subject_id=? ORDER BY manual_order, id",
            (subject_id,),
        ) as cur:
            rows = await cur.fetchall()
        ids = [int(r["id"]) for r in rows]
        if lecture_id not in ids:
            return
        idx = ids.index(lecture_id)
        if direction == "up" and idx == 0:
            return
        if direction == "down" and idx == len(ids) - 1:
            return
        swap_idx = idx - 1 if direction == "up" else idx + 1
        a_id = ids[idx]
        b_id = ids[swap_idx]
        a_ord = int(rows[idx]["manual_order"] or idx + 1)
        b_ord = int(rows[swap_idx]["manual_order"] or swap_idx + 1)
        await conn.execute("BEGIN")
        await conn.execute("UPDATE lectures SET manual_order=? WHERE id=? AND subject_id=?", (b_ord, a_id, subject_id))
        await conn.execute("UPDATE lectures SET manual_order=? WHERE id=? AND subject_id=?", (a_ord, b_id, subject_id))
        await conn.commit()


async def _admin_show_sort_subjects(query, page: int) -> None:
    await _ensure_manual_order_subjects()
    offset = page * SORT_PAGE_SIZE
    async with db_connect() as conn:
        async with conn.execute(
            """
            SELECT id, name
            FROM subjects
            ORDER BY manual_order, id
            LIMIT ? OFFSET ?
            """,
            (SORT_PAGE_SIZE + 1, offset),
        ) as cur:
            rows = await cur.fetchall()
    if not rows:
        await query.message.reply_text("📌 لا توجد مواد.", reply_markup=markup([nav_row()]))
        return
    has_next = len(rows) > SORT_PAGE_SIZE
    rows = rows[:SORT_PAGE_SIZE]
    keyboard: list[list[InlineKeyboardButton]] = []
    for r in rows:
        sid = int(r["id"])
        keyboard.append(
            [
                btn("⬆️", f"a:sort_subjects:move:id={sid}:dir=up:p={page}"),
                btn(f"📚 {r['name']}", f"a:sort_lectures:subject={sid}:p=0"),
                btn("⬇️", f"a:sort_subjects:move:id={sid}:dir=down:p={page}"),
            ]
        )
    keyboard.append(pagination_row(base_cb="a:sort_subjects", page=page, has_prev=page > 0, has_next=has_next))
    keyboard.append(admin_nav_row())
    await query.message.reply_text("↕️📚 ترتيب المواد (يدوي):", reply_markup=markup(keyboard))


async def _admin_show_sort_lectures(query, subject_id: int, page: int) -> None:
    await _ensure_manual_order_lectures(subject_id)
    offset = page * SORT_PAGE_SIZE
    async with db_connect() as conn:
        async with conn.execute("SELECT name FROM subjects WHERE id=?", (subject_id,)) as cur:
            sub = await cur.fetchone()
        async with conn.execute(
            """
            SELECT id, title
            FROM lectures
            WHERE subject_id=?
            ORDER BY manual_order, id
            LIMIT ? OFFSET ?
            """,
            (subject_id, SORT_PAGE_SIZE + 1, offset),
        ) as cur2:
            rows = await cur2.fetchall()
    if not rows:
        await query.message.reply_text("📌 لا توجد محاضرات.", reply_markup=markup([nav_row()]))
        return
    has_next = len(rows) > SORT_PAGE_SIZE
    rows = rows[:SORT_PAGE_SIZE]
    keyboard: list[list[InlineKeyboardButton]] = []
    for r in rows:
        lid = int(r["id"])
        keyboard.append(
            [
                btn("⬆️", f"a:sort_lectures:move:s={subject_id}:id={lid}:dir=up:p={page}"),
                btn(f"📄 {r['title']}", f"u:lec:id={lid}"),
                btn("⬇️", f"a:sort_lectures:move:s={subject_id}:id={lid}:dir=down:p={page}"),
            ]
        )
    keyboard.append(
        pagination_row(
            base_cb=f"a:sort_lectures:subject={subject_id}",
            page=page,
            has_prev=page > 0,
            has_next=has_next,
        )
    )
    keyboard.append([btn("⬅️ رجوع لترتيب المواد", "a:sort_subjects:p=0")])
    keyboard.append(admin_nav_row())
    sub_name = sub["name"] if sub else str(subject_id)
    await query.message.reply_text(f"↕️📄 ترتيب المحاضرات (يدوي)\n📚 المادة: {sub_name}", reply_markup=markup(keyboard))


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return
    if not await is_admin(update.effective_user.id):
        return
    await update.message.reply_text("🛠 لوحة التحكم (Admin)", reply_markup=admin_panel_keyboard())


async def admin_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return
    await query.answer()
    global BOT_ENABLED
    uid = query.from_user.id
    if not await is_admin(uid):
        return

    if cleanup_expired_upload_session(context):
        await query.message.reply_text("⌛ انتهت جلسة الرفع. ابدأ من لوحة الأدمن مرة أخرى.", reply_markup=admin_panel_keyboard())
        return

    data = query.data or ""
    if data in ("a:panel",):
        await query.message.reply_text("🛠 لوحة التحكم (Admin)", reply_markup=admin_panel_keyboard())
        return

    if data == "a:add_subject":
        context.user_data["admin_mode"] = "add_subject"
        await query.message.reply_text("➕📚 اكتب اسم المادة الجديدة.", reply_markup=markup([admin_nav_row()]))
        return

    if data == "a:add_lecture":
        # Improved: choose subject, then upload documents (multi-upload session)
        context.user_data["admin_mode"] = "add_lecture_choose_subject"
        await _admin_choose_subject_for_upload(query, page=0, cb_base="a:add_lecture")
        return

    if data.startswith("a:add_lecture:p="):
        page = _parse_page(data)
        context.user_data["admin_mode"] = "add_lecture_choose_subject"
        await _admin_choose_subject_for_upload(query, page=page, cb_base="a:add_lecture")
        return

    if data.startswith("a:add_lecture:subject="):
        sid = _parse_int_param(data, "subject")
        if sid is None:
            return
        session = UploadSession(mode="add_lecture", subject_id=sid, created_at=int(time.time()))
        set_upload_session(context, session)
        # Create a single progress message that we keep editing
        try:
            m = await query.message.reply_text(
                "📦 رفع المحاضرات بدأ...\n✅ Saved: 0\n⚠️ Failed: 0\n\n📤 ارفع الملفات الآن (أي نوع).\nاضغط ✅ تم عند الانتهاء.",
                reply_markup=markup([[btn("✅ تم", "a:upload_done")], admin_nav_row()]),
            )
            session.progress_chat_id = m.chat_id
            session.progress_message_id = m.message_id
        except Exception:
            pass
        context.user_data["admin_mode"] = "uploading"
        return

    if data == "a:import_folder":
        context.user_data["admin_mode"] = "import_folder_ask_subject"
        await query.message.reply_text(
            "📥📁 Import folder\nاكتب اسم المادة وسيتم إنشاؤها تلقائيًا ثم ارفع ملفات متعددة.",
            reply_markup=markup([admin_nav_row()]),
        )
        return

    if data == "a:import_batch":
        context.user_data["admin_mode"] = "import_batch_choose_subject"
        await _admin_choose_subject_for_upload(query, page=0, cb_base="a:import_batch")
        return

    if data.startswith("a:import_batch:p="):
        page = _parse_page(data)
        context.user_data["admin_mode"] = "import_batch_choose_subject"
        await _admin_choose_subject_for_upload(query, page=page, cb_base="a:import_batch")
        return

    if data.startswith("a:import_batch:subject="):
        sid = _parse_int_param(data, "subject")
        if sid is None:
            return
        session = UploadSession(mode="import_batch", subject_id=sid, created_at=int(time.time()))
        set_upload_session(context, session)
        try:
            m = await query.message.reply_text(
                "📦 رفع batch بدأ...\n✅ Saved: 0\n⚠️ Failed: 0\n\n📤 ارفع الملفات الآن.\nاضغط ✅ تم عند الانتهاء.",
                reply_markup=markup([[btn("✅ تم", "a:upload_done")], admin_nav_row()]),
            )
            session.progress_chat_id = m.chat_id
            session.progress_message_id = m.message_id
        except Exception:
            pass
        context.user_data["admin_mode"] = "uploading"
        return

    if data == "a:upload_done":
        session = get_upload_session(context)
        counters = context.user_data.get("upload_counters") if isinstance(context.user_data.get("upload_counters"), dict) else {}
        set_upload_session(context, None)
        context.user_data.pop("admin_mode", None)
        summary = f"✅ تم إنهاء الرفع.\n✅ Saved: {int(counters.get('saved', 0))}\n⚠️ Failed: {int(counters.get('failed', 0))}"
        # Try to edit the single progress message if it exists
        if session and session.progress_chat_id and session.progress_message_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=session.progress_chat_id,
                    message_id=session.progress_message_id,
                    text=summary,
                    reply_markup=admin_panel_keyboard(),
                )
                return
            except Exception:
                pass
        await query.message.reply_text(summary, reply_markup=admin_panel_keyboard())
        return

    # ---- Delete subject ----
    if data == "a:delete_subject":
        context.user_data["admin_mode"] = "delete_subject_choose"
        await _admin_choose_subject_generic(query, page=0, cb_base="a:delete_subject")
        return
    if data.startswith("a:delete_subject:p="):
        await _admin_choose_subject_generic(query, page=_parse_page(data), cb_base="a:delete_subject")
        return
    if data.startswith("a:delete_subject:subject="):
        sid = _parse_int_param(data, "subject")
        if sid is None:
            return
        await query.message.reply_text(
            "⚠️ تأكيد حذف المادة؟ سيتم حذف كل محاضراتها أيضًا.",
            reply_markup=markup([[btn("🗑️ تأكيد الحذف", f"a:delete_subject_confirm:id={sid}")], admin_nav_row()]),
        )
        return
    if data.startswith("a:delete_subject_confirm:"):
        sid = _parse_int_param(data, "id")
        if sid is None:
            return
        async with db_connect() as conn:
            await conn.execute("DELETE FROM lectures WHERE subject_id=?", (sid,))
            await conn.execute("DELETE FROM subjects WHERE id=?", (sid,))
            await conn.commit()
        save_db()
        await query.message.reply_text("✅ تم حذف المادة.", reply_markup=admin_panel_keyboard())
        return

    # ---- Delete lecture ----
    if data == "a:delete_lecture":
        context.user_data["admin_mode"] = "delete_lecture_choose_subject"
        await _admin_choose_subject_generic(query, page=0, cb_base="a:delete_lecture")
        return
    if data.startswith("a:delete_lecture:p="):
        await _admin_choose_subject_generic(query, page=_parse_page(data), cb_base="a:delete_lecture")
        return
    if data.startswith("a:delete_lecture:subject="):
        sid = _parse_int_param(data, "subject")
        if sid is None:
            return
        await _admin_choose_lecture(query, subject_id=sid, page=0, cb_base=f"a:delete_lecture_list:s={sid}")
        return
    if data.startswith("a:delete_lecture_list:s="):
        sid = _parse_int_param(data, "s")
        page = _parse_page(data)
        if sid is None:
            return
        await _admin_choose_lecture(query, subject_id=sid, page=page, cb_base=f"a:delete_lecture_list:s={sid}")
        return
    if data.startswith("a:delete_lecture_pick:s="):
        sid = _parse_int_param(data, "s")
        lid = _parse_int_param(data, "id")
        if sid is None or lid is None:
            return
        await query.message.reply_text(
            "⚠️ تأكيد حذف المحاضرة؟",
            reply_markup=markup([[btn("🗑️ تأكيد الحذف", f"a:delete_lecture_confirm:s={sid}:id={lid}")], admin_nav_row()]),
        )
        return
    if data.startswith("a:delete_lecture_confirm:"):
        sid = _parse_int_param(data, "s")
        lid = _parse_int_param(data, "id")
        if sid is None or lid is None:
            return
        async with db_connect() as conn:
            await conn.execute("DELETE FROM favorites WHERE lecture_id=?", (lid,))
            await conn.execute("DELETE FROM lectures WHERE id=? AND subject_id=?", (lid, sid))
            await conn.commit()
        save_db()
        await query.message.reply_text("✅ تم حذف المحاضرة.", reply_markup=admin_panel_keyboard())
        return

    # ---- Edit subject name ----
    if data == "a:edit_subject":
        context.user_data["admin_mode"] = "edit_subject_choose"
        await _admin_choose_subject_generic(query, page=0, cb_base="a:edit_subject")
        return
    if data.startswith("a:edit_subject:p="):
        await _admin_choose_subject_generic(query, page=_parse_page(data), cb_base="a:edit_subject")
        return
    if data.startswith("a:edit_subject:subject="):
        sid = _parse_int_param(data, "subject")
        if sid is None:
            return
        context.user_data["admin_mode"] = "edit_subject_wait_name"
        context.user_data["edit_subject_id"] = sid
        await query.message.reply_text("✏️ اكتب الاسم الجديد للمادة.", reply_markup=markup([admin_nav_row()]))
        return

    # ---- Edit lecture title ----
    if data == "a:edit_lecture":
        context.user_data["admin_mode"] = "edit_lecture_choose_subject"
        await _admin_choose_subject_generic(query, page=0, cb_base="a:edit_lecture")
        return
    if data.startswith("a:edit_lecture:p="):
        await _admin_choose_subject_generic(query, page=_parse_page(data), cb_base="a:edit_lecture")
        return
    if data.startswith("a:edit_lecture:subject="):
        sid = _parse_int_param(data, "subject")
        if sid is None:
            return
        await _admin_choose_lecture(query, subject_id=sid, page=0, cb_base=f"a:edit_lecture_list:s={sid}")
        return
    if data.startswith("a:edit_lecture_list:s="):
        sid = _parse_int_param(data, "s")
        page = _parse_page(data)
        if sid is None:
            return
        await _admin_choose_lecture(query, subject_id=sid, page=page, cb_base=f"a:edit_lecture_list:s={sid}")
        return
    if data.startswith("a:edit_lecture_pick:s="):
        sid = _parse_int_param(data, "s")
        lid = _parse_int_param(data, "id")
        if sid is None or lid is None:
            return
        context.user_data["admin_mode"] = "edit_lecture_wait_title"
        context.user_data["edit_lecture_subject_id"] = sid
        context.user_data["edit_lecture_id"] = lid
        await query.message.reply_text("✏️ اكتب العنوان الجديد للمحاضرة.", reply_markup=markup([admin_nav_row()]))
        return

    # ---- Manage links ----
    if data == "a:manage_links":
        await _admin_show_links_manage(query, page=0)
        return
    if data.startswith("a:manage_links:p="):
        await _admin_show_links_manage(query, page=_parse_page(data))
        return
    if data == "a:links_reorder_init":
        await _ensure_links_positions()
        save_db()
        await query.message.reply_text("✅ تم تجهيز ترتيب الروابط.", reply_markup=admin_panel_keyboard())
        return
    if data.startswith("a:links_move:"):
        lid = _parse_int_param(data, "id")
        direction = "up" if "dir=up" in data else "down"
        page = _parse_page(data)
        if lid is not None:
            await _swap_link_position(lid, direction)
            save_db()
        await _admin_show_links_manage(query, page=page)
        return
    if data == "a:links_add":
        context.user_data["admin_mode"] = "links_add_title"
        await query.message.reply_text("➕ اكتب عنوان اللينك.", reply_markup=markup([admin_nav_row()]))
        return
    if data.startswith("a:links_del:id="):
        lid = _parse_int_param(data, "id")
        if lid is None:
            return
        async with db_connect() as conn:
            await conn.execute("DELETE FROM important_links WHERE id=?", (lid,))
            await conn.commit()
        save_db()
        await query.message.reply_text("✅ تم حذف اللينك.", reply_markup=admin_panel_keyboard())
        return

    # ---- Broadcast ----
    if data == "a:broadcast":
        context.user_data["admin_mode"] = "broadcast_text"
        await query.message.reply_text("📢 اكتب رسالة البرودكاست.", reply_markup=markup([admin_nav_row()]))
        return

    # ---- Clean data ----
    if data == "a:clean":
        async with db_connect() as conn:
            # remove favorites pointing to missing lectures
            await conn.execute(
                "DELETE FROM favorites WHERE lecture_id NOT IN (SELECT id FROM lectures)"
            )
            await conn.commit()
        save_db()
        await query.message.reply_text("🧹 تم تنظيف البيانات الأساسية.", reply_markup=admin_panel_keyboard())
        return

    # ---- Manage admins ----
    if data == "a:manage_admins":
        await _admin_show_admins(query)
        return
    if data == "a:admins_add":
        context.user_data["admin_mode"] = "admins_add_user_id"
        await query.message.reply_text("👮 اكتب User ID لإضافته كأدمن.", reply_markup=markup([admin_nav_row()]))
        return
    if data.startswith("a:admins_del:id="):
        aid = _parse_int_param(data, "id")
        if aid is None:
            return
        if aid == MAIN_ADMIN_ID:
            await query.message.reply_text("⚠️ لا يمكن حذف الأدمن الرئيسي.", reply_markup=admin_panel_keyboard())
            return
        async with db_connect() as conn:
            await conn.execute("DELETE FROM admins WHERE user_id=?", (aid,))
            await conn.commit()
        save_db()
        await query.message.reply_text("✅ تم حذف الأدمن.", reply_markup=admin_panel_keyboard())
        return
    if data == "a:user_search":
        context.user_data["admin_mode"] = "user_search"
        await query.message.reply_text("🔎 اكتب User ID أو username للبحث.", reply_markup=markup([admin_nav_row()]))
        return

    if data == "a:backup":
        save_db()
        await query.message.reply_text("✅ تم عمل نسخة احتياطية للقاعدة.", reply_markup=admin_panel_keyboard())
        return

    if data == "a:stats":
        async with db_connect() as conn:
            async with conn.execute("SELECT COUNT(*) AS c FROM users") as cur1:
                users_c = int((await cur1.fetchone())["c"])
            async with conn.execute("SELECT COUNT(*) AS c FROM subjects") as cur2:
                sub_c = int((await cur2.fetchone())["c"])
            async with conn.execute("SELECT COUNT(*) AS c FROM lectures") as cur3:
                lec_c = int((await cur3.fetchone())["c"])
            async with conn.execute("SELECT COUNT(*) AS c FROM uploads") as cur4:
                up_c = int((await cur4.fetchone())["c"])
        await query.message.reply_text(
            "📊 Statistics\n"
            f"👥 Users: {users_c}\n"
            f"📚 Subjects: {sub_c}\n"
            f"📄 Lectures: {lec_c}\n"
            f"📤 Uploads: {up_c}",
            reply_markup=admin_panel_keyboard(),
        )
        return

    if data == "a:stop":
        BOT_ENABLED = False
        await query.message.reply_text("⛔ تم إيقاف البوت للمستخدمين (وضع صيانة).", reply_markup=admin_panel_keyboard())
        return

    if data == "a:start":
        BOT_ENABLED = True
        await query.message.reply_text("✅ تم تشغيل البوت للمستخدمين.", reply_markup=admin_panel_keyboard())
        return

    # ---- Sorting (manual reorder) ----
    if data.startswith("a:sort_subjects"):
        page = _parse_page(data)
        await _admin_show_sort_subjects(query, page=page)
        return

    if data.startswith("a:sort_subjects:move:"):
        sid = _parse_int_param(data, "id")
        direction = "up" if "dir=up" in data else "down"
        page = _parse_page(data)
        if sid is not None:
            await _swap_subject_order(sid, direction)
            save_db()
        await _admin_show_sort_subjects(query, page=page)
        return

    if data.startswith("a:sort_lectures:subject="):
        sid = _parse_int_param(data, "subject")
        page = _parse_page(data)
        if sid is None:
            return
        await _admin_show_sort_lectures(query, subject_id=sid, page=page)
        return

    if data.startswith("a:sort_lectures:move:"):
        sid = _parse_int_param(data, "s")
        lid = _parse_int_param(data, "id")
        direction = "up" if "dir=up" in data else "down"
        page = _parse_page(data)
        if sid is not None and lid is not None:
            await _swap_lecture_order(sid, lid, direction)
            save_db()
        await _admin_show_sort_lectures(query, subject_id=sid or 0, page=page)
        return

    # required buttons exist; wiring/logic will be implemented in next steps
    await query.message.reply_text("⏳ قيد التطوير الآن…", reply_markup=markup([admin_nav_row()]))


async def admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    uid = update.effective_user.id
    if not await is_admin(uid):
        return
    cleanup_expired_upload_session(context)
    mode = context.user_data.get("admin_mode")
    text = (update.message.text or "").strip()
    if not text:
        return

    if mode == "add_subject":
        now = int(time.time())
        async with db_connect() as conn:
            try:
                await conn.execute(
                    "INSERT INTO subjects(name, created_at) VALUES(?, ?)",
                    (text, now),
                )
                await conn.commit()
            except Exception:
                await update.message.reply_text("⚠️ لم أستطع إضافة المادة (قد تكون موجودة بالفعل).")
                return
        save_db()
        context.user_data.pop("admin_mode", None)
        await update.message.reply_text("✅ تم إضافة المادة.", reply_markup=admin_panel_keyboard())
        return

    if mode == "import_folder_ask_subject":
        subject_name = text
        now = int(time.time())
        async with db_connect() as conn:
            # create subject if missing
            await conn.execute(
                "INSERT OR IGNORE INTO subjects(name, created_at) VALUES(?, ?)",
                (subject_name, now),
            )
            await conn.commit()
            async with conn.execute("SELECT id FROM subjects WHERE name=?", (subject_name,)) as cur:
                row = await cur.fetchone()
        if not row:
            await update.message.reply_text("⚠️ لم أستطع إنشاء المادة.", reply_markup=admin_panel_keyboard())
            return
        sid = int(row["id"])
        await _ensure_manual_order_subjects()
        session = UploadSession(mode="import_folder", subject_id=sid, created_at=int(time.time()))
        set_upload_session(context, session)
        context.user_data["admin_mode"] = "uploading"
        save_db()
        try:
            m = await update.message.reply_text(
                f"✅ تم تجهيز المادة: {subject_name}\n\n📦 رفع الملفات بدأ...\n✅ Saved: 0\n⚠️ Failed: 0\n\n📤 ارفع الملفات الآن.\nاضغط ✅ تم عند الانتهاء.",
                reply_markup=markup([[btn("✅ تم", "a:upload_done")], admin_nav_row()]),
            )
            session.progress_chat_id = m.chat_id
            session.progress_message_id = m.message_id
        except Exception:
            await update.message.reply_text(
                f"✅ تم تجهيز المادة: {subject_name}\n📤 ارفع الملفات الآن.\nاضغط ✅ تم عند الانتهاء.",
                reply_markup=markup([[btn("✅ تم", "a:upload_done")], admin_nav_row()]),
            )
        return

    if mode == "edit_subject_wait_name":
        sid = context.user_data.get("edit_subject_id")
        if not isinstance(sid, int):
            return
        async with db_connect() as conn:
            try:
                await conn.execute("UPDATE subjects SET name=? WHERE id=?", (text, sid))
                await conn.commit()
            except Exception:
                await update.message.reply_text("⚠️ لم أستطع تعديل الاسم (قد يكون موجود).")
                return
        save_db()
        context.user_data.pop("admin_mode", None)
        context.user_data.pop("edit_subject_id", None)
        await update.message.reply_text("✅ تم تعديل اسم المادة.", reply_markup=admin_panel_keyboard())
        return

    if mode == "edit_lecture_wait_title":
        sid = context.user_data.get("edit_lecture_subject_id")
        lid = context.user_data.get("edit_lecture_id")
        if not isinstance(sid, int) or not isinstance(lid, int):
            return
        # Duplicate protection (per-subject) for edited titles
        async with db_connect() as conn:
            async with conn.execute(
                "SELECT 1 FROM lectures WHERE subject_id=? AND title=? AND id<>?",
                (sid, text, lid),
            ) as cur:
                exists = await cur.fetchone()
            new_title = text
            if exists:
                base, ext = _split_filename(text)
                i = 2
                while True:
                    cand = f"{base} ({i}){ext}"
                    async with conn.execute(
                        "SELECT 1 FROM lectures WHERE subject_id=? AND title=?",
                        (sid, cand),
                    ) as cur2:
                        if not await cur2.fetchone():
                            new_title = cand
                            break
                    i += 1
            await conn.execute(
                "UPDATE lectures SET title=? WHERE id=? AND subject_id=?",
                (new_title, lid, sid),
            )
            await conn.commit()
        save_db()
        context.user_data.pop("admin_mode", None)
        context.user_data.pop("edit_lecture_subject_id", None)
        context.user_data.pop("edit_lecture_id", None)
        await update.message.reply_text("✅ تم تعديل عنوان المحاضرة.", reply_markup=admin_panel_keyboard())
        return

    if mode == "links_add_title":
        context.user_data["links_new_title"] = text
        context.user_data["admin_mode"] = "links_add_url"
        await update.message.reply_text("🔗 اكتب رابط URL.", reply_markup=markup([admin_nav_row()]))
        return

    if mode == "links_add_url":
        title = context.user_data.get("links_new_title")
        url = text
        if not isinstance(title, str) or not url:
            return
        async with db_connect() as conn:
            await conn.execute(
                "INSERT INTO important_links(title, url, position) VALUES(?,?,?)",
                (title, url, 999),
            )
            await conn.commit()
        save_db()
        context.user_data.pop("admin_mode", None)
        context.user_data.pop("links_new_title", None)
        await update.message.reply_text("✅ تم إضافة اللينك.", reply_markup=admin_panel_keyboard())
        return

    if mode == "broadcast_text":
        msg = text
        async with db_connect() as conn:
            async with conn.execute("SELECT user_id FROM users") as cur:
                users = [int(r["user_id"]) for r in await cur.fetchall()]

        progress = await update.message.reply_text("⏳ جاري إرسال البرودكاست...", reply_markup=admin_panel_keyboard())

        sent = 0
        failed = 0

        sem = asyncio.Semaphore(20)

        async def _send_one(chat_id: int) -> None:
            nonlocal sent, failed
            async with sem:
                for attempt in range(4):
                    try:
                        await context.bot.send_message(chat_id=chat_id, text=msg)
                        sent += 1
                        return
                    except RetryAfter as e:
                        await asyncio.sleep(float(getattr(e, "retry_after", 1.5)) + 0.2)
                    except (TimedOut, NetworkError):
                        await asyncio.sleep(0.6 * (attempt + 1))
                    except Exception:
                        failed += 1
                        return

        batch_size = 200
        for i in range(0, len(users), batch_size):
            batch = users[i : i + batch_size]
            await asyncio.gather(*[_send_one(uid) for uid in batch])
            try:
                await progress.edit_text(
                    f"📢 Broadcast progress\n📨 Sent: {sent}\n⚠️ Failed: {failed}\n👥 Total: {len(users)}"
                )
            except Exception:
                pass
            await asyncio.sleep(0.2)

        await update.message.reply_text(
            f"✅ Broadcast done.\n📨 Sent: {sent}\n⚠️ Failed: {failed}\n👥 Total: {len(users)}",
            reply_markup=admin_panel_keyboard(),
        )
        context.user_data.pop("admin_mode", None)
        return

    if mode == "admins_add_user_id":
        try:
            new_id = int(re.sub(r"\\D+", "", text))
        except Exception:
            await update.message.reply_text("⚠️ اكتب رقم User ID صحيح.")
            return
        if new_id == MAIN_ADMIN_ID:
            await update.message.reply_text("✅ الأدمن الرئيسي موجود بالفعل.", reply_markup=admin_panel_keyboard())
            context.user_data.pop("admin_mode", None)
            return
        async with db_connect() as conn:
            await conn.execute("INSERT OR IGNORE INTO admins(user_id) VALUES(?)", (new_id,))
            await conn.commit()
        save_db()
        context.user_data.pop("admin_mode", None)
        await update.message.reply_text("✅ تم إضافة الأدمن.", reply_markup=admin_panel_keyboard())
        return

    if mode == "user_search":
        q = text.strip().lstrip("@")
        if not q:
            return
        async with db_connect() as conn:
            if q.isdigit():
                async with conn.execute(
                    "SELECT user_id, username, first_name, last_name FROM users WHERE user_id=?",
                    (int(q),),
                ) as cur:
                    rows = await cur.fetchall()
            else:
                like = f"%{q}%"
                async with conn.execute(
                    """
                    SELECT user_id, username, first_name, last_name
                    FROM users
                    WHERE COALESCE(username,'') LIKE ? COLLATE NOCASE
                       OR COALESCE(first_name,'') LIKE ? COLLATE NOCASE
                       OR COALESCE(last_name,'') LIKE ? COLLATE NOCASE
                    LIMIT 20
                    """,
                    (like, like, like),
                ) as cur:
                    rows = await cur.fetchall()
        if not rows:
            await update.message.reply_text("🔎 لا توجد نتائج.", reply_markup=admin_panel_keyboard())
        else:
            lines = []
            for r in rows:
                lines.append(
                    f"👤 {int(r['user_id'])} | @{r['username'] or '-'} | {r['first_name'] or ''} {r['last_name'] or ''}".strip()
                )
            await update.message.reply_text("🔎 نتائج البحث:\n" + "\n".join(lines), reply_markup=admin_panel_keyboard())
        context.user_data.pop("admin_mode", None)
        return


async def _admin_choose_subject_for_upload(query, page: int, cb_base: str) -> None:
    PAGE_SIZE = 12
    offset = page * PAGE_SIZE
    loading_msg = None
    try:
        if LIST_LOADING_EDIT:
            loading_msg = await query.message.reply_text("⏳ جاري تحميل المواد...")
    except Exception:
        loading_msg = None
    async with db_connect() as conn:
        async with conn.execute(
            """
            SELECT id, name
            FROM subjects
            ORDER BY COALESCE(manual_order, 999999999), created_at, id
            LIMIT ? OFFSET ?
            """,
            (PAGE_SIZE + 1, offset),
        ) as cur:
            rows = await cur.fetchall()
    if not rows:
        if loading_msg:
            try:
                await loading_msg.edit_text("📌 لا توجد مواد بعد.")
            except Exception:
                await query.message.reply_text("📌 لا توجد مواد بعد.", reply_markup=admin_panel_keyboard())
        else:
            await query.message.reply_text("📌 لا توجد مواد بعد.", reply_markup=admin_panel_keyboard())
        return
    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    keyboard: list[list[InlineKeyboardButton]] = []
    buttons = [btn(f"📚 {r['name']}", f"{cb_base}:subject={int(r['id'])}") for r in rows]
    keyboard.extend(grid_2col(buttons))
    keyboard.append(pagination_row(base_cb=cb_base, page=page, has_prev=page > 0, has_next=has_next))
    keyboard.append(admin_nav_row())
    if loading_msg:
        try:
            await loading_msg.edit_text("📚 اختر المادة:", reply_markup=markup(keyboard))
        except Exception:
            await query.message.reply_text("📚 اختر المادة:", reply_markup=markup(keyboard))
    else:
        await query.message.reply_text("📚 اختر المادة:", reply_markup=markup(keyboard))


async def _admin_choose_subject_generic(query, page: int, cb_base: str) -> None:
    await _admin_choose_subject_for_upload(query, page=page, cb_base=cb_base)


async def _admin_choose_lecture(query, subject_id: int, page: int, cb_base: str) -> None:
    PAGE_SIZE = 12
    offset = page * PAGE_SIZE
    loading_msg = None
    try:
        if LIST_LOADING_EDIT:
            loading_msg = await query.message.reply_text("⏳ جاري تحميل المحاضرات...")
    except Exception:
        loading_msg = None
    async with db_connect() as conn:
        async with conn.execute(
            """
            SELECT id, title
            FROM lectures
            WHERE subject_id=?
            ORDER BY COALESCE(manual_order, 999999999), created_at, id
            LIMIT ? OFFSET ?
            """,
            (subject_id, PAGE_SIZE + 1, offset),
        ) as cur:
            rows = await cur.fetchall()
    if not rows:
        if loading_msg:
            try:
                await loading_msg.edit_text("📌 لا توجد محاضرات.")
            except Exception:
                await query.message.reply_text("📌 لا توجد محاضرات.", reply_markup=admin_panel_keyboard())
        else:
            await query.message.reply_text("📌 لا توجد محاضرات.", reply_markup=admin_panel_keyboard())
        return
    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    keyboard: list[list[InlineKeyboardButton]] = []
    buttons = [btn(f"📄 {r['title']}", f"{cb_base.replace('_list', '_pick')}:id={int(r['id'])}") for r in rows]
    # cb_base like "a:delete_lecture_list:s=123"
    keyboard.extend(grid_2col(buttons))
    keyboard.append(pagination_row(base_cb=cb_base, page=page, has_prev=page > 0, has_next=has_next))
    keyboard.append(admin_nav_row())
    if loading_msg:
        try:
            await loading_msg.edit_text("📄 اختر المحاضرة:", reply_markup=markup(keyboard))
        except Exception:
            await query.message.reply_text("📄 اختر المحاضرة:", reply_markup=markup(keyboard))
    else:
        await query.message.reply_text("📄 اختر المحاضرة:", reply_markup=markup(keyboard))


async def _admin_show_links_manage(query, page: int) -> None:
    PAGE_SIZE = 10
    offset = page * PAGE_SIZE
    loading_msg = None
    try:
        if LIST_LOADING_EDIT:
            loading_msg = await query.message.reply_text("⏳ جاري تحميل الروابط...")
    except Exception:
        loading_msg = None
    async with db_connect() as conn:
        async with conn.execute(
            """
            SELECT id, title, url
            FROM important_links
            ORDER BY position, id
            LIMIT ? OFFSET ?
            """,
            (PAGE_SIZE + 1, offset),
        ) as cur:
            rows = await cur.fetchall()
    if not rows:
        text = "🔗 Manage links\nلا توجد لينكات بعد."
        if loading_msg:
            try:
                await loading_msg.edit_text(text, reply_markup=markup([[btn("➕ إضافة لينك", "a:links_add")], admin_nav_row()]))
            except Exception:
                await query.message.reply_text(
                    text,
                    reply_markup=markup([[btn("➕ إضافة لينك", "a:links_add")], admin_nav_row()]),
                )
        else:
            await query.message.reply_text(
                text,
                reply_markup=markup([[btn("➕ إضافة لينك", "a:links_add")], admin_nav_row()]),
            )
        return
    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    keyboard: list[list[InlineKeyboardButton]] = []
    keyboard.append([btn("➕ إضافة لينك", "a:links_add")])
    keyboard.append([btn("↕️ تفعيل الترتيب اليدوي", "a:links_reorder_init")])
    for r in rows:
        link_id = int(r["id"])
        keyboard.append(
            [
                btn("⬆️", f"a:links_move:id={link_id}:dir=up:p={page}"),
                url_btn(f"🔗 {r['title']}", r["url"]),
                btn("⬇️", f"a:links_move:id={link_id}:dir=down:p={page}"),
            ]
        )
        keyboard.append([btn("🗑️ حذف", f"a:links_del:id={link_id}")])
    keyboard.append(pagination_row(base_cb="a:manage_links", page=page, has_prev=page > 0, has_next=has_next))
    keyboard.append(admin_nav_row())
    if loading_msg:
        try:
            await loading_msg.edit_text("🔗 Manage links:", reply_markup=markup(keyboard))
        except Exception:
            await query.message.reply_text("🔗 Manage links:", reply_markup=markup(keyboard))
    else:
        await query.message.reply_text("🔗 Manage links:", reply_markup=markup(keyboard))


async def _ensure_links_positions() -> None:
    async with db_connect() as conn:
        async with conn.execute(
            "SELECT id FROM important_links ORDER BY position, id"
        ) as cur:
            rows = await cur.fetchall()
        if not rows:
            return
        # Normalize positions to 1..n
        for i, r in enumerate(rows, start=1):
            await conn.execute("UPDATE important_links SET position=? WHERE id=?", (i, int(r["id"])))
        await conn.commit()


async def _swap_link_position(link_id: int, direction: str) -> None:
    await _ensure_links_positions()
    async with db_connect() as conn:
        async with conn.execute(
            "SELECT id, position FROM important_links ORDER BY position, id"
        ) as cur:
            rows = await cur.fetchall()
        ids = [int(r["id"]) for r in rows]
        if link_id not in ids:
            return
        idx = ids.index(link_id)
        if direction == "up" and idx == 0:
            return
        if direction == "down" and idx == len(ids) - 1:
            return
        swap_idx = idx - 1 if direction == "up" else idx + 1
        a_id = ids[idx]
        b_id = ids[swap_idx]
        a_pos = int(rows[idx]["position"] or idx + 1)
        b_pos = int(rows[swap_idx]["position"] or swap_idx + 1)
        await conn.execute("BEGIN")
        await conn.execute("UPDATE important_links SET position=? WHERE id=?", (b_pos, a_id))
        await conn.execute("UPDATE important_links SET position=? WHERE id=?", (a_pos, b_id))
        await conn.commit()


async def _admin_show_admins(query) -> None:
    async with db_connect() as conn:
        async with conn.execute("SELECT user_id FROM admins ORDER BY user_id") as cur:
            rows = await cur.fetchall()
    buttons: list[list[InlineKeyboardButton]] = []
    buttons.append([btn("➕ إضافة أدمن", "a:admins_add"), btn("🔎 بحث مستخدمين", "a:user_search")])
    buttons.append([btn(f"👑 MAIN: {MAIN_ADMIN_ID}", "noop")])
    for r in rows:
        uid = int(r["user_id"])
        buttons.append([btn(f"👮 {uid}", "noop"), btn("🗑️", f"a:admins_del:id={uid}")])
    buttons.append(admin_nav_row())
    await query.message.reply_text("👮 Manage admins:", reply_markup=markup(buttons))


def _split_filename(name: str) -> tuple[str, str]:
    name = (name or "").strip()
    if not name:
        return ("ملف", "")
    if "." not in name:
        return (name, "")
    base, ext = name.rsplit(".", 1)
    return (base, "." + ext)


async def _dedupe_lecture_title(subject_id: int, title: str) -> str:
    base, ext = _split_filename(title)
    candidate = f"{base}{ext}"
    async with db_connect() as conn:
        async with conn.execute(
            "SELECT 1 FROM lectures WHERE subject_id=? AND title=?",
            (subject_id, candidate),
        ) as cur:
            exists = await cur.fetchone()
        if not exists:
            return candidate
        i = 2
        while True:
            candidate = f"{base} ({i}){ext}"
            async with conn.execute(
                "SELECT 1 FROM lectures WHERE subject_id=? AND title=?",
                (subject_id, candidate),
            ) as cur2:
                exists2 = await cur2.fetchone()
            if not exists2:
                return candidate
            i += 1


async def admin_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    uid = update.effective_user.id
    if not await is_admin(uid):
        return
    session = get_upload_session(context)
    if not session or not session.subject_id:
        # If admin is uploading but session expired, guide them
        if update.message and context.user_data.get("admin_mode") == "uploading":
            await update.message.reply_text("⌛ انتهت جلسة الرفع. افتح الأمر مرة أخرى من لوحة الأدمن.")
            context.user_data.pop("admin_mode", None)
        return
    session.last_activity = int(time.time())

    doc = update.message.document
    if not doc:
        return
    file_id = doc.file_id
    raw_title = (doc.file_name or "ملف").strip()

    # Progress counters per upload session/admin
    counters = context.user_data.setdefault("upload_counters", {"saved": 0, "failed": 0, "last_notice": 0.0})
    if not isinstance(counters, dict):
        counters = {"saved": 0, "failed": 0, "last_notice": 0.0}
        context.user_data["upload_counters"] = counters

    now = int(time.time())
    async with db_connect() as conn:
        try:
            # De-dupe title within the same transaction/connection
            base, ext = _split_filename(raw_title)
            title = f"{base}{ext}"
            async with conn.execute(
                "SELECT 1 FROM lectures WHERE subject_id=? AND title=?",
                (session.subject_id, title),
            ) as cur0:
                if await cur0.fetchone():
                    i = 2
                    while True:
                        cand = f"{base} ({i}){ext}"
                        async with conn.execute(
                            "SELECT 1 FROM lectures WHERE subject_id=? AND title=?",
                            (session.subject_id, cand),
                        ) as cur1:
                            if not await cur1.fetchone():
                                title = cand
                                break
                        i += 1

            cur = await conn.execute(
                "INSERT INTO lectures(subject_id, title, file_id, created_at) VALUES(?,?,?,?)",
                (session.subject_id, title, file_id, now),
            )
            lecture_id = int(cur.lastrowid)
            await conn.execute(
                "INSERT INTO uploads(admin_id, lecture_id, created_at) VALUES(?,?,?)",
                (uid, lecture_id, now),
            )
            await conn.commit()
        except Exception:
            counters["failed"] = int(counters.get("failed", 0)) + 1
            await update.message.reply_text("⚠️ حصل خطأ أثناء حفظ الملف، حاول مرة أخرى.")
            return

    counters["saved"] = int(counters.get("saved", 0)) + 1
    save_db()

    # Single progress message (edited), no spam per file
    now_ts = time.time()
    should_edit = (now_ts - float(session.last_progress_edit_ts or 0.0)) >= UPLOAD_PROGRESS_EDIT_EVERY_S
    if should_edit and session.progress_chat_id and session.progress_message_id:
        session.last_progress_edit_ts = now_ts
        try:
            await context.bot.edit_message_text(
                chat_id=session.progress_chat_id,
                message_id=session.progress_message_id,
                text=(
                    "📦 جاري الرفع...\n"
                    f"✅ Saved: {int(counters.get('saved', 0))}\n"
                    f"⚠️ Failed: {int(counters.get('failed', 0))}\n\n"
                    "📤 ارفع المزيد من الملفات، أو اضغط ✅ تم."
                ),
                reply_markup=markup([[btn("✅ تم", "a:upload_done")], admin_nav_row()]),
            )
        except Exception:
            pass


# =======================
# GLOBAL / MAINTENANCE + NOOP + ERRORS


async def noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.callback_query:
        await update.callback_query.answer()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOG.exception("Unhandled error", exc_info=context.error)


async def maintenance_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # This is a handler group guard via MessageHandler/CallbackQueryHandler is complex in PTB,
    # so we enforce in main entrypoints for now. (Will be hardened in security todo.)
    return


async def _job_backup(context: ContextTypes.DEFAULT_TYPE) -> None:
    save_db()


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Single TEXT handler to avoid PTB short-circuiting between admin_text/user_text.
    if not update.message or not update.effective_user:
        return
    uid = update.effective_user.id

    limiter: RateLimiter = context.application.bot_data["limiter"]
    if not limiter.check(uid):
        await update.message.reply_text(RATE_LIMIT_MESSAGE)
        return

    if not BOT_ENABLED and not await is_admin(uid):
        await update.message.reply_text(MAINTENANCE_MESSAGE)
        return

    # Cleanup possible expired sessions (no-op if none)
    cleanup_expired_upload_session(context)

    if await is_admin(uid) and context.user_data.get("admin_mode"):
        await admin_text(update, context)
        return

    await user_text(update, context)


# =======================
# APP BOOTSTRAP


def main() -> None:
    logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    application = (
    Application.builder()
    .token(TOKEN)
    .post_init(_post_init)
    .build()
)

    # shared runtime state
    application.bot_data["limiter"] = RateLimiter()

    # errors
    application.add_error_handler(error_handler)

    # commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel))

    # callbacks
    application.add_handler(CallbackQueryHandler(noop_callback, pattern="^noop$"))
    application.add_handler(CallbackQueryHandler(user_callbacks, pattern=r"^u:"))
    application.add_handler(CallbackQueryHandler(admin_callbacks, pattern=r"^a:"))
    application.add_handler(CallbackQueryHandler(user_callbacks, pattern=r"^nav:"))

    # messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    application.add_handler(MessageHandler(filters.Document.ALL, admin_document))

    async def _post_init(app: Application) -> None:
        await init_db()
        app.job_queue.run_repeating(_job_backup, interval=60 * 30, first=60 * 5)

    print("BOT STARTED")
    LOG.info("Starting polling...")
    application.run_polling()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        LOG.exception("Startup failed")
        raise
