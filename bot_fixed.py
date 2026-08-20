
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
from PIL import Image, ImageDraw, ImageFont

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
    filters,
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DB_PATH = os.environ.get("DB_PATH", "goals.db")
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


def db():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init_db():
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
    c.execute("""CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,subject TEXT,status TEXT NOT NULL DEFAULT 'open',created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS ticket_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,ticket_id INTEGER,sender_id INTEGER,message TEXT,created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS price_alerts(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,asset TEXT,target REAL,direction TEXT,enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,payload TEXT,currency TEXT,total_amount INTEGER,telegram_charge_id TEXT UNIQUE,created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS favorites(user_id INTEGER,asset TEXT,created_at TEXT NOT NULL,PRIMARY KEY(user_id,asset))""")
    c.execute("""CREATE TABLE IF NOT EXISTS daily_reports(report_date TEXT PRIMARY KEY,data TEXT NOT NULL,created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS health_checks(id INTEGER PRIMARY KEY AUTOINCREMENT,service TEXT,status TEXT,details TEXT,created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS auto_pending(
        id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id TEXT NOT NULL, topic TEXT NOT NULL,
        content TEXT NOT NULL, publish_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL)""")
    now_iso=datetime.now(TZ).isoformat()
    for key in ["ai","vip","reminders","sports","nutrition","investing","self_growth","morning","night","auto_publish","images","feedback","referrals","mini_app","support","price_data","approval"]:
        c.execute("INSERT OR IGNORE INTO feature_flags(key,enabled,updated_at) VALUES(?,?,?)",(key,1,now_iso))
    c.execute("INSERT OR IGNORE INTO feature_flags(key,enabled,updated_at) VALUES('payments',0,?)",(now_iso,))
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


def keyboard(uid):
    rows = [list(row) for row in T[lang(uid)]["menu"]]
    if admin_is_allowed(uid):
        if lang(uid) == "fa":
            rows.append(["📢 مدیریت کانال", "🛡 پنل مدیریت"])
        else:
            rows.append(["📢 Channel Management", "🛡 Admin Panel"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


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
    streak = max((calculate_streak(uid, g["id"]) for g in goals), default=0)
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

def set_channel_config(channel_id):
    c=db(); c.execute("""INSERT INTO channel_config(id,channel_id,enabled,updated_at) VALUES(1,?,1,?)
    ON CONFLICT(id) DO UPDATE SET channel_id=excluded.channel_id, enabled=1, updated_at=excluded.updated_at""",(str(channel_id).strip(),datetime.now(TZ).isoformat())); c.commit(); c.close()

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

def ai_generate_post(topic):
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("OPENAI_MODEL", "gpt-5-mini").strip()
    if api_key:
        try:
            prompt = (
                f"یک پست فارسی کوتاه و مفید برای کانال MyTasks درباره «{topic}» بنویس. "
                "حداکثر 90 کلمه. یک تیتر کوتاه، 2 یا 3 نکته کاربردی و در پایان یک تمرین یک‌خطی بده. "
                "لحن دوستانه و حرفه‌ای باشد. از توضیح اضافه، مقدمه طولانی، ادعاهای قطعی پزشکی یا مالی "
                "و ایموجی زیاد خودداری کن. فقط متن پست را برگردان."
            )
            payload = json.dumps({
                "model": model,
                "input": prompt,
                "max_output_tokens": 260
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
            if text_out:
                return text_out
        except Exception as e:
            logger.error("AI text generation failed: %s", e)

    return (
        f"🎯 {topic}\n\n"
        "یک قدم کوچک اما مشخص انتخاب کن و همان را امروز انجام بده.\n"
        "• هدف را ساده و قابل اندازه‌گیری کن.\n"
        "• برایش زمان مشخص بگذار.\n"
        "• نتیجه را ثبت کن.\n\n"
        "💡 تمرین امروز: فقط ۱۰ دقیقه برای این موضوع وقت بگذار."
    )


def get_auto_topic():
    category = get_auto_setting("category", "random")
    subcategory = get_auto_setting("subcategory", "random")
    if category in AUTO_TOPIC_TREE_FA:
        items = AUTO_TOPIC_TREE_FA[category]
        if subcategory in items:
            return category, subcategory
        return category, items[datetime.now(TZ).toordinal() % len(items)]
    categories = list(AUTO_TOPIC_TREE_FA.keys())
    cat = categories[datetime.now(TZ).toordinal() % len(categories)]
    items = AUTO_TOPIC_TREE_FA[cat]
    return cat, items[datetime.now(TZ).toordinal() % len(items)]


def compact_channel_footer(bot_username, channel_username):
    parts = []
    if channel_username:
        parts.append(f"📢 کانال: {channel_username}")
    if bot_username:
        parts.append(f"🤖 ربات: {bot_username}")
    return "\n\n" + " | ".join(parts) if parts else ""


async def generate_topic_image(topic):
    """Generate a related local PNG image at zero API cost."""
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


async def send_auto_channel_post(context, channel, topic):
    content = ai_generate_post(topic)
    bot_username, channel_username = await get_identity_handles(context.bot, channel)
    content = content[:950] + compact_channel_footer(bot_username, channel_username)

    image = await generate_topic_image(topic)
    try:
        feedback_markup = content_feedback_keyboard(topic)
        if image is not None:
            msg = await context.bot.send_photo(
                chat_id=channel,
                photo=image,
                caption=content,
                reply_markup=feedback_markup,
            )
        else:
            msg = await context.bot.send_message(
                chat_id=channel,
                text=content,
                reply_markup=feedback_markup,
            )
        # ورزش‌های کوتاه یک نظرسنجی جداگانه دارند تا انجام‌شدن تمرین قابل سنجش باشد.
        if any(k in topic for k in ("ورزش", "حرکات", "تمرین")):
            try:
                await context.bot.send_poll(
                    chat_id=channel,
                    question="🏃 تمرین امروز را انجام دادی؟",
                    options=["✅ انجام دادم", "⏳ هنوز نه", "❌ انجام ندادم"],
                    is_anonymous=False,
                )
            except Exception as e:
                logger.warning("Exercise poll failed: %s", e)
        return msg
    except Exception:
        # If image generation/upload fails, never lose the scheduled post.
        msg = await context.bot.send_message(chat_id=channel, text=content)
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
    if not channel or get_auto_setting("enabled","0")!="1": return
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
            category,topic=get_auto_topic(); content=ai_generate_post(topic); bot_username,channel_username=await get_identity_handles(context.bot,channel); content=content[:950]+compact_channel_footer(bot_username,channel_username)
            c=db(); cur=c.execute("INSERT INTO auto_pending(channel_id,topic,content,publish_at,created_at) VALUES(?,?,?,?,?)",(str(channel),topic,content,next_run.isoformat(),now.isoformat())); pid=cur.lastrowid; c.commit(); c.close()
            kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ تأیید انتشار",callback_data=f"appr:{pid}"),InlineKeyboardButton("❌ رد",callback_data=f"apprrej:{pid}")]])
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
                log_activity(ADMIN_IDS[0],"auto_channel_post_approved")
            except Exception as e: logger.error("Approved auto post failed: %s",e)
        c=db(); c.execute("UPDATE auto_pending SET status=CASE WHEN status='approved' THEN 'published' ELSE 'expired' END WHERE channel_id=? AND publish_at=?",(str(channel),next_run.isoformat())); c.commit(); c.close()
        set_auto_setting("last_run",now.isoformat()); set_auto_setting("next_run",(now+timedelta(minutes=interval)).isoformat()); return
    if now<next_run: return
    next_run=now+timedelta(minutes=interval); set_auto_setting("next_run",next_run.isoformat())
    category,topic=get_auto_topic()
    try:
        msg=await send_auto_channel_post(context,channel,topic); set_auto_setting("last_run",now.isoformat()); set_auto_setting("last_message_id",str(msg.message_id)); set_auto_setting("last_category",category); set_auto_setting("last_topic",topic); log_activity(ADMIN_IDS[0] if ADMIN_IDS else 0,"auto_channel_post")
    except Exception as e:
        set_auto_setting("next_run",now.isoformat()); logger.error("Automatic channel post failed: %s",e)


async def approval_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔",show_alert=True); return
    await q.answer(); pid=int(q.data.split(":",1)[1]); c=db(); r=c.execute("SELECT * FROM auto_pending WHERE id=?",(pid,)).fetchone();
    if not r: c.close(); await q.message.reply_text("❌ پیش‌نمایش پیدا نشد."); return
    c.execute("UPDATE auto_pending SET status='approved' WHERE id=?",(pid,)); c.commit(); c.close(); await q.message.reply_text("✅ پست برای انتشار تأیید شد.")

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
    rows.append([InlineKeyboardButton("✏️ زمان دلخواه",callback_data=f"{prefix}:custom")])
    return InlineKeyboardMarkup(rows)

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
            "📡 آیدی یا @username کانال را بفرست.\n"
            "مثال: <code>@MyTasks</code>\n"
            "یا: <code>-1001234567890</code>",
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

def normalize_channel_input(text):
    """Normalize channel input: accept @username, numeric ID, or t.me link."""
    text = text.strip()
    # Handle t.me links
    m = re.match(r'https?://t\.me/(\w+)', text)
    if m:
        return f"@{m.group(1)}"
    # Already a @username or numeric ID
    return text

async def bot_can_manage_channel(bot, channel):
    """Check if the bot is an administrator in the channel with post permission."""
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id=channel, user_id=me.id)
        if member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            return True, "✅ ربات مدیر کانال است."
        return False, "❌ ربات باید Administrator کانال باشد. لطفاً ربات را به عنوان مدیر اضافه کن."
    except Exception as e:
        logger.error("bot_can_manage_channel check failed: %s", e)
        return False, "❌ بررسی دسترسی ربات به کانال ناموفق بود."

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
        except Exception as e: logger.error("Set channel: %s",e); await update.message.reply_text("❌ کانال پیدا نشد یا ربات دسترسی ندارد.")
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


async def morning_job(context):
    now = datetime.now(TZ)
    if now.hour != 7 or now.minute != 0:
        return
    c = db()
    users = c.execute("SELECT user_id FROM users").fetchall()
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


async def reminder_job(context):
    now = datetime.now(TZ)
    hhmm = now.strftime("%H:%M")
    c = db()
    goals = c.execute(
        "SELECT * FROM goals WHERE enabled=1 AND reminder_time=?",
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


async def text_router(update, context):
    uid = update.effective_user.id
    register_user(uid, update.effective_user.first_name or "")
    if not await require_subscription(update, context):
        return
    text = update.message.text.strip()
    if text in ("⬅️ برگشت","⬅️ Back","🏠 منوی اصلی","🏠 Main Menu"):
        clear_flow(context)
        await update.message.reply_text("🏠 منوی اصلی",reply_markup=keyboard(uid))
        return
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

    if await rename_save(update, context):
        return

    menu = T[lang(uid)]["menu"]
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



# ========================= FINAL MYTASKS FEATURES =========================
def feature_enabled(key):
    c=db(); r=c.execute("SELECT enabled FROM feature_flags WHERE key=?",(key,)).fetchone(); c.close(); return bool(r["enabled"]) if r else True

def set_feature(key,enabled,admin_id=0):
    c=db(); c.execute("INSERT INTO feature_flags(key,enabled,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at",(key,int(enabled),datetime.now(TZ).isoformat())); c.commit(); c.close();
    if admin_id: admin_log(admin_id,"feature_toggle",None,f"{key}={int(enabled)}")

def admin_log(admin_id,action,target_user=None,details=""):
    c=db(); c.execute("INSERT INTO admin_logs(admin_id,action,target_user,details,created_at) VALUES(?,?,?,?,?)",(admin_id,action,target_user,details,datetime.now(TZ).isoformat())); c.commit(); c.close()

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
    return InlineKeyboardMarkup([[InlineKeyboardButton("📊 داشبورد",callback_data="adm:stats"),InlineKeyboardButton("👥 کاربران",callback_data="adm:users")],[InlineKeyboardButton("🔎 جستجو",callback_data="adm:search"),InlineKeyboardButton("🧰 ابزار کاربر",callback_data="adm:tools")],[InlineKeyboardButton("📡 کانال و پست‌گذاری",callback_data="adm:channel"),InlineKeyboardButton("⚙️ قابلیت‌ها",callback_data="adm:features")],[InlineKeyboardButton("⭐ XP / VIP",callback_data="adm:xpvip"),InlineKeyboardButton("🎫 تیکت‌ها",callback_data="adm:tickets")],[InlineKeyboardButton("🩺 Health Check",callback_data="adm:health"),InlineKeyboardButton("📋 گزارش روز",callback_data="adm:report")],[InlineKeyboardButton("📢 پیام همگانی",callback_data="adm:broadcast")],[InlineKeyboardButton("🏠 منوی اصلی",callback_data="adm:main")]])

async def final_admin_panel_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔ دسترسی ندارید",show_alert=True); return
    await q.answer(); a=q.data.split(":",1)[1]
    if a=="stats":
        s=admin_stats(); text="📊 داشبورد\n\n"+f"👥 کاربران: {s['users']}\n🆕 جدید امروز: {s['new_today']}\n🟢 فعال امروز: {s['active_today']}\n🎯 اهداف: {s['goals']}\n⏰ یادآوری: {s['reminders']}\n🏆 دستاورد: {s['achievements']}"; await q.message.reply_text(text,reply_markup=final_admin_keyboard()); return
    if a=="users":
        c=db(); rows=c.execute("SELECT user_id,first_name,COALESCE(xp,0) xp,blocked,warnings FROM users ORDER BY created_at DESC LIMIT 30").fetchall(); c.close(); text="👥 کاربران\n\n"+"\n".join(f"{r['first_name'] or 'بدون نام'} | {r['user_id']} | ⭐{r['xp']} | {'⛔' if r['blocked'] else '🟢'} | ⚠️{r['warnings']}" for r in rows); await q.message.reply_text(text or "کاربری نیست",reply_markup=final_admin_keyboard()); return
    if a=="search": context.user_data["admin_tool_mode"]="search"; await q.message.reply_text("🔎 شناسه یا نام کاربر را بفرست:",reply_markup=nav_keyboard(uid)); return
    if a in ("tools","xpvip"): context.user_data["admin_tool_mode"]="tools"; await q.message.reply_text("🧰 دستورات: BLOCK:ID | UNBLOCK:ID | WARN:ID | XP:ID:50 | VIP:ID:30",reply_markup=nav_keyboard(uid)); return
    if a=="features":
        c=db(); rows=c.execute("SELECT key,enabled FROM feature_flags ORDER BY key").fetchall(); c.close(); kb=[]
        for i in range(0,len(rows),2): kb.append([InlineKeyboardButton(("🟢 " if rows[j]["enabled"] else "🔴 " )+rows[j]["key"],callback_data=f"feat:{rows[j]['key']}") for j in range(i,min(i+2,len(rows)))])
        kb.append([InlineKeyboardButton("⬅️ مدیریت",callback_data="adm:stats")]); await q.message.reply_text("⚙️ قابلیت‌ها",reply_markup=InlineKeyboardMarkup(kb)); return
    if a=="main": await q.message.reply_text("🏠 منوی اصلی",reply_markup=keyboard(uid)); return
    if a=="channel": await q.message.reply_text("📡 مدیریت کانال و پست‌گذاری",reply_markup=channel_keyboard()); return
    if a=="tickets":
        c=db(); rows=c.execute("SELECT id,user_id,subject FROM tickets WHERE status='open' ORDER BY updated_at DESC LIMIT 20").fetchall(); c.close(); await q.message.reply_text("🎫 تیکت‌های باز\n\n"+"\n".join(f"#{r['id']} | {r['user_id']} | {r['subject'] or 'بدون عنوان'}" for r in rows) or "تیکت بازی نیست",reply_markup=final_admin_keyboard()); return
    if a=="health": await run_health_checks(context.bot,uid); await q.message.reply_text(health_text(),reply_markup=final_admin_keyboard()); return
    if a=="report": await build_daily_report(); await q.message.reply_text(get_daily_report_text(),reply_markup=final_admin_keyboard()); return
    if a=="broadcast": context.user_data["admin_broadcast"]=True; await q.message.reply_text("📢 متن پیام را بفرست:",reply_markup=nav_keyboard(uid)); return

async def final_feature_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("⛔",show_alert=True); return
    key=q.data.split(":",1)[1]; set_feature(key,not feature_enabled(key),uid); await q.answer("تغییر کرد"); await final_admin_panel_callback(update,context)

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
        until=base+timedelta(days=30); c.execute("UPDATE users SET vip_until=? WHERE user_id=?",(until.isoformat(),uid)); c.commit(); c.close()
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
    if not feature_enabled("ai"):
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
    d=datetime.now(TZ).date().isoformat(); c=db(); data={"posts":c.execute("SELECT COUNT(*) n FROM channel_posts WHERE substr(COALESCE(last_sent_at,created_at),1,10)=?",(d,)).fetchone()["n"],"active":c.execute("SELECT COUNT(DISTINCT user_id) n FROM activity_log WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],"new":c.execute("SELECT COUNT(*) n FROM users WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],"xp":c.execute("SELECT COALESCE(SUM(amount),0) n FROM xp_log WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],"done":c.execute("SELECT COUNT(*) n FROM goal_days WHERE goal_date=? AND status='done'",(d,)).fetchone()["n"],"likes":c.execute("SELECT COUNT(*) n FROM content_feedback WHERE rating=1 AND substr(created_at,1,10)=?",(d,)).fetchone()["n"],"dislikes":c.execute("SELECT COUNT(*) n FROM content_feedback WHERE rating=-1 AND substr(created_at,1,10)=?",(d,)).fetchone()["n"]}; c.execute("INSERT OR REPLACE INTO daily_reports(report_date,data,created_at) VALUES(?,?,?)",(d,json.dumps(data,ensure_ascii=False),datetime.now(TZ).isoformat())); c.commit(); c.close()
def get_daily_report_text():
    d=datetime.now(TZ).date().isoformat(); c=db(); r=c.execute("SELECT data FROM daily_reports WHERE report_date=?",(d,)).fetchone(); c.close(); x=json.loads(r["data"]) if r else {}; return "📋 گزارش پایان روز\n\n"+f"📢 پست‌ها: {x.get('posts',0)}\n🟢 فعال: {x.get('active',0)}\n🆕 جدید: {x.get('new',0)}\n⭐ XP: {x.get('xp',0)}\n✅ اهداف انجام‌شده: {x.get('done',0)}\n👍 مفید: {x.get('likes',0)}\n👎 نامناسب: {x.get('dislikes',0)}"
async def run_health_checks(bot,admin_id=0):
    checks=[("Bot","OK" if BOT_TOKEN else "ERROR","token")]
    try: c=db(); c.execute("SELECT 1"); c.close(); checks.append(("Database","OK","SQLite"))
    except Exception as e: checks.append(("Database","ERROR",str(e)))
    cfg=get_channel_config()
    if cfg and cfg["channel_id"]:
        try: await bot.get_chat(cfg["channel_id"]); checks.append(("Channel","OK","reachable"))
        except Exception as e: checks.append(("Channel","ERROR",str(e)))
    else: checks.append(("Channel","WARN","not configured"))
    checks += [("Scheduler","OK","configured"),("AI","OK" if (feature_enabled("ai") and os.environ.get("OPENAI_API_KEY","").strip()) else ("OFF" if not feature_enabled("ai") else "WARN"),"key configured" if os.environ.get("OPENAI_API_KEY","").strip() else "OPENAI_API_KEY missing")]
    c=db(); now=datetime.now(TZ).isoformat(); c.executemany("INSERT INTO health_checks(service,status,details,created_at) VALUES(?,?,?,?)",[(a,b,d,now) for a,b,d in checks]); c.commit(); c.close()
def health_text():
    c=db(); rows=c.execute("SELECT service,status FROM health_checks ORDER BY id DESC LIMIT 8").fetchall(); c.close(); return "🩺 Health Check\n\n"+"\n".join(f"{'🟢' if r['status']=='OK' else '🔴' if r['status']=='ERROR' else '🟡'} {r['service']}: {r['status']}" for r in rows)
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

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", my_id))
    app.add_handler(CommandHandler("admin", admin_command))

    app.add_handler(CallbackQueryHandler(subscription_check_callback, pattern=r"^subcheck$"))
    app.add_handler(CallbackQueryHandler(admin_panel_callback, pattern=r"^adm:"))
    app.add_handler(CallbackQueryHandler(channel_panel_callback, pattern=r"^ch:"))
    app.add_handler(CallbackQueryHandler(auto_channel_callback, pattern=r"^auto:"))
    app.add_handler(CallbackQueryHandler(auto_category_callback, pattern=r"^autocat:"))
    app.add_handler(CallbackQueryHandler(auto_subcategory_callback, pattern=r"^autosub:"))
    app.add_handler(CallbackQueryHandler(auto_interval_callback, pattern=r"^autoint:"))
    app.add_handler(CallbackQueryHandler(approval_callback, pattern=r"^appr:"))
    app.add_handler(CallbackQueryHandler(approval_reject_callback, pattern=r"^apprrej:"))
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_error_handler(error_handler)

    if app.job_queue:
        app.job_queue.run_repeating(reminder_job, interval=60, first=5)
        app.job_queue.run_repeating(morning_job, interval=60, first=10)
        app.job_queue.run_repeating(channel_scheduler_job, interval=60, first=15)
        app.job_queue.run_repeating(auto_channel_job, interval=60, first=20)
        app.job_queue.run_repeating(daily_report_job, interval=60, first=25)

    logger.info("Goal bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
