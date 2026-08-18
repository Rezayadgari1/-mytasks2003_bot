"""
ربات تلگرام یادآوری کارهای روزانه
------------------------------------
این ربات به هر کاربر اجازه می‌دهد کارهای روزانه‌ی خودش را ثبت کند
و هر روز، سر ساعت دلخواه، لیست کارها را به صورت خودکار برایش ارسال می‌کند.

دستورات:
/start            - شروع کار با ربات و راهنما
/help             - نمایش راهنما
/add <متن کار>    - افزودن یک کار جدید
/list             - نمایش لیست کارهای امروز
/done <شماره>     - علامت زدن یک کار به عنوان انجام‌شده (حذف از لیست)
/clear            - پاک کردن همه‌ی کارها
/settime HH:MM    - تنظیم ساعت یادآوری روزانه (مثلا 08:30)
/mytime           - نمایش ساعت یادآوری فعلی
"""

import logging
import os
import re
import sqlite3
from datetime import time, datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ------------------------------------------------------------------
# تنظیمات کلی
# ------------------------------------------------------------------

# توکن را ترجیحاً از متغیر محیطی BOT_TOKEN می‌خوانیم (مثلاً روی Railway/Render).
# اگر متغیر محیطی تنظیم نشده بود، از مقدار زیر استفاده می‌شود (برای اجرای محلی/تست).
BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUT_YOUR_TELEGRAM_BOT_TOKEN_HERE")
DB_PATH = "tasks.db"
DEFAULT_TIME = "09:00"
DEFAULT_TZ = "Asia/Tehran"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# دیتابیس
# ------------------------------------------------------------------

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
    conn.commit()
    conn.close()


def get_user_time(user_id: int) -> str:
    conn = get_db()
    row = conn.execute(
        "SELECT remind_time FROM user_settings WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return row["remind_time"] if row else DEFAULT_TIME


def set_user_time(user_id: int, hhmm: str):
    conn = get_db()
    conn.execute(
        """
        INSERT INTO user_settings (user_id, remind_time, timezone)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET remind_time = excluded.remind_time
        """,
        (user_id, hhmm, DEFAULT_TZ),
    )
    conn.commit()
    conn.close()


def add_task(user_id: int, text: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO tasks (user_id, text, created_at) VALUES (?, ?, ?)",
        (user_id, text, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def list_tasks(user_id: int):
    conn = get_db()
    rows = conn.execute(
        "SELECT id, text FROM tasks WHERE user_id = ? ORDER BY id", (user_id,)
    ).fetchall()
    conn.close()
    return rows


def remove_task(user_id: int, task_id: int) -> bool:
    conn = get_db()
    cur = conn.execute(
        "DELETE FROM tasks WHERE user_id = ? AND id = ?", (user_id, task_id)
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def clear_tasks(user_id: int):
    conn = get_db()
    conn.execute("DELETE FROM tasks WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_all_user_ids():
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT user_id FROM tasks "
        "UNION SELECT DISTINCT user_id FROM user_settings"
    ).fetchall()
    conn.close()
    return [r["user_id"] for r in rows]


# ------------------------------------------------------------------
# ابزار کمکی برای فرمت کردن لیست کارها
# ------------------------------------------------------------------

def format_task_list(rows) -> str:
    if not rows:
        return "فعلاً کاری تو لیستت ثبت نشده. با /add کارتو اضافه کن."
    lines = ["📋 لیست کارهای تو:\n"]
    for row in rows:
        lines.append(f"{row['id']}. {row['text']}")
    lines.append("\nبرای حذف یک کار: /done <شماره>")
    return "\n".join(lines)


# ------------------------------------------------------------------
# دستورات ربات
# ------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    schedule_reminder_for_user(context.application, user_id)
    await update.message.reply_text(
        "سلام! 👋\n"
        "من ربات یادآوری کارهای روزانه‌ت هستم.\n\n"
        "دستورات من:\n"
        "/add <متن کار> - افزودن یک کار\n"
        "/list - نمایش لیست کارها\n"
        "/done <شماره> - حذف یک کار از لیست\n"
        "/clear - پاک کردن همه‌ی کارها\n"
        "/settime HH:MM - تنظیم ساعت یادآوری روزانه (مثلا 08:30)\n"
        "/mytime - نمایش ساعت یادآوری فعلی\n\n"
        f"ساعت یادآوری پیش‌فرض تو {get_user_time(user_id)} تنظیم شده."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(
            "لطفاً متن کار رو بعد از دستور بنویس. مثال:\n/add خرید نان"
        )
        return
    add_task(user_id, text)
    await update.message.reply_text(f"✅ کار اضافه شد: {text}")


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    rows = list_tasks(user_id)
    await update.message.reply_text(format_task_list(rows))


async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "لطفاً شماره‌ی کار رو مشخص کن. مثال:\n/done 2"
        )
        return
    task_id = int(context.args[0])
    if remove_task(user_id, task_id):
        await update.message.reply_text("🗑️ کار حذف شد.")
    else:
        await update.message.reply_text("همچین کاری با این شماره پیدا نشد.")


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    clear_tasks(user_id)
    await update.message.reply_text("همه‌ی کارها پاک شدند. 🧹")


async def settime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text(
            "لطفاً ساعت رو به این شکل بفرست: /settime 08:30"
        )
        return
    hhmm = context.args[0]
    if not re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", hhmm):
        await update.message.reply_text(
            "فرمت ساعت درست نیست. نمونه‌ی درست: /settime 21:15"
        )
        return
    set_user_time(user_id, hhmm)
    schedule_reminder_for_user(context.application, user_id)
    await update.message.reply_text(f"⏰ ساعت یادآوری روزانه‌ت رو {hhmm} تنظیم کردم.")


async def mytime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(f"ساعت یادآوری فعلی تو: {get_user_time(user_id)}")


# ------------------------------------------------------------------
# منطق یادآوری روزانه
# ------------------------------------------------------------------

async def send_daily_reminder(context: ContextTypes.DEFAULT_TYPE):
    """این تابع توسط JobQueue فراخوانی می‌شود و لیست کارهای کاربر را برایش می‌فرستد."""
    user_id = context.job.data["user_id"]
    rows = list_tasks(user_id)
    if not rows:
        text = "امروز کاری تو لیستت ثبت نیست. یه کار خوب برای امروز با /add اضافه کن! 🌤️"
    else:
        text = "☀️ صبح بخیر! این کارهای امروزته:\n\n" + format_task_list(rows)
    await context.bot.send_message(chat_id=user_id, text=text)


def schedule_reminder_for_user(application: Application, user_id: int):
    """جاب یادآوری روزانه‌ی این کاربر را (با حذف نسخه‌ی قبلی در صورت وجود) دوباره زمان‌بندی می‌کند."""
    job_name = f"reminder_{user_id}"
    for job in application.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()

    hhmm = get_user_time(user_id)
    hour, minute = map(int, hhmm.split(":"))
    tz = ZoneInfo(DEFAULT_TZ)

    application.job_queue.run_daily(
        send_daily_reminder,
        time=time(hour=hour, minute=minute, tzinfo=tz),
        name=job_name,
        data={"user_id": user_id},
    )


async def schedule_all_existing_users(application: Application):
    """هنگام روشن شدن ربات، برای همه‌ی کاربران قبلی دوباره یادآوری‌ها را زمان‌بندی می‌کند."""
    for user_id in get_all_user_ids():
        schedule_reminder_for_user(application, user_id)


# ------------------------------------------------------------------
# اجرای ربات
# ------------------------------------------------------------------

async def post_init(application: Application):
    await schedule_all_existing_users(application)
    logger.info("یادآوری‌های همه‌ی کاربران قبلی دوباره زمان‌بندی شدند.")


def main():
    init_db()

    if BOT_TOKEN == "PUT_YOUR_TELEGRAM_BOT_TOKEN_HERE":
        raise SystemExit(
            "لطفاً اول توکن رباتت رو داخل فایل bot.py در متغیر BOT_TOKEN قرار بده.\n"
            "توکن رو می‌تونی از طریق ربات @BotFather در تلگرام بگیری."
        )

    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("add", add))
    application.add_handler(CommandHandler("list", list_cmd))
    application.add_handler(CommandHandler("done", done))
    application.add_handler(CommandHandler("clear", clear))
    application.add_handler(CommandHandler("settime", settime))
    application.add_handler(CommandHandler("mytime", mytime))

    logger.info("ربات در حال اجراست...")
    application.run_polling()


if __name__ == "__main__":
    main()
