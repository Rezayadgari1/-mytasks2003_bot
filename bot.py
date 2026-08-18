import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
)

# ============================================================
# تنظیمات
# ============================================================

BOT_TOKEN = os.environ.get(
    "BOT_TOKEN",
    "PUT_YOUR_TELEGRAM_BOT_TOKEN_HERE"
)

DB_PATH = os.environ.get("DB_PATH", "tasks.db")

DEFAULT_TIME = "09:00"
DEFAULT_TZ = "Asia/Tehran"
TEHRAN = ZoneInfo(DEFAULT_TZ)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

# ============================================================
# مراحل افزودن کار
# ============================================================

ADD_TEXT = 1
ADD_CATEGORY = 2
ADD_PRIORITY = 3
ADD_REPEAT = 4


# ============================================================
# دیتابیس
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            remind_time TEXT NOT NULL DEFAULT '09:00',
            timezone TEXT NOT NULL DEFAULT 'Asia/Tehran'
        )
    """)

    columns = {
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(tasks)"
        ).fetchall()
    }

    new_columns = {
        "status": "TEXT NOT NULL DEFAULT 'pending'",
        "completed_at": "TEXT",
        "category": "TEXT NOT NULL DEFAULT 'عمومی'",
        "priority": "TEXT NOT NULL DEFAULT 'متوسط'",
        "repeat_type": "TEXT NOT NULL DEFAULT 'none'",
        "reminder_time": "TEXT",
        "reminder_enabled": "INTEGER NOT NULL DEFAULT 0",
    }

    for column, definition in new_columns.items():
        if column not in columns:
            conn.execute(
                f"ALTER TABLE tasks ADD COLUMN {column} {definition}"
            )

    conn.commit()
    conn.close()


# ============================================================
# تنظیمات کاربر
# ============================================================

def get_user_time(user_id):
    conn = get_db()

    row = conn.execute("""
        SELECT remind_time
        FROM user_settings
        WHERE user_id = ?
    """, (user_id,)).fetchone()

    conn.close()

    if row:
        return row["remind_time"]

    return DEFAULT_TIME


def set_user_time(user_id, hhmm):
    conn = get_db()

    conn.execute("""
        INSERT INTO user_settings
        (user_id, remind_time, timezone)
        VALUES (?, ?, ?)

        ON CONFLICT(user_id)
        DO UPDATE SET
            remind_time = excluded.remind_time,
            timezone = excluded.timezone
    """, (
        user_id,
        hhmm,
        DEFAULT_TZ,
    ))

    conn.commit()
    conn.close()


# ============================================================
# کارها
# ============================================================

def add_task(
    user_id,
    text,
    category="عمومی",
    priority="متوسط",
    repeat_type="none",
):
    text = text.strip()

    if not text:
        return False

    conn = get_db()

    now = datetime.now(TEHRAN).isoformat()

    conn.execute("""
        INSERT INTO tasks (
            user_id,
            text,
            created_at,
            status,
            category,
            priority,
            repeat_type
        )
        VALUES (?, ?, ?, 'pending', ?, ?, ?)
    """, (
        user_id,
        text,
        now,
        category,
        priority,
        repeat_type,
    ))

    conn.commit()
    conn.close()

    return True


def get_task(user_id, task_id):
    conn = get_db()

    row = conn.execute("""
        SELECT *
        FROM tasks
        WHERE user_id = ?
        AND id = ?
    """, (
        user_id,
        task_id,
    )).fetchone()

    conn.close()

    return row


def get_all_tasks(user_id):
    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM tasks
        WHERE user_id = ?
        ORDER BY
            CASE WHEN status = 'pending' THEN 0 ELSE 1 END,
            CASE priority
                WHEN 'زیاد' THEN 1
                WHEN 'متوسط' THEN 2
                WHEN 'کم' THEN 3
                ELSE 4
            END,
            id DESC
    """, (user_id,)).fetchall()

    conn.close()

    return rows


def get_pending_tasks(user_id):
    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM tasks
        WHERE user_id = ?
        AND status = 'pending'
        ORDER BY
            CASE priority
                WHEN 'زیاد' THEN 1
                WHEN 'متوسط' THEN 2
                WHEN 'کم' THEN 3
                ELSE 4
            END,
            id DESC
    """, (user_id,)).fetchall()

    conn.close()

    return rows


def complete_task(user_id, task_id):
    conn = get_db()

    now = datetime.now(TEHRAN).isoformat()

    cur = conn.execute("""
        UPDATE tasks
        SET
            status = 'done',
            completed_at = ?
        WHERE user_id = ?
        AND id = ?
        AND status = 'pending'
    """, (
        now,
        user_id,
        task_id,
    ))

    conn.commit()

    result = cur.rowcount > 0

    conn.close()

    return result


def reopen_task(user_id, task_id):
    conn = get_db()

    cur = conn.execute("""
        UPDATE tasks
        SET
            status = 'pending',
            completed_at = NULL
        WHERE user_id = ?
        AND id = ?
    """, (
        user_id,
        task_id,
    ))

    conn.commit()

    result = cur.rowcount > 0

    conn.close()

    return result


def delete_task(user_id, task_id):
    conn = get_db()

    cur = conn.execute("""
        DELETE FROM tasks
        WHERE user_id = ?
        AND id = ?
    """, (
        user_id,
        task_id,
    ))

    conn.commit()

    result = cur.rowcount > 0

    conn.close()

    return result


def clear_pending_tasks(user_id):
    conn = get_db()

    cur = conn.execute("""
        DELETE FROM tasks
        WHERE user_id = ?
        AND status = 'pending'
    """, (user_id,))

    conn.commit()

    count = cur.rowcount

    conn.close()

    return count


# ============================================================
# جست‌وجوی کار
# ============================================================

def search_tasks(user_id, text):
    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM tasks
        WHERE user_id = ?
        AND text LIKE ?
        ORDER BY id DESC
