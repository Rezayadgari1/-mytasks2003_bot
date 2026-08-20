
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
import random
import hashlib
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
DB_SCHEMA_VERSION = 22

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

def subscription_required(func):
    @wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        uid = update.effective_user.id if update.effective_user else 0
        if feature_enabled("maintenance") and uid not in ADMIN_IDS:
            msg = "🛠 ربات در حال بروزرسانی است. لطفاً بعداً دوباره تلاش کن."
            if update.callback_query:
                await update.callback_query.answer(msg, show_alert=True)
            elif update.message:
                await update.message.reply_text(msg)
            return
        if not await require_subscription(update, context):
            return
        return await func(update, context, *args, **kwargs)
    return wrapper



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
        return True
    except Exception as e:
        logger.error("Database backup failed: %s", e)
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
    c.execute("""CREATE TABLE IF NOT EXISTS channel_posts(
        id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL,
        schedule_type TEXT NOT NULL DEFAULT 'once', schedule_time TEXT, weekday INTEGER,
        run_at TEXT, enabled INTEGER NOT NULL DEFAULT 1, last_sent_at TEXT,
        created_at TEXT NOT NULL, created_by INTEGER NOT NULL)""")
    user_cols={r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()}
    for col,ddl in [("xp","INTEGER NOT NULL DEFAULT 0"),("blocked","INTEGER NOT NULL DEFAULT 0"),("warnings","INTEGER NOT NULL DEFAULT 0"),("vip_until","TEXT"),("referrer_id","INTEGER"),("referral_code","TEXT")]:
        if col not in user_cols: c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
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
    now_iso=datetime.now(TZ).isoformat()
    for key in ["customers"]:
        c.execute("INSERT OR IGNORE INTO feature_flags(key,enabled,updated_at) VALUES(?,?,?)",(key,1,now_iso))
        c.execute("INSERT OR IGNORE INTO feature_access(key,mode,updated_at) VALUES(?,?,?)",(key,"vip",now_iso))

    now_iso=datetime.now(TZ).isoformat()
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
    c.execute("UPDATE users SET referral_code=COALESCE(referral_code,?) WHERE user_id=?",(hashlib.sha256(str(uid).encode()).hexdigest()[:10],uid))
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

def user_feature_allowed(uid,key):
    if admin_is_allowed(uid) or key=="xp": return True
    try:
        if not feature_enabled(key): return False
        mode=feature_access_mode(key,uid)
        return mode!="off" and (mode!="vip" or is_vip(uid))
    except Exception:
        return True

def filter_menu_rows(uid,rows):
    out=[]
    for row in rows:
        r=[label for label in row if (FEATURE_MENU_MAP.get(label) is None or user_feature_allowed(uid,FEATURE_MENU_MAP[label]))]
        if r: out.append(r)
    return out

def keyboard(uid):
    rows=filter_menu_rows(uid,[list(row) for row in T[lang(uid)]["menu"]])
    try:
        if user_feature_allowed(uid,"customers"):
            rows.append(["👥 مدیریت مشتری و نوبت‌دهی" if lang(uid)=="fa" else "👥 Customer & Appointments"])
    except Exception: pass
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
    _, gid_s, mins_s = q.data.split(":")
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
    _, step_id, gid = q.data.split(":")
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
            if await customer_booking_start(update, context, arg[5:].strip()):
                return
        if arg.startswith("ref_"):
            code=arg[4:].strip()
            try:
                c=db(); inviter=c.execute("SELECT user_id FROM users WHERE referral_code=?",(code,)).fetchone()
                if inviter and int(inviter["user_id"])!=uid:
                    c.execute("UPDATE users SET referrer_id=? WHERE user_id=? AND (referrer_id IS NULL OR referrer_id=0)",(int(inviter["user_id"]),uid))
                    c.execute("INSERT OR IGNORE INTO referrals(inviter_id,invited_id,created_at,rewarded) VALUES(?,?,?,0)",(int(inviter["user_id"]),uid,datetime.now(TZ).isoformat()))
                    c.commit()
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


@subscription_required
async def gender_callback(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    value = q.data.split(":")[1]
    set_gender(uid, value)
    log_activity(uid, "gender_selected")
    await q.message.reply_text(
        T[lang(uid)]["gender_saved"].format(name=display_name(uid)),
        reply_markup=keyboard(uid),
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
    await update.message.reply_text(T[lang(uid)]["settings"],reply_markup=settings_keyboard(uid))

async def goals_navigation_callback(update, context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id
    action=q.data.split(":",1)[1]
    if action=="main":
        context.user_data.clear()
        await q.message.reply_text("🏠 منوی اصلی",reply_markup=keyboard(uid))


async def settings_language_callback(update, context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; value=q.data.split(":",1)[1]
    set_lang(uid,value); log_activity(uid,"language_change")
    await q.message.reply_text(T[value]["language_saved"],reply_markup=settings_keyboard(uid))


async def settings_callback(update, context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; action=q.data.split(":",1)[1]; fa=lang(uid)=="fa"
    if action=="language":
        await q.message.reply_text("زبان را انتخاب کن / Choose language:",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🇮🇷 فارسی",callback_data="setlang:fa"),InlineKeyboardButton("🇬🇧 English",callback_data="setlang:en")],[InlineKeyboardButton("↩️ تنظیمات",callback_data="settings:back")]])); return
    if action=="channel":
        if not admin_is_allowed(uid):
            await q.message.reply_text("⛔ دسترسی ندارید." if fa else "⛔ Access denied.")
            return
        await q.message.reply_text(
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
        await q.message.reply_text(("🔔 یادآوری‌ها: " + ("روشن" if state else "خاموش")) if fa else ("🔔 Reminders: " + ("On" if state else "Off")),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تغییر وضعیت",callback_data="settings:toggle_reminders")],[InlineKeyboardButton("↩️ تنظیمات",callback_data="settings:back")]])); return
    if action=="toggle_reminders":
        c=db(); c.execute("INSERT OR IGNORE INTO user_settings(user_id) VALUES(?)",(uid,)); c.execute("UPDATE user_settings SET reminders_enabled=1-reminders_enabled WHERE user_id=?",(uid,)); c.commit(); c.close(); await q.message.reply_text("✅ تنظیم شد.",reply_markup=settings_keyboard(uid)); return
    if action=="goals":
        await q.message.reply_text(("🎯 هدف‌ها دائمی هستند و فقط خودت می‌توانی حذفشان کنی. هنگام ساخت هدف می‌توانی مدت انجام را هم تعیین کنی." if fa else "🎯 Goals stay saved until you delete them. When creating a goal you can also set its duration."),reply_markup=settings_keyboard(uid)); return
    if action=="ai":
        configured=bool(os.environ.get("OPENAI_API_KEY","").strip())
        await q.message.reply_text(("🤖 چت با AI\n\nوضعیت کلید: " + ("🟢 تنظیم شده" if configured else "🔴 تنظیم نشده") + "\nسهمیه رایگان روزانه: ۱۰ پیام" if fa else "🤖 AI Chat\n\nKey status: " + ("🟢 configured" if configured else "🔴 missing") + "\nFree daily quota: 10 messages"),reply_markup=settings_keyboard(uid)); return
    if action=="vip":
        xp,level,vip_until=xp_info(uid)
        text=(f"💎 VIP\n\nوضعیت: {'🟢 فعال' if is_vip(uid) else '⚪ عادی'}\n⭐ سطح: {level}\n👥 دعوت دوستان و فعالیت‌ها می‌توانند XP و پاداش بگیرند.\n\nپرداخت واقعی فعلاً از پنل مدیر قابل کنترل است." if fa else f"💎 VIP\n\nStatus: {'🟢 Active' if is_vip(uid) else '⚪ Free'}\n⭐ Level: {level}\n👥 Referrals and activity can earn XP/rewards.\n\nReal payments are controlled from the admin panel for now.")
        await q.message.reply_text(text,reply_markup=settings_keyboard(uid)); return
    if action in ("back","main"):
        if action=="main": await q.message.reply_text("🏠 منوی اصلی",reply_markup=keyboard(uid))
        else: await q.message.reply_text(T[lang(uid)]["settings"],reply_markup=settings_keyboard(uid))


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
    await q.message.reply_text(
        T[lang(uid)]["choose_goal"],
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@subscription_required
async def new_back(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    context.user_data.pop("category", None)
    await q.message.reply_text(
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
    await q.message.reply_text(
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
    await q.message.reply_text(
        "⏱ مدت انجام هدف را انتخاب کن:" if lang(uid)=="fa" else "⏱ How long should this goal take?",
        reply_markup=duration_keyboard(uid),
    )


@subscription_required
async def duration_callback(update, context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id
    value=q.data.split(":",1)[1]
    if value=="custom":
        context.user_data["awaiting_custom_duration"]=True
        await q.message.reply_text("✏️ مدت را به دقیقه وارد کن (مثلاً 45)." if lang(uid)=="fa" else "✏️ Enter duration in minutes (e.g. 45).")
        return
    context.user_data["duration_minutes"] = None if value=="0" else int(value)
    await q.message.reply_text(T[lang(uid)]["choose_time"],reply_markup=time_keyboard(uid))


@subscription_required
async def time_callback(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    value = q.data.split(":", 1)[1]

    if value == "custom":
        context.user_data["awaiting_custom_time"] = True
        await q.message.reply_text(T[lang(uid)]["custom_time"])
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
    await q.message.reply_text(
        T[lang(uid)]["goal_added"].format(name=display_name(uid)),
        reply_markup=keyboard(uid),
    )


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
    await q.message.reply_text(
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
            await q.message.reply_text(
                ("🏆 دستاورد جدید!\n" + "\n".join(new_achievements))
                if lang(uid) == "fa"
                else ("🏆 New achievement!\n" + "\n".join(new_achievements))
            )
    await q.message.reply_text(
        T[lang(uid)]["done"].format(name=display_name(uid))
        if is_done
        else T[lang(uid)]["missed"].format(name=display_name(uid)),
        reply_markup=keyboard(uid),
    )


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
    await q.message.reply_text(
        f"🎯 {g['name']}\n⏰ {g['reminder_time'] or 'Off'}",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@subscription_required
async def rename_start(update, context):
    q = update.callback_query
    await q.answer()
    context.user_data["edit_id"] = int(q.data.split(":")[1])
    context.user_data["awaiting_rename"] = True
    await q.message.reply_text(T[lang(q.from_user.id)]["name"])


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
    await q.message.reply_text(
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
        await q.message.reply_text(T[lang(uid)]["custom_time"])
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
    await q.message.reply_text(
        "✅ زمان یادآوری تغییر کرد." if lang(uid) == "fa" else "✅ Reminder time updated.",
        reply_markup=keyboard(uid),
    )


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
        await q.message.reply_text(T[lang(uid)]["custom_time"])
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
    await q.message.reply_text(T[lang(uid)]["changed"], reply_markup=keyboard(uid))


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
    await q.message.reply_text(
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
    await q.message.reply_text(
        T[lang(uid)]["deleted"],
        reply_markup=keyboard(uid),
    )


@subscription_required
async def delete_no(update, context):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text(
        "❌ Cancelled" if lang(q.from_user.id) == "en" else "❌ لغو شد.",
        reply_markup=keyboard(q.from_user.id),
    )



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
            date=joined,
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



def get_channel_config():
    c=db(); r=c.execute("SELECT * FROM channel_config WHERE id=1").fetchone(); c.close(); return r

def persistent_channel_config(channel_id):
    """Update the existing channel connection in place."""
    now = datetime.now(TZ).isoformat()
    c = db()
    try:
        c.execute(
            """INSERT INTO channel_config(id,channel_id,enabled,updated_at)
               VALUES(1,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   channel_id=excluded.channel_id,
                   enabled=1,
                   updated_at=excluded.updated_at""",
            (str(channel_id), 1, now),
        )
        c.commit()
    finally:
        c.close()


def set_channel_config(channel_id):
    c=db(); c.execute("""INSERT INTO channel_config(id,channel_id,enabled,updated_at) VALUES(1,?,1,?)
    ON CONFLICT(id) DO UPDATE SET channel_id=excluded.channel_id, enabled=1, updated_at=excluded.updated_at""",(str(channel_id).strip(),datetime.now(TZ).isoformat())); c.commit(); c.close()

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
    exact = t in body
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
    for row in recent_auto_posts(channel_id,12):
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
    avoid="\\n".join(f"- {r['topic']}: {str(r['content'])[:220]}" for r in recent)
    for attempt in range(1,4):
        content=ai_generate_post(topic, avoid_text=avoid, variation_seed=attempt)
        duplicate,score=post_is_duplicate(channel_id,topic,content)
        if not duplicate and _is_topic_relevant(content,topic):
            return content
        logger.warning("Auto post rejected topic=%s attempt=%s similarity=%.2f",topic,attempt,score)
        avoid += f"\\n- نسخه ردشده: {str(content)[:220]}"
    return topic_specific_fallback(topic,4)

def ai_generate_post(topic, avoid_text='', variation_seed=1):
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("OPENAI_MODEL", "gpt-5-mini").strip()
    focus = _topic_focus(topic)
    topic_terms = ", ".join(_topic_terms(topic)[:6])
    if api_key:
        try:
            prompt = (
                "تو نویسنده محتوای تخصصی کانال MyTasks هستی.\n"
                f"موضوع انتخاب‌شده و غیرقابل‌تغییر: «{topic}»\n"
                f"راهنمای محتوایی: {focus}\n"
                f"واژه‌های مرتبط پیشنهادی: {topic_terms}\n"
                f"تنوع تولید: نسخه {variation_seed}.\n"
                f"پست‌های اخیر که نباید از نظر جمله‌بندی، تیتر یا ساختار تکرار شوند:\n{avoid_text[:1800]}\n\n"
                "قانون بسیار مهم: حداقل ۸۰ درصد متن باید مستقیماً درباره همین موضوع انتخاب‌شده باشد. "
                "موضوع را با موضوعات عمومی مدیریت هدف، انگیزشی یا بهره‌وری جایگزین نکن. "
                "اگر موضوع درباره ورزش است، درباره خود ورزش و اثرات و اجرای آن بنویس؛ اگر درباره خواب است، درباره خواب بنویس؛ "
                "و همین منطق را برای هر موضوع دیگری رعایت کن.\n"
                "حداکثر 120 کلمه. یک تیتر دقیق، توضیح کوتاه موضوع، 3 نکته کاربردی مرتبط و در پایان یک تمرین/اقدام یک‌خطی مرتبط بده. "
                "ادعاهای پزشکی یا مالی قطعی نکن. فقط متن پست را برگردان."
            )
            payload = json.dumps({
                "model": model,
                "input": prompt,
                "max_output_tokens": 360
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.openai.com/v1/responses",
                data=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=35) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text_out = data.get("output_text", "").strip()
            if text_out and _is_topic_relevant(text_out, topic):
                return text_out
            if text_out:
                logger.warning("AI output rejected for weak topic relevance: %s", topic)
        except Exception as e:
            logger.error("AI text generation failed: %s", e)

    # Topic-specific fallback. Never use one generic goal-management text for
    # every automatic topic.
    return topic_specific_fallback(topic, variation_seed)


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
    _,rating,topic=q.data.split(":",2); score=1 if rating=="up" else -1; now=datetime.now(TZ).isoformat(); c=db(); c.execute("INSERT INTO content_feedback(post_key,user_id,rating,reaction,created_at) VALUES(?,?,?,?,?)",(topic,uid,score,rating,now)); c.execute("INSERT INTO content_preferences(user_id,category,score) VALUES(?,?,?) ON CONFLICT(user_id,category) DO UPDATE SET score=score+excluded.score",(uid,topic,score)); c.commit(); c.close(); add_xp(uid,2,"content_feedback")


async def send_auto_channel_post(context, channel, topic, category=None):
    if not feature_enabled("auto_publish"):
        raise RuntimeError("auto_publish feature is disabled")
    category=category or get_auto_setting("category","random")
    content=generate_unique_auto_post(channel,category,topic)
    bot_username,channel_username=await get_identity_handles(context.bot,channel)
    content=content[:950]+compact_channel_footer(bot_username,channel_username)
    image=await generate_topic_image(topic)
    try:
        feedback_markup=content_feedback_keyboard(topic)
        if image is not None:
            msg=await context.bot.send_photo(chat_id=channel,photo=image,caption=content,reply_markup=feedback_markup)
        else:
            msg=await context.bot.send_message(chat_id=channel,text=content,reply_markup=feedback_markup)
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


def get_auto_setting(key, default=""):
    c = db()
    c.execute("""CREATE TABLE IF NOT EXISTS auto_channel_settings(
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    )""")
    r = c.execute("SELECT value FROM auto_channel_settings WHERE key=?", (key,)).fetchone()
    c.commit()
    c.close()
    return r["value"] if r else default


def set_auto_setting(key, value):
    c = db()
    c.execute("""CREATE TABLE IF NOT EXISTS auto_channel_settings(
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    )""")
    c.execute("""INSERT INTO auto_channel_settings(key,value) VALUES(?,?)
                 ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (key, value))
    c.commit()
    c.close()


async def auto_channel_job(context):
    cfg=get_channel_config(); channel=cfg["channel_id"] if cfg else ""
    if not channel or get_auto_setting("enabled","0")!="1" or not feature_enabled("auto_publish"): return
    now=datetime.now(TZ); interval=int(get_auto_setting("interval_minutes","60") or 60)
    next_raw=get_auto_setting("next_run","")
    try: next_run=datetime.fromisoformat(next_raw) if next_raw else now+timedelta(minutes=interval)
    except ValueError: next_run=now+timedelta(minutes=interval)
    if next_run.tzinfo is None: next_run=next_run.replace(tzinfo=TZ)
    approval=True and bool(ADMIN_IDS)
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
                image=await generate_topic_image(pending["topic"]); bot_username,channel_username=await get_identity_handles(context.bot,channel); content=pending["content"]
                if image is not None: await context.bot.send_photo(chat_id=channel,photo=image,caption=content[:1024],reply_markup=content_feedback_keyboard(pending["topic"]))
                else: await context.bot.send_message(chat_id=channel,text=content,reply_markup=content_feedback_keyboard(pending["topic"]))
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
    if not r: c.close(); await q.message.reply_text("❌ پیش‌نمایش پیدا نشد."); return
    c.execute("UPDATE auto_pending SET status='approved' WHERE id=?",(pid,)); c.commit(); c.close(); await q.message.reply_text("✅ تأیید شد. پست در زمان تعیین‌شده منتشر می‌شود.")

async def approval_reject_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔",show_alert=True); return
    await q.answer(); pid=int(q.data.split(":",1)[1]); c=db(); c.execute("UPDATE auto_pending SET status='rejected' WHERE id=? AND status='pending'",(pid,)); c.commit(); c.close(); await q.message.reply_text("❌ پست رد شد و منتشر نمی‌شود.")


def channel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📡 تنظیم کانال", callback_data="ch:set"),
         InlineKeyboardButton("🔌 تست اتصال", callback_data="ch:test")],
        [InlineKeyboardButton("📝 ساخت پست", callback_data="ch:new"),
         InlineKeyboardButton("📋 پست‌ها", callback_data="ch:list")],
        [InlineKeyboardButton("🤖 انتشار خودکار", callback_data="ch:auto")],
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
        await q.message.reply_text(
            "🟢 انتشار خودکار روشن شد." if new_value == "1" else "⚪ انتشار خودکار خاموش شد.",
            reply_markup=auto_channel_keyboard()
        )

    elif action == "interval":
        await q.message.reply_text(
            "⏱ فاصله انتشار را انتخاب کن.\nاز ۵ دقیقه تا ۲۴ ساعت، یا زمان دلخواه:",
            reply_markup=auto_interval_keyboard()
        )

    elif action == "category":
        await q.message.reply_text(
            "🧠 دسته‌بندی کامل را انتخاب کن:",
            reply_markup=auto_category_keyboard()
        )

    elif action == "guide":
        await q.message.reply_text(
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
        await q.message.reply_text(
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
        await q.message.reply_text(
            f"🤖 وضعیت انتشار خودکار\n\n"
            f"وضعیت: {'🟢 روشن' if get_auto_setting('enabled','0')=='1' else '⚪ خاموش'}\n"
            f"⏱ فاصله: هر {interval} دقیقه\n"
            f"🧠 دسته: {category}\n"
            f"📌 شاخه: {sub}\n"
            f"🕐 انتشار بعدی: {next_run}",
            reply_markup=auto_channel_keyboard()
        )

    elif action == "back":
        await q.message.reply_text("🤖 انتشار خودکار", reply_markup=auto_channel_keyboard())


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
        await q.message.reply_text(
            "🎲 موضوعات به‌صورت تصادفی انتخاب می‌شوند.\n\n⏱ حالا بگو هر چند دقیقه یک پست منتشر شود:",
            reply_markup=auto_interval_keyboard(),
        )
        return
    idx = int(value)
    category = list(AUTO_TOPIC_TREE_FA.keys())[idx]
    await q.message.reply_text(
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
    _, cat_idx, sub_idx = q.data.split(":")
    cat_idx = int(cat_idx)
    category = list(AUTO_TOPIC_TREE_FA.keys())[cat_idx]
    if sub_idx == "random":
        sub = "random"
    else:
        sub = AUTO_TOPIC_TREE_FA[category][int(sub_idx)]
    set_auto_setting("category", category)
    set_auto_setting("subcategory", sub)
    await q.message.reply_text(
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
        await q.message.reply_text(
            "✏️ فاصله دلخواه را به دقیقه وارد کن.\nمثال: 45\nحداقل ۵ و حداکثر ۱۴۴۰ دقیقه (۲۴ ساعت)."
        )
        return
    minutes = int(raw_minutes)
    set_auto_setting("interval_minutes", str(minutes))
    set_auto_setting("enabled", "1")
    set_auto_setting("next_run", (datetime.now(TZ) + timedelta(minutes=minutes)).isoformat())
    await q.message.reply_text(
        f"✅ انتشار خودکار روی هر {minutes} دقیقه تنظیم شد و روشن است.",
        reply_markup=auto_channel_keyboard()
    )


@subscription_required
async def channel_panel_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    if not admin_guard(uid):
        await q.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await q.answer()
    action = q.data.split(":", 1)[1]
    cfg = get_channel_config()
    channel = cfg["channel_id"] if cfg and cfg["channel_id"] else "تنظیم نشده"

    if action == "main":
        await q.message.reply_text(
            f"📡 <b>مدیریت کانال</b>\n\n📢 کانال: <code>{channel}</code>",
            parse_mode="HTML",
            reply_markup=channel_keyboard(),
        )
    elif action == "set":
        context.user_data["channel_state"] = "set"
        await q.message.reply_text(
            "📡 یوزرنیم کانال را بفرست.\n"
            "مثال: <code>@MyTasks</code>\n"
            "لینک t.me هم پذیرفته می‌شود.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ مدیریت کانال", callback_data="ch:main")]]),
        )
    elif action == "auto":
        await q.message.reply_text(
            "🤖 <b>انتشار خودکار</b>\n\n"
            "پست کوتاه + تصویر مرتبط + دسته‌بندی و زمان‌بندی قابل تنظیم.",
            parse_mode="HTML",
            reply_markup=auto_channel_keyboard(),
        )
    elif action == "test":
        if channel == "تنظیم نشده":
            await q.message.reply_text("❌ ابتدا کانال را تنظیم کن.", reply_markup=channel_keyboard())
            return
        try:
            chat = await context.bot.get_chat(channel)
            await q.message.reply_text(
                f"✅ اتصال فعال است.\n📢 {chat.title or channel}\n🆔 <code>{chat.id}</code>",
                parse_mode="HTML",
                reply_markup=channel_keyboard(),
            )
        except Exception as e:
            logger.error("Channel test: %s", e)
            await q.message.reply_text(
                "❌ اتصال ناموفق.\nربات باید Administrator کانال باشد و اجازه ارسال پیام داشته باشد.",
                reply_markup=channel_keyboard(),
            )
    elif action == "new":
        context.user_data["channel_state"] = "content"
        await q.message.reply_text("📝 متن پست را بفرست:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ مدیریت کانال", callback_data="ch:main")]]))
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
        await q.message.reply_text(
            text_out, parse_mode="HTML", reply_markup=channel_keyboard()
        )


@subscription_required
async def channel_schedule_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔ دسترسی ندارید.",show_alert=True); return
    await q.answer(); a=q.data.split(":",1)[1]
    if a=="cancel": context.user_data.clear(); await q.message.reply_text("❌ لغو شد.",reply_markup=channel_keyboard()); return
    if a=="now":
        cfg=get_channel_config()
        if not cfg or not cfg["channel_id"]: await q.message.reply_text("❌ ابتدا کانال را تنظیم کن.",reply_markup=channel_keyboard()); return
        try: await context.bot.send_message(chat_id=cfg["channel_id"],text=context.user_data["channel_content"]); context.user_data.clear(); await q.message.reply_text("✅ پست منتشر شد.",reply_markup=channel_keyboard())
        except Exception as e: logger.error("Immediate channel post: %s",e); await q.message.reply_text("❌ انتشار ناموفق. دسترسی کانال را بررسی کن.",reply_markup=channel_keyboard())
    elif a=="once": context.user_data["channel_state"]="once"; await q.message.reply_text("📅 تاریخ و ساعت را بفرست: 2026-08-20 18:30")
    elif a=="daily": context.user_data["channel_state"]="daily"; await q.message.reply_text("⏰ ساعت روزانه:",reply_markup=channel_time_keyboard("chd"))
    elif a=="weekly": context.user_data["channel_state"]="wday"; await q.message.reply_text("📆 روز هفته:",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("شنبه",callback_data="chw:5"),InlineKeyboardButton("یکشنبه",callback_data="chw:6")],[InlineKeyboardButton("دوشنبه",callback_data="chw:0"),InlineKeyboardButton("سه‌شنبه",callback_data="chw:1")],[InlineKeyboardButton("چهارشنبه",callback_data="chw:2"),InlineKeyboardButton("پنجشنبه",callback_data="chw:3")],[InlineKeyboardButton("جمعه",callback_data="chw:4")]]))

@subscription_required
async def channel_daily_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔ دسترسی ندارید.",show_alert=True); return
    await q.answer(); v=q.data.split(":",1)[1]
    if v=="custom": context.user_data["channel_state"]="daily_custom"; await q.message.reply_text("🕐 ساعت را بفرست، مثال 18:30"); return
    await save_channel_post(context,uid,"daily",v,None,None,q.message)

@subscription_required
async def channel_weekday_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔ دسترسی ندارید.",show_alert=True); return
    await q.answer(); context.user_data["channel_weekday"]=int(q.data.split(":",1)[1]); context.user_data["channel_state"]="wtime"; await q.message.reply_text("⏰ ساعت هفتگی:",reply_markup=channel_time_keyboard("chwtime"))

@subscription_required
async def channel_weektime_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔ دسترسی ندارید.",show_alert=True); return
    await q.answer(); v=q.data.split(":",1)[1]
    if v=="custom": context.user_data["channel_state"]="wtime_custom"; await q.message.reply_text("🕐 ساعت را بفرست، مثال 18:30"); return
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
            set_channel_config(normalized)
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
    if s=="content": context.user_data["channel_content"]=text; context.user_data["channel_state"]="choose"; await update.message.reply_text("📅 زمان انتشار را انتخاب کن:",reply_markup=channel_schedule_keyboard()); return True
    if s=="once":
        try:
            dt=datetime.strptime(text,"%Y-%m-%d %H:%M").replace(tzinfo=TZ)
            if dt<=datetime.now(TZ): raise ValueError
            await save_channel_post(context,uid,"once",None,None,dt.isoformat(),update.message)
        except ValueError: await update.message.reply_text("❌ فرمت اشتباه است. مثال: 2026-08-20 18:30")
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
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=admin_keyboard())

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
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=admin_keyboard())

    elif action == "goals":
        c = db()
        rows = c.execute(
            """SELECT category,COUNT(*) AS n
               FROM goals GROUP BY category ORDER BY n DESC"""
        ).fetchall()
        c.close()
        text = "🎯 <b>اهداف بر اساس دسته</b>\n\n"
        text += "\n".join(f"• {r['category']}: <b>{r['n']}</b>" for r in rows) or "موردی نیست."
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=admin_keyboard())

    elif action == "activity":
        c = db()
        rows = c.execute(
            """SELECT activity,COUNT(*) AS n
               FROM activity_log GROUP BY activity ORDER BY n DESC LIMIT 15"""
        ).fetchall()
        c.close()
        text = "📈 <b>فعالیت‌ها</b>\n\n"
        text += "\n".join(f"• {r['activity']}: <b>{r['n']}</b>" for r in rows) or "موردی نیست."
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=admin_keyboard())

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
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=admin_keyboard())

    elif action == "achievements":
        c = db()
        rows = c.execute(
            """SELECT code,COUNT(*) AS n
               FROM achievements GROUP BY code ORDER BY n DESC"""
        ).fetchall()
        c.close()
        text = "🏆 <b>دستاوردها</b>\n\n"
        text += "\n".join(f"• {r['code']}: <b>{r['n']}</b>" for r in rows) or "دستاوردی ثبت نشده."
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=admin_keyboard())

    elif action == "channel":
        await q.message.reply_text(
            "📢 <b>مدیریت کانال و پست‌گذاری</b>\n\n"
            "اتصال کانال، تست اتصال، ساخت پست، مشاهده پست‌ها و انتشار خودکار.",
            parse_mode="HTML",
            reply_markup=channel_keyboard(),
        )

    elif action == "broadcast":
        context.user_data["admin_broadcast"] = True
        await q.message.reply_text(
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
        "🛡 <b>پنل مدیریت حرفه‌ای</b>\n\nیکی از بخش‌ها را انتخاب کن:",
        parse_mode="HTML",
        reply_markup=admin_keyboard(),
    )


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
        await q.message.reply_text(T[lang(uid)]["admin_denied"])
        return
    context.user_data["admin_broadcast"] = True
    await q.message.reply_text(T[lang(uid)]["broadcast_prompt"])


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
    rows = c.execute("SELECT user_id FROM users").fetchall()
    c.close()

    sent = 0
    for row in rows:
        try:
            await context.bot.send_message(row["user_id"], f"📢 {text}")
            sent += 1
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
            text=f"🌙 <b>گزارش روزانه تو</b>\n\n🎯 امروز: {td}/{tt} هدف انجام شد ({tp}٪)\n📅 دیروز: {yd}/{yt} هدف انجام شد ({yp}٪)\n\n{trend}: {sign}{diff}٪ نسبت به دیروز\n⭐ XP فعلی: {xp}\n\nفردا یک قدم بهتر شروع می‌کنیم. 💪"
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
    goals = c.execute(
        """SELECT g.* FROM goals g
           JOIN users u ON u.user_id=g.user_id
           WHERE g.enabled=1
             AND g.reminder_time=?
             AND COALESCE(u.blocked,0)=0""",
        (hhmm,),
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
                        "⏱ Snooze" if lang(uid) == "en" else "⏱ یادآوری بعداً",
                        callback_data=f"snooze_menu:{g['id']}",
                    ),
                ]]),
            )
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

def jalali_date_str(value):
    try:
        d=value if hasattr(value,'year') else datetime.fromisoformat(str(value)[:10]).date(); y,m,day=gregorian_to_jalali(d.year,d.month,d.day); return f"{y:04d}/{m:02d}/{day:02d}"
    except Exception: return str(value)

# ================= CUSTOMER / APPOINTMENT MODULE =================
CUSTOMER_REMINDER_OPTIONS=[1,5,10,30,60,120,1440]
BUSINESS_TYPES_FA=["💇 آرایشگر / سالن","🎨 تتو آرتیست","🔧 تعمیرکار","🩺 خدمات پزشکی","💆 زیبایی / ماساژ","🏋️ مربی","📚 مدرس / مشاور","📸 عکاس","🛠️ خدمات تخصصی","✏️ سایر"]
BUSINESS_TYPES_EN=["💇 Barber / Salon","🎨 Tattoo Artist","🔧 Repairer","🩺 Medical Services","💆 Beauty / Massage","🏋️ Coach","📚 Teacher / Consultant","📸 Photographer","🛠️ Professional Services","✏️ Other"]

def customer_feature_allowed(uid):
    mode=feature_access_mode("customers",uid)
    return feature_enabled("customers") and mode!="off" and (mode!="vip" or is_vip(uid) or uid in ADMIN_IDS)

def ensure_business_profile(uid):
    now=datetime.now(TZ).isoformat(); token=hashlib.sha256(f"booking:{uid}".encode()).hexdigest()[:20]
    c=db(); c.execute("INSERT OR IGNORE INTO business_profiles(user_id,business_type,business_name,contact_phone,contact_telegram,contact_instagram,booking_enabled,booking_token,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(uid,"","","","","",1,token,now,now))
    for wd in range(7): c.execute("INSERT OR IGNORE INTO working_hours(owner_user_id,weekday,start_time,end_time,enabled) VALUES(?,?,?,?,?)",(uid,wd,"09:00","20:00",1))
    c.commit(); r=c.execute("SELECT * FROM business_profiles WHERE user_id=?",(uid,)).fetchone(); c.close(); return r

def customer_keyboard(uid):
    fa=lang(uid)=="fa"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 نوبت‌های امروز" if fa else "📅 Today's Appointments",callback_data="cust:today"),InlineKeyboardButton("➕ نوبت جدید" if fa else "➕ New Appointment",callback_data="cust:new")],
        [InlineKeyboardButton("👥 مشتریان" if fa else "👥 Customers",callback_data="cust:list"),InlineKeyboardButton("🗓️ تقویم کاری" if fa else "🗓️ Calendar",callback_data="cust:calendar")],
        [InlineKeyboardButton("⏰ ساعات کاری" if fa else "⏰ Working Hours",callback_data="cust:hours"),InlineKeyboardButton("🔔 یادآوری‌ها" if fa else "🔔 Reminders",callback_data="cust:reminders")],
        [InlineKeyboardButton("📊 آمار مشتریان" if fa else "📊 Customer Analytics",callback_data="cust:analytics"),InlineKeyboardButton("🏆 مشتریان وفادار" if fa else "🏆 Loyal Customers",callback_data="cust:loyal")],
        [InlineKeyboardButton("📆 هفتگی/ماهانه/سالانه" if fa else "📆 Weekly/Monthly/Yearly",callback_data="cust:period")],
        [InlineKeyboardButton("🔗 لینک رزرو آنلاین" if fa else "🔗 Online Booking Link",callback_data="cust:link"),InlineKeyboardButton("⚙️ تنظیمات کسب‌وکار" if fa else "⚙️ Business Settings",callback_data="cust:settings")],
        [InlineKeyboardButton("🏠 منوی اصلی" if fa else "🏠 Main Menu",callback_data="nav:main")]] )

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
    ensure_business_profile(uid); await update.message.reply_text("👥 <b>مدیریت مشتری و نوبت‌دهی</b>\n\nپنل مستقل مشتریان، نوبت‌ها، تقویم و یادآوری‌ها.",parse_mode="HTML",reply_markup=customer_keyboard(uid))

async def customer_panel_callback(update,context):
    q=update.callback_query; uid=q.from_user.id; await q.answer()
    if not customer_feature_allowed(uid): await q.message.edit_text(customer_feature_message(uid)); return
    ensure_business_profile(uid); p=q.data.split(":"); a=p[1] if len(p)>1 else "main"
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
    if a=="contact": context.user_data["customer_mode"]="contact"; await q.message.edit_text("📱 از قابلیت ارسال Contact تلگرام استفاده کن و مخاطب را برای ربات بفرست.\n⚠️ ربات به دفترچه مخاطبین خصوصی گوشی دسترسی مستقیم ندارد."); return
    if a=="bizname": context.user_data["customer_mode"]="bizname"; await q.message.edit_text("🏪 نام کسب‌وکار را بفرست یا - برای حذف نام:"); return
    if a=="contacts": context.user_data["customer_mode"]="contact_phone"; context.user_data["business_contact_pending"]={}; await q.message.edit_text("📞 شماره تماس را بفرست یا - بزن. (اختیاری)"); return
    if a=="type":
        types=BUSINESS_TYPES_FA if lang(uid)=="fa" else BUSINESS_TYPES_EN; idx=int(p[2]); c=db(); c.execute("UPDATE business_profiles SET business_type=?,updated_at=? WHERE user_id=?",(types[idx],datetime.now(TZ).isoformat(),uid)); c.commit(); c.close(); await q.message.edit_text("✅ نوع فعالیت ذخیره شد.",reply_markup=customer_keyboard(uid)); return
    if a=="done": await appointment_status(update,context,"done",int(p[2])); return
    if a=="cancel": await appointment_status(update,context,"cancelled",int(p[2])); return
    if a=="reschedule": context.user_data.update(appointment_id=int(p[2]),customer_mode="reschedule_date"); await q.message.edit_text("📅 تاریخ جدید را بفرست. مثال: 2026-08-20"); return
    if a=="cust": await customer_detail(update,context,int(p[2])); return
    if a=="edit": context.user_data.update(customer_mode="edit_name",customer_id=int(p[2])); await q.message.edit_text("✏️ نام جدید مشتری را بفرست:"); return
    if a=="delete":
        c=db(); c.execute("UPDATE customers SET status='inactive',updated_at=? WHERE id=? AND owner_user_id=?",(datetime.now(TZ).isoformat(),int(p[2]),uid)); c.commit(); c.close(); await q.message.edit_text("🗑 مشتری از لیست فعال خارج شد؛ سابقه و فاکتور/نوبت‌های قبلی حذف نشد.",reply_markup=customer_keyboard(uid)); return
    if a=="appt":
        context.user_data.update(customer_id=int(p[2]),customer_mode="appt_date")
        await q.message.edit_text("📅 تاریخ نوبت را بفرست: 2026-08-20")
        return
    if a=="bookdate": await booking_date_menu(update,context,p[2]); return
    if a=="booklink": await booking_date_menu_list(update,context); return
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
    text=f"👤 <b>{html.escape(r['name'])}</b>\n📞 {html.escape(r['phone']) if r['phone'] else '—'}\n🔗 @{html.escape(r['telegram_username']) if r['telegram_username'] else '—'}\n\n{status}\n⭐ امتیاز وفاداری: {score}/100\n📅 کل مراجعه: {visits}\n❌ لغو: {canc}\n\n📋 سابقه:\n"+"\n".join(f"• {a['appointment_date']} {a['appointment_time']} — {a['status']}" for a in hist)
    await q.message.edit_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ نوبت جدید",callback_data=f"cust:appt:{cid}")],[InlineKeyboardButton("✏️ ویرایش مشتری",callback_data=f"cust:edit:{cid}"),InlineKeyboardButton("🗑 حذف مشتری",callback_data=f"cust:delete:{cid}")],[back_button("cust:list",uid=uid),main_menu_button(uid)]]))

async def appointment_detail(update,context,aid):
    q=update.callback_query; uid=q.from_user.id; r=get_appointment(uid,aid)
    if not r:return
    await q.message.edit_text(f"📅 <b>{r['appointment_date']} {r['appointment_time']}</b>\n👤 {html.escape(r['name'])}\n📞 {html.escape(r['phone']) if r['phone'] else '—'}\n🛠️ {html.escape(r['service'] or '—')}\n📝 {html.escape(r['notes'] or '—')}\n🔔 {', '.join(reminder_label(x,lang(uid)=='fa') for x in parse_reminder_list(r['reminder_minutes'])) or 'بدون یادآوری'}",parse_mode="HTML",reply_markup=appointment_reminder_keyboard(uid,aid))

async def customer_today(update,context):
    q=update.callback_query; uid=q.from_user.id; d=datetime.now(TZ).date().isoformat(); c=db(); rows=c.execute("SELECT a.*,c.name,c.phone FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.owner_user_id=? AND a.appointment_date=? ORDER BY a.appointment_time",(uid,d)).fetchall(); c.close(); lines=["🌅 <b>نوبت‌های امروز</b>",""]
    for r in rows: lines.append(f"🕐 <b>{r['appointment_time']}</b> — 👤 {html.escape(r['name'])}"+(f" — 📞 {html.escape(r['phone'])}" if r['phone'] else "")+f" — {'🟢' if r['status']=='booked' else '✅' if r['status']=='done' else '❌'}")
    lines.append(f"\n👥 مجموع: {len(rows)}")
    await q.message.edit_text("\n".join(lines),parse_mode="HTML",reply_markup=customer_back(uid))

async def customer_calendar(update,context):
    q=update.callback_query; uid=q.from_user.id; today=datetime.now(TZ).date(); c=db(); kb=[]
    for i in range(30):
        d=today+timedelta(days=i); iso=d.isoformat(); n=c.execute("SELECT COUNT(*) n FROM appointments WHERE owner_user_id=? AND appointment_date=? AND status='booked'",(uid,iso)).fetchone()["n"]; h=c.execute("SELECT 1 FROM business_holidays WHERE owner_user_id=? AND holiday_date=?",(uid,iso)).fetchone(); kb.append([InlineKeyboardButton(f"{'🔴' if h else '🟢'} {iso} — {n} نوبت",callback_data=f"cust:day:{iso}")])
    c.close(); kb.append([back_button("cust:main",uid=uid)]); await q.message.edit_text("🗓️ <b>تقویم کاری و نوبت‌ها</b>\n۳۰ روز آینده:",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def customer_day(update,context,d):
    q=update.callback_query; uid=q.from_user.id; c=db(); rows=c.execute("SELECT a.*,c.name,c.phone FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.owner_user_id=? AND a.appointment_date=? ORDER BY a.appointment_time",(uid,d)).fetchall(); h=c.execute("SELECT note FROM business_holidays WHERE owner_user_id=? AND holiday_date=?",(uid,d)).fetchone(); c.close(); text=f"📅 <b>{d}</b>\n{'🚫 تعطیل' if h else '🟢 روز کاری'}\n\n"+ ("\n".join(f"🕐 {r['appointment_time']} — {html.escape(r['name'])}" + (f" — 📞 {html.escape(r['phone'])}" if r['phone'] else "") for r in rows) or "بدون نوبت"); kb=[[InlineKeyboardButton("🚫 باز/تعطیل",callback_data=f"cust:holiday:{d}")],[back_button("cust:calendar",uid=uid)]]; await q.message.edit_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def holiday_toggle(update,context,d):
    q=update.callback_query; uid=q.from_user.id; c=db(); r=c.execute("SELECT id FROM business_holidays WHERE owner_user_id=? AND holiday_date=?",(uid,d)).fetchone()
    if r:c.execute("DELETE FROM business_holidays WHERE id=?",(r["id"],)); msg="🟢 روز باز شد."
    else:c.execute("INSERT INTO business_holidays(owner_user_id,holiday_date,note) VALUES(?,?,?)",(uid,d,"تعطیلی توسط کاربر")); msg="🔴 روز تعطیل شد."
    c.commit(); c.close(); await q.message.edit_text(msg,reply_markup=customer_back(uid,"cust:calendar"))

async def customer_hours(update,context):
    q=update.callback_query; uid=q.from_user.id; c=db(); rows=c.execute("SELECT * FROM working_hours WHERE owner_user_id=? ORDER BY weekday",(uid,)).fetchall(); c.close(); nf=["شنبه","یکشنبه","دوشنبه","سه‌شنبه","چهارشنبه","پنجشنبه","جمعه"]; ne=["Sat","Sun","Mon","Tue","Wed","Thu","Fri"]; kb=[[InlineKeyboardButton(f"{'🟢' if r['enabled'] else '🔴'} {(nf if lang(uid)=='fa' else ne)[r['weekday']]} {r['start_time']}-{r['end_time']}",callback_data=f"cust:hours_edit:{r['weekday']}")] for r in rows]; kb.append([back_button("cust:main",uid=uid)]); await q.message.edit_text("⏰ <b>ساعات کاری</b>\nروی روز بزن و زمان را تغییر بده.",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def customer_reminders(update,context):
    q=update.callback_query; uid=q.from_user.id; c=db(); rows=c.execute("SELECT a.appointment_date,a.appointment_time,a.reminder_minutes,c.name FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.owner_user_id=? AND a.status='booked' AND a.appointment_date>=? ORDER BY a.appointment_date,a.appointment_time LIMIT 50",(uid,datetime.now(TZ).date().isoformat())).fetchall(); c.close(); text="🔔 <b>یادآوری‌های نوبت</b>\n\n"+ ("\n".join(f"{r['appointment_date']} {r['appointment_time']} — {html.escape(r['name'])} — {', '.join(reminder_label(x,lang(uid)=='fa') for x in parse_reminder_list(r['reminder_minutes']))}" for r in rows) or "یادآوری‌ای نیست."); await q.message.edit_text(text,parse_mode="HTML",reply_markup=customer_back(uid))

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
    c=db(); c.execute("UPDATE appointments SET status=?,updated_at=? WHERE id=? AND owner_user_id=?",(status,datetime.now(TZ).isoformat(),aid,uid)); c.execute("INSERT INTO customer_events(owner_user_id,customer_id,appointment_id,event_type,details,created_at) VALUES(?,?,?,?,?,?)",(uid,r['customer_id'],aid,status,"",datetime.now(TZ).isoformat())); c.commit(); c.close(); await q.message.edit_text("✅ نوبت انجام شد و در سابقه مشتری ثبت شد." if status=="done" else "❌ نوبت لغو شد و سابقه حفظ شد.",reply_markup=customer_back(uid))

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
        p=context.user_data.pop("customer_pending",{}); p["notes"]="" if text=="-" else text; now=datetime.now(TZ).isoformat(); c=db(); cid=c.execute("INSERT INTO customers(owner_user_id,name,phone,notes,created_at,updated_at) VALUES(?,?,?,?,?,?)",(uid,p["name"],p.get("phone"),p.get("notes"),now,now)).lastrowid; c.commit(); c.close(); context.user_data.update(customer_id=cid,customer_mode="appt_date"); await update.message.reply_text("✅ مشتری ثبت شد.\n📅 تاریخ نوبت را بفرست: 2026-08-20"); return True
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
        if not tm or has_conflict(uid,d,tm,30,aid): await update.message.reply_text("❌ این زمان آزاد نیست."); return True
        c=db(); c.execute("UPDATE appointments SET appointment_date=?,appointment_time=?,updated_at=? WHERE id=? AND owner_user_id=?",(d,tm,datetime.now(TZ).isoformat(),aid,uid)); c.commit(); c.close(); context.user_data.clear(); await update.message.reply_text("🔄 نوبت جابه‌جا شد.",reply_markup=customer_keyboard(uid)); return True
    return False

async def customer_contact_save(update,context):
    if context.user_data.get("customer_mode")!="contact":return False
    uid=update.effective_user.id; ct=update.message.contact; name=((ct.first_name or "")+" "+(ct.last_name or "")).strip() or "مشتری"; now=datetime.now(TZ).isoformat(); c=db(); c.execute("INSERT INTO customers(owner_user_id,name,phone,telegram_user_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",(uid,name,ct.phone_number,ct.user_id,now,now)); c.commit(); c.close(); context.user_data.pop("customer_mode",None); await update.message.reply_text(f"✅ {name} به مشتریان اضافه شد.",reply_markup=customer_keyboard(uid)); return True

async def customer_booking_start(update,context,token):
    uid=update.effective_user.id; c=db(); p=c.execute("SELECT * FROM business_profiles WHERE booking_token=? AND booking_enabled=1",(token,)).fetchone(); c.close()
    if not p: await update.message.reply_text("❌ لینک رزرو معتبر نیست یا غیرفعال شده."); return
    context.user_data["booking_owner"]=p["user_id"]; await booking_date_menu_list(update,context)

async def booking_date_menu_list(update,context):
    owner=context.user_data.get("booking_owner"); today=datetime.now(TZ).date(); kb=[]
    for i in range(7):
        d=today+timedelta(days=i); slots=available_slots(owner,d.isoformat()) if owner else []; kb.append([InlineKeyboardButton(f"📅 {d.isoformat()} — {'🟢 '+str(len(slots))+' آزاد' if slots else '🔴'}",callback_data=f"cust:bookdate:{d.isoformat()}")])
    if update.callback_query: await update.callback_query.message.reply_text("📅 تاریخ را انتخاب کن:",reply_markup=InlineKeyboardMarkup(kb))
    else: await update.message.reply_text("📅 تاریخ را انتخاب کن:",reply_markup=InlineKeyboardMarkup(kb))

async def booking_date_menu(update,context,d): context.user_data["booking_date"]=d; await booking_slots_for_owner(update,context,context.user_data.get("booking_owner"),d)

async def booking_slots_for_owner(update,context,owner,d):
    q=update.callback_query; slots=available_slots(owner,d) if owner else []; kb=[[InlineKeyboardButton(x,callback_data=f"cust:slot:{x}") for x in slots[i:i+4]] for i in range(0,len(slots),4)]; kb.append([InlineKeyboardButton("↩️ تاریخ دیگر",callback_data="cust:booklink")]); await q.message.edit_text(f"📅 {d}\n\n{'⏰ زمان آزاد را انتخاب کن:' if slots else '❌ زمان آزادی نیست.'}",reply_markup=InlineKeyboardMarkup(kb))

async def booking_slot_select(update,context,tm):
    q=update.callback_query; owner=context.user_data.get("booking_owner"); d=context.user_data.get("booking_date")
    if not owner or tm not in available_slots(owner,d): await q.answer("این زمان دیگر آزاد نیست.",show_alert=True); return
    context.user_data.update(booking_time=tm,customer_mode="public_booking_name"); await q.message.edit_text("👤 نام شما را بفرست:")

async def public_booking_save(update,context):
    mode=context.user_data.get("customer_mode");
    if mode not in ("public_booking_name","public_booking_phone"):return False
    text=update.message.text.strip(); uid=update.effective_user.id
    if mode=="public_booking_name": context.user_data.update(public_name=text,customer_mode="public_booking_phone"); await update.message.reply_text("📞 شماره تلفن را بفرست یا - بزن:"); return True
    owner=context.user_data.get("booking_owner"); d=context.user_data.get("booking_date"); tm=context.user_data.get("booking_time"); phone="" if text=="-" else text; name=context.user_data.get("public_name") or display_name(uid); now=datetime.now(TZ).isoformat();
    c=db(); existing=c.execute("SELECT id FROM customers WHERE owner_user_id=? AND telegram_user_id=? LIMIT 1",(owner,uid)).fetchone(); cid=existing["id"] if existing else c.execute("INSERT INTO customers(owner_user_id,name,phone,telegram_username,telegram_user_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(owner,name,phone,update.effective_user.username or '',uid,now,now)).lastrowid
    if existing:c.execute("UPDATE customers SET name=?,phone=?,telegram_username=?,updated_at=? WHERE id=?",(name,phone,update.effective_user.username or '',now,cid))
    if has_conflict(owner,d,tm,30):c.close(); context.user_data.clear(); await update.message.reply_text("❌ این زمان همین الان پر شد. لطفاً دوباره لینک را باز کن."); return True
    aid=c.execute("INSERT INTO appointments(owner_user_id,customer_id,appointment_date,appointment_time,duration_minutes,service,notes,reminder_minutes,status,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(owner,cid,d,tm,30,'','رزرو آنلاین','30','booked','online',now,now)).lastrowid; c.execute("INSERT INTO customer_events(owner_user_id,customer_id,appointment_id,event_type,details,created_at) VALUES(?,?,?,?,?,?)",(owner,cid,aid,'online_booking','',now)); c.commit(); c.close()
    p=ensure_business_profile(owner); business_name=p["business_name"] or p["business_type"] or "کسب‌وکار"
    try: await context.bot.send_message(owner,f"🔔 نوبت آنلاین جدید\n🏪 {business_name}\n👤 {name}\n📅 {jalali_date_str(d)}\n⏰ {tm}\n📞 {phone or '—'}")
    except Exception:pass
    context.user_data.clear(); await update.message.reply_text(f"✅ رزرو با موفقیت ثبت شد.\n\n🏪 {business_name}\n📅 {jalali_date_str(d)}\n⏰ {tm}\n\n🔔 یادآوری نوبت برای شما ارسال خواهد شد."); return True

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
        "channel_state", "admin_broadcast", "ai_chat", "auto_wait_interval",
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
    if await support_text(update, context):
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
            await update.message.reply_text(
                "📢 <b>مدیریت کانال و پست‌گذاری</b>",
                parse_mode="HTML",
                reply_markup=channel_keyboard(),
            )
        else:
            await update.message.reply_text("⛔ دسترسی ندارید.")
    elif text in ("🛡 پنل مدیریت", "🛡 Admin Panel"):
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
    return InlineKeyboardMarkup([[InlineKeyboardButton("📊 داشبورد",callback_data="adm:stats"),InlineKeyboardButton("👥 کاربران",callback_data="adm:users")],[InlineKeyboardButton("🔎 جستجو",callback_data="adm:search"),InlineKeyboardButton("🧰 ابزار کاربر",callback_data="adm:tools")],[InlineKeyboardButton("📡 کانال و پست‌گذاری",callback_data="adm:channel"),InlineKeyboardButton("👥 مدیریت مشتری",callback_data="adm:customers")],[InlineKeyboardButton("⚙️ قابلیت‌ها",callback_data="adm:features"),InlineKeyboardButton("⭐ XP / VIP",callback_data="adm:xpvip")],[InlineKeyboardButton("🎫 تیکت‌ها",callback_data="adm:tickets"),InlineKeyboardButton("🩺 Health Check",callback_data="adm:health")],[InlineKeyboardButton("📋 گزارش روز",callback_data="adm:report"),InlineKeyboardButton("📢 پیام همگانی",callback_data="adm:broadcast")],[InlineKeyboardButton("🏠 منوی اصلی",callback_data="adm:main")]])

async def admin_user_detail_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔",show_alert=True); return
    await q.answer(); target=int(q.data.split(":",1)[1]); c=db(); u=c.execute("SELECT * FROM users WHERE user_id=?",(target,)).fetchone()
    if not u: c.close(); await q.message.reply_text("❌ کاربر پیدا نشد.",reply_markup=final_admin_keyboard()); return
    usage=c.execute("SELECT COUNT(*) n FROM bot_usage_events WHERE user_id=?",(target,)).fetchone()["n"]; usage30=c.execute("SELECT COUNT(*) n FROM bot_usage_events WHERE user_id=? AND created_at>=?",(target,(datetime.now(TZ)-timedelta(days=30)).isoformat())).fetchone()["n"]
    goals=c.execute("SELECT COUNT(*) n FROM goals WHERE user_id=?",(target,)).fetchone()["n"]; done=c.execute("SELECT COUNT(*) n FROM goal_days WHERE user_id=? AND status='done'",(target,)).fetchone()["n"]; reactions=c.execute("SELECT COUNT(*) n FROM channel_reactions WHERE user_id=?",(target,)).fetchone()["n"]; polls=c.execute("SELECT COUNT(*) n FROM channel_poll_votes WHERE user_id=?",(target,)).fetchone()["n"]; referrals=c.execute("SELECT COUNT(*) n FROM referrals WHERE inviter_id=?",(target,)).fetchone()["n"]; appts=c.execute("SELECT COUNT(*) n FROM appointments WHERE owner_user_id=?",(target,)).fetchone()["n"]; subs=c.execute("SELECT * FROM subscription_history WHERE user_id=? ORDER BY created_at DESC LIMIT 10",(target,)).fetchall(); c.close()
    sub_lines="\n".join(f"• {r['plan']} | {r['duration_days']} روز | {r['source']} | تا {r['expires_at'] or '—'}" for r in subs) or "سابقه‌ای ثبت نشده"
    text=(f"👤 <b>پرونده کاربر</b>\n\nنام: {html.escape(u['first_name'] or 'بدون نام')}\n🆔 ID: <code>{target}</code>\nوضعیت: {'⛔ محدود' if u['blocked'] else '🟢 فعال'}\n💎 اشتراک: {'فعال تا '+(u['vip_until'] or '')[:16] if u['vip_until'] else 'رایگان'}\n⭐ XP: {u['xp']}\n\n📊 <b>آمار استفاده</b>\n🤖 رویدادهای ربات: {usage}\n📅 ۳۰ روز اخیر: {usage30}\n🎯 اهداف: {goals} | انجام‌شده: {done}\n📣 واکنش کانال: {reactions}\n🗳 نظرسنجی: {polls}\n🤝 دعوت موفق: {referrals}\n👥 نوبت‌های کسب‌وکار: {appts}\n\n💳 <b>سوابق اشتراک/تمدید</b>\n{sub_lines}")
    kb=[[InlineKeyboardButton("🚫 محدود کردن" if not u['blocked'] else "🔓 رفع محدودیت",callback_data=f"admu_block:{target}")],[InlineKeyboardButton("🎁 ۷ روز رایگان",callback_data=f"admu_vip:{target}:7"),InlineKeyboardButton("💎 ۳۰ روز",callback_data=f"admu_vip:{target}:30")],[InlineKeyboardButton("♾️ اشتراک نامحدود",callback_data=f"admu_unlimited:{target}")],[InlineKeyboardButton("⬅️ کاربران",callback_data="adm:users")]]
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
    else: c.close(); return
    q.data=f"admu:{target}"; await admin_user_detail_callback(update,context)

async def final_admin_panel_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔ دسترسی ندارید",show_alert=True); return
    await q.answer(); a=q.data.split(":",1)[1]
    if a=="stats":
        s=admin_stats(); text="📊 داشبورد\n\n"+f"👥 کاربران: {s['users']}\n🆕 جدید امروز: {s['new_today']}\n🟢 فعال امروز: {s['active_today']}\n🎯 اهداف: {s['goals']}\n⏰ یادآوری: {s['reminders']}\n🏆 دستاورد: {s['achievements']}"; await q.message.edit_text(text,reply_markup=final_admin_keyboard()); return
    if a=="users":
        c=db(); rows=c.execute("SELECT user_id,first_name,COALESCE(xp,0) xp,blocked,warnings FROM users ORDER BY created_at DESC LIMIT 50").fetchall(); c.close(); kb=[[InlineKeyboardButton(f"👤 {r['first_name'] or 'بدون نام'} | ID: {r['user_id']} | ⭐{r['xp']}",callback_data=f"admu:{r['user_id']}")] for r in rows]; kb.append([InlineKeyboardButton("⬅️ پنل مدیریت",callback_data="adm:stats")]); await q.message.edit_text("👥 <b>تمام کاربران</b>\n\nروی هر کاربر بزن تا پرونده کاملش باز شود.",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb)); return
    if a=="search": context.user_data["admin_tool_mode"]="search"; await q.message.edit_text("🔎 شناسه یا نام کاربر را بفرست:",reply_markup=nav_keyboard(uid)); return
    if a in ("tools","xpvip"): context.user_data["admin_tool_mode"]="tools"; await q.message.edit_text("🧰 دستورات: BLOCK:ID | UNBLOCK:ID | WARN:ID | XP:ID:50 | VIP:ID:30",reply_markup=nav_keyboard(uid)); return
    if a=="features":
        await q.message.edit_text(feature_admin_text(),reply_markup=feature_admin_keyboard()); return
    if a=="main": await q.message.edit_text("🏠 منوی اصلی",reply_markup=keyboard(uid)); return
    if a=="channel": await q.message.edit_text("📡 مدیریت کانال و پست‌گذاری",reply_markup=channel_keyboard()); return
    if a=="customers":
        c=db(); total=c.execute("SELECT COUNT(*) n FROM customers").fetchone()["n"]; appts=c.execute("SELECT COUNT(*) n FROM appointments").fetchone()["n"]; today=datetime.now(TZ).date().isoformat(); today_n=c.execute("SELECT COUNT(*) n FROM appointments WHERE appointment_date=?",(today,)).fetchone()["n"]; c.close()
        mode=feature_access_mode("customers")
        label={"free":"🟢 رایگان","vip":"💎 VIP","off":"🔴 غیرفعال"}.get(mode,mode)
        kb=InlineKeyboardMarkup([[InlineKeyboardButton(f"وضعیت فعلی: {label} — تغییر",callback_data="feat:customers")],[InlineKeyboardButton("⬅️ پنل مدیریت",callback_data="adm:stats")]])
        await q.message.edit_text(f"👥 <b>مدیریت سیستم مشتری</b>\n\n👤 مشتریان: {total}\n📅 کل نوبت‌ها: {appts}\n🌅 نوبت امروز: {today_n}\n\n💎 دسترسی قابلیت: {label}",parse_mode="HTML",reply_markup=kb); return
    if a=="tickets":
        c=db(); rows=c.execute("SELECT id,user_id,subject FROM tickets WHERE status='open' ORDER BY updated_at DESC LIMIT 20").fetchall(); c.close(); await q.message.edit_text("🎫 تیکت‌های باز\n\n"+"\n".join(f"#{r['id']} | {r['user_id']} | {r['subject'] or 'بدون عنوان'}" for r in rows) or "تیکت بازی نیست",reply_markup=final_admin_keyboard()); return
    if a=="health": await run_health_checks(context.bot,uid); await q.message.edit_text(health_text(),reply_markup=final_admin_keyboard()); return
    if a=="report": await build_daily_report(); await q.message.edit_text(get_daily_report_text(),reply_markup=final_admin_keyboard()); return
    if a=="broadcast": context.user_data["admin_broadcast"]=True; await q.message.edit_text("📢 متن پیام را بفرست:",reply_markup=nav_keyboard(uid)); return


FEATURE_LABELS_FA = {
    "ai": "🤖 هوش مصنوعی", "vip": "💎 VIP", "reminders": "⏰ یادآوری",
    "sports": "⚽ ورزش", "nutrition": "🥗 تغذیه", "investing": "💰 سرمایه‌گذاری",
    "self_growth": "🌱 رشد شخصی", "morning": "☀️ پیام صبح", "night": "🌙 پیام شب",
    "auto_publish": "🤖 انتشار خودکار", "images": "🖼 تصاویر", "feedback": "👍 بازخورد",
    "referrals": "🤝 دعوت دوستان", "mini_app": "📱 Mini App", "support": "🎫 پشتیبانی",
    "price_data": "📈 قیمت آنلاین", "approval": "👁 تأیید قبل از انتشار",
    "maintenance": "🛠 حالت تعمیرات", "test_mode": "🧪 تست ۷ روزه", "payments": "💳 پرداخت", "goals": "🎯 اهداف", "weekly": "📅 جدول هفتگی", "stats": "📊 آمار من", "profile": "👤 پروفایل", "achievements": "🏆 دستاوردها", "settings": "⚙️ تنظیمات", "customers": "👥 مشتری و نوبت‌دهی",
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

FEATURE_CATEGORIES={"goals":("🎯 اهداف و کارهای شخصی",["goals","weekly","stats","profile","achievements","reminders","morning","night"]),"customers":("👥 مشتری و نوبت‌دهی",["customers"]),"channel":("📢 کانال و انتشار",["auto_publish","approval","images","feedback"]),"ai":("🤖 هوش مصنوعی و ابزارها",["ai","price_data"]),"engagement":("⭐ XP / VIP / دعوت",["xp","vip","referrals","payments"]),"support":("🎫 پشتیبانی و سیستم",["support","mini_app","maintenance","test_mode"])}

def feature_admin_keyboard():
    buttons=[[InlineKeyboardButton(label,callback_data=f"fcat:{key}")] for key,(label,_) in FEATURE_CATEGORIES.items()]; buttons.append([InlineKeyboardButton("🔧 همه قابلیت‌ها",callback_data="fcat:all")]); buttons.append([InlineKeyboardButton("⬅️ پنل مدیریت",callback_data="adm:stats")]); return InlineKeyboardMarkup(buttons)

async def feature_category_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔",show_alert=True); return
    await q.answer(); cat=q.data.split(":",1)[1]; c=db(); rows={r["key"]:r["enabled"] for r in c.execute("SELECT key,enabled FROM feature_flags").fetchall()}; c.close(); keys=list(rows.keys()) if cat=="all" else FEATURE_CATEGORIES.get(cat,("",[]))[1]
    kb=[]
    for i in range(0,len(keys),2):
        row=[]
        for key in keys[i:i+2]:
            if key in rows: row.append(InlineKeyboardButton(("🟢 " if rows[key] else "🔴 ")+FEATURE_LABELS_FA.get(key,key),callback_data=f"feat:{key}"))
        if row: kb.append(row)
    kb.append([InlineKeyboardButton("⬅️ دسته‌های قابلیت",callback_data="adm:features")]); await q.message.edit_text("⚙️ <b>مدیریت قابلیت‌ها</b>\n\n🟢 فعال = نمایش و دسترسی\n🔴 خاموش = پنهان از کاربر\n\nاطلاعات ذخیره‌شده حذف نمی‌شود.",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))


def feature_admin_text():
    c=db()
    rows=c.execute("SELECT key,enabled FROM feature_flags ORDER BY key").fetchall()
    c.close()
    active=sum(1 for r in rows if r["enabled"])
    return ("⚙️ مدیریت قابلیت‌ها\n\n"
            f"🟢 فعال: {active}\n🔴 خاموش: {len(rows)-active}\n\n"
            "خاموش کردن قابلیت، اطلاعات قبلی کاربران را حذف نمی‌کند.")

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
        await q.answer()
        await q.message.edit_text(
            "🧪 تست ۷ روزه\n\n"
            f"وضعیت: {'🟢 فعال' if test_mode_active() else '🔴 پایان یافته/خاموش'}\n"
            f"زمان باقی‌مانده: {test_mode_remaining()}\n\n"
            "در زمان تست، انتشار خودکار قبل از انتشار نهایی برای Admin پیش‌نمایش می‌شود."
        )
        return
    if key == "customers":
        current=feature_access_mode("customers")
        new_mode={"free":"vip","vip":"off","off":"free"}.get(current,"vip")
        c=db(); c.execute("INSERT INTO feature_access(key,mode,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET mode=excluded.mode,updated_at=excluded.updated_at",("customers",new_mode,datetime.now(TZ).isoformat())); c.commit(); c.close()
        await q.answer({"free":"🟢 رایگان","vip":"💎 VIP","off":"🔴 غیرفعال"}[new_mode])
        await q.message.edit_text(f"👥 دسترسی سیستم مشتری تغییر کرد: {new_mode}",reply_markup=final_admin_keyboard()); return
    current=feature_enabled(key)
    new_value=not current
    set_feature(key,new_value,uid)
    if key=="test_mode" and new_value:
        set_system_setting("test_mode_started_at", datetime.now(TZ).isoformat())
    await q.answer("🟢 روشن شد" if new_value else "🔴 خاموش شد")
    await q.message.edit_text(feature_admin_text(),reply_markup=feature_admin_keyboard())

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
        await update.message.reply_text(f"❌ خطا: {e}"); return True

async def navigation_callback(update,context):
    q=update.callback_query; uid=q.from_user.id; await q.answer(); action=q.data.split(":",1)[1]; clear_flow(context)
    if action=="main":
        try: await q.message.delete()
        except Exception: pass
        await context.bot.send_message(uid,"🏠",reply_markup=keyboard(uid))

def support_keyboard(uid):
    fa=lang(uid)=="fa"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❓ سوالات متداول" if fa else "❓ FAQ", callback_data="support:faq")],
        [InlineKeyboardButton("📝 ارسال تیکت" if fa else "📝 New Ticket", callback_data="support:new")],
        [InlineKeyboardButton("🏠 منوی اصلی" if fa else "🏠 Main Menu", callback_data="support:main")],
    ])

async def support_start(update,context):
    uid=update.effective_user.id; clear_flow(context); await update.message.reply_text("🎫 پشتیبانی\n\nیک گزینه را انتخاب کن:" if lang(uid)=="fa" else "🎫 Support\n\nChoose an option:",reply_markup=support_keyboard(uid))

async def support_callback(update,context):
    q=update.callback_query; uid=q.from_user.id; await q.answer(); action=q.data.split(":",1)[1]
    if action=="main": clear_flow(context); await q.message.reply_text("🏠 منوی اصلی",reply_markup=keyboard(uid)); return
    if action=="faq":
        text=("❓ سوالات متداول\n\n• چطور هدف اضافه کنم؟ از «✏️ هدف خودم می‌نویسم» استفاده کن.\n• چطور زمان یادآوری را عوض کنم؟ از «✏️ ویرایش اهداف».\n• چطور AI را فعال کنم؟ مدیر باید OPENAI_API_KEY را در Railway تنظیم کند.\n• چطور کانال را وصل کنم؟ مدیر ← مدیریت کانال ← تنظیم کانال.")
        await q.message.reply_text(text,reply_markup=support_keyboard(uid)); return
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
    try:
        c=db(); c.execute("INSERT OR IGNORE INTO payments(user_id,payload,currency,total_amount,telegram_charge_id,created_at) VALUES(?,?,?,?,?,?)",(uid,payment.invoice_payload,payment.currency,payment.total_amount,payment.telegram_payment_charge_id,datetime.now(TZ).isoformat()))
        base=datetime.now(TZ); r=c.execute("SELECT vip_until FROM users WHERE user_id=?",(uid,)).fetchone()
        if r and r["vip_until"]:
            try: base=max(base,datetime.fromisoformat(r["vip_until"]))
            except Exception: pass
        until=base+timedelta(days=30); now_iso=datetime.now(TZ).isoformat(); c.execute("UPDATE users SET vip_until=? WHERE user_id=?",(until.isoformat(),uid)); c.execute("INSERT INTO subscription_history(user_id,plan,duration_days,source,amount,started_at,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?)",(uid,"VIP",30,"telegram_stars",payment.total_amount,now_iso,until.isoformat(),now_iso)); c.commit(); c.close()
        add_xp(uid,20,"vip_purchase")
        await update.message.reply_text(f"✅ پرداخت موفق بود. VIP تا {until.strftime('%Y-%m-%d %H:%M')} فعال شد.",reply_markup=keyboard(uid))
    except Exception as e:
        logger.error("Successful payment handling failed: %s",e); await update.message.reply_text("✅ پرداخت ثبت شد؛ فعال‌سازی VIP در حال بررسی است.",reply_markup=keyboard(uid))

async def referral(update,context):
    uid=update.effective_user.id
    c=db(); r=c.execute("SELECT referral_code FROM users WHERE user_id=?",(uid,)).fetchone(); n=c.execute("SELECT COUNT(*) n FROM referrals WHERE inviter_id=?",(uid,)).fetchone()["n"]; c.close()
    code=r["referral_code"] if r and r["referral_code"] else hashlib.sha256(str(uid).encode()).hexdigest()[:10]
    c=db(); c.execute("UPDATE users SET referral_code=? WHERE user_id=?",(code,uid)); c.commit(); c.close()
    # Automatic referral reward: every 10 successful referrals grants 30 days VIP.
    if feature_enabled("referrals") and n>0 and n%10==0:
        c=db(); r=c.execute("SELECT vip_until FROM users WHERE user_id=?",(uid,)).fetchone(); base=datetime.now(TZ)
        if r and r["vip_until"]:
            try: base=max(base,datetime.fromisoformat(r["vip_until"]))
            except Exception: pass
        new_until=base+timedelta(days=30); c.execute("UPDATE users SET vip_until=? WHERE user_id=?",(new_until.isoformat(),uid)); c.commit(); c.close()
    me=await context.bot.get_me(); link=f"https://t.me/{me.username}?start=ref_{code}" if me.username else code
    await update.message.reply_text(f"🤝 دعوت دوستان\n\n{link}\n\n👥 دعوت موفق: {n}\n⭐ امتیاز: {n*20}\n💎 هر ۱۰ دعوت موفق = ۳۰ روز VIP")

def prices_keyboard(uid):
    fa=lang(uid)=="fa"
    labels=[("usd","💵 دلار" if fa else "💵 USD"),("eur","💶 یورو" if fa else "💶 EUR"),("gold18","🪙 طلای ۱۸" if fa else "🪙 18K Gold"),("coin","🪙 سکه امامی" if fa else "🪙 Coin"),("btc","₿ BTC"),("eth","Ξ ETH"),("sp500","📊 S&P 500"),("nasdaq","📊 Nasdaq"),("dow","📊 Dow Jones")]
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
            return f"{float(last_trade)/10:,.0f} تومان"
        except Exception as e:
            logger.warning("Nobitex v3 orderbook %s failed: %s", symbol, e)
        try:
            data = await asyncio.to_thread(
                fetch_url_json,
                f"https://api.nobitex.ir/v2/trades/{symbol}",
            )
            trades = data.get("trades") or []
            if trades:
                return f"{float(trades[0]['price'])/10:,.0f} تومان"
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
            return f"{float(latest)/10:,.0f} تومان"
        except Exception as e:
            logger.warning("Nobitex stats %s failed: %s", symbol, e)
            raise
    if asset in ("sp500","nasdaq","dow"):
        symbols={"sp500":"%5EGSPC","nasdaq":"%5EIXIC","dow":"%5EDJI"}
        data=await asyncio.to_thread(fetch_url_json,f"https://query1.finance.yahoo.com/v8/finance/chart/{symbols[asset]}?range=1d&interval=1m")
        meta=data["chart"]["result"][0]["meta"]
        return f"{meta.get('regularMarketPrice',0):,.2f} USD"
    urls={"usd":"https://www.tgju.org/profile/price_dollar_rl","eur":"https://www.tgju.org/profile/price_eur","gold18":"https://www.tgju.org/profile/geram18","coin":"https://www.tgju.org/profile/sekee"}
    return await asyncio.to_thread(tgju_value,urls[asset]) + (" تومان" if asset in ("usd","eur","gold18","coin") else "")

async def price_callback(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; asset=q.data.split(":",1)[1]
    if asset=="main": await q.message.reply_text("🏠 منوی اصلی",reply_markup=keyboard(uid)); return
    names={"usd":"دلار","eur":"یورو","gold18":"طلای ۱۸ عیار","coin":"سکه امامی","btc":"BTC (بازار ایران)","eth":"ETH (بازار ایران)","sp500":"S&P 500","nasdaq":"Nasdaq","dow":"Dow Jones"}
    assets=list(names) if asset=="all" else [asset]
    lines=["📈 قیمت آنلاین", f"🕒 بروزرسانی: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}", ""]
    for a in assets:
        try: lines.append(f"{names[a]}: {await fetch_price(a)}")
        except Exception as e: lines.append(f"{names[a]}: ❌ دریافت نشد") ; logger.warning("Price %s failed: %s",a,e)
    await q.message.reply_text("\n".join(lines),reply_markup=prices_keyboard(uid))

async def prices(update,context):
    uid=update.effective_user.id
    await update.message.reply_text("📈 قیمت آنلاین\n\nیکی را انتخاب کن:",reply_markup=prices_keyboard(uid))

async def ai_chat_start(update,context):
    uid=update.effective_user.id
    if not user_feature_allowed(uid,"ai"):
        await update.message.reply_text("🤖 چت AI فعلاً غیرفعال است." if lang(uid)=="fa" else "🤖 AI Chat is currently disabled.", reply_markup=keyboard(uid)); return
    api_key=os.environ.get("OPENAI_API_KEY","").strip()
    if not api_key:
        clear_flow(context)
        await update.message.reply_text(
            "⚠️ چت AI فعلاً آماده نیست چون OPENAI_API_KEY در Railway تنظیم نشده است.\n\n"
            "بقیه امکانات ربات بدون AI باید正常 کار کنند.",
            reply_markup=keyboard(uid),
        )
        return
    clear_flow(context)
    context.user_data["ai_chat"]=True
    await update.message.reply_text(
        "🤖 سوالت را بفرست. برای خروج «⬅️ برگشت» یا «🏠 منوی اصلی» را بزن." if lang(uid)=="fa" else
        "🤖 Send your question. Use «⬅️ Back» or «🏠 Main Menu» to exit.",
        reply_markup=nav_keyboard(uid),
    )

async def ai_chat_text(update,context):
    if not context.user_data.get("ai_chat"): return False
    uid=update.effective_user.id; text=update.message.text.strip()
    if text in ("⬅️ برگشت","⬅️ Back","🏠 منوی اصلی","🏠 Main Menu"):
        clear_flow(context)
        await update.message.reply_text("🏠 منوی اصلی",reply_markup=keyboard(uid))
        return True
    api_key=os.environ.get("OPENAI_API_KEY","").strip()
    if not api_key:
        clear_flow(context)
        await update.message.reply_text(
            "⚠️ سرویس AI در دسترس نیست چون OPENAI_API_KEY تنظیم نشده است.\n"
            "از Railway → Variables آن را تنظیم کن؛ بعد دوباره «🤖 چت با AI» را بزن.",
            reply_markup=keyboard(uid),
        )
        return True
    c=db(); c.execute("INSERT OR IGNORE INTO user_settings(user_id) VALUES(?)",(uid,)); r=c.execute("SELECT ai_daily_used,ai_used_date FROM user_settings WHERE user_id=?",(uid,)).fetchone(); today=datetime.now(TZ).date().isoformat(); used=r["ai_daily_used"] if r and r["ai_used_date"]==today else 0; limit=100 if is_vip(uid) else 10
    if used>=limit:
        c.close(); await update.message.reply_text("⛔ سهمیه AI امروز تمام شده است." if lang(uid)=="fa" else "⛔ Your AI quota for today is used up.",reply_markup=nav_keyboard(uid)); return True
    c.close()
    try:
        payload=json.dumps({"model":os.environ.get("OPENAI_MODEL","gpt-5.6-luna"),"input":f"پاسخ کوتاه، مفید و امن به این سوال کاربر بده. اگر موضوع پزشکی یا مالی است، پاسخ را عمومی و غیرقطعی نگه دار: {text}","max_output_tokens":500}).encode("utf-8")
        req=urllib.request.Request("https://api.openai.com/v1/responses",data=payload,headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"},method="POST")
        with urllib.request.urlopen(req,timeout=35) as resp: data=json.loads(resp.read().decode("utf-8"))
        answer=data.get("output_text","").strip() or "پاسخی دریافت نشد."
        c=db(); c.execute("INSERT OR IGNORE INTO user_settings(user_id) VALUES(?)",(uid,)); c.execute("UPDATE user_settings SET ai_daily_used=?,ai_used_date=? WHERE user_id=?",(used+1,today,uid)); c.commit(); c.close()
        await update.message.reply_text(answer,reply_markup=nav_keyboard(uid))
    except Exception as e:
        logger.error("AI chat failed: %s",e)
        clear_flow(context)
        await update.message.reply_text("❌ پاسخ AI دریافت نشد. بخش AI بسته شد تا بقیه امکانات ربات بدون مشکل در دسترس باشند.",reply_markup=keyboard(uid))
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
        "total_users":c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"],
        "usage_events":c.execute("SELECT COUNT(*) n FROM bot_usage_events WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "usage_users":c.execute("SELECT COUNT(DISTINCT user_id) n FROM bot_usage_events WHERE substr(created_at,1,10)=? AND user_id IS NOT NULL",(d,)).fetchone()["n"],
        "goals_created":c.execute("SELECT COUNT(*) n FROM goals WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "poll_participation":c.execute("SELECT COUNT(DISTINCT user_id) n FROM channel_poll_votes WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "reaction_users":c.execute("SELECT COUNT(DISTINCT user_id) n FROM channel_reactions WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],
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
            f"📢 پست‌ها: {x.get('posts',0)}\n"
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
            f"🔥 فعالیت‌های پرتکرار: {top_text}")

async def run_health_checks(bot,admin_id=0):
    checks=[("Bot","OK" if BOT_TOKEN else "ERROR","token")]
    try: c=db(); c.execute("SELECT 1"); c.close(); checks.append(("Database","OK","SQLite"))
    except Exception as e: checks.append(("Database","ERROR",str(e)))
    cfg=get_channel_config()
    if cfg and cfg["channel_id"]:
        try: await bot.get_chat(cfg["channel_id"]); checks.append(("Channel","OK","reachable"))
        except Exception as e: checks.append(("Channel","ERROR",str(e)))
    else: checks.append(("Channel","WARN","not configured"))
    scheduler_ok=bool(getattr(bot,"job_queue",None))
    checks += [("Scheduler","OK" if scheduler_ok else "ERROR","job queue available" if scheduler_ok else "job queue unavailable"),("AI","OK" if (feature_enabled("ai") and os.environ.get("OPENAI_API_KEY","").strip()) else ("OFF" if not feature_enabled("ai") else "WARN"),"key configured" if os.environ.get("OPENAI_API_KEY","").strip() else "OPENAI_API_KEY missing")]
    c=db(); now=datetime.now(TZ).isoformat(); c.executemany("INSERT INTO health_checks(service,status,details,created_at) VALUES(?,?,?,?)",[(a,b,d,now) for a,b,d in checks]); c.commit(); c.close()
def health_text():
    c=db(); rows=c.execute("SELECT service,status FROM health_checks ORDER BY id DESC LIMIT 8").fetchall(); c.close(); return "🩺 Health Check\n\n"+"\n".join(f"{'🟢' if r['status']=='OK' else '🔴' if r['status']=='ERROR' else '🟡'} {r['service']}: {r['status']}" for r in rows)
async def scheduled_health_check_job(context):
    try:
        last=get_system_setting("last_auto_health_check",""); due=True
        if last:
            try: due=(datetime.now(TZ)-datetime.fromisoformat(last)).total_seconds()>=3*86400
            except Exception: due=True
        if not due or not ADMIN_IDS:return
        await run_health_checks(context.bot, next(iter(ADMIN_IDS)))
        report=health_text()+"\n\n🩺 چکاپ دوره‌ای خودکار انجام شد."
        for admin_id in ADMIN_IDS:
            try: await context.bot.send_message(admin_id,report)
            except Exception: pass
        set_system_setting("last_auto_health_check",datetime.now(TZ).isoformat())
    except Exception: logger.exception("Scheduled health check failed")

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
    logger.error("Bot error", exc_info=context.error)
    try:
        uid = update.effective_user.id if update and update.effective_user else None
        if uid:
            clear_flow(context)
            if update.callback_query:
                await update.callback_query.answer("❌ این بخش با خطا روبه‌رو شد؛ به منوی اصلی برگشتیم.", show_alert=True)
                await update.callback_query.message.reply_text("🏠 منوی اصلی", reply_markup=keyboard(uid))
            elif update.message:
                await update.message.reply_text("❌ این بخش با خطا روبه‌رو شد؛ بقیه امکانات همچنان در دسترس‌اند.", reply_markup=keyboard(uid))
    except Exception:
        logger.exception("Failed to send recovery menu")


async def my_id(update, context):
    uid = update.effective_user.id
    await update.message.reply_text(
        f"🆔 شناسه تلگرام شما: <code>{uid}</code>\n\n"
        "این عدد را در Railway → Variables داخل ADMIN_IDS یا ADMIN_ID قرار بده و سرویس را Restart/Redeploy کن.",
        parse_mode="HTML",
    )


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Set BOT_TOKEN in your environment variables.")

    init_db()
    # Safe defaults for automatic channel publishing.
    if not get_auto_setting("interval_minutes", ""):
        set_auto_setting("interval_minutes", "60")
    if not get_auto_setting("category", ""):
        set_auto_setting("category", "random")
    if not get_auto_setting("subcategory", ""):
        set_auto_setting("subcategory", "random")
    c=db()
    c.execute("DELETE FROM auto_post_history WHERE created_at < ?",((datetime.now(TZ)-timedelta(days=45)).isoformat(),))
    c.commit()
    c.close()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", my_id))
    app.add_handler(CommandHandler("admin", admin_command))

    app.add_handler(CallbackQueryHandler(subscription_check_callback, pattern=r"^subcheck$"))
    app.add_handler(CallbackQueryHandler(customer_panel_callback, pattern=r"^cust:"))
    app.add_handler(CallbackQueryHandler(admin_user_detail_callback, pattern=r"^admu:\d+$"))
    app.add_handler(CallbackQueryHandler(admin_user_action_callback, pattern=r"^admu_(block|vip|unlimited):"))
    app.add_handler(CallbackQueryHandler(feature_category_callback, pattern=r"^fcat:"))
    app.add_handler(CallbackQueryHandler(navigation_callback, pattern=r"^nav:"))
    app.add_handler(CallbackQueryHandler(admin_panel_callback, pattern=r"^adm:"))
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
    app.add_handler(CallbackQueryHandler(gender_callback, pattern=r"^gender:"))
    app.add_handler(CallbackQueryHandler(priority_callback, pattern=r"^priority:"))
    app.add_handler(CallbackQueryHandler(duration_callback, pattern=r"^duration:"))
    app.add_handler(CallbackQueryHandler(snooze_menu, pattern=r"^snooze_menu:"))
    app.add_handler(CallbackQueryHandler(snooze_callback, pattern=r"^snooze:"))
    app.add_handler(CallbackQueryHandler(steps_menu, pattern=r"^steps:"))
    app.add_handler(CallbackQueryHandler(step_add_start, pattern=r"^step_add:"))
    app.add_handler(CallbackQueryHandler(step_toggle, pattern=r"^step_toggle:"))
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
    app.add_handler(MessageHandler(filters.CONTACT, customer_contact_save))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_error_handler(error_handler)

    if app.job_queue:
        app.job_queue.run_repeating(reminder_job, interval=60, first=5)
        app.job_queue.run_repeating(morning_job, interval=60, first=10)
        app.job_queue.run_repeating(user_daily_progress_job, interval=60, first=11)
        app.job_queue.run_repeating(send_channel_morning_message, interval=60, first=12)
        app.job_queue.run_repeating(send_night_channel_feedback, interval=60, first=14)
        app.job_queue.run_repeating(channel_scheduler_job, interval=60, first=15)
        app.job_queue.run_repeating(auto_channel_job, interval=60, first=20)
        app.job_queue.run_repeating(final_daily_report_job, interval=60, first=25)
        app.job_queue.run_repeating(scheduled_health_check_job, interval=3600, first=120)
        app.job_queue.run_repeating(customer_reminder_job, interval=60, first=30)
        app.job_queue.run_repeating(customer_morning_job, interval=60, first=35)
        app.job_queue.run_repeating(customer_daily_report_job, interval=60, first=40)

    logger.info("Goal bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
