
import logging
from functools import wraps
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
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
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
            ["🎯 اهداف امروز", "➕ هدف جدید"],
            ["🏆 اهداف آماده", "✏️ ویرایش اهداف"],
            ["📅 جدول هفتگی", "📊 آمار من"],
            ["👤 پروفایل", "🏆 دستاوردها"],
            ["⚙️ تنظیمات"],
        ],
        "today": "🎯 اهداف امروز",
        "no_goals": "🎯 {name} عزیز، هنوز هدفی ثبت نکردی.\nاز «➕ هدف جدید» شروع کنیم؟",
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
            ["🎯 Today's Goals", "➕ New Goal"],
            ["🏆 Ready Goals", "✏️ Edit Goals"],
            ["📅 Weekly Table", "📊 My Stats"],
            ["👤 Profile", "⚙️ Settings"],
        ],
        "today": "🎯 Today's Goals",
        "no_goals": "🎯 {name}, you have no goals yet.\nLet's start with «➕ New Goal».",
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
        rows.append(["🛡 پنل مدیریت"] if lang(uid) == "fa" else ["🛡 Admin Panel"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


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


def add_goal(uid, name, category, reminder, priority=2):
    c = db()
    c.execute(
        "INSERT INTO goals(user_id,name,category,reminder_time,priority,created_at) VALUES(?,?,?,?,?,?)",
        (uid, name, category, reminder, priority, datetime.now(TZ).isoformat()),
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
    streak = max((calculate_streak(uid, g["id"]) for g in goals), default=0)

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
    rows.append([InlineKeyboardButton(T[lang(uid)]["back"], callback_data="newback")])
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


def normalize_channel_input(value):
    """Normalize @username, t.me links, or numeric channel IDs."""
    value = (value or "").strip()
    value = value.replace("\u200c", "").strip()
    if not value:
        return ""
    # Accept public Telegram links such as https://t.me/MyTasks or t.me/MyTasks.
    m = re.match(r"^(?:https?://)?t\.me/([A-Za-z0-9_]{4,})/?$", value, re.I)
    if m:
        return "@" + m.group(1)
    if value.startswith("@"):
        return "@" + value[1:].strip()
    # Telegram numeric chat IDs can be negative (channels normally start with -100).
    if re.fullmatch(r"-?\d+", value):
        return value
    return value


def required_channel_url():
    if REQUIRED_CHANNEL_URL:
        return REQUIRED_CHANNEL_URL
    channel = required_channel()
    if channel.startswith("@"):
        return f"https://t.me/{channel[1:]}"
    return ""


async def bot_can_manage_channel(bot, channel):
    """Return (ok, message) after checking that the bot can actually manage/post."""
    try:
        chat = await bot.get_chat(channel)
    except Exception as e:
        logger.error("get_chat failed for %s: %s", channel, e)
        return False, "❌ کانال پیدا نشد. @username یا ID کانال را درست وارد کن."

    if getattr(chat, "type", None) != "channel":
        return False, "❌ این شناسه مربوط به کانال نیست. فقط Channel را وارد کن."

    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat.id, me.id)
        status = getattr(member, "status", None)
        if status not in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}:
            return False, "❌ ربات ادمین کانال نیست. ربات را Administrator کانال کن و اجازه ارسال پیام بده."
        if status == ChatMemberStatus.ADMINISTRATOR and getattr(member, "can_post_messages", True) is False:
            return False, "❌ ربات ادمین است ولی اجازه ارسال پیام در کانال را ندارد."
    except Exception as e:
        logger.error("bot channel permission check failed for %s: %s", channel, e)
        return False, "❌ دسترسی ربات به کانال تأیید نشد. ربات را Administrator کانال کن."

    return True, chat


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


async def settings(update, context):
    uid = update.effective_user.id
    log_activity(uid, "settings")
    await update.message.reply_text(
        T[lang(uid)]["settings"],
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🇮🇷 فارسی", callback_data="language:fa"),
                InlineKeyboardButton("🇬🇧 English", callback_data="language:en"),
            ]
        ]),
    )


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



@subscription_required
async def priority_callback(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    value = int(q.data.split(":")[1])
    context.user_data["priority"] = value
    await q.message.reply_text(
        T[lang(uid)]["choose_time"],
        reply_markup=time_keyboard(uid),
    )


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
    add_goal(uid, name, category, reminder, priority)
    context.user_data.clear()
    log_activity(uid, "goal_created")
    await q.message.reply_text(
        T[lang(uid)]["goal_added"].format(name=display_name(uid)),
        reply_markup=keyboard(uid),
    )


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
    add_goal(uid, name, category, reminder, priority)
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
        ]]),
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
    await update.message.reply_text(
        T[lang(uid)]["edit"].format(name=display_name(uid)),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(g["name"], callback_data=f"edit:{g['id']}")]
            for g in goals
        ]),
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


AUTO_TOPICS_FA = [
    "موفقیت و رشد فردی", "هدف‌گذاری", "عادت‌های خوب", "مدیریت زمان",
    "تمرکز", "اعتمادبه‌نفس", "یادگیری", "انضباط شخصی", "مطالعه",
    "کسب‌وکار و پیشرفت شغلی"
]

def ai_generate_post(topic):
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("OPENAI_MODEL", "gpt-5-mini").strip()
    if api_key:
        try:
            import urllib.request, json
            prompt = (
                f"یک پست فارسی دوستانه، آموزشی و کاربردی درباره «{topic}» بنویس. "
                "حدود 180 تا 280 کلمه، با تیتر، 3 نکته عملی، یک تمرین کوتاه و یک سؤال تعاملی. "
                "از ادعاهای قطعی پزشکی/مالی و اطلاعات ساختگی خودداری کن. ایموجی‌ها متعادل باشند."
            )
            payload = json.dumps({
                "model": model, "input": prompt, "max_output_tokens": 700
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.openai.com/v1/responses", data=payload,
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=35) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = data.get("output_text", "").strip()
            if text:
                return text
        except Exception as e:
            logger.error("AI generation failed: %s", e)

    templates = [
        f"🎯 {topic}\n\n"
        "موفقیت از چند کار کوچک و تکرارشونده ساخته می‌شود.\n\n"
        "1️⃣ هدف را کوچک و مشخص کن.\n"
        "2️⃣ برای انجامش زمان مشخص بگذار.\n"
        "3️⃣ نتیجه امروزت را ثبت کن.\n\n"
        "💡 تمرین امروز: فقط یک قدم کوچک برای هدف اصلی‌ات انجام بده.\n\n"
        "تو امروز چه قدمی برمی‌داری؟ 👇",
        f"✨ یک نکته مهم درباره {topic}\n\n"
        "به‌جای منتظر ماندن برای انگیزه، یک زمان ثابت برای عمل کردن تعیین کن.\n\n"
        "🔥 قانون ساده: کوچک شروع کن، ولی ادامه بده.\n\n"
        "📝 تمرین: یک کار ۱۰ دقیقه‌ای انتخاب کن و همین امروز انجامش بده.\n\n"
        "انجامش دادی؟ ❤️"
    ]
    import hashlib
    pick = int(hashlib.sha256(
        f"{topic}-{datetime.now(TZ).date().isoformat()}".encode()
    ).hexdigest(), 16) % len(templates)
    return templates[pick]


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
    """Publish an automatically generated channel post on schedule."""
    cfg = get_channel_config()
    channel = cfg["channel_id"] if cfg else ""
    if not channel or get_auto_setting("enabled", "0") != "1":
        return

    now = datetime.now(TZ)
    schedule_type = get_auto_setting("type", "daily")
    due = False

    if schedule_type == "interval":
        interval_hours = int(get_auto_setting("interval_hours", "3") or 3)
        interval_hours = max(1, min(interval_hours, 168))
        next_run_raw = get_auto_setting("next_run", "")
        if not next_run_raw:
            next_run = now + timedelta(hours=interval_hours)
            set_auto_setting("next_run", next_run.isoformat())
            return
        try:
            next_run = datetime.fromisoformat(next_run_raw)
            if next_run.tzinfo is None:
                next_run = next_run.replace(tzinfo=TZ)
            due = now >= next_run
        except ValueError:
            set_auto_setting("next_run", now.isoformat())
            due = True
    else:
        schedule_time = get_auto_setting("time", "18:00")
        weekday = int(get_auto_setting("weekday", "0") or 0)
        due = now.strftime("%H:%M") == schedule_time
        if schedule_type == "weekly":
            due = due and now.weekday() == weekday

    if not due:
        return

    # The scheduler checks every minute; this prevents duplicate posts.
    stamp = now.strftime("%Y-%m-%d-%H:%M")
    if schedule_type != "interval" and get_auto_setting("last_run", "") == stamp:
        return

    topic_index = int(get_auto_setting("topic_index", "0") or 0)
    topic = AUTO_TOPICS_FA[topic_index % len(AUTO_TOPICS_FA)]
    content = ai_generate_post(topic)

    try:
        msg = await context.bot.send_message(chat_id=channel, text=content)
        set_auto_setting("last_run", stamp)
        set_auto_setting("last_message_id", str(msg.message_id))
        set_auto_setting("topic_index", str((topic_index + 1) % len(AUTO_TOPICS_FA)))

        if schedule_type == "interval":
            interval_hours = int(get_auto_setting("interval_hours", "3") or 3)
            interval_hours = max(1, min(interval_hours, 168))
            set_auto_setting("next_run", (now + timedelta(hours=interval_hours)).isoformat())

        log_activity(ADMIN_IDS[0] if ADMIN_IDS else 0, "auto_channel_post")
        logger.info("Automatic channel post published: %s", msg.message_id)
    except Exception as e:
        logger.error("Automatic channel post failed: %s", e)

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
    rows.append([InlineKeyboardButton("🕐 ساعت دیگر",callback_data=f"{prefix}:custom")]); return InlineKeyboardMarkup(rows)

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
    state = "🟢 خودکار روشن" if enabled else "⚪ خودکار خاموش"
    mode = get_auto_setting("type", "daily")
    interval_hours = get_auto_setting("interval_hours", "3")
    next_run = get_auto_setting("next_run", "")

    rows = [
        [InlineKeyboardButton(state, callback_data="auto:toggle")],
        [InlineKeyboardButton("⏱ هر ۲ ساعت", callback_data="auto:2h"),
         InlineKeyboardButton("⏱ هر ۳ ساعت", callback_data="auto:3h")],
        [InlineKeyboardButton("🔄 روزانه", callback_data="auto:daily"),
         InlineKeyboardButton("📆 هفتگی", callback_data="auto:weekly")],
        [InlineKeyboardButton("⏰ تغییر ساعت", callback_data="auto:time")],
        [InlineKeyboardButton("🧠 موضوعات", callback_data="auto:topics")],
    ]

    if mode == "interval":
        rows.insert(1, [InlineKeyboardButton(f"📌 فعال: هر {interval_hours} ساعت", callback_data="auto:info")])
        if next_run:
            rows.insert(2, [InlineKeyboardButton(
                f"🕐 بعدی: {next_run.replace('T', ' ')[:16]}",
                callback_data="auto:info"
            )])

    rows.append([InlineKeyboardButton("⬅️ مدیریت کانال", callback_data="ch:main")])
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
        cur = get_auto_setting("enabled", "0")
        new_value = "0" if cur == "1" else "1"
        set_auto_setting("enabled", new_value)
        if new_value == "1" and get_auto_setting("type", "daily") == "interval":
            hours = int(get_auto_setting("interval_hours", "3") or 3)
            set_auto_setting("next_run", (datetime.now(TZ) + timedelta(hours=hours)).isoformat())
        await q.message.reply_text(
            "🟢 انتشار خودکار روشن شد." if new_value == "1" else "⚪ انتشار خودکار خاموش شد.",
            reply_markup=auto_channel_keyboard()
        )
    elif action in ("2h", "3h"):
        hours = 2 if action == "2h" else 3
        set_auto_setting("type", "interval")
        set_auto_setting("interval_hours", str(hours))
        set_auto_setting("next_run", (datetime.now(TZ) + timedelta(hours=hours)).isoformat())
        set_auto_setting("enabled", "1")
        await q.message.reply_text(
            f"✅ انتشار خودکار روی هر {hours} ساعت تنظیم و روشن شد.\n\n"
            f"📌 اولین انتشار حدود {hours} ساعت دیگر انجام می‌شود.\n"
            "🤖 بعد از هر انتشار، موضوع بعدی به‌صورت خودکار انتخاب می‌شود.",
            reply_markup=auto_channel_keyboard()
        )
    elif action == "info":
        await q.message.reply_text(
            "ℹ️ ربات طبق فاصله انتخاب‌شده، خودش پست جدید تولید و در کانال منتشر می‌کند.",
            reply_markup=auto_channel_keyboard()
        )
    elif action in ("daily", "weekly"):
        set_auto_setting("type", action)
        await q.message.reply_text(
            "🔄 حالت روزانه تنظیم شد." if action == "daily" else "📆 حالت هفتگی تنظیم شد.",
            reply_markup=auto_channel_keyboard()
        )
    elif action == "time":
        context.user_data["auto_wait_time"] = True
        await q.message.reply_text("⏰ ساعت را بفرست، مثلاً 18:00")
    elif action == "topics":
        await q.message.reply_text(
            "🧠 موضوعات خودکار:\n\n" + "\n".join("• " + x for x in AUTO_TOPICS_FA),
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
    channel = cfg["channel_id"] if cfg and cfg["channel_id"] else ""

    if action == "main":
        current = channel or "تنظیم نشده"
        await q.message.reply_text(
            f"📡 <b>مدیریت کانال</b>\n\n📢 کانال فعلی: <code>{current}</code>",
            parse_mode="HTML",
            reply_markup=channel_keyboard(),
        )

    elif action == "set":
        context.user_data["channel_state"] = "set"
        await q.message.reply_text(
            "📡 شناسه کانال را بفرست.\n\n"
            "مثال کانال عمومی: <code>@MyTasks</code>\n"
            "یا: <code>https://t.me/MyTasks</code>\n"
            "برای کانال خصوصی: <code>-1001234567890</code>",
            parse_mode="HTML",
        )

    elif action == "test":
        if not channel:
            await q.message.reply_text("❌ هنوز کانالی تنظیم نشده است.", reply_markup=channel_keyboard())
            return
        ok, result = await bot_can_manage_channel(context.bot, channel)
        if ok:
            await q.message.reply_text(
                f"✅ اتصال کامل است.\n📢 {result.title or channel}\n🆔 <code>{result.id}</code>\n\n"
                "عضویت اجباری و انتشار پست‌ها می‌توانند از همین کانال استفاده کنند.",
                parse_mode="HTML",
                reply_markup=channel_keyboard(),
            )
        else:
            await q.message.reply_text(result, reply_markup=channel_keyboard())

    elif action == "auto":
        await q.message.reply_text(
            "🤖 <b>انتشار خودکار کانال</b>\n\n"
            "ربات خودش برای هر نوبت یک پست جدید می‌سازد و در کانال منتشر می‌کند.\n\n"
            "⏱ می‌توانی روی هر ۲ ساعت یا هر ۳ ساعت بگذاری، یا حالت روزانه/هفتگی را انتخاب کنی.\n"
            "🧠 موضوعات به‌صورت چرخشی انتخاب می‌شوند تا پست‌ها پشت‌سرهم یک موضوع نباشند.",
            parse_mode="HTML",
            reply_markup=auto_channel_keyboard(),
        )

    elif action == "new":
        if not channel:
            await q.message.reply_text("❌ ابتدا کانال را تنظیم و تست کن.", reply_markup=channel_keyboard())
            return
        context.user_data["channel_state"] = "content"
        await q.message.reply_text("📝 متن پست را بفرست:")

    elif action == "list":
        c = db()
        rows = c.execute(
            "SELECT * FROM channel_posts WHERE enabled=1 ORDER BY id DESC LIMIT 20"
        ).fetchall()
        c.close()
        text = "📋 <b>پست‌های فعال</b>\n\n" + (
            "\n".join(
                f"#{r['id']} — {channel_schedule_text(r)}\n📝 {r['content'][:60]}"
                for r in rows
            )
            if rows else "موردی نیست."
        )
        await q.message.reply_text(
            text, parse_mode="HTML", reply_markup=channel_keyboard()
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
        channel = normalize_channel_input(text)
        if not channel:
            await update.message.reply_text("❌ شناسه کانال خالی است.")
            return True
        ok, result = await bot_can_manage_channel(context.bot, channel)
        if not ok:
            await update.message.reply_text(result, reply_markup=channel_keyboard())
            return True
        # Keep @username when the channel is public so the membership button
        # can be generated automatically; otherwise keep the numeric ID.
        stored_channel = f"@{result.username}" if getattr(result, "username", None) else str(result.id)
        set_channel_config(stored_channel)
        context.user_data.pop("channel_state", None)
        await update.message.reply_text(
            f"✅ کانال با موفقیت وصل شد.\n\n"
            f"📢 {result.title or channel}\n"
            f"🆔 <code>{result.id}</code>\n\n"
            "حالا عضویت اجباری، ارسال فوری، زمان‌بندی و انتشار خودکار از همین کانال استفاده می‌کنند.",
            parse_mode="HTML",
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
        [InlineKeyboardButton("📡 مدیریت کانال", callback_data="adm:channel")],
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
        await q.message.reply_text("📢 <b>مدیریت کانال</b>\n\nاتصال کانال، ساخت پست و زمان‌بندی روزانه/هفتگی.",parse_mode="HTML",reply_markup=channel_keyboard())

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

    if await channel_text_save(update, context):
        return

    if await admin_broadcast_save(update, context):
        return

    if await custom_time_save(update, context):
        return

    if await custom_edit_time_save(update, context):
        return

    if await rename_save(update, context):
        return

    menu = T[lang(uid)]["menu"]
    if text in (menu[0][0], "🎯 اهداف امروز", "🎯 Today's Goals"):
        await today(update, context)
    elif text in (menu[0][1], "➕ هدف جدید", "➕ New Goal"):
        await new_goal(update, context)
    elif text in (menu[1][0], "🏆 اهداف آماده", "🏆 Ready Goals"):
        await ready_menu(update, context)
    elif text in (menu[1][1], "✏️ ویرایش اهداف", "✏️ Edit Goals"):
        await edit_menu(update, context)
    elif text in (menu[2][0], "📅 جدول هفتگی", "📅 Weekly Table"):
        await weekly(update, context)
    elif text in (menu[2][1], "📊 آمار من", "📊 My Stats"):
        await stats(update, context)
    elif text in (menu[3][0], "👤 پروفایل", "👤 Profile"):
        await profile(update, context)
    elif text in (menu[3][1], "🏆 دستاوردها", "🏆 Achievements"):
        await achievements(update, context)
    elif text in (menu[4][0], "⚙️ تنظیمات", "⚙️ Settings"):
        await settings(update, context)
    elif text in ("🛡 پنل مدیریت", "🛡 Admin Panel"):
        await admin_command(update, context)
    else:
        log_activity(uid, "text_message")


async def error_handler(update, context):
    logger.error("Bot error", exc_info=context.error)


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

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", my_id))
    app.add_handler(CommandHandler("admin", admin_command))

    app.add_handler(CallbackQueryHandler(subscription_check_callback, pattern=r"^subcheck$"))
    app.add_handler(CallbackQueryHandler(admin_panel_callback, pattern=r"^adm:"))
    app.add_handler(CallbackQueryHandler(channel_panel_callback, pattern=r"^ch:"))
    app.add_handler(CallbackQueryHandler(auto_channel_callback, pattern=r"^auto:"))
    app.add_handler(CallbackQueryHandler(channel_schedule_callback, pattern=r"^chs:"))
    app.add_handler(CallbackQueryHandler(channel_daily_callback, pattern=r"^chd:"))
    app.add_handler(CallbackQueryHandler(channel_weekday_callback, pattern=r"^chw:"))
    app.add_handler(CallbackQueryHandler(channel_weektime_callback, pattern=r"^chwtime:"))
    app.add_handler(CallbackQueryHandler(language_callback, pattern=r"^language:"))
    app.add_handler(CallbackQueryHandler(gender_callback, pattern=r"^gender:"))
    app.add_handler(CallbackQueryHandler(priority_callback, pattern=r"^priority:"))
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

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_error_handler(error_handler)

    if app.job_queue:
        app.job_queue.run_repeating(reminder_job, interval=60, first=5)
        app.job_queue.run_repeating(morning_job, interval=60, first=10)
        app.job_queue.run_repeating(channel_scheduler_job, interval=60, first=15)
        app.job_queue.run_repeating(auto_channel_job, interval=60, first=20)

    logger.info("Goal bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
