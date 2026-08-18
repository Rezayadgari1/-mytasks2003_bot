import logging
import os
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

ADD_NAME = 1
ADD_CATEGORY = 2
ADD_TIME = 3
ADD_DAYS = 4


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
        "تمرین مهارت جدید",
        "مطالعه کتاب",
        "مرور مطالب",
    ],
    "کار و شغل": [
        "برنامه‌ریزی کارهای امروز",
        "انجام مهم‌ترین کار روز",
        "بررسی ایمیل‌ها",
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
        "مطالعه مطالب مورد علاقه",
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
        "مطالعه روزانه",
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


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'عمومی',
            reminder_time TEXT,
            repeat_type TEXT NOT NULL DEFAULT 'daily',
            repeat_days TEXT NOT NULL DEFAULT '0,1,2,3,4,5,6',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS goal_days (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            goal_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            completed_at TEXT,
            UNIQUE(goal_id, goal_date)
        )
        """
    )

    conn.commit()
    conn.close()


def today():
    return datetime.now(TEHRAN).date()


def date_string(value):
    return value.strftime("%Y-%m-%d")


def weekday_name(value):
    names = {
        0: "دوشنبه",
        1: "سه‌شنبه",
        2: "چهارشنبه",
        3: "پنجشنبه",
        4: "جمعه",
        5: "شنبه",
        6: "یکشنبه",
    }
    return names[value.weekday()]


def add_goal(
    user_id,
    name,
    category,
    reminder_time,
    repeat_days,
):
    conn = get_db()

    now = datetime.now(TEHRAN).isoformat()

    cur = conn.execute(
        """
        INSERT INTO goals (
            user_id,
            name,
            category,
            reminder_time,
            repeat_type,
            repeat_days,
            enabled,
            created_at
        )
        VALUES (?, ?, ?, ?, 'weekly', ?, 1, ?)
        """,
        (
            user_id,
            name,
            category,
            reminder_time,
            repeat_days,
            now,
        ),
    )

    goal_id = cur.lastrowid

    conn.commit()
    conn.close()

    return goal_id


def get_goals(user_id):
    conn = get_db()

    rows = conn.execute(
        """
        SELECT *
        FROM goals
        WHERE user_id = ?
        ORDER BY id DESC
        """,
        (user_id,),
    ).fetchall()

    conn.close()

    return rows


def get_goal(user_id, goal_id):
    conn = get_db()

    row = conn.execute(
        """
        SELECT *
        FROM goals
        WHERE user_id = ?
        AND id = ?
        """,
        (
            user_id,
            goal_id,
        ),
    ).fetchone()

    conn.close()

    return row


def delete_goal(user_id, goal_id):
    conn = get_db()

    conn.execute(
        """
        DELETE FROM goal_days
        WHERE user_id = ?
        AND goal_id = ?
        """,
        (
            user_id,
            goal_id,
        ),
    )

    cur = conn.execute(
        """
        DELETE FROM goals
        WHERE user_id = ?
        AND id = ?
        """,
        (
            user_id,
            goal_id,
        ),
    )

    conn.commit()

    result = cur.rowcount > 0

    conn.close()

    return result


def set_goal_status(
    user_id,
    goal_id,
    goal_date,
    status,
):
    conn = get_db()

    completed_at = None

    if status == "done":
        completed_at = datetime.now(
            TEHRAN
        ).isoformat()

    conn.execute(
        """
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
        """,
        (
            goal_id,
            user_id,
            goal_date,
            status,
            completed_at,
        ),
    )

    conn.commit()
    conn.close()


def get_goal_status(
    user_id,
    goal_id,
    goal_date,
):
    conn = get_db()

    row = conn.execute(
        """
        SELECT status
        FROM goal_days
        WHERE user_id = ?
        AND goal_id = ?
        AND goal_date = ?
        """,
        (
            user_id,
            goal_id,
            goal_date,
        ),
    ).fetchone()

    conn.close()

    if not row:
        return "pending"

    return row["status"]


def get_week_status(
    user_id,
    goal_id,
):
    conn = get_db()

    start = today() - timedelta(days=6)
    end = today()

    rows = conn.execute(
        """
        SELECT goal_date, status
        FROM goal_days
        WHERE user_id = ?
        AND goal_id = ?
        AND goal_date BETWEEN ? AND ?
        """,
        (
            user_id,
            goal_id,
            date_string(start),
            date_string(end),
        ),
    ).fetchall()

    conn.close()

    return {
        row["goal_date"]: row["status"]
        for row in rows
    }


def selected_days_text(days):
    if days == "0,1,2,3,4,5,6":
        return "هر روز"

    names = {
        "0": "دوشنبه",
        "1": "سه‌شنبه",
        "2": "چهارشنبه",
        "3": "پنجشنبه",
        "4": "جمعه",
        "5": "شنبه",
        "6": "یکشنبه",
    }

    result = []

    for item in days.split(","):
        if item in names:
            result.append(names[item])

    return "، ".join(result)


def main_keyboard():
    keyboard = [
        ["🎯 اهداف امروز", "➕ هدف جدید"],
        ["🏆 اهداف آماده", "📅 جدول هفتگی"],
        ["📊 آمار من", "⚙️ تنظیمات"],
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
    )


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    text = (
        "🎯 مدیریت اهداف روزانه\n\n"
        "هدف‌های خودت را ثبت کن.\n"
        "برای هر هدف زمان یادآوری تعیین کن.\n"
        "هر روز وضعیت هدف را ثبت کن.\n"
        "جدول هفتگی پیشرفتت را ببین."
    )

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    text = (
        "راهنمای ربات\n\n"
        "/start شروع ربات\n"
        "/addgoal افزودن هدف\n"
        "/goals اهداف من\n"
        "/today اهداف امروز\n"
        "/week جدول هفتگی\n"
        "/stats آمار\n"
        "/cancel لغو عملیات"
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
        "🎯 نام هدف را بفرست.\n\n"
        "مثال:\n"
        "۳۰ دقیقه ورزش"
    )

    return ADD_NAME


async def add_goal_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    name = update.message.text.strip()

    if not name:
        await update.message.reply_text(
            "نام هدف خالی است."
        )
        return ADD_NAME

    context.user_data["goal_name"] = name

    buttons = []

    for category in READY_GOALS.keys():
        buttons.append(
            [
                InlineKeyboardButton(
                    category,
                    callback_data=f"newcat:{category}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "📌 عمومی",
                callback_data="newcat:عمومی",
            )
        ]
    )

    await update.message.reply_text(
        "📁 دسته هدف را انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )

    return ADD_CATEGORY


async def new_category_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    category = query.data.split(":", 1)[1]

    context.user_data["goal_category"] = category

    await query.edit_message_text(
        "⏰ ساعت یادآوری را بفرست.\n\n"
        "مثال:\n"
        "18:00\n\n"
        "اگر یادآوری نمی‌خواهی، بنویس:\n"
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
            datetime.strptime(
                value,
                "%H:%M",
            )
            reminder = value
        except ValueError:
            await update.message.reply_text(
                "فرمت ساعت اشتباه است.\n"
                "مثال: 18:00"
            )
            return ADD_TIME

    context.user_data["goal_time"] = reminder

    buttons = [
        [
            InlineKeyboardButton(
                "هر روز",
                callback_data="days:all",
            )
        ],
        [
            InlineKeyboardButton(
                "شنبه تا چهارشنبه",
                callback_data="days:work",
            )
        ],
        [
            InlineKeyboardButton(
                "پنجشنبه و جمعه",
                callback_data="days:weekend",
            )
        ],
        [
            InlineKeyboardButton(
                "انتخاب روزها",
                callback_data="days:choose",
            )
        ],
    ]

    await update.message.reply_text(
        "📅 هدف در چه روزهایی تکرار شود؟",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )

    return ADD_DAYS


async def days_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    value = query.data.split(":", 1)[1]

    if value == "all":
        days = "0,1,2,3,4,5,6"

    elif value == "work":
        days = "0,1,2,3,5"

    elif value == "weekend":
        days = "4,6"

    else:
        buttons = []

        names = [
            ("دوشنبه", "0"),
            ("سه‌شنبه", "1"),
            ("چهارشنبه", "2"),
            ("پنجشنبه", "3"),
            ("جمعه", "4"),
            ("شنبه", "5"),
            ("یکشنبه", "6"),
        ]

        for name, number in names:
            buttons.append(
                [
                    InlineKeyboardButton(
                        name,
                        callback_data=f"selectday:{number}",
                    )
                ]
            )

        buttons.append(
            [
                InlineKeyboardButton(
                    "✅ پایان انتخاب",
                    callback_data="days:finish",
                )
            ]
        )

        context.user_data["selected_days"] = []

        await query.edit_message_text(
            "روزها را یکی‌یکی انتخاب کن.",
            reply_markup=InlineKeyboardMarkup(
                buttons
            ),
        )

        return ADD_DAYS

    await finish_goal_creation(
        query,
        context,
        days,
    )

    return ConversationHandler.END


async def select_day_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    value = query.data.split(":", 1)[1]

    selected = context.user_data.get(
        "selected_days",
        [],
    )

    if value not in selected:
        selected.append(value)

    context.user_data["selected_days"] = selected

    await query.answer(
        "روز انتخاب شد."
    )


async def finish_days_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    selected = context.user_data.get(
        "selected_days",
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

    days = ",".join(selected)

    await finish_goal_creation(
        query,
        context,
        days,
    )

    return ConversationHandler.END


async def finish_goal_creation(
    query,
    context,
    days,
):
    name = context.user_data.get(
        "goal_name",
        "",
    )

    category = context.user_data.get(
        "goal_category",
        "عمومی",
    )

    reminder = context.user_data.get(
        "goal_time"
    )

    goal_id = add_goal(
        query.from_user.id,
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
        f"📅 {selected_days_text(days)}",
        reply_markup=main_keyboard(),
    )

    await send_goal_details(
        query,
        goal_id,
    )


async def send_goal_details(
    query,
    goal_id,
):
    goal = get_goal(
        query.from_user.id,
        goal_id,
    )

    if not goal:
        return

    await query.message.reply_text(
        f"🎯 {goal['name']}\n\n"
        f"📁 دسته: {goal['category']}\n"
        f"⏰ زمان: {goal['reminder_time'] or 'خاموش'}\n"
        f"📅 {selected_days_text(goal['repeat_days'])}"
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

    buttons = []

    for goal in goals:
        buttons.append(
            [
                InlineKeyboardButton(
                    f"🎯 {goal['name']}",
                    callback_data=f"goal:{goal['id']}",
                )
            ]
        )

    await update.message.reply_text(
        "🎯 هدف مورد نظر را انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def goal_details_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    goal_id = int(
        query.data.split(":", 1)[1]
    )

    goal = get_goal(
        query.from_user.id,
        goal_id,
    )

    if not goal:
        await query.message.reply_text(
            "هدف پیدا نشد."
        )
        return

    status = get_goal_status(
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
        f"📅 {selected_days_text(goal['repeat_days'])}\n\n"
        f"امروز: {icon}"
    )

    buttons = [
        [
            InlineKeyboardButton(
                "✅ انجام شد",
                callback_data=f"done:{goal_id}",
            ),
            InlineKeyboardButton(
                "❌ انجام نشد",
                callback_data=f"missed:{goal_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "📅 جدول هفتگی",
                callback_data=f"week:{goal_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "🗑 حذف هدف",
                callback_data=f"delete:{goal_id}",
            )
        ],
    ]

    await query.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def status_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    action, goal_id_text = query.data.split(
        ":",
        1,
    )

    goal_id = int(goal_id_text)

    status = "done"

    if action == "missed":
        status = "missed"

    set_goal_status(
        query.from_user.id,
        goal_id,
        date_string(today()),
        status,
    )

    if status == "done":
        text = "✅ هدف امروز ثبت شد."
    else:
        text = "❌ هدف امروز به عنوان انجام‌نشده ثبت شد."

    await query.message.reply_text(
        text,
        reply_markup=main_keyboard(),
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

    current = today()
    weekday = current.weekday()

    text = "🎯 اهداف امروز\n\n"

    for goal in goals:
        days = [
            int(x)
            for x in goal["repeat_days"].split(",")
            if x
        ]

        if weekday not in days:
            continue

        status = get_goal_status(
            update.effective_user.id,
            goal["id"],
            date_string(current),
        )

        icon = {
            "done": "✅",
            "missed": "❌",
            "pending": "⬜",
        }.get(status, "⬜")

        text += (
            f"{icon} {goal['name']}\n"
            f"   ⏰ {goal['reminder_time'] or '-'}\n\n"
        )

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


async def weekly_table(
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

    buttons = []

    for goal in goals:
        buttons.append(
            [
                InlineKeyboardButton(
                    f"📅 {goal['name']}",
                    callback_data=f"week:{goal['id']}",
                )
            ]
        )

    await update.message.reply_text(
        "📅 هدف را برای دیدن جدول هفتگی انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def week_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    goal_id = int(
        query.data.split(":", 1)[1]
    )

    goal = get_goal(
        query.from_user.id,
        goal_id,
    )

    if not goal:
        return

    statuses = get_week_status(
        query.from_user.id,
        goal_id,
    )

    start = today() - timedelta(days=6)

    done_count = 0
    table = []

    for i in range(7):
        day = start + timedelta(days=i)
        key = date_string(day)

        status = statuses.get(
            key,
            "pending",
        )

        if status == "done":
            icon = "✅"
            done_count += 1
        elif status == "missed":
            icon = "❌"
        else:
            icon = "⬜"

        table.append(
            f"{weekday_name(day)}  {key[5:]}  {icon}"
        )

    text = (
        f"📅 جدول هفتگی\n\n"
        f"🎯 {goal['name']}\n\n"
        + "\n".join(table)
        + f"\n\n📊 انجام‌شده: {done_count} از 7"
    )

    await query.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


async def ready_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    buttons = []

    icons = {
        "سلامتی": "❤️",
        "ورزش و تناسب اندام": "💪",
        "تغذیه": "🥗",
        "مطالعه و آموزش": "📚",
        "کار و شغل": "💼",
        "مالی": "💰",
        "خانه و زندگی": "🏠",
        "ذهن و تمرکز": "🧠",
        "خواب و استراحت": "😴",
        "روابط اجتماعی": "👥",
        "سرگرمی": "🎮",
        "کنترل موبایل": "📱",
        "عادت‌های شخصی": "🌱",
        "معنوی": "🕌",
        "خودرو": "🚗",
        "نظم و نظافت": "🧹",
        "اهداف شخصی": "🎯",
    }

    for category in READY_GOALS:
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{icons.get(category, '🎯')} {category}",
                    callback_data=f"readycat:{category}",
                )
            ]
        )

    await update.message.reply_text(
        "🏆 اهداف آماده\n\n"
        "یک دسته را انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def ready_category_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    category = query.data.split(
        ":",
        1,
    )[1]

    goals = READY_GOALS.get(
        category,
        [],
    )

    buttons = []

    for index, name in enumerate(goals):
        buttons.append(
            [
                InlineKeyboardButton(
                    f"🎯 {name}",
                    callback_data=f"readygoal:{category}:{index}",
                )
            ]
        )

    await query.message.reply_text(
        f"🏆 اهداف آماده\n\n"
        f"📁 {category}\n\n"
        "یک هدف را انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def ready_goal_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")

    category = parts[1]
    index = int(parts[2])

    goals = READY_GOALS.get(
        category,
        [],
    )

    if index >= len(goals):
        return

    name = goals[index]

    context.user_data["goal_name"] = name
    context.user_data["goal_category"] = category

    await query.message.reply_text(
        f"🎯 هدف انتخاب شد:\n\n"
        f"{name}\n\n"
        "⏰ ساعت یادآوری را بفرست.\n\n"
        "مثال:\n"
        "18:00\n\n"
        "یا بنویس:\n"
        "بدون یادآوری"
    )


async def ready_goal_time_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if "goal_name" not in context.user_data:
        return

    value = update.message.text.strip()

    if value == "بدون یادآوری":
        reminder = None
    else:
        try:
            datetime.strptime(
                value,
                "%H:%M",
            )
            reminder = value
        except ValueError:
            await update.message.reply_text(
                "فرمت ساعت اشتباه است.\n"
                "مثال: 18:00"
            )
            return

    context.user_data["goal_time"] = reminder

    buttons = [
        [
            InlineKeyboardButton(
                "هر روز",
                callback_data="ready_days:all",
            )
        ],
        [
            InlineKeyboardButton(
                "انتخاب روزها",
                callback_data="ready_days:choose",
            )
        ],
    ]

    await update.message.reply_text(
        "📅 روزهای تکرار هدف را انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def ready_days_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    value = query.data.split(
        ":",
        1,
    )[1]

    if value == "all":
        days = "0,1,2,3,4,5,6"

        await create_ready_goal(
            query,
            context,
            days,
        )

        return

    context.user_data["ready_selected_days"] = []

    buttons = []

    names = [
        ("دوشنبه", "0"),
        ("سه‌شنبه", "1"),
        ("چهارشنبه", "2"),
        ("پنجشنبه", "3"),
        ("جمعه", "4"),
        ("شنبه", "5"),
        ("یکشنبه", "6"),
    ]

    for name, number in names:
        buttons.append(
            [
                InlineKeyboardButton(
                    name,
                    callback_data=f"rday:{number}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "✅ ثبت روزها",
                callback_data="rday:finish",
            )
        ]
    )

    await query.message.reply_text(
        "روزها را انتخاب کن.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def ready_select_day_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    value = query.data.split(
        ":",
        1,
    )[1]

    selected = context.user_data.get(
        "ready_selected_days",
        [],
    )

    if value == "finish":
        if not selected:
            await query.message.reply_text(
                "حداقل یک روز را انتخاب کن."
            )
            return

        selected.sort(
            key=lambda x: int(x)
        )

        await create_ready_goal(
            query,
            context,
            ",".join(selected),
        )

        return

    if value not in selected:
        selected.append(value)

    context.user_data["ready_selected_days"] = selected

    await query.answer(
        "روز انتخاب شد."
    )


async def create_ready_goal(
    query,
    context,
    days,
):
    name = context.user_data.get(
        "goal_name"
    )

    category = context.user_data.get(
        "goal_category",
        "عمومی",
    )

    reminder = context.user_data.get(
        "goal_time"
    )

    goal_id = add_goal(
        query.from_user.id,
        name,
        category,
        reminder,
        days,
    )

    context.user_data.clear()

    await query.message.reply_text(
        "✅ هدف آماده به اهداف تو اضافه شد.\n\n"
        f"🎯 {name}\n"
        f"📁 {category}\n"
        f"⏰ {reminder or 'بدون یادآوری'}\n"
        f"📅 {selected_days_text(days)}",
        reply_markup=main_keyboard(),
    )


async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id

    goals = get_goals(user_id)

    total = len(goals)
    done = 0
    missed = 0

    current = date_string(today())

    for goal in goals:
        status = get_goal_status(
            user_id,
            goal["id"],
            current,
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

    text = (
        "📊 آمار امروز\n\n"
        f"🎯 کل اهداف: {total}\n"
        f"✅ انجام‌شده: {done}\n"
        f"❌ انجام‌نشده: {missed}\n"
        f"⬜ بدون ثبت: {pending}\n\n"
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
    current_day = now.weekday()
    current_date = date_string(
        now.date()
    )

    conn = get_db()

    goals = conn.execute(
        """
        SELECT *
        FROM goals
        WHERE enabled = 1
        AND reminder_time = ?
        """,
        (current_time,),
    ).fetchall()

    conn.close()

    for goal in goals:
        days = [
            int(x)
            for x in goal["repeat_days"].split(",")
            if x
        ]

        if current_day not in days:
            continue

        status = get_goal_status(
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
                    callback_data=f"missed:{goal['id']}",
                ),
            ]
        ]

        try:
            await context.bot.send_message(
                chat_id=goal["user_id"],
                text=(
                    "⏰ یادآوری هدف\n\n"
                    f"🎯 {goal['name']}\n\n"
                    "امروز این هدف را انجام دادی؟"
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


async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    text = update.message.text

    if text == "🎯 اهداف امروز":
        await today_command(
            update,
            context,
        )
        return

    if text == "➕ هدف جدید":
        await add_goal_start(
            update,
            context,
        )
        return

    if text == "🏆 اهداف آماده":
        await ready_menu(
            update,
            context,
        )
        return

    if text == "📅 جدول هفتگی":
        await weekly_table(
            update,
            context,
        )
        return

    if text == "📊 آمار من":
        await stats_command(
            update,
            context,
        )
        return

    if text == "⚙️ تنظیمات":
        await update.message.reply_text(
            "⚙️ تنظیمات\n\n"
            "در نسخه بعدی بخش تنظیمات هدف‌ها از اینجا مدیریت می‌شود.",
            reply_markup=main_keyboard(),
        )
        return

    if (
        "goal_name" in context.user_data
        and "goal_category" in context.user_data
        and "goal_time" not in context.user_data
    ):
        await ready_goal_time_handler(
            update,
            context,
        )
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
        "Bot error",
        exc_info=context.error,
    )


def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN را در Variables وارد کن."
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
                filters.Regex("^➕ هدف جدید$"),
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
                    new_category_callback,
                    pattern=r"^newcat:",
                )
            ],
            ADD_TIME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    goal_time_received,
                )
            ],
            ADD_DAYS: [
                CallbackQueryHandler(
                    days_callback,
                    pattern=r"^days:",
                ),
                CallbackQueryHandler(
                    select_day_callback,
                    pattern=r"^selectday:",
                ),
                CallbackQueryHandler(
                    finish_days_callback,
                    pattern=r"^days:finish$",
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
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "goals",
            goals_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "today",
            today_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "week",
            weekly_table,
        )
    )

    application.add_handler(
        CommandHandler(
            "stats",
            stats_command,
        )
    )

    application.add_handler(
        conversation
    )

    application.add_handler(
        CallbackQueryHandler(
            ready_category_callback,
            pattern=r"^readycat:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            ready_goal_callback,
            pattern=r"^readygoal:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            ready_days_callback,
            pattern=r"^ready_days:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            ready_select_day_callback,
            pattern=r"^rday:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            goal_details_callback,
            pattern=r"^goal:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            status_callback,
            pattern=r"^(done|missed):",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            week_callback,
            pattern=r"^week:",
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

    logger.info("Bot started")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
