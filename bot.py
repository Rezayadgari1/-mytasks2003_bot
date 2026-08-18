import logging
import os
import sqlite3
from datetime import datetime
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

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DB_PATH = os.environ.get("DB_PATH", "goals.db")

TEHRAN = ZoneInfo("Asia/Tehran")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

ADD_NAME = 1
ADD_CATEGORY = 2
ADD_TIME = 3


def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'عمومی',
            reminder_time TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS goal_days (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            goal_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            completed_at TEXT,
            UNIQUE(goal_id, goal_date)
        )
    """)

    conn.commit()
    conn.close()


def today():
    return datetime.now(TEHRAN).strftime("%Y-%m-%d")


def date_text(date_value):
    days = {
        0: "دوشنبه",
        1: "سه‌شنبه",
        2: "چهارشنبه",
        3: "پنجشنبه",
        4: "جمعه",
        5: "شنبه",
        6: "یکشنبه",
    }

    dt = datetime.strptime(date_value, "%Y-%m-%d")
    return days[dt.weekday()]


def add_goal(user_id, name, category, reminder_time):
    conn = db()

    now = datetime.now(TEHRAN).isoformat()

    cur = conn.execute("""
        INSERT INTO goals (
            user_id,
            name,
            category,
            reminder_time,
            enabled,
            created_at
        )
        VALUES (?, ?, ?, ?, 1, ?)
    """, (
        user_id,
        name,
        category,
        reminder_time,
        now,
    ))

    goal_id = cur.lastrowid

    conn.commit()
    conn.close()

    return goal_id


def get_goals(user_id):
    conn = db()

    rows = conn.execute("""
        SELECT *
        FROM goals
        WHERE user_id = ?
        ORDER BY id DESC
    """, (user_id,)).fetchall()

    conn.close()

    return rows


def get_goal(user_id, goal_id):
    conn = db()

    row = conn.execute("""
        SELECT *
        FROM goals
        WHERE user_id = ?
        AND id = ?
    """, (
        user_id,
        goal_id,
    )).fetchone()

    conn.close()

    return row


def delete_goal(user_id, goal_id):
    conn = db()

    conn.execute("""
        DELETE FROM goal_days
        WHERE user_id = ?
        AND goal_id = ?
    """, (
        user_id,
        goal_id,
    ))

    cur = conn.execute("""
        DELETE FROM goals
        WHERE user_id = ?
        AND id = ?
    """, (
        user_id,
        goal_id,
    ))

    conn.commit()

    result = cur.rowcount > 0

    conn.close()

    return result


def set_goal_status(user_id, goal_id, date_value, status):
    conn = db()

    now = datetime.now(TEHRAN).isoformat()

    conn.execute("""
        INSERT INTO goal_days (
            goal_id,
            user_id,
            goal_date,
            status,
            completed_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(goal_id, goal_date)
        DO UPDATE SET
            status = excluded.status,
            completed_at = excluded.completed_at
    """, (
        goal_id,
        user_id,
        date_value,
        status,
        now if status == "done" else None,
    ))

    conn.commit()
    conn.close()


def get_goal_status(user_id, goal_id, date_value):
    conn = db()

    row = conn.execute("""
        SELECT status
        FROM goal_days
        WHERE user_id = ?
        AND goal_id = ?
        AND goal_date = ?
    """, (
        user_id,
        goal_id,
        date_value,
    )).fetchone()

    conn.close()

    if not row:
        return "pending"

    return row["status"]


def get_week_status(user_id, goal_id):
    conn = db()

    rows = conn.execute("""
        SELECT goal_date, status
        FROM goal_days
        WHERE user_id = ?
        AND goal_id = ?
        ORDER BY goal_date
    """, (
        user_id,
        goal_id,
    )).fetchall()

    conn.close()

    return {
        row["goal_date"]: row["status"]
        for row in rows
    }


def get_week_stats(user_id, goal_id):
    conn = db()

    done = conn.execute("""
        SELECT COUNT(*)
        FROM goal_days
        WHERE user_id = ?
        AND goal_id = ?
        AND status = 'done'
        AND goal_date >= date('now', 'localtime', '-6 day')
    """, (
        user_id,
        goal_id,
    )).fetchone()[0]

    conn.close()

    return done


def main_keyboard():
    keyboard = [
        ["🎯 افزودن هدف", "📋 اهداف من"],
        ["📅 جدول هفتگی", "🎯 اهداف امروز"],
        ["🏆 اهداف آماده", "📊 آمار"],
        ["ℹ️ راهنما"],
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name or "دوست من"

    text = (
        f"سلام {name} 👋\n\n"
        "به ربات اهداف روزانه خوش آمدی.\n\n"
        "هدف‌های خودت را ثبت کن.\n"
        "برای هر هدف ساعت یادآوری تعیین کن.\n"
        "هر روز انجام هدف را ثبت کن."
    )

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "راهنمای ربات\n\n"
        "/start شروع\n"
        "/addgoal افزودن هدف\n"
        "/goals نمایش هدف‌ها\n"
        "/today اهداف امروز\n"
        "/week جدول هفتگی\n"
        "/stats آمار\n"
        "/cancel لغو"
    )

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


async def add_goal_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "🎯 اسم هدف را بفرست.\n\n"
        "مثال:\n"
        "ورزش ۳۰ دقیقه"
    )

    return ADD_NAME


async def add_goal_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    name = update.message.text.strip()

    if not name:
        await update.message.reply_text(
            "اسم هدف خالی است. دوباره بفرست."
        )
        return ADD_NAME

    context.user_data["goal_name"] = name

    keyboard = [
        [
            InlineKeyboardButton(
                "🏃 سلامتی",
                callback_data="category_سلامتی",
            ),
            InlineKeyboardButton(
                "📚 مطالعه",
                callback_data="category_مطالعه",
            ),
        ],
        [
            InlineKeyboardButton(
                "💼 کار",
                callback_data="category_کار",
            ),
            InlineKeyboardButton(
                "🧠 یادگیری",
                callback_data="category_یادگیری",
            ),
        ],
        [
            InlineKeyboardButton(
                "🏠 شخصی",
                callback_data="category_شخصی",
            ),
            InlineKeyboardButton(
                "📌 عمومی",
                callback_data="category_عمومی",
            ),
        ],
    ]

    await update.message.reply_text(
        "دسته هدف را انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    return ADD_CATEGORY


async def category_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    category = query.data.replace(
        "category_",
        "",
        1,
    )

    context.user_data["goal_category"] = category

    await query.edit_message_text(
        "⏰ ساعت یادآوری را بفرست.\n\n"
        "مثال:\n"
        "18:00\n\n"
        "اگر برای هدف یادآوری نمی‌خواهی، بنویس:\n"
        "بدون یادآوری"
    )

    return ADD_TIME


async def goal_time_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    value = update.message.text.strip()

    if value == "بدون یادآوری":
        reminder = None
    else:
        try:
            datetime.strptime(value, "%H:%M")
            reminder = value
        except ValueError:
            await update.message.reply_text(
                "فرمت ساعت درست نیست.\n"
                "مثال: 18:00"
            )
            return ADD_TIME

    name = context.user_data.get("goal_name")
    category = context.user_data.get(
        "goal_category",
        "عمومی",
    )

    goal_id = add_goal(
        update.effective_user.id,
        name,
        category,
        reminder,
    )

    context.user_data.clear()

    await update.message.reply_text(
        f"✅ هدف ثبت شد.\n\n"
        f"🎯 {name}\n"
        f"📁 دسته: {category}\n"
        f"⏰ یادآوری: {reminder or 'خاموش'}\n\n"
        "هر روز وضعیت هدف در جدول ثبت می‌شود.",
        reply_markup=main_keyboard(),
    )

    await show_goal(
        update,
        update.effective_user.id,
        goal_id,
    )

    return ConversationHandler.END


def week_dates():
    now = datetime.now(TEHRAN)

    start = now.date()

    dates = []

    for i in range(7):
        value = start.fromordinal(
            start.toordinal() - 6 + i
        )

        dates.append(
            value.strftime("%Y-%m-%d")
        )

    return dates


async def show_goal(
    update,
    user_id,
    goal_id,
):
    goal = get_goal(user_id, goal_id)

    if not goal:
        return

    statuses = get_week_status(
        user_id,
        goal_id,
    )

    text = (
        f"🎯 هدف: {goal['name']}\n"
        f"📁 دسته: {goal['category']}\n"
        f"⏰ ساعت: {goal['reminder_time'] or 'خاموش'}\n\n"
        "📅 وضعیت ۷ روز اخیر\n\n"
    )

    for date_value in week_dates():
        status = statuses.get(
            date_value,
            "pending",
        )

        if status == "done":
            icon = "✅"
        elif status == "missed":
            icon = "❌"
        else:
            icon = "➖"

        text += (
            f"{date_text(date_value)} "
            f"{date_value[5:]}  {icon}\n"
        )

    done_count = get_week_stats(
        user_id,
        goal_id,
    )

    text += (
        f"\n📊 انجام این هفته: {done_count} از 7 روز"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ امروز انجام شد",
                callback_data=f"done_{goal_id}",
            ),
            InlineKeyboardButton(
                "❌ امروز انجام نشد",
                callback_data=f"missed_{goal_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "📅 جدول کامل",
                callback_data=f"week_{goal_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "🗑 حذف هدف",
                callback_data=f"delete_{goal_id}",
            ),
        ],
    ]

    markup = InlineKeyboardMarkup(keyboard)

    if hasattr(update, "message") and update.message:
        await update.message.reply_text(
            text,
            reply_markup=markup,
        )
    elif hasattr(update, "callback_query") and update.callback_query:
        await update.callback_query.message.reply_text(
            text,
            reply_markup=markup,
        )


async def goals_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    goals = get_goals(
        update.effective_user.id
    )

    if not goals:
        await update.message.reply_text(
            "هنوز هدفی ثبت نکردی.",
            reply_markup=main_keyboard(),
        )
        return

    keyboard = []

    for goal in goals:
        keyboard.append([
            InlineKeyboardButton(
                f"🎯 {goal['name']}",
                callback_data=f"goal_{goal['id']}",
            )
        ])

    await update.message.reply_text(
        "هدف مورد نظر را انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def today_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    goals = get_goals(
        update.effective_user.id
    )

    if not goals:
        await update.message.reply_text(
            "هنوز هدفی ثبت نکردی."
        )
        return

    date_value = today()

    text = "🎯 اهداف امروز\n\n"

    for goal in goals:
        status = get_goal_status(
            update.effective_user.id,
            goal["id"],
            date_value,
        )

        if status == "done":
            icon = "✅"
        elif status == "missed":
            icon = "❌"
        else:
            icon = "➖"

        text += (
            f"{icon} {goal['name']}"
            f"  ⏰ {goal['reminder_time'] or '-'}\n"
        )

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


async def week_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await goals_command(update, context)


async def goal_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("goal_"):
        goal_id = int(
            query.data.split("_")[1]
        )

        await show_goal(
            update,
            query.from_user.id,
            goal_id,
        )


async def status_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")

    status = parts[0]
    goal_id = int(parts[1])

    set_goal_status(
        query.from_user.id,
        goal_id,
        today(),
        "done" if status == "done" else "missed",
    )

    await query.message.reply_text(
        "✅ وضعیت امروز ثبت شد."
        if status == "done"
        else "❌ هدف امروز به عنوان انجام‌نشده ثبت شد."
    )

    await show_goal(
        update,
        query.from_user.id,
        goal_id,
    )


async def delete_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    goal_id = int(
        query.data.split("_")[1]
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "بله، حذف شود",
                callback_data=f"confirmdelete_{goal_id}",
            ),
            InlineKeyboardButton(
                "لغو",
                callback_data="nodelete",
            ),
        ]
    ]

    await query.message.reply_text(
        "این هدف حذف شود؟",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def confirm_delete_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    goal_id = int(
        query.data.split("_")[1]
    )

    result = delete_goal(
        query.from_user.id,
        goal_id,
    )

    if result:
        await query.message.reply_text(
            "🗑 هدف حذف شد.",
            reply_markup=main_keyboard(),
        )
    else:
        await query.message.reply_text(
            "هدف پیدا نشد."
        )


async def ready_goals(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    ready = [
        "نوشیدن ۸ لیوان آب",
        "۳۰ دقیقه ورزش",
        "۳۰ دقیقه پیاده‌روی",
        "۳۰ دقیقه مطالعه",
        "خواب قبل از ساعت ۱۲",
        "بیدار شدن در ساعت مشخص",
        "مرتب کردن اتاق",
        "خوردن میوه",
        "کم کردن مصرف شیرینی",
        "یادگیری یک مهارت",
        "۱۰ دقیقه مدیتیشن",
        "رسیدگی به کارهای شخصی",
    ]

    keyboard = []

    for index, name in enumerate(ready):
        keyboard.append([
            InlineKeyboardButton(
                f"🎯 {name}",
                callback_data=f"ready_{index}",
            )
        ])

    await update.message.reply_text(
        "یک هدف آماده انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def ready_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    ready = [
        "نوشیدن ۸ لیوان آب",
        "۳۰ دقیقه ورزش",
        "۳۰ دقیقه پیاده‌روی",
        "۳۰ دقیقه مطالعه",
        "خواب قبل از ساعت ۱۲",
        "بیدار شدن در ساعت مشخص",
        "مرتب کردن اتاق",
        "خوردن میوه",
        "کم کردن مصرف شیرینی",
        "یادگیری یک مهارت",
        "۱۰ دقیقه مدیتیشن",
        "رسیدگی به کارهای شخصی",
    ]

    index = int(
        query.data.split("_")[1]
    )

    if index >= len(ready):
        return

    context.user_data["goal_name"] = ready[index]
    context.user_data["goal_category"] = "عمومی"

    await query.edit_message_text(
        f"🎯 هدف انتخاب شد:\n"
        f"{ready[index]}\n\n"
        "⏰ ساعت یادآوری را بفرست.\n\n"
        "مثال:\n"
        "18:00\n\n"
        "اگر یادآوری نمی‌خواهی بنویس:\n"
        "بدون یادآوری"
    )

    context.user_data["ready_goal"] = True


async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id

    goals = get_goals(user_id)

    total = len(goals)

    done_today = 0

    for goal in goals:
        status = get_goal_status(
            user_id,
            goal["id"],
            today(),
        )

        if status == "done":
            done_today += 1

    percent = 0

    if total:
        percent = int(
            done_today / total * 100
        )

    text = (
        "📊 آمار امروز\n\n"
        f"🎯 کل اهداف: {total}\n"
        f"✅ انجام‌شده: {done_today}\n"
        f"❌ انجام‌نشده: {total - done_today}\n"
        f"📈 پیشرفت: {percent}%"
    )

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


async def daily_reminder(
    context: ContextTypes.DEFAULT_TYPE,
):
    now = datetime.now(TEHRAN)
    current_time = now.strftime("%H:%M")

    conn = db()

    goals = conn.execute("""
        SELECT *
        FROM goals
        WHERE enabled = 1
        AND reminder_time = ?
    """, (current_time,)).fetchall()

    conn.close()

    for goal in goals:
        status = get_goal_status(
            goal["user_id"],
            goal["id"],
            today(),
        )

        if status == "done":
            continue

        keyboard = [
            [
                InlineKeyboardButton(
                    "✅ انجام دادم",
                    callback_data=f"done_{goal['id']}",
                ),
                InlineKeyboardButton(
                    "❌ انجام ندادم",
                    callback_data=f"missed_{goal['id']}",
                ),
            ]
        ]

        try:
            await context.bot.send_message(
                chat_id=goal["user_id"],
                text=(
                    "⏰ زمان هدف رسیده.\n\n"
                    f"🎯 {goal['name']}\n\n"
                    "امروز این هدف را انجام دادی؟"
                ),
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception as error:
            logger.error(
                "Reminder error: %s",
                error,
            )


async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    text = update.message.text

    if text == "🎯 افزودن هدف":
        return await add_goal_start(
            update,
            context,
        )

    if text == "📋 اهداف من":
        await goals_command(
            update,
            context,
        )
        return

    if text == "📅 جدول هفتگی":
        await week_command(
            update,
            context,
        )
        return

    if text == "🎯 اهداف امروز":
        await today_command(
            update,
            context,
        )
        return

    if text == "🏆 اهداف آماده":
        await ready_goals(
            update,
            context,
        )
        return

    if text == "📊 آمار":
        await stats_command(
            update,
            context,
        )
        return

    if text == "ℹ️ راهنما":
        await help_command(
            update,
            context,
        )
        return

    await update.message.reply_text(
        "از منوی پایین استفاده کن.",
        reply_markup=main_keyboard(),
    )


async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.clear()

    await update.message.reply_text(
        "❌ عملیات لغو شد.",
        reply_markup=main_keyboard(),
    )

    return ConversationHandler.END


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

    conversation = ConversationHandler(
        entry_points=[
            CommandHandler(
                "addgoal",
                add_goal_start,
            ),
            MessageHandler(
                filters.Regex("^🎯 افزودن هدف$"),
                add_goal_start,
            ),
        ],
        states={
            ADD_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    add_goal_name,
                )
            ],
            ADD_CATEGORY: [
                CallbackQueryHandler(
                    category_callback,
                    pattern=r"^category_",
                )
            ],
            ADD_TIME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    goal_time_received,
                )
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                cancel,
            )
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
        CommandHandler("goals", goals_command)
    )

    application.add_handler(
        CommandHandler("today", today_command)
    )

    application.add_handler(
        CommandHandler("week", week_command)
    )

    application.add_handler(
        CommandHandler("stats", stats_command)
    )

    application.add_handler(
        conversation
    )

    application.add_handler(
        CallbackQueryHandler(
            status_callback,
            pattern=r"^(done|missed)_",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            delete_callback,
            pattern=r"^delete_",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            confirm_delete_callback,
            pattern=r"^confirmdelete_",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            ready_callback,
            pattern=r"^ready_",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            goal_callback,
            pattern=r"^goal_",
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler,
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
