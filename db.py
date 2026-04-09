"""Async SQLite data access (aiosqlite)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import aiosqlite

from config import ADMIN_ID, DATABASE_PATH

_connection: Optional[aiosqlite.Connection] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_db() -> aiosqlite.Connection:
    global _connection
    if _connection is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _connection


async def init_db() -> None:
    global _connection
    _connection = await aiosqlite.connect(DATABASE_PATH)
    _connection.row_factory = aiosqlite.Row
    await _connection.execute("PRAGMA foreign_keys = ON")
    await _connection.execute("PRAGMA journal_mode = WAL")

    await _connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            created_at TEXT NOT NULL,
            last_active TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            sort_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS lectures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            file_id TEXT NOT NULL,
            file_unique_id TEXT,
            file_name TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER NOT NULL,
            lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, lecture_id)
        );

        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subject_name TEXT NOT NULL,
            lecture_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_lectures_subject ON lectures(subject_id);
        CREATE INDEX IF NOT EXISTS idx_lectures_created ON lectures(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id);
        """
    )
    await _connection.commit()

    cur = await _connection.execute(
        "SELECT 1 FROM settings WHERE key = 'bot_running' LIMIT 1"
    )
    if await cur.fetchone() is None:
        await _connection.execute(
            "INSERT INTO settings (key, value) VALUES ('bot_running', '1')"
        )
    await _connection.commit()

    cur = await _connection.execute(
        "SELECT 1 FROM admins WHERE user_id = ?", (ADMIN_ID,)
    )
    if await cur.fetchone() is None:
        await _connection.execute(
            "INSERT INTO admins (user_id, added_by, created_at) VALUES (?, NULL, ?)",
            (ADMIN_ID, _now_iso()),
        )
        await _connection.commit()


async def close_db() -> None:
    global _connection
    if _connection:
        await _connection.close()
        _connection = None


# --- Users ---


async def upsert_user(user_id: int, username: Optional[str], first_name: Optional[str]) -> None:
    db = await get_db()
    now = _now_iso()
    await db.execute(
        """
        INSERT INTO users (user_id, username, first_name, created_at, last_active)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name,
            last_active = excluded.last_active
        """,
        (user_id, username or "", first_name or "", now, now),
    )
    await db.commit()


async def count_users() -> int:
    db = await get_db()
    cur = await db.execute("SELECT COUNT(*) FROM users")
    row = await cur.fetchone()
    return int(row[0]) if row else 0


# --- Settings ---


async def get_setting(key: str, default: str = "") -> str:
    db = await get_db()
    cur = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = await cur.fetchone()
    return str(row[0]) if row else default


async def set_setting(key: str, value: str) -> None:
    db = await get_db()
    await db.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    await db.commit()


async def is_bot_running() -> bool:
    return (await get_setting("bot_running", "1")) == "1"


# --- Admins ---


async def is_admin(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    db = await get_db()
    cur = await db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
    return await cur.fetchone() is not None


async def list_admins() -> list[dict[str, Any]]:
    db = await get_db()
    cur = await db.execute(
        "SELECT user_id, added_by, created_at FROM admins ORDER BY created_at"
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def add_admin(user_id: int, added_by: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO admins (user_id, added_by, created_at) VALUES (?, ?, ?)",
            (user_id, added_by, _now_iso()),
        )
        await db.commit()
        return True
    except aiosqlite.IntegrityError:
        return False


async def remove_admin(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return False
    db = await get_db()
    cur = await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    await db.commit()
    return cur.rowcount > 0


# --- Subjects ---


async def add_subject(name: str) -> tuple[bool, Optional[int]]:
    db = await get_db()
    cur = await db.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM subjects")
    row = await cur.fetchone()
    sort_order = int(row[0]) if row else 0
    try:
        await db.execute(
            "INSERT INTO subjects (name, sort_order) VALUES (?, ?)",
            (name.strip(), sort_order),
        )
        await db.commit()
        cur = await db.execute("SELECT last_insert_rowid()")
        rid = await cur.fetchone()
        return True, int(rid[0]) if rid else None
    except aiosqlite.IntegrityError:
        return False, None


async def get_subject(subject_id: int) -> Optional[dict[str, Any]]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def list_subjects_page(offset: int, limit: int) -> list[dict[str, Any]]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM subjects ORDER BY sort_order, id LIMIT ? OFFSET ?",
        (limit, offset),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def count_subjects() -> int:
    db = await get_db()
    cur = await db.execute("SELECT COUNT(*) FROM subjects")
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def update_subject_name(subject_id: int, name: str) -> bool:
    db = await get_db()
    try:
        await db.execute(
            "UPDATE subjects SET name = ? WHERE id = ?", (name.strip(), subject_id)
        )
        await db.commit()
        return True
    except aiosqlite.IntegrityError:
        return False


async def delete_subject(subject_id: int) -> None:
    db = await get_db()
    await db.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))
    await db.commit()


async def reorder_subjects(ordered_ids: list[int]) -> None:
    db = await get_db()
    for i, sid in enumerate(ordered_ids):
        await db.execute(
            "UPDATE subjects SET sort_order = ? WHERE id = ?", (i, sid)
        )
    await db.commit()


async def list_all_subject_ids_ordered() -> list[int]:
    db = await get_db()
    cur = await db.execute("SELECT id FROM subjects ORDER BY sort_order, id")
    rows = await cur.fetchall()
    return [int(r[0]) for r in rows]


# --- Lectures ---


async def add_lecture(
    subject_id: int,
    title: str,
    file_id: str,
    file_unique_id: Optional[str],
    file_name: Optional[str],
) -> int:
    db = await get_db()
    cur = await db.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM lectures WHERE subject_id = ?",
        (subject_id,),
    )
    row = await cur.fetchone()
    sort_order = int(row[0]) if row else 0
    now = _now_iso()
    await db.execute(
        """
        INSERT INTO lectures (subject_id, title, file_id, file_unique_id, file_name, sort_order, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (subject_id, title, file_id, file_unique_id, file_name, sort_order, now),
    )
    await db.commit()
    cur = await db.execute("SELECT last_insert_rowid()")
    rid = await cur.fetchone()
    return int(rid[0]) if rid else 0


async def get_lecture(lecture_id: int) -> Optional[dict[str, Any]]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM lectures WHERE id = ?", (lecture_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def count_lectures_in_subject(subject_id: int) -> int:
    db = await get_db()
    cur = await db.execute(
        "SELECT COUNT(*) FROM lectures WHERE subject_id = ?", (subject_id,)
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def list_lectures_page(subject_id: int, offset: int, limit: int) -> list[dict[str, Any]]:
    db = await get_db()
    cur = await db.execute(
        """
        SELECT * FROM lectures WHERE subject_id = ?
        ORDER BY sort_order, id LIMIT ? OFFSET ?
        """,
        (subject_id, limit, offset),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def count_lectures_total() -> int:
    db = await get_db()
    cur = await db.execute("SELECT COUNT(*) FROM lectures")
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def delete_lecture(lecture_id: int) -> None:
    db = await get_db()
    await db.execute("DELETE FROM lectures WHERE id = ?", (lecture_id,))
    await db.commit()


async def update_lecture_title(lecture_id: int, title: str) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE lectures SET title = ? WHERE id = ?", (title.strip(), lecture_id)
    )
    await db.commit()


async def update_lecture_file(
    lecture_id: int,
    file_id: str,
    file_unique_id: Optional[str],
    file_name: Optional[str],
    title_from_file: Optional[str] = None,
) -> None:
    db = await get_db()
    if title_from_file:
        await db.execute(
            """
            UPDATE lectures SET file_id = ?, file_unique_id = ?, file_name = ?, title = ?
            WHERE id = ?
            """,
            (file_id, file_unique_id, file_name, title_from_file.strip(), lecture_id),
        )
    else:
        await db.execute(
            """
            UPDATE lectures SET file_id = ?, file_unique_id = ?, file_name = ?
            WHERE id = ?
            """,
            (file_id, file_unique_id, file_name, lecture_id),
        )
    await db.commit()


async def move_lecture(lecture_id: int, new_subject_id: int) -> None:
    db = await get_db()
    cur = await db.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM lectures WHERE subject_id = ?",
        (new_subject_id,),
    )
    row = await cur.fetchone()
    sort_order = int(row[0]) if row else 0
    await db.execute(
        "UPDATE lectures SET subject_id = ?, sort_order = ? WHERE id = ?",
        (new_subject_id, sort_order, lecture_id),
    )
    await db.commit()


async def delete_all_lectures_in_subject(subject_id: int) -> int:
    db = await get_db()
    cur = await db.execute(
        "DELETE FROM lectures WHERE subject_id = ?", (subject_id,)
    )
    await db.commit()
    return cur.rowcount


async def reorder_lectures(subject_id: int, ordered_ids: list[int]) -> None:
    db = await get_db()
    for i, lid in enumerate(ordered_ids):
        await db.execute(
            "UPDATE lectures SET sort_order = ? WHERE id = ? AND subject_id = ?",
            (i, lid, subject_id),
        )
    await db.commit()


async def list_all_lectures_in_subject(subject_id: int) -> list[dict[str, Any]]:
    db = await get_db()
    cur = await db.execute(
        """
        SELECT * FROM lectures WHERE subject_id = ?
        ORDER BY sort_order, id
        """,
        (subject_id,),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def list_lecture_ids_in_subject_ordered(subject_id: int) -> list[int]:
    db = await get_db()
    cur = await db.execute(
        "SELECT id FROM lectures WHERE subject_id = ? ORDER BY sort_order, id",
        (subject_id,),
    )
    rows = await cur.fetchall()
    return [int(r[0]) for r in rows]


async def search_lectures_page(
    query: str, offset: int, limit: int
) -> list[dict[str, Any]]:
    db = await get_db()
    like = f"%{query.strip()}%"
    cur = await db.execute(
        """
        SELECT l.*, s.name AS subject_name FROM lectures l
        JOIN subjects s ON s.id = l.subject_id
        WHERE l.title LIKE ? OR s.name LIKE ?
        ORDER BY l.created_at DESC
        LIMIT ? OFFSET ?
        """,
        (like, like, limit, offset),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def count_search_lectures(query: str) -> int:
    db = await get_db()
    like = f"%{query.strip()}%"
    cur = await db.execute(
        """
        SELECT COUNT(*) FROM lectures l
        JOIN subjects s ON s.id = l.subject_id
        WHERE l.title LIKE ? OR s.name LIKE ?
        """,
        (like, like),
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def latest_lectures_page(offset: int, limit: int) -> list[dict[str, Any]]:
    db = await get_db()
    cur = await db.execute(
        """
        SELECT l.*, s.name AS subject_name FROM lectures l
        JOIN subjects s ON s.id = l.subject_id
        ORDER BY l.created_at DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def count_latest_lectures() -> int:
    return await count_lectures_total()


# --- Favorites ---


async def add_favorite(user_id: int, lecture_id: int) -> bool:
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO favorites (user_id, lecture_id, created_at) VALUES (?, ?, ?)",
            (user_id, lecture_id, _now_iso()),
        )
        await db.commit()
        return True
    except aiosqlite.IntegrityError:
        return False


async def remove_favorite(user_id: int, lecture_id: int) -> None:
    db = await get_db()
    await db.execute(
        "DELETE FROM favorites WHERE user_id = ? AND lecture_id = ?",
        (user_id, lecture_id),
    )
    await db.commit()


async def is_favorite(user_id: int, lecture_id: int) -> bool:
    db = await get_db()
    cur = await db.execute(
        "SELECT 1 FROM favorites WHERE user_id = ? AND lecture_id = ?",
        (user_id, lecture_id),
    )
    return await cur.fetchone() is not None


async def list_favorites_page(user_id: int, offset: int, limit: int) -> list[dict[str, Any]]:
    db = await get_db()
    cur = await db.execute(
        """
        SELECT l.*, s.name AS subject_name FROM favorites f
        JOIN lectures l ON l.id = f.lecture_id
        JOIN subjects s ON s.id = l.subject_id
        WHERE f.user_id = ?
        ORDER BY f.created_at DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, limit, offset),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def count_user_favorites(user_id: int) -> int:
    db = await get_db()
    cur = await db.execute(
        "SELECT COUNT(*) FROM favorites WHERE user_id = ?", (user_id,)
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def count_favorites_total() -> int:
    db = await get_db()
    cur = await db.execute("SELECT COUNT(*) FROM favorites")
    row = await cur.fetchone()
    return int(row[0]) if row else 0


# --- Links ---


async def add_link(title: str, url: str) -> int:
    db = await get_db()
    cur = await db.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM links")
    row = await cur.fetchone()
    sort_order = int(row[0]) if row else 0
    await db.execute(
        "INSERT INTO links (title, url, sort_order) VALUES (?, ?, ?)",
        (title.strip(), url.strip(), sort_order),
    )
    await db.commit()
    cur = await db.execute("SELECT last_insert_rowid()")
    rid = await cur.fetchone()
    return int(rid[0]) if rid else 0


async def get_link(link_id: int) -> Optional[dict[str, Any]]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM links WHERE id = ?", (link_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def list_links() -> list[dict[str, Any]]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM links ORDER BY sort_order, id")
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def delete_link(link_id: int) -> None:
    db = await get_db()
    await db.execute("DELETE FROM links WHERE id = ?", (link_id,))
    await db.commit()


async def update_link_title(link_id: int, title: str) -> None:
    db = await get_db()
    await db.execute("UPDATE links SET title = ? WHERE id = ?", (title.strip(), link_id))
    await db.commit()


async def update_link_url(link_id: int, url: str) -> None:
    db = await get_db()
    await db.execute("UPDATE links SET url = ? WHERE id = ?", (url.strip(), link_id))
    await db.commit()


async def reorder_links(ordered_ids: list[int]) -> None:
    db = await get_db()
    for i, lid in enumerate(ordered_ids):
        await db.execute("UPDATE links SET sort_order = ? WHERE id = ?", (i, lid))
    await db.commit()


async def list_all_link_ids_ordered() -> list[int]:
    db = await get_db()
    cur = await db.execute("SELECT id FROM links ORDER BY sort_order, id")
    rows = await cur.fetchall()
    return [int(r[0]) for r in rows]


# --- Requests ---


async def add_request(user_id: int, subject_name: str, lecture_name: str) -> int:
    db = await get_db()
    await db.execute(
        """
        INSERT INTO requests (user_id, subject_name, lecture_name, created_at, status)
        VALUES (?, ?, ?, ?, 'pending')
        """,
        (user_id, subject_name.strip(), lecture_name.strip(), _now_iso()),
    )
    await db.commit()
    cur = await db.execute("SELECT last_insert_rowid()")
    rid = await cur.fetchone()
    return int(rid[0]) if rid else 0


async def count_requests() -> int:
    db = await get_db()
    cur = await db.execute("SELECT COUNT(*) FROM requests")
    row = await cur.fetchone()
    return int(row[0]) if row else 0


# --- Logs ---


async def add_log(admin_id: int, action: str, details: Optional[str] = None) -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO logs (admin_id, action, details, created_at) VALUES (?, ?, ?, ?)",
        (admin_id, action, details, _now_iso()),
    )
    await db.commit()


async def recent_logs(limit: int = 30) -> list[dict[str, Any]]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


# --- Backup ---


async def export_db_blob() -> bytes:
    """Return database file bytes for backup (non-blocking read)."""

    def _read() -> bytes:
        with open(DATABASE_PATH, "rb") as f:
            return f.read()

    import asyncio

    return await asyncio.to_thread(_read)
