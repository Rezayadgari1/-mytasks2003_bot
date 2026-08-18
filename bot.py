import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta
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

    return row["remind_time"] if row else DEFAULT_TIME


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
    category: str = "عمومی",
    priority: str = "متوسط",
    repeat_type: str = "none",
):
    text = text.strip()
    if not text:
        return False

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
    return True


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

    cur = conn.execute(
        """
        DELETE FROM tasks
        WHERE user_id = ?
          AND status = 'pending'
        """,
        (user_id,),
    )

    conn.commit()
    deleted = cur.rowcount
    conn.close()
    return deleted


# ============================================================
# کارهای تکراری
# ============================================================

def create_recurring_tasks():
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
        try:
            created_date = datetime.fromisoformat(
                task["created_at"]
            ).astimezone(TEHRAN).date()
        except Exception:
            continue

        if task["repeat_type"] == "روزانه":
            should_create = True
        elif task["repeat_type"] == "هفتگی":
            should_create = created_date.weekday() == today.weekday()
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
# امتیاز و گزارش
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

    return round((done / total) * 100), done, total


def get_period_report(user_id: int, start, end):
    conn = get_db()

    row = conn.execute(
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
            end.isoformat(),
        ),
    ).fetchone()

    conn.close()

    total = row["total"] or 0
    done = row["done"] or 0
    score = round((done / total) * 100) if total else 0

    return done, total, score


def get_weekly_report(user_id: int):
    today = datetime.now(TEHRAN).date()
    start = today - timedelta(days=6)
    done, total, score = get_period_report(user_id, start, today)
    return start, today, done, total, score


def get_monthly_report(user_id: int):
    today = datetime.now(TEHRAN).date()
    start = today.replace(day=1)
    done, total, score = get_period_report(user_id, start, today)
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
        icon = "☑️" if task["status"] == "done" else "☐"

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
        return "📋 کاری ثبت نشده است."

    lines = ["📋 کارهای تو", ""]

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
                    "   انجام شد: "
                    f"{completed.strftime('%Y/%m/%d %H:%M')}"
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
# /start و /help
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    set_user_time(user_id, get_user_time(user_id))
    schedule_reminder_for_user(context.application, user_id)

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
    text = (update.message.text or "").strip()

    if not text:
        await update.message.reply_text("متن کار نمی‌تواند خالی باشد.")
        return ADD_TEXT

    context.user_data["task_text"] = text

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
            InlineKeyboardButton(
                "بدون تکرار",
                callback_data="repeat:none",
            ),
        ],
        [
            InlineKeyboardButton(
                "هر روز",
                callback_data="repeat:روزانه",
            ),
            InlineKeyboardButton(
                "هر هفته",
                callback_data="repeat:هفتگی",
            ),
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

    if not text:
        await query.edit_message_text("خطا: متن کار پیدا نشد.")
        context.user_data.clear()
        return ConversationHandler.END

    add_task(
        user_id,
        text,
        category,
        priority,
        repeat_type,
    )

    context.user_data.clear()

    await query.edit_message_text("✅ کار با موفقیت ثبت شد.")

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
# دکمه‌های کار
# ============================================================

async def task_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    try:
        action, task_id_text = query.data.split(":", 1)
        task_id = int(task_id_text)
    except (ValueError, AttributeError):
        await query.answer("دکمه نامعتبر است.", show_alert=True)
        return

    if action == "toggle":
        task = get_task(user_id, task_id)

        if not task:
            await query.answer("کار پیدا نشد.", show_alert=True)
            return

        if task["status"] == "done":
            reopen_task(user_id, task_id)
        else:
            complete_task(user_id, task_id)

    elif action == "delete":
        if not delete_task(user_id, task_id):
            await query.answer("کار پیدا نشد.", show_alert=True)
            return
    else:
        return

    rows = get_all_tasks(user_id)

    try:
        await query.edit_message_text(
            format_tasks(rows),
            reply_markup=task_keyboard(rows) if rows else None,
        )
    except Exception as exc:
        logger.debug("Could not edit task message: %s", exc)


# ============================================================
# دستورات قدیمی
# ============================================================

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = " ".join(context.args).strip()

    if not text:
        await update.message.reply_text("مثال:\n/add خرید نان")
        return

    add_task(user_id, text)

    await update.message.reply_text(
        f"✅ کار ثبت شد:\n{text}",
        reply_markup=main_menu(),
    )


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_tasks(update, context)


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text("مثال:\n/done 12")
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("شماره کار درست نیست.")
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
    deleted = clear_pending_tasks(user_id)

    await update.message.reply_text(
        f"🗑 {deleted} کار انجام‌نشده پاک شد.\n"
        "سابقه کارهای انجام‌شده باقی ماند."
    )


# ============================================================
# ساعت یادآوری
# ============================================================

async def settime_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text("مثال:\n/settime 08:30")
        return

    hhmm = context.args[0]

    if not re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", hhmm):
        await update.message.reply_text(
            "فرمت ساعت درست نیست.\n"
            "مثال: /settime 21:15"
        )
        return

    set_user_time(user_id, hhmm)
    schedule_reminder_for_user(context.application, user_id)

    await update.message.reply_text(
        f"⏰ ساعت یادآوری روی {hhmm} تنظیم شد."
    )


async def mytime_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    await update.message.reply_text(
        f"⏰ ساعت یادآوری تو: {get_user_time(user_id)}"
    )


# ============================================================
# گزارش‌ها
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


async def suggestion_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    try:
        index = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        return

    if not 0 <= index < len(SUGGESTIONS):
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

    try:
        create_recurring_tasks()
        rows = get_pending_tasks(user_id)

        if not rows:
            text = (
                "☀️ صبح بخیر!\n\n"
                "امروز کاری برای انجام نداری. 😊"
            )
        else:
            text = (
                "☀️ صبح بخیر!\n\n"
                "📋 کارهای امروز:\n\n"
                f"{format_tasks(rows)}"
            )

        await context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=task_keyboard(rows) if rows else main_menu(),
        )

    except Exception as exc:
        logger.exception(
            "Error sending reminder to user %s: %s",
            user_id,
            exc,
        )


def remove_user_reminders(application: Application, user_id: int):
    jobs = application.job_queue.get_jobs_by_name(
        f"daily_reminder_{user_id}"
    )

    for job in jobs:
        job.schedule_removal()


def schedule_reminder_for_user(application: Application, user_id: int):
    if application.job_queue is None:
        logger.error(
            "JobQueue is unavailable. Install python-telegram-bot with "
            "the job-queue extra."
        )
        return

    remove_user_reminders(application, user_id)

    hhmm = get_user_time(user_id)

    hour, minute = map(int, hhmm.split(":"))

    from datetime import time as dt_time

    reminder_time = dt_time(
        hour=hour,
        minute=minute,
        tzinfo=TEHRAN,
    )

    application.job_queue.run_daily(
        send_daily_reminder,
        time=reminder_time,
        data={"user_id": user_id},
        name=f"daily_reminder_{user_id}",
    )

    logger.info(
        "Daily reminder scheduled for user %s at %s",
        user_id,
        hhmm,
    )


# ============================================================
# تنظیمات
# ============================================================

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    keyboard = [
        [
            InlineKeyboardButton(
                "⏰ تغییر ساعت یادآوری",
                callback_data="settings:time",
            )
        ],
        [
            InlineKeyboardButton(
                "📋 نمایش ساعت فعلی",
                callback_data="settings:showtime",
            )
        ],
    ]

    await update.message.reply_text(
        "⚙️ تنظیمات",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def settings_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    action = query.data.split(":", 1)[1]
    user_id = query.from_user.id

    if action == "showtime":
        await query.message.reply_text(
            f"⏰ ساعت یادآوری تو: {get_user_time(user_id)}"
        )
        return

    if action == "time":
        await query.message.reply_text(
            "برای تغییر ساعت، این دستور را بفرست:\n\n"
            "/settime 08:30"
        )


# ============================================================
# مدیریت پیام‌های منوی اصلی
# ============================================================

async def menu_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if text == "➕ افزودن کار":
        return await add_start(update, context)

    if text == "📋 کارهای من":
        return await show_tasks(update, context)

    if text == "📊 امتیاز امروز":
        return await daily_score(update, context)

    if text == "📅 گزارش هفتگی":
        return await weekly_report(update, context)

    if text == "📈 گزارش ماهانه":
        return await monthly_report(update, context)

    if text == "💡 پیشنهاد کار":
        return await suggestions(update, context)

    if text == "⏰ تنظیم یادآوری":
        user_id = update.effective_user.id
        await update.message.reply_text(
            "⏰ ساعت فعلی یادآوری:\n\n"
            f"{get_user_time(user_id)}\n\n"
            "برای تغییر:\n"
            "/settime 08:30"
        )
        return

    if text == "⚙️ تنظیمات":
        return await settings_command(update, context)

    await update.message.reply_text(
        "از منوی پایین یک گزینه انتخاب کن.",
        reply_markup=main_menu(),
    )


# ============================================================
# خطا
# ============================================================

async def error_handler(update, context):
    logger.exception(
        "Unhandled exception while processing update",
        exc_info=context.error,
    )


# ============================================================
# اجرای ربات
# ============================================================

def main():
    if not BOT_TOKEN or BOT_TOKEN == "PUT_YOUR_TELEGRAM_BOT_TOKEN_HERE":
        raise RuntimeError(
            "BOT_TOKEN is not set. Add BOT_TOKEN to Railway Variables."
        )

    init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    # مکالمه افزودن کار
    add_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("addtask", add_start),
        ],
        states={
            ADD_TEXT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    add_text,
                )
            ],
            ADD_CATEGORY: [
                CallbackQueryHandler(
                    add_category,
                    pattern=r"^cat:",
                )
            ],
            ADD_PRIORITY: [
                CallbackQueryHandler(
                    add_priority,
                    pattern=r"^priority:",
                )
            ],
            ADD_REPEAT: [
                CallbackQueryHandler(
                    add_repeat,
                    pattern=r"^repeat:",
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_add),
        ],
        allow_reentry=True,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    application.add_handler(add_conversation)

    application.add_handler(
        CallbackQueryHandler(
            add_category,
            pattern=r"^cat:",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            add_priority,
            pattern=r"^priority:",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            add_repeat,
            pattern=r"^repeat:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            task_button,
            pattern=r"^(toggle|delete):",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            suggestion_button,
            pattern=r"^suggest:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            settings_button,
            pattern=r"^settings:",
        )
    )

    application.add_handler(CommandHandler("add", add_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("done", done_command))
    application.add_handler(CommandHandler("clear", clear_command))
    application.add_handler(CommandHandler("settime", settime_command))
    application.add_handler(CommandHandler("mytime", mytime_command))
    application.add_handler(CommandHandler("weekly", weekly_report))
    application.add_handler(CommandHandler("monthly", monthly_report))
    application.add_handler(CommandHandler("settings", settings_command))

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            menu_message,
        )
    )

    application.add_error_handler(error_handler)

    logger.info("Bot is starting...")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
