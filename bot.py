import logging
import os
import re
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DB_PATH = os.environ.get("DB_PATH", "goals.db")
TZ = ZoneInfo("Asia/Tehran")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

ADD_NAME, ADD_CATEGORY, ADD_TIME = range(3)

# Common daily goals. English names are used when the user selects English.
GOALS_FA = {
    "سلامتی": ["نوشیدن ۸ لیوان آب", "۳۰ دقیقه پیاده‌روی", "خوردن میوه", "خوردن سبزیجات", "خواب ۷ تا ۸ ساعت"],
    "ورزش": ["۳۰ دقیقه ورزش", "۵۰۰۰ قدم پیاده‌روی", "۱۰۰۰۰ قدم پیاده‌روی", "تمرین شکم", "حرکات کششی"],
    "مطالعه": ["۳۰ دقیقه مطالعه", "۲۰ دقیقه مطالعه", "یادگیری ۱۰ لغت جدید", "مطالعه کتاب", "مرور مطالب"],
    "کار و شغل": ["برنامه‌ریزی روز", "انجام مهم‌ترین کار روز", "۳۰ دقیقه کار بدون حواس‌پرتی", "بررسی کارهای امروز"],
    "تغذیه": ["صبحانه سالم", "ناهار سالم", "شام سبک", "نخوردن نوشابه", "کاهش مصرف شیرینی"],
    "مالی": ["ثبت هزینه‌های امروز", "بررسی حساب بانکی", "پس‌انداز روزانه", "حذف یک هزینه غیرضروری"],
    "خانه": ["مرتب کردن اتاق", "مرتب کردن میز", "مرتب کردن تخت", "نظافت خانه", "مرتب کردن لباس‌ها"],
    "تمرکز": ["۱۰ دقیقه تمرکز", "۱۰ دقیقه مدیتیشن", "۳۰ دقیقه بدون موبایل", "۳۰ دقیقه بدون شبکه اجتماعی"],
    "خودرو": ["بررسی روغن موتور", "بررسی باد لاستیک", "بررسی آب رادیاتور", "تمیز کردن خودرو"],
    "شخصی": ["مسواک زدن", "رسیدگی به ظاهر", "انجام یک کار عقب‌افتاده", "یادگیری یک چیز جدید"],
}

GOALS_EN = {
    "Health": ["Drink 8 glasses of water", "30 minute walk", "Eat fruit", "Eat vegetables", "Sleep 7 to 8 hours"],
    "Fitness": ["30 minute workout", "5000 steps", "10000 steps", "Abs workout", "Stretching"],
    "Study": ["Study for 30 minutes", "Study for 20 minutes", "Learn 10 new words", "Read a book", "Review lessons"],
    "Work": ["Plan your day", "Do the most important task", "30 minutes of focused work", "Review today's tasks"],
    "Nutrition": ["Healthy breakfast", "Healthy lunch", "Light dinner", "No soft drinks", "Reduce sweets"],
    "Finance": ["Record today's expenses", "Check your bank account", "Save money", "Remove one unnecessary expense"],
    "Home": ["Clean your room", "Organize your desk", "Make your bed", "Clean the house", "Organize your clothes"],
    "Focus": ["10 minutes of focus", "10 minutes of meditation", "30 minutes without your phone", "30 minutes without social media"],
    "Car": ["Check engine oil", "Check tire pressure", "Check coolant", "Clean the car"],
    "Personal": ["Brush your teeth", "Personal care", "Finish one delayed task", "Learn something new"],
}

T = {
    "fa": {
        "welcome": "🎯 خوش آمدی! زبان ربات را انتخاب کن:",
        "language_saved": "✅ زبان روی فارسی تنظیم شد.",
        "menu": [["🎯 اهداف امروز", "➕ هدف جدید"], ["🏆 اهداف آماده", "✏️ ویرایش اهداف"], ["📅 جدول هفتگی", "📊 آمار من"], ["⚙️ تنظیمات"]],
        "today": "🎯 اهداف امروز",
        "no_goals": "🎯 هنوز هدفی ثبت نکردی.",
        "new_goal": "🎯 یک دسته را انتخاب کن:",
        "choose_goal": "🎯 یکی از نمونه‌های این دسته را انتخاب کن:",
        "goal_added": "✅ هدف ثبت شد.",
        "time": "⏰ زمان یادآوری را وارد کن.\n\nنمونه: 18:00، ۱۸:۰۰، 1800، ۱۸۰۰، 8:30، ۸۳۰\n\nاگر یادآوری نمی‌خواهی بنویس: بدون یادآوری",
        "bad_time": "❌ ساعت اشتباه است. نمونه: 18:00 یا ۱۸۰۰",
        "morning": "☀️ صبح بخیر!\n\nآماده‌ای روزت را شروع کنی؟\nهدف‌هایت را کامل کن و روز خوبی داشته باشی. 💪",
        "done": "✅ انجام شد.",
        "missed": "❌ انجام نشد.",
        "settings": "⚙️ تنظیمات",
        "language": "🌐 زبان",
        "edit": "✏️ هدف را انتخاب کن:",
        "deleted": "🗑 هدف حذف شد.",
        "name": "✏️ نام جدید هدف را بفرست:",
        "changed": "✅ هدف تغییر کرد.",
        "reminder": "⏰ یادآوری هدف\n\n🎯 {name}\n\nانجامش دادی؟",
    },
    "en": {
        "welcome": "🎯 Welcome! Select your language:",
        "language_saved": "✅ Language set to English.",
        "menu": [["🎯 Today's Goals", "➕ New Goal"], ["🏆 Ready Goals", "✏️ Edit Goals"], ["📅 Weekly Table", "📊 My Stats"], ["⚙️ Settings"]],
        "today": "🎯 Today's Goals",
        "no_goals": "🎯 You have no goals yet.",
        "new_goal": "🎯 Select a category:",
        "choose_goal": "🎯 Select a goal from this category:",
        "goal_added": "✅ Goal added.",
        "time": "⏰ Enter reminder time.\n\nExamples: 18:00, 1800, 8:30, 830\n\nType: no reminder",
        "bad_time": "❌ Invalid time. Example: 18:00 or 1800",
        "morning": "☀️ Good morning!\n\nAre you ready to start your day?\nComplete your goals and have a good day. 💪",
        "done": "✅ Done.",
        "missed": "❌ Not done.",
        "settings": "⚙️ Settings",
        "language": "🌐 Language",
        "edit": "✏️ Select a goal to edit:",
        "deleted": "🗑 Goal deleted.",
        "name": "✏️ Send the new goal name:",
        "changed": "✅ Goal updated.",
        "reminder": "⏰ Goal reminder\n\n🎯 {name}\n\nDid you complete it?",
    }
}

def db():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = db()
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        language TEXT NOT NULL DEFAULT 'fa',
        created_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS goals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        reminder_time TEXT,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS goal_days(
        goal_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        goal_date TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        completed_at TEXT,
        PRIMARY KEY(goal_id, goal_date)
    )""")
    c.commit()
    c.close()

def lang(uid):
    c = db()
    r = c.execute("SELECT language FROM users WHERE user_id=?", (uid,)).fetchone()
    c.close()
    return r["language"] if r else "fa"

def set_lang(uid, value):
    c = db()
    now = datetime.now(TZ).isoformat()
    c.execute("""INSERT INTO users(user_id,language,created_at) VALUES(?,?,?)
                 ON CONFLICT(user_id) DO UPDATE SET language=excluded.language""",
              (uid, value, now))
    c.commit()
    c.close()

def keyboard(uid):
    return ReplyKeyboardMarkup(T[lang(uid)]["menu"], resize_keyboard=True)

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

def add_goal(uid, name, category, reminder):
    c = db()
    c.execute("INSERT INTO goals(user_id,name,category,reminder_time,created_at) VALUES(?,?,?,?,?)",
              (uid, name, category, reminder, datetime.now(TZ).isoformat()))
    c.commit()
    c.close()

def get_goals(uid):
    c = db()
    rows = c.execute("SELECT * FROM goals WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall()
    c.close()
    return rows

def get_goal(uid, gid):
    c = db()
    r = c.execute("SELECT * FROM goals WHERE user_id=? AND id=?", (uid, gid)).fetchone()
    c.close()
    return r

def set_status(uid, gid, status):
    d = datetime.now(TZ).date().isoformat()
    done = datetime.now(TZ).isoformat() if status == "done" else None
    c = db()
    c.execute("""INSERT INTO goal_days(goal_id,user_id,goal_date,status,completed_at)
                 VALUES(?,?,?,?,?)
                 ON CONFLICT(goal_id,goal_date) DO UPDATE SET
                 status=excluded.status, completed_at=excluded.completed_at""",
              (gid, uid, d, status, done))
    c.commit()
    c.close()

def status(uid, gid):
    d = datetime.now(TZ).date().isoformat()
    c = db()
    r = c.execute("SELECT status FROM goal_days WHERE user_id=? AND goal_id=? AND goal_date=?",
                  (uid, gid, d)).fetchone()
    c.close()
    return r["status"] if r else "pending"

def start_categories(uid, prefix="cat"):
    data = GOALS_EN if lang(uid) == "en" else GOALS_FA
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(k, callback_data=f"{prefix}:{i}")]
        for i, k in enumerate(data.keys())
    ])

def data_category(uid, index):
    data = GOALS_EN if lang(uid) == "en" else GOALS_FA
    return list(data.keys())[index]

def data_goals(uid, category):
    data = GOALS_EN if lang(uid) == "en" else GOALS_FA
    return data[category]

async def start(update, context):
    uid = update.effective_user.id
    if get_existing_language(uid):
        await update.message.reply_text(T[lang(uid)]["welcome"].replace("Select your language:", "Your menu is ready.") if lang(uid) == "en" else "🎯 خوش آمدی!", reply_markup=keyboard(uid))
        return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🇮🇷 فارسی", callback_data="language:fa"),
        InlineKeyboardButton("🇬🇧 English", callback_data="language:en")
    ]])
    await update.message.reply_text("🎯 خوش آمدی! زبان ربات را انتخاب کن:\n\n🎯 Welcome! Select your language:", reply_markup=kb)

def get_existing_language(uid):
    c = db()
    r = c.execute("SELECT language FROM users WHERE user_id=?", (uid,)).fetchone()
    c.close()
    return r is not None

async def language_callback(update, context):
    q = update.callback_query
    await q.answer()
    value = q.data.split(":")[1]
    set_lang(q.from_user.id, value)
    await q.message.reply_text(T[value]["language_saved"], reply_markup=keyboard(q.from_user.id))

async def settings(update, context):
    uid = update.effective_user.id
    await update.message.reply_text(
        T[lang(uid)]["settings"],
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🇮🇷 فارسی", callback_data="language:fa"),
            InlineKeyboardButton("🇬🇧 English", callback_data="language:en")
        ]])
    )

async def new_goal(update, context):
    uid = update.effective_user.id
    await update.message.reply_text(T[lang(uid)]["new_goal"], reply_markup=start_categories(uid, "newcat"))

async def new_category(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    category = data_category(uid, int(q.data.split(":")[1]))
    context.user_data["category"] = category
    goals = data_goals(uid, category)
    buttons = [[InlineKeyboardButton(x, callback_data=f"newgoal:{i}")] for i, x in enumerate(goals)]
    buttons.append([InlineKeyboardButton("⬅️ Back" if lang(uid) == "en" else "⬅️ برگشت", callback_data="newback")])
    await q.message.reply_text(T[lang(uid)]["choose_goal"], reply_markup=InlineKeyboardMarkup(buttons))

async def new_goal_pick(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    category = context.user_data["category"]
    name = data_goals(uid, category)[int(q.data.split(":")[1])]
    context.user_data["name"] = name
    await q.message.reply_text(T[lang(uid)]["time"])

async def save_new_goal(update, context):
    uid = update.effective_user.id
    value = update.message.text.strip()
    no_reminder = value.lower() in ("بدون یادآوری", "no reminder", "none", "off")
    reminder = None if no_reminder else parse_time(value)
    if not no_reminder and reminder is None:
        await update.message.reply_text(T[lang(uid)]["bad_time"])
        return
    add_goal(uid, context.user_data["name"], context.user_data["category"], reminder)
    context.user_data.clear()
    await update.message.reply_text(T[lang(uid)]["goal_added"], reply_markup=keyboard(uid))

async def ready_menu(update, context):
    uid = update.effective_user.id
    await update.message.reply_text(T[lang(uid)]["new_goal"], reply_markup=start_categories(uid, "newcat"))

async def today(update, context):
    uid = update.effective_user.id
    goals = get_goals(uid)
    if not goals:
        await update.message.reply_text(T[lang(uid)]["no_goals"], reply_markup=keyboard(uid))
        return
    buttons = []
    for g in goals:
        s = status(uid, g["id"])
        icon = "✅" if s == "done" else "❌" if s == "missed" else "⬜"
        buttons.append([InlineKeyboardButton(f"{icon} {g['name']}", callback_data=f"detail:{g['id']}")])
    await update.message.reply_text(T[lang(uid)]["today"], reply_markup=InlineKeyboardMarkup(buttons))

async def detail(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    gid = int(q.data.split(":")[1])
    g = get_goal(uid, gid)
    if not g:
        return
    await q.message.reply_text(
        f"🎯 {g['name']}\n📁 {g['category']}\n⏰ {g['reminder_time'] or 'Off'}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Done" if lang(uid) == "en" else "✅ انجام دادم", callback_data=f"done:{gid}"),
            InlineKeyboardButton("❌ Not done" if lang(uid) == "en" else "❌ انجام ندادم", callback_data=f"miss:{gid}")
        ]])
    )

async def mark(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    gid = int(q.data.split(":")[1])
    is_done = q.data.startswith("done:")
    set_status(uid, gid, "done" if is_done else "missed")
    await q.message.reply_text(T[lang(uid)]["done"] if is_done else T[lang(uid)]["missed"], reply_markup=keyboard(uid))

async def edit_menu(update, context):
    uid = update.effective_user.id
    goals = get_goals(uid)
    if not goals:
        await update.message.reply_text(T[lang(uid)]["no_goals"], reply_markup=keyboard(uid))
        return
    await update.message.reply_text(T[lang(uid)]["edit"], reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton(g["name"], callback_data=f"edit:{g['id']}")] for g in goals
    ]))

async def edit_goal(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    gid = int(q.data.split(":")[1])
    g = get_goal(uid, gid)
    if not g:
        return
    buttons = [
        [InlineKeyboardButton("✏️ Change name" if lang(uid) == "en" else "✏️ تغییر نام", callback_data=f"rename:{gid}")],
        [InlineKeyboardButton("🗑 Delete" if lang(uid) == "en" else "🗑 حذف", callback_data=f"delete:{gid}")]
    ]
    await q.message.reply_text(f"🎯 {g['name']}\n⏰ {g['reminder_time'] or 'Off'}", reply_markup=InlineKeyboardMarkup(buttons))

async def rename_start(update, context):
    q = update.callback_query
    await q.answer()
    context.user_data["edit_id"] = int(q.data.split(":")[1])
    await q.message.reply_text(T[lang(q.from_user.id)]["name"])

async def rename_save(update, context):
    uid = update.effective_user.id
    gid = context.user_data.get("edit_id")
    name = update.message.text.strip()
    c = db()
    c.execute("UPDATE goals SET name=? WHERE user_id=? AND id=?", (name, uid, gid))
    c.commit()
    c.close()
    context.user_data.clear()
    await update.message.reply_text(T[lang(uid)]["changed"], reply_markup=keyboard(uid))

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
    await q.message.reply_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton(yes, callback_data=f"delete_yes:{gid}")],
        [InlineKeyboardButton(no, callback_data="delete_no")]
    ]))

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
    await q.message.reply_text(T[lang(uid)]["deleted"], reply_markup=keyboard(uid))

async def delete_no(update, context):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("❌ Cancelled" if lang(q.from_user.id) == "en" else "❌ لغو شد.", reply_markup=keyboard(q.from_user.id))

async def morning_job(context):
    now = datetime.now(TZ)
    if now.hour != 7 or now.minute != 0:
        return
    c = db()
    users = c.execute("SELECT user_id FROM users").fetchall()
    c.close()
    for r in users:
        uid = r["user_id"]
        try:
            await context.bot.send_message(uid, T[lang(uid)]["morning"], reply_markup=keyboard(uid))
        except Exception as e:
            logger.error("Morning message error: %s", e)

async def reminder_job(context):
    now = datetime.now(TZ)
    hhmm = now.strftime("%H:%M")
    c = db()
    goals = c.execute("SELECT * FROM goals WHERE enabled=1 AND reminder_time=?", (hhmm,)).fetchall()
    c.close()
    for g in goals:
        if status(g["user_id"], g["id"]) == "done":
            continue
        uid = g["user_id"]
        try:
            await context.bot.send_message(
                uid,
                T[lang(uid)]["reminder"].format(name=g["name"]),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Done" if lang(uid) == "en" else "✅ انجام دادم", callback_data=f"done:{g['id']}"),
                    InlineKeyboardButton("❌ Not done" if lang(uid) == "en" else "❌ انجام ندادم", callback_data=f"miss:{g['id']}")
                ]])
            )
        except Exception as e:
            logger.error("Reminder error: %s", e)

async def text_router(update, context):
    uid = update.effective_user.id
    text = update.message.text
    menu = T[lang(uid)]["menu"]
    if text in (menu[0][0], "🎯 اهداف امروز", "🎯 Today's Goals"):
        await today(update, context)
    elif text in (menu[0][1], "➕ هدف جدید", "➕ New Goal"):
        await new_goal(update, context)
    elif text in (menu[1][0], "🏆 اهداف آماده", "🏆 Ready Goals"):
        await ready_menu(update, context)
    elif text in (menu[1][1], "✏️ ویرایش اهداف", "✏️ Edit Goals"):
        await edit_menu(update, context)
    elif text in (menu[3][0], "⚙️ تنظیمات", "⚙️ Settings"):
        await settings(update, context)
    else:
        await save_new_goal(update, context)

async def error_handler(update, context):
    logger.error("Bot error", exc_info=context.error)

def main():
    if not BOT_TOKEN:
        raise RuntimeError("Set BOT_TOKEN in your environment variables.")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(language_callback, pattern=r"^language:"))
    app.add_handler(CallbackQueryHandler(new_category, pattern=r"^newcat:"))
    app.add_handler(CallbackQueryHandler(new_goal_pick, pattern=r"^newgoal:"))
    app.add_handler(CallbackQueryHandler(detail, pattern=r"^detail:"))
    app.add_handler(CallbackQueryHandler(mark, pattern=r"^(done|miss):"))
    app.add_handler(CallbackQueryHandler(edit_goal, pattern=r"^edit:"))
    app.add_handler(CallbackQueryHandler(rename_start, pattern=r"^rename:"))
    app.add_handler(CallbackQueryHandler(delete_start, pattern=r"^delete:"))
    app.add_handler(CallbackQueryHandler(delete_confirm, pattern=r"^delete_yes:"))
    app.add_handler(CallbackQueryHandler(delete_no, pattern=r"^delete_no$"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_error_handler(error_handler)

    if app.job_queue:
        app.job_queue.run_repeating(reminder_job, interval=60, first=5)
        # Morning message at 07:00 Tehran time.
        app.job_queue.run_repeating(morning_job, interval=60, first=10)

    logger.info("Goal bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

