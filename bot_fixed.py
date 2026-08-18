import logging
import os
import re
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DB_PATH = os.environ.get("DB_PATH", "tasks.db")

DEFAULT_TIME = "09:00"
DEFAULT_TZ = "Asia/Tehran"
TEHRAN = ZoneInfo(DEFAULT_TZ)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

ADD_TEXT = 1
ADD_CATEGORY = 2
ADD_PRIORITY = 3
ADD_REPEAT = 4


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
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


def get_user_time(user_id):
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


def set_user_time(user_id, hhmm):
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

    conn.execute(
        """
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


def get_task(user_id, task_id):
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


def get_all_tasks(user_id):
    conn = get_db()

    rows = conn.execute(
        """
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
        """,
        (user_id,),
    ).fetchall()

    conn.close()

    return rows


def get_pending_tasks(user_id):
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
            id DESC
        """,
        (user_id,),
    ).fetchall()

    conn.close()

    return rows


def complete_task(user_id, task_id):
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
        (
            now,
            user_id,
            task_id,
        ),
    )

    conn.commit()

    result = cur.rowcount > 0

    conn.close()

    return result


def reopen_task(user_id, task_id):
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
        (
            user_id,
            task_id,
        ),
    )

    conn.commit()

    result = cur.rowcount > 0

    conn.close()

    return result


def delete_task(user_id, task_id):
    conn = get_db()

    cur = conn.execute(
        """
        DELETE FROM tasks
        WHERE user_id = ?
        AND id = ?
        """,
        (
            user_id,
            task_id,
        ),
    )

    conn.commit()

    result = cur.rowcount > 0

    conn.close()

    return result


def clear_pending_tasks(user_id):
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

    count = cur.rowcount

    conn.close()

    return count


def search_tasks(user_id, text):
    conn = get_db()

    rows = conn.execute(
        """
        SELECT *
        FROM tasks
        WHERE user_id = ?
        AND text LIKE ?
        ORDER BY id DESC
        """,
        (
            user_id,
            f"%{text}%",
        ),
    ).fetchall()

    conn.close()

    return rows


def get_stats(user_id):
    conn = get_db()

    total = conn.execute(
        """
        SELECT COUNT(*)
        FROM tasks
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()[0]

    pending = conn.execute(
        """
        SELECT COUNT(*)
        FROM tasks
        WHERE user_id = ?
        AND status = 'pending'
        """,
        (user_id,),
    ).fetchone()[0]

    done = conn.execute(
        """
        SELECT COUNT(*)
        FROM tasks
        WHERE user_id = ?
        AND status = 'done'
        """,
        (user_id,),
    ).fetchone()[0]

    conn.close()

    return total, pending, done


def main_keyboard():
    keyboard = [
        ["➕ افزودن کار", "📋 کارهای من"],
        ["✅ انجام‌شده", "🔎 جستجو"],
        ["⏰ یادآوری", "📊 آمار"],
        ["🗑 پاک کردن", "ℹ️ راهنما"],
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name or "دوست من"

    text = (
        f"سلام {name} 👋\n\n"
        "به ربات مدیریت کارها خوش آمدی.\n\n"
        "از منوی پایین استفاده کن."
    )

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "راهنمای ربات\n\n"
        "/start شروع ربات\n"
        "/help راهنما\n"
        "/add متن کار افزودن سریع\n"
        "/list نمایش کارها\n"
        "/done شماره انجام کار\n"
        "/reopen شماره بازکردن کار\n"
        "/delete شماره حذف کار\n"
        "/search متن جستجو\n"
        "/stats آمار\n"
        "/time 09:00 تنظیم یادآوری\n"
        "/clear حذف کارهای انجام‌نشده\n"
        "/cancel لغو عملیات"
    )

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


def format_task(row):
    status = "✅" if row["status"] == "done" else "⏳"

    category = row["category"] or "عمومی"
    priority = row["priority"] or "متوسط"

    repeat_names = {
        "none": "بدون تکرار",
        "daily": "روزانه",
        "weekly": "هفتگی",
    }

    repeat = repeat_names.get(
        row["repeat_type"],
        "بدون تکرار",
    )

    return (
        f"{status} {row['text']}\n"
        f"🆔 شماره: {row['id']}\n"
        f"📁 دسته: {category}\n"
        f"⚡ اولویت: {priority}\n"
        f"🔁 تکرار: {repeat}"
    )


async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_all_tasks(update.effective_user.id)

    if not rows:
        await update.message.reply_text(
            "هنوز کاری ثبت نکردی.",
            reply_markup=main_keyboard(),
        )
        return

    text = "📋 کارهای تو\n\n"

    for row in rows:
        text += format_task(row)
        text += "\n\n"

    if len(text) > 4000:
        text = text[:3900] + "\n\n..."

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


async def done_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()

    rows = conn.execute(
        """
        SELECT *
        FROM tasks
        WHERE user_id = ?
        AND status = 'done'
        ORDER BY id DESC
        """,
        (update.effective_user.id,),
    ).fetchall()

    conn.close()

    if not rows:
        await update.message.reply_text(
            "هنوز کاری انجام نشده است.",
            reply_markup=main_keyboard(),
        )
        return

    text = "✅ کارهای انجام‌شده\n\n"

    for row in rows:
        text += format_task(row)
        text += "\n\n"

    if len(text) > 4000:
        text = text[:3900] + "\n\n..."

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "مثال:\n/add خرید نان"
        )
        return

    text = " ".join(context.args)

    add_task(
        update.effective_user.id,
        text,
    )

    await update.message.reply_text(
        f"✅ کار ثبت شد:\n{text}",
        reply_markup=main_keyboard(),
    )


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "مثال:\n/done 12"
        )
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "شماره کار باید عدد باشد."
        )
        return

    result = complete_task(
        update.effective_user.id,
        task_id,
    )

    if result:
        await update.message.reply_text(
            "✅ کار انجام شد.",
            reply_markup=main_keyboard(),
        )
    else:
        await update.message.reply_text(
            "کار پیدا نشد یا قبلاً انجام شده است."
        )


async def reopen_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "مثال:\n/reopen 12"
        )
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "شماره کار باید عدد باشد."
        )
        return

    result = reopen_task(
        update.effective_user.id,
        task_id,
    )

    if result:
        await update.message.reply_text(
            "🔄 کار دوباره فعال شد.",
            reply_markup=main_keyboard(),
        )
    else:
        await update.message.reply_text(
            "کار پیدا نشد."
        )


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "مثال:\n/delete 12"
        )
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "شماره کار باید عدد باشد."
        )
        return

    result = delete_task(
        update.effective_user.id,
        task_id,
    )

    if result:
        await update.message.reply_text(
            "🗑 کار حذف شد.",
            reply_markup=main_keyboard(),
        )
    else:
        await update.message.reply_text(
            "کار پیدا نشد."
        )


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton(
                "بله، حذف کن",
                callback_data="clear_yes",
            ),
            InlineKeyboardButton(
                "لغو",
                callback_data="clear_no",
            ),
        ]
    ]

    await update.message.reply_text(
        "همه کارهای انجام‌نشده حذف شوند؟",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "مثال:\n/search خرید"
        )
        return

    query = " ".join(context.args)

    rows = search_tasks(
        update.effective_user.id,
        query,
    )

    if not rows:
        await update.message.reply_text(
            "نتیجه‌ای پیدا نشد."
        )
        return

    text = f"🔎 نتیجه جستجو برای «{query}»\n\n"

    for row in rows:
        text += format_task(row)
        text += "\n\n"

    if len(text) > 4000:
        text = text[:3900] + "\n\n..."

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total, pending, done = get_stats(
        update.effective_user.id
    )

    percent = 0

    if total:
        percent = int((done / total) * 100)

    text = (
        "📊 آمار کارها\n\n"
        f"📋 کل کارها: {total}\n"
        f"⏳ انجام‌نشده: {pending}\n"
        f"✅ انجام‌شده: {done}\n"
        f"📈 درصد انجام: {percent}%"
    )

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


def valid_time(value):
    return bool(
        re.fullmatch(
            r"(?:[01]\d|2[0-3]):[0-5]\d",
            value,
        )
    )


async def time_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        current = get_user_time(
            update.effective_user.id
        )

        await update.message.reply_text(
            f"⏰ ساعت فعلی: {current}\n\n"
            "برای تغییر:\n"
            "/time 09:30"
        )
        return

    hhmm = context.args[0]

    if not valid_time(hhmm):
        await update.message.reply_text(
            "فرمت ساعت اشتباه است.\n"
            "مثال:\n"
            "/time 09:30"
        )
        return

    set_user_time(
        update.effective_user.id,
        hhmm,
    )

    await update.message.reply_text(
        f"⏰ ساعت یادآوری روی {hhmm} تنظیم شد.",
        reply_markup=main_keyboard(),
    )


async def daily_reminder(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TEHRAN)
    current_time = now.strftime("%H:%M")

    conn = get_db()

    users = conn.execute(
        """
        SELECT user_id, remind_time
        FROM user_settings
        WHERE remind_time = ?
        """,
        (current_time,),
    ).fetchall()

    conn.close()

    for user in users:
        tasks = get_pending_tasks(user["user_id"])

        if not tasks:
            continue

        text = "⏰ یادآوری کارهای امروز\n\n"

        for task in tasks:
            text += (
                f"⏳ {task['text']}\n"
                f"⚡ اولویت: {task['priority']}\n"
                f"🆔 شماره: {task['id']}\n\n"
            )

        try:
            await context.bot.send_message(
                chat_id=user["user_id"],
                text=text,
            )
        except Exception as error:
            logger.error(
                "Reminder error: %s",
                error,
            )


async def add_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 متن کار را بفرست.\n\n"
        "مثال:\n"
        "خرید نان و شیر"
    )

    return ADD_TEXT


async def add_text_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    text = update.message.text.strip()

    if not text:
        await update.message.reply_text(
            "متن کار خالی است. دوباره بفرست."
        )
        return ADD_TEXT

    context.user_data["new_task_text"] = text

    keyboard = [
        [
            InlineKeyboardButton(
                "📚 عمومی",
                callback_data="cat_عمومی",
            ),
            InlineKeyboardButton(
                "💼 کار",
                callback_data="cat_کار",
            ),
        ],
        [
            InlineKeyboardButton(
                "🏠 شخصی",
                callback_data="cat_شخصی",
            ),
            InlineKeyboardButton(
                "🛒 خرید",
                callback_data="cat_خرید",
            ),
        ],
    ]

    await update.message.reply_text(
        "📁 دسته کار را انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    return ADD_CATEGORY


async def add_category_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    category = query.data.replace(
        "cat_",
        "",
        1,
    )

    context.user_data["new_task_category"] = category

    keyboard = [
        [
            InlineKeyboardButton(
                "🔴 زیاد",
                callback_data="pri_زیاد",
            ),
            InlineKeyboardButton(
                "🟡 متوسط",
                callback_data="pri_متوسط",
            ),
            InlineKeyboardButton(
                "🟢 کم",
                callback_data="pri_کم",
            ),
        ]
    ]

    await query.edit_message_text(
        "⚡ اولویت را انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    return ADD_PRIORITY


async def add_priority_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    priority = query.data.replace(
        "pri_",
        "",
        1,
    )

    context.user_data["new_task_priority"] = priority

    keyboard = [
        [
            InlineKeyboardButton(
                "بدون تکرار",
                callback_data="rep_none",
            )
        ],
        [
            InlineKeyboardButton(
                "روزانه",
                callback_data="rep_daily",
            ),
            InlineKeyboardButton(
                "هفتگی",
                callback_data="rep_weekly",
            ),
        ],
    ]

    await query.edit_message_text(
        "🔁 نوع تکرار را انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    return ADD_REPEAT


async def add_repeat_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    repeat_type = query.data.replace(
        "rep_",
        "",
        1,
    )

    task_text = context.user_data.get(
        "new_task_text",
        "",
    )

    category = context.user_data.get(
        "new_task_category",
        "عمومی",
    )

    priority = context.user_data.get(
        "new_task_priority",
        "متوسط",
    )

    if not task_text:
        await query.edit_message_text(
            "خطا در ثبت کار. دوباره تلاش کن."
        )

        context.user_data.clear()

        return ConversationHandler.END

    add_task(
        query.from_user.id,
        task_text,
        category,
        priority,
        repeat_type,
    )

    repeat_names = {
        "none": "بدون تکرار",
        "daily": "روزانه",
        "weekly": "هفتگی",
    }

    repeat_name = repeat_names.get(
        repeat_type,
        "بدون تکرار",
    )

    await query.edit_message_text(
        "✅ کار ثبت شد.\n\n"
        f"📝 {task_text}\n"
        f"📁 دسته: {category}\n"
        f"⚡ اولویت: {priority}\n"
        f"🔁 تکرار: {repeat_name}"
    )

    context.user_data.clear()

    return ConversationHandler.END


async def cancel_add(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.clear()

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            "❌ عملیات لغو شد."
        )
    else:
        await update.message.reply_text(
            "❌ عملیات لغو شد.",
            reply_markup=main_keyboard(),
        )

    return ConversationHandler.END


async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    if query.data == "clear_yes":
        count = clear_pending_tasks(
            query.from_user.id
        )

        await query.edit_message_text(
            f"🗑 تعداد {count} کار حذف شد."
        )

    elif query.data == "clear_no":
        await query.edit_message_text(
            "❌ حذف لغو شد."
        )


async def text_buttons(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    text = update.message.text

    if text == "📋 کارهای من":
        await list_tasks(update, context)
        return

    if text == "✅ انجام‌شده":
        await done_list(update, context)
        return

    if text == "📊 آمار":
        await stats_command(update, context)
        return

    if text == "🔎 جستجو":
        await update.message.reply_text(
            "برای جستجو بنویس:\n\n"
            "/search متن"
        )
        return

    if text == "⏰ یادآوری":
        await time_command(update, context)
        return

    if text == "🗑 پاک کردن":
        await clear_command(update, context)
        return

    if text == "ℹ️ راهنما":
        await help_command(update, context)
        return

    await update.message.reply_text(
        "از منوی پایین استفاده کن.",
        reply_markup=main_keyboard(),
    )


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error(
        "Bot error:",
        exc_info=context.error,
    )


def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN در Variables تنظیم نشده است."
        )

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    add_conversation = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^➕ افزودن کار$"),
                add_menu,
            )
        ],
        states={
            ADD_TEXT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    add_text_received,
                )
            ],
            ADD_CATEGORY: [
                CallbackQueryHandler(
                    add_category_callback,
                    pattern=r"^cat_",
                )
            ],
            ADD_PRIORITY: [
                CallbackQueryHandler(
                    add_priority_callback,
                    pattern=r"^pri_",
                )
            ],
            ADD_REPEAT: [
                CallbackQueryHandler(
                    add_repeat_callback,
                    pattern=r"^rep_",
                )
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                cancel_add,
            ),
            CallbackQueryHandler(
                cancel_add,
                pattern=r"^cancel_add$",
            ),
        ],
        allow_reentry=True,
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(
        CommandHandler("add", add_command)
    )

    application.add_handler(
        CommandHandler("list", list_tasks)
    )

    application.add_handler(
        CommandHandler("done", done_command)
    )

    application.add_handler(
        CommandHandler("reopen", reopen_command)
    )

    application.add_handler(
        CommandHandler("delete", delete_command)
    )

    application.add_handler(
        CommandHandler("clear", clear_command)
    )

    application.add_handler(
        CommandHandler("search", search_command)
    )

    application.add_handler(
        CommandHandler("stats", stats_command)
    )

    application.add_handler(
        CommandHandler("time", time_command)
    )

    application.add_handler(
        CommandHandler("cancel", cancel_add)
    )

    application.add_handler(
        add_conversation
    )

    application.add_handler(
        CallbackQueryHandler(callback_handler)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_buttons,
        )
    )

    application.add_error_handler(
        error_handler
    )

    if application.job_queue:
        application.job_queue.run_repeating(
            daily_reminder,
            interval=60,
            first=10,
        )

    logger.info("Bot started.")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
