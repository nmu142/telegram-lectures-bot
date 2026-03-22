"""
Production Telegram lecture management bot — full admin, search, favorites, pagination,
broadcast (all media), bulk import, backups, maintenance mode.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from telegram import (
    BotCommand,
    InputFile,
    MenuButtonCommands,
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    AIORateLimiter,
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

TOKEN = os.getenv("TOKEN")
MAIN_ADMIN_ID = int(os.getenv("MAIN_ADMIN_ID", "8377544927"))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "El8awy116")

DB_FILE = os.getenv("DB_FILE", "lectures.db")
BACKUP_FILE = os.getenv("BACKUP_FILE", "backup.db")
BACKUPS_DIR = os.getenv("BACKUPS_DIR", "backups")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

IMPORT_BATCH_COMMIT_EVERY = int(os.getenv("IMPORT_BATCH_COMMIT_EVERY", "5"))
BROADCAST_DELAY = float(os.getenv("BROADCAST_DELAY", "0.035"))
AUTO_BACKUP_HOURS = int(os.getenv("AUTO_BACKUP_HOURS", "24"))
LECTURES_PER_PAGE = 10
LATEST_COUNT = 10
CACHE_TTL_SEC = 45

GITHUB_BACKUP_TOKEN = os.getenv("GITHUB_BACKUP_TOKEN", "")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("lectures_bot")

# -----------------------------------------------------------------------------
# Maintenance
# -----------------------------------------------------------------------------

BOT_ENABLED = True

MAINTENANCE_MESSAGE = (
    "🚧 تم إيقاف البوت مؤقتًا لوجود تحديثات\n"
    "وسيتم العودة في أسرع وقت ❤️"
)

RATE_LIMIT_WINDOW = 10
RATE_LIMIT_MAX_MESSAGES = 6
RATE_LIMIT_BLOCK_SECONDS = 10
RATE_LIMIT_MESSAGE = (
    "🚫 برجاء الانتظار قليلًا قبل إرسال رسائل جديدة حتى لا يتعطل البوت."
)

user_messages: dict[int, list[float]] = {}
blocked_users: dict[int, float] = {}

# Simple cache: (expires_at, value)
_cache_subjects_non_empty: Optional[tuple[float, list[tuple[int, str, int]]]] = None
# Lecture list pages: key (subject_id, page) -> (expires_at, (total, pages, page, rows))
_cache_lecture_pages: dict[tuple[int, int], tuple[float, tuple[int, int, int, list]]] = {}


def cache_invalidate() -> None:
    global _cache_subjects_non_empty, _cache_lecture_pages
    _cache_subjects_non_empty = None
    _cache_lecture_pages.clear()


# -----------------------------------------------------------------------------
# Title / file helpers
# -----------------------------------------------------------------------------

_HASH_SUFFIX = re.compile(r"_[a-fA-F0-9]{32}$")
_HASH_SUFFIX_LONG = re.compile(r"_[a-fA-F0-9]{8,}$")
# Trailing UUID (with or without underscores)
_UUID_TAIL = re.compile(
    r"[_-]?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def extract_lecture_title_from_filename(filename: str) -> str:
    """Title = basename without extension; strip hashes / UUID / long hex suffixes."""
    base = os.path.splitext(os.path.basename(filename))[0]
    base = _UUID_TAIL.sub("", base)
    base = _HASH_SUFFIX.sub("", base)
    base = _HASH_SUFFIX_LONG.sub("", base)
    return base.strip()


IMPORT_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".ppt",
        ".pptx",
        ".doc",
        ".docx",
        ".zip",
        ".rar",
        ".png",
        ".jpg",
        ".jpeg",
        ".mp4",
        ".mp3",
    }
)


def file_allowed_for_import(name: str) -> bool:
    low = name.lower()
    return any(low.endswith(ext) for ext in IMPORT_EXTENSIONS)


# -----------------------------------------------------------------------------
# Backup
# -----------------------------------------------------------------------------


def ensure_backups_dir() -> None:
    os.makedirs(BACKUPS_DIR, exist_ok=True)


def save_db_mirror() -> None:
    try:
        if os.path.exists(DB_FILE):
            shutil.copy2(DB_FILE, BACKUP_FILE)
    except OSError as e:
        logger.warning("save_db_mirror: %s", e)


def restore_db_if_missing() -> None:
    try:
        if not os.path.exists(DB_FILE) and os.path.exists(BACKUP_FILE):
            shutil.copy2(BACKUP_FILE, DB_FILE)
            logger.info("Restored DB from backup.db")
    except OSError as e:
        logger.warning("restore_db_if_missing: %s", e)


def admin_backup_timestamped() -> str:
    ensure_backups_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUPS_DIR, f"lectures_{ts}.db")
    shutil.copy2(DB_FILE, dest)
    shutil.copy2(DB_FILE, BACKUP_FILE)
    return dest


def commit_and_backup(conn: sqlite3.Connection) -> None:
    conn.commit()
    save_db_mirror()


async def send_db_to_admins(bot) -> None:
    if not os.path.exists(DB_FILE):
        return
    for aid in get_all_admins():
        try:
            with open(DB_FILE, "rb") as f:
                await bot.send_document(
                    chat_id=aid,
                    document=InputFile(f, filename="lectures.db"),
                    caption="📦 نسخة احتياطية تلقائية",
                )
        except Exception as e:
            logger.warning("Telegram backup to %s: %s", aid, e)


def optional_github_backup() -> None:
    if not GITHUB_BACKUP_TOKEN:
        return
    logger.info("GitHub backup: token set — implement push to your repo/gist if needed.")


# -----------------------------------------------------------------------------
# Database
# -----------------------------------------------------------------------------

_db_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def get_connection() -> sqlite3.Connection:
    global _conn
    with _db_lock:
        if _conn is None:
            _conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA foreign_keys = ON")
            _conn.execute("PRAGMA journal_mode = WAL")
        return _conn


class DbBatch:
    def __init__(self, n: int = IMPORT_BATCH_COMMIT_EVERY) -> None:
        self.n = max(1, n)
        self._c = 0

    def step(self, conn: sqlite3.Connection, force: bool = False) -> None:
        self._c += 1
        if force or self._c >= self.n:
            commit_and_backup(conn)
            self._c = 0


def _migrate(conn: sqlite3.Connection) -> None:
    c = conn.cursor()
    c.execute("PRAGMA table_info(subjects)")
    sub_cols = {row[1] for row in c.fetchall()}
    c.execute("PRAGMA table_info(lectures)")
    lec_cols = {row[1] for row in c.fetchall()}
    if "sort_order" not in sub_cols:
        c.execute("ALTER TABLE subjects ADD COLUMN sort_order INTEGER DEFAULT 0")
        logger.info("Migration: subjects.sort_order")
    if "download_count" not in lec_cols:
        c.execute("ALTER TABLE lectures ADD COLUMN download_count INTEGER DEFAULT 0")
        logger.info("Migration: lectures.download_count")
    if "created_at" not in lec_cols:
        c.execute("ALTER TABLE lectures ADD COLUMN created_at TEXT")
        logger.info("Migration: lectures.created_at")
    if "content_type" not in lec_cols:
        c.execute(
            "ALTER TABLE lectures ADD COLUMN content_type TEXT DEFAULT 'document'"
        )
        logger.info("Migration: lectures.content_type")


def init_db() -> None:
    restore_db_if_missing()
    conn = get_connection()
    c = conn.cursor()

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS lectures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            file_id TEXT NOT NULL,
            content_type TEXT NOT NULL DEFAULT 'document',
            download_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT,
            FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE CASCADE
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS important_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 999
        )
        """
    )
    c.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)")
    c.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER NOT NULL,
            lecture_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, lecture_id),
            FOREIGN KEY(lecture_id) REFERENCES lectures(id) ON DELETE CASCADE
        )
        """
    )

    conn.commit()
    _migrate(conn)
    c = conn.cursor()
    c.execute("UPDATE subjects SET sort_order = id WHERE sort_order IS NULL")
    c.execute("CREATE INDEX IF NOT EXISTS idx_lecture_title ON lectures(title)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_subject_name ON subjects(name)")
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_lectures_subject_id ON lectures(subject_id)"
    )
    conn.commit()
    save_db_mirror()

    if not os.path.exists("admin_log.txt"):
        open("admin_log.txt", "a", encoding="utf-8").close()


def log_admin_action(user_id: int, action: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open("admin_log.txt", "a", encoding="utf-8") as f:
            f.write(f"[{now}] Admin({user_id}) -> {action}\n")
    except OSError:
        pass


# --- Admins ---


def get_extra_admins() -> list[int]:
    c = get_connection().cursor()
    c.execute("SELECT user_id FROM admins")
    return [r[0] for r in c.fetchall()]


def get_all_admins() -> list[int]:
    return list({MAIN_ADMIN_ID, *get_extra_admins()})


def is_main_admin(uid: int) -> bool:
    return uid == MAIN_ADMIN_ID


def is_admin(uid: int) -> bool:
    return uid in get_all_admins()


def add_admin_user(user_id: int) -> bool:
    if user_id == MAIN_ADMIN_ID:
        return True
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (user_id,))
    commit_and_backup(conn)
    return c.rowcount > 0


def remove_admin_user(user_id: int) -> bool:
    if user_id == MAIN_ADMIN_ID:
        return False
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    commit_and_backup(conn)
    return c.rowcount > 0


# --- Users ---


async def register_user(update: Update) -> None:
    uid = update.effective_user.id
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users(user_id) VALUES (?)", (uid,))
    commit_and_backup(conn)


def get_all_user_ids() -> list[int]:
    c = get_connection().cursor()
    c.execute("SELECT user_id FROM users")
    return [r[0] for r in c.fetchall()]


# --- Subjects / lectures ---


def get_subject_id_by_name(name: str) -> Optional[int]:
    c = get_connection().cursor()
    c.execute("SELECT id FROM subjects WHERE name = ?", (name,))
    row = c.fetchone()
    return int(row[0]) if row else None


def insert_subject(name: str) -> int:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM subjects")
    nxt = c.fetchone()[0]
    c.execute("INSERT INTO subjects(name, sort_order) VALUES (?, ?)", (name, nxt))
    commit_and_backup(conn)
    cache_invalidate()
    return int(c.lastrowid)


def get_or_create_subject_id(name: str) -> int:
    sid = get_subject_id_by_name(name)
    if sid is not None:
        return sid
    return insert_subject(name)


def lecture_exists(subject_id: int, title: str) -> bool:
    c = get_connection().cursor()
    c.execute(
        "SELECT 1 FROM lectures WHERE subject_id = ? AND title = ? LIMIT 1",
        (subject_id, title),
    )
    return c.fetchone() is not None


def insert_lecture_full(
    subject_id: int, title: str, file_id: str, content_type: str = "document"
) -> int:
    conn = get_connection()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    c.execute(
        """
        INSERT INTO lectures(subject_id, title, file_id, content_type, download_count, created_at)
        VALUES (?, ?, ?, ?, 0, ?)
        """,
        (subject_id, title, file_id, content_type, now),
    )
    commit_and_backup(conn)
    cache_invalidate()
    return int(c.lastrowid)


def insert_lecture_batched(
    conn: sqlite3.Connection,
    subject_id: int,
    title: str,
    file_id: str,
    content_type: str,
    batch: DbBatch,
) -> int:
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    c.execute(
        """
        INSERT INTO lectures(subject_id, title, file_id, content_type, download_count, created_at)
        VALUES (?, ?, ?, ?, 0, ?)
        """,
        (subject_id, title, file_id, content_type, now),
    )
    last = int(c.lastrowid)
    batch.step(conn)
    cache_invalidate()
    return last


def increment_download(lecture_id: int) -> None:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "UPDATE lectures SET download_count = download_count + 1 WHERE id = ?",
        (lecture_id,),
    )
    commit_and_backup(conn)


def get_subjects_non_empty_cached() -> list[tuple[int, str, int]]:
    global _cache_subjects_non_empty
    now = time.time()
    if _cache_subjects_non_empty and _cache_subjects_non_empty[0] > now:
        return _cache_subjects_non_empty[1]
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT s.id, s.name, COUNT(l.id) AS cnt
        FROM subjects s
        INNER JOIN lectures l ON l.subject_id = s.id
        GROUP BY s.id
        HAVING cnt > 0
        ORDER BY s.sort_order ASC, s.name ASC
        """
    )
    rows = [(int(r[0]), r[1], int(r[2])) for r in c.fetchall()]
    _cache_subjects_non_empty = (now + CACHE_TTL_SEC, rows)
    return rows


def get_lectures_page(subject_id: int, page: int, per_page: int = LECTURES_PER_PAGE):
    global _cache_lecture_pages
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*) FROM lectures WHERE subject_id = ?",
        (subject_id,),
    )
    total = int(c.fetchone()[0])
    pages = max(1, (total + per_page - 1) // per_page)
    page_clamped = max(0, min(page, pages - 1))
    now = time.time()
    key = (subject_id, page_clamped)
    if key in _cache_lecture_pages:
        exp, payload = _cache_lecture_pages[key]
        if exp > now:
            return payload

    offset = page_clamped * per_page
    c.execute(
        """
        SELECT id, title FROM lectures
        WHERE subject_id = ?
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        (subject_id, per_page, offset),
    )
    rows = c.fetchall()
    payload = (total, pages, page_clamped, rows)
    _cache_lecture_pages[key] = (now + CACHE_TTL_SEC, payload)
    return payload


def get_lecture_row(lid: int) -> Optional[sqlite3.Row]:
    c = get_connection().cursor()
    c.execute(
        """
        SELECT l.*, s.name AS subject_name FROM lectures l
        JOIN subjects s ON s.id = l.subject_id
        WHERE l.id = ?
        """,
        (lid,),
    )
    return c.fetchone()


def get_latest_lectures(n: int = LATEST_COUNT):
    c = get_connection().cursor()
    c.execute(
        """
        SELECT l.id, l.title, s.name FROM lectures l
        JOIN subjects s ON s.id = l.subject_id
        ORDER BY l.id DESC
        LIMIT ?
        """,
        (n,),
    )
    return c.fetchall()


def search_lectures(q: str) -> list[tuple[int, str, str]]:
    """Case-insensitive partial match on lecture title and subject name (ASCII via LOWER)."""
    text = (q or "").strip()
    if not text:
        return []
    keyword = text.lower()
    like = f"%{keyword}%"
    c = get_connection().cursor()
    c.execute(
        """
        SELECT lectures.id, lectures.title, subjects.name
        FROM lectures
        JOIN subjects ON subjects.id = lectures.subject_id
        WHERE LOWER(lectures.title) LIKE ?
           OR LOWER(subjects.name) LIKE ?
        ORDER BY lectures.title
        LIMIT 40
        """,
        (like, like),
    )
    return [(int(r[0]), r[1], r[2]) for r in c.fetchall()]


def fav_add(uid: int, lid: int) -> bool:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO favorites(user_id, lecture_id) VALUES (?, ?)",
        (uid, lid),
    )
    commit_and_backup(conn)
    return c.rowcount > 0


def fav_remove(uid: int, lid: int) -> None:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "DELETE FROM favorites WHERE user_id = ? AND lecture_id = ?",
        (uid, lid),
    )
    commit_and_backup(conn)


def fav_is(uid: int, lid: int) -> bool:
    c = get_connection().cursor()
    c.execute(
        "SELECT 1 FROM favorites WHERE user_id = ? AND lecture_id = ?",
        (uid, lid),
    )
    return c.fetchone() is not None


def fav_list(uid: int) -> list[tuple[int, str, str]]:
    c = get_connection().cursor()
    c.execute(
        """
        SELECT l.id, l.title, s.name FROM favorites f
        JOIN lectures l ON l.id = f.lecture_id
        JOIN subjects s ON s.id = l.subject_id
        WHERE f.user_id = ?
        ORDER BY l.title
        LIMIT 50
        """,
        (uid,),
    )
    return [(int(r[0]), r[1], r[2]) for r in c.fetchall()]


def stats_bundle() -> dict[str, Any]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    nu = int(c.fetchone()[0])
    c.execute("SELECT COUNT(*) FROM subjects")
    ns = int(c.fetchone()[0])
    c.execute("SELECT COUNT(*) FROM lectures")
    nl = int(c.fetchone()[0])
    c.execute("SELECT COUNT(*) FROM favorites")
    nf = int(c.fetchone()[0])

    c.execute(
        """
        SELECT s.name, COALESCE(SUM(l.download_count), 0) AS t
        FROM subjects s
        LEFT JOIN lectures l ON l.subject_id = s.id
        GROUP BY s.id
        HAVING COALESCE(SUM(l.download_count), 0) > 0
        ORDER BY t DESC
        LIMIT 1
        """
    )
    top_sub = c.fetchone()

    c.execute(
        "SELECT title, download_count FROM lectures ORDER BY download_count DESC LIMIT 1"
    )
    top_lec = c.fetchone()

    return {
        "users": nu,
        "subjects": ns,
        "lectures": nl,
        "favorites": nf,
        "top_subject": (top_sub[0], int(top_sub[1])) if top_sub else ("—", 0),
        "top_lecture": (top_lec[0], int(top_lec[1])) if top_lec else ("—", 0),
    }


# -----------------------------------------------------------------------------
# Send lecture file by stored type
# -----------------------------------------------------------------------------


async def send_lecture_content(bot, chat_id: int, row: sqlite3.Row) -> None:
    fid = row["file_id"]
    ct = row["content_type"] or "document"
    try:
        if ct == "photo":
            await bot.send_photo(chat_id=chat_id, photo=fid)
        elif ct == "video":
            await bot.send_video(chat_id=chat_id, video=fid)
        elif ct == "animation":
            await bot.send_animation(chat_id=chat_id, animation=fid)
        elif ct == "audio":
            await bot.send_audio(chat_id=chat_id, audio=fid)
        elif ct == "voice":
            await bot.send_voice(chat_id=chat_id, voice=fid)
        elif ct == "video_note":
            await bot.send_video_note(chat_id=chat_id, video_note=fid)
        elif ct == "sticker":
            await bot.send_sticker(chat_id=chat_id, sticker=fid)
        else:
            await bot.send_document(chat_id=chat_id, document=fid)
    except Exception as e:
        logger.warning("Fallback document send: %s", e)
        await bot.send_document(chat_id=chat_id, document=fid)


# -----------------------------------------------------------------------------
# Bulk import (all file types as document path)
# -----------------------------------------------------------------------------


def iter_subject_folders(base_path: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if not os.path.isdir(base_path):
        return out
    for name in sorted(os.listdir(base_path)):
        p = os.path.join(base_path, name)
        if os.path.isdir(p):
            out.append((name, p))
    return out


def iter_import_files(folder: str) -> list[str]:
    files: list[str] = []
    for n in sorted(os.listdir(folder)):
        p = os.path.join(folder, n)
        if os.path.isfile(p) and file_allowed_for_import(n):
            files.append(p)
    return files


def _default_import_base() -> str:
    env = os.getenv("LECTURES_IMPORT_PATH")
    if env:
        return os.path.abspath(env)
    lectures = os.path.join(SCRIPT_DIR, "lectures")
    if os.path.isdir(lectures):
        return lectures
    return SCRIPT_DIR


async def import_lectures_from_folders(
    base_path: str,
    bot,
    chat_id: int,
    progress_callback: Optional[Callable[[str], Any]] = None,
) -> tuple[int, int, int]:
    created = skipped = errors = 0
    conn = get_connection()
    batch = DbBatch()

    async def report(msg: str) -> None:
        logger.info(msg)
        if progress_callback:
            try:
                r = progress_callback(msg)
                if asyncio.iscoroutine(r):
                    await r
            except Exception as e:
                logger.debug("progress: %s", e)

    folders = iter_subject_folders(base_path)
    if not folders:
        await report(f"⚠️ لا توجد مجلدات مواد داخل:\n{base_path}")
        return 0, 0, 0

    total_files = sum(len(iter_import_files(fp)) for _, fp in folders)
    await report(f"📥 استيراد من:\n{base_path}\n📂 مجلدات: {len(folders)} — ملفات: {total_files}")

    for subject_name, folder_path in folders:
        subject_id = get_or_create_subject_id(subject_name)
        for fpath in iter_import_files(folder_path):
            title = extract_lecture_title_from_filename(fpath)
            if not title:
                errors += 1
                continue
            if lecture_exists(subject_id, title):
                skipped += 1
                await report(f"⏭ تخطي: {subject_name} / {title}")
                continue
            try:
                with open(fpath, "rb") as f:
                    msg = await bot.send_document(
                        chat_id=chat_id,
                        document=InputFile(f, filename=os.path.basename(fpath)),
                        disable_notification=True,
                    )
                doc = msg.document
                if not doc:
                    errors += 1
                    continue
                insert_lecture_batched(
                    conn, subject_id, title, doc.file_id, "document", batch
                )
                created += 1
                await report(f"✅ {created+skipped+errors}/{total_files} — {title}")
            except Exception as e:
                errors += 1
                logger.exception("import fail %s", fpath)
                await report(f"❌ {title}: {e!s}")

    batch.step(conn, force=True)
    save_db_mirror()
    await report(f"🏁 انتهى.\n✅ جديد: {created}\n⏭ تخطي: {skipped}\n❌ أخطاء: {errors}")
    return created, skipped, errors


# -----------------------------------------------------------------------------
# UI — 2 columns where possible
# -----------------------------------------------------------------------------


def kb_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📚 الدخول للمواد", callback_data="u_subjects"),
                InlineKeyboardButton("🆕 أحدث المحاضرات", callback_data="u_latest"),
            ],
            [
                InlineKeyboardButton("🔍 البحث عن محاضرة", callback_data="u_search"),
                InlineKeyboardButton("⭐ المفضلة", callback_data="u_fav"),
            ],
            [
                InlineKeyboardButton("📥 تحميل كل محاضرات المادة", callback_data="u_dl_pick_sub"),
                InlineKeyboardButton("🔗 لينكات مهمة", callback_data="u_links"),
            ],
            [
                InlineKeyboardButton("📊 إحصائيات البوت", callback_data="u_pubstats"),
                InlineKeyboardButton("🛠 الإبلاغ عن مشكلة", callback_data="u_report"),
            ],
            [
                InlineKeyboardButton(
                    "👤 التواصل مع الأدمن",
                    url=f"https://t.me/{ADMIN_USERNAME}",
                )
            ],
        ]
    )


def kb_back_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🏠 الرئيسية", callback_data="u_home")]]
    )


def kb_lecture_nav(subject_id: int, page: int, pages: int) -> list[list[InlineKeyboardButton]]:
    row = []
    if page > 0:
        row.append(
            InlineKeyboardButton("⬅️ السابق", callback_data=f"u_lecp_{subject_id}_{page-1}")
        )
    if page < pages - 1:
        row.append(
            InlineKeyboardButton("➡️ التالي", callback_data=f"u_lecp_{subject_id}_{page+1}")
        )
    nav = [row] if row else []
    nav.append(
        [
            InlineKeyboardButton("🔙 رجوع للمواد", callback_data="u_subjects"),
            InlineKeyboardButton("🏠 الرئيسية", callback_data="u_home"),
        ]
    )
    return nav


def kb_after_lecture(lid: int, uid: int) -> InlineKeyboardMarkup:
    on = fav_is(uid, lid)
    fav_label = "⭐ إزالة من المفضلة" if on else "⭐ إضافة للمفضلة"
    fav_cb = f"u_favtog_{lid}"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(fav_label, callback_data=fav_cb)],
            [
                InlineKeyboardButton("📂 المفضلة", callback_data="u_fav"),
                InlineKeyboardButton("🏠 الرئيسية", callback_data="u_home"),
            ],
        ]
    )


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➕ إضافة مادة", callback_data="adm_add_subject"),
                InlineKeyboardButton("➕ إضافة محاضرة", callback_data="adm_add_lecture"),
            ],
            [
                InlineKeyboardButton("📂 استيراد مجلد كامل", callback_data="adm_import_folder"),
                InlineKeyboardButton("📥 استيراد دفعة واحدة", callback_data="adm_import"),
            ],
            [
                InlineKeyboardButton("🗑 حذف مادة", callback_data="adm_del_subject"),
                InlineKeyboardButton("🗑 حذف محاضرة", callback_data="adm_del_lecture"),
            ],
            [
                InlineKeyboardButton("✏️ تعديل اسم مادة", callback_data="adm_edit_subject"),
                InlineKeyboardButton("✏️ تعديل عنوان محاضرة", callback_data="adm_edit_lecture"),
            ],
            [InlineKeyboardButton("⬆️ ترتيب المواد", callback_data="adm_sort_subjects")],
            [InlineKeyboardButton("🔗 إدارة اللينكات", callback_data="adm_links")],
            [
                InlineKeyboardButton("📢 رسالة جماعية", callback_data="adm_broadcast"),
                InlineKeyboardButton("📊 إحصائيات", callback_data="adm_stats"),
            ],
            [
                InlineKeyboardButton("📦 Backup Database", callback_data="adm_backup"),
                InlineKeyboardButton("🧹 تنظيف الداتا", callback_data="adm_cleanup"),
            ],
            [
                InlineKeyboardButton("⏸ إيقاف البوت", callback_data="adm_stop"),
                InlineKeyboardButton("▶️ تشغيل البوت", callback_data="adm_start"),
            ],
            [InlineKeyboardButton("👑 إدارة المشرفين", callback_data="adm_admins")],
            [InlineKeyboardButton("🏠 رجوع للأدمن", callback_data="adm_home")],
        ]
    )


# -----------------------------------------------------------------------------
# Rate limit
# -----------------------------------------------------------------------------


def check_rate_limit(user_id: int) -> bool:
    now = time.time()
    if user_id in blocked_users:
        if now < blocked_users[user_id]:
            return False
        del blocked_users[user_id]
    user_messages.setdefault(user_id, [])
    user_messages[user_id] = [t for t in user_messages[user_id] if now - t < RATE_LIMIT_WINDOW]
    user_messages[user_id].append(now)
    if len(user_messages[user_id]) > RATE_LIMIT_MAX_MESSAGES:
        blocked_users[user_id] = now + RATE_LIMIT_BLOCK_SECONDS
        return False
    return True


# -----------------------------------------------------------------------------
# Commands & entry
# -----------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not check_rate_limit(uid):
        await update.message.reply_text(RATE_LIMIT_MESSAGE)
        return
    await register_user(update)
    if not BOT_ENABLED and not is_admin(uid):
        await update.message.reply_text(MAINTENANCE_MESSAGE)
        return
    await update.message.reply_text(
        "✨ أهلاً بك\nاختر من القائمة:",
        reply_markup=kb_main_menu(),
    )


async def cmd_subjects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    await register_user(update)
    await send_subjects_screen(update.message)


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    context.user_data["search_mode"] = True
    await update.message.reply_text(
        "🔍 اكتب كلمة البحث (في عنوان المحاضرة أو اسم المادة):",
        reply_markup=kb_back_home(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not BOT_ENABLED and not is_admin(uid):
        await update.message.reply_text(MAINTENANCE_MESSAGE)
        return
    await update.message.reply_text(
        "📖 **مساعدة**\n\n"
        "/start — القائمة الرئيسية\n"
        "/subjects — عرض المواد\n"
        "/search — بحث عن محاضرة\n"
        "/help — هذه الرسالة\n"
        "/admin — لوحة المشرفين (للمصرّح لهم فقط)\n",
        parse_mode="Markdown",
        reply_markup=kb_back_home(),
    )


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ غير مصرّح.")
        return
    await update.message.reply_text(
        "🛠 لوحة التحكم",
        reply_markup=admin_panel_keyboard(),
    )


async def _gate(update: Update) -> bool:
    uid = update.effective_user.id
    if not BOT_ENABLED and not is_admin(uid):
        await update.message.reply_text(MAINTENANCE_MESSAGE)
        return False
    return True


async def send_subjects_screen(message) -> None:
    rows = get_subjects_non_empty_cached()
    if not rows:
        await message.reply_text("📌 لا توجد مواد تحتوي محاضرات بعد.", reply_markup=kb_back_home())
        return
    kb = []
    pair: list[InlineKeyboardButton] = []
    for sid, name, cnt in rows:
        pair.append(
            InlineKeyboardButton(f"{name} ({cnt})", callback_data=f"u_sub_{sid}_0")
        )
        if len(pair) == 2:
            kb.append(pair)
            pair = []
    if pair:
        kb.append(pair)
    kb.append(
        [InlineKeyboardButton("🏠 الرئيسية", callback_data="u_home")]
    )
    await message.reply_text("📚 المواد — اختر مادة:", reply_markup=InlineKeyboardMarkup(kb))


async def send_lecture_page(query, subject_id: int, page: int) -> None:
    total, pages, page, items = get_lectures_page(subject_id, page)
    if total == 0:
        await query.message.reply_text("📌 لا توجد محاضرات.", reply_markup=kb_back_home())
        return
    c = get_connection().cursor()
    c.execute("SELECT name FROM subjects WHERE id = ?", (subject_id,))
    sname = c.fetchone()[0]
    lines = [f"📘 **{sname}**\n📄 المحاضرات — صفحة {page + 1}/{pages}\n"]
    kb = []
    for lid, title in items:
        kb.append([InlineKeyboardButton(title[:64], callback_data=f"u_get_{lid}")])
    kb.extend(kb_lecture_nav(subject_id, page, pages))
    await query.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb),
    )


# -----------------------------------------------------------------------------
# User callbacks (prefix u_)
# -----------------------------------------------------------------------------


async def user_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if not BOT_ENABLED and not is_admin(uid):
        await query.message.reply_text(MAINTENANCE_MESSAGE)
        return

    d = query.data or ""

    if d == "u_home":
        await query.message.reply_text("🏠 القائمة الرئيسية", reply_markup=kb_main_menu())
        return

    if d == "u_subjects":
        await send_subjects_screen(query.message)
        return

    if d == "u_latest":
        rows = get_latest_lectures(LATEST_COUNT)
        if not rows:
            await query.message.reply_text("📌 لا توجد محاضرات بعد.", reply_markup=kb_back_home())
            return
        kb = []
        for lid, title, sname in rows:
            kb.append(
                [
                    InlineKeyboardButton(
                        f"{title[:40]} — {sname[:20]}",
                        callback_data=f"u_get_{lid}",
                    )
                ]
            )
        kb.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="u_home")])
        await query.message.reply_text(
            f"🆕 أحدث {LATEST_COUNT} محاضرات:",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    if d == "u_search":
        context.user_data["search_mode"] = True
        await query.message.reply_text(
            "🔍 اكتب كلمة البحث في المحادثة:",
            reply_markup=kb_back_home(),
        )
        return

    if d == "u_fav":
        fl = fav_list(uid)
        if not fl:
            await query.message.reply_text(
                "📂 المفضلة فارغة.",
                reply_markup=kb_back_home(),
            )
            return
        kb = []
        for lid, title, sname in fl:
            kb.append(
                [
                    InlineKeyboardButton(
                        f"{title[:45]} ({sname[:15]})",
                        callback_data=f"u_get_{lid}",
                    )
                ]
            )
        kb.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="u_home")])
        await query.message.reply_text("⭐ مفضلاتك:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if d == "u_dl_pick_sub":
        rows = get_subjects_non_empty_cached()
        if not rows:
            await query.message.reply_text("📌 لا توجد مواد.", reply_markup=kb_back_home())
            return
        kb = []
        for sid, name, _ in rows:
            kb.append(
                [
                    InlineKeyboardButton(
                        name[:50], callback_data=f"u_dlall_{sid}"
                    )
                ]
            )
        kb.append([InlineKeyboardButton("🔙 رجوع", callback_data="u_home")])
        await query.message.reply_text(
            "📥 اختر المادة لتحميل كل محاضراتها (قد يستغرق وقتًا):",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    if d.startswith("u_dlall_"):
        sid = int(d.replace("u_dlall_", ""))
        c = get_connection().cursor()
        c.execute(
            "SELECT id FROM lectures WHERE subject_id = ? ORDER BY id",
            (sid,),
        )
        lids = [r[0] for r in c.fetchall()]
        await query.message.reply_text(f"⏳ جاري الإرسال — {len(lids)} ملف...")
        for lid in lids:
            row = get_lecture_row(lid)
            if row:
                try:
                    await send_lecture_content(context.bot, uid, row)
                    increment_download(lid)
                except Exception as e:
                    logger.warning("dlall %s: %s", lid, e)
            await asyncio.sleep(0.12)
        await query.message.reply_text("✅ اكتمل التحميل.", reply_markup=kb_back_home())
        return

    if d == "u_links":
        c = get_connection().cursor()
        c.execute(
            "SELECT title, url FROM important_links ORDER BY position, id"
        )
        rows = c.fetchall()
        if not rows:
            await query.message.reply_text("📌 لا توجد لينكات.", reply_markup=kb_back_home())
            return
        lines = ["🔗 **لينكات مهمة**\n"]
        for t, u in rows:
            lines.append(f"• [{t}]({u})")
        await query.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown",
            disable_web_page_preview=True,
            reply_markup=kb_back_home(),
        )
        return

    if d == "u_pubstats":
        st = stats_bundle()
        await query.message.reply_text(
            "📊 **إحصائيات البوت**\n"
            f"👥 المستخدمون: {st['users']}\n"
            f"📚 المواد: {st['subjects']}\n"
            f"📄 المحاضرات: {st['lectures']}\n"
            f"⭐ المفضلة: {st['favorites']}\n",
            parse_mode="Markdown",
            reply_markup=kb_back_home(),
        )
        return

    if d == "u_report":
        await query.message.reply_text(
            "🛠 للإبلاغ عن مشكلة تواصل مع الأدمن من زر التواصل.",
            reply_markup=kb_back_home(),
        )
        return

    if d.startswith("u_sub_"):
        # u_sub_{sid}_{page}
        parts = d.split("_")
        if len(parts) >= 4 and parts[1] == "sub":
            sid = int(parts[2])
            page = int(parts[3])
            await send_lecture_page(query, sid, page)
        return

    if d.startswith("u_lecp_"):
        rest = d[len("u_lecp_") :]
        sid_s, _, pg_s = rest.rpartition("_")
        if sid_s and pg_s.isdigit():
            await send_lecture_page(query, int(sid_s), int(pg_s))
        return

    if d.startswith("u_get_"):
        lid = int(d.replace("u_get_", ""))
        row = get_lecture_row(lid)
        if not row:
            await query.message.reply_text("❌ غير موجود.")
            return
        try:
            await send_lecture_content(context.bot, uid, row)
            increment_download(lid)
        except Exception as e:
            logger.exception("send lecture")
            await query.message.reply_text(f"❌ تعذر الإرسال: {e}")
            return
        await query.message.reply_text(
            f"📄 {row['title']}\n📚 {row['subject_name']}",
            reply_markup=kb_after_lecture(lid, uid),
        )
        return

    if d.startswith("u_favtog_"):
        lid = int(d.replace("u_favtog_", ""))
        if fav_is(uid, lid):
            fav_remove(uid, lid)
            await query.answer("أُزيلت من المفضلة", show_alert=False)
        else:
            fav_add(uid, lid)
            await query.answer("أُضيفت للمفضلة", show_alert=False)
        await query.message.reply_text(
            "تم التحديث.",
            reply_markup=kb_after_lecture(lid, uid),
        )
        return


# -----------------------------------------------------------------------------
# Admin router (keep existing adm_ handlers + new)
# -----------------------------------------------------------------------------


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(
        "🛠 لوحة التحكم",
        reply_markup=admin_panel_keyboard(),
    )


async def admin_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global BOT_ENABLED
    query = update.callback_query
    uid = query.from_user.id
    if not is_admin(uid):
        await query.answer("غير مصرّح.", show_alert=True)
        return
    data = query.data or ""
    await query.answer()

    if data == "adm_home":
        await query.message.reply_text(
            "🛠 لوحة التحكم",
            reply_markup=admin_panel_keyboard(),
        )
        return

    if data == "adm_add_subject":
        context.user_data.clear()
        context.user_data["waiting_subject"] = True
        await query.message.reply_text("✏️ اكتب اسم المادة:")
        return

    if data == "adm_add_lecture":
        context.user_data.clear()
        c = get_connection().cursor()
        c.execute("SELECT id, name FROM subjects ORDER BY sort_order, name")
        subs = c.fetchall()
        if not subs:
            await query.message.reply_text("📌 لا توجد مواد.")
            return
        kb = [
            [InlineKeyboardButton(n, callback_data=f"adm_choose_lec_{sid}")]
            for sid, n in subs
        ]
        await query.message.reply_text("📚 اختر المادة:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("adm_choose_lec_"):
        sid = int(data.replace("adm_choose_lec_", ""))
        context.user_data["add_lec_subject"] = sid
        await query.message.reply_text(
            "أرسل عنوانًا نصيًا اختياريًا، ثم أرسل **أي ملف** (أو أرسل الملف مباشرة — العنوان من الاسم).",
            parse_mode="Markdown",
        )
        return

    if data == "adm_import_folder":
        base = _default_import_base()
        pm = await query.message.reply_text(f"⏳ استيراد مجلد:\n{base}")
        async def prog(t: str) -> None:
            try:
                await pm.edit_text(t[:4000])
            except Exception:
                pass
        await import_lectures_from_folders(base, context.bot, uid, prog)
        log_admin_action(uid, "import_folder")
        return

    if data == "adm_import":
        base = _default_import_base()
        pm = await query.message.reply_text(f"⏳ استيراد دفعة من:\n{base}")
        async def prog(t: str) -> None:
            try:
                await pm.edit_text(t[:4000])
            except Exception:
                pass
        await import_lectures_from_folders(base, context.bot, uid, prog)
        log_admin_action(uid, "import_batch")
        return

    if data == "adm_sort_subjects":
        c = get_connection().cursor()
        c.execute("SELECT id, name FROM subjects ORDER BY sort_order, name")
        subs = c.fetchall()
        if not subs:
            await query.message.reply_text("📌 لا توجد مواد.")
            return
        kb = []
        for i, (sid, name) in enumerate(subs):
            row = [
                InlineKeyboardButton("⬆️", callback_data=f"adm_sort_up_{sid}"),
                InlineKeyboardButton(name[:35], callback_data="adm_noop"),
                InlineKeyboardButton("⬇️", callback_data=f"adm_sort_dn_{sid}"),
            ]
            kb.append(row)
        kb.append([InlineKeyboardButton("🏠 رجوع", callback_data="adm_home")])
        await query.message.reply_text(
            "⬆️⬇️ ترتيب المواد (تبديل مع الجار):",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    if data == "adm_noop":
        return

    if data.startswith("adm_sort_up_"):
        sid = int(data.replace("adm_sort_up_", ""))
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT sort_order FROM subjects WHERE id = ?", (sid,))
        cur = c.fetchone()
        if not cur:
            return
        so = cur[0]
        c.execute(
            "SELECT id, sort_order FROM subjects WHERE sort_order < ? ORDER BY sort_order DESC LIMIT 1",
            (so,),
        )
        prev = c.fetchone()
        if prev:
            c.execute("UPDATE subjects SET sort_order = ? WHERE id = ?", (prev[1], sid))
            c.execute("UPDATE subjects SET sort_order = ? WHERE id = ?", (so, prev[0]))
            commit_and_backup(conn)
            cache_invalidate()
        await query.message.reply_text("✅ تم تعديل الترتيب. افتح ترتيب المواد مجددًا إن لزم.")
        return

    if data.startswith("adm_sort_dn_"):
        sid = int(data.replace("adm_sort_dn_", ""))
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT sort_order FROM subjects WHERE id = ?", (sid,))
        cur = c.fetchone()
        if not cur:
            return
        so = cur[0]
        c.execute(
            "SELECT id, sort_order FROM subjects WHERE sort_order > ? ORDER BY sort_order ASC LIMIT 1",
            (so,),
        )
        nxt = c.fetchone()
        if nxt:
            c.execute("UPDATE subjects SET sort_order = ? WHERE id = ?", (nxt[1], sid))
            c.execute("UPDATE subjects SET sort_order = ? WHERE id = ?", (so, nxt[0]))
            commit_and_backup(conn)
            cache_invalidate()
        await query.message.reply_text("✅ تم تعديل الترتيب.")
        return

    if data == "adm_cleanup":
        conn = get_connection()
        c = conn.cursor()
        c.execute("DELETE FROM favorites WHERE lecture_id NOT IN (SELECT id FROM lectures)")
        c.execute("VACUUM")
        commit_and_backup(conn)
        optional_github_backup()
        await query.message.reply_text("🧹 تم تنظيف المفضلة اليتيمة وتنفيذ VACUUM.")
        log_admin_action(uid, "cleanup")
        return

    if data == "adm_del_subject":
        c = get_connection().cursor()
        c.execute("SELECT id, name FROM subjects ORDER BY sort_order, name")
        subs = c.fetchall()
        if not subs:
            await query.message.reply_text("📌 لا توجد مواد.")
            return
        kb = [
            [InlineKeyboardButton(n, callback_data=f"adm_confirm_del_sub_{sid}")]
            for sid, n in subs
        ]
        await query.message.reply_text("🗑 اختر مادة للحذف:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("adm_confirm_del_sub_"):
        sid = int(data.replace("adm_confirm_del_sub_", ""))
        conn = get_connection()
        c = conn.cursor()
        c.execute("DELETE FROM lectures WHERE subject_id = ?", (sid,))
        c.execute("DELETE FROM subjects WHERE id = ?", (sid,))
        commit_and_backup(conn)
        cache_invalidate()
        await query.message.reply_text("✅ تم الحذف.")
        log_admin_action(uid, f"del_sub {sid}")
        return

    if data == "adm_del_lecture":
        c = get_connection().cursor()
        c.execute("SELECT id, name FROM subjects ORDER BY sort_order, name")
        subs = c.fetchall()
        kb = [
            [InlineKeyboardButton(n, callback_data=f"adm_pick_del_lec_sub_{sid}")]
            for sid, n in subs
        ]
        await query.message.reply_text("اختر المادة:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("adm_pick_del_lec_sub_"):
        sid = int(data.replace("adm_pick_del_lec_sub_", ""))
        c = get_connection().cursor()
        c.execute(
            "SELECT id, title FROM lectures WHERE subject_id = ? ORDER BY title",
            (sid,),
        )
        lecs = c.fetchall()
        if not lecs:
            await query.message.reply_text("📌 لا محاضرات.")
            return
        kb = [
            [InlineKeyboardButton(t[:58], callback_data=f"adm_confirm_del_lec_{lid}")]
            for lid, t in lecs
        ]
        await query.message.reply_text("🗑 اختر محاضرة:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("adm_confirm_del_lec_"):
        lid = int(data.replace("adm_confirm_del_lec_", ""))
        conn = get_connection()
        c = conn.cursor()
        c.execute("DELETE FROM lectures WHERE id = ?", (lid,))
        commit_and_backup(conn)
        cache_invalidate()
        await query.message.reply_text("✅ تم حذف المحاضرة.")
        log_admin_action(uid, f"del_lec {lid}")
        return

    if data == "adm_edit_subject":
        c = get_connection().cursor()
        c.execute("SELECT id, name FROM subjects ORDER BY sort_order, name")
        subs = c.fetchall()
        kb = [
            [InlineKeyboardButton(n, callback_data=f"adm_edit_subj_{sid}")]
            for sid, n in subs
        ]
        await query.message.reply_text("اختر مادة:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("adm_edit_subj_"):
        sid = int(data.replace("adm_edit_subj_", ""))
        context.user_data.clear()
        context.user_data["edit_subject_id"] = sid
        await query.message.reply_text("اكتب الاسم الجديد:")
        return

    if data == "adm_edit_lecture":
        c = get_connection().cursor()
        c.execute("SELECT id, name FROM subjects ORDER BY sort_order, name")
        subs = c.fetchall()
        kb = [
            [InlineKeyboardButton(n, callback_data=f"adm_edit_lec_sub_{sid}")]
            for sid, n in subs
        ]
        await query.message.reply_text("اختر المادة:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("adm_edit_lec_sub_"):
        sid = int(data.replace("adm_edit_lec_sub_", ""))
        c = get_connection().cursor()
        c.execute(
            "SELECT id, title FROM lectures WHERE subject_id = ? ORDER BY title",
            (sid,),
        )
        lecs = c.fetchall()
        kb = [
            [InlineKeyboardButton(t[:58], callback_data=f"adm_edit_lec_id_{lid}")]
            for lid, t in lecs
        ]
        await query.message.reply_text("اختر محاضرة:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("adm_edit_lec_id_"):
        lid = int(data.replace("adm_edit_lec_id_", ""))
        context.user_data.clear()
        context.user_data["edit_lecture_id"] = lid
        await query.message.reply_text("عنوان جديد:")
        return

    if data == "adm_links":
        c = get_connection().cursor()
        c.execute("SELECT id, title, url, position FROM important_links ORDER BY position, id")
        rows = c.fetchall()
        txt = "\n".join(f"{p}. {t} — {u}" for _, t, u, p in rows) if rows else "لا لينكات."
        kb = [
            [InlineKeyboardButton("➕ إضافة", callback_data="adm_link_add")],
            [InlineKeyboardButton("🗑 حذف", callback_data="adm_link_del_pick")],
            [InlineKeyboardButton("🏠 رجوع", callback_data="adm_home")],
        ]
        await query.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))
        return

    if data == "adm_link_add":
        context.user_data.clear()
        context.user_data["link_add_step"] = "title"
        await query.message.reply_text("عنوان اللينك:")
        return

    if data == "adm_link_del_pick":
        c = get_connection().cursor()
        c.execute("SELECT id, title FROM important_links ORDER BY position, id")
        rows = c.fetchall()
        if not rows:
            await query.message.reply_text("لا شيء للحذف.")
            return
        kb = [
            [InlineKeyboardButton(t[:50], callback_data=f"adm_link_del_{lid}")]
            for lid, t in rows
        ]
        await query.message.reply_text("اختر لينكًا:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("adm_link_del_") and not data.startswith("adm_link_del_pick"):
        lid = int(data.replace("adm_link_del_", ""))
        conn = get_connection()
        c = conn.cursor()
        c.execute("DELETE FROM important_links WHERE id = ?", (lid,))
        commit_and_backup(conn)
        await query.message.reply_text("✅ تم.")
        return

    if data == "adm_broadcast":
        context.user_data.clear()
        context.user_data["broadcast_wait"] = True
        await query.message.reply_text(
            "📢 أرسل أي رسالة (نص، صورة، ملف، صوت، فيديو، ملصق...)"
        )
        return

    if data == "adm_stats":
        st = stats_bundle()
        await query.message.reply_text(
            "📊 **إحصائيات المشرف**\n"
            f"👥 مستخدمون: {st['users']}\n"
            f"📚 مواد: {st['subjects']}\n"
            f"📄 محاضرات: {st['lectures']}\n"
            f"⭐ مفضلة: {st['favorites']}\n"
            f"🏆 أكثر مادة تحميلًا: {st['top_subject'][0]} ({st['top_subject'][1]})\n"
            f"🏆 أكثر محاضرة: {st['top_lecture'][0]} ({st['top_lecture'][1]} تحميل)\n",
            parse_mode="Markdown",
        )
        return

    if data == "adm_backup":
        try:
            path = admin_backup_timestamped()
            await query.message.reply_text(f"📦 تم النسخ:\n{path}")
            await send_db_to_admins(context.bot)
            log_admin_action(uid, "backup")
        except OSError as e:
            await query.message.reply_text(f"❌ {e}")
        return

    if data == "adm_stop":
        BOT_ENABLED = False
        await query.message.reply_text("⏸ تم إيقاف البوت للطلاب.")
        log_admin_action(uid, "stop")
        return

    if data == "adm_start":
        BOT_ENABLED = True
        await query.message.reply_text("▶️ تم التشغيل.")
        log_admin_action(uid, "start")
        return

    if data == "adm_admins":
        if not is_main_admin(uid):
            await query.message.reply_text("⛔ للمشرف الرئيسي فقط.")
            return
        extras = get_extra_admins()
        lines = [f"👑 `{MAIN_ADMIN_ID}`", "المشرفون الإضافيون:"]
        for e in extras:
            lines.append(f"• `{e}` — /remove_admin {e}")
        if not extras:
            lines.append("(لا يوجد)")
        kb = [
            [InlineKeyboardButton("➕ إضافة مشرف", callback_data="adm_admin_add")],
            [InlineKeyboardButton("🏠 رجوع", callback_data="adm_home")],
        ]
        await query.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    if data == "adm_admin_add":
        if not is_main_admin(uid):
            return
        context.user_data.clear()
        context.user_data["admin_add_wait"] = True
        await query.message.reply_text("أرسل user id:")
        return


# -----------------------------------------------------------------------------
# Broadcast first — any message type
# -----------------------------------------------------------------------------


async def admin_broadcast_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    if not context.user_data.get("broadcast_wait"):
        return
    if not update.message:
        return
    context.user_data["broadcast_wait"] = False
    uids = get_all_user_ids()
    ok = fail = 0
    from_chat = update.effective_chat.id
    mid = update.message.message_id
    await update.message.reply_text(f"⏳ جاري الإرسال إلى {len(uids)} مستخدم...")
    for ouid in uids:
        try:
            await context.bot.copy_message(
                chat_id=ouid,
                from_chat_id=from_chat,
                message_id=mid,
            )
            ok += 1
        except Exception as e:
            fail += 1
            logger.debug("bc fail %s: %s", ouid, e)
        await asyncio.sleep(BROADCAST_DELAY)
    await update.message.reply_text(
        f"تم الإرسال بنجاح: {ok}\nفشل الإرسال: {fail}"
    )
    context.user_data["broadcast_handled"] = True
    log_admin_action(update.effective_user.id, "broadcast")


# -----------------------------------------------------------------------------
# Admin text / media
# -----------------------------------------------------------------------------


async def admin_handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    if context.user_data.pop("broadcast_handled", False):
        return
    if context.user_data.get("broadcast_wait"):
        return
    text = (update.message.text or "").strip()

    if context.user_data.get("waiting_subject"):
        if not text:
            await update.message.reply_text("❌ فارغ.")
            return
        conn = get_connection()
        c = conn.cursor()
        try:
            c.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM subjects")
            nxt = int(c.fetchone()[0])
            c.execute(
                "INSERT INTO subjects(name, sort_order) VALUES (?, ?)",
                (text, nxt),
            )
            commit_and_backup(conn)
        except sqlite3.IntegrityError:
            await update.message.reply_text("❌ موجودة مسبقًا.")
            return
        context.user_data.clear()
        cache_invalidate()
        await update.message.reply_text("✅ تمت إضافة المادة.")
        return

    if context.user_data.get("add_lec_subject") is not None and context.user_data.get("lecture_title") is None:
        context.user_data["lecture_title"] = text
        await update.message.reply_text("📤 أرسل الملف الآن (أي نوع مدعوم).")
        return

    if context.user_data.get("edit_subject_id") is not None:
        sid = context.user_data["edit_subject_id"]
        conn = get_connection()
        c = conn.cursor()
        try:
            c.execute("UPDATE subjects SET name = ? WHERE id = ?", (text, sid))
            commit_and_backup(conn)
        except sqlite3.IntegrityError:
            await update.message.reply_text("❌ الاسم مستخدم.")
            return
        context.user_data.clear()
        cache_invalidate()
        await update.message.reply_text("✅ تم.")
        return

    if context.user_data.get("edit_lecture_id") is not None:
        lid = context.user_data["edit_lecture_id"]
        conn = get_connection()
        c = conn.cursor()
        c.execute("UPDATE lectures SET title = ? WHERE id = ?", (text, lid))
        commit_and_backup(conn)
        context.user_data.clear()
        cache_invalidate()
        await update.message.reply_text("✅ تم.")
        return

    if context.user_data.get("link_add_step") == "title":
        context.user_data["link_title"] = text
        context.user_data["link_add_step"] = "url"
        await update.message.reply_text("الرابط:")
        return

    if context.user_data.get("link_add_step") == "url":
        title = context.user_data.get("link_title", "")
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT COALESCE(MAX(position),0)+1 FROM important_links")
        pos = c.fetchone()[0]
        c.execute(
            "INSERT INTO important_links(title, url, position) VALUES (?,?,?)",
            (title, text, pos),
        )
        commit_and_backup(conn)
        context.user_data.clear()
        await update.message.reply_text("✅ تم.")
        return

    if context.user_data.get("admin_add_wait") and is_main_admin(uid):
        if not text.isdigit():
            await update.message.reply_text("أرقام فقط.")
            return
        nid = int(text)
        if add_admin_user(nid):
            context.user_data.clear()
            await update.message.reply_text(f"✅ أُضيف {nid}")
        else:
            await update.message.reply_text("موجود أو غير صالح.")
        return


async def admin_handle_any_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add lecture: any file sent as document (filters.Document.ALL)."""
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    if context.user_data.pop("broadcast_handled", False):
        return
    if context.user_data.get("broadcast_wait"):
        return
    if context.user_data.get("add_lec_subject") is None:
        return
    msg = update.message
    if not msg.document:
        return
    fid = msg.document.file_id
    ctype = "document"
    sid = context.user_data["add_lec_subject"]
    title = context.user_data.get("lecture_title")
    if title:
        title = title.strip()
    if not title:
        fn = msg.document.file_name or "file"
        title = extract_lecture_title_from_filename(fn) or "محاضرة"
    if lecture_exists(sid, title):
        context.user_data.clear()
        await msg.reply_text("⏭ موجودة مسبقًا.")
        return
    insert_lecture_full(sid, title, fid, ctype)
    context.user_data.clear()
    await msg.reply_text(f"✅ أُضيفت: {title}")


async def handle_public_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if is_admin(uid):
        return
    if not BOT_ENABLED:
        await update.message.reply_text(MAINTENANCE_MESSAGE)
        return
    if context.user_data.get("search_mode"):
        q = (update.message.text or "").strip()
        context.user_data.pop("search_mode", None)
        res = search_lectures(q)
        if not res:
            await update.message.reply_text(
                "🔍 لا نتائج.",
                reply_markup=kb_back_home(),
            )
            return
        kb = []
        for lid, title, sname in res[:30]:
            kb.append(
                [
                    InlineKeyboardButton(
                        f"{title[:40]} — {sname[:18]}",
                        callback_data=f"u_get_{lid}",
                    )
                ]
            )
        kb.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="u_home")])
        await update.message.reply_text(
            f"🔍 نتائج البحث ({len(res)}):",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return
    if not check_rate_limit(uid):
        await update.message.reply_text(RATE_LIMIT_MESSAGE)


async def remove_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not is_main_admin(update.effective_user.id):
        return
    args = context.args or []
    if len(args) < 1:
        await update.message.reply_text("استخدم: /remove_admin ثم رقم المستخدم")
        return
    try:
        t = int(args[0])
    except ValueError:
        return
    if remove_admin_user(t):
        await update.message.reply_text("✅ تم.")
    else:
        await update.message.reply_text("❌ لا يمكن.")


# -----------------------------------------------------------------------------
# Jobs
# -----------------------------------------------------------------------------


async def job_auto_backup(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        admin_backup_timestamped()
        await send_db_to_admins(context.bot)
        optional_github_backup()
        logger.info("Scheduled backup OK")
    except Exception as e:
        logger.warning("Scheduled backup: %s", e)


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("start", "القائمة الرئيسية"),
            BotCommand("subjects", "عرض المواد"),
            BotCommand("search", "بحث عن محاضرة"),
            BotCommand("help", "مساعدة"),
            BotCommand("admin", "لوحة المشرف"),
        ]
    )
    try:
        await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as e:
        logger.warning("menu button: %s", e)
    jq = application.job_queue
    if jq:
        jq.run_repeating(
            job_auto_backup,
            interval=timedelta(hours=AUTO_BACKUP_HOURS),
            first=timedelta(seconds=30),
            name="auto_backup",
        )
    else:
        logger.warning("JobQueue unavailable — install: pip install 'python-telegram-bot[job-queue]'")


def main() -> None:
    if not TOKEN:
        raise SystemExit("Set TOKEN environment variable.")

    init_db()

    builder = Application.builder().token(TOKEN).post_init(post_init)
    try:
        builder = builder.rate_limiter(AIORateLimiter())
    except Exception as e:
        logger.warning("AIORateLimiter: %s", e)
    application = builder.build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("subjects", cmd_subjects))
    application.add_handler(CommandHandler("search", cmd_search))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("admin", cmd_admin))
    application.add_handler(CommandHandler("remove_admin", remove_admin_command))

    application.add_handler(
        CallbackQueryHandler(admin_callback_router, pattern=r"^adm_"),
        group=0,
    )
    application.add_handler(
        CallbackQueryHandler(user_callback_router, pattern=r"^u_"),
        group=0,
    )

    # Broadcast copies any message — must run before other admin handlers
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE
            & ~filters.COMMAND
            & ~filters.StatusUpdate.ALL,
            admin_broadcast_entry,
        ),
        group=0,
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, admin_handle_text),
        group=1,
    )
    application.add_handler(
        MessageHandler(filters.Document.ALL, admin_handle_any_file),
        group=1,
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_public_text),
        group=2,
    )

    logger.info("Starting polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
