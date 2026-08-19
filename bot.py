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

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DB_PATH = os.environ.get("DB_PATH", "goals.db")

TEHRAN = ZoneInfo("Asia/Tehran")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# مراحل
# ============================================================

ADD_NAME = 1
ADD_CATEGORY = 2
ADD_TIME = 3
ADD_DAYS = 4

EDIT_NAME = 10
EDIT_TIME = 11
EDIT_DAYS = 12


# ============================================================
# اهداف آماده
# ============================================================

READY_GOALS = {
    "سلامتی": [
        "نوشیدن ۸ لیوان آب",
        "نوشیدن آب بعد از بیدار شدن",
        "۳۰ دقیقه پیاده‌روی",
        "خوردن میوه",
        "خوردن سبزیجات",
        "کم کردن مصرف شیرینی",
        "نخوردن نوشابه",
        "رسیدگی به بهداشت شخصی",
    ],

    "ورزش و تناسب اندام": [
        "۳۰ دقیقه ورزش",
        "۵۰۰۰ قدم پیاده‌روی",
        "۱۰۰۰۰ قدم پیاده‌روی",
        "۳۰ حرکت شنا",
        "تمرین شکم",
        "تمرین پا",
        "تمرین بالاتنه",
        "۱۵ دقیقه حرکات کششی",
        "۳۰ دقیقه دوچرخه‌سواری",
    ],

    "تغذیه": [
        "خوردن صبحانه",
        "خوردن ناهار سالم",
        "خوردن شام سبک",
        "نخوردن فست‌فود",
        "نخوردن نوشابه",
        "کاهش مصرف شیرینی",
        "نوشیدن آب کافی",
        "خوردن یک وعده میوه",
        "خوردن سبزیجات",
    ],

    "مطالعه و آموزش": [
        "۳۰ دقیقه مطالعه",
        "۲۰ دقیقه مطالعه",
        "یادگیری زبان",
        "یادگیری ۱۰ لغت جدید",
        "دیدن یک درس آموزشی",
        "یادگیری یک مهارت جدید",
        "مطالعه کتاب",
        "مرور مطالب",
    ],

    "کار و شغل": [
        "برنامه‌ریزی کارهای امروز",
        "انجام مهم‌ترین کار روز",
        "بررسی کارهای امروز",
        "مرتب کردن فایل‌ها",
        "یادداشت کارهای فردا",
        "۳۰ دقیقه کار بدون حواس‌پرتی",
    ],

    "مالی": [
        "ثبت هزینه‌های امروز",
        "بررسی حساب بانکی",
        "پس‌انداز روزانه",
        "بررسی خریدهای امروز",
        "حذف یک هزینه غیرضروری",
        "بررسی بودجه ماهانه",
    ],

    "خانه و زندگی": [
        "مرتب کردن اتاق",
        "مرتب کردن میز",
        "شستن ظرف‌ها",
        "مرتب کردن لباس‌ها",
        "نظافت خانه",
        "مرتب کردن کمد",
        "خارج کردن زباله",
    ],

    "ذهن و تمرکز": [
        "۱۰ دقیقه تمرکز",
        "۱۰ دقیقه مدیتیشن",
        "۱۰ دقیقه تنفس آرام",
        "نوشتن برنامه امروز",
        "نوشتن سه هدف امروز",
        "۳۰ دقیقه بدون موبایل",
        "۳۰ دقیقه بدون شبکه اجتماعی",
    ],

    "خواب و استراحت": [
        "خواب قبل از ساعت ۱۲",
        "بیدار شدن در ساعت مشخص",
        "۷ تا ۸ ساعت خواب",
        "خاموش کردن موبایل قبل از خواب",
        "آماده شدن برای خواب",
        "استراحت کوتاه در طول روز",
    ],

    "روابط اجتماعی": [
        "تماس با خانواده",
        "پیام دادن به یک دوست",
        "وقت گذاشتن برای خانواده",
        "احوالپرسی از یک نفر",
        "دیدار با دوستان",
    ],

    "سرگرمی": [
        "۳۰ دقیقه بازی",
        "دیدن فیلم",
        "گوش دادن به موسیقی",
        "انجام سرگرمی مورد علاقه",
    ],

    "کنترل موبایل": [
        "کم کردن زمان موبایل",
        "۳۰ دقیقه بدون موبایل",
        "۱ ساعت بدون شبکه اجتماعی",
        "خاموش کردن اعلان‌های غیرضروری",
        "ندیدن موبایل هنگام غذا",
        "ندیدن موبایل قبل از خواب",
    ],

    "عادت‌های شخصی": [
        "مرتب کردن تخت",
        "مسواک زدن",
        "نوشیدن آب صبح",
        "برنامه‌ریزی روز",
        "ثبت اهداف روزانه",
        "رسیدگی به ظاهر",
        "مرتب کردن وسایل شخصی",
    ],

    "معنوی": [
        "زمان آرامش و تفکر",
        "شکرگزاری",
        "انجام کار خیر",
        "کمک به یک نفر",
    ],

    "خودرو": [
        "بررسی بنزین خودرو",
        "بررسی باد لاستیک",
        "بررسی روغن موتور",
        "تمیز کردن خودرو",
        "بررسی چراغ‌های خودرو",
        "بررسی آب رادیاتور",
    ],

    "نظم و نظافت": [
        "مرتب کردن اتاق",
        "مرتب کردن میز",
        "مرتب کردن کمد",
        "نظافت خانه",
        "تمیز کردن موبایل",
        "مرتب کردن فایل‌های گوشی",
        "مرتب کردن فایل‌های کامپیوتر",
    ],

    "اهداف شخصی": [
        "انجام یک کار عقب‌افتاده",
        "یادگیری یک چیز جدید",
        "انجام یک کار سخت",
        "رسیدن به هدف امروز",
        "برنامه‌ریزی فردا",
        "نوشتن اهداف هفته",
    ],
}


# ============================================================
# دیتابیس
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            language TEXT NOT NULL DEFAULT 'fa',
            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'عمومی',
            reminder_time TEXT,
            repeat_days TEXT NOT NULL DEFAULT '0,1,2,3,4,5,6',
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


# ============================================================
# زبان
# ============================================================

def get_language(user_id):
    conn = get_db()

    row = conn.execute(
        "SELECT language FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    conn.close()

    if row:
        return row["language"]

    return None


def save_language(user_id, language):
    conn = get_db()

    now = datetime.now(TEHRAN).isoformat()

    conn.execute("""
        INSERT INTO users (
            user_id,
            language,
            created_at
        )
        VALUES (?, ?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET language = excluded.language
    """, (
        user_id,
        language,
        now,
    ))

    conn.commit()
    conn.close()


# ============================================================
# ابزارها
# ============================================================

def today():
    return datetime.now(TEHRAN).date()


def date_string(value):
    return value.strftime("%Y-%m-%d")


def normalize_digits(text):
    table = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789",
    )
    return text.translate(table)


def parse_time(text):
    text = normalize_digits(text.strip())

    text = text.replace(".", ":")
    text = text.replace("٫", ":")
    text = text.replace("：", ":")

    text = re.sub(r"\s+", "", text)

    if text.isdigit():
        if len(text) == 1:
            hour = int(text)
            minute = 0
        elif len(text) == 2:
            hour = int(text)
            minute = 0
        elif len(text) == 3:
            hour = int(text[0])
            minute = int(text[1:])
        elif len(text) == 4:
            hour = int(text[:2])
            minute = int(text[2:])
        else:
            return None
    else:
        match = re.fullmatch(
            r"(\d{1,2}):(\d{1,2})",
            text,
        )

        if not match:
            return None

        hour = int(match.group(1))
        minute = int(match.group(2))

    if hour < 0 or hour > 23:
        return None

    if minute < 0 or minute > 59:
        return None

    return f"{hour:02d}:{minute:02d}"


def day_name(index):
    names = [
        "دوشنبه",
        "سه‌شنبه",
        "چهارشنبه",
        "پنجشنبه",
        "جمعه",
        "شنبه",
        "یکشنبه",
    ]

    return names[index]


def days_text(days):
    if days == "0,1,2,3,4,5,6":
        return "هر روز"

    result = []

    for value in days.split(","):
        if value.isdigit():
            number = int(value)

            if 0 <= number <= 6:
                result.append(day_name(number))

    return "، ".join(result)


def main_keyboard():
    keyboard = [
        ["🎯 اهداف امروز", "➕ هدف جدید"],
        ["🏆 اهداف آماده", "✏️ ویرایش اهداف"],
        ["📅 جدول هفتگی", "📊 آمار من"],
        ["⚙️ تنظیمات"],
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
    )


# ============================================================
# اهداف
# ============================================================

def add_goal(
    user_id,
    name,
    category,
    reminder_time,
    repeat_days,
):
    conn = get_db()

    cur = conn.execute("""
        INSERT INTO goals (
            user_id,
            name,
            category,
            reminder_time,
            repeat_days,
            enabled,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, 1, ?)
    """, (
        user_id,
        name,
        category,
        reminder_time,
        repeat_days,
        datetime.now(TEHRAN).isoformat(),
    ))

    goal_id = cur.lastrowid

    conn.commit()
    conn.close()

    return goal_id


def get_goal(user_id, goal_id):
    conn = get_db()

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


def get_goals(user_id):
    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM goals
        WHERE user_id = ?
        ORDER BY id DESC
    """, (user_id,)).fetchall()

    conn.close()

    return rows


def update_goal_name(user_id, goal_id, name):
    conn = get_db()

    conn.execute("""
        UPDATE goals
        SET name = ?
        WHERE user_id = ?
        AND id = ?
    """, (
        name,
        user_id,
        goal_id,
    ))

    conn.commit()
    conn.close()


def update_goal_time(user_id, goal_id, reminder_time):
    conn = get_db()

    conn.execute("""
        UPDATE goals
        SET reminder_time = ?
        WHERE user_id = ?
        AND id = ?
    """, (
        reminder_time,
        user_id,
        goal_id,
    ))

    conn.commit()
    conn.close()


def update_goal_days(user_id, goal_id, repeat_days):
    conn = get_db()

    conn.execute("""
        UPDATE goals
        SET repeat_days = ?
        WHERE user_id = ?
        AND id = ?
    """, (
        repeat_days,
        user_id,
        goal_id,
    ))

    conn.commit()
    conn.close()


def toggle_goal(user_id, goal_id):
    conn = get_db()

    conn.execute("""
        UPDATE goals
        SET enabled =
            CASE
                WHEN enabled = 1 THEN 0
                ELSE 1
            END
        WHERE user_id = ?
        AND id = ?
    """, (
        user_id,
        goal_id,
    ))

    conn.commit()
    conn.close()


def delete_goal(user_id, goal_id):
    conn = get_db()

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


# ============================================================
# وضعیت روزانه
# ============================================================

def get_status(user_id, goal_id, goal_date):
    conn = get_db()

    row = conn.execute("""
        SELECT status
        FROM goal_days
        WHERE user_id = ?
        AND goal_id = ?
        AND goal_date = ?
    """, (
        user_id,
        goal_id,
        goal_date,
    )).fetchone()

    conn.close()

    if row:
        return row["status"]

    return "pending"


def set_status(
    user_id,
    goal_id,
    goal_date,
    status,
):
    completed_at = None

    if status == "done":
        completed_at = datetime.now(
            TEHRAN
        ).isoformat()

    conn = get_db()

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
        goal_date,
        status,
        completed_at,
    ))

    conn.commit()
    conn.close()


# ============================================================
# شروع
# ============================================================

async def start(update, context):
    user_id = update.effective_user.id

    language = get_language(user_id)

    if language:
        await update.message.reply_text(
            "🎯 به ربات اهداف روزانه خوش آمدی.",
            reply_markup=main_keyboard(),
        )
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "🇮🇷 فارسی",
                callback_data="lang:fa",
            ),
            InlineKeyboardButton(
                "🇬🇧 English",
                callback_data="lang:en",
            ),
        ]
    ]

    await update.message.reply_text(
        "🤖 خوش آمدی!\n\n"
        "🌐 زبان ربات را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )


async def language_callback(update, context):
    query = update.callback_query
    await query.answer()

    language = query.data.split(":")[1]

    save_language(
        query.from_user.id,
        language,
    )

    if language == "en":
        text = (
            "🎯 Welcome!\n\n"
            "Your language is set to English."
        )
    else:
        text = (
            "🎯 خوش آمدی!\n\n"
            "زبان ربات روی فارسی تنظیم شد."
        )

    await query.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


# ============================================================
# اهداف آماده
# ============================================================

async def ready_menu(update, context):
    buttons = []

    for category in READY_GOALS:
        buttons.append([
            InlineKeyboardButton(
                f"📁 {category}",
                callback_data=f"rcat:{category}",
            )
        ])

    await update.message.reply_text(
        "🏆 اهداف آماده\n\n"
        "دسته مورد نظر را انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def ready_category(update, context):
    query = update.callback_query
    await query.answer()

    category = query.data.split(":", 1)[1]

    context.user_data["ready_category"] = category
    context.user_data["ready_selected"] = []

    goals = READY_GOALS[category]

    buttons = []

    for index, name in enumerate(goals):
        buttons.append([
            InlineKeyboardButton(
                f"⬜ {name}",
                callback_data=f"rsel:{index}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "➕ افزودن اهداف انتخاب‌شده",
            callback_data="radd",
        )
    ])

    await query.message.reply_text(
        f"📁 {category}\n\n"
        "چند هدف را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def ready_select(update, context):
    query = update.callback_query
    await query.answer()

    category = context.user_data.get(
        "ready_category"
    )

    if not category:
        return

    index = int(
        query.data.split(":")[1]
    )

    selected = context.user_data.get(
        "ready_selected",
        [],
    )

    if index in selected:
        selected.remove(index)
    else:
        selected.append(index)

    context.user_data["ready_selected"] = selected

    goals = READY_GOALS[category]

    buttons = []

    for i, name in enumerate(goals):
        icon = "✅" if i in selected else "⬜"

        buttons.append([
            InlineKeyboardButton(
                f"{icon} {name}",
                callback_data=f"rsel:{i}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            f"➕ افزودن {len(selected)} هدف",
            callback_data="radd",
        )
    ])

    await query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


async def ready_add(update, context):
    query = update.callback_query
    await query.answer()

    category = context.user_data.get(
        "ready_category"
    )

    selected = context.user_data.get(
        "ready_selected",
        [],
    )

    if not selected:
        await query.message.reply_text(
            "❌ حداقل یک هدف را انتخاب کن."
        )
        return

    goals = READY_GOALS[category]

    selected_names = [
        goals[index]
        for index in selected
    ]

    context.user_data["pending_names"] = selected_names
    context.user_data["pending_category"] = category

    text = "🎯 اهداف انتخاب‌شده\n\n"

    for number, name in enumerate(
        selected_names,
        1,
    ):
        text += f"{number}. {name}\n"

    text += (
        "\nآیا این اهداف را اضافه کنم؟"
    )

    buttons = [
        [
            InlineKeyboardButton(
                "✅ تأیید و ادامه",
                callback_data="rconfirm",
            )
        ],
        [
            InlineKeyboardButton(
                "✏️ تغییر انتخاب",
                callback_data="rback",
            ),
            InlineKeyboardButton(
                "❌ لغو",
                callback_data="rcancel",
            ),
        ],
    ]

    await query.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def ready_confirm(update, context):
    query = update.callback_query
    await query.answer()

    names = context.user_data.get(
        "pending_names",
        [],
    )

    category = context.user_data.get(
        "pending_category",
        "عمومی",
    )

    if not names:
        return

    context.user_data["adding_ready"] = True
    context.user_data["pending_names"] = names
    context.user_data["pending_category"] = category

    await query.message.reply_text(
        "⏰ زمان یادآوری را وارد کن.\n\n"
        "نمونه‌های قابل قبول:\n"
        "18:00\n"
        "۱۸:۰۰\n"
        "1800\n"
        "۱۸۰۰\n"
        "8:30\n"
        "۸۳۰\n\n"
        "اگر یادآوری نمی‌خواهی بنویس:\n"
        "بدون یادآوری"
    )


async def ready_back(update, context):
    query = update.callback_query
    await query.answer()

    category = context.user_data.get(
        "ready_category"
    )

    if not category:
        return

    selected = context.user_data.get(
        "ready_selected",
        [],
    )

    goals = READY_GOALS[category]

    buttons = []

    for i, name in enumerate(goals):
        icon = "✅" if i in selected else "⬜"

        buttons.append([
            InlineKeyboardButton(
                f"{icon} {name}",
                callback_data=f"rsel:{i}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            f"➕ افزودن {len(selected)} هدف",
            callback_data="radd",
        )
    ])

    await query.message.reply_text(
        "✏️ انتخاب اهداف را تغییر بده.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def ready_cancel(update, context):
    query = update.callback_query
    await query.answer()

    context.user_data.clear()

    await query.message.reply_text(
        "❌ انتخاب اهداف لغو شد.",
        reply_markup=main_keyboard(),
    )


async def ready_time_handler(update, context):
    if not context.user_data.get(
        "adding_ready"
    ):
        return False

    value = update.message.text.strip()

    if value == "بدون یادآوری":
        reminder = None
    else:
        reminder = parse_time(value)

        if reminder is None:
            await update.message.reply_text(
                "❌ ساعت اشتباه است.\n\n"
                "نمونه:\n"
                "18:00\n"
                "۱۸:۰۰\n"
                "1800\n"
                "۱۸۰۰"
            )
            return True

    context.user_data["pending_time"] = reminder

    buttons = [
        [
            InlineKeyboardButton(
                "📅 هر روز",
                callback_data="rday:all",
            )
        ],
        [
            InlineKeyboardButton(
                "📅 انتخاب روزها",
                callback_data="rday:choose",
            )
        ],
    ]

    await update.message.reply_text(
        "📅 روزهای تکرار هدف‌ها را انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )

    return True


async def ready_days(update, context):
    query = update.callback_query
    await query.answer()

    value = query.data.split(":")[1]

    if value == "all":
        await show_ready_final(
            query,
            context,
            "0,1,2,3,4,5,6",
        )
        return

    context.user_data["ready_days"] = []

    buttons = []

    for index in range(7):
        buttons.append([
            InlineKeyboardButton(
                day_name(index),
                callback_data=f"rchoose:{index}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "✅ ثبت روزها",
            callback_data="rfinish",
        )
    ])

    await query.message.reply_text(
        "📅 روزها را انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def ready_choose_day(update, context):
    query = update.callback_query
    await query.answer()

    value = query.data.split(":")[1]

    selected = context.user_data.get(
        "ready_days",
        [],
    )

    if value not in selected:
        selected.append(value)
    else:
        selected.remove(value)

    context.user_data["ready_days"] = selected

    await query.answer(
        "انتخاب شد."
    )


async def ready_finish_days(update, context):
    query = update.callback_query
    await query.answer()

    selected = context.user_data.get(
        "ready_days",
        [],
    )

    if not selected:
        await query.message.reply_text(
            "❌ حداقل یک روز را انتخاب کن."
        )
        return

    selected.sort(
        key=lambda x: int(x)
    )

    await show_ready_final(
        query,
        context,
        ",".join(selected),
    )


async def show_ready_final(
    query,
    context,
    days,
):
    names = context.user_data.get(
        "pending_names",
        [],
    )

    category = context.user_data.get(
        "pending_category",
        "عمومی",
    )

    reminder = context.user_data.get(
        "pending_time"
    )

    text = "🎯 بررسی نهایی اهداف\n\n"

    for number, name in enumerate(
        names,
        1,
    ):
        text += (
            f"{number}. {name}\n"
            f"   📁 {category}\n"
            f"   ⏰ {reminder or 'بدون یادآوری'}\n"
            f"   📅 {days_text(days)}\n\n"
        )

    text += "این اهداف ثبت شوند؟"

    context.user_data["final_days"] = days

    buttons = [
        [
            InlineKeyboardButton(
                "✅ ثبت اهداف",
                callback_data="rfinal",
            )
        ],
        [
            InlineKeyboardButton(
                "✏️ تغییر",
                callback_data="rchange",
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ لغو",
                callback_data="rcancel",
            )
        ],
    ]

    await query.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def ready_final(update, context):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    names = context.user_data.get(
        "pending_names",
        [],
    )

    category = context.user_data.get(
        "pending_category",
        "عمومی",
    )

    reminder = context.user_data.get(
        "pending_time"
    )

    days = context.user_data.get(
        "final_days",
        "0,1,2,3,4,5,6",
    )

    for name in names:
        add_goal(
            user_id,
            name,
            category,
            reminder,
            days,
        )

    count = len(names)

    context.user_data.clear()

    await query.message.reply_text(
        f"✅ {count} هدف ثبت شد.\n\n"
        "🎯 اهداف به لیست تو اضافه شدند.",
        reply_markup=main_keyboard(),
    )


async def ready_change(update, context):
    query = update.callback_query
    await query.answer()

    context.user_data.pop(
        "pending_time",
        None,
    )
    context.user_data.pop(
        "final_days",
        None,
    )

    await ready_back(
        update,
        context,
    )


# ============================================================
# افزودن هدف دستی
# ============================================================

async def add_start(update, context):
    await update.message.reply_text(
        "🎯 نام هدف را بفرست.\n\n"
        "مثال:\n"
        "۳۰ دقیقه ورزش"
    )

    return ADD_NAME


async def add_name(update, context):
    name = update.message.text.strip()

    if not name:
        await update.message.reply_text(
            "نام هدف خالی است."
        )
        return ADD_NAME

    context.user_data["manual_name"] = name

    buttons = []

    for category in READY_GOALS:
        buttons.append([
            InlineKeyboardButton(
                category,
                callback_data=f"mcat:{category}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "عمومی",
            callback_data="mcat:عمومی",
        )
    ])

    await update.message.reply_text(
        "📁 دسته هدف را انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )

    return ADD_CATEGORY


async def manual_category(update, context):
    query = update.callback_query
    await query.answer()

    category = query.data.split(":", 1)[1]

    context.user_data["manual_category"] = category

    await query.message.reply_text(
        "⏰ ساعت یادآوری را وارد کن.\n\n"
        "18:00\n"
        "۱۸:۰۰\n"
        "1800\n"
        "۱۸۰۰\n\n"
        "یا:\n"
        "بدون یادآوری"
    )

    return ADD_TIME


async def manual_time(update, context):
    value = update.message.text.strip()

    if value == "بدون یادآوری":
        reminder = None
    else:
        reminder = parse_time(value)

        if reminder is None:
            await update.message.reply_text(
                "❌ ساعت اشتباه است."
            )
            return ADD_TIME

    context.user_data["manual_time"] = reminder

    buttons = [
        [
            InlineKeyboardButton(
                "هر روز",
                callback_data="mday:all",
            )
        ],
        [
            InlineKeyboardButton(
                "انتخاب روزها",
                callback_data="mday:choose",
            )
        ],
    ]

    await update.message.reply_text(
        "📅 روزهای تکرار را انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )

    return ADD_DAYS


async def manual_days(update, context):
    query = update.callback_query
    await query.answer()

    value = query.data.split(":")[1]

    if value == "all":
        days = "0,1,2,3,4,5,6"

        await save_manual_goal(
            query,
            context,
            days,
        )

        return ConversationHandler.END

    context.user_data["manual_days"] = []

    buttons = []

    for index in range(7):
        buttons.append([
            InlineKeyboardButton(
                day_name(index),
                callback_data=f"mchoose:{index}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "✅ ثبت",
            callback_data="mfinish",
        )
    ])

    await query.message.reply_text(
        "روزها را انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )

    return ADD_DAYS


async def manual_choose(update, context):
    query = update.callback_query
    await query.answer()

    value = query.data.split(":")[1]

    selected = context.user_data.get(
        "manual_days",
        [],
    )

    if value not in selected:
        selected.append(value)
    else:
        selected.remove(value)

    context.user_data["manual_days"] = selected


async def manual_finish(update, context):
    query = update.callback_query
    await query.answer()

    selected = context.user_data.get(
        "manual_days",
        [],
    )

    if not selected:
        await query.message.reply_text(
            "حداقل یک روز را انتخاب کن."
        )
        return ADD_DAYS

    selected.sort(
        key=lambda x: int(x)
    )

    await save_manual_goal(
        query,
        context,
        ",".join(selected),
    )

    return ConversationHandler.END


async def save_manual_goal(
    query,
    context,
    days,
):
    user_id = query.from_user.id

    name = context.user_data["manual_name"]
    category = context.user_data["manual_category"]
    reminder = context.user_data["manual_time"]

    goal_id = add_goal(
        user_id,
        name,
        category,
        reminder,
        days,
    )

    context.user_data.clear()

    await query.message.reply_text(
        "✅ هدف ثبت شد.\n\n"
        f"🎯 {name}\n"
        f"📁 {category}\n"
        f"⏰ {reminder or 'بدون یادآوری'}\n"
        f"📅 {days_text(days)}",
        reply_markup=main_keyboard(),
    )


# ============================================================
# اهداف امروز
# ============================================================

async def today_command(update, context):
    user_id = update.effective_user.id

    goals = get_goals(user_id)

    if not goals:
        await update.message.reply_text(
            "🎯 هنوز هدفی نداری."
        )
        return

    current = today()
    weekday = current.weekday()

    buttons = []

    for goal in goals:
        if not goal["enabled"]:
            continue

        days = [
            int(x)
            for x in goal["repeat_days"].split(",")
            if x
        ]

        if weekday not in days:
            continue

        status = get_status(
            user_id,
            goal["id"],
            date_string(current),
        )

        icon = {
            "done": "✅",
            "missed": "❌",
            "pending": "⬜",
        }.get(status, "⬜")

        buttons.append([
            InlineKeyboardButton(
                f"{icon} {goal['name']}",
                callback_data=f"gdetail:{goal['id']}",
            )
        ])

    if not buttons:
        await update.message.reply_text(
            "🎯 برای امروز هدفی نداری."
        )
        return

    await update.message.reply_text(
        "🎯 اهداف امروز\n\n"
        "برای دیدن جزئیات روی هدف بزن.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


# ============================================================
# جزئیات هدف
# ============================================================

async def goal_detail(update, context):
    query = update.callback_query
    await query.answer()

    goal_id = int(
        query.data.split(":")[1]
    )

    goal = get_goal(
        query.from_user.id,
        goal_id,
    )

    if not goal:
        return

    status = get_status(
        query.from_user.id,
        goal_id,
        date_string(today()),
    )

    icon = {
        "done": "✅",
        "missed": "❌",
        "pending": "⬜",
    }.get(status, "⬜")

    text = (
        f"🎯 {goal['name']}\n\n"
        f"📁 دسته: {goal['category']}\n"
        f"⏰ زمان: {goal['reminder_time'] or 'خاموش'}\n"
        f"📅 روزها: {days_text(goal['repeat_days'])}\n"
        f"🔔 یادآوری: "
        f"{'فعال' if goal['enabled'] else 'خاموش'}\n\n"
        f"امروز: {icon}"
    )

    buttons = [
        [
            InlineKeyboardButton(
                "✅ انجام دادم",
                callback_data=f"done:{goal_id}",
            ),
            InlineKeyboardButton(
                "❌ انجام ندادم",
                callback_data=f"miss:{goal_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "📅 جدول هفتگی",
                callback_data=f"week:{goal_id}",
            )
        ],
    ]

    await query.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def status_callback(update, context):
    query = update.callback_query
    await query.answer()

    action, goal_id = query.data.split(":")

    status = (
        "done"
        if action == "done"
        else "missed"
    )

    set_status(
        query.from_user.id,
        int(goal_id),
        date_string(today()),
        status,
    )

    if status == "done":
        text = "✅ انجام هدف ثبت شد."
    else:
        text = "❌ هدف به عنوان انجام‌نشده ثبت شد."

    await query.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


# ============================================================
# ویرایش اهداف
# ============================================================

async def edit_menu(update, context):
    goals = get_goals(
        update.effective_user.id
    )

    if not goals:
        await update.message.reply_text(
            "🎯 هنوز هدفی ثبت نکردی."
        )
        return

    buttons = []

    for goal in goals:
        icon = "🟢" if goal["enabled"] else "🔴"

        buttons.append([
            InlineKeyboardButton(
                f"{icon} {goal['name']}",
                callback_data=f"edit:{goal['id']}",
            )
        ])

    await update.message.reply_text(
        "✏️ هدفی را که می‌خواهی ویرایش کنی انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def edit_goal(update, context):
    query = update.callback_query
    await query.answer()

    goal_id = int(
        query.data.split(":")[1]
    )

    goal = get_goal(
        query.from_user.id,
        goal_id,
    )

    if not goal:
        return

    text = (
        f"✏️ ویرایش هدف\n\n"
        f"🎯 {goal['name']}\n"
        f"⏰ {goal['reminder_time'] or 'خاموش'}\n"
        f"📅 {days_text(goal['repeat_days'])}\n"
        f"🔔 {'فعال' if goal['enabled'] else 'خاموش'}"
    )

    buttons = [
        [
            InlineKeyboardButton(
                "✏️ تغییر نام",
                callback_data=f"ename:{goal_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "⏰ تغییر ساعت",
                callback_data=f"etime:{goal_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "📅 تغییر روزها",
                callback_data=f"edays:{goal_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "🔔 روشن / خاموش",
                callback_data=f"toggle:{goal_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "🗑 حذف هدف",
                callback_data=f"del:{goal_id}",
            )
        ],
    ]

    await query.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def edit_name_start(update, context):
    query = update.callback_query
    await query.answer()

    goal_id = int(
        query.data.split(":")[1]
    )

    context.user_data["edit_goal_id"] = goal_id

    await query.message.reply_text(
        "✏️ نام جدید هدف را بفرست."
    )

    return EDIT_NAME


async def edit_name_save(update, context):
    goal_id = context.user_data.get(
        "edit_goal_id"
    )

    name = update.message.text.strip()

    if not name:
        await update.message.reply_text(
            "نام هدف خالی است."
        )
        return EDIT_NAME

    update_goal_name(
        update.effective_user.id,
        goal_id,
        name,
    )

    context.user_data.clear()

    await update.message.reply_text(
        "✅ نام هدف تغییر کرد.",
        reply_markup=main_keyboard(),
    )

    return ConversationHandler.END


async def edit_time_start(update, context):
    query = update.callback_query
    await query.answer()

    goal_id = int(
        query.data.split(":")[1]
    )

    context.user_data["edit_goal_id"] = goal_id

    await query.message.reply_text(
        "⏰ ساعت جدید را وارد کن.\n\n"
        "18:00\n"
        "۱۸:۰۰\n"
        "1800\n"
        "۱۸۰۰\n\n"
        "یا:\n"
        "بدون یادآوری"
    )

    return EDIT_TIME


async def edit_time_save(update, context):
    goal_id = context.user_data.get(
        "edit_goal_id"
    )

    value = update.message.text.strip()

    if value == "بدون یادآوری":
        reminder = None
    else:
        reminder = parse_time(value)

        if reminder is None:
            await update.message.reply_text(
                "❌ ساعت اشتباه است."
            )
            return EDIT_TIME

    update_goal_time(
        update.effective_user.id,
        goal_id,
        reminder,
    )

    context.user_data.clear()

    await update.message.reply_text(
        "✅ ساعت هدف تغییر کرد.",
        reply_markup=main_keyboard(),
    )

    return ConversationHandler.END


async def edit_days_start(update, context):
    query = update.callback_query
    await query.answer()

    goal_id = int(
        query.data.split(":")[1]
    )

    context.user_data["edit_goal_id"] = goal_id
    context.user_data["edit_days"] = []

    buttons = []

    for index in range(7):
        buttons.append([
            InlineKeyboardButton(
                day_name(index),
                callback_data=f"eday:{index}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "✅ ثبت روزها",
            callback_data="edaysfinish",
        )
    ])

    await query.message.reply_text(
        "📅 روزهای جدید را انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )

    return EDIT_DAYS


async def edit_day_select(update, context):
    query = update.callback_query
    await query.answer()

    value = query.data.split(":")[1]

    selected = context.user_data.get(
        "edit_days",
        [],
    )

    if value not in selected:
        selected.append(value)
    else:
        selected.remove(value)

    context.user_data["edit_days"] = selected


async def edit_days_finish(update, context):
    query = update.callback_query
    await query.answer()

    selected = context.user_data.get(
        "edit_days",
        [],
    )

    if not selected:
        await query.message.reply_text(
            "حداقل یک روز را انتخاب کن."
        )
        return EDIT_DAYS

    selected.sort(
        key=lambda x: int(x)
    )

    goal_id = context.user_data["edit_goal_id"]

    update_goal_days(
        query.from_user.id,
        goal_id,
        ",".join(selected),
    )

    context.user_data.clear()

    await query.message.reply_text(
        "✅ روزهای هدف تغییر کرد.",
        reply_markup=main_keyboard(),
    )

    return ConversationHandler.END


async def toggle_callback(update, context):
    query = update.callback_query
    await query.answer()

    goal_id = int(
        query.data.split(":")[1]
    )

    toggle_goal(
        query.from_user.id,
        goal_id,
    )

    await query.message.reply_text(
        "✅ وضعیت یادآوری تغییر کرد.",
        reply_markup=main_keyboard(),
    )


async def delete_start(update, context):
    query = update.callback_query
    await query.answer()

    goal_id = int(
        query.data.split(":")[1]
    )

    goal = get_goal(
        query.from_user.id,
        goal_id,
    )

    if not goal:
        return

    buttons = [
        [
            InlineKeyboardButton(
                "✅ بله، حذف کن",
                callback_data=f"deleteyes:{goal_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ لغو",
                callback_data="deleteno",
            )
        ],
    ]

    await query.message.reply_text(
        "⚠️ حذف هدف\n\n"
        f"🎯 {goal['name']}\n\n"
        "این هدف حذف شود؟",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def delete_confirm(update, context):
    query = update.callback_query
    await query.answer()

    goal_id = int(
        query.data.split(":")[1]
    )

    delete_goal(
        query.from_user.id,
        goal_id,
    )

    await query.message.reply_text(
        "🗑 هدف حذف شد.",
        reply_markup=main_keyboard(),
    )


async def delete_cancel(update, context):
    query = update.callback_query
    await query.answer()

    await query.message.reply_text(
        "❌ حذف لغو شد.",
        reply_markup=main_keyboard(),
    )


# ============================================================
# جدول هفتگی
# ============================================================

async def week_menu(update, context):
    goals = get_goals(
        update.effective_user.id
    )

    if not goals:
        await update.message.reply_text(
            "هنوز هدفی ثبت نکردی."
        )
        return

    buttons = []

    for goal in goals:
        buttons.append([
            InlineKeyboardButton(
                f"📅 {goal['name']}",
                callback_data=f"week:{goal['id']}",
            )
        ])

    await update.message.reply_text(
        "📅 هدف را انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def week_callback(update, context):
    query = update.callback_query
    await query.answer()

    goal_id = int(
        query.data.split(":")[1]
    )

    goal = get_goal(
        query.from_user.id,
        goal_id,
    )

    if not goal:
        return

    start = today() - timedelta(days=6)

    text = (
        f"📅 جدول ۷ روز اخیر\n\n"
        f"🎯 {goal['name']}\n\n"
    )

    done_count = 0

    for i in range(7):
        current = start + timedelta(days=i)

        status = get_status(
            query.from_user.id,
            goal_id,
            date_string(current),
        )

        if status == "done":
            icon = "✅"
            done_count += 1
        elif status == "missed":
            icon = "❌"
        else:
            icon = "⬜"

        text += (
            f"{day_name(current.weekday())} "
            f"{current.strftime('%m/%d')} "
            f"{icon}\n"
        )

    text += (
        f"\n📊 انجام‌شده: {done_count} از 7"
    )

    await query.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


# ============================================================
# آمار
# ============================================================

async def stats(update, context):
    user_id = update.effective_user.id

    goals = get_goals(user_id)

    total = 0
    done = 0
    missed = 0

    current = today()
    weekday = current.weekday()

    for goal in goals:
        days = [
            int(x)
            for x in goal["repeat_days"].split(",")
            if x
        ]

        if weekday not in days:
            continue

        total += 1

        status = get_status(
            user_id,
            goal["id"],
            date_string(current),
        )

        if status == "done":
            done += 1

        elif status == "missed":
            missed += 1

    pending = total - done - missed

    percent = 0

    if total:
        percent = int(
            done / total * 100
        )

    await update.message.reply_text(
        "📊 آمار امروز\n\n"
        f"🎯 اهداف امروز: {total}\n"
        f"✅ انجام‌شده: {done}\n"
        f"❌ انجام‌نشده: {missed}\n"
        f"⬜ بدون ثبت: {pending}\n\n"
        f"📈 پیشرفت: {percent}%",
        reply_markup=main_keyboard(),
    )


# ============================================================
# یادآوری
# ============================================================

async def reminder_job(context):
    now = datetime.now(TEHRAN)

    current_time = now.strftime("%H:%M")
    current_day = now.weekday()
    current_date = date_string(
        now.date()
    )

    conn = get_db()

    goals = conn.execute("""
        SELECT *
        FROM goals
        WHERE enabled = 1
        AND reminder_time = ?
    """, (
        current_time,
    )).fetchall()

    conn.close()

    for goal in goals:
        days = [
            int(x)
            for x in goal["repeat_days"].split(",")
            if x
        ]

        if current_day not in days:
            continue

        status = get_status(
            goal["user_id"],
            goal["id"],
            current_date,
        )

        if status == "done":
            continue

        buttons = [
            [
                InlineKeyboardButton(
                    "✅ انجام دادم",
                    callback_data=f"done:{goal['id']}",
                ),
                InlineKeyboardButton(
                    "❌ انجام ندادم",
                    callback_data=f"miss:{goal['id']}",
                ),
            ]
        ]

        try:
            await context.bot.send_message(
                chat_id=goal["user_id"],
                text=(
                    "⏰ یادآوری هدف\n\n"
                    f"🎯 {goal['name']}\n\n"
                    "امروز انجامش دادی؟"
                ),
                reply_markup=InlineKeyboardMarkup(
                    buttons
                ),
            )

        except Exception as error:
            logger.error(
                "Reminder error: %s",
                error,
            )


# ============================================================
# تنظیمات
# ============================================================

async def settings(update, context):
    buttons = [
        [
            InlineKeyboardButton(
                "🇮🇷 فارسی",
                callback_data="setlang:fa",
            ),
            InlineKeyboardButton(
                "🇬🇧 English",
                callback_data="setlang:en",
            ),
        ]
    ]

    await update.message.reply_text(
        "⚙️ تنظیمات\n\n"
        "🌐 زبان ربات را انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def settings_language(update, context):
    query = update.callback_query
    await query.answer()

    language = query.data.split(":")[1]

    save_language(
        query.from_user.id,
        language,
    )

    await query.message.reply_text(
        "✅ زبان تغییر کرد.",
        reply_markup=main_keyboard(),
    )


# ============================================================
# پیام‌های منو
# ============================================================

async def text_router(update, context):
    text = update.message.text

    if text == "🎯 اهداف امروز":
        await today_command(update, context)
        return

    if text == "➕ هدف جدید":
        await add_start(update, context)
        return

    if text == "🏆 اهداف آماده":
        await ready_menu(update, context)
        return

    if text == "✏️ ویرایش اهداف":
        await edit_menu(update, context)
        return

    if text == "📅 جدول هفتگی":
        await week_menu(update, context)
        return

    if text == "📊 آمار من":
        await stats(update, context)
        return

    if text == "⚙️ تنظیمات":
        await settings(update, context)
        return

    handled = await ready_time_handler(
        update,
        context,
    )

    if handled:
        return

    await update.message.reply_text(
        "از منوی ربات استفاده کن.",
        reply_markup=main_keyboard(),
    )


# ============================================================
# لغو
# ============================================================

async def cancel(update, context):
    context.user_data.clear()

    await update.message.reply_text(
        "❌ عملیات لغو شد.",
        reply_markup=main_keyboard(),
    )

    return ConversationHandler.END


# ============================================================
# خطا
# ============================================================

async def error_handler(update, context):
    logger.error(
        "Bot error",
        exc_info=context.error,
    )


# ============================================================
# MAIN
# ============================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN را در Variables محیط قرار بده."
        )

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # --------------------------------------------------------
    # زبان
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            language_callback,
            pattern=r"^lang:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            settings_language,
            pattern=r"^setlang:",
        )
    )

    # --------------------------------------------------------
    # افزودن دستی
    # --------------------------------------------------------

    add_conversation = ConversationHandler(
        entry_points=[
            CommandHandler(
                "addgoal",
                add_start,
            ),
            MessageHandler(
                filters.Regex("^➕ هدف جدید$"),
                add_start,
            ),
        ],

        states={
            ADD_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    add_name,
                )
            ],

            ADD_CATEGORY: [
                CallbackQueryHandler(
                    manual_category,
                    pattern=r"^mcat:",
                )
            ],

            ADD_TIME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    manual_time,
                )
            ],

            ADD_DAYS: [
                CallbackQueryHandler(
                    manual_days,
                    pattern=r"^mday:",
                ),
                CallbackQueryHandler(
                    manual_choose,
                    pattern=r"^mchoose:",
                ),
                CallbackQueryHandler(
                    manual_finish,
                    pattern=r"^mfinish$",
                ),
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
        add_conversation
    )

    # --------------------------------------------------------
    # ویرایش
    # --------------------------------------------------------

    edit_conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                edit_name_start,
                pattern=r"^ename:",
            ),
            CallbackQueryHandler(
                edit_time_start,
                pattern=r"^etime:",
            ),
            CallbackQueryHandler(
                edit_days_start,
                pattern=r"^edays:",
            ),
        ],

        states={
            EDIT_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    edit_name_save,
                )
            ],

            EDIT_TIME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    edit_time_save,
                )
            ],

            EDIT_DAYS: [
                CallbackQueryHandler(
                    edit_day_select,
                    pattern=r"^eday:",
                ),
                CallbackQueryHandler(
                    edit_days_finish,
                    pattern=r"^edaysfinish$",
                ),
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
        edit_conversation
    )

    # --------------------------------------------------------
    # اهداف آماده
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            ready_category,
            pattern=r"^rcat:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            ready_select,
            pattern=r"^rsel:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            ready_add,
            pattern=r"^radd$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            ready_confirm,
            pattern=r"^rconfirm$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            ready_back,
            pattern=r"^rback$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            ready_cancel,
            pattern=r"^rcancel$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            ready_days,
            pattern=r"^rday:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            ready_choose_day,
            pattern=r"^rchoose:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            ready_finish_days,
            pattern=r"^rfinish$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            ready_final,
            pattern=r"^rfinal$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            ready_change,
            pattern=r"^rchange$",
        )
    )

    # --------------------------------------------------------
    # جزئیات و وضعیت
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            goal_detail,
            pattern=r"^gdetail:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            status_callback,
            pattern=r"^(done|miss):",
        )
    )

    # --------------------------------------------------------
    # ویرایش و حذف
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            edit_goal,
            pattern=r"^edit:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            toggle_callback,
            pattern=r"^toggle:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            delete_start,
            pattern=r"^del:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            delete_confirm,
            pattern=r"^deleteyes:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            delete_cancel,
            pattern=r"^deleteno$",
        )
    )

    # --------------------------------------------------------
    # جدول
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            week_callback,
            pattern=r"^week:",
        )
    )

    # --------------------------------------------------------
    # متن منو
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_router,
        )
    )

    application.add_error_handler(
        error_handler
    )

    # --------------------------------------------------------
    # یادآوری هر دقیقه
    # --------------------------------------------------------

    if application.job_queue:
        application.job_queue.run_repeating(
            reminder_job,
            interval=60,
            first=10,
        )

    logger.info(
        "Goal bot started"
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
