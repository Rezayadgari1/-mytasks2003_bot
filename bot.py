import logging
import os
import re
import sqlite3
from datetime import datetime, time, timedelta
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

BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUT_YOUR_TELEGRAM_BOT_TOKEN_HERE")

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
# وضعیت افزودن کار
# ============================================================

ADD_TEXT, ADD_CATEGORY, ADD_PRIORITY, ADD_REPEAT = range(4)


# ============================================================
# دیتابیس
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            remind_time TEXT NOT NULL DEFAULT '09:00',
            timezone TEXT NOT NULL DEFAULT 'Asia/Tehran'
        )
        """
    )

    # --------------------------------------------------------
    # اضافه کردن ستون‌های جدید به دیتابیس قدیمی
    # --------------------------------------------------------

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
    }

    new_columns = {
        "status": "TEXT NOT NULL DEFAULT 'pending'",
        "completed_at": "TEXT",
        "category": "TEXT NOT NULL DEFAULT 'عمومی'",
        "priority": "TEXT NOT NULL DEFAULT 'متوسط'",
        "repeat_type": "TEXT NOT NULL DEFAULT 'none'",
        "last_reminded": "TEXT",
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

def get_user_time(user_id: int):
    conn = get_db()

    row = conn.execute(
        """
        SELECT remind_time
        FROM user_settings
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()

    conn.close()

    if row:
        return row["remind_time"]

    return DEFAULT_TIME


def set_user_time(user_id: int, hhmm: str):
    conn = get_db()

    conn.execute(
        """
        INSERT INTO user_settings
        (user_id, remind_time, timezone)
        VALUES (?, ?, ?)

        ON CONFLICT(user_id)
        DO UPDATE SET
            remind_time = excluded.remind_time,
            timezone = excluded.timezone
        """,
        (user_id, hhmm, DEFAULT_TZ),
    )

    conn.commit()
    conn.close()


# ============================================================
# کارها
# ============================================================

def add_task(
    user_id: int,
    text: str,
    category: str,
    priority: str,
    repeat_type: str,
):
    conn = get_db()

    now = datetime.now(TEHRAN).isoformat()

    conn.execute(
        """
        INSERT INTO tasks
        (
            user_id,
            text,
            created_at,
            status,
            category,
            priority,
            repeat_type
        )
        VALUES (?, ?, ?, 'pending', ?, ?, ?)
        """,
        (
            user_id,
            text,
            now,
            category,
            priority,
            repeat_type,
        ),
    )

    conn.commit()
    conn.close()


def get_pending_tasks(user_id: int):
    conn = get_db()

    rows = conn.execute(
        """
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
            id
        """,
        (user_id,),
    ).fetchall()

    conn.close()

    return rows


def get_all_tasks(user_id: int):
    conn = get_db()

    rows = conn.execute(
        """
        SELECT *
        FROM tasks
        WHERE user_id = ?
        ORDER BY id DESC
        """,
        (user_id,),
    ).fetchall()

    conn.close()

    return rows


def get_task(user_id: int, task_id: int):
    conn = get_db()

    row = conn.execute(
        """
        SELECT *
        FROM tasks
        WHERE user_id = ?
        AND id = ?
        """,
        (user_id, task_id),
    ).fetchone()

    conn.close()

    return row


def complete_task(user_id: int, task_id: int):
    conn = get_db()

    now = datetime.now(TEHRAN).isoformat()

    cur = conn.execute(
        """
        UPDATE tasks
        SET
            status = 'done',
            completed_at = ?
        WHERE user_id = ?
        AND id = ?
        AND status = 'pending'
        """,
        (now, user_id, task_id),
    )

    conn.commit()

    success = cur.rowcount > 0

    conn.close()

    return success


def reopen_task(user_id: int, task_id: int):
    conn = get_db()

    cur = conn.execute(
        """
        UPDATE tasks
        SET
            status = 'pending',
            completed_at = NULL
        WHERE user_id = ?
        AND id = ?
        """,
        (user_id, task_id),
    )

    conn.commit()

    success = cur.rowcount > 0

    conn.close()

    return success


def delete_task(user_id: int, task_id: int):
    conn = get_db()

    cur = conn.execute(
        """
        DELETE FROM tasks
        WHERE user_id = ?
        AND id = ?
        """,
        (user_id, task_id),
    )

    conn.commit()

    success = cur.rowcount > 0

    conn.close()

    return success


def clear_pending_tasks(user_id: int):
    conn = get_db()

    conn.execute(
        """
        DELETE FROM tasks
        WHERE user_id = ?
        AND status = 'pending'
        """,
        (user_id,),
    )

    conn.commit()
    conn.close()


# ============================================================
# کارهای تکراری
# ============================================================

def create_recurring_tasks():
    """
    برای کارهای تکراری که مربوط به امروز هستند،
    یک نسخه جدید ایجاد می‌کند.
    """

    today = datetime.now(TEHRAN).date()

    conn = get_db()

    recurring = conn.execute(
        """
        SELECT *
        FROM tasks
        WHERE repeat_type != 'none'
        """
    ).fetchall()

    for task in recurring:

        if task["repeat_type"] == "روزانه":
            should_create = True

        elif task["repeat_type"] == "هفتگی":
            should_create = datetime.fromisoformat(
                task["created_at"]
            ).date().weekday() == today.weekday()

        else:
            should_create = False

        if not should_create:
            continue

        exists = conn.execute(
            """
            SELECT id
            FROM tasks
            WHERE user_id = ?
            AND text = ?
            AND repeat_type = ?
            AND date(created_at) = ?
            """,
            (
                task["user_id"],
                task["text"],
                task["repeat_type"],
                today.isoformat(),
            ),
        ).fetchone()

        if exists:
            continue

        conn.execute(
            """
            INSERT INTO tasks
            (
                user_id,
                text,
                created_at,
                status,
                category,
                priority,
                repeat_type
            )
            VALUES (?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                task["user_id"],
                task["text"],
                datetime.now(TEHRAN).isoformat(),
                task["category"],
                task["priority"],
                task["repeat_type"],
            ),
        )

    conn.commit()
    conn.close()


# ============================================================
# امتیاز
# ============================================================

def get_daily_score(user_id: int):
    conn = get_db()

    today = datetime.now(TEHRAN).date().isoformat()

    total = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM tasks
        WHERE user_id = ?
        AND date(created_at) = ?
        """,
        (user_id, today),
    ).fetchone()["count"]

    done = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM tasks
        WHERE user_id = ?
        AND date(created_at) = ?
        AND status = 'done'
        """,
        (user_id, today),
    ).fetchone()["count"]

    conn.close()

    if total == 0:
        return 0, 0, 0

    score = round((done / total) * 100)

    return score, done, total


def get_weekly_report(user_id: int):
    conn = get_db()

    today = datetime.now(TEHRAN).date()

    start = today - timedelta(days=6)

    rows = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(
                CASE
                    WHEN status = 'done' THEN 1
                    ELSE 0
                END
            ) AS done
        FROM tasks
        WHERE user_id = ?
        AND date(created_at) BETWEEN ? AND ?
        """,
        (
            user_id,
            start.isoformat(),
            today.isoformat(),
        ),
    ).fetchone()

    conn.close()

    total = rows["total"] or 0
    done = rows["done"] or 0

    score = round((done / total) * 100) if total else 0

    return start, today, done, total, score


def get_monthly_report(user_id: int):
    conn = get_db()

    today = datetime.now(TEHRAN).date()

    start = today.replace(day=1)

    rows = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(
                CASE
                    WHEN status = 'done' THEN 1
                    ELSE 0
                END
            ) AS done
        FROM tasks
        WHERE user_id = ?
        AND date(created_at) BETWEEN ? AND ?
        """,
        (
            user_id,
            start.isoformat(),
            today.isoformat(),
        ),
    ).fetchone()

    conn.close()

    total = rows["total"] or 0
    done = rows["done"] or 0

    score = round((done / total) * 100) if total else 0

    return start, today, done, total, score


# ============================================================
# منوی اصلی
# ============================================================

def main_menu():
    return ReplyKeyboardMarkup(
        [
            ["➕ افزودن کار", "📋 کارهای من"],
            ["📊 امتیاز امروز", "📅 گزارش هفتگی"],
            ["📈 گزارش ماهانه", "💡 پیشنهاد کار"],
            ["⏰ تنظیم یادآوری", "⚙️ تنظیمات"],
        ],
        resize_keyboard=True,
    )


# ============================================================
# نمایش کارها
# ============================================================

def task_keyboard(rows):
    keyboard = []

    for task in rows:

        if task["status"] == "done":
            icon = "☑️"
        else:
            icon = "☐"

        priority_icon = {
            "زیاد": "🔴",
            "متوسط": "🟡",
            "کم": "🟢",
        }.get(task["priority"], "⚪")

        title = f"{icon} {priority_icon} {task['text']}"

        if len(title) > 55:
            title = title[:52] + "..."

        keyboard.append(
            [
                InlineKeyboardButton(
                    title,
                    callback_data=f"toggle:{task['id']}",
                )
            ]
        )

        keyboard.append(
            [
                InlineKeyboardButton(
                    "🗑 حذف",
                    callback_data=f"delete:{task['id']}",
                )
            ]
        )

    return InlineKeyboardMarkup(keyboard)


def format_tasks(rows):
    if not rows:
        return "📋 امروز کاری ثبت نشده است."

    lines = [
        "📋 کارهای تو",
        "",
    ]

    for index, task in enumerate(rows, start=1):

        status = "☑️" if task["status"] == "done" else "☐"

        priority = {
            "زیاد": "🔴",
            "متوسط": "🟡",
            "کم": "🟢",
        }.get(task["priority"], "⚪")

        repeat = ""

        if task["repeat_type"] != "none":
            repeat = f" 🔁{task['repeat_type']}"

        lines.append(
            f"{index}. {status} {priority} {task['text']}"
            f" | {task['category']}{repeat}"
        )

        if task["status"] == "done" and task["completed_at"]:
            try:
                completed = datetime.fromisoformat(
                    task["completed_at"]
                ).astimezone(TEHRAN)

                lines.append(
                    f"   انجام شد: {completed.strftime('%Y/%m/%d %H:%M')}"
                )
            except Exception:
                pass

    return "\n".join(lines)


async def show_tasks(update, context):
    user_id = update.effective_user.id

    rows = get_all_tasks(user_id)

    await update.message.reply_text(
        format_tasks(rows),
        reply_markup=task_keyboard(rows) if rows else None,
    )


# ============================================================
# /start
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    schedule_reminder_for_user(
        context.application,
        user_id,
    )

    await update.message.reply_text(
        "سلام 👋\n\n"
        "من ربات مدیریت کارهای روزانه هستم.\n\n"
        "از منوی پایین استفاده کن.",
        reply_markup=main_menu(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "راهنما:\n\n"
        "/add متن کار\n"
        "/list\n"
        "/done شماره\n"
        "/clear\n"
        "/settime 08:30\n"
        "/mytime\n"
        "/weekly\n"
        "/monthly",
        reply_markup=main_menu(),
    )


# ============================================================
# افزودن کار
# ============================================================

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "متن کار را بفرست.\n\n"
        "مثال:\n"
        "ورزش ۳۰ دقیقه"
    )

    return ADD_TEXT


async def add_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["task_text"] = update.message.text.strip()

    keyboard = [
        [
            InlineKeyboardButton("🏠 شخصی", callback_data="cat:شخصی"),
            InlineKeyboardButton("💼 کار", callback_data="cat:کار"),
        ],
        [
            InlineKeyboardButton("🏃 ورزش", callback_data="cat:ورزش"),
            InlineKeyboardButton("📚 مطالعه", callback_data="cat:مطالعه"),
        ],
        [
            InlineKeyboardButton("🛒 خرید", callback_data="cat:خرید"),
            InlineKeyboardButton("📌 عمومی", callback_data="cat:عمومی"),
        ],
    ]

    await update.message.reply_text(
        "دسته‌بندی کار را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    return ADD_CATEGORY


async def add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    category = query.data.split(":", 1)[1]

    context.user_data["category"] = category

    keyboard = [
        [
            InlineKeyboardButton("🔴 زیاد", callback_data="priority:زیاد"),
            InlineKeyboardButton("🟡 متوسط", callback_data="priority:متوسط"),
            InlineKeyboardButton("🟢 کم", callback_data="priority:کم"),
        ]
    ]

    await query.edit_message_text(
        "اولویت کار را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    return ADD_PRIORITY


async def add_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    priority = query.data.split(":", 1)[1]

    context.user_data["priority"] = priority

    keyboard = [
        [
            InlineKeyboardButton("بدون تکرار", callback_data="repeat:none"),
        ],
        [
            InlineKeyboardButton("هر روز", callback_data="repeat:روزانه"),
            InlineKeyboardButton("هر هفته", callback_data="repeat:هفتگی"),
        ],
    ]

    await query.edit_message_text(
        "تکرار کار را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    return ADD_REPEAT


async def add_repeat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    repeat_type = query.data.split(":", 1)[1]

    user_id = query.from_user.id

    text = context.user_data.get("task_text", "")
    category = context.user_data.get("category", "عمومی")
    priority = context.user_data.get("priority", "متوسط")

    add_task(
        user_id,
        text,
        category,
        priority,
        repeat_type,
    )

    context.user_data.clear()

    await query.edit_message_text(
        "✅ کار با موفقیت ثبت شد."
    )

    await query.message.reply_text(
        "از منوی پایین ادامه بده.",
        reply_markup=main_menu(),
    )

    return ConversationHandler.END


async def cancel_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "افزودن کار لغو شد.",
        reply_markup=main_menu(),
    )

    return ConversationHandler.END


# ============================================================
# دکمه کار
# ============================================================

async def task_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    action, task_id_text = query.data.split(":")

    task_id = int(task_id_text)

    if action == "toggle":

        task = get_task(user_id, task_id)

        if not task:
            await query.answer(
                "کار پیدا نشد.",
                show_alert=True,
            )
            return

        if task["status"] == "done":
            reopen_task(user_id, task_id)
            await query.answer("کار دوباره فعال شد.")
        else:
            complete_task(user_id, task_id)
            await query.answer("✅ کار انجام شد.")

    elif action == "delete":

        if delete_task(user_id, task_id):
            await query.answer("کار حذف شد.")
        else:
            await query.answer(
                "کار پیدا نشد.",
                show_alert=True,
            )

    rows = get_all_tasks(user_id)

    text = format_tasks(rows)

    try:
        await query.edit_message_text(
            text,
            reply_markup=task_keyboard(rows) if rows else None,
        )
    except Exception:
        pass


# ============================================================
# دستورات قدیمی
# ============================================================

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    text = " ".join(context.args).strip()

    if not text:
        await update.message.reply_text(
            "مثال:\n/add خرید نان"
        )
        return

    add_task(
        user_id,
        text,
        "عمومی",
        "متوسط",
        "none",
    )

    await update.message.reply_text(
        f"✅ کار ثبت شد:\n{text}"
    )


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_tasks(update, context)


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "مثال:\n/done 12"
        )
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "شماره کار درست نیست."
        )
        return

    if complete_task(user_id, task_id):
        await update.message.reply_text(
            "☑️ کار انجام شد و سابقه آن حفظ شد."
        )
    else:
        await update.message.reply_text(
            "کار پیدا نشد یا قبلاً انجام شده است."
        )


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    clear_pending_tasks(user_id)

    await update.message.reply_text(
        "کارهای انجام‌نشده پاک شدند.\n"
        "سابقه کارهای انجام‌شده باقی ماند."
    )


# ============================================================
# ساعت یادآوری
# ============================================================

async def settime_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "مثال:\n/settime 08:30"
        )
        return

    hhmm = context.args[0]

    if not re.match(
        r"^([01]\d|2[0-3]):([0-5]\d)$",
        hhmm,
    ):
        await update.message.reply_text(
            "فرمت ساعت درست نیست.\n"
            "مثال: /settime 21:15"
        )
        return

    set_user_time(user_id, hhmm)

    schedule_reminder_for_user(
        context.application,
        user_id,
    )

    await update.message.reply_text(
        f"⏰ ساعت یادآوری روی {hhmm} تنظیم شد."
    )


async def mytime_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    await update.message.reply_text(
        f"⏰ ساعت یادآوری تو: {get_user_time(user_id)}"
    )


# ============================================================
# گزارش روزانه
# ============================================================

async def daily_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    score, done, total = get_daily_score(user_id)

    await update.message.reply_text(
        "📊 امتیاز امروز\n\n"
        f"انجام‌شده: {done}\n"
        f"کل کارها: {total}\n"
        f"امتیاز: {score}%"
    )


# ============================================================
# گزارش هفتگی
# ============================================================

async def weekly_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    start, end, done, total, score = get_weekly_report(user_id)

    await update.message.reply_text(
        "📅 گزارش هفتگی\n\n"
        f"از {start.strftime('%Y/%m/%d')}\n"
        f"تا {end.strftime('%Y/%m/%d')}\n\n"
        f"کل کارها: {total}\n"
        f"انجام‌شده: {done}\n"
        f"امتیاز: {score}%"
    )


# ============================================================
# گزارش ماهانه
# ============================================================

async def monthly_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    start, end, done, total, score = get_monthly_report(user_id)

    await update.message.reply_text(
        "📈 گزارش ماهانه\n\n"
        f"از {start.strftime('%Y/%m/%d')}\n"
        f"تا {end.strftime('%Y/%m/%d')}\n\n"
        f"کل کارها: {total}\n"
        f"انجام‌شده: {done}\n"
        f"امتیاز: {score}%"
    )


# ============================================================
# پیشنهاد کار
# ============================================================

SUGGESTIONS = [
    "ورزش ۳۰ دقیقه",
    "مطالعه ۲۰ دقیقه",
    "نوشیدن آب",
    "مرتب کردن اتاق",
    "خرید روزانه",
    "پیاده‌روی",
    "بررسی کارهای فردا",
    "مطالعه کتاب",
    "تمرین زبان",
    "رسیدگی به ماشین",
]


async def suggestions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []

    for i, item in enumerate(SUGGESTIONS):
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"➕ {item}",
                    callback_data=f"suggest:{i}",
                )
            ]
        )

    await update.message.reply_text(
        "💡 یک کار را برای افزودن انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def suggestion_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    index = int(query.data.split(":")[1])

    if index < 0 or index >= len(SUGGESTIONS):
        return

    user_id = query.from_user.id

    add_task(
        user_id,
        SUGGESTIONS[index],
        "عمومی",
        "متوسط",
        "none",
    )

    await query.edit_message_text(
        f"✅ اضافه شد:\n{SUGGESTIONS[index]}"
    )


# ============================================================
# یادآوری روزانه
# ============================================================

async def send_daily_reminder(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.data["user_id"]

    create_recurring_tasks()

    rows = get_pending_tasks(user_id)

    if not rows:
        text = (
            "☀️ صبح بخیر!\
