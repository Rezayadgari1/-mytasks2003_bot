

import logging
from functools import wraps
import base64
import io
import json
import asyncio
import os
import re
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import urllib.request
import urllib.parse
import random
import hashlib
import secrets
import html
import difflib
# Pillow is optional: image generation is disabled by default and must never
# prevent the main bot from starting if Pillow is not installed.
try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    Image = ImageDraw = ImageFont = None
    PIL_AVAILABLE = False

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    LabeledPrice,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    PollAnswerHandler,
    filters,
)
try:
    from telegram.ext import MessageReactionHandler
except ImportError:
    MessageReactionHandler = None


BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", "").strip() or os.path.join(_SCRIPT_DIR, "goals.db")
# IMPORTANT: In Railway, set DB_PATH to a path on a persistent Volume.
# Example only (choose the actual mounted Volume path in your project):
# DB_PATH=/data/goals.db
# Do not store the live DB only inside an ephemeral deploy filesystem.

DB_SCHEMA_VERSION = 25

# DATA PERSISTENCE CONTRACT
# -------------------------
# Code updates must never recreate the database.
# Do not remove goals, users, history, channel_config, channel_posts,
# payments, VIP records, referrals, or settings during startup.
# New fields must use additive migrations only.
# Existing channel connection is updated in place.
# Keep DB_PATH stable in Railway Variables / Volume.

DB_BACKUP_PATH = os.environ.get("DB_BACKUP_PATH", DB_PATH + ".backup")
TZ = ZoneInfo("Asia/Tehran")

# اجباری بودن عضویت در کانال برای استفاده از ربات.
# کانال از تنظیمات «مدیریت کانال» خوانده می‌شود؛ برای لینک عضویت خصوصی
# می‌توان REQUIRED_CHANNEL_URL را در Variables تنظیم کرد.
REQUIRED_CHANNEL_URL = os.environ.get("REQUIRED_CHANNEL_URL", "").strip()

# Optional AI/automation gateway. n8n is never trusted with permissions, payments,
# or raw user records; it only receives explicitly approved workflow payloads.
N8N_WEBHOOK_URL = os.environ.get("N8N_WEBHOOK_URL", "").strip()
N8N_API_KEY = os.environ.get("N8N_API_KEY", "").strip()
N8N_TIMEOUT = float(os.environ.get("N8N_TIMEOUT", "12"))
MYTASKS_BUILD_ID = "2026-08-23-ADMIN-ROOT-UNIFIED-AI-01"
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()
AI_FAILOVER_TO_N8N = os.environ.get("AI_FAILOVER_TO_N8N", "1").strip() != "0"

# OmniRoute: optional self-hosted OpenAI-compatible AI gateway.
# Only HTTPS remote endpoints are accepted. It receives prompts, never payment,
# ownership, or raw database records.
OMNIROUTE_BASE_URL = os.environ.get("OMNIROUTE_BASE_URL", "").strip().rstrip("/")
OMNIROUTE_API_KEY = os.environ.get("OMNIROUTE_API_KEY", "").strip()
OMNIROUTE_MODEL = os.environ.get("OMNIROUTE_MODEL", "auto").strip() or "auto"
OMNIROUTE_TIMEOUT = float(os.environ.get("OMNIROUTE_TIMEOUT", "20"))


# Set admin Telegram IDs in environment:
# ADMIN_IDS=123456789,987654321
def _parse_admin_ids():
    values = []
    for raw in (os.environ.get("ADMIN_IDS", ""), os.environ.get("ADMIN_ID", "")):
        raw = raw.strip().strip("\"").strip("'")
        if not raw:
            continue
        for part in raw.split(","):
            part = part.strip().strip("\"").strip("'")
            if part.isdigit():
                values.append(int(part))
    return set(values)

ADMIN_IDS = _parse_admin_ids()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logger.info("MyTasks build %s | AI gateway: OmniRoute -> OpenAI -> n8n", MYTASKS_BUILD_ID)

def subscription_required(func):
    @wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        uid = update.effective_user.id if update.effective_user else 0
        maintenance_exempt = {
            "today", "new_goal", "new_goal_pick", "mark",
            "edit_menu", "edit_goal", "delete_goal",
        }
        paused_until = get_system_setting("bot_paused_until", "") if "get_system_setting" in globals() else ""
        paused = False
        if paused_until:
            try:
                paused = datetime.now(TZ) < datetime.fromisoformat(paused_until)
            except Exception:
                paused = False
        if (paused or feature_enabled("maintenance")) and uid not in ADMIN_IDS and func.__name__ not in maintenance_exempt:
            msg = "⏸ ربات موقتاً متوقف است. لطفاً کمی بعد دوباره تلاش کن." if paused else "🛠 ربات در حال بروزرسانی است. لطفاً بعداً دوباره تلاش کن."
            if update.callback_query:
                await update.callback_query.answer(msg, show_alert=True)
            elif update.message:
                await update.message.reply_text(msg)
            return
        if not await require_subscription(update, context):
            return
        return await func(update, context, *args, **kwargs)
    return wrapper



def _safe_cb_parts(data, sep=':', n=3):
    """Return exactly n parts from callback data, or None on failure."""
    try:
        parts = str(data or '').split(sep, n - 1)
        return parts if len(parts) == n else None
    except Exception:
        return None



GOALS_FA = {
    "🩺 سلامتی": [
        "💧 نوشیدن ۸ لیوان آب",
        "🚶 ۳۰ دقیقه پیاده‌روی",
        "🍎 خوردن میوه",
        "🥗 خوردن سبزیجات",
        "😴 خواب ۷ تا ۸ ساعت",
    ],
    "🏃 ورزش": [
        "🏋️ ۳۰ دقیقه ورزش",
        "🚶 ۵۰۰۰ قدم پیاده‌روی",
        "🏃 ۱۰۰۰۰ قدم پیاده‌روی",
        "💪 تمرین شکم",
        "🧘 حرکات کششی",
    ],
    "📚 مطالعه": [
        "📖 ۳۰ دقیقه مطالعه",
        "📖 ۲۰ دقیقه مطالعه",
        "🔤 یادگیری ۱۰ لغت جدید",
        "📚 مطالعه کتاب",
        "🔁 مرور مطالب",
    ],
    "💼 کار و شغل": [
        "📝 برنامه‌ریزی روز",
        "⭐ انجام مهم‌ترین کار روز",
        "🎯 ۳۰ دقیقه کار بدون حواس‌پرتی",
        "📋 بررسی کارهای امروز",
    ],
    "🍎 تغذیه": [
        "🥚 صبحانه سالم",
        "🥗 ناهار سالم",
        "🍲 شام سبک",
        "🥤 نخوردن نوشابه",
        "🍬 کاهش مصرف شیرینی",
    ],
    "💰 مالی": [
        "🧾 ثبت هزینه‌های امروز",
        "🏦 بررسی حساب بانکی",
        "💵 پس‌انداز روزانه",
        "✂️ حذف یک هزینه غیرضروری",
    ],
    "🏠 خانه": [
        "🧹 مرتب کردن اتاق",
        "🗂 مرتب کردن میز",
        "🛏 مرتب کردن تخت",
        "🧼 نظافت خانه",
        "👕 مرتب کردن لباس‌ها",
    ],
    "🧠 تمرکز": [
        "🎯 ۱۰ دقیقه تمرکز",
        "🧘 ۱۰ دقیقه مدیتیشن",
        "📵 ۳۰ دقیقه بدون موبایل",
        "🚫 ۳۰ دقیقه بدون شبکه اجتماعی",
    ],
    "🚗 خودرو": [
        "🛢 بررسی روغن موتور",
        "🛞 بررسی باد لاستیک",
        "💧 بررسی آب رادیاتور",
        "🧽 تمیز کردن خودرو",
    ],
    "✨ شخصی": [
        "🪥 مسواک زدن",
        "✨ رسیدگی به ظاهر",
        "⏳ انجام یک کار عقب‌افتاده",
        "💡 یادگیری یک چیز جدید",
    ],
}

GOALS_EN = {
    "🩺 Health": [
        "💧 Drink 8 glasses of water",
        "🚶 30 minute walk",
        "🍎 Eat fruit",
        "🥗 Eat vegetables",
        "😴 Sleep 7 to 8 hours",
    ],
    "🏃 Fitness": [
        "🏋️ 30 minute workout",
        "🚶 5000 steps",
        "🏃 10000 steps",
        "💪 Abs workout",
        "🧘 Stretching",
    ],
    "📚 Study": [
        "📖 Study for 30 minutes",
        "📖 Study for 20 minutes",
        "🔤 Learn 10 new words",
        "📚 Read a book",
        "🔁 Review lessons",
    ],
    "💼 Work": [
        "📝 Plan your day",
        "⭐ Do the most important task",
        "🎯 30 minutes of focused work",
        "📋 Review today's tasks",
    ],
    "🍎 Nutrition": [
        "🥚 Healthy breakfast",
        "🥗 Healthy lunch",
        "🍲 Light dinner",
        "🥤 No soft drinks",
        "🍬 Reduce sweets",
    ],
    "💰 Finance": [
        "🧾 Record today's expenses",
        "🏦 Check your bank account",
        "💵 Save money",
        "✂️ Remove one unnecessary expense",
    ],
    "🏠 Home": [
        "🧹 Clean your room",
        "🗂 Organize your desk",
        "🛏 Make your bed",
        "🧼 Clean the house",
        "👕 Organize your clothes",
    ],
    "🧠 Focus": [
        "🎯 10 minutes of focus",
        "🧘 10 minutes of meditation",
        "📵 30 minutes without your phone",
        "🚫 30 minutes without social media",
    ],
    "🚗 Car": [
        "🛢 Check engine oil",
        "🛞 Check tire pressure",
        "💧 Check coolant",
        "🧽 Clean the car",
    ],
    "✨ Personal": [
        "🪥 Brush your teeth",
        "✨ Personal care",
        "⏳ Finish one delayed task",
        "💡 Learn something new",
    ],
}

T = {
    "fa": {
        "welcome": "🎯 سلام {name} عزیز! خوش اومدی 🌷\n\nزبان ربات رو انتخاب کن:",
        "language_saved": "✅ زبان ربات روی فارسی تنظیم شد.",
        "gender": "👤 دوست داری جنسیتت رو مشخص کنی؟",
        "gender_saved": "✅ ممنون {name} عزیز 🌷",
        "menu": [
            ["🎯 اهداف امروز", "✏️ هدف خودم می‌نویسم"],
            ["🏆 اهداف آماده", "✏️ ویرایش اهداف"],
            ["📅 جدول هفتگی", "📊 آمار من"],
            ["👤 پروفایل", "🏆 دستاوردها"],
            ["⭐ XP", "🤝 دعوت دوستان"],
            ["📈 قیمت آنلاین", "🤖 چت با AI"],
            ["💎 VIP", "🎫 پشتیبانی"],
            ["⚙️ تنظیمات"],
        ],
        "today": "🎯 اهداف امروز",
        "no_goals": "🎯 {name} عزیز، هنوز هدفی ثبت نکردی.\nاز «✏️ هدف خودم می‌نویسم» یا «🏆 اهداف آماده» شروع کنیم؟",
        "new_goal": "🎯 {name} عزیز، یک دسته را انتخاب کن:",
        "choose_goal": "🎯 حالا یکی از هدف‌های آماده را انتخاب کن:",
        "goal_added": "🎉 عالیه {name} عزیز! هدف ثبت شد.",
        "choose_time": "⏰ زمان یادآوری را انتخاب کن:",
        "custom_time": "🕐 ساعت را به صورت 24 ساعته بفرست.\nمثال: 18:30 یا 1830",
        "bad_time": "❌ ساعت واردشده درست نیست. مثال: 18:30",
        "morning": "☀️ صبح بخیر {name} عزیز!\n\nآماده‌ای روزت رو شروع کنی؟ 💪\nامروز یک قدم دیگه به هدف‌هات نزدیک شو!",
        "done": "✅ آفرین {name} عزیز! انجام شد. 👏",
        "missed": "❌ اشکالی نداره {name} عزیز؛ فردا دوباره شروع می‌کنیم. 💪",
        "settings": "⚙️ تنظیمات",
        "language": "🌐 زبان",
        "edit": "✏️ {name} عزیز، هدفی که می‌خوای تغییر بدی رو انتخاب کن:",
        "deleted": "🗑 هدف حذف شد.",
        "name": "✏️ اسم جدید هدف را بفرست:",
        "changed": "✅ هدف تغییر کرد.",
        "reminder": "⏰ یادآوری هدف\n\nسلام {name} عزیز 🌷\n🎯 {goal}\n\nانجامش دادی؟",
        "profile": "👤 پروفایل {name}\n\n🎯 تعداد اهداف: {goals}\n🔥 اهداف انجام‌شده امروز: {done}\n📅 تاریخ عضویت: {date}",
        "weekly": "📅 جدول هفتگی {name}\n\n{rows}",
        "stats": "📊 آمار {name}\n\n🎯 کل اهداف: {goals}\n✅ انجام‌شده امروز: {done}\n❌ انجام‌نشده امروز: {missed}\n🔥 مجموع انجام‌ها: {total_done}",
        "gender_male": "👨 پسر / مرد",
        "gender_female": "👩 دختر / زن",
        "gender_none": "🙂 ترجیح می‌دم نگم",
        "no_reminder": "🔕 بدون یادآوری",
        "other_time": "🕐 ساعت دیگر",
        "back": "⬅️ برگشت",
        "profile_gender": "جنسیت: {gender}",
        "broadcast_prompt": "📢 متن پیام همگانی را بفرست:",
        "broadcast_done": "✅ پیام برای {sent} کاربر ارسال شد.",
        "admin_denied": "⛔ دسترسی ندارید.",
    },
    "en": {
        "welcome": "🎯 Hi {name}! Welcome 🌷\n\nChoose your language:",
        "language_saved": "✅ Language set to English.",
        "gender": "👤 Would you like to specify your gender?",
        "gender_saved": "✅ Thanks, {name} 🌷",
        "menu": [
            ["🎯 Today's Goals", "✏️ Write my own goal"],
            ["🏆 Ready Goals", "✏️ Edit Goals"],
            ["📅 Weekly Table", "📊 My Stats"],
            ["👤 Profile", "🏆 Achievements"],
            ["⭐ XP", "🤝 Referrals"],
            ["📈 Online Prices", "🤖 AI Chat"],
            ["💎 VIP", "🎫 Support"],
            ["⚙️ Settings"],
        ],
        "today": "🎯 Today's Goals",
        "no_goals": "🎯 {name}, you have no goals yet.\nLet's start with «✏️ Write my own goal» or «🏆 Ready Goals».",
        "new_goal": "🎯 {name}, select a category:",
        "choose_goal": "🎯 Now choose one of the ready goals:",
        "goal_added": "🎉 Great {name}! Goal added.",
        "choose_time": "⏰ Choose a reminder time:",
        "custom_time": "🕐 Send the time in 24-hour format.\nExample: 18:30 or 1830",
        "bad_time": "❌ Invalid time. Example: 18:30",
        "morning": "☀️ Good morning {name}!\n\nReady to start your day? 💪\nTake one more step toward your goals today!",
        "done": "✅ Great job {name}! Done. 👏",
        "missed": "❌ That's okay {name}; we'll try again tomorrow. 💪",
        "settings": "⚙️ Settings",
        "language": "🌐 Language",
        "edit": "✏️ {name}, choose the goal you want to edit:",
        "deleted": "🗑 Goal deleted.",
        "name": "✏️ Send the new goal name:",
        "changed": "✅ Goal updated.",
        "reminder": "⏰ Goal reminder\n\nHi {name} 🌷\n🎯 {goal}\n\nDid you complete it?",
        "profile": "👤 {name}'s Profile\n\n🎯 Goals: {goals}\n🔥 Completed today: {done}\n📅 Joined: {date}",
        "weekly": "📅 {name}'s Weekly Table\n\n{rows}",
        "stats": "📊 {name}'s Stats\n\n🎯 Total goals: {goals}\n✅ Done today: {done}\n❌ Missed today: {missed}\n🔥 Total completions: {total_done}",
        "gender_male": "👨 Male",
        "gender_female": "👩 Female",
        "gender_none": "🙂 Prefer not to say",
        "no_reminder": "🔕 No reminder",
        "other_time": "🕐 Other time",
        "back": "⬅️ Back",
        "profile_gender": "Gender: {gender}",
        "broadcast_prompt": "📢 Send the broadcast message:",
        "broadcast_done": "✅ Message sent to {sent} users.",
        "admin_denied": "⛔ Access denied.",
    },
}

TIME_BUTTONS = ["07:00", "08:00", "10:00", "12:00", "15:00", "18:00", "20:00", "22:00"]



def restore_database_if_missing():
    """Restore the live SQLite database from the last backup if it disappeared."""
    if os.path.exists(DB_PATH) or not os.path.exists(DB_BACKUP_PATH):
        return False
    try:
        os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
        src_conn = sqlite3.connect(DB_BACKUP_PATH, timeout=30)
        dst_conn = sqlite3.connect(DB_PATH, timeout=30)
        with dst_conn:
            src_conn.backup(dst_conn)
        dst_conn.close(); src_conn.close()
        logger.warning("Restored missing live database from %s", DB_BACKUP_PATH)
        return True
    except Exception:
        logger.exception("Database restore failed")
        try:
            if os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) == 0:
                os.remove(DB_PATH)
        except Exception:
            pass
        return False


def backup_database():
    """Create a safe SQLite backup without deleting or replacing live data."""
    if not os.path.exists(DB_PATH):
        return False
    try:
        src_conn = sqlite3.connect(DB_PATH, timeout=30)
        dst_conn = sqlite3.connect(DB_BACKUP_PATH, timeout=30)
        with dst_conn:
            src_conn.backup(dst_conn)
        dst_conn.close()
        src_conn.close()
        try:
            if os.name == "posix":
                os.chmod(DB_BACKUP_PATH, 0o600)
        except OSError:
            pass
        return True
    except Exception as e:
        logger.error("Database backup failed: %s", e)
        return False



def backup_database_snapshot(keep=10):
    """Keep timestamped SQLite snapshots so a code update can be rolled back safely."""
    if not os.path.exists(DB_PATH):
        return False
    try:
        folder=os.path.join(os.path.dirname(os.path.abspath(DB_PATH)),"backups")
        os.makedirs(folder,exist_ok=True)
        stamp=datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
        target=os.path.join(folder,f"goals_{stamp}.db")
        src_conn=sqlite3.connect(DB_PATH,timeout=30)
        dst_conn=sqlite3.connect(target,timeout=30)
        with dst_conn:
            src_conn.backup(dst_conn)
        dst_conn.close(); src_conn.close()
        try:
            if os.name == "posix":
                os.chmod(target, 0o600)
        except OSError:
            pass
        files=sorted(
            [os.path.join(folder,x) for x in os.listdir(folder) if x.endswith(".db")],
            key=lambda x: os.path.getmtime(x), reverse=True
        )
        for old in files[keep:]:
            try: os.remove(old)
            except OSError: pass
        return True
    except Exception as e:
        logger.error("Timestamped database backup failed: %s",e)
        return False

def ensure_column(c, table, column, ddl):
    """Add a column only when the old database does not have it."""
    columns = {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def get_schema_version(c):
    c.execute("""
        CREATE TABLE IF NOT EXISTS app_meta(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    row = c.execute(
        "SELECT value FROM app_meta WHERE key='schema_version'"
    ).fetchone()
    return int(row["value"]) if row else 0


def set_schema_version(c, version):
    c.execute("""
        INSERT INTO app_meta(key,value) VALUES('schema_version',?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (str(version),))


def migrate_database(c):
    """
    Forward-only migrations.
    Existing users, goals, channel settings, payments and history stay in place.
    Never DROP TABLE and never DELETE user data during a normal code update.
    """
    version = get_schema_version(c)

    # Add future columns here. Each migration must be additive.
    # Example:
    # if version < 19:
    #     ensure_column(c, "users", "new_field", "TEXT")
    #     set_schema_version(c, 19)

    if version < 22:
        ensure_column(c, "business_profiles", "business_name", "TEXT NOT NULL DEFAULT ''")
        ensure_column(c, "business_profiles", "contact_phone", "TEXT NOT NULL DEFAULT ''")
        ensure_column(c, "business_profiles", "contact_telegram", "TEXT NOT NULL DEFAULT ''")
        ensure_column(c, "business_profiles", "contact_instagram", "TEXT NOT NULL DEFAULT ''")
        c.execute("""CREATE TABLE IF NOT EXISTS subscription_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, plan TEXT NOT NULL,
            duration_days INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL DEFAULT 'admin', amount INTEGER NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL, expires_at TEXT, created_at TEXT NOT NULL
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_subscription_history_user ON subscription_history(user_id, created_at)")
        set_schema_version(c, 22)
    if version < 25:
        c.execute("""CREATE TABLE IF NOT EXISTS weekly_reports(
            report_week TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        set_schema_version(c, 25)
    if version < DB_SCHEMA_VERSION:
        set_schema_version(c, DB_SCHEMA_VERSION)


def db():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


def init_db():
    restore_database_if_missing()
    backup_database()
    backup_database_snapshot()
    c = db()
    c.execute(
        """CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            first_name TEXT NOT NULL DEFAULT '',
            language TEXT NOT NULL DEFAULT 'fa',
            gender TEXT,
            created_at TEXT NOT NULL,
            last_active_at TEXT
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS goals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            reminder_time TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS goal_days(
            goal_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            goal_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            completed_at TEXT,
            PRIMARY KEY(goal_id, goal_date)
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS goal_steps(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS achievements(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            unlocked_at TEXT NOT NULL,
            UNIQUE(user_id, code)
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS activity_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            activity TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    # Migrate old databases safely.
    goal_columns = {r["name"] for r in c.execute("PRAGMA table_info(goals)").fetchall()}
    if "priority" not in goal_columns:
        c.execute("ALTER TABLE goals ADD COLUMN priority INTEGER NOT NULL DEFAULT 2")
    if "duration_minutes" not in goal_columns:
        c.execute("ALTER TABLE goals ADD COLUMN duration_minutes INTEGER")
    c.execute("""CREATE TABLE IF NOT EXISTS user_settings(
        user_id INTEGER PRIMARY KEY, reminders_enabled INTEGER NOT NULL DEFAULT 1,
        ai_daily_used INTEGER NOT NULL DEFAULT 0, ai_used_date TEXT)""")

    columns = {r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()}
    if "first_name" not in columns:
        c.execute("ALTER TABLE users ADD COLUMN first_name TEXT NOT NULL DEFAULT ''")
    if "gender" not in columns:
        c.execute("ALTER TABLE users ADD COLUMN gender TEXT")
    if "last_active_at" not in columns:
        c.execute("ALTER TABLE users ADD COLUMN last_active_at TEXT")
    c.execute("""CREATE TABLE IF NOT EXISTS channel_config(
        id INTEGER PRIMARY KEY CHECK(id=1), channel_id TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS managed_channels(
        id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS goal_reminder_overrides(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, goal_id INTEGER NOT NULL,
        reminder_date TEXT NOT NULL, reminder_time TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(user_id,goal_id,reminder_date))""")
    c.execute("""CREATE TABLE IF NOT EXISTS customer_broadcasts(
        id INTEGER PRIMARY KEY AUTOINCREMENT, owner_user_id INTEGER NOT NULL, audience TEXT NOT NULL,
        message TEXT NOT NULL, sent_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS customer_reengagement_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT, owner_user_id INTEGER NOT NULL, customer_id INTEGER NOT NULL,
        reference_date TEXT NOT NULL, sent_at TEXT NOT NULL, UNIQUE(owner_user_id,customer_id,reference_date))""")
    c.execute("""CREATE TABLE IF NOT EXISTS channel_posts(
        id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL,
        schedule_type TEXT NOT NULL DEFAULT 'once', schedule_time TEXT, weekday INTEGER,
        run_at TEXT, enabled INTEGER NOT NULL DEFAULT 1, last_sent_at TEXT,
        created_at TEXT NOT NULL, created_by INTEGER NOT NULL)""")
    user_cols={r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()}
    for col,ddl in [("xp","INTEGER NOT NULL DEFAULT 0"),("blocked","INTEGER NOT NULL DEFAULT 0"),("warnings","INTEGER NOT NULL DEFAULT 0"),("vip_until","TEXT"),("referrer_id","INTEGER"),("referral_code","TEXT")]:
        if col not in user_cols: c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
    c.execute("""CREATE TABLE IF NOT EXISTS user_feature_preferences(
        user_id INTEGER NOT NULL, feature_key TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL, PRIMARY KEY(user_id, feature_key)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS service_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT, service TEXT NOT NULL, status TEXT NOT NULL,
        details TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS feature_flags(key TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS admin_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,admin_id INTEGER,action TEXT,target_user INTEGER,details TEXT,created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS xp_log(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,amount INTEGER NOT NULL,reason TEXT,created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS referrals(id INTEGER PRIMARY KEY AUTOINCREMENT,inviter_id INTEGER NOT NULL,invited_id INTEGER UNIQUE NOT NULL,created_at TEXT NOT NULL,rewarded INTEGER NOT NULL DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS content_feedback(id INTEGER PRIMARY KEY AUTOINCREMENT,post_key TEXT,user_id INTEGER,rating INTEGER,reaction TEXT,created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS content_preferences(user_id INTEGER,category TEXT,score INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,category))""")

    # Production indexes and idempotency tables.
    c.executescript("""
    CREATE INDEX IF NOT EXISTS idx_users_last_active ON users(last_active_at);
    CREATE INDEX IF NOT EXISTS idx_goals_user_enabled_reminder ON goals(user_id, enabled, reminder_time);
    CREATE INDEX IF NOT EXISTS idx_goal_days_user_date ON goal_days(user_id, goal_date);
    CREATE INDEX IF NOT EXISTS idx_goal_days_status_date ON goal_days(status, goal_date);
    CREATE INDEX IF NOT EXISTS idx_goal_steps_goal_user ON goal_steps(goal_id, user_id);
    CREATE INDEX IF NOT EXISTS idx_activity_user_created ON activity_log(user_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log(created_at);
    CREATE INDEX IF NOT EXISTS idx_channel_posts_due ON channel_posts(enabled, schedule_type, run_at);
    CREATE INDEX IF NOT EXISTS idx_xp_log_user_created ON xp_log(user_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_referrals_inviter ON referrals(inviter_id);
    CREATE INDEX IF NOT EXISTS idx_content_feedback_post ON content_feedback(post_key);
    CREATE TABLE IF NOT EXISTS delivery_log(
        delivery_key TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        delivery_type TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_delivery_user_type
        ON delivery_log(user_id, delivery_type, created_at);
    CREATE TABLE IF NOT EXISTS reward_log(
        reward_key TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        reward_type TEXT NOT NULL,
        amount INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS bot_usage_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        event_type TEXT NOT NULL,
        details TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_bot_usage_events_day ON bot_usage_events(created_at, event_type);
    CREATE TABLE IF NOT EXISTS broadcast_jobs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER NOT NULL,
        message_text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        sent_count INTEGER NOT NULL DEFAULT 0,
        failed_count INTEGER NOT NULL DEFAULT 0,
        last_user_id INTEGER,
        created_at TEXT NOT NULL,
        finished_at TEXT
    );
    """)
    c.execute("""CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,subject TEXT,status TEXT NOT NULL DEFAULT 'open',created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS ticket_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,ticket_id INTEGER,sender_id INTEGER,message TEXT,created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS price_alerts(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,asset TEXT,target REAL,direction TEXT,enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_price_alerts_enabled ON price_alerts(enabled)")
    c.execute("""CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,payload TEXT,currency TEXT,total_amount INTEGER,telegram_charge_id TEXT UNIQUE,created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS favorites(user_id INTEGER,asset TEXT,created_at TEXT NOT NULL,PRIMARY KEY(user_id,asset))""")
    c.execute("""CREATE TABLE IF NOT EXISTS daily_reports(report_date TEXT PRIMARY KEY,data TEXT NOT NULL,created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS weekly_reports(
        report_week TEXT PRIMARY KEY,
        data TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS channel_reactions(
        id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id TEXT NOT NULL, message_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL, reaction TEXT NOT NULL, is_paid INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(channel_id, message_id, user_id, reaction)
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_channel_reactions_day ON channel_reactions(channel_id, created_at)")
    c.execute("""CREATE TABLE IF NOT EXISTS channel_polls(
        poll_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, poll_type TEXT NOT NULL,
        question TEXT NOT NULL, options TEXT NOT NULL, created_at TEXT NOT NULL, report_date TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS channel_poll_votes(
        poll_id TEXT NOT NULL, user_id INTEGER NOT NULL, option_id INTEGER NOT NULL,
        created_at TEXT NOT NULL, PRIMARY KEY(poll_id, user_id)
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_channel_poll_votes_poll ON channel_poll_votes(poll_id)")
    c.execute("""CREATE TABLE IF NOT EXISTS health_checks(id INTEGER PRIMARY KEY AUTOINCREMENT,service TEXT,status TEXT,details TEXT,created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS auto_pending(
        id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id TEXT NOT NULL, topic TEXT NOT NULL,
        content TEXT NOT NULL, publish_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS system_settings(
        key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS auto_post_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id TEXT NOT NULL,
        topic TEXT NOT NULL,
        category TEXT,
        content TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(channel_id, content_hash))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_auto_history_channel_created ON auto_post_history(channel_id, created_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_auto_history_topic_created ON auto_post_history(topic, created_at)")

    # ================= ADDITIVE CUSTOMER / FEATURE ACCESS SCHEMA =================
    c.execute("""CREATE TABLE IF NOT EXISTS business_profiles(user_id INTEGER PRIMARY KEY,business_type TEXT NOT NULL DEFAULT '',business_name TEXT NOT NULL DEFAULT '',contact_phone TEXT NOT NULL DEFAULT '',contact_telegram TEXT NOT NULL DEFAULT '',contact_instagram TEXT NOT NULL DEFAULT '',booking_enabled INTEGER NOT NULL DEFAULT 1,booking_token TEXT UNIQUE,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY AUTOINCREMENT,owner_user_id INTEGER NOT NULL,name TEXT NOT NULL,phone TEXT,telegram_username TEXT,telegram_user_id INTEGER,notes TEXT,status TEXT NOT NULL DEFAULT 'active',created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS appointments(id INTEGER PRIMARY KEY AUTOINCREMENT,owner_user_id INTEGER NOT NULL,customer_id INTEGER NOT NULL,appointment_date TEXT NOT NULL,appointment_time TEXT NOT NULL,duration_minutes INTEGER NOT NULL DEFAULT 30,service TEXT,notes TEXT,reminder_minutes TEXT NOT NULL DEFAULT '30',status TEXT NOT NULL DEFAULT 'booked',source TEXT NOT NULL DEFAULT 'manual',created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS working_hours(owner_user_id INTEGER NOT NULL,weekday INTEGER NOT NULL,start_time TEXT NOT NULL DEFAULT '09:00',end_time TEXT NOT NULL DEFAULT '20:00',enabled INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(owner_user_id,weekday))""")
    c.execute("""CREATE TABLE IF NOT EXISTS business_holidays(id INTEGER PRIMARY KEY AUTOINCREMENT,owner_user_id INTEGER NOT NULL,holiday_date TEXT NOT NULL,note TEXT,UNIQUE(owner_user_id,holiday_date))""")
    c.execute("""CREATE TABLE IF NOT EXISTS customer_events(id INTEGER PRIMARY KEY AUTOINCREMENT,owner_user_id INTEGER NOT NULL,customer_id INTEGER NOT NULL,appointment_id INTEGER,event_type TEXT NOT NULL,details TEXT,created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS user_feature_overrides(user_id INTEGER NOT NULL,feature_key TEXT NOT NULL,mode TEXT NOT NULL DEFAULT 'inherit',updated_at TEXT NOT NULL,PRIMARY KEY(user_id,feature_key))""")
    c.execute("""CREATE TABLE IF NOT EXISTS feature_access(key TEXT PRIMARY KEY,mode TEXT NOT NULL DEFAULT 'free',updated_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS service_costs(
        key TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'free',
        provider TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL
    )""")
    now_iso=datetime.now(TZ).isoformat()
    # Infrastructure/service cost registry. This is separate from feature_access:
    # feature_access controls who may use a feature (free/VIP/off), while service_costs
    # records whether the underlying provider is free, optional-paid, or variable-cost.
    service_cost_defaults = {
        "telegram_bot_api": ("🤖 هسته Telegram Bot API", "free", "Telegram", "استفاده عادی از Bot API رایگان است؛ محدودیت نرخ ارسال دارد."),
        "hosting": ("🖥️ هاست / اجرای ربات", "variable", "Railway یا سرور دیگر", "هزینه به سرویس میزبانی و مصرف CPU/RAM/Storage/Network بستگی دارد."),
        "database": ("🗄️ دیتابیس SQLite", "free", "خود ربات", "برای نسخه فعلی داخل همان سرویس است؛ هزینه API جداگانه ندارد."),
        "ai_api": ("🧠 AI / Voice / پردازش هوشمند", "optional_paid", "OpenAI API یا سرویس جایگزین", "بدون API Key خاموش می‌ماند؛ مصرف API می‌تواند هزینه داشته باشد."),
        "price_sources": ("📈 منابع قیمت آنلاین", "free_or_variable", "منابع عمومی/API", "بعضی منابع رایگان‌اند؛ APIهای تجاری ممکن است هزینه یا محدودیت داشته باشند."),
        "sms": ("📱 پیامک SMS", "optional_paid", "پنل SMS انتخابی", "خود قابلیت رایگان است؛ ارسال SMS معمولاً هزینه هر پیام/بسته دارد."),
        "payment_gateway": ("💳 درگاه پرداخت ایرانی", "variable", "پرداخت‌یار/PSP انتخابی", "اتصال فنی می‌تواند رایگان باشد؛ کارمزد و شرایط را ارائه‌دهنده تعیین می‌کند."),
        "telegram_stars": ("⭐ پرداخت VIP با Telegram Stars", "transactional", "Telegram", "پرداخت داخل Telegram انجام می‌شود؛ شرایط/کارمزد طبق سازوکار Telegram است."),
        "channel_media": ("🖼️ رسانه و انتشار کانال", "free", "Telegram", "برای خود Bot API هزینه جداگانه ندارد؛ محدودیت‌های Telegram برقرار است."),
        "voice_transcription": ("🎙️ تبدیل Voice به متن", "optional_paid", "AI/STT provider", "بدون سرویس خارجی می‌توان قابلیت را خاموش نگه داشت؛ سرویس STT ممکن است هزینه داشته باشد."),
    }
    for key,(label,status,provider,note) in service_cost_defaults.items():
        c.execute("INSERT OR IGNORE INTO service_costs(key,label,status,provider,note,enabled,updated_at) VALUES(?,?,?,?,?,?,?)",(key,label,status,provider,note,1,now_iso))
    # Central access matrix: every module has its own independent mode.
    # Modes: free / vip / off. Existing databases are migrated additively.
    access_defaults = {
        "customers":"vip",
        "ai":"free", "vip":"free", "reminders":"free", "sports":"free",
        "nutrition":"free", "investing":"free", "self_growth":"free", "morning":"free", "night":"free",
        "auto_publish":"free", "images":"free", "feedback":"free", "referrals":"free",
        "mini_app":"free", "support":"free", "price_data":"free", "approval":"free",
        "goals":"free", "weekly":"free", "stats":"free", "profile":"free", "achievements":"free",
        "settings":"free", "xp":"free", "payments":"off", "maintenance":"off", "test_mode":"free",
        # Customer sub-options
        "customer_today":"free", "customer_new_appointment":"free", "customer_customers":"free",
        "customer_calendar":"free", "customer_hours":"free", "customer_reminders":"free",
        "customer_analytics":"free", "customer_loyal":"free", "customer_period":"free",
        "customer_booking_link":"free", "customer_online_booking":"free", "customer_business_settings":"free",
    }
    for key, mode in access_defaults.items():
        c.execute("INSERT OR IGNORE INTO feature_flags(key,enabled,updated_at) VALUES(?,?,?)",(key,1 if mode != "off" else 0,now_iso))
        c.execute("INSERT OR IGNORE INTO feature_access(key,mode,updated_at) VALUES(?,?,?)",(key,mode,now_iso))

    for key in ["ai","vip","reminders","sports","nutrition","investing","self_growth","morning","night","auto_publish","images","feedback","referrals","mini_app","support","price_data","approval","goals","weekly","stats","profile","achievements","settings"]:
        c.execute("INSERT OR IGNORE INTO feature_flags(key,enabled,updated_at) VALUES(?,?,?)",(key,1,now_iso))
    c.execute("INSERT OR IGNORE INTO feature_flags(key,enabled,updated_at) VALUES('payments',0,?)",(now_iso,))
    c.execute("INSERT OR IGNORE INTO feature_flags(key,enabled,updated_at) VALUES('maintenance',0,?)",(now_iso,))
    c.execute("INSERT OR IGNORE INTO feature_flags(key,enabled,updated_at) VALUES('test_mode',1,?)",(now_iso,))
    migrate_database(c)
    c.commit()
    c.close()


def register_user(uid, first_name):
    now = datetime.now(TZ).isoformat()
    c = db()
    c.execute(
        """INSERT INTO users(user_id, first_name, created_at, last_active_at)
           VALUES(?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET
           first_name=excluded.first_name,
           last_active_at=excluded.last_active_at""",
        (uid, first_name or "", now, now),
    )
    c.execute("UPDATE users SET referral_code=COALESCE(referral_code,?) WHERE user_id=?",(secrets.token_urlsafe(12),uid))
    c.commit()
    c.close()


def log_activity(uid, activity):
    now = datetime.now(TZ).isoformat()
    c = db()
    c.execute("UPDATE users SET last_active_at=? WHERE user_id=?", (now, uid))
    c.execute(
        "INSERT INTO activity_log(user_id, activity, created_at) VALUES(?,?,?)",
        (uid, activity, now),
    )
    c.commit()
    c.close()


def lang(uid):
    c = db()
    r = c.execute("SELECT language FROM users WHERE user_id=?", (uid,)).fetchone()
    c.close()
    return r["language"] if r else "fa"


def set_lang(uid, value):
    c = db()
    c.execute("UPDATE users SET language=? WHERE user_id=?", (value, uid))
    c.commit()
    c.close()


def set_gender(uid, value):
    c = db()
    c.execute("UPDATE users SET gender=? WHERE user_id=?", (value, uid))
    c.commit()
    c.close()


def user_info(uid):
    c = db()
    r = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    c.close()
    return r


def display_name(uid):
    r = user_info(uid)
    return (r["first_name"] if r and r["first_name"] else "دوست من")


FEATURE_MENU_MAP = {
    "🎯 اهداف امروز":"goals","🎯 Today's Goals":"goals","✏️ هدف خودم می‌نویسم":"goals","✏️ Write my own goal":"goals",
    "🏆 اهداف آماده":"goals","🏆 Ready Goals":"goals","✏️ ویرایش اهداف":"goals","✏️ Edit Goals":"goals",
    "📅 جدول هفتگی":"weekly","📅 Weekly Table":"weekly","📊 آمار من":"stats","📊 My Stats":"stats",
    "👤 پروفایل":"profile","👤 Profile":"profile","🏆 دستاوردها":"achievements","🏆 Achievements":"achievements",
    "⭐ XP":"xp","🤝 دعوت دوستان":"referrals","🤝 Referrals":"referrals","📈 قیمت آنلاین":"price_data","📈 Online Prices":"price_data",
    "🤖 چت با AI":"ai","🤖 AI Chat":"ai","💎 VIP":"vip","💎 VIP & Paid Features":"vip",
    "🎫 پشتیبانی":"support","🎫 Support":"support","⚙️ تنظیمات":"settings","⚙️ Settings":"settings",
    "👥 مدیریت مشتری و نوبت‌دهی":"customers","👥 Customer & Appointments":"customers",
}

def _ensure_user_feature_preferences(uid):
    """Create default personal menu preferences without touching existing user data."""
    now=datetime.now(TZ).isoformat()
    c=db()
    try:
        keys=set(FEATURE_MENU_MAP.values()) if "FEATURE_MENU_MAP" in globals() else set()
        for key in keys:
            c.execute(
                "INSERT OR IGNORE INTO user_feature_preferences(user_id,feature_key,enabled,updated_at) VALUES(?,?,1,?)",
                (int(uid),key,now)
            )
        c.commit()
    finally:
        c.close()

def user_pref_enabled(uid,key):
    try:
        _ensure_user_feature_preferences(uid)
        c=db()
        row=c.execute(
            "SELECT enabled FROM user_feature_preferences WHERE user_id=? AND feature_key=?",
            (int(uid),key)
        ).fetchone()
        c.close()
        return True if row is None else bool(row["enabled"])
    except Exception:
        logger.exception("User feature preference check failed: %s", key)
        return True

def set_user_pref(uid,key,enabled):
    c=db()
    c.execute(
        """INSERT INTO user_feature_preferences(user_id,feature_key,enabled,updated_at)
           VALUES(?,?,?,?)
           ON CONFLICT(user_id,feature_key)
           DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at""",
        (int(uid),key,int(bool(enabled)),datetime.now(TZ).isoformat())
    )
    c.commit(); c.close()

def user_feature_allowed(uid,key):
    if admin_is_allowed(uid) or key=="xp": return True
    try:
        # Manager-level OFF always wins over a user's personal preference.
        if not feature_enabled(key): return False
        mode=feature_access_mode(key,uid)
        if mode=="off" or (mode=="vip" and not is_vip(uid)): return False
        return user_pref_enabled(uid,key)
    except Exception:
        return True

def filter_menu_rows(uid,rows):
    out=[]
    for row in rows:
        r=[label for label in row if (FEATURE_MENU_MAP.get(label) is None or user_feature_allowed(uid,FEATURE_MENU_MAP[label]))]
        if r: out.append(r)
    return out

def keyboard(uid):
    _ensure_user_feature_preferences(uid)
    rows=filter_menu_rows(uid,[list(row) for row in T[lang(uid)]["menu"]])
    try:
        if user_feature_allowed(uid,"customers"):
            rows.append(["👥 مدیریت مشتری و نوبت‌دهی" if lang(uid)=="fa" else "👥 Customer & Appointments"])
    except Exception: pass
    rows.append(["📅 رزروهای من" if lang(uid)=="fa" else "📅 My Bookings"])
    if admin_is_allowed(uid):
        rows.append(["📢 مدیریت کانال","🛡 پنل مدیریت"] if lang(uid)=="fa" else ["📢 Channel Management","🛡 Admin Panel"])
    return ReplyKeyboardMarkup(rows,resize_keyboard=True)


def nav_keyboard(uid, include_back=True):
    """Temporary navigation keyboard for text-input modes. Back always exits the current mode safely."""
    fa = lang(uid) == "fa"
    rows = []
    if include_back:
        rows.append(["⬅️ برگشت" if fa else "⬅️ Back", "🏠 منوی اصلی" if fa else "🏠 Main Menu"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)


def clear_flow(context):
    """Clear only transient conversation state; persistent goals/settings remain intact."""
    for key in list(context.user_data.keys()):
        if key not in {"last_menu"}:
            context.user_data.pop(key, None)


def normalize_digits(s):
    return s.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))


def parse_time(s):
    s = normalize_digits(s.strip()).replace(".", ":").replace("：", ":")
    s = re.sub(r"\s+", "", s)
    if s.isdigit():
        if len(s) <= 2:
            h, m = int(s), 0
        elif len(s) == 3:
            h, m = int(s[0]), int(s[1:])
        elif len(s) == 4:
            h, m = int(s[:2]), int(s[2:])
        else:
            return None
    else:
        x = re.fullmatch(r"(\d{1,2}):(\d{1,2})", s)
        if not x:
            return None
        h, m = int(x.group(1)), int(x.group(2))
    if 0 <= h <= 23 and 0 <= m <= 59:
        return f"{h:02d}:{m:02d}"
    return None


def add_goal(uid, name, category, reminder, priority=2, duration_minutes=None):
    c = db()
    c.execute(
        "INSERT INTO goals(user_id,name,category,reminder_time,priority,duration_minutes,created_at) VALUES(?,?,?,?,?,?,?)",
        (uid, name, category, reminder, priority, duration_minutes, datetime.now(TZ).isoformat()),
    )
    c.commit()
    c.close()


def get_goals(uid):
    c = db()
    rows = c.execute(
        "SELECT * FROM goals WHERE user_id=? ORDER BY id DESC", (uid,)
    ).fetchall()
    c.close()
    return rows


def get_goal(uid, gid):
    c = db()
    r = c.execute(
        "SELECT * FROM goals WHERE user_id=? AND id=?", (uid, gid)
    ).fetchone()
    c.close()
    return r


def set_status(uid, gid, value):
    d = datetime.now(TZ).date().isoformat()
    done = datetime.now(TZ).isoformat() if value == "done" else None
    c = db()
    c.execute(
        """INSERT INTO goal_days(goal_id,user_id,goal_date,status,completed_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(goal_id,goal_date) DO UPDATE SET
           status=excluded.status, completed_at=excluded.completed_at""",
        (gid, uid, d, value, done),
    )
    c.commit()
    c.close()


def get_status(uid, gid, date=None):
    d = date or datetime.now(TZ).date().isoformat()
    c = db()
    r = c.execute(
        "SELECT status FROM goal_days WHERE user_id=? AND goal_id=? AND goal_date=?",
        (uid, gid, d),
    ).fetchone()
    c.close()
    return r["status"] if r else "pending"



def priority_keyboard(uid):
    if lang(uid) == "en":
        labels = [("🔴 High", 1), ("🟡 Medium", 2), ("🟢 Low", 3)]
    else:
        labels = [("🔴 زیاد", 1), ("🟡 متوسط", 2), ("🟢 کم", 3)]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"priority:{value}")]
        for label, value in labels
    ])


def add_step(uid, gid, title):
    c = db()
    c.execute(
        "INSERT INTO goal_steps(goal_id,user_id,title,created_at) VALUES(?,?,?,?)",
        (gid, uid, title, datetime.now(TZ).isoformat()),
    )
    c.commit()
    c.close()


def get_steps(uid, gid):
    c = db()
    rows = c.execute(
        "SELECT * FROM goal_steps WHERE user_id=? AND goal_id=? ORDER BY id",
        (uid, gid),
    ).fetchall()
    c.close()
    return rows


def toggle_step(uid, step_id):
    c = db()
    c.execute(
        """UPDATE goal_steps SET done=CASE WHEN done=1 THEN 0 ELSE 1 END
           WHERE user_id=? AND id=?""",
        (uid, step_id),
    )
    c.commit()
    c.close()


def calculate_streak(uid, gid):
    c = db()
    rows = c.execute(
        """SELECT goal_date FROM goal_days
           WHERE user_id=? AND goal_id=? AND status='done'
           ORDER BY goal_date DESC""",
        (uid, gid),
    ).fetchall()
    c.close()
    dates = {r["goal_date"] for r in rows}
    current = datetime.now(TZ).date()
    streak = 0
    while current.isoformat() in dates:
        streak += 1
        current = current.fromordinal(current.toordinal() - 1)
    return streak


def unlock_achievement(uid, code):
    c = db()
    before = c.total_changes
    c.execute(
        "INSERT OR IGNORE INTO achievements(user_id,code,unlocked_at) VALUES(?,?,?)",
        (uid, code, datetime.now(TZ).isoformat()),
    )
    unlocked = c.total_changes > before
    c.commit()
    c.close()
    return unlocked


def achievement_check(uid):
    c = db()
    total_goals = c.execute(
        "SELECT COUNT(*) AS n FROM goals WHERE user_id=?", (uid,)
    ).fetchone()["n"]
    total_done = c.execute(
        "SELECT COUNT(*) AS n FROM goal_days WHERE user_id=? AND status='done'",
        (uid,),
    ).fetchone()["n"]
    c.close()
    streak = max((calculate_streak(uid, g["id"]) for g in get_goals(uid)), default=0)

    found = []
    if total_goals >= 1 and unlock_achievement(uid, "first_goal"):
        found.append("🎯 اولین هدف")
    if total_done >= 1 and unlock_achievement(uid, "first_done"):
        found.append("🏅 اولین انجام")
    if total_done >= 10 and unlock_achievement(uid, "ten_done"):
        found.append("🔥 ۱۰ انجام موفق")
    if total_done >= 50 and unlock_achievement(uid, "fifty_done"):
        found.append("🏆 ۵۰ انجام موفق")
    return found


def achievement_text(uid):
    c = db()
    rows = c.execute(
        "SELECT code, unlocked_at FROM achievements WHERE user_id=? ORDER BY id DESC",
        (uid,),
    ).fetchall()
    c.close()
    labels = {
        "first_goal": "🎯 اولین هدف",
        "first_done": "🏅 اولین انجام",
        "ten_done": "🔥 ۱۰ انجام موفق",
        "fifty_done": "🏆 ۵۰ انجام موفق",
    }
    if not rows:
        return "🏆 هنوز دستاوردی نداری." if lang(uid) == "fa" else "🏆 No achievements yet."
    return "\n".join(f"{labels.get(r['code'], r['code'])} — {r['unlocked_at'][:10]}" for r in rows)


def goal_reminder_keyboard(uid,gid):
    fa=lang(uid)=="fa"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏰ فردا همین ساعت" if fa else "⏰ Tomorrow same time",callback_data=f"goalrem:{gid}:same")],
        [InlineKeyboardButton("🕐 فردا ساعت دلخواه" if fa else "🕐 Tomorrow custom time",callback_data=f"goalrem:{gid}:custom")],
        [InlineKeyboardButton("⏱ ۱۰ دقیقه بعد" if fa else "⏱ 10 min",callback_data=f"snooze:{gid}:10"),InlineKeyboardButton("⏱ ۳۰ دقیقه بعد" if fa else "⏱ 30 min",callback_data=f"snooze:{gid}:30")],
        [InlineKeyboardButton("⬅️ برگشت" if fa else "⬅️ Back",callback_data=f"detail:{gid}")]
    ])

async def goal_reminder_callback(update,context):
    q=update.callback_query; uid=q.from_user.id; await q.answer()
    parts=_safe_cb_parts(q.data)
    if not parts: return
    _,gid_s,mode=parts; gid=int(gid_s); g=get_goal(uid,gid)
    if not g: await q.answer("هدف پیدا نشد",show_alert=True); return
    if mode=="menu":
        await q.message.edit_text("⏰ <b>یادآوری دوباره</b>\n\nبرای فردا همان ساعت، ساعت جدید یا بعداً را انتخاب کن.",parse_mode="HTML",reply_markup=goal_reminder_keyboard(uid,gid)); return
    if mode=="same":
        tm=g["reminder_time"] or datetime.now(TZ).strftime("%H:%M"); d=(datetime.now(TZ).date()+timedelta(days=1)).isoformat(); c=db(); c.execute("INSERT OR REPLACE INTO goal_reminder_overrides(user_id,goal_id,reminder_date,reminder_time,created_at) VALUES(?,?,?,?,?)",(uid,gid,d,tm,datetime.now(TZ).isoformat())); c.commit(); c.close(); await q.message.edit_text(f"✅ برای فردا ساعت {tm} یادآوری شد.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ هدف",callback_data=f"detail:{gid}")],[main_menu_button(uid)]])); return
    if mode=="custom":
        context.user_data["goal_reminder_custom"]=gid; context.user_data["_flow_started_at"]=datetime.now(TZ).isoformat(); await q.message.reply_text("🕐 ساعت فردا را بفرست. مثال: 20:30",reply_markup=nav_keyboard(uid)); return

def snooze_keyboard(uid, gid):
    if lang(uid) == "en":
        labels = [("⏱ 10 min", 10), ("⏱ 30 min", 30), ("⏱ 60 min", 60)]
    else:
        labels = [("⏱ ۱۰ دقیقه", 10), ("⏱ ۳۰ دقیقه", 30), ("⏱ ۶۰ دقیقه", 60)]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"snooze:{gid}:{minutes}")]
        for label, minutes in labels
    ])


@subscription_required
async def snooze_callback(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    parts = _safe_cb_parts(q.data)
    if not parts: return
    _, gid_s, mins_s = parts
    gid, minutes = int(gid_s), int(mins_s)
    g = get_goal(uid, gid)
    if not g:
        return
    # One-shot snooze stored in job data. The regular daily reminder remains unchanged.
    context.job_queue.run_once(
        snooze_send,
        when=minutes * 60,
        data={"uid": uid, "gid": gid},
        name=f"snooze:{uid}:{gid}:{datetime.now(TZ).timestamp()}",
    )
    log_activity(uid, "snooze")
    text = (
        f"⏱ یادآوری «{g['name']}» برای {minutes} دقیقه دیگر تنظیم شد."
        if lang(uid) == "fa"
        else f"⏱ Reminder for “{g['name']}” set for {minutes} minutes."
    )
    await q.message.reply_text(text)


async def snooze_send(context):
    data = context.job.data
    uid, gid = data["uid"], data["gid"]
    g = get_goal(uid, gid)
    if not g or get_status(uid, gid) == "done":
        return
    try:
        await context.bot.send_message(
            uid,
            T[lang(uid)]["reminder"].format(
                name=display_name(uid), goal=g["name"]
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "✅ Done" if lang(uid) == "en" else "✅ انجام دادم",
                    callback_data=f"done:{gid}",
                ),
                InlineKeyboardButton(
                    "⏱ Snooze" if lang(uid) == "en" else "⏱ یادآوری بعداً",
                    callback_data=f"snooze_menu:{gid}",
                ),
            ]]),
        )
    except Exception as e:
        logger.error("Snooze send error: %s", e)


@subscription_required
async def snooze_menu(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    gid = int(q.data.split(":")[1])
    await q.message.reply_text(
        "⏱ زمان یادآوری مجدد را انتخاب کن:" if lang(uid) == "fa"
        else "⏱ Choose snooze duration:",
        reply_markup=snooze_keyboard(uid, gid),
    )


@subscription_required
async def steps_menu(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    gid = int(q.data.split(":")[1])
    steps = get_steps(uid, gid)
    buttons = []
    for s in steps:
        icon = "✅" if s["done"] else "⬜"
        buttons.append([InlineKeyboardButton(
            f"{icon} {s['title']}", callback_data=f"step_toggle:{s['id']}:{gid}"
        )])
    buttons.append([InlineKeyboardButton(
        "➕ Add step" if lang(uid) == "en" else "➕ افزودن مرحله",
        callback_data=f"step_add:{gid}"
    )])
    await q.message.reply_text(
        "📋 مراحل هدف" if lang(uid) == "fa" else "📋 Goal steps",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@subscription_required
async def step_add_start(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    gid = int(q.data.split(":")[1])
    context.user_data["step_gid"] = gid
    context.user_data["awaiting_step"] = True
    await q.message.reply_text(
        "✏️ نام مرحله را بفرست:" if lang(uid) == "fa" else "✏️ Send the step name:"
    )


async def step_save(update, context):
    uid = update.effective_user.id
    if not context.user_data.get("awaiting_step"):
        return False
    gid = context.user_data.get("step_gid")
    title = update.message.text.strip()
    if title:
        add_step(uid, gid, title)
        log_activity(uid, "step_created")
    context.user_data.pop("step_gid", None)
    context.user_data.pop("awaiting_step", None)
    await update.message.reply_text(
        "✅ مرحله اضافه شد." if lang(uid) == "fa" else "✅ Step added."
    )
    return True


@subscription_required
async def step_toggle(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    parts = _safe_cb_parts(q.data)
    if not parts: return
    _, step_id, gid = parts
    toggle_step(uid, int(step_id))
    log_activity(uid, "step_toggled")
    await steps_menu(update, context)


def categories_keyboard(uid, prefix="newcat"):
    data = GOALS_EN if lang(uid) == "en" else GOALS_FA
    rows = []
    for i, key in enumerate(data.keys()):
        rows.append([InlineKeyboardButton(key, callback_data=f"{prefix}:{i}")])
    rows.append([InlineKeyboardButton("🏠 منوی اصلی" if lang(uid)=="fa" else "🏠 Main Menu", callback_data="goals:main")])
    return InlineKeyboardMarkup(rows)


def category_by_index(uid, index):
    data = GOALS_EN if lang(uid) == "en" else GOALS_FA
    keys = list(data.keys())
    return keys[index]


def goals_by_category(uid, category):
    data = GOALS_EN if lang(uid) == "en" else GOALS_FA
    return data[category]


def time_keyboard(uid):
    buttons = [
        InlineKeyboardButton(x, callback_data=f"time:{x}") for x in TIME_BUTTONS
    ]
    rows = [buttons[i:i + 4] for i in range(0, len(buttons), 4)]
    rows.append([
        InlineKeyboardButton(T[lang(uid)]["no_reminder"], callback_data="time:none"),
        InlineKeyboardButton(T[lang(uid)]["other_time"], callback_data="time:custom"),
    ])
    return InlineKeyboardMarkup(rows)


def gender_keyboard(uid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(T[lang(uid)]["gender_male"], callback_data="gender:male")],
        [InlineKeyboardButton(T[lang(uid)]["gender_female"], callback_data="gender:female")],
        [InlineKeyboardButton(T[lang(uid)]["gender_none"], callback_data="gender:none")],
    ])


def required_channel():
    cfg = get_channel_config()
    return cfg["channel_id"] if cfg and cfg["channel_id"] else ""


def required_channel_url():
    if REQUIRED_CHANNEL_URL:
        return REQUIRED_CHANNEL_URL
    channel = required_channel()
    if channel.startswith("@"):
        return f"https://t.me/{channel[1:]}"
    return ""


async def is_channel_member(bot, uid):
    channel = required_channel()
    if not channel:
        return True
    # مدیر ربات نیازی به عضویت اجباری ندارد.
    if uid in ADMIN_IDS:
        return True
    try:
        member = await bot.get_chat_member(chat_id=channel, user_id=uid)
        return member.status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        } or (member.status == ChatMemberStatus.RESTRICTED and bool(getattr(member, "is_member", False)))
    except Exception as e:
        logger.error("Membership check failed for %s in %s: %s", uid, channel, e)
        return False


def subscription_keyboard():
    url = required_channel_url()
    rows = []
    if url:
        rows.append([InlineKeyboardButton("📢 عضویت در کانال", url=url)])
    rows.append([InlineKeyboardButton("✅ عضو شدم؛ بررسی کن", callback_data="subcheck")])
    return InlineKeyboardMarkup(rows)


async def require_subscription(update, context):
    uid = update.effective_user.id
    if await is_channel_member(context.bot, uid):
        return True

    url = required_channel_url()
    if url:
        text = (
            "🔒 برای استفاده از امکانات ربات، ابتدا عضو کانال شوید.\n\n"
            "بعد از عضویت روی «✅ عضو شدم؛ بررسی کن» بزنید."
        )
    else:
        text = (
            "🔒 برای استفاده از امکانات ربات، ابتدا عضو کانال شوید.\n\n"
            "لینک عضویت کانال هنوز برای ربات تنظیم نشده است. مدیر باید REQUIRED_CHANNEL_URL را تنظیم کند."
        )
    if update.callback_query:
        await update.callback_query.answer("ابتدا عضو کانال شوید.", show_alert=True)
        await update.callback_query.message.reply_text(text, reply_markup=subscription_keyboard())
    elif update.message:
        await update.message.reply_text(text, reply_markup=subscription_keyboard())
    return False


async def subscription_check_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    if await is_channel_member(context.bot, uid):
        await q.answer("✅ عضویت تأیید شد.")
        await q.message.reply_text(
            "✅ عضویت شما تأیید شد. حالا می‌توانید از همه امکانات ربات استفاده کنید.",
            reply_markup=keyboard(uid),
        )
    else:
        await q.answer("❌ هنوز عضویت شما تأیید نشده است.", show_alert=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name = update.effective_user.first_name or "دوست من"
    register_user(uid, name)
    if context.args:
        arg=context.args[0]
        if arg.startswith("book_"):
            # Public booking links are a complete flow. Do not continue into
            # the normal /start language-selection flow after opening them.
            handled = await customer_booking_start(update, context, arg[5:].strip())
            if handled:
                return
        if arg.startswith("ref_"):
            code=arg[4:].strip()
            try:
                c=db(); inviter=c.execute("SELECT user_id FROM users WHERE referral_code=?",(code,)).fetchone()
                if inviter and int(inviter["user_id"])!=uid:
                    c.execute("UPDATE users SET referrer_id=? WHERE user_id=? AND (referrer_id IS NULL OR referrer_id=0)",(int(inviter["user_id"]),uid))
                    cur_ref=c.execute("INSERT OR IGNORE INTO referrals(inviter_id,invited_id,created_at,rewarded) VALUES(?,?,?,0)",(int(inviter["user_id"]),uid,datetime.now(TZ).isoformat()))
                    c.commit()
                    if cur_ref.rowcount == 1:
                        try:
                            token_referral_reward(int(inviter["user_id"]), uid)
                            try:
                                await context.bot.send_message(
                                    chat_id=int(inviter["user_id"]),
                                    text="🎉 یک دعوت موفق ثبت شد!\n🎁 پاداش دعوت به کیف پولت اضافه شد.\nبرای دیدن آمار: /referral"
                                )
                            except Exception:
                                logger.exception("Referral reward notification failed")
                        except Exception: logger.exception("Referral token reward failed")
                c.close()
            except Exception as e: logger.warning("Referral registration failed: %s",e)
    if not await require_subscription(update, context):
        return
    info = user_info(uid)

    if info["gender"] is None:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🇮🇷 فارسی", callback_data="language:fa"),
            InlineKeyboardButton("🇬🇧 English", callback_data="language:en"),
        ]])
        await update.message.reply_text(
            f"🎯 سلام {name} عزیز! خوش اومدی 🌷\n\n"
            "زبان ربات رو انتخاب کن:\n"
            "🎯 Welcome! Select your language:",
            reply_markup=kb,
        )
    else:
        await update.message.reply_text(
            T[lang(uid)]["welcome"].format(name=name).replace(
                "زبان ربات رو انتخاب کن:", "منوی اصلی آماده‌ست 👇"
            ).replace(
                "Choose your language:", "Your menu is ready 👇"
            ),
            reply_markup=keyboard(uid),
        )
    log_activity(uid, "start")


@subscription_required
async def language_callback(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    value = q.data.split(":")[1]
    set_lang(uid, value)
    log_activity(uid, "language_change")
    await q.message.reply_text(
        T[value]["language_saved"],
        reply_markup=gender_keyboard(uid),
    )
    await q.message.reply_text(T[value]["gender"])


def onboarding_business_keyboard(uid):
    types=BUSINESS_TYPES_FA if lang(uid)=="fa" else BUSINESS_TYPES_EN
    rows=[[InlineKeyboardButton(x,callback_data=f"onboardtype:{i}")] for i,x in enumerate(types)]
    rows.append([InlineKeyboardButton("⏭️ رد کردن / بعداً انتخاب می‌کنم",callback_data="onboardtype:skip")])
    return InlineKeyboardMarkup(rows)


def onboarding_feature_keyboard(uid):
    fa=lang(uid)=="fa"
    choices=[
        ("goals","🎯 اهداف","🎯 Goals"),
        ("weekly","📅 جدول هفتگی","📅 Weekly"),
        ("stats","📊 آمار من","📊 My Stats"),
        ("price_data","📈 قیمت آنلاین","📈 Online Prices"),
        ("ai","🤖 چت با AI","🤖 AI Chat"),
        ("support","🎫 پشتیبانی","🎫 Support"),
        ("customers","👥 مدیریت مشتری و نوبت‌دهی","👥 Customers & Appointments"),
    ]
    rows=[]
    for key,fa_label,en_label in choices:
        if not feature_enabled(key): continue
        label=fa_label if fa else en_label
        mark="✅" if user_pref_enabled(uid,key) else "⬜"
        rows.append([InlineKeyboardButton(f"{mark} {label}",callback_data=f"pref:{key}")])
    rows.append([InlineKeyboardButton("⏭️ رد کردن / بعداً تنظیم می‌کنم" if fa else "⏭️ Skip / Configure later",callback_data="pref:skip")])
    rows.append([InlineKeyboardButton("✅ ادامه" if fa else "✅ Continue",callback_data="pref:done")])
    return InlineKeyboardMarkup(rows)

async def onboarding_feature_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not q: return
    await q.answer()
    action=q.data.split(":",1)[1]
    if action in ("done","skip"):
        await q.message.edit_text(
            "✅ تنظیمات اولیه ذخیره شد. هر زمان تمایل داشته باشید می‌توانید منوی شخصی خود را تغییر دهید."
            if lang(uid)=="fa" else
            "✅ Your initial preferences were saved. You can change your personal menu anytime.",
            reply_markup=InlineKeyboardMarkup([[main_menu_button(uid)]])
        )
        return
    set_user_pref(uid,action,not user_pref_enabled(uid,action))
    await q.message.edit_reply_markup(reply_markup=onboarding_feature_keyboard(uid))

@subscription_required
async def onboarding_business_callback(update,context):
    q=update.callback_query; uid=q.from_user.id; await q.answer()
    value=q.data.split(":",1)[1]
    if value!="skip":
        types=BUSINESS_TYPES_FA if lang(uid)=="fa" else BUSINESS_TYPES_EN; idx=int(value)
        ensure_business_profile(uid)
        c=db(); c.execute("UPDATE business_profiles SET business_type=?,updated_at=? WHERE user_id=?",(types[idx],datetime.now(TZ).isoformat(),uid)); c.commit(); c.close()
    _ensure_user_feature_preferences(uid)
    await q.message.edit_text(
        "⚙️ اگر تمایل داشته باشید، می‌توانید مشخص کنید کدام بخش‌ها در منوی شخصی شما نمایش داده شوند. این مرحله کاملاً اختیاری است."
        if lang(uid)=="fa" else
        "⚙️ If you wish, choose which sections you would like to see in your personal menu. This step is completely optional.",
        reply_markup=onboarding_feature_keyboard(uid)
    )


@subscription_required
async def gender_callback(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    value = q.data.split(":")[1]
    set_gender(uid, value)
    log_activity(uid, "gender_selected")
    await q.message.reply_text(
        T[lang(uid)]["gender_saved"].format(name=display_name(uid)) + "\n\n💼 اگر دوست داری، نوع فعالیت یا شغلت را هم انتخاب کن. این مرحله اجباری نیست:",
        reply_markup=onboarding_business_keyboard(uid),
    )


def settings_keyboard(uid):
    fa=lang(uid)=="fa"
    rows = [
        [InlineKeyboardButton("🌐 زبان" if fa else "🌐 Language",callback_data="settings:language")],
        [InlineKeyboardButton("🔔 اعلان‌ها" if fa else "🔔 Notifications",callback_data="settings:notifications")],
        [InlineKeyboardButton("🎯 اهداف" if fa else "🎯 Goals",callback_data="settings:goals")],
        [InlineKeyboardButton("🤖 هوش مصنوعی" if fa else "🤖 AI",callback_data="settings:ai")],
        [InlineKeyboardButton("💎 VIP و امکانات پولی" if fa else "💎 VIP & Paid Features",callback_data="settings:vip")],
    ]
    if admin_is_allowed(uid):
        rows.append([InlineKeyboardButton("📢 مدیریت کانال" if fa else "📢 Channel Management",callback_data="settings:channel")])
    rows.append([InlineKeyboardButton("🏠 منوی اصلی" if fa else "🏠 Main Menu",callback_data="settings:main")])
    return InlineKeyboardMarkup(rows)

async def settings(update, context):
    uid=update.effective_user.id; log_activity(uid,"settings")
    await hide_main_reply_keyboard(update)
    await update.message.reply_text(T[lang(uid)]["settings"],reply_markup=settings_keyboard(uid))

async def goals_navigation_callback(update, context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id
    action=q.data.split(":",1)[1] if ":" in (q.data or "") else ""
    if action=="main":
        clear_flow(context)
        # Do not delete the current goals screen. Replace it with the compact root.
        try:
            fa = lang(uid) == "fa"
            root_text = "🏠 <b>منوی اصلی</b>\n\nیک بخش را انتخاب کن." if fa else "🏠 <b>Main Menu</b>\n\nChoose a section."
            await q.message.edit_text(root_text, parse_mode="HTML", reply_markup=_compact_root_inline(uid))
        except Exception:
            try:
                await q.message.reply_text("🏠 منوی اصلی", reply_markup=keyboard(uid))
            except Exception:
                pass
        return
    await q.answer("این گزینه دیگر معتبر نیست. منوی اهداف را دوباره باز کن.", show_alert=True)


async def settings_language_callback(update, context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; value=q.data.split(":",1)[1]
    set_lang(uid,value); log_activity(uid,"language_change")
    await q.message.edit_text(T[value]["language_saved"],reply_markup=settings_keyboard(uid))


async def settings_callback(update, context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; action=q.data.split(":",1)[1] if ":" in (q.data or "") else ""; fa=lang(uid)=="fa"
    if action=="main":
        clear_flow(context)
        try:
            await q.message.edit_text(
                "🏠 <b>منوی اصلی</b>\n\nیک بخش را انتخاب کن." if fa else "🏠 <b>Main Menu</b>\n\nChoose a section.",
                parse_mode="HTML", reply_markup=_compact_root_inline(uid)
            )
        except Exception:
            try: await q.message.reply_text("🏠 منوی اصلی", reply_markup=keyboard(uid))
            except Exception: pass
        return
    if action=="language":
        await q.message.edit_text("زبان را انتخاب کن / Choose language:",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🇮🇷 فارسی",callback_data="setlang:fa"),InlineKeyboardButton("🇬🇧 English",callback_data="setlang:en")],[InlineKeyboardButton("↩️ تنظیمات",callback_data="settings:back")]])); return
    if action=="channel":
        if not admin_is_allowed(uid):
            await q.message.edit_text("⛔ دسترسی ندارید." if fa else "⛔ Access denied.")
            return
        await q.message.edit_text(
            "📢 <b>مدیریت کانال و پست‌گذاری</b>\n\n"
            "از اینجا می‌توانی اتصال کانال، ساخت پست، لیست پست‌ها و انتشار خودکار را مدیریت کنی."
            if fa else
            "📢 <b>Channel & Posting Management</b>\n\n"
            "Manage channel connection, posts, post list and automatic publishing here.",
            parse_mode="HTML",
            reply_markup=channel_keyboard(),
        )
        return
    if action=="notifications":
        c=db(); c.execute("INSERT OR IGNORE INTO user_settings(user_id) VALUES(?)",(uid,)); r=c.execute("SELECT reminders_enabled FROM user_settings WHERE user_id=?",(uid,)).fetchone(); c.close()
        state=bool(r["reminders_enabled"])
        await q.message.edit_text(("🔔 یادآوری‌ها: " + ("روشن" if state else "خاموش")) if fa else ("🔔 Reminders: " + ("On" if state else "Off")),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تغییر وضعیت",callback_data="settings:toggle_reminders")],[InlineKeyboardButton("↩️ تنظیمات",callback_data="settings:back")]])); return
    if action=="toggle_reminders":
        c=db(); c.execute("INSERT OR IGNORE INTO user_settings(user_id) VALUES(?)",(uid,)); c.execute("UPDATE user_settings SET reminders_enabled=1-reminders_enabled WHERE user_id=?",(uid,)); c.commit(); c.close(); await q.message.edit_text("✅ تنظیم شد.",reply_markup=settings_keyboard(uid)); return
    if action=="goals":
        await q.message.edit_text(("🎯 هدف‌ها دائمی هستند و فقط خودت می‌توانی حذفشان کنی. هنگام ساخت هدف می‌توانی مدت انجام را هم تعیین کنی." if fa else "🎯 Goals stay saved until you delete them. When creating a goal you can also set its duration."),reply_markup=settings_keyboard(uid)); return
    if action=="ai":
        providers=[]
        if omniroute_configured(): providers.append("🟢 OmniRoute")
        if bool(os.environ.get("OPENAI_API_KEY","").strip()): providers.append("🟢 OpenAI")
        if n8n_configured(): providers.append("🟢 n8n")
        status = "، ".join(providers) if providers else ("🔴 فعلاً هیچ سرویس AI متصل نیست." if fa else "🔴 No AI provider is connected yet.")
        text = (f"🤖 <b>چت با AI</b>\\n\\nسرویس‌های آماده: {status}\\nسهمیه رایگان روزانه: ۱۰ پیام"
                if fa else f"🤖 <b>AI Chat</b>\\n\\nAvailable providers: {status}\\nFree daily quota: 10 messages")
        await q.message.edit_text(text,parse_mode="HTML",reply_markup=settings_keyboard(uid)); return
    if action=="vip":
        xp,level,vip_until=xp_info(uid)
        text=(f"💎 VIP\n\nوضعیت: {'🟢 فعال' if is_vip(uid) else '⚪ عادی'}\n⭐ سطح: {level}\n👥 دعوت دوستان و فعالیت‌ها می‌توانند XP و پاداش بگیرند.\n\nپرداخت واقعی فعلاً از پنل مدیر قابل کنترل است." if fa else f"💎 VIP\n\nStatus: {'🟢 Active' if is_vip(uid) else '⚪ Free'}\n⭐ Level: {level}\n👥 Referrals and activity can earn XP/rewards.\n\nReal payments are controlled from the admin panel for now.")
        await q.message.edit_text(text,reply_markup=settings_keyboard(uid)); return
    if action in ("back","main"):
        if action=="main":
            await q.message.edit_text("🏠 منوی اصلی")
            await q.message.reply_text("🏠 منوی اصلی",reply_markup=keyboard(uid))
        else: await q.message.edit_text(T[lang(uid)]["settings"],reply_markup=settings_keyboard(uid))


def edit_time_keyboard(uid):
    buttons = [
        InlineKeyboardButton(x, callback_data=f"edit_time:{x}") for x in TIME_BUTTONS
    ]
    rows = [buttons[i:i + 4] for i in range(0, len(buttons), 4)]
    rows.append([
        InlineKeyboardButton(
            "🔕 بدون یادآوری" if lang(uid) == "fa" else "🔕 No reminder",
            callback_data="edit_time:none",
        ),
        InlineKeyboardButton(
            "🕐 ساعت دیگر" if lang(uid) == "fa" else "🕐 Custom time",
            callback_data="edit_time:custom",
        ),
    ])
    return InlineKeyboardMarkup(rows)


async def custom_goal_start(update, context):
    uid = update.effective_user.id
    clear_flow(context)
    context.user_data["awaiting_custom_goal"] = True
    await update.message.reply_text(
        "✏️ هدف خودت را بنویس:\nمثلاً: هر روز ۳۰ دقیقه زبان انگلیسی بخوانم"
        if lang(uid) == "fa" else
        "✏️ Write your own goal:\nExample: Study English for 30 minutes every day",
        reply_markup=nav_keyboard(uid),
    )


async def new_goal(update, context):
    uid = update.effective_user.id
    log_activity(uid, "new_goal")
    await update.message.reply_text(
        T[lang(uid)]["new_goal"].format(name=display_name(uid)),
        reply_markup=categories_keyboard(uid),
    )


@subscription_required
async def new_category(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    category = category_by_index(uid, int(q.data.split(":")[1]))
    context.user_data["category"] = category
    goals = goals_by_category(uid, category)
    buttons = [
        [InlineKeyboardButton(x, callback_data=f"newgoal:{i}")]
        for i, x in enumerate(goals)
    ]
    buttons.append([InlineKeyboardButton(T[lang(uid)]["back"], callback_data="newback")])
    await q.message.edit_text(
        T[lang(uid)]["choose_goal"],
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@subscription_required
async def new_back(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    context.user_data.pop("category", None)
    await q.message.edit_text(
        T[lang(uid)]["new_goal"].format(name=display_name(uid)),
        reply_markup=categories_keyboard(uid),
    )


@subscription_required
async def new_goal_pick(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    category = context.user_data.get("category")
    if not category:
        return
    goals = goals_by_category(uid, category)
    name = goals[int(q.data.split(":")[1])]
    context.user_data["name"] = name
    await q.message.edit_text(
        "⭐ اولویت هدف را انتخاب کن:" if lang(uid) == "fa" else "⭐ Choose goal priority:",
        reply_markup=priority_keyboard(uid),
    )



def duration_keyboard(uid):
    fa = lang(uid) == "fa"
    labels = [(5,"۵ دقیقه"),(10,"۱۰ دقیقه"),(20,"۲۰ دقیقه"),(30,"۳۰ دقیقه"),(60,"۱ ساعت"),(120,"۲ ساعت"),(0,"♾️ بدون محدودیت")]
    rows=[]
    for i in range(0,len(labels),2):
        pair=labels[i:i+2]
        rows.append([InlineKeyboardButton((label if fa else ({5:"5 min",10:"10 min",20:"20 min",30:"30 min",60:"1 hour",120:"2 hours",0:"♾️ No limit"}[m])),callback_data=f"duration:{m}") for m,label in pair])
    rows.append([InlineKeyboardButton("✏️ زمان دلخواه" if fa else "✏️ Custom",callback_data="duration:custom")])
    rows.append([InlineKeyboardButton(T[lang(uid)]["back"],callback_data="newback")])
    return InlineKeyboardMarkup(rows)


@subscription_required
async def priority_callback(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    context.user_data["priority"] = int(q.data.split(":")[1])
    await q.message.edit_text(
        "⏱ مدت انجام هدف را انتخاب کن:" if lang(uid)=="fa" else "⏱ How long should this goal take?",
        reply_markup=duration_keyboard(uid),
    )


@subscription_required
async def duration_callback(update, context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id
    value=q.data.split(":",1)[1]
    if value=="custom":
        context.user_data["awaiting_custom_duration"]=True
        await q.message.edit_text("✏️ مدت را به دقیقه وارد کن (مثلاً 45)." if lang(uid)=="fa" else "✏️ Enter duration in minutes (e.g. 45).")
        return
    context.user_data["duration_minutes"] = None if value=="0" else int(value)
    await q.message.edit_text(T[lang(uid)]["choose_time"],reply_markup=time_keyboard(uid))


@subscription_required
async def time_callback(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    value = q.data.split(":", 1)[1]

    if value == "custom":
        context.user_data["awaiting_custom_time"] = True
        await q.message.edit_text(T[lang(uid)]["custom_time"])
        return

    if value == "none":
        reminder = None
    else:
        reminder = parse_time(value)

    name = context.user_data.get("name")
    category = context.user_data.get("category")
    if not name or not category:
        return

    priority = context.user_data.get("priority", 2)
    duration = context.user_data.get("duration_minutes")
    add_goal(uid, name, category, reminder, priority, duration)
    context.user_data.clear()
    log_activity(uid, "goal_created")
    # Callback messages accept InlineKeyboardMarkup only. keyboard(uid) is a ReplyKeyboardMarkup.
    await q.message.edit_text(T[lang(uid)]["goal_added"].format(name=display_name(uid)))
    await q.message.reply_text("🏠 منوی اصلی", reply_markup=keyboard(uid))


async def custom_duration_save(update, context):
    uid=update.effective_user.id
    if not context.user_data.get("awaiting_custom_duration"): return False
    try:
        minutes=int(normalize_digits(update.message.text.strip()))
        if minutes<1 or minutes>1440: raise ValueError
    except ValueError:
        await update.message.reply_text("❌ عدد نامعتبر است؛ بین ۱ تا ۱۴۴۰ دقیقه." if lang(uid)=="fa" else "❌ Enter a number from 1 to 1440 minutes.")
        return True
    context.user_data["duration_minutes"]=minutes
    context.user_data.pop("awaiting_custom_duration",None)
    await update.message.reply_text(T[lang(uid)]["choose_time"],reply_markup=time_keyboard(uid))
    return True


async def custom_goal_save(update, context):
    uid=update.effective_user.id
    if not context.user_data.get("awaiting_custom_goal"): return False
    name=update.message.text.strip()
    if not name:
        return True
    context.user_data["name"]=name
    context.user_data["category"]="🎯 هدف دلخواه" if lang(uid)=="fa" else "🎯 Custom"
    context.user_data.pop("awaiting_custom_goal",None)
    await update.message.reply_text("⭐ اولویت هدف را انتخاب کن:" if lang(uid)=="fa" else "⭐ Choose goal priority:",reply_markup=priority_keyboard(uid))
    return True


async def custom_time_save(update, context):
    uid = update.effective_user.id
    if not context.user_data.get("awaiting_custom_time"):
        return False

    value = update.message.text.strip()
    reminder = parse_time(value)
    if reminder is None:
        await update.message.reply_text(T[lang(uid)]["bad_time"])
        return True

    name = context.user_data.get("name")
    category = context.user_data.get("category")
    if not name or not category:
        context.user_data.clear()
        return False

    priority = context.user_data.get("priority", 2)
    duration = context.user_data.get("duration_minutes")
    add_goal(uid, name, category, reminder, priority, duration)
    context.user_data.clear()
    log_activity(uid, "goal_created")
    await update.message.reply_text(
        T[lang(uid)]["goal_added"].format(name=display_name(uid)),
        reply_markup=keyboard(uid),
    )
    return True


async def ready_menu(update, context):
    await new_goal(update, context)


async def today(update, context):
    uid = update.effective_user.id
    goals = get_goals(uid)
    log_activity(uid, "view_today")
    if not goals:
        await update.message.reply_text(
            T[lang(uid)]["no_goals"].format(name=display_name(uid)),
            reply_markup=keyboard(uid),
        )
        return

    buttons = []
    for g in goals:
        s = get_status(uid, g["id"])
        icon = "✅" if s == "done" else "❌" if s == "missed" else "⬜"
        buttons.append([
            InlineKeyboardButton(
                f"{icon} {g['name']}",
                callback_data=f"detail:{g['id']}",
            )
        ])
    buttons.append([InlineKeyboardButton("🏠 منوی اصلی" if lang(uid)=="fa" else "🏠 Main Menu",callback_data="goals:main")])
    await update.message.reply_text(
        T[lang(uid)]["today"],
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@subscription_required
async def detail(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    gid = int(q.data.split(":")[1])
    g = get_goal(uid, gid)
    if not g:
        return
    await q.message.edit_text(
        f"🎯 {g['name']}\n📁 {g['category']}\n⭐ اولویت: {g['priority']}\n⏰ {g['reminder_time'] or 'Off'}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "✅ Done" if lang(uid) == "en" else "✅ انجام دادم",
                callback_data=f"done:{gid}",
            ),
            InlineKeyboardButton(
                "❌ Not done" if lang(uid) == "en" else "❌ انجام ندادم",
                callback_data=f"miss:{gid}",
            ),
        ], [
            InlineKeyboardButton(
                "📋 Steps" if lang(uid) == "en" else "📋 مراحل",
                callback_data=f"steps:{gid}",
            ),
        ], [InlineKeyboardButton("↩️ اهداف امروز" if lang(uid)=="fa" else "↩️ Today's Goals",callback_data="goals:main")]]),
    )


@subscription_required
async def mark(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    gid = int(q.data.split(":")[1])
    is_done = q.data.startswith("done:")
    set_status(uid, gid, "done" if is_done else "missed")
    log_activity(uid, "goal_done" if is_done else "goal_missed")
    if is_done:
        add_xp(uid, 10, "goal_completed")
        new_achievements = achievement_check(uid)
        if new_achievements:
            await q.message.edit_text(
                ("🏆 دستاورد جدید!\n" + "\n".join(new_achievements))
                if lang(uid) == "fa"
                else ("🏆 New achievement!\n" + "\n".join(new_achievements))
            )
    result_text = (
        T[lang(uid)]["done"].format(name=display_name(uid))
        if is_done
        else T[lang(uid)]["missed"].format(name=display_name(uid))
    )
    await q.message.edit_text(result_text)
    await q.message.reply_text("🏠 منوی اصلی", reply_markup=keyboard(uid))


async def edit_menu(update, context):
    uid = update.effective_user.id
    goals = get_goals(uid)
    log_activity(uid, "edit_goals")
    if not goals:
        await update.message.reply_text(
            T[lang(uid)]["no_goals"].format(name=display_name(uid)),
            reply_markup=keyboard(uid),
        )
        return
    edit_buttons=[[InlineKeyboardButton(g["name"], callback_data=f"edit:{g['id']}")] for g in goals]
    edit_buttons.append([InlineKeyboardButton("🏠 منوی اصلی" if lang(uid)=="fa" else "🏠 Main Menu",callback_data="goals:main")])
    await update.message.reply_text(
        T[lang(uid)]["edit"].format(name=display_name(uid)),
        reply_markup=InlineKeyboardMarkup(edit_buttons),
    )


@subscription_required
async def edit_goal(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    gid = int(q.data.split(":")[1])
    g = get_goal(uid, gid)
    if not g:
        return
    buttons = [
        [InlineKeyboardButton(
            "✏️ Change name" if lang(uid) == "en" else "✏️ تغییر نام",
            callback_data=f"rename:{gid}",
        )],
        [InlineKeyboardButton(
            "⏰ Change reminder" if lang(uid) == "en" else "⏰ تغییر یادآوری",
            callback_data=f"changereminder:{gid}",
        )],
        [InlineKeyboardButton(
            "🗑 Delete" if lang(uid) == "en" else "🗑 حذف",
            callback_data=f"delete:{gid}",
        )],
        [InlineKeyboardButton("🏠 منوی اصلی" if lang(uid)=="fa" else "🏠 Main Menu",callback_data="goals:main")],
    ]
    await q.message.edit_text(
        f"🎯 {g['name']}\n⏰ {g['reminder_time'] or 'Off'}",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@subscription_required
async def rename_start(update, context):
    q = update.callback_query
    await q.answer()
    context.user_data["edit_id"] = int(q.data.split(":")[1])
    context.user_data["awaiting_rename"] = True
    await q.message.edit_text(T[lang(q.from_user.id)]["name"])


async def rename_save(update, context):
    uid = update.effective_user.id
    gid = context.user_data.get("edit_id")
    if not gid or not context.user_data.get("awaiting_rename"):
        return False
    name = update.message.text.strip()
    if not name:
        return True
    c = db()
    c.execute(
        "UPDATE goals SET name=? WHERE user_id=? AND id=?",
        (name, uid, gid),
    )
    c.commit()
    c.close()
    context.user_data.clear()
    log_activity(uid, "goal_renamed")
    await update.message.reply_text(
        T[lang(uid)]["changed"],
        reply_markup=keyboard(uid),
    )
    return True


@subscription_required
async def change_reminder(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    gid = int(q.data.split(":")[1])
    if not get_goal(uid, gid):
        return
    context.user_data["edit_reminder_id"] = gid
    await q.message.edit_text(
        T[lang(uid)]["choose_time"],
        reply_markup=time_keyboard(uid),
    )



@subscription_required
async def edit_time_callback(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    gid = context.user_data.get("edit_reminder_id")
    if not gid:
        return
    value = q.data.split(":", 1)[1]
    if value == "custom":
        context.user_data["awaiting_custom_edit_time"] = True
        await q.message.edit_text(T[lang(uid)]["custom_time"])
        return
    reminder = None if value == "none" else parse_time(value)
    c = db()
    c.execute(
        "UPDATE goals SET reminder_time=? WHERE user_id=? AND id=?",
        (reminder, uid, gid),
    )
    c.commit()
    c.close()
    context.user_data.pop("edit_reminder_id", None)
    log_activity(uid, "reminder_changed")
    await q.message.edit_text("✅ زمان یادآوری تغییر کرد." if lang(uid) == "fa" else "✅ Reminder time updated.")
    await q.message.reply_text("🏠 منوی اصلی", reply_markup=keyboard(uid))


@subscription_required
async def time_change_callback(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    gid = context.user_data.get("edit_reminder_id")
    if not gid:
        return

    value = q.data.split(":", 1)[1]
    if value == "custom":
        context.user_data["awaiting_edit_time"] = True
        await q.message.edit_text(T[lang(uid)]["custom_time"])
        return

    reminder = None if value == "none" else parse_time(value)
    c = db()
    c.execute(
        "UPDATE goals SET reminder_time=? WHERE user_id=? AND id=?",
        (reminder, uid, gid),
    )
    c.commit()
    c.close()
    context.user_data.pop("edit_reminder_id", None)
    log_activity(uid, "reminder_changed")
    await q.message.edit_text(T[lang(uid)]["changed"])
    await q.message.reply_text("🏠 منوی اصلی", reply_markup=keyboard(uid))


async def custom_edit_time_save(update, context):
    uid = update.effective_user.id
    if not context.user_data.get("awaiting_edit_time"):
        return False
    gid = context.user_data.get("edit_reminder_id")
    reminder = parse_time(update.message.text.strip())
    if reminder is None:
        await update.message.reply_text(T[lang(uid)]["bad_time"])
        return True
    c = db()
    c.execute(
        "UPDATE goals SET reminder_time=? WHERE user_id=? AND id=?",
        (reminder, uid, gid),
    )
    c.commit()
    c.close()
    context.user_data.pop("edit_reminder_id", None)
    context.user_data.pop("awaiting_edit_time", None)
    log_activity(uid, "reminder_changed")
    await update.message.reply_text(T[lang(uid)]["changed"], reply_markup=keyboard(uid))
    return True


@subscription_required
async def delete_start(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    gid = int(q.data.split(":")[1])
    g = get_goal(uid, gid)
    if not g:
        return
    text = "Delete this goal?" if lang(uid) == "en" else "این هدف حذف شود؟"
    yes = "Yes, delete" if lang(uid) == "en" else "بله، حذف کن"
    no = "Cancel" if lang(uid) == "en" else "لغو"
    await q.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(yes, callback_data=f"delete_yes:{gid}")],
            [InlineKeyboardButton(no, callback_data="delete_no")],
        ]),
    )


@subscription_required
async def delete_confirm(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    gid = int(q.data.split(":")[1])
    c = db()
    c.execute("DELETE FROM goal_days WHERE user_id=? AND goal_id=?", (uid, gid))
    c.execute("DELETE FROM goals WHERE user_id=? AND id=?", (uid, gid))
    c.commit()
    c.close()
    log_activity(uid, "goal_deleted")
    await q.message.edit_text(T[lang(uid)]["deleted"])
    await q.message.reply_text("🏠 منوی اصلی", reply_markup=keyboard(uid))


@subscription_required
async def delete_no(update, context):
    q = update.callback_query
    await q.answer()
    await q.message.edit_text("❌ Cancelled" if lang(q.from_user.id) == "en" else "❌ لغو شد.")
    await q.message.reply_text("🏠 منوی اصلی" if lang(q.from_user.id) == "fa" else "🏠 Main Menu", reply_markup=keyboard(q.from_user.id))



async def achievements(update, context):
    uid = update.effective_user.id
    await update.message.reply_text(
        "🏆 دستاوردهای تو\n\n" + achievement_text(uid)
        if lang(uid) == "fa"
        else "🏆 Your achievements\n\n" + achievement_text(uid)
    )
    log_activity(uid, "achievements")


async def profile(update, context):
    uid = update.effective_user.id
    info = user_info(uid)
    goals = get_goals(uid)
    today_date = datetime.now(TZ).date().isoformat()
    c = db()
    done = c.execute(
        "SELECT COUNT(*) AS n FROM goal_days WHERE user_id=? AND goal_date=? AND status='done'",
        (uid, today_date),
    ).fetchone()["n"]
    c.close()
    gender_map = {
        "male": T[lang(uid)]["gender_male"],
        "female": T[lang(uid)]["gender_female"],
        "none": T[lang(uid)]["gender_none"],
    }
    gender = gender_map.get(info["gender"], "-")
    joined = info["created_at"][:10]
    await update.message.reply_text(
        T[lang(uid)]["profile"].format(
            name=display_name(uid),
            goals=len(goals),
            done=done,
            date=jalali_pretty_date(joined),
        )
        + "\n"
        + T[lang(uid)]["profile_gender"].format(gender=gender)
    )
    log_activity(uid, "profile")


async def weekly(update, context):
    uid = update.effective_user.id
    goals = get_goals(uid)
    lines = []
    today_date = datetime.now(TZ).date()
    names = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]
    if lang(uid) == "en":
        names = ["Sat", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri"]

    # Show the last 7 calendar days, with a compact per-day completion count.
    for offset in range(6, -1, -1):
        d = today_date.fromordinal(today_date.toordinal() - offset)
        ds = d.isoformat()
        c = db()
        done = c.execute(
            "SELECT COUNT(*) AS n FROM goal_days WHERE user_id=? AND goal_date=? AND status='done'",
            (uid, ds),
        ).fetchone()["n"]
        total = len(goals)
        c.close()
        weekday = names[(d.weekday() + 1) % 7] if lang(uid) == "fa" else names[d.weekday()]
        lines.append(f"📅 {weekday} {ds}: {done}/{total} ✅")
    await update.message.reply_text(
        T[lang(uid)]["weekly"].format(name=display_name(uid), rows="\n".join(lines))
    )
    log_activity(uid, "weekly")


async def stats(update, context):
    uid = update.effective_user.id
    goals = get_goals(uid)
    # Calculate the user's current streak here.  The previous V22 version
    # referenced `streak` without defining it, which caused the whole
    # "📊 آمار من" handler to fail and the global error handler to show
    # "بقیه امکانات همچنان در دسترس‌اند".
    streak = max((calculate_streak(uid, g["id"]) for g in goals), default=0)
    d = datetime.now(TZ).date().isoformat()
    c = db()
    done = c.execute(
        "SELECT COUNT(*) AS n FROM goal_days WHERE user_id=? AND goal_date=? AND status='done'",
        (uid, d),
    ).fetchone()["n"]
    missed = c.execute(
        "SELECT COUNT(*) AS n FROM goal_days WHERE user_id=? AND goal_date=? AND status='missed'",
        (uid, d),
    ).fetchone()["n"]
    total_done = c.execute(
        "SELECT COUNT(*) AS n FROM goal_days WHERE user_id=? AND status='done'",
        (uid,),
    ).fetchone()["n"]
    c.close()
    await update.message.reply_text(
        T[lang(uid)]["stats"].format(
            name=display_name(uid),
            goals=len(goals),
            done=done,
            missed=missed,
            total_done=total_done,
        )
        + (
            f"\n🔥 رکورد زنجیره فعلی: {streak} روز"
            if lang(uid) == "fa"
            else f"\n🔥 Current streak: {streak} days"
        )
    )
    log_activity(uid, "stats")



def _safe_channel_enabled(value, default=1):
    """Normalize legacy channel enabled values without allowing a malformed DB value to abort /start."""
    if value is None:
        return int(default)
    if isinstance(value, bool):
        return int(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled", "active"}:
        return 1
    if text in {"0", "false", "no", "off", "disabled", "inactive", ""}:
        return 0
    try:
        return 1 if int(float(text)) != 0 else 0
    except (TypeError, ValueError):
        logger.warning("Invalid channel enabled value %r; using default=%s", value, default)
        return int(default)


def get_channel_config():
    """Return the currently selected channel, while preserving legacy channel_config."""
    c=db()
    try:
        active=get_system_setting("active_channel_id", "") if "get_system_setting" in globals() else ""
        if active:
            r=c.execute("SELECT channel_id,enabled,updated_at FROM managed_channels WHERE channel_id=?",(active,)).fetchone()
            if r: return r
        r=c.execute("SELECT * FROM channel_config WHERE id=1").fetchone()
        if r and r["channel_id"]:
            enabled = _safe_channel_enabled(r["enabled"], 1)
            c.execute("INSERT OR IGNORE INTO managed_channels(channel_id,title,enabled,created_at,updated_at) VALUES(?,?,?,?,?)",(str(r["channel_id"]),str(r["channel_id"]),enabled,r["updated_at"],r["updated_at"]))
            c.commit()
        return r
    finally:
        c.close()

def list_managed_channels():
    c=db(); rows=c.execute("SELECT * FROM managed_channels ORDER BY id").fetchall(); c.close(); return rows

def set_active_channel(channel_id):
    channel_id=str(channel_id)
    set_system_setting("active_channel_id",channel_id)
    now=datetime.now(TZ).isoformat(); c=db(); r=c.execute("SELECT channel_id,enabled FROM managed_channels WHERE channel_id=?",(channel_id,)).fetchone()
    if r: c.execute("UPDATE managed_channels SET enabled=1,updated_at=? WHERE channel_id=?",(now,channel_id))
    c.execute("INSERT INTO channel_config(id,channel_id,enabled,updated_at) VALUES(1,?,1,?) ON CONFLICT(id) DO UPDATE SET channel_id=excluded.channel_id,enabled=1,updated_at=excluded.updated_at",(channel_id,now)); c.commit(); c.close()

def persistent_channel_config(channel_id):
    set_channel_config(channel_id)

def set_channel_config(channel_id, title=""):
    channel_id=str(channel_id).strip(); now=datetime.now(TZ).isoformat(); c=db()
    c.execute("INSERT INTO managed_channels(channel_id,title,enabled,created_at,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(channel_id) DO UPDATE SET title=CASE WHEN excluded.title!='' THEN excluded.title ELSE managed_channels.title END,enabled=1,updated_at=excluded.updated_at",(channel_id,title or channel_id,1,now,now))
    c.execute("INSERT INTO channel_config(id,channel_id,enabled,updated_at) VALUES(1,?,1,?) ON CONFLICT(id) DO UPDATE SET channel_id=excluded.channel_id,enabled=1,updated_at=excluded.updated_at",(channel_id,now)); c.commit(); c.close(); set_system_setting("active_channel_id",channel_id)

def remove_managed_channel(channel_id):
    channel_id=str(channel_id); c=db(); c.execute("DELETE FROM managed_channels WHERE channel_id=?",(channel_id,)); rows=c.execute("SELECT channel_id FROM managed_channels WHERE enabled=1 ORDER BY id").fetchall(); next_id=rows[0]["channel_id"] if rows else ""; c.execute("UPDATE channel_config SET channel_id=?,enabled=?,updated_at=? WHERE id=1",(next_id,1 if next_id else 0,datetime.now(TZ).isoformat())); c.commit(); c.close(); set_system_setting("active_channel_id",next_id)

def normalize_channel_input(value):
    """Accept a public channel username and convert it to Telegram @username format."""
    value = (value or "").strip()
    value = value.replace("https://t.me/", "").replace("http://t.me/", "")
    value = value.split("?", 1)[0].split("/", 1)[0].strip()
    value = value.lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", value):
        raise ValueError("invalid channel username")
    return "@" + value


async def bot_can_manage_channel(bot, channel):
    """Check that the bot is an administrator with permission to post in the channel."""
    me = await bot.get_me()
    member = await bot.get_chat_member(chat_id=channel, user_id=me.id)
    if member.status not in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}:
        return False, "❌ ربات در کانال ادمین نیست. ابتدا ربات را ادمین کانال کن."
    if member.status == ChatMemberStatus.ADMINISTRATOR and not bool(getattr(member, "can_post_messages", False)):
        return False, "❌ ربات ادمین است، ولی اجازه ارسال پست ندارد. دسترسی ارسال پیام را فعال کن."
    return True, "OK"


def add_channel_post(content, typ, schedule_time=None, weekday=None, run_at=None, created_by=0):
    c=db(); cur=c.execute("INSERT INTO channel_posts(content,schedule_type,schedule_time,weekday,run_at,enabled,created_at,created_by) VALUES(?,?,?,?,?,1,?,?)",(content,typ,schedule_time,weekday,run_at,datetime.now(TZ).isoformat(),created_by)); pid=cur.lastrowid; c.commit(); c.close(); return pid


AUTO_TOPIC_TREE_FA = {
    "🎯 هدف‌گذاری": [
        "تعیین هدف‌های کوچک و قابل اندازه‌گیری",
        "اولویت‌بندی هدف‌ها",
        "برنامه‌ریزی هفتگی",
        "شکستن هدف بزرگ به قدم‌های کوچک",
        "پیگیری پیشرفت هدف"
    ],
    "📈 رشد فردی": [
        "ساخت عادت‌های خوب",
        "انضباط شخصی",
        "اعتمادبه‌نفس",
        "مدیریت اهمال‌کاری",
        "خودشناسی و ارزیابی پیشرفت"
    ],
    "⏱ مدیریت زمان": [
        "برنامه‌ریزی روزانه",
        "تمرکز عمیق",
        "مقابله با حواس‌پرتی",
        "اولویت‌بندی کارها",
        "استراحت و زمان‌بندی درست"
    ],
    "💰 سرمایه‌گذاری و مالی": [
        "سواد مالی",
        "بودجه‌بندی شخصی",
        "پس‌انداز",
        "مدیریت ریسک",
        "مفاهیم پایه سرمایه‌گذاری",
        "ارز و دلار",
        "طلا و سکه",
        "کریپتو و بازار رمزارز",
        "بورس و شاخص‌ها"
    ],
    "🏃 ورزش و برنامه کوتاه": [
        "ورزش ۱۰ دقیقه‌ای در خانه",
        "حرکات کششی بعد از کار",
        "ورزش سبک برای روزهای خستگی",
        "تمرین کل بدن بدون وسیله",
        "گرم‌کردن و سردکردن"
    ],
    "🍎 تغذیه و خواص خوراکی‌ها": [
        "خواص سیب",
        "خواص لیمو",
        "خواص موز",
        "آب و هیدراته ماندن",
        "میان‌وعده سالم"
    ],
    "🧠 سخنان بزرگان و دانشمندان": [
        "سخنان دانشمندان ایرانی",
        "سخنان دانشمندان جهان",
        "بزرگان و اندیشمندان قدیمی",
        "دانشمندان و استادان معاصر",
        "جملات کوتاه برای شروع روز"
    ],
    "🌅 صبح و 🌙 شب": [
        "پیام شروع روز",
        "هدف‌گذاری صبحگاهی",
        "جمع‌بندی شبانه",
        "ارزیابی روز",
        "آرام‌سازی قبل از خواب"
    ],
    "💼 کار و کسب‌وکار": [
        "مهارت‌های شغلی",
        "راه‌اندازی کسب‌وکار",
        "برند شخصی",
        "مدیریت پروژه",
        "افزایش بهره‌وری"
    ],
    "📚 یادگیری": [
        "روش مطالعه بهتر",
        "یادگیری مهارت جدید",
        "مرور و یادسپاری",
        "کتاب‌خوانی",
        "یادگیری زبان"
    ],
    "🧠 ذهن و تمرکز": [
        "تمرکز",
        "مدیریت استرس",
        "مدیریت افکار",
        "مدیتیشن و آرام‌سازی",
        "استراحت ذهنی"
    ],
    "🏃 سلامتی و سبک زندگی": [
        "خواب بهتر",
        "ورزش و حرکت",
        "تغذیه متعادل",
        "آب و انرژی روزانه",
        "روتین صبح و شب"
    ],
    "🤝 ارتباطات": [
        "مهارت گفت‌وگو",
        "گوش دادن فعال",
        "مرزبندی سالم",
        "حل تعارض",
        "روابط حرفه‌ای"
    ],
    "🚀 انگیزه و موفقیت": [
        "شروع کردن",
        "ادامه دادن",
        "عبور از شکست",
        "ساختن نظم",
        "ثبت موفقیت‌های کوچک"
    ],
}

AUTO_INTERVALS_MIN = [5, 10, 15, 20, 30, 60, 120, 180, 240, 360, 720, 1440]

def _topic_focus(topic):
    """Return a strict content brief for the selected topic.
    The selected topic is the subject of the post, not merely an example.
    """
    t = str(topic or "").strip()
    rules = {
        "ورزش ۱۰ دقیقه‌ای": "روی اثر فعالیت ۱۰ دقیقه‌ای بر بدن، انرژی و حال‌وهوای روز، نمونه ساختار ۱۰ دقیقه‌ای و نکات اجرای ایمن تمرکز کن.",
        "خواب بهتر": "روی کیفیت خواب، عادت‌های قبل خواب، نور و صفحه‌نمایش، زمان‌بندی و اثر خواب کافی بر انرژی و تمرکز تمرکز کن.",
        "آب کافی": "روی نقش آب در بدن، نشانه‌های کم‌آبی، زمان‌های مناسب نوشیدن و یک روش عملی برای مصرف منظم آب تمرکز کن.",
        "صبحانه سالم": "روی اجزای یک صبحانه متعادل، انرژی صبح، ترکیب پروتئین/فیبر و چند انتخاب عملی تمرکز کن.",
        "مطالعه ۲۰ دقیقه‌ای": "روی مطالعه متمرکز ۲۰ دقیقه‌ای، انتخاب مطلب، حذف حواس‌پرتی و یک روش ساده برای شروع تمرکز کن.",
        "۱۰ دقیقه تمرکز": "روی ایجاد یک بازه تمرکز ۱۰ دقیقه‌ای، حذف مزاحمت‌ها، شروع کار و استراحت کوتاه تمرکز کن.",
        "۳۰ دقیقه کار بدون حواس‌پرتی": "روی یک بازه کاری ۳۰ دقیقه‌ای، حذف اعلان‌ها، تعیین یک خروجی مشخص و حفظ تمرکز تمرکز کن.",
        "پس‌انداز روزانه": "روی کنار گذاشتن مبلغ کوچک روزانه، کنترل هزینه‌های غیرضروری و ساخت عادت پس‌انداز تمرکز کن.",
    }
    return rules.get(t, f"تمام محتوای پست باید مستقیماً درباره «{t}» باشد؛ تعریف موضوع، فایده یا کاربرد آن، یک روش عملی مرتبط و یک تمرین مشخص مرتبط با همان موضوع را توضیح بده.")


def _topic_terms(topic):
    """Keywords used as a lightweight relevance gate after generation."""
    t = str(topic or "").strip().lower()
    aliases = {
        "ورزش ۱۰ دقیقه‌ای": ["ورزش", "۱۰ دقیقه", "دقیقه", "بدن", "تمرین"],
        "خواب بهتر": ["خواب", "شب", "استراحت", "خوابیدن"],
        "آب کافی": ["آب", "نوشیدن", "کم‌آبی", "لیوان"],
        "مطالعه ۲۰ دقیقه‌ای": ["مطالعه", "۲۰ دقیقه", "کتاب", "یادگیری"],
        "۱۰ دقیقه تمرکز": ["تمرکز", "۱۰ دقیقه", "حواس", "کار"],
        "پس‌انداز روزانه": ["پس‌انداز", "هزینه", "پول", "روزانه"],
    }
    return aliases.get(t, [x for x in re.findall(r"[؀-ۿA-Za-z0-9]+", t) if len(x) > 2])


def _is_topic_relevant(text, topic):
    """Reject generic output when it barely mentions the selected topic."""
    body = str(text or "").lower()
    terms = _topic_terms(topic)
    if not terms:
        return True
    hits = sum(1 for term in terms if term.lower() in body)
    # Generic topics can have fewer lexical matches, but must still mention
    # the topic itself or a meaningful keyword from it.
    exact = str(topic or "").strip().lower() in body
    needed = 1 if len(terms) == 1 else 2
    return exact or hits >= min(needed, len(terms))



def _normalize_post_text(value):
    value=str(value or "").lower()
    value=re.sub(r"https?://\S+", " ", value)
    value=re.sub(r"[^\w؀-ۿ]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())

def _post_similarity(a,b):
    na=_normalize_post_text(a); nb=_normalize_post_text(b)
    if not na or not nb: return 0.0
    if hashlib.sha256(na.encode("utf-8")).hexdigest()==hashlib.sha256(nb.encode("utf-8")).hexdigest():
        return 1.0
    return difflib.SequenceMatcher(None,na,nb).ratio()

def recent_auto_posts(channel_id, limit=12):
    c=db()
    try:
        return c.execute("SELECT topic,content,created_at FROM auto_post_history WHERE channel_id=? ORDER BY id DESC LIMIT ?",(str(channel_id),limit)).fetchall()
    finally: c.close()

def post_is_duplicate(channel_id, topic, content, threshold=0.78):
    """Never reuse an automatic post on the same channel.
    Exact duplicates are blocked forever; near-duplicates are checked against
    the full stored history, not only the last few posts.
    """
    normalized=_normalize_post_text(content)
    digest=hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    c=db()
    try:
        exact=c.execute("SELECT 1 FROM auto_post_history WHERE channel_id=? AND content_hash=? LIMIT 1",(str(channel_id),digest)).fetchone()
        if exact:
            return True,1.0
        rows=c.execute("SELECT topic,content FROM auto_post_history WHERE channel_id=? ORDER BY id DESC",(str(channel_id),)).fetchall()
    finally:
        c.close()
    for row in rows:
        sim=_post_similarity(content,row["content"])
        if sim>=threshold or (row["topic"]==topic and sim>=0.62):
            return True,sim
    return False,0.0

def save_auto_post_history(channel_id, topic, category, content):
    normalized=_normalize_post_text(content)
    digest=hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    c=db()
    try:
        c.execute("""INSERT OR IGNORE INTO auto_post_history
                     (channel_id,topic,category,content,content_hash,created_at)
                     VALUES(?,?,?,?,?,?)""",
                  (str(channel_id),topic,category,content,digest,datetime.now(TZ).isoformat()))
        c.commit()
    finally: c.close()

def topic_specific_fallback(topic, attempt=1):
    focus=_topic_focus(topic)
    variants=[
        f"🎯 {topic}\\n\\n{focus}\\n\\n• امروز یک اقدام مشخص درباره همین موضوع انتخاب کن.\\n• نتیجه را کوتاه ثبت کن.\\n💡 تمرین: ۱۰ دقیقه فقط روی «{topic}» کار کن.",
        f"📌 {topic}\\n\\n{focus}\\n\\n• یک مانع مرتبط با این موضوع را حذف کن.\\n• یک قدم کوچک و قابل اندازه‌گیری بردار.\\n💡 تمرین امروز: یک اقدام مستقیم درباره «{topic}» انجام بده.",
        f"🧠 {topic}\\n\\n{focus}\\n\\n• موضوع را به یک کار کوچک تبدیل کن.\\n• زمان شروع را مشخص کن.\\n💡 اقدام امروز: یک قدم مرتبط با «{topic}» انجام بده.",
    ]
    return variants[(attempt-1)%len(variants)]

def generate_unique_auto_post(channel_id, category, topic):
    recent=recent_auto_posts(channel_id,8)
    avoid="\n".join(f"- {r['topic']}: {str(r['content'])[:220]}" for r in recent)
    for attempt in range(1,9):
        content=ai_generate_post(topic, avoid_text=avoid, variation_seed=attempt)
        duplicate,score=post_is_duplicate(channel_id,topic,content)
        if not duplicate and _is_topic_relevant(content,topic):
            return content
        logger.warning("Auto post rejected topic=%s attempt=%s similarity=%.2f",topic,attempt,score)
        avoid += f"\n- نسخه ردشده: {str(content)[:220]}"
    for attempt in range(1,9):
        candidate=topic_specific_fallback(topic,attempt)
        duplicate,_=post_is_duplicate(channel_id,topic,candidate,threshold=0.90)
        if not duplicate:
            return candidate
    # Last-resort uniqueness guard: preserve the topic while making the text
    # materially different so the database UNIQUE hash can never collide.
    # Last-resort fallback: add a deterministic per-attempt nonce and verify it
    # against the complete history before returning.
    for n in range(1,21):
        candidate=topic_specific_fallback(topic,8)+f"\n\n🆕 نسخه {datetime.now(TZ).strftime('%Y%m%d')}-{n}"
        duplicate,_=post_is_duplicate(channel_id,topic,candidate,threshold=0.995)
        if not duplicate:
            return candidate
    raise RuntimeError("Unable to generate a unique automatic post")

def _ai_post_prompt(topic, focus, topic_terms, avoid_text, variation_seed):
    return (
        "تو نویسنده حرفه‌ای محتوای کانال MyTasks هستی.\n"
        f"موضوع انتخاب‌شده و غیرقابل‌تغییر: «{topic}»\n"
        f"راهنمای موضوع: {focus}\n"
        f"کلیدواژه‌ها: {topic_terms}\n"
        f"نسخه: {variation_seed}\n"
        f"پست‌های اخیر که نباید تکرار شوند:\n{avoid_text[:1800]}\n\n"
        "فقط متن نهایی پست را برگردان. خود prompt، قوانین، تحلیل یا توضیح فرایند را منتشر نکن. "
        "حداکثر 120 کلمه. یک تیتر دقیق، یک توضیح کوتاه، سه نکته کاربردی مرتبط و یک اقدام یک‌خطی مرتبط بنویس. "
        "اگر موضوع ورزش است فقط درباره همان ورزش/تمرین بنویس؛ اگر خواب است درباره خواب؛ موضوع را به مدیریت هدف عمومی تبدیل نکن. "
        "از ادعاهای قطعی پزشکی یا مالی خودداری کن. متن را با پاراگراف‌بندی طبیعی و بدون نمایش عبارت‌های literal مانند \\\\n برگردان."
    )

def _clean_ai_post(text):
    text=str(text or "").strip()
    text=text.replace("\\\\r\\\\n","\n").replace("\\\\n","\n").replace("\\r\\n","\n")
    text=re.sub(r'\n{3,}','\n\n',text)
    # Remove accidental prompt/meta preambles.
    bad_prefixes=("تمام محتوای پست باید","قانون بسیار مهم","prompt:","system:")
    lines=text.splitlines()
    while lines and any(lines[0].strip().lower().startswith(x.lower()) for x in bad_prefixes):
        lines.pop(0)
    return "\n".join(lines).strip()

def _gemini_generate_text(prompt):
    if not GEMINI_API_KEY:
        return ""
    try:
        payload=json.dumps({
            "contents":[{"parts":[{"text":prompt}]}],
            "generationConfig":{"temperature":0.8,"maxOutputTokens":360}
        },ensure_ascii=False).encode("utf-8")
        url=f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(GEMINI_MODEL)}:generateContent"
        req=urllib.request.Request(url,data=payload,headers={"Content-Type":"application/json","x-goog-api-key":GEMINI_API_KEY},method="POST")
        with urllib.request.urlopen(req,timeout=35) as resp:
            data=json.loads(resp.read().decode("utf-8"))
        parts=data.get("candidates",[{}])[0].get("content",{}).get("parts",[])
        return _clean_ai_post("".join(str(x.get("text","")) for x in parts))
    except Exception as e:
        logger.error("Gemini text generation failed: %s",e)
        return ""

def ai_text_generate(prompt, max_output_tokens=500, purpose="general"):
    """Unified text-AI gateway used by chat, smart posts and other AI text features.
    Provider failures are isolated. The next configured provider is tried.
    No secret is exposed in logs or user messages.
    """
    providers=[]
    if n8n_configured():
        providers.append(("n8n", lambda: _n8n_ai_fallback_sync(prompt)))
    if omniroute_configured():
        providers.append(("OmniRoute", lambda: _omniroute_ai_sync(prompt)))
    if GEMINI_API_KEY:
        providers.append(("Gemini", lambda: _gemini_generate_text(prompt)))
    api_key=os.environ.get("OPENAI_API_KEY", "").strip()
    if api_key:
        def _openai_text():
            payload=json.dumps({
                "model": OPENAI_MODEL,
                "input": str(prompt)[:8000],
                "max_output_tokens": int(max_output_tokens),
            }, ensure_ascii=False).encode("utf-8")
            req=urllib.request.Request(
                "https://api.openai.com/v1/responses",
                data=payload,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=35) as resp:
                data=json.loads(resp.read().decode("utf-8"))
            return str(data.get("output_text") or "").strip()
        providers.append(("OpenAI", _openai_text))

    for name, fn in providers:
        try:
            answer=str(fn() or "").strip()
            if answer:
                _record_service_event(name.lower(), "OK", f"{purpose} unified AI")
                return answer[:8000]
        except Exception as exc:
            _record_service_event(name.lower(), "ERROR", f"{purpose}:{type(exc).__name__}")
            logger.warning("Unified AI provider %s failed for %s: %s", name, purpose, type(exc).__name__)
    return ""

def ai_generate_post(topic, avoid_text='', variation_seed=1):
    focus=_topic_focus(topic)
    topic_terms=", ".join(_topic_terms(topic)[:6])
    prompt=_ai_post_prompt(topic,focus,topic_terms,avoid_text,variation_seed)

    result=_clean_ai_post(ai_text_generate(prompt, max_output_tokens=360, purpose="channel_post"))
    if result and _is_topic_relevant(result,topic):
        return result
    return topic_specific_fallback(topic,variation_seed)


def get_auto_topic():
    category = get_auto_setting("category", "random")
    subcategory = get_auto_setting("subcategory", "random")
    last_topic = get_auto_setting("last_topic", "")
    if category in AUTO_TOPIC_TREE_FA:
        items = AUTO_TOPIC_TREE_FA[category]
        if subcategory in items and subcategory != last_topic:
            return category, subcategory
        choices=[x for x in items if x != last_topic] or items
        return category, random.choice(choices)
    categories=list(AUTO_TOPIC_TREE_FA)
    history=[]
    cfg=get_channel_config()
    if cfg and cfg["channel_id"]:
        history=[r["topic"] for r in recent_auto_posts(cfg["channel_id"],6)]
    cat_choices=[c for c in categories if c not in history] or categories
    cat=random.choice(cat_choices)
    items=AUTO_TOPIC_TREE_FA[cat]
    choices=[x for x in items if x != last_topic and x not in history] or items
    return cat, random.choice(choices)


def compact_channel_footer(bot_username, channel_username):
    parts = []
    if channel_username:
        parts.append(f"📢 کانال: {channel_username}")
    if bot_username:
        parts.append(f"🤖 ربات: {bot_username}")
    return "\n\n" + " | ".join(parts) if parts else ""


async def generate_topic_image(topic):
    """Generate a related local PNG image at zero API cost.

    Pillow is optional. If it is unavailable, image generation is simply
    skipped and the text-only post flow continues normally.
    """
    if not PIL_AVAILABLE:
        logger.info("Pillow is not installed; image generation skipped.")
        return None
    try:
        bg=(17,24,39); accent=(124,58,237)
        img=Image.new("RGB",(1024,1024),bg); d=ImageDraw.Draw(img)
        d.rounded_rectangle((80,80,944,944),radius=60,outline=(255,255,255),width=3)
        d.ellipse((690,70,950,330),fill=(255,255,255),outline=None)
        d.ellipse((40,690,360,1010),fill=(255,255,255),outline=None)
        try: font_big=ImageFont.truetype("DejaVuSans-Bold.ttf",72); font=ImageFont.truetype("DejaVuSans.ttf",42); small=ImageFont.truetype("DejaVuSans.ttf",30)
        except: font_big=font=small=ImageFont.load_default()
        d.text((512,380),"MyTasks",font=font_big,anchor="mm",fill="white")
        topic_text=str(topic)[:70]
        d.multiline_text((512,510),topic_text,font=font,anchor="mm",align="center",fill=(229,231,235),spacing=10)
        d.text((512,820),"یک قدم کوچک، هر روز",font=small,anchor="mm",fill=(203,213,225))
        bio=io.BytesIO(); img.save(bio,format="PNG",optimize=True); bio.seek(0); bio.name="mytasks_post.png"; return bio
    except Exception as e:
        logger.error("Free image generation failed: %s",e); return None

async def get_identity_handles(bot, channel):
    bot_identity = ""
    channel_identity = ""
    try:
        me = await bot.get_me()
        bot_identity = f"@{me.username}" if me.username else str(me.id)
    except Exception as e:
        logger.warning("Could not get bot identity: %s", e)
    try:
        chat = await bot.get_chat(channel)
        channel_identity = f"@{chat.username}" if getattr(chat, "username", None) else str(chat.id)
    except Exception as e:
        logger.warning("Could not get channel identity: %s", e)
    return bot_identity, channel_identity


def content_feedback_keyboard(topic):
    key=re.sub(r"\s+","_",str(topic))[:50]
    return InlineKeyboardMarkup([[InlineKeyboardButton("👍 مفید بود",callback_data=f"feedback:up:{key}"),InlineKeyboardButton("👎 مناسب نبود",callback_data=f"feedback:down:{key}")]])

async def feedback_callback(update,context):
    q=update.callback_query; uid=q.from_user.id; await q.answer("ثبت شد")
    parts = _safe_cb_parts(q.data, ":", 3)
    if not parts: return
    _,rating,topic=parts; score=1 if rating=="up" else -1; now=datetime.now(TZ).isoformat(); c=db(); c.execute("INSERT INTO content_feedback(post_key,user_id,rating,reaction,created_at) VALUES(?,?,?,?,?)",(topic,uid,score,rating,now)); c.execute("INSERT INTO content_preferences(user_id,category,score) VALUES(?,?,?) ON CONFLICT(user_id,category) DO UPDATE SET score=score+excluded.score",(uid,topic,score)); c.commit(); c.close(); add_xp(uid,2,"content_feedback")


async def send_auto_channel_post(context, channel, topic, category=None):
    if not feature_enabled("auto_publish"):
        raise RuntimeError("auto_publish feature is disabled")
    category=category or get_auto_setting("category","random")
    content=generate_unique_auto_post(channel,category,topic)
    bot_username,channel_username=await get_identity_handles(context.bot,channel)
    content=content[:950]+compact_channel_footer(bot_username,channel_username)
    # Channel auto-posts are intentionally text-only. Feedback is collected in
    # the end-of-day poll, not under each individual post.
    try:
        msg=await context.bot.send_message(chat_id=channel,text=content)
        save_auto_post_history(channel,topic,category,content)
        if any(k in topic for k in ("ورزش","حرکات","تمرین")):
            try:
                await context.bot.send_poll(chat_id=channel,question="🏃 تمرین امروز را انجام دادی؟",options=["✅ انجام دادم","⏳ هنوز نه","❌ انجام ندادم"],is_anonymous=False)
            except Exception as e:
                logger.warning("Exercise poll failed: %s",e)
        return msg
    except Exception:
        msg=await context.bot.send_message(chat_id=channel,text=content)
        save_auto_post_history(channel,topic,category,content)
        return msg


def _auto_channel_scope():
    try:
        cfg=get_channel_config()
        return str(cfg["channel_id"]) if cfg and cfg["channel_id"] else "__global__"
    except Exception:
        return "__global__"

def get_auto_setting(key, default=""):
    c=db()
    c.execute("""CREATE TABLE IF NOT EXISTS auto_channel_settings_v2(
        channel_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
        PRIMARY KEY(channel_id,key)
    )""")
    scope=_auto_channel_scope(); r=c.execute("SELECT value FROM auto_channel_settings_v2 WHERE channel_id=? AND key=?",(scope,key)).fetchone()
    if not r:
        # Backward-compatible migration from the old global setting table.
        try:
            old=c.execute("SELECT value FROM auto_channel_settings WHERE key=?",(key,)).fetchone()
        except Exception:
            old=None
        if old:
            c.execute("INSERT OR IGNORE INTO auto_channel_settings_v2(channel_id,key,value) VALUES(?,?,?)",(scope,key,old["value"])); c.commit(); value=old["value"]
        else: value=default
    else: value=r["value"]
    c.close(); return value

def set_auto_setting(key,value):
    c=db(); c.execute("""CREATE TABLE IF NOT EXISTS auto_channel_settings_v2(
        channel_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(channel_id,key))""")
    c.execute("INSERT INTO auto_channel_settings_v2(channel_id,key,value) VALUES(?,?,?) ON CONFLICT(channel_id,key) DO UPDATE SET value=excluded.value",(_auto_channel_scope(),key,str(value))); c.commit(); c.close()


async def auto_channel_job(context):
    cfg=get_channel_config(); channel=cfg["channel_id"] if cfg else ""
    if not channel or get_auto_setting("enabled","0")!="1" or not feature_enabled("auto_publish"): return
    now=datetime.now(TZ); interval=int(get_auto_setting("interval_minutes","60") or 60)
    next_raw=get_auto_setting("next_run","")
    try: next_run=datetime.fromisoformat(next_raw) if next_raw else now+timedelta(minutes=interval)
    except ValueError: next_run=now+timedelta(minutes=interval)
    if next_run.tzinfo is None: next_run=next_run.replace(tzinfo=TZ)
    approval=feature_enabled("approval") and bool(ADMIN_IDS)
    if approval:
        preview_at=next_run-timedelta(minutes=5)
        c=db(); pending=c.execute("SELECT * FROM auto_pending WHERE channel_id=? AND publish_at=? AND status IN ('pending','approved') ORDER BY id DESC LIMIT 1",(str(channel),next_run.isoformat())).fetchone(); c.close()
        if now>=preview_at and now<next_run and not pending:
            category,topic=get_auto_topic(); content=generate_unique_auto_post(channel,category,topic); bot_username,channel_username=await get_identity_handles(context.bot,channel); content=content[:950]+compact_channel_footer(bot_username,channel_username)
            c=db(); cur=c.execute("INSERT INTO auto_pending(channel_id,topic,content,publish_at,created_at) VALUES(?,?,?,?,?)",(str(channel),topic,content,next_run.isoformat(),now.isoformat())); pid=cur.lastrowid; c.commit(); c.close()
            kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ تأیید شده از طرف من → انتشار",callback_data=f"appr:{pid}"),InlineKeyboardButton("❌ رد",callback_data=f"apprrej:{pid}")]])
            for admin_id in ADMIN_IDS:
                try: await context.bot.send_message(admin_id,f"👁 <b>پیش‌نمایش پست</b>\n\n📂 {category}\n🕐 انتشار در: {next_run.strftime('%H:%M')}\n\n{content}",parse_mode="HTML",reply_markup=kb)
                except Exception as e: logger.error("Approval preview failed: %s",e)
            return
        if now<next_run: return
        c=db(); pending=c.execute("SELECT * FROM auto_pending WHERE channel_id=? AND publish_at=? ORDER BY id DESC LIMIT 1",(str(channel),next_run.isoformat())).fetchone(); c.close()
        if pending and pending["status"]=="approved":
            try:
                bot_username,channel_username=await get_identity_handles(context.bot,channel); content=pending["content"]
                await context.bot.send_message(chat_id=channel,text=content)
                save_auto_post_history(channel,pending["topic"],get_auto_setting("category","random"),content)
                log_activity(ADMIN_IDS[0],"auto_channel_post_approved")
            except Exception as e: logger.error("Approved auto post failed: %s",e)
        c=db(); c.execute("UPDATE auto_pending SET status=CASE WHEN status='approved' THEN 'published' ELSE 'expired' END WHERE channel_id=? AND publish_at=?",(str(channel),next_run.isoformat())); c.commit(); c.close()
        set_auto_setting("last_run",now.isoformat()); set_auto_setting("next_run",(now+timedelta(minutes=interval)).isoformat()); return
    if now<next_run: return
    next_run=now+timedelta(minutes=interval); set_auto_setting("next_run",next_run.isoformat())
    category,topic=get_auto_topic()
    try:
        msg=await send_auto_channel_post(context,channel,topic,category); set_auto_setting("last_run",now.isoformat()); set_auto_setting("last_message_id",str(msg.message_id)); set_auto_setting("last_category",category); set_auto_setting("last_topic",topic); log_activity(ADMIN_IDS[0] if ADMIN_IDS else 0,"auto_channel_post")
    except Exception as e:
        set_auto_setting("next_run",now.isoformat()); logger.error("Automatic channel post failed: %s",e)


async def approval_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔",show_alert=True); return
    await q.answer(); pid=int(q.data.split(":",1)[1]); c=db(); r=c.execute("SELECT * FROM auto_pending WHERE id=?",(pid,)).fetchone();
    if not r: c.close(); await q.message.edit_text("❌ پیش‌نمایش پیدا نشد."); return
    c.execute("UPDATE auto_pending SET status='approved' WHERE id=?",(pid,)); c.commit(); c.close(); await q.message.edit_text("✅ تأیید شد. پست در زمان تعیین‌شده منتشر می‌شود.")

async def approval_reject_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔",show_alert=True); return
    await q.answer(); pid=int(q.data.split(":",1)[1]); c=db(); c.execute("UPDATE auto_pending SET status='rejected' WHERE id=? AND status='pending'",(pid,)); c.commit(); c.close(); await q.message.edit_text("❌ پست رد شد و منتشر نمی‌شود.")


def channel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📡 تنظیم کانال", callback_data="ch:set"),
         InlineKeyboardButton("🔌 تست اتصال", callback_data="ch:test")],
        [InlineKeyboardButton("📝 ساخت پست", callback_data="ch:new"),
         InlineKeyboardButton("🤖 ساخت پست هوشمند", callback_data="ch:smart")],
        [InlineKeyboardButton("📋 پست‌ها", callback_data="ch:list"),
         InlineKeyboardButton("🕘 تاریخچه انتشار", callback_data="ch:history")],
        [InlineKeyboardButton("🧩 چند پست / زمان‌بندی", callback_data="ch:batch"),
         InlineKeyboardButton("🤖 انتشار خودکار", callback_data="ch:auto")],
        [InlineKeyboardButton("📢 کانال‌های متصل", callback_data="ch:channels")],
        [InlineKeyboardButton("⬅️ پنل مدیریت", callback_data="adm:stats")]
    ])

def channel_schedule_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📤 ارسال فوری",callback_data="chs:now")],[InlineKeyboardButton("📅 یک‌بار",callback_data="chs:once"),InlineKeyboardButton("🔄 روزانه",callback_data="chs:daily")],[InlineKeyboardButton("📆 هفتگی",callback_data="chs:weekly")],[InlineKeyboardButton("❌ لغو",callback_data="chs:cancel")]])

def channel_time_keyboard(prefix):
    rows=[[InlineKeyboardButton(x,callback_data=f"{prefix}:{x}") for x in TIME_BUTTONS[i:i+4]] for i in range(0,len(TIME_BUTTONS),4)]

def channel_schedule_text(r):
    if r["schedule_type"]=="daily": return f"🔄 روزانه {r['schedule_time']}"
    if r["schedule_type"]=="weekly": return f"📆 هفتگی روز {r['weekday']} ساعت {r['schedule_time']}"
    return f"📅 {r['run_at'].replace('T',' ') if r['run_at'] else 'فوری'}"

async def channel_scheduler_job(context):
    now=datetime.now(TZ); key=now.strftime("%Y-%m-%d %H:%M"); hhmm=now.strftime("%H:%M"); cfg=get_channel_config()
    if not cfg or not cfg["enabled"] or not cfg["channel_id"]: return
    c=db(); rows=c.execute("SELECT * FROM channel_posts WHERE enabled=1").fetchall(); c.close()
    for r in rows:
        due=(r["schedule_type"]=="daily" and r["schedule_time"]==hhmm) or (r["schedule_type"]=="weekly" and r["weekday"]==now.weekday() and r["schedule_time"]==hhmm) or (r["schedule_type"]=="once" and r["run_at"] and r["run_at"][:16]==key)
        if not due or (r["last_sent_at"] and r["last_sent_at"][:16]==key): continue
        try:
            await context.bot.send_message(chat_id=cfg["channel_id"],text=r["content"])
            c=db()
            if r["schedule_type"]=="once": c.execute("UPDATE channel_posts SET enabled=0,last_sent_at=? WHERE id=?",(now.isoformat(),r["id"]))
            else: c.execute("UPDATE channel_posts SET last_sent_at=? WHERE id=?",(now.isoformat(),r["id"]))
            c.commit(); c.close()
        except Exception as e: logger.error("Channel post failed: %s",e)


def auto_channel_keyboard():
    enabled = get_auto_setting("enabled", "0") == "1"
    interval = int(get_auto_setting("interval_minutes", "60") or 60)
    category = get_auto_setting("category", "random")
    subcategory = get_auto_setting("subcategory", "random")
    state = "🟢 خودکار روشن" if enabled else "⚪ خودکار خاموش"
    topic_text = "🎲 تصادفی"
    if category != "random":
        topic_text = category
        if subcategory != "random":
            topic_text += f"\n↳ {subcategory}"

    rows = [
        [InlineKeyboardButton(state, callback_data="auto:toggle")],
        [
            InlineKeyboardButton(f"⏱ هر {interval} دقیقه", callback_data="auto:interval"),
            InlineKeyboardButton("🧠 موضوع", callback_data="auto:category"),
        ],
        [InlineKeyboardButton(topic_text[:60], callback_data="auto:category")],
        [InlineKeyboardButton("📋 وضعیت و زمان بعدی", callback_data="auto:info")],
        [InlineKeyboardButton("🧪 تست ۷ روزه", callback_data="auto:test")],
        [InlineKeyboardButton("📚 راهنمای استفاده", callback_data="auto:guide")],
        [InlineKeyboardButton("⬅️ مدیریت کانال", callback_data="ch:main")],
    ]
    return InlineKeyboardMarkup(rows)


def auto_category_keyboard():
    rows = [
        [InlineKeyboardButton("🎲 انتخاب تصادفی", callback_data="autocat:random")]
    ]
    for i, category in enumerate(AUTO_TOPIC_TREE_FA):
        rows.append([InlineKeyboardButton(category, callback_data=f"autocat:{i}")])
    rows.append([InlineKeyboardButton("⬅️ انتشار خودکار", callback_data="auto:back")])
    return InlineKeyboardMarkup(rows)


def auto_subcategory_keyboard(category_index):
    categories = list(AUTO_TOPIC_TREE_FA.keys())
    category = categories[category_index]
    rows = [
        [InlineKeyboardButton("🎲 همه شاخه‌های این دسته", callback_data=f"autosub:{category_index}:random")]
    ]
    for i, sub in enumerate(AUTO_TOPIC_TREE_FA[category]):
        rows.append([InlineKeyboardButton(sub, callback_data=f"autosub:{category_index}:{i}")])
    rows.append([InlineKeyboardButton("⬅️ دسته‌ها", callback_data="auto:category")])
    return InlineKeyboardMarkup(rows)


def auto_interval_keyboard():
    rows = []
    for i in range(0, len(AUTO_INTERVALS_MIN), 2):
        pair = AUTO_INTERVALS_MIN[i:i + 2]
        rows.append([
            InlineKeyboardButton(
                f"هر {m // 60} ساعت" if m >= 60 and m % 60 == 0 else f"هر {m} دقیقه",
                callback_data=f"autoint:{m}"
            )
            for m in pair
        ])
    rows.append([InlineKeyboardButton("✏️ زمان دلخواه", callback_data="autoint:custom")])
    rows.append([InlineKeyboardButton("⬅️ انتشار خودکار", callback_data="auto:back")])
    return InlineKeyboardMarkup(rows)


@subscription_required
async def auto_channel_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    if not admin_guard(uid):
        await q.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await q.answer()
    action = q.data.split(":", 1)[1]

    if action == "toggle":
        new_value = "0" if get_auto_setting("enabled", "0") == "1" else "1"
        set_auto_setting("enabled", new_value)
        if new_value == "1":
            interval = int(get_auto_setting("interval_minutes", "60") or 60)
            set_auto_setting("next_run", (datetime.now(TZ) + timedelta(minutes=interval)).isoformat())
        await q.message.edit_text(
            "🟢 انتشار خودکار روشن شد." if new_value == "1" else "⚪ انتشار خودکار خاموش شد.",
            reply_markup=auto_channel_keyboard()
        )

    elif action == "interval":
        await q.message.edit_text(
            "⏱ فاصله انتشار را انتخاب کن.\nاز ۵ دقیقه تا ۲۴ ساعت، یا زمان دلخواه:",
            reply_markup=auto_interval_keyboard()
        )

    elif action == "category":
        await q.message.edit_text(
            "🧠 دسته‌بندی کامل را انتخاب کن:",
            reply_markup=auto_category_keyboard()
        )

    elif action == "guide":
        await q.message.edit_text(
            "📚 <b>راهنمای انتشار خودکار MyTasks</b>\n\n"
            "1️⃣ اول از بخش «🧠 موضوع» دسته موردنظر را انتخاب کن.\n"
            "2️⃣ سپس زیرشاخه را انتخاب کن؛ مثلاً سرمایه‌گذاری ← ارز و دلار.\n"
            "3️⃣ بعد از انتخاب موضوع، ربات از تو می‌پرسد هر چند دقیقه یک پست منتشر شود.\n"
            "4️⃣ زمان را از ۵ دقیقه تا ۲۴ ساعت انتخاب کن یا زمان دلخواه وارد کن.\n"
            "5️⃣ با انتخاب زمان، انتشار خودکار روشن می‌شود.\n\n"
            "⏸ برای توقف، روی «خودکار روشن» بزن.\n"
            "⚙️ برای تغییر موضوع یا فاصله انتشار، دوباره همان گزینه را انتخاب کن.\n"
            "↩️ در همه بخش‌ها امکان برگشت وجود دارد.",
            parse_mode="HTML",
            reply_markup=auto_channel_keyboard(),
        )

    elif action == "test":
        await q.message.edit_text(
            "🧪 تست ۷ روزه\n\n"
            f"وضعیت: {'🟢 فعال' if test_mode_active() else '🔴 خاموش/پایان یافته'}\n"
            f"زمان باقی‌مانده: {test_mode_remaining()}\n\n"
            "در زمان تست، پست خودکار قبل از انتشار برای Admin پیش‌نمایش می‌شود."
        )
    elif action == "info":
        interval = get_auto_setting("interval_minutes", "60")
        next_run = get_auto_setting("next_run", "تنظیم نشده").replace("T", " ")[:16]
        category = get_auto_setting("category", "random")
        sub = get_auto_setting("subcategory", "random")
        await q.message.edit_text(
            f"🤖 وضعیت انتشار خودکار\n\n"
            f"وضعیت: {'🟢 روشن' if get_auto_setting('enabled','0')=='1' else '⚪ خاموش'}\n"
            f"⏱ فاصله: هر {interval} دقیقه\n"
            f"🧠 دسته: {category}\n"
            f"📌 شاخه: {sub}\n"
            f"🕐 انتشار بعدی: {next_run}",
            reply_markup=auto_channel_keyboard()
        )

    elif action == "back":
        await q.message.edit_text("🤖 انتشار خودکار", reply_markup=auto_channel_keyboard())


@subscription_required
async def auto_category_callback(update, context):
    q = update.callback_query
    if not admin_guard(q.from_user.id):
        await q.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await q.answer()
    value = q.data.split(":", 1)[1]
    if value == "random":
        set_auto_setting("category", "random")
        set_auto_setting("subcategory", "random")
        await q.message.edit_text(
            "🎲 موضوعات به‌صورت تصادفی انتخاب می‌شوند.\n\n⏱ حالا بگو هر چند دقیقه یک پست منتشر شود:",
            reply_markup=auto_interval_keyboard(),
        )
        return
    idx = int(value)
    category = list(AUTO_TOPIC_TREE_FA.keys())[idx]
    await q.message.edit_text(
        f"📂 {category}\n\nحالا شاخه موردنظر را انتخاب کن:",
        reply_markup=auto_subcategory_keyboard(idx)
    )


@subscription_required
async def auto_subcategory_callback(update, context):
    q = update.callback_query
    if not admin_guard(q.from_user.id):
        await q.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await q.answer()
    parts = _safe_cb_parts(q.data)
    if not parts: return
    _, cat_idx, sub_idx = parts
    cat_idx = int(cat_idx)
    category = list(AUTO_TOPIC_TREE_FA.keys())[cat_idx]
    if sub_idx == "random":
        sub = "random"
    else:
        sub = AUTO_TOPIC_TREE_FA[category][int(sub_idx)]
    set_auto_setting("category", category)
    set_auto_setting("subcategory", sub)
    await q.message.edit_text(
        f"✅ موضوع انتخاب شد:\n{category}\n↳ {sub if sub != 'random' else 'همه شاخه‌ها'}\n\n⏱ حالا بگو هر چند دقیقه یک پست منتشر شود:",
        reply_markup=auto_interval_keyboard()
    )


@subscription_required
async def auto_interval_callback(update, context):
    q = update.callback_query
    if not admin_guard(q.from_user.id):
        await q.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await q.answer()
    raw_minutes = q.data.split(":", 1)[1]
    if raw_minutes == "custom":
        context.user_data["auto_wait_interval"] = True
        await q.message.edit_text(
            "✏️ فاصله دلخواه را به دقیقه وارد کن.\nمثال: 45\nحداقل ۵ و حداکثر ۱۴۴۰ دقیقه (۲۴ ساعت)."
        )
        return
    minutes = int(raw_minutes)
    set_auto_setting("interval_minutes", str(minutes))
    set_auto_setting("enabled", "1")
    set_auto_setting("next_run", (datetime.now(TZ) + timedelta(minutes=minutes)).isoformat())
    await q.message.edit_text(
        f"✅ انتشار خودکار روی هر {minutes} دقیقه تنظیم شد و روشن است.",
        reply_markup=auto_channel_keyboard()
    )


def smart_post_category_keyboard():
    rows=[[InlineKeyboardButton("🎲 موضوع تصادفی",callback_data="chgen:random")]]
    for i,category in enumerate(AUTO_TOPIC_TREE_FA):
        rows.append([InlineKeyboardButton(category,callback_data=f"chgen:cat:{i}")])
    rows.append([InlineKeyboardButton("⬅️ مدیریت کانال",callback_data="ch:main")])
    return InlineKeyboardMarkup(rows)


def smart_post_subcategory_keyboard(cat_idx):
    categories=list(AUTO_TOPIC_TREE_FA.keys()); category=categories[cat_idx]
    rows=[[InlineKeyboardButton("🎲 شاخه تصادفی",callback_data=f"chgen:sub:{cat_idx}:random")]]
    for i,sub in enumerate(AUTO_TOPIC_TREE_FA[category]):
        rows.append([InlineKeyboardButton(sub,callback_data=f"chgen:sub:{cat_idx}:{i}")])
    rows.append([InlineKeyboardButton("⬅️ دسته‌ها",callback_data="ch:smart")])
    return InlineKeyboardMarkup(rows)


def smart_post_preview_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 انتشار فوری پست",callback_data="chgen:publish"),InlineKeyboardButton("🔄 ساخت دوباره",callback_data="chgen:regen")],
        [InlineKeyboardButton("⬅️ انتخاب موضوع",callback_data="ch:smart"),InlineKeyboardButton("🏠 مدیریت کانال",callback_data="ch:main")]
    ])


async def smart_post_show_preview(update,context,category,topic):
    q=update.callback_query; uid=q.from_user.id
    channel=get_channel_config()
    if not channel or not channel["channel_id"]:
        await q.message.edit_text("❌ ابتدا کانال را تنظیم کن.",reply_markup=channel_keyboard()); return
    content=generate_unique_auto_post(channel["channel_id"],category,topic)
    context.user_data["smart_post"]={"channel":str(channel["channel_id"]),"category":category,"topic":topic,"content":content}
    await q.message.edit_text(
        f"👁 <b>پیش‌نمایش پست</b>\n\n📂 {html.escape(category)}\n🎯 {html.escape(topic)}\n\n{html.escape(content)}",
        parse_mode="HTML",reply_markup=smart_post_preview_keyboard())


async def smart_post_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔ دسترسی ندارید.",show_alert=True); return
    await q.answer(); parts=q.data.split(":"); action=parts[1]
    if action=="smart":
        context.user_data.pop("smart_post",None)
        await q.message.edit_text("🤖 <b>ساخت پست هوشمند</b>\n\nموضوع را انتخاب کن. پست قبل از انتشار کامل به تو نمایش داده می‌شود:",parse_mode="HTML",reply_markup=smart_post_category_keyboard()); return
    if action=="cat":
        idx=int(parts[2]); categories=list(AUTO_TOPIC_TREE_FA.keys()); category=categories[idx]
        await q.message.edit_text(f"📂 <b>{html.escape(category)}</b>\n\nزیرموضوع را انتخاب کن:",parse_mode="HTML",reply_markup=smart_post_subcategory_keyboard(idx)); return
    if action=="random":
        category,topic=get_auto_topic(); await smart_post_show_preview(update,context,category,topic); return
    if action=="sub":
        cat_idx=int(parts[2]); sub_idx=parts[3]; categories=list(AUTO_TOPIC_TREE_FA.keys()); category=categories[cat_idx]
        topic=random.choice(AUTO_TOPIC_TREE_FA[category]) if sub_idx=="random" else AUTO_TOPIC_TREE_FA[category][int(sub_idx)]
        await smart_post_show_preview(update,context,category,topic); return
    if action=="regen":
        data=context.user_data.get("smart_post")
        if not data: await q.message.edit_text("❌ پیش‌نمایش منقضی شده است.",reply_markup=channel_keyboard()); return
        content=generate_unique_auto_post(data["channel"],data["category"],data["topic"])
        data["content"]=content; context.user_data["smart_post"]=data
        await q.message.edit_text(f"👁 <b>پیش‌نمایش جدید</b>\n\n📂 {html.escape(data['category'])}\n🎯 {html.escape(data['topic'])}\n\n{html.escape(content)}",parse_mode="HTML",reply_markup=smart_post_preview_keyboard()); return
    if action=="publish":
        data=context.user_data.get("smart_post")
        if not data: await q.message.edit_text("❌ پیش‌نمایش منقضی شده است.",reply_markup=channel_keyboard()); return
        try:
            image=await generate_topic_image(data["topic"])
            content=data["content"][:1024]
            if image is not None: await context.bot.send_photo(chat_id=data["channel"],photo=image,caption=content,reply_markup=content_feedback_keyboard(data["topic"]))
            else: await context.bot.send_message(chat_id=data["channel"],text=content,reply_markup=content_feedback_keyboard(data["topic"]))
            save_auto_post_history(data["channel"],data["topic"],data["category"],content)
            context.user_data.pop("smart_post",None)
            await q.message.edit_text("✅ <b>پست با موفقیت فوری منتشر شد.</b>",parse_mode="HTML",reply_markup=channel_keyboard())
        except Exception as e:
            logger.exception("Smart immediate post failed")
            await q.message.edit_text("❌ انتشار ناموفق بود. دسترسی ربات به کانال را بررسی کن.",reply_markup=channel_keyboard())


@subscription_required
async def channel_panel_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    if not admin_guard(uid):
        await q.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await q.answer()
    parts=q.data.split(":")
    action = parts[1] if len(parts)>1 else "main"
    cfg = get_channel_config()
    if action=="history":
        channel_id=str(cfg["channel_id"]) if cfg and cfg["channel_id"] else ""
        c=db()
        rows=c.execute("SELECT topic,category,content,created_at FROM auto_post_history WHERE channel_id=? ORDER BY id DESC LIMIT 30",(channel_id,)).fetchall() if channel_id else []
        c.close()
        if not rows:
            text="🕘 <b>تاریخچه انتشار</b>\n\nهنوز پستی در تاریخچه ثبت نشده است."
        else:
            lines=["🕘 <b>تاریخچه انتشار</b>",""]
            for r in rows:
                stamp=str(r["created_at"]).replace("T"," ")[:16]
                preview=html.escape(str(r["content"]).replace("\n"," ")[:90])
                lines.append(f"📅 <code>{stamp}</code>\n📝 {html.escape(str(r['topic']))}\n{preview}\n")
            text="\n".join(lines)
        await q.message.edit_text(text,parse_mode="HTML",reply_markup=channel_keyboard())
        return
    if action=="batch":
        context.user_data["channel_state"]="batch"
        await q.message.edit_text(
            "🧩 <b>چند پست / زمان‌بندی</b>\n\n"
            "چند خط بفرست؛ هر خط یک پست باشد و زمان را با | جدا کن:\n"
            "<code>14:00 | متن پست اول</code>\n"
            "<code>16:00 | متن پست دوم</code>\n\n"
            "اگر متن طولانی باشد، می‌توانی فقط متن را بفرستی؛ ربات آن را به چند بخش منطقی تقسیم می‌کند.",
            parse_mode="HTML")
        return
    if action=="channels":
        rows=list_managed_channels(); active=str(cfg["channel_id"]) if cfg and cfg["channel_id"] else ""
        kb=[]
        for r in rows:
            mark="✅" if str(r["channel_id"])==active else "⚪"
            kb.append([InlineKeyboardButton(f"{mark} {r['title'] or r['channel_id']}",callback_data=f"ch:select:{r['channel_id']}"),InlineKeyboardButton("🗑",callback_data=f"ch:remove:{r['channel_id']}")])
        kb.append([InlineKeyboardButton("➕ افزودن کانال",callback_data="ch:set"),InlineKeyboardButton("⬅️ برگشت",callback_data="ch:main")])
        await q.message.edit_text("📢 <b>کانال‌های متصل</b>\n\nکانال فعال با ✅ مشخص است. هر کانال تنظیمات و تاریخچه پست خودش را حفظ می‌کند.",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb)); return
    if action=="select" and len(parts)>2:
        set_active_channel(parts[2]); await q.message.edit_text("✅ کانال فعال تغییر کرد.",reply_markup=channel_keyboard()); return
    if action=="remove" and len(parts)>2:
        remove_managed_channel(parts[2]); await q.message.edit_text("🗑 کانال از فهرست مدیریت حذف شد؛ اطلاعات پست‌های قبلی پاک نشد.",reply_markup=channel_keyboard()); return
    channel = cfg["channel_id"] if cfg and cfg["channel_id"] else "تنظیم نشده"

    if action == "main":
        await q.message.edit_text(
            f"📡 <b>مدیریت کانال</b>\n\n📢 کانال: <code>{channel}</code>",
            parse_mode="HTML",
            reply_markup=channel_keyboard(),
        )
    elif action == "set":
        context.user_data["channel_state"] = "set"
        await q.message.edit_text(
            "📡 یوزرنیم کانال را بفرست.\n"
            "مثال: <code>@MyTasks</code>\n"
            "لینک t.me هم پذیرفته می‌شود.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ مدیریت کانال", callback_data="ch:main")]]),
        )
    elif action == "smart":
        context.user_data.pop("smart_post",None)
        await q.message.edit_text("🤖 <b>ساخت پست هوشمند</b>\n\nموضوع را انتخاب کن. بعد از انتخاب، پیش‌نمایش کامل پست نمایش داده می‌شود و خودت انتشار فوری را تأیید می‌کنی.",parse_mode="HTML",reply_markup=smart_post_category_keyboard())
    elif action == "auto":
        await q.message.edit_text(
            "🤖 <b>انتشار خودکار</b>\n\n"
            "پست متنی تمیز + دسته‌بندی و زمان‌بندی قابل تنظیم.\nتصویر خودکار برای انتشار کانال خاموش است.",
            parse_mode="HTML",
            reply_markup=auto_channel_keyboard(),
        )
    elif action == "test":
        if channel == "تنظیم نشده":
            await q.message.edit_text("❌ ابتدا کانال را تنظیم کن.", reply_markup=channel_keyboard())
            return
        try:
            chat = await context.bot.get_chat(channel)
            await q.message.edit_text(
                f"✅ اتصال فعال است.\n📢 {chat.title or channel}\n🆔 <code>{chat.id}</code>",
                parse_mode="HTML",
                reply_markup=channel_keyboard(),
            )
            await hide_main_reply_keyboard(update)
        except Exception as e:
            logger.error("Channel test: %s", e)
            await q.message.edit_text(
                "❌ اتصال ناموفق.\nربات باید Administrator کانال باشد و اجازه ارسال پیام داشته باشد.",
                reply_markup=channel_keyboard(),
            )
    elif action == "new":
        context.user_data["channel_state"] = "content"
        await q.message.edit_text("📝 متن پست را بفرست:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ مدیریت کانال", callback_data="ch:main")]]))
    elif action == "list":
        c = db()
        rows = c.execute(
            "SELECT * FROM channel_posts WHERE enabled=1 ORDER BY id DESC LIMIT 20"
        ).fetchall()
        c.close()
        text_out = "📋 <b>پست‌های فعال</b>\n\n"
        if rows:
            text_out += "\n".join(
                f"#{r['id']} — {channel_schedule_text(r)}\n📝 {r['content'][:60]}"
                for r in rows
            )
        else:
            text_out += "موردی نیست."
        await q.message.edit_text(
            text_out, parse_mode="HTML", reply_markup=channel_keyboard()
        )


@subscription_required
async def channel_schedule_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔ دسترسی ندارید.",show_alert=True); return
    await q.answer(); a=q.data.split(":",1)[1]
    if a=="cancel": context.user_data.clear(); await q.message.edit_text("❌ لغو شد.",reply_markup=channel_keyboard()); return
    if a=="now":
        cfg=get_channel_config()
        if not cfg or not cfg["channel_id"]: await q.message.edit_text("❌ ابتدا کانال را تنظیم کن.",reply_markup=channel_keyboard()); return
        try: await context.bot.send_message(chat_id=cfg["channel_id"],text=_clean_ai_post(context.user_data["channel_content"])); context.user_data.clear(); await q.message.edit_text("✅ پست منتشر شد.",reply_markup=channel_keyboard())
        except Exception as e: logger.error("Immediate channel post: %s",e); await q.message.edit_text("❌ انتشار ناموفق. دسترسی کانال را بررسی کن.",reply_markup=channel_keyboard())
    elif a=="once": context.user_data["channel_state"]="once"; await q.message.edit_text("📅 تاریخ و ساعت را بفرست: ۱۴۰۵/۰۵/۲۹ ۱۸:۳۰")
    elif a=="daily": context.user_data["channel_state"]="daily"; await q.message.edit_text("⏰ ساعت روزانه:",reply_markup=channel_time_keyboard("chd"))
    elif a=="weekly": context.user_data["channel_state"]="wday"; await q.message.edit_text("📆 روز هفته:",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("شنبه",callback_data="chw:5"),InlineKeyboardButton("یکشنبه",callback_data="chw:6")],[InlineKeyboardButton("دوشنبه",callback_data="chw:0"),InlineKeyboardButton("سه‌شنبه",callback_data="chw:1")],[InlineKeyboardButton("چهارشنبه",callback_data="chw:2"),InlineKeyboardButton("پنجشنبه",callback_data="chw:3")],[InlineKeyboardButton("جمعه",callback_data="chw:4")]]))

@subscription_required
async def channel_daily_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔ دسترسی ندارید.",show_alert=True); return
    await q.answer(); v=q.data.split(":",1)[1]
    if v=="custom": context.user_data["channel_state"]="daily_custom"; await q.message.edit_text("🕐 ساعت را بفرست، مثال 18:30"); return
    await save_channel_post(context,uid,"daily",v,None,None,q.message)

@subscription_required
async def channel_weekday_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔ دسترسی ندارید.",show_alert=True); return
    await q.answer(); context.user_data["channel_weekday"]=int(q.data.split(":",1)[1]); context.user_data["channel_state"]="wtime"; await q.message.edit_text("⏰ ساعت هفتگی:",reply_markup=channel_time_keyboard("chwtime"))

@subscription_required
async def channel_weektime_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔ دسترسی ندارید.",show_alert=True); return
    await q.answer(); v=q.data.split(":",1)[1]
    if v=="custom": context.user_data["channel_state"]="wtime_custom"; await q.message.edit_text("🕐 ساعت را بفرست، مثال 18:30"); return
    await save_channel_post(context,uid,"weekly",v,context.user_data["channel_weekday"],None,q.message)

async def save_channel_post(context,uid,typ,tm,weekday,run_at,message):
    cfg=get_channel_config()
    if not cfg or not cfg["channel_id"]: await message.reply_text("❌ ابتدا کانال را تنظیم کن.",reply_markup=channel_keyboard()); return
    pid=add_channel_post(context.user_data["channel_content"],typ,tm,weekday,run_at,uid); context.user_data.clear(); await message.reply_text(f"✅ زمان‌بندی شد. #{pid}",reply_markup=channel_keyboard())

async def channel_text_save(update,context):
    uid=update.effective_user.id
    if not admin_guard(uid): return False
    s=context.user_data.get("channel_state"); text=update.message.text.strip()
    if not s: return False
    if s=="set":
        try:
            normalized = normalize_channel_input(text)
            chat=await context.bot.get_chat(normalized)
            ok, check = await bot_can_manage_channel(context.bot, normalized)
            if not ok:
                await update.message.reply_text(check, reply_markup=channel_keyboard())
                return True
            set_channel_config(normalized, chat.title or normalized)
            context.user_data.pop("channel_state",None)
            await update.message.reply_text(f"✅ کانال وصل شد: {chat.title or normalized}",reply_markup=channel_keyboard())
        except Exception as e:
            logger.error("Set channel: %s", e)
            context.user_data.pop("channel_state", None)
            context.user_data.pop("channel_content", None)
            context.user_data.pop("channel_weekday", None)
            await update.message.reply_text(
                "❌ کانال پیدا نشد یا ربات دسترسی ندارد.\n\n"
                "حالت تنظیم کانال بسته شد. دوباره «تنظیم کانال» را بزن.",
                reply_markup=channel_keyboard(),
            )
        return True
    if s=="batch":
        cfg=get_channel_config()
        if not cfg or not cfg["channel_id"]:
            await update.message.reply_text("❌ ابتدا کانال را تنظیم کن.",reply_markup=channel_keyboard()); return True
        lines=[x.strip() for x in text.splitlines() if x.strip()]
        scheduled=0; immediate=[]
        for line in lines:
            if "|" in line:
                tm,body=line.split("|",1); tm=parse_time(tm.strip()); body=body.strip()
                if tm and body:
                    add_channel_post(body,"daily",tm,None,None,uid); scheduled+=1
                    continue
            immediate.append(line)
        # A long text without explicit times is split into readable chunks.
        if len(lines)==1 and len(text)>700 and not scheduled:
            chunks=[x.strip() for x in re.split(r'\n\s*\n',text) if x.strip()]
            if len(chunks)<2:
                words=text.split(); chunks=[" ".join(words[i:i+90]) for i in range(0,len(words),90)]
            for i,chunk in enumerate(chunks[:12]):
                hh=(datetime.now(TZ)+timedelta(minutes=5*(i+1))).strftime("%H:%M")
                add_channel_post(chunk,"once",None,None,(datetime.now(TZ)+timedelta(minutes=5*(i+1))).isoformat(),uid)
                scheduled+=1
        context.user_data.pop("channel_state",None)
        await update.message.reply_text(f"✅ {scheduled} پست برای انتشار زمان‌بندی شد.",reply_markup=channel_keyboard())
        return True
    if s=="content": context.user_data["channel_content"]=text; context.user_data["channel_state"]="choose"; await update.message.reply_text("📅 زمان انتشار را انتخاب کن:",reply_markup=channel_schedule_keyboard()); return True
    if s=="once":
        try:
            dt=parse_user_datetime(text)
            if dt<=datetime.now(TZ): raise ValueError
            await save_channel_post(context,uid,"once",None,None,dt.isoformat(),update.message)
        except ValueError: await update.message.reply_text("❌ فرمت اشتباه است. نمونه: ۱۴۰۵/۰۶/۰۲ ۱۸:۳۰")
        return True
    if s in ("daily_custom","wtime_custom"):
        v=parse_time(text)
        if not v: await update.message.reply_text("❌ ساعت اشتباه است. مثال 18:30"); return True
        await save_channel_post(context,uid,"daily" if s=="daily_custom" else "weekly",v,None if s=="daily_custom" else context.user_data.get("channel_weekday"),None,update.message); return True
    return False

def admin_is_allowed(uid):
    return uid in ADMIN_IDS


def admin_guard(uid):
    return admin_is_allowed(uid)


def admin_stats():
    c = db()
    users = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    goals = c.execute("SELECT COUNT(*) AS n FROM goals").fetchone()["n"]
    reminders = c.execute(
        "SELECT COUNT(*) AS n FROM goals WHERE enabled=1 AND reminder_time IS NOT NULL"
    ).fetchone()["n"]
    activities = c.execute("SELECT COUNT(*) AS n FROM activity_log").fetchone()["n"]
    achievements = c.execute("SELECT COUNT(*) AS n FROM achievements").fetchone()["n"]
    today = datetime.now(TZ).date().isoformat()
    active_today = c.execute(
        "SELECT COUNT(DISTINCT user_id) AS n FROM activity_log WHERE substr(created_at,1,10)=?",
        (today,),
    ).fetchone()["n"]
    done_today = c.execute(
        """SELECT COUNT(*) AS n FROM goal_days
           WHERE goal_date=? AND status='done'""",
        (today,),
    ).fetchone()["n"]
    new_today = c.execute(
        "SELECT COUNT(*) AS n FROM users WHERE substr(created_at,1,10)=?",
        (today,),
    ).fetchone()["n"]
    appointments_today = c.execute(
        "SELECT COUNT(*) AS n FROM appointments WHERE appointment_date=? AND status='booked'",
        (today,),
    ).fetchone()["n"]
    vip_users = c.execute(
        "SELECT COUNT(*) AS n FROM users WHERE vip_until IS NOT NULL AND vip_until>?",
        (datetime.now(TZ).isoformat(),),
    ).fetchone()["n"]
    open_tickets = c.execute(
        "SELECT COUNT(*) AS n FROM tickets WHERE status='open'"
    ).fetchone()["n"]
    c.close()
    return {
        "users": users,
        "goals": goals,
        "reminders": reminders,
        "activities": activities,
        "achievements": achievements,
        "active_today": active_today,
        "done_today": done_today,
        "new_today": new_today,
        "appointments_today": appointments_today,
        "vip_users": vip_users,
        "open_tickets": open_tickets,
    }


def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 آمار کلی", callback_data="adm:stats"),
            InlineKeyboardButton("👥 کاربران", callback_data="adm:users"),
        ],
        [
            InlineKeyboardButton("🎯 اهداف", callback_data="adm:goals"),
            InlineKeyboardButton("📈 فعالیت‌ها", callback_data="adm:activity"),
        ],
        [
            InlineKeyboardButton("⏰ یادآوری‌ها", callback_data="adm:reminders"),
            InlineKeyboardButton("🏆 دستاوردها", callback_data="adm:achievements"),
        ],
        [
            InlineKeyboardButton("📢 پیام همگانی", callback_data="adm:broadcast"),
        ],
        [InlineKeyboardButton("📢 مدیریت کانال و پست‌گذاری", callback_data="adm:channel")],
        [InlineKeyboardButton("🩺 سلامت ربات", callback_data="adm:health"), InlineKeyboardButton("📋 گزارش روزانه", callback_data="adm:report")],
        [InlineKeyboardButton("⚙️ کنترل قابلیت‌ها", callback_data="adm:features")],
        [InlineKeyboardButton("🏠 منوی اصلی", callback_data="adm:main")],
    ])


@subscription_required
async def admin_panel_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    if not admin_guard(uid):
        await q.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await q.answer()
    action = q.data.split(":", 1)[1]
    s = admin_stats()

    if action == "stats":
        text = (
            "📊 <b>داشبورد مدیریت</b>\n\n"
            f"👥 کاربران: <b>{s['users']}</b>\n"
            f"🆕 کاربران جدید امروز: <b>{s['new_today']}</b>\n"
            f"🟢 فعال امروز: <b>{s['active_today']}</b>\n"
            f"🎯 اهداف: <b>{s['goals']}</b>\n"
            f"✅ انجام‌شده امروز: <b>{s['done_today']}</b>\n"
            f"⏰ یادآوری فعال: <b>{s['reminders']}</b>\n"
            f"👀 کل فعالیت‌ها: <b>{s['activities']}</b>\n"
            f"🏆 دستاوردها: <b>{s['achievements']}</b>"
        )
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=admin_keyboard())

    elif action == "users":
        c = db()
        rows = c.execute(
            """SELECT u.user_id,u.first_name,u.gender,u.created_at,
                      (SELECT COUNT(*) FROM goals g WHERE g.user_id=u.user_id) AS goals
               FROM users u ORDER BY u.created_at DESC LIMIT 20"""
        ).fetchall()
        c.close()
        if not rows:
            text = "👥 کاربری ثبت نشده."
        else:
            text = "👥 <b>آخرین کاربران</b>\n\n"
            for r in rows:
                name = r["first_name"] or "بدون نام"
                text += f"👤 {name} | ID: <code>{r['user_id']}</code> | 🎯 {r['goals']}\n"
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=admin_keyboard())

    elif action == "goals":
        c = db()
        rows = c.execute(
            """SELECT category,COUNT(*) AS n
               FROM goals GROUP BY category ORDER BY n DESC"""
        ).fetchall()
        c.close()
        text = "🎯 <b>اهداف بر اساس دسته</b>\n\n"
        text += "\n".join(f"• {r['category']}: <b>{r['n']}</b>" for r in rows) or "موردی نیست."
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=admin_keyboard())

    elif action == "activity":
        c = db()
        rows = c.execute(
            """SELECT activity,COUNT(*) AS n
               FROM activity_log GROUP BY activity ORDER BY n DESC LIMIT 15"""
        ).fetchall()
        c.close()
        text = "📈 <b>فعالیت‌ها</b>\n\n"
        text += "\n".join(f"• {r['activity']}: <b>{r['n']}</b>" for r in rows) or "موردی نیست."
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=admin_keyboard())

    elif action == "reminders":
        c = db()
        rows = c.execute(
            """SELECT reminder_time,COUNT(*) AS n
               FROM goals
               WHERE enabled=1 AND reminder_time IS NOT NULL
               GROUP BY reminder_time ORDER BY reminder_time"""
        ).fetchall()
        c.close()
        text = "⏰ <b>یادآوری‌ها</b>\n\n"
        text += "\n".join(f"🕐 {r['reminder_time']}: <b>{r['n']}</b>" for r in rows) or "یادآوری فعالی نیست."
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=admin_keyboard())

    elif action == "achievements":
        c = db()
        rows = c.execute(
            """SELECT code,COUNT(*) AS n
               FROM achievements GROUP BY code ORDER BY n DESC"""
        ).fetchall()
        c.close()
        text = "🏆 <b>دستاوردها</b>\n\n"
        text += "\n".join(f"• {r['code']}: <b>{r['n']}</b>" for r in rows) or "دستاوردی ثبت نشده."
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=admin_keyboard())

    elif action == "channel":
        await q.message.edit_text(
            "📢 <b>مدیریت کانال و پست‌گذاری</b>\n\n"
            "اتصال کانال، تست اتصال، ساخت پست، مشاهده پست‌ها و انتشار خودکار.",
            parse_mode="HTML",
            reply_markup=channel_keyboard(),
        )

    elif action == "broadcast":
        context.user_data["admin_broadcast"] = True
        await q.message.edit_text(
            "📢 متن پیام همگانی را ارسال کن.\n\n"
            "⚠️ بعد از ارسال، قبل از فرستادن برای همه تأیید می‌گیریم."
        )


async def admin_command(update, context):
    uid = update.effective_user.id
    if not admin_guard(uid):
        await update.message.reply_text(
            f"⛔ دسترسی به پنل مدیریت ندارید.\n\n🆔 ID شما: {uid}\n\n"
            "این ID را در Railway → Variables در ADMIN_IDS یا ADMIN_ID قرار بده و سرویس را Restart/Redeploy کن."
        )
        return
    log_activity(uid, "admin_open")
    await update.message.reply_text(
        "🛡 <b>پنل مدیریت مرکزی</b>\n\nابتدا بخش موردنظر را انتخاب کن. هر بخش تنظیمات مستقل خودش را دارد:",
        parse_mode="HTML",
        reply_markup=admin_keyboard(),
    )
    await hide_main_reply_keyboard(update)


async def admin(update, context):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await update.message.reply_text(T[lang(uid)]["admin_denied"])
        return

    c = db()
    users = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    goals = c.execute("SELECT COUNT(*) AS n FROM goals").fetchone()["n"]
    activity = c.execute("SELECT COUNT(*) AS n FROM activity_log").fetchone()["n"]
    today = datetime.now(TZ).date().isoformat()
    active_today = c.execute(
        "SELECT COUNT(*) AS n FROM users WHERE substr(last_active_at,1,10)=?",
        (today,),
    ).fetchone()["n"]
    reminders = c.execute(
        "SELECT COUNT(*) AS n FROM goals WHERE enabled=1 AND reminder_time IS NOT NULL"
    ).fetchone()["n"]
    c.close()

    await update.message.reply_text(
        "🛠 پنل مدیریت\n\n"
        f"📊 آمار کلی\n"
        f"👥 تعداد کاربران: {users}\n"
        f"🎯 تعداد اهداف: {goals}\n"
        f"👀 تعداد استفاده و فعالیت‌ها: {activity}\n"
        f"🟢 کاربران فعال امروز: {active_today}\n"
        f"⏰ تعداد یادآوری‌ها: {reminders}\n"
        f"📢 ارسال پیام همگانی: با دکمه زیر",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📢 ارسال پیام همگانی", callback_data="admin:broadcast")
        ]]),
    )
    log_activity(uid, "admin_panel")


@subscription_required
async def admin_broadcast_start(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if uid not in ADMIN_IDS:
        await q.message.edit_text(T[lang(uid)]["admin_denied"])
        return
    context.user_data["admin_broadcast"] = True
    await q.message.edit_text(T[lang(uid)]["broadcast_prompt"])


async def admin_broadcast_save(update, context):
    uid = update.effective_user.id
    if not admin_guard(uid):
        context.user_data.pop("admin_broadcast", None)
        return False
    uid = update.effective_user.id
    if not context.user_data.get("admin_broadcast"):
        return False
    if uid not in ADMIN_IDS:
        context.user_data.clear()
        return True

    text = update.message.text.strip()
    context.user_data.clear()

    c = db()
    try:
        rows = c.execute("SELECT user_id FROM users").fetchall()
    finally:
        c.close()

    sent = 0
    for row in rows:
        try:
            await context.bot.send_message(row["user_id"], f"📢 {text}")
            sent += 1
            if sent % 30 == 0:
                await asyncio.sleep(1)
        except Exception as e:
            logger.warning("Broadcast failed for %s: %s", row["user_id"], e)

    log_activity(uid, "broadcast")
    await update.message.reply_text(
        T[lang(uid)]["broadcast_done"].format(sent=sent)
    )
    return True


async def user_daily_progress_job(context):
    now=datetime.now(TZ)
    if now.hour!=23 or now.minute!=30 or not feature_enabled("night"): return
    today=now.date().isoformat(); yesterday=(now.date()-timedelta(days=1)).isoformat(); c=db(); users=c.execute("SELECT user_id FROM users WHERE COALESCE(blocked,0)=0 AND user_id IN (SELECT DISTINCT user_id FROM goals)").fetchall()
    for row in users:
        uid=row["user_id"]
        if not delivery_once(f"user_daily_progress:{uid}:{today}",uid,"user_daily_progress"): continue
        try:
            t=c.execute("SELECT COUNT(*) total,SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) done FROM goal_days WHERE user_id=? AND goal_date=?",(uid,today)).fetchone(); y=c.execute("SELECT COUNT(*) total,SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) done FROM goal_days WHERE user_id=? AND goal_date=?",(uid,yesterday)).fetchone()
            tt=int(t["total"] or 0); td=int(t["done"] or 0); yt=int(y["total"] or 0); yd=int(y["done"] or 0); tp=round(td*100/tt) if tt else 0; yp=round(yd*100/yt) if yt else 0; diff=tp-yp
            trend="📈 پیشرفت" if diff>0 else "📉 پسرفت" if diff<0 else "➡️ بدون تغییر"
            sign="+" if diff>0 else ""
            xp=xp_info(uid)[0]
            text=f"🌙 <b>شب بخیر {html.escape(display_name(uid))}!</b>\n\nگزارش روزانه تو 🌙\n\n🎯 امروز: {td}/{tt} هدف انجام شد ({tp}٪)\n📅 دیروز: {yd}/{yt} هدف انجام شد ({yp}٪)\n\n{trend}: {sign}{diff}٪ نسبت به دیروز\n⭐ XP فعلی: {xp}\n\nفردا یک قدم بهتر شروع می‌کنیم. 💪"
            await context.bot.send_message(uid,text,parse_mode="HTML",reply_markup=keyboard(uid))
        except Exception: logger.exception("User daily progress failed for %s",uid)
    c.close()

async def morning_job(context):
    now = datetime.now(TZ)
    if now.hour != 7 or now.minute != 0 or not feature_enabled("morning"):
        return
    c = db()
    users = c.execute(
        "SELECT user_id FROM users WHERE COALESCE(blocked,0)=0"
    ).fetchall()
    c.close()
    for row in users:
        uid = row["user_id"]
        try:
            await context.bot.send_message(
                uid,
                T[lang(uid)]["morning"].format(name=display_name(uid)),
                reply_markup=keyboard(uid),
            )
            log_activity(uid, "morning_message")
        except Exception as e:
            logger.error("Morning message error: %s", e)



def delivery_once(delivery_key, user_id, delivery_type):
    """Atomically claim a notification key. Returns True only once."""
    c = db()
    try:
        cur = c.execute(
            "INSERT OR IGNORE INTO delivery_log(delivery_key,user_id,delivery_type,created_at) VALUES(?,?,?,?)",
            (delivery_key, user_id, delivery_type, datetime.now(TZ).isoformat()),
        )
        c.commit()
        return cur.rowcount == 1
    finally:
        c.close()


def reward_once(reward_key, user_id, reward_type, amount=0):
    """Atomically claim a reward key. Returns True only once."""
    c = db()
    try:
        cur = c.execute(
            "INSERT OR IGNORE INTO reward_log(reward_key,user_id,reward_type,amount,created_at) VALUES(?,?,?,?,?)",
            (reward_key, user_id, reward_type, amount, datetime.now(TZ).isoformat()),
        )
        c.commit()
        return cur.rowcount == 1
    finally:
        c.close()


def cleanup_delivery_log(days=90):
    cutoff = (datetime.now(TZ) - timedelta(days=days)).isoformat()
    c = db()
    try:
        c.execute("DELETE FROM delivery_log WHERE created_at < ?", (cutoff,))
        c.commit()
    finally:
        c.close()


async def reminder_job(context):
    now = datetime.now(TZ)
    hhmm = now.strftime("%H:%M")
    c = db()
    today=now.date().isoformat()
    goals = c.execute(
        """SELECT g.* FROM goals g JOIN users u ON u.user_id=g.user_id
           WHERE g.enabled=1 AND COALESCE(u.blocked,0)=0
             AND (g.reminder_time=? OR EXISTS(SELECT 1 FROM goal_reminder_overrides o WHERE o.user_id=g.user_id AND o.goal_id=g.id AND o.reminder_date=? AND o.reminder_time=?))""",
        (hhmm,today,hhmm),
    ).fetchall()
    c.close()

    for g in goals:
        sc=db(); rr=sc.execute("SELECT reminders_enabled FROM user_settings WHERE user_id=?",(g["user_id"],)).fetchone(); sc.close()
        if rr and not rr["reminders_enabled"]:
            continue
        if get_status(g["user_id"], g["id"]) == "done":
            continue
        uid = g["user_id"]
        try:
            await context.bot.send_message(
                uid,
                T[lang(uid)]["reminder"].format(
                    name=display_name(uid),
                    goal=g["name"],
                ),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "✅ Done" if lang(uid) == "en" else "✅ انجام دادم",
                        callback_data=f"done:{g['id']}",
                    ),
                    InlineKeyboardButton(
                        "❌ Not done" if lang(uid) == "en" else "❌ انجام ندادم",
                        callback_data=f"miss:{g['id']}",
                    ),
                ], [
                    InlineKeyboardButton(
                        "⏰ Tomorrow / فردا",
                        callback_data=f"goalrem:{g['id']}:menu",
                    ),
                    InlineKeyboardButton(
                        "⏱ Snooze" if lang(uid) == "en" else "⏱ یادآوری بعداً",
                        callback_data=f"snooze_menu:{g['id']}",
                    ),
                ]]),
            )
            c=db(); c.execute("DELETE FROM goal_reminder_overrides WHERE user_id=? AND goal_id=? AND reminder_date=? AND reminder_time=?",(uid,g["id"],today,hhmm)); c.commit(); c.close()
            log_activity(uid, "reminder_sent")
        except Exception as e:
            logger.error("Reminder error: %s", e)



def is_menu_button(uid, text):
    """Return True when text is a normal UI button, not input for a flow."""
    known = {
        "⬅️ برگشت", "⬅️ Back", "🏠 منوی اصلی", "🏠 Main Menu",
        "📢 مدیریت کانال", "📢 Channel Management",
        "🛡 پنل مدیریت", "🛡 Admin Panel",
        "📊 آمار من", "📊 My Stats",
        "🎯 اهداف من", "🎯 My Goals",
        "➕ افزودن هدف", "➕ Add Goal",
        "📅 برنامه امروز", "📅 Today's Plan",
        "⏰ یادآوری‌ها", "⏰ Reminders",
        "🏆 دستاوردها", "🏆 Achievements",
        "🤖 چت با AI", "🤖 AI Chat",
        "💎 VIP و امکانات پولی", "💎 VIP & Paid Features",
        "⚙️ تنظیمات", "⚙️ Settings",
        "📈 قیمت آنلاین", "📈 Online Prices",
        "🤝 دعوت دوستان", "🤝 Invite Friends",
        "🎫 پشتیبانی", "🎫 Support",
        "📅 رزروهای من", "📅 My Bookings",
    }
    if text in known:
        return True
    try:
        menu = T[lang(uid)]["menu"]
        return any(text == item for row in menu for item in row)
    except Exception:
        return False



def feature_access_mode(key, uid=None):
    try:
        c=db()
        if uid is not None:
            r=c.execute("SELECT mode FROM user_feature_overrides WHERE user_id=? AND feature_key=?",(int(uid),key)).fetchone()
            if r and r["mode"] in ("free","vip","off"):
                c.close(); return r["mode"]
        r=c.execute("SELECT mode FROM feature_access WHERE key=?",(key,)).fetchone()
        c.close(); return r["mode"] if r and r["mode"] in ("free","vip","off") else "free"
    except Exception:
        return "free"

def main_menu_button(uid):
    return InlineKeyboardButton("🏠 منوی اصلی" if lang(uid)=="fa" else "🏠 Main Menu", callback_data="nav:main")

def back_button(callback_data, label_fa="⬅️ برگشت", label_en="⬅️ Back", uid=None):
    return InlineKeyboardButton(label_en if uid is not None and lang(uid)=="en" else label_fa, callback_data=callback_data)

def gregorian_to_jalali(gy,gm,gd):
    gdim=(31,29 if gy%4==0 and (gy%100!=0 or gy%400==0) else 28,31,30,31,30,31,31,30,31,30,31); jdim=(31,31,31,31,31,31,30,30,30,30,30,29)
    gy2=gy-1600; gm2=gm-1; gd2=gd-1; gdn=365*gy2+(gy2+3)//4-(gy2+99)//100+(gy2+399)//400+sum(gdim[:gm2])+gd2
    jdn=gdn-79; jnp=jdn//12053; jdn%=12053; jy=979+33*jnp+4*(jdn//1461); jdn%=1461
    if jdn>=366: jy+=(jdn-1)//365; jdn=(jdn-1)%365
    jm=0
    while jm<11 and jdn>=jdim[jm]: jdn-=jdim[jm]; jm+=1
    return jy,jm+1,jdn+1

def jalali_to_gregorian(jy,jm,jd):
    jy=int(jy); jm=int(jm); jd=int(jd)
    jy2=jy-979
    jdn=365*jy2 + (jy2//33)*8 + ((jy2%33)+3)//4 + (0 if jm<=6 else 6*(jm-1)+2) + (jm-1)*31 + (jd-1)
    gdn=jdn+79
    gy=1600+400*(gdn//146097); gdn%=146097
    leap=True
    if gdn>=36525:
        gdn-=1; gy+=100*(gdn//36524); gdn%=36524
        if gdn>=365: gdn+=1
        else: leap=False
    gy+=4*(gdn//1461); gdn%=1461
    if gdn>=366:
        leap=False; gdn-=1; gy+=gdn//365; gdn%=365
    gm=0; gdim=[31,29 if leap else 28,31,30,31,30,31,31,30,31,30,31]
    while gm<12 and gdn>=gdim[gm]:
        gdn-=gdim[gm]; gm+=1
    return gy,gm+1,gdn+1

def jalali_date_str(value):
    try:
        d=value if hasattr(value,'year') else datetime.fromisoformat(str(value)[:10]).date(); y,m,day=gregorian_to_jalali(d.year,d.month,d.day); return f"{y:04d}/{m:02d}/{day:02d}"
    except Exception: return str(value)

def fa_digits(value):
    return str(value).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))

JALALI_MONTHS_FA = [
    "فروردین","اردیبهشت","خرداد","تیر","مرداد","شهریور",
    "مهر","آبان","آذر","دی","بهمن","اسفند"
]
WEEKDAYS_FA = [
    "دوشنبه","سه‌شنبه","چهارشنبه","پنجشنبه","جمعه","شنبه","یکشنبه"
]

def jalali_pretty_date(value):
    try:
        d=value if hasattr(value,'year') else datetime.fromisoformat(str(value)[:10]).date()
        y,m,day=gregorian_to_jalali(d.year,d.month,d.day)
        return f"{WEEKDAYS_FA[d.weekday()]} {fa_digits(day)} {JALALI_MONTHS_FA[m-1]} {fa_digits(y)}"
    except Exception:
        return jalali_date_str(value)

def parse_user_date(value):
    """Parse a user date in Jalali (preferred) or Gregorian format and store ISO Gregorian."""
    raw=normalize_digits(str(value).strip()).replace("-","/")
    parts=raw.split("/")
    if len(parts)==3:
        try:
            y,m,d=map(int,parts)
            if 1300 <= y <= 1600:
                gy,gm,gd=jalali_to_gregorian(y,m,d)
                return f"{gy:04d}-{gm:02d}-{gd:02d}"
            return datetime(y,m,d).date().isoformat()
        except Exception:
            pass
    raise ValueError("invalid date")

def parse_user_datetime(value):
    """Parse Jalali or Gregorian date-time. Output remains Gregorian ISO for database compatibility."""
    raw=normalize_digits(str(value).strip()).replace("/","-")
    parts=raw.rsplit(" ",1)
    if len(parts)==2:
        date_part,time_part=parts
    else:
        raise ValueError("invalid datetime")
    iso_date=parse_user_date(date_part)
    tm=parse_time(time_part)
    if not tm:
        raise ValueError("invalid time")
    return datetime.fromisoformat(f"{iso_date}T{tm}").replace(tzinfo=TZ)

def fa_datetime(value, with_seconds=False):
    try:
        if isinstance(value, str):
            dt=datetime.fromisoformat(value.replace("Z","+00:00"))
        else:
            dt=value
        if dt.tzinfo is not None:
            dt=dt.astimezone(TZ)
        date_part=jalali_pretty_date(dt.date())
        clock=dt.strftime("%H:%M:%S" if with_seconds else "%H:%M")
        return f"{date_part}، ساعت {fa_digits(clock)}"
    except Exception:
        return str(value)

def fa_date_iso(value):
    return jalali_pretty_date(value)


# ================= CUSTOMER / APPOINTMENT MODULE =================
CUSTOMER_REMINDER_OPTIONS=[1,5,10,30,60,120,1440]
BUSINESS_TYPES_FA=["💇 آرایشگر / سالن","🎨 تتو آرتیست","🔧 تعمیرکار","🩺 خدمات پزشکی","💆 زیبایی / ماساژ","🏋️ مربی","📚 مدرس / مشاور","📸 عکاس","🛠️ خدمات تخصصی","✏️ سایر"]
BUSINESS_TYPES_EN=["💇 Barber / Salon","🎨 Tattoo Artist","🔧 Repairer","🩺 Medical Services","💆 Beauty / Massage","🏋️ Coach","📚 Teacher / Consultant","📸 Photographer","🛠️ Professional Services","✏️ Other"]

def customer_feature_allowed(uid):
    mode=feature_access_mode("customers",uid)
    return feature_enabled("customers") and mode!="off" and (mode!="vip" or is_vip(uid) or uid in ADMIN_IDS)

def ensure_business_profile(uid):
    now=datetime.now(TZ).isoformat(); token=secrets.token_urlsafe(32)
    c=db(); c.execute("INSERT OR IGNORE INTO business_profiles(user_id,business_type,business_name,contact_phone,contact_telegram,contact_instagram,booking_enabled,booking_token,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(uid,"","","","","",1,token,now,now))
    for wd in range(7): c.execute("INSERT OR IGNORE INTO working_hours(owner_user_id,weekday,start_time,end_time,enabled) VALUES(?,?,?,?,?)",(uid,wd,"09:00","20:00",1))
    c.commit(); r=c.execute("SELECT * FROM business_profiles WHERE user_id=?",(uid,)).fetchone(); c.close(); return r

def customer_option_allowed(uid, key):
    if admin_is_allowed(uid):
        return True
    try:
        if not feature_enabled(key):
            return False
        mode = feature_access_mode(key, uid)
        return mode != "off" and (mode != "vip" or is_vip(uid))
    except Exception:
        return True


def customer_keyboard(uid):
    fa=lang(uid)=="fa"
    rows=[]
    if customer_option_allowed(uid,"customer_today") or customer_option_allowed(uid,"customer_new_appointment"):
        row=[]
        if customer_option_allowed(uid,"customer_today"):
            row.append(InlineKeyboardButton("📅 نوبت‌های امروز" if fa else "📅 Today's Appointments",callback_data="cust:today"))
        if customer_option_allowed(uid,"customer_new_appointment"):
            row.append(InlineKeyboardButton("➕ نوبت جدید" if fa else "➕ New Appointment",callback_data="cust:new"))
        if row: rows.append(row)
    if customer_option_allowed(uid,"customer_customers") or customer_option_allowed(uid,"customer_calendar"):
        row=[]
        if customer_option_allowed(uid,"customer_customers"):
            row.append(InlineKeyboardButton("👥 مشتریان" if fa else "👥 Customers",callback_data="cust:list"))
        if customer_option_allowed(uid,"customer_calendar"):
            row.append(InlineKeyboardButton("🗓️ تقویم کاری" if fa else "🗓️ Calendar",callback_data="cust:calendar"))
        if row: rows.append(row)
    if customer_option_allowed(uid,"customer_hours") or customer_option_allowed(uid,"customer_reminders"):
        row=[]
        if customer_option_allowed(uid,"customer_hours"):
            row.append(InlineKeyboardButton("⏰ ساعات کاری" if fa else "⏰ Working Hours",callback_data="cust:hours"))
        if customer_option_allowed(uid,"customer_reminders"):
            row.append(InlineKeyboardButton("🔔 یادآوری‌ها" if fa else "🔔 Reminders",callback_data="cust:reminders"))
        if row: rows.append(row)
    if customer_option_allowed(uid,"customer_analytics") or customer_option_allowed(uid,"customer_loyal"):
        row=[]
        if customer_option_allowed(uid,"customer_analytics"):
            row.append(InlineKeyboardButton("📊 آمار مشتریان" if fa else "📊 Customer Analytics",callback_data="cust:analytics"))
        if customer_option_allowed(uid,"customer_loyal"):
            row.append(InlineKeyboardButton("🏆 مشتریان وفادار" if fa else "🏆 Loyal Customers",callback_data="cust:loyal"))
        if row: rows.append(row)
    if customer_option_allowed(uid,"customer_period"):
        rows.append([InlineKeyboardButton("📆 هفتگی/ماهانه/سالانه" if fa else "📆 Weekly/Monthly/Yearly",callback_data="cust:period")])
    if customer_option_allowed(uid,"customer_booking_link"):
        rows.append([InlineKeyboardButton("🔗 لینک رزرو آنلاین" if fa else "🔗 Online Booking Link",callback_data="cust:link")])
    if customer_option_allowed(uid,"customer_business_settings"):
        rows.append([InlineKeyboardButton("⚙️ تنظیمات کسب‌وکار" if fa else "⚙️ Business Settings",callback_data="cust:settings")])
    if customer_option_allowed(uid,"customer_customers"):
        rows.append([InlineKeyboardButton("📨 پیام به مشتری‌ها" if fa else "📨 Message Customers",callback_data="cust:broadcast")])
    rows.append([InlineKeyboardButton("🏠 منوی اصلی" if fa else "🏠 Main Menu",callback_data="nav:main")])
    return InlineKeyboardMarkup(rows)

def customer_back(uid,cb="cust:main"): return InlineKeyboardMarkup([[back_button(cb,uid=uid),main_menu_button(uid)]])

def appointment_reminder_keyboard(uid,aid):
    fa=lang(uid)=="fa"
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ انجام شد" if fa else "✅ Done",callback_data=f"cust:done:{aid}"),InlineKeyboardButton("❌ لغو شد" if fa else "❌ Cancelled",callback_data=f"cust:cancel:{aid}")],[InlineKeyboardButton("🔄 جابه‌جایی" if fa else "🔄 Reschedule",callback_data=f"cust:reschedule:{aid}"),InlineKeyboardButton("🏠 مشتریان" if fa else "🏠 Customers",callback_data="cust:main")]])

def get_customer(owner,cid):
    c=db(); r=c.execute("SELECT * FROM customers WHERE id=? AND owner_user_id=?",(cid,owner)).fetchone(); c.close(); return r

def get_appointment(owner,aid):
    c=db(); r=c.execute("SELECT a.*,c.name,c.phone,c.telegram_username,c.telegram_user_id FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.id=? AND a.owner_user_id=?",(aid,owner)).fetchone(); c.close(); return r

def parse_reminder_list(v):
    out=[]
    for x in str(v or "").split(","):
        try:
            n=int(x.strip())
            if n in CUSTOMER_REMINDER_OPTIONS and n not in out: out.append(n)
        except Exception: pass
    return sorted(out,reverse=True)

def reminder_label(n,fa=True):
    if n==1440:return "۱ روز قبل" if fa else "1 day before"
    if n>=60:return f"{n//60} ساعت قبل" if fa else f"{n//60} hour(s) before"
    return f"{n} دقیقه قبل" if fa else f"{n} min before"

def working_hours_for(uid,wd):
    c=db(); r=c.execute("SELECT * FROM working_hours WHERE owner_user_id=? AND weekday=?",(uid,wd)).fetchone(); c.close(); return r

def is_holiday(uid,d):
    c=db(); r=c.execute("SELECT note FROM business_holidays WHERE owner_user_id=? AND holiday_date=?",(uid,d)).fetchone(); c.close(); return r

def _mins(t): h,m=map(int,t.split(":")); return h*60+m

def has_conflict(owner,d,tm,duration=30,exclude=None):
    start=_mins(tm); end=start+int(duration or 30); c=db(); sql="SELECT appointment_time,duration_minutes FROM appointments WHERE owner_user_id=? AND appointment_date=? AND status='booked'"; params=[owner,d]
    if exclude: sql+=" AND id!=?"; params.append(exclude)
    rows=c.execute(sql,params).fetchall(); c.close()
    return any(start < _mins(r["appointment_time"])+int(r["duration_minutes"] or 30) and _mins(r["appointment_time"]) < end for r in rows)

def available_slots(owner,d,step=30):
    if is_holiday(owner,d):return []
    wd=datetime.fromisoformat(d).date().weekday(); wh=working_hours_for(owner,wd)
    if not wh or not wh["enabled"]:return []
    cur=_mins(wh["start_time"]); end=_mins(wh["end_time"]); out=[]
    while cur+step<=end:
        tm=f"{cur//60:02d}:{cur%60:02d}"
        if not has_conflict(owner,d,tm,step):out.append(tm)
        cur+=step
    return out

def loyalty_score(visits,cancelled=0): return min(100,(min(visits,15)*5)+(min(max(0,visits-cancelled),10)*3)) if visits else 0

def customer_feature_message(uid):
    return "💎 بخش مشتری و نوبت‌دهی برای پلن VIP فعال است." if lang(uid)=="fa" else "💎 Customers & Appointments are available on VIP plans."

async def customer_panel(update,context):
    uid=update.effective_user.id
    if not customer_feature_allowed(uid): await update.message.reply_text(customer_feature_message(uid)); return
    ensure_business_profile(uid); await update.message.reply_text("👥 <b>مدیریت مشتری و نوبت‌دهی</b>\n\nپنل مستقل مشتریان، نوبت‌ها، تقویم و یادآوری‌ها.",parse_mode="HTML",reply_markup=customer_keyboard(uid));
    await hide_main_reply_keyboard(update)

async def customer_panel_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    p=q.data.split(":"); a=p[1] if len(p)>1 else "main"
    # Public booking callbacks are independent of the owner's customer-panel
    # access mode, so a normal customer can book without a VIP account.
    public_actions={"bookdate","booklink","slot","mybookings","mybook","cancelbook","reschedulebook"}
    await q.answer()
    if a not in public_actions and not customer_feature_allowed(uid):
        await q.message.edit_text(customer_feature_message(uid)); return
    ensure_business_profile(uid)
    customer_action_keys={
        "today":"customer_today", "new":"customer_new_appointment", "list":"customer_customers",
        "calendar":"customer_calendar", "hours":"customer_hours", "reminders":"customer_reminders",
        "analytics":"customer_analytics", "loyal":"customer_loyal", "period":"customer_period",
        "link":"customer_booking_link", "settings":"customer_business_settings", "broadcast":"customer_customers"
    }
    required_key=customer_action_keys.get(a)
    if required_key and not customer_option_allowed(uid,required_key):
        await q.message.edit_text("⛔ این گزینه توسط مدیر غیرفعال شده یا برای پلن شما فعال نیست.",reply_markup=customer_back(uid))
        return
    if a=="main": await q.message.edit_text("👥 مدیریت مشتری و نوبت‌دهی",reply_markup=customer_keyboard(uid)); return
    if a=="today": await customer_today(update,context); return
    if a=="new": context.user_data["customer_mode"]="new_name"; await q.message.edit_text("➕ نام مشتری را بفرست:"); return
    if a=="list": await customer_list_view(update,context); return
    if a=="calendar": await customer_calendar(update,context); return
    if a=="hours": await customer_hours(update,context); return
    if a=="hours_edit": context.user_data.update(customer_mode="hours_edit",weekday=int(p[2])); await q.message.edit_text("⏰ ساعت شروع و پایان را بفرست. مثال: 09:00-20:00\nبرای تعطیل: off"); return
    if a=="reminders": await customer_reminders(update,context); return
    if a=="analytics": await customer_analytics_view(update,context); return
    if a=="period": await customer_period_menu(update,context); return
    if a=="periodreport": await customer_period_report(update,context,p[2]); return
    if a=="loyal": await customer_loyal(update,context); return
    if a=="link": await customer_booking_link(update,context); return
    if a=="settings": await customer_settings(update,context); return
    if a=="broadcast": context.user_data["customer_broadcast_mode"]="all"; await q.message.reply_text("📨 پیام را بفرست. برای لغو ⬅️ برگشت را بزن.",reply_markup=nav_keyboard(uid)); return
    if a=="contact": context.user_data["customer_mode"]="contact"; await q.message.edit_text("📱 از قابلیت ارسال Contact تلگرام استفاده کن و مخاطب را برای ربات بفرست.\n⚠️ ربات به دفترچه مخاطبین خصوصی گوشی دسترسی مستقیم ندارد."); return
    if a=="bizname": context.user_data["customer_mode"]="bizname"; await q.message.edit_text("🏪 نام کسب‌وکار را بفرست یا - برای حذف نام:"); return
    if a=="contacts": context.user_data["customer_mode"]="contact_phone"; context.user_data["business_contact_pending"]={}; await q.message.edit_text("📞 شماره تماس را بفرست یا - بزن. (اختیاری)"); return
    if a=="type":
        types=BUSINESS_TYPES_FA if lang(uid)=="fa" else BUSINESS_TYPES_EN; idx=int(p[2]); c=db(); c.execute("UPDATE business_profiles SET business_type=?,updated_at=? WHERE user_id=?",(types[idx],datetime.now(TZ).isoformat(),uid)); c.commit(); c.close(); await q.message.edit_text("✅ نوع فعالیت ذخیره شد.",reply_markup=customer_keyboard(uid)); return
    if a=="done": await appointment_status(update,context,"done",int(p[2])); return
    if a=="cancel": await appointment_status(update,context,"cancelled",int(p[2])); return
    if a=="reschedule": context.user_data.update(appointment_id=int(p[2]),customer_mode="reschedule_date"); await q.message.edit_text("📅 تاریخ جدید را بفرست. مثال: ۱۴۰۵/۰۵/۲۹"); return
    if a=="cust": await customer_detail(update,context,int(p[2])); return
    if a=="edit": context.user_data.update(customer_mode="edit_name",customer_id=int(p[2])); await q.message.edit_text("✏️ نام جدید مشتری را بفرست:"); return
    if a=="delete":
        c=db(); c.execute("UPDATE customers SET status='inactive',updated_at=? WHERE id=? AND owner_user_id=?",(datetime.now(TZ).isoformat(),int(p[2]),uid)); c.commit(); c.close(); await q.message.edit_text("🗑 مشتری از لیست فعال خارج شد؛ سابقه و فاکتور/نوبت‌های قبلی حذف نشد.",reply_markup=customer_keyboard(uid)); return
    if a=="appt":
        context.user_data.update(customer_id=int(p[2]),customer_mode="appt_date")
        await q.message.edit_text("📅 تاریخ نوبت را بفرست: ۱۴۰۵/۰۵/۲۹")
        return
    if a=="mybookings":
        await customer_my_bookings_callback(update,context); return
    if a=="mybook":
        await customer_booking_detail(update,context,int(p[2])); return
    if a=="cancelbook":
        await customer_cancel_booking(update,context,int(p[2])); return
    if a=="reschedulebook":
        await customer_reschedule_booking(update,context,int(p[2])); return
    if a=="bookdate": await booking_date_menu(update,context,p[2]); return
    if a=="bookmonth":
        await booking_month_menu(update,context,p[2]); return
    if a=="calmonth":
        await customer_calendar_month(update,context,p[2]); return
    if a=="booklink":
        if len(p)>2:
            owner=int(p[2]); prof=ensure_business_profile(owner); context.user_data["booking_owner"]=owner
        await booking_date_menu_list(update,context); return
    if a=="slot": await booking_slot_select(update,context,p[2]); return
    if a=="day": await customer_day(update,context,p[2]); return
    if a=="holiday": await holiday_toggle(update,context,p[2]); return

def customer_list_rows(uid):
    c=db(); rows=c.execute("SELECT c.*,COUNT(CASE WHEN a.status='done' THEN 1 END) visits FROM customers c LEFT JOIN appointments a ON a.customer_id=c.id WHERE c.owner_user_id=? AND c.status='active' GROUP BY c.id ORDER BY c.name",(uid,)).fetchall(); c.close(); return rows

async def customer_list_view(update,context):
    q=update.callback_query; uid=q.from_user.id; rows=customer_list_rows(uid); kb=[[InlineKeyboardButton(f"👤 {r['name']} — {r['visits']} مراجعه",callback_data=f"cust:cust:{r['id']}")] for r in rows[:40]]; kb.append([InlineKeyboardButton("➕ افزودن دستی",callback_data="cust:new"),InlineKeyboardButton("📱 افزودن از Contact",callback_data="cust:contact")]); kb.append([back_button("cust:main",uid=uid)])
    text="👥 <b>لیست مشتری‌ها</b>\n\n"+ ("\n".join(f"• {r['name']} — {r['visits']} مراجعه" for r in rows) if rows else "مشتری‌ای ثبت نشده.")
    await q.message.edit_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def customer_detail(update,context,cid):
    q=update.callback_query; uid=q.from_user.id; r=get_customer(uid,cid)
    if not r:return
    c=db(); visits=c.execute("SELECT COUNT(*) n FROM appointments WHERE customer_id=? AND status='done'",(cid,)).fetchone()["n"]; canc=c.execute("SELECT COUNT(*) n FROM appointments WHERE customer_id=? AND status='cancelled'",(cid,)).fetchone()["n"]; hist=c.execute("SELECT * FROM appointments WHERE customer_id=? ORDER BY appointment_date DESC,appointment_time DESC LIMIT 10",(cid,)).fetchall(); c.close(); score=loyalty_score(visits,canc); status="💎 مشتری وفادار" if score>=70 else "⭐ مشتری فعال" if score>=40 else "🆕 مشتری جدید"
    text=f"👤 <b>{html.escape(r['name'])}</b>\n📞 {html.escape(r['phone']) if r['phone'] else '—'}\n🔗 @{html.escape(r['telegram_username']) if r['telegram_username'] else '—'}\n\n{status}\n⭐ امتیاز وفاداری: {score}/100\n📅 کل مراجعه: {visits}\n❌ لغو: {canc}\n\n📋 سابقه:\n"+"\n".join(f"• {jalali_pretty_date(a['appointment_date'])} | ⏰ {a['appointment_time']} — {a['status']}" for a in hist)
    await q.message.edit_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ نوبت جدید",callback_data=f"cust:appt:{cid}")],[InlineKeyboardButton("✏️ ویرایش مشتری",callback_data=f"cust:edit:{cid}"),InlineKeyboardButton("🗑 حذف مشتری",callback_data=f"cust:delete:{cid}")],[back_button("cust:list",uid=uid),main_menu_button(uid)]]))

async def appointment_detail(update,context,aid):
    q=update.callback_query; uid=q.from_user.id; r=get_appointment(uid,aid)
    if not r:return
    await q.message.edit_text(f"📅 <b>{jalali_pretty_date(r['appointment_date'])} | ⏰ {r['appointment_time']}</b>\n👤 {html.escape(r['name'])}\n📞 {html.escape(r['phone']) if r['phone'] else '—'}\n🛠️ {html.escape(r['service'] or '—')}\n📝 {html.escape(r['notes'] or '—')}\n🔔 {', '.join(reminder_label(x,lang(uid)=='fa') for x in parse_reminder_list(r['reminder_minutes'])) or 'بدون یادآوری'}",parse_mode="HTML",reply_markup=appointment_reminder_keyboard(uid,aid))

async def customer_today(update,context):
    q=update.callback_query; uid=q.from_user.id; d=datetime.now(TZ).date().isoformat(); c=db(); rows=c.execute("SELECT a.*,c.name,c.phone FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.owner_user_id=? AND a.appointment_date=? ORDER BY a.appointment_time",(uid,d)).fetchall(); c.close(); lines=["🌅 <b>نوبت‌های امروز</b>",""]
    for r in rows: lines.append(f"🕐 <b>{r['appointment_time']}</b> — 👤 {html.escape(r['name'])}"+(f" — 📞 {html.escape(r['phone'])}" if r['phone'] else "")+f" — {'🟢' if r['status']=='booked' else '✅' if r['status']=='done' else '❌'}")
    lines.append(f"\n👥 مجموع: {len(rows)}")
    await q.message.edit_text("\n".join(lines),parse_mode="HTML",reply_markup=customer_back(uid))

def _jalali_months_buttons(prefix, years=2):
    gy,gm,gd=gregorian_to_jalali(*datetime.now(TZ).date().timetuple()[:3])
    rows=[]
    for y in range(gy,gy+years):
        for m in range(1,13):
            rows.append([InlineKeyboardButton(f"🗓️ {JALALI_MONTHS_FA[m-1]} {fa_digits(y)}",
                                               callback_data=f"{prefix}:{y:04d}-{m:02d}")])
    return rows


async def customer_calendar(update,context):
    q=update.callback_query; uid=q.from_user.id
    kb=_jalali_months_buttons("cust:calmonth")
    kb.append([back_button("cust:main",uid=uid)])
    await q.message.edit_text(
        "🗓️ <b>تقویم کاری و نوبت‌ها</b>\n\nماه موردنظر را انتخاب کن.\nتمام ماه‌های ۲ سال آینده در دسترس است.",
        parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def customer_calendar_month(update,context,ym):
    q=update.callback_query; uid=q.from_user.id
    try: jy,jm=map(int,ym.split("-")); gy,gm,gd=jalali_to_gregorian(jy,jm,1)
    except Exception:
        await q.answer("تاریخ نامعتبر است.",show_alert=True); return
    import calendar as _cal
    today=datetime.now(TZ).date()
    # Number of days in a Jalali month.
    days=31 if jm<=6 else 30 if jm<=11 else 30 if jalali_to_gregorian(jy+1,1,1)[0] else 29
    kb=[]; c=db()
    for day in range(1,days+1):
        try: gy,gm,gd=jalali_to_gregorian(jy,jm,day); d=datetime(gy,gm,gd,tzinfo=TZ).date()
        except Exception: continue
        iso=d.isoformat()
        n=c.execute("SELECT COUNT(*) n FROM appointments WHERE owner_user_id=? AND appointment_date=? AND status='booked'",(uid,iso)).fetchone()["n"]
        h=c.execute("SELECT 1 FROM business_holidays WHERE owner_user_id=? AND holiday_date=?",(uid,iso)).fetchone()
        mark="🔴" if h else "🟢"
        kb.append([InlineKeyboardButton(f"{mark} {fa_digits(day)} — {fa_digits(n)} نوبت",callback_data=f"cust:day:{iso}")])
    c.close()
    kb.append([InlineKeyboardButton("⬅️ ماه‌ها",callback_data="cust:calendar")])
    await q.message.edit_text(f"🗓️ <b>{JALALI_MONTHS_FA[jm-1]} {fa_digits(jy)}</b>\nروز را انتخاب کن:",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def customer_day(update,context,d):
    q=update.callback_query; uid=q.from_user.id; c=db(); rows=c.execute("SELECT a.*,c.name,c.phone FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.owner_user_id=? AND a.appointment_date=? ORDER BY a.appointment_time",(uid,d)).fetchall(); h=c.execute("SELECT note FROM business_holidays WHERE owner_user_id=? AND holiday_date=?",(uid,d)).fetchone(); c.close(); text=f"📅 <b>{jalali_pretty_date(d)}</b>\n{'🚫 تعطیل' if h else '🟢 روز کاری'}\n\n"+ ("\n".join(f"🕐 {r['appointment_time']} — {html.escape(r['name'])}" + (f" — 📞 {html.escape(r['phone'])}" if r['phone'] else "") for r in rows) or "بدون نوبت"); kb=[[InlineKeyboardButton("🚫 باز/تعطیل",callback_data=f"cust:holiday:{d}")],[back_button("cust:calendar",uid=uid)]]; await q.message.edit_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def holiday_toggle(update,context,d):
    q=update.callback_query; uid=q.from_user.id; c=db(); r=c.execute("SELECT id FROM business_holidays WHERE owner_user_id=? AND holiday_date=?",(uid,d)).fetchone()
    if r:c.execute("DELETE FROM business_holidays WHERE id=?",(r["id"],)); msg="🟢 روز باز شد."
    else:c.execute("INSERT INTO business_holidays(owner_user_id,holiday_date,note) VALUES(?,?,?)",(uid,d,"تعطیلی توسط کاربر")); msg="🔴 روز تعطیل شد."
    c.commit(); c.close(); await q.message.edit_text(msg,reply_markup=customer_back(uid,"cust:calendar"))

async def customer_hours(update,context):
    q=update.callback_query; uid=q.from_user.id; c=db(); rows=c.execute("SELECT * FROM working_hours WHERE owner_user_id=? ORDER BY weekday",(uid,)).fetchall(); c.close(); nf=["دوشنبه","سه‌شنبه","چهارشنبه","پنجشنبه","جمعه","شنبه","یکشنبه"]; ne=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]; kb=[[InlineKeyboardButton(f"{'🟢' if r['enabled'] else '🔴'} {(nf if lang(uid)=='fa' else ne)[r['weekday']]} {r['start_time']}-{r['end_time']}",callback_data=f"cust:hours_edit:{r['weekday']}")] for r in rows]; kb.append([back_button("cust:main",uid=uid)]); await q.message.edit_text("⏰ <b>ساعات کاری</b>\nروی روز بزن و زمان را تغییر بده.",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def customer_reminders(update,context):
    q=update.callback_query; uid=q.from_user.id; c=db(); rows=c.execute("SELECT a.appointment_date,a.appointment_time,a.reminder_minutes,c.name FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.owner_user_id=? AND a.status='booked' AND a.appointment_date>=? ORDER BY a.appointment_date,a.appointment_time LIMIT 50",(uid,datetime.now(TZ).date().isoformat())).fetchall(); c.close(); text="🔔 <b>یادآوری‌های نوبت</b>\n\n"+ ("\n".join(f"{jalali_pretty_date(r['appointment_date'])} — ⏰ {fa_digits(r['appointment_time'])} — {html.escape(r['name'])} — {', '.join(reminder_label(x,lang(uid)=='fa') for x in parse_reminder_list(r['reminder_minutes']))}" for r in rows) or "یادآوری‌ای نیست."); await q.message.edit_text(text,parse_mode="HTML",reply_markup=customer_back(uid))

async def customer_period_menu(update,context):
    q=update.callback_query; uid=q.from_user.id; kb=[[InlineKeyboardButton("📅 هفتگی",callback_data="cust:periodreport:7"),InlineKeyboardButton("📅 ماهانه",callback_data="cust:periodreport:30")],[InlineKeyboardButton("📅 سالانه",callback_data="cust:periodreport:365")],[back_button("cust:main",uid=uid)]]; await q.message.edit_text("📊 دوره گزارش مشتری را انتخاب کن:",reply_markup=InlineKeyboardMarkup(kb))

async def customer_period_report(update,context,days):
    q=update.callback_query; uid=q.from_user.id; days=int(days); since=(datetime.now(TZ).date()-timedelta(days=days-1)).isoformat(); c=db(); total=c.execute("SELECT COUNT(*) n FROM appointments WHERE owner_user_id=? AND status='done' AND appointment_date>=?",(uid,since)).fetchone()["n"]; unique=c.execute("SELECT COUNT(DISTINCT customer_id) n FROM appointments WHERE owner_user_id=? AND status='done' AND appointment_date>=?",(uid,since)).fetchone()["n"]; rows=c.execute("SELECT c.name,COUNT(*) n FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.owner_user_id=? AND a.status='done' AND a.appointment_date>=? GROUP BY a.customer_id ORDER BY n DESC LIMIT 10",(uid,since)).fetchall(); c.close(); title="هفتگی" if days==7 else "ماهانه" if days==30 else "سالانه"; text=f"📊 <b>گزارش {title} مشتریان</b>\n\n👥 مشتری یکتا: {unique}\n✅ نوبت انجام‌شده: {total}\n\n🏆 پرتکرارترین‌ها:\n"+("\n".join(f"• {r['name']} — {r['n']} مراجعه" for r in rows) or "موردی نیست"); await q.message.edit_text(text,parse_mode="HTML",reply_markup=customer_back(uid,"cust:period"))

async def customer_analytics_view(update,context):
    q=update.callback_query; uid=q.from_user.id; c=db(); total=c.execute("SELECT COUNT(*) n FROM appointments WHERE owner_user_id=? AND status='done' AND appointment_date>=?",(uid,(datetime.now(TZ).date()-timedelta(days=29)).isoformat())).fetchone()["n"]; unique=c.execute("SELECT COUNT(DISTINCT customer_id) n FROM appointments WHERE owner_user_id=? AND status='done' AND appointment_date>=?",(uid,(datetime.now(TZ).date()-timedelta(days=29)).isoformat())).fetchone()["n"]; alltime=c.execute("SELECT COUNT(*) n FROM appointments WHERE owner_user_id=? AND status='done'",(uid,)).fetchone()["n"]; c.close(); await q.message.edit_text(f"📊 <b>تحلیل مشتریان</b>\n\n📅 ۳۰ روز اخیر: {total} نوبت\n👥 مشتری یکتا: {unique}\n📈 کل مراجعه انجام‌شده: {alltime}\n\nگزارش ماهانه/سالانه از همین سابقه قابل محاسبه است.",parse_mode="HTML",reply_markup=customer_back(uid))

async def customer_loyal(update,context):
    q=update.callback_query; uid=q.from_user.id; c=db(); rows=c.execute("SELECT c.id,c.name,COUNT(CASE WHEN a.status='done' THEN 1 END) visits,COUNT(CASE WHEN a.status='cancelled' THEN 1 END) canc FROM customers c LEFT JOIN appointments a ON a.customer_id=c.id WHERE c.owner_user_id=? GROUP BY c.id ORDER BY visits DESC LIMIT 30",(uid,)).fetchall(); c.close(); text="🏆 <b>مشتریان وفادار</b>\n\n"+("\n".join(f"{'🥇' if i==0 else '🥈' if i==1 else '🥉' if i==2 else '⭐'} {r['name']} — {r['visits']} مراجعه — امتیاز {loyalty_score(r['visits'],r['canc'])}/100" for i,r in enumerate(rows)) or "مشتری‌ای نیست."); await q.message.edit_text(text,parse_mode="HTML",reply_markup=customer_back(uid))

async def customer_booking_link(update,context):
    q=update.callback_query; uid=q.from_user.id; p=ensure_business_profile(uid); me=await context.bot.get_me(); link=f"https://t.me/{me.username}?start=book_{p['booking_token']}" if me.username else "—"
    contacts=[]
    if p["contact_phone"]: contacts.append(f"📞 {html.escape(p['contact_phone'])}")
    if p["contact_telegram"]: contacts.append(f"💬 تلگرام: @{html.escape(p['contact_telegram'].lstrip('@'))}")
    if p["contact_instagram"]: contacts.append(f"📸 اینستاگرام: @{html.escape(p['contact_instagram'].lstrip('@'))}")
    title=html.escape(p["business_name"] or p["business_type"] or "کسب‌وکار")
    text=f"🔗 <b>لینک رزرو آنلاین</b>\n\n🏪 {title}\n\n<code>{link}</code>\n\nمشتری از این لینک زمان‌های آزاد را می‌بیند و نوبت ثبت می‌کند."
    if contacts: text += "\n\n"+"\n".join(contacts)
    await q.message.edit_text(text,parse_mode="HTML",reply_markup=customer_back(uid))

async def customer_settings(update,context):
    q=update.callback_query; uid=q.from_user.id; p=ensure_business_profile(uid); types=BUSINESS_TYPES_FA if lang(uid)=="fa" else BUSINESS_TYPES_EN
    kb=[[InlineKeyboardButton(x,callback_data=f"cust:type:{i}")] for i,x in enumerate(types)]
    kb += [[InlineKeyboardButton("🏪 نام کسب‌وکار",callback_data="cust:bizname"),InlineKeyboardButton("📞 اطلاعات تماس",callback_data="cust:contacts")],[InlineKeyboardButton("📱 افزودن از Contact",callback_data="cust:contact")],[back_button("cust:main",uid=uid)]]
    contacts=[]
    if p["contact_phone"]: contacts.append(f"📞 {html.escape(p['contact_phone'])}")
    if p["contact_telegram"]: contacts.append(f"💬 @{html.escape(p['contact_telegram'].lstrip('@'))}")
    if p["contact_instagram"]: contacts.append(f"📸 @{html.escape(p['contact_instagram'].lstrip('@'))}")
    text=f"⚙️ <b>تنظیمات کسب‌وکار</b>\n\n🏪 نام: {html.escape(p['business_name'] or 'ثبت نشده')}\nنوع فعلی: {html.escape(p['business_type'] or 'انتخاب نشده')}\n\n"+"\n".join(contacts or ["📭 اطلاعات تماس ثبت نشده است."])
    await q.message.edit_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def appointment_status(update,context,status,aid):
    q=update.callback_query; uid=q.from_user.id; r=get_appointment(uid,aid)
    if not r:return
    now=datetime.now(TZ).isoformat()
    c=db(); c.execute("UPDATE appointments SET status=?,updated_at=? WHERE id=? AND owner_user_id=?",(status,now,aid,uid)); c.execute("INSERT INTO customer_events(owner_user_id,customer_id,appointment_id,event_type,details,created_at) VALUES(?,?,?,?,?,?)",(uid,r['customer_id'],aid,status,"",now)); c.commit(); c.close()
    if r["telegram_user_id"]:
        try:
            msg = (f"❌ <b>نوبت شما توسط ارائه‌دهنده لغو شد.</b>\n\n📅 {jalali_pretty_date(r['appointment_date'])}\n⏰ {r['appointment_time']}") if status=="cancelled" else (f"✅ <b>نوبت شما انجام‌شده ثبت شد.</b>\n\n📅 {jalali_pretty_date(r['appointment_date'])}\n⏰ {r['appointment_time']}")
            await context.bot.send_message(r["telegram_user_id"],msg,parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📅 رزروهای من",callback_data="cust:mybookings")],[InlineKeyboardButton("🏠 منوی اصلی",callback_data="nav:main")]]))
        except Exception: logger.warning("Customer status notification failed")
    await q.message.edit_text("✅ نوبت انجام شد و در سابقه مشتری ثبت شد." if status=="done" else "❌ نوبت لغو شد و سابقه حفظ شد.",reply_markup=customer_back(uid))

async def customer_text_save(update,context):
    uid=update.effective_user.id; mode=context.user_data.get("customer_mode"); text=update.message.text.strip()
    if not mode:return False
    if mode=="bizname":
        value="" if text=="-" else text[:100]; c=db(); c.execute("UPDATE business_profiles SET business_name=?,updated_at=? WHERE user_id=?",(value,datetime.now(TZ).isoformat(),uid)); c.commit(); c.close(); context.user_data.pop("customer_mode",None); await update.message.reply_text("✅ نام کسب‌وکار ذخیره شد.",reply_markup=customer_keyboard(uid)); return True
    if mode=="contact_phone":
        context.user_data["business_contact_pending"]["phone"]="" if text=="-" else text[:50]; context.user_data["customer_mode"]="contact_telegram"; await update.message.reply_text("💬 آیدی تلگرام را بفرست یا - بزن. (اختیاری)"); return True
    if mode=="contact_telegram":
        context.user_data["business_contact_pending"]["telegram"]="" if text=="-" else text.lstrip("@").strip()[:100]; context.user_data["customer_mode"]="contact_instagram"; await update.message.reply_text("📸 آیدی اینستاگرام را بفرست یا - بزن. (اختیاری)"); return True
    if mode=="contact_instagram":
        pend=context.user_data.pop("business_contact_pending",{}); value="" if text=="-" else text.lstrip("@").strip()[:100]; c=db(); c.execute("UPDATE business_profiles SET contact_phone=?,contact_telegram=?,contact_instagram=?,updated_at=? WHERE user_id=?",(pend.get("phone",""),pend.get("telegram",""),value,datetime.now(TZ).isoformat(),uid)); c.commit(); c.close(); context.user_data.pop("customer_mode",None); await update.message.reply_text("✅ اطلاعات تماس ذخیره شد. موارد خالی نمایش داده نمی‌شوند.",reply_markup=customer_keyboard(uid)); return True
    if mode=="hours_edit":
        wd=context.user_data.get("weekday"); val=normalize_digits(text)
        if val.lower() in ("off","تعطیل"):
            c=db(); c.execute("UPDATE working_hours SET enabled=0 WHERE owner_user_id=? AND weekday=?",(uid,wd)); c.commit(); c.close(); context.user_data.pop("customer_mode",None); context.user_data.pop("weekday",None); await update.message.reply_text("🚫 روز تعطیل شد.",reply_markup=customer_keyboard(uid)); return True
        m=re.fullmatch(r"(\d{1,2}:\d{2})[-–](\d{1,2}:\d{2})",val)
        if not m or not parse_time(m.group(1)) or not parse_time(m.group(2)): await update.message.reply_text("❌ فرمت نادرست. مثال: 09:00-20:00"); return True
        c=db(); c.execute("UPDATE working_hours SET start_time=?,end_time=?,enabled=1 WHERE owner_user_id=? AND weekday=?",(parse_time(m.group(1)),parse_time(m.group(2)),uid,wd)); c.commit(); c.close(); context.user_data.pop("customer_mode",None); context.user_data.pop("weekday",None); await update.message.reply_text("✅ ساعات کاری ذخیره شد.",reply_markup=customer_keyboard(uid)); return True
    if mode=="edit_name": context.user_data["customer_mode"]="edit_phone"; await update.message.reply_text("📞 شماره جدید را بفرست یا - برای بدون تغییر:"); context.user_data["customer_pending"]={"name":text}; return True
    if mode=="edit_phone":
        cid=context.user_data.get("customer_id"); phone=text if text!="-" else None; p=context.user_data.pop("customer_pending",{}); c=db();
        if phone is None: c.execute("UPDATE customers SET name=?,updated_at=? WHERE id=? AND owner_user_id=?",(p.get("name",""),datetime.now(TZ).isoformat(),cid,uid))
        else: c.execute("UPDATE customers SET name=?,phone=?,updated_at=? WHERE id=? AND owner_user_id=?",(p.get("name",""),phone,datetime.now(TZ).isoformat(),cid,uid))
        c.commit(); c.close(); context.user_data.pop("customer_id",None); context.user_data.pop("customer_mode",None); await update.message.reply_text("✅ اطلاعات مشتری ویرایش شد.",reply_markup=customer_keyboard(uid)); return True
    if mode=="new_name": context.user_data["customer_pending"]={"name":text}; context.user_data["customer_mode"]="new_phone"; await update.message.reply_text("📞 شماره مشتری را بفرست یا - بزن:"); return True
    if mode=="new_phone": context.user_data["customer_pending"]["phone"]="" if text=="-" else text; context.user_data["customer_mode"]="new_notes"; await update.message.reply_text("📝 توضیحات اختیاری را بفرست یا - بزن:"); return True
    if mode=="new_notes":
        p=context.user_data.pop("customer_pending",{}); p["notes"]="" if text=="-" else text; now=datetime.now(TZ).isoformat(); c=db(); cid=c.execute("INSERT INTO customers(owner_user_id,name,phone,notes,created_at,updated_at) VALUES(?,?,?,?,?,?)",(uid,p["name"],p.get("phone"),p.get("notes"),now,now)).lastrowid; c.commit(); c.close(); context.user_data.update(customer_id=cid,customer_mode="appt_date"); await update.message.reply_text("✅ مشتری ثبت شد.\n📅 تاریخ نوبت را بفرست: ۱۴۰۵/۰۵/۲۹"); return True
    if mode=="appt_date":
        try:d=datetime.fromisoformat(text).date().isoformat()
        except Exception: await update.message.reply_text("❌ تاریخ نامعتبر است."); return True
        slots=available_slots(uid,d)
        if not slots: await update.message.reply_text("⚠️ این روز تعطیل است یا زمان خالی ندارد."); return True
        context.user_data.update(booking_date=d,customer_mode="appt_time"); await update.message.reply_text("⏰ زمان آزاد را بفرست:\n"+" | ".join(slots[:50])); return True
    if mode=="appt_time":
        tm=parse_time(text); d=context.user_data.get("booking_date")
        if not tm or tm not in available_slots(uid,d): await update.message.reply_text("❌ این ساعت آزاد نیست."); return True
        context.user_data.update(booking_time=tm,customer_mode="appt_service"); await update.message.reply_text("🛠️ نوع خدمت را بفرست یا - بزن:"); return True
    if mode=="appt_service": context.user_data["customer_pending"]={"service":"" if text=="-" else text}; context.user_data["customer_mode"]="appt_rem"; await update.message.reply_text("🔔 یادآوری‌ها را با دقیقه و کاما بنویس: 1440,120,30\nگزینه‌ها: 1،5،10،30،60،120،1440"); return True
    if mode=="appt_rem":
        vals=parse_reminder_list(text); p=context.user_data.pop("customer_pending",{}); now=datetime.now(TZ).isoformat(); c=db(); aid=c.execute("INSERT INTO appointments(owner_user_id,customer_id,appointment_date,appointment_time,duration_minutes,service,notes,reminder_minutes,status,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(uid,context.user_data["customer_id"],context.user_data["booking_date"],context.user_data["booking_time"],30,p.get("service",""),"",",".join(map(str,vals or [30])),"booked","manual",now,now)).lastrowid; c.execute("INSERT INTO customer_events(owner_user_id,customer_id,appointment_id,event_type,details,created_at) VALUES(?,?,?,?,?,?)",(uid,context.user_data["customer_id"],aid,"booked","manual",now)); c.commit(); c.close(); context.user_data.clear(); await update.message.reply_text("✅ نوبت ثبت شد.",reply_markup=customer_keyboard(uid)); return True
    if mode=="reschedule_date":
        try:d=datetime.fromisoformat(text).date().isoformat()
        except Exception: await update.message.reply_text("❌ تاریخ نامعتبر است."); return True
        context.user_data.update(booking_date=d,customer_mode="reschedule_time"); await update.message.reply_text("⏰ ساعت جدید را بفرست:"); return True
    if mode=="reschedule_time":
        aid=context.user_data["appointment_id"]; tm=parse_time(text); d=context.user_data["booking_date"]
        if not tm or not d or tm not in available_slots(uid,d,30) or has_conflict(uid,d,tm,30,aid):
            await update.message.reply_text("❌ این زمان خارج از ساعات کاری است یا آزاد نیست. یکی از زمان‌های نمایش‌داده‌شده را انتخاب کن."); return True
        now=datetime.now(TZ).isoformat()
        c=db()
        try:
            c.execute("BEGIN IMMEDIATE")
            r=c.execute("SELECT a.*,c.name,c.telegram_user_id FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.id=? AND a.owner_user_id=?",(aid,uid)).fetchone()
            if not r:
                c.rollback(); c.close(); context.user_data.clear(); await update.message.reply_text("❌ نوبت پیدا نشد.",reply_markup=customer_keyboard(uid)); return True
            rows=c.execute("SELECT appointment_time,duration_minutes FROM appointments WHERE owner_user_id=? AND appointment_date=? AND status='booked' AND id!=?",(uid,d,aid)).fetchall()
            start=_mins(tm); end=start+int(r['duration_minutes'] or 30)
            if any(start < _mins(x['appointment_time'])+int(x['duration_minutes'] or 30) and _mins(x['appointment_time']) < end for x in rows):
                c.rollback(); c.close(); await update.message.reply_text("❌ این زمان دیگر آزاد نیست.",reply_markup=customer_keyboard(uid)); return True
            old_date,old_time=r["appointment_date"],r["appointment_time"]
            c.execute("UPDATE appointments SET appointment_date=?,appointment_time=?,updated_at=? WHERE id=? AND owner_user_id=?",(d,tm,now,aid,uid))
            c.execute("INSERT INTO customer_events(owner_user_id,customer_id,appointment_id,event_type,details,created_at) VALUES(?,?,?,?,?,?)",(uid,r["customer_id"],aid,"owner_rescheduled",f"{old_date} {old_time} -> {d} {tm}",now)); c.commit(); c.close()
        except Exception:
            try: c.rollback(); c.close()
            except Exception: pass
            raise
        if r["telegram_user_id"]:
            try:
                await context.bot.send_message(r["telegram_user_id"],f"🔄 <b>زمان نوبت شما تغییر کرد.</b>\n\n📅 قبلی: {jalali_pretty_date(old_date)}\n⏰ {old_time}\n📅 جدید: {jalali_pretty_date(d)}\n⏰ {tm}",parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📅 رزروهای من",callback_data="cust:mybookings")]]))
            except Exception: logger.warning("Customer owner-reschedule notification failed")
        context.user_data.clear(); await update.message.reply_text("🔄 نوبت جابه‌جا شد و مشتری هم مطلع شد.",reply_markup=customer_keyboard(uid)); return True
    return False

async def customer_contact_save(update,context):
    if context.user_data.get("customer_mode")!="contact":return False
    uid=update.effective_user.id; ct=update.message.contact; name=((ct.first_name or "")+" "+(ct.last_name or "")).strip() or "مشتری"; now=datetime.now(TZ).isoformat(); c=db(); c.execute("INSERT INTO customers(owner_user_id,name,phone,telegram_user_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",(uid,name,ct.phone_number,ct.user_id,now,now)); c.commit(); c.close(); context.user_data.pop("customer_mode",None); await update.message.reply_text(f"✅ {name} به مشتریان اضافه شد.",reply_markup=customer_keyboard(uid)); return True


async def customer_my_bookings(update,context):
    """Show active online bookings belonging to the current Telegram user."""
    uid=update.effective_user.id
    c=db()
    rows=c.execute("""
        SELECT a.id,a.owner_user_id,a.appointment_date,a.appointment_time,
               a.status,a.source,c.name,c.phone
        FROM appointments a
        JOIN customers c ON c.id=a.customer_id
        WHERE c.telegram_user_id=? AND a.status='booked'
          AND a.appointment_date>=?
        ORDER BY a.appointment_date,a.appointment_time
        LIMIT 30
    """,(uid,datetime.now(TZ).date().isoformat())).fetchall()
    c.close()
    if not rows:
        await update.message.reply_text(
            "📅 <b>رزروهای من</b>\n\nرزرو فعال و آینده‌ای برای شما ثبت نشده است.",
            parse_mode="HTML", reply_markup=keyboard(uid)
        )
        return
    kb=[]
    lines=[]
    for r in rows:
        date_text=jalali_pretty_date(r["appointment_date"])
        lines.append(f"• {date_text} — ⏰ {r['appointment_time']}")
        kb.append([InlineKeyboardButton(
            f"📅 {date_text} | ⏰ {r['appointment_time']}",
            callback_data=f"cust:mybook:{r['id']}"
        )])
    kb.append([InlineKeyboardButton("🏠 منوی اصلی",callback_data="nav:main")])
    await update.message.reply_text(
        "📅 <b>رزروهای من</b>\n\n"+"\n".join(lines),
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb)
    )

async def customer_my_bookings_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    c=db()
    rows=c.execute("""
        SELECT a.id,a.appointment_date,a.appointment_time
        FROM appointments a JOIN customers c ON c.id=a.customer_id
        WHERE c.telegram_user_id=? AND a.status='booked' AND a.appointment_date>=?
        ORDER BY a.appointment_date,a.appointment_time LIMIT 30
    """,(uid,datetime.now(TZ).date().isoformat())).fetchall()
    c.close()
    if not rows:
        await q.message.edit_text(
            "📅 <b>رزروهای من</b>\n\nرزرو فعال و آینده‌ای ندارید.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 منوی اصلی",callback_data="nav:main")]])
        ); return
    kb=[[InlineKeyboardButton(
        f"📅 {jalali_pretty_date(r['appointment_date'])} | ⏰ {r['appointment_time']}",
        callback_data=f"cust:mybook:{r['id']}"
    )] for r in rows]
    kb.append([InlineKeyboardButton("🏠 منوی اصلی",callback_data="nav:main")])
    await q.message.edit_text("📅 <b>رزروهای من</b>\n\nیک رزرو را انتخاب کن:",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def customer_booking_detail(update,context,aid):
    q=update.callback_query; uid=q.from_user.id
    c=db()
    r=c.execute("""
        SELECT a.*,c.name,c.phone,c.telegram_user_id
        FROM appointments a JOIN customers c ON c.id=a.customer_id
        WHERE a.id=? AND c.telegram_user_id=? AND a.status='booked'
    """,(aid,uid)).fetchone()
    c.close()
    if not r:
        await q.answer("این رزرو فعال پیدا نشد.",show_alert=True); return
    p=ensure_business_profile(r["owner_user_id"])
    bname=p["business_name"] or p["business_type"] or "کسب‌وکار"
    kb=[
        [InlineKeyboardButton("🔄 تغییر زمان",callback_data=f"cust:reschedulebook:{aid}"),
         InlineKeyboardButton("❌ لغو رزرو",callback_data=f"cust:cancelbook:{aid}")],
        [InlineKeyboardButton("⬅️ رزروهای من",callback_data="cust:mybookings"),
         InlineKeyboardButton("🏠 منوی اصلی",callback_data="nav:main")]
    ]
    await q.message.edit_text(
        f"📅 <b>جزئیات رزرو</b>\n\n"
        f"🏪 {html.escape(bname)}\n"
        f"👤 {html.escape(r['name'] or 'مشتری')}\n"
        f"📅 {jalali_pretty_date(r['appointment_date'])}\n"
        f"⏰ {r['appointment_time']}\n"
        f"📞 {html.escape(r['phone']) if r['phone'] else '—'}\n\n"
        "از اینجا می‌توانید زمان را تغییر دهید یا رزرو را لغو کنید.",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb)
    )

async def customer_cancel_booking(update,context,aid):
    q=update.callback_query; uid=q.from_user.id
    c=db()
    r=c.execute("""
        SELECT a.*,c.name,c.phone,c.telegram_user_id
        FROM appointments a JOIN customers c ON c.id=a.customer_id
        WHERE a.id=? AND c.telegram_user_id=? AND a.status='booked'
    """,(aid,uid)).fetchone()
    if not r:
        c.close(); await q.answer("این رزرو دیگر فعال نیست.",show_alert=True); return
    now=datetime.now(TZ).isoformat()
    c.execute("UPDATE appointments SET status='cancelled',updated_at=? WHERE id=? AND status='booked'",(now,aid))
    c.execute(
        "INSERT INTO customer_events(owner_user_id,customer_id,appointment_id,event_type,details,created_at) VALUES(?,?,?,?,?,?)",
        (r["owner_user_id"],r["customer_id"],aid,"customer_cancelled","customer cancelled online",now)
    )
    c.commit(); c.close()
    try:
        await context.bot.send_message(
            r["owner_user_id"],
            f"❌ <b>مشتری رزرو آنلاین را لغو کرد</b>\n\n"
            f"👤 {html.escape(r['name'] or 'مشتری')}\n"
            f"📅 {jalali_pretty_date(r['appointment_date'])}\n"
            f"⏰ {r['appointment_time']}\n"
            f"📞 {html.escape(r['phone']) if r['phone'] else '—'}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning("Owner cancellation notification failed: %s",e)
    await q.answer("✅ رزرو لغو شد.")
    await q.message.edit_text(
        "✅ <b>رزرو شما لغو شد.</b>\n\nصاحب کسب‌وکار نیز از لغو رزرو مطلع شد.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 رزروهای من",callback_data="cust:mybookings")],
            [InlineKeyboardButton("🏠 منوی اصلی",callback_data="nav:main")]
        ])
    )

async def customer_reschedule_booking(update,context,aid):
    q=update.callback_query; uid=q.from_user.id
    c=db()
    r=c.execute("""
        SELECT a.*,c.telegram_user_id
        FROM appointments a JOIN customers c ON c.id=a.customer_id
        WHERE a.id=? AND c.telegram_user_id=? AND a.status='booked'
    """,(aid,uid)).fetchone()
    c.close()
    if not r:
        await q.answer("این رزرو دیگر فعال نیست.",show_alert=True); return
    context.user_data.clear()
    context.user_data.update(
        booking_owner=r["owner_user_id"],
        reschedule_appointment_id=aid,
        customer_mode="public_reschedule"
    )
    await q.answer()
    await booking_date_menu_list(update,context,reschedule=True)

async def customer_booking_start(update,context,token):
    uid=update.effective_user.id
    owner_row=None
    c=db(); p=c.execute("SELECT * FROM business_profiles WHERE booking_token=? AND booking_enabled=1",(token,)).fetchone(); c.close()
    if p:
        owner_row=p["user_id"]
    if not p:
        await update.message.reply_text("❌ لینک رزرو معتبر نیست یا غیرفعال شده."); return True
    if not feature_enabled("customer_online_booking") or feature_access_mode("customer_online_booking", owner_row)=="off":
        await update.message.reply_text("❌ رزرو آنلاین این کسب‌وکار فعلاً غیرفعال است."); return True
    context.user_data["booking_owner"]=p["user_id"]
    await booking_date_menu_list(update,context)
    return True

async def booking_date_menu_list(update,context,reschedule=False):
    kb=_jalali_months_buttons("cust:bookmonth")
    kb.append([InlineKeyboardButton("⬅️ رزرو من" if reschedule else "⬅️ برگشت",
                                    callback_data="cust:mybookings" if reschedule else "nav:main")])
    text=("🔄 <b>تغییر زمان رزرو</b>\n\nماه موردنظر را انتخاب کن."
          if reschedule else "📅 <b>تقویم رزرو آنلاین</b>\n\nماه موردنظر را انتخاب کن.")
    target=update.callback_query.message if update.callback_query else update.message
    await target.edit_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb)) if update.callback_query else await target.reply_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def booking_month_menu(update,context,ym):
    owner=context.user_data.get("booking_owner")
    if not owner:
        await update.callback_query.answer("صاحب کسب‌وکار مشخص نیست.",show_alert=True); return
    try: jy,jm=map(int,ym.split("-"))
    except Exception:
        await update.callback_query.answer("تاریخ نامعتبر است.",show_alert=True); return
    today=datetime.now(TZ).date(); kb=[]
    days=31 if jm<=6 else 30
    if jm==12:
        days=30 if jalali_to_gregorian(jy+1,1,1)[0] else 29
    for day in range(1,days+1):
        try: gy,gm,gd=jalali_to_gregorian(jy,jm,day); d=datetime(gy,gm,gd,tzinfo=TZ).date()
        except Exception: continue
        if d<today: continue
        slots=available_slots(owner,d.isoformat())
        status=f"🟢 {fa_digits(len(slots))} زمان آزاد" if slots else "🔴 تکمیل"
        kb.append([InlineKeyboardButton(f"📅 {fa_digits(day)} — {status}",callback_data=f"cust:bookdate:{d.isoformat()}")])
    kb.append([InlineKeyboardButton("⬅️ ماه‌ها",callback_data="cust:booklink")])
    await update.callback_query.message.edit_text(f"📅 <b>{JALALI_MONTHS_FA[jm-1]} {fa_digits(jy)}</b>\nروز را انتخاب کن:",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def booking_date_menu(update,context,d):
    context.user_data["booking_date"]=d
    await booking_slots_for_owner(update,context,context.user_data.get("booking_owner"),d)

async def booking_slots_for_owner(update,context,owner,d):
    q=update.callback_query
    slots=available_slots(owner,d) if owner else []
    kb=[[InlineKeyboardButton(x,callback_data=f"cust:slot:{x}") for x in slots[i:i+4]] for i in range(0,len(slots),4)]
    kb.append([InlineKeyboardButton("↩️ تاریخ دیگر",callback_data="cust:booklink")])
    kb.append([InlineKeyboardButton("🏠 منوی اصلی",callback_data="nav:main")])
    title="🔄 ساعت جدید را انتخاب کن:" if context.user_data.get("reschedule_appointment_id") else "⏰ زمان آزاد را انتخاب کن:"
    await q.message.edit_text(
        f"📅 {jalali_pretty_date(d)}\n\n{title if slots else '❌ این روز زمان آزادی ندارد.'}",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb)
    )

async def booking_slot_select(update,context,tm):
    q=update.callback_query
    owner=context.user_data.get("booking_owner")
    d=context.user_data.get("booking_date")
    tm = parse_time(tm)
    if not owner or not d or not tm or tm not in available_slots(owner,d,30):
        await q.answer("این زمان دیگر آزاد نیست یا خارج از ساعات کاری است.",show_alert=True); return
    aid=context.user_data.get("reschedule_appointment_id")
    if aid:
        if has_conflict(owner,d,tm,30,aid):
            await q.answer("این زمان دیگر آزاد نیست.",show_alert=True); return
        now=datetime.now(TZ).isoformat()
        c=db()
        r=c.execute("""
            SELECT a.*,c.name,c.phone,c.telegram_user_id
            FROM appointments a JOIN customers c ON c.id=a.customer_id
            WHERE a.id=? AND a.status='booked' AND c.telegram_user_id=?
        """,(aid,q.from_user.id)).fetchone()
        if not r:
            c.close(); await q.answer("رزرو پیدا نشد.",show_alert=True); return
        c.execute("UPDATE appointments SET appointment_date=?,appointment_time=?,updated_at=? WHERE id=?",(d,tm,now,aid))
        c.execute(
            "INSERT INTO customer_events(owner_user_id,customer_id,appointment_id,event_type,details,created_at) VALUES(?,?,?,?,?,?)",
            (owner,r["customer_id"],aid,"customer_rescheduled",f"{r['appointment_date']} {r['appointment_time']} -> {d} {tm}",now)
        )
        c.commit(); c.close()
        try:
            await context.bot.send_message(
                owner,
                f"🔄 <b>مشتری زمان رزرو را تغییر داد</b>\n\n"
                f"👤 {html.escape(r['name'] or 'مشتری')}\n"
                f"📅 قبلی: {jalali_pretty_date(r['appointment_date'])}\n"
                f"⏰ قبلی: {r['appointment_time']}\n"
                f"📅 جدید: {jalali_pretty_date(d)}\n"
                f"⏰ جدید: {tm}",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning("Owner reschedule notification failed: %s",e)
        try:
            await context.bot.send_message(
                q.from_user.id,
                f"🔄 <b>رزرو شما تغییر کرد</b>\n\n📅 {jalali_pretty_date(d)}\n⏰ {tm}\n\nصاحب کسب‌وکار از تغییر زمان مطلع شد.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning("Customer reschedule confirmation failed: %s",e)
        context.user_data.clear()
        await q.answer("✅ زمان رزرو تغییر کرد.")
        await q.message.edit_text(
            f"✅ <b>زمان رزرو با موفقیت تغییر کرد.</b>\n\n"
            f"📅 {jalali_pretty_date(d)}\n⏰ {tm}\n\n"
            "صاحب کسب‌وکار از تغییر زمان مطلع شد.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📅 رزروهای من",callback_data="cust:mybookings")],
                [InlineKeyboardButton("🏠 منوی اصلی",callback_data="nav:main")]
            ])
        )
        return
    context.user_data.update(booking_time=tm,customer_mode="public_booking_name")
    await q.answer()
    await q.message.edit_text("👤 <b>نام شما را بفرست:</b>",parse_mode="HTML")

async def public_booking_save(update,context):

    mode=context.user_data.get("customer_mode");
    if mode not in ("public_booking_name","public_booking_phone"):return False
    text=update.message.text.strip(); uid=update.effective_user.id
    if mode=="public_booking_name": context.user_data.update(public_name=text,customer_mode="public_booking_phone"); await update.message.reply_text("📞 شماره تلفن را بفرست یا - بزن:"); return True
    owner=context.user_data.get("booking_owner"); d=context.user_data.get("booking_date"); tm=context.user_data.get("booking_time"); phone="" if text=="-" else text; name=context.user_data.get("public_name") or display_name(uid); now=datetime.now(TZ).isoformat();
    c=db(); existing=c.execute("SELECT id FROM customers WHERE owner_user_id=? AND telegram_user_id=? LIMIT 1",(owner,uid)).fetchone(); cid=existing["id"] if existing else c.execute("INSERT INTO customers(owner_user_id,name,phone,telegram_username,telegram_user_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(owner,name,phone,update.effective_user.username or '',uid,now,now)).lastrowid
    if existing:c.execute("UPDATE customers SET name=?,phone=?,telegram_username=?,updated_at=? WHERE id=?",(name,phone,update.effective_user.username or '',now,cid))
    if not d or not tm or tm not in available_slots(owner,d,30) or has_conflict(owner,d,tm,30):
        c.close(); context.user_data.clear(); await update.message.reply_text("❌ این زمان دیگر آزاد نیست یا خارج از ساعات کاری است. لطفاً دوباره تاریخ را انتخاب کن."); return True
    aid=c.execute("INSERT INTO appointments(owner_user_id,customer_id,appointment_date,appointment_time,duration_minutes,service,notes,reminder_minutes,status,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(owner,cid,d,tm,30,'','رزرو آنلاین','30','booked','online',now,now)).lastrowid; c.execute("INSERT INTO customer_events(owner_user_id,customer_id,appointment_id,event_type,details,created_at) VALUES(?,?,?,?,?,?)",(owner,cid,aid,'online_booking','',now)); c.commit(); c.close()
    p=ensure_business_profile(owner); business_name=p["business_name"] or p["business_type"] or "کسب‌وکار"
    try: await context.bot.send_message(owner,f"🔔 <b>رزرو جدید داری!</b>\n\n🏪 {html.escape(business_name)}\n👤 {html.escape(name)}\n📅 {jalali_pretty_date(d)}\n⏰ {tm}\n📞 {html.escape(phone) if phone else '—'}")
    except Exception:pass
    context.user_data.clear(); await update.message.reply_text(f"✅ <b>رزرو شما با موفقیت ثبت شد.</b>\n\n🏪 {html.escape(business_name)}\n📅 {jalali_pretty_date(d)}\n⏰ {tm}\n\n📅 از بخش «رزروهای من» می‌توانید رزرو را مدیریت، لغو یا جابه‌جا کنید."); return True

async def customer_reminder_job(context):
    now=datetime.now(TZ).replace(second=0,microsecond=0); c=db(); rows=c.execute("SELECT a.*,c.name,c.phone,c.telegram_user_id FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.status='booked' AND a.appointment_date>=?",(now.date().isoformat(),)).fetchall(); c.close()
    for r in rows:
        try:
            dt=datetime.fromisoformat(f"{r['appointment_date']}T{r['appointment_time']}").replace(tzinfo=TZ); diff=int((dt-now).total_seconds()//60)
            if diff not in parse_reminder_list(r['reminder_minutes']):continue
            reminder_key=f"appointment:{r['id']}:{diff}:{r['appointment_date']}"
            if not delivery_once(reminder_key,r['owner_user_id'],"owner_appointment_reminder"): continue
            await context.bot.send_message(r['owner_user_id'],f"🔔 <b>یادآوری نوبت</b>\n\n👤 {html.escape(r['name'])}\n📅 {jalali_date_str(r['appointment_date'])}\n⏰ {r['appointment_time']}\n📞 {html.escape(r['phone']) if r['phone'] else '—'}",parse_mode="HTML",reply_markup=appointment_reminder_keyboard(r['owner_user_id'],r['id']))
            if r['telegram_user_id']:
                customer_key=f"appointment_customer:{r['id']}:{diff}:{r['appointment_date']}"
                if delivery_once(customer_key,r['telegram_user_id'],"customer_appointment_reminder"):
                    p=ensure_business_profile(r['owner_user_id']); bname=p['business_name'] or p['business_type'] or 'کسب‌وکار'
                    await context.bot.send_message(r['telegram_user_id'],f"🔔 <b>یادآوری نوبت شما</b>\n\n🏪 {html.escape(bname)}\n📅 {jalali_date_str(r['appointment_date'])}\n⏰ {r['appointment_time']}",parse_mode='HTML')
        except Exception as e:logger.warning("Customer reminder failed: %s",e)

async def customer_reengagement_job(context):
    """Send a single re-booking reminder roughly ten months after a completed/held appointment."""
    now=datetime.now(TZ); target=(now.date()-timedelta(days=304)).isoformat()
    c=db(); rows=c.execute("""SELECT a.id,a.owner_user_id,a.customer_id,a.appointment_date,c.telegram_user_id,c.name
        FROM appointments a JOIN customers c ON c.id=a.customer_id
        WHERE a.status IN ('done','booked') AND a.appointment_date=? AND c.telegram_user_id IS NOT NULL""",(target,)).fetchall(); c.close()
    for r in rows:
        key=f"rebook:{r['owner_user_id']}:{r['customer_id']}:{r['appointment_date']}"
        if not delivery_once(key,int(r['telegram_user_id']),"customer_reengagement"): continue
        try:
            p=ensure_business_profile(r['owner_user_id']); bname=p['business_name'] or p['business_type'] or 'کسب‌وکار'
            await context.bot.send_message(r['telegram_user_id'],f"📅 <b>یادآوری نوبت بعدی</b>\n\n{html.escape(r['name'] or 'مشتری')} عزیز، حدود ۱۰ ماه از نوبت قبلی شما در <b>{html.escape(bname)}</b> گذشته است.\n\nاگر برای نوبت بعدی آماده‌ای، می‌توانی دوباره رزرو کنی.",parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📅 رزرو دوباره",callback_data=f"cust:booklink:{r['owner_user_id']}")],[main_menu_button(r['telegram_user_id'])]]))
        except Exception: logger.warning("Customer reengagement failed",exc_info=True)

async def customer_daily_report_job(context):
    now=datetime.now(TZ)
    if now.hour!=23 or now.minute!=0:return
    d=now.date().isoformat(); c=db(); owners=c.execute("SELECT DISTINCT owner_user_id FROM appointments WHERE appointment_date=?",(d,)).fetchall()
    for o in owners:
        uid=o["owner_user_id"]; total=c.execute("SELECT COUNT(*) n FROM appointments WHERE owner_user_id=? AND appointment_date=?",(uid,d)).fetchone()["n"]; done=c.execute("SELECT COUNT(*) n FROM appointments WHERE owner_user_id=? AND appointment_date=? AND status='done'",(uid,d)).fetchone()["n"]; cancelled=c.execute("SELECT COUNT(*) n FROM appointments WHERE owner_user_id=? AND appointment_date=? AND status='cancelled'",(uid,d)).fetchone()["n"]; top=c.execute("SELECT c.name,COUNT(*) n FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.owner_user_id=? AND a.appointment_date=? AND a.status='done' GROUP BY a.customer_id ORDER BY n DESC LIMIT 5",(uid,d)).fetchall()
        text=f"🌙 <b>گزارش پایان روز مشتریان</b>\n\n📅 {d}\n👥 کل نوبت‌ها: {total}\n✅ انجام‌شده: {done}\n❌ لغوشده: {cancelled}\n\n🏆 مشتریان پرتکرار امروز:\n"+("\n".join(f"• {r['name']} — {r['n']} مراجعه" for r in top) or "امروز مراجعه‌ای ثبت نشده.")
        try: await context.bot.send_message(uid,text,parse_mode="HTML",reply_markup=customer_keyboard(uid))
        except Exception as e: logger.warning("Customer daily report failed: %s",e)
    c.close()

async def customer_morning_job(context):
    now=datetime.now(TZ)
    if now.hour!=7 or now.minute!=0:return
    c=db(); rows=c.execute("SELECT a.*,c.name,c.phone FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.appointment_date=? AND a.status='booked' ORDER BY a.owner_user_id,a.appointment_time",(now.date().isoformat(),)).fetchall(); c.close(); groups={}
    for r in rows:groups.setdefault(r['owner_user_id'],[]).append(r)
    for owner,items in groups.items():
        lines=["🌅 <b>برنامه مشتری‌های امروز</b>",""]+[f"🕐 <b>{r['appointment_time']}</b> — 👤 {html.escape(r['name'])}"+(f" — 📞 {html.escape(r['phone'])}" if r['phone'] else '') for r in items]+[f"\n👥 مجموع: {len(items)} مشتری"]
        try:await context.bot.send_message(owner,"\n".join(lines),parse_mode="HTML",reply_markup=customer_keyboard(owner))
        except Exception as e:logger.warning("Customer morning failed: %s",e)

async def hide_main_reply_keyboard(update):
    """Remove the persistent main ReplyKeyboard without adding a visible UI message."""
    try:
        m = await update.effective_chat.send_message("⁣", reply_markup=ReplyKeyboardRemove())
        try:
            await m.delete()
        except Exception:
            pass
    except Exception:
        pass


async def text_router(update, context):
    uid = update.effective_user.id
    register_user(uid, update.effective_user.first_name or "")

    # Safety timeout: a stale text-input flow expires after 15 minutes.
    flow_started = context.user_data.get("_flow_started_at")
    if flow_started:
        try:
            if (datetime.now(TZ) - datetime.fromisoformat(flow_started)).total_seconds() > 900:
                clear_flow(context)
        except Exception:
            clear_flow(context)

    if not await require_subscription(update, context):
        return
    text = update.message.text.strip()
    if any(k in context.user_data for k in (
        "channel_state", "admin_broadcast", "ai_chat", "auto_wait_interval", "admin_health_time",
        "auto_wait_time", "awaiting_custom_duration", "awaiting_custom_edit_time",
        "awaiting_custom_goal", "awaiting_custom_time", "awaiting_edit_time",
        "awaiting_rename", "awaiting_step", "support_new"
    )):
        context.user_data.setdefault("_flow_started_at", datetime.now(TZ).isoformat())

    if text in ("⬅️ برگشت","⬅️ Back","🏠 منوی اصلی","🏠 Main Menu"):
        clear_flow(context)
        try: await update.message.delete()
        except Exception: pass
        await update.message.chat.send_message("🏠",reply_markup=keyboard(uid))
        return

    # A failed input flow must never trap the user inside that flow.
    # Normal menu buttons always have priority over transient input states.
    if is_menu_button(uid, text) and context.user_data:
        clear_flow(context)

    if context.user_data.get("admin_vip_edit_user") and admin_guard(uid):
        target=int(context.user_data.pop("admin_vip_edit_user")); raw=normalize_digits(text)
        try: days=int(raw); assert -3650<=days<=3650
        except Exception: await update.message.reply_text("❌ تعداد روز نامعتبر است.",reply_markup=nav_keyboard(uid)); context.user_data["admin_vip_edit_user"]=target; return True
        c=db(); r=c.execute("SELECT vip_until FROM users WHERE user_id=?",(target,)).fetchone(); now_dt=datetime.now(TZ);
        if days==0: new_until=None
        else:
            base=now_dt
            if r and r["vip_until"]:
                try: base=max(base,datetime.fromisoformat(r["vip_until"]))
                except Exception: pass
            new_until=(base+timedelta(days=days)).isoformat()
        c.execute("UPDATE users SET vip_until=? WHERE user_id=?",(new_until,target)); c.execute("INSERT INTO subscription_history(user_id,plan,duration_days,source,started_at,expires_at,created_at) VALUES(?,?,?,?,?,?,?)",(target,"VIP Edit",days,"admin_edit",now_dt.isoformat(),new_until,now_dt.isoformat())); c.commit(); c.close(); admin_log(uid,"vip_edit",target,str(days)); await update.message.reply_text("❌ VIP لغو شد." if days==0 else f"✅ اشتراک ویرایش شد. پایان: {fa_datetime(new_until)}",reply_markup=final_admin_keyboard()); return True

    if context.user_data.get("customer_broadcast_mode"):
        msg=text.strip()
        if not msg: await update.message.reply_text("❌ پیام خالی است.",reply_markup=nav_keyboard(uid)); return True
        c=db(); rows=c.execute("SELECT DISTINCT telegram_user_id FROM customers WHERE owner_user_id=? AND status='active' AND telegram_user_id IS NOT NULL",(uid,)).fetchall(); now=datetime.now(TZ).isoformat(); cur=c.execute("INSERT INTO customer_broadcasts(owner_user_id,audience,message,created_at) VALUES(?,?,?,?)",(uid,"active",msg,now)); bid=cur.lastrowid; c.commit(); c.close(); sent=0
        for r in rows:
            try:
                await context.bot.send_message(r["telegram_user_id"],f"📢 <b>پیام از {html.escape(ensure_business_profile(uid)['business_name'] or 'کسب‌وکار')}</b>\n\n{html.escape(msg)}",parse_mode="HTML")
                sent+=1
            except Exception: pass
        c=db(); c.execute("UPDATE customer_broadcasts SET sent_count=? WHERE id=?",(sent,bid)); c.commit(); c.close(); clear_flow(context); await update.message.reply_text(f"✅ پیام برای {sent} مشتری ارسال شد.",reply_markup=customer_keyboard(uid)); return True

    if context.user_data.get("goal_reminder_custom"):
        gid=int(context.user_data.pop("goal_reminder_custom")); tm=parse_time(text); g=get_goal(uid,gid)
        if not g or not tm:
            await update.message.reply_text("❌ ساعت نامعتبر است. مثال: 20:30",reply_markup=nav_keyboard(uid)); context.user_data["goal_reminder_custom"]=gid; return True
        d=(datetime.now(TZ).date()+timedelta(days=1)).isoformat(); c=db(); c.execute("INSERT OR REPLACE INTO goal_reminder_overrides(user_id,goal_id,reminder_date,reminder_time,created_at) VALUES(?,?,?,?,?)",(uid,gid,d,tm,datetime.now(TZ).isoformat())); c.commit(); c.close(); clear_flow(context); await update.message.reply_text(f"✅ یادآوری هدف «{html.escape(g['name'])}» برای فردا ساعت {tm} تنظیم شد.",parse_mode="HTML",reply_markup=keyboard(uid)); return True

    if context.user_data.get("admin_pause_mode") and admin_guard(uid):
        value=text.strip().lower()
        if value in ("forever","نامحدود"):
            set_system_setting("bot_paused_until","9999-12-31T23:59:59+03:30")
            admin_log(uid,"bot_pause_on",None,"forever")
            context.user_data.pop("admin_pause_mode",None)
            await update.message.reply_text("⏸ ربات متوقف شد. مدیران همچنان دسترسی دارند.",reply_markup=final_admin_keyboard()); return
        try: minutes=int(normalize_digits(value)); assert 1<=minutes<=10080
        except Exception:
            await update.message.reply_text("❌ عدد نامعتبر است. بین ۱ تا ۱۰۰۸۰ دقیقه وارد کن یا forever بنویس.",reply_markup=nav_keyboard(uid)); return
        until=datetime.now(TZ)+timedelta(minutes=minutes); set_system_setting("bot_paused_until",until.isoformat()); admin_log(uid,"bot_pause_on",None,str(minutes)); context.user_data.pop("admin_pause_mode",None)
        await update.message.reply_text(f"⏸ ربات تا {fa_datetime(until)} متوقف شد.",reply_markup=final_admin_keyboard()); return

    if context.user_data.get("auto_wait_interval"):
        try:
            minutes = int(text)
            if minutes < 5 or minutes > 1440:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ عدد نامعتبر است. فقط عددی بین ۵ تا ۱۴۴۰ دقیقه وارد کن.")
            return
        set_auto_setting("interval_minutes", str(minutes))
        set_auto_setting("enabled", "1")
        set_auto_setting("next_run", (datetime.now(TZ) + timedelta(minutes=minutes)).isoformat())
        context.user_data.pop("auto_wait_interval", None)
        await update.message.reply_text(
            f"✅ انتشار خودکار روی هر {minutes} دقیقه تنظیم شد و روشن است.",
            reply_markup=auto_channel_keyboard(),
        )
        return

    if context.user_data.get("auto_wait_time"):
        value = parse_time(text)
        if not value:
            await update.message.reply_text("❌ ساعت نامعتبر است. مثال: 18:00")
            return
        set_auto_setting("time", value)
        context.user_data.pop("auto_wait_time", None)
        await update.message.reply_text(
            "✅ ساعت انتشار خودکار روی " + value + " تنظیم شد.",
            reply_markup=auto_channel_keyboard()
        )
        return

    if not await final_guard(update, context):
        return
    if text in ("📅 رزروهای من","📅 My Bookings"):
        await customer_my_bookings(update,context)
        return
    if await support_text(update, context):
        return
    if await admin_health_time_save(update, context):
        return
    if await final_admin_text(update, context):
        return
    if await channel_text_save(update, context):
        return

    if await admin_broadcast_save(update, context):
        return

    if await ai_chat_text(update, context):
        return
    if await custom_goal_save(update, context):
        return
    if await custom_duration_save(update, context):
        return
    if await custom_time_save(update, context):
        return

    if await custom_edit_time_save(update, context):
        return

    if await public_booking_save(update, context):
        return
    if await customer_text_save(update, context):
        return

    if await rename_save(update, context):
        return

    menu = T[lang(uid)]["menu"]
    requested_feature=FEATURE_MENU_MAP.get(text)
    if requested_feature and not user_feature_allowed(uid,requested_feature):
        await update.message.reply_text("⛔ این قابلیت فعلاً توسط مدیر غیرفعال شده است.",reply_markup=keyboard(uid)); return
    if text in ("🎯 اهداف امروز", "🎯 Today's Goals"):
        await today(update, context)
    elif text in ("✏️ هدف خودم می‌نویسم", "✏️ Write my own goal"):
        await custom_goal_start(update, context)
    elif text in ("🏆 اهداف آماده", "🏆 Ready Goals"):
        await ready_menu(update, context)
    elif text in ("✏️ ویرایش اهداف", "✏️ Edit Goals"):
        await edit_menu(update, context)
    elif text in ("📅 جدول هفتگی", "📅 Weekly Table"):
        await weekly(update, context)
    elif text in ("📊 آمار من", "📊 My Stats"):
        await stats(update, context)
    elif text in ("👤 پروفایل", "👤 Profile"):
        await profile(update, context)
    elif text in ("🏆 دستاوردها", "🏆 Achievements"):
        await achievements(update, context)
    elif text in ("⭐ XP",):
        await xp_command(update, context)
    elif text in ("🤝 دعوت دوستان", "🤝 Referrals"):
        await referral(update, context)
    elif text in ("📈 قیمت آنلاین", "📈 Online Prices"):
        await prices(update, context)
    elif text in ("💎 VIP",):
        await vip_center(update, context)
    elif text in ("🤖 چت با AI", "🤖 AI Chat"):
        await ai_chat_start(update, context)
    elif text in ("🎫 پشتیبانی", "🎫 Support"):
        await support_start(update, context)
    elif text in ("⚙️ تنظیمات", "⚙️ Settings"):
        await settings(update, context)
    elif text in ("👥 مدیریت مشتری و نوبت‌دهی", "👥 Customer & Appointments"):
        await customer_panel(update, context)
    elif text in ("📢 مدیریت کانال", "📢 Channel Management"):
        if admin_guard(uid):
            clear_flow(context)
            await update.message.reply_text(
                "📢 <b>مدیریت کانال و پست‌گذاری</b>\n\nاتصال کانال، ساخت پست، زمان‌بندی و انتشار خودکار.",
                parse_mode="HTML",
                reply_markup=channel_keyboard(),
            )
            await hide_main_reply_keyboard(update)
        else:
            await update.message.reply_text("⛔ دسترسی ندارید.")
    elif text in ("🛡 پنل مدیریت", "🛡 Admin Panel"):
        clear_flow(context)
        await admin_command(update, context)

    else:
        log_activity(uid, "text_message")
    log_usage_event(uid, "text_message")



# ========================= FINAL MYTASKS FEATURES =========================
def feature_enabled(key):
    c=db(); r=c.execute("SELECT enabled FROM feature_flags WHERE key=?",(key,)).fetchone(); c.close(); return bool(r["enabled"]) if r else True

def set_feature(key,enabled,admin_id=0):
    c=db(); c.execute("INSERT INTO feature_flags(key,enabled,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at",(key,int(enabled),datetime.now(TZ).isoformat())); c.commit(); c.close();
    if admin_id: admin_log(admin_id,"feature_toggle",None,f"{key}={int(enabled)}")

def admin_log(admin_id,action,target_user=None,details=""):
    c=db(); c.execute("INSERT INTO admin_logs(admin_id,action,target_user,details,created_at) VALUES(?,?,?,?,?)",(admin_id,action,target_user,details,datetime.now(TZ).isoformat())); c.commit(); c.close()

def is_registered_user(uid):
    c=db(); r=c.execute("SELECT 1 FROM users WHERE user_id=?",(int(uid),)).fetchone(); c.close(); return bool(r)

def log_usage_event(uid, event_type, details=""):
    try:
        c=db(); c.execute("INSERT INTO bot_usage_events(user_id,event_type,details,created_at) VALUES(?,?,?,?)",(uid,event_type,details,datetime.now(TZ).isoformat())); c.commit(); c.close()
    except Exception:
        logger.exception("Usage event logging failed: %s", event_type)

def award_engagement_xp_once(uid, amount, reason, reward_key, event_type="engagement"):
    try:
        if not is_registered_user(uid):
            return False
        c=db(); cur=c.execute("INSERT OR IGNORE INTO reward_log(reward_key,user_id,reward_type,amount,created_at) VALUES(?,?,?,?,?)",(reward_key,int(uid),reason,int(amount),datetime.now(TZ).isoformat())); inserted=(cur.rowcount==1); c.commit(); c.close()
        if inserted:
            add_xp(int(uid), int(amount), reason)
            log_activity(int(uid), event_type)
            log_usage_event(int(uid), event_type, reason)
        return inserted
    except Exception:
        logger.exception("Engagement XP award failed: %s", reason)
        return False

def add_xp(uid,amount,reason="interaction"):
    if amount<=0:return
    c=db(); today=datetime.now(TZ).date().isoformat(); used=c.execute("SELECT COALESCE(SUM(amount),0) n FROM xp_log WHERE user_id=? AND substr(created_at,1,10)=?",(uid,today)).fetchone()["n"]; amount=min(amount,max(0,100-used))
    if amount: c.execute("UPDATE users SET xp=COALESCE(xp,0)+? WHERE user_id=?",(amount,uid)); c.execute("INSERT INTO xp_log(user_id,amount,reason,created_at) VALUES(?,?,?,?)",(uid,amount,reason,datetime.now(TZ).isoformat()))
    c.commit(); c.close()

def xp_info(uid):
    c=db(); r=c.execute("SELECT COALESCE(xp,0) xp,COALESCE(vip_until,'') vip_until FROM users WHERE user_id=?",(uid,)).fetchone(); c.close(); xp=int(r["xp"] if r else 0); return xp,1+xp//100,(r["vip_until"] if r else "")

def is_vip(uid):
    c=db(); r=c.execute("SELECT vip_until FROM users WHERE user_id=?",(uid,)).fetchone(); c.close()
    if not r or not r["vip_until"]: return False
    try:return datetime.fromisoformat(r["vip_until"])>datetime.now(TZ)
    except:return False

def user_blocked(uid):
    c=db(); r=c.execute("SELECT blocked FROM users WHERE user_id=?",(uid,)).fetchone(); c.close(); return bool(r and r["blocked"])

async def final_guard(update,context):
    uid=update.effective_user.id
    if user_blocked(uid) and uid not in ADMIN_IDS:
        if update.callback_query: await update.callback_query.answer("⛔ حساب شما مسدود است.",show_alert=True)
        elif update.message: await update.message.reply_text("⛔ حساب شما مسدود است.")
        return False
    return True

async def xp_command(update,context):
    uid=update.effective_user.id; xp,level,_=xp_info(uid); await update.message.reply_text(f"⭐ XP: {xp}\n🏅 سطح: {level}\n👑 VIP: {'فعال' if is_vip(uid) else 'غیرفعال'}")

def final_admin_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📊 داشبورد",callback_data="adm:stats"),InlineKeyboardButton("👥 کاربران",callback_data="adm:users")],[InlineKeyboardButton("🔎 جستجو",callback_data="adm:search"),InlineKeyboardButton("🧰 ابزار کاربر",callback_data="adm:tools")],[InlineKeyboardButton("📡 کانال و پست‌گذاری",callback_data="adm:channel"),InlineKeyboardButton("👥 مدیریت مشتری",callback_data="adm:customers")],[InlineKeyboardButton("⚙️ قابلیت‌ها",callback_data="adm:features"),InlineKeyboardButton("💰 هزینه/سرویس‌ها",callback_data="adm:costs")],
        [InlineKeyboardButton("⭐ XP / VIP",callback_data="adm:xpvip"),InlineKeyboardButton("👥 ظرفیت/کاربران",callback_data="adm:capacity")],[InlineKeyboardButton("🎫 تیکت‌ها",callback_data="adm:tickets"),InlineKeyboardButton("🩺 Health Check",callback_data="adm:health")],[InlineKeyboardButton("⏰ زمان‌بندی چکاپ",callback_data="adm:health_schedule"),InlineKeyboardButton("⏸ توقف موقت ربات",callback_data="adm:pause")],[InlineKeyboardButton("🧪 مرکز تست",callback_data="adm:test"),InlineKeyboardButton("💾 بکاپ",callback_data="adm:backup")],[InlineKeyboardButton("🔎 عیب‌یابی کامل",callback_data="adm:diagnostics"),InlineKeyboardButton("📝 لاگ مدیران",callback_data="adm:audit")],[InlineKeyboardButton("📋 گزارش روز",callback_data="adm:report"),InlineKeyboardButton("📢 پیام همگانی",callback_data="adm:broadcast")],[InlineKeyboardButton("🏠 منوی اصلی",callback_data="adm:main")]])

async def admin_user_detail_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔",show_alert=True); return
    await q.answer(); target=int(q.data.split(":",1)[1]); c=db(); u=c.execute("SELECT * FROM users WHERE user_id=?",(target,)).fetchone()
    if not u: c.close(); await q.message.reply_text("❌ کاربر پیدا نشد.",reply_markup=final_admin_keyboard()); return
    usage=c.execute("SELECT COUNT(*) n FROM bot_usage_events WHERE user_id=?",(target,)).fetchone()["n"]; usage30=c.execute("SELECT COUNT(*) n FROM bot_usage_events WHERE user_id=? AND created_at>=?",(target,(datetime.now(TZ)-timedelta(days=30)).isoformat())).fetchone()["n"]
    goals=c.execute("SELECT COUNT(*) n FROM goals WHERE user_id=?",(target,)).fetchone()["n"]; done=c.execute("SELECT COUNT(*) n FROM goal_days WHERE user_id=? AND status='done'",(target,)).fetchone()["n"]; reactions=c.execute("SELECT COUNT(*) n FROM channel_reactions WHERE user_id=?",(target,)).fetchone()["n"]; polls=c.execute("SELECT COUNT(*) n FROM channel_poll_votes WHERE user_id=?",(target,)).fetchone()["n"]; referrals=c.execute("SELECT COUNT(*) n FROM referrals WHERE inviter_id=?",(target,)).fetchone()["n"]; appts=c.execute("SELECT COUNT(*) n FROM appointments WHERE owner_user_id=?",(target,)).fetchone()["n"]; subs=c.execute("SELECT * FROM subscription_history WHERE user_id=? ORDER BY created_at DESC LIMIT 10",(target,)).fetchall(); c.close()
    sub_lines="\n".join(f"• {r['plan']} | {r['duration_days']} روز | {r['source']} | تا {r['expires_at'] or '—'}" for r in subs) or "سابقه‌ای ثبت نشده"
    text=(f"👤 <b>پرونده کاربر</b>\n\nنام: {html.escape(u['first_name'] or 'بدون نام')}\n🆔 ID: <code>{target}</code>\nوضعیت: {'⛔ محدود' if u['blocked'] else '🟢 فعال'}\n💎 اشتراک: {'فعال تا '+(u['vip_until'] or '')[:16] if u['vip_until'] else 'رایگان'}\n⭐ XP: {u['xp']}\n\n📊 <b>آمار استفاده</b>\n🤖 رویدادهای ربات: {usage}\n📅 ۳۰ روز اخیر: {usage30}\n🎯 اهداف: {goals} | انجام‌شده: {done}\n📣 واکنش کانال: {reactions}\n🗳 نظرسنجی: {polls}\n🤝 دعوت موفق: {referrals}\n👥 نوبت‌های کسب‌وکار: {appts}\n\n💳 <b>سوابق اشتراک/تمدید</b>\n{sub_lines}")
    kb=[[InlineKeyboardButton("🚫 محدود کردن" if not u['blocked'] else "🔓 رفع محدودیت",callback_data=f"admu_block:{target}")],[InlineKeyboardButton("🎁 ۷ روز رایگان",callback_data=f"admu_vip:{target}:7"),InlineKeyboardButton("💎 ۳۰ روز",callback_data=f"admu_vip:{target}:30")],[InlineKeyboardButton("♾️ اشتراک نامحدود",callback_data=f"admu_unlimited:{target}")],[InlineKeyboardButton("✏️ ویرایش اشتراک",callback_data=f"admu_editvip:{target}")],[InlineKeyboardButton("⬅️ کاربران",callback_data="adm:users")]]
    await q.message.reply_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def admin_user_action_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔",show_alert=True); return
    parts=q.data.split(":"); action=parts[0].split("_",1)[1]; target=int(parts[1]); now=datetime.now(TZ).isoformat(); c=db()
    if action=="block":
        r=c.execute("SELECT blocked FROM users WHERE user_id=?",(target,)).fetchone(); new=0 if r and r["blocked"] else 1; c.execute("UPDATE users SET blocked=? WHERE user_id=?",(new,target)); c.commit(); c.close(); admin_log(uid,"user_block_toggle",target,str(new)); await q.answer("🔓 رفع محدودیت شد" if not new else "🚫 محدود شد")
    elif action=="vip":
        days=int(parts[2]); expires=(datetime.now(TZ)+timedelta(days=days)).isoformat(); c.execute("UPDATE users SET vip_until=? WHERE user_id=?",(expires,target)); c.execute("INSERT INTO subscription_history(user_id,plan,duration_days,source,started_at,expires_at,created_at) VALUES(?,?,?,?,?,?,?)",(target,"VIP",days,"admin",now,expires,now)); c.commit(); c.close(); admin_log(uid,"vip_grant",target,f"{days}d"); await q.answer(f"💎 {days} روز VIP شد")
    elif action=="unlimited":
        expires="9999-12-31T23:59:59"; c.execute("UPDATE users SET vip_until=? WHERE user_id=?",(expires,target)); c.execute("INSERT INTO subscription_history(user_id,plan,duration_days,source,started_at,expires_at,created_at) VALUES(?,?,?,?,?,?,?)",(target,"VIP Unlimited",0,"admin",now,expires,now)); c.commit(); c.close(); admin_log(uid,"vip_unlimited",target); await q.answer("♾️ اشتراک نامحدود شد")
    elif action=="editvip":
        c.close(); await q.message.reply_text("✏️ <b>ویرایش اشتراک</b>\n\nروز مثبت = اضافه کردن\nروز منفی = کم کردن\n0 = لغو کامل\nمثال: -7 یا 15",parse_mode="HTML",reply_markup=nav_keyboard(uid)); context.user_data["admin_vip_edit_user"]=target; return
    else: c.close(); return
    q.data=f"admu:{target}"; await admin_user_detail_callback(update,context)


def _admin_db_diagnostics():
    """Local, non-destructive diagnostics for the management panel."""
    checks=[]
    c=db()
    try:
        integrity=c.execute("PRAGMA integrity_check").fetchone()[0]
        checks.append(("SQLite integrity", integrity == "ok", str(integrity)))
        required = [
            "users","goals","goal_days","user_settings","feature_flags",
            "appointments","customers","working_hours","channel_config",
            "channel_posts","auto_post_history","auto_pending",
            "health_checks","admin_logs","tickets","payments",
            "daily_reports","system_settings","managed_channels","goal_reminder_overrides","customer_broadcasts","customer_reengagement_log","auto_channel_settings_v2","service_events","user_feature_preferences"
        ]
        existing={r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for name in required:
            checks.append((f"table:{name}", name in existing, "present" if name in existing else "MISSING"))
        # Validate important indexes/unique constraints indirectly by prepare-only queries.
        for sql,label in [
            ("SELECT 1 FROM users LIMIT 1","users query"),
            ("SELECT 1 FROM goals LIMIT 1","goals query"),
            ("SELECT 1 FROM appointments LIMIT 1","appointments query"),
            ("SELECT 1 FROM auto_post_history LIMIT 1","auto post history query"),
        ]:
            try:
                c.execute(sql).fetchone()
                checks.append((label,True,"ok"))
            except Exception as e:
                checks.append((label,False,str(e)))
    except Exception as e:
        checks.append(("database",False,str(e)))
    finally:
        c.close()
    return checks

def _admin_diagnostics_text():
    checks=_admin_db_diagnostics()
    lines=["🔎 <b>عیب‌یابی کامل ربات</b>",""]
    for name,ok,detail in checks:
        lines.append(f"{'🟢' if ok else '🔴'} {html.escape(name)} — {html.escape(detail)}")
    bad=sum(1 for _,ok,_ in checks if not ok)
    lines += ["", f"نتیجه: {'🟢 سالم' if not bad else f'🔴 {bad} مورد نیازمند بررسی'}"]
    return "\n".join(lines)

def _admin_test_text():
    checks=_admin_db_diagnostics()
    db_ok=all(ok for name,ok,_ in checks if name=="SQLite integrity")
    tables_ok=sum(1 for name,ok,_ in checks if name.startswith("table:") and ok)
    tables_total=sum(1 for name,_,_ in checks if name.startswith("table:"))
    return (
        "🧪 <b>مرکز تست</b>\n\n"
        f"🗄 دیتابیس: {'🟢 سالم' if db_ok else '🔴 مشکل دارد'}\n"
        f"📦 جداول اصلی: {tables_ok}/{tables_total}\n"
        f"🩺 Health Check: آماده اجرا از پنل\n"
        f"📢 کانال: تست اتصال از بخش کانال\n"
        f"📅 رزرو: تاریخ/ساعت در مسیر واقعی رزرو بررسی می‌شود\n\n"
        "این بخش تست‌های غیرمخرب انجام می‌دهد و هیچ داده کاربر را حذف نمی‌کند."
    )

async def _admin_manual_backup(update, context):
    uid=update.effective_user.id
    if not admin_guard(uid):
        await update.callback_query.answer("⛔",show_alert=True); return
    await update.callback_query.answer("در حال ساخت بکاپ...")
    ok=backup_database_snapshot(keep=20)
    admin_log(uid,"manual_backup",None,"success" if ok else "failed")
    await update.callback_query.message.edit_text(
        ("💾 <b>بکاپ با موفقیت ساخته شد.</b>\nنسخه‌های قبلی هم حفظ شدند."
         if ok else "❌ ساخت بکاپ انجام نشد. لاگ خطا را بررسی کن."),
        parse_mode="HTML",reply_markup=final_admin_keyboard()
    )

def admin_costs_text():
    c=db()
    rows=c.execute("SELECT key,label,status,provider,note,enabled FROM service_costs ORDER BY key").fetchall()
    users=c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    active24=c.execute("SELECT COUNT(*) n FROM users WHERE last_active_at>=?",((datetime.now(TZ)-timedelta(hours=24)).isoformat(),)).fetchone()["n"]
    active7=c.execute("SELECT COUNT(*) n FROM users WHERE last_active_at>=?",((datetime.now(TZ)-timedelta(days=7)).isoformat(),)).fetchone()["n"]
    c.close()
    labels={
        "free":"🟢 رایگان", "optional_paid":"🟡 پولیِ اختیاری", "variable":"🟠 هزینه متغیر",
        "free_or_variable":"🟠 رایگان/متغیر", "transactional":"🔵 وابسته به تراکنش"
    }
    lines=["💰 <b>مرکز هزینه و سرویس‌ها</b>","","این بخش با «رایگان/VIP» فرق دارد:","• ⚙️ قابلیت‌ها = دسترسی کاربر","• 💰 این بخش = هزینه سرویس زیرساختی/خارجی","",f"👥 کاربران ثبت‌شده: <b>{users}</b>",f"🟢 فعال ۲۴ ساعت اخیر: <b>{active24}</b>",f"📅 فعال ۷ روز اخیر: <b>{active7}</b>",""]
    for r in rows:
        state=labels.get(r["status"],r["status"])
        on="🟢 روشن" if r["enabled"] else "🔴 خاموش"
        lines.append(f"{state} {html.escape(r['label'])} — {on}")
        lines.append(f"  ارائه‌دهنده: {html.escape(r['provider'] or '—')}")
        lines.append(f"  {html.escape(r['note'])}")
    lines += ["","⚠️ قیمت دقیق سرویس‌های خارجی ثابت نیست و باید طبق ارائه‌دهنده انتخابی تنظیم شود."]
    return "\n".join(lines)

def admin_capacity_text():
    c=db()
    total=c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    d1=c.execute("SELECT COUNT(*) n FROM users WHERE last_active_at>=?",((datetime.now(TZ)-timedelta(days=1)).isoformat(),)).fetchone()["n"]
    d7=c.execute("SELECT COUNT(*) n FROM users WHERE last_active_at>=?",((datetime.now(TZ)-timedelta(days=7)).isoformat(),)).fetchone()["n"]
    d30=c.execute("SELECT COUNT(*) n FROM users WHERE last_active_at>=?",((datetime.now(TZ)-timedelta(days=30)).isoformat(),)).fetchone()["n"]
    blocked=c.execute("SELECT COUNT(*) n FROM users WHERE blocked=1").fetchone()["n"]
    c.close()
    return ("👥 <b>ظرفیت و کاربران ربات</b>\n\n"
            f"👤 کل کاربران ثبت‌شده: <b>{total}</b>\n"
            f"🟢 فعال ۲۴ ساعت اخیر: <b>{d1}</b>\n"
            f"📅 فعال ۷ روز اخیر: <b>{d7}</b>\n"
            f"🗓 فعال ۳۰ روز اخیر: <b>{d30}</b>\n"
            f"⛔ محدودشده: <b>{blocked}</b>\n\n"
            "ℹ️ در کد فعلی سقف عددیِ ثابت برای تعداد کاربران تعریف نشده است. ظرفیت واقعی به منابع سرور، دیتابیس، APIها و محدودیت‌های Telegram بستگی دارد.\n"
            "📌 این نسخه از SQLite استفاده می‌کند؛ برای تعداد بسیار زیاد کاربر بهتر است بعداً دیتابیس سروری مثل PostgreSQL و صف/کش اضافه شود.")

async def final_admin_panel_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔ دسترسی ندارید",show_alert=True); return
    await q.answer(); a=q.data.split(":",1)[1]
    if a=="stats":
        s=admin_stats(); text="📊 داشبورد مرکزی\n\n"+f"👥 کاربران: {s['users']}\n🆕 جدید امروز: {s['new_today']}\n🟢 فعال امروز: {s['active_today']}\n🎯 اهداف: {s['goals']}\n✅ انجام‌شده امروز: {s['done_today']}\n⏰ یادآوری: {s['reminders']}\n🏆 دستاورد: {s['achievements']}\n📅 نوبت امروز: {s['appointments_today']}\n💎 VIP فعال: {s['vip_users']}\n🎫 تیکت باز: {s['open_tickets']}"; await q.message.edit_text(text,reply_markup=final_admin_keyboard()); return
    if a=="users":
        c=db(); rows=c.execute("SELECT user_id,first_name,COALESCE(xp,0) xp,blocked,warnings FROM users ORDER BY created_at DESC LIMIT 50").fetchall(); c.close(); kb=[[InlineKeyboardButton(f"👤 {r['first_name'] or 'بدون نام'} | ID: {r['user_id']} | ⭐{r['xp']}",callback_data=f"admu:{r['user_id']}")] for r in rows]; kb.append([InlineKeyboardButton("⬅️ پنل مدیریت",callback_data="adm:stats")]); await q.message.edit_text("👥 <b>تمام کاربران</b>\n\nروی هر کاربر بزن تا پرونده کاملش باز شود.",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb)); return
    if a=="search": context.user_data["admin_tool_mode"]="search"; await q.message.reply_text("🔎 شناسه یا نام کاربر را بفرست:",reply_markup=nav_keyboard(uid)); return
    if a=="tools": context.user_data["admin_tool_mode"]="tools"; await q.message.reply_text("🧰 دستورات: BLOCK:ID | UNBLOCK:ID | WARN:ID | XP:ID:50 | VIP:ID:30",reply_markup=nav_keyboard(uid)); return
    if a=="xpvip":
        await q.message.edit_text("⭐ <b>XP / VIP</b>\n\nاز بخش کاربران، پرونده هر کاربر را باز کن تا XP و اشتراک را مدیریت کنی.\n\nبرای اشتراک: ➕ اضافه‌کردن روز، ➖ کم‌کردن روز، ✏️ ویرایش یا ❌ لغو کامل.",parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👥 کاربران",callback_data="adm:users")],[InlineKeyboardButton("⚙️ امکانات VIP",callback_data="adm:features")],[InlineKeyboardButton("⬅️ پنل مدیریت",callback_data="adm:stats")]])); return
    if a=="features":
        await q.message.edit_text(feature_admin_text(),reply_markup=feature_admin_keyboard()); return
    if a=="costs":
        await q.message.edit_text(admin_costs_text(),parse_mode="HTML",reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 بروزرسانی",callback_data="adm:costs")],
            [InlineKeyboardButton("⚙️ مدیریت دسترسی قابلیت‌ها",callback_data="adm:features")],
            [InlineKeyboardButton("👥 ظرفیت/کاربران",callback_data="adm:capacity")],
            [InlineKeyboardButton("⬅️ پنل مدیریت",callback_data="adm:stats")]
        ])); return
    if a=="capacity":
        await q.message.edit_text(admin_capacity_text(),parse_mode="HTML",reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 داشبورد",callback_data="adm:stats")],
            [InlineKeyboardButton("💰 هزینه/سرویس‌ها",callback_data="adm:costs")],
            [InlineKeyboardButton("⬅️ پنل مدیریت",callback_data="adm:stats")]
        ])); return
    if a=="pause":
        until=get_system_setting("bot_paused_until","")
        paused=False
        if until:
            try: paused=datetime.now(TZ)<datetime.fromisoformat(until)
            except Exception: paused=False
        if paused:
            set_system_setting("bot_paused_until","")
            admin_log(uid,"bot_pause_off",None,until)
            await q.message.edit_text("▶️ توقف موقت لغو شد و ربات دوباره فعال است.",reply_markup=final_admin_keyboard())
        else:
            context.user_data["admin_pause_mode"]=True
            await q.message.reply_text("⏸ مدت توقف را به دقیقه بفرست. مثال: 60\nبرای توقف نامحدود بنویس: forever",reply_markup=nav_keyboard(uid))
        return
    if a=="main":
        await q.message.edit_text("🏠 منوی اصلی")
        await q.message.reply_text("🏠 منوی اصلی",reply_markup=keyboard(uid))
        return
    if a=="channel": await q.message.edit_text("📡 مدیریت کانال و پست‌گذاری",reply_markup=channel_keyboard()); return
    if a=="customers":
        c=db()
        total=c.execute("SELECT COUNT(*) n FROM customers").fetchone()["n"]
        appts=c.execute("SELECT COUNT(*) n FROM appointments").fetchone()["n"]
        today=datetime.now(TZ).date().isoformat()
        today_n=c.execute("SELECT COUNT(*) n FROM appointments WHERE appointment_date=?",(today,)).fetchone()["n"]
        c.close()
        keys=FEATURE_CATEGORIES["customers"][1]
        kb=[]
        for i in range(0,len(keys),2):
            row=[]
            for key in keys[i:i+2]:
                mode=feature_access_mode(key)
                row.append(InlineKeyboardButton(
                    f"{feature_mode_label(mode)} {FEATURE_LABELS_FA.get(key,key)}",
                    callback_data=f"feat:{key}"
                ))
            kb.append(row)
        kb.append([InlineKeyboardButton("🔄 بازخوانی وضعیت",callback_data="adm:customers")])
        kb.append([InlineKeyboardButton("⬅️ پنل مدیریت",callback_data="adm:stats")])
        await q.message.edit_text(
            f"👥 <b>مدیریت مشتری و نوبت‌دهی</b>\n\n"
            f"👤 مشتریان ثبت‌شده: {total}\n"
            f"📅 کل نوبت‌ها: {appts}\n"
            f"🌅 نوبت امروز: {today_n}\n\n"
            "هر گزینه مستقل است. با زدن هر گزینه وضعیتش بین 🟢 رایگان، 💎 VIP / پولی و 🔴 غیرفعال تغییر می‌کند.",
            parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb)
        ); return
    if a=="tickets":
        c=db(); rows=c.execute("SELECT id,user_id,subject FROM tickets WHERE status='open' ORDER BY updated_at DESC LIMIT 20").fetchall(); c.close(); await q.message.edit_text("🎫 تیکت‌های باز\n\n"+"\n".join(f"#{r['id']} | {r['user_id']} | {r['subject'] or 'بدون عنوان'}" for r in rows) or "تیکت بازی نیست",reply_markup=final_admin_keyboard()); return
    if a=="health":
        await run_health_checks(context.bot,uid)
        await q.message.edit_text(health_text(),reply_markup=final_admin_keyboard())
        return
    if a=="health_schedule":
        enabled = get_system_setting("health_check_enabled", "1") != "0"
        schedule = get_system_setting("health_check_time", "03:00")
        status = "🟢 روشن" if enabled else "🔴 خاموش"
        kb = [
            [InlineKeyboardButton("⏰ تغییر ساعت", callback_data="adm:health_time")],
            [InlineKeyboardButton("🔴 خاموش کردن" if enabled else "🟢 روشن کردن", callback_data="adm:health_toggle")],
            [InlineKeyboardButton("🩺 اجرای همین الان", callback_data="adm:health_run")],
            [InlineKeyboardButton("⬅️ پنل مدیریت", callback_data="adm:stats")],
        ]
        await q.message.edit_text(
            f"⏰ <b>زمان‌بندی Health Check</b>\n\n"
            f"وضعیت: {status}\n"
            f"ساعت روزانه: <b>{html.escape(schedule)}</b>\n\n"
            "ربات فقط یک‌بار در همان روز و در ساعت انتخاب‌شده چکاپ می‌گیرد.\n"
            "ساعت را با قالب 24 ساعته مثل <code>14:30</code> وارد کن.",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb)
        )
        return
    if a=="health_time":
        context.user_data["admin_health_time"] = True
        await q.message.reply_text("⏰ ساعت جدید چکاپ را بفرست. مثال: 14:30", reply_markup=nav_keyboard(uid))
        return
    if a=="health_toggle":
        enabled = get_system_setting("health_check_enabled", "1") != "0"
        set_system_setting("health_check_enabled", "0" if enabled else "1")
        await q.message.edit_text("⏰ زمان‌بندی چکاپ تغییر کرد.", reply_markup=final_admin_keyboard())
        return
    if a=="health_run":
        await run_health_checks(context.bot,uid)
        await q.message.edit_text(health_text()+"\n\n🩺 چکاپ دستی انجام شد.", reply_markup=final_admin_keyboard())
        return
    if a=="test":
        await q.message.edit_text(_admin_test_text(),parse_mode="HTML",reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🩺 اجرای Health Check",callback_data="adm:health_run")],
            [InlineKeyboardButton("🔎 اجرای عیب‌یابی",callback_data="adm:diagnostics")],
            [InlineKeyboardButton("⬅️ پنل مدیریت",callback_data="adm:stats")]
        ])); return
    if a=="diagnostics":
        await q.message.edit_text(_admin_diagnostics_text(),parse_mode="HTML",reply_markup=final_admin_keyboard()); return
    if a=="backup":
        ok=backup_database_snapshot(keep=20)
        admin_log(uid,"manual_backup",None,"success" if ok else "failed")
        await q.message.edit_text(
            "💾 بکاپ با موفقیت ساخته شد." if ok else "❌ ساخت بکاپ ناموفق بود.",
            reply_markup=final_admin_keyboard()
        ); return
    if a=="audit":
        c=db()
        rows=c.execute("SELECT admin_id,action,target_user,details,created_at FROM admin_logs ORDER BY id DESC LIMIT 25").fetchall()
        c.close()
        text="📝 <b>آخرین اقدامات مدیران</b>\n\n"+("\n".join(
            f"• {r['created_at'][:16]} | {r['admin_id']} | {html.escape(r['action'])} | {r['target_user'] or '-'} | {html.escape(r['details'] or '')}"
            for r in rows
        ) or "لاگی ثبت نشده.")
        await q.message.edit_text(text,parse_mode="HTML",reply_markup=final_admin_keyboard()); return
    if a=="report": await build_daily_report(); await q.message.edit_text(get_daily_report_text(),reply_markup=final_admin_keyboard()); return
    if a=="broadcast": context.user_data["admin_broadcast"]=True; await q.message.reply_text("📢 متن پیام را بفرست:",reply_markup=nav_keyboard(uid)); return


FEATURE_LABELS_FA = {
    "ai": "🤖 هوش مصنوعی", "vip": "💎 VIP", "reminders": "⏰ یادآوری",
    "sports": "⚽ ورزش", "nutrition": "🥗 تغذیه", "investing": "💰 سرمایه‌گذاری",
    "self_growth": "🌱 رشد شخصی", "morning": "☀️ پیام صبح", "night": "🌙 پیام شب",
    "auto_publish": "🤖 انتشار خودکار", "images": "🖼 تصاویر", "feedback": "👍 بازخورد",
    "referrals": "🤝 دعوت دوستان", "mini_app": "📱 Mini App", "support": "🎫 پشتیبانی",
    "price_data": "📈 قیمت آنلاین", "approval": "👁 تأیید قبل از انتشار",
    "maintenance": "🛠 حالت تعمیرات", "test_mode": "🧪 تست ۷ روزه", "payments": "💳 پرداخت",
    "customer_today": "📅 نوبت‌های امروز", "customer_new_appointment": "➕ نوبت جدید",
    "customer_customers": "👥 مشتریان", "customer_calendar": "🗓️ تقویم کاری",
    "customer_hours": "⏰ ساعات کاری", "customer_reminders": "🔔 یادآوری‌های مشتری",
    "customer_analytics": "📊 آمار مشتریان", "customer_loyal": "🏆 مشتریان وفادار",
    "customer_period": "📆 گزارش دوره‌ای", "customer_booking_link": "🔗 لینک رزرو آنلاین",
    "customer_online_booking": "🌐 رزرو آنلاین", "customer_business_settings": "⚙️ تنظیمات کسب‌وکار",
    "goals": "🎯 اهداف", "weekly": "📅 جدول هفتگی", "stats": "📊 آمار من", "profile": "👤 پروفایل", "achievements": "🏆 دستاوردها", "settings": "⚙️ تنظیمات", "customers": "👥 مشتری و نوبت‌دهی",
}

def get_system_setting(key, default=""):
    c=db()
    try:
        r=c.execute("SELECT value FROM system_settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default
    finally:
        c.close()

def set_system_setting(key, value):
    c=db()
    try:
        c.execute("""INSERT INTO system_settings(key,value,updated_at) VALUES(?,?,?)
                     ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                  (key, str(value), datetime.now(TZ).isoformat()))
        c.commit()
    finally:
        c.close()

def test_mode_active():
    if not feature_enabled("test_mode"):
        return False
    raw=get_system_setting("test_mode_started_at","")
    if not raw:
        set_system_setting("test_mode_started_at", datetime.now(TZ).isoformat())
        return True
    try:
        started=datetime.fromisoformat(raw)
        if started.tzinfo is None: started=started.replace(tzinfo=TZ)
        if datetime.now(TZ)-started >= timedelta(days=7):
            return False
        return True
    except Exception:
        set_system_setting("test_mode_started_at", datetime.now(TZ).isoformat())
        return True

def test_mode_remaining():
    raw=get_system_setting("test_mode_started_at","")
    if not raw: return "شروع نشده"
    try:
        started=datetime.fromisoformat(raw)
        if started.tzinfo is None: started=started.replace(tzinfo=TZ)
        left=started+timedelta(days=7)-datetime.now(TZ)
        if left.total_seconds() <= 0: return "پایان یافته"
        return f"{left.days} روز و {left.seconds//3600} ساعت"
    except Exception:
        return "نامشخص"

FEATURE_CATEGORIES={
    "goals":("🎯 تنظیمات اهداف",["goals","weekly","stats","profile","achievements","reminders","morning","night"]),
    "customers":("👥 تنظیمات مشتری و نوبت‌دهی",["customers","customer_today","customer_new_appointment","customer_customers","customer_calendar","customer_hours","customer_reminders","customer_analytics","customer_loyal","customer_period","customer_booking_link","customer_online_booking","customer_business_settings"]),
    "channel":("📢 تنظیمات کانال و انتشار",["auto_publish","approval","images","feedback"]),
    "ai":("🤖 تنظیمات هوش مصنوعی و ابزارها",["ai","price_data"]),
    "engagement":("⭐ تنظیمات XP / VIP / دعوت",["xp","vip","referrals","payments"]),
    "support":("🎫 تنظیمات پشتیبانی و سیستم",["support","mini_app","maintenance","test_mode"]),
    "vipbuilder":("💎 سازنده امکانات VIP",["vip","ai","price_data","goals","weekly","stats","customers","customer_online_booking","auto_publish","approval","support","referrals"]),
}

def feature_mode_label(mode):
    return {"free":"🟢 رایگان","vip":"💎 VIP / پولی","off":"🔴 غیرفعال"}.get(mode,"🟢 رایگان")


def set_feature_access_mode(key, mode, admin_id=0):
    if mode not in ("free","vip","off"):
        mode="free"
    now=datetime.now(TZ).isoformat()
    c=db()
    c.execute("INSERT INTO feature_access(key,mode,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET mode=excluded.mode,updated_at=excluded.updated_at",(key,mode,now))
    c.execute("INSERT INTO feature_flags(key,enabled,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at",(key,0 if mode=="off" else 1,now))
    c.commit(); c.close()
    if admin_id:
        admin_log(admin_id,"feature_access_change",None,f"{key}={mode}")


def feature_admin_keyboard():
    buttons=[[InlineKeyboardButton(label,callback_data=f"fcat:{key}")] for key,(label,_) in FEATURE_CATEGORIES.items()]
    buttons.append([InlineKeyboardButton("🔧 همه قابلیت‌ها",callback_data="fcat:all")])
    buttons.append([InlineKeyboardButton("⬅️ پنل مدیریت",callback_data="adm:stats")])
    return InlineKeyboardMarkup(buttons)


async def feature_category_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔",show_alert=True); return
    await q.answer(); cat=q.data.split(":",1)[1]
    c=db(); all_keys=[r["key"] for r in c.execute("SELECT key FROM feature_flags ORDER BY key").fetchall()]; c.close()
    keys=all_keys if cat=="all" else FEATURE_CATEGORIES.get(cat,("",[]))[1]
    kb=[]
    for i in range(0,len(keys),2):
        row=[]
        for key in keys[i:i+2]:
            mode=feature_access_mode(key)
            row.append(InlineKeyboardButton(f"{feature_mode_label(mode)} {FEATURE_LABELS_FA.get(key,key)}",callback_data=f"feat:{key}"))
        if row: kb.append(row)
    back_cat="adm:features"
    kb.append([InlineKeyboardButton("⬅️ دسته‌های تنظیمات",callback_data=back_cat)])
    title=FEATURE_CATEGORIES.get(cat,("⚙️ همه قابلیت‌ها",[]))[0] if cat!="all" else "⚙️ همه قابلیت‌ها"
    await q.message.edit_text(f"⚙️ <b>{title}</b>\n\n🟢 رایگان = همه کاربران\n💎 VIP / پولی = فقط VIP\n🔴 غیرفعال = پنهان و غیرقابل استفاده\n\nبا زدن هر قابلیت، وضعیت آن بین این سه حالت تغییر می‌کند.",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))


def feature_admin_text():
    c=db(); rows=c.execute("SELECT key,mode FROM feature_access ORDER BY key").fetchall(); c.close()
    free=sum(1 for r in rows if r["mode"]=="free"); vip=sum(1 for r in rows if r["mode"]=="vip"); off=sum(1 for r in rows if r["mode"]=="off")
    return ("⚙️ <b>مرکز تنظیمات قابلیت‌ها</b>\n\n"
            f"🟢 رایگان: {free}\n💎 VIP / پولی: {vip}\n🔴 غیرفعال: {off}\n\n"
            "هر دسته تنظیمات مستقل خودش را دارد و خاموش‌کردن قابلیت، اطلاعات قبلی را حذف نمی‌کند.")

async def feature_info_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid):
        await q.answer("⛔",show_alert=True); return
    await q.answer()
    await q.message.edit_text(
        "🧪 تست ۷ روزه\n\n"
        f"وضعیت: {'🟢 فعال' if test_mode_active() else '🔴 پایان یافته/خاموش'}\n"
        f"زمان باقی‌مانده: {test_mode_remaining()}\n\n"
        "در زمان تست، انتشار خودکار قبل از انتشار نهایی برای Admin پیش‌نمایش می‌شود.",
        reply_markup=feature_admin_keyboard()
    )

async def final_feature_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔",show_alert=True); return
    key=q.data.split(":",1)[1]
    if key == "test":
        await q.answer(); await q.message.edit_text("🧪 تست ۷ روزه\n\n" f"وضعیت: {'🟢 فعال' if test_mode_active() else '🔴 پایان یافته/خاموش'}\n" f"زمان باقی‌مانده: {test_mode_remaining()}",reply_markup=feature_admin_keyboard()); return
    current=feature_access_mode(key)
    new_mode={"free":"vip","vip":"off","off":"free"}.get(current,"free")
    set_feature_access_mode(key,new_mode,uid)
    await q.answer(feature_mode_label(new_mode))
    # Return to the exact category containing this feature, not the global list.
    category="all"
    for cat,(_,keys) in FEATURE_CATEGORIES.items():
        if key in keys:
            category=cat; break
    if category=="all":
        c=db(); keys=[r["key"] for r in c.execute("SELECT key FROM feature_flags ORDER BY key").fetchall()]; c.close()
    else:
        keys=FEATURE_CATEGORIES[category][1]
    kb=[]
    for i in range(0,len(keys),2):
        row=[]
        for k in keys[i:i+2]:
            row.append(InlineKeyboardButton(f"{feature_mode_label(feature_access_mode(k))} {FEATURE_LABELS_FA.get(k,k)}",callback_data=f"feat:{k}"))
        kb.append(row)
    kb.append([InlineKeyboardButton("⬅️ دسته‌های تنظیمات",callback_data="adm:features")])
    title=FEATURE_CATEGORIES.get(category,("⚙️ همه قابلیت‌ها",[]))[0]
    await q.message.edit_text(f"⚙️ <b>{title}</b>\n\n🟢 رایگان | 💎 VIP / پولی | 🔴 غیرفعال",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def admin_health_time_save(update, context):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS or not context.user_data.get("admin_health_time"):
        return False
    value = update.message.text.strip()
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        await update.message.reply_text("❌ ساعت نامعتبر است. مثال صحیح: 14:30")
        return True
    set_system_setting("health_check_time", value)
    set_system_setting("health_check_enabled", "1")
    context.user_data.pop("admin_health_time", None)
    await update.message.reply_text(
        f"✅ زمان چکاپ روزانه روی <b>{html.escape(value)}</b> تنظیم شد.",
        parse_mode="HTML", reply_markup=final_admin_keyboard()
    )
    return True

async def final_admin_text(update,context):
    uid=update.effective_user.id
    if uid not in ADMIN_IDS or not context.user_data.get("admin_tool_mode"): return False
    mode=context.user_data.pop("admin_tool_mode"); text=update.message.text.strip()
    try:
        if mode=="search":
            c=db(); rows=c.execute("SELECT user_id,first_name,COALESCE(xp,0) xp,blocked,warnings FROM users WHERE CAST(user_id AS TEXT) LIKE ? OR first_name LIKE ? LIMIT 10",(f"%{text}%",f"%{text}%")).fetchall(); c.close()
            await update.message.reply_text("🔎 نتایج:\n\n"+"\n".join(f"{r['first_name']} | {r['user_id']} | XP {r['xp']} | ⚠️{r['warnings']} | {'⛔' if r['blocked'] else '🟢'}" for r in rows) or "یافت نشد",reply_markup=final_admin_keyboard()); return True
        parts=text.split(":"); cmd=parts[0].upper(); target=int(parts[1]); c=db()
        if cmd=="BLOCK": c.execute("UPDATE users SET blocked=1 WHERE user_id=?",(target,)); action="block"
        elif cmd=="UNBLOCK": c.execute("UPDATE users SET blocked=0 WHERE user_id=?",(target,)); action="unblock"
        elif cmd=="WARN": c.execute("UPDATE users SET warnings=COALESCE(warnings,0)+1 WHERE user_id=?",(target,)); action="warn"
        elif cmd=="XP": c.close(); add_xp(target,int(parts[2]),"admin_adjust"); admin_log(uid,"xp_adjust",target,parts[2]); await update.message.reply_text("✅ XP تغییر کرد",reply_markup=final_admin_keyboard()); return True
        elif cmd=="VIP": c.execute("UPDATE users SET vip_until=? WHERE user_id=?",((datetime.now(TZ)+timedelta(days=int(parts[2]))).isoformat(),target)); action="vip_adjust"
        else: c.close(); await update.message.reply_text("❌ دستور نامعتبر"); return True
        c.commit(); c.close(); admin_log(uid,action,target,text); await update.message.reply_text("✅ انجام شد",reply_markup=final_admin_keyboard()); return True
    except Exception as e:
        await update.message.reply_text(f"❌ خطا: {html.escape(str(e))}", parse_mode="HTML"); return True

async def navigation_callback(update,context):
    q=update.callback_query; uid=q.from_user.id; await q.answer(); action=q.data.split(":",1)[1] if ":" in (q.data or "") else ""; clear_flow(context)
    if action=="main":
        # Main Menu must work from every inline error/recovery screen without
        # deleting the only visible bot message. Render the compact root in-place.
        try:
            fa = lang(uid) == "fa"
            root_text = "🏠 <b>منوی اصلی</b>\n\nیک بخش را انتخاب کن." if fa else "🏠 <b>Main Menu</b>\n\nChoose a section."
            await q.message.edit_text(root_text, parse_mode="HTML", reply_markup=_compact_root_inline(uid))
        except Exception:
            # Last-resort fallback: keep the reply keyboard available.
            try:
                await q.message.reply_text("🏠 منوی اصلی", reply_markup=keyboard(uid))
            except Exception:
                pass
        return

def support_keyboard(uid):
    fa=lang(uid)=="fa"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❓ سوالات متداول" if fa else "❓ FAQ", callback_data="support:faq")],
        [InlineKeyboardButton("📝 ارسال تیکت" if fa else "📝 New Ticket", callback_data="support:new")],
        [InlineKeyboardButton("🏠 منوی اصلی" if fa else "🏠 Main Menu", callback_data="support:main")],
    ])

async def support_start(update,context):
    uid=update.effective_user.id; clear_flow(context);
    await hide_main_reply_keyboard(update)
    await update.message.reply_text("🎫 پشتیبانی\n\nیک گزینه را انتخاب کن:" if lang(uid)=="fa" else "🎫 Support\n\nChoose an option:",reply_markup=support_keyboard(uid))

async def support_callback(update,context):
    q=update.callback_query; uid=q.from_user.id; await q.answer(); action=q.data.split(":",1)[1]
    if action=="main": clear_flow(context); await q.message.reply_text("🏠 منوی اصلی",reply_markup=keyboard(uid)); return
    if action=="faq":
        text=("❓ سوالات متداول\n\n• چطور هدف اضافه کنم؟ از «✏️ هدف خودم می‌نویسم» استفاده کن.\n• چطور زمان یادآوری را عوض کنم؟ از «✏️ ویرایش اهداف».\n• چطور AI را فعال کنم؟ اگر سرویس هوشمند در دسترس نباشد، مدیر می‌تواند اتصال سرویس‌های AI را از تنظیمات سرور بررسی کند.\n• چطور کانال را وصل کنم؟ مدیر ← مدیریت کانال ← تنظیم کانال.")
        await q.message.edit_text(text,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ پشتیبانی",callback_data="support:main")],[main_menu_button(uid)]])); return
    if action=="new":
        context.user_data["support_new"]=True; await q.message.reply_text("📝 پیام پشتیبانی را بفرست. برای لغو «⬅️ برگشت» را بزن.",reply_markup=nav_keyboard(uid)); return

async def support_text(update,context):
    if not context.user_data.get("support_new"): return False
    uid=update.effective_user.id; text=update.message.text.strip()
    if text in ("⬅️ برگشت","⬅️ Back","🏠 منوی اصلی","🏠 Main Menu"):
        clear_flow(context); await update.message.reply_text("🏠 منوی اصلی",reply_markup=keyboard(uid)); return True
    now=datetime.now(TZ).isoformat(); c=db(); cur=c.execute("INSERT INTO tickets(user_id,subject,created_at,updated_at) VALUES(?,?,?,?)",(uid,text[:80],now,now)); c.execute("INSERT INTO ticket_messages(ticket_id,sender_id,message,created_at) VALUES(?,?,?,?)",(cur.lastrowid,uid,text,now)); c.commit(); c.close(); context.user_data.pop("support_new",None); await update.message.reply_text(f"🎫 تیکت #{cur.lastrowid} ثبت شد.",reply_markup=keyboard(uid)); return True

def vip_keyboard(uid):
    fa=lang(uid)=="fa"
    rows=[]
    if feature_enabled("payments") and feature_enabled("vip"):
        rows.append([InlineKeyboardButton("💎 خرید VIP — 100 ⭐ / 30 روز" if fa else "💎 Buy VIP — 100 ⭐ / 30 days",callback_data="vip:buy")])
    rows.append([InlineKeyboardButton("🤝 دعوت دوستان" if fa else "🤝 Referrals",callback_data="vip:ref")])
    rows.append([InlineKeyboardButton("🏠 منوی اصلی" if fa else "🏠 Main Menu",callback_data="vip:main")])
    return InlineKeyboardMarkup(rows)

async def vip_center(update,context):
    uid=update.effective_user.id; xp,level,vip_until=xp_info(uid)
    fa=lang(uid)=="fa"
    status="🟢 فعال" if is_vip(uid) else "⚪ رایگان"
    text=(f"💎 VIP\n\nوضعیت: {status}\n⭐ سطح: {level}\n🕐 پایان VIP: {vip_until[:16] if vip_until else '—'}\n\nامکانات VIP: سهمیه بیشتر AI و قابلیت‌های پولی فعال‌شده توسط مدیر." if fa else f"💎 VIP\n\nStatus: {status}\n⭐ Level: {level}\n🕐 VIP until: {vip_until[:16] if vip_until else '—'}\n\nVIP includes higher AI quota and paid features enabled by the admin.")
    await update.message.reply_text(text,reply_markup=vip_keyboard(uid))

async def vip_callback(update,context):
    q=update.callback_query; uid=q.from_user.id; await q.answer(); action=q.data.split(":",1)[1]
    if action=="main": clear_flow(context); await q.message.reply_text("🏠 منوی اصلی",reply_markup=keyboard(uid)); return
    if action=="ref": await referral(update,context); return
    if action=="buy":
        if not (feature_enabled("payments") and feature_enabled("vip")):
            await q.message.reply_text("💎 خرید VIP فعلاً توسط مدیر غیرفعال است.",reply_markup=vip_keyboard(uid)); return
        payload=f"vip30:{uid}:{int(datetime.now(TZ).timestamp())}"
        try:
            await context.bot.send_invoice(chat_id=uid,title="MyTasks VIP 30 روزه",description="فعال‌سازی VIP ربات MyTasks برای ۳۰ روز",payload=payload,provider_token="",currency="XTR",prices=[LabeledPrice("VIP 30 روزه",100)],start_parameter="mytasks-vip-30")
        except Exception as e:
            logger.error("VIP invoice failed: %s",e); await q.message.reply_text("❌ ساخت فاکتور VIP انجام نشد. تنظیمات پرداخت را بررسی کن.",reply_markup=vip_keyboard(uid))

async def precheckout_callback(update,context):
    q=update.pre_checkout_query
    if not q.invoice_payload.startswith("vip30:") or not feature_enabled("payments"):
        await q.answer(ok=False,error_message="این خرید در حال حاضر فعال نیست.")
        return
    await q.answer(ok=True)

async def successful_payment_callback(update,context):
    payment=update.message.successful_payment; uid=update.effective_user.id
    charge_id=(payment.telegram_payment_charge_id or '').strip()
    if not charge_id:
        await update.message.reply_text("❌ شناسه پرداخت معتبر نیست.",reply_markup=keyboard(uid)); return
    c=db()
    try:
        c.execute("BEGIN IMMEDIATE")
        now_iso=datetime.now(TZ).isoformat()
        cur=c.execute("INSERT OR IGNORE INTO payments(user_id,payload,currency,total_amount,telegram_charge_id,created_at) VALUES(?,?,?,?,?,?)",(uid,payment.invoice_payload,payment.currency,payment.total_amount,charge_id,now_iso))
        if cur.rowcount != 1:
            c.rollback(); c.close(); await update.message.reply_text("ℹ️ این پرداخت قبلاً ثبت شده است.",reply_markup=keyboard(uid)); return
        base=datetime.now(TZ); r=c.execute("SELECT vip_until FROM users WHERE user_id=?",(uid,)).fetchone()
        if r and r["vip_until"]:
            try: base=max(base,datetime.fromisoformat(r["vip_until"]))
            except Exception: pass
        until=base+timedelta(days=30)
        c.execute("UPDATE users SET vip_until=? WHERE user_id=?",(until.isoformat(),uid))
        c.execute("INSERT INTO subscription_history(user_id,plan,duration_days,source,amount,started_at,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?)",(uid,"VIP",30,"telegram_stars",payment.total_amount,now_iso,until.isoformat(),now_iso))
        c.commit(); c.close(); add_xp(uid,20,"vip_purchase")
        await update.message.reply_text(f"✅ پرداخت موفق بود. VIP تا {fa_datetime(until)} فعال شد.",reply_markup=keyboard(uid))
    except Exception:
        try: c.rollback(); c.close()
        except Exception: pass
        logger.exception("Successful payment handling failed")
        await update.message.reply_text("❌ ثبت پرداخت انجام نشد.",reply_markup=keyboard(uid))

async def referral(update,context):
    uid=update.effective_user.id
    c=db()
    try:
        r=c.execute("SELECT referral_code FROM users WHERE user_id=?",(uid,)).fetchone(); n=c.execute("SELECT COUNT(*) n FROM referrals WHERE inviter_id=?",(uid,)).fetchone()["n"]
    finally: c.close()
    code=r["referral_code"] if r and r["referral_code"] else secrets.token_urlsafe(12)
    c=db(); c.execute("UPDATE users SET referral_code=? WHERE user_id=?",(code,uid)); c.commit(); c.close()
    # Referral VIP rewards are idempotent: viewing the referral page must never
    # grant the same 10-referral milestone more than once.
    if feature_enabled("referrals") and n>0 and n%10==0:
        milestone = n // 10
        reward_key = f"referral_vip:{uid}:{milestone}"
        c=db()
        try:
            cur=c.execute("INSERT OR IGNORE INTO reward_log(reward_key,user_id,reward_type,amount,created_at) VALUES(?,?,?,?,?)",
                          (reward_key,uid,"referral_vip_days",30,datetime.now(TZ).isoformat()))
            if cur.rowcount == 1:
                r=c.execute("SELECT vip_until FROM users WHERE user_id=?",(uid,)).fetchone(); base=datetime.now(TZ)
                if r and r["vip_until"]:
                    try: base=max(base,datetime.fromisoformat(r["vip_until"]))
                    except Exception: pass
                new_until=base+timedelta(days=30)
                c.execute("UPDATE users SET vip_until=? WHERE user_id=?",(new_until.isoformat(),uid))
                c.commit()
            else:
                c.rollback()
        finally:
            c.close()
    me=await context.bot.get_me(); link=f"https://t.me/{me.username}?start=ref_{code}" if me.username else code
    reward=int(token_setting("referral_tokens_per_success","10") or 10)
    next_count=((n//10)+1)*10
    await update.message.reply_text(
        f"🤝 <b>دعوت دوستان</b>\n\n"
        f"🔗 لینک اختصاصی تو:\n<code>{html.escape(link)}</code>\n\n"
        f"👥 دعوت موفق: <b>{n}</b> نفر\n"
        f"🎁 پاداش دریافت‌شده: <b>{n*reward}</b> توکن\n"
        f"🎁 هر دعوت موفق: <b>{reward}</b> توکن\n"
        f"💎 هر ۱۰ دعوت موفق = ۳۰ روز VIP\n"
        f"📈 تا پاداش VIP بعدی: <b>{max(0,next_count-n)}</b> دعوت",
        parse_mode="HTML")

def prices_keyboard(uid):
    fa=lang(uid)=="fa"
    labels=[("usd","💵 دلار" if fa else "💵 USD"),("eur","💶 یورو" if fa else "💶 EUR"),("gold18","🪙 طلای ۱۸" if fa else "🪙 18K Gold"),("coin","🪙 سکه امامی" if fa else "🪙 Coin"),("btc","₿ BTC"),("eth","Ξ ETH"),("usdt","💵 USDT"),("bnb","🟡 BNB"),("sol","🟣 SOL"),("xrp","⚡ XRP"),("sp500","📊 S&P 500"),("nasdaq","📊 Nasdaq"),("dow","📊 Dow Jones")]
    rows=[]
    for i in range(0,len(labels),2): rows.append([InlineKeyboardButton(a,callback_data=f"price:{k}") for k,a in labels[i:i+2]])
    rows.append([InlineKeyboardButton("🔄 بروزرسانی همه" if fa else "🔄 Refresh all",callback_data="price:all")])
    rows.append([InlineKeyboardButton("🏠 منوی اصلی" if fa else "🏠 Main Menu",callback_data="price:main")])
    return InlineKeyboardMarkup(rows)

async def fetch_url_json(url):
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 MyTasksBot/1.0"})
    with urllib.request.urlopen(req,timeout=15) as r: return json.loads(r.read().decode("utf-8"))

def fetch_url_json_post(url, payload):
    body=json.dumps(payload).encode("utf-8")
    req=urllib.request.Request(url,data=body,headers={"Content-Type":"application/json","User-Agent":"MyTasksBot/1.0"},method="POST")
    with urllib.request.urlopen(req,timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def tgju_value(url):
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req,timeout=15) as r: html=r.read().decode("utf-8","ignore")
    # Common TGJU price page pattern: the first numeric market value in the page.
    # Prefer the page's explicit current-price field; generic first-number scraping can hit unrelated values.
    plain=re.sub(r"<[^>]+>"," ",html)
    plain=re.sub(r"\s+"," ",plain)
    current=re.search(r'(?:نرخ\s*فعلی|Last)\s*:?[\s:=]*([0-9][0-9,٫٬]*)',plain,re.I)
    if current: return current.group(1)
    vals=re.findall(r'<span[^>]*class=["\'][^"\']*(?:price|value)[^"\']*["\'][^>]*>\s*([0-9,٫٬]+)',html,re.I)
    if not vals: vals=re.findall(r'([0-9]{1,3}(?:,[0-9]{3})+)',html)
    if not vals: raise ValueError("price not found")
    return vals[0]

async def fetch_price(asset):
    # BTC/ETH: use the direct Iranian IRT market from Nobitex.
    # The v3 orderbook is the preferred live source; values are Rial and
    # are converted to Toman exactly once. Stats/trades are fallbacks.
    if asset in ("btc", "eth"):
        symbol = f"{asset.upper()}IRT"
        try:
            data = await asyncio.to_thread(
                fetch_url_json,
                f"https://api.nobitex.ir/v3/orderbook/{symbol}",
            )
            last_trade = data.get("lastTradePrice")
            if last_trade is None:
                raise ValueError("lastTradePrice missing")
            return f"{float(last_trade):,.0f} ریال"
        except Exception as e:
            logger.warning("Nobitex v3 orderbook %s failed: %s", symbol, e)
        try:
            data = await asyncio.to_thread(
                fetch_url_json,
                f"https://api.nobitex.ir/v2/trades/{symbol}",
            )
            trades = data.get("trades") or []
            if trades:
                return f"{float(trades[0]['price']):,.0f} ریال"
            raise ValueError("no trades")
        except Exception as e:
            logger.warning("Nobitex trades %s failed: %s", symbol, e)
        try:
            data = await asyncio.to_thread(
                fetch_url_json_post,
                "https://api.nobitex.ir/market/stats",
                {"srcCurrency": asset, "dstCurrency": "rls"},
            )
            latest = data.get("stats", {}).get(f"{asset}-rls", {}).get("latest")
            if latest is None:
                raise ValueError("latest price missing")
            return f"{float(latest):,.0f} ریال"
        except Exception as e:
            logger.warning("Nobitex stats %s failed: %s", symbol, e)
            raise
    if asset in ("usdt","bnb","sol","xrp"):
        ids={"usdt":"tether","bnb":"binancecoin","sol":"solana","xrp":"ripple"}
        try:
            data=await asyncio.to_thread(fetch_url_json,"https://api.coingecko.com/api/v3/simple/price?ids="+ids[asset]+"&vs_currencies=usd")
            usd=float(data[ids[asset]]["usd"])
            usd_raw=await asyncio.to_thread(tgju_value,"https://www.tgju.org/profile/price_dollar_rl")
            irr=float(usd_raw.replace(",","").replace("٫",".").replace("٬",""))
            return f"{usd*irr:,.0f} ریال"
        except Exception as e:
            logger.warning("CoinGecko %s failed: %s",asset,e)
            raise
    if asset in ("sp500","nasdaq","dow"):
        symbols={"sp500":"%5EGSPC","nasdaq":"%5EIXIC","dow":"%5EDJI"}
        data=await asyncio.to_thread(fetch_url_json,f"https://query1.finance.yahoo.com/v8/finance/chart/{symbols[asset]}?range=1d&interval=1m")
        meta=data["chart"]["result"][0]["meta"]
        return f"{meta.get('regularMarketPrice',0):,.2f} USD"
    urls={"usd":"https://www.tgju.org/profile/price_dollar_rl","eur":"https://www.tgju.org/profile/price_eur","gold18":"https://www.tgju.org/profile/geram18","coin":"https://www.tgju.org/profile/sekee"}
    # بدون تبدیل عددی؛ فقط واحد نمایش داده می‌شود.
    try:
        raw = await asyncio.to_thread(tgju_value, urls[asset])
    except Exception as primary_error:
        # Optional secondary source for USD/EUR; never invent a price when both fail.
        if asset in ("usd", "eur"):
            secondary = await v25_bonbast_secondary(asset) if "v25_bonbast_secondary" in globals() else None
            if secondary is not None:
                return f"{float(secondary):,.0f} ریال"
        raise primary_error
    if asset in ("usd", "eur"):
        try:
            secondary = await v25_bonbast_secondary(asset) if "v25_bonbast_secondary" in globals() else None
            normalized = float(raw.replace(",", "").replace("٫", ".").replace("٬", ""))
            if secondary is not None and normalized:
                # If sources are within 1%, average to reduce transient source noise.
                if abs(float(secondary)-normalized)/max(abs(normalized),1) <= 0.01:
                    normalized=(normalized+float(secondary))/2
        except Exception:
            normalized = float(raw.replace(",", "").replace("٫", ".").replace("٬", ""))
        return f"{normalized:,.0f} ریال"
    if asset in ("gold18", "coin"):
        normalized = raw.replace(",", "").replace("٫", ".").replace("٬", "")
        return f"{float(normalized):,.0f} ریال"
    return raw

async def price_callback(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; asset=q.data.split(":",1)[1]
    if asset=="main": await q.message.reply_text("🏠 منوی اصلی",reply_markup=keyboard(uid)); return
    names={"usd":"دلار","eur":"یورو","gold18":"طلای ۱۸ عیار","coin":"سکه امامی","btc":"BTC (بازار ایران)","eth":"ETH (بازار ایران)","usdt":"USDT","bnb":"BNB","sol":"Solana","xrp":"XRP","sp500":"S&P 500","nasdaq":"Nasdaq","dow":"Dow Jones"}
    assets=list(names) if asset=="all" else [asset]
    lines=["📈 قیمت آنلاین", f"🕒 بروزرسانی: {fa_datetime(datetime.now(TZ), True)}", ""]
    for a in assets:
        try: lines.append(f"{names[a]}: {await fetch_price(a)}")
        except Exception as e: lines.append(f"{names[a]}: ❌ دریافت نشد") ; logger.warning("Price %s failed: %s",a,e)
    try:
        await q.message.edit_text("\n".join(lines),reply_markup=prices_keyboard(uid))
    except Exception:
        await q.message.reply_text("\n".join(lines),reply_markup=prices_keyboard(uid))

async def prices(update,context):
    uid=update.effective_user.id
    await hide_main_reply_keyboard(update)
    await update.message.reply_text("📈 قیمت آنلاین\n\nیکی را انتخاب کن:",reply_markup=prices_keyboard(uid))

def _record_service_event(service,status,details=""):
    try:
        c=db()
        c.execute(
            "INSERT INTO service_events(service,status,details,created_at) VALUES(?,?,?,?)",
            (str(service),str(status),str(details)[:1000],datetime.now(TZ).isoformat())
        )
        c.commit(); c.close()
    except Exception:
        pass

def _n8n_ai_fallback_sync(prompt):
    """Call the configured n8n AI workflow.
    n8n itself owns the OpenAI credential; Railway does not need OPENAI_API_KEY
    when this workflow is configured. Only the user prompt is sent.
    """
    if not AI_FAILOVER_TO_N8N or not _secure_remote_base(N8N_WEBHOOK_URL):
        return None
    payload=json.dumps({
        "event":"ai_chat",
        "prompt":str(prompt)[:8000],
        "model": os.environ.get("N8N_AI_MODEL", "gpt-4o-mini").strip(),
        "messages":[
            {"role":"system","content":"پاسخ کوتاه، مفید، مودبانه و امن بده. اطلاعات حساس یا حدس قطعی ارائه نکن."},
            {"role":"user","content":str(prompt)[:8000]}
        ],
    },ensure_ascii=False).encode("utf-8")
    headers={"Content-Type":"application/json"}
    # Optional shared secret. A standard n8n Webhook does not require one.
    if N8N_API_KEY:
        headers["X-MyTasks-Key"]=N8N_API_KEY
        headers["Authorization"]=f"Bearer {N8N_API_KEY}"
    try:
        req=urllib.request.Request(N8N_WEBHOOK_URL,data=payload,headers=headers,method="POST")
        with urllib.request.urlopen(req,timeout=N8N_TIMEOUT) as resp:
            raw=resp.read().decode("utf-8","replace")
        data=json.loads(raw)
        # n8n Webhook responses commonly arrive either as an object or a one-item array.
        if isinstance(data,list):
            data=data[0] if data and isinstance(data[0],dict) else {}
        answer=""
        if isinstance(data,dict):
            answer=(data.get("output_text") or data.get("answer") or data.get("text")
                    or data.get("output") or data.get("response") or "")
            if not answer and isinstance(data.get("data"),dict):
                nested=data["data"]
                answer=(nested.get("output_text") or nested.get("answer")
                        or nested.get("text") or nested.get("output") or "")
        answer=str(answer).strip()
        if answer:
            _record_service_event("n8n","OK","AI workflow")
            return answer[:4000]
        _record_service_event("n8n","WARN","empty AI workflow response")
    except Exception as exc:
        _record_service_event("n8n","ERROR",type(exc).__name__)
        logger.warning("n8n AI workflow failed: %s", type(exc).__name__)
    return None

def _secure_remote_base(url):
    if not url:
        return False
    low=url.lower()
    # Plain HTTP is accepted only for loopback/private local deployment.
    if low.startswith("https://"):
        return True
    return low.startswith("http://127.0.0.1") or low.startswith("http://localhost")

def omniroute_configured():
    return bool(_secure_remote_base(OMNIROUTE_BASE_URL) and OMNIROUTE_API_KEY)

def _omniroute_root():
    if not omniroute_configured():
        return None
    return OMNIROUTE_BASE_URL[:-3] if OMNIROUTE_BASE_URL.endswith("/v1") else OMNIROUTE_BASE_URL

def _omniroute_api_base():
    root=_omniroute_root()
    return root + "/v1" if root else None

def _omniroute_ai_sync(prompt):
    """Call the self-hosted OmniRoute OpenAI-compatible gateway safely."""
    if not omniroute_configured():
        return None
    payload=json.dumps({
        "model": OMNIROUTE_MODEL,
        "messages":[
            {"role":"system","content":"پاسخ کوتاه، مفید، مودبانه و امن بده. اطلاعات حساس یا حدس قطعی ارائه نکن."},
            {"role":"user","content":str(prompt)[:8000]}
        ],
        "temperature":0.2,
        "max_tokens":500
    },ensure_ascii=False).encode("utf-8")
    try:
        req=urllib.request.Request(
            _omniroute_api_base()+"/chat/completions",
            data=payload,
            headers={
                "Authorization":f"Bearer {OMNIROUTE_API_KEY}",
                "Content-Type":"application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req,timeout=OMNIROUTE_TIMEOUT) as resp:
            data=json.loads(resp.read().decode("utf-8"))
        choices=data.get("choices") or []
        answer=""
        if choices:
            message=choices[0].get("message") or {}
            content=message.get("content","")
            if isinstance(content,list):
                content=" ".join(str(x.get("text","")) for x in content if isinstance(x,dict))
            answer=str(content).strip()
        if answer:
            _record_service_event("omniroute","OK","AI gateway")
            return answer[:4000]
        _record_service_event("omniroute","WARN","empty response")
    except Exception as exc:
        _record_service_event("omniroute","ERROR",type(exc).__name__)
        logger.warning("OmniRoute AI failed: %s", type(exc).__name__)
    return None

def _omniroute_health_sync():
    if not omniroute_configured():
        return False, "OMNIROUTE_BASE_URL/OMNIROUTE_API_KEY تنظیم نشده است."
    try:
        req=urllib.request.Request(
            _omniroute_root() + "/api/health",
            headers={"Authorization":f"Bearer {OMNIROUTE_API_KEY}"},
            method="GET",
        )
        with urllib.request.urlopen(req,timeout=min(OMNIROUTE_TIMEOUT,8)) as resp:
            status=getattr(resp,"status",200)
            body=resp.read(4096).decode("utf-8","replace")
        if status == 200:
            return True, "OmniRoute در دسترس است."
        return False, f"HTTP {status}"
    except Exception as exc:
        return False, f"اتصال برقرار نشد: {type(exc).__name__}"

def ai_provider_diagnostics():
    """Return provider state for admin/health-check use only; never expose secrets."""
    return {
        "omniroute": omniroute_configured(),
        "openai": bool(os.environ.get("OPENAI_API_KEY","").strip()),
        "n8n": n8n_configured(),
        "gemini": bool(GEMINI_API_KEY),
        "text_unified": bool(omniroute_configured() or n8n_configured() or GEMINI_API_KEY or os.environ.get("OPENAI_API_KEY", "").strip()),
        "voice_stt": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
    }

def n8n_configured():
    # A normal n8n Webhook can be secured by its own URL/auth. The shared key
    # is optional so a valid webhook can act as the AI gateway without exposing
    # OPENAI_API_KEY to Railway.
    return bool(_secure_remote_base(N8N_WEBHOOK_URL))

async def ai_chat_start(update,context):
    uid=update.effective_user.id
    if not user_feature_allowed(uid,"ai"):
        await update.message.reply_text("🤖 چت AI فعلاً غیرفعال است." if lang(uid)=="fa" else "🤖 AI Chat is currently disabled.", reply_markup=keyboard(uid)); return
    allowed, reason = feature_token_gate(uid, "ai")
    if not allowed:
        await update.message.reply_text(token_gate_message(uid, "ai", reason), reply_markup=keyboard(uid))
        return
    api_key=os.environ.get("OPENAI_API_KEY","").strip()
    if not api_key and not omniroute_configured() and not n8n_configured() and not GEMINI_API_KEY:
        clear_flow(context)
        await update.message.reply_text(
            "⚠️ در حال حاضر سرویس هوش مصنوعی در دسترس نیست. اگر تمایل داشته باشید، می‌توانید بعداً دوباره تلاش بفرمایید.",
            reply_markup=keyboard(uid),
        )
        return
    clear_flow(context)
    context.user_data["ai_chat"] = True
    # AI is entered from Smart Tools, so Back must return there; Main Menu
    # always goes to the root. Keep this state for both inline and legacy
    # reply-keyboard navigation paths.
    context.user_data["_nav_parent_section"] = "tools"
    await update.message.reply_text(
        "🤖 آماده‌ام. سوالت را بفرست."
        if lang(uid)=="fa" else
        "🤖 Ready. Send your question.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ برگشت" if lang(uid)=="fa" else "⬅️ Back", callback_data="aichat:back"),
            InlineKeyboardButton("🏠 منوی اصلی" if lang(uid)=="fa" else "🏠 Main Menu", callback_data="nav:main"),
        ]]),
    )

async def ai_chat_navigation_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    action = (q.data or "").split(":", 1)[1] if ":" in (q.data or "") else ""
    try:
        await q.answer()
    except Exception:
        pass
    clear_flow(context)
    fa = lang(uid) == "fa"
    if action == "back":
        await q.message.edit_text(
            "🤖 <b>ابزارهای هوشمند</b>" if fa else
            "🤖 <b>Smart Tools</b>",
            parse_mode="HTML",
            reply_markup=_compact_menu_keyboard(uid, "tools"),
        )
        return
    # Main Menu (and any unknown legacy AI navigation action) is a hard root.
    await q.message.edit_text(
        "🏠 <b>منوی اصلی</b>\n\nیک بخش را انتخاب کن." if fa else
        "🏠 <b>Main Menu</b>\n\nChoose a section.",
        parse_mode="HTML",
        reply_markup=_compact_root_inline(uid),
    )


async def ai_chat_text(update,context):
    if not context.user_data.get("ai_chat"): return False
    uid=update.effective_user.id; text=update.message.text.strip()
    if text in ("⬅️ برگشت","⬅️ Back","🏠 منوی اصلی","🏠 Main Menu"):
        clear_flow(context)
        await update.message.reply_text("🏠 منوی اصلی",reply_markup=keyboard(uid))
        return True
    api_key=os.environ.get("OPENAI_API_KEY","").strip()
    if not api_key and not omniroute_configured() and not n8n_configured() and not GEMINI_API_KEY:
        clear_flow(context)
        await update.message.reply_text(
            "⚠️ در حال حاضر دستیار هوشمند موقتاً در دسترس نیست.\n"
            "لطفاً کمی بعد دوباره تلاش بفرمایید. 🌷",
            reply_markup=keyboard(uid),
        )
        return True
    c=db(); c.execute("INSERT OR IGNORE INTO user_settings(user_id) VALUES(?)",(uid,)); r=c.execute("SELECT ai_daily_used,ai_used_date FROM user_settings WHERE user_id=?",(uid,)).fetchone(); today=datetime.now(TZ).date().isoformat(); used=r["ai_daily_used"] if r and r["ai_used_date"]==today else 0; limit=100 if is_vip(uid) else 10
    if used>=limit:
        c.close(); await update.message.reply_text("⛔ سهمیه AI امروز تمام شده است." if lang(uid)=="fa" else "⛔ Your AI quota for today is used up.",reply_markup=nav_keyboard(uid)); return True
    c.close()
    try:
        prompt=(
            "پاسخ کوتاه، مفید، مودبانه و امن به این سوال کاربر بده. "
            "اگر موضوع پزشکی یا مالی است، پاسخ عمومی و غیرقطعی نگه دار.\n\n" + text
        )
        answer=ai_text_generate(prompt, max_output_tokens=500, purpose="chat")
        if not answer:
            raise RuntimeError("No AI provider returned a response")
        c=db()
        c.execute("INSERT OR IGNORE INTO user_settings(user_id) VALUES(?)",(uid,))
        c.execute(
            "UPDATE user_settings SET ai_daily_used=?,ai_used_date=? WHERE user_id=?",
            (used+1,today,uid)
        )
        c.commit(); c.close()
        await update.message.reply_text(answer,reply_markup=nav_keyboard(uid))
    except Exception as e:
        logger.error("AI chat failed: %s",e)
        clear_flow(context)
        await update.message.reply_text(
            (
                "⚠️ متأسفانه در حال حاضر پاسخ هوش مصنوعی دریافت نشد. "
                "لطفاً کمی بعد دوباره تلاش بفرمایید."
            )
            if lang(uid) == "fa" else
            (
                "⚠️ The AI provider did not return a response right now. "
                "Please try again in a little while."
            ),
            reply_markup=keyboard(uid)
        )
    return True


async def build_daily_report():
    d=datetime.now(TZ).date().isoformat()
    c=db()
    data={
        "posts":c.execute("SELECT COUNT(*) n FROM channel_posts WHERE substr(COALESCE(last_sent_at,created_at),1,10)=?",(d,)).fetchone()["n"],
        "active":c.execute("SELECT COUNT(DISTINCT user_id) n FROM activity_log WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "new":c.execute("SELECT COUNT(*) n FROM users WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "xp":c.execute("SELECT COALESCE(SUM(amount),0) n FROM xp_log WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "done":c.execute("SELECT COUNT(*) n FROM goal_days WHERE goal_date=? AND status='done'",(d,)).fetchone()["n"],
        "likes":c.execute("SELECT COUNT(*) n FROM content_feedback WHERE rating=1 AND substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "dislikes":c.execute("SELECT COUNT(*) n FROM content_feedback WHERE rating=-1 AND substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "auto_posts":c.execute("SELECT COUNT(*) n FROM auto_post_history WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "published_posts":c.execute("SELECT COUNT(*) n FROM auto_post_history WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "total_users":c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"],
        "usage_events":c.execute("SELECT COUNT(*) n FROM bot_usage_events WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "usage_users":c.execute("SELECT COUNT(DISTINCT user_id) n FROM bot_usage_events WHERE substr(created_at,1,10)=? AND user_id IS NOT NULL",(d,)).fetchone()["n"],
        "goals_created":c.execute("SELECT COUNT(*) n FROM goals WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "poll_participation":c.execute("SELECT COUNT(DISTINCT user_id) n FROM channel_poll_votes WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "reaction_users":c.execute("SELECT COUNT(DISTINCT user_id) n FROM channel_reactions WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "tickets_created":c.execute("SELECT COUNT(*) n FROM tickets WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "tickets_closed":c.execute("SELECT COUNT(*) n FROM tickets WHERE status IN ('closed','resolved') AND substr(updated_at,1,10)=?",(d,)).fetchone()["n"],
        "ticket_messages":c.execute("SELECT COUNT(*) n FROM ticket_messages WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "payment_count":c.execute("SELECT COUNT(*) n FROM payments WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "paying_users":c.execute("SELECT COUNT(DISTINCT user_id) n FROM payments WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "revenue":c.execute("SELECT COALESCE(SUM(total_amount),0) n FROM payments WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "vip_users":c.execute("SELECT COUNT(*) n FROM users WHERE vip_until IS NOT NULL AND vip_until>?",(datetime.now(TZ).isoformat(),)).fetchone()["n"],
        "normal_users":c.execute("SELECT COUNT(*) n FROM users WHERE vip_until IS NULL OR vip_until<=?",(datetime.now(TZ).isoformat(),)).fetchone()["n"],
        "xp_spent":c.execute("SELECT COALESCE(SUM(-amount),0) n FROM xp_log WHERE amount<0 AND substr(created_at,1,10)=?",(d,)).fetchone()["n"],
    }
    top_usage=c.execute("SELECT event_type,COUNT(*) n FROM bot_usage_events WHERE substr(created_at,1,10)=? GROUP BY event_type ORDER BY n DESC LIMIT 6",(d,)).fetchall()
    data["top_usage"]=[{"event":r["event_type"],"count":r["n"]} for r in top_usage]
    c.execute("INSERT OR REPLACE INTO daily_reports(report_date,data,created_at) VALUES(?,?,?)",(d,json.dumps(data,ensure_ascii=False),datetime.now(TZ).isoformat()))
    c.commit(); c.close()

def get_daily_report_text():
    d=datetime.now(TZ).date().isoformat(); c=db(); r=c.execute("SELECT data FROM daily_reports WHERE report_date=?",(d,)).fetchone(); c.close(); x=json.loads(r["data"]) if r else {}
    top=x.get("top_usage") or []
    top_text=" | ".join(f"{row.get('event')}: {row.get('count')}" for row in top[:6]) or "ثبت نشده"
    return ("📋 گزارش پایان روز\n\n"
            f"📢 پست‌های زمان‌بندی‌شده: {x.get('posts',0)}\n" + f"🤖 پست‌های خودکار منتشرشده: {x.get('published_posts',x.get('auto_posts',0))}\n"
            f"👥 کاربران ثبت‌شده: {x.get('total_users',0)}\n"
            f"🟢 کاربران فعال امروز: {x.get('active',0)}\n"
            f"🆕 کاربران جدید: {x.get('new',0)}\n"
            f"📈 رویدادهای استفاده از ربات: {x.get('usage_events',0)}\n"
            f"👤 کاربران استفاده‌کننده: {x.get('usage_users',0)}\n"
            f"🎯 اهداف ساخته‌شده: {x.get('goals_created',0)}\n"
            f"✅ اهداف انجام‌شده: {x.get('done',0)}\n"
            f"⭐ XP کسب‌شده: {x.get('xp',0)}\n"
            f"🗳 مشارکت در نظرسنجی: {x.get('poll_participation',0)} نفر\n"
            f"❤️ کاربران دارای واکنش کانال: {x.get('reaction_users',0)} نفر\n"
            f"👍 مفید: {x.get('likes',0)}\n"
            f"👎 نامناسب: {x.get('dislikes',0)}\n"
            f"🎫 تیکت‌های جدید: {x.get('tickets_created',0)}\n"
            f"✅ تیکت‌های بسته‌شده: {x.get('tickets_closed',0)}\n"
            f"💬 پیام‌های پشتیبانی: {x.get('ticket_messages',0)}\n"
            f"🔥 فعالیت‌های پرتکرار: {top_text}")

async def build_weekly_admin_report():
    """Build a Friday-end operational/financial report for Owner/authorized admins."""
    now=datetime.now(TZ)
    # Iran week: Saturday(0) ... Friday(6) in Jalali terms is not represented by
    # Python weekday directly; we only use the current 7-day rolling window.
    end=now.date()
    start=end-timedelta(days=6)
    start_iso=start.isoformat()
    end_iso=end.isoformat()
    c=db()
    data={
        "start": start_iso, "end": end_iso,
        "new_users": c.execute("SELECT COUNT(*) n FROM users WHERE substr(created_at,1,10) BETWEEN ? AND ?",(start_iso,end_iso)).fetchone()["n"],
        "active_users": c.execute("SELECT COUNT(DISTINCT user_id) n FROM activity_log WHERE substr(created_at,1,10) BETWEEN ? AND ?",(start_iso,end_iso)).fetchone()["n"],
        "payments": c.execute("SELECT COUNT(*) n FROM payments WHERE substr(created_at,1,10) BETWEEN ? AND ?",(start_iso,end_iso)).fetchone()["n"],
        "paying_users": c.execute("SELECT COUNT(DISTINCT user_id) n FROM payments WHERE substr(created_at,1,10) BETWEEN ? AND ?",(start_iso,end_iso)).fetchone()["n"],
        "revenue": c.execute("SELECT COALESCE(SUM(total_amount),0) n FROM payments WHERE substr(created_at,1,10) BETWEEN ? AND ?",(start_iso,end_iso)).fetchone()["n"],
        "vip_users": c.execute("SELECT COUNT(*) n FROM users WHERE vip_until IS NOT NULL AND vip_until>?",(now.isoformat(),)).fetchone()["n"],
        "normal_users": c.execute("SELECT COUNT(*) n FROM users WHERE vip_until IS NULL OR vip_until<=?",(now.isoformat(),)).fetchone()["n"],
        "xp_earned": c.execute("SELECT COALESCE(SUM(amount),0) n FROM xp_log WHERE amount>0 AND substr(created_at,1,10) BETWEEN ? AND ?",(start_iso,end_iso)).fetchone()["n"],
        "xp_spent": c.execute("SELECT COALESCE(SUM(-amount),0) n FROM xp_log WHERE amount<0 AND substr(created_at,1,10) BETWEEN ? AND ?",(start_iso,end_iso)).fetchone()["n"],
        "tickets": c.execute("SELECT COUNT(*) n FROM tickets WHERE substr(created_at,1,10) BETWEEN ? AND ?",(start_iso,end_iso)).fetchone()["n"],
        "tickets_closed": c.execute("SELECT COUNT(*) n FROM tickets WHERE status IN ('closed','resolved') AND substr(updated_at,1,10) BETWEEN ? AND ?",(start_iso,end_iso)).fetchone()["n"],
        "posts": c.execute("SELECT COUNT(*) n FROM auto_post_history WHERE substr(created_at,1,10) BETWEEN ? AND ?",(start_iso,end_iso)).fetchone()["n"],
    }
    key=f"{start_iso}:{end_iso}"
    c.execute("INSERT OR REPLACE INTO weekly_reports(report_week,data,created_at) VALUES(?,?,?)",(key,json.dumps(data,ensure_ascii=False),now.isoformat()))
    c.commit(); c.close()
    return data

def get_weekly_admin_report_text(data):
    return (
        "📊 گزارش هفتگی ربات\n\n"
        f"📅 بازه: {data.get('start','—')} تا {data.get('end','—')}\n"
        f"🆕 کاربران جدید: {data.get('new_users',0)}\n"
        f"🟢 کاربران فعال: {data.get('active_users',0)}\n"
        f"💳 پرداخت‌ها: {data.get('payments',0)}\n"
        f"👤 خریداران یکتا: {data.get('paying_users',0)}\n"
        f"💰 مبلغ پرداخت‌های ثبت‌شده: {data.get('revenue',0):,}\n"
        f"💎 VIP فعال: {data.get('vip_users',0)}\n"
        f"👤 عادی: {data.get('normal_users',0)}\n"
        f"⭐ XP کسب‌شده: {data.get('xp_earned',0)}\n"
        f"⭐ XP مصرف‌شده: {data.get('xp_spent',0)}\n"
        f"🎫 تیکت جدید: {data.get('tickets',0)}\n"
        f"✅ تیکت بسته‌شده: {data.get('tickets_closed',0)}\n"
        f"📢 پست خودکار: {data.get('posts',0)}"
    )

async def weekly_admin_report_job(context):
    now=datetime.now(TZ)
    if now.hour != 23 or now.minute != 59 or now.weekday() != 4:
        return
    key=(now.date()-timedelta(days=6)).isoformat()+":"+now.date().isoformat()
    if get_system_setting("last_weekly_admin_report", "") == key:
        return
    try:
        data=await build_weekly_admin_report()
        report=get_weekly_admin_report_text(data)
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(chat_id=admin_id,text=report)
            except Exception:
                logger.exception("Weekly admin report delivery failed")
        set_system_setting("last_weekly_admin_report",key)
    except Exception:
        logger.exception("Weekly admin report build failed")

async def run_health_checks(bot,admin_id=0):
    checks=[]
    checks.append(("Bot","OK" if BOT_TOKEN else "ERROR","توکن BOT_TOKEN تنظیم شده است." if BOT_TOKEN else "BOT_TOKEN تنظیم نشده است."))

    try:
        c=db()
        c.execute("SELECT 1")
        integrity=c.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity=="ok":
            checks.append(("Database","OK","SQLite و integrity_check سالم است."))
        else:
            checks.append(("Database","ERROR",f"integrity_check: {integrity}"))
        required_tables={"users","goals","channel_config","customers","appointments","business_profiles","feature_access"}
        existing={r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        missing=sorted(required_tables-existing)
        checks.append(("Customer/Booking","ERROR",f"جدول‌های ناقص: {', '.join(missing)}") if missing
                      else ("Customer/Booking","OK","جداول کاربران، مشتری، نوبت و رزرو آنلاین موجود هستند."))
        c.close()
    except Exception as e:
        checks.append(("Database","ERROR",f"اتصال یا integrity_check خطا دارد: {e}"))
        checks.append(("Customer/Booking","ERROR","به دیتابیس دسترسی نشد."))

    cfg=get_channel_config()
    if cfg and cfg["channel_id"]:
        try:
            chat=await bot.get_chat(cfg["channel_id"])
            me=await bot.get_me()
            member=await bot.get_chat_member(cfg["channel_id"],me.id)
            allowed = member.status in {ChatMemberStatus.ADMINISTRATOR,ChatMemberStatus.OWNER}
            if allowed:
                checks.append(("Channel","OK",f"کانال {getattr(chat,'title',cfg['channel_id'])} قابل دسترسی و ربات ادمین است."))
            else:
                checks.append(("Channel","ERROR","ربات به کانال دسترسی مدیریتی ندارد؛ ارسال/مدیریت پست ممکن است متوقف شود."))
        except Exception as e:
            checks.append(("Channel","ERROR",f"اتصال یا دسترسی کانال مشکل دارد: {e}"))
    else:
        checks.append(("Channel","WARN","کانال هنوز در تنظیمات مدیریت کانال متصل نشده است."))

    scheduler_ok=bool(getattr(bot,"job_queue",None))
    checks.append(("Scheduler","OK" if scheduler_ok else "WARN",
                   "زمان‌بندی ربات فعال است."
                   if scheduler_ok else
                   "صف زمان‌بندی داخلی کتابخانه در دسترس نیست؛ fallback داخلی ربات استفاده می‌شود."))

    n8n_ok=n8n_configured()
    checks.append(("n8n","OK" if n8n_ok else "WARN",
                   "Workflow هوش مصنوعی/اتوماسیون متصل است؛ اعتبارنامه OpenAI می‌تواند داخل n8n نگهداری شود."
                   if n8n_ok else
                   "Workflow هوش مصنوعی n8n هنوز متصل نشده است؛ مسیرهای دیگر در صورت تنظیم استفاده می‌شوند."))

    omni_ok=False
    omni_detail="OmniRoute تنظیم نشده است."
    if omniroute_configured():
        omni_ok, omni_detail = _omniroute_health_sync()
    checks.append(("OmniRoute","OK" if omni_ok else ("WARN" if not omniroute_configured() else "ERROR"), omni_detail))

    price_enabled=feature_enabled("price_data")
    checks.append(("Price Sources","OK" if price_enabled else "OFF",
                   "قیمت از منابع بازار دریافت می‌شود؛ AI منبع عدد قیمت نیست."
                   if price_enabled else
                   "قیمت آنلاین توسط مدیر غیرفعال شده است."))

    try:
        c=db()
        orphan_appointments=c.execute("""
            SELECT COUNT(*) n FROM appointments a
            LEFT JOIN customers cu ON cu.id=a.customer_id
            WHERE a.customer_id IS NOT NULL AND cu.id IS NULL
        """).fetchone()["n"]
        bad_customer_owners=c.execute("""
            SELECT COUNT(*) n FROM appointments a
            JOIN customers cu ON cu.id=a.customer_id
            WHERE a.owner_user_id IS NOT NULL AND cu.owner_user_id IS NOT NULL
              AND a.owner_user_id != cu.owner_user_id
        """).fetchone()["n"]
        c.close()
        isolation_bad=int(orphan_appointments or 0)+int(bad_customer_owners or 0)
        checks.append(("Data Isolation","OK" if isolation_bad==0 else "ERROR",
                       "روابط مالکیت کاربران و مشتری/رزرو سازگار است."
                       if isolation_bad==0 else
                       f"{isolation_bad} رابطه ناسازگار پیدا شد؛ نیازمند بررسی مدیر است."))
    except Exception:
        checks.append(("Data Isolation","WARN","ممیزی مالکیت در این نوبت کامل نشد."))

    ai_enabled=feature_enabled("ai")
    provider_state=ai_provider_diagnostics()
    ai_key=provider_state["openai"] or provider_state.get("gemini", False)
    if not ai_enabled:
        checks.append(("AI","OFF","هوش مصنوعی توسط مدیر غیرفعال شده است."))
    elif any(provider_state.values()):
        providers=[]
        if provider_state["omniroute"]: providers.append("OmniRoute")
        if provider_state["openai"]: providers.append("OpenAI")
        if provider_state["n8n"]: providers.append("n8n fallback")
        if provider_state.get("gemini"): providers.append("Gemini")
        checks.append(("AI","OK","AI فعال است؛ مسیرها: " + " + ".join(providers)))
    else:
        checks.append(("AI","WARN","دستیار هوشمند فعال است ولی هیچ مسیر AI قابل استفاده‌ای متصل نیست."))

    try:
        c=db()
        feature_count=c.execute("SELECT COUNT(*) n FROM feature_access").fetchone()["n"]
        c.close()
        checks.append(("Feature Access","OK" if feature_count else "ERROR",
                       f"{feature_count} قابلیت در ماتریس دسترسی ثبت شده است."
                       if feature_count else "ماتریس دسترسی قابلیت‌ها خالی است."))
    except Exception as e:
        checks.append(("Feature Access","ERROR",f"خواندن تنظیمات قابلیت‌ها خطا دارد: {e}"))

    for name,ok,detail in _security_patch_audit():
        checks.append((name,"OK" if ok else "ERROR",detail))

    c=db()
    now=datetime.now(TZ).isoformat()
    c.executemany(
        "INSERT INTO health_checks(service,status,details,created_at) VALUES(?,?,?,?)",
        [(a,b,d,now) for a,b,d in checks]
    )
    c.commit(); c.close()

def _security_patch_audit():
    """Non-destructive runtime security audit for the management health check."""
    checks=[]
    # SQLite security primitives
    try:
        c=db()
        fk=c.execute("PRAGMA foreign_keys").fetchone()[0]
        journal=c.execute("PRAGMA journal_mode").fetchone()[0]
        sync=c.execute("PRAGMA synchronous").fetchone()[0]
        checks.append(("SQLite Foreign Keys", fk==1, f"foreign_keys={fk}"))
        checks.append(("SQLite Journal", str(journal).lower()=="wal", f"journal_mode={journal}"))
        checks.append(("SQLite Sync", int(sync)>=1, f"synchronous={sync}"))
        # Cross-user ownership checks for the most sensitive business records.
        queries=[
            ("""SELECT COUNT(*) n FROM goals g LEFT JOIN users u ON u.user_id=g.user_id WHERE u.user_id IS NULL""","Orphan goals"),
            ("""SELECT COUNT(*) n FROM customers c LEFT JOIN users u ON u.user_id=c.owner_user_id WHERE c.owner_user_id IS NOT NULL AND u.user_id IS NULL""","Orphan customers"),
            ("""SELECT COUNT(*) n FROM appointments a LEFT JOIN users u ON u.user_id=a.owner_user_id WHERE a.owner_user_id IS NOT NULL AND u.user_id IS NULL""","Orphan appointments"),
            ("""SELECT COUNT(*) n FROM payments p LEFT JOIN users u ON u.user_id=p.user_id WHERE p.user_id IS NOT NULL AND u.user_id IS NULL""","Orphan payments"),
        ]
        for sql,label in queries:
            try:
                n=int(c.execute(sql).fetchone()["n"])
                checks.append((label,n==0,str(n) if n else "0"))
            except Exception:
                checks.append((label,True,"table/schema not applicable"))
        c.close()
    except Exception as exc:
        checks.append(("Database security audit",False,type(exc).__name__))

    # Restrict DB/backup files from group/other users where the OS supports chmod.
    if os.name == "posix":
        for path,label in [(DB_PATH,"DB file permissions"),(DB_BACKUP_PATH,"Backup permissions")]:
            try:
                if os.path.exists(path):
                    mode=os.stat(path).st_mode & 0o777
                    checks.append((label,(mode & 0o077)==0,oct(mode)))
                    if (mode & 0o077)!=0:
                        try: os.chmod(path,0o600)
                        except OSError: pass
            except OSError as exc:
                checks.append((label,False,type(exc).__name__))
    return checks

def health_text():
    """Compact, RTL-friendly Health Check report for Telegram.
    Keep each check visually self-contained and translate technical status labels
    so mixed Persian/English text does not scramble the message direction.
    """
    c=db()
    rows=c.execute("""
        SELECT service,status,details
        FROM health_checks
        WHERE created_at=(SELECT MAX(created_at) FROM health_checks)
        ORDER BY id ASC
    """).fetchall()
    c.close()

    service_fa={
        "Bot":"🤖 ربات",
        "Database":"🗄️ پایگاه‌داده",
        "Customer/Booking":"👥 مشتری و رزرو",
        "Channel":"📢 کانال",
        "Scheduler":"⏰ زمان‌بندی",
        "n8n":"🔗 n8n",
        "OmniRoute":"🔀 OmniRoute",
        "Price Sources":"💹 منابع قیمت",
        "Data Isolation":"🔐 جداسازی داده",
        "AI":"🤖 هوش مصنوعی",
        "Feature Access":"🧩 دسترسی قابلیت‌ها",
        "SQLite Foreign Keys":"🔗 کلیدهای خارجی SQLite",
        "SQLite Journal":"💾 ژورنال SQLite",
        "SQLite Sync":"⚙️ همگام‌سازی SQLite",
        "SQLite integrity":"🛡️ سلامت SQLite",
    }
    status_fa={
        "OK":"سالم", "ERROR":"خطا", "WARN":"هشدار", "OFF":"خاموش"
    }
    icon={"OK":"🟢","ERROR":"🔴","WARN":"🟡","OFF":"⚪"}
    lines=["🩺 <b>چکاپ ربات</b>","<i>وضعیت سرویس‌ها و زیرساخت</i>",""]
    for r in rows:
        raw_service=re.sub(r"<[^>]+>", "", str(r["service"] or "")).strip()
        status=str(r["status"] or "").upper()
        details=re.sub(r"<[^>]+>", "", str(r["details"] or "")).strip()
        label=service_fa.get(raw_service, raw_service)
        st=status_fa.get(status, status)
        lines.append(f"{icon.get(status,'⚪')} <b>{html.escape(label)}</b> — {html.escape(st)}")
        if details:
            # Keep long technical diagnostics readable on mobile.
            details=' '.join(details.split())
            if len(details)>260:
                details=details[:257]+'…'
            lines.append(f"   <i>↳ {html.escape(details)}</i>")
        lines.append("")
    return "\n".join(lines).rstrip()
async def scheduled_health_check_job(context):
    """Run the automatic admin health check once per day at the configured time."""
    try:
        if not ADMIN_IDS:
            return
        enabled = get_system_setting("health_check_enabled", "1") != "0"
        if not enabled:
            return
        schedule = get_system_setting("health_check_time", "03:00")
        # JobQueue هر 60 ثانیه از زمان شروع ربات اجرا می‌شود و الزاماً روی ثانیه 00
        # قرار نمی‌گیرد؛ بنابراین فقط منتظر برابری دقیق HH:MM نمی‌مانیم.
        # اگر از ساعت تعیین‌شده عبور کرده باشیم و امروز هنوز چکاپ نشده باشد، اجرا می‌شود.
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", schedule):
            schedule = "03:00"
            set_system_setting("health_check_time", schedule)
        now = datetime.now(TZ)
        if now.strftime("%H:%M") < schedule:
            return
        today = now.date().isoformat()
        if get_system_setting("last_auto_health_check_date", "") == today:
            return
        await run_health_checks(context.bot, next(iter(ADMIN_IDS)))
        report = health_text() + "\n\n🩺 چکاپ دوره‌ای خودکار انجام شد."
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(admin_id, report)
            except Exception:
                pass
        set_system_setting("last_auto_health_check_date", today)
        set_system_setting("last_auto_health_check", now.isoformat())
    except Exception:
        logger.exception("Scheduled health check failed")

async def daily_report_job(context):
    now=datetime.now(TZ)
    if now.hour == 23 and now.minute == 59:
        await build_daily_report()
        cfg = get_channel_config()
        channel = cfg["channel_id"] if cfg else ""
        if channel and get_auto_setting("night_poll_date", "") != now.date().isoformat():
            try:
                await context.bot.send_poll(
                    chat_id=channel,
                    question="🌙 ارزیابی امشب: از محتوای امروز راضی بودی؟",
                    options=["😍 خیلی خوب بود", "👍 خوب بود", "🔄 بهترش کنیم"],
                    is_anonymous=False,
                )
                set_auto_setting("night_poll_date", now.date().isoformat())
            except Exception as e:
                logger.warning("Night evaluation poll failed: %s", e)


async def channel_reaction_handler(update, context):
    """Store channel post reactions for end-of-day analytics."""
    try:
        mr = getattr(update, "message_reaction", None)
        if not mr:
            return
        chat = getattr(mr, "chat", None)
        if not chat or getattr(chat, "type", "") not in ("channel", "supergroup", "group"):
            return
        user = getattr(mr, "user", None)
        if not user:
            return
        message_id = int(getattr(mr, "message_id", 0) or 0)
        channel_id = str(getattr(chat, "id", ""))
        if not channel_id or not message_id:
            return
        old = getattr(mr, "old_reaction", []) or []
        new = getattr(mr, "new_reaction", []) or []
        c = db()
        # Replace this user's reaction set for this message atomically.
        c.execute("DELETE FROM channel_reactions WHERE channel_id=? AND message_id=? AND user_id=?", (channel_id, message_id, int(user.id)))
        now = datetime.now(TZ).isoformat()
        new_emojis=[]
        for reaction in new:
            emoji = getattr(reaction, "emoji", None) or getattr(reaction, "custom_emoji_id", None) or str(reaction)
            is_paid = 1 if getattr(reaction, "type", "") == "paid" else 0
            emoji=str(emoji); new_emojis.append(emoji)
            c.execute("INSERT OR IGNORE INTO channel_reactions(channel_id,message_id,user_id,reaction,is_paid,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                      (channel_id, message_id, int(user.id), emoji, is_paid, now, now))
        c.commit(); c.close()
        for emoji in new_emojis:
            award_engagement_xp_once(int(user.id), 2, "channel_reaction", f"reaction:{channel_id}:{message_id}:{int(user.id)}:{datetime.now(TZ).date().isoformat()}", "channel_reaction")
    except Exception:
        logger.exception("Channel reaction handler failed")

async def channel_poll_answer_handler(update, context):
    """Persist anonymous/non-anonymous end-of-day poll choices."""
    try:
        pa = getattr(update, "poll_answer", None)
        if not pa:
            return
        poll_id = str(getattr(pa, "poll_id", ""))
        user = getattr(pa, "user", None)
        if not poll_id or not user:
            return
        options = list(getattr(pa, "option_ids", []) or [])
        c = db()
        poll_row = c.execute("SELECT poll_type,channel_id FROM channel_polls WHERE poll_id=?", (poll_id,)).fetchone()
        c.execute("DELETE FROM channel_poll_votes WHERE poll_id=? AND user_id=?", (poll_id, int(user.id)))
        now = datetime.now(TZ).isoformat()
        for option_id in options:
            c.execute("INSERT OR REPLACE INTO channel_poll_votes(poll_id,user_id,option_id,created_at) VALUES(?,?,?,?)",
                      (poll_id, int(user.id), int(option_id), now))
        c.commit(); c.close()
        if options and poll_row:
            award_engagement_xp_once(int(user.id), 3, "channel_poll", f"poll:{poll_id}:{int(user.id)}", "channel_poll_vote")
    except Exception:
        logger.exception("Channel poll answer handler failed")

def _today_channel_id():
    cfg = get_channel_config()
    return str(cfg["channel_id"]) if cfg and cfg["channel_id"] else ""

def _reaction_stats(channel_id, date_iso):
    c = db()
    rows = c.execute("SELECT reaction, COUNT(*) n FROM channel_reactions WHERE channel_id=? AND substr(created_at,1,10)=? GROUP BY reaction ORDER BY n DESC", (str(channel_id), date_iso)).fetchall()
    total = c.execute("SELECT COUNT(*) n FROM channel_reactions WHERE channel_id=? AND substr(created_at,1,10)=?", (str(channel_id), date_iso)).fetchone()["n"]
    c.close()
    return [(r["reaction"], r["n"]) for r in rows], total

def _poll_vote_stats(poll_type, date_iso):
    c = db()
    rows = c.execute("""SELECT cp.question, cp.options, cp.poll_id, cpv.option_id, COUNT(*) n
                       FROM channel_polls cp JOIN channel_poll_votes cpv ON cp.poll_id=cpv.poll_id
                       WHERE cp.poll_type=? AND cp.report_date=? GROUP BY cp.poll_id, cpv.option_id""", (poll_type, date_iso)).fetchall()
    c.close()
    return rows

async def send_channel_morning_message(context):
    now = datetime.now(TZ)
    if now.hour != int(get_auto_setting("morning_channel_hour", "7") or 7) or now.minute != int(get_auto_setting("morning_channel_minute", "0") or 0):
        return
    channel = _today_channel_id()
    if not channel or get_auto_setting("channel_morning_date", "") == now.date().isoformat():
        return
    try:
        await context.bot.send_message(chat_id=channel, text="☀️ صبح بخیر همراهان MyTasks!\n\nیک روز تازه، یک فرصت تازه برای یک قدم بهتر. 🌱\nامروز هم با هم یک موضوع کاربردی و مفید را بررسی می‌کنیم. 🎯")
        set_auto_setting("channel_morning_date", now.date().isoformat())
    except Exception:
        logger.exception("Channel morning message failed")

async def send_night_channel_feedback(context):
    now = datetime.now(TZ)
    if now.hour != 23 or now.minute != 30:
        return
    date_iso = now.date().isoformat()
    channel = _today_channel_id()
    if not channel or get_auto_setting("night_feedback_date", "") == date_iso:
        return
    try:
        # Night greeting is deliberately separate from both polls.
        await context.bot.send_message(chat_id=channel, text="🌙 شب بخیر همراهان MyTasks!\n\nممنون که امروز هم همراه ما بودید. ❤️\nقبل از پایان روز، نظرتان درباره محتوای امروز را با ما در میان بگذارید.")
        msg = await context.bot.send_poll(chat_id=channel, question="📊 محتوای امروز چقدر برایت مفید بود؟", options=["😍 خیلی مفید بود", "👍 مفید بود", "😐 معمولی بود", "👎 مفید نبود"], is_anonymous=False)
        c=db(); c.execute("INSERT OR REPLACE INTO channel_polls(poll_id,channel_id,poll_type,question,options,created_at,report_date) VALUES(?,?,?,?,?,?,?)", (str(msg.poll.id),str(channel),"usefulness",msg.poll.question,json.dumps(msg.poll.options,ensure_ascii=False,default=lambda o:o.text),datetime.now(TZ).isoformat(),date_iso))
        c.commit(); c.close()
        topics = ["🏃 ورزش و سلامتی", "🧠 تمرکز و یادگیری", "😴 خواب و سبک زندگی", "💰 مدیریت مالی", "📚 مطالعه و رشد فردی"]
        msg2 = await context.bot.send_poll(chat_id=channel, question="🎯 فردا بیشتر درباره کدام موضوع صحبت کنیم؟", options=topics, is_anonymous=False)
        c=db(); c.execute("INSERT OR REPLACE INTO channel_polls(poll_id,channel_id,poll_type,question,options,created_at,report_date) VALUES(?,?,?,?,?,?,?)", (str(msg2.poll.id),str(channel),"topic",msg2.poll.question,json.dumps(topics,ensure_ascii=False),datetime.now(TZ).isoformat(),date_iso))
        c.commit(); c.close()
        set_auto_setting("night_feedback_date", date_iso)
    except Exception:
        logger.exception("Night channel feedback failed")

async def final_daily_report_job(context):
    now = datetime.now(TZ)
    if now.hour == 23 and now.minute == 59:
        await build_daily_report()
        channel = _today_channel_id()
        date_iso = now.date().isoformat()
        reactions, reaction_total = _reaction_stats(channel, date_iso) if channel else ([],0)
        topic_rows = _poll_vote_stats("topic", date_iso)
        useful_rows = _poll_vote_stats("usefulness", date_iso)
        # Feed topic preference back into the next day's topic picker without replacing the admin's configured topic.
        topic_votes = {}
        for r in topic_rows:
            try:
                opts=json.loads(r["options"])
                label = opts[int(r["option_id"])] if int(r["option_id"]) < len(opts) else str(r["option_id"])
                topic_votes[label]=topic_votes.get(label,0)+int(r["n"])
            except Exception: pass
        top_topic = max(topic_votes, key=topic_votes.get) if topic_votes else ""
        c=db()
        c.execute("INSERT OR REPLACE INTO system_settings(key,value,updated_at) VALUES(?,?,?)", ("tomorrow_topic_preference", top_topic, datetime.now(TZ).isoformat()))
        c.commit(); c.close()
        # Append channel reaction data to the existing daily report rather than replacing it.
        c=db(); row=c.execute("SELECT data FROM daily_reports WHERE report_date=?",(date_iso,)).fetchone(); data=json.loads(row["data"]) if row else {}
        data.update({"channel_reactions":reaction_total,"reaction_breakdown":dict(reactions),"tomorrow_topic":top_topic,"topic_votes":topic_votes})
        c.execute("INSERT OR REPLACE INTO daily_reports(report_date,data,created_at) VALUES(?,?,?)",(date_iso,json.dumps(data,ensure_ascii=False),datetime.now(TZ).isoformat())); c.commit(); c.close()
        if ADMIN_IDS:
            report = get_daily_report_text()+f"\n\n📣 واکنش‌های کانال: {reaction_total}"
            if reactions: report += "\n" + " | ".join(f"{e}: {n}" for e,n in reactions[:8])
            report += f"\n🎯 موضوع پیشنهادی فردا: {top_topic or 'رأی کافی ثبت نشده'}"
            for admin_id in ADMIN_IDS:
                try: await context.bot.send_message(chat_id=admin_id,text=report)
                except Exception: logger.exception("Admin daily report delivery failed")

admin_panel_callback=final_admin_panel_callback
admin_keyboard=final_admin_keyboard

async def error_handler(update, context):
    """Global recovery must preserve the user's current flow instead of masking errors with Home."""
    logger.error("Bot error", exc_info=context.error)
    try:
        uid = update.effective_user.id if update and update.effective_user else None
        if not uid:
            return
        err = context.error
        err_name = type(err).__name__ if err else "UnknownError"
        logger.error("Route failure for uid=%s type=%s", uid, err_name)
        if update.callback_query:
            q = update.callback_query
            try:
                await q.answer("❌ خطا در همین بخش؛ وضعیتت حفظ شد.", show_alert=True)
            except Exception:
                pass
            data = q.data or ""
            retry = data if data else "v25:hub"
            await q.message.reply_text(
                f"⚠️ اجرای این بخش با خطا متوقف شد.\n\nکد خطا: <code>{err_name}</code>\n"
                "صفحه فعلی پاک نشد؛ می‌توانی دوباره امتحان کنی.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 تلاش دوباره", callback_data=retry)],
                    [InlineKeyboardButton("⬅️ مرکز من", callback_data="v25:hub"), main_menu_button(uid)],
                ]),
            )
        elif update.message:
            await update.message.reply_text(
                f"⚠️ اجرای این بخش با خطا متوقف شد.\nکد خطا: <code>{err_name}</code>\n"
                "وضعیت فعلی حفظ شد؛ می‌توانی دوباره تلاش کنی.",
                parse_mode="HTML",
                reply_markup=nav_keyboard(uid),
            )
    except Exception:
        logger.exception("Failed to send contextual recovery message")


async def my_id(update, context):
    uid = update.effective_user.id
    await update.message.reply_text(
        f"🆔 شناسه تلگرام شما: <code>{uid}</code>\n\n"
        "این عدد را در Railway → Variables داخل ADMIN_IDS یا ADMIN_ID قرار بده و سرویس را Restart/Redeploy کن.",
        parse_mode="HTML",
    )


# ===================== MYTASKS V25 UNIFIED EXPERIENCE LAYER =====================
# Additive, backward-compatible layer for profile sharing, finance, installments,
# portfolio, surveys, business services/payments, VIP plans, voice, SMS hooks,
# unified navigation and admin feature status.

V25_FEATURE_KEYS = {
    'unified_hub','important_reminders','calendar_hub','profile_sharing','portfolio',
    'installments','business_services','business_finance','booking_payments','card_to_card',
    'surveys','sms','voice','vip_plans','market_prices_v25'
}


def _feature_flag_exists(key):
    try:
        c=db(); row=c.execute('SELECT 1 FROM feature_flags WHERE key=? LIMIT 1',(str(key),)).fetchone(); c.close()
        return bool(row)
    except Exception:
        return False

V25_FEATURE_LABELS = {
    'unified_hub':'🧠 مرکز فرمان هوشمند',
    'important_reminders':'🔔 یادآوری‌های مهم',
    'calendar_hub':'📅 تقویم یکپارچه',
    'profile_sharing':'👤 اطلاعات من',
    'portfolio':'💰 سرمایه‌های من',
    'installments':'💳 اقساط و تسهیلات',
    'business_services':'🛠️ خدمات کسب‌وکار',
    'business_finance':'📒 مالی مشتریان',
    'booking_payments':'💳 پرداخت رزرو',
    'card_to_card':'💵 کارت‌به‌کارت',
    'surveys':'⭐ نظرسنجی مشتری',
    'sms':'📱 پیامک',
    'voice':'🎙️ دستیار صوتی',
    'vip_plans':'💎 پلن‌های VIP',
    'market_prices_v25':'📈 قیمت بازار'
}

V25_DEFAULT_QUESTIONS = [
    ('space','🏪 فضای مجموعه چطور بود؟'),
    ('clean','🧹 تمیزی و نظم محیط چطور بود؟'),
    ('staff','👥 برخورد کارکنان چطور بود؟'),
    ('speed','⚡ سرعت پاسخگویی چطور بود؟'),
    ('quality','🛠️ کیفیت خدمت چطور بود؟'),
    ('value','💰 ارزش خدمت نسبت به هزینه چطور بود؟'),
    ('booking','📅 رزرو آنلاین چقدر راحت بود؟'),
]

V25_PLAN_SEEDS = [
    ('one_hour','⏱️ یک ساعته',1,0),
    ('one_day','📅 یک روزه',1,0),
    ('one_week','📆 یک هفته‌ای',7,0),
    ('one_month','🗓️ یک ماهه',30,0),
    ('two_months','🗓️ دو ماهه',60,0),
    ('three_months','🗓️ سه ماهه',90,0),
    ('six_months','🗓️ شش ماهه',180,0),
    ('one_year','🎉 یک ساله',365,0),
]

def _v25_now():
    return datetime.now(TZ).isoformat()

def _v25_exec(sql, params=(), fetchone=False, fetchall=False, commit=True):
    c=db()
    try:
        cur=c.execute(sql, params)
        result = cur.fetchone() if fetchone else (cur.fetchall() if fetchall else cur.rowcount)
        if commit: c.commit()
        return result
    finally:
        c.close()

def v25_init_db():
    c=db()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS user_profile (
        user_id INTEGER PRIMARY KEY,
        full_name TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        telegram_id INTEGER,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS profile_share (
        user_id INTEGER NOT NULL,
        scope TEXT NOT NULL,
        field TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(user_id,scope,field)
    );
    CREATE TABLE IF NOT EXISTS business_services (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        duration_minutes INTEGER NOT NULL DEFAULT 30,
        price_rial INTEGER NOT NULL DEFAULT 0,
        description TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS customer_finance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_user_id INTEGER NOT NULL,
        customer_id INTEGER NOT NULL,
        appointment_id INTEGER,
        kind TEXT NOT NULL DEFAULT 'charge',
        amount_rial INTEGER NOT NULL DEFAULT 0,
        paid_rial INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        due_date TEXT,
        description TEXT NOT NULL DEFAULT '',
        receipt_file_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS installment_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        bank_name TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        principal_rial INTEGER NOT NULL DEFAULT 0,
        interest_pct REAL NOT NULL DEFAULT 0,
        term_months INTEGER NOT NULL DEFAULT 1,
        first_due_date TEXT NOT NULL,
        day_of_month INTEGER NOT NULL DEFAULT 1,
        monthly_rial INTEGER NOT NULL DEFAULT 0,
        total_interest_rial INTEGER NOT NULL DEFAULT 0,
        total_payable_rial INTEGER NOT NULL DEFAULT 0,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS installment_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id INTEGER NOT NULL,
        installment_no INTEGER NOT NULL,
        due_date TEXT NOT NULL,
        amount_rial INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        paid_rial INTEGER NOT NULL DEFAULT 0,
        paid_at TEXT,
        note TEXT NOT NULL DEFAULT '',
        UNIQUE(plan_id,installment_no)
    );
    CREATE TABLE IF NOT EXISTS portfolio_assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        asset_code TEXT NOT NULL,
        title TEXT NOT NULL,
        quantity REAL NOT NULL DEFAULT 0,
        buy_price_rial REAL NOT NULL DEFAULT 0,
        buy_date TEXT NOT NULL,
        fees_rial REAL NOT NULL DEFAULT 0,
        note TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS survey_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_user_id INTEGER NOT NULL,
        code TEXT NOT NULL,
        question TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        UNIQUE(owner_user_id,code)
    );
    CREATE TABLE IF NOT EXISTS survey_responses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_user_id INTEGER NOT NULL,
        appointment_id INTEGER NOT NULL,
        customer_id INTEGER NOT NULL,
        rating INTEGER,
        comment TEXT NOT NULL DEFAULT '',
        suggestion TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE(appointment_id)
    );
    CREATE TABLE IF NOT EXISTS survey_answers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        response_id INTEGER NOT NULL,
        question_id INTEGER NOT NULL,
        value TEXT NOT NULL,
        UNIQUE(response_id,question_id)
    );
    CREATE TABLE IF NOT EXISTS payment_methods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_user_id INTEGER NOT NULL,
        method_type TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 0,
        title TEXT NOT NULL DEFAULT '',
        details TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        UNIQUE(owner_user_id,method_type)
    );
    CREATE TABLE IF NOT EXISTS gateway_configs (
        owner_user_id INTEGER PRIMARY KEY,
        provider TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 0,
        payment_link TEXT NOT NULL DEFAULT '',
        merchant_id TEXT NOT NULL DEFAULT '',
        api_key TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS booking_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_user_id INTEGER NOT NULL,
        appointment_id INTEGER NOT NULL,
        customer_id INTEGER NOT NULL,
        amount_rial INTEGER NOT NULL DEFAULT 0,
        method TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        receipt_file_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS subscription_plans_v25 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        duration_minutes INTEGER NOT NULL,
        price_rial INTEGER NOT NULL DEFAULT 0,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS important_reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        remind_at TEXT NOT NULL,
        recurrence TEXT NOT NULL DEFAULT 'once',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(user_id,title,remind_at)
    );
    CREATE TABLE IF NOT EXISTS sms_settings (
        owner_user_id INTEGER PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 0,
        provider TEXT NOT NULL DEFAULT 'custom',
        endpoint TEXT NOT NULL DEFAULT '',
        api_key TEXT NOT NULL DEFAULT '',
        sender TEXT NOT NULL DEFAULT '',
        customer_enabled INTEGER NOT NULL DEFAULT 1,
        owner_enabled INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS sms_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_user_id INTEGER NOT NULL,
        recipient TEXT NOT NULL,
        message TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        response TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS notification_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_user_id INTEGER NOT NULL,
        customer_id INTEGER,
        appointment_id INTEGER,
        event_type TEXT NOT NULL,
        details TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS vip_receipts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        plan_id INTEGER NOT NULL,
        amount_rial INTEGER NOT NULL DEFAULT 0,
        receipt_file_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        reviewed_at TEXT,
        reviewed_by INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_vip_receipts_status ON vip_receipts(status, created_at);
    ''')
    now=_v25_now()
    for key,val in [('morning_message_enabled','1'),('night_message_enabled','1'),('friday_pause','0'),('price_data_status','auto'),('vip_card_enabled','0'),('vip_gateway_enabled','0')]:
        c.execute('INSERT OR IGNORE INTO system_settings(key,value,updated_at) VALUES(?,?,?)',(key,val,now))
    for key in V25_FEATURE_KEYS:
        c.execute("INSERT OR IGNORE INTO feature_flags(key,enabled,updated_at) VALUES(?,?,?)",(key,1,now))
        c.execute("INSERT OR IGNORE INTO feature_access(key,mode,updated_at) VALUES(?,?,?)",(key,'free',now))
    for code,name,days,_ in V25_PLAN_SEEDS:
        minutes=60 if code=='one_hour' else days*24*60
        c.execute("INSERT OR IGNORE INTO subscription_plans_v25(code,name,duration_minutes,price_rial,enabled,created_at,updated_at) VALUES(?,?,?,?,1,?,?)",(code,name,minutes,0,now,now))
    c.commit(); c.close()

def v25_bootstrap_profile(uid, tg_user=None):
    now=_v25_now(); name=(tg_user.first_name if tg_user and tg_user.first_name else display_name(uid) or '')
    _v25_exec("INSERT OR IGNORE INTO user_profile(user_id,full_name,telegram_id,updated_at) VALUES(?,?,?,?)",(uid,name,uid,now))
    if tg_user:
        _v25_exec("UPDATE user_profile SET telegram_id=?,updated_at=? WHERE user_id=?",(uid,now,uid))

def v25_profile(uid):
    v25_bootstrap_profile(uid)
    return _v25_exec("SELECT * FROM user_profile WHERE user_id=?",(uid,),fetchone=True)

def v25_allowed(uid,key):
    if uid in ADMIN_IDS: return True
    try: return feature_enabled(key) and feature_access_mode(key,uid) != 'off' and (feature_access_mode(key,uid) != 'vip' or is_vip(uid))
    except Exception: return True

def v25_back(uid, cb='v25:hub'):
    fa=lang(uid)=='fa'
    return InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ بازگشت' if fa else '⬅️ Back',callback_data=cb),InlineKeyboardButton('🏠 منوی اصلی' if fa else '🏠 Main Menu',callback_data='nav:main')]])

def v25_hub_keyboard(uid):
    fa=lang(uid)=='fa'; rows=[]
    labels=[
        ('v25:today','🧠 امروز من','🧠 My Day','unified_hub'),
        ('v25:reminders','🔔 یادآوری‌های مهم','🔔 Important Reminders','important_reminders'),
        ('v25:calendar','📅 تقویم من','📅 My Calendar','calendar_hub'),
        ('v25:portfolio','💰 سرمایه‌های من','💰 My Portfolio','portfolio'),
        ('v25:installments','💳 اقساط و تسهیلات','💳 Installments','installments'),
        ('v25:profile','👤 اطلاعات من','👤 My Profile','profile_sharing'),
        ('v25:voice','🎙️ دستیار صوتی','🎙️ Voice Assistant','voice'),
        ('v25:vip','💎 VIP و اشتراک','💎 VIP & Subscription','vip_plans'),
        ('v25:business','🏪 پنل کسب‌وکار','🏪 Business Panel','business_services'),
    ]
    row=[]
    for cb,fa_text,en_text,key in labels:
        if v25_allowed(uid,key):
            row.append(InlineKeyboardButton(fa_text if fa else en_text,callback_data=cb))
            if len(row)==2: rows.append(row); row=[]
    if row: rows.append(row)
    # Customer/appointment management must be directly reachable from My Center.
    # This uses the existing customer access gate so VIP/off settings are respected.
    try:
        if customer_feature_allowed(uid):
            rows.append([InlineKeyboardButton('👥 مدیریت مشتری و نوبت‌دهی' if fa else '👥 Customer & Appointments', callback_data='v25:customers')])
    except Exception:
        logger.exception('Customer menu feature check failed')
    rows.append([main_menu_button(uid)])
    return InlineKeyboardMarkup(rows)

def v25_hub_text(uid):
    fa=lang(uid)=='fa'; p=v25_profile(uid); goals=get_goals(uid)
    d=datetime.now(TZ).date().isoformat(); c=db(); done=c.execute("SELECT COUNT(*) n FROM goal_days WHERE user_id=? AND goal_date=? AND status='done'",(uid,d)).fetchone()['n']; events=c.execute("SELECT COUNT(*) n FROM important_reminders WHERE user_id=? AND enabled=1 AND substr(remind_at,1,10)=?",(uid,d)).fetchone()['n']; appts=c.execute("SELECT COUNT(*) n FROM appointments WHERE owner_user_id=? AND appointment_date=? AND status='booked'",(uid,d)).fetchone()['n']; c.close()
    if fa:
        return f"☀️ <b>امروزت</b>\n\n🎯 هدف‌ها: {len(goals)}\n✅ انجام‌شده: {done}\n🔔 یادآوری امروز: {events}\n📅 نوبت امروز: {appts}\n\n👤 {html.escape(p['full_name'] or 'دوست من')}\n\nاز همین‌جا هر کاری لازم داری با چند کلیک انجام بده."
    return f"☀️ <b>My Day</b>\n\n🎯 Goals: {len(goals)}\n✅ Completed: {done}\n🔔 Reminders today: {events}\n📅 Appointments today: {appts}\n\n👤 {html.escape(p['full_name'] or 'Friend')}\n\nEverything you need is one tap away."

async def v25_hub(update,context):
    uid=update.effective_user.id
    # Support both normal-message entry and inline-callback entry.
    q = getattr(update, 'callback_query', None)
    if q:
        try:
            await q.answer()
        except Exception:
            pass
    target = q.message if q else update.message
    await target.reply_text(v25_hub_text(uid),parse_mode='HTML',reply_markup=v25_hub_keyboard(uid))

async def v25_reminders_menu(update,context):
    uid=update.effective_user.id; fa=lang(uid)=="fa"
    c=db(); rows=c.execute("SELECT * FROM important_reminders WHERE user_id=? AND enabled=1 ORDER BY remind_at LIMIT 30",(uid,)).fetchall(); c.close()
    kb=[[InlineKeyboardButton(f"🔔 {r['title']} — {r['remind_at'].replace('T',' ')}",callback_data=f"v25:remview:{r['id']}")] for r in rows]
    kb.append([InlineKeyboardButton("➕ یادآوری جدید" if fa else "➕ New Reminder",callback_data="v25:remadd")])
    kb.append([InlineKeyboardButton("⬅️ بازگشت" if fa else "⬅️ Back",callback_data="v25:hub"),main_menu_button(uid)])
    if rows:
        listing="\n".join(f"• {r['title']} — {r['remind_at'].replace('T',' ')}" for r in rows)
    else:
        listing="هنوز یادآوری مهمی نداری." if fa else "No important reminders yet."
    heading="🔔 <b>یادآوری‌های مهم</b>" if fa else "🔔 <b>Important Reminders</b>"
    await update.message.reply_text(heading+"\n\n"+listing,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def v25_profile_menu(update,context,edit=False):
    uid=update.effective_user.id; fa=lang(uid)=='fa'; p=v25_profile(uid); c=db(); perms=c.execute("SELECT scope,field,enabled FROM profile_share WHERE user_id=? ORDER BY scope,field",(uid,)).fetchall(); c.close()
    text=(f"👤 <b>اطلاعات من</b>\n\nنام: {html.escape(p['full_name'] or '—')}\n📱 تلفن: {html.escape(p['phone'] or '—')}\n📧 ایمیل: {html.escape(p['email'] or '—')}\n🆔 Telegram ID: {p['telegram_id'] or uid}\n\nبرای هر قابلیت مشخص کن چه اطلاعاتی مجاز است. هیچ فیلدی اجباری نیست.") if fa else (f"👤 <b>My Profile</b>\n\nName: {html.escape(p['full_name'] or '—')}\n📱 Phone: {html.escape(p['phone'] or '—')}\n📧 Email: {html.escape(p['email'] or '—')}\n🆔 Telegram ID: {p['telegram_id'] or uid}\n\nChoose which fields may be shared with each feature. Nothing is mandatory.")
    kb=[[InlineKeyboardButton('✏️ نام' if fa else '✏️ Name',callback_data='v25:profile_edit:name')],[InlineKeyboardButton('📱 تلفن' if fa else '📱 Phone',callback_data='v25:profile_edit:phone')],[InlineKeyboardButton('📧 ایمیل' if fa else '📧 Email',callback_data='v25:profile_edit:email')],[InlineKeyboardButton('🔐 مجوز استفاده از اطلاعات' if fa else '🔐 Data sharing',callback_data='v25:profile_share')],[InlineKeyboardButton('⬅️ بازگشت' if fa else '⬅️ Back',callback_data='v25:hub'),main_menu_button(uid)]]
    await (update.callback_query.message if update.callback_query else update.message).reply_text(text,parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_profile_share_menu(update,context):
    uid=update.effective_user.id; fa=lang(uid)=='fa'; scopes=[('booking','🔗 رزرو'),('payment','💳 پرداخت'),('survey','⭐ نظرسنجی'),('crm','👥 مشتری')]; fields=['full_name','phone','email']
    kb=[]
    for scope, label in scopes:
        for field in fields:
            r=_v25_exec("SELECT enabled FROM profile_share WHERE user_id=? AND scope=? AND field=?",(uid,scope,field),fetchone=True)
            enabled=1 if r is None else int(r['enabled'])
            fl={'full_name':'نام','phone':'تلفن','email':'ایمیل'}[field] if fa else {'full_name':'Name','phone':'Phone','email':'Email'}[field]
            sl=label if fa else scope.title()
            kb.append([InlineKeyboardButton(f"{'🟢' if enabled else '🔴'} {sl} — {fl}",callback_data=f"v25:share:{scope}:{field}")])
    kb.append([InlineKeyboardButton('⬅️ بازگشت' if fa else '⬅️ Back',callback_data='v25:profile'),main_menu_button(uid)])
    await update.callback_query.message.edit_text('🔐 <b>مجوز استفاده از اطلاعات</b>\n\nسبز یعنی اجازه استفاده؛ قرمز یعنی استفاده نشود.' if fa else '🔐 <b>Data Sharing</b>\n\nGreen means allowed; red means not shared.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

def v25_portfolio_menu_keyboard(uid):
    fa=lang(uid)=='fa'; return InlineKeyboardMarkup([[InlineKeyboardButton('➕ افزودن سرمایه' if fa else '➕ Add Asset',callback_data='v25:portadd')],[InlineKeyboardButton('📊 خلاصه سرمایه' if fa else '📊 Portfolio Summary',callback_data='v25:portsummary')],[InlineKeyboardButton('⬅️ بازگشت' if fa else '⬅️ Back',callback_data='v25:hub'),main_menu_button(uid)]])

async def v25_portfolio_menu(update,context):
    uid=update.effective_user.id; c=db(); rows=c.execute("SELECT * FROM portfolio_assets WHERE user_id=? AND enabled=1 ORDER BY id DESC",(uid,)).fetchall(); c.close(); lines=['💰 <b>سرمایه‌های من</b>','']
    if rows:
        for r in rows: lines.append(f"• {html.escape(r['title'])} — مقدار {r['quantity']:g} | قیمت خرید {r['buy_price_rial']:,.0f} ریال")
    else: lines.append('هنوز سرمایه‌ای ثبت نکرده‌ای.')
    await (update.callback_query.message if update.callback_query else update.message).reply_text('\n'.join(lines),parse_mode='HTML',reply_markup=v25_portfolio_menu_keyboard(uid))

async def v25_portfolio_summary(update,context):
    uid=update.effective_user.id; c=db(); rows=c.execute("SELECT * FROM portfolio_assets WHERE user_id=? AND enabled=1",(uid,)).fetchall(); c.close(); total_cost=sum(float(r['quantity'])*float(r['buy_price_rial'])+float(r['fees_rial'] or 0) for r in rows)
    text=f"📊 <b>خلاصه سرمایه</b>\n\n💰 هزینه خرید ثبت‌شده: {total_cost:,.0f} ریال\n\nبرای سود/زیان لحظه‌ای، قیمت جاری هر دارایی از بخش «قیمت بازار» گرفته می‌شود."
    await update.callback_query.message.edit_text(text,parse_mode='HTML',reply_markup=v25_portfolio_menu_keyboard(uid))

async def v25_installments_menu(update,context):
    uid=update.effective_user.id; c=db(); rows=c.execute("SELECT * FROM installment_plans WHERE user_id=? AND enabled=1 ORDER BY first_due_date",(uid,)).fetchall(); c.close(); lines=['💳 <b>اقساط و تسهیلات</b>','']
    if rows:
        for r in rows: lines.append(f"🏦 {html.escape(r['bank_name'])} — {html.escape(r['title'])} — قسط: {r['monthly_rial']:,.0f} ریال")
    else: lines.append('تسهیلاتی ثبت نشده.')
    kb=[]
    for r in rows: kb.append([InlineKeyboardButton(f"🏦 {r['bank_name']} — {r['title']}",callback_data=f"v25:instview:{r['id']}")])
    kb.append([InlineKeyboardButton('➕ ثبت تسهیلات' if lang(uid)=='fa' else '➕ Add Loan',callback_data='v25:instadd')])
    kb.append([InlineKeyboardButton('⬅️ بازگشت' if lang(uid)=='fa' else '⬅️ Back',callback_data='v25:hub'),main_menu_button(uid)])
    await (update.callback_query.message if update.callback_query else update.message).reply_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

def v25_calc_installment(principal, annual_interest, months):
    principal=float(principal); months=max(1,int(months)); rate=float(annual_interest)/100/12
    if rate==0: monthly=principal/months
    else: monthly=principal*rate*((1+rate)**months)/(((1+rate)**months)-1)
    total=monthly*months; interest=total-principal
    return round(monthly),round(interest),round(total)

def v25_banks():
    return ['ملی ایران','سپهر صادرات','تجارت','ملت','رفاه کارگران','پارسیان','پاسارگاد','سامان','سپه','مسکن','کشاورزی','شهر','دی','سینا','آینده','گردشگری','خاورمیانه','ایران‌زمین','مهر ایران','رسالت','پست بانک ایران']

def v25_bank_keyboard(uid):
    fa=lang(uid)=='fa'; rows=[[InlineKeyboardButton('🏦 '+b,callback_data=f'v25:instbank:{i}')] for i,b in enumerate(v25_banks())]; rows.append([InlineKeyboardButton('✏️ بانک دلخواه' if fa else '✏️ Custom Bank',callback_data='v25:instbank_custom')]); rows.append([InlineKeyboardButton('⬅️ بازگشت' if fa else '⬅️ Back',callback_data='v25:installments'),main_menu_button(uid)]); return InlineKeyboardMarkup(rows)

def v25_rates_keyboard(uid):
    vals=[0,1,2,3,4,5,7,10,12,14,15,16,17,18,20,21,22,23,24,25,26,27,28,29,30]
    rows=[[InlineKeyboardButton(f'{x}٪',callback_data=f'v25:instrate:{x}'),InlineKeyboardButton(f'{x+1}٪',callback_data=f'v25:instrate:{x+1}')] for x in vals[::2] if x<30]
    rows.append([InlineKeyboardButton('✏️ نرخ دلخواه' if lang(uid)=='fa' else '✏️ Custom Rate',callback_data='v25:instrate_custom')]); rows.append([InlineKeyboardButton('⬅️ بازگشت' if lang(uid)=='fa' else '⬅️ Back',callback_data='v25:instadd'),main_menu_button(uid)]); return InlineKeyboardMarkup(rows)

async def v25_business_menu(update,context):
    uid=update.effective_user.id; fa=lang(uid)=='fa'; ensure_business_profile(uid)
    rows=[]
    items=[('v25:bizprofile','🏪 اطلاعات کسب‌وکار','🏪 Business Info','customer_business_settings'),('v25:customers','👥 مدیریت مشتری و نوبت‌دهی','👥 Customer & Appointments','customers'),('v25:services','🛠️ خدمات','🛠️ Services','business_services'),('v25:bizfinance','📒 مالی مشتریان','📒 Customer Finance','business_finance'),('v25:bizpay','💳 پرداخت‌ها','💳 Payments','booking_payments'),('v25:surveyadmin','⭐ نظرسنجی','⭐ Surveys','surveys'),('v25:plans','💎 پلن‌های VIP','💎 VIP Plans','vip_plans'),('v25:sms','📱 پیامک','📱 SMS','sms'),('v25:customermsg','📩 پیام به مشتریان','📩 Message Customers','customer_customers'),('v25:bookinglink','🔗 لینک رزرو','🔗 Booking Link','customer_booking_link')]
    for cb,ft,et,key in items:
        if v25_allowed(uid,key): rows.append([InlineKeyboardButton(ft if fa else et,callback_data=cb)])
    rows.append([InlineKeyboardButton('⬅️ بازگشت' if fa else '⬅️ Back',callback_data='v25:hub'),main_menu_button(uid)])
    text='🏪 <b>پنل کسب‌وکار</b>\n\nهمه بخش‌ها قابل فعال/غیرفعال شدن هستند.' if fa else '🏪 <b>Business Panel</b>\n\nEvery module can be enabled or disabled.'
    await (update.callback_query.message if update.callback_query else update.message).reply_text(text,parse_mode='HTML',reply_markup=InlineKeyboardMarkup(rows))

async def v25_services_menu(update,context):
    uid=update.effective_user.id; c=db(); rows=c.execute("SELECT * FROM business_services WHERE owner_user_id=? ORDER BY id DESC",(uid,)).fetchall(); c.close(); lines=['🛠️ <b>خدمات</b>','']
    lines += [f"• {html.escape(r['name'])} | ⏱ {r['duration_minutes']} دقیقه | 💰 {r['price_rial']:,.0f} ریال | {'🟢' if r['enabled'] else '🔴'}" for r in rows] or ['هنوز خدمتی ثبت نشده.']
    kb=[[InlineKeyboardButton(f"{'🟢' if r['enabled'] else '🔴'} {r['name']}",callback_data=f"v25:service_toggle:{r['id']}")] for r in rows]
    kb.append([InlineKeyboardButton('➕ افزودن خدمت' if lang(uid)=='fa' else '➕ Add Service',callback_data='v25:serviceadd')]); kb.append([InlineKeyboardButton('⬅️ بازگشت' if lang(uid)=='fa' else '⬅️ Back',callback_data='v25:business'),main_menu_button(uid)])
    await (update.callback_query.message if update.callback_query else update.message).reply_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_payment_methods_menu(update,context):
    uid=update.effective_user.id; c=db(); rows=c.execute("SELECT * FROM payment_methods WHERE owner_user_id=? ORDER BY method_type",(uid,)).fetchall(); g=c.execute("SELECT * FROM gateway_configs WHERE owner_user_id=?",(uid,)).fetchone(); c.close(); card=next((r for r in rows if r['method_type']=='card'),None); online=next((r for r in rows if r['method_type']=='online'),None)
    text='💳 <b>روش‌های پرداخت</b>\n\n'+f"💵 کارت‌به‌کارت: {'🟢' if card and card['enabled'] else '🔴'}\n💳 پرداخت آنلاین: {'🟢' if g and g['enabled'] else '🔴'}\n"
    kb=[[InlineKeyboardButton('💵 تنظیم کارت‌به‌کارت',callback_data='v25:card')],[InlineKeyboardButton('💳 تنظیم درگاه',callback_data='v25:gateway')],[InlineKeyboardButton('⬅️ بازگشت',callback_data='v25:business'),main_menu_button(uid)]]
    await update.callback_query.message.edit_text(text,parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_survey_admin(update,context):
    uid=update.effective_user.id; c=db(); rows=c.execute("SELECT * FROM survey_questions WHERE owner_user_id=? ORDER BY id",(uid,)).fetchall(); c.close(); text='⭐ <b>نظرسنجی مشتری</b>\n\n'+('\n'.join(f"{'🟢' if r['enabled'] else '🔴'} {r['question']}" for r in rows) if rows else 'سؤال پیش‌فرض هنگام ثبت اولین نظرسنجی ساخته می‌شود.')
    kb=[[InlineKeyboardButton('➕ سؤال سفارشی',callback_data='v25:surveyadd')],[InlineKeyboardButton('⬅️ بازگشت',callback_data='v25:business'),main_menu_button(uid)]]
    await update.callback_query.message.edit_text(text,parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_vip_plans(update,context):
    uid=update.effective_user.id; c=db(); rows=c.execute("SELECT * FROM subscription_plans_v25 WHERE enabled=1 ORDER BY duration_minutes",()).fetchall(); c.close(); lines=['💎 <b>پلن‌های VIP</b>','']
    lines += [f"• {r['name']} — {r['price_rial']:,.0f} ریال" for r in rows]
    kb=[[InlineKeyboardButton(r['name'],callback_data=f"v25:buyplan:{r['id']}")] for r in rows]
    kb.append([InlineKeyboardButton('⬅️ بازگشت' if lang(uid)=='fa' else '⬅️ Back',callback_data='v25:business'),main_menu_button(uid)])
    await (update.callback_query.message if update.callback_query else update.message).reply_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_admin_feature_status(update,context):
    uid=update.effective_user.id; c=db(); rows=c.execute("SELECT key,enabled FROM feature_flags WHERE key IN (%s) ORDER BY key" % ','.join('?'*len(V25_FEATURE_KEYS)),tuple(V25_FEATURE_KEYS)).fetchall(); c.close(); text='🔧 <b>وضعیت قابلیت‌های جدید</b>\n\n'; kb=[]
    for r in rows:
        key=r['key']; label=V25_FEATURE_LABELS.get(key,key); text+=f"{'🟢' if r['enabled'] else '🔴'} {label}\n"; kb.append([InlineKeyboardButton(f"{'🟢' if r['enabled'] else '🔴'} {label}",callback_data=f'v25:feat:{key}')])
    kb.append([InlineKeyboardButton('⬅️ پنل مدیریت' if lang(uid)=='fa' else '⬅️ Admin Panel',callback_data='adm:stats')])
    await update.callback_query.message.edit_text(text,parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_voice_prompt(update,context):
    uid=update.effective_user.id; fa=lang(uid)=='fa'; clear_flow(context); context.user_data['v25_voice_mode']=True
    await update.callback_query.message.edit_text('🎙️ <b>دستیار صوتی</b>\n\nویست رو بفرست؛ من متنش را درمی‌آورم و قبل از اجرا بهت نشان می‌دهم. می‌توانی همان متن را ویرایش کنی یا با یک ویس دیگر اصلاحش کنی.' if fa else '🎙️ <b>Voice Assistant</b>\n\nSend a voice message. I will transcribe it, show you the result, and let you edit or correct it before execution.',parse_mode='HTML',reply_markup=v25_back(uid))

async def v25_transcribe_voice(file_bytes, filename='voice.ogg'):
    api_key=os.environ.get('OPENAI_API_KEY','').strip()
    if not api_key:
        raise RuntimeError('OpenAI transcription provider is not configured. Set OPENAI_API_KEY for voice transcription.')
    model=os.environ.get('OPENAI_TRANSCRIBE_MODEL','gpt-4o-mini-transcribe').strip()
    boundary='----MyTasksBoundary'+hashlib.sha256(os.urandom(16)).hexdigest()
    body=[]
    def add_field(name,value):
        body.append(f'--{boundary}\r\n'.encode()); body.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()); body.append(str(value).encode()); body.append(b'\r\n')
    def add_file(name,fn,data,mime):
        body.append(f'--{boundary}\r\n'.encode()); body.append(f'Content-Disposition: form-data; name="{name}"; filename="{fn}"\r\n'.encode()); body.append(f'Content-Type: {mime}\r\n\r\n'.encode()); body.append(data); body.append(b'\r\n')
    add_file('file',filename,file_bytes,'audio/ogg')
    add_field('model',model)
    add_field('response_format','json')
    req=urllib.request.Request('https://api.openai.com/v1/audio/transcriptions',data=b''.join(body)+f'--{boundary}--\r\n'.encode(),headers={'Authorization':f'Bearer {api_key}','Content-Type':f'multipart/form-data; boundary={boundary}'},method='POST')
    with urllib.request.urlopen(req,timeout=60) as resp:
        data=json.loads(resp.read().decode('utf-8')); return (data.get('text') or '').strip()

async def v25_voice_handler(update,context):
    uid=update.effective_user.id
    allowed, reason = feature_token_gate(uid, "voice")
    if not allowed:
        await update.message.reply_text(token_gate_message(uid, "voice", reason), reply_markup=keyboard(uid)); return
    if not v25_allowed(uid,'voice'): return
    try:
        tg_file=await update.message.voice.get_file(); data=await tg_file.download_as_bytearray(); text=await v25_transcribe_voice(bytes(data))
    except Exception as e:
        logger.warning('Voice transcription failed: %s',type(e).__name__); await update.message.reply_text('🎙️ فعلاً امکان تبدیل این ویس به متن فراهم نیست. لطفاً کمی بعد دوباره تلاش بفرمایید. 🌷' if lang(uid)=='fa' else '🎙️ Voice transcription is temporarily unavailable. Please try again later. 🌷'); return
    if not text:
        await update.message.reply_text('❌ متن قابل تشخیصی از ویس پیدا نشد.'); return
    context.user_data['v25_voice_text']=text
    if re.search(r'قیمت|چند شده|نرخ|price|how much|cost',text,re.I):
        context.user_data['v25_voice_action']='price';
    elif re.search(r'هدف|یادم|یادآوری|remind|goal',text,re.I):
        context.user_data['v25_voice_action']='goal'
    elif admin_guard(uid) and re.search(r'فعال|غیرفعال|روشن|خاموش|enable|disable',text,re.I):
        context.user_data['v25_voice_action']='admin'
    else: context.user_data['v25_voice_action']='note'
    await update.message.reply_text(('🎙️ <b>متن تشخیص‌داده‌شده</b>\n\n'+html.escape(text)+'\n\nقبل از اجرا می‌توانی آن را ویرایش کنی یا تأییدش کنی.'),parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ تأیید',callback_data='v25:voice_confirm'),InlineKeyboardButton('✏️ ویرایش متن',callback_data='v25:voice_edit')],[InlineKeyboardButton('🎙️ اصلاح با ویس',callback_data='v25:voice_retry')],[main_menu_button(uid)]]))

async def v25_voice_confirm(update,context):
    uid=update.effective_user.id; text=context.user_data.get('v25_voice_text',''); action=context.user_data.get('v25_voice_action'); await update.callback_query.answer()
    if action=='price':
        # Map common spoken price requests to the market screen.
        if 'طلا' in text or 'gold' in text.lower(): asset='gold18'
        elif 'نقره' in text or 'silver' in text.lower(): asset='silver'
        elif 'مس' in text or 'copper' in text.lower(): asset='copper'
        elif 'دلار' in text or 'usd' in text.lower(): asset='usd'
        else: asset='all'
        context.user_data.clear(); await v25_show_price(update,context,asset); return
    if action=='goal':
        context.user_data.clear(); await update.callback_query.message.edit_text('🎯 متن هدف آماده شد.\n\n'+html.escape(text)+'\n\nمی‌توانی بعداً تاریخ و ساعت را از بخش اهداف تنظیم کنی.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🎯 ثبت هدف',callback_data='goals:main')],[main_menu_button(uid)]])); return
    if action=='admin' and admin_guard(uid):
        context.user_data.clear(); await update.callback_query.message.edit_text('🛡️ متن فرمان آماده است. برای فرمان‌های مدیریتی حساس، تأیید مستقیم از پنل امن را پیشنهاد می‌کنم.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🛠️ پنل مدیریت',callback_data='adm:stats')]])); return
    context.user_data.clear(); await update.callback_query.message.edit_text('✅ ثبت شد. اگر می‌خواهی این متن به هدف یا یادآوری تبدیل شود، از منوی همان بخش استفاده کن.',reply_markup=v25_back(uid))

async def v25_voice_edit(update,context):
    uid=update.effective_user.id; context.user_data['v25_editing_voice']=True; await update.callback_query.answer(); await update.callback_query.message.edit_text('✏️ متن اصلاح‌شده را بفرست. همان اطلاعات را ویرایش کن؛ لازم نیست از اول همه‌چیز را بنویسی.',reply_markup=v25_back(uid,'v25:voice_edit_cancel'))

async def v25_add_text_state(update,context):
    # handled by text_router wrapper
    return False

async def v25_add_reminder_save(update,context):
    uid=update.effective_user.id; title=context.user_data.get('v25_rem_title'); raw=update.message.text.strip();
    dt=None
    try: dt=parse_user_datetime(raw)
    except Exception: dt=None
    if not dt: await update.message.reply_text('❌ فرمت تاریخ/ساعت نامعتبر است. نمونه: ۱۴۰۵/۰۶/۰۳ ۱۲:۰۰'); return True
    dt=dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt
    _v25_exec('INSERT OR IGNORE INTO important_reminders(user_id,title,remind_at,created_at,updated_at) VALUES(?,?,?,?,?)',(uid,title,dt.isoformat(),_v25_now(),_v25_now()))
    clear_flow(context); await update.message.reply_text('✅ یادآوری ثبت شد.',reply_markup=v25_hub_keyboard(uid)); return True

async def v25_installment_text_save(update,context):
    uid=update.effective_user.id; mode=context.user_data.get('v25_mode'); text=normalize_digits(update.message.text.strip())
    if mode=='inst_bank': context.user_data['inst_bank']=text; context.user_data['v25_mode']='inst_title'; await update.message.reply_text('📝 عنوان تسهیلات را بفرست یا «قسط» بنویس:'); return True
    if mode=='inst_title': context.user_data['inst_title']=text or 'قسط'; context.user_data['v25_mode']='inst_principal'; await update.message.reply_text('💰 مبلغ اصل وام را به ریال وارد کن:'); return True
    if mode=='inst_principal':
        try: principal=int(float(text.replace(',','')))
        except Exception: await update.message.reply_text('❌ مبلغ نامعتبر است.'); return True
        if principal <= 0 or principal > 10**15:
            await update.message.reply_text('❌ مبلغ باید بیشتر از صفر و در محدوده مجاز باشد.'); return True
        context.user_data['inst_principal']=principal; context.user_data['v25_mode']='inst_rate'; await update.message.reply_text('📈 نرخ سود را انتخاب کن:',reply_markup=v25_rates_keyboard(uid)); return True
    if mode=='inst_rate_custom':
        try: rate=float(text.replace('%',''))
        except Exception: await update.message.reply_text('❌ نرخ نامعتبر است.'); return True
        context.user_data['inst_rate']=rate; context.user_data['v25_mode']='inst_months'; await update.message.reply_text('🔢 تعداد ماه بازپرداخت را بفرست:'); return True
    if mode=='inst_months':
        try: months=int(text)
        except Exception: await update.message.reply_text('❌ تعداد ماه نامعتبر است.'); return True
        if not 1 <= months <= 600:
            await update.message.reply_text('❌ تعداد ماه باید بین ۱ تا ۶۰۰ باشد.'); return True
        context.user_data['inst_months']=months; context.user_data['v25_mode']='inst_first_date'; await update.message.reply_text('📅 تاریخ اولین قسط را بفرست. نمونه: ۱۴۰۵/۰۶/۰۳'); return True
    if mode=='inst_first_date':
        try: d=parse_user_date(text)
        except Exception: await update.message.reply_text('❌ تاریخ نامعتبر است.'); return True
        context.user_data['inst_first_date']=d; principal=context.user_data['inst_principal']; rate=context.user_data['inst_rate']; months=context.user_data['inst_months']; monthly,interest,total=v25_calc_installment(principal,rate,months); context.user_data.update(inst_monthly=monthly,inst_interest=interest,inst_total=total)
        kb=InlineKeyboardMarkup([[InlineKeyboardButton('✅ ذخیره',callback_data='v25:instsave'),InlineKeyboardButton('✏️ ویرایش',callback_data='v25:instedit')],[main_menu_button(uid)]])
        await update.message.reply_text(f'🧮 <b>محاسبه تقریبی</b>\n\nاصل: {principal:,.0f} ریال\nسود سالانه: {rate:g}%\nمدت: {months} ماه\n\n💵 قسط ماهانه: {monthly:,.0f} ریال\n💰 سود کل: {interest:,.0f} ریال\n💳 مجموع بازپرداخت: {total:,.0f} ریال',parse_mode='HTML',reply_markup=kb); return True
    if mode=='booking_name':
        context.user_data['public_name']='' if text=='-' else text; context.user_data['v25_mode']='booking_phone'; await update.message.reply_text('📱 شماره تلفن را بفرست یا «-» بزن. این هم اختیاری است:'); return True
    if mode=='booking_phone':
        context.user_data['public_phone']='' if text=='-' else text; context.user_data.pop('v25_mode',None); owner=context.user_data.get('booking_owner'); sid=int(context.user_data.get('booking_service_id') or 0);
        try: aid,service,amount,name,phone=await v25_create_booking(context,uid,owner,sid); prof=ensure_business_profile(owner); context.user_data['booking_appointment_id']=aid; context.user_data['booking_amount_rial']=amount; await context.bot.send_message(owner,f"🎉 <b>رزرو جدید ثبت شد!</b>\n\n🏪 {html.escape(prof['business_name'] or 'کسب‌وکار')}\n👤 {html.escape(name)}\n📅 {jalali_pretty_date(context.user_data['booking_date'])}\n⏰ {context.user_data['booking_time']}",parse_mode='HTML');
        except ValueError: await update.message.reply_text('❌ این زمان دیگر آزاد نیست.'); return True
        # Reuse a small confirmation message with inline buttons.
        g=_v25_exec('SELECT * FROM gateway_configs WHERE owner_user_id=?',(owner,),fetchone=True); pm=_v25_exec("SELECT * FROM payment_methods WHERE owner_user_id=? AND method_type='card' AND enabled=1",(owner,),fetchone=True); kb=[]
        if amount and g and g['enabled'] and g['payment_link']: kb.append([InlineKeyboardButton('💳 پرداخت آنلاین',url=g['payment_link'])])
        if amount and pm: kb.append([InlineKeyboardButton('💵 کارت‌به‌کارت',callback_data=f'v25:bookingcard:{aid}')])
        kb.append([InlineKeyboardButton('📅 رزروهای من',callback_data='cust:mybookings'),main_menu_button(uid)]); await update.message.reply_text(f"✅ <b>رزرو شما با موفقیت ثبت شد.</b>\n\n🏪 {html.escape(prof['business_name'] or 'کسب‌وکار')}\n📅 {jalali_pretty_date(context.user_data.get('booking_date'))}\n⏰ {context.user_data.get('booking_time')}"+(f"\n💰 هزینه: {amount:,.0f} ریال" if amount else ''),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb)); clear_flow(context); return True
    if mode=='survey_comment':
        aid=int(context.user_data.get('survey_appointment_id')); c=db(); c.execute('UPDATE survey_responses SET comment=? WHERE appointment_id=?',(text,aid)); owner=c.execute('SELECT owner_user_id FROM appointments WHERE id=?',(aid,)).fetchone(); c.commit(); c.close();
        if owner:
            try: await context.bot.send_message(owner,f'💡 پیشنهاد مشتری برای رزرو #{aid}:\n{text}')
            except Exception: pass
        clear_flow(context); await update.message.reply_text('✅ پیشنهادت ثبت شد. ممنون که کمک می‌کنی بهتر شویم.',reply_markup=keyboard(uid)); return True
    if mode=='port_asset':
        context.user_data['port_title']=text; context.user_data['v25_mode']='port_quantity'; await update.message.reply_text('📦 مقدار را بفرست. مثال: 10'); return True
    if mode=='port_quantity':
        context.user_data['port_qty']=float(text.replace(',','')); context.user_data['v25_mode']='port_buyprice'; await update.message.reply_text('💰 قیمت خرید هر واحد را به ریال بفرست:'); return True
    if mode=='port_buyprice':
        context.user_data['port_price']=float(text.replace(',','')); context.user_data['v25_mode']='port_date'; await update.message.reply_text('📅 تاریخ خرید را بفرست. مثال: ۱۴۰۵/۰۵/۳۰'); return True
    if mode=='port_date':
        try: d=parse_user_date(text)
        except Exception: await update.message.reply_text('❌ تاریخ نامعتبر است.'); return True
        s=context.user_data; _v25_exec('INSERT INTO portfolio_assets(user_id,asset_code,title,quantity,buy_price_rial,buy_date,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(uid,'custom',s['port_title'],s['port_qty'],s['port_price'],d,_v25_now(),_v25_now())); clear_flow(context); await update.message.reply_text('✅ سرمایه ثبت شد.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('💰 سرمایه‌های من',callback_data='v25:portfolio')],[main_menu_button(uid)]])); return True
    if mode=='profile_edit:name':
        _v25_exec('UPDATE user_profile SET full_name=?,updated_at=? WHERE user_id=?',(text,_v25_now(),uid)); clear_flow(context); await update.message.reply_text('✅ نام به‌روزرسانی شد.',reply_markup=keyboard(uid)); return True
    if mode=='profile_edit:phone':
        _v25_exec('UPDATE user_profile SET phone=?,updated_at=? WHERE user_id=?',(text,_v25_now(),uid)); clear_flow(context); await update.message.reply_text('✅ شماره تلفن به‌روزرسانی شد.',reply_markup=keyboard(uid)); return True
    if mode=='profile_edit:email':
        _v25_exec('UPDATE user_profile SET email=?,updated_at=? WHERE user_id=?',(text,_v25_now(),uid)); clear_flow(context); await update.message.reply_text('✅ ایمیل به‌روزرسانی شد.',reply_markup=keyboard(uid)); return True
    if mode=='v25_edit_voice':
        context.user_data['v25_voice_text']=text; context.user_data['v25_voice_action']='note'; context.user_data.pop('v25_editing_voice',None); await update.message.reply_text('✅ متن اصلاح شد. حالا تأییدش کن.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ تأیید',callback_data='v25:voice_confirm')],[main_menu_button(uid)]])); return True
    if mode=='v25_sms_test':
        await update.message.reply_text('📱 تست SMS در نسخه تمیز به تنظیمات سرویس پیامکی نیاز دارد. ابتدا endpoint و API key را در پنل کسب‌وکار ثبت کن.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ پیامک',callback_data='v25:sms')],[main_menu_button(uid)]])); clear_flow(context); return True
    return False

async def v25_business_text_save(update,context):
    uid=update.effective_user.id; mode=context.user_data.get('v25_mode'); text=update.message.text.strip()
    if mode=='service_name': context.user_data['service_name']=text; context.user_data['v25_mode']='service_duration'; await update.message.reply_text('⏱ مدت خدمت را به دقیقه بفرست:'); return True
    if mode=='service_duration':
        try: duration=int(normalize_digits(text))
        except Exception: await update.message.reply_text('❌ مدت نامعتبر است.'); return True
        if not 1 <= duration <= 1440: await update.message.reply_text('❌ مدت خدمت باید بین ۱ تا ۱۴۴۰ دقیقه باشد.'); return True
        context.user_data['service_duration']=duration; context.user_data['v25_mode']='service_price'; await update.message.reply_text('💰 قیمت خدمت را به ریال بفرست:'); return True
    if mode=='service_price':
        try: price=int(float(normalize_digits(text).replace(',','')))
        except Exception: await update.message.reply_text('❌ قیمت نامعتبر است.'); return True
        if price < 0 or price > 10**15: await update.message.reply_text('❌ قیمت باید در محدوده مجاز باشد.'); return True
        s=context.user_data; now=_v25_now(); _v25_exec('INSERT INTO business_services(owner_user_id,name,duration_minutes,price_rial,created_at,updated_at) VALUES(?,?,?,?,?,?)',(uid,s['service_name'][:200],s['service_duration'],price,now,now)); clear_flow(context); await update.message.reply_text('✅ خدمت ثبت شد.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🛠️ خدمات',callback_data='v25:services')],[main_menu_button(uid)]])); return True
    if mode=='card_number': context.user_data['card_number']=text; context.user_data['v25_mode']='card_name'; await update.message.reply_text('👤 نام صاحب کارت را بفرست یا - بزن:'); return True
    if mode=='card_name':
        title='' if text=='-' else text; details=f"شماره کارت: {context.user_data['card_number']}\nبه نام: {title}"; now=_v25_now(); _v25_exec('INSERT INTO payment_methods(owner_user_id,method_type,enabled,title,details,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(owner_user_id,method_type) DO UPDATE SET enabled=1,title=excluded.title,details=excluded.details,updated_at=excluded.updated_at',(uid,'card',1,'کارت‌به‌کارت',details,now)); clear_flow(context); await update.message.reply_text('✅ کارت‌به‌کارت فعال شد.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('💳 پرداخت‌ها',callback_data='v25:bizpay')],[main_menu_button(uid)]])); return True
    if mode=='gateway_link':
        now=_v25_now(); _v25_exec('INSERT INTO gateway_configs(owner_user_id,provider,enabled,payment_link,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(owner_user_id) DO UPDATE SET enabled=1,payment_link=excluded.payment_link,updated_at=excluded.updated_at',(uid,'custom',1,text,now)); clear_flow(context); await update.message.reply_text('✅ لینک درگاه ذخیره شد. تا وقتی کلید درگاه در پنل فعال باشد، پرداخت آنلاین نمایش داده می‌شود.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('💳 پرداخت‌ها',callback_data='v25:bizpay')],[main_menu_button(uid)]])); return True
    if mode=='survey_question':
        now=_v25_now(); code=hashlib.sha256(text.encode()).hexdigest()[:10]; _v25_exec('INSERT OR IGNORE INTO survey_questions(owner_user_id,code,question,created_at) VALUES(?,?,?,?)',(uid,code,text,now)); clear_flow(context); await update.message.reply_text('✅ سؤال نظرسنجی اضافه شد.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⭐ نظرسنجی',callback_data='v25:surveyadmin')],[main_menu_button(uid)]])); return True
    if mode=='bizname_v25':
        _v25_exec('UPDATE business_profiles SET business_name=?,updated_at=? WHERE user_id=?',(text,_v25_now(),uid)); clear_flow(context); await update.message.reply_text('✅ نام کسب‌وکار به‌روزرسانی شد.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🏪 پنل کسب‌وکار',callback_data='v25:business')],[main_menu_button(uid)]])); return True
    return False

async def v25_installment_text_dispatch(update,context):
    return await v25_installment_text_save(update,context) or await v25_business_text_save(update,context)

async def v25_send_survey(bot, owner, appointment_id):
    c=db(); r=c.execute("SELECT a.*,c.name,c.telegram_user_id FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.id=? AND a.owner_user_id=?",(appointment_id,owner)).fetchone(); c.close()
    if not r or not r['telegram_user_id']: return
    c=db(); qs=c.execute("SELECT * FROM survey_questions WHERE owner_user_id=? AND enabled=1 ORDER BY id LIMIT 10",(owner,)).fetchall()
    if not qs:
        c.executemany("INSERT OR IGNORE INTO survey_questions(owner_user_id,code,question,created_at) VALUES(?,?,?,?)",[(owner,code,q,_v25_now()) for code,q in V25_DEFAULT_QUESTIONS]); c.commit(); qs=c.execute("SELECT * FROM survey_questions WHERE owner_user_id=? AND enabled=1 ORDER BY id LIMIT 10",(owner,)).fetchall()
    c.close()
    kb=[[InlineKeyboardButton('😍 5',callback_data=f'v25:survey_rate:{appointment_id}:5'),InlineKeyboardButton('🙂 4',callback_data=f'v25:survey_rate:{appointment_id}:4'),InlineKeyboardButton('😐 3',callback_data=f'v25:survey_rate:{appointment_id}:3')],[InlineKeyboardButton('🙁 2',callback_data=f'v25:survey_rate:{appointment_id}:2'),InlineKeyboardButton('😡 1',callback_data=f'v25:survey_rate:{appointment_id}:1')]]
    await bot.send_message(r['telegram_user_id'],'🌷 ممنون که ما را انتخاب کردی!\n\n⭐ تجربه کلی‌ات چطور بود؟',reply_markup=InlineKeyboardMarkup(kb))

async def v25_reminder_job(context):
    now=datetime.now(TZ).replace(second=0,microsecond=0); c=db(); rows=c.execute("SELECT * FROM important_reminders WHERE enabled=1 AND remind_at<=? AND remind_at>=?",(now.isoformat(),(now-timedelta(minutes=1)).isoformat())).fetchall(); c.close()
    for r in rows:
        try:
            await context.bot.send_message(r['user_id'],f"🔔 {r['title']}\n\n⏰ زمان یادآوری رسید.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ انجام شد',callback_data='v25:remdone:%s'%r['id'])],[main_menu_button(r['user_id'])]]))
            _v25_exec('UPDATE important_reminders SET enabled=0,updated_at=? WHERE id=?',( _v25_now(),r['id']))
        except Exception: logger.exception('v25 reminder delivery failed')

async def v25_sms_send(owner, phone, message):
    c=db(); cfg=c.execute('SELECT * FROM sms_settings WHERE owner_user_id=?',(owner,)).fetchone(); c.close()
    if not cfg or not cfg['enabled'] or not cfg['endpoint'] or not cfg['api_key']: return False,'SMS disabled/unconfigured'
    payload={'to':phone,'message':message,'from':cfg['sender'],'api_key':cfg['api_key']}
    try:
        data=await asyncio.to_thread(fetch_url_json_post,cfg['endpoint'],payload)
        ok=True
        return ok,json.dumps(data,ensure_ascii=False)[:1000]
    except Exception as e: return False,str(e)

async def v25_receipt_handler(update,context):
    if not update.message: return
    mode=context.user_data.get('v25_mode')
    uid=update.effective_user.id
    photo=update.message.photo[-1] if update.message.photo else None; doc=update.message.document
    file_id=(photo.file_id if photo else (doc.file_id if doc else None))
    if not file_id: return

    if mode=='vip_receipt':
        plan_id=int(context.user_data.get('vip_plan_id') or 0)
        plan=_v25_exec('SELECT id,name,price_rial,duration_minutes,enabled FROM subscription_plans_v25 WHERE id=? AND enabled=1',(plan_id,),fetchone=True)
        if not plan:
            clear_flow(context); await update.message.reply_text('❌ پلن VIP معتبر نیست.',reply_markup=keyboard(uid)); return
        rid=_v25_exec('INSERT INTO vip_receipts(user_id,plan_id,amount_rial,receipt_file_id,status,created_at) VALUES(?,?,?,?,?,?)',(uid,plan_id,int(plan['price_rial'] or 0),file_id,'pending',_v25_now()),commit=True)
        clear_flow(context)
        await update.message.reply_text('📎 رسید VIP دریافت شد و برای بررسی مدیر ارسال شد. بعد از تأیید، اشتراک فعال می‌شود.',reply_markup=keyboard(uid))
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(admin_id,f'💎 <b>رسید VIP جدید</b>\n\n👤 کاربر: <code>{uid}</code>\n📦 پلن: {html.escape(plan["name"])}\n💰 مبلغ: {irr(plan["price_rial"])}\n🧾 رسید: #{rid}',parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ تأیید',callback_data=f'v25:vip_receipt:approve:{rid}'),InlineKeyboardButton('❌ رد',callback_data=f'v25:vip_receipt:reject:{rid}')]]))
                if hasattr(context.bot,'send_document') and doc:
                    await context.bot.send_document(admin_id,document=file_id)
                elif hasattr(context.bot,'send_photo') and photo:
                    await context.bot.send_photo(admin_id,photo=file_id)
            except Exception:
                logger.exception('VIP receipt admin notification failed')
        return

    if mode!='booking_receipt': return
    owner=context.user_data.get('booking_owner'); aid=context.user_data.get('booking_appointment_id'); amount=int(context.user_data.get('booking_amount_rial') or 0); now=_v25_now()
    # Safe explicit insert below (avoid any schema assumptions in the compatibility query above).
    c=db(); a=c.execute('SELECT owner_user_id,customer_id FROM appointments WHERE id=?',(aid,)).fetchone()
    if a:
        c.execute('INSERT INTO booking_payments(owner_user_id,appointment_id,customer_id,amount_rial,method,status,receipt_file_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',(owner,aid,a['customer_id'],amount,'card','pending',file_id,now,now)); c.commit()
        try: await context.bot.send_message(owner,f'📎 <b>رسید جدید دریافت شد</b>\n\n👤 مشتری\n💰 مبلغ: {amount:,.0f} ریال\n🧾 وضعیت: 🟡 در انتظار تأیید',parse_mode='HTML')
        except Exception: pass
    c.close(); clear_flow(context); await update.message.reply_text('✅ رسیدت دریافت شد و برای صاحب کسب‌وکار ارسال شد. بعد از بررسی، وضعیت پرداخت بهت اطلاع داده می‌شود.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📅 رزروهای من',callback_data='cust:mybookings')],[main_menu_button(uid)]]))

async def v25_callback(update,context):
    q=update.callback_query; uid=q.from_user.id; await q.answer(); p=q.data.split(':'); action=p[1] if len(p)>1 else 'hub'
    try:
        if action=='hub': await q.message.edit_text(v25_hub_text(uid),parse_mode='HTML',reply_markup=v25_hub_keyboard(uid)); return
        if action=='today': await q.message.edit_text(v25_hub_text(uid),parse_mode='HTML',reply_markup=v25_hub_keyboard(uid)); return
        if action=='customers':
            if not customer_feature_allowed(uid):
                await q.message.edit_text(customer_feature_message(uid), reply_markup=v25_back(uid)); return
            ensure_business_profile(uid)
            await q.message.edit_text('👥 <b>مدیریت مشتری و نوبت‌دهی</b>\n\nپنل مستقل مشتریان، نوبت‌ها، تقویم و یادآوری‌ها.', parse_mode='HTML', reply_markup=customer_keyboard(uid))
            return
        if action=='reminders': await v25_reminders_menu(update,context); return
        if action=='remadd': context.user_data['v25_mode']='rem_title'; await q.message.edit_text('✏️ عنوان یادآوری را بفرست:',reply_markup=v25_back(uid)); return
        if action=='remview':
            rid=int(p[2]); r=_v25_exec('SELECT * FROM important_reminders WHERE id=? AND user_id=?',(rid,uid),fetchone=True); await q.message.edit_text(f"🔔 {html.escape(r['title'])}\n⏰ {r['remind_at'].replace('T',' ')}",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🗑 حذف',callback_data=f'v25:remdel:{rid}')],[InlineKeyboardButton('⬅️ بازگشت',callback_data='v25:reminders'),main_menu_button(uid)]])); return
        if action=='remdel': _v25_exec('DELETE FROM important_reminders WHERE id=? AND user_id=?',(int(p[2]),uid)); await q.message.edit_text('✅ حذف شد.',reply_markup=v25_back(uid)); return
        if action=='remdone': _v25_exec('UPDATE important_reminders SET enabled=0 WHERE id=? AND user_id=?',(int(p[2]),uid)); await q.message.edit_text('✅ انجام شد.',reply_markup=v25_hub_keyboard(uid)); return
        if action=='calendar': await q.message.edit_text('📅 <b>تقویم من</b>\n\nنوبت‌ها و یادآوری‌های مهم در این تقویم یکپارچه نگهداری می‌شوند. برای مشاهده سریع، از «امروز من» یا «یادآوری‌های مهم» استفاده کن.',parse_mode='HTML',reply_markup=v25_back(uid)); return
        if action=='profile': await v25_profile_menu(update,context); return
        if action=='profile_share': await v25_profile_share_menu(update,context); return
        if action=='profile_edit': context.user_data['v25_mode']=f'profile_edit:{p[2]}'; await q.message.edit_text('✏️ مقدار جدید را بفرست. اگر نمی‌خواهی ذخیره شود «-» بزن.',reply_markup=v25_back(uid,'v25:profile')); return
        if action=='share':
            scope,field=p[2],p[3]; cur=_v25_exec('SELECT enabled FROM profile_share WHERE user_id=? AND scope=? AND field=?',(uid,scope,field),fetchone=True); en=1 if cur is None else int(cur['enabled']); _v25_exec('INSERT INTO profile_share(user_id,scope,field,enabled,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id,scope,field) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at',(uid,scope,field,0 if en else 1,_v25_now())); await v25_profile_share_menu(update,context); return
        if action=='portfolio': await v25_portfolio_menu(update,context); return
        if action=='portadd': context.user_data['v25_mode']='port_asset'; await q.message.edit_text('💰 نام دارایی را بفرست. مثال: طلای ۱۸ عیار',reply_markup=v25_back(uid)); return
        if action=='portsummary': await v25_portfolio_summary(update,context); return
        if action=='installments': await v25_installments_menu(update,context); return
        if action=='instadd': context.user_data['v25_mode']='inst_bank'; await q.message.edit_text('🏦 بانک را انتخاب کن:',reply_markup=v25_bank_keyboard(uid)); return
        if action=='instbank': context.user_data['inst_bank']=v25_banks()[int(p[2])]; context.user_data['v25_mode']='inst_title'; await q.message.edit_text('📝 عنوان تسهیلات را بفرست یا «قسط» بنویس:'); return
        if action=='instbank_custom': context.user_data['v25_mode']='inst_bank'; await q.message.edit_text('🏦 نام بانک یا مؤسسه را بفرست:'); return
        if action=='instrate': context.user_data['inst_rate']=float(p[2]); context.user_data['v25_mode']='inst_months'; await q.message.edit_text('🔢 تعداد ماه بازپرداخت را بفرست:'); return
        if action=='instrate_custom': context.user_data['v25_mode']='inst_rate_custom'; await q.message.edit_text('📈 نرخ سود را به درصد بنویس. مثال: 23'); return
        if action=='instsave':
            s=context.user_data; now=_v25_now(); c=db(); plan_id=c.execute('INSERT INTO installment_plans(user_id,bank_name,title,principal_rial,interest_pct,term_months,first_due_date,day_of_month,monthly_rial,total_interest_rial,total_payable_rial,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(uid,s['inst_bank'],s['inst_title'],s['inst_principal'],s['inst_rate'],s['inst_months'],s['inst_first_date'],datetime.fromisoformat(s['inst_first_date']).day,s['inst_monthly'],s['inst_interest'],s['inst_total'],now,now)).lastrowid
            first=datetime.fromisoformat(s['inst_first_date']).date();
            for i in range(s['inst_months']):
                # Month stepping without external dependency.
                month=first.month-1+i; year=first.year+month//12; mon=month%12+1; day=min(first.day,[31,29 if year%4==0 else 28,31,30,31,30,31,31,30,31,30,31][mon-1]); due=f'{year:04d}-{mon:02d}-{day:02d}'
                c.execute('INSERT OR IGNORE INTO installment_payments(plan_id,installment_no,due_date,amount_rial,status) VALUES(?,?,?,?,?)',(plan_id,i+1,due,s['inst_monthly'],'pending'))
            c.commit(); c.close(); clear_flow(context); await q.message.edit_text('✅ تسهیلات ذخیره شد و اقساط ماهانه در تاریخچه ساخته شد.',reply_markup=v25_back(uid,'v25:installments')); return
        if action=='instview':
            pid=int(p[2]); c=db(); plan=c.execute('SELECT * FROM installment_plans WHERE id=? AND user_id=?',(pid,uid)).fetchone(); pays=c.execute('SELECT * FROM installment_payments WHERE plan_id=? ORDER BY installment_no LIMIT 24',(pid,)).fetchall(); c.close(); paid=sum(1 for x in pays if x['status']=='paid'); text=f"🏦 <b>{html.escape(plan['bank_name'])}</b>\n📝 {html.escape(plan['title'])}\n💵 قسط: {plan['monthly_rial']:,.0f} ریال\n📈 سود: {plan['interest_pct']:g}%\n📊 پرداخت‌شده: {paid}/{plan['term_months']}\n\n"+('\n'.join(f"{'✅' if x['status']=='paid' else '⏳'} {x['installment_no']} — {x['due_date']} — {x['amount_rial']:,.0f} ریال" for x in pays))
            await q.message.edit_text(text,parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ اقساط',callback_data='v25:installments'),main_menu_button(uid)]])); return
        if action=='business': await v25_business_menu(update,context); return
        if action=='bizprofile':
            p=ensure_business_profile(uid); await q.message.edit_text(f"🏪 <b>اطلاعات کسب‌وکار</b>\n\nنام: {html.escape(p['business_name'] or '—')}\nنوع: {html.escape(p['business_type'] or '—')}\n📞 {html.escape(p['contact_phone'] or '—')}\n\nاطلاعات خالی اختیاری است.",parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✏️ نام کسب‌وکار',callback_data='v25:bizname')],[InlineKeyboardButton('⬅️ بازگشت',callback_data='v25:business'),main_menu_button(uid)]])); return
        if action=='bizname': context.user_data['v25_mode']='bizname_v25'; await q.message.edit_text('🏪 نام جدید کسب‌وکار را بفرست:',reply_markup=v25_back(uid,'v25:business')); return
        if action=='bizfinance':
            c=db(); rows=c.execute("SELECT c.id,c.name,COALESCE(SUM(f.amount_rial),0) total,COALESCE(SUM(f.paid_rial),0) paid FROM customers c LEFT JOIN customer_finance f ON f.customer_id=c.id WHERE c.owner_user_id=? GROUP BY c.id ORDER BY c.name LIMIT 50",(uid,)).fetchall(); c.close();
            text='📒 <b>مالی مشتریان</b>\n\n'+('\n'.join(f"👤 {html.escape(r['name'])} | 💰 {r['total']:,.0f} ریال | ✅ {r['paid']:,.0f} ریال | ⏳ {(r['total']-r['paid']):,.0f} ریال" for r in rows) if rows else 'هنوز تراکنشی ثبت نشده.'); await q.message.edit_text(text,parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ بازگشت',callback_data='v25:business'),main_menu_button(uid)]])); return
        if action=='bookservice':
            sid=int(p[2]); context.user_data['booking_service_id']=sid; await v25_booking_profile_prompt(update,context); return
        if action=='bookuse':
            owner=context.user_data.get('booking_owner'); sid=int(context.user_data.get('booking_service_id') or 0);
            try: aid,service,amount,name,phone=await v25_create_booking(context,uid,owner,sid); prof=ensure_business_profile(owner); context.user_data['booking_appointment_id']=aid; context.user_data['booking_amount_rial']=amount; await context.bot.send_message(owner,f"🎉 <b>رزرو جدید ثبت شد!</b>\n\n🏪 {html.escape(prof['business_name'] or 'کسب‌وکار')}\n👤 {html.escape(name)}\n📅 {jalali_pretty_date(context.user_data['booking_date'])}\n⏰ {context.user_data['booking_time']}"+ (f"\n🛠️ {html.escape(service)}\n💰 {amount:,.0f} ریال" if service else ''),parse_mode='HTML'); await v25_booking_payment_menu(update,context,aid,owner,amount,prof['business_name'] or 'کسب‌وکار');
            except ValueError: await q.message.edit_text('❌ این زمان دیگر آزاد نیست. لطفاً زمان دیگری انتخاب کن.',reply_markup=v25_back(uid));
            return
        if action=='bookskip':
            owner=context.user_data.get('booking_owner'); sid=int(context.user_data.get('booking_service_id') or 0); context.user_data['public_name']=''; context.user_data['public_phone']='';
            try: aid,service,amount,name,phone=await v25_create_booking(context,uid,owner,sid); prof=ensure_business_profile(owner); context.user_data['booking_appointment_id']=aid; context.user_data['booking_amount_rial']=amount; await context.bot.send_message(owner,f"🎉 <b>رزرو جدید ثبت شد!</b>\n\n🏪 {html.escape(prof['business_name'] or 'کسب‌وکار')}\n👤 مشتری بدون اطلاعات شخصی\n📅 {jalali_pretty_date(context.user_data['booking_date'])}\n⏰ {context.user_data['booking_time']}",parse_mode='HTML'); await v25_booking_payment_menu(update,context,aid,owner,amount,prof['business_name'] or 'کسب‌وکار');
            except ValueError: await q.message.edit_text('❌ این زمان دیگر آزاد نیست.',reply_markup=v25_back(uid));
            return
        if action=='bookedit':
            context.user_data['v25_mode']='booking_name'; await q.message.edit_text('👤 اگر دوست داری نامت را وارد کن؛ اختیاری است. برای رد کردن «-» بزن:',reply_markup=v25_back(uid)); return
        if action=='bookingcard':
            aid=int(p[2])
            # Authorization: only the booking customer (or the business owner) may
            # open card-payment instructions for this appointment.
            booking=_v25_exec("SELECT a.owner_user_id,c.telegram_user_id FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.id=?",(aid,),fetchone=True)
            if not booking or (int(booking['telegram_user_id'] or 0) != uid and int(booking['owner_user_id']) != uid):
                await q.answer('⛔ دسترسی به این رزرو مجاز نیست.',show_alert=True); return
            owner=int(booking['owner_user_id'])
            pm=_v25_exec("SELECT details FROM payment_methods WHERE owner_user_id=? AND method_type='card' AND enabled=1",(owner,),fetchone=True);
            if not pm: await q.message.edit_text('⚠️ کارت‌به‌کارت فعال نیست.',reply_markup=v25_back(uid)); return
            amount=context.user_data.get('booking_amount_rial',0); context.user_data['v25_mode']='booking_receipt'; context.user_data['booking_appointment_id']=aid; context.user_data['booking_owner']=owner;
            await q.message.edit_text(f"💵 <b>کارت‌به‌کارت</b>\n\n💰 مبلغ: {amount:,.0f} ریال\n\n{html.escape(pm['details'])}\n\n📎 بعد از واریز تصویر رسید را بفرست.",parse_mode='HTML',reply_markup=v25_back(uid)); return
        if action=='survey_rate':
            aid=int(p[2]); rating=int(p[3]); c=db(); r=c.execute("SELECT owner_user_id,customer_id FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.id=? AND c.telegram_user_id=?",(aid,uid)).fetchone();
            if not r: c.close(); await q.message.edit_text('❌ این نظرسنجی برای شما پیدا نشد.',reply_markup=v25_back(uid)); return
            c.execute('INSERT OR IGNORE INTO survey_responses(owner_user_id,appointment_id,customer_id,rating,created_at) VALUES(?,?,?,?,?)',(r['owner_user_id'],aid,r['customer_id'],rating,_v25_now())); c.commit(); c.close();
            try: await context.bot.send_message(r['owner_user_id'],f"⭐ امتیاز جدید مشتری: {rating}/5");
            except Exception: pass
            await q.message.edit_text('🙏 ممنون! نظرت ثبت شد. اگر پیشنهادی داری می‌توانی در پیام بعدی بنویسی.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✍️ نوشتن پیشنهاد',callback_data=f'v25:surveycomment:{aid}')],[main_menu_button(uid)]])); return
        if action=='surveycomment': context.user_data['v25_mode']='survey_comment'; context.user_data['survey_appointment_id']=int(p[2]); await q.message.edit_text('💬 پیشنهاد یا توضیح خودت را بنویس. این بخش اختیاری است.',reply_markup=v25_back(uid)); return
        if action=='voice': await v25_voice_prompt(update,context); return
        if action=='services': await v25_services_menu(update,context); return
        if action=='serviceadd': context.user_data['v25_mode']='service_name'; await q.message.edit_text('🛠️ نام خدمت را بفرست:',reply_markup=v25_back(uid,'v25:services')); return
        if action=='service_toggle':
            sid=int(p[2]); _v25_exec('UPDATE business_services SET enabled=CASE enabled WHEN 1 THEN 0 ELSE 1 END,updated_at=? WHERE id=? AND owner_user_id=?',(_v25_now(),sid,uid)); await v25_services_menu(update,context); return
        if action=='bizpay': await v25_payment_methods_menu(update,context); return
        if action=='card': context.user_data['v25_mode']='card_number'; await q.message.edit_text('💳 شماره کارت را بفرست:',reply_markup=v25_back(uid,'v25:bizpay')); return
        if action=='gateway': context.user_data['v25_mode']='gateway_link'; await q.message.edit_text('🔗 لینک پرداخت درگاه ایرانی را بفرست. این لینک می‌تواند لینک پرداخت سرویس موردنظر باشد. برای خاموش کردن، از پنل فعال/غیرفعال کن.',reply_markup=v25_back(uid,'v25:bizpay')); return
        if action=='surveyadmin': await v25_survey_admin(update,context); return
        if action=='surveyadd': context.user_data['v25_mode']='survey_question'; await q.message.edit_text('✏️ سؤال جدید نظرسنجی را بفرست:',reply_markup=v25_back(uid,'v25:surveyadmin')); return
        if action=='plans': await v25_vip_plans(update,context); return
        if action=='sms':
            cfg=_v25_exec('SELECT * FROM sms_settings WHERE owner_user_id=?',(uid,),fetchone=True); await q.message.edit_text(f"📱 <b>پیامک</b>\n\nوضعیت: {'🟢' if cfg and cfg['enabled'] else '🔴'}\n\nبرای فعال‌سازی، endpoint و API key سرویس پیامکی در تنظیمات ذخیره شود.",parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🧪 تست',callback_data='v25:smstest')],[InlineKeyboardButton('⬅️ بازگشت',callback_data='v25:business'),main_menu_button(uid)]])); return
        if action=='smstest': context.user_data['v25_mode']='v25_sms_test'; await q.message.edit_text('📱 شماره‌ای که باید تست شود را بفرست:',reply_markup=v25_back(uid,'v25:sms')); return
        if action=='bookinglink': await customer_booking_link(update,context); return
        if action=='buyplan':
            pid=int(p[2]); plan=_v25_exec('SELECT * FROM subscription_plans_v25 WHERE id=? AND enabled=1',(pid,),fetchone=True)
            if not plan: await q.message.edit_text('❌ پلن موجود نیست.',reply_markup=v25_back(uid,'v25:business')); return
            c=db(); card=c.execute("SELECT * FROM payment_methods WHERE owner_user_id=? AND method_type='card' AND enabled=1",(uid,)).fetchone(); c.close()
            await q.message.edit_text(f"💎 {html.escape(plan['name'])}\n\n💰 {plan['price_rial']:,.0f} ریال\n\nروش پرداخت را انتخاب کن.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('💳 پرداخت آنلاین',callback_data=f'v25:viponline:{pid}')],[InlineKeyboardButton('💵 کارت‌به‌کارت',callback_data=f'v25:vipcard:{pid}')],[InlineKeyboardButton('⬅️ برگشت',callback_data='v25:business'),main_menu_button(uid)]])); return
        if action=='viponline':
            plan=_v25_exec('SELECT * FROM subscription_plans_v25 WHERE id=?',(int(p[2]),),fetchone=True); cfg=_v25_exec('SELECT * FROM gateway_configs WHERE owner_user_id=?',(uid,),fetchone=True)
            url=cfg['payment_link'] if cfg and cfg['enabled'] else ''
            if url:
                await q.message.edit_text('💳 برای پرداخت آنلاین از دکمه زیر استفاده کن.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('💳 پرداخت آنلاین',url=url)],[main_menu_button(uid)]]))
            else: await q.message.edit_text('⚠️ درگاه آنلاین هنوز در پنل مدیریت تنظیم نشده است.',reply_markup=v25_back(uid,'v25:business'))
            return
        if action=='vipcard':
            plan=_v25_exec('SELECT * FROM subscription_plans_v25 WHERE id=?',(int(p[2]),),fetchone=True); pm=_v25_exec("SELECT * FROM payment_methods WHERE owner_user_id=? AND method_type='card' AND enabled=1",(uid,),fetchone=True)
            if pm: await q.message.edit_text(f"💵 <b>پرداخت کارت‌به‌کارت</b>\n\n💰 مبلغ: {plan['price_rial']:,.0f} ریال\n\n{html.escape(pm['details'])}\n\nبعد از واریز تصویر رسید را بفرست.",parse_mode='HTML',reply_markup=v25_back(uid,'v25:business')); context.user_data['v25_mode']='vip_receipt'; context.user_data['vip_plan_id']=plan['id']
            else: await q.message.edit_text('⚠️ کارت‌به‌کارت فعال نشده است.',reply_markup=v25_back(uid,'v25:business'))
            return
        if action=='feat':
            if not admin_guard(uid): await q.answer('⛔',show_alert=True); return
            if len(p) < 3 or not _feature_flag_exists(p[2]): await q.answer('قابلیت نامعتبر است.',show_alert=True); return
            key=p[2]; cur=feature_enabled(key); set_feature(key,not cur,uid); mode='free' if not cur else 'off'; set_feature_access_mode(key,mode,uid); await v25_admin_feature_status(update,context); return
        if action=='voice_retry': context.user_data['v25_voice_mode']=True; await q.message.edit_text('🎙️ ویس اصلاحی را بفرست. من متن جدید را جایگزین می‌کنم.',reply_markup=v25_back(uid)); return
        if action=='voice_edit': await v25_voice_edit(update,context); return
        if action=='voice_edit_cancel': clear_flow(context); await q.message.edit_text('لغو شد.',reply_markup=v25_hub_keyboard(uid)); return
        if action=='voice_confirm': await v25_voice_confirm(update,context); return
        if action=='voice': await v25_voice_prompt(update,context); return
    except Exception as e:
        logger.exception('v25 callback error: %s',e)
        await q.message.reply_text(
            f'⚠️ این بخش با خطا روبه‌رو شد.\n\nکد خطا: <code>{type(e).__name__}</code>\n'
            'از گزینه «تلاش دوباره» استفاده کن؛ برای خروج هم منوی اصلی در دسترس است.',
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('🔄 تلاش دوباره',callback_data=q.data)],
                [InlineKeyboardButton('⬅️ مرکز من',callback_data='v25:hub'),main_menu_button(uid)]
            ])
        )

# Patch the legacy navigation with a unified menu while retaining every old button.
_LEGACY_KEYBOARD = keyboard

def keyboard(uid):
    try:
        rows=list(filter_menu_rows(uid,[list(row) for row in T[lang(uid)]['menu']]))
    except Exception: rows=[]
    fa=lang(uid)=='fa'
    # Keep legacy rows first; append only the unified functions that are enabled.
    extra=[]
    if v25_allowed(uid,'unified_hub'): extra.append('🧠 مرکز من' if fa else '🧠 My Center')
    if v25_allowed(uid,'portfolio'): extra.append('💰 سرمایه‌های من' if fa else '💰 My Portfolio')
    if v25_allowed(uid,'installments'): extra.append('💳 اقساط' if fa else '💳 Installments')
    if v25_allowed(uid,'profile_sharing'): extra.append('👤 اطلاعات من' if fa else '👤 My Profile')
    if v25_allowed(uid,'voice'): extra.append('🎙️ دستیار صوتی' if fa else '🎙️ Voice Assistant')
    if v25_allowed(uid,'calendar_hub'): extra.append('📅 تقویم من' if fa else '📅 My Calendar')
    if extra:
        for i in range(0,len(extra),2): rows.append(extra[i:i+2])
    if admin_is_allowed(uid): rows.append(['🛡 پنل مدیریت' if fa else '🛡 Admin Panel'])
    return ReplyKeyboardMarkup(rows,resize_keyboard=True)

# Improve the online-price menu and remove crypto from the default user-facing list.
def prices_keyboard(uid):
    fa=lang(uid)=='fa'; labels=[('usd','💵 دلار' if fa else '💵 USD'),('eur','💶 یورو' if fa else '💶 EUR'),('gold18','🥇 طلای ۱۸ عیار' if fa else '🥇 18K Gold'),('coin','🪙 سکه امامی' if fa else '🪙 Emami Coin'),('silver','🥈 نقره' if fa else '🥈 Silver'),('copper','🟠 مس' if fa else '🟠 Copper'),('aluminum','⚙️ آلومینیوم' if fa else '⚙️ Aluminum'),('nickel','🔩 نیکل' if fa else '🔩 Nickel'),('zinc','🔘 روی' if fa else '🔘 Zinc'),('lead','⛓️ سرب' if fa else '⛓️ Lead')]
    rows=[[InlineKeyboardButton(a,callback_data=f'price:{k}') for k,a in labels[i:i+2]] for i in range(0,len(labels),2)]
    rows.append([InlineKeyboardButton('🔄 بروزرسانی همه' if fa else '🔄 Refresh all',callback_data='price:all')])
    rows.append([InlineKeyboardButton('💰 سرمایه‌های من' if fa else '💰 My Portfolio',callback_data='v25:portfolio')])
    rows.append([InlineKeyboardButton('🏠 منوی اصلی' if fa else '🏠 Main Menu',callback_data='price:main')])
    return InlineKeyboardMarkup(rows)

async def fetch_price_v25(asset):
    # Local Iran-market values from TGJU plus global-metal fallbacks converted via USD/Rial.
    urls={'usd':'https://www.tgju.org/profile/price_dollar_rl','eur':'https://www.tgju.org/profile/price_eur','gold18':'https://www.tgju.org/profile/geram18','coin':'https://www.tgju.org/profile/sekee'}
    if asset in urls:
        raw=await asyncio.to_thread(tgju_value,urls[asset]); val=float(raw.replace(',','').replace('٫','.').replace('٬','')); return val,'ریال','single'
    if asset in ('silver','copper','aluminum','nickel','zinc','lead'):
        # Yahoo symbols provide global spot/futures indications; these are converted to Rial using USD/Irr.
        syms={'silver':'SI=F','copper':'HG=F','aluminum':'ALI=F','nickel':'NI=F','zinc':'ZNC=F','lead':'PB=F'}
        try:
            y=await asyncio.to_thread(fetch_url_json,f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(syms[asset],safe='')}?range=1d&interval=1m")
            meta=y['chart']['result'][0]['meta']; usd=float(meta.get('regularMarketPrice') or meta.get('previousClose'))
            dollar,_u,_c=await fetch_price_v25('usd')
            if asset=='silver': rial_per_unit=usd*dollar; unit='ریال/اونس جهانی'
            elif asset=='copper': rial_per_unit=usd*dollar; unit='ریال/پوند جهانی'
            else: rial_per_unit=usd*dollar; unit='ریال/واحد جهانی'
            return rial_per_unit,unit,'single'
        except Exception as e: raise RuntimeError(f'{asset}: {e}')
    raise KeyError(asset)

async def v25_show_price(update,context,asset):
    uid=update.effective_user.id; fa=lang(uid)=='fa'; names={'usd':'دلار','eur':'یورو','gold18':'طلای ۱۸ عیار','coin':'سکه امامی','silver':'نقره','copper':'مس','aluminum':'آلومینیوم','nickel':'نیکل','zinc':'روی','lead':'سرب'}
    assets=list(names) if asset=='all' else [asset]; lines=['📈 <b>قیمت بازار</b>','']; stamp=fa_datetime(datetime.now(TZ), True)
    for a in assets:
        try:
            val,unit,confidence=await fetch_price_v25(a); lines.append(f"{names[a]}: <b>{val:,.0f}</b> {unit} | {'🟢 اطمینان بالا' if confidence=='multi' else '🟡 یک منبع در دسترس'}")
        except Exception:
            lines.append(f"{names[a]}: ⚠️ در حال حاضر داده قابل‌اعتماد در دسترس نیست")
    lines += ['',f'🕐 آخرین بررسی: {stamp}', '⚠️ قیمت‌ها لحظه‌ای‌اند و ممکن است با بازار کمی تفاوت داشته باشند.']
    kb=prices_keyboard(uid)
    if update.callback_query: await update.callback_query.message.edit_text('\n'.join(lines),parse_mode='HTML',reply_markup=kb)
    else: await update.message.reply_text('\n'.join(lines),parse_mode='HTML',reply_markup=kb)

async def price_callback(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; asset=q.data.split(':',1)[1]
    if asset=='main':
        await q.message.edit_text('🏠 منوی اصلی')
        await q.message.reply_text('🏠 منوی اصلی',reply_markup=keyboard(uid))
        return
    await v25_show_price(update,context,asset)

# Patch booking after a slot: use saved profile when possible, then services, then payment options.
_LEGACY_BOOKING_SLOT_SELECT=booking_slot_select
async def booking_slot_select(update,context,tm):
    q=update.callback_query; uid=q.from_user.id; owner=context.user_data.get('booking_owner'); d=context.user_data.get('booking_date'); tm=parse_time(tm)
    if not owner or not d or not tm or tm not in available_slots(owner,d,30): await q.answer('این زمان دیگر آزاد نیست.',show_alert=True); return
    if context.user_data.get('reschedule_appointment_id'):
        return await _LEGACY_BOOKING_SLOT_SELECT(update,context,tm)
    context.user_data['booking_time']=tm
    c=db(); services=c.execute('SELECT * FROM business_services WHERE owner_user_id=? AND enabled=1 ORDER BY id',(owner,)).fetchall(); c.close()
    if services:
        kb=[[InlineKeyboardButton(f"🛠️ {s['name']} — {s['price_rial']:,.0f} ریال",callback_data=f'v25:bookservice:{s["id"]}') ] for s in services]
        kb.append([InlineKeyboardButton('⏭️ بدون انتخاب خدمت',callback_data='v25:bookservice:0')])
        kb.append([InlineKeyboardButton('⬅️ تاریخ دیگر',callback_data='cust:booklink'),main_menu_button(uid)])
        await q.message.edit_text(f'📅 {jalali_pretty_date(d)}\n⏰ {tm}\n\n🛠️ خدمت را انتخاب کن:',reply_markup=InlineKeyboardMarkup(kb))
    else:
        await v25_booking_profile_prompt(update,context)

async def v25_booking_profile_prompt(update,context):
    uid=update.effective_user.id; p=v25_profile(uid); owner=context.user_data.get('booking_owner');
    share_name=_v25_exec('SELECT enabled FROM profile_share WHERE user_id=? AND scope=? AND field=?',(uid,'booking','full_name'),fetchone=True); share_phone=_v25_exec('SELECT enabled FROM profile_share WHERE user_id=? AND scope=? AND field=?',(uid,'booking','phone'),fetchone=True)
    use_name=bool(p['full_name']) and (share_name is None or share_name['enabled'])
    use_phone=bool(p['phone']) and (share_phone is None or share_phone['enabled'])
    name=p['full_name'] if use_name else ''; phone=p['phone'] if use_phone else ''
    context.user_data['public_name']=name; context.user_data['public_phone']=phone
    if name or phone:
        await update.callback_query.message.edit_text(f"😊 اطلاعات ذخیره‌شده پیدا شد.\n\n👤 نام: {html.escape(name or '—')}\n📱 تلفن: {html.escape(phone or '—')}\n\nدرسته؟",parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ استفاده از اطلاعات من',callback_data='v25:bookuse')],[InlineKeyboardButton('✏️ ویرایش',callback_data='v25:bookedit')],[InlineKeyboardButton('⏭️ بدون اطلاعات شخصی',callback_data='v25:bookskip')],[main_menu_button(uid)]]))
    else:
        await update.callback_query.message.edit_text('😊 برای رزرو اگر دوست داشتی نام و شماره‌ات را وارد کن؛ هر دو اختیاری‌اند.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('👤 وارد کردن نام',callback_data='v25:bookedit')],[InlineKeyboardButton('⏭️ فعلاً بدون اطلاعات',callback_data='v25:bookskip')],[main_menu_button(uid)]]))

async def v25_create_booking(context,uid,owner,service_id=0):
    d=context.user_data['booking_date']; tm=context.user_data['booking_time']; name=context.user_data.get('public_name') or ''; phone=context.user_data.get('public_phone') or ''
    now=_v25_now(); c=db(); existing=c.execute('SELECT id FROM customers WHERE owner_user_id=? AND telegram_user_id=? LIMIT 1',(owner,uid)).fetchone();
    if existing: cid=existing['id']; c.execute('UPDATE customers SET name=?,phone=?,telegram_username=?,updated_at=? WHERE id=?',(name,phone,context._application.user if False else '',now,cid))
    else: cid=c.execute('INSERT INTO customers(owner_user_id,name,phone,telegram_user_id,created_at,updated_at) VALUES(?,?,?,?,?,?)',(owner,name,phone,uid,now,now)).lastrowid
    service=''; amount=0; duration=30
    if service_id:
        s=c.execute('SELECT * FROM business_services WHERE id=? AND owner_user_id=? AND enabled=1',(service_id,owner)).fetchone();
        if s: service=s['name']; amount=int(s['price_rial']); duration=int(s['duration_minutes'] or 30)
    try:
        c.execute('BEGIN IMMEDIATE')
        rows=c.execute("SELECT appointment_time,duration_minutes FROM appointments WHERE owner_user_id=? AND appointment_date=? AND status='booked'",(owner,d)).fetchall()
        start=_mins(tm); end=start+duration
        if any(start < _mins(r['appointment_time'])+int(r['duration_minutes'] or 30) and _mins(r['appointment_time']) < end for r in rows):
            raise ValueError('slot-conflict')
        aid=c.execute('INSERT INTO appointments(owner_user_id,customer_id,appointment_date,appointment_time,duration_minutes,service,notes,reminder_minutes,status,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(owner,cid,d,tm,duration,service,'رزرو آنلاین','30','booked','online',now,now)).lastrowid
        c.execute('INSERT INTO customer_events(owner_user_id,customer_id,appointment_id,event_type,details,created_at) VALUES(?,?,?,?,?,?)',(owner,cid,aid,'online_booking',service,now))
        c.commit(); c.close()
    except Exception:
        try: c.rollback(); c.close()
        except Exception: pass
        raise;
    return aid,service,amount,name,phone

async def v25_booking_payment_menu(update,context,aid,owner,amount,business_name):
    uid=update.effective_user.id; c=db(); g=c.execute('SELECT * FROM gateway_configs WHERE owner_user_id=?',(owner,)).fetchone(); card=c.execute("SELECT * FROM payment_methods WHERE owner_user_id=? AND method_type='card' AND enabled=1",(owner,)).fetchone(); c.close(); rows=[]
    if amount>0:
        if g and g['enabled'] and g['payment_link']: rows.append([InlineKeyboardButton('💳 پرداخت آنلاین',url=g['payment_link'])])
        if card: rows.append([InlineKeyboardButton('💵 پرداخت کارت‌به‌کارت',callback_data=f'v25:bookingcard:{aid}')])
    rows.append([InlineKeyboardButton('📅 رزروهای من',callback_data='cust:mybookings'),main_menu_button(uid)])
    await update.callback_query.message.edit_text(f'✅ <b>رزرو با موفقیت ثبت شد.</b>\n\n🏪 {html.escape(business_name)}\n📅 {jalali_pretty_date(context.user_data.get("booking_date"))}\n⏰ {context.user_data.get("booking_time")}\n'+(f'💰 هزینه: {amount:,.0f} ریال\n\n' if amount else '\n')+'می‌توانی از گزینه‌های زیر پرداخت و مدیریت رزرو را انجام بدهی.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(rows))

# Extend callback cases for service/profile/payment without replacing the legacy customer router.
_LEGACY_CUSTOMER_PANEL_CALLBACK=customer_panel_callback
async def customer_panel_callback(update,context):
    if update.callback_query and update.callback_query.data.startswith('cust:'):
        # Handle v25-compatible public booking actions that reuse the old cust namespace.
        p=update.callback_query.data.split(':'); a=p[1] if len(p)>1 else ''
        if a=='slot':
            return await booking_slot_select(update,context,p[2])
        if a=='bookdate':
            return await booking_date_menu(update,context,p[2])
        if a=='booklink' and len(p)>2:
            context.user_data['booking_owner']=int(p[2])
        return await _LEGACY_CUSTOMER_PANEL_CALLBACK(update,context)
    return await _LEGACY_CUSTOMER_PANEL_CALLBACK(update,context)

# Unified text router: consume V25 states first, otherwise preserve legacy behavior.
_LEGACY_TEXT_ROUTER=text_router
async def text_router(update,context):
    uid=update.effective_user.id
    if not update.message or not update.message.text: return await _LEGACY_TEXT_ROUTER(update,context)
    txt=update.message.text.strip()
    # Universal navigation labels.
    if txt in ('⬅️ برگشت','⬅️ Back'):
        clear_flow(context); await update.message.reply_text(v25_hub_text(uid),parse_mode='HTML',reply_markup=v25_hub_keyboard(uid)); return
    if txt in ('🏠 منوی اصلی','🏠 Main Menu'):
        clear_flow(context); await update.message.reply_text('🏠 منوی اصلی',reply_markup=keyboard(uid)); return
    if txt in ('🧠 مرکز من','🧠 My Center'):
        clear_flow(context); await v25_hub(update,context); return
    if txt in ('💰 سرمایه‌های من','💰 My Portfolio'):
        await v25_portfolio_menu(update,context); return
    if txt in ('💳 اقساط','💳 Installments'):
        await v25_installments_menu(update,context); return
    if txt in ('👤 اطلاعات من','👤 My Profile'):
        await v25_profile_menu(update,context); return
    if txt in ('🎙️ دستیار صوتی','🎙️ Voice Assistant'):
        context.user_data['v25_voice_mode']=True; await update.message.reply_text('🎙️ ویست رو بفرست.'); return
    mode=context.user_data.get('v25_mode')
    if mode=='rem_title': context.user_data['v25_rem_title']=txt; context.user_data['v25_mode']='rem_time'; await update.message.reply_text('📅 تاریخ و ساعت را بفرست. نمونه: ۱۴۰۵/۰۶/۰۳ ۱۲:۰۰'); return
    if mode in ('rem_time','inst_bank','inst_title','inst_principal','inst_rate_custom','inst_months','inst_first_date','profile_edit:name','profile_edit:phone','profile_edit:email','service_name','service_duration','service_price','card_number','card_name','gateway_link','survey_question','bizname_v25'):
        if await v25_add_reminder_save(update,context) if mode=='rem_time' else False: return
        if await v25_installment_text_save(update,context): return
        if await v25_business_text_save(update,context): return
    if context.user_data.get('v25_editing_voice'):
        context.user_data['v25_voice_text']=txt; context.user_data['v25_voice_action']='note'; context.user_data.pop('v25_editing_voice',None); await update.message.reply_text('✅ متن اصلاح شد.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ تأیید',callback_data='v25:voice_confirm')],[main_menu_button(uid)]])); return
    # Fall through to original router.
    await _LEGACY_TEXT_ROUTER(update,context)

# Add optional Voice handler and V25 menus before the generic text handler in main.

# Wrap appointment completion to deliver the customer survey.
_LEGACY_APPOINTMENT_STATUS=appointment_status
async def appointment_status(update,context,status,aid):
    await _LEGACY_APPOINTMENT_STATUS(update,context,status,aid)
    if status=='done':
        try: await v25_send_survey(context.bot,update.effective_user.id,aid)
        except Exception: logger.exception('Survey send failed')

# Public booking start: retain old behavior but ensure the profile-aware flow is initialized.

# Extend the final admin keyboard and callback while retaining all legacy actions.
_LEGACY_FINAL_ADMIN_KEYBOARD=final_admin_keyboard

def final_admin_keyboard():
    base=_LEGACY_FINAL_ADMIN_KEYBOARD().inline_keyboard
    rows=[list(r) for r in base]
    rows.insert(-1,[InlineKeyboardButton('🔧 وضعیت همه قابلیت‌ها',callback_data='v25:adminfeatures'),InlineKeyboardButton('🧩 تنظیمات جدید',callback_data='v25:business')])
    return InlineKeyboardMarkup(rows)

# Preserve original final admin callback and add only the new entry points.
_LEGACY_FINAL_ADMIN_PANEL_CALLBACK=final_admin_panel_callback
async def final_admin_panel_callback(update,context):
    if update.callback_query and update.callback_query.data=='adm:stats':
        return await _LEGACY_FINAL_ADMIN_PANEL_CALLBACK(update,context)
    return await _LEGACY_FINAL_ADMIN_PANEL_CALLBACK(update,context)

# Update alias used by main/older code.
admin_panel_callback=final_admin_panel_callback
admin_keyboard=final_admin_keyboard

# One extra feature-route into V25 without touching existing admin logic.
_ORIGINAL_V25_CALLBACK=v25_callback
async def v25_callback(update,context):
    q=update.callback_query; data=q.data
    if data=='v25:adminfeatures': return await v25_admin_feature_status(update,context)
    return await _ORIGINAL_V25_CALLBACK(update,context)

# Wrap init_db so current database is preserved and new tables are additive only.
_LEGACY_INIT_DB=init_db
def init_db():
    _LEGACY_INIT_DB(); v25_init_db()




# ===================== FINAL V25 ENHANCEMENT LAYER =====================
# This layer is additive and intentionally sits above the existing implementation.
# It adds: richer goal catalog, unified navigation, optional-profile sharing,
# IRR-first money formatting, installment due notifications, portfolio P/L,
# enriched morning/night summaries, central admin control, SMS/gateway toggles,
# admin-configurable VIP plans, and safer voice/admin command confirmation.

# Expanded ready-goal catalog (user can still create a custom goal).
GOALS_FA.update({
    "🧠 تفکر و ذهن": [
        "🧘 ۱۰ دقیقه مدیتیشن", "📖 مطالعه کتاب", "✍️ نوشتن افکار", "💭 مرور روزانه",
        "🧠 تمرین تمرکز", "🔍 حل مسئله", "📝 نوشتن ایده‌ها", "🌱 تفکر مثبت",
        "🎯 تعیین اولویت‌های روز", "📋 برنامه‌ریزی روز", "🔄 بررسی یک اشتباه و درس آن",
        "💡 پیدا کردن یک ایده جدید", "📵 یک ساعت بدون موبایل", "🌙 مرور اتفاقات روز",
        "🧩 حل یک معما", "🤔 ۱۰ دقیقه تفکر عمیق", "🧠 یادگیری یک مفهوم جدید",
    ],
    "🚀 رشد فردی": [
        "🎯 تعیین یک هدف مهم", "📈 بهتر شدن ۱٪ امروز", "📚 ۲۰ دقیقه یادگیری", "🗣️ تمرین ارتباط مؤثر",
        "💪 انجام یک کار سخت", "🔥 غلبه بر یک تعلل", "📝 ثبت سه نکته مثبت", "🌱 ساخت یک عادت خوب",
        "⏰ شروع به‌موقع کار", "🎯 تکمیل مهم‌ترین کار روز", "🧹 حذف یک حواس‌پرتی",
        "💡 یادگیری از یک تجربه امروز", "🙏 قدردانی از سه چیز", "🪴 مراقبت از خود",
    ],
    "👥 روابط و خانواده": [
        "☎️ تماس با خانواده", "💬 احوال‌پرسی از یک دوست", "❤️ وقت با خانواده", "🤝 کمک به یک نفر",
        "🙏 تشکر از یک نفر", "🎁 انجام یک کار خوب برای خانواده", "🗣️ گفت‌وگوی بدون موبایل",
        "👨‍👩‍👧 برنامه خانوادگی", "💌 ارسال یک پیام محبت‌آمیز",
    ],
    "🎨 خلاقیت": [
        "✍️ نوشتن یک ایده", "🎨 طراحی یا نقاشی", "📸 ثبت یک عکس خلاقانه", "🎵 گوش دادن فعال به موسیقی",
        "🧠 ساخت یک ایده جدید", "📝 نوشتن ۱۰ دقیقه آزاد", "🛠️ ساخت یک چیز ساده",
    ],
    "🕌 معنوی": [
        "🙏 دعا و نیایش", "📖 مطالعه کوتاه معنوی", "🧘 چند دقیقه سکوت", "❤️ انجام یک کار خیر",
        "🌙 مرور یک نکته معنوی", "🤲 تشکر و قدردانی",
    ],
    "📱 دیجیتال": [
        "📵 ۳۰ دقیقه بدون شبکه اجتماعی", "📱 پاک کردن اعلان‌های اضافی", "🧹 مرتب کردن فایل‌ها",
        "📧 پاسخ به ایمیل‌های ضروری", "🔐 بررسی امنیت حساب‌ها", "💻 ۳۰ دقیقه کار متمرکز با رایانه",
    ],
    "✈️ سفر و برنامه‌ریزی": [
        "🗺️ بررسی مسیر سفر", "🎒 آماده‌سازی وسایل", "🏨 بررسی محل اقامت", "💰 برنامه هزینه سفر",
        "📅 برنامه‌ریزی روز سفر", "📸 ساخت لیست مکان‌های دیدنی",
    ],
})
GOALS_EN.update({
    "🧠 Thinking & Mind": [
        "🧘 10 minutes of meditation", "📖 Read a book", "✍️ Journal your thoughts", "💭 Review the day",
        "🧠 Focus exercise", "🔍 Solve a problem", "📝 Write ideas", "🌱 Positive thinking",
        "🎯 Set today's priorities", "📋 Plan the day", "🔄 Review one mistake and its lesson",
        "💡 Find one new idea", "📵 One hour without phone", "🌙 Review the day",
        "🧩 Solve a puzzle", "🤔 10 minutes of deep thinking", "🧠 Learn one new concept",
    ],
    "🚀 Personal Growth": [
        "🎯 Set one important goal", "📈 Improve 1% today", "📚 20 minutes of learning", "🗣️ Practice communication",
        "💪 Do one hard thing", "🔥 Beat one procrastination", "📝 Write three positives", "🌱 Build one good habit",
        "⏰ Start on time", "🎯 Finish the most important task", "🧹 Remove one distraction",
        "💡 Learn from today's experience", "🙏 Write three things you appreciate", "🪴 Take care of yourself",
    ],
    "👥 Relationships & Family": [
        "☎️ Call family", "💬 Check in with a friend", "❤️ Spend time with family", "🤝 Help someone",
        "🙏 Thank someone", "🎁 Do something kind for family", "🗣️ Have a phone-free conversation",
        "👨‍👩‍👧 Plan family time", "💌 Send a kind message",
    ],
    "🎨 Creativity": [
        "✍️ Write one idea", "🎨 Draw or design", "📸 Take a creative photo", "🎵 Listen to music mindfully",
        "🧠 Build one new idea", "📝 Free-write for 10 minutes", "🛠️ Make something simple",
    ],
    "🕌 Spiritual": [
        "🙏 Prayer or reflection", "📖 Read something spiritual", "🧘 A few minutes of silence", "❤️ Do a good deed",
        "🌙 Reflect on one spiritual lesson", "🤲 Practice gratitude",
    ],
    "📱 Digital Life": [
        "📵 30 minutes without social media", "📱 Remove unnecessary notifications", "🧹 Organize digital files",
        "📧 Reply to important emails", "🔐 Review account security", "💻 30 minutes of focused computer work",
    ],
    "✈️ Travel & Planning": [
        "🗺️ Check the route", "🎒 Prepare travel items", "🏨 Review accommodation", "💰 Plan travel budget",
        "📅 Plan travel day", "📸 Make a sightseeing list",
    ],
})

# Money is stored/displayed in Iranian Rials in this layer.
def irr(amount):
    try:
        return f"{float(amount):,.0f} ریال"
    except Exception:
        return "— ریال"

def toman_to_irr(toman):
    return int(round(float(toman) * 10))

# Better interest-rate choices: 0..30 inclusive, plus custom.
def v25_rates_keyboard(uid):
    fa = lang(uid) == 'fa'
    vals = list(range(0,31))
    rows = []
    for i in range(0, len(vals), 3):
        rows.append([InlineKeyboardButton(f'{x}٪', callback_data=f'v25:instrate:{x}') for x in vals[i:i+3]])
    rows.append([InlineKeyboardButton('✏️ نرخ دلخواه' if fa else '✏️ Custom Rate', callback_data='v25:instrate_custom')])
    rows.append([InlineKeyboardButton('⬅️ بازگشت' if fa else '⬅️ Back', callback_data='v25:instadd'), main_menu_button(uid)])
    return InlineKeyboardMarkup(rows)

# A broader Iran-bank catalog. Kept configurable later from admin.
def v25_banks():
    return [
        'ملی ایران','سپه','کشاورزی','مسکن','صادرات ایران','تجارت','ملت','رفاه کارگران',
        'پست بانک ایران','صنعت و معدن','توسعه صادرات ایران','توسعه تعاون','پارسـیان','پاسارگاد',
        'سامان','اقتصاد نوین','کارآفرین','سینا','شهر','دی','آینده','گردشگری','ایران‌زمین',
        'خاورمیانه','سرمایه','مهر اقتصاد','قرض‌الحسنه مهر ایران','قرض‌الحسنه رسالت','نور','ملل',
        'تات','حکمت ایرانیان','انصار','قوامین','کوثر','مؤسسه/بانک دلخواه'
    ]

# ------------------ Unified admin control ------------------
async def v25_admin_menu(update, context):
    uid = update.effective_user.id
    if not admin_guard(uid):
        await update.callback_query.answer('⛔ دسترسی ندارید.', show_alert=True); return
    fa = lang(uid) == 'fa'
    items = [
        ('v25:adminfeatures','🔧 وضعیت قابلیت‌ها','🔧 Feature Status'),
        ('v25:adminplans','💎 مدیریت پلن‌های VIP','💎 VIP Plans'),
        ('v25:adminpayment','💳 تنظیمات پرداخت','💳 Payment Settings'),
        ('v25:adminvip','💎 پرداخت VIP','💎 VIP Payment'),
        ('v25:adminsms','📱 تنظیمات پیامک','📱 SMS Settings'),
        ('v25:adminsurvey','⭐ تنظیمات نظرسنجی','⭐ Survey Settings'),
        ('v25:adminvoice','🎙️ تنظیمات Voice','🎙️ Voice Settings'),
        ('v25:adminmorning','☀️ صبح/شب و جمعه','☀️ Morning/Night & Friday'),
        ('v25:adminprices','📈 قیمت بازار','📈 Market Prices'),
    ]
    rows=[[InlineKeyboardButton(ft if fa else et, callback_data=cb)] for cb,ft,et in items]
    rows.append([InlineKeyboardButton('⬅️ پنل مدیریت' if fa else '⬅️ Admin Panel', callback_data='adm:stats'), main_menu_button(uid)])
    await update.callback_query.message.edit_text('🛡️ <b>مرکز مدیریت نسخه نهایی</b>\n\nتمام قابلیت‌های جدید از همین بخش کنترل می‌شوند.' if fa else '🛡️ <b>Final Admin Center</b>\n\nAll new modules are controlled here.', parse_mode='HTML', reply_markup=InlineKeyboardMarkup(rows))

async def v25_admin_feature_status(update,context):
    uid=update.effective_user.id
    if not admin_guard(uid):
        await update.callback_query.answer('⛔ دسترسی ندارید.',show_alert=True); return
    keys=list(V25_FEATURE_LABELS.keys())
    c=db(); rows=c.execute("SELECT key,enabled FROM feature_flags WHERE key IN (%s) ORDER BY key" % ','.join('?'*len(keys)),tuple(keys)).fetchall(); c.close()
    text='🔧 <b>وضعیت همه قابلیت‌ها</b>\n\n'; kb=[]
    for r in rows:
        label=V25_FEATURE_LABELS.get(r['key'],r['key']); state='🟢' if r['enabled'] else '🔴'; text+=f'{state} {label}\n'
        kb.append([InlineKeyboardButton(f'{state} {label}',callback_data=f'v25:feat:{r["key"]}')])
    kb.append([InlineKeyboardButton('⬅️ مدیریت' if lang(uid)=='fa' else '⬅️ Admin',callback_data='v25:adminmenu'), main_menu_button(uid)])
    await update.callback_query.message.edit_text(text,parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_admin_plans(update,context):
    uid=update.effective_user.id
    if not admin_guard(uid): return await update.callback_query.answer('⛔ دسترسی ندارید.',show_alert=True)
    c=db(); rows=c.execute('SELECT * FROM subscription_plans_v25 ORDER BY duration_minutes').fetchall(); c.close()
    kb=[]
    lines=['💎 <b>مدیریت پلن‌های VIP</b>','']
    for r in rows:
        st='🟢' if r['enabled'] else '🔴'; lines.append(f'{st} {html.escape(r["name"])} — {irr(r["price_rial"])}')
        kb.append([InlineKeyboardButton(f'{st} {r["name"]}',callback_data=f'v25:planedit:{r["id"]}')])
    kb.append([InlineKeyboardButton('➕ پلن سفارشی',callback_data='v25:planadd'), InlineKeyboardButton('⬅️ بازگشت',callback_data='v25:adminmenu')])
    await update.callback_query.message.edit_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_admin_payment(update,context):
    uid=update.effective_user.id
    if not admin_guard(uid): return await update.callback_query.answer('⛔ دسترسی ندارید.',show_alert=True)
    c=db(); rows=c.execute("SELECT * FROM feature_flags WHERE key IN ('payments','booking_payments','card_to_card','vip') ORDER BY key").fetchall(); c.close()
    states='\n'.join((('🟢' if r['enabled'] else '🔴')+' '+r['key']) for r in rows)
    kb=[[InlineKeyboardButton('💳 پیکربندی درگاه/لینک پرداخت',callback_data='v25:gateway')],[InlineKeyboardButton('💵 کارت‌به‌کارت',callback_data='v25:card')],[InlineKeyboardButton('⬅️ بازگشت',callback_data='v25:adminmenu'),main_menu_button(uid)]]
    await update.callback_query.message.edit_text('💳 <b>تنظیمات پرداخت</b>\n\n'+states+'\n\nدرگاه‌ها خاموش می‌توانند باقی بمانند تا بعداً فعال شوند.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_admin_sms(update,context):
    uid=update.effective_user.id
    if not admin_guard(uid): return await update.callback_query.answer('⛔ دسترسی ندارید.',show_alert=True)
    cfg=_v25_exec('SELECT * FROM sms_settings WHERE owner_user_id=?',(uid,),fetchone=True)
    state='🟢' if cfg and cfg['enabled'] else '🔴'
    kb=[[InlineKeyboardButton(f'{state} روشن/خاموش',callback_data='v25:smstoggle')],[InlineKeyboardButton('⚙️ تنظیم Endpoint/API',callback_data='v25:smsconfig')],[InlineKeyboardButton('🧪 تست',callback_data='v25:smstest')],[InlineKeyboardButton('⬅️ بازگشت',callback_data='v25:adminmenu')]]
    await update.callback_query.message.edit_text(f'📱 <b>مدیریت پیامک</b>\n\nوضعیت: {state}\n\nاتصال واقعی به سرویس‌دهنده فقط بعد از ثبت endpoint و کلید سرویس انجام می‌شود.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_admin_survey(update,context):
    uid=update.effective_user.id
    if not admin_guard(uid): return await update.callback_query.answer('⛔ دسترسی ندارید.',show_alert=True)
    await update.callback_query.message.edit_text('⭐ <b>تنظیمات نظرسنجی</b>\n\nسؤال‌های اصلی شامل محیط، تمیزی، کارکنان، سرعت، کیفیت، ارزش نسبت به قیمت و راحتی رزرو هستند. کسب‌وکار می‌تواند سؤال سفارشی هم اضافه کند.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🟢/🔴 مدیریت سوال‌ها',callback_data='v25:surveyadmin')],[InlineKeyboardButton('⬅️ بازگشت',callback_data='v25:adminmenu')]]))

async def v25_admin_voice(update,context):
    uid=update.effective_user.id
    if not admin_guard(uid): return await update.callback_query.answer('⛔ دسترسی ندارید.',show_alert=True)
    await update.callback_query.message.edit_text('🎙️ <b>Voice</b>\n\nفارسی و انگلیسی پشتیبانی می‌شوند. متن ویس قبل از اجرا نمایش داده می‌شود و امکان ویرایش/اصلاح صوتی وجود دارد. برای عملیات حساس مدیر، اجرای فرمان بعد از تأیید دوم انجام می‌شود.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🟢/🔴 Voice',callback_data='v25:feat:voice')],[InlineKeyboardButton('⬅️ بازگشت',callback_data='v25:adminmenu')]]))

async def v25_admin_morning(update,context):
    uid=update.effective_user.id
    if not admin_guard(uid): return await update.callback_query.answer('⛔ دسترسی ندارید.',show_alert=True)
    friday=get_system_setting('friday_pause','0')=='1'; night=get_system_setting('night_message_enabled','1')=='1'; morning=get_system_setting('morning_message_enabled','1')=='1'
    txt=f'☀️ صبح: {"🟢" if morning else "🔴"}\n🌙 شب: {"🟢" if night else "🔴"}\n🗓️ توقف جمعه: {"🟢" if friday else "🔴"}'
    kb=[[InlineKeyboardButton('☀️ روشن/خاموش صبح',callback_data='v25:toggle_morning')],[InlineKeyboardButton('🌙 روشن/خاموش شب',callback_data='v25:toggle_night')],[InlineKeyboardButton('🗓️ روشن/خاموش جمعه',callback_data='v25:toggle_friday')],[InlineKeyboardButton('⬅️ بازگشت',callback_data='v25:adminmenu')]]
    await update.callback_query.message.edit_text('☀️ <b>پیام‌های صبح/شب و جمعه</b>\n\n'+txt,parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_admin_prices(update,context):
    uid=update.effective_user.id
    if not admin_guard(uid): return await update.callback_query.answer('⛔ دسترسی ندارید.',show_alert=True)
    status=get_system_setting('price_data_status','auto')
    txt='📈 <b>قیمت بازار</b>\n\n🟢 داده‌ها: '+html.escape(status)+'\n💵 واحد اصلی: ریال\n🛡️ اگر منبع ثانویه در Variables فعال باشد، اختلاف منابع نیز بررسی می‌شود.'
    await update.callback_query.message.edit_text(txt,parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🟢/🔴 قیمت بازار',callback_data='v25:toggle_prices')],[InlineKeyboardButton('⬅️ بازگشت',callback_data='v25:adminmenu')]]))

# ------------------ Installment due history & notifications ------------------
async def v25_installment_view(update,context,plan_id):
    uid=update.effective_user.id; plan=_v25_exec('SELECT * FROM installment_plans WHERE id=? AND user_id=?',(plan_id,uid),fetchone=True)
    if not plan: return
    payments=_v25_exec('SELECT * FROM installment_payments WHERE plan_id=? ORDER BY installment_no',(plan_id,),fetchall=True)
    lines=[f'🏦 <b>{html.escape(plan["bank_name"])}</b>',f'📝 {html.escape(plan["title"])}',f'💵 قسط ماهانه: {irr(plan["monthly_rial"])}','', '📜 <b>تاریخچه</b>']
    for r in payments[:36]:
        icon={'paid':'✅','partial':'🟡','unpaid':'❌','pending':'⏳'}.get(r['status'],'⏳')
        lines.append(f'{icon} قسط {r["installment_no"]} — {jalali_pretty_date(r["due_date"])} — {irr(r["amount_rial"])}')
    kb=[[InlineKeyboardButton('⬅️ بازگشت',callback_data='v25:installments'),main_menu_button(uid)]]
    await update.callback_query.message.edit_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_installment_due_job(context):
    if datetime.now(TZ).weekday()==4 and get_system_setting('friday_pause','0')=='1': return
    d=datetime.now(TZ).date().isoformat()
    rows=_v25_exec("SELECT ip.*,p.user_id,p.bank_name,p.title FROM installment_payments ip JOIN installment_plans p ON p.id=ip.plan_id WHERE ip.due_date=? AND ip.status IN ('pending','unpaid','partial') AND p.enabled=1",(d,),fetchall=True)
    for r in rows:
        key=f'inst_due:{r["id"]}:{d}'
        if _v25_exec('SELECT 1 FROM delivery_log WHERE delivery_key=?',(key,),fetchone=True): continue
        try:
            await context.bot.send_message(r['user_id'],f'🔔 <b>یادآوری قسط</b>\n\n🏦 {html.escape(r["bank_name"])}\n📝 {html.escape(r["title"])}\n📅 امروز\n💰 مبلغ: {irr(r["amount_rial"])}\n\nوضعیت پرداخت را مشخص کن:',parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ پرداخت شد',callback_data=f'v25:instpay:{r["id"]}:paid'),InlineKeyboardButton('🟡 بعداً پرداخت می‌کنم',callback_data=f'v25:instpay:{r["id"]}:later')],[InlineKeyboardButton('❌ پرداخت نشد',callback_data=f'v25:instpay:{r["id"]}:unpaid')],[main_menu_button(r['user_id'])]]))
            _v25_exec('INSERT OR IGNORE INTO delivery_log(delivery_key,user_id,delivery_type,created_at) VALUES(?,?,?,?)',(key,r['user_id'],'installment_due',_v25_now()))
        except Exception as e: logger.warning('installment notification failed: %s',e)

# ------------------ Portfolio P/L ------------------
async def v25_current_price_for_portfolio(title):
    t=(title or '').lower()
    mappings=[('gold18',['طلا','gold']),('usd',['دلار','usd','dollar']),('eur',['یورو','eur','euro']),('coin',['سکه','coin']),('silver',['نقره','silver']),('copper',['مس','copper']),('aluminum',['آلومینیوم','aluminum']),('nickel',['نیکل','nickel']),('zinc',['روی','zinc']),('lead',['سرب','lead'])]
    for code,keys in mappings:
        if any(k in t for k in keys):
            try:
                val,_,conf=await fetch_price_v25(code); return float(val),conf
            except Exception: return None,'unavailable'
    return None,'unavailable'

async def v25_portfolio_summary(update,context):
    uid=update.effective_user.id; rows=_v25_exec('SELECT * FROM portfolio_assets WHERE user_id=? AND enabled=1',(uid,),fetchall=True)
    total_cost=sum(float(r['quantity'])*float(r['buy_price_rial'])+float(r['fees_rial'] or 0) for r in rows)
    current=0.0; known=0; details=[]
    for r in rows:
        cur,conf=await v25_current_price_for_portfolio(r['title'])
        cost=float(r['quantity'])*float(r['buy_price_rial'])+float(r['fees_rial'] or 0)
        if cur is not None:
            cur_value=float(r['quantity'])*cur; current+=cur_value; known+=1
            pnl=cur_value-cost; pct=(pnl/cost*100) if cost else 0
            details.append(f"• {html.escape(r['title'])}: {'🟢' if pnl>=0 else '🔴'} {irr(pnl)} ({pct:+.2f}٪)")
        else: details.append(f"• {html.escape(r['title'])}: ⚪ قیمت جاری در دسترس نیست")
    pnl=current-total_cost if known else None
    lines=["📊 <b>خلاصه سرمایه</b>",f"💰 هزینه خرید: {irr(total_cost)}"]
    if known: lines += [f"📈 ارزش فعلی دارایی‌های قابل‌قیمت‌گذاری: {irr(current)}",f"{'🟢' if pnl>=0 else '🔴'} سود/زیان فعلی: {irr(pnl)}"]
    lines += ['',*details]
    lines += ['', '⚠️ سود/زیان فقط برای دارایی‌هایی محاسبه می‌شود که قیمت جاری آن‌ها با داده معتبر در دسترس باشد.']
    await update.callback_query.message.edit_text('\n'.join(lines),parse_mode='HTML',reply_markup=v25_portfolio_menu_keyboard(uid))

# ------------------ Multi-source price connector (optional secondary) ------------------
async def v25_bonbast_secondary(asset):
    user=os.environ.get('BONBAST_USER','').strip(); hsh=os.environ.get('BONBAST_HASH','').strip()
    if not user or not hsh or asset not in ('usd','eur'): return None
    req=urllib.request.Request(f'https://bonbast.com/api/{urllib.parse.quote(user)}/',data=urllib.parse.urlencode({'hash':hsh}).encode(),headers={'Content-Type':'application/x-www-form-urlencoded'},method='POST')
    def fetch():
        with urllib.request.urlopen(req,timeout=12) as resp: return json.loads(resp.read().decode('utf-8'))
    data=await asyncio.to_thread(fetch)
    key='usd1' if asset=='usd' else 'eur1'
    if key not in data: return None
    return toman_to_irr(float(data[key]))

_ORIGINAL_FETCH_PRICE_V25=fetch_price_v25
async def fetch_price_v25(asset):
    val,unit,conf=await _ORIGINAL_FETCH_PRICE_V25(asset)
    secondary=None
    try: secondary=await v25_bonbast_secondary(asset)
    except Exception: secondary=None
    if secondary and val:
        diff=abs(secondary-val)/max(abs(val),1)
        if diff <= 0.01:
            return (val+secondary)/2, unit, 'multi'
        # Material disagreement: show the primary but flag uncertainty.
        return val, unit+' | اختلاف منابع', 'disputed'
    return val,unit,conf

async def v25_show_price(update,context,asset):
    uid=update.effective_user.id; fa=lang(uid)=='fa'
    names={'usd':'دلار','eur':'یورو','gold18':'طلای ۱۸ عیار','coin':'سکه امامی','silver':'نقره','copper':'مس','aluminum':'آلومینیوم','nickel':'نیکل','zinc':'روی','lead':'سرب'}
    names_en={'usd':'USD','eur':'EUR','gold18':'18K Gold','coin':'Emami Coin','silver':'Silver','copper':'Copper','aluminum':'Aluminum','nickel':'Nickel','zinc':'Zinc','lead':'Lead'}
    assets=list(names) if asset=='all' else [asset]; lines=[('📈 <b>قیمت بازار</b>' if fa else '📈 <b>Market Prices</b>'),'']
    for a in assets:
        try:
            val,unit,confidence=await fetch_price_v25(a)
            if confidence=='multi': conf='🟢 تطبیق دو منبع' if fa else '🟢 Two sources agree'
            elif confidence=='disputed': conf='🟡 اختلاف قابل‌توجه بین منابع' if fa else '🟡 Source disagreement'
            else: conf='🟡 یک منبع در دسترس' if fa else '🟡 One source available'
            label=names[a] if fa else names_en[a]
            lines.append(f'{label}: <b>{val:,.0f}</b> {unit}\n{conf}')
        except Exception:
            label=names[a] if fa else names_en[a]; lines.append(f'{label}: ⚠️ '+('داده قابل‌اعتماد در دسترس نیست' if fa else 'Reliable data unavailable'))
    lines += ['',('🕐 زمان بررسی: '+fa_datetime(datetime.now(TZ), True) if fa else '🕐 Checked: '+fa_datetime(datetime.now(TZ), True)),'⚠️ قیمت بازار قطعیِ مطلق نیست و ممکن است در لحظه تغییر کند.' if fa else '⚠️ Market prices are live indications and can move between updates.']
    kb=prices_keyboard(uid)
    if update.callback_query: await update.callback_query.message.edit_text('\n'.join(lines),parse_mode='HTML',reply_markup=kb)
    else: await update.message.reply_text('\n'.join(lines),parse_mode='HTML',reply_markup=kb)

# ------------------ Morning / Night / Friday unified messages ------------------
async def morning_job(context):
    now=datetime.now(TZ)
    if now.hour!=7 or now.minute!=0 or get_system_setting('morning_message_enabled','1')!='1': return
    rows=_v25_exec('SELECT user_id FROM users WHERE COALESCE(blocked,0)=0',fetchall=True)
    for r in rows:
        uid=r['user_id']; key=f'morning:{uid}:{now.date().isoformat()}'
        if _v25_exec('SELECT 1 FROM delivery_log WHERE delivery_key=?',(key,),fetchone=True): continue
        goals=get_goals(uid); today=now.date().isoformat(); done=_v25_exec("SELECT COUNT(*) n FROM goal_days WHERE user_id=? AND goal_date=? AND status='done'",(uid,today),fetchone=True)['n']; total=len(goals)
        lines=[f'☀️ <b>صبح بخیر {html.escape(display_name(uid))}!</b>','', 'امروز این برنامه را داری 👇','']
        if goals:
            for g in goals[:12]:
                st=get_status(uid,g['id']); icon='✅' if st=='done' else '⬜'
                lines.append(f'{icon} {html.escape(g["name"])}')
        else:
            lines.append('🎯 هنوز هدفی برای امروز نداری. یک هدف کوچک انتخاب کن.')
        if now.weekday()==4 and get_system_setting('friday_pause','0')=='1': lines += ['', '🗓️ <b>امروز جمعه و روز استراحت است.</b>', 'یادآوری‌های عادیِ هدف‌ها برای امروز متوقف هستند.']
        lines += ['',f'📊 امروز: {done}/{total} هدف انجام شده', '🚀 قدم کوچک امروزت را همین حالا شروع کن.']
        kb=InlineKeyboardMarkup([[InlineKeyboardButton('🎯 اهداف امروز' if lang(uid)=='fa' else '🎯 Today',callback_data='v25:today'),InlineKeyboardButton('🚀 بریم' if lang(uid)=='fa' else '🚀 Start',callback_data='v25:today')],[InlineKeyboardButton('🎯 هدف‌سازی' if lang(uid)=='fa' else '🎯 Build a Goal',callback_data='goals:main'),InlineKeyboardButton('📊 گزارش روزانه' if lang(uid)=='fa' else '📊 Daily Report',callback_data='v25:reports')],[main_menu_button(uid)]])
        try:
            await context.bot.send_message(uid,'\n'.join(lines),parse_mode='HTML',reply_markup=kb); _v25_exec('INSERT OR IGNORE INTO delivery_log(delivery_key,user_id,delivery_type,created_at) VALUES(?,?,?,?)',(key,uid,'morning',_v25_now()))
        except Exception as e: logger.warning('morning V25 failed for %s: %s',uid,e)

async def v25_night_job(context):
    now=datetime.now(TZ)
    if now.hour!=22 or now.minute!=0 or get_system_setting('night_message_enabled','1')!='1': return
    rows=_v25_exec('SELECT user_id FROM users WHERE COALESCE(blocked,0)=0',fetchall=True)
    for r in rows:
        uid=r['user_id']; key=f'night:{uid}:{now.date().isoformat()}'
        if _v25_exec('SELECT 1 FROM delivery_log WHERE delivery_key=?',(key,),fetchone=True): continue
        d=now.date().isoformat(); done=_v25_exec("SELECT COUNT(*) n FROM goal_days WHERE user_id=? AND goal_date=? AND status='done'",(uid,d),fetchone=True)['n']; total=len(get_goals(uid)); streaks=[calculate_streak(uid,g['id']) for g in get_goals(uid)]; streak=max(streaks,default=0)
        text=f'🌙 <b>شب بخیر {html.escape(display_name(uid))}</b>\n\nخسته نباشی 🌷\n\n📊 گزارش امروز\n✅ انجام‌شده: {done}\n🎯 کل هدف‌ها: {total}\n🔥 Streak: {streak} روز\n\nهر چیزی که امروز انجام نشد، می‌تواند فردا دوباره شروع شود.'
        kb=InlineKeyboardMarkup([[InlineKeyboardButton('📊 گزارش روزانه' if lang(uid)=='fa' else '📊 Daily',callback_data='v25:reports'),InlineKeyboardButton('📆 گزارش هفتگی' if lang(uid)=='fa' else '📆 Weekly',callback_data='v25:report_week')],[InlineKeyboardButton('🗓 گزارش ماهانه' if lang(uid)=='fa' else '🗓 Monthly',callback_data='v25:report_month')],[main_menu_button(uid)]])
        try:
            await context.bot.send_message(uid,text,parse_mode='HTML',reply_markup=kb); _v25_exec('INSERT OR IGNORE INTO delivery_log(delivery_key,user_id,delivery_type,created_at) VALUES(?,?,?,?)',(key,uid,'night',_v25_now()))
        except Exception as e: logger.warning('night V25 failed for %s: %s',uid,e)

async def v25_unified_reminder_job(context):
    # Existing goal reminders remain active except an optional Friday pause.
    if datetime.now(TZ).weekday()==4 and get_system_setting('friday_pause','0')=='1':
        pass
    else:
        try: await _ORIGINAL_REMINDER_JOB(context)
        except Exception: logger.exception('legacy reminder failed')
    await v25_reminder_job(context)
    await v25_installment_due_job(context)

_ORIGINAL_REMINDER_JOB=reminder_job

# ------------------ Simple daily/weekly/monthly report views ------------------
async def v25_reports(update,context,period='day'):
    uid=update.effective_user.id; today=datetime.now(TZ).date(); days=1 if period=='day' else (7 if period=='week' else 30)
    start=today-timedelta(days=days-1)
    c=db(); row=c.execute("SELECT COUNT(*) n FROM goal_days WHERE user_id=? AND goal_date BETWEEN ? AND ? AND status='done'",(uid,start.isoformat(),today.isoformat())).fetchone(); done=row['n']; row2=c.execute("SELECT COUNT(*) n FROM goal_days WHERE user_id=? AND goal_date BETWEEN ? AND ? AND status='missed'",(uid,start.isoformat(),today.isoformat())).fetchone(); missed=row2['n']; c.close(); total=done+missed; rate=(done/total*100) if total else 0
    label={'day':'روزانه','week':'هفتگی','month':'ماهانه'}[period]
    text=f'📊 <b>گزارش {label}</b>\n\n✅ انجام‌شده: {done}\n❌ انجام‌نشده: {missed}\n📈 نرخ موفقیت: {rate:.1f}%\n🗓 بازه: {start.isoformat()} تا {today.isoformat()}'
    kb=InlineKeyboardMarkup([[InlineKeyboardButton('📊 روزانه',callback_data='v25:reports'),InlineKeyboardButton('📆 هفتگی',callback_data='v25:report_week'),InlineKeyboardButton('🗓 ماهانه',callback_data='v25:report_month')],[main_menu_button(uid)]])
    if update.callback_query: await update.callback_query.message.edit_text(text,parse_mode='HTML',reply_markup=kb)
    else: await update.message.reply_text(text,parse_mode='HTML',reply_markup=kb)


async def v25_user_vip_plans(update,context):
    uid=update.effective_user.id; fa=lang(uid)=='fa'
    rows=_v25_exec('SELECT * FROM subscription_plans_v25 WHERE enabled=1 ORDER BY duration_minutes',fetchall=True)
    lines=['💎 <b>VIP و اشتراک</b>','']
    kb=[]
    for r in rows:
        lines.append(f"• {html.escape(r['name'])} — {irr(r['price_rial'])}")
        kb.append([InlineKeyboardButton(r['name'],callback_data=f'v25:userplan:{r["id"]}')])
    lines.append(''); lines.append('هر پلن را که خواستی انتخاب کن و روش پرداخت را ببین.')
    kb.append([InlineKeyboardButton('⬅️ بازگشت' if fa else '⬅️ Back',callback_data='v25:hub'),main_menu_button(uid)])
    target=update.callback_query.message if update.callback_query else update.message
    if update.callback_query: await target.edit_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))
    else: await target.reply_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_admin_vip_payment(update,context):
    uid=update.effective_user.id
    if not admin_guard(uid): return await update.callback_query.answer('⛔ دسترسی ندارید.',show_alert=True)
    card_on=get_system_setting('vip_card_enabled','0')=='1'; gw_on=get_system_setting('vip_gateway_enabled','0')=='1'
    card='🟢' if card_on else '🔴'; gw='🟢' if gw_on else '🔴'
    txt=f'💎 <b>پرداخت VIP</b>\n\n💵 کارت‌به‌کارت: {card}\n💳 درگاه آنلاین: {gw}\n\nشماره کارت و نام صاحب کارت فقط در صورت فعال‌سازی به مشتری نمایش داده می‌شود.'
    kb=[[InlineKeyboardButton(f'{card} کارت‌به‌کارت',callback_data='v25:vipcardtoggle')],[InlineKeyboardButton(f'{gw} درگاه آنلاین',callback_data='v25:vipgatewaytoggle')],[InlineKeyboardButton('💳 شماره کارت / نام',callback_data='v25:vipcardinfo'),InlineKeyboardButton('🔗 لینک درگاه',callback_data='v25:vipgatewayinfo')],[InlineKeyboardButton('⬅️ بازگشت',callback_data='v25:adminmenu')]]
    await update.callback_query.message.edit_text(txt,parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

# Finance/CRM helper: record a charge for a selected customer, with full/partial/unpaid state.
async def v25_finance_menu(update,context):
    uid=update.effective_user.id; rows=customer_list_rows(uid); lines=['📒 <b>مالی مشتریان</b>',''];
    if rows:
        lines += [f"• {html.escape(r['name'])} — {r['id']}" for r in rows]
    else: lines.append('هنوز مشتری فعالی ثبت نشده است.')
    kb=[[InlineKeyboardButton(f'➕ ثبت پرداخت برای {r["name"]}',callback_data=f'v25:finadd:{r["id"]}')] for r in rows[:40]]
    kb.append([InlineKeyboardButton('⬅️ بازگشت',callback_data='v25:business'),main_menu_button(uid)])
    await update.callback_query.message.edit_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

# 4) Extend callback one more time for global VIP + finance.
_OLD_V25_CALLBACK_EXTRA=v25_callback
async def v25_callback(update,context):
    q=update.callback_query; data=q.data; uid=q.from_user.id; p=data.split(':'); action=p[1] if len(p)>1 else ''
    try:
        if data=='v25:vip': return await v25_user_vip_plans(update,context)
        if action=='userplan':
            pid=int(p[2]); plan=_v25_exec('SELECT * FROM subscription_plans_v25 WHERE id=? AND enabled=1',(pid,),fetchone=True)
            if not plan: await q.answer('پلن پیدا نشد.',show_alert=True); return
            card_on=get_system_setting('vip_card_enabled','0')=='1'; gw_on=get_system_setting('vip_gateway_enabled','0')=='1'
            kb=[]
            if gw_on and get_system_setting('vip_gateway_url',''):
                kb.append([InlineKeyboardButton('💳 پرداخت آنلاین',url=get_system_setting('vip_gateway_url',''))])
            if card_on and get_system_setting('vip_card_number',''):
                kb.append([InlineKeyboardButton('💵 کارت‌به‌کارت',callback_data=f'v25:vipglobalcard:{pid}')])
            kb.append([InlineKeyboardButton('⬅️ پلن‌ها',callback_data='v25:vip'),main_menu_button(uid)])
            await q.message.edit_text(f"💎 <b>{html.escape(plan['name'])}</b>\n\n💰 {irr(plan['price_rial'])}\n\nروش پرداخت را انتخاب کن:",parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb)); return
        if action=='vipglobalcard':
            plan=_v25_exec('SELECT * FROM subscription_plans_v25 WHERE id=? AND enabled=1',(int(p[2]),),fetchone=True)
            if not plan or get_system_setting('vip_card_enabled','0')!='1' or not get_system_setting('vip_card_number',''):
                await q.answer('پرداخت کارت‌به‌کارت VIP در حال حاضر فعال نیست.',show_alert=True); return
            num=get_system_setting('vip_card_number',''); name=get_system_setting('vip_card_name','')
            context.user_data['v25_mode']='vip_receipt'; context.user_data['vip_plan_id']=int(p[2])
            await q.message.edit_text(f'💵 <b>پرداخت کارت‌به‌کارت VIP</b>\n\n💰 مبلغ: {irr(plan["price_rial"])}\n💳 شماره کارت: <code>{html.escape(num)}</code>\n👤 به نام: {html.escape(name or "—")}\n\n📎 بعد از واریز تصویر رسید را بفرست.',parse_mode='HTML',reply_markup=v25_back(uid,'v25:vip')); return
        if data=='v25:adminvip': return await v25_admin_vip_payment(update,context)
        if data=='v25:vipcardtoggle':
            if not admin_guard(uid): await q.answer('⛔',show_alert=True); return
            set_system_setting('vip_card_enabled','0' if get_system_setting('vip_card_enabled','0')=='1' else '1',uid); return await v25_admin_vip_payment(update,context)
        if data=='v25:vipgatewaytoggle':
            if not admin_guard(uid): await q.answer('⛔',show_alert=True); return
            set_system_setting('vip_gateway_enabled','0' if get_system_setting('vip_gateway_enabled','0')=='1' else '1',uid); return await v25_admin_vip_payment(update,context)
        if data=='v25:vipcardinfo':
            if not admin_guard(uid): await q.answer('⛔',show_alert=True); return
            context.user_data['v25_mode']='admin_vip_card_number'; await q.message.edit_text('💳 شماره کارت VIP را بفرست:'); return
        if data=='v25:vipgatewayinfo':
            if not admin_guard(uid): await q.answer('⛔',show_alert=True); return
            context.user_data['v25_mode']='admin_vip_gateway_url'; await q.message.edit_text('🔗 لینک درگاه VIP را بفرست:'); return
        if action=='finadd':
            if not admin_guard(uid) and not customer_feature_allowed(uid): await q.answer('⛔',show_alert=True); return
            customer_id=int(p[2]); c=db(); owner_row=c.execute('SELECT id FROM customers WHERE id=? AND owner_user_id=?',(customer_id,uid)).fetchone(); c.close();
            if not owner_row: await q.answer('⛔ مشتری متعلق به حساب شما نیست.',show_alert=True); return
            context.user_data['v25_mode']='v25_mode_fin_total'; context.user_data['fin_customer_id']=customer_id; await q.message.edit_text('💰 مبلغ کل خدمت را به ریال وارد کن:'); return
        if action=='bizfinance': return await v25_finance_menu(update,context)
        return await _OLD_V25_CALLBACK_EXTRA(update,context)
    except Exception as e:
        logger.exception('V25 extra callback error: %s',e); await q.message.reply_text('❌ عملیات انجام نشد.',reply_markup=v25_back(uid))

# 5) Admin feature page: include ALL feature flags, not only V25-specific ones.
async def v25_admin_feature_status(update,context):
    uid=update.effective_user.id
    if not admin_guard(uid): return await update.callback_query.answer('⛔ دسترسی ندارید.',show_alert=True)
    c=db(); rows=c.execute('SELECT key,enabled FROM feature_flags ORDER BY key').fetchall(); c.close()
    labels=dict(FEATURE_LABELS_FA)
    labels.update(V25_FEATURE_LABELS)
    text='🔧 <b>وضعیت همه قابلیت‌ها</b>\n\n'; kb=[]
    for r in rows:
        label=labels.get(r['key'],r['key'].replace('_',' ')); state='🟢' if r['enabled'] else '🔴'; text+=f'{state} {label}\n'; kb.append([InlineKeyboardButton(f'{state} {label}',callback_data=f'v25:feat:{r["key"]}')])
    kb.append([InlineKeyboardButton('⬅️ مدیریت',callback_data='v25:adminmenu'),main_menu_button(uid)])
    await update.callback_query.message.edit_text(text,parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

# 6) Text modes for global VIP settings.
_OLD_V25_INSTALLMENT_TEXT_EXTRA=v25_installment_text_save
async def v25_installment_text_save(update,context):
    uid=update.effective_user.id; mode=context.user_data.get('v25_mode'); txt=(update.message.text or '').strip()
    if mode=='admin_vip_card_number':
        if not admin_guard(uid): clear_flow(context); return True
        context.user_data['vip_card_number_new']=txt; context.user_data['v25_mode']='admin_vip_card_name'; await update.message.reply_text('👤 نام صاحب کارت را بفرست یا - بزن:'); return True
    if mode=='admin_vip_card_name':
        if not admin_guard(uid): clear_flow(context); return True
        set_system_setting('vip_card_number',context.user_data.get('vip_card_number_new',''),uid); set_system_setting('vip_card_name','' if txt=='-' else txt,uid); clear_flow(context); await update.message.reply_text('✅ اطلاعات کارت VIP ذخیره شد؛ برای جلوگیری از فعال‌شدن ناخواسته، وضعیت همچنان جداگانه قابل کنترل است.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('💎 پرداخت VIP',callback_data='v25:adminvip')],[main_menu_button(uid)]])); return True
    if mode=='admin_vip_gateway_url':
        if not admin_guard(uid): clear_flow(context); return True
        set_system_setting('vip_gateway_url',txt,uid); clear_flow(context); await update.message.reply_text('✅ لینک درگاه VIP ذخیره شد. وضعیت روشن/خاموش جداست.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('💎 پرداخت VIP',callback_data='v25:adminvip')],[main_menu_button(uid)]])); return True
    return await _OLD_V25_INSTALLMENT_TEXT_EXTRA(update,context)

# 7) Customer direct-message shortcut in customer detail.
_OLD_CUSTOMER_DETAIL_V25=customer_detail
async def customer_detail(update,context,cid):
    await _OLD_CUSTOMER_DETAIL_V25(update,context,cid)
    try:
        # Send an additional message button after the detail view; keeping this non-destructive.
        q=update.callback_query; uid=q.from_user.id; r=get_customer(uid,cid)
        if r and r['telegram_user_id']:
            await q.message.reply_text('📩 برای ارسال پیام مستقیم به این مشتری، متن را بفرست:',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📩 ارسال پیام',callback_data=f'v25:msgcustomer:{cid}')],[main_menu_button(uid)]]))
    except Exception: pass

_OLD_V25_CALLBACK_EXTRA2=v25_callback
async def v25_callback(update,context):
    q=update.callback_query; data=q.data; uid=q.from_user.id; p=data.split(':'); action=p[1] if len(p)>1 else ''
    if action=='msgcustomer':
        cid=int(p[2]); r=get_customer(uid,cid)
        if not r or not r['telegram_user_id']: await q.answer('این مشتری شناسه تلگرام ندارد.',show_alert=True); return
        context.user_data['v25_mode']='customer_direct_message'; context.user_data['customer_message_id']=cid; await q.answer(); await q.message.reply_text('📩 متن پیام را بفرست:',reply_markup=v25_back(uid,'v25:business')); return
    return await _OLD_V25_CALLBACK_EXTRA2(update,context)

# 8) Final text mode: direct customer message.
_OLD_V25_INSTALLMENT_TEXT_FINAL=v25_installment_text_save
async def v25_installment_text_save(update,context):
    uid=update.effective_user.id; mode=context.user_data.get('v25_mode'); txt=(update.message.text or '').strip()
    if mode=='customer_direct_message':
        cid=int(context.user_data.get('customer_message_id')); r=get_customer(uid,cid); clear_flow(context)
        if not r or not r['telegram_user_id']: await update.message.reply_text('❌ مشتری قابل پیام‌رسانی نیست.',reply_markup=customer_keyboard(uid)); return True
        try:
            await context.bot.send_message(r['telegram_user_id'],f'📩 <b>پیام از {html.escape(ensure_business_profile(uid)["business_name"] or "کسب‌وکار")}</b>\n\n{html.escape(txt)}',parse_mode='HTML')
            await update.message.reply_text('✅ پیام ارسال شد.',reply_markup=customer_keyboard(uid))
        except Exception:
            await update.message.reply_text('❌ ارسال پیام ناموفق بود.',reply_markup=customer_keyboard(uid))
        return True
    return await _OLD_V25_INSTALLMENT_TEXT_FINAL(update,context)



# Voice execution: simple, confirm-first admin commands + goal/reminder creation.
_OLD_V25_VOICE_CONFIRM=v25_voice_confirm
async def v25_voice_confirm(update,context):
    uid=update.effective_user.id; text=(context.user_data.get('v25_voice_text') or '').strip(); action=context.user_data.get('v25_voice_action'); await update.callback_query.answer()
    if action=='admin' and admin_guard(uid):
        low=text.lower(); feature_map={
            'رزرو':'booking','رزرو آنلاین':'customer_online_booking','booking':'customer_online_booking',
            'مشتری':'customers','crm':'customers','پیامک':'sms','sms':'sms','وی آی پی':'vip','vip':'vip',
            'هوش مصنوعی':'ai','هوش مصنوعی':'ai','ai':'ai','قیمت':'price_data','قیمت بازار':'price_data',
            'نظرسنجی':'surveys','voice':'voice','دستیار صوتی':'voice','کارت به کارت':'card_to_card','درگاه':'payments',
            'یادآوری':'reminders','اهداف':'goals','تقویم':'calendar_hub'
        }
        target=None
        for k,v in sorted(feature_map.items(),key=lambda x:-len(x[0])):
            if k in low: target=v; break
        turning_off=bool(re.search(r'غیرفعال|خاموش|خاموشش|disable|off',low))
        turning_on=(not turning_off) and bool(re.search(r'فعال|روشن|روشنش|enable|on',low))
        # VIP monthly price via voice: "قیمت VIP یک ماهه 3990000 ریال".
        m=re.search(r'(?:vip|وی[ -]?آی[ -]?پی).*?(?:یک ماهه|ماهانه|one month).*?(\d[\d,]*)',low,re.I)
        if m:
            price=int(m.group(1).replace(',','')); row=_v25_exec("SELECT id FROM subscription_plans_v25 WHERE code='one_month'",fetchone=True)
            if row: _v25_exec('UPDATE subscription_plans_v25 SET price_rial=?,updated_at=? WHERE id=?',(price,_v25_now(),row['id']))
            context.user_data.clear(); await update.callback_query.message.edit_text(f'✅ قیمت پلن یک‌ماهه VIP به {irr(price)} تغییر کرد.',reply_markup=v25_back(uid,'v25:adminmenu')); return
        # Card number in admin voice.
        nums=re.sub(r'[^0-9]','',text)
        if len(nums)==16 and ('کارت' in low or 'card' in low):
            set_system_setting('vip_card_number',nums,uid); context.user_data.clear(); await update.callback_query.message.edit_text(f'✅ شماره کارت VIP ذخیره شد.\n💳 <code>{nums}</code>\n\nفعال‌شدن نمایش کارت هنوز از تنظیمات جداگانه کنترل می‌شود.',parse_mode='HTML',reply_markup=v25_back(uid,'v25:adminvip')); return
        if target and (turning_on or turning_off):
            set_feature(target,turning_on,uid); set_feature_access_mode(target,'free' if turning_on else 'off',uid); context.user_data.clear(); await update.callback_query.message.edit_text(('✅ قابلیت «'+V25_FEATURE_LABELS.get(target,target)+'» فعال شد.' if turning_on else '🔴 قابلیت «'+V25_FEATURE_LABELS.get(target,target)+'» غیرفعال شد.'),reply_markup=v25_back(uid,'v25:adminmenu')); return
        await update.callback_query.message.edit_text('🛡️ فرمان صوتی تشخیص داده شد اما برای اجرای ایمن، دستور واضح‌تری لازم است. مثال: «رزرو آنلاین غیرفعال شود» یا «قیمت بازار فعال شود».',reply_markup=v25_back(uid,'v25:adminmenu')); return
    if action=='goal':
        # Register a user goal from the transcript; any explicit time becomes the daily goal reminder.
        title=re.sub(r'^(?:برای|هدف|یادم|یادآوری)\s*','',text,flags=re.I).strip(' .،') or text
        m=re.search(r'(?:ساعت|at)\s*(\d{1,2})(?::(\d{2}))?',text,re.I)
        tm=None
        if m:
            tm=parse_time(f'{m.group(1)}:{m.group(2) or "00"}')
        add_goal(uid,title,'✨ شخصی',tm,2)
        context.user_data.clear(); await update.callback_query.message.edit_text(f'✅ هدف ثبت شد.\n\n🎯 {html.escape(title)}'+(f'\n⏰ یادآوری: {tm}' if tm else ''),parse_mode='HTML',reply_markup=v25_back(uid,'v25:hub')); return
    return await _OLD_V25_VOICE_CONFIRM(update,context)


async def v25_customer_message_menu(update,context):
    uid=update.effective_user.id; rows=customer_list_rows(uid); selected=set(context.user_data.get('customer_message_selected',[])); lines=['📩 <b>ارسال پیام به مشتریان</b>','']
    lines.append(f'انتخاب‌شده: {len(selected)}')
    kb=[]
    for r in rows[:50]:
        mark='✅' if r['id'] in selected else '☐'; kb.append([InlineKeyboardButton(f'{mark} {r["name"]}',callback_data=f'v25:msgsel:{r["id"]}')])
    kb += [[InlineKeyboardButton('📢 همه مشتریان',callback_data='v25:msgall')],[InlineKeyboardButton('📤 ارسال پیام انتخاب‌شده',callback_data='v25:msgsend')],[InlineKeyboardButton('⬅️ بازگشت',callback_data='v25:business'),main_menu_button(uid)]]
    await update.callback_query.message.edit_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

_OLD_V25_CALLBACK_MSG=v25_callback
async def v25_callback(update,context):
    q=update.callback_query; data=q.data; uid=q.from_user.id; p=data.split(':'); action=p[1] if len(p)>1 else ''
    if data=='v25:customermsg': return await v25_customer_message_menu(update,context)
    if action=='msgsel':
        cid=int(p[2]); selected=set(context.user_data.get('customer_message_selected',[]));
        if cid in selected: selected.remove(cid)
        else: selected.add(cid)
        context.user_data['customer_message_selected']=list(selected); await q.answer(); return await v25_customer_message_menu(update,context)
    if data=='v25:msgall':
        rows=customer_list_rows(uid); context.user_data['customer_message_selected']=[r['id'] for r in rows if r['telegram_user_id']]; await q.answer(); return await v25_customer_message_menu(update,context)
    if data=='v25:msgsend':
        if not context.user_data.get('customer_message_selected'): await q.answer('حداقل یک مشتری را انتخاب کن.',show_alert=True); return
        context.user_data['v25_mode']='customer_multi_message'; await q.answer(); await q.message.edit_text('📩 متن پیام را بفرست:',reply_markup=v25_back(uid,'v25:customermsg')); return
    return await _OLD_V25_CALLBACK_MSG(update,context)

_OLD_V25_INSTALLMENT_TEXT_MSG=v25_installment_text_save
async def v25_installment_text_save(update,context):
    uid=update.effective_user.id; mode=context.user_data.get('v25_mode'); txt=(update.message.text or '').strip()
    if mode=='customer_multi_message':
        ids=list(context.user_data.get('customer_message_selected',[])); biz=ensure_business_profile(uid); sent=0
        c=db(); rows=c.execute('SELECT id,telegram_user_id FROM customers WHERE owner_user_id=? AND id IN (%s)'%(','.join('?'*len(ids))),tuple([uid,*ids])).fetchall() if ids else []; c.close()
        for r in rows:
            if not r['telegram_user_id']: continue
            try:
                await context.bot.send_message(r['telegram_user_id'],f'📩 <b>پیام از {html.escape(biz["business_name"] or "کسب‌وکار")}</b>\n\n{html.escape(txt)}',parse_mode='HTML'); sent+=1
            except Exception: pass
        clear_flow(context); await update.message.reply_text(f'✅ پیام برای {sent} مشتری ارسال شد.',reply_markup=customer_keyboard(uid)); return True
    return await _OLD_V25_INSTALLMENT_TEXT_MSG(update,context)

# ------------------ Final callback dispatcher extension ------------------
_OLD_V25_CALLBACK_FINAL=v25_callback
async def v25_callback(update,context):
    q=update.callback_query; data=q.data; uid=q.from_user.id; parts=data.split(':'); action=parts[1] if len(parts)>1 else ''
    try:
        if data=='v25:adminmenu': return await v25_admin_menu(update,context)
        if data=='v25:adminfeatures': return await v25_admin_feature_status(update,context)
        if data=='v25:adminplans': return await v25_admin_plans(update,context)
        if data=='v25:adminpayment': return await v25_admin_payment(update,context)
        if data=='v25:adminsms': return await v25_admin_sms(update,context)
        if data=='v25:adminsurvey': return await v25_admin_survey(update,context)
        if data=='v25:adminvoice': return await v25_admin_voice(update,context)
        if data=='v25:adminmorning': return await v25_admin_morning(update,context)
        if data=='v25:adminprices': return await v25_admin_prices(update,context)
        if data in {'v25:reports','v25:report_week','v25:report_month','v25:toggle_morning','v25:toggle_night','v25:toggle_friday','v25:toggle_prices'} and not admin_guard(uid):
            await q.answer('⛔ دسترسی ندارید.',show_alert=True); return
        if data=='v25:reports': return await v25_reports(update,context,'day')
        if data=='v25:report_week': return await v25_reports(update,context,'week')
        if data=='v25:report_month': return await v25_reports(update,context,'month')
        if data=='v25:toggle_morning': set_system_setting('morning_message_enabled','0' if get_system_setting('morning_message_enabled','1')=='1' else '1',uid); return await v25_admin_morning(update,context)
        if data=='v25:toggle_night': set_system_setting('night_message_enabled','0' if get_system_setting('night_message_enabled','1')=='1' else '1',uid); return await v25_admin_morning(update,context)
        if data=='v25:toggle_friday': set_system_setting('friday_pause','0' if get_system_setting('friday_pause','0')=='1' else '1',uid); return await v25_admin_morning(update,context)
        if data=='v25:toggle_prices': set_system_setting('price_data_status','off' if get_system_setting('price_data_status','auto')!='off' else 'auto',uid); return await v25_admin_prices(update,context)
        if action=='vip_receipt':
            if not admin_guard(uid): await q.answer('⛔ دسترسی ندارید.',show_alert=True); return
            if len(parts) < 4 or parts[2] not in {'approve','reject'}:
                await q.answer('عملیات نامعتبر است.',show_alert=True); return
            rid=int(parts[3])
            c=db(); row=c.execute('SELECT vr.*,p.name,p.duration_minutes FROM vip_receipts vr JOIN subscription_plans_v25 p ON p.id=vr.plan_id WHERE vr.id=?',(rid,)).fetchone()
            if not row: c.close(); await q.answer('رسید پیدا نشد.',show_alert=True); return
            if row['status']!='pending': c.close(); await q.answer('این رسید قبلاً بررسی شده است.',show_alert=True); return
            now=_v25_now(); status=parts[2]
            if status=='approve':
                base=datetime.now(TZ); u=c.execute('SELECT vip_until FROM users WHERE user_id=?',(row['user_id'],)).fetchone()
                if u and u['vip_until']:
                    try: base=max(base,datetime.fromisoformat(u['vip_until']))
                    except Exception: pass
                expires=base+timedelta(minutes=int(row['duration_minutes']))
                c.execute('UPDATE users SET vip_until=? WHERE user_id=?',(expires.isoformat(),row['user_id']))
                c.execute('INSERT INTO subscription_history(user_id,plan,duration_days,source,amount,started_at,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?)',(row['user_id'],row['name'],max(0,int(round(int(row['duration_minutes'])/1440))), 'card_receipt',row['amount_rial'],now,expires.isoformat(),now))
                msg='✅ رسید تأیید شد و VIP فعال شد.'
                user_msg=f'✅ پرداخت VIP شما تأیید شد.\n\n💎 پلن: {html.escape(row["name"])}\n⏰ پایان VIP: {fa_datetime(expires)}'
            else:
                msg='❌ رسید رد شد.'; user_msg='❌ رسید پرداخت VIP شما تأیید نشد. لطفاً اطلاعات پرداخت را بررسی و در صورت نیاز دوباره اقدام کنید.'
            c.execute("UPDATE vip_receipts SET status=?,reviewed_at=?,reviewed_by=? WHERE id=? AND status=\'pending\'",(status,now,uid,rid)); c.commit(); c.close()
            try: await context.bot.send_message(row['user_id'],user_msg,parse_mode='HTML',reply_markup=keyboard(row['user_id']))
            except Exception: logger.exception('VIP receipt user notification failed')
            await q.message.edit_text(msg,reply_markup=v25_back(uid,'v25:adminvip')); return
        if action=='instview': return await v25_installment_view(update,context,int(parts[2]))
        if action=='instpay':
            ipid=int(parts[2]); status=parts[3]
            # Never allow a user to mutate another user's installment by guessing its ID.
            row=_v25_exec('SELECT ip.*,p.user_id FROM installment_payments ip JOIN installment_plans p ON p.id=ip.plan_id WHERE ip.id=? AND p.user_id=?', (ipid,uid), fetchone=True)
            if not row: await q.answer('⛔ این قسط متعلق به حساب شما نیست.',show_alert=True); return
            if status not in {'paid','later','unpaid'}: await q.answer('وضعیت نامعتبر است.',show_alert=True); return
            if status=='paid': _v25_exec('UPDATE installment_payments SET status="paid",paid_rial=amount_rial,paid_at=? WHERE id=?',(_v25_now(),ipid)); msg='✅ پرداخت ثبت شد.'
            elif status=='later': _v25_exec('UPDATE installment_payments SET status="partial",note=? WHERE id=?',('کاربر اعلام کرد بعداً پرداخت می‌کند.',ipid)); msg='⏳ برای بعد نگه داشته شد.'
            else: _v25_exec('UPDATE installment_payments SET status="unpaid" WHERE id=?',(ipid,)); msg='❌ عدم پرداخت ثبت شد.'
            await q.message.edit_text(msg,reply_markup=v25_back(uid,'v25:installments')); return
        if action=='planedit':
            if not admin_guard(uid): await q.answer('⛔',show_alert=True); return
            pid=int(parts[2]); plan=_v25_exec('SELECT * FROM subscription_plans_v25 WHERE id=?',(pid,),fetchone=True)
            if not plan: return
            context.user_data['v25_admin_plan_id']=pid; context.user_data['v25_mode']='admin_plan_price'; await q.message.edit_text(f'💎 {html.escape(plan["name"])}\n\nقیمت جدید را به ریال بفرست:'); return
        if action=='planadd':
            if not admin_guard(uid): await q.answer('⛔',show_alert=True); return
            context.user_data['v25_mode']='admin_plan_name'; await q.message.edit_text('📝 نام پلن جدید را بفرست:'); return
        if action=='feat':
            if not admin_guard(uid): await q.answer('⛔',show_alert=True); return
            if len(parts) < 3 or not _feature_flag_exists(parts[2]):
                await q.answer('قابلیت نامعتبر است.',show_alert=True); return
            key=parts[2]
            cur=feature_enabled(key)
            set_feature(key,not cur,uid)
            set_feature_access_mode(key,'free' if not cur else 'off',uid)
            return await v25_admin_feature_status(update,context)
        if action=='smstoggle':
            if not admin_guard(uid): await q.answer('⛔',show_alert=True); return
            cfg=_v25_exec('SELECT enabled FROM sms_settings WHERE owner_user_id=?',(uid,),fetchone=True); enabled=not bool(cfg and cfg['enabled']); now=_v25_now(); _v25_exec('INSERT INTO sms_settings(owner_user_id,enabled,updated_at) VALUES(?,?,?) ON CONFLICT(owner_user_id) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at',(uid,int(enabled),now)); return await v25_admin_sms(update,context)
        if action=='smsconfig':
            if not admin_guard(uid): await q.answer('⛔',show_alert=True); return
            context.user_data['v25_mode']='admin_sms_endpoint'; await q.message.edit_text('📡 Endpoint سرویس پیامکی را بفرست:'); return
        if action=='smstest':
            if not admin_guard(uid): await q.answer('⛔',show_alert=True); return
            context.user_data['v25_mode']='v25_sms_test'; await q.message.edit_text('📱 شماره مقصد تست را بفرست:',reply_markup=v25_back(uid,'v25:adminsms')); return
        if action=='voice_confirm' and admin_guard(uid):
            return await _OLD_V25_CALLBACK_FINAL(update,context)
        if action=='gateway' and admin_guard(uid):
            return await _OLD_V25_CALLBACK_FINAL(update,context)
        if action=='card' and admin_guard(uid):
            return await _OLD_V25_CALLBACK_FINAL(update,context)
        return await _OLD_V25_CALLBACK_FINAL(update,context)
    except Exception as e:
        logger.exception('Final V25 callback error: %s',e)
        await q.message.reply_text(
            f'⚠️ این عملیات با خطا روبه‌رو شد.\n\nکد خطا: <code>{type(e).__name__}</code>',
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('🔄 تلاش دوباره',callback_data=q.data)],
                [InlineKeyboardButton('⬅️ بازگشت',callback_data='v25:hub'),main_menu_button(uid)]
            ])
        )

# Intercept a few additional text modes while preserving the legacy router.
_OLD_V25_INSTALLMENT_TEXT_SAVE=v25_installment_text_save
async def v25_installment_text_save(update,context):
    uid=update.effective_user.id; mode=context.user_data.get('v25_mode'); text=normalize_digits((update.message.text or '').strip())
    if mode=='admin_plan_price':
        if not admin_guard(uid): clear_flow(context); return True
        try: price=int(float(text.replace(',','')))
        except Exception: await update.message.reply_text('❌ مبلغ نامعتبر است.'); return True
        pid=context.user_data.get('v25_admin_plan_id'); _v25_exec('UPDATE subscription_plans_v25 SET price_rial=?,updated_at=? WHERE id=?',(price,_v25_now(),pid)); clear_flow(context); await update.message.reply_text('✅ قیمت پلن تغییر کرد.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('💎 پلن‌ها',callback_data='v25:adminplans')],[main_menu_button(uid)]])); return True
    if mode=='admin_plan_name':
        if not admin_guard(uid): clear_flow(context); return True
        context.user_data['admin_plan_name']=text; context.user_data['v25_mode']='admin_plan_duration'; await update.message.reply_text('⏱️ مدت را به دقیقه وارد کن. مثال: 43200 برای ۳۰ روز:'); return True
    if mode=='admin_plan_duration':
        if not admin_guard(uid): clear_flow(context); return True
        try: dur=int(text)
        except Exception: await update.message.reply_text('❌ مدت نامعتبر است.'); return True
        context.user_data['admin_plan_duration']=dur; context.user_data['v25_mode']='admin_plan_create_price'; await update.message.reply_text('💰 قیمت پلن را به ریال بفرست:'); return True
    if mode=='admin_plan_create_price':
        if not admin_guard(uid): clear_flow(context); return True
        try: price=int(float(text.replace(',','')))
        except Exception: await update.message.reply_text('❌ مبلغ نامعتبر است.'); return True
        code='custom_'+hashlib.sha256((context.user_data.get('admin_plan_name','')+_v25_now()).encode()).hexdigest()[:10]
        _v25_exec('INSERT INTO subscription_plans_v25(code,name,duration_minutes,price_rial,enabled,created_at,updated_at) VALUES(?,?,?,?,1,?,?)',(code,context.user_data['admin_plan_name'],context.user_data['admin_plan_duration'],price,_v25_now(),_v25_now())); clear_flow(context); await update.message.reply_text('✅ پلن جدید ساخته شد.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('💎 پلن‌ها',callback_data='v25:adminplans')],[main_menu_button(uid)]])); return True
    if mode=='admin_sms_endpoint':
        if not admin_guard(uid): clear_flow(context); return True
        context.user_data['admin_sms_endpoint']=text; context.user_data['v25_mode']='admin_sms_key'; await update.message.reply_text('🔐 API Key را بفرست:'); return True
    if mode=='admin_sms_key':
        if not admin_guard(uid): clear_flow(context); return True
        endpoint=context.user_data.get('admin_sms_endpoint',''); now=_v25_now(); _v25_exec('INSERT INTO sms_settings(owner_user_id,enabled,provider,endpoint,api_key,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(owner_user_id) DO UPDATE SET endpoint=excluded.endpoint,api_key=excluded.api_key,updated_at=excluded.updated_at',(uid,0,'custom',endpoint,text,now)); clear_flow(context); await update.message.reply_text('✅ تنظیمات سرویس پیامکی ذخیره شد؛ سرویس همچنان خاموش است تا خودت فعالش کنی.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📱 پیامک',callback_data='v25:adminsms')],[main_menu_button(uid)]])); return True
    if mode=='v25_mode_fin_customer':
        context.user_data['fin_customer_id']=int(text); context.user_data['v25_mode']='v25_mode_fin_total'; await update.message.reply_text('💰 مبلغ کل خدمت را به ریال وارد کن:'); return True
    if mode=='v25_mode_fin_total':
        context.user_data['fin_total']=int(float(text.replace(',',''))); context.user_data['v25_mode']='v25_mode_fin_paid'; await update.message.reply_text('✅ مبلغ پرداخت‌شده را به ریال وارد کن (اگر هنوز چیزی پرداخت نشده 0 بزن):'); return True
    if mode=='v25_mode_fin_paid':
        paid=int(float(text.replace(',',''))); total=int(context.user_data.get('fin_total',0)); customer_id=int(context.user_data['fin_customer_id'])
        if total < 0 or paid < 0 or paid > total:
            await update.message.reply_text('❌ مبلغ واردشده معتبر نیست.'); return True
        c=db(); owner_row=c.execute('SELECT id FROM customers WHERE id=? AND owner_user_id=?',(customer_id,uid)).fetchone()
        if not owner_row:
            c.close(); clear_flow(context); await update.message.reply_text('⛔ مشتری متعلق به حساب شما نیست.'); return True
        status='paid' if paid>=total else ('partial' if paid>0 else 'pending')
        c.execute('INSERT INTO customer_finance(owner_user_id,customer_id,amount_rial,paid_rial,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(uid,customer_id,total,paid,status,_v25_now(),_v25_now())); c.commit(); c.close(); clear_flow(context)
        await update.message.reply_text('✅ تراکنش مالی مشتری ثبت شد.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📒 مالی مشتریان',callback_data='v25:bizfinance')],[main_menu_button(uid)]])); return True
    if mode=='survey_comment':
        aid=int(context.user_data.get('survey_appointment_id')); c=db(); owner_ok=c.execute('SELECT id FROM appointments WHERE id=? AND customer_id IN (SELECT id FROM customers WHERE telegram_user_id=? )',(aid,uid)).fetchone();
        if not owner_ok: c.close(); clear_flow(context); await update.message.reply_text('⛔ این نظرسنجی متعلق به حساب شما نیست.'); return True
        c.execute('UPDATE survey_responses SET suggestion=?,comment=? WHERE appointment_id=?',(text,text,aid)); c.commit(); c.close(); clear_flow(context); await update.message.reply_text('🙏 ممنون؛ پیشنهادت ثبت شد.',reply_markup=InlineKeyboardMarkup([[main_menu_button(uid)]])); return True
    return await _OLD_V25_INSTALLMENT_TEXT_SAVE(update,context)

_OLD_V25_BUSINESS_TEXT_SAVE=v25_business_text_save
async def v25_business_text_save(update,context):
    uid=update.effective_user.id; mode=context.user_data.get('v25_mode'); txt=(update.message.text or '').strip()
    if mode=='admin_sms_endpoint' or mode=='admin_sms_key' or mode.startswith('admin_plan') if isinstance(mode,str) else False:
        return await v25_installment_text_save(update,context)
    if mode=='v25_sms_test':
        phone=txt; cfg=_v25_exec('SELECT * FROM sms_settings WHERE owner_user_id=?',(uid,),fetchone=True); msg='MyTasks SMS test'; ok=False; details=''
        if cfg and cfg['endpoint'] and cfg['api_key']:
            try: ok,details=await v25_sms_send(uid,phone,msg)
            except Exception as e: details=str(e)
        clear_flow(context); await update.message.reply_text('✅ تست ارسال شد.' if ok else '⚠️ تست انجام نشد؛ تنظیمات سرویس را بررسی کن.\n'+html.escape(details),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📱 پیامک',callback_data='v25:sms')],[main_menu_button(uid)]])); return True
    if mode=='booking_phone' or mode=='booking_name' or mode=='survey_comment':
        return await v25_installment_text_save(update,context)
    return await _OLD_V25_BUSINESS_TEXT_SAVE(update,context)

# The final text router uses the enhanced state dispatcher before legacy routes.
_OLD_TEXT_ROUTER_FINAL=text_router
async def text_router(update,context):
    uid=update.effective_user.id
    txt=(update.message.text or '').strip() if update.message else ''
    if txt in ('⬅️ برگشت','⬅️ Back'):
        clear_flow(context); await update.message.reply_text(v25_hub_text(uid),parse_mode='HTML',reply_markup=v25_hub_keyboard(uid)); return
    if txt in ('🏠 منوی اصلی','🏠 Main Menu'):
        clear_flow(context); await update.message.reply_text('🏠 منوی اصلی',reply_markup=keyboard(uid)); return
    if txt in ('🎙️ دستیار صوتی','🎙️ Voice Assistant'):
        context.user_data['v25_voice_mode']=True; await update.message.reply_text('🎙️ ویست رو بفرست. / Send a voice message.'); return
    if txt in ('🧠 مرکز من','🧠 My Center'):
        await v25_hub(update,context); return
    mode=context.user_data.get('v25_mode')
    if mode in {'admin_plan_price','admin_plan_name','admin_plan_duration','admin_plan_create_price','admin_sms_endpoint','admin_sms_key','v25_mode_fin_customer','v25_mode_fin_total','v25_mode_fin_paid','survey_comment','booking_name','booking_phone','v25_sms_test'}:
        if await v25_installment_text_save(update,context): return
    # Existing wrapper already handles V25 regular states and legacy fallback.
    return await _OLD_TEXT_ROUTER_FINAL(update,context)

# Final admin button opens the real V25 admin center, not the business panel.
_ORIGINAL_FINAL_ADMIN_KEYBOARD_2=final_admin_keyboard
def final_admin_keyboard():
    base=_LEGACY_FINAL_ADMIN_KEYBOARD().inline_keyboard
    rows=[list(r) for r in base]
    rows.append([InlineKeyboardButton('🛡️ مرکز مدیریت جدید',callback_data='v25:adminmenu')])
    return InlineKeyboardMarkup(rows)
admin_keyboard=final_admin_keyboard


# ===================== TOKEN / QUOTA SYSTEM =====================
# Free by default. Admin can later switch any feature to limited/VIP/off.
TOKEN_FEATURES = [
    ("ai", "🤖 هوش مصنوعی"),
    ("voice", "🎙️ دستیار صوتی"),
    ("price_data", "📈 قیمت بازار"),
    ("portfolio", "💰 سرمایه‌های من"),
    ("customers", "👥 CRM / مشتریان"),
    ("customer_online_booking", "🔗 رزرو آنلاین"),
    ("reminders", "🔔 یادآوری"),
    ("reports", "📊 گزارش حرفه‌ای"),
    ("mini_app", "📱 Mini App"),
    ("smart_planner", "🧠 برنامه‌ریزی هوشمند"),
]

def token_now():
    return datetime.now(TZ).isoformat()

def token_setting(key, default=""):
    try:
        c=db(); r=c.execute("SELECT value FROM token_settings WHERE key=?",(key,)).fetchone(); c.close()
        return r["value"] if r else default
    except Exception:
        return default

def set_token_setting(key,value,admin_id=0):
    c=db(); c.execute("INSERT INTO token_settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",(key,str(value),token_now())); c.commit(); c.close()
    if admin_id: admin_log(admin_id,"token_setting",None,f"{key}={value}")

def token_balance(uid):
    c=db(); r=c.execute("SELECT balance FROM token_wallets WHERE user_id=?",(int(uid),)).fetchone(); c.close(); return int(r["balance"]) if r else 0

def add_tokens(uid, amount, reason="", ref_key=None):
    amount=int(amount)
    if amount == 0: return True
    c=db()
    try:
        c.execute("INSERT OR IGNORE INTO token_wallets(user_id,balance,updated_at) VALUES(?,?,?)",(int(uid),0,token_now()))
        if ref_key:
            cur=c.execute("INSERT OR IGNORE INTO token_ledger(user_id,delta,reason,ref_key,created_at) VALUES(?,?,?,?,?)",(int(uid),amount,reason,ref_key,token_now()))
            if cur.rowcount != 1:
                c.close(); return False
        else:
            c.execute("INSERT INTO token_ledger(user_id,delta,reason,ref_key,created_at) VALUES(?,?,?,?,?)",(int(uid),amount,reason,None,token_now()))
        c.execute("UPDATE token_wallets SET balance=MAX(0,balance+?),updated_at=? WHERE user_id=?",(amount,token_now(),int(uid)))
        c.commit(); c.close(); return True
    except Exception:
        c.rollback(); c.close(); logger.exception("Token award failed")
        return False

def spend_tokens(uid, amount, reason="", ref_key=None):
    amount=int(amount)
    if amount <= 0: return True
    c=db()
    try:
        # Serialize balance checks + deductions so concurrent requests cannot overspend.
        c.execute("BEGIN IMMEDIATE")
        r=c.execute("SELECT balance FROM token_wallets WHERE user_id=?",(int(uid),)).fetchone()
        if not r or int(r["balance"]) < amount:
            c.rollback(); c.close(); return False
        if ref_key:
            cur=c.execute("INSERT OR IGNORE INTO token_ledger(user_id,delta,reason,ref_key,created_at) VALUES(?,?,?,?,?)",(int(uid),-amount,reason,ref_key,token_now()))
            if cur.rowcount != 1:
                c.close(); return False
        else:
            c.execute("INSERT INTO token_ledger(user_id,delta,reason,ref_key,created_at) VALUES(?,?,?,?,?)",(int(uid),-amount,reason,None,token_now()))
        c.execute("UPDATE token_wallets SET balance=balance-?,updated_at=? WHERE user_id=?",(amount,token_now(),int(uid)))
        c.commit(); c.close(); return True
    except Exception:
        c.rollback(); c.close(); logger.exception("Token spend failed")
        return False

def token_period_key(period):
    now=datetime.now(TZ)
    if period == "daily": return now.strftime("%Y-%m-%d")
    if period == "weekly": return f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    if period == "monthly": return now.strftime("%Y-%m")
    return "lifetime"

def token_rule(key):
    c=db(); r=c.execute("SELECT * FROM token_rules WHERE feature_key=?",(key,)).fetchone(); c.close()
    return r

def set_token_rule(key, free_limit=-1, period="lifetime", token_cost=0, after_limit="vip", enabled=1, admin_id=0):
    c=db(); c.execute("INSERT INTO token_rules(feature_key,free_limit,period,token_cost,after_limit,enabled,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(feature_key) DO UPDATE SET free_limit=excluded.free_limit,period=excluded.period,token_cost=excluded.token_cost,after_limit=excluded.after_limit,enabled=excluded.enabled,updated_at=excluded.updated_at",(key,int(free_limit),period,int(token_cost),after_limit,int(enabled),token_now())); c.commit(); c.close()
    if admin_id: admin_log(admin_id,"token_rule_change",None,f"{key}:{free_limit}:{period}:{token_cost}:{after_limit}:{enabled}")

def feature_token_gate(uid, key):
    """Return (allowed, reason). Defaults to unlimited/free for all features."""
    if uid in ADMIN_IDS: return True, "admin"
    try:
        mode=feature_access_mode(key,uid)
        if mode == "off": return False, "off"
        if mode == "vip" and not is_vip(uid):
            return False, "vip"
        rule=token_rule(key)
        if not rule or not int(rule["enabled"]) or int(rule["free_limit"]) < 0:
            return True, "free"
        period_key=token_period_key(rule["period"])
        c=db(); r=c.execute("SELECT used_count FROM feature_usage WHERE user_id=? AND feature_key=? AND period_key=?",(int(uid),key,period_key)).fetchone(); used=int(r["used_count"]) if r else 0
        limit=int(rule["free_limit"])
        if used < limit:
            c.execute("INSERT INTO feature_usage(user_id,feature_key,period_key,used_count,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id,feature_key,period_key) DO UPDATE SET used_count=feature_usage.used_count+1,updated_at=excluded.updated_at",(int(uid),key,period_key,1,token_now())); c.commit(); c.close(); return True, "free_quota"
        cost=int(rule["token_cost"])
        if cost > 0 and spend_tokens(uid,cost,f"use:{key}",f"use:{key}:{uid}:{period_key}:{used}"):
            c.close() if not c is None else None
            return True, "tokens"
        if rule["after_limit"] == "vip" and is_vip(uid):
            c.close(); return True, "vip"
        c.close(); return False, "quota"
    except Exception:
        logger.exception("Token gate failed for %s",key)
        return True, "fallback"

def token_gate_message(uid, key, reason):
    fa=lang(uid)=="fa"
    label=dict(TOKEN_FEATURES).get(key,"این قابلیت" if fa else "This feature")
    if reason == "vip":
        return f"💎 {label}\n\nاین بخش در حال حاضر مخصوص VIP است." if fa else f"💎 {label}\n\nThis feature is currently for VIP users."
    if reason == "quota":
        return (f"🎟️ سهمیه رایگان {label} تمام شده است.\n\n⭐ توکن شما: {token_balance(uid)}\n💎 می‌توانید با توکن ادامه دهید یا اشتراک VIP تهیه کنید.") if fa else (f"🎟️ Your free quota for {label} is finished.\n\n⭐ Tokens: {token_balance(uid)}\n💎 You can continue with tokens or get VIP.")
    return "⛔ این قابلیت فعلاً در دسترس نیست." if fa else "⛔ This feature is not available right now."

def token_user_text(uid):
    bal=token_balance(uid); fa=lang(uid)=="fa"
    c=db(); rows=c.execute("SELECT feature_key,free_limit,period,token_cost,after_limit,enabled FROM token_rules ORDER BY feature_key").fetchall(); c.close()
    lines=["🎟️ <b>توکن‌های من</b>","",f"⭐ موجودی: <b>{bal}</b>",""]
    if fa:
        lines.append("توکن‌ها برای استفاده بیشتر از قابلیت‌هایی که مدیر سهمیه‌گذاری کرده‌اند قابل مصرف‌اند.")
    else: lines.append("Tokens can be used for extra usage on features configured by the admin.")
    return "\n".join(lines)

def token_user_keyboard(uid):
    fa=lang(uid)=="fa"
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 بروزرسانی" if fa else "🔄 Refresh",callback_data="v25:tokens")],[InlineKeyboardButton("💎 VIP" if fa else "💎 VIP",callback_data="vip:main")],[InlineKeyboardButton("⬅️ بازگشت" if fa else "⬅️ Back",callback_data="v25:hub"),main_menu_button(uid)]])

def token_admin_text():
    ref=int(token_setting("referral_tokens_per_success","10") or 10)
    xp_per=int(token_setting("xp_per_token","100") or 100)
    vip_cost=int(token_setting("tokens_for_vip_days","100") or 100)
    vip_days=int(token_setting("vip_days_per_token_pack","30") or 30)
    c=db(); top=c.execute("SELECT COUNT(*) n FROM token_wallets").fetchone()["n"]; total=c.execute("SELECT COALESCE(SUM(balance),0) n FROM token_wallets").fetchone()["n"]; c.close()
    return (f"🎟️ <b>مدیریت توکن و سهمیه</b>\n\n👥 کیف‌پول‌های فعال: <b>{top}</b>\n⭐ مجموع توکن‌های موجود: <b>{total}</b>\n\n🤝 هر دعوت موفق: <b>{ref} توکن</b>\n⭐ هر <b>{xp_per} XP</b>: 1 توکن\n💎 <b>{vip_days} روز VIP</b>: {vip_cost} توکن\n\nهر قابلیت را می‌توانی جداگانه روی رایگان نامحدود، سهمیه‌دار یا VIP قرار بدهی.")

def token_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤝 توکن دعوت",callback_data="v25:token_ref"),InlineKeyboardButton("⭐ XP → توکن",callback_data="v25:token_xp")],
        [InlineKeyboardButton("💎 توکن → VIP",callback_data="v25:token_vip")],
        [InlineKeyboardButton("⚙️ سهمیه قابلیت‌ها",callback_data="v25:token_rules")],
        [InlineKeyboardButton("📊 کیف‌پول کاربران",callback_data="v25:token_users")],
        [InlineKeyboardButton("⬅️ پنل مدیریت",callback_data="adm:stats")]
    ])

def token_rules_keyboard():
    rows=[]
    for key,label in TOKEN_FEATURES:
        r=token_rule(key)
        if r:
            limit="∞" if int(r["free_limit"])<0 else str(r["free_limit"])
            cost=int(r["token_cost"])
            txt=f"{label} | {limit} | 🎟️{cost}"
        else: txt=f"{label} | ∞ | 🎟️0"
        rows.append([InlineKeyboardButton(txt,callback_data=f"v25:token_rule:{key}")])
    rows.append([InlineKeyboardButton("⬅️ توکن و سهمیه",callback_data="v25:tokens_admin")])
    return InlineKeyboardMarkup(rows)

def token_rule_keyboard(key):
    r=token_rule(key); fa=True
    limit=int(r["free_limit"]) if r else -1; cost=int(r["token_cost"]) if r else 0
    period=r["period"] if r else "lifetime"
    after=r["after_limit"] if r else "vip"
    def lim(n): return "🟢 ∞ رایگان" if n<0 else f"🔢 {n} بار"
    def mark(n): return "✅" if limit==n else ""
    def cmk(n): return "✅" if cost==n else ""
    rows=[
        [InlineKeyboardButton(f"{mark(-1)} ∞ رایگان",callback_data=f"v25:token_setlimit:{key}:-1")],
        [InlineKeyboardButton(f"{mark(1)} 1",callback_data=f"v25:token_setlimit:{key}:1"),InlineKeyboardButton(f"{mark(3)} 3",callback_data=f"v25:token_setlimit:{key}:3"),InlineKeyboardButton(f"{mark(5)} 5",callback_data=f"v25:token_setlimit:{key}:5")],
        [InlineKeyboardButton(f"{mark(10)} 10",callback_data=f"v25:token_setlimit:{key}:10"),InlineKeyboardButton(f"{mark(20)} 20",callback_data=f"v25:token_setlimit:{key}:20"),InlineKeyboardButton(f"{mark(50)} 50",callback_data=f"v25:token_setlimit:{key}:50")],
        [InlineKeyboardButton(f"{cmk(0)} 🎟️0",callback_data=f"v25:token_setcost:{key}:0"),InlineKeyboardButton(f"{cmk(1)} 🎟️1",callback_data=f"v25:token_setcost:{key}:1"),InlineKeyboardButton(f"{cmk(5)} 🎟️5",callback_data=f"v25:token_setcost:{key}:5")],
        [InlineKeyboardButton(f"{cmk(10)} 🎟️10",callback_data=f"v25:token_setcost:{key}:10"),InlineKeyboardButton(f"{cmk(20)} 🎟️20",callback_data=f"v25:token_setcost:{key}:20"),InlineKeyboardButton(f"{cmk(50)} 🎟️50",callback_data=f"v25:token_setcost:{key}:50")],
        [InlineKeyboardButton("📅 روزانه",callback_data=f"v25:token_period:{key}:daily"),InlineKeyboardButton("📅 هفتگی",callback_data=f"v25:token_period:{key}:weekly"),InlineKeyboardButton("📅 ماهانه",callback_data=f"v25:token_period:{key}:monthly")],
        [InlineKeyboardButton("♾️ هرگز بازنشانی نشود",callback_data=f"v25:token_period:{key}:lifetime")],
        [InlineKeyboardButton("💎 بعد از سهمیه → VIP",callback_data=f"v25:token_after:{key}:vip")],
        [InlineKeyboardButton("🟢 فعال",callback_data=f"v25:token_enable:{key}:1"),InlineKeyboardButton("🔴 غیرفعال",callback_data=f"v25:token_enable:{key}:0")],
        [InlineKeyboardButton("⬅️ سهمیه قابلیت‌ها",callback_data="v25:token_rules")]
    ]
    return InlineKeyboardMarkup(rows)

async def token_admin_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔",show_alert=True); return
    await q.answer(); p=q.data.split(":")
    action=p[1] if len(p)>1 else "tokens_admin"
    if action=="tokens_admin": await q.message.edit_text(token_admin_text(),parse_mode="HTML",reply_markup=token_admin_keyboard()); return
    if action=="token_ref":
        cur=int(token_setting("referral_tokens_per_success","10") or 10); nxt={1:10,10:20,20:50,50:100,100:1}.get(cur,10); set_token_setting("referral_tokens_per_success",nxt,uid); await q.message.edit_text(token_admin_text(),parse_mode="HTML",reply_markup=token_admin_keyboard()); return
    if action=="token_xp":
        cur=int(token_setting("xp_per_token","100") or 100); nxt={10:50,50:100,100:250,250:500,500:10}.get(cur,100); set_token_setting("xp_per_token",nxt,uid); await q.message.edit_text(token_admin_text(),parse_mode="HTML",reply_markup=token_admin_keyboard()); return
    if action=="token_vip":
        cur=int(token_setting("tokens_for_vip_days","100") or 100); nxt={50:100,100:250,250:500,500:1000,1000:50}.get(cur,100); set_token_setting("tokens_for_vip_days",nxt,uid); await q.message.edit_text(token_admin_text(),parse_mode="HTML",reply_markup=token_admin_keyboard()); return
    if action=="token_rules": await q.message.edit_text("⚙️ <b>سهمیه قابلیت‌ها</b>\n\n∞ یعنی فعلاً کاملاً رایگان و نامحدود. وقتی سهمیه تعیین کنی، بعد از اتمام آن قابلیت می‌تواند با توکن یا VIP ادامه پیدا کند.",parse_mode="HTML",reply_markup=token_rules_keyboard()); return
    if action=="token_users":
        c=db(); rows=c.execute("SELECT user_id,balance FROM token_wallets ORDER BY balance DESC LIMIT 50").fetchall(); c.close(); txt="📊 <b>کیف‌پول کاربران</b>\n\n"+"\n".join(f"👤 <code>{r['user_id']}</code> — 🎟️ {r['balance']}" for r in rows) if rows else "📊 کیف‌پولی ثبت نشده."; await q.message.edit_text(txt,parse_mode="HTML",reply_markup=token_admin_keyboard()); return
    if action=="token_rule":
        key=p[2]; label=dict(TOKEN_FEATURES).get(key,key); await q.message.edit_text(f"⚙️ <b>{html.escape(label)}</b>\n\nدکمه‌های زیر را بزن تا سهمیه و هزینه توکن تنظیم شود.",parse_mode="HTML",reply_markup=token_rule_keyboard(key)); return
    if action=="token_setlimit":
        key=p[2]; limit=int(p[3]); r=token_rule(key); set_token_rule(key,limit,r["period"] if r else "lifetime",int(r["token_cost"]) if r else 0,r["after_limit"] if r else "vip",int(r["enabled"]) if r else 1,uid); await q.message.edit_text("✅ سهمیه تغییر کرد.",reply_markup=token_rule_keyboard(key)); return
    if action=="token_setcost":
        key=p[2]; cost=int(p[3]); r=token_rule(key); set_token_rule(key,int(r["free_limit"]) if r else -1,r["period"] if r else "lifetime",cost,r["after_limit"] if r else "vip",int(r["enabled"]) if r else 1,uid); await q.message.edit_text("✅ هزینه توکن تغییر کرد.",reply_markup=token_rule_keyboard(key)); return
    if action=="token_period":
        key=p[2]; period=p[3]; r=token_rule(key); set_token_rule(key,int(r["free_limit"]) if r else -1,period,int(r["token_cost"]) if r else 0,r["after_limit"] if r else "vip",int(r["enabled"]) if r else 1,uid); await q.message.edit_text("✅ دوره سهمیه تغییر کرد.",reply_markup=token_rule_keyboard(key)); return
    if action=="token_after":
        key=p[2]; after=p[3]; r=token_rule(key); set_token_rule(key,int(r["free_limit"]) if r else -1,r["period"] if r else "lifetime",int(r["token_cost"]) if r else 0,after,int(r["enabled"]) if r else 1,uid); await q.message.edit_text("✅ رفتار بعد از پایان سهمیه تنظیم شد.",reply_markup=token_rule_keyboard(key)); return
    if action=="token_enable":
        key=p[2]; enabled=int(p[3]); r=token_rule(key); set_token_rule(key,int(r["free_limit"]) if r else -1,r["period"] if r else "lifetime",int(r["token_cost"]) if r else 0,r["after_limit"] if r else "vip",enabled,uid); await q.message.edit_text("✅ وضعیت سهمیه تغییر کرد.",reply_markup=token_rule_keyboard(key)); return

def token_init_db():
    c=db(); now=token_now()
    c.execute("CREATE TABLE IF NOT EXISTS token_wallets(user_id INTEGER PRIMARY KEY,balance INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL)")
    c.execute("CREATE TABLE IF NOT EXISTS token_ledger(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,delta INTEGER NOT NULL,reason TEXT,ref_key TEXT UNIQUE,created_at TEXT NOT NULL)")
    c.execute("CREATE TABLE IF NOT EXISTS token_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL)")
    c.execute("CREATE TABLE IF NOT EXISTS token_rules(feature_key TEXT PRIMARY KEY,free_limit INTEGER NOT NULL DEFAULT -1,period TEXT NOT NULL DEFAULT 'lifetime',token_cost INTEGER NOT NULL DEFAULT 0,after_limit TEXT NOT NULL DEFAULT 'vip',enabled INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL)")
    c.execute("CREATE TABLE IF NOT EXISTS feature_usage(user_id INTEGER NOT NULL,feature_key TEXT NOT NULL,period_key TEXT NOT NULL,used_count INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL,PRIMARY KEY(user_id,feature_key,period_key))")
    for k,v in [("referral_tokens_per_success","10"),("xp_per_token","100"),("tokens_for_vip_days","100"),("vip_days_per_token_pack","30")]: c.execute("INSERT OR IGNORE INTO token_settings(key,value,updated_at) VALUES(?,?,?)",(k,v,now))
    for k,_ in TOKEN_FEATURES: c.execute("INSERT OR IGNORE INTO token_rules(feature_key,free_limit,period,token_cost,after_limit,enabled,updated_at) VALUES(?,?,?,?,?,?,?)",(k,-1,"lifetime",0,"vip",1,now))
    c.commit(); c.close()

# Seed every registered user with a wallet and migrate referrals -> tokens only once.
def token_backfill_existing_users():
    c=db()
    try:
        now=token_now()
        users=c.execute("SELECT user_id FROM users").fetchall()
        c.executemany("INSERT OR IGNORE INTO token_wallets(user_id,balance,updated_at) VALUES(?,?,?)",[(int(r["user_id"]),0,now) for r in users])
        c.commit()
    finally:
        c.close()

def token_referral_reward(inviter_id, invited_id):
    amount=int(token_setting("referral_tokens_per_success","10") or 10)
    return add_tokens(int(inviter_id),amount,"successful_referral",f"referral:{inviter_id}:{invited_id}")

def tokens_from_xp(uid):
    per=int(token_setting("xp_per_token","100") or 100)
    if per<=0: return 0
    c=db(); r=c.execute("SELECT COALESCE(SUM(amount),0) n FROM xp_log WHERE user_id=?",(int(uid),)).fetchone(); c.close(); total=int(r["n"] or 0)
    already=token_setting(f"xp_converted:{uid}","0")
    converted=int(already or 0); available=max(0,(total//per)-converted)
    if available>0:
        if add_tokens(uid,available,"xp_to_tokens",f"xpconvert:{uid}:{total//per}"):
            set_token_setting(f"xp_converted:{uid}",converted+available)
    return available

def redeem_tokens_for_vip(uid):
    cost=int(token_setting("tokens_for_vip_days","100") or 100); days=int(token_setting("vip_days_per_token_pack","30") or 30)
    if spend_tokens(uid,cost,"tokens_to_vip",f"vip_token:{uid}:{datetime.now(TZ).strftime('%Y%m%d%H%M')}" ):
        base=datetime.now(TZ); c=db(); r=c.execute("SELECT vip_until FROM users WHERE user_id=?",(int(uid),)).fetchone()
        if r and r["vip_until"]:
            try: base=max(base,datetime.fromisoformat(r["vip_until"]))
            except Exception: pass
        expires=base+timedelta(days=days); c.execute("UPDATE users SET vip_until=? WHERE user_id=?",(expires.isoformat(),int(uid))); c.execute("INSERT INTO subscription_history(user_id,plan,duration_days,source,amount,started_at,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?)",(int(uid),"VIP",days,"tokens",0,token_now(),expires.isoformat(),token_now())); c.commit(); c.close(); return True,days
    return False,0

# Add a token button to the unified user keyboard without disturbing legacy rows.
_OLD_KEYBOARD_TOKEN=keyboard
def keyboard(uid):
    kb=_OLD_KEYBOARD_TOKEN(uid); rows=[list(r) for r in kb.keyboard]
    fa=lang(uid)=="fa"
    label="🎟️ توکن‌های من" if fa else "🎟️ My Tokens"
    if not any(label in x for r in rows for x in r): rows.append([label])
    return ReplyKeyboardMarkup(rows,resize_keyboard=True)

# Re-route token text and token callbacks through the already registered v25 dispatcher.
_OLD_V25_CALLBACK_TOKEN=v25_callback
async def v25_callback(update,context):
    data=update.callback_query.data
    if data in ("v25:tokens","v25:token_wallet"):
        uid=update.effective_user.id; await update.callback_query.answer(); await update.callback_query.message.edit_text(token_user_text(uid),parse_mode="HTML",reply_markup=token_user_keyboard(uid)); return
    if data in ("v25:tokens_admin","v25:token_ref","v25:token_xp","v25:token_vip","v25:token_rules","v25:token_users") or data.startswith("v25:token_rule:") or data.startswith("v25:token_setlimit:") or data.startswith("v25:token_setcost:") or data.startswith("v25:token_period:") or data.startswith("v25:token_after:") or data.startswith("v25:token_enable:"):
        return await token_admin_callback(update,context)
    return await _OLD_V25_CALLBACK_TOKEN(update,context)

# Add token entry in the central admin panel while preserving all previous buttons.
_OLD_ADMIN_KEYBOARD_TOKEN=final_admin_keyboard
def final_admin_keyboard():
    base=_OLD_ADMIN_KEYBOARD_TOKEN().inline_keyboard
    rows=[list(r) for r in base]
    rows.insert(-1,[InlineKeyboardButton("🎟️ مدیریت توکن و سهمیه",callback_data="v25:tokens_admin")])
    return InlineKeyboardMarkup(rows)
admin_keyboard=final_admin_keyboard

# Add token wallet text routing and minimal XP->token / token->VIP actions.
_OLD_TEXT_ROUTER_TOKEN=text_router
async def text_router(update,context):
    uid=update.effective_user.id; txt=(update.message.text or '').strip()
    if txt in ('🎟️ توکن‌های من','🎟️ My Tokens'):
        tokens_from_xp(uid)
        await update.message.reply_text(token_user_text(uid),parse_mode='HTML',reply_markup=token_user_keyboard(uid)); return
    return await _OLD_TEXT_ROUTER_TOKEN(update,context)

# Ensure token tables exist at every startup, without dropping or rewriting existing data.
_OLD_INIT_DB_TOKEN=init_db
def init_db():
    _OLD_INIT_DB_TOKEN(); token_init_db(); token_backfill_existing_users()


# Token access inside VIP center.
_OLD_VIP_KEYBOARD_TOKEN=vip_keyboard
def vip_keyboard(uid):
    base=_OLD_VIP_KEYBOARD_TOKEN(uid).inline_keyboard
    rows=[list(r) for r in base]
    fa=lang(uid)=="fa"
    rows.insert(-1,[InlineKeyboardButton("🎟️ تبدیل توکن به VIP" if fa else "🎟️ Convert Tokens to VIP",callback_data="vip:tokens")])
    return InlineKeyboardMarkup(rows)

_OLD_VIP_CALLBACK_TOKEN=vip_callback
async def vip_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if q.data == "vip:tokens":
        await q.answer()
        tokens_from_xp(uid)
        ok,days=redeem_tokens_for_vip(uid)
        if ok:
            await q.message.edit_text(f"✅ {days} روز VIP با توکن فعال شد.\n\n🎟️ موجودی باقیمانده: {token_balance(uid)}",reply_markup=vip_keyboard(uid))
        else:
            cost=int(token_setting("tokens_for_vip_days","100") or 100)
            await q.message.edit_text(f"🎟️ توکن کافی نیست.\n\nموجودی: {token_balance(uid)}\nنیاز: {cost} توکن",reply_markup=vip_keyboard(uid))
        return
    return await _OLD_VIP_CALLBACK_TOKEN(update,context)

# Token wallet also exposes XP conversion and VIP redemption directly.
_OLD_TOKEN_USER_KEYBOARD=token_user_keyboard
def token_user_keyboard(uid):
    fa=lang(uid)=="fa"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 XP → توکن" if fa else "🔄 XP → Tokens",callback_data="v25:token_xp_convert")],
        [InlineKeyboardButton("💎 تبدیل توکن به VIP" if fa else "💎 Convert Tokens to VIP",callback_data="vip:tokens")],
        [InlineKeyboardButton("🔄 بروزرسانی" if fa else "🔄 Refresh",callback_data="v25:tokens")],
        [InlineKeyboardButton("⬅️ بازگشت" if fa else "⬅️ Back",callback_data="v25:hub"),main_menu_button(uid)]
    ])

# Extend the token callback router with XP conversion.
_OLD_V25_CALLBACK_TOKEN2=v25_callback
async def v25_callback(update,context):
    data=update.callback_query.data
    if data == "v25:token_xp_convert":
        uid=update.effective_user.id; await update.callback_query.answer(); n=tokens_from_xp(uid)
        await update.callback_query.message.edit_text((f"✅ {n} توکن جدید از XP تبدیل شد.\n\n⭐ موجودی XP به توکن فعلی ثبت شد.\n🎟️ موجودی: {token_balance(uid)}" if lang(uid)=="fa" else f"✅ Converted {n} new tokens from XP.\n\n🎟️ Balance: {token_balance(uid)}"),reply_markup=token_user_keyboard(uid)); return
    return await _OLD_V25_CALLBACK_TOKEN2(update,context)



# ===================== CALLBACK REPLY-KEYBOARD SAFETY =====================
# Telegram edit_message_text accepts InlineKeyboardMarkup, not ReplyKeyboardMarkup.
# Goal creation and other callback flows must edit the old message first, then send
# the ReplyKeyboard as a new message. This prevents silent failures after a button tap.

def _reply_keyboard_is_safe_for_message_send(markup):
    return isinstance(markup, ReplyKeyboardMarkup)

# ===================== FINAL NAVIGATION / ONBOARDING REPAIR =====================
# This compatibility layer is intentionally last so it wins over older wrappers.
# It does not touch persistent data or database schemas.

# Onboarding choices must be selectable before channel/subscription gating.
# The original callbacks were decorated with subscription_required, which could
# reject the very buttons needed to finish /start onboarding.
try:
    if hasattr(language_callback, "__wrapped__"):
        language_callback = language_callback.__wrapped__
    if hasattr(gender_callback, "__wrapped__"):
        gender_callback = gender_callback.__wrapped__
    if hasattr(onboarding_business_callback, "__wrapped__"):
        onboarding_business_callback = onboarding_business_callback.__wrapped__
except Exception:
    logger.exception("Failed to repair onboarding callback wrappers")


def keyboard(uid):
    """Stable, deterministic two-column main menu; preserves legacy + V25 buttons."""
    fa = lang(uid) == "fa"
    try:
        base = filter_menu_rows(uid, [list(row) for row in T["fa" if fa else "en"]["menu"]])
    except Exception:
        base = []

    # Keep the original menu order, then add enhanced modules in a predictable grid.
    extras = []
    extra_defs = [
        ("unified_hub", "🧠 مرکز من", "🧠 My Center"),
        ("portfolio", "💰 سرمایه‌های من", "💰 My Portfolio"),
        ("installments", "💳 اقساط", "💳 Installments"),
        ("profile_sharing", "👤 اطلاعات من", "👤 My Profile"),
        ("voice", "🎙️ دستیار صوتی", "🎙️ Voice Assistant"),
        ("calendar_hub", "📅 تقویم من", "📅 My Calendar"),
    ]
    for key, fa_label, en_label in extra_defs:
        try:
            if v25_allowed(uid, key):
                extras.append(fa_label if fa else en_label)
        except Exception:
            # If an optional feature check fails, do not break the main menu.
            logger.exception("Menu feature check failed: %s", key)

    # Token wallet is available independently of the optional V25 feature flags.
    extras.append("🎟️ توکن‌های من" if fa else "🎟️ My Tokens")

    rows = [list(r) for r in base if r]
    for i in range(0, len(extras), 2):
        rows.append(extras[i:i + 2])

    if admin_is_allowed(uid):
        rows.append(["📢 مدیریت کانال" if fa else "📢 Channel Management",
                     "🛡 پنل مدیریت" if fa else "🛡 Admin Panel"])

    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)


async def text_router(update, context):
    """Single final text dispatcher. Main-menu buttons always win over flow states."""
    if not update.message or not update.message.text:
        return
    uid = update.effective_user.id
    text = update.message.text.strip()
    # Keep the legacy variable name used by older branches in this dispatcher.
    txt = text

    # Universal navigation has absolute priority.
    # These are real ReplyKeyboard buttons, so this routing must live in the
    # handler that is registered by main(); later wrapper definitions are too late.
    if text in ("🏠 منوی اصلی", "🏠 Main Menu"):
        clear_flow(context)
        try:
            await update.message.delete()
        except Exception:
            pass
        fa = lang(uid) == "fa"
        await context.bot.send_message(
            chat_id=uid,
            text="🏠 <b>منوی اصلی</b>\n\nیک بخش را انتخاب کن." if fa else "🏠 <b>Main Menu</b>\n\nChoose a section.",
            parse_mode="HTML",
            reply_markup=_compact_root_inline(uid),
        )
        return
    if text in ("⬅️ برگشت", "⬅️ Back"):
        clear_flow(context)
        try:
            await update.message.delete()
        except Exception:
            pass
        # From an error/input recovery keyboard, Back returns to the compact
        # Goals section—the section that owns the "🎯 برنامه من" entry.
        await context.bot.send_message(
            chat_id=uid,
            text="🎯 <b>برنامه و اهداف</b>" if lang(uid)=="fa" else "🎯 <b>Goals & Plan</b>",
            parse_mode="HTML",
            reply_markup=_compact_menu_keyboard(uid, "goals"),
        )
        return

    # "🎯 برنامه من" is a top-level ReplyKeyboard action. Handle it here before
    # the legacy input state machine; otherwise the old chain can raise TypeError.
    if text in ("🎯 برنامه من", "🎯 My Plan"):
        clear_flow(context)
        await _compact_menu_show(update, context, "goals")
        return

    if txt in ("👤 استفاده از ربات", "👤 Use Bot"):
        clear_flow(context)
        await update.message.reply_text(
            "👤 <b>استفاده از ربات</b>\n\nقابلیت‌های عادی ربات در دسترس تو هستند." if lang(uid)=="fa" else
            "👤 <b>Use Bot</b>\n\nAll normal bot features are available here.",
            parse_mode="HTML", reply_markup=_compact_user_keyboard(uid)
        )
        return
    if txt in ("🛡 مدیریت ربات", "🛡 Bot Management"):
        if not admin_guard(uid):
            await update.message.reply_text("⛔ دسترسی ندارید.", reply_markup=keyboard(uid))
            return
        clear_flow(context)
        await _show_admin_management(update, context)
        return
    if txt in ("📊 داشبورد و گزارش", "📊 Dashboard & Reports"):
        await admin_command(update, context)
        return
    if txt in ("👥 کاربران و نقش‌ها", "👥 Users & Roles", "🎫 تیکت‌ها و Incident", "🎫 Tickets & Incidents",
                "💰 مالی و پرداخت", "💰 Finance & Payments", "💎 VIP / XP / Token",
                "📢 کانال و انتشار", "📢 Channels & Publishing", "🩺 سلامت و Diagnostics",
                "🩺 Health & Diagnostics", "💾 Backup و Recovery", "💾 Backup & Recovery",
                "🧩 قابلیت‌ها و Feature Flags", "🧩 Features & Flags", "🔐 امنیت و Audit",
                "🔐 Security & Audit", "🧪 مرکز تست و Regression", "🧪 Test & Regression",
                "⚙️ تنظیمات سیستم", "⚙️ System Settings", "📦 سایر ماژول‌های مدیریتی",
                "📦 Other Admin Modules"):
        await admin_command(update, context)
        return

    # V25 modules must also have priority over transient legacy input states.
    v25_routes = {
        "🧠 مرکز من": v25_hub,
        "🧠 My Center": v25_hub,
        "💰 سرمایه‌های من": v25_portfolio_menu,
        "💰 My Portfolio": v25_portfolio_menu,
        "💳 اقساط": v25_installments_menu,
        "💳 Installments": v25_installments_menu,
        "👤 اطلاعات من": v25_profile_menu,
        "👤 My Profile": v25_profile_menu,
    }
    if text in v25_routes:
        clear_flow(context)
        await v25_routes[text](update, context)
        return

    if text in ("🎙️ دستیار صوتی", "🎙️ Voice Assistant"):
        clear_flow(context)
        context.user_data["v25_voice_mode"] = True
        await update.message.reply_text("🎙️ ویس را بفرست. / Send a voice message.")
        return

    if text in ("🎟️ توکن‌های من", "🎟️ My Tokens"):
        clear_flow(context)
        tokens_from_xp(uid)
        await update.message.reply_text(token_user_text(uid), parse_mode="HTML", reply_markup=token_user_keyboard(uid))
        return

    # Every normal legacy button is handled here before any text-input flow.
    legacy_routes = {
        "🎯 اهداف امروز": today, "🎯 Today's Goals": today,
        "✏️ هدف خودم می‌نویسم": custom_goal_start, "✏️ Write my own goal": custom_goal_start,
        "🏆 اهداف آماده": ready_menu, "🏆 Ready Goals": ready_menu,
        "✏️ ویرایش اهداف": edit_menu, "✏️ Edit Goals": edit_menu,
        "📅 جدول هفتگی": weekly, "📅 Weekly Table": weekly,
        "📊 آمار من": stats, "📊 My Stats": stats,
        "👤 پروفایل": profile, "👤 Profile": profile,
        "🏆 دستاوردها": achievements, "🏆 Achievements": achievements,
        "🤝 دعوت دوستان": referral, "🤝 Referrals": referral,
        "📈 قیمت آنلاین": prices, "📈 Online Prices": prices,
        "🤖 چت با AI": ai_chat_start, "🤖 AI Chat": ai_chat_start,
        "💎 VIP": vip_center,
        "🎫 پشتیبانی": support_start, "🎫 Support": support_start,
        "⚙️ تنظیمات": settings, "⚙️ Settings": settings,
        "👥 مدیریت مشتری و نوبت‌دهی": customer_panel, "👥 Customer & Appointments": customer_panel,
    }
    if text in legacy_routes:
        requested = FEATURE_MENU_MAP.get(text)
        if requested and not user_feature_allowed(uid, requested):
            await update.message.reply_text("⛔ این قابلیت فعلاً توسط مدیر غیرفعال شده است.", reply_markup=keyboard(uid))
            return
        clear_flow(context)
        await legacy_routes[text](update, context)
        return

    if text == "⭐ XP":
        clear_flow(context)
        await xp_command(update, context)
        return

    if text in ("📢 مدیریت کانال", "📢 Channel Management"):
        clear_flow(context)
        if admin_guard(uid):
            await update.message.reply_text(
                "📢 <b>مدیریت کانال و پست‌گذاری</b>\n\nاتصال کانال، ساخت پست، زمان‌بندی و انتشار خودکار.",
                parse_mode="HTML", reply_markup=channel_keyboard())
            await hide_main_reply_keyboard(update)
        else:
            await update.message.reply_text("⛔ دسترسی ندارید.")
        return

    if text in ("🛡 پنل مدیریت", "🛡 Admin Panel"):
        clear_flow(context)
        await admin_command(update, context)
        return

    # Not a menu button: preserve the complete legacy/V25 input state machine.
    return await _OLD_TEXT_ROUTER_TOKEN(update, context)


# Ensure the application registers the repaired definitions above.
admin_keyboard = final_admin_keyboard


class _FallbackJob:
    def __init__(self, data=None, name=""):
        self.data=data
        self.name=name

class _FallbackJobQueue:
    """Small asyncio fallback when python-telegram-bot was installed without APScheduler.
    It preserves the bot's existing run_once/run_repeating API instead of silently disabling jobs.
    """
    def __init__(self, application):
        self.application=application
        self._tasks=set()

    def _context(self, job):
        return type("FallbackJobContext", (), {
            "bot": self.application.bot,
            "application": self.application,
            "job": job,
            "job_queue": self,
        })()

    def _track(self, task):
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def run_once(self, callback, when, data=None, name=""):
        delay=float(when.total_seconds()) if isinstance(when,timedelta) else float(when)
        job=_FallbackJob(data=data,name=name)
        async def runner():
            await asyncio.sleep(max(0.0,delay))
            try:
                await callback(self._context(job))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Fallback JobQueue one-shot job failed: %s",name)
        return self._track(asyncio.get_event_loop().create_task(runner()))

    def run_repeating(self, callback, interval, first=None, data=None, name=""):
        delay=float(first.total_seconds()) if isinstance(first,timedelta) else float(first if first is not None else interval)
        period=float(interval.total_seconds()) if isinstance(interval,timedelta) else float(interval)
        period=max(1.0,period)
        job=_FallbackJob(data=data,name=name)
        async def runner():
            await asyncio.sleep(max(0.0,delay))
            while True:
                try:
                    await callback(self._context(job))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Fallback JobQueue repeating job failed: %s",name)
                await asyncio.sleep(period)
        return self._track(asyncio.get_event_loop().create_task(runner()))

    def stop(self):
        for task in tuple(self._tasks):
            task.cancel()
        self._tasks.clear()

def main():
    if not BOT_TOKEN:
        raise RuntimeError("Set BOT_TOKEN in your environment variables.")

    init_db()
    # Optional deployment control: set MAINTENANCE_MODE=0/1 explicitly to control the global lock.
    # If unset, the existing admin/database setting is preserved.
    maintenance_env = os.environ.get("MAINTENANCE_MODE", "").strip()
    if maintenance_env == "0":
        set_feature("maintenance", False)
    elif maintenance_env == "1":
        set_feature("maintenance", True)
    # Safe defaults for automatic channel publishing.
    if not get_auto_setting("interval_minutes", ""):
        set_auto_setting("interval_minutes", "60")
    if not get_auto_setting("category", ""):
        set_auto_setting("category", "random")
    if not get_auto_setting("subcategory", ""):
        set_auto_setting("subcategory", "random")
    # Keep auto-post history permanently so duplicate prevention remains global.
    c=db()
    c.execute("DELETE FROM delivery_log WHERE created_at < ?",((datetime.now(TZ)-timedelta(days=180)).isoformat(),))
    c.commit()
    c.close()

    app = Application.builder().token(BOT_TOKEN).build()
    # If the deployment lacks APScheduler, keep scheduled features alive through asyncio
    # instead of silently skipping every reminder/report/health-check job.
    if app.job_queue is None:
        app._job_queue = _FallbackJobQueue(app)
        logger.warning("python-telegram-bot JobQueue unavailable; using asyncio fallback scheduler.")
    else:
        logger.info("python-telegram-bot JobQueue is active.")

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", my_id))
    app.add_handler(CommandHandler("admin", admin_command))

    app.add_handler(CallbackQueryHandler(subscription_check_callback, pattern=r"^subcheck$"))
    app.add_handler(CallbackQueryHandler(customer_panel_callback, pattern=r"^cust:"))
    app.add_handler(CallbackQueryHandler(admin_user_detail_callback, pattern=r"^admu:\d+$"))
    app.add_handler(CallbackQueryHandler(admin_user_action_callback, pattern=r"^admu_(block|vip|unlimited|editvip):"))
    app.add_handler(CallbackQueryHandler(feature_category_callback, pattern=r"^fcat:"))
    app.add_handler(CallbackQueryHandler(navigation_callback, pattern=r"^nav:"))
    app.add_handler(CallbackQueryHandler(ai_chat_navigation_callback, pattern=r"^aichat:"))
    app.add_handler(CallbackQueryHandler(admin_panel_callback, pattern=r"^adm:"))
    app.add_handler(CallbackQueryHandler(smart_post_callback, pattern=r"^chgen:"))
    app.add_handler(CallbackQueryHandler(channel_panel_callback, pattern=r"^ch:"))
    app.add_handler(CallbackQueryHandler(auto_channel_callback, pattern=r"^auto:"))
    app.add_handler(CallbackQueryHandler(auto_category_callback, pattern=r"^autocat:"))
    app.add_handler(CallbackQueryHandler(auto_subcategory_callback, pattern=r"^autosub:"))
    app.add_handler(CallbackQueryHandler(auto_interval_callback, pattern=r"^autoint:"))
    app.add_handler(CallbackQueryHandler(approval_callback, pattern=r"^appr:"))
    app.add_handler(CallbackQueryHandler(approval_reject_callback, pattern=r"^apprrej:"))
    app.add_handler(PollAnswerHandler(channel_poll_answer_handler))
    if MessageReactionHandler is not None:
        app.add_handler(MessageReactionHandler(channel_reaction_handler))
    app.add_handler(CallbackQueryHandler(feedback_callback, pattern=r"^feedback:"))
    app.add_handler(CallbackQueryHandler(channel_schedule_callback, pattern=r"^chs:"))
    app.add_handler(CallbackQueryHandler(channel_daily_callback, pattern=r"^chd:"))
    app.add_handler(CallbackQueryHandler(channel_weekday_callback, pattern=r"^chw:"))
    app.add_handler(CallbackQueryHandler(channel_weektime_callback, pattern=r"^chwtime:"))
    app.add_handler(CallbackQueryHandler(language_callback, pattern=r"^language:"))
    app.add_handler(CallbackQueryHandler(settings_language_callback, pattern=r"^setlang:"))
    app.add_handler(CallbackQueryHandler(goals_navigation_callback, pattern=r"^goals:"))
    app.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^settings:"))
    app.add_handler(CallbackQueryHandler(price_callback, pattern=r"^price:"))
    app.add_handler(CallbackQueryHandler(onboarding_business_callback, pattern=r"^onboardtype:"))
    app.add_handler(CallbackQueryHandler(onboarding_feature_callback, pattern=r"^pref:"))
    app.add_handler(CallbackQueryHandler(gender_callback, pattern=r"^gender:"))
    app.add_handler(CallbackQueryHandler(priority_callback, pattern=r"^priority:"))
    app.add_handler(CallbackQueryHandler(duration_callback, pattern=r"^duration:"))
    app.add_handler(CallbackQueryHandler(snooze_menu, pattern=r"^snooze_menu:"))
    app.add_handler(CallbackQueryHandler(goal_reminder_callback, pattern=r"^goalrem:"))
    app.add_handler(CallbackQueryHandler(snooze_callback, pattern=r"^snooze:"))
    app.add_handler(CallbackQueryHandler(steps_menu, pattern=r"^steps:"))
    app.add_handler(CallbackQueryHandler(step_add_start, pattern=r"^step_add:"))
    app.add_handler(CallbackQueryHandler(step_toggle, pattern=r"^step_toggle:"))
    app.add_handler(CallbackQueryHandler(ready_subcategory_callback, pattern=r"^readysub:"))
    app.add_handler(CallbackQueryHandler(goal_reminders_list, pattern=r"^goalreminders$"))
    app.add_handler(CallbackQueryHandler(goal_calendar_callback, pattern=r"^goalcalendar:"))
    app.add_handler(CallbackQueryHandler(goal_calendar_day, pattern=r"^goalcalday:"))
    app.add_handler(CallbackQueryHandler(my_goals_callback, pattern=r"^cm:my_goals$"))
    app.add_handler(CallbackQueryHandler(new_category, pattern=r"^newcat:"))
    app.add_handler(CallbackQueryHandler(new_back, pattern=r"^newback$"))
    app.add_handler(CallbackQueryHandler(new_goal_pick, pattern=r"^newgoal:"))
    app.add_handler(CallbackQueryHandler(time_callback, pattern=r"^time:"))
    app.add_handler(CallbackQueryHandler(edit_time_callback, pattern=r"^edit_time:"))
    app.add_handler(CallbackQueryHandler(detail, pattern=r"^detail:"))
    app.add_handler(CallbackQueryHandler(mark, pattern=r"^(done|miss):"))
    app.add_handler(CallbackQueryHandler(edit_goal, pattern=r"^edit:"))
    app.add_handler(CallbackQueryHandler(rename_start, pattern=r"^rename:"))
    app.add_handler(CallbackQueryHandler(change_reminder, pattern=r"^changereminder:"))
    app.add_handler(CallbackQueryHandler(delete_start, pattern=r"^delete:"))
    app.add_handler(CallbackQueryHandler(delete_confirm, pattern=r"^delete_yes:"))
    app.add_handler(CallbackQueryHandler(delete_no, pattern=r"^delete_no$"))
    app.add_handler(CallbackQueryHandler(admin_broadcast_start, pattern=r"^admin:broadcast$"))

    app.add_handler(CommandHandler("xp", xp_command))
    app.add_handler(CommandHandler("referral", referral))
    app.add_handler(CommandHandler("prices", prices))
    app.add_handler(CommandHandler("support", support_start))
    app.add_handler(CallbackQueryHandler(support_callback, pattern=r"^support:"))
    app.add_handler(CallbackQueryHandler(vip_callback, pattern=r"^vip:"))
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    app.add_handler(CallbackQueryHandler(final_feature_callback, pattern=r"^feat:"))
    app.add_handler(CallbackQueryHandler(feature_info_callback, pattern=r"^featinfo:"))
    app.add_handler(CallbackQueryHandler(compact_section_callback, pattern=r"^menu:"))
    app.add_handler(CallbackQueryHandler(compact_menu_callback, pattern=r"^cm:"))
    app.add_handler(CallbackQueryHandler(v25_callback, pattern=r"^v25:"))
    # Recurring goal-duration buttons use the separate goalrepeat: callback namespace.
    # Register it explicitly; otherwise Telegram sends the callback but no handler receives it.
    app.add_handler(CallbackQueryHandler(targeted_goalrepeat_callback, pattern=r"^goalrepeat:"))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, v25_receipt_handler))
    app.add_handler(MessageHandler(filters.VOICE, v25_voice_handler))
    app.add_handler(MessageHandler(filters.CONTACT, customer_contact_save))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_error_handler(error_handler)

    if app.job_queue:
        app.job_queue.run_repeating(v25_unified_reminder_job, interval=60, first=5)
        app.job_queue.run_repeating(morning_job, interval=60, first=10)
        app.job_queue.run_repeating(v25_night_job, interval=60, first=16)
        app.job_queue.run_repeating(user_daily_progress_job, interval=60, first=11)
        app.job_queue.run_repeating(send_channel_morning_message, interval=60, first=12)
        app.job_queue.run_repeating(send_night_channel_feedback, interval=60, first=14)
        app.job_queue.run_repeating(channel_scheduler_job, interval=60, first=15)
        app.job_queue.run_repeating(auto_channel_job, interval=60, first=20)
        app.job_queue.run_repeating(final_daily_report_job, interval=60, first=25)
        app.job_queue.run_repeating(weekly_admin_report_job, interval=60, first=27)
        app.job_queue.run_repeating(scheduled_health_check_job, interval=60, first=60)
        app.job_queue.run_repeating(customer_reminder_job, interval=60, first=30)
        app.job_queue.run_repeating(customer_morning_job, interval=60, first=35)
        app.job_queue.run_repeating(customer_daily_report_job, interval=60, first=40)
        app.job_queue.run_repeating(customer_reengagement_job, interval=60, first=45)
        app.job_queue.run_repeating(v25_reminder_job, interval=60, first=50)

    logger.info("MyTasks build: 2026-08-23-ADMIN-ROOT-UNIFIED-AI-01")
    logger.info("AI providers configured: OmniRoute=%s OpenAI=%s n8n=%s", omniroute_configured(), bool(os.environ.get("OPENAI_API_KEY","").strip()), n8n_configured())
    logger.info("Goal bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)




# Friendly user-facing report renderer. Unlike the old admin report gate,
# this function is deliberately available to every registered user.
_OLD_V25_REPORTS_FINAL = v25_reports
async def v25_reports(update, context, period="day"):
    uid = update.effective_user.id
    today = datetime.now(TZ).date()
    days = 1 if period == "day" else (7 if period == "week" else 30)
    start = today - timedelta(days=days - 1)
    c = db()
    try:
        rows = c.execute(
            """SELECT goal_date,
                      COUNT(*) AS total,
                      SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,
                      SUM(CASE WHEN status='missed' THEN 1 ELSE 0 END) AS missed
               FROM goal_days
               WHERE user_id=? AND goal_date BETWEEN ? AND ?
               GROUP BY goal_date ORDER BY goal_date""",
            (uid, start.isoformat(), today.isoformat()),
        ).fetchall()
    finally:
        c.close()

    done = sum(int(r["done"] or 0) for r in rows)
    missed = sum(int(r["missed"] or 0) for r in rows)
    total = sum(int(r["total"] or 0) for r in rows)
    rate = (done / total * 100.0) if total else 0.0
    label = {"day": "روزانه", "week": "هفتگی", "month": "ماهانه"}.get(period, "گزارش")

    if lang(uid) == "fa":
        lines = [
            f"📊 <b>گزارش {label} شما</b>",
            "",
            f"سلام {html.escape(display_name(uid))} 👋",
            "این گزارش خلاصه فعالیت و هدف‌های ثبت‌شده شما در بازه انتخاب‌شده است.",
            "",
            f"✅ انجام‌شده: <b>{done}</b>",
            f"❌ انجام‌نشده: <b>{missed}</b>",
            f"📌 کل موارد ثبت‌شده: <b>{total}</b>",
            f"📈 نرخ موفقیت: <b>{rate:.1f}%</b>",
            f"🗓 بازه: <b>{start.isoformat()} تا {today.isoformat()}</b>",
        ]
        if period == "week" and rows:
            lines += ["", "📅 <b>جزئیات روزها:</b>"]
            for r in rows:
                d = r["goal_date"]
                dt = datetime.fromisoformat(d).date()
                day_names = ["شنبه","یکشنبه","دوشنبه","سه‌شنبه","چهارشنبه","پنجشنبه","جمعه"]
                day_name = day_names[(dt.weekday() + 1) % 7]
                lines.append(f"• {day_name} {d}: {int(r['done'] or 0)}/{int(r['total'] or 0)} ✅")
        if not rows:
            lines += ["", "ℹ️ هنوز برای این بازه فعالیت ثبت‌شده‌ای ندارید."]
        text = "\n".join(lines)
    else:
        text = (
            f"📊 <b>Your {label} Report</b>\n\n"
            f"Hello {html.escape(display_name(uid))} 👋\n"
            "Here is your activity summary for the selected period.\n\n"
            f"✅ Completed: <b>{done}</b>\n"
            f"❌ Missed: <b>{missed}</b>\n"
            f"📌 Recorded items: <b>{total}</b>\n"
            f"📈 Success rate: <b>{rate:.1f}%</b>\n"
            f"🗓 Range: <b>{start.isoformat()} to {today.isoformat()}</b>"
        )
        if not rows:
            text += "\n\nℹ️ No activity has been recorded for this period."

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 روزانه" if lang(uid)=="fa" else "📊 Daily", callback_data="v25:reports"),
            InlineKeyboardButton("📆 هفتگی" if lang(uid)=="fa" else "📆 Weekly", callback_data="v25:report_week"),
            InlineKeyboardButton("🗓 ماهانه" if lang(uid)=="fa" else "🗓 Monthly", callback_data="v25:report_month"),
        ],
        [main_menu_button(uid)],
    ])
    if getattr(update, "callback_query", None):
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

# ===================== FINAL UX / RESILIENCE PATCH 2026-08-22 =====================
# Compact navigation + public weekly report + safer callback failover.
# This layer is additive: it does not delete or reset persistent user data.

def _compact_menu_keyboard(uid, section):
    fa = lang(uid) == "fa"
    common = {
        "goals": [
            [("🎯 اهداف امروز", "cm:today"), ("✏️ هدف جدید", "cm:custom_goal")],
            [("🏆 اهداف آماده", "cm:ready_goals"), ("✏️ ویرایش اهداف", "cm:edit_goals")],
            [("📅 گزارش هفتگی", "cm:weekly"), ("📊 آمار من", "cm:stats")],
        ],
        "reports": [
            [("📅 گزارش هفتگی", "cm:weekly"), ("📊 آمار من", "cm:stats")],
            [("🏆 دستاوردها", "cm:achievements"), ("⭐ XP", "cm:xp")],
        ],
        "tools": [
            [("🤖 چت با AI", "cm:ai"), ("🎙️ دستیار صوتی", "cm:voice")],
            [("📈 قیمت آنلاین", "cm:prices"), ("🧠 مرکز من", "cm:center")],
        ],
        "vip": [
            [("💎 VIP و اشتراک", "cm:vip"), ("⭐ XP", "cm:xp")],
            [("🤝 دعوت دوستان", "cm:referral"), ("🎟️ توکن‌های من", "cm:tokens")],
        ],
        "account": [
            [("👤 پروفایل", "cm:profile"), ("⚙️ تنظیمات", "cm:settings")],
            [("🔔 یادآوری‌ها", "cm:reminders"), ("📅 تقویم من", "cm:calendar")],
        ],
        "support": [
            [("🎫 پشتیبانی", "cm:support"), ("📚 راهنمای ربات", "cm:guide")],
        ],
    }
    titles = {
        "goals": ("🎯 <b>برنامه و اهداف</b>", "🎯 <b>Goals & Plan</b>"),
        "reports": ("📊 <b>گزارش و پیشرفت</b>", "📊 <b>Reports & Progress</b>"),
        "tools": ("🤖 <b>ابزارهای هوشمند</b>", "🤖 <b>Smart Tools</b>"),
        "vip": ("💎 <b>VIP و پاداش‌ها</b>", "💎 <b>VIP & Rewards</b>"),
        "account": ("👤 <b>حساب من</b>", "👤 <b>My Account</b>"),
        "support": ("🎫 <b>پشتیبانی</b>", "🎫 <b>Support</b>"),
    }
    rows = []
    for row in common.get(section, []):
        rows.append([
            InlineKeyboardButton((fa_text if fa else en_text), callback_data=cb)
            for fa_text, cb in row
            for en_text in [fa_text]
        ])
    # The labels above are intentionally Persian-first for this bot; translate
    # the small set of category navigation buttons separately where needed.
    if not fa:
        en = {
            "cm:today":"🎯 Today's Goals","cm:custom_goal":"✏️ New Goal",
            "cm:ready_goals":"🏆 Ready Goals","cm:edit_goals":"✏️ Edit Goals",
            "cm:weekly":"📅 Weekly Report","cm:stats":"📊 My Stats",
            "cm:achievements":"🏆 Achievements","cm:xp":"⭐ XP",
            "cm:ai":"🤖 AI Chat","cm:voice":"🎙️ Voice Assistant",
            "cm:prices":"📈 Online Prices","cm:center":"🧠 My Center",
            "cm:vip":"💎 VIP & Subscription","cm:referral":"🤝 Referrals",
            "cm:tokens":"🎟️ My Tokens","cm:profile":"👤 Profile",
            "cm:settings":"⚙️ Settings","cm:reminders":"🔔 Reminders",
            "cm:calendar":"📅 Calendar","cm:support":"🎫 Support",
            "cm:guide":"📚 Bot Guide",
        }
        rows = [[InlineKeyboardButton(en.get(btn.callback_data, btn.text), callback_data=btn.callback_data) for btn in row] for row in rows]
    rows.append([
        InlineKeyboardButton("⬅️ بازگشت" if fa else "⬅️ Back", callback_data="cm:home")
    ])
    rows.append([main_menu_button(uid)])
    return InlineKeyboardMarkup(rows)


async def _compact_menu_show(update, context, section):
    uid = update.effective_user.id
    fa = lang(uid) == "fa"
    titles = {
        "goals": ("🎯 <b>برنامه و اهداف</b>", "🎯 <b>Goals & Plan</b>"),
        "reports": ("📊 <b>گزارش و پیشرفت</b>", "📊 <b>Reports & Progress</b>"),
        "tools": ("🤖 <b>ابزارهای هوشمند</b>", "🤖 <b>Smart Tools</b>"),
        "vip": ("💎 <b>VIP و پاداش‌ها</b>", "💎 <b>VIP & Rewards</b>"),
        "account": ("👤 <b>حساب من</b>", "👤 <b>My Account</b>"),
        "support": ("🎫 <b>پشتیبانی</b>", "🎫 <b>Support</b>"),
    }
    text = titles.get(section, titles["goals"])[0 if fa else 1]
    q = update.callback_query
    if q:
        await q.answer()
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=_compact_menu_keyboard(uid, section))
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=_compact_menu_keyboard(uid, section))


async def general_guide(update, context):
    """User-facing guide route for the compact Smart Tools menu."""
    uid = update.effective_user.id
    fa = lang(uid) == "fa"
    text = (
        "📚 <b>راهنمای ربات</b>\n\n"
        "🎯 برنامه و اهداف: ساخت و پیگیری هدف‌ها\n"
        "📊 گزارش و پیشرفت: مشاهده آمار و گزارش‌ها\n"
        "🤖 ابزارهای هوشمند: چت AI، دستیار صوتی و قیمت‌ها\n"
        "🧠 مرکز من: یادآوری، تقویم، سرمایه‌ها، اقساط و پروفایل\n"
        "👥 مدیریت مشتری و نوبت‌دهی: برای حساب‌های مجاز\n\n"
        "برای برگشت از دکمه «⬅️ بازگشت» استفاده کن."
        if fa else
        "📚 <b>Bot Guide</b>\n\n"
        "🎯 My Plan: create and track goals\n"
        "📊 Reports: view progress and statistics\n"
        "🤖 Smart Tools: AI chat, voice assistant and prices\n"
        "🧠 My Center: reminders, calendar, portfolio, installments and profile\n"
        "👥 Customers & Appointments: for eligible accounts\n\n"
        "Use «⬅️ Back» to return."
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ بازگشت" if fa else "⬅️ Back", callback_data="cm:tools"),
        main_menu_button(uid),
    ]])
    if update.callback_query:
        await update.callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def compact_menu_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    data = q.data
    await q.answer()
    if data == "cm:home":
        await q.message.edit_text(
            "🏠 <b>منوی اصلی</b>\n\nیک بخش را انتخاب کن." if lang(uid) == "fa"
            else "🏠 <b>Main Menu</b>\n\nChoose a section.",
            parse_mode="HTML",
            reply_markup=_compact_root_inline(uid),
        )
        return

    routes = {
        "cm:today": today,
        "cm:custom_goal": custom_goal_start,
        "cm:ready_goals": ready_menu,
        "cm:edit_goals": edit_menu,
        "cm:weekly": weekly,
        "cm:stats": stats,
        "cm:achievements": achievements,
        "cm:xp": xp_command,
        "cm:ai": ai_chat_start,
        "cm:voice": None,
        "cm:prices": prices,
        "cm:center": v25_hub,
        "cm:vip": vip_center,
        "cm:referral": referral,
        "cm:tokens": None,
        "cm:profile": profile,
        "cm:settings": settings,
        "cm:reminders": v25_reminders_menu,
        "cm:calendar": None,
        "cm:support": support_start,
        "cm:guide": general_guide,
    }
    if data == "cm:voice":
        clear_flow(context)
        await q.message.edit_text(
            "🎙️ <b>دستیار صوتی</b>\n\nویس خودت را در پیام بعدی بفرست." if lang(uid) == "fa"
            else "🎙️ <b>Voice Assistant</b>\n\nSend your voice message next.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[main_menu_button(uid)]])
        )
        context.user_data["v25_voice_mode"] = True
        return
    if data == "cm:tokens":
        tokens_from_xp(uid)
        await q.message.edit_text(token_user_text(uid), parse_mode="HTML", reply_markup=token_user_keyboard(uid))
        return
    if data == "cm:calendar":
        await q.message.edit_text(
            "📅 <b>تقویم من</b>\n\nاین بخش از «🧠 مرکز من» قابل مدیریت است." if lang(uid) == "fa"
            else "📅 <b>My Calendar</b>\n\nManage it from My Center.",
            parse_mode="HTML",
            reply_markup=v25_hub_keyboard(uid)
        )
        return
    fn = routes.get(data)
    if fn:
        clear_flow(context)
        # Adapt the callback query into a minimal update object so existing
        # message-based handlers can be reused without duplicating business logic.
        proxy = type("_MenuUpdate", (), {
            "effective_user": q.from_user,
            "message": q.message,
            "callback_query": None,
        })()
        try:
            await fn(proxy, context)
        except Exception as exc:
            logger.exception("Compact menu route failed: %s", data)
            # Never hide a route failure by resetting the whole bot to the home menu.
            # Offer a direct retry and a controlled back path instead.
            retry_text = (
                "⚠️ این بخش با خطا روبه‌رو شد.\n\n"
                f"کد خطا: <code>{type(exc).__name__}</code>\n"
                "می‌توانی دوباره همین بخش را امتحان کنی یا به ابزارهای هوشمند برگردی."
                if lang(uid) == "fa" else
                "⚠️ This section encountered an error.\n\n"
                f"Error: <code>{type(exc).__name__}</code>\n"
                "Retry this section or return to Smart Tools."
            )
            await q.message.reply_text(
                retry_text, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 تلاش دوباره" if lang(uid)=="fa" else "🔄 Retry", callback_data=data)],
                    [InlineKeyboardButton("🤖 ابزارهای هوشمند" if lang(uid)=="fa" else "🤖 Smart Tools", callback_data="menu:tools")],
                    [main_menu_button(uid)],
                ]),
            )
        return


def _compact_root_inline(uid):
    fa = lang(uid) == "fa"
    labels = [
        ("🎯 برنامه من", "menu:goals", "🎯 My Plan"),
        ("📊 گزارش و پیشرفت", "menu:reports", "📊 Reports"),
        ("🤖 ابزارهای هوشمند", "menu:tools", "🤖 Smart Tools"),
        ("💎 VIP و XP", "menu:vip", "💎 VIP & XP"),
        ("👤 حساب من", "menu:account", "👤 My Account"),
        ("🎫 پشتیبانی", "menu:support", "🎫 Support"),
    ]
    rows = []
    for i in range(0, len(labels), 2):
        rows.append([
            InlineKeyboardButton((a if fa else c), callback_data=b)
            for a, b, c in labels[i:i+2]
        ])
    return InlineKeyboardMarkup(rows)


async def compact_section_callback(update, context):
    q = update.callback_query
    section = q.data.split(":", 1)[1]
    await _compact_menu_show(update, context, section)


def _compact_user_keyboard(uid):
    # Compact user area: two columns so the main capabilities are visible
    # without a long vertical keyboard. Keep customer/booking tools available.
    fa = lang(uid) == "fa"
    rows = [
        ["🎯 برنامه من" if fa else "🎯 My Plan", "📊 گزارش و پیشرفت" if fa else "📊 Reports"],
        ["🤖 ابزارهای هوشمند" if fa else "🤖 Smart Tools", "💎 VIP و XP" if fa else "💎 VIP & XP"],
        ["👤 حساب من" if fa else "👤 My Account", "🎫 پشتیبانی" if fa else "🎫 Support"],
        ["👥 مدیریت مشتری و نوبت‌دهی" if fa else "👥 Customer & Appointments", "📅 رزروهای من" if fa else "📅 My Bookings"],
        ["⚙️ تنظیمات" if fa else "⚙️ Settings", "📈 قیمت آنلاین" if fa else "📈 Online Prices"],
        ["🤝 دعوت دوستان" if fa else "🤝 Invite Friends", "🎟️ توکن‌های من" if fa else "🎟️ My Tokens"],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)


def _compact_admin_management_keyboard(uid):
    """Management-only menu. User features stay in the separate user area."""
    fa=lang(uid)=="fa"
    rows=[
        ["📊 داشبورد و گزارش" if fa else "📊 Dashboard & Reports"],
        ["👥 کاربران و نقش‌ها" if fa else "👥 Users & Roles", "🎫 تیکت‌ها و Incident" if fa else "🎫 Tickets & Incidents"],
        ["💰 مالی و پرداخت" if fa else "💰 Finance & Payments", "💎 VIP / XP / Token" if fa else "💎 VIP / XP / Token"],
        ["📢 کانال و انتشار" if fa else "📢 Channels & Publishing", "🤖 مدیریت AI" if fa else "🤖 AI Management"],
        ["🩺 سلامت و Diagnostics" if fa else "🩺 Health & Diagnostics", "💾 Backup و Recovery" if fa else "💾 Backup & Recovery"],
        ["🧩 قابلیت‌ها و Feature Flags" if fa else "🧩 Features & Flags", "🔐 امنیت و Audit" if fa else "🔐 Security & Audit"],
        ["🧪 مرکز تست و Regression" if fa else "🧪 Test & Regression", "⚙️ تنظیمات سیستم" if fa else "⚙️ System Settings"],
        ["📦 سایر ماژول‌های مدیریتی" if fa else "📦 Other Admin Modules"],
        ["👤 استفاده از ربات" if fa else "👤 Use Bot"],
        ["🏠 منوی اصلی" if fa else "🏠 Main Menu"],
    ]
    return ReplyKeyboardMarkup(rows,resize_keyboard=True,one_time_keyboard=False)


def _compact_admin_root_keyboard(uid):
    """Admin root: exactly two choices, user area or management area."""
    fa=lang(uid)=="fa"
    return ReplyKeyboardMarkup([
        ["👤 استفاده از ربات" if fa else "👤 Use Bot"],
        ["🛡 مدیریت ربات" if fa else "🛡 Bot Management"],
    ],resize_keyboard=True,one_time_keyboard=False)


def _show_admin_management(update, context):
    uid=update.effective_user.id
    return update.message.reply_text(
        "🛡 <b>مدیریت ربات</b>\n\nبخش مدیریت را انتخاب کن." if lang(uid)=="fa" else
        "🛡 <b>Bot Management</b>\n\nChoose a management section.",
        parse_mode="HTML", reply_markup=_compact_admin_management_keyboard(uid)
    )


def compact_keyboard(uid):
    """Root keyboard. Admins see only the two requested top-level choices."""
    if admin_is_allowed(uid):
        return _compact_admin_root_keyboard(uid)
    return _compact_user_keyboard(uid)


# Make the compact menu the final renderer used by all subsequent handlers.
keyboard = compact_keyboard


_OLD_TEXT_ROUTER_COMPACT = text_router
async def text_router(update, context):
    if not update.message or not update.message.text:
        return
    uid = update.effective_user.id
    txt = update.message.text.strip()
    # Keep both names for compatibility with the older dispatcher code below.
    # The final router historically used `text`, while its input was stored in `txt`.
    # That NameError caused menu/AI actions to fall into the global error handler.
    text = txt
    category_map = {
        "🎯 برنامه من": "goals", "🎯 My Plan": "goals",
        "📊 گزارش و پیشرفت": "reports", "📊 Reports": "reports",
        "🤖 ابزارهای هوشمند": "tools", "🤖 Smart Tools": "tools",
        "💎 VIP و XP": "vip", "💎 VIP & XP": "vip",
        "👤 حساب من": "account", "👤 My Account": "account",
        "🎫 پشتیبانی": "support", "🎫 Support": "support",
    }
    if txt in category_map:
        await _compact_menu_show(update, context, category_map[txt])
        return
    if txt in ("🛡 پنل مدیریت", "🛡 Admin Panel"):
        await admin_command(update, context)
        return
    if txt in ("📊 گزارش مدیریت", "📊 Admin Reports"):
        await admin_command(update, context)
        return
    if txt in ("🎫 تیکت‌ها", "🎫 Tickets"):
        await admin_command(update, context)
        return
    if txt in ("🧩 قابلیت‌ها", "🧩 Features"):
        await admin_command(update, context)
        return
    if txt in ("🤖 مدیریت AI", "🤖 AI Management"):
        await admin_command(update, context)
        return
    if txt in ("👥 کاربران", "👥 Users"):
        await admin_command(update, context)
        return
    if txt in ("💰 مالی", "💰 Finance"):
        await admin_command(update, context)
        return
    if txt in ("🧭 کنترل کامل سیستم", "🧭 Full System Control"):
        if not admin_guard(uid):
            await update.message.reply_text("⛔ دسترسی ندارید.", reply_markup=keyboard(uid))
            return
        await update.message.reply_text(
            "🧭 <b>مرکز کنترل کامل سیستم</b>\n\nاز دکمه زیر وارد مرکز مدیریت کامل شو.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🧭 ورود به مرکز مدیریت", callback_data="v25:master:home")],
                [main_menu_button(uid)],
            ]),
        )
        return
    if txt in ("📢 مدیریت کانال", "📢 Channel Management"):
        if not admin_guard(uid):
            await update.message.reply_text("⛔ دسترسی ندارید.", reply_markup=keyboard(uid))
            return
        await update.message.reply_text(
            "📢 <b>مدیریت کانال و پست‌گذاری</b>",
            parse_mode="HTML",
            reply_markup=channel_keyboard(),
        )
        return
    if txt == "⚙️ تنظیمات" or txt == "⚙️ Settings":
        await settings(update, context)
        return
    return await _OLD_TEXT_ROUTER_COMPACT(update, context)


# Weekly/day/month reports are user-facing. They must never be blocked by the
# admin permission gate that previously caused the screenshot's error message.
_OLD_V25_CALLBACK_COMPACT = v25_callback
async def v25_callback(update, context):
    data = update.callback_query.data
    uid = update.effective_user.id
    if data in {"v25:reports", "v25:report_week", "v25:report_month"}:
        await update.callback_query.answer()
        period = {"v25:reports": "day", "v25:report_week": "week", "v25:report_month": "month"}[data]
        return await v25_reports(update, context, period)
    return await _OLD_V25_CALLBACK_COMPACT(update, context)


# ===================== MASTER MANAGEMENT CONTROL CENTER =====================
# Added after the legacy layers so the new management UI is additive and does
# not replace existing user features, payments, VIP, channel, or customer flows.
MASTER_RBAC_ROLES = {
    "owner": "👑 Owner",
    "senior_manager": "🛡 مدیر ارشد",
    "general_manager": "👤 مدیر عمومی",
    "technical_manager": "🧰 مدیر فنی",
    "finance_manager": "💰 مدیر مالی",
    "ticket_manager": "🎫 مدیر تیکت",
    "channel_manager": "📢 مدیر کانال",
}

MASTER_PERMISSION_KEYS = [
    "view_dashboard", "manage_users", "manage_roles", "manage_vip", "manage_xp",
    "manage_tickets", "manage_finance", "manage_channels", "manage_ai", "manage_features",
    "run_health", "run_diagnostics", "backup", "restore", "view_audit", "run_tests",
    "manage_system",
]

MASTER_ROLE_PERMISSIONS = {
    "owner": set(MASTER_PERMISSION_KEYS),
    "senior_manager": {"view_dashboard", "manage_users", "manage_vip", "manage_xp", "manage_tickets", "manage_channels", "manage_ai", "manage_features", "run_health", "run_diagnostics", "backup", "view_audit", "run_tests"},
    "general_manager": {"view_dashboard", "manage_users", "manage_vip", "manage_xp", "manage_tickets", "manage_channels", "manage_features", "run_health", "run_diagnostics", "run_tests"},
    "technical_manager": {"view_dashboard", "manage_ai", "manage_features", "run_health", "run_diagnostics", "backup", "view_audit", "run_tests"},
    "finance_manager": {"view_dashboard", "manage_vip", "manage_xp", "manage_finance", "view_audit", "run_tests"},
    "ticket_manager": {"view_dashboard", "manage_tickets", "run_health", "view_audit", "run_tests"},
    "channel_manager": {"view_dashboard", "manage_channels", "run_health", "run_tests"},
}

MASTER_DOMAIN_PERMISSION = {
    "users": "manage_users", "roles": "manage_roles", "vip": "manage_vip", "xp": "manage_xp",
    "tickets": "manage_tickets", "finance": "manage_finance", "channels": "manage_channels",
    "ai": "manage_ai", "features": "manage_features", "health": "run_health",
    "diagnostics": "run_diagnostics", "backup": "backup", "restore": "restore",
    "audit": "view_audit", "tests": "run_tests", "system": "manage_system",
}

def master_owner_id():
    return min(ADMIN_IDS) if ADMIN_IDS else 0

def master_rbac_init_db():
    c=db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS management_roles(
        user_id INTEGER PRIMARY KEY,
        role TEXT NOT NULL DEFAULT 'general_manager',
        domain TEXT NOT NULL DEFAULT 'general',
        permissions_json TEXT NOT NULL DEFAULT '[]',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS management_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        requested_by INTEGER NOT NULL,
        action TEXT NOT NULL,
        target_user INTEGER,
        payload TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'pending',
        approved_by INTEGER,
        created_at TEXT NOT NULL,
        decided_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_management_requests_status ON management_requests(status, created_at);
    CREATE TABLE IF NOT EXISTS incident_tickets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fingerprint TEXT NOT NULL,
        module TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'error',
        details TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open',
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        occurrences INTEGER NOT NULL DEFAULT 1,
        UNIQUE(fingerprint, status)
    );
    CREATE INDEX IF NOT EXISTS idx_incident_tickets_status ON incident_tickets(status, last_seen_at);
    CREATE TABLE IF NOT EXISTS system_test_runs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER NOT NULL,
        passed INTEGER NOT NULL DEFAULT 0,
        total INTEGER NOT NULL DEFAULT 0,
        details TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    """)
    now=datetime.now(TZ).isoformat()
    owner=master_owner_id()
    if owner:
        c.execute("INSERT OR IGNORE INTO management_roles(user_id,role,domain,permissions_json,active,created_at,updated_at) VALUES(?,?,?,?,1,?,?)",(owner,"owner","all",json.dumps(sorted(MASTER_ROLE_PERMISSIONS["owner"])),now,now))
    for admin_id in ADMIN_IDS:
        c.execute("INSERT OR IGNORE INTO management_roles(user_id,role,domain,permissions_json,active,created_at,updated_at) VALUES(?,?,?,?,1,?,?)",(admin_id,"general_manager","general",json.dumps(sorted(MASTER_ROLE_PERMISSIONS["general_manager"])),now,now))
    c.commit(); c.close()

_OLD_INIT_DB_MASTER=init_db
def init_db():
    _OLD_INIT_DB_MASTER()
    try:
        master_rbac_init_db()
    except Exception:
        logger.exception("Master RBAC initialization failed")


def master_role(uid):
    if uid == master_owner_id() and uid:
        return "owner"
    try:
        c=db(); r=c.execute("SELECT role FROM management_roles WHERE user_id=? AND active=1",(int(uid),)).fetchone(); c.close()
        return r["role"] if r else ("owner" if uid in ADMIN_IDS else "")
    except Exception:
        return "owner" if uid in ADMIN_IDS else ""


def master_has_permission(uid, permission):
    if uid == master_owner_id() and uid:
        return True
    role=master_role(uid)
    if not role:
        return False
    if role == "owner":
        return True
    return permission in MASTER_ROLE_PERMISSIONS.get(role,set())


def master_guard(uid, permission=None):
    if uid not in ADMIN_IDS:
        return False
    return True if permission is None else master_has_permission(uid, permission)


def master_log(uid, action, target=None, details=""):
    try:
        admin_log(uid, f"master:{action}", target, details)
    except Exception:
        logger.exception("Master audit log failed")


def master_incident(module, details, severity="error"):
    """Create one open incident per fingerprint. Repeated failures increment a counter."""
    try:
        now=datetime.now(TZ).isoformat()
        fingerprint=hashlib.sha256(f"{module}|{details}".encode("utf-8","ignore")).hexdigest()[:32]
        c=db()
        row=c.execute("SELECT id,occurrences FROM incident_tickets WHERE fingerprint=? AND status='open'",(fingerprint,)).fetchone()
        if row:
            c.execute("UPDATE incident_tickets SET last_seen_at=?,occurrences=occurrences+1,details=?,severity=? WHERE id=?",(now,details,severity,row["id"]))
        else:
            c.execute("INSERT INTO incident_tickets(fingerprint,module,severity,details,status,first_seen_at,last_seen_at,occurrences) VALUES(?,?,?,?,?,?,?,1)",(fingerprint,module,severity,details,"open",now,now))
        c.commit(); c.close()
    except Exception:
        logger.exception("Incident ticket creation failed")


def master_dashboard_text():
    s=admin_stats()
    c=db()
    incidents=int(c.execute("SELECT COUNT(*) n FROM incident_tickets WHERE status='open'").fetchone()["n"])
    managers=int(c.execute("SELECT COUNT(*) n FROM management_roles WHERE active=1").fetchone()["n"])
    tests=int(c.execute("SELECT COUNT(*) n FROM system_test_runs").fetchone()["n"])
    ai_errors=int(c.execute("SELECT COUNT(*) n FROM service_events WHERE service LIKE '%ai%' AND status IN ('ERROR','error')").fetchone()["n"])
    c.close()
    return ("📊 <b>داشبورد مرکزی مدیریت</b>\n\n"
            f"👥 کاربران: <b>{s['users']}</b>\n🟢 فعال امروز: <b>{s['active_today']}</b>\n🆕 جدید امروز: <b>{s['new_today']}</b>\n"
            f"💎 VIP فعال: <b>{s['vip_users']}</b>\n🎫 تیکت باز: <b>{s['open_tickets']}</b>\n"
            f"🚨 Incident باز: <b>{incidents}</b>\n👤 مدیر فعال: <b>{managers}</b>\n"
            f"🤖 خطای ثبت‌شده AI: <b>{ai_errors}</b>\n🧪 تست‌های اجراشده: <b>{tests}</b>")


def master_root_keyboard(uid):
    fa=lang(uid)=="fa"
    items=[
        ("📊 داشبورد و Analytics","📊 Dashboard & Analytics","dashboard"),
        ("👥 کاربران و نقش‌ها","👥 Users & Roles","users"),
        ("🎫 تیکت و Incident","🎫 Tickets & Incidents","tickets"),
        ("💰 مالی و پرداخت","💰 Finance & Payments","finance"),
        ("💎 VIP / XP / Token","💎 VIP / XP / Token","vip"),
        ("📢 کانال و انتشار","📢 Channels & Publishing","channels"),
        ("🤖 AI و Voice","🤖 AI & Voice","ai"),
        ("🩺 سلامت و Diagnostics","🩺 Health & Diagnostics","health"),
        ("💾 Backup و Recovery","💾 Backup & Recovery","backup"),
        ("🧩 قابلیت‌ها و Feature Flags","🧩 Features & Flags","features"),
        ("🔐 امنیت و Audit","🔐 Security & Audit","audit"),
        ("🧪 مرکز تست و Regression","🧪 Test & Regression","tests"),
        ("⚙️ تنظیمات سیستم","⚙️ System Settings","system"),
        ("📦 سایر ماژول‌های مدیریتی","📦 Other Admin Modules","other"),
    ]
    rows=[]
    for ft,et,key in items:
        perm=MASTER_DOMAIN_PERMISSION.get(key)
        if perm and not master_has_permission(uid,perm):
            continue
        rows.append([InlineKeyboardButton(ft if fa else et,callback_data=f"v25:master:{key}")])
    rows.append([InlineKeyboardButton("🏠 منوی اصلی" if fa else "🏠 Main Menu",callback_data="v25:master:main")])
    return InlineKeyboardMarkup(rows)


def master_back_keyboard(uid):
    fa=lang(uid)=="fa"
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ مرکز مدیریت" if fa else "⬅️ Management Center",callback_data="v25:master:home"),main_menu_button(uid)]])


def master_feature_text():
    c=db(); rows=c.execute("SELECT key,enabled FROM feature_flags ORDER BY key").fetchall(); c.close()
    lines=["🧩 <b>Feature Flags</b>",""]
    for r in rows:
        lines.append(f"{'🟢' if r['enabled'] else '🔴'} {html.escape(r['key'])}")
    return "\n".join(lines)


def master_finance_text():
    c=db()
    revenue=int(c.execute("SELECT COALESCE(SUM(total_amount),0) n FROM payments").fetchone()["n"])
    payments=int(c.execute("SELECT COUNT(*) n FROM payments").fetchone()["n"])
    vip=int(c.execute("SELECT COUNT(*) n FROM subscription_history").fetchone()["n"])
    c.close()
    return f"💰 <b>مالی و پرداخت</b>\n\n💳 تراکنش‌ها: <b>{payments}</b>\n💵 مبلغ ثبت‌شده: <b>{revenue:,}</b>\n💎 سوابق اشتراک: <b>{vip}</b>\n\n🔐 اطلاعات حساس مالی فقط برای Owner نمایش داده می‌شود."


def master_users_text():
    c=db(); rows=c.execute("SELECT user_id,first_name,blocked,vip_until FROM users ORDER BY created_at DESC LIMIT 15").fetchall(); roles=c.execute("SELECT role,COUNT(*) n FROM management_roles WHERE active=1 GROUP BY role ORDER BY n DESC").fetchall(); c.close()
    lines=["👥 <b>کاربران و نقش‌ها</b>","", "<b>نقش‌های مدیریتی</b>"]
    lines += [f"• {MASTER_RBAC_ROLES.get(r['role'],r['role'])}: {r['n']}" for r in roles]
    lines += ["","<b>آخرین کاربران</b>"]
    for r in rows:
        vip="💎" if r["vip_until"] else ""
        blocked="⛔" if r["blocked"] else "🟢"
        lines.append(f"{blocked} {html.escape(r['first_name'] or 'بدون نام')} | <code>{r['user_id']}</code> {vip}")
    return "\n".join(lines)


def master_ai_text():
    state=ai_provider_diagnostics()
    return ("🤖 <b>AI و Voice</b>\n\n"
            f"OmniRoute: {'🟢' if state.get('omniroute') else '🔴'}\n"
            f"OpenAI: {'🟢' if state.get('openai') else '🔴'}\n"
            f"n8n: {'🟢' if state.get('n8n') else '🔴'}\n"
            f"Gemini: {'🟢' if state.get('gemini') else '🔴'}\n"
            f"Text AI unified: {'🟢' if state.get('text_unified') else '🔴'}\n"
            f"Voice STT: {'🟢' if state.get('voice_stt') else '🔴'}\n\n"
            "AI مجاز به اجرای مستقیم عملیات حساس نیست. خروجی باید از مسیر اعتبارسنجی عبور کند.")


def master_tests():
    results=[]
    def check(name, fn):
        try:
            ok=bool(fn()); results.append((name,ok,"OK" if ok else "FAIL"))
        except Exception as exc:
            results.append((name,False,type(exc).__name__))
    check("Time parser", lambda: parse_time("۱۸:۳۰")=="18:30" and parse_time("2360") is None)
    check("Admin isolation", lambda: (not master_guard(0)) and master_guard(master_owner_id()))
    check("RBAC default deny", lambda: not master_has_permission(999999999,"manage_finance"))
    check("DB integrity", lambda: db().execute("PRAGMA integrity_check").fetchone()[0]=="ok")
    check("Feature table", lambda: db().execute("SELECT 1 FROM feature_flags LIMIT 1").fetchone() is not None)
    check("Audit table", lambda: db().execute("SELECT 1 FROM admin_logs LIMIT 1").fetchone() is not None)
    check("Incident table", lambda: db().execute("SELECT 1 FROM incident_tickets LIMIT 1").fetchone() is not None)
    return results


def master_test_text(uid):
    results=master_tests(); passed=sum(1 for _,ok,_ in results if ok); total=len(results)
    details="\n".join(f"{'🟢' if ok else '🔴'} {html.escape(name)}: {html.escape(detail)}" for name,ok,detail in results)
    c=db(); c.execute("INSERT INTO system_test_runs(admin_id,passed,total,details,created_at) VALUES(?,?,?,?,?)",(uid,passed,total,details,datetime.now(TZ).isoformat())); c.commit(); c.close()
    return f"🧪 <b>Regression Test</b>\n\n{details}\n\nنتیجه: <b>{passed}/{total}</b>"


async def master_management_callback(update,context):
    q=update.callback_query; uid=q.from_user.id; data=q.data
    if not master_guard(uid):
        await q.answer("⛔ دسترسی ندارید.",show_alert=True); return
    action=data.split(":",2)[2] if data.count(":")>=2 else "home"
    if action in {"home","dashboard"}:
        await q.answer(); await q.message.edit_text(master_dashboard_text(),parse_mode="HTML",reply_markup=master_root_keyboard(uid)); return
    if action=="main":
        await q.answer(); clear_flow(context); await q.message.edit_text("🏠 منوی اصلی",reply_markup=None); await q.message.reply_text("🏠 منوی اصلی",reply_markup=keyboard(uid)); return
    perm=MASTER_DOMAIN_PERMISSION.get(action)
    if perm and not master_has_permission(uid,perm):
        await q.answer("⛔ این بخش برای نقش شما مجاز نیست.",show_alert=True); return
    await q.answer()
    if action=="users":
        await q.message.edit_text(master_users_text(),parse_mode="HTML",reply_markup=master_back_keyboard(uid)); return
    if action=="tickets":
        c=db(); rows=c.execute("SELECT id,module,severity,occurrences,last_seen_at FROM incident_tickets WHERE status='open' ORDER BY last_seen_at DESC LIMIT 15").fetchall(); open_t=c.execute("SELECT COUNT(*) n FROM tickets WHERE status='open'").fetchone()["n"]; c.close()
        text="🎫 <b>تیکت و Incident</b>\n\n"+f"تیکت‌های باز: <b>{open_t}</b>\nIncidentهای باز: <b>{len(rows)}</b>\n\n"+"\n".join(f"🚨 #{r['id']} | {html.escape(r['module'])} | {r['severity']} | x{r['occurrences']}" for r in rows) or "مورد بازی نیست."
        await q.message.edit_text(text,parse_mode="HTML",reply_markup=master_back_keyboard(uid)); return
    if action=="finance":
        if uid!=master_owner_id():
            await q.message.edit_text("💰 <b>مالی</b>\n\nاطلاعات حساس مالی فقط برای Owner قابل مشاهده است.\nگزارش‌های غیرحساس سیستم از داشبورد در دسترس نقش‌های مجاز است.",parse_mode="HTML",reply_markup=master_back_keyboard(uid)); return
        await q.message.edit_text(master_finance_text(),parse_mode="HTML",reply_markup=master_back_keyboard(uid)); return
    if action=="vip":
        text="💎 <b>VIP / XP / Token</b>\n\nاین بخش به مرکز فعلی VIP، XP و Token متصل است.\n\nمدیریت پلن VIP و Token از پنل فعلی انجام می‌شود."
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("💎 مرکز VIP",callback_data="v25:adminplans")],[InlineKeyboardButton("🎟️ مدیریت Token",callback_data="v25:tokens_admin")], [InlineKeyboardButton("⬅️ مرکز مدیریت",callback_data="v25:master:home")]])
        await q.message.edit_text(text,parse_mode="HTML",reply_markup=kb); return
    if action=="channels":
        await q.message.edit_text("📢 <b>کانال و انتشار</b>\n\nمدیریت کانال، پست‌گذاری، زمان‌بندی، انتشار خودکار، تأیید قبل از انتشار و بررسی عضویت در این بخش‌های موجود ربات فعال هستند.",parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📢 مدیریت کانال",callback_data="adm:channel")],[InlineKeyboardButton("🤖 انتشار خودکار",callback_data="auto:menu")],[InlineKeyboardButton("⬅️ مرکز مدیریت",callback_data="v25:master:home")]])); return
    if action=="ai":
        await q.message.edit_text(master_ai_text(),parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎙️ تنظیمات Voice",callback_data="v25:adminvoice")],[InlineKeyboardButton("🔧 وضعیت قابلیت‌ها",callback_data="v25:adminfeatures")],[InlineKeyboardButton("⬅️ مرکز مدیریت",callback_data="v25:master:home")]])); return
    if action=="health":
        await run_health_checks(context.bot,uid); text=health_text();
        await q.message.edit_text(text,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🩺 اجرای دوباره",callback_data="v25:master:health")],[InlineKeyboardButton("🔎 Diagnostics",callback_data="v25:master:diagnostics")],[InlineKeyboardButton("⬅️ مرکز مدیریت",callback_data="v25:master:home")]])); return
    if action=="diagnostics":
        await q.message.edit_text(_admin_diagnostics_text(),parse_mode="HTML",reply_markup=master_back_keyboard(uid)); return
    if action=="backup":
        ok=backup_database_snapshot(keep=20); master_log(uid,"backup",details="success" if ok else "failed");
        await q.message.edit_text("💾 بکاپ با موفقیت ساخته شد." if ok else "❌ ساخت بکاپ ناموفق بود.",reply_markup=master_back_keyboard(uid)); return
    if action=="features":
        await q.message.edit_text(master_feature_text(),parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🧩 مرکز قابلیت‌های فعلی",callback_data="adm:features")],[InlineKeyboardButton("⬅️ مرکز مدیریت",callback_data="v25:master:home")]])); return
    if action=="audit":
        c=db(); rows=c.execute("SELECT admin_id,action,target_user,details,created_at FROM admin_logs ORDER BY id DESC LIMIT 30").fetchall(); c.close();
        text="🔐 <b>Security / Audit</b>\n\n"+"\n".join(f"• {fa_datetime(r['created_at'])} | {r['admin_id']} | {html.escape(r['action'])} | {r['target_user'] or '-'}" for r in rows) or "لاگی ثبت نشده."
        await q.message.edit_text(text,parse_mode="HTML",reply_markup=master_back_keyboard(uid)); return
    if action=="tests":
        await q.message.edit_text(master_test_text(uid),parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 اجرای دوباره",callback_data="v25:master:tests")],[InlineKeyboardButton("⬅️ مرکز مدیریت",callback_data="v25:master:home")]])); return
    if action=="other":
        await q.message.edit_text("📦 <b>سایر ماژول‌های مدیریتی</b>\n\nدر این بخش، ماژول‌های موجود نسخه فعلی را بدون حذف مسیرهای قدیمی کنترل می‌کنی.",parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎 پلن‌های VIP",callback_data="v25:adminplans")],[InlineKeyboardButton("💳 پرداخت",callback_data="v25:adminpayment")],[InlineKeyboardButton("📱 پیامک",callback_data="v25:adminsms")],[InlineKeyboardButton("⭐ نظرسنجی",callback_data="v25:adminsurvey")],[InlineKeyboardButton("☀️ صبح/شب",callback_data="v25:adminmorning")],[InlineKeyboardButton("📈 قیمت بازار",callback_data="v25:adminprices")],[InlineKeyboardButton("👥 مشتری و نوبت",callback_data="adm:customers")],[InlineKeyboardButton("📋 گزارش مدیریت",callback_data="adm:report")],[InlineKeyboardButton("⬅️ مرکز مدیریت",callback_data="v25:master:home")]])); return
    if action=="system":
        paused=get_system_setting("bot_paused_until","")
        maintenance=feature_enabled("maintenance")
        text=f"⚙️ <b>تنظیمات سیستم</b>\n\n🛠 Maintenance: {'🟢' if maintenance else '🔴'}\n⏸ توقف موقت: {html.escape(paused or 'فعال نیست')}\n🗄 Schema: {DB_SCHEMA_VERSION}\n\nمالک اصلی: <code>{master_owner_id() or '-'}</code>"
        await q.message.edit_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🧩 تغییر قابلیت‌ها",callback_data="adm:features")],[InlineKeyboardButton("⏸ مدیریت توقف",callback_data="adm:pause")],[InlineKeyboardButton("⬅️ مرکز مدیریت",callback_data="v25:master:home")]])); return
    await q.message.edit_text("این بخش هنوز به عملیات اختصاصی متصل نشده است.",reply_markup=master_back_keyboard(uid))

_OLD_V25_CALLBACK_MASTER=v25_callback
async def v25_callback(update,context):
    data=update.callback_query.data if update.callback_query else ""
    if data.startswith("v25:master:"):
        return await master_management_callback(update,context)
    return await _OLD_V25_CALLBACK_MASTER(update,context)

# Make the complete management center visible from the existing admin panel.
_OLD_FINAL_ADMIN_KEYBOARD_MASTER=final_admin_keyboard
def final_admin_keyboard():
    base=_OLD_FINAL_ADMIN_KEYBOARD_MASTER().inline_keyboard
    rows=[list(r) for r in base]
    rows.append([InlineKeyboardButton("🧭 کنترل کامل سیستم",callback_data="v25:master:home")])
    return InlineKeyboardMarkup(rows)
admin_keyboard=final_admin_keyboard


# Final callback handler for the compact section buttons.


# ===================== FINAL ADMIN / MANAGER MANAGEMENT REPAIR =====================
# Adds:
#   - Add/manage managers from the management center
#   - Admin manager controls inside Settings
#   - Categorized/arrow-style management settings
#   - Language-correct AI error messages (handled above)
#
# Existing database is preserved. New manager records use management_roles only.

def _manager_is_owner(uid):
    return bool(uid and uid == master_owner_id())

def _manager_role_label(role, fa=True):
    labels = {
        "owner": ("👑 مالک", "👑 Owner"),
        "senior_manager": ("🛡 مدیر ارشد", "🛡 Senior Manager"),
        "general_manager": ("👤 مدیر عمومی", "👤 General Manager"),
        "technical_manager": ("🧰 مدیر فنی", "🧰 Technical Manager"),
        "finance_manager": ("💰 مدیر مالی", "💰 Finance Manager"),
        "ticket_manager": ("🎫 مدیر تیکت", "🎫 Ticket Manager"),
        "channel_manager": ("📢 مدیر کانال", "📢 Channel Manager"),
    }
    fa_label, en_label = labels.get(role, (role, role))
    return fa_label if fa else en_label

def _master_managers_text(uid):
    fa = lang(uid) == "fa"
    c = db()
    rows = c.execute(
        "SELECT user_id, role, domain, active, created_at "
        "FROM management_roles ORDER BY active DESC, user_id"
    ).fetchall()
    c.close()
    lines = [
        "🧑‍💼 <b>مدیریت مدیران</b>" if fa else "🧑‍💼 <b>Manager Management</b>",
        "",
    ]
    if not rows:
        lines.append("مدیری ثبت نشده است." if fa else "No managers are registered.")
    for r in rows:
        state = "🟢 فعال" if r["active"] else "🔴 غیرفعال"
        if not fa:
            state = "🟢 Active" if r["active"] else "🔴 Disabled"
        lines.append(
            f"{state}  <code>{r['user_id']}</code>  "
            f"{html.escape(_manager_role_label(r['role'], fa))}"
        )
    return "\n".join(lines)

def _master_manager_keyboard(uid):
    # Use the interactive manager directory defined below so every manager
    # appears as a selectable item with status, role and permissions.
    # The targeted callbacks also provide the disable confirmation (Yes/No)
    # and the per-manager permission controls.
    return _targeted_manager_keyboard(uid)

def _master_add_role_keyboard(uid):
    fa = lang(uid) == "fa"
    roles = [
        ("general_manager", "👤 مدیر عمومی", "👤 General Manager"),
        ("senior_manager", "🛡 مدیر ارشد", "🛡 Senior Manager"),
        ("technical_manager", "🧰 مدیر فنی", "🧰 Technical Manager"),
        ("finance_manager", "💰 مدیر مالی", "💰 Finance Manager"),
        ("ticket_manager", "🎫 مدیر تیکت", "🎫 Ticket Manager"),
        ("channel_manager", "📢 مدیر کانال", "📢 Channel Manager"),
    ]
    rows = []
    for role, fa_label, en_label in roles:
        rows.append([InlineKeyboardButton(
            fa_label if fa else en_label,
            callback_data=f"v25:master:manager_role:{role}"
        )])
    rows.append([InlineKeyboardButton(
        "⬅️ مدیریت مدیران" if fa else "⬅️ Manager Management",
        callback_data="v25:master:manager_list"
    )])
    return InlineKeyboardMarkup(rows)

def _master_settings_keyboard(uid):
    fa = lang(uid) == "fa"
    rows = [
        [InlineKeyboardButton(
            "🧑‍💼 مدیریت مدیران  ›" if fa else "🧑‍💼 Manager Management  ›",
            callback_data="settings:managers"
        )],
        [InlineKeyboardButton(
            "🤖 تنظیمات AI  ›" if fa else "🤖 AI Settings  ›",
            callback_data="settings:ai"
        )],
        [InlineKeyboardButton(
            "📢 تنظیمات کانال  ›" if fa else "📢 Channel Settings  ›",
            callback_data="settings:channel"
        )],
        [InlineKeyboardButton(
            "🔔 اعلان‌ها  ›" if fa else "🔔 Notifications  ›",
            callback_data="settings:notifications"
        )],
        [InlineKeyboardButton(
            "🎯 اهداف  ›" if fa else "🎯 Goals  ›",
            callback_data="settings:goals"
        )],
        [InlineKeyboardButton(
            "🌐 زبان  ›" if fa else "🌐 Language  ›",
            callback_data="settings:language"
        )],
        [InlineKeyboardButton(
            "🏠 منوی اصلی" if fa else "🏠 Main Menu",
            callback_data="settings:main"
        )],
    ]
    return InlineKeyboardMarkup(rows)

def _manager_settings_text(uid):
    fa = lang(uid) == "fa"
    if fa:
        return (
            "🛡️ <b>تنظیمات مدیریتی</b>\n\n"
            "از این بخش می‌توانی تنظیمات مدیریت را دسته‌بندی‌شده کنترل کنی.\n\n"
            "🧑‍💼 مدیریت مدیران › افزودن، مشاهده و کنترل نقش مدیران\n"
            "🤖 تنظیمات AI › وضعیت سرویس‌های هوشمند\n"
            "📢 تنظیمات کانال › اتصال و انتشار\n"
            "🔔 اعلان‌ها › تنظیمات اعلان‌های حساب"
        )
    return (
        "🛡️ <b>Management Settings</b>\n\n"
        "Use this categorized menu to manage administration settings.\n\n"
        "🧑‍💼 Manager Management › Add, view and control manager roles\n"
        "🤖 AI Settings › Smart service status\n"
        "📢 Channel Settings › Connection and publishing\n"
        "🔔 Notifications › Account notifications"
    )

# New manager access must not depend on the static ADMIN_IDS list.
_OLD_MASTER_GUARD_FINAL = master_guard
def master_guard(uid, permission=None):
    if uid == master_owner_id() and uid:
        return True if permission is None else master_has_permission(uid, permission)
    try:
        c = db()
        r = c.execute(
            "SELECT active FROM management_roles WHERE user_id=? LIMIT 1",
            (int(uid),)
        ).fetchone()
        c.close()
        if r and int(r["active"] or 0) == 1:
            return True if permission is None else master_has_permission(uid, permission)
    except Exception:
        pass
    return _OLD_MASTER_GUARD_FINAL(uid, permission)

# Settings callback wrapper: add the missing manager section and a categorized menu.
_OLD_SETTINGS_CALLBACK_MANAGER = settings_callback
async def settings_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    action = q.data.split(":", 1)[1] if ":" in q.data else ""
    fa = lang(uid) == "fa"

    if action == "admin":
        await q.answer()
        if not admin_is_allowed(uid):
            await q.message.edit_text(
                "⛔ دسترسی ندارید." if fa else "⛔ Access denied."
            )
            return
        await q.message.edit_text(
            _manager_settings_text(uid),
            parse_mode="HTML",
            reply_markup=_master_settings_keyboard(uid)
        )
        return

    if action == "managers":
        await q.answer()
        if not master_guard(uid, "manage_roles"):
            await q.message.edit_text(
                "⛔ این بخش فقط برای مدیر مجاز است."
                if fa else
                "⛔ This section is restricted to authorized managers."
            )
            return
        await q.message.edit_text(
            _master_managers_text(uid),
            parse_mode="HTML",
            reply_markup=_master_manager_keyboard(uid)
        )
        return

    return await _OLD_SETTINGS_CALLBACK_MANAGER(update, context)

# Settings keyboard: add a visible management category for admins.
_OLD_SETTINGS_KEYBOARD_MANAGER = settings_keyboard
def settings_keyboard(uid):
    base = _OLD_SETTINGS_KEYBOARD_MANAGER(uid)
    rows = [list(r) for r in base.inline_keyboard]
    fa = lang(uid) == "fa"
    # Keep management entry directly above the Main Menu row.
    main_index = len(rows) - 1
    if master_guard(uid, "manage_roles"):
        rows.insert(main_index, [InlineKeyboardButton(
            "🛡️ تنظیمات مدیریتی  ›" if fa else "🛡️ Management Settings  ›",
            callback_data="settings:admin"
        )])
    return InlineKeyboardMarkup(rows)

# Master callback wrapper for manager operations.
_OLD_MASTER_MANAGEMENT_CALLBACK_FINAL = master_management_callback
async def master_management_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    data = q.data or ""
    fa = lang(uid) == "fa"

    if not master_guard(uid):
        await q.answer(
            "⛔ دسترسی ندارید." if fa else "⛔ Access denied.",
            show_alert=True
        )
        return

    # Manager list.
    if data == "v25:master:manager_list":
        if not master_has_permission(uid, "manage_roles"):
            await q.answer("⛔ دسترسی ندارید.", show_alert=True)
            return
        await q.answer()
        await q.message.edit_text(
            _master_managers_text(uid),
            parse_mode="HTML",
            reply_markup=_master_manager_keyboard(uid)
        )
        return

    # Start add-manager flow. Owner only.
    if data == "v25:master:manager_add":
        if not _manager_is_owner(uid):
            await q.answer(
                "⛔ فقط Owner می‌تواند مدیر اضافه کند."
                if fa else
                "⛔ Only the Owner can add managers.",
                show_alert=True
            )
            return
        context.user_data.clear()
        context.user_data["_flow_started_at"] = datetime.now(TZ).timestamp()
        context.user_data["master_add_manager"] = True
        await q.answer()
        await q.message.edit_text(
            "🆔 آیدی عددی تلگرام مدیر جدید را ارسال کن.\n\n"
            "مثال: <code>123456789</code>"
            if fa else
            "🆔 Send the new manager's numeric Telegram ID.\n\n"
            "Example: <code>123456789</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "❌ لغو" if fa else "❌ Cancel",
                    callback_data="v25:master:manager_list"
                )
            ]])
        )
        return

    # Role selection after the ID is received.
    if data.startswith("v25:master:manager_role:"):
        if not _manager_is_owner(uid):
            await q.answer("⛔ Owner only.", show_alert=True)
            return
        role = data.split(":", 3)[3].strip()
        if role not in MASTER_RBAC_ROLES or role == "owner":
            await q.answer(
                "❌ نقش نامعتبر است." if fa else "❌ Invalid role.",
                show_alert=True
            )
            return
        target = int(context.user_data.get("master_pending_manager_id") or 0)
        if not target:
            await q.answer(
                "❌ آیدی مدیر پیدا نشد. دوباره شروع کن."
                if fa else
                "❌ Manager ID was not found. Please start again.",
                show_alert=True
            )
            return
        now = datetime.now(TZ).isoformat()
        c = db()
        c.execute(
            "INSERT INTO management_roles(user_id,role,domain,permissions_json,active,created_at,updated_at) "
            "VALUES(?,?,?,?,1,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET role=excluded.role,domain=excluded.domain,"
            "permissions_json=excluded.permissions_json,active=1,updated_at=excluded.updated_at",
            (
                target, role, "general",
                json.dumps(sorted(MASTER_ROLE_PERMISSIONS.get(role, set()))),
                now, now
            )
        )
        c.commit()
        c.close()
        master_log(uid, "manager_added", target, role)
        clear_flow(context)
        await q.answer()
        await q.message.edit_text(
            (
                f"✅ مدیر <code>{target}</code> با نقش "
                f"<b>{html.escape(_manager_role_label(role, True))}</b> اضافه شد."
            )
            if fa else
            (
                f"✅ Manager <code>{target}</code> added as "
                f"<b>{html.escape(_manager_role_label(role, False))}</b>."
            ),
            parse_mode="HTML",
            reply_markup=_master_manager_keyboard(uid)
        )
        return

    # Disable manager help: keep it deliberate to avoid accidental lockouts.
    if data == "v25:master:manager_disable_help":
        if not _manager_is_owner(uid):
            await q.answer("⛔ Owner only.", show_alert=True)
            return
        await q.answer()
        await q.message.edit_text(
            "🗑️ <b>غیرفعال‌سازی مدیر</b>\n\n"
            "برای جلوگیری از حذف اشتباهی، در این مرحله آیدی مدیر را به صورت پیام ارسال کن."
            if fa else
            "🗑️ <b>Disable Manager</b>\n\n"
            "To avoid accidental lockouts, send the manager's numeric ID as a message.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "⬅️ مدیریت مدیران" if fa else "⬅️ Manager Management",
                    callback_data="v25:master:manager_list"
                )
            ]])
        )
        context.user_data.clear()
        context.user_data["_flow_started_at"] = datetime.now(TZ).timestamp()
        context.user_data["master_disable_manager"] = True
        return

    return await _OLD_MASTER_MANAGEMENT_CALLBACK_FINAL(update, context)

# Text router wrapper for the add/disable manager flows.
_OLD_TEXT_ROUTER_MANAGER_FINAL = text_router
async def text_router(update, context):
    if not update.message or not update.message.text:
        return await _OLD_TEXT_ROUTER_MANAGER_FINAL(update, context)
    uid = update.effective_user.id
    txt = update.message.text.strip()
    fa = lang(uid) == "fa"

    if context.user_data.get("master_add_manager"):
        if not _manager_is_owner(uid):
            clear_flow(context)
            await update.message.reply_text(
                "⛔ فقط Owner می‌تواند مدیر اضافه کند."
                if fa else "⛔ Only the Owner can add managers."
            )
            return
        if not txt.isdigit():
            await update.message.reply_text(
                "❌ فقط آیدی عددی تلگرام را بفرست."
                if fa else "❌ Send a numeric Telegram ID."
            )
            return
        target = int(txt)
        context.user_data["master_add_manager"] = False
        context.user_data["master_pending_manager_id"] = target
        await update.message.reply_text(
            "🎯 نقش مدیر را انتخاب کن:"
            if fa else "🎯 Choose the manager role:",
            reply_markup=_master_add_role_keyboard(uid)
        )
        return

    if context.user_data.get("master_disable_manager"):
        if not _manager_is_owner(uid):
            clear_flow(context)
            await update.message.reply_text(
                "⛔ فقط Owner مجاز است." if fa else "⛔ Owner only."
            )
            return
        if not txt.isdigit():
            await update.message.reply_text(
                "❌ فقط آیدی عددی را بفرست."
                if fa else "❌ Send a numeric ID."
            )
            return
        target = int(txt)
        if target == master_owner_id():
            await update.message.reply_text(
                "❌ مالک اصلی قابل غیرفعال‌سازی نیست."
                if fa else "❌ The Owner cannot be disabled."
            )
            return
        c = db()
        c.execute(
            "UPDATE management_roles SET active=0, updated_at=? WHERE user_id=?",
            (datetime.now(TZ).isoformat(), target)
        )
        changed = c.rowcount
        c.commit()
        c.close()
        clear_flow(context)
        master_log(uid, "manager_disabled", target)
        await update.message.reply_text(
            (
                f"✅ مدیر <code>{target}</code> غیرفعال شد."
                if changed else
                f"ℹ️ مدیری با آیدی <code>{target}</code> پیدا نشد."
            )
            if fa else
            (
                f"✅ Manager <code>{target}</code> disabled."
                if changed else
                f"ℹ️ No manager found for <code>{target}</code>."
            ),
            parse_mode="HTML",
            reply_markup=keyboard(uid)
        )
        return

    return await _OLD_TEXT_ROUTER_MANAGER_FINAL(update, context)

# Final admin keyboard: make the manager section obvious.
_OLD_FINAL_ADMIN_KEYBOARD_MANAGERS = final_admin_keyboard
def final_admin_keyboard():
    base = _OLD_FINAL_ADMIN_KEYBOARD_MANAGERS().inline_keyboard
    rows = [list(r) for r in base]
    fa = True  # Existing admin keyboard is Persian-first; English is handled inside master UI.
    # Avoid duplicates if this patch is applied to an already patched source.
    if not any(
        any("مدیر" in getattr(btn, "text", "") for btn in row)
        for row in rows
    ):
        insert_at = max(0, len(rows) - 1)
        rows.insert(insert_at, [
            InlineKeyboardButton(
                "🧑‍💼 مدیریت مدیران",
                callback_data="v25:master:manager_list"
            )
        ])
    return InlineKeyboardMarkup(rows)

admin_keyboard = final_admin_keyboard



# ===================== MANAGER-SPECIFIC MAIN MENU =====================
# Managers must never receive the ordinary new-user main menu as their primary menu.
# They get a dedicated management keyboard, while retaining a clear entry to user features.

def _is_active_manager(uid):
    try:
        if not uid:
            return False
        if uid == master_owner_id():
            return True
        c = db()
        r = c.execute(
            "SELECT 1 FROM management_roles WHERE user_id=? AND active=1 LIMIT 1",
            (int(uid),)
        ).fetchone()
        c.close()
        return bool(r)
    except Exception:
        return uid in ADMIN_IDS if uid else False

def _manager_main_keyboard(uid):
    fa = lang(uid) == "fa"
    role = master_role(uid)
    role_label = _manager_role_label(role, fa)

    rows = [
        ["🛡 مدیریت ربات" if fa else "🛡 Bot Management", "📊 داشبورد و گزارش" if fa else "📊 Dashboard & Reports"],
        ["👥 کاربران و نقش‌ها" if fa else "👥 Users & Roles", "🎫 تیکت‌ها و Incident" if fa else "🎫 Tickets & Incidents"],
        ["🤖 مدیریت AI" if fa else "🤖 AI Management", "📢 کانال و انتشار" if fa else "📢 Channels & Publishing"],
        ["💰 مالی و پرداخت" if fa else "💰 Finance & Payments", "💎 VIP / XP / Token" if fa else "💎 VIP / XP / Token"],
        ["🩺 سلامت و Diagnostics" if fa else "🩺 Health & Diagnostics", "⚙️ تنظیمات سیستم" if fa else "⚙️ System Settings"],
        ["🧑‍💼 مدیریت مدیران" if fa else "🧑‍💼 Manager Management", "👤 استفاده از ربات" if fa else "👤 Use Bot"],
        ["🏠 منوی اصلی" if fa else "🏠 Main Menu"],
    ]

    # Respect RBAC: remove management entries the role cannot access.
    if not master_has_permission(uid, "manage_roles"):
        rows = [
            r for r in rows
            if not any(
                ("مدیریت مدیران" in str(x)) or ("Manager Management" in str(x))
                for x in r
            )
        ]

    # Keep the role visible as the first informational row.
    title = (
        f"🛡️ پنل مدیر\nنقش: <b>{html.escape(role_label)}</b>"
        if fa else
        f"🛡️ Manager Panel\nRole: <b>{html.escape(role_label)}</b>"
    )
    return title, ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)

async def _show_manager_main(update, context):
    uid = update.effective_user.id
    title, markup = _manager_main_keyboard(uid)
    await update.message.reply_text(title, parse_mode="HTML", reply_markup=markup)

# A manager is an admin even when not present in the legacy ADMIN_IDS environment variable.
_OLD_ADMIN_IS_ALLOWED_MANAGER_MENU = admin_is_allowed
def admin_is_allowed(uid):
    if _is_active_manager(uid):
        return True
    return _OLD_ADMIN_IS_ALLOWED_MANAGER_MENU(uid)

# Make the manager menu the actual primary keyboard for active managers.
_OLD_COMPACT_KEYBOARD_MANAGER_MENU = compact_keyboard
def compact_keyboard(uid):
    if _is_active_manager(uid):
        _, markup = _manager_main_keyboard(uid)
        return markup
    return _OLD_COMPACT_KEYBOARD_MANAGER_MENU(uid)

keyboard = compact_keyboard

# Manager-specific text routing. User area remains available through "Use Bot".
_OLD_TEXT_ROUTER_MANAGER_MENU = text_router
async def text_router(update, context):
    if not update.message or not update.message.text:
        return await _OLD_TEXT_ROUTER_MANAGER_MENU(update, context)

    uid = update.effective_user.id
    txt = update.message.text.strip()
    fa = lang(uid) == "fa"

    if _is_active_manager(uid):
        # Navigation buttons must always win over any pending text-input state
        # (for example waiting for an admin ID/username).  Otherwise a stale
        # manager-add/disable state can incorrectly consume normal menu taps.
        if txt in ("🏠 منوی اصلی", "🏠 Main Menu"):
            clear_flow(context)
            title, markup = _manager_main_keyboard(uid)
            await update.message.reply_text(title, parse_mode="HTML", reply_markup=markup)
            return

        if txt in ("👤 استفاده از ربات", "👤 Use Bot"):
            clear_flow(context)
            await update.message.reply_text(
                "👤 <b>بخش کاربر</b>\n\nقابلیت‌های عادی ربات در این بخش در دسترس است."
                if fa else
                "👤 <b>User Area</b>\n\nNormal user features are available here.",
                parse_mode="HTML",
                reply_markup=_compact_user_keyboard(uid)
            )
            return

        if txt in ("⚙️ تنظیمات سیستم", "⚙️ System Settings"):
            # Entering settings must also cancel any pending admin ID/username
            # input mode.  Show the same system-settings view as the management
            # center callback, without routing the label through the legacy
            # ID/username parser.
            clear_flow(context)
            paused = get_system_setting("bot_paused_until", "")
            maintenance = feature_enabled("maintenance")
            text = (
                f"⚙️ <b>تنظیمات سیستم</b>\n\n"
                f"🛠 Maintenance: {'🟢' if maintenance else '🔴'}\n"
                f"⏸ توقف موقت: {html.escape(paused or 'فعال نیست')}\n"
                f"🗄 Schema: {DB_SCHEMA_VERSION}\n\n"
                f"مالک اصلی: <code>{master_owner_id() or '-'}</code>"
            ) if fa else (
                f"⚙️ <b>System Settings</b>\n\n"
                f"🛠 Maintenance: {'🟢' if maintenance else '🔴'}\n"
                f"⏸ Temporary pause: {html.escape(paused or 'Not active')}\n"
                f"🗄 Schema: {DB_SCHEMA_VERSION}\n\n"
                f"Owner: <code>{master_owner_id() or '-'}</code>"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🧩 تغییر قابلیت‌ها" if fa else "🧩 Feature Flags", callback_data="adm:features")],
                [InlineKeyboardButton("⏸ مدیریت توقف" if fa else "⏸ Pause Management", callback_data="adm:pause")],
                [InlineKeyboardButton("⬅️ مرکز مدیریت" if fa else "⬅️ Management Center", callback_data="v25:master:home")]
            ])
            await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
            return

        if txt in ("🛡 مدیریت ربات", "🛡 Bot Management"):
            clear_flow(context)
            await _show_admin_management(update, context)
            return

        if txt in ("📊 داشبورد و گزارش", "📊 Dashboard & Reports"):
            await admin_command(update, context)
            return

        if txt in ("👥 کاربران و نقش‌ها", "👥 Users & Roles"):
            await update.message.reply_text(
                master_users_text(),
                parse_mode="HTML",
                reply_markup=master_back_keyboard(uid)
            )
            return

        if txt in ("🤖 مدیریت AI", "🤖 AI Management"):
            await update.message.reply_text(
                master_ai_text(),
                parse_mode="HTML",
                reply_markup=master_back_keyboard(uid)
            )
            return

        if txt in ("🧑‍💼 مدیریت مدیران", "🧑‍💼 Manager Management"):
            if not master_has_permission(uid, "manage_roles"):
                await update.message.reply_text(
                    "⛔ دسترسی ندارید." if fa else "⛔ Access denied.",
                    reply_markup=compact_keyboard(uid)
                )
                return
            await update.message.reply_text(
                _master_managers_text(uid),
                parse_mode="HTML",
                reply_markup=_master_manager_keyboard(uid)
            )
            return

    return await _OLD_TEXT_ROUTER_MANAGER_MENU(update, context)

# After assigning a manager, notify them with the exact manager-panel behavior.
_OLD_MASTER_MANAGEMENT_CALLBACK_MANAGER_MENU = master_management_callback
async def master_management_callback(update, context):
    q = update.callback_query
    data = q.data or ""
    if data.startswith("v25:master:manager_role:"):
        uid = q.from_user.id
        fa = lang(uid) == "fa"
        # Let the previous implementation persist the role first.
        result = await _OLD_MASTER_MANAGEMENT_CALLBACK_MANAGER_MENU(update, context)

        target = None
        try:
            # The previous handler clears user_data after successful insert, so
            # recover the target from the most recent audit row.
            c = db()
            r = c.execute(
                "SELECT target_user FROM admin_logs "
                "WHERE admin_id=? AND action='manager_added' "
                "ORDER BY id DESC LIMIT 1",
                (int(uid),)
            ).fetchone()
            c.close()
            target = int(r["target_user"]) if r and r["target_user"] else None
        except Exception:
            target = None

        if target:
            try:
                target_fa = lang(target) == "fa"
                role = master_role(target)
                title = (
                    f"🛡️ <b>پنل مدیریت برای شما فعال شد</b>\n\n"
                    f"نقش شما: <b>{html.escape(_manager_role_label(role, True))}</b>\n\n"
                    "از این به بعد با ورود به ربات، منوی مدیریتی اختصاصی خودت را می‌بینی."
                    if target_fa else
                    f"🛡️ <b>Your manager panel is active</b>\n\n"
                    f"Role: <b>{html.escape(_manager_role_label(role, False))}</b>\n\n"
                    "From now on, your bot entry will use the dedicated manager menu."
                )
                _, target_markup = _manager_main_keyboard(target)
                await context.bot.send_message(
                    target, title, parse_mode="HTML", reply_markup=target_markup
                )
            except Exception as exc:
                logger.info("Manager welcome message skipped: %s", exc)
        return result

    return await _OLD_MASTER_MANAGEMENT_CALLBACK_MANAGER_MENU(update, context)


# ===================== PERSISTENCE + DATA ISOLATION SAFETY LAYER =====================
# This layer is intentionally additive. It does NOT migrate existing records away,
# rename the database, or delete/recreate tables.
#
# Production rule:
#   - Keep DB_PATH stable (prefer a Railway Volume path or PostgreSQL in the future).
#   - Never use an ephemeral deployment directory for the live database.
#   - Every user-owned query must include user_id/owner_user_id.
#   - Admin access must be permission-gated; UI hiding is not a security boundary.

def persistent_db_health():
    """Return basic persistence information without exposing user data."""
    try:
        exists = os.path.exists(DB_PATH)
        size = os.path.getsize(DB_PATH) if exists else 0
        backup_exists = os.path.exists(DB_BACKUP_PATH)
        return {
            "path": DB_PATH,
            "exists": exists,
            "size": size,
            "backup_exists": backup_exists,
        }
    except Exception as exc:
        logger.exception("Persistent DB health check failed: %s", exc)
        return {"path": DB_PATH, "exists": False, "size": 0, "backup_exists": False}

def require_user_ownership(uid, owner_uid):
    """Hard authorization check for user-owned records."""
    try:
        return int(uid) == int(owner_uid)
    except (TypeError, ValueError):
        return False

def get_customer_for_owner(uid, customer_id):
    c = db()
    row = c.execute(
        "SELECT * FROM customers WHERE id=? AND owner_user_id=?",
        (int(customer_id), int(uid))
    ).fetchone()
    c.close()
    return row

def get_appointment_for_owner(uid, appointment_id):
    c = db()
    row = c.execute(
        "SELECT * FROM appointments WHERE id=? AND owner_user_id=?",
        (int(appointment_id), int(uid))
    ).fetchone()
    c.close()
    return row

def get_business_profile_for_owner(uid):
    c = db()
    row = c.execute(
        "SELECT * FROM business_profiles WHERE user_id=?",
        (int(uid),)
    ).fetchone()
    c.close()
    return row

def persistent_backup_now():
    """Create a backup on demand; never modifies/deletes the live DB."""
    return backup_database_snapshot(keep=10)

# Wrap init_db once more so every normal startup performs a safe backup after
# migrations. No destructive reset is introduced.
_ORIGINAL_INIT_DB_PERSISTENCE_SAFE = init_db
def init_db():
    _ORIGINAL_INIT_DB_PERSISTENCE_SAFE()
    try:
        backup_database()
        backup_database_snapshot(keep=10)
    except Exception:
        logger.exception("Post-init persistent backup failed")

# Keep the original keyboard and authorization logic intact. This layer only
# supplies hard ownership helpers for handlers that need them.


# ===================== FINAL TARGETED REPAIR LAYER =====================
# This layer is intentionally additive: it preserves the existing database,
# handlers and user data, while fixing only the requested manager, live-price,
# navigation and recurring-reminder behavior.

# ---------- Persistent username / price / reminder migrations ----------
_ORIGINAL_INIT_DB_TARGETED = init_db

def _targeted_schema_migrate():
    c = db()
    try:
        user_cols = {r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()}
        if "username" not in user_cols:
            c.execute("ALTER TABLE users ADD COLUMN username TEXT")
        role_cols = {r["name"] for r in c.execute("PRAGMA table_info(management_roles)").fetchall()}
        if "username" not in role_cols:
            c.execute("ALTER TABLE management_roles ADD COLUMN username TEXT")
        goal_cols = {r["name"] for r in c.execute("PRAGMA table_info(goals)").fetchall()}
        if "reminder_start_date" not in goal_cols:
            c.execute("ALTER TABLE goals ADD COLUMN reminder_start_date TEXT")
        if "reminder_end_date" not in goal_cols:
            c.execute("ALTER TABLE goals ADD COLUMN reminder_end_date TEXT")
        if "reminder_repeat" not in goal_cols:
            c.execute("ALTER TABLE goals ADD COLUMN reminder_repeat TEXT NOT NULL DEFAULT 'daily'")
        c.execute("""CREATE TABLE IF NOT EXISTS price_asset_settings(
            asset TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        )""")
        now=datetime.now(TZ).isoformat()
        for asset in ("usd","eur","gold18","coin","silver","copper","aluminum","nickel","zinc","lead"):
            c.execute("INSERT OR IGNORE INTO price_asset_settings(asset,enabled,updated_at) VALUES(?,1,?)",(asset,now))
        c.commit()
    finally:
        c.close()

def init_db():
    _ORIGINAL_INIT_DB_TARGETED()
    try:
        _targeted_schema_migrate()
    except Exception:
        logger.exception("Targeted schema migration failed")

# ---------- Username tracking ----------
def _targeted_record_username(user):
    if not user:
        return
    try:
        uid=int(user.id)
        uname=(getattr(user,"username",None) or "").strip().lstrip("@").lower() or None
        first=getattr(user,"first_name",None) or ""
        c=db()
        c.execute("UPDATE users SET username=?, first_name=COALESCE(NULLIF(?,''),first_name), last_active_at=? WHERE user_id=?",(uname,first,datetime.now(TZ).isoformat(),uid))
        c.execute("UPDATE management_roles SET username=?, updated_at=? WHERE user_id=? AND username IS NOT ?",(uname,datetime.now(TZ).isoformat(),uid,uname))
        c.commit(); c.close()
    except Exception:
        logger.exception("Username tracking failed")

# ---------- Manager authorization: disabled managers become ordinary users ----------
_OLD_TARGETED_ADMIN_ALLOWED = admin_is_allowed

def admin_is_allowed(uid):
    uid=int(uid or 0)
    try:
        if uid == master_owner_id() and uid:
            return True
        c=db(); row=c.execute("SELECT active FROM management_roles WHERE user_id=? LIMIT 1",(uid,)).fetchone(); c.close()
        if row is not None:
            return bool(int(row["active"] or 0))
    except Exception:
        pass
    return bool(uid in ADMIN_IDS)

# ---------- Manager list / permissions UI ----------
TARGETED_PERMISSION_LABELS = {
    "view_dashboard":"📊 داشبورد",
    "manage_users":"👥 کاربران",
    "manage_roles":"🧑‍💼 مدیریت مدیران",
    "manage_vip":"💎 VIP",
    "manage_xp":"⭐ XP / Token",
    "manage_tickets":"🎫 تیکت‌ها",
    "manage_finance":"💰 مالی",
    "manage_channels":"📢 کانال",
    "manage_ai":"🤖 هوش مصنوعی",
    "manage_features":"🧩 قابلیت‌ها",
    "run_health":"🩺 Health Check",
    "run_diagnostics":"🔎 عیب‌یابی",
    "backup":"💾 بکاپ",
    "restore":"♻️ بازیابی",
    "view_audit":"📝 لاگ مدیران",
    "run_tests":"🧪 تست‌ها",
    "manage_system":"⚙️ سیستم",
}

def _targeted_manager_rows():
    c=db()
    rows=c.execute("SELECT user_id,username,role,domain,permissions_json,active,created_at FROM management_roles ORDER BY active DESC,user_id").fetchall()
    c.close(); return rows

def _targeted_manager_text(uid):
    fa=lang(uid)=="fa"; rows=_targeted_manager_rows()
    lines=["🧑‍💼 <b>مدیریت مدیران</b>" if fa else "🧑‍💼 <b>Manager Management</b>",""]
    if not rows: lines.append("مدیری ثبت نشده است." if fa else "No managers registered.")
    for r in rows:
        uname=("@"+r["username"]) if r["username"] else "—"
        state="🟢 فعال" if r["active"] else "🔴 غیرفعال"
        if not fa: state="🟢 Active" if r["active"] else "🔴 Disabled"
        lines.append(f"{state}  {html.escape(uname)}  <code>{r['user_id']}</code>  {html.escape(_manager_role_label(r['role'],fa))}")
    return "\n".join(lines)

def _targeted_manager_keyboard(uid):
    fa=lang(uid)=="fa"; rows=[]
    if _manager_is_owner(uid):
        rows.append([InlineKeyboardButton("➕ افزودن مدیر" if fa else "➕ Add Manager",callback_data="v25:targeted:manager_add")])
        rows.append([InlineKeyboardButton("🗑️ لغو/حذف مدیر از لیست" if fa else "🗑️ Disable Manager",callback_data="v25:targeted:manager_disable_list")])
    for r in _targeted_manager_rows():
        uname=("@"+r["username"]) if r["username"] else str(r["user_id"])
        label=f"{'🟢' if r['active'] else '🔴'} {uname} | {r['user_id']}"
        rows.append([InlineKeyboardButton(label,callback_data=f"v25:targeted:manager_detail:{r['user_id']}")])
    rows.append([InlineKeyboardButton("🔄 به‌روزرسانی" if fa else "🔄 Refresh",callback_data="v25:targeted:manager_list")])
    rows.append([InlineKeyboardButton("⬅️ مرکز مدیریت" if fa else "⬅️ Management Center",callback_data="v25:master:home")])
    return InlineKeyboardMarkup(rows)

def _targeted_manager_detail(uid,target):
    c=db(); r=c.execute("SELECT user_id,username,role,permissions_json,active FROM management_roles WHERE user_id=?",(int(target),)).fetchone(); c.close()
    if not r: return "مدیر پیدا نشد.", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ مدیریت مدیران",callback_data="v25:targeted:manager_list")]])
    fa=lang(uid)=="fa"; uname=("@"+r["username"]) if r["username"] else "—"
    try: perms=set(json.loads(r["permissions_json"] or "[]"))
    except Exception: perms=set()
    lines=[f"🧑‍💼 <b>{html.escape(uname)}</b>",f"🆔 <code>{r['user_id']}</code>",f"🎭 {html.escape(_manager_role_label(r['role'],fa))}",f"{'🟢 فعال' if r['active'] else '🔴 غیرفعال'}" if fa else ("🟢 Active" if r['active'] else "🔴 Disabled"),"","🔐 دسترسی‌ها:" if fa else "🔐 Permissions:"]
    for key in MASTER_PERMISSION_KEYS:
        lines.append(("🟢 " if key in perms else "🔴 ")+TARGETED_PERMISSION_LABELS.get(key,key))
    rows=[]
    if _manager_is_owner(uid) and int(target)!=master_owner_id():
        rows.append([InlineKeyboardButton("🔄 فعال/غیرفعال مدیر" if fa else "🔄 Toggle Manager",callback_data=f"v25:targeted:manager_toggle:{target}")])
        rows.append([InlineKeyboardButton("🛡 تغییر نقش" if fa else "🛡 Change Role",callback_data=f"v25:targeted:manager_role:{target}")])
        rows.append([InlineKeyboardButton("🗑️ لغو مدیریت" if fa else "🗑️ Disable",callback_data=f"v25:targeted:manager_disable_confirm:{target}")])
    if _manager_is_owner(uid):
        rows.append([InlineKeyboardButton("🔐 مدیریت دسترسی‌ها" if fa else "🔐 Manage Permissions",callback_data=f"v25:targeted:manager_perms:{target}")])
    rows.append([InlineKeyboardButton("⬅️ مدیریت مدیران" if fa else "⬅️ Managers",callback_data="v25:targeted:manager_list")])
    return "\n".join(lines),InlineKeyboardMarkup(rows)

def _targeted_permissions_keyboard(uid,target):
    c=db(); r=c.execute("SELECT permissions_json FROM management_roles WHERE user_id=?",(int(target),)).fetchone(); c.close()
    if not r: return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ مدیریت مدیران",callback_data="v25:targeted:manager_list")]])
    try: perms=set(json.loads(r["permissions_json"] or "[]"))
    except Exception: perms=set()
    fa=lang(uid)=="fa"; rows=[]
    for key in MASTER_PERMISSION_KEYS:
        mark="🟢" if key in perms else "🔴"
        rows.append([InlineKeyboardButton(f"{mark} {TARGETED_PERMISSION_LABELS.get(key,key)}",callback_data=f"v25:targeted:manager_perm:{target}:{key}")])
    rows.append([InlineKeyboardButton("⬅️ پرونده مدیر" if fa else "⬅️ Manager",callback_data=f"v25:targeted:manager_detail:{target}")])
    return InlineKeyboardMarkup(rows)

# ---------- Add/disable manager by username OR numeric ID ----------
async def _targeted_resolve_manager_ref(context,bot,ref):
    ref=ref.strip();
    if ref.isdigit(): return int(ref), None
    uname=ref.lstrip("@").lower()
    if not uname or not re.fullmatch(r"[A-Za-z0-9_]{3,32}",uname): return None,None
    c=db(); r=c.execute("SELECT user_id,username FROM users WHERE lower(username)=? LIMIT 1",(uname,)).fetchone(); c.close()
    if r: return int(r["user_id"]),uname
    try:
        chat=await bot.get_chat("@"+uname)
        return int(chat.id),uname
    except Exception:
        return None,uname

# ---------- Live prices: online only, manager controls which assets appear ----------
def _targeted_enabled_prices():
    try:
        c=db(); rows=c.execute("SELECT asset FROM price_asset_settings WHERE enabled=1 ORDER BY rowid").fetchall(); c.close()
        return [r["asset"] for r in rows]
    except Exception:
        return ["usd","eur","gold18","coin","silver","copper","aluminum","nickel","zinc","lead"]

def _targeted_set_price_enabled(asset,enabled):
    c=db(); c.execute("INSERT INTO price_asset_settings(asset,enabled,updated_at) VALUES(?,?,?) ON CONFLICT(asset) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at",(asset,int(bool(enabled)),datetime.now(TZ).isoformat())); c.commit(); c.close()

def prices_keyboard(uid):
    fa=lang(uid)=="fa"
    all_labels=[('usd','💵 دلار','💵 USD'),('eur','💶 یورو','💶 EUR'),('gold18','🥇 طلای ۱۸ عیار','🥇 18K Gold'),('coin','🪙 سکه امامی','🪙 Emami Coin'),('silver','🥈 نقره','🥈 Silver'),('copper','🟠 مس','🟠 Copper'),('aluminum','⚙️ آلومینیوم','⚙️ Aluminum'),('nickel','🔩 نیکل','🔩 Nickel'),('zinc','🔘 روی','🔘 Zinc'),('lead','⛓️ سرب','⛓️ Lead')]
    enabled=set(_targeted_enabled_prices()); labels=[x for x in all_labels if x[0] in enabled]
    rows=[[InlineKeyboardButton((x[1] if fa else x[2]),callback_data=f"price:{x[0]}") for x in labels[i:i+2]] for i in range(0,len(labels),2)]
    rows.append([InlineKeyboardButton("🔄 بروزرسانی همه" if fa else "🔄 Refresh all",callback_data="price:all")])
    rows.append([InlineKeyboardButton("💰 سرمایه‌های من" if fa else "💰 My Portfolio",callback_data="v25:portfolio")])
    rows.append([InlineKeyboardButton("🏠 منوی اصلی" if fa else "🏠 Main Menu",callback_data="price:main")])
    return InlineKeyboardMarkup(rows)

_ORIGINAL_FETCH_PRICE_V25_TARGETED = fetch_price_v25
async def fetch_price_v25(asset):
    # Gold/coin use TGJU's explicit daily/current page. No manual correction is applied.
    if asset in ("gold18","coin"):
        url={"gold18":"https://www.tgju.org/profile/geram18/today","coin":"https://www.tgju.org/profile/sekee/today"}[asset]
        raw=await asyncio.to_thread(tgju_value,url)
        return float(raw.replace(",","").replace("٫",".").replace("٬","")),"ریال","single"
    return await _ORIGINAL_FETCH_PRICE_V25(asset)

async def v25_show_price(update,context,asset):
    uid=update.effective_user.id; fa=lang(uid)=="fa"; names={'usd':'دلار','eur':'یورو','gold18':'طلای ۱۸ عیار','coin':'سکه امامی','silver':'نقره','copper':'مس','aluminum':'آلومینیوم','nickel':'نیکل','zinc':'روی','lead':'سرب'}; names_en={'usd':'USD','eur':'EUR','gold18':'18K Gold','coin':'Emami Coin','silver':'Silver','copper':'Copper','aluminum':'Aluminum','nickel':'Nickel','zinc':'Zinc','lead':'Lead'}
    enabled=set(_targeted_enabled_prices())
    assets=_targeted_enabled_prices() if asset=='all' else [asset]
    if asset!='all' and asset not in enabled:
        if update.callback_query: await update.callback_query.answer("⛔ این قیمت فعلاً توسط مدیر غیرفعال است." if fa else "⛔ This price is disabled by the admin.",show_alert=True)
        return
    lines=[('📈 <b>قیمت آنلاین</b>' if fa else '📈 <b>Live Prices</b>'),'']
    for a in assets:
        try:
            val,unit,confidence=await fetch_price_v25(a); label=names[a] if fa else names_en[a]; lines.append(f"{label}: <b>{val:,.0f}</b> {unit}")
        except Exception:
            label=names[a] if fa else names_en[a]; lines.append(f"{label}: ⚠️ {'داده آنلاین در دسترس نیست' if fa else 'Live data unavailable'}")
    lines += ['',('🕐 آخرین بررسی: '+fa_datetime(datetime.now(TZ),True) if fa else '🕐 Checked: '+fa_datetime(datetime.now(TZ),True))]
    if update.callback_query: await update.callback_query.message.edit_text("\n".join(lines),parse_mode='HTML',reply_markup=prices_keyboard(uid))
    else: await update.message.reply_text("\n".join(lines),parse_mode='HTML',reply_markup=prices_keyboard(uid))

async def price_callback(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; asset=q.data.split(':',1)[1]
    if asset=='main':
        try: await q.message.edit_text("🏠 منوی اصلی",reply_markup=InlineKeyboardMarkup([[main_menu_button(uid)]]))
        except Exception: pass
        return
    await v25_show_price(update,context,asset)

# ---------- Recurring goal reminders ----------
def _targeted_repeat_keyboard(uid):
    fa=lang(uid)=="fa"
    labels=[("today_tomorrow","📅 امروز و فردا" if fa else "📅 Today + Tomorrow"),("tomorrow","➡️ فقط فردا" if fa else "➡️ Tomorrow only"),("week","📆 یک هفته" if fa else "📆 One week"),("month","🗓️ یک ماه" if fa else "🗓️ One month"),("two_months","🗓️ دو ماه" if fa else "🗓️ Two months"),("daily","🔁 روزانه تا پایان هدف" if fa else "🔁 Daily until goal ends")]
    rows=[[InlineKeyboardButton(t,callback_data=f"goalrepeat:{k}")] for k,t in labels]
    rows.append([InlineKeyboardButton("❌ بدون تکرار" if fa else "❌ No repeat",callback_data="goalrepeat:none")])
    return InlineKeyboardMarkup(rows)

def _targeted_apply_repeat(uid,gid,mode,base_date=None):
    today=datetime.now(TZ).date(); base=base_date or today
    if mode=='none': start=end=None; repeat='none'
    elif mode=='tomorrow': start=today+timedelta(days=1); end=start; repeat='once'
    elif mode=='today_tomorrow': start=today; end=today+timedelta(days=1); repeat='daily'
    elif mode=='week': start=today; end=today+timedelta(days=6); repeat='daily'
    elif mode=='month': start=today; end=today+timedelta(days=29); repeat='daily'
    elif mode=='two_months': start=today; end=today+timedelta(days=59); repeat='daily'
    else: start=today; end=None; repeat='daily'
    c=db(); c.execute("UPDATE goals SET reminder_start_date=?,reminder_end_date=?,reminder_repeat=? WHERE user_id=? AND id=?",(start.isoformat() if start else None,end.isoformat() if end else None,repeat,uid,gid)); c.commit(); c.close()

async def targeted_goalrepeat_callback(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; mode=q.data.split(':',1)[1]
    gid=int(context.user_data.get('pending_repeat_goal_id') or 0)
    if not gid or not get_goal(uid,gid): return
    _targeted_apply_repeat(uid,gid,mode)
    context.user_data.pop('pending_repeat_goal_id',None)
    g=get_goal(uid,gid); fa=lang(uid)=='fa'
    await q.message.edit_text((f"✅ هدف «{html.escape(g['name'])}» ثبت شد.\n⏰ ساعت: {g['reminder_time'] or 'خاموش'}\n🔁 تکرار یادآوری تنظیم شد." if fa else f"✅ Goal '{html.escape(g['name'])}' saved.\n⏰ Time: {g['reminder_time'] or 'Off'}\n🔁 Reminder repetition configured."),parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[main_menu_button(uid)]]))

async def time_callback(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; value=q.data.split(':',1)[1]
    if value=='custom':
        context.user_data['awaiting_custom_time']=True; await q.message.edit_text(T[lang(uid)]['custom_time']); return
    reminder=None if value=='none' else parse_time(value)
    name=context.user_data.get('name'); category=context.user_data.get('category')
    if not name or not category: return
    priority=context.user_data.get('priority',2); duration=context.user_data.get('duration_minutes')
    add_goal(uid,name,category,reminder,priority,duration)
    if reminder:
        c=db(); gid=c.execute("SELECT id FROM goals WHERE user_id=? ORDER BY id DESC LIMIT 1",(uid,)).fetchone()['id']; c.close(); context.user_data.clear(); context.user_data['pending_repeat_goal_id']=int(gid)
        await q.message.edit_text("🔁 چند روز/چه مدت یادآوری شود؟" if lang(uid)=='fa' else "🔁 How long should reminders repeat?",reply_markup=_targeted_repeat_keyboard(uid)); return
    context.user_data.clear(); log_activity(uid,'goal_created'); await q.message.edit_text(T[lang(uid)]['goal_added'].format(name=display_name(uid)),reply_markup=InlineKeyboardMarkup([[main_menu_button(uid)]]))

async def custom_time_save(update,context):
    uid=update.effective_user.id
    if not context.user_data.get('awaiting_custom_time'): return False
    reminder=parse_time(update.message.text.strip())
    if reminder is None: await update.message.reply_text(T[lang(uid)]['bad_time']); return True
    name=context.user_data.get('name'); category=context.user_data.get('category')
    if not name or not category: context.user_data.clear(); return False
    priority=context.user_data.get('priority',2); duration=context.user_data.get('duration_minutes'); add_goal(uid,name,category,reminder,priority,duration)
    c=db(); gid=c.execute("SELECT id FROM goals WHERE user_id=? ORDER BY id DESC LIMIT 1",(uid,)).fetchone()['id']; c.close(); context.user_data.clear(); context.user_data['pending_repeat_goal_id']=int(gid); context.user_data.pop('awaiting_custom_time',None)
    await update.message.reply_text("🔁 چند روز/چه مدت یادآوری شود؟" if lang(uid)=='fa' else "🔁 How long should reminders repeat?",reply_markup=_targeted_repeat_keyboard(uid)); return True

async def reminder_job(context):
    now=datetime.now(TZ); hhmm=now.strftime('%H:%M'); today=now.date().isoformat(); c=db()
    goals=c.execute("""SELECT g.* FROM goals g JOIN users u ON u.user_id=g.user_id WHERE g.enabled=1 AND COALESCE(u.blocked,0)=0 AND g.reminder_time=? AND (g.reminder_start_date IS NULL OR g.reminder_start_date<=?) AND (g.reminder_end_date IS NULL OR g.reminder_end_date>=?)""",(hhmm,today,today)).fetchall(); c.close()
    for g in goals:
        uid=g['user_id']
        try:
            sc=db(); rr=sc.execute("SELECT reminders_enabled FROM user_settings WHERE user_id=?",(uid,)).fetchone(); sc.close()
            if rr and not rr['reminders_enabled']: continue
            if get_status(uid,g['id'],today)=='done': continue
            await context.bot.send_message(uid,T[lang(uid)]['reminder'].format(name=display_name(uid),goal=g['name']),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ انجام دادم' if lang(uid)=='fa' else '✅ Done',callback_data=f"done:{g['id']}"),InlineKeyboardButton('❌ انجام ندادم' if lang(uid)=='fa' else '❌ Not done',callback_data=f"miss:{g['id']}")],[InlineKeyboardButton('⏰ یادآوری فردا' if lang(uid)=='fa' else '⏰ Tomorrow',callback_data=f"goalrem:{g['id']}:menu")]]))
            log_activity(uid,'reminder_sent')
        except Exception as e: logger.error('Reminder error: %s',e)

# ---------- Targeted manager callbacks ----------
_OLD_V25_CALLBACK_TARGETED = v25_callback
async def v25_callback(update,context):
    data=update.callback_query.data if update.callback_query else ''; uid=update.effective_user.id; q=update.callback_query
    if data.startswith('goalrepeat:'):
        return await targeted_goalrepeat_callback(update,context)
    if data.startswith('v25:targeted:'):
        fa=lang(uid)=='fa'
        if not master_guard(uid,'manage_roles'):
            await q.answer('⛔ دسترسی ندارید.',show_alert=True); return
        parts=data.split(':'); action=parts[2]
        if action=='manager_list':
            await q.answer(); await q.message.edit_text(_targeted_manager_text(uid),parse_mode='HTML',reply_markup=_targeted_manager_keyboard(uid)); return
        if action=='manager_add':
            if not _manager_is_owner(uid): await q.answer('⛔ Owner only.',show_alert=True); return
            context.user_data.clear(); context.user_data['targeted_add_manager']=True; await q.answer(); await q.message.edit_text('🆔 @username یا آیدی عددی مدیر جدید را بفرست.' if fa else '🆔 Send the new manager @username or numeric ID.'); return
        if action=='manager_disable_list':
            await q.answer(); rows=[]
            for r in _targeted_manager_rows():
                if int(r['user_id'])==master_owner_id() or not r['active']: continue
                label=(('@'+r['username']) if r['username'] else str(r['user_id']))
                rows.append([InlineKeyboardButton('🗑️ '+label,callback_data=f'v25:targeted:manager_disable_confirm:{r["user_id"]}')])
            rows.append([InlineKeyboardButton('⬅️ مدیریت مدیران',callback_data='v25:targeted:manager_list')]); await q.message.edit_text('مدیری را برای لغو مدیریت انتخاب کن:' if fa else 'Choose a manager to disable:',reply_markup=InlineKeyboardMarkup(rows)); return
        if action=='manager_disable_confirm':
            target=int(parts[3]); await q.answer(); await q.message.edit_text(f'⚠️ مدیریت این مدیر لغو شود؟\n🆔 <code>{target}</code>',parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ بله',callback_data=f'v25:targeted:manager_disable:{target}'),InlineKeyboardButton('❌ خیر',callback_data=f'v25:targeted:manager_detail:{target}')]])); return
        if action=='manager_disable':
            target=int(parts[3]);
            if target==master_owner_id(): await q.answer('❌ Owner قابل لغو نیست.',show_alert=True); return
            c=db(); c.execute('UPDATE management_roles SET active=0,updated_at=? WHERE user_id=?',(datetime.now(TZ).isoformat(),target)); c.commit(); c.close(); master_log(uid,'manager_disabled',target); await q.answer(); await q.message.edit_text(_targeted_manager_text(uid),parse_mode='HTML',reply_markup=_targeted_manager_keyboard(uid)); return
        if action=='manager_toggle':
            target=int(parts[3]);
            if target==master_owner_id(): await q.answer('Owner همیشه فعال است.',show_alert=True); return
            c=db(); c.execute('UPDATE management_roles SET active=CASE WHEN active=1 THEN 0 ELSE 1 END,updated_at=? WHERE user_id=?',(datetime.now(TZ).isoformat(),target)); c.commit(); c.close(); await q.answer(); text,kb=_targeted_manager_detail(uid,target); await q.message.edit_text(text,parse_mode='HTML',reply_markup=kb); return
        if action=='manager_detail':
            target=int(parts[3]); text,kb=_targeted_manager_detail(uid,target); await q.answer(); await q.message.edit_text(text,parse_mode='HTML',reply_markup=kb); return
        if action=='manager_perms':
            target=int(parts[3]); await q.answer(); await q.message.edit_text('🔐 دسترسی‌های مدیر را روشن/خاموش کن:',reply_markup=_targeted_permissions_keyboard(uid,target)); return
        if action=='manager_perm':
            target=int(parts[3]); perm=parts[4]
            if perm not in MASTER_PERMISSION_KEYS: await q.answer('Invalid permission',show_alert=True); return
            c=db(); r=c.execute('SELECT permissions_json FROM management_roles WHERE user_id=?',(target,)).fetchone();
            try: perms=set(json.loads(r['permissions_json'] or '[]')) if r else set()
            except Exception: perms=set()
            if perm in perms: perms.remove(perm)
            else: perms.add(perm)
            c.execute('UPDATE management_roles SET permissions_json=?,updated_at=? WHERE user_id=?',(json.dumps(sorted(perms)),datetime.now(TZ).isoformat(),target)); c.commit(); c.close(); await q.answer(); await q.message.edit_reply_markup(reply_markup=_targeted_permissions_keyboard(uid,target)); return
        return
    return await _OLD_V25_CALLBACK_TARGETED(update,context)

# ---------- Extend text router for username manager add and track usernames ----------
_OLD_TEXT_ROUTER_TARGETED = text_router
async def text_router(update,context):
    if update.message and update.effective_user:
        _targeted_record_username(update.effective_user)
    uid=update.effective_user.id if update.effective_user else 0; txt=(update.message.text or '').strip() if update.message else ''; fa=lang(uid)=='fa'
    if context.user_data.get('targeted_add_manager'):
        if not _manager_is_owner(uid): context.user_data.clear(); await update.message.reply_text('⛔ Owner only.'); return
        target,uname=await _targeted_resolve_manager_ref(context,context.bot,txt)
        if not target:
            await update.message.reply_text('❌ کاربر پیدا نشد. @username معتبر یا ID عددی بفرست.'); return
        context.user_data['targeted_add_manager']=False; context.user_data['targeted_pending_manager_id']=target; context.user_data['targeted_pending_manager_username']=uname
        await update.message.reply_text('🎭 نقش مدیر را انتخاب کن:',reply_markup=_master_add_role_keyboard(uid)); return
    # Fix legacy add-manager flow too: accept username and store it.
    if context.user_data.get('master_add_manager'):
        if not _manager_is_owner(uid): context.user_data.clear(); await update.message.reply_text('⛔ Owner only.'); return
        target,uname=await _targeted_resolve_manager_ref(context,context.bot,txt)
        if not target: await update.message.reply_text('❌ @username یا ID معتبر نیست.'); return
        context.user_data['master_add_manager']=False; context.user_data['master_pending_manager_id']=target; context.user_data['master_pending_manager_username']=uname
        await update.message.reply_text('🎭 نقش مدیر را انتخاب کن:',reply_markup=_master_add_role_keyboard(uid)); return
    # Legacy disable flow: accept username too.
    if context.user_data.get('master_disable_manager'):
        if not _manager_is_owner(uid): context.user_data.clear(); await update.message.reply_text('⛔ Owner only.'); return
        target,uname=await _targeted_resolve_manager_ref(context,context.bot,txt)
        if not target: await update.message.reply_text('❌ @username یا ID معتبر نیست.'); return
        if target==master_owner_id(): await update.message.reply_text('❌ Owner قابل لغو نیست.'); return
        c=db(); c.execute('UPDATE management_roles SET active=0,updated_at=? WHERE user_id=?',(datetime.now(TZ).isoformat(),target)); c.commit(); c.close(); clear_flow(context); await update.message.reply_text('✅ مدیریت این کاربر لغو شد.',reply_markup=keyboard(uid)); return
    return await _OLD_TEXT_ROUTER_TARGETED(update,context)

# When role is selected after targeted username add, persist username as well.
_OLD_MASTER_CALLBACK_ROLE_TARGETED=v25_callback
async def _targeted_role_bridge(update,context):
    return await _OLD_MASTER_CALLBACK_ROLE_TARGETED(update,context)

# Patch the existing role selection through a small DB hook by wrapping the current callback.
_ORIGINAL_TARGETED_ROLE_HANDLER = v25_callback
# The callback above delegates to the previous role handler; intercept its pending username
# after it runs and write the username to the resulting management row.
async def v25_callback(update,context):
    data=update.callback_query.data if update.callback_query else ''; uid=update.effective_user.id
    pending_id=context.user_data.get('targeted_pending_manager_id') or context.user_data.get('master_pending_manager_id')
    pending_uname=context.user_data.get('targeted_pending_manager_username') or context.user_data.get('master_pending_manager_username')
    if data.startswith('v25:master:manager_role:') and pending_id:
        result=await _ORIGINAL_TARGETED_ROLE_HANDLER(update,context)
        if pending_uname:
            c=db(); c.execute('UPDATE management_roles SET username=?,updated_at=? WHERE user_id=?',(pending_uname,datetime.now(TZ).isoformat(),int(pending_id))); c.commit(); c.close()
        return result
    return await _ORIGINAL_TARGETED_ROLE_HANDLER(update,context)

# ---------- Manager panel: add a live-price management category ----------
_ORIGINAL_MASTER_ROOT_KEYBOARD_TARGETED=master_root_keyboard
def master_root_keyboard(uid):
    kb=_ORIGINAL_MASTER_ROOT_KEYBOARD_TARGETED(uid); rows=[list(r) for r in kb.inline_keyboard]
    if master_has_permission(uid,'manage_features'):
        rows.insert(max(0,len(rows)-1),[InlineKeyboardButton('📈 مدیریت قیمت‌های آنلاین' if lang(uid)=='fa' else '📈 Live Price Management',callback_data='v25:targeted:prices')])
    return InlineKeyboardMarkup(rows)

# Extend the callback one final time for live-price settings.
_PREV_V25_CALLBACK_PRICE_PANEL=v25_callback
async def v25_callback(update,context):
    data=update.callback_query.data if update.callback_query else ''; uid=update.effective_user.id; q=update.callback_query
    if data=='v25:targeted:prices':
        if not master_has_permission(uid,'manage_features'): await q.answer('⛔ دسترسی ندارید.',show_alert=True); return
        rows=[]; c=db(); all_rows=c.execute('SELECT asset,enabled FROM price_asset_settings ORDER BY rowid').fetchall(); c.close(); labels={'usd':'دلار','eur':'یورو','gold18':'طلای ۱۸ عیار','coin':'سکه امامی','silver':'نقره','copper':'مس','aluminum':'آلومینیوم','nickel':'نیکل','zinc':'روی','lead':'سرب'}
        for r in all_rows:
            rows.append([InlineKeyboardButton(('🟢 ' if r['enabled'] else '🔴 ')+labels.get(r['asset'],r['asset']),callback_data=f"v25:targeted:price_toggle:{r['asset']}")])
        rows.append([InlineKeyboardButton('⬅️ مرکز مدیریت',callback_data='v25:master:home')]); await q.answer(); await q.message.edit_text('📈 <b>مدیریت قیمت‌های آنلاین</b>\n\nسبز = نمایش در ربات\nقرمز = مخفی از کاربران',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(rows)); return
    if data.startswith('v25:targeted:price_toggle:'):
        if not master_has_permission(uid,'manage_features'): await q.answer('⛔',show_alert=True); return
        asset=data.rsplit(':',1)[1]; c=db(); r=c.execute('SELECT enabled FROM price_asset_settings WHERE asset=?',(asset,)).fetchone(); new=0 if r and r['enabled'] else 1; c.execute('UPDATE price_asset_settings SET enabled=?,updated_at=? WHERE asset=?',(new,datetime.now(TZ).isoformat(),asset)); c.commit(); c.close(); await q.answer('روشن شد' if new else 'خاموش شد');
        rows=[]; c=db(); all_rows=c.execute('SELECT asset,enabled FROM price_asset_settings ORDER BY rowid').fetchall(); c.close(); labels={'usd':'دلار','eur':'یورو','gold18':'طلای ۱۸ عیار','coin':'سکه امامی','silver':'نقره','copper':'مس','aluminum':'آلومینیوم','nickel':'نیکل','zinc':'روی','lead':'سرب'}
        for rr in all_rows: rows.append([InlineKeyboardButton(('🟢 ' if rr['enabled'] else '🔴 ')+labels.get(rr['asset'],rr['asset']),callback_data=f"v25:targeted:price_toggle:{rr['asset']}")])
        rows.append([InlineKeyboardButton('⬅️ مرکز مدیریت',callback_data='v25:master:home')]); await q.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(rows)); return
    return await _PREV_V25_CALLBACK_PRICE_PANEL(update,context)


# Strict RBAC final override: a manager explicitly disabled in management_roles
# loses every management permission even if the legacy ADMIN_IDS variable still contains the ID.
def master_guard(uid, permission=None):
    uid=int(uid or 0)
    if uid == master_owner_id() and uid:
        return True if permission is None else master_has_permission(uid,permission)
    try:
        c=db(); row=c.execute("SELECT active FROM management_roles WHERE user_id=? LIMIT 1",(uid,)).fetchone(); c.close()
        if row is not None:
            if int(row["active"] or 0) != 1: return False
            return True if permission is None else master_has_permission(uid,permission)
    except Exception:
        return False
    if uid not in ADMIN_IDS: return False
    return True if permission is None else master_has_permission(uid,permission)

# Filter the manager home screen by the actual permissions, not just by role existence.
def _manager_main_keyboard(uid):
    """Compact manager home: preserve every label/order, change only layout."""
    fa = lang(uid) == "fa"
    role = master_role(uid)
    role_label = _manager_role_label(role, fa)

    specs = [
        ("manage_bot", "🛡 مدیریت ربات", "🛡 Bot Management"),
        ("view_dashboard", "📊 داشبورد و گزارش", "📊 Dashboard & Reports"),
        ("manage_users", "👥 کاربران و نقش‌ها", "👥 Users & Roles"),
        ("manage_tickets", "🎫 تیکت‌ها و Incident", "🎫 Tickets & Incidents"),
        ("manage_ai", "🤖 مدیریت AI", "🤖 AI Management"),
        ("manage_channels", "📢 کانال و انتشار", "📢 Channels & Publishing"),
        ("manage_finance", "💰 مالی و پرداخت", "💰 Finance & Payments"),
        ("manage_vip", "💎 VIP / XP / Token", "💎 VIP / XP / Token"),
        ("run_health", "🩺 سلامت و Diagnostics", "🩺 Health & Diagnostics"),
        ("manage_system", "⚙️ تنظیمات سیستم", "⚙️ System Settings"),
        ("manage_roles", "🧑‍💼 مدیریت مدیران", "🧑‍💼 Manager Management"),
        ("use_bot", "👤 استفاده از ربات", "👤 Use Bot"),
    ]

    labels = []
    for perm, fa_label, en_label in specs:
        if perm in ("manage_bot", "use_bot") or master_has_permission(uid, perm):
            labels.append(fa_label if fa else en_label)

    # Two buttons per row, like the requested compact reference layout.
    rows = [labels[i:i + 2] for i in range(0, len(labels), 2)]
    rows.append(["🏠 منوی اصلی" if fa else "🏠 Main Menu"])

    title = (
        f"🛡️ پنل مدیر\nنقش: <b>{html.escape(role_label)}</b>"
        if fa else
        f"🛡️ Manager Panel\nRole: <b>{html.escape(role_label)}</b>"
    )
    return title, ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)
# ===================== FINAL NAVIGATION / VALUEERROR REPAIR =====================
# This layer is intentionally last. It only repairs navigation presentation and
# protects the two manager/user entry buttons from stale legacy routing.
# Persistent data and existing business logic are unchanged.

async def _set_root_keyboard_silently(update, uid):
    """Do not emit a visible Main Menu message.

    Reply keyboards are persistent in Telegram. The current keyboard therefore
    stays visible after the user's navigation message is removed. Sending a new
    "🏠 منوی اصلی" carrier here was the source of the duplicate text bubbles.
    No bot message is created by this helper.
    No message is deleted here.
    No user data is changed here.
    No navigation state is changed here.
    The caller only uses this helper as a compatibility hook.
    This intentionally avoids creating a second bot bubble.
    The active ReplyKeyboard remains owned by the existing carrier message.
    Legacy callers can still invoke the helper safely.
    This is a navigation-only change; persistence and business logic are untouched.
    """
    return None


async def navigation_callback(update, context):
    """Return to the persistent root menu without leaving a visible Home message."""
    q = update.callback_query
    uid = q.from_user.id
    await q.answer()
    clear_flow(context)
    if (q.data or "") == "nav:main":
        # Delete the old inline screen FIRST, then send the message that carries
        # the persistent ReplyKeyboard. This prevents the keyboard carrier from
        # being removed/flickering on Telegram clients.
        try:
            await q.message.delete()
        except Exception:
            pass
        await _set_root_keyboard_silently(update, uid)
        return

# Keep the settings callback on the same message and never emit a duplicate
# "🏠 منوی اصلی" message when the user chooses Main Menu.
_OLD_SETTINGS_CALLBACK_FINAL_NAV_REPAIR = settings_callback
async def settings_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    action = q.data.split(":", 1)[1] if ":" in (q.data or "") else ""
    if action == "main":
        await q.answer()
        clear_flow(context)
        try:
            await q.message.delete()
        except Exception:
            try:
                await q.message.edit_text("‎")
            except Exception:
                pass
        return
    return await _OLD_SETTINGS_CALLBACK_FINAL_NAV_REPAIR(update, context)

# The manager's reply-keyboard labels must never fall through to old text-input
# parsers. This wrapper handles the two navigation labels explicitly first.
_OLD_TEXT_ROUTER_FINAL_NAV_REPAIR = text_router
async def text_router(update, context):
    if not update.message or not update.message.text:
        return await _OLD_TEXT_ROUTER_FINAL_NAV_REPAIR(update, context)
    txt = update.message.text.strip()
    uid = update.effective_user.id

    if txt in ("🏠 منوی اصلی", "🏠 Main Menu", "⬅️ برگشت", "⬅️ Back"):
        clear_flow(context)
        # The ReplyKeyboard is persistent. Remove only the user's navigation
        # command and do NOT send another visible "🏠 منوی اصلی" message.
        try:
            await update.message.delete()
        except Exception:
            pass
        return

    if txt in ("👤 استفاده از ربات", "👤 Use Bot"):
        clear_flow(context)
        fa = lang(uid) == "fa"
        await update.message.reply_text(
            "👤 <b>بخش کاربر</b>\n\nقابلیت‌های عادی ربات در این بخش در دسترس است."
            if fa else
            "👤 <b>User Area</b>\n\nNormal user features are available here.",
            parse_mode="HTML",
            reply_markup=_compact_user_keyboard(uid),
        )
        return

    if txt in ("⚙️ تنظیمات سیستم", "⚙️ System Settings"):
        clear_flow(context)
        if _is_active_manager(uid):
            fa = lang(uid) == "fa"
            paused = get_system_setting("bot_paused_until", "")
            maintenance = feature_enabled("maintenance")
            text = (
                f"⚙️ <b>تنظیمات سیستم</b>\n\n"
                f"🛠 Maintenance: {'🟢' if maintenance else '🔴'}\n"
                f"⏸ توقف موقت: {html.escape(paused or 'فعال نیست')}\n"
                f"🗄 Schema: {DB_SCHEMA_VERSION}\n\n"
                f"مالک اصلی: <code>{master_owner_id() or '-'}</code>"
            ) if fa else (
                f"⚙️ <b>System Settings</b>\n\n"
                f"🛠 Maintenance: {'🟢' if maintenance else '🔴'}\n"
                f"⏸ Temporary pause: {html.escape(paused or 'Not active')}\n"
                f"🗄 Schema: {DB_SCHEMA_VERSION}\n\n"
                f"Owner: <code>{master_owner_id() or '-'}</code>"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🧩 تغییر قابلیت‌ها" if fa else "🧩 Feature Flags", callback_data="adm:features")],
                [InlineKeyboardButton("⏸ مدیریت توقف" if fa else "⏸ Pause Management", callback_data="adm:pause")],
                [InlineKeyboardButton("⬅️ مرکز مدیریت" if fa else "⬅️ Management Center", callback_data="v25:master:home")],
            ])
            await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
            return

    return await _OLD_TEXT_ROUTER_FINAL_NAV_REPAIR(update, context)

# If a stale legacy handler still raises ValueError for one of the two top-level
# manager navigation buttons, recover directly instead of exposing the exception
# to the user. Other exceptions continue through the normal error handler.
_OLD_ERROR_HANDLER_FINAL_NAV_REPAIR = error_handler
async def error_handler(update, context):
    err = context.error
    uid = update.effective_user.id if update and update.effective_user else None
    txt = (update.message.text or "").strip() if update and update.message else ""
    if isinstance(err, ValueError) and uid and txt in (
        "👤 استفاده از ربات", "👤 Use Bot", "⚙️ تنظیمات سیستم", "⚙️ System Settings"
    ):
        try:
            clear_flow(context)
            if txt in ("👤 استفاده از ربات", "👤 Use Bot"):
                fa = lang(uid) == "fa"
                await update.message.reply_text(
                    "👤 <b>بخش کاربر</b>\n\nقابلیت‌های عادی ربات در این بخش در دسترس است."
                    if fa else
                    "👤 <b>User Area</b>\n\nNormal user features are available here.",
                    parse_mode="HTML", reply_markup=_compact_user_keyboard(uid)
                )
            else:
                fa = lang(uid) == "fa"
                await update.message.reply_text(
                    "⚙️ <b>تنظیمات سیستم</b>\n\nوضعیت تنظیمات سیستم را از این بخش مدیریت کن."
                    if fa else
                    "⚙️ <b>System Settings</b>\n\nManage the system settings from this section.",
                    parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🧩 تغییر قابلیت‌ها" if fa else "🧩 Feature Flags", callback_data="adm:features")],
                        [InlineKeyboardButton("⬅️ مرکز مدیریت" if fa else "⬅️ Management Center", callback_data="v25:master:home")],
                    ])
                )
            logger.warning("Recovered stale ValueError route for uid=%s text=%r", uid, txt)
            return
        except Exception:
            logger.exception("ValueError route recovery failed")
    return await _OLD_ERROR_HANDLER_FINAL_NAV_REPAIR(update, context)



# ===================== GOALS / REMINDERS / JALALI CALENDAR UPGRADE =====================
# Additive final layer. Existing user data, manager/RBAC logic and main-menu labels
# are preserved. This layer only extends the goals/reminders experience.

READY_CATALOG_FA = {
    "👤 شخصی": {
        "🩺 سلامت": [
            "🩺 وقت دکتر", "🧪 آزمایش دوره‌ای", "🩸 آزمایش کامل خون", "📋 چکاپ ماهانه", "📅 چکاپ سالانه",
            "🦷 دندانپزشکی", "👁️ معاینه چشم", "💊 خرید/تمدید دارو", "💉 واکسن"
        ],
        "🏠 خانه": [
            "🧹 نظافت خانه", "🔧 تعمیرات خانه", "❄️ سرویس کولر", "🔥 سرویس پکیج/بخاری", "💧 تعویض فیلتر آب",
            "🛒 خرید لوازم خانه", "📦 خرید ماهانه خانه"
        ],
        "👨‍👩‍👧 خانواده": [
            "☎️ تماس با خانواده", "❤️ وقت خانوادگی", "🎂 تولد", "💍 سالگرد", "🏫 پیگیری مدرسه/کلاس",
            "🩺 وقت دکتر عضو خانواده", "🎁 خرید هدیه"
        ],
        "✈️ سفر": [
            "✈️ سفر کاری", "🏖 سفر تفریحی", "👨‍👩‍👧 سفر خانوادگی", "🌍 سفر خارجی", "🎒 آماده‌سازی سفر"
        ],
        "📌 شخصی و اداری": [
            "📝 کار شخصی", "🏢 کار اداری", "📞 تماس مهم", "🤝 قرار", "🎯 پیگیری یک کار", "⭐ کار مهم"
        ],
    },
    "💰 مالی": {
        "🧾 چک و سررسید": ["🧾 چک پرداختی", "🧾 چک دریافتی", "📅 سررسید چک", "💵 طلب", "💳 بدهی"],
        "💳 پرداخت‌ها": ["💳 قسط", "🏦 وام", "🏠 اجاره", "💡 قبض", "🛡️ بیمه", "🧮 مالیات", "🎓 شهریه"],
        "🔄 دوره‌ای": ["🔄 پرداخت ماهانه", "🔄 پرداخت سالانه", "📺 تمدید اشتراک", "🌐 تمدید سرویس آنلاین"],
        "📊 مدیریت مالی": ["📒 ثبت هزینه", "🏦 بررسی حساب", "💰 پس‌انداز", "📈 بررسی هزینه ماهانه"],
    },
    "🚗 خودرو": {
        "🛢 سرویس": ["🛢 تعویض روغن", "🔧 سرویس دوره‌ای", "🧰 تعمیر خودرو", "🔩 تعویض شمع", "〰️ تعویض تسمه"],
        "🛑 ترمز و لاستیک": ["🛑 تعویض لنت", "🔍 بررسی ترمز", "🛞 تعویض لاستیک", "💨 تنظیم باد", "🔄 جابه‌جایی لاستیک"],
        "💧 مایعات و فیلترها": ["💧 ضدیخ/آب رادیاتور", "🧴 روغن ترمز", "🌬 فیلتر هوا", "❄️ فیلتر کابین", "🛢 فیلتر روغن"],
        "📄 مدارک و نگهداری": ["📄 معاینه فنی", "🛡️ تمدید بیمه خودرو", "🔋 بررسی باتری", "❄️ سرویس کولر", "🧽 کارواش"],
    },
    "💼 کار": {
        "📅 برنامه": ["📅 جلسه", "📋 وظیفه کاری", "🎯 پروژه", "📊 گزارش روزانه", "📆 گزارش هفتگی", "🗓 گزارش ماهانه"],
        "👥 مشتری": ["📞 تماس با مشتری", "🔔 پیگیری مشتری", "🤝 قرار با مشتری", "📨 ارسال پیام", "🧾 ارسال فاکتور"],
        "📄 قرارداد و مالی": ["📄 قرارداد", "🔄 تمدید قرارداد", "💵 پیگیری پرداخت", "🧾 پیگیری فاکتور"],
    },
    "📚 تحصیل": {
        "📖 مطالعه": ["📖 مطالعه", "🔁 مرور درس", "🔤 یادگیری لغت", "📝 جزوه"],
        "🎓 دانشگاه/مدرسه": ["🏫 کلاس", "📝 امتحان", "📋 تکلیف", "💻 پروژه", "🎓 ثبت‌نام", "💳 شهریه"],
    },
    "🏋️ ورزش": {
        "🏋️ تمرین": ["🏋️ بدنسازی", "🏃 دویدن", "🚶 پیاده‌روی", "🏠 ورزش خانگی", "🧘 یوگا", "🤸 کشش و نرمش"],
        "⚽ ورزش‌های گروهی": ["⚽ فوتبال", "🥅 فوتسال", "🏐 والیبال", "🏀 بسکتبال", "🎾 تنیس"],
        "🏊 فضای باز": ["🏊 شنا", "🚴 دوچرخه‌سواری", "🥾 کوهنوردی"],
    },
    "📄 مدارک": {
        "🪪 شناسایی": ["🪪 گواهینامه", "🛂 پاسپورت", "💳 کارت بانکی"],
        "🔄 تمدیدها": ["🛡️ تمدید بیمه", "📄 تمدید مجوز", "📝 تمدید قرارداد", "🌐 تمدید دامنه", "💻 تمدید هاست"],
        "🏢 اداری": ["🏢 مراجعه اداری", "📑 تکمیل مدرک", "📬 پیگیری پرونده"],
    },
    "📌 سایر": {
        "💻 فناوری": ["💻 بکاپ اطلاعات", "🔐 بررسی امنیت حساب", "📱 تعمیر موبایل", "🖥 تعمیر کامپیوتر", "🔄 به‌روزرسانی نرم‌افزار"],
        "🐾 حیوانات": ["🐾 دامپزشک", "💉 واکسن حیوان", "💊 دارو", "🪱 ضدانگل", "🛁 حمام", "✂️ اصلاح", "🍖 خرید غذا"],
        "🛒 خرید": ["🛒 خرید روزانه", "🛒 خرید هفتگی", "🛒 خرید ماهانه", "🎁 خرید هدیه", "🧴 خرید لوازم مصرفی"],
        "🗒 متفرقه": ["🗒 کار متفرقه", "🔔 یادآوری سفارشی", "📌 پیگیری", "⏳ کار عقب‌افتاده"],
    },
}
READY_CATALOG_EN = {
    "👤 Personal": {"🩺 Health":["🩺 Doctor appointment","🧪 Periodic lab test","🩸 Full blood test","📋 Monthly checkup","📅 Annual checkup","🦷 Dentist","👁️ Eye exam","💊 Medication refill","💉 Vaccine"],"🏠 Home":["🧹 House cleaning","🔧 Home repair","❄️ AC service","🔥 Heater/boiler service","💧 Water filter change","🛒 Home supplies","📦 Monthly home shopping"],"👨‍👩‍👧 Family":["☎️ Call family","❤️ Family time","🎂 Birthday","💍 Anniversary","🏫 School/class follow-up","🩺 Family doctor appointment","🎁 Buy a gift"],"✈️ Travel":["✈️ Business trip","🏖 Vacation","👨‍👩‍👧 Family trip","🌍 International trip","🎒 Prepare for travel"],"📌 Personal & Admin":["📝 Personal task","🏢 Administrative task","📞 Important call","🤝 Appointment","🎯 Follow-up","⭐ Important task"]},
    "💰 Finance": {"🧾 Checks & Due Dates":["🧾 Pay a check","🧾 Receive a check","📅 Check due date","💵 Receivable","💳 Debt"],"💳 Payments":["💳 Installment","🏦 Loan","🏠 Rent","💡 Bill","🛡️ Insurance","🧮 Tax","🎓 Tuition"],"🔄 Recurring":["🔄 Monthly payment","🔄 Annual payment","📺 Subscription renewal","🌐 Online service renewal"],"📊 Finance Management":["📒 Record expense","🏦 Check bank account","💰 Save money","📈 Review monthly expenses"]},
    "🚗 Car": {"🛢 Service":["🛢 Oil change","🔧 Periodic service","🧰 Car repair","🔩 Spark plug change","〰️ Belt change"],"🛑 Brakes & Tires":["🛑 Brake pad change","🔍 Brake check","🛞 Tire change","💨 Tire pressure","🔄 Tire rotation"],"💧 Fluids & Filters":["💧 Coolant","🧴 Brake fluid","🌬 Air filter","❄️ Cabin filter","🛢 Oil filter"],"📄 Documents & Care":["📄 Inspection","🛡️ Car insurance renewal","🔋 Battery check","❄️ AC service","🧽 Car wash"]},
    "💼 Work": {"📅 Planning":["📅 Meeting","📋 Work task","🎯 Project","📊 Daily report","📆 Weekly report","🗓 Monthly report"],"👥 Customers":["📞 Call customer","🔔 Follow up customer","🤝 Customer appointment","📨 Send message","🧾 Send invoice"],"📄 Contract & Finance":["📄 Contract","🔄 Renew contract","💵 Payment follow-up","🧾 Invoice follow-up"]},
    "📚 Study": {"📖 Study":["📖 Study","🔁 Review","🔤 Learn vocabulary","📝 Notes"],"🎓 School/University":["🏫 Class","📝 Exam","📋 Homework","💻 Project","🎓 Registration","💳 Tuition"]},
    "🏋️ Fitness": {"🏋️ Training":["🏋️ Gym","🏃 Running","🚶 Walking","🏠 Home workout","🧘 Yoga","🤸 Stretching"],"⚽ Team Sports":["⚽ Football","🥅 Futsal","🏐 Volleyball","🏀 Basketball","🎾 Tennis"],"🏊 Outdoor":["🏊 Swimming","🚴 Cycling","🥾 Hiking"]},
    "📄 Documents": {"🪪 Identity":["🪪 Driver license","🛂 Passport","💳 Bank card"],"🔄 Renewals":["🛡️ Insurance renewal","📄 Permit renewal","📝 Contract renewal","🌐 Domain renewal","💻 Hosting renewal"],"🏢 Admin":["🏢 Office visit","📑 Complete document","📬 Case follow-up"]},
    "📌 Other": {"💻 Technology":["💻 Backup data","🔐 Account security check","📱 Phone repair","🖥 Computer repair","🔄 Software update"],"🐾 Pets":["🐾 Vet","💉 Pet vaccine","💊 Medication","🪱 Deworming","🛁 Bath","✂️ Grooming","🍖 Pet food"],"🛒 Shopping":["🛒 Daily shopping","🛒 Weekly shopping","🛒 Monthly shopping","🎁 Gift shopping","🧴 Supplies"],"🗒 Misc":["🗒 Misc task","🔔 Custom reminder","📌 Follow-up","⏳ Overdue task"]},
}

GOALS_FA = READY_CATALOG_FA
GOALS_EN = READY_CATALOG_EN

# Extra persistent per-goal data. Existing goals/goal_days rows are never removed by this layer.
_ORIGINAL_INIT_DB_GOALS_UPGRADE = init_db

def _goals_upgrade_schema():
    c=db()
    try:
        c.execute("""CREATE TABLE IF NOT EXISTS goal_metadata(
            user_id INTEGER NOT NULL, goal_id INTEGER NOT NULL, meta_key TEXT NOT NULL,
            meta_value TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
            PRIMARY KEY(user_id,goal_id,meta_key))""")
        c.execute("""CREATE TABLE IF NOT EXISTS goal_completion_notice(
            user_id INTEGER NOT NULL, goal_id INTEGER NOT NULL, completed_at TEXT NOT NULL,
            PRIMARY KEY(user_id,goal_id))""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_goal_metadata_user_goal ON goal_metadata(user_id,goal_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_goals_user_enabled_end ON goals(user_id,enabled,reminder_end_date)")
        c.commit()
    finally:
        c.close()

def init_db():
    _ORIGINAL_INIT_DB_GOALS_UPGRADE()
    _goals_upgrade_schema()


def _goal_catalog_data(uid):
    return GOALS_EN if lang(uid)=="en" else GOALS_FA

def _goal_top_keys(uid):
    return list(_goal_catalog_data(uid).keys())

def _goal_store_meta(uid,gid,key,value):
    c=db(); c.execute("INSERT INTO goal_metadata(user_id,goal_id,meta_key,meta_value,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id,goal_id,meta_key) DO UPDATE SET meta_value=excluded.meta_value,updated_at=excluded.updated_at",(uid,gid,key,str(value),datetime.now(TZ).isoformat())); c.commit(); c.close()

def _goal_meta(uid,gid):
    c=db(); rows=c.execute("SELECT meta_key,meta_value FROM goal_metadata WHERE user_id=? AND goal_id=?",(uid,gid)).fetchall(); c.close(); return {r['meta_key']:r['meta_value'] for r in rows}

def _active_goals(uid):
    c=db(); rows=c.execute("SELECT * FROM goals WHERE user_id=? AND enabled=1 AND (reminder_end_date IS NULL OR reminder_end_date>=?) ORDER BY id DESC",(uid,datetime.now(TZ).date().isoformat())).fetchall(); c.close(); return rows

# Compact top-level categories: two columns, no long vertical scrolling.
def categories_keyboard(uid, prefix="newcat"):
    keys=_goal_top_keys(uid); rows=[]
    for i in range(0,len(keys),2):
        rows.append([InlineKeyboardButton(keys[j],callback_data=f"{prefix}:{j}") for j in range(i,min(i+2,len(keys)))])
    rows.append([InlineKeyboardButton("🏠 منوی اصلی" if lang(uid)=="fa" else "🏠 Main Menu",callback_data="goals:main")])
    return InlineKeyboardMarkup(rows)

def category_by_index(uid,index):
    return _goal_top_keys(uid)[index]

def goals_by_category(uid,category):
    data=_goal_catalog_data(uid); value=data[category]
    if isinstance(value,dict):
        out=[]
        for subitems in value.values(): out.extend(subitems)
        return out
    return value

async def new_category(update, context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id
    category=category_by_index(uid,int(q.data.split(':')[1])); context.user_data['ready_category']=category
    data=_goal_catalog_data(uid)[category]
    if isinstance(data,dict):
        keys=list(data.keys()); rows=[]
        for i in range(0,len(keys),2):
            rows.append([InlineKeyboardButton(keys[j],callback_data=f"readysub:{i+j-i}:{j}") for j in range(i,min(i+2,len(keys)))])
        # callback uses category index + sub index for stable routing
        rows=[]
        catidx=_goal_top_keys(uid).index(category)
        for i in range(0,len(keys),2): rows.append([InlineKeyboardButton(keys[j],callback_data=f"readysub:{catidx}:{j}") for j in range(i,min(i+2,len(keys)))])
        rows.append([InlineKeyboardButton("⬅️ برگشت" if lang(uid)=="fa" else "⬅️ Back",callback_data="newback")])
        await q.message.edit_text("📂 یک زیرگروه را انتخاب کن:" if lang(uid)=="fa" else "📂 Choose a subgroup:",reply_markup=InlineKeyboardMarkup(rows)); return
    await _show_ready_goals(update,context,category)

async def ready_subcategory_callback(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id
    parts = _safe_cb_parts(q.data)
    if not parts: return
    _,catidx,subidx=parts; category=category_by_index(uid,int(catidx)); sub=list(_goal_catalog_data(uid)[category].keys())[int(subidx)]
    context.user_data['ready_category']=category; context.user_data['ready_subcategory']=sub
    goals=_goal_catalog_data(uid)[category][sub]
    rows=[]
    for i in range(0,len(goals),2): rows.append([InlineKeyboardButton(goals[j],callback_data=f"newgoal:{j}") for j in range(i,min(i+2,len(goals)))])
    rows.append([InlineKeyboardButton("⬅️ برگشت" if lang(uid)=="fa" else "⬅️ Back",callback_data=f"newcat:{catidx}")])
    await q.message.edit_text(T[lang(uid)]["choose_goal"],reply_markup=InlineKeyboardMarkup(rows))

async def _show_ready_goals(update,context,category):
    uid=update.effective_user.id; goals=goals_by_category(uid,category); rows=[]
    for i in range(0,len(goals),2): rows.append([InlineKeyboardButton(goals[j],callback_data=f"newgoal:{j}") for j in range(i,min(i+2,len(goals)))])
    rows.append([InlineKeyboardButton("⬅️ برگشت" if lang(uid)=="fa" else "⬅️ Back",callback_data="newback")])
    await update.callback_query.message.edit_text(T[lang(uid)]["choose_goal"],reply_markup=InlineKeyboardMarkup(rows))


def _ready_template(name,category):
    n=name.lower()
    if 'تعویض روغن' in name or 'oil change' in n:
        return [('last_date','📅 تاریخ تعویض قبلی را بفرست:'),('last_km','🚗 کیلومتر خودرو در زمان تعویض:'),('next_km','🔢 کیلومتر سرویس بعدی:'),('next_date','📅 تاریخ تقریبی سرویس بعدی:'),('oil_type','🛢 نوع/ویسکوزیته روغن:'),('shop','🔧 تعمیرگاه یا شخص انجام‌دهنده (اختیاری):')]
    if 'چک' in name and ('پرداختی' in name or 'دریافتی' in name):
        return [('amount','💰 مبلغ چک:'),('check_date','📅 تاریخ سررسید:'),('counterparty','👤 نام شخص/شرکت:'),('bank','🏦 بانک:'),('check_no','🔢 شماره چک (اختیاری):')]
    if 'آزمایش' in name or 'چکاپ' in name or 'دکتر' in name or 'doctor' in n or 'checkup' in n or 'blood' in n:
        return [('last_date','📅 تاریخ مراجعه/آزمایش قبلی:'),('next_date','📅 تاریخ بعدی:'),('doctor','👨‍⚕️ پزشک/مرکز (اختیاری):'),('note','📝 توضیحات (اختیاری):')]
    if 'سفر' in name or 'trip' in n or 'vacation' in n:
        return [('destination','📍 مقصد:'),('depart_date','📅 تاریخ حرکت:'),('depart_time','⏰ ساعت حرکت:'),('return_date','📅 تاریخ برگشت:'),('note','📝 توضیحات (اختیاری):')]
    if 'بدنسازی' in name or 'gym' in n or 'دویدن' in name or 'running' in n or 'فوتبال' in name or 'football' in n or 'والیبال' in name or 'volleyball' in n or 'بسکتبال' in name or 'basketball' in n or 'شنا' in name or 'swimming' in n:
        return [('start_date','📅 تاریخ شروع برنامه:'),('location','📍 محل تمرین (اختیاری):'),('duration','⏱ مدت تمرین به دقیقه:')]
    return [('note','📝 توضیحات این هدف (اختیاری):')]

async def new_goal_pick(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id
    category=context.user_data.get('ready_category')
    if not category: return
    data=_goal_catalog_data(uid)[category]
    if isinstance(data,dict):
        sub=context.user_data.get('ready_subcategory')
        if not sub: return
        goals=data[sub]
    else: goals=data
    name=goals[int(q.data.split(':')[1])]
    context.user_data['name']=name; context.user_data['category']=category
    fields=_ready_template(name,category)
    context.user_data['ready_detail_fields']=fields; context.user_data['ready_detail_index']=0
    key,prompt=fields[0]; context.user_data['ready_detail_key']=key; context.user_data['ready_detail_mode']=True
    await q.message.edit_text(prompt+"\n\n(برای مورد اختیاری می‌توانی «-» بفرستی.)" if lang(uid)=='fa' else prompt+"\n\n(For optional fields, send '-'.)")

async def ready_detail_text_save(update,context):
    if not context.user_data.get('ready_detail_mode'): return False
    uid=update.effective_user.id; text=(update.message.text or '').strip(); fields=context.user_data.get('ready_detail_fields') or []; idx=int(context.user_data.get('ready_detail_index',0)); key=context.user_data.get('ready_detail_key')
    if not key or idx>=len(fields): return False
    _pending={k:v for k,v in context.user_data.get('ready_details',[])}
    _pending[key]='' if text=='-' else text
    context.user_data['ready_details']=list(_pending.items()); idx+=1
    if idx < len(fields):
        context.user_data['ready_detail_index']=idx; nk,np=fields[idx]; context.user_data['ready_detail_key']=nk
        await update.message.reply_text(np+"\n\n(برای مورد اختیاری می‌توانی «-» بفرستی.)" if lang(uid)=='fa' else np+"\n\n(For optional fields, send '-'.)")
        return True
    context.user_data.pop('ready_detail_mode',None); context.user_data.pop('ready_detail_fields',None); context.user_data.pop('ready_detail_key',None); context.user_data.pop('ready_detail_index',None)
    await update.message.reply_text("⭐ اولویت هدف را انتخاب کن:" if lang(uid)=='fa' else "⭐ Choose goal priority:",reply_markup=priority_keyboard(uid))
    return True

# Replace the existing generic goal list with an active-only list while preserving history in DB.
async def today(update,context):
    uid=update.effective_user.id; goals=_active_goals(uid); fa=lang(uid)=='fa'; log_activity(uid,'view_today')
    if not goals:
        await update.message.reply_text(T[lang(uid)]['no_goals'].format(name=display_name(uid)),reply_markup=keyboard(uid)); return
    rows=[]
    for g in goals:
        st=get_status(uid,g['id']); icon='✅' if st=='done' else '❌' if st=='missed' else '⬜'; rows.append([InlineKeyboardButton(f"{icon} {g['name']}",callback_data=f"detail:{g['id']}")])
    rows.append([InlineKeyboardButton('🔔 یادآوری‌های من' if fa else '🔔 My Reminders',callback_data='goalreminders'),InlineKeyboardButton('📅 تقویم' if fa else '📅 Calendar',callback_data='goalcalendar:today')])
    rows.append([main_menu_button(uid)])
    await update.message.reply_text(T[lang(uid)]['today'],reply_markup=InlineKeyboardMarkup(rows))

async def edit_menu(update,context):
    uid=update.effective_user.id; goals=_active_goals(uid); fa=lang(uid)=='fa'
    if not goals:
        await update.message.reply_text(T[lang(uid)]['no_goals'].format(name=display_name(uid)),reply_markup=keyboard(uid)); return
    rows=[]
    for i in range(0,len(goals),2): rows.append([InlineKeyboardButton(goals[j]['name'],callback_data=f"edit:{goals[j]['id']}") for j in range(i,min(i+2,len(goals)))])
    rows.append([InlineKeyboardButton('🔔 یادآوری‌های من' if fa else '🔔 My Reminders',callback_data='goalreminders'),InlineKeyboardButton('📅 تقویم' if fa else '📅 Calendar',callback_data='goalcalendar:today')])
    rows.append([main_menu_button(uid)])
    await update.message.reply_text(T[lang(uid)]['edit'].format(name=display_name(uid)),reply_markup=InlineKeyboardMarkup(rows))

async def goal_reminders_list(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; fa=lang(uid)=='fa'; goals=_active_goals(uid); today=datetime.now(TZ).date()
    lines=['🔔 <b>یادآوری‌های من</b>',''] if fa else ['🔔 <b>My Reminders</b>','']; rows=[]
    if not goals: lines.append('یادآوری فعالی نداری.' if fa else 'No active reminders.')
    for g in goals:
        if not g['reminder_time']: continue
        end=g['reminder_end_date'] or 'بدون پایان'
        meta=_goal_meta(uid,g['id']); lines.append(f"• {html.escape(g['name'])} — ⏰ {g['reminder_time']} — تا {html.escape(end)}")
        if meta:
            details=' | '.join(f'{k}: {v}' for k,v in list(meta.items())[:3] if v)
            if details: lines.append(f"  📝 {html.escape(details)}")
        rows.append([InlineKeyboardButton(f"✏️ {g['name']}",callback_data=f"edit:{g['id']}")])
    rows += [[InlineKeyboardButton('📅 تقویم' if fa else '📅 Calendar',callback_data='goalcalendar:today'),main_menu_button(uid)]]
    target=q.message; await target.edit_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(rows))

# Minimal Jalali conversion, independent of external packages.
def _g2j(gy,gm,gd):
    gdm=[0,31,59,90,120,151,181,212,243,273,304,334]
    if gy>1600: jy=979; gy-=1600
    else: jy=0; gy-=621
    gy2=gy+1 if gm>2 else gy
    days=365*gy+(gy2+3)//4-(gy2+99)//100+(gy2+399)//400-80+gd+gdm[gm-1]
    jy+=33*(days//12053); days%=12053; jy+=4*(days//1461); days%=1461
    if days>365: jy+=(days-1)//365; days=(days-1)%365
    jm=1+days//31 if days<186 else 7+(days-186)//30; jd=1+(days%31 if days<186 else (days-186)%30)
    return jy,jm,jd

def _j2g(jy,jm,jd):
    if jy>979: gy=1600; jy-=979
    else: gy=621
    days=365*jy+(jy//33)*8+((jy%33)+3)//4+78+jd+(31*(jm-1) if jm<7 else (30*(jm-7)+186))
    gy+=400*(days//146097); days%=146097
    if days>36524: gy+=100*((days-1)//36524); days=(days-1)%36524; days+=1
    gy+=4*(days//1461); days%=1461
    if days>365: gy+=(days-1)//365; days=(days-1)%365
    gd=days+1
    mdays=[31,29 if (gy%4==0 and gy%100!=0) or gy%400==0 else 28,31,30,31,30,31,31,30,31,30,31]
    gm=1
    while gd>mdays[gm-1]: gd-=mdays[gm-1]; gm+=1
    return gy,gm,gd

def _jalali_from_iso(iso):
    d=datetime.fromisoformat(iso).date(); return _g2j(d.year,d.month,d.day)

def _jalali_iso(text):
    import re
    m=re.fullmatch(r'\s*(\d{4})[/-](\d{1,2})[/-](\d{1,2})\s*',text or '')
    if not m: return None
    jy,jm,jd=map(int,m.groups())
    try:
        gy,gm,gd=_j2g(jy,jm,jd); return datetime(gy,gm,gd).date().isoformat()
    except Exception: return None

def _jalali_month_days(y,m): return 31 if m<=6 else 30 if m<=11 else (30 if (y%33) in {1,5,9,13,17,22,26,30} else 29)

async def goal_calendar(update,context,year=None,month=None):
    q=update.callback_query; uid=q.from_user.id; fa=lang(uid)=='fa'; today=datetime.now(TZ).date(); jy,jm,jd=_g2j(today.year,today.month,today.day); year=year or jy; month=month or jm
    if q: await q.answer()
    names=['فروردین','اردیبهشت','خرداد','تیر','مرداد','شهریور','مهر','آبان','آذر','دی','بهمن','اسفند']
    en_names=['Farvardin','Ordibehesht','Khordad','Tir','Mordad','Shahrivar','Mehr','Aban','Azar','Dey','Bahman','Esfand']
    lines=[f"📅 <b>{names[month-1] if fa else en_names[month-1]} {year}</b>",'']
    rows=[]
    # Six weeks, Monday-based. Convert each Jalali day to Gregorian for lookup.
    first_iso=datetime(*_j2g(year,month,1)).date().isoformat(); first=datetime.fromisoformat(first_iso).date(); offset=(first.weekday())
    week=[]; header=['ش','ی','د','س','چ','پ','ج'] if fa else ['Mo','Tu','We','Th','Fr','Sa','Su']; rows.append([InlineKeyboardButton(x,callback_data='noop') for x in header])
    for pos in range(offset): week.append('')
    for day in range(1,_jalali_month_days(year,month)+1):
        iso=datetime(*_j2g(year,month,day)).date().isoformat(); c=db(); cnt=c.execute("SELECT COUNT(*) n FROM goals WHERE user_id=? AND enabled=1 AND (reminder_end_date IS NULL OR reminder_end_date>=?) AND substr(COALESCE(reminder_start_date,created_at),1,10)<=?",(uid,iso,iso)).fetchone()['n']; c.close(); label=f"{day}{'•' if cnt else ''}"; week.append((label,iso))
        if len(week)==7:
            rows.append([InlineKeyboardButton(x if isinstance(x,str) else x[0],callback_data='noop' if isinstance(x,str) else f'goalcalday:{x[1]}') for x in week]); week=[]
    if week: week += ['']*(7-len(week)); rows.append([InlineKeyboardButton(x if isinstance(x,str) else x[0],callback_data='noop' if isinstance(x,str) else f'goalcalday:{x[1]}') for x in week])
    prev_y,prev_m=(year,month-1) if month>1 else (year-1,12); next_y,next_m=(year,month+1) if month<12 else (year+1,1)
    rows.append([InlineKeyboardButton('⬅️ ماه قبل' if fa else '⬅️ Prev',callback_data=f'goalcalendar:{prev_y}:{prev_m}'),InlineKeyboardButton('📍 امروز' if fa else '📍 Today',callback_data='goalcalendar:today'),InlineKeyboardButton('ماه بعد ➡️' if fa else 'Next ➡️',callback_data=f'goalcalendar:{next_y}:{next_m}')])
    rows.append([main_menu_button(uid)])
    target=q.message if q else update.message; await target.edit_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(rows))

async def goal_calendar_day(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; iso=q.data.split(':',1)[1]; fa=lang(uid)=='fa'; c=db(); goals=c.execute("SELECT * FROM goals WHERE user_id=? AND enabled=1 ORDER BY id DESC",(uid,)).fetchall(); c.close(); selected=[]
    for g in goals:
        if g['reminder_start_date'] and iso<g['reminder_start_date']: continue
        if g['reminder_end_date'] and iso>g['reminder_end_date']: continue
        selected.append(g)
    jy,jm,jd=_jalali_from_iso(iso); lines=[f"📅 <b>{jy:04d}/{jm:02d}/{jd:02d}</b>",'']; rows=[]
    if not selected: lines.append('کاری برای این روز ثبت نشده.' if fa else 'No goals for this day.')
    for g in selected:
        lines.append(f"• {html.escape(g['name'])} — ⏰ {g['reminder_time'] or '—'}"); rows.append([InlineKeyboardButton(g['name'],callback_data=f'edit:{g["id"]}')])
    rows.append([InlineKeyboardButton('⬅️ تقویم' if fa else '⬅️ Calendar',callback_data=f'goalcalendar:{jy}:{jm}'),main_menu_button(uid)])
    await q.message.edit_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(rows))

async def goal_calendar_callback(update,context):
    q=update.callback_query; data=q.data
    if data=='goalcalendar:today':
        d=datetime.now(TZ).date(); y,m,_=_g2j(d.year,d.month,d.day); return await goal_calendar(update,context,y,m)
    _,y,m=data.split(':'); return await goal_calendar(update,context,int(y),int(m))

# Extend goal detail screen with stored metadata and edit entry point.
_OLD_DETAIL_GOALS_UPGRADE=detail
async def detail(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; gid=int(q.data.split(':')[1]); g=get_goal(uid,gid)
    if not g: return
    meta=_goal_meta(uid,gid); fa=lang(uid); lines=[f"🎯 <b>{html.escape(g['name'])}</b>",f"📁 {html.escape(g['category'])}",f"⭐ اولویت: {g['priority']}",f"⏰ {g['reminder_time'] or 'خاموش'}"]
    if g['reminder_end_date']: lines.append(f"📅 پایان تکرار: {html.escape(g['reminder_end_date'])}")
    for k,v in meta.items():
        if v: lines.append(f"📝 {html.escape(k)}: {html.escape(v)}")
    rows=[[InlineKeyboardButton('✏️ ویرایش' if fa=='fa' else '✏️ Edit',callback_data=f'edit:{gid}')],[InlineKeyboardButton('✅ انجام دادم' if fa=='fa' else '✅ Done',callback_data=f'done:{gid}'),InlineKeyboardButton('❌ انجام ندادم' if fa=='fa' else '❌ Not done',callback_data=f'miss:{gid}')],[main_menu_button(uid)]]
    await q.message.edit_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(rows))

# Save detailed fields after the goal row exists.
_OLD_TIME_CALLBACK_GOALS_UPGRADE=time_callback
async def time_callback(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; value=q.data.split(':',1)[1]
    if value=='custom': context.user_data['awaiting_custom_time']=True; await q.message.edit_text(T[lang(uid)]['custom_time']); return
    reminder=None if value=='none' else parse_time(value); name=context.user_data.get('name'); category=context.user_data.get('category')
    if not name or not category: return
    priority=context.user_data.get('priority',2); duration=context.user_data.get('duration_minutes'); add_goal(uid,name,category,reminder,priority,duration)
    c=db(); gid=c.execute("SELECT id FROM goals WHERE user_id=? ORDER BY id DESC LIMIT 1",(uid,)).fetchone()['id']; c.close()
    for k,v in context.user_data.get('ready_details',[]): _goal_store_meta(uid,int(gid),k,v)
    context.user_data.pop('ready_details',None)
    if reminder:
        context.user_data.clear(); context.user_data['pending_repeat_goal_id']=int(gid); await q.message.edit_text('🔁 مدت تکرار یادآوری را انتخاب کن:' if lang(uid)=='fa' else '🔁 Choose reminder duration:',reply_markup=_targeted_repeat_keyboard(uid)); return
    context.user_data.clear(); log_activity(uid,'goal_created'); await q.message.edit_text(T[lang(uid)]['goal_added'].format(name=display_name(uid)),reply_markup=InlineKeyboardMarkup([[main_menu_button(uid)]]))

async def custom_time_save(update,context):
    # Preserve legacy custom-goal flow, but persist ready-goal metadata when present.
    if context.user_data.get('ready_details'):
        uid=update.effective_user.id; reminder=parse_time((update.message.text or '').strip())
        if reminder is None: await update.message.reply_text(T[lang(uid)]['bad_time']); return True
        name=context.user_data.get('name'); category=context.user_data.get('category'); priority=context.user_data.get('priority',2); duration=context.user_data.get('duration_minutes'); add_goal(uid,name,category,reminder,priority,duration)
        c=db(); gid=c.execute("SELECT id FROM goals WHERE user_id=? ORDER BY id DESC LIMIT 1",(uid,)).fetchone()['id']; c.close()
        for k,v in context.user_data.get('ready_details',[]): _goal_store_meta(uid,int(gid),k,v)
        context.user_data.clear(); context.user_data['pending_repeat_goal_id']=int(gid); await update.message.reply_text('🔁 مدت تکرار یادآوری را انتخاب کن:',reply_markup=_targeted_repeat_keyboard(uid)); return True
    return await _OLD_CUSTOM_TIME_SAVE_GOALS_UPGRADE(update,context)

_OLD_CUSTOM_TIME_SAVE_GOALS_UPGRADE=globals().get('custom_time_save')
if _OLD_CUSTOM_TIME_SAVE_GOALS_UPGRADE is None:
    async def _OLD_CUSTOM_TIME_SAVE_GOALS_UPGRADE(update, context):
        return False

# Reminder completion: keep the DB row/history, disable only the active schedule and notify once.
_OLD_UNIFIED_REMINDER_GOALS_UPGRADE=v25_unified_reminder_job
async def v25_unified_reminder_job(context):
    await _OLD_UNIFIED_REMINDER_GOALS_UPGRADE(context)
    today=datetime.now(TZ).date().isoformat(); rows=[]; c=db();
    try: rows=c.execute("SELECT * FROM goals WHERE enabled=1 AND reminder_end_date=?",(today,)).fetchall()
    finally: c.close()
    for g in rows:
        uid=g['user_id']; c=db(); already=c.execute('SELECT 1 FROM goal_completion_notice WHERE user_id=? AND goal_id=?',(uid,g['id'])).fetchone(); c.close()
        if already: continue
        try:
            await context.bot.send_message(uid,'🎉 <b>هدف شما کامل شد!</b>\n\nامروز آخرین روز این هدف بود و دوره‌ای که تعیین کرده بودید به پایان رسید.\n\nسابقه هدف حفظ می‌شود و از تاریخچه قابل مشاهده خواهد بود.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📋 یادآوری‌های من',callback_data='goalreminders'),InlineKeyboardButton('📅 تقویم',callback_data='goalcalendar:today')],[main_menu_button(uid)]]))
            c=db(); c.execute('INSERT OR IGNORE INTO goal_completion_notice(user_id,goal_id,completed_at) VALUES(?,?,?)',(uid,g['id'],datetime.now(TZ).isoformat())); c.execute('UPDATE goals SET enabled=0 WHERE user_id=? AND id=?',(uid,g['id'])); c.commit(); c.close()
        except Exception as e: logger.warning('Goal completion notice failed: %s',e)

# Compact goals menu: add My Goals, Reminders and Calendar without changing root menu text/layout.
_OLD_COMPACT_MENU_KEYBOARD_GOALS_UPGRADE=_compact_menu_keyboard
def _compact_menu_keyboard(uid,section):
    kb=_OLD_COMPACT_MENU_KEYBOARD_GOALS_UPGRADE(uid,section)
    if section=='goals':
        fa=lang(uid)=='fa'; extra=[
            [InlineKeyboardButton('🎯 اهداف من' if fa else '🎯 My Goals',callback_data='cm:my_goals'),InlineKeyboardButton('🔔 یادآوری‌ها' if fa else '🔔 Reminders',callback_data='goalreminders')],
            [InlineKeyboardButton('📅 تقویم شمسی' if fa else '📅 Jalali Calendar',callback_data='goalcalendar:today')],
        ]
        rows = [list(row) for row in kb.inline_keyboard]
        rows[1:1] = extra
        kb = InlineKeyboardMarkup(rows)
    return kb

async def my_goals_callback(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; fa=lang(uid)=='fa'; goals=_active_goals(uid); lines=['🎯 <b>اهداف من</b>',''] if fa else ['🎯 <b>My Goals</b>','']; rows=[]
    if not goals: lines.append('هدف فعالی نداری.' if fa else 'No active goals.')
    for g in goals:
        lines.append(f"• {html.escape(g['name'])} — ⏰ {g['reminder_time'] or 'خاموش'}")
        rows.append([InlineKeyboardButton(g['name'],callback_data=f'edit:{g["id"]}')])
    rows.append([InlineKeyboardButton('🔔 یادآوری‌ها' if fa else '🔔 Reminders',callback_data='goalreminders'),InlineKeyboardButton('📅 تقویم' if fa else '📅 Calendar',callback_data='goalcalendar:today')]); rows.append([main_menu_button(uid)])
    await q.message.edit_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(rows))

_OLD_COMPACT_MENU_CALLBACK_GOALS_UPGRADE=compact_menu_callback
async def compact_menu_callback(update,context):
    q=update.callback_query; data=q.data
    if data=='cm:my_goals': return await my_goals_callback(update,context)
    if data=='goalreminders': return await goal_reminders_list(update,context)
    if data.startswith('goalcalendar:'):
        if data.count(':')==2:
            return await goal_calendar_callback(update,context)
    return await _OLD_COMPACT_MENU_CALLBACK_GOALS_UPGRADE(update,context)

# Final text router wrapper: detailed ready-goal fields must win before generic input handlers.
_OLD_TEXT_ROUTER_GOALS_UPGRADE=text_router
async def text_router(update,context):
    if update.message and context.user_data.get('ready_detail_mode'):
        return await ready_detail_text_save(update,context)
    return await _OLD_TEXT_ROUTER_GOALS_UPGRADE(update,context)




# ===================== FINAL GOALS / NAVIGATION STABILITY PATCH =====================
# This layer is intentionally last so it wins over older text-router/callback wrappers.
# It fixes two production issues seen in testing:
#   1) "🎯 برنامه من" could fall through a legacy router and surface TypeError.
#   2) Main-menu callbacks could delete the current screen and then create a second
#      carrier message, leaving the user with an empty/deleted screen.
# No database tables or user-owned data are changed here.

async def _render_compact_root_inline_safe(update, context):
    """Render the compact root in-place; never delete the current bot screen."""
    q = getattr(update, "callback_query", None)
    uid = update.effective_user.id
    fa = lang(uid) == "fa"
    text = "🏠 <b>منوی اصلی</b>\n\nیک بخش را انتخاب کن." if fa else "🏠 <b>Main Menu</b>\n\nChoose a section."
    markup = _compact_root_inline(uid)
    if q:
        try:
            await q.answer()
        except Exception:
            pass
        try:
            await q.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            # If Telegram reports that the message is already identical, do not
            # create a duplicate bubble.
            try:
                await q.message.edit_reply_markup(reply_markup=markup)
            except Exception:
                pass
    else:
        # Reply-keyboard navigation is already persistent; delete only the user's
        # navigation command so it does not accumulate in the chat.
        try:
            await update.message.delete()
        except Exception:
            pass
    return True


# Replace the old goals callback that deleted the current message and sent a new
# visible Home carrier. All existing goals:main buttons now return in-place.
async def goals_navigation_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    action = q.data.split(":", 1)[1] if ":" in (q.data or "") else ""
    if action == "main":
        clear_flow(context)
        try:
            await q.answer()
        except Exception:
            pass
        try:
            await q.message.delete()
        except Exception:
            pass
        fa = lang(uid) == "fa"
        await context.bot.send_message(
            chat_id=uid,
            text="🏠 <b>منوی اصلی</b>\n\nیک بخش را انتخاب کن." if fa else
                 "🏠 <b>Main Menu</b>\n\nChoose a section.",
            parse_mode="HTML",
            reply_markup=compact_keyboard(uid),
        )
        return
    try:
        await q.answer("این گزینه دیگر معتبر نیست. منوی اهداف را دوباره باز کن.", show_alert=True)
    except Exception:
        pass


# Same in-place behavior for the generic Main Menu callback used by most modules.
async def navigation_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    clear_flow(context)
    if (q.data or "") == "nav:main":
        try:
            await q.answer()
        except Exception:
            pass
        try:
            await q.message.delete()
        except Exception:
            pass
        fa = lang(uid) == "fa"
        await context.bot.send_message(
            chat_id=uid,
            text="🏠 <b>منوی اصلی</b>\n\nیک بخش را انتخاب کن." if fa else
                 "🏠 <b>Main Menu</b>\n\nChoose a section.",
            parse_mode="HTML",
            reply_markup=compact_keyboard(uid),
        )
        return
    try:
        await q.answer()
    except Exception:
        pass


# Settings Main Menu must also use the same in-place root renderer.
_OLD_SETTINGS_CALLBACK_STABLE_NAV = settings_callback
async def settings_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    action = q.data.split(":", 1)[1] if ":" in (q.data or "") else ""
    if action == "main":
        clear_flow(context)
        try:
            await q.answer()
        except Exception:
            pass
        try:
            await q.message.delete()
        except Exception:
            pass
        fa = lang(uid) == "fa"
        await context.bot.send_message(
            chat_id=uid,
            text="🏠 <b>منوی اصلی</b>\n\nیک بخش را انتخاب کن." if fa else
                 "🏠 <b>Main Menu</b>\n\nChoose a section.",
            parse_mode="HTML",
            reply_markup=compact_keyboard(uid),
        )
        return
    return await _OLD_SETTINGS_CALLBACK_STABLE_NAV(update, context)


async def _render_goals_section_direct(update, context):
    """Direct, minimal renderer for the user's Goals section.

    This intentionally bypasses the long legacy text-router chain. The section
    is built from the already-tested compact keyboard and therefore cannot fall
    into the stale handler that produced the TypeError shown by the user.
    """
    uid = update.effective_user.id
    clear_flow(context)
    fa = lang(uid) == "fa"
    text = "🎯 <b>برنامه و اهداف</b>" if fa else "🎯 <b>Goals & Plan</b>"
    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=_compact_menu_keyboard(uid, "goals"),
    )
    return True


# Final text-router guard: handle the two navigation labels and the Goals entry
# before any older wrapper can consume them. This is deliberately the last layer.
_OLD_TEXT_ROUTER_STABLE_NAV = text_router
async def text_router(update, context):
    if not update.message or not update.message.text:
        return await _OLD_TEXT_ROUTER_STABLE_NAV(update, context)
    txt = update.message.text.strip()
    uid = update.effective_user.id

    if txt in ("🎯 برنامه من", "🎯 My Plan"):
        return await _render_goals_section_direct(update, context)

    # Main Menu is absolute: clear every transient flow and show exactly one
    # root screen. Never allow a pending form/AI state to consume this label.
    if txt in ("🏠 منوی اصلی", "🏠 Main Menu"):
        clear_flow(context)
        try:
            await update.message.delete()
        except Exception:
            pass
        fa = lang(uid) == "fa"
        await update.message.reply_text(
            "🏠 <b>منوی اصلی</b>\n\nیک بخش را انتخاب کن." if fa else
            "🏠 <b>Main Menu</b>\n\nChoose a section.",
            parse_mode="HTML",
            reply_markup=compact_keyboard(uid),
        )
        return True

    # Back is relative. Use the section recorded when the current flow was
    # entered; if no parent exists, fall back safely to the root rather than
    # incorrectly jumping to Goals.
    if txt in ("⬅️ برگشت", "⬅️ Back"):
        parent = context.user_data.get("_nav_parent_section") or "root"
        try:
            await update.message.delete()
        except Exception:
            pass
        clear_flow(context)
        fa = lang(uid) == "fa"
        if parent == "tools":
            await update.message.reply_text(
                "🤖 <b>ابزارهای هوشمند</b>" if fa else "🤖 <b>Smart Tools</b>",
                parse_mode="HTML",
                reply_markup=_compact_menu_keyboard(uid, "tools"),
            )
        elif parent in {"goals", "reports", "vip", "account", "support"}:
            titles = {
                "goals": ("🎯 <b>برنامه و اهداف</b>", "🎯 <b>Goals & Plan</b>"),
                "reports": ("📊 <b>گزارش و پیشرفت</b>", "📊 <b>Reports & Progress</b>"),
                "vip": ("💎 <b>VIP و پاداش‌ها</b>", "💎 <b>VIP & Rewards</b>"),
                "account": ("👤 <b>حساب من</b>", "👤 <b>My Account</b>"),
                "support": ("🎫 <b>پشتیبانی</b>", "🎫 <b>Support</b>"),
            }
            await update.message.reply_text(
                titles[parent][0 if fa else 1],
                parse_mode="HTML",
                reply_markup=_compact_menu_keyboard(uid, parent),
            )
        else:
            await update.message.reply_text(
                "🏠 <b>منوی اصلی</b>\n\nیک بخش را انتخاب کن." if fa else
                "🏠 <b>Main Menu</b>\n\nChoose a section.",
                parse_mode="HTML",
                reply_markup=compact_keyboard(uid),
            )
        return True

    return await _OLD_TEXT_ROUTER_STABLE_NAV(update, context)


# Explicit callback registration is normally already present, but this final
# assignment guarantees the dispatcher uses the repaired functions above.

if __name__ == "__main__":
    main()
