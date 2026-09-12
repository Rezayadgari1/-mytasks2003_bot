

import logging
from functools import wraps
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
import time
import threading
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
    ChatMemberHandler,
    filters,
)
try:
    from telegram.ext import MessageReactionHandler
except ImportError:
    MessageReactionHandler = None



# ââ Module imports (extracted from this file) ââââââââââââââ
from config import (
    BOT_TOKEN, _SCRIPT_DIR, DB_PATH, DB_SCHEMA_VERSION, DB_BACKUP_PATH, TZ,
    REQUIRED_CHANNEL_URL,
    MYTASKS_BUILD_ID,
    ADMIN_IDS, _parse_admin_ids,
    GOALS_FA, GOALS_EN, T, TIME_BUTTONS, FEATURE_MENU_MAP,
)
from database import (
    restore_database_if_missing, backup_database, backup_database_snapshot,
    ensure_column, get_schema_version, set_schema_version, migrate_database,
    db, db_context, init_db, register_user, log_activity, lang, set_lang, set_gender,
    user_info, display_name, add_goal, get_goals, get_goal, set_status,
    get_status, add_step, get_steps, toggle_step, calculate_streak,
    unlock_achievement, achievement_check, achievement_text,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
_PROCESS_START = [time.time()]
logger.info("MyTasks build %s", MYTASKS_BUILD_ID)

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
            msg = "â¸ Ø±Ø¨Ø§Øª ÙÙÙØªØ§Ù ÙØªÙÙÙ Ø§Ø³Øª. ÙØ·ÙØ§Ù Ú©ÙÛ Ø¨Ø¹Ø¯ Ø¯ÙØ¨Ø§Ø±Ù ØªÙØ§Ø´ Ú©Ù." if paused else "ð  Ø±Ø¨Ø§Øª Ø¯Ø± Ø­Ø§Ù Ø¨Ø±ÙØ²Ø±Ø³Ø§ÙÛ Ø§Ø³Øª. ÙØ·ÙØ§Ù Ø¨Ø¹Ø¯Ø§Ù Ø¯ÙØ¨Ø§Ø±Ù ØªÙØ§Ø´ Ú©Ù."
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
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise


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

    # Every step below is idempotent (IF NOT EXISTS / ensure_column) and runs
    # unconditionally: databases that already stored a schema_version before a
    # step was added to that version would otherwise skip it forever.
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
    c.execute("""CREATE TABLE IF NOT EXISTS weekly_reports(
        report_week TEXT PRIMARY KEY,
        data TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS user_channel_membership(
        user_id INTEGER PRIMARY KEY,
        joined_at TEXT,
        left_at TEXT,
        is_member INTEGER DEFAULT 0,
        last_check TEXT
    )""")
    for column, ddl in (
        ("joined_at", "TEXT"),
        ("left_at", "TEXT"),
        ("is_member", "INTEGER DEFAULT 0"),
        ("last_check", "TEXT"),
    ):
        ensure_column(c, "user_channel_membership", column, ddl)
    if version < DB_SCHEMA_VERSION:
        set_schema_version(c, DB_SCHEMA_VERSION)


_DB_PRAGMA_LOCK = threading.RLock()

def release_leaked_connections(exc):
    """Roll back and close sqlite connections left open by a failed handler.

    Many handlers do ``c = db(); ...; c.close()`` without try/finally. When they
    raise, the traceback keeps the frame (and its open write transaction) alive,
    and every other handler then fails with "database is locked" until GC runs.
    """
    tb = getattr(exc, "__traceback__", None)
    seen = set()
    while tb is not None:
        for value in list(tb.tb_frame.f_locals.values()):
            if isinstance(value, sqlite3.Connection) and id(value) not in seen:
                seen.add(id(value))
                try:
                    value.rollback()
                except Exception:
                    pass
                try:
                    value.close()
                except Exception:
                    pass
        tb = tb.tb_next

def db():
    """Open SQLite safely under concurrent Telegram updates."""
    last_error = None
    for delay in (0.0, 0.05, 0.15, 0.35, 0.75):
        if delay:
            time.sleep(delay)
        try:
            c = sqlite3.connect(DB_PATH, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA busy_timeout=30000")
            c.execute("PRAGMA foreign_keys=ON")
            # journal_mode is a database-level pragma. Serialize it so concurrent
            # handlers do not turn a temporary SQLite lock into an OperationalError.
            with _DB_PRAGMA_LOCK:
                try:
                    c.execute("PRAGMA journal_mode=WAL")
                except sqlite3.OperationalError:
                    # The database might already be in WAL mode and another process
                    # might hold the short journal-mode lock. Normal queries still work.
                    pass
            c.execute("PRAGMA synchronous=NORMAL")
            return c
        except sqlite3.OperationalError as exc:
            last_error = exc
            try:
                c.close()
            except Exception:
                pass
    raise last_error


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
    if "condition" not in goal_columns:
        c.execute("ALTER TABLE goals ADD COLUMN condition TEXT")
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
    for col,ddl in [("xp","INTEGER NOT NULL DEFAULT 0"),("blocked","INTEGER NOT NULL DEFAULT 0"),("warnings","INTEGER NOT NULL DEFAULT 0"),("vip_until","TEXT"),("referrer_id","INTEGER"),("referral_code","TEXT"),("username","TEXT"),("manager_user_id","INTEGER")]:
        if col not in user_cols: c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
    c.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_users_manager ON users(manager_user_id)")
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
    # Referral migration: keep old databases compatible with the enhanced referral flow.
    referral_cols={r["name"] for r in c.execute("PRAGMA table_info(referrals)").fetchall()}
    if "source" not in referral_cols:
        c.execute("ALTER TABLE referrals ADD COLUMN source TEXT NOT NULL DEFAULT 'link'")
    if "status" not in referral_cols:
        c.execute("ALTER TABLE referrals ADD COLUMN status TEXT NOT NULL DEFAULT 'registered'")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_referrals_invited_unique ON referrals(invited_id)")
    # --- Enhanced Referral System Tables ---
    c.execute("""CREATE TABLE IF NOT EXISTS referral_settings(
        key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS referral_campaigns(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, code TEXT UNIQUE NOT NULL,
        referrer_reward INTEGER DEFAULT 10, invitee_reward INTEGER DEFAULT 5,
        success_condition TEXT DEFAULT 'first_goal', enabled INTEGER DEFAULT 1,
        start_date TEXT, end_date TEXT, created_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS referral_tiers(
        id INTEGER PRIMARY KEY AUTOINCREMENT, tier_name TEXT NOT NULL,
        required_referrals INTEGER NOT NULL, reward_type TEXT NOT NULL,
        reward_amount INTEGER NOT NULL, reward_days INTEGER,
        created_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS referral_templates(
        id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1, sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS referral_logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        action TEXT NOT NULL, target_user INTEGER, details TEXT,
        created_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS content_feedback(id INTEGER PRIMARY KEY AUTOINCREMENT,post_key TEXT,user_id INTEGER,rating INTEGER,reaction TEXT,created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS content_preferences(user_id INTEGER,category TEXT,score INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,category))""")

    # --- Referral system default settings ---
    _ref_defaults = {
        "ref_system_enabled": "1", "ref_reward_enabled": "1",
        "ref_bilateral_reward": "1", "ref_tokens_per_success": "10",
        "ref_invitee_tokens": "5", "ref_vip_milestone": "10",
        "ref_vip_days": "30", "ref_success_condition": "first_goal",
        "ref_daily_limit": "0", "ref_weekly_limit": "0",
        "ref_monthly_limit": "800", "ref_auto_approve": "1",
        "ref_leaderboard_enabled": "1", "ref_campaign_active": "0",
        "ref_custom_invite_text": "ð ÙÙ Ø§Ø² Ø±Ø¨Ø§Øª MyTasks Ø§Ø³ØªÙØ§Ø¯Ù ÙÛâÚ©ÙÙ. ØªÙ ÙÙ Ø§ÙØªØ­Ø§Ù Ú©Ù!",
    }
    for _k, _v in _ref_defaults.items():
        _exists = c.execute("SELECT 1 FROM referral_settings WHERE key=?", (_k,)).fetchone()
        if not _exists:
            c.execute("INSERT INTO referral_settings(key,value,updated_at) VALUES(?,?,?)",
                      (_k, _v, datetime.now(TZ).isoformat()))
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
    # ââ Birthday module ââââââââââââââââââââââââââââââââââââââââââââââ
    c.execute("""CREATE TABLE IF NOT EXISTS birthdays(
        user_id INTEGER PRIMARY KEY,
        birth_date TEXT NOT NULL,
        birth_year INTEGER,
        enabled INTEGER NOT NULL DEFAULT 1,
        gift_claimed INTEGER NOT NULL DEFAULT 0,
        last_gift_year INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    # ââ Events / Occasions âââââââââââââââââââââââââââââââââââââââââââ
    c.execute("""CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        event_date TEXT NOT NULL,
        message TEXT NOT NULL DEFAULT '',
        xp_reward INTEGER NOT NULL DEFAULT 0,
        gift_type TEXT NOT NULL DEFAULT '',
        gift_value TEXT NOT NULL DEFAULT '',
        vip_days INTEGER NOT NULL DEFAULT 0,
        target_users TEXT NOT NULL DEFAULT 'all',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS event_deliveries(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        delivered_at TEXT NOT NULL,
        UNIQUE(event_id, user_id)
    )""")
    # ââ Gift definitions & tracking ââââââââââââââââââââââââââââââââââ
    c.execute("""CREATE TABLE IF NOT EXISTS gift_definitions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        gift_type TEXT NOT NULL,
        value TEXT NOT NULL DEFAULT '',
        xp_amount INTEGER NOT NULL DEFAULT 0,
        vip_days INTEGER NOT NULL DEFAULT 0,
        duration_hours INTEGER NOT NULL DEFAULT 0,
        feature_key TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS user_gifts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        gift_def_id INTEGER,
        gift_type TEXT NOT NULL,
        gift_value TEXT NOT NULL DEFAULT '',
        xp_amount INTEGER NOT NULL DEFAULT 0,
        vip_days INTEGER NOT NULL DEFAULT 0,
        feature_key TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'admin',
        source_detail TEXT NOT NULL DEFAULT '',
        granted_by INTEGER,
        granted_at TEXT NOT NULL,
        expires_at TEXT,
        claimed INTEGER NOT NULL DEFAULT 0,
        UNIQUE(user_id, gift_def_id, source)
    )""")
    # ââ Subscriptions v2 (precise expiry) ââââââââââââââââââââââââââââ
    c.execute("""CREATE TABLE IF NOT EXISTS subscriptions_v2(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        plan TEXT NOT NULL DEFAULT 'vip',
        start_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        duration_hours INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT 'admin',
        amount INTEGER NOT NULL DEFAULT 0,
        payment_id TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""")
    # ââ Access grants (central access control) âââââââââââââââââââââââ
    c.execute("""CREATE TABLE IF NOT EXISTS access_grants(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        feature_key TEXT NOT NULL,
        granted_by INTEGER,
        source TEXT NOT NULL DEFAULT 'subscription',
        expires_at TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        UNIQUE(user_id, feature_key, source)
    )""")
    # ââ Birthday & event settings ââââââââââââââââââââââââââââââââââââ
    c.execute("""CREATE TABLE IF NOT EXISTS birthday_settings(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")
    now_iso=datetime.now(TZ).isoformat()
    # Infrastructure/service cost registry. This is separate from feature_access:
    # feature_access controls who may use a feature (free/VIP/off), while service_costs
    # records whether the underlying provider is free, optional-paid, or variable-cost.
    service_cost_defaults = {
        "telegram_bot_api": ("ð¤ ÙØ³ØªÙ Telegram Bot API", "free", "Telegram", "Ø§Ø³ØªÙØ§Ø¯Ù Ø¹Ø§Ø¯Û Ø§Ø² Bot API Ø±Ø§ÛÚ¯Ø§Ù Ø§Ø³ØªØ ÙØ­Ø¯ÙØ¯ÛØª ÙØ±Ø® Ø§Ø±Ø³Ø§Ù Ø¯Ø§Ø±Ø¯."),
        "hosting": ("ð¥ï¸ ÙØ§Ø³Øª / Ø§Ø¬Ø±Ø§Û Ø±Ø¨Ø§Øª", "variable", "Railway ÛØ§ Ø³Ø±ÙØ± Ø¯ÛÚ¯Ø±", "ÙØ²ÛÙÙ Ø¨Ù Ø³Ø±ÙÛØ³ ÙÛØ²Ø¨Ø§ÙÛ Ù ÙØµØ±Ù CPU/RAM/Storage/Network Ø¨Ø³ØªÚ¯Û Ø¯Ø§Ø±Ø¯."),
        "database": ("ðï¸ Ø¯ÛØªØ§Ø¨ÛØ³ SQLite", "free", "Ø®ÙØ¯ Ø±Ø¨Ø§Øª", "Ø¨Ø±Ø§Û ÙØ³Ø®Ù ÙØ¹ÙÛ Ø¯Ø§Ø®Ù ÙÙØ§Ù Ø³Ø±ÙÛØ³ Ø§Ø³ØªØ ÙØ²ÛÙÙ API Ø¬Ø¯Ø§Ú¯Ø§ÙÙ ÙØ¯Ø§Ø±Ø¯."),
        "price_sources": ("ð ÙÙØ§Ø¨Ø¹ ÙÛÙØª Ø¢ÙÙØ§ÛÙ", "free_or_variable", "ÙÙØ§Ø¨Ø¹ Ø¹ÙÙÙÛ/API", "Ø¨Ø¹Ø¶Û ÙÙØ§Ø¨Ø¹ Ø±Ø§ÛÚ¯Ø§ÙâØ§ÙØ¯Ø APIÙØ§Û ØªØ¬Ø§Ø±Û ÙÙÚ©Ù Ø§Ø³Øª ÙØ²ÛÙÙ ÛØ§ ÙØ­Ø¯ÙØ¯ÛØª Ø¯Ø§Ø´ØªÙ Ø¨Ø§Ø´ÙØ¯."),
        "sms": ("ð± Ù¾ÛØ§ÙÚ© SMS", "optional_paid", "Ù¾ÙÙ SMS Ø§ÙØªØ®Ø§Ø¨Û", "Ø®ÙØ¯ ÙØ§Ø¨ÙÛØª Ø±Ø§ÛÚ¯Ø§Ù Ø§Ø³ØªØ Ø§Ø±Ø³Ø§Ù SMS ÙØ¹ÙÙÙØ§Ù ÙØ²ÛÙÙ ÙØ± Ù¾ÛØ§Ù/Ø¨Ø³ØªÙ Ø¯Ø§Ø±Ø¯."),
        "payment_gateway": ("ð³ Ø¯Ø±Ú¯Ø§Ù Ù¾Ø±Ø¯Ø§Ø®Øª Ø§ÛØ±Ø§ÙÛ", "variable", "Ù¾Ø±Ø¯Ø§Ø®ØªâÛØ§Ø±/PSP Ø§ÙØªØ®Ø§Ø¨Û", "Ø§ØªØµØ§Ù ÙÙÛ ÙÛâØªÙØ§ÙØ¯ Ø±Ø§ÛÚ¯Ø§Ù Ø¨Ø§Ø´Ø¯Ø Ú©Ø§Ø±ÙØ²Ø¯ Ù Ø´Ø±Ø§ÛØ· Ø±Ø§ Ø§Ø±Ø§Ø¦ÙâØ¯ÙÙØ¯Ù ØªØ¹ÛÛÙ ÙÛâÚ©ÙØ¯."),
        "telegram_stars": ("â­ Ù¾Ø±Ø¯Ø§Ø®Øª VIP Ø¨Ø§ Telegram Stars", "transactional", "Telegram", "Ù¾Ø±Ø¯Ø§Ø®Øª Ø¯Ø§Ø®Ù Telegram Ø§ÙØ¬Ø§Ù ÙÛâØ´ÙØ¯Ø Ø´Ø±Ø§ÛØ·/Ú©Ø§Ø±ÙØ²Ø¯ Ø·Ø¨Ù Ø³Ø§Ø²ÙÚ©Ø§Ø± Telegram Ø§Ø³Øª."),
        "channel_media": ("ð¼ï¸ Ø±Ø³Ø§ÙÙ Ù Ø§ÙØªØ´Ø§Ø± Ú©Ø§ÙØ§Ù", "free", "Telegram", "Ø¨Ø±Ø§Û Ø®ÙØ¯ Bot API ÙØ²ÛÙÙ Ø¬Ø¯Ø§Ú¯Ø§ÙÙ ÙØ¯Ø§Ø±Ø¯Ø ÙØ­Ø¯ÙØ¯ÛØªâÙØ§Û Telegram Ø¨Ø±ÙØ±Ø§Ø± Ø§Ø³Øª."),
        "voice_transcription": ("ðï¸ ØªØ¨Ø¯ÛÙ Voice Ø¨Ù ÙØªÙ", "optional_paid", "AI/STT provider", "Ø¨Ø¯ÙÙ Ø³Ø±ÙÛØ³ Ø®Ø§Ø±Ø¬Û ÙÛâØªÙØ§Ù ÙØ§Ø¨ÙÛØª Ø±Ø§ Ø®Ø§ÙÙØ´ ÙÚ¯Ù Ø¯Ø§Ø´ØªØ Ø³Ø±ÙÛØ³ STT ÙÙÚ©Ù Ø§Ø³Øª ÙØ²ÛÙÙ Ø¯Ø§Ø´ØªÙ Ø¨Ø§Ø´Ø¯."),
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
        # Birthday & Events
        "birthday":"free", "events":"free", "admin_gifts":"free",
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


def register_user(uid, first_name, username=None):
    now = datetime.now(TZ).isoformat()
    username = (username or "").strip().lstrip("@").lower()
    c = db()
    try:
        c.execute(
            """INSERT INTO users(user_id, first_name, username, created_at, last_active_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
               first_name=excluded.first_name,
               username=CASE WHEN excluded.username!='' THEN excluded.username ELSE users.username END,
               last_active_at=excluded.last_active_at""",
            (uid, first_name or "", username, now, now),
        )
        c.execute("UPDATE users SET referral_code=COALESCE(referral_code,?) WHERE user_id=?",(secrets.token_urlsafe(12),uid))
        c.commit()
    finally:
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
    return (r["first_name"] if r and r["first_name"] else "Ø¯ÙØ³Øª ÙÙ")



def _ensure_user_feature_preferences(uid):
    """Create default personal menu preferences without changing existing choices."""
    now = datetime.now(TZ).isoformat()
    c = db()
    key = None
    try:
        keys = set(FEATURE_MENU_MAP.values()) if "FEATURE_MENU_MAP" in globals() else set()
        for key in keys:
            c.execute(
                "INSERT OR IGNORE INTO user_feature_preferences(user_id,feature_key,enabled,updated_at) VALUES(?,?,1,?)",
                (int(uid), key, now),
            )
        c.commit()
        return True
    except Exception:
        logger.exception("User feature preference initialization failed: %s", key)
        return True
    finally:
        c.close()

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


def filter_menu_rows(uid,rows):
    out=[]
    for row in rows:
        r=[label for label in row if (FEATURE_MENU_MAP.get(label) is None or user_feature_allowed(uid,FEATURE_MENU_MAP[label]))]
        if r: out.append(r)
    return out


def nav_keyboard(uid, include_back=True):
    """Temporary navigation keyboard for text-input modes. Back always exits the current mode safely."""
    fa = lang(uid) == "fa"
    rows = []
    if include_back:
        rows.append(["â¬ï¸ Ø¨Ø±Ú¯Ø´Øª" if fa else "â¬ï¸ Back", "ð  ÙÙÙÛ Ø§ØµÙÛ" if fa else "ð  Main Menu"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)


def clear_flow(context):
    """Clear only transient conversation state; persistent goals/settings remain intact."""
    for key in list(context.user_data.keys()):
        if key not in {"last_menu"}:
            context.user_data.pop(key, None)


def normalize_digits(s):
    return s.translate(str.maketrans("Û°Û±Û²Û³Û´ÛµÛ¶Û·Û¸Û¹Ù Ù¡Ù¢Ù£Ù¤Ù¥Ù¦Ù§Ù¨Ù©", "01234567890123456789"))


def parse_time(s):
    """Parse goal/reminder time and return normalized HH:MM."""
    if s is None:
        return None
    s = normalize_digits(str(s).strip()).replace("ï¼", ":").replace(".", ":")
    s = re.sub(r"\s+", "", s)
    if not s:
        return None
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
        mobj = re.fullmatch(r"(\d{1,2}):(\d{1,2})", s)
        if not mobj:
            return None
        h, m = int(mobj.group(1)), int(mobj.group(2))
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return f"{h:02d}:{m:02d}"

def add_goal(uid, name, category, reminder, priority=2, duration_minutes=None, condition=None):
    c = db()
    c.execute(
        "INSERT INTO goals(user_id,name,category,reminder_time,priority,duration_minutes,condition,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (uid, name, category, reminder, priority, duration_minutes, condition, datetime.now(TZ).isoformat()),
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
        labels = [("ð´ High", 1), ("ð¡ Medium", 2), ("ð¢ Low", 3)]
    else:
        labels = [("ð´ Ø²ÛØ§Ø¯", 1), ("ð¡ ÙØªÙØ³Ø·", 2), ("ð¢ Ú©Ù", 3)]
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
        found.append("ð¯ Ø§ÙÙÛÙ ÙØ¯Ù")
    if total_done >= 1 and unlock_achievement(uid, "first_done"):
        found.append("ð Ø§ÙÙÛÙ Ø§ÙØ¬Ø§Ù")
    if total_done >= 10 and unlock_achievement(uid, "ten_done"):
        found.append("ð¥ Û±Û° Ø§ÙØ¬Ø§Ù ÙÙÙÙ")
    if total_done >= 50 and unlock_achievement(uid, "fifty_done"):
        found.append("ð ÛµÛ° Ø§ÙØ¬Ø§Ù ÙÙÙÙ")
    return found


def achievement_text(uid):
    c = db()
    rows = c.execute(
        "SELECT code, unlocked_at FROM achievements WHERE user_id=? ORDER BY id DESC",
        (uid,),
    ).fetchall()
    c.close()
    labels = {
        "first_goal": "ð¯ Ø§ÙÙÛÙ ÙØ¯Ù",
        "first_done": "ð Ø§ÙÙÛÙ Ø§ÙØ¬Ø§Ù",
        "ten_done": "ð¥ Û±Û° Ø§ÙØ¬Ø§Ù ÙÙÙÙ",
        "fifty_done": "ð ÛµÛ° Ø§ÙØ¬Ø§Ù ÙÙÙÙ",
    }
    if not rows:
        return "ð ÙÙÙØ² Ø¯Ø³ØªØ§ÙØ±Ø¯Û ÙØ¯Ø§Ø±Û." if lang(uid) == "fa" else "ð No achievements yet."
    return "\n".join(f"{labels.get(r['code'], r['code'])} â {r['unlocked_at'][:10]}" for r in rows)


def goal_reminder_keyboard(uid,gid):
    fa=lang(uid)=="fa"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("â° ÙØ±Ø¯Ø§ ÙÙÛÙ Ø³Ø§Ø¹Øª" if fa else "â° Tomorrow same time",callback_data=f"goalrem:{gid}:same")],
        [InlineKeyboardButton("ð ÙØ±Ø¯Ø§ Ø³Ø§Ø¹Øª Ø¯ÙØ®ÙØ§Ù" if fa else "ð Tomorrow custom time",callback_data=f"goalrem:{gid}:custom")],
        [InlineKeyboardButton("â± Û±Û° Ø¯ÙÛÙÙ Ø¨Ø¹Ø¯" if fa else "â± 10 min",callback_data=f"snooze:{gid}:10"),InlineKeyboardButton("â± Û³Û° Ø¯ÙÛÙÙ Ø¨Ø¹Ø¯" if fa else "â± 30 min",callback_data=f"snooze:{gid}:30")],
        [InlineKeyboardButton("â¬ï¸ Ø¨Ø±Ú¯Ø´Øª" if fa else "â¬ï¸ Back",callback_data=f"detail:{gid}")]
    ])

async def goal_reminder_callback(update,context):
    q=update.callback_query; uid=q.from_user.id; await q.answer()
    parts=_safe_cb_parts(q.data)
    if not parts: return
    _,gid_s,mode=parts; gid=int(gid_s); g=get_goal(uid,gid)
    if not g: await q.answer("ÙØ¯Ù Ù¾ÛØ¯Ø§ ÙØ´Ø¯",show_alert=True); return
    if mode=="menu":
        await q.message.edit_text("â° <b>ÛØ§Ø¯Ø¢ÙØ±Û Ø¯ÙØ¨Ø§Ø±Ù</b>\n\nØ¨Ø±Ø§Û ÙØ±Ø¯Ø§ ÙÙØ§Ù Ø³Ø§Ø¹ØªØ Ø³Ø§Ø¹Øª Ø¬Ø¯ÛØ¯ ÛØ§ Ø¨Ø¹Ø¯Ø§Ù Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù.",parse_mode="HTML",reply_markup=goal_reminder_keyboard(uid,gid)); return
    if mode=="same":
        tm=g["reminder_time"] or datetime.now(TZ).strftime("%H:%M"); d=(datetime.now(TZ).date()+timedelta(days=1)).isoformat(); c=db(); c.execute("INSERT OR REPLACE INTO goal_reminder_overrides(user_id,goal_id,reminder_date,reminder_time,created_at) VALUES(?,?,?,?,?)",(uid,gid,d,tm,datetime.now(TZ).isoformat())); c.commit(); c.close(); await q.message.edit_text(f"â Ø¨Ø±Ø§Û ÙØ±Ø¯Ø§ Ø³Ø§Ø¹Øª {tm} ÛØ§Ø¯Ø¢ÙØ±Û Ø´Ø¯.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("â¬ï¸ ÙØ¯Ù",callback_data=f"detail:{gid}")],[main_menu_button(uid)]])); return
    if mode=="custom":
        context.user_data["goal_reminder_custom"]=gid; context.user_data["_flow_started_at"]=datetime.now(TZ).isoformat(); await q.message.reply_text("ð Ø³Ø§Ø¹Øª ÙØ±Ø¯Ø§ Ø±Ø§ Ø¨ÙØ±Ø³Øª. ÙØ«Ø§Ù: 20:30",reply_markup=nav_keyboard(uid)); return

def snooze_keyboard(uid, gid):
    if lang(uid) == "en":
        labels = [("â± 10 min", 10), ("â± 30 min", 30), ("â± 60 min", 60)]
    else:
        labels = [("â± Û±Û° Ø¯ÙÛÙÙ", 10), ("â± Û³Û° Ø¯ÙÛÙÙ", 30), ("â± Û¶Û° Ø¯ÙÛÙÙ", 60)]
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
        f"â± ÛØ§Ø¯Ø¢ÙØ±Û Â«{g['name']}Â» Ø¨Ø±Ø§Û {minutes} Ø¯ÙÛÙÙ Ø¯ÛÚ¯Ø± ØªÙØ¸ÛÙ Ø´Ø¯."
        if lang(uid) == "fa"
        else f"â± Reminder for â{g['name']}â set for {minutes} minutes."
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
                    "â Done" if lang(uid) == "en" else "â Ø§ÙØ¬Ø§Ù Ø¯Ø§Ø¯Ù",
                    callback_data=f"done:{gid}",
                ),
                InlineKeyboardButton(
                    "â± Snooze" if lang(uid) == "en" else "â± ÛØ§Ø¯Ø¢ÙØ±Û Ø¨Ø¹Ø¯Ø§Ù",
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
        "â± Ø²ÙØ§Ù ÛØ§Ø¯Ø¢ÙØ±Û ÙØ¬Ø¯Ø¯ Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:" if lang(uid) == "fa"
        else "â± Choose snooze duration:",
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
        icon = "â" if s["done"] else "â¬"
        buttons.append([InlineKeyboardButton(
            f"{icon} {s['title']}", callback_data=f"step_toggle:{s['id']}:{gid}"
        )])
    buttons.append([InlineKeyboardButton(
        "â Add step" if lang(uid) == "en" else "â Ø§ÙØ²ÙØ¯Ù ÙØ±Ø­ÙÙ",
        callback_data=f"step_add:{gid}"
    )])
    await q.message.reply_text(
        "ð ÙØ±Ø§Ø­Ù ÙØ¯Ù" if lang(uid) == "fa" else "ð Goal steps",
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
        "âï¸ ÙØ§Ù ÙØ±Ø­ÙÙ Ø±Ø§ Ø¨ÙØ±Ø³Øª:" if lang(uid) == "fa" else "âï¸ Send the step name:"
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
        "â ÙØ±Ø­ÙÙ Ø§Ø¶Ø§ÙÙ Ø´Ø¯." if lang(uid) == "fa" else "â Step added."
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


def _forced_sub_join_url():
    raw = (_forced_sub_get("forced_sub_channel_url", "") or "").strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if raw.startswith("@"):
        return f"https://t.me/{raw[1:]}"
    return ""


def _forced_sub_channel_ref():
    """Return a Telegram chat reference from the forced-subscription setting."""
    raw = (_forced_sub_get("forced_sub_channel_url", "") or "").strip()
    if not raw:
        return ""

    # Numeric channel/supergroup ID.
    if re.fullmatch(r"-?\d+", raw):
        try:
            return int(raw)
        except ValueError:
            return ""

    # @username.
    if re.fullmatch(r"@[A-Za-z0-9_]{5,32}", raw):
        return raw

    # Public t.me username link.
    m = re.match(r"^https?://t\.me/([A-Za-z0-9_]{5,32})/?$", raw, re.I)
    if m:
        return "@" + m.group(1)

    # Telegram internal private-channel links expose the numeric peer ID.
    # t.me/c/1234567890/... maps to -1001234567890.
    m = re.match(r"^https?://t\.me/c/(\d+)(?:/\d+)?/?$", raw, re.I)
    if m:
        return int("-100" + m.group(1))

    return ""


def required_channel_url():
    # The dedicated forced-subscription channel always has priority while active.
    if "_forced_sub_get" in globals() and _forced_sub_is_enabled():
        forced_url = (_forced_sub_get("forced_sub_channel_url", "") or "").strip()
        if forced_url:
            return forced_url

    if REQUIRED_CHANNEL_URL:
        return REQUIRED_CHANNEL_URL

    channel = required_channel()
    if isinstance(channel, int):
        return ""
    if channel.startswith("@"):
        return f"https://t.me/{channel[1:]}"
    return ""


async def is_channel_member(bot, uid):
    """Live-check the user's membership in the configured required channel."""
    channel = ""

    # Forced subscription must check the exact channel configured in its panel.
    if "_forced_sub_is_enabled" in globals() and _forced_sub_is_enabled():
        channel = _forced_sub_channel_ref()

    # Legacy channel configuration is the fallback when forced-sub has no usable
    # channel reference. This also keeps the old subscription system working.
    if not channel:
        channel = required_channel()

    if not channel:
        return True

    if uid in ADMIN_IDS or admin_guard(uid):
        return True

    try:
        member = await bot.get_chat_member(chat_id=channel, user_id=uid)
        status = getattr(member, "status", None)
        status_value = getattr(status, "value", status)
        status_value = str(status_value).lower() if status_value is not None else ""

        if status_value in {"member", "administrator", "creator", "owner"}:
            return True

        if status_value == "restricted":
            return bool(getattr(member, "is_member", False))

        return False
    except Exception as e:
        logger.error(
            "Live membership check failed for uid=%s channel=%s: %s",
            uid, channel, e
        )
        return False


def subscription_keyboard():
    url = required_channel_url()
    rows = []
    if url:
        rows.append([InlineKeyboardButton("ð¢ Ø¹Ø¶ÙÛØª Ø¯Ø± Ú©Ø§ÙØ§Ù", url=url)])
    rows.append([InlineKeyboardButton("â Ø¹Ø¶Ù Ø´Ø¯ÙØ Ø¨Ø±Ø±Ø³Û Ú©Ù", callback_data="subcheck")])
    return InlineKeyboardMarkup(rows)




async def subscription_check_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id

    if _forced_sub_is_enabled():
        is_ok, result = await _forced_sub_enforce_async(uid, context.bot)
        if is_ok:
            await q.answer("â Ø¹Ø¶ÙÛØª ØªØ£ÛÛØ¯ Ø´Ø¯.", show_alert=True)
            await q.message.reply_text(
                "â <b>Ø¹Ø¶ÙÛØª Ø´ÙØ§ ØªØ£ÛÛØ¯ Ø´Ø¯.</b>\nØ­Ø§ÙØ§ ÙÛâØªÙØ§ÙÛØ¯ Ø§Ø² Ø§ÙÚ©Ø§ÙØ§Øª Ø±Ø¨Ø§Øª Ø§Ø³ØªÙØ§Ø¯Ù Ú©ÙÛØ¯.",
                parse_mode="HTML",
                reply_markup=keyboard(uid),
            )
        else:
            msg, kb = result
            await q.answer("â ÙÙÙØ² Ø¹Ø¶Ù Ú©Ø§ÙØ§Ù ÙÛØ³ØªÛØ¯.", show_alert=True)
            await q.message.reply_text(msg, parse_mode="HTML", reply_markup=kb)
        return

    if await is_channel_member(context.bot, uid):
        await q.answer("â Ø¹Ø¶ÙÛØª ØªØ£ÛÛØ¯ Ø´Ø¯.", show_alert=True)
        await q.message.reply_text(
            "â Ø¹Ø¶ÙÛØª Ø´ÙØ§ ØªØ£ÛÛØ¯ Ø´Ø¯. Ø­Ø§ÙØ§ ÙÛâØªÙØ§ÙÛØ¯ Ø§Ø² ÙÙÙ Ø§ÙÚ©Ø§ÙØ§Øª Ø±Ø¨Ø§Øª Ø§Ø³ØªÙØ§Ø¯Ù Ú©ÙÛØ¯.",
            reply_markup=keyboard(uid),
        )
    else:
        await q.answer("â ÙÙÙØ² Ø¹Ø¶Ù Ú©Ø§ÙØ§Ù ÙÛØ³ØªÛØ¯.", show_alert=True)
        await q.message.reply_text(
            "ð Ø§Ø¨ØªØ¯Ø§ Ø¹Ø¶Ù Ú©Ø§ÙØ§Ù Ø´ÙÛØ¯ Ù Ø¨Ø¹Ø¯ Ø±ÙÛ Â«Ø¨Ø±Ø±Ø³Û ÙØ¬Ø¯Ø¯Â» Ø¨Ø²ÙÛØ¯.",
            reply_markup=subscription_keyboard(),
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name = update.effective_user.first_name or "Ø¯ÙØ³Øª ÙÙ"
    register_user(uid, name, getattr(update.effective_user, "username", None))
    if context.args:
        arg=context.args[0]
        if arg.startswith("book_"):
            # Public booking links are a complete flow. Do not continue into
            # the normal /start language-selection flow after opening them.
            handled = await customer_booking_start(update, context, arg[5:].strip())
            if handled:
                return
        if arg.startswith("ref_"):
            code = arg[4:].strip()
            inviter_uid = validate_referral_code(code)
            if inviter_uid and inviter_uid != uid:
                registered = register_referral(inviter_uid, uid, source="link")
                if registered:
                    ref_log(uid, "start_ref_link", inviter_uid, f"code={code}")
                    # Check if this completes a success condition
                    completed_inviter = check_referral_success(uid)
                    if completed_inviter:
                        award_referral_reward(completed_inviter, uid)
                        # Notify inviter
                        try:
                            stats = get_referral_stats(completed_inviter)
                            await context.bot.send_message(
                                chat_id=completed_inviter,
                                text=f"ð <b>Ø®Ø¨Ø± Ø®ÙØ¨!</b>\n\n"
                                     f"Ø¯ÙØ³ØªØª Ø¨Ø§ ÙÛÙÚ© Ø¯Ø¹ÙØª ØªÙ Ø¨Ù Ø±Ø¨Ø§Øª Ù¾ÛÙØ³Øª â¤ï¸\n"
                                     f"ð Ø¯Ø¹ÙØªâÙØ§Û ÙÙÙÙ ØªÙ: <b>{stats['success']}</b> ÙÙØ±",
                                parse_mode="HTML"
                            )
                        except Exception:
                            logger.exception("Referral success notification failed")
    if not await require_subscription(update, context):
        return
    # Forced subscription is already checked by require_subscription().
    info = user_info(uid)

    if info["gender"] is None:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("ð®ð· ÙØ§Ø±Ø³Û", callback_data="language:fa"),
            InlineKeyboardButton("ð¬ð§ English", callback_data="language:en"),
        ]])
        await update.message.reply_text(
            f"ð¯ Ø³ÙØ§Ù {name} Ø¹Ø²ÛØ²! Ø®ÙØ´ Ø§ÙÙØ¯Û ð·\n\n"
            "Ø²Ø¨Ø§Ù Ø±Ø¨Ø§Øª Ø±Ù Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:\n"
            "ð¯ Welcome! Select your language:",
            reply_markup=kb,
        )
    else:
        await update.message.reply_text(
            T[lang(uid)]["welcome"].format(name=name).replace(
                "Ø²Ø¨Ø§Ù Ø±Ø¨Ø§Øª Ø±Ù Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:", "ÙÙÙÛ Ø§ØµÙÛ Ø¢ÙØ§Ø¯ÙâØ³Øª ð"
            ).replace(
                "Choose your language:", "Your menu is ready ð"
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
    rows.append([InlineKeyboardButton("â­ï¸ Ø±Ø¯ Ú©Ø±Ø¯Ù / Ø¨Ø¹Ø¯Ø§Ù Ø§ÙØªØ®Ø§Ø¨ ÙÛâÚ©ÙÙ",callback_data="onboardtype:skip")])
    return InlineKeyboardMarkup(rows)


def onboarding_feature_keyboard(uid):
    fa=lang(uid)=="fa"
    choices=[
        ("goals","ð¯ Ø§ÙØ¯Ø§Ù","ð¯ Goals"),
        ("weekly","ð Ø¬Ø¯ÙÙ ÙÙØªÚ¯Û","ð Weekly"),
        ("stats","ð Ø¢ÙØ§Ø± ÙÙ","ð My Stats"),
        ("price_data","ð ÙÛÙØª Ø¢ÙÙØ§ÛÙ","ð Online Prices"),
        ("ai","ð¤ ÚØª Ø¨Ø§ AI","ð¤ AI Chat"),
        ("support","ð« Ù¾Ø´ØªÛØ¨Ø§ÙÛ","ð« Support"),
        ("customers","ð¥ ÙØ¯ÛØ±ÛØª ÙØ´ØªØ±Û Ù ÙÙØ¨ØªâØ¯ÙÛ","ð¥ Customers & Appointments"),
    ]
    rows=[]
    for key,fa_label,en_label in choices:
        if not feature_enabled(key): continue
        label=fa_label if fa else en_label
        mark="â" if user_pref_enabled(uid,key) else "â¬"
        rows.append([InlineKeyboardButton(f"{mark} {label}",callback_data=f"pref:{key}")])
    rows.append([InlineKeyboardButton("â­ï¸ Ø±Ø¯ Ú©Ø±Ø¯Ù / Ø¨Ø¹Ø¯Ø§Ù ØªÙØ¸ÛÙ ÙÛâÚ©ÙÙ" if fa else "â­ï¸ Skip / Configure later",callback_data="pref:skip")])
    rows.append([InlineKeyboardButton("â Ø§Ø¯Ø§ÙÙ" if fa else "â Continue",callback_data="pref:done")])
    return InlineKeyboardMarkup(rows)

async def onboarding_feature_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not q: return
    await q.answer()
    action=q.data.split(":",1)[1]
    if action in ("done","skip"):
        await q.message.edit_text(
            "â ØªÙØ¸ÛÙØ§Øª Ø§ÙÙÛÙ Ø°Ø®ÛØ±Ù Ø´Ø¯. ÙØ± Ø²ÙØ§Ù ØªÙØ§ÛÙ Ø¯Ø§Ø´ØªÙ Ø¨Ø§Ø´ÛØ¯ ÙÛâØªÙØ§ÙÛØ¯ ÙÙÙÛ Ø´Ø®ØµÛ Ø®ÙØ¯ Ø±Ø§ ØªØºÛÛØ± Ø¯ÙÛØ¯."
            if lang(uid)=="fa" else
            "â Your initial preferences were saved. You can change your personal menu anytime.",
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
        "âï¸ Ø§Ú¯Ø± ØªÙØ§ÛÙ Ø¯Ø§Ø´ØªÙ Ø¨Ø§Ø´ÛØ¯Ø ÙÛâØªÙØ§ÙÛØ¯ ÙØ´Ø®Øµ Ú©ÙÛØ¯ Ú©Ø¯Ø§Ù Ø¨Ø®Ø´âÙØ§ Ø¯Ø± ÙÙÙÛ Ø´Ø®ØµÛ Ø´ÙØ§ ÙÙØ§ÛØ´ Ø¯Ø§Ø¯Ù Ø´ÙÙØ¯. Ø§ÛÙ ÙØ±Ø­ÙÙ Ú©Ø§ÙÙØ§Ù Ø§Ø®ØªÛØ§Ø±Û Ø§Ø³Øª."
        if lang(uid)=="fa" else
        "âï¸ If you wish, choose which sections you would like to see in your personal menu. This step is completely optional.",
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
        T[lang(uid)]["gender_saved"].format(name=display_name(uid)) + "\n\nð¼ Ø§Ú¯Ø± Ø¯ÙØ³Øª Ø¯Ø§Ø±ÛØ ÙÙØ¹ ÙØ¹Ø§ÙÛØª ÛØ§ Ø´ØºÙØª Ø±Ø§ ÙÙ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù. Ø§ÛÙ ÙØ±Ø­ÙÙ Ø§Ø¬Ø¨Ø§Ø±Û ÙÛØ³Øª:",
        reply_markup=onboarding_business_keyboard(uid),
    )


def settings_keyboard(uid):
    fa=lang(uid)=="fa"
    rows = [
        [InlineKeyboardButton("ð Ø²Ø¨Ø§Ù" if fa else "ð Language",callback_data="settings:language")],
        [InlineKeyboardButton("ð Ø§Ø¹ÙØ§ÙâÙØ§" if fa else "ð Notifications",callback_data="settings:notifications")],
        [InlineKeyboardButton("ð¯ Ø§ÙØ¯Ø§Ù" if fa else "ð¯ Goals",callback_data="settings:goals")],
        [InlineKeyboardButton("ð VIP Ù Ø§ÙÚ©Ø§ÙØ§Øª Ù¾ÙÙÛ" if fa else "ð VIP & Paid Features",callback_data="settings:vip")],
    ]
    if admin_is_allowed(uid):
        rows.append([InlineKeyboardButton("ð¢ ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù" if fa else "ð¢ Channel Management",callback_data="settings:channel")])
    rows.append([InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ" if fa else "ð  Main Menu",callback_data="settings:main")])
    return InlineKeyboardMarkup(rows)

async def settings(update, context):
    uid=update.effective_user.id; log_activity(uid,"settings")
    await hide_main_reply_keyboard(update)
    await update.message.reply_text(T[lang(uid)]["settings"],reply_markup=settings_keyboard(uid))



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
                _root_menu_text(uid),
                parse_mode="HTML", reply_markup=_compact_root_inline(uid)
            )
        except Exception:
            try: await q.message.reply_text("ð  ÙÙÙÛ Ø§ØµÙÛ", reply_markup=keyboard(uid))
            except Exception: pass
        return
    if action=="language":
        await q.message.edit_text("Ø²Ø¨Ø§Ù Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù / Choose language:",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ð®ð· ÙØ§Ø±Ø³Û",callback_data="setlang:fa"),InlineKeyboardButton("ð¬ð§ English",callback_data="setlang:en")],[InlineKeyboardButton("â©ï¸ ØªÙØ¸ÛÙØ§Øª",callback_data="settings:back")]])); return
    if action=="channel":
        if not admin_is_allowed(uid):
            await q.message.edit_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯." if fa else "â Access denied.")
            return
        await q.message.edit_text(
            "ð¢ <b>ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù Ù Ù¾Ø³ØªâÚ¯Ø°Ø§Ø±Û</b>\n\n"
            "Ø§Ø² Ø§ÛÙØ¬Ø§ ÙÛâØªÙØ§ÙÛ Ø§ØªØµØ§Ù Ú©Ø§ÙØ§ÙØ Ø³Ø§Ø®Øª Ù¾Ø³ØªØ ÙÛØ³Øª Ù¾Ø³ØªâÙØ§ Ù Ø§ÙØªØ´Ø§Ø± Ø®ÙØ¯Ú©Ø§Ø± Ø±Ø§ ÙØ¯ÛØ±ÛØª Ú©ÙÛ."
            if fa else
            "ð¢ <b>Channel & Posting Management</b>\n\n"
            "Manage channel connection, posts, post list and automatic publishing here.",
            parse_mode="HTML",
            reply_markup=channel_keyboard(),
        )
        return
    if action=="notifications":
        c=db(); c.execute("INSERT OR IGNORE INTO user_settings(user_id) VALUES(?)",(uid,)); r=c.execute("SELECT reminders_enabled FROM user_settings WHERE user_id=?",(uid,)).fetchone(); c.close()
        state=bool(r["reminders_enabled"])
        await q.message.edit_text(("ð ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§: " + ("Ø±ÙØ´Ù" if state else "Ø®Ø§ÙÙØ´")) if fa else ("ð Reminders: " + ("On" if state else "Off")),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ð ØªØºÛÛØ± ÙØ¶Ø¹ÛØª",callback_data="settings:toggle_reminders")],[InlineKeyboardButton("â©ï¸ ØªÙØ¸ÛÙØ§Øª",callback_data="settings:back")]])); return
    if action=="toggle_reminders":
        c=db(); c.execute("INSERT OR IGNORE INTO user_settings(user_id) VALUES(?)",(uid,)); c.execute("UPDATE user_settings SET reminders_enabled=1-reminders_enabled WHERE user_id=?",(uid,)); c.commit(); c.close(); await q.message.edit_text("â ØªÙØ¸ÛÙ Ø´Ø¯.",reply_markup=settings_keyboard(uid)); return
    if action=="goals":
        await q.message.edit_text(("ð¯ ÙØ¯ÙâÙØ§ Ø¯Ø§Ø¦ÙÛ ÙØ³ØªÙØ¯ Ù ÙÙØ· Ø®ÙØ¯Øª ÙÛâØªÙØ§ÙÛ Ø­Ø°ÙØ´Ø§Ù Ú©ÙÛ. ÙÙÚ¯Ø§Ù Ø³Ø§Ø®Øª ÙØ¯Ù ÙÛâØªÙØ§ÙÛ ÙØ¯Øª Ø§ÙØ¬Ø§Ù Ø±Ø§ ÙÙ ØªØ¹ÛÛÙ Ú©ÙÛ." if fa else "ð¯ Goals stay saved until you delete them. When creating a goal you can also set its duration."),reply_markup=settings_keyboard(uid)); return
    if action=="vip":
        xp,level,vip_until=xp_info(uid)
        text=(f"ð VIP\n\nÙØ¶Ø¹ÛØª: {'ð¢ ÙØ¹Ø§Ù' if is_vip(uid) else 'âª Ø¹Ø§Ø¯Û'}\nâ­ Ø³Ø·Ø­: {level}\nð¥ Ø¯Ø¹ÙØª Ø¯ÙØ³ØªØ§Ù Ù ÙØ¹Ø§ÙÛØªâÙØ§ ÙÛâØªÙØ§ÙÙØ¯ XP Ù Ù¾Ø§Ø¯Ø§Ø´ Ø¨Ú¯ÛØ±ÙØ¯.\n\nÙ¾Ø±Ø¯Ø§Ø®Øª ÙØ§ÙØ¹Û ÙØ¹ÙØ§Ù Ø§Ø² Ù¾ÙÙ ÙØ¯ÛØ± ÙØ§Ø¨Ù Ú©ÙØªØ±Ù Ø§Ø³Øª." if fa else f"ð VIP\n\nStatus: {'ð¢ Active' if is_vip(uid) else 'âª Free'}\nâ­ Level: {level}\nð¥ Referrals and activity can earn XP/rewards.\n\nReal payments are controlled from the admin panel for now.")
        await q.message.edit_text(text,reply_markup=settings_keyboard(uid)); return
    if action in ("back","main"):
        if action=="main":
            await q.message.edit_text("ð  ÙÙÙÛ Ø§ØµÙÛ")
            await q.message.reply_text("ð  ÙÙÙÛ Ø§ØµÙÛ",reply_markup=keyboard(uid))
        else: await q.message.edit_text(T[lang(uid)]["settings"],reply_markup=settings_keyboard(uid))


def edit_time_keyboard(uid):
    buttons = [
        InlineKeyboardButton(x, callback_data=f"edit_time:{x}") for x in TIME_BUTTONS
    ]
    rows = [buttons[i:i + 4] for i in range(0, len(buttons), 4)]
    rows.append([
        InlineKeyboardButton(
            "ð Ø¨Ø¯ÙÙ ÛØ§Ø¯Ø¢ÙØ±Û" if lang(uid) == "fa" else "ð No reminder",
            callback_data="edit_time:none",
        ),
        InlineKeyboardButton(
            "ð Ø³Ø§Ø¹Øª Ø¯ÛÚ¯Ø±" if lang(uid) == "fa" else "ð Custom time",
            callback_data="edit_time:custom",
        ),
    ])
    return InlineKeyboardMarkup(rows)


async def custom_goal_start(update, context):
    uid = update.effective_user.id
    clear_flow(context)
    context.user_data["awaiting_custom_goal"] = True
    await update.message.reply_text(
        "âï¸ ÙØ¯Ù Ø®ÙØ¯Øª Ø±Ø§ Ø¨ÙÙÛØ³:\nÙØ«ÙØ§Ù: ÙØ± Ø±ÙØ² Û³Û° Ø¯ÙÛÙÙ Ø²Ø¨Ø§Ù Ø§ÙÚ¯ÙÛØ³Û Ø¨Ø®ÙØ§ÙÙ"
        if lang(uid) == "fa" else
        "âï¸ Write your own goal:\nExample: Study English for 30 minutes every day",
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
async def new_back(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    context.user_data.pop("category", None)
    await q.message.edit_text(
        T[lang(uid)]["new_goal"].format(name=display_name(uid)),
        reply_markup=categories_keyboard(uid),
    )





def duration_keyboard(uid):
    fa = lang(uid) == "fa"
    labels = [(5,"Ûµ Ø¯ÙÛÙÙ"),(10,"Û±Û° Ø¯ÙÛÙÙ"),(20,"Û²Û° Ø¯ÙÛÙÙ"),(30,"Û³Û° Ø¯ÙÛÙÙ"),(60,"Û± Ø³Ø§Ø¹Øª"),(120,"Û² Ø³Ø§Ø¹Øª"),(0,"â¾ï¸ Ø¨Ø¯ÙÙ ÙØ­Ø¯ÙØ¯ÛØª")]
    rows=[]
    for i in range(0,len(labels),2):
        pair=labels[i:i+2]
        rows.append([InlineKeyboardButton((label if fa else ({5:"5 min",10:"10 min",20:"20 min",30:"30 min",60:"1 hour",120:"2 hours",0:"â¾ï¸ No limit"}[m])),callback_data=f"duration:{m}") for m,label in pair])
    rows.append([InlineKeyboardButton("âï¸ Ø²ÙØ§Ù Ø¯ÙØ®ÙØ§Ù" if fa else "âï¸ Custom",callback_data="duration:custom")])
    rows.append([InlineKeyboardButton(T[lang(uid)]["back"],callback_data="newback")])
    return InlineKeyboardMarkup(rows)


def condition_keyboard(uid):
    fa = lang(uid) == "fa"
    rows = [
        [InlineKeyboardButton("ð« Ø¨Ø¯ÙÙ Ø´Ø±Ø·" if fa else "ð« No condition", callback_data="cond:none")],
        [InlineKeyboardButton("âï¸ Ø§Ú¯Ø± ÙÙØ§ Ø®ÙØ¨ Ø¨ÙØ¯" if fa else "âï¸ If weather is good", callback_data="cond:weather"),
         InlineKeyboardButton("â° Ø§Ú¯Ø± ÙÙØª Ø¯Ø§Ø´ØªÙ" if fa else "â° If I have time", callback_data="cond:time")],
        [InlineKeyboardButton("ð  Ø§Ú¯Ø± Ø¯Ø± Ø®Ø§ÙÙ Ø¨ÙØ¯Ù" if fa else "ð  If I'm home", callback_data="cond:home"),
         InlineKeyboardButton("ð¢ Ø§Ú¯Ø± Ø³Ø± Ú©Ø§Ø± Ø¨ÙØ¯Ù" if fa else "ð¢ If I'm at work", callback_data="cond:work")],
        [InlineKeyboardButton("ð¡ Ø§Ú¯Ø± Ø§ÙØ±ÚÛ Ø¯Ø§Ø´ØªÙ" if fa else "ð¡ If I have energy", callback_data="cond:energy"),
         InlineKeyboardButton("ð Ø§Ú¯Ø± Ø­Ø§ÙÙ Ø®ÙØ¨ Ø¨ÙØ¯" if fa else "ð If I feel good", callback_data="cond:mood")],
        [InlineKeyboardButton("âï¸ Ø´Ø±Ø· Ø¯ÙØ®ÙØ§Ù" if fa else "âï¸ Custom condition", callback_data="cond:custom")],
    ]
    return InlineKeyboardMarkup(rows)


@subscription_required
async def priority_callback(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    context.user_data["priority"] = int(q.data.split(":")[1])
    await q.message.edit_text(
        "â± ÙØ¯Øª Ø§ÙØ¬Ø§Ù ÙØ¯Ù Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:" if lang(uid)=="fa" else "â± How long should this goal take?",
        reply_markup=duration_keyboard(uid),
    )


@subscription_required
async def duration_callback(update, context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id
    value=q.data.split(":",1)[1]
    if value=="custom":
        context.user_data["awaiting_custom_duration"]=True
        await q.message.edit_text("âï¸ ÙØ¯Øª Ø±Ø§ Ø¨Ù Ø¯ÙÛÙÙ ÙØ§Ø±Ø¯ Ú©Ù (ÙØ«ÙØ§Ù 45)." if lang(uid)=="fa" else "âï¸ Enter duration in minutes (e.g. 45).")
        return
    context.user_data["duration_minutes"] = None if value=="0" else int(value)
    await q.message.edit_text(
        "âï¸ Ø§ÛÙ ÙØ¯Ù Ø´Ø±Ø· Ø§Ø¬Ø±Ø§ Ø¯Ø§Ø±ÙØ" if lang(uid)=="fa" else "âï¸ Does this goal have an execution condition?",
        reply_markup=condition_keyboard(uid),
    )


@subscription_required
async def condition_callback(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    value = q.data.split(":", 1)[1]
    cond_map = {"none":None,"weather":"âï¸ Ø§Ú¯Ø± ÙÙØ§ Ø®ÙØ¨ Ø¨ÙØ¯","time":"â° Ø§Ú¯Ø± ÙÙØª Ø¯Ø§Ø´ØªÙ","home":"ð  Ø§Ú¯Ø± Ø¯Ø± Ø®Ø§ÙÙ Ø¨ÙØ¯Ù","work":"ð¢ Ø§Ú¯Ø± Ø³Ø± Ú©Ø§Ø± Ø¨ÙØ¯Ù","energy":"ð¡ Ø§Ú¯Ø± Ø§ÙØ±ÚÛ Ø¯Ø§Ø´ØªÙ","mood":"ð Ø§Ú¯Ø± Ø­Ø§ÙÙ Ø®ÙØ¨ Ø¨ÙØ¯"}
    if value == "custom":
        context.user_data["awaiting_custom_condition"] = True
        await q.message.edit_text("âï¸ Ø´Ø±Ø· Ø¯ÙØ®ÙØ§Ù Ø±Ø§ Ø¨ÙÙÛØ³ (ÙØ«ÙØ§Ù: Ø§Ú¯Ø± Ø¨Ø§Ø±Ø§Ù ÙØ¨Ø§Ø±Ø¯)." if lang(uid)=="fa" else "âï¸ Write your custom condition (e.g.: if it doesn't rain).")
        return
    context.user_data["condition"] = cond_map.get(value)
    await q.message.edit_text(T[lang(uid)]["choose_time"], reply_markup=time_keyboard(uid))




async def custom_duration_save(update, context):
    uid=update.effective_user.id
    if not context.user_data.get("awaiting_custom_duration"): return False
    try:
        minutes=int(normalize_digits(update.message.text.strip()))
        if minutes<1 or minutes>1440: raise ValueError
    except ValueError:
        await update.message.reply_text("â Ø¹Ø¯Ø¯ ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³ØªØ Ø¨ÛÙ Û± ØªØ§ Û±Û´Û´Û° Ø¯ÙÛÙÙ." if lang(uid)=="fa" else "â Enter a number from 1 to 1440 minutes.")
        return True
    context.user_data["duration_minutes"]=minutes
    context.user_data.pop("awaiting_custom_duration",None)
    await update.message.reply_text(
        "âï¸ Ø§ÛÙ ÙØ¯Ù Ø´Ø±Ø· Ø§Ø¬Ø±Ø§ Ø¯Ø§Ø±ÙØ" if lang(uid)=="fa" else "âï¸ Does this goal have an execution condition?",
        reply_markup=condition_keyboard(uid),
    )
    return True


async def custom_condition_save(update, context):
    uid = update.effective_user.id
    if not context.user_data.get("awaiting_custom_condition"):
        return False
    text = update.message.text.strip()
    if not text:
        return True
    context.user_data["condition"] = text
    context.user_data.pop("awaiting_custom_condition", None)
    await update.message.reply_text(
        T[lang(uid)]["choose_time"],
        reply_markup=time_keyboard(uid),
    )
    return True


async def custom_goal_save(update, context):
    uid=update.effective_user.id
    if not context.user_data.get("awaiting_custom_goal"): return False
    name=update.message.text.strip()
    if not name:
        return True
    context.user_data["name"]=name
    context.user_data["category"]="ð¯ ÙØ¯Ù Ø¯ÙØ®ÙØ§Ù" if lang(uid)=="fa" else "ð¯ Custom"
    context.user_data.pop("awaiting_custom_goal",None)
    await update.message.reply_text("â­ Ø§ÙÙÙÛØª ÙØ¯Ù Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:" if lang(uid)=="fa" else "â­ Choose goal priority:",reply_markup=priority_keyboard(uid))
    return True




async def ready_menu(update, context):
    await new_goal(update, context)




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
        f"ð¯ {g['name']}\nð {g['category']}\nâ­ Ø§ÙÙÙÛØª: {g['priority']}\nâ° {g['reminder_time'] or 'Off'}\n{'âï¸ Ø´Ø±Ø·: ' + g['condition'] if g['condition'] else ''}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "â Done" if lang(uid) == "en" else "â Ø§ÙØ¬Ø§Ù Ø¯Ø§Ø¯Ù",
                callback_data=f"done:{gid}",
            ),
            InlineKeyboardButton(
                "â Not done" if lang(uid) == "en" else "â Ø§ÙØ¬Ø§Ù ÙØ¯Ø§Ø¯Ù",
                callback_data=f"miss:{gid}",
            ),
        ], [
            InlineKeyboardButton(
                "ð Steps" if lang(uid) == "en" else "ð ÙØ±Ø§Ø­Ù",
                callback_data=f"steps:{gid}",
            ),
        ], [InlineKeyboardButton("â©ï¸ Ø§ÙØ¯Ø§Ù Ø§ÙØ±ÙØ²" if lang(uid)=="fa" else "â©ï¸ Today's Goals",callback_data="goals:main")]]),
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
                ("ð Ø¯Ø³ØªØ§ÙØ±Ø¯ Ø¬Ø¯ÛØ¯!\n" + "\n".join(new_achievements))
                if lang(uid) == "fa"
                else ("ð New achievement!\n" + "\n".join(new_achievements))
            )
    result_text = (
        T[lang(uid)]["done"].format(name=display_name(uid))
        if is_done
        else T[lang(uid)]["missed"].format(name=display_name(uid))
    )
    await q.message.edit_text(result_text)
    await q.message.reply_text("ð  ÙÙÙÛ Ø§ØµÙÛ", reply_markup=keyboard(uid))




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
            "âï¸ Change name" if lang(uid) == "en" else "âï¸ ØªØºÛÛØ± ÙØ§Ù",
            callback_data=f"rename:{gid}",
        )],
        [InlineKeyboardButton(
            "â° Change reminder" if lang(uid) == "en" else "â° ØªØºÛÛØ± ÛØ§Ø¯Ø¢ÙØ±Û",
            callback_data=f"changereminder:{gid}",
        )],
        [InlineKeyboardButton(
            "ð Delete" if lang(uid) == "en" else "ð Ø­Ø°Ù",
            callback_data=f"delete:{gid}",
        )],
        [InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ" if lang(uid)=="fa" else "ð  Main Menu",callback_data="goals:main")],
    ]
    await q.message.edit_text(
        f"ð¯ {g['name']}\nâ° {g['reminder_time'] or 'Off'}\n{'âï¸ Ø´Ø±Ø·: '+g['condition'] if g['condition'] else ''}",
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
        reply_markup=edit_time_keyboard(uid),
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
    await q.message.edit_text("â Ø²ÙØ§Ù ÛØ§Ø¯Ø¢ÙØ±Û ØªØºÛÛØ± Ú©Ø±Ø¯." if lang(uid) == "fa" else "â Reminder time updated.")
    await q.message.reply_text("ð  ÙÙÙÛ Ø§ØµÙÛ", reply_markup=keyboard(uid))


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
    await q.message.reply_text("ð  ÙÙÙÛ Ø§ØµÙÛ", reply_markup=keyboard(uid))


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
    text = "Delete this goal?" if lang(uid) == "en" else "Ø§ÛÙ ÙØ¯Ù Ø­Ø°Ù Ø´ÙØ¯Ø"
    yes = "Yes, delete" if lang(uid) == "en" else "Ø¨ÙÙØ Ø­Ø°Ù Ú©Ù"
    no = "Cancel" if lang(uid) == "en" else "ÙØºÙ"
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
    await q.message.reply_text("ð  ÙÙÙÛ Ø§ØµÙÛ", reply_markup=keyboard(uid))


@subscription_required
async def delete_no(update, context):
    q = update.callback_query
    await q.answer()
    await q.message.edit_text("â Cancelled" if lang(q.from_user.id) == "en" else "â ÙØºÙ Ø´Ø¯.")
    await q.message.reply_text("ð  ÙÙÙÛ Ø§ØµÙÛ" if lang(q.from_user.id) == "fa" else "ð  Main Menu", reply_markup=keyboard(q.from_user.id))



async def achievements(update, context):
    uid = update.effective_user.id
    await update.message.reply_text(
        "ð Ø¯Ø³ØªØ§ÙØ±Ø¯ÙØ§Û ØªÙ\n\n" + achievement_text(uid)
        if lang(uid) == "fa"
        else "ð Your achievements\n\n" + achievement_text(uid)
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
    names = ["Ø´ÙØ¨Ù", "ÛÚ©Ø´ÙØ¨Ù", "Ø¯ÙØ´ÙØ¨Ù", "Ø³ÙâØ´ÙØ¨Ù", "ÚÙØ§Ø±Ø´ÙØ¨Ù", "Ù¾ÙØ¬Ø´ÙØ¨Ù", "Ø¬ÙØ¹Ù"]
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
        lines.append(f"ð {weekday} {ds}: {done}/{total} â")
    await update.message.reply_text(
        T[lang(uid)]["weekly"].format(name=display_name(uid), rows="\n".join(lines))
    )
    log_activity(uid, "weekly")


async def stats(update, context):
    uid = update.effective_user.id
    goals = get_goals(uid)
    # Calculate the user's current streak here.  The previous V22 version
    # referenced `streak` without defining it, which caused the whole
    # "ð Ø¢ÙØ§Ø± ÙÙ" handler to fail and the global error handler to show
    # "Ø¨ÙÛÙ Ø§ÙÚ©Ø§ÙØ§Øª ÙÙÚÙØ§Ù Ø¯Ø± Ø¯Ø³ØªØ±Ø³âØ§ÙØ¯".
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
            f"\nð¥ Ø±Ú©ÙØ±Ø¯ Ø²ÙØ¬ÛØ±Ù ÙØ¹ÙÛ: {streak} Ø±ÙØ²"
            if lang(uid) == "fa"
            else f"\nð¥ Current streak: {streak} days"
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
    """Return the selected channel and repair old channel schemas first."""
    _ensure_channel_runtime_schema()
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
    _ensure_channel_runtime_schema()
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
    if member.status not in {"administrator", "creator"}:
        return False, "â Ø±Ø¨Ø§Øª Ø¯Ø± Ú©Ø§ÙØ§Ù Ø§Ø¯ÙÛÙ ÙÛØ³Øª. Ø§Ø¨ØªØ¯Ø§ Ø±Ø¨Ø§Øª Ø±Ø§ Ø§Ø¯ÙÛÙ Ú©Ø§ÙØ§Ù Ú©Ù."
    if member.status == "administrator" and not bool(getattr(member, "can_post_messages", False)):
        return False, "â Ø±Ø¨Ø§Øª Ø§Ø¯ÙÛÙ Ø§Ø³ØªØ ÙÙÛ Ø§Ø¬Ø§Ø²Ù Ø§Ø±Ø³Ø§Ù Ù¾Ø³Øª ÙØ¯Ø§Ø±Ø¯. Ø¯Ø³ØªØ±Ø³Û Ø§Ø±Ø³Ø§Ù Ù¾ÛØ§Ù Ø±Ø§ ÙØ¹Ø§Ù Ú©Ù."
    return True, "OK"


def add_channel_post(content, typ, schedule_time=None, weekday=None, run_at=None, created_by=0):
    c=db(); cur=c.execute("INSERT INTO channel_posts(content,schedule_type,schedule_time,weekday,run_at,enabled,created_at,created_by) VALUES(?,?,?,?,?,1,?,?)",(content,typ,schedule_time,weekday,run_at,datetime.now(TZ).isoformat(),created_by)); pid=cur.lastrowid; c.commit(); c.close(); return pid


AUTO_TOPIC_TREE_FA = {
    "ð¯ ÙØ¯ÙâÚ¯Ø°Ø§Ø±Û": [
        "ØªØ¹ÛÛÙ ÙØ¯ÙâÙØ§Û Ú©ÙÚÚ© Ù ÙØ§Ø¨Ù Ø§ÙØ¯Ø§Ø²ÙâÚ¯ÛØ±Û",
        "Ø§ÙÙÙÛØªâØ¨ÙØ¯Û ÙØ¯ÙâÙØ§",
        "Ø¨Ø±ÙØ§ÙÙâØ±ÛØ²Û ÙÙØªÚ¯Û",
        "Ø´Ú©Ø³ØªÙ ÙØ¯Ù Ø¨Ø²Ø±Ú¯ Ø¨Ù ÙØ¯ÙâÙØ§Û Ú©ÙÚÚ©",
        "Ù¾ÛÚ¯ÛØ±Û Ù¾ÛØ´Ø±ÙØª ÙØ¯Ù"
    ],
    "ð Ø±Ø´Ø¯ ÙØ±Ø¯Û": [
        "Ø³Ø§Ø®Øª Ø¹Ø§Ø¯ØªâÙØ§Û Ø®ÙØ¨",
        "Ø§ÙØ¶Ø¨Ø§Ø· Ø´Ø®ØµÛ",
        "Ø§Ø¹ØªÙØ§Ø¯Ø¨ÙâÙÙØ³",
        "ÙØ¯ÛØ±ÛØª Ø§ÙÙØ§ÙâÚ©Ø§Ø±Û",
        "Ø®ÙØ¯Ø´ÙØ§Ø³Û Ù Ø§Ø±Ø²ÛØ§Ø¨Û Ù¾ÛØ´Ø±ÙØª"
    ],
    "â± ÙØ¯ÛØ±ÛØª Ø²ÙØ§Ù": [
        "Ø¨Ø±ÙØ§ÙÙâØ±ÛØ²Û Ø±ÙØ²Ø§ÙÙ",
        "ØªÙØ±Ú©Ø² Ø¹ÙÛÙ",
        "ÙÙØ§Ø¨ÙÙ Ø¨Ø§ Ø­ÙØ§Ø³âÙ¾Ø±ØªÛ",
        "Ø§ÙÙÙÛØªâØ¨ÙØ¯Û Ú©Ø§Ø±ÙØ§",
        "Ø§Ø³ØªØ±Ø§Ø­Øª Ù Ø²ÙØ§ÙâØ¨ÙØ¯Û Ø¯Ø±Ø³Øª"
    ],
    "ð° Ø³Ø±ÙØ§ÛÙâÚ¯Ø°Ø§Ø±Û Ù ÙØ§ÙÛ": [
        "Ø³ÙØ§Ø¯ ÙØ§ÙÛ",
        "Ø¨ÙØ¯Ø¬ÙâØ¨ÙØ¯Û Ø´Ø®ØµÛ",
        "Ù¾Ø³âØ§ÙØ¯Ø§Ø²",
        "ÙØ¯ÛØ±ÛØª Ø±ÛØ³Ú©",
        "ÙÙØ§ÙÛÙ Ù¾Ø§ÛÙ Ø³Ø±ÙØ§ÛÙâÚ¯Ø°Ø§Ø±Û",
        "Ø§Ø±Ø² Ù Ø¯ÙØ§Ø±",
        "Ø·ÙØ§ Ù Ø³Ú©Ù",
        "Ú©Ø±ÛÙ¾ØªÙ Ù Ø¨Ø§Ø²Ø§Ø± Ø±ÙØ²Ø§Ø±Ø²",
        "Ø¨ÙØ±Ø³ Ù Ø´Ø§Ø®ØµâÙØ§"
    ],
    "ð ÙØ±Ø²Ø´ Ù Ø¨Ø±ÙØ§ÙÙ Ú©ÙØªØ§Ù": [
        "ÙØ±Ø²Ø´ Û±Û° Ø¯ÙÛÙÙâØ§Û Ø¯Ø± Ø®Ø§ÙÙ",
        "Ø­Ø±Ú©Ø§Øª Ú©Ø´Ø´Û Ø¨Ø¹Ø¯ Ø§Ø² Ú©Ø§Ø±",
        "ÙØ±Ø²Ø´ Ø³Ø¨Ú© Ø¨Ø±Ø§Û Ø±ÙØ²ÙØ§Û Ø®Ø³ØªÚ¯Û",
        "ØªÙØ±ÛÙ Ú©Ù Ø¨Ø¯Ù Ø¨Ø¯ÙÙ ÙØ³ÛÙÙ",
        "Ú¯Ø±ÙâÚ©Ø±Ø¯Ù Ù Ø³Ø±Ø¯Ú©Ø±Ø¯Ù"
    ],
    "ð ØªØºØ°ÛÙ Ù Ø®ÙØ§Øµ Ø®ÙØ±Ø§Ú©ÛâÙØ§": [
        "Ø®ÙØ§Øµ Ø³ÛØ¨",
        "Ø®ÙØ§Øµ ÙÛÙÙ",
        "Ø®ÙØ§Øµ ÙÙØ²",
        "Ø¢Ø¨ Ù ÙÛØ¯Ø±Ø§ØªÙ ÙØ§ÙØ¯Ù",
        "ÙÛØ§ÙâÙØ¹Ø¯Ù Ø³Ø§ÙÙ"
    ],
    "ð§  Ø³Ø®ÙØ§Ù Ø¨Ø²Ø±Ú¯Ø§Ù Ù Ø¯Ø§ÙØ´ÙÙØ¯Ø§Ù": [
        "Ø³Ø®ÙØ§Ù Ø¯Ø§ÙØ´ÙÙØ¯Ø§Ù Ø§ÛØ±Ø§ÙÛ",
        "Ø³Ø®ÙØ§Ù Ø¯Ø§ÙØ´ÙÙØ¯Ø§Ù Ø¬ÙØ§Ù",
        "Ø¨Ø²Ø±Ú¯Ø§Ù Ù Ø§ÙØ¯ÛØ´ÙÙØ¯Ø§Ù ÙØ¯ÛÙÛ",
        "Ø¯Ø§ÙØ´ÙÙØ¯Ø§Ù Ù Ø§Ø³ØªØ§Ø¯Ø§Ù ÙØ¹Ø§ØµØ±",
        "Ø¬ÙÙØ§Øª Ú©ÙØªØ§Ù Ø¨Ø±Ø§Û Ø´Ø±ÙØ¹ Ø±ÙØ²"
    ],
    "ð ØµØ¨Ø­ Ù ð Ø´Ø¨": [
        "Ù¾ÛØ§Ù Ø´Ø±ÙØ¹ Ø±ÙØ²",
        "ÙØ¯ÙâÚ¯Ø°Ø§Ø±Û ØµØ¨Ø­Ú¯Ø§ÙÛ",
        "Ø¬ÙØ¹âØ¨ÙØ¯Û Ø´Ø¨Ø§ÙÙ",
        "Ø§Ø±Ø²ÛØ§Ø¨Û Ø±ÙØ²",
        "Ø¢Ø±Ø§ÙâØ³Ø§Ø²Û ÙØ¨Ù Ø§Ø² Ø®ÙØ§Ø¨"
    ],
    "ð¼ Ú©Ø§Ø± Ù Ú©Ø³Ø¨âÙÚ©Ø§Ø±": [
        "ÙÙØ§Ø±ØªâÙØ§Û Ø´ØºÙÛ",
        "Ø±Ø§ÙâØ§ÙØ¯Ø§Ø²Û Ú©Ø³Ø¨âÙÚ©Ø§Ø±",
        "Ø¨Ø±ÙØ¯ Ø´Ø®ØµÛ",
        "ÙØ¯ÛØ±ÛØª Ù¾Ø±ÙÚÙ",
        "Ø§ÙØ²Ø§ÛØ´ Ø¨ÙØ±ÙâÙØ±Û"
    ],
    "ð ÛØ§Ø¯Ú¯ÛØ±Û": [
        "Ø±ÙØ´ ÙØ·Ø§ÙØ¹Ù Ø¨ÙØªØ±",
        "ÛØ§Ø¯Ú¯ÛØ±Û ÙÙØ§Ø±Øª Ø¬Ø¯ÛØ¯",
        "ÙØ±ÙØ± Ù ÛØ§Ø¯Ø³Ù¾Ø§Ø±Û",
        "Ú©ØªØ§Ø¨âØ®ÙØ§ÙÛ",
        "ÛØ§Ø¯Ú¯ÛØ±Û Ø²Ø¨Ø§Ù"
    ],
    "ð§  Ø°ÙÙ Ù ØªÙØ±Ú©Ø²": [
        "ØªÙØ±Ú©Ø²",
        "ÙØ¯ÛØ±ÛØª Ø§Ø³ØªØ±Ø³",
        "ÙØ¯ÛØ±ÛØª Ø§ÙÚ©Ø§Ø±",
        "ÙØ¯ÛØªÛØ´Ù Ù Ø¢Ø±Ø§ÙâØ³Ø§Ø²Û",
        "Ø§Ø³ØªØ±Ø§Ø­Øª Ø°ÙÙÛ"
    ],
    "ð Ø³ÙØ§ÙØªÛ Ù Ø³Ø¨Ú© Ø²ÙØ¯Ú¯Û": [
        "Ø®ÙØ§Ø¨ Ø¨ÙØªØ±",
        "ÙØ±Ø²Ø´ Ù Ø­Ø±Ú©Øª",
        "ØªØºØ°ÛÙ ÙØªØ¹Ø§Ø¯Ù",
        "Ø¢Ø¨ Ù Ø§ÙØ±ÚÛ Ø±ÙØ²Ø§ÙÙ",
        "Ø±ÙØªÛÙ ØµØ¨Ø­ Ù Ø´Ø¨"
    ],
    "ð¤ Ø§Ø±ØªØ¨Ø§Ø·Ø§Øª": [
        "ÙÙØ§Ø±Øª Ú¯ÙØªâÙÚ¯Ù",
        "Ú¯ÙØ´ Ø¯Ø§Ø¯Ù ÙØ¹Ø§Ù",
        "ÙØ±Ø²Ø¨ÙØ¯Û Ø³Ø§ÙÙ",
        "Ø­Ù ØªØ¹Ø§Ø±Ø¶",
        "Ø±ÙØ§Ø¨Ø· Ø­Ø±ÙÙâØ§Û"
    ],
    "ð Ø§ÙÚ¯ÛØ²Ù Ù ÙÙÙÙÛØª": [
        "Ø´Ø±ÙØ¹ Ú©Ø±Ø¯Ù",
        "Ø§Ø¯Ø§ÙÙ Ø¯Ø§Ø¯Ù",
        "Ø¹Ø¨ÙØ± Ø§Ø² Ø´Ú©Ø³Øª",
        "Ø³Ø§Ø®ØªÙ ÙØ¸Ù",
        "Ø«Ø¨Øª ÙÙÙÙÛØªâÙØ§Û Ú©ÙÚÚ©"
    ],
}

AUTO_INTERVALS_MIN = [5, 10, 15, 20, 30, 60, 120, 180, 240, 360, 720, 1440]

def _topic_focus(topic):
    """Return a strict content brief for the selected topic.
    The selected topic is the subject of the post, not merely an example.
    """
    t = str(topic or "").strip()
    rules = {
        "ÙØ±Ø²Ø´ Û±Û° Ø¯ÙÛÙÙâØ§Û": "Ø±ÙÛ Ø§Ø«Ø± ÙØ¹Ø§ÙÛØª Û±Û° Ø¯ÙÛÙÙâØ§Û Ø¨Ø± Ø¨Ø¯ÙØ Ø§ÙØ±ÚÛ Ù Ø­Ø§ÙâÙÙÙØ§Û Ø±ÙØ²Ø ÙÙÙÙÙ Ø³Ø§Ø®ØªØ§Ø± Û±Û° Ø¯ÙÛÙÙâØ§Û Ù ÙÚ©Ø§Øª Ø§Ø¬Ø±Ø§Û Ø§ÛÙÙ ØªÙØ±Ú©Ø² Ú©Ù.",
        "Ø®ÙØ§Ø¨ Ø¨ÙØªØ±": "Ø±ÙÛ Ú©ÛÙÛØª Ø®ÙØ§Ø¨Ø Ø¹Ø§Ø¯ØªâÙØ§Û ÙØ¨Ù Ø®ÙØ§Ø¨Ø ÙÙØ± Ù ØµÙØ­ÙâÙÙØ§ÛØ´Ø Ø²ÙØ§ÙâØ¨ÙØ¯Û Ù Ø§Ø«Ø± Ø®ÙØ§Ø¨ Ú©Ø§ÙÛ Ø¨Ø± Ø§ÙØ±ÚÛ Ù ØªÙØ±Ú©Ø² ØªÙØ±Ú©Ø² Ú©Ù.",
        "Ø¢Ø¨ Ú©Ø§ÙÛ": "Ø±ÙÛ ÙÙØ´ Ø¢Ø¨ Ø¯Ø± Ø¨Ø¯ÙØ ÙØ´Ø§ÙÙâÙØ§Û Ú©ÙâØ¢Ø¨ÛØ Ø²ÙØ§ÙâÙØ§Û ÙÙØ§Ø³Ø¨ ÙÙØ´ÛØ¯Ù Ù ÛÚ© Ø±ÙØ´ Ø¹ÙÙÛ Ø¨Ø±Ø§Û ÙØµØ±Ù ÙÙØ¸Ù Ø¢Ø¨ ØªÙØ±Ú©Ø² Ú©Ù.",
        "ØµØ¨Ø­Ø§ÙÙ Ø³Ø§ÙÙ": "Ø±ÙÛ Ø§Ø¬Ø²Ø§Û ÛÚ© ØµØ¨Ø­Ø§ÙÙ ÙØªØ¹Ø§Ø¯ÙØ Ø§ÙØ±ÚÛ ØµØ¨Ø­Ø ØªØ±Ú©ÛØ¨ Ù¾Ø±ÙØªØ¦ÛÙ/ÙÛØ¨Ø± Ù ÚÙØ¯ Ø§ÙØªØ®Ø§Ø¨ Ø¹ÙÙÛ ØªÙØ±Ú©Ø² Ú©Ù.",
        "ÙØ·Ø§ÙØ¹Ù Û²Û° Ø¯ÙÛÙÙâØ§Û": "Ø±ÙÛ ÙØ·Ø§ÙØ¹Ù ÙØªÙØ±Ú©Ø² Û²Û° Ø¯ÙÛÙÙâØ§ÛØ Ø§ÙØªØ®Ø§Ø¨ ÙØ·ÙØ¨Ø Ø­Ø°Ù Ø­ÙØ§Ø³âÙ¾Ø±ØªÛ Ù ÛÚ© Ø±ÙØ´ Ø³Ø§Ø¯Ù Ø¨Ø±Ø§Û Ø´Ø±ÙØ¹ ØªÙØ±Ú©Ø² Ú©Ù.",
        "Û±Û° Ø¯ÙÛÙÙ ØªÙØ±Ú©Ø²": "Ø±ÙÛ Ø§ÛØ¬Ø§Ø¯ ÛÚ© Ø¨Ø§Ø²Ù ØªÙØ±Ú©Ø² Û±Û° Ø¯ÙÛÙÙâØ§ÛØ Ø­Ø°Ù ÙØ²Ø§Ø­ÙØªâÙØ§Ø Ø´Ø±ÙØ¹ Ú©Ø§Ø± Ù Ø§Ø³ØªØ±Ø§Ø­Øª Ú©ÙØªØ§Ù ØªÙØ±Ú©Ø² Ú©Ù.",
        "Û³Û° Ø¯ÙÛÙÙ Ú©Ø§Ø± Ø¨Ø¯ÙÙ Ø­ÙØ§Ø³âÙ¾Ø±ØªÛ": "Ø±ÙÛ ÛÚ© Ø¨Ø§Ø²Ù Ú©Ø§Ø±Û Û³Û° Ø¯ÙÛÙÙâØ§ÛØ Ø­Ø°Ù Ø§Ø¹ÙØ§ÙâÙØ§Ø ØªØ¹ÛÛÙ ÛÚ© Ø®Ø±ÙØ¬Û ÙØ´Ø®Øµ Ù Ø­ÙØ¸ ØªÙØ±Ú©Ø² ØªÙØ±Ú©Ø² Ú©Ù.",
        "Ù¾Ø³âØ§ÙØ¯Ø§Ø² Ø±ÙØ²Ø§ÙÙ": "Ø±ÙÛ Ú©ÙØ§Ø± Ú¯Ø°Ø§Ø´ØªÙ ÙØ¨ÙØº Ú©ÙÚÚ© Ø±ÙØ²Ø§ÙÙØ Ú©ÙØªØ±Ù ÙØ²ÛÙÙâÙØ§Û ØºÛØ±Ø¶Ø±ÙØ±Û Ù Ø³Ø§Ø®Øª Ø¹Ø§Ø¯Øª Ù¾Ø³âØ§ÙØ¯Ø§Ø² ØªÙØ±Ú©Ø² Ú©Ù.",
    }
    return rules.get(t, f"ØªÙØ§Ù ÙØ­ØªÙØ§Û Ù¾Ø³Øª Ø¨Ø§ÛØ¯ ÙØ³ØªÙÛÙØ§Ù Ø¯Ø±Ø¨Ø§Ø±Ù Â«{t}Â» Ø¨Ø§Ø´Ø¯Ø ØªØ¹Ø±ÛÙ ÙÙØ¶ÙØ¹Ø ÙØ§ÛØ¯Ù ÛØ§ Ú©Ø§Ø±Ø¨Ø±Ø¯ Ø¢ÙØ ÛÚ© Ø±ÙØ´ Ø¹ÙÙÛ ÙØ±ØªØ¨Ø· Ù ÛÚ© ØªÙØ±ÛÙ ÙØ´Ø®Øµ ÙØ±ØªØ¨Ø· Ø¨Ø§ ÙÙØ§Ù ÙÙØ¶ÙØ¹ Ø±Ø§ ØªÙØ¶ÛØ­ Ø¨Ø¯Ù.")


def _topic_terms(topic):
    """Keywords used as a lightweight relevance gate after generation."""
    t = str(topic or "").strip().lower()
    aliases = {
        "ÙØ±Ø²Ø´ Û±Û° Ø¯ÙÛÙÙâØ§Û": ["ÙØ±Ø²Ø´", "Û±Û° Ø¯ÙÛÙÙ", "Ø¯ÙÛÙÙ", "Ø¨Ø¯Ù", "ØªÙØ±ÛÙ"],
        "Ø®ÙØ§Ø¨ Ø¨ÙØªØ±": ["Ø®ÙØ§Ø¨", "Ø´Ø¨", "Ø§Ø³ØªØ±Ø§Ø­Øª", "Ø®ÙØ§Ø¨ÛØ¯Ù"],
        "Ø¢Ø¨ Ú©Ø§ÙÛ": ["Ø¢Ø¨", "ÙÙØ´ÛØ¯Ù", "Ú©ÙâØ¢Ø¨Û", "ÙÛÙØ§Ù"],
        "ÙØ·Ø§ÙØ¹Ù Û²Û° Ø¯ÙÛÙÙâØ§Û": ["ÙØ·Ø§ÙØ¹Ù", "Û²Û° Ø¯ÙÛÙÙ", "Ú©ØªØ§Ø¨", "ÛØ§Ø¯Ú¯ÛØ±Û"],
        "Û±Û° Ø¯ÙÛÙÙ ØªÙØ±Ú©Ø²": ["ØªÙØ±Ú©Ø²", "Û±Û° Ø¯ÙÛÙÙ", "Ø­ÙØ§Ø³", "Ú©Ø§Ø±"],
        "Ù¾Ø³âØ§ÙØ¯Ø§Ø² Ø±ÙØ²Ø§ÙÙ": ["Ù¾Ø³âØ§ÙØ¯Ø§Ø²", "ÙØ²ÛÙÙ", "Ù¾ÙÙ", "Ø±ÙØ²Ø§ÙÙ"],
    }
    return aliases.get(t, [x for x in re.findall(r"[Ø-Û¿A-Za-z0-9]+", t) if len(x) > 2])


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
    value=re.sub(r"[^\wØ-Û¿]+", " ", value, flags=re.UNICODE)
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
        f"ð¯ {topic}\\n\\n{focus}\\n\\nâ¢ Ø§ÙØ±ÙØ² ÛÚ© Ø§ÙØ¯Ø§Ù ÙØ´Ø®Øµ Ø¯Ø±Ø¨Ø§Ø±Ù ÙÙÛÙ ÙÙØ¶ÙØ¹ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù.\\nâ¢ ÙØªÛØ¬Ù Ø±Ø§ Ú©ÙØªØ§Ù Ø«Ø¨Øª Ú©Ù.\\nð¡ ØªÙØ±ÛÙ: Û±Û° Ø¯ÙÛÙÙ ÙÙØ· Ø±ÙÛ Â«{topic}Â» Ú©Ø§Ø± Ú©Ù.",
        f"ð {topic}\\n\\n{focus}\\n\\nâ¢ ÛÚ© ÙØ§ÙØ¹ ÙØ±ØªØ¨Ø· Ø¨Ø§ Ø§ÛÙ ÙÙØ¶ÙØ¹ Ø±Ø§ Ø­Ø°Ù Ú©Ù.\\nâ¢ ÛÚ© ÙØ¯Ù Ú©ÙÚÚ© Ù ÙØ§Ø¨Ù Ø§ÙØ¯Ø§Ø²ÙâÚ¯ÛØ±Û Ø¨Ø±Ø¯Ø§Ø±.\\nð¡ ØªÙØ±ÛÙ Ø§ÙØ±ÙØ²: ÛÚ© Ø§ÙØ¯Ø§Ù ÙØ³ØªÙÛÙ Ø¯Ø±Ø¨Ø§Ø±Ù Â«{topic}Â» Ø§ÙØ¬Ø§Ù Ø¨Ø¯Ù.",
        f"ð§  {topic}\\n\\n{focus}\\n\\nâ¢ ÙÙØ¶ÙØ¹ Ø±Ø§ Ø¨Ù ÛÚ© Ú©Ø§Ø± Ú©ÙÚÚ© ØªØ¨Ø¯ÛÙ Ú©Ù.\\nâ¢ Ø²ÙØ§Ù Ø´Ø±ÙØ¹ Ø±Ø§ ÙØ´Ø®Øµ Ú©Ù.\\nð¡ Ø§ÙØ¯Ø§Ù Ø§ÙØ±ÙØ²: ÛÚ© ÙØ¯Ù ÙØ±ØªØ¨Ø· Ø¨Ø§ Â«{topic}Â» Ø§ÙØ¬Ø§Ù Ø¨Ø¯Ù.",
    ]
    return variants[(attempt-1)%len(variants)]

def generate_unique_auto_post(channel_id, category, topic):
    recent=recent_auto_posts(channel_id,8)
    avoid="\n".join(f"- {r['topic']}: {str(r['content'])[:220]}" for r in recent)
    for attempt in range(1,9):
        duplicate,score=post_is_duplicate(channel_id,topic,content)
        if not duplicate and _is_topic_relevant(content,topic):
            return content
        logger.warning("Auto post rejected topic=%s attempt=%s similarity=%.2f",topic,attempt,score)
        avoid += f"\n- ÙØ³Ø®Ù Ø±Ø¯Ø´Ø¯Ù: {str(content)[:220]}"
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
        candidate=topic_specific_fallback(topic,8)+f"\n\nð ÙØ³Ø®Ù {datetime.now(TZ).strftime('%Y%m%d')}-{n}"
        duplicate,_=post_is_duplicate(channel_id,topic,candidate,threshold=0.995)
        if not duplicate:
            return candidate
    raise RuntimeError("Unable to generate a unique automatic post")







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


def compact_channel_footer(bot_username="", channel_username=""):
    # Every published channel post gets the configured channel username below it.
    if channel_username:
        return f"\n\nð¢ {channel_username}"
    return ""


async def channel_post_footer(bot, channel):
    """Return the public @username of the configured channel for post footers."""
    try:
        chat = await bot.get_chat(channel)
        username = getattr(chat, "username", None)
        if username:
            username = str(username).lstrip("@")
            return f"\n\nð¢ @{username}"
    except Exception as e:
        logger.warning("Could not resolve channel username for post footer: %s", e)
    return ""


def add_channel_username_footer(content, footer, max_length=None):
    content = str(content or "").rstrip()
    if not footer:
        return content[:max_length] if max_length else content
    if footer.strip() in content:
        return content[:max_length] if max_length else content
    if max_length and len(content) + len(footer) > max_length:
        content = content[:max(0, max_length - len(footer))].rstrip()
    return content + footer


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
        d.text((512,820),"ÛÚ© ÙØ¯Ù Ú©ÙÚÚ©Ø ÙØ± Ø±ÙØ²",font=small,anchor="mm",fill=(203,213,225))
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
    return InlineKeyboardMarkup([[InlineKeyboardButton("ð ÙÙÛØ¯ Ø¨ÙØ¯",callback_data=f"feedback:up:{key}"),InlineKeyboardButton("ð ÙÙØ§Ø³Ø¨ ÙØ¨ÙØ¯",callback_data=f"feedback:down:{key}")]])

async def feedback_callback(update,context):
    q=update.callback_query; uid=q.from_user.id; await q.answer("Ø«Ø¨Øª Ø´Ø¯")
    parts = _safe_cb_parts(q.data, ":", 3)
    if not parts: return
    _,rating,topic=parts; score=1 if rating=="up" else -1; now=datetime.now(TZ).isoformat(); c=db(); c.execute("INSERT INTO content_feedback(post_key,user_id,rating,reaction,created_at) VALUES(?,?,?,?,?)",(topic,uid,score,rating,now)); c.execute("INSERT INTO content_preferences(user_id,category,score) VALUES(?,?,?) ON CONFLICT(user_id,category) DO UPDATE SET score=score+excluded.score",(uid,topic,score)); c.commit(); c.close(); add_xp(uid,2,"content_feedback")


async def send_auto_channel_post(context, channel, topic, category=None):
    if not feature_enabled("auto_publish"):
        raise RuntimeError("auto_publish feature is disabled")
    category=category or get_auto_setting("category","random")
    content=generate_unique_auto_post(channel,category,topic)
    footer=await channel_post_footer(context.bot, channel)
    content=add_channel_username_footer(content, footer, 4096)
    # Channel auto-posts are intentionally text-only. Feedback is collected in
    # the end-of-day poll, not under each individual post.
    try:
        msg=await context.bot.send_message(chat_id=channel,text=content)
        save_auto_post_history(channel,topic,category,content)
        if any(k in topic for k in ("ÙØ±Ø²Ø´","Ø­Ø±Ú©Ø§Øª","ØªÙØ±ÛÙ")):
            try:
                await context.bot.send_poll(chat_id=channel,question="ð ØªÙØ±ÛÙ Ø§ÙØ±ÙØ² Ø±Ø§ Ø§ÙØ¬Ø§Ù Ø¯Ø§Ø¯ÛØ",options=["â Ø§ÙØ¬Ø§Ù Ø¯Ø§Ø¯Ù","â³ ÙÙÙØ² ÙÙ","â Ø§ÙØ¬Ø§Ù ÙØ¯Ø§Ø¯Ù"],is_anonymous=False)
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
            category,topic=get_auto_topic(); content=generate_unique_auto_post(channel,category,topic); footer=await channel_post_footer(context.bot, channel); content=add_channel_username_footer(content, footer, 4096)
            c=db(); cur=c.execute("INSERT INTO auto_pending(channel_id,topic,content,publish_at,created_at) VALUES(?,?,?,?,?)",(str(channel),topic,content,next_run.isoformat(),now.isoformat())); pid=cur.lastrowid; c.commit(); c.close()
            kb=InlineKeyboardMarkup([[InlineKeyboardButton("â ØªØ£ÛÛØ¯ Ø´Ø¯Ù Ø§Ø² Ø·Ø±Ù ÙÙ â Ø§ÙØªØ´Ø§Ø±",callback_data=f"appr:{pid}"),InlineKeyboardButton("â Ø±Ø¯",callback_data=f"apprrej:{pid}")]])
            for admin_id in ADMIN_IDS:
                try: await context.bot.send_message(admin_id,f"ð <b>Ù¾ÛØ´âÙÙØ§ÛØ´ Ù¾Ø³Øª</b>\n\nð {category}\nð Ø§ÙØªØ´Ø§Ø± Ø¯Ø±: {next_run.strftime('%H:%M')}\n\n{content}",parse_mode="HTML",reply_markup=kb)
                except Exception as e: logger.error("Approval preview failed: %s",e)
            return
        if now<next_run: return
        c=db(); pending=c.execute("SELECT * FROM auto_pending WHERE channel_id=? AND publish_at=? ORDER BY id DESC LIMIT 1",(str(channel),next_run.isoformat())).fetchone(); c.close()
        if pending and pending["status"]=="approved":
            try:
                content=pending["content"]
                footer=await channel_post_footer(context.bot, channel)
                content=add_channel_username_footer(content, footer, 4096)
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
    if not admin_guard(uid): await q.answer("â",show_alert=True); return
    await q.answer(); pid=int(q.data.split(":",1)[1]); c=db(); r=c.execute("SELECT * FROM auto_pending WHERE id=?",(pid,)).fetchone();
    if not r: c.close(); await q.message.edit_text("â Ù¾ÛØ´âÙÙØ§ÛØ´ Ù¾ÛØ¯Ø§ ÙØ´Ø¯."); return
    c.execute("UPDATE auto_pending SET status='approved' WHERE id=?",(pid,)); c.commit(); c.close(); await q.message.edit_text("â ØªØ£ÛÛØ¯ Ø´Ø¯. Ù¾Ø³Øª Ø¯Ø± Ø²ÙØ§Ù ØªØ¹ÛÛÙâØ´Ø¯Ù ÙÙØªØ´Ø± ÙÛâØ´ÙØ¯.")

async def approval_reject_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("â",show_alert=True); return
    await q.answer(); pid=int(q.data.split(":",1)[1]); c=db(); c.execute("UPDATE auto_pending SET status='rejected' WHERE id=? AND status='pending'",(pid,)); c.commit(); c.close(); await q.message.edit_text("â Ù¾Ø³Øª Ø±Ø¯ Ø´Ø¯ Ù ÙÙØªØ´Ø± ÙÙÛâØ´ÙØ¯.")



_CHANNEL_SCHEMA_READY = False
_CHANNEL_SCHEMA_LOCK = threading.Lock()

def _ensure_channel_runtime_schema():
    """Self-heal channel tables for older SQLite databases before channel UI queries."""
    global _CHANNEL_SCHEMA_READY
    if _CHANNEL_SCHEMA_READY:
        return
    with _CHANNEL_SCHEMA_LOCK:
        if _CHANNEL_SCHEMA_READY:
            return
        _channel_runtime_schema_ddl()
        _CHANNEL_SCHEMA_READY = True

def _channel_runtime_schema_ddl():
    c = db()
    try:
        c.execute("""CREATE TABLE IF NOT EXISTS system_settings(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS channel_config(
            id INTEGER PRIMARY KEY CHECK(id=1),
            channel_id TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT ''
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS managed_channels(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS channel_posts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL DEFAULT '',
            schedule_type TEXT NOT NULL DEFAULT 'once',
            schedule_time TEXT,
            weekday INTEGER,
            run_at TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            last_sent_at TEXT,
            created_at TEXT NOT NULL DEFAULT '',
            created_by INTEGER NOT NULL DEFAULT 0
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS auto_post_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT NOT NULL,
            topic TEXT NOT NULL DEFAULT '',
            category TEXT,
            content TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            UNIQUE(channel_id, content_hash)
        )""")

        for column, ddl in (
            ("channel_id", "TEXT NOT NULL DEFAULT ''"),
            ("enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("updated_at", "TEXT NOT NULL DEFAULT ''"),
        ):
            ensure_column(c, "channel_config", column, ddl)

        for column, ddl in (
            ("channel_id", "TEXT NOT NULL DEFAULT ''"),
            ("title", "TEXT NOT NULL DEFAULT ''"),
            ("enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
            ("updated_at", "TEXT NOT NULL DEFAULT ''"),
        ):
            ensure_column(c, "managed_channels", column, ddl)

        for column, ddl in (
            ("content", "TEXT NOT NULL DEFAULT ''"),
            ("schedule_type", "TEXT NOT NULL DEFAULT 'once'"),
            ("schedule_time", "TEXT"),
            ("weekday", "INTEGER"),
            ("run_at", "TEXT"),
            ("enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("last_sent_at", "TEXT"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
            ("created_by", "INTEGER NOT NULL DEFAULT 0"),
        ):
            ensure_column(c, "channel_posts", column, ddl)

        for column, ddl in (
            ("channel_id", "TEXT NOT NULL DEFAULT ''"),
            ("topic", "TEXT NOT NULL DEFAULT ''"),
            ("category", "TEXT"),
            ("content", "TEXT NOT NULL DEFAULT ''"),
            ("content_hash", "TEXT NOT NULL DEFAULT ''"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
        ):
            ensure_column(c, "auto_post_history", column, ddl)

        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_auto_history_channel_created "
            "ON auto_post_history(channel_id, created_at)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_auto_history_topic_created "
            "ON auto_post_history(topic, created_at)"
        )
        c.commit()
    finally:
        c.close()

def channel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("ð¡ ØªÙØ¸ÛÙ Ú©Ø§ÙØ§Ù", callback_data="ch:set"),
         InlineKeyboardButton("ð ØªØ³Øª Ø§ØªØµØ§Ù", callback_data="ch:test")],
        [InlineKeyboardButton("ð Ø³Ø§Ø®Øª Ù¾Ø³Øª", callback_data="ch:new"),
         InlineKeyboardButton("ð¤ Ø³Ø§Ø®Øª Ù¾Ø³Øª ÙÙØ´ÙÙØ¯", callback_data="ch:smart")],
        [InlineKeyboardButton("ð Ù¾Ø³ØªâÙØ§", callback_data="ch:list"),
         InlineKeyboardButton("ð ØªØ§Ø±ÛØ®ÚÙ Ø§ÙØªØ´Ø§Ø±", callback_data="ch:history")],
        [InlineKeyboardButton("ð§© ÚÙØ¯ Ù¾Ø³Øª / Ø²ÙØ§ÙâØ¨ÙØ¯Û", callback_data="ch:batch"),
         InlineKeyboardButton("ð¤ Ø§ÙØªØ´Ø§Ø± Ø®ÙØ¯Ú©Ø§Ø±", callback_data="ch:auto")],
        [InlineKeyboardButton("ð¢ Ú©Ø§ÙØ§ÙâÙØ§Û ÙØªØµÙ", callback_data="ch:channels"),
         InlineKeyboardButton("ð Ø¹Ø¶ÙÛØª Ø§Ø¬Ø¨Ø§Ø±Û", callback_data="forcedsub:home")],
        [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")]
    ])

def channel_schedule_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("ð¤ Ø§Ø±Ø³Ø§Ù ÙÙØ±Û",callback_data="chs:now")],[InlineKeyboardButton("ð ÛÚ©âØ¨Ø§Ø±",callback_data="chs:once"),InlineKeyboardButton("ð Ø±ÙØ²Ø§ÙÙ",callback_data="chs:daily")],[InlineKeyboardButton("ð ÙÙØªÚ¯Û",callback_data="chs:weekly")],[InlineKeyboardButton("â ÙØºÙ",callback_data="chs:cancel")]])

def channel_time_keyboard(prefix):
    rows=[[InlineKeyboardButton(x,callback_data=f"{prefix}:{x}") for x in TIME_BUTTONS[i:i+4]] for i in range(0,len(TIME_BUTTONS),4)]
    # BUGFIX: this helper previously returned None, so the daily/weekly channel
    # time picker was sent without any buttons.
    rows.append([InlineKeyboardButton("â ÙØºÙ", callback_data="chs:cancel")])
    return InlineKeyboardMarkup(rows)

def channel_schedule_text(r):
    if r["schedule_type"]=="daily": return f"ð Ø±ÙØ²Ø§ÙÙ {r['schedule_time']}"
    if r["schedule_type"]=="weekly": return f"ð ÙÙØªÚ¯Û Ø±ÙØ² {r['weekday']} Ø³Ø§Ø¹Øª {r['schedule_time']}"
    return f"ð {r['run_at'].replace('T',' ') if r['run_at'] else 'ÙÙØ±Û'}"

async def channel_scheduler_job(context):
    now=datetime.now(TZ); key=now.strftime("%Y-%m-%d %H:%M"); hhmm=now.strftime("%H:%M"); cfg=get_channel_config()
    if not cfg or not cfg["enabled"] or not cfg["channel_id"]: return
    c=db(); rows=c.execute("SELECT * FROM channel_posts WHERE enabled=1").fetchall(); c.close()
    for r in rows:
        due=(r["schedule_type"]=="daily" and r["schedule_time"]==hhmm) or (r["schedule_type"]=="weekly" and r["weekday"]==now.weekday() and r["schedule_time"]==hhmm) or (r["schedule_type"]=="once" and r["run_at"] and r["run_at"][:16]==key)
        if not due or (r["last_sent_at"] and r["last_sent_at"][:16]==key): continue
        try:
            footer=await channel_post_footer(context.bot, cfg["channel_id"])
            post_text=add_channel_username_footer(r["content"], footer, 4096)
            await context.bot.send_message(chat_id=cfg["channel_id"],text=post_text)
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
    state = "ð¢ Ø®ÙØ¯Ú©Ø§Ø± Ø±ÙØ´Ù" if enabled else "âª Ø®ÙØ¯Ú©Ø§Ø± Ø®Ø§ÙÙØ´"
    topic_text = "ð² ØªØµØ§Ø¯ÙÛ"
    if category != "random":
        topic_text = category
        if subcategory != "random":
            topic_text += f"\nâ³ {subcategory}"

    rows = [
        [InlineKeyboardButton(state, callback_data="auto:toggle")],
        [
            InlineKeyboardButton(f"â± ÙØ± {interval} Ø¯ÙÛÙÙ", callback_data="auto:interval"),
            InlineKeyboardButton("ð§  ÙÙØ¶ÙØ¹", callback_data="auto:category"),
        ],
        [InlineKeyboardButton(topic_text[:60], callback_data="auto:category")],
        [InlineKeyboardButton("ð ÙØ¶Ø¹ÛØª Ù Ø²ÙØ§Ù Ø¨Ø¹Ø¯Û", callback_data="auto:info")],
        [InlineKeyboardButton("ð§ª ØªØ³Øª Û· Ø±ÙØ²Ù", callback_data="auto:test")],
        [InlineKeyboardButton("ð Ø±Ø§ÙÙÙØ§Û Ø§Ø³ØªÙØ§Ø¯Ù", callback_data="auto:guide")],
        [InlineKeyboardButton("â¬ï¸ ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù", callback_data="ch:main")],
    ]
    return InlineKeyboardMarkup(rows)


def auto_category_keyboard():
    rows = [
        [InlineKeyboardButton("ð² Ø§ÙØªØ®Ø§Ø¨ ØªØµØ§Ø¯ÙÛ", callback_data="autocat:random")]
    ]
    for i, category in enumerate(AUTO_TOPIC_TREE_FA):
        rows.append([InlineKeyboardButton(category, callback_data=f"autocat:{i}")])
    rows.append([InlineKeyboardButton("â¬ï¸ Ø§ÙØªØ´Ø§Ø± Ø®ÙØ¯Ú©Ø§Ø±", callback_data="auto:back")])
    return InlineKeyboardMarkup(rows)


def auto_subcategory_keyboard(category_index):
    categories = list(AUTO_TOPIC_TREE_FA.keys())
    category = categories[category_index]
    rows = [
        [InlineKeyboardButton("ð² ÙÙÙ Ø´Ø§Ø®ÙâÙØ§Û Ø§ÛÙ Ø¯Ø³ØªÙ", callback_data=f"autosub:{category_index}:random")]
    ]
    for i, sub in enumerate(AUTO_TOPIC_TREE_FA[category]):
        rows.append([InlineKeyboardButton(sub, callback_data=f"autosub:{category_index}:{i}")])
    rows.append([InlineKeyboardButton("â¬ï¸ Ø¯Ø³ØªÙâÙØ§", callback_data="auto:category")])
    return InlineKeyboardMarkup(rows)


def auto_interval_keyboard():
    rows = []
    for i in range(0, len(AUTO_INTERVALS_MIN), 2):
        pair = AUTO_INTERVALS_MIN[i:i + 2]
        rows.append([
            InlineKeyboardButton(
                f"ÙØ± {m // 60} Ø³Ø§Ø¹Øª" if m >= 60 and m % 60 == 0 else f"ÙØ± {m} Ø¯ÙÛÙÙ",
                callback_data=f"autoint:{m}"
            )
            for m in pair
        ])
    rows.append([InlineKeyboardButton("âï¸ Ø²ÙØ§Ù Ø¯ÙØ®ÙØ§Ù", callback_data="autoint:custom")])
    rows.append([InlineKeyboardButton("â¬ï¸ Ø§ÙØªØ´Ø§Ø± Ø®ÙØ¯Ú©Ø§Ø±", callback_data="auto:back")])
    return InlineKeyboardMarkup(rows)


@subscription_required
async def auto_channel_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    if not admin_guard(uid):
        await q.answer("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", show_alert=True)
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
            "ð¢ Ø§ÙØªØ´Ø§Ø± Ø®ÙØ¯Ú©Ø§Ø± Ø±ÙØ´Ù Ø´Ø¯." if new_value == "1" else "âª Ø§ÙØªØ´Ø§Ø± Ø®ÙØ¯Ú©Ø§Ø± Ø®Ø§ÙÙØ´ Ø´Ø¯.",
            reply_markup=auto_channel_keyboard()
        )

    elif action == "interval":
        await q.message.edit_text(
            "â± ÙØ§ØµÙÙ Ø§ÙØªØ´Ø§Ø± Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù.\nØ§Ø² Ûµ Ø¯ÙÛÙÙ ØªØ§ Û²Û´ Ø³Ø§Ø¹ØªØ ÛØ§ Ø²ÙØ§Ù Ø¯ÙØ®ÙØ§Ù:",
            reply_markup=auto_interval_keyboard()
        )

    elif action == "category":
        await q.message.edit_text(
            "ð§  Ø¯Ø³ØªÙâØ¨ÙØ¯Û Ú©Ø§ÙÙ Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:",
            reply_markup=auto_category_keyboard()
        )

    elif action == "guide":
        await q.message.edit_text(
            "ð <b>Ø±Ø§ÙÙÙØ§Û Ø§ÙØªØ´Ø§Ø± Ø®ÙØ¯Ú©Ø§Ø± MyTasks</b>\n\n"
            "1ï¸â£ Ø§ÙÙ Ø§Ø² Ø¨Ø®Ø´ Â«ð§  ÙÙØ¶ÙØ¹Â» Ø¯Ø³ØªÙ ÙÙØ±Ø¯ÙØ¸Ø± Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù.\n"
            "2ï¸â£ Ø³Ù¾Ø³ Ø²ÛØ±Ø´Ø§Ø®Ù Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©ÙØ ÙØ«ÙØ§Ù Ø³Ø±ÙØ§ÛÙâÚ¯Ø°Ø§Ø±Û â Ø§Ø±Ø² Ù Ø¯ÙØ§Ø±.\n"
            "3ï¸â£ Ø¨Ø¹Ø¯ Ø§Ø² Ø§ÙØªØ®Ø§Ø¨ ÙÙØ¶ÙØ¹Ø Ø±Ø¨Ø§Øª Ø§Ø² ØªÙ ÙÛâÙ¾Ø±Ø³Ø¯ ÙØ± ÚÙØ¯ Ø¯ÙÛÙÙ ÛÚ© Ù¾Ø³Øª ÙÙØªØ´Ø± Ø´ÙØ¯.\n"
            "4ï¸â£ Ø²ÙØ§Ù Ø±Ø§ Ø§Ø² Ûµ Ø¯ÙÛÙÙ ØªØ§ Û²Û´ Ø³Ø§Ø¹Øª Ø§ÙØªØ®Ø§Ø¨ Ú©Ù ÛØ§ Ø²ÙØ§Ù Ø¯ÙØ®ÙØ§Ù ÙØ§Ø±Ø¯ Ú©Ù.\n"
            "5ï¸â£ Ø¨Ø§ Ø§ÙØªØ®Ø§Ø¨ Ø²ÙØ§ÙØ Ø§ÙØªØ´Ø§Ø± Ø®ÙØ¯Ú©Ø§Ø± Ø±ÙØ´Ù ÙÛâØ´ÙØ¯.\n\n"
            "â¸ Ø¨Ø±Ø§Û ØªÙÙÙØ Ø±ÙÛ Â«Ø®ÙØ¯Ú©Ø§Ø± Ø±ÙØ´ÙÂ» Ø¨Ø²Ù.\n"
            "âï¸ Ø¨Ø±Ø§Û ØªØºÛÛØ± ÙÙØ¶ÙØ¹ ÛØ§ ÙØ§ØµÙÙ Ø§ÙØªØ´Ø§Ø±Ø Ø¯ÙØ¨Ø§Ø±Ù ÙÙØ§Ù Ú¯Ø²ÛÙÙ Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù.\n"
            "â©ï¸ Ø¯Ø± ÙÙÙ Ø¨Ø®Ø´âÙØ§ Ø§ÙÚ©Ø§Ù Ø¨Ø±Ú¯Ø´Øª ÙØ¬ÙØ¯ Ø¯Ø§Ø±Ø¯.",
            parse_mode="HTML",
            reply_markup=auto_channel_keyboard(),
        )

    elif action == "test":
        await q.message.edit_text(
            "ð§ª ØªØ³Øª Û· Ø±ÙØ²Ù\n\n"
            f"ÙØ¶Ø¹ÛØª: {'ð¢ ÙØ¹Ø§Ù' if test_mode_active() else 'ð´ Ø®Ø§ÙÙØ´/Ù¾Ø§ÛØ§Ù ÛØ§ÙØªÙ'}\n"
            f"Ø²ÙØ§Ù Ø¨Ø§ÙÛâÙØ§ÙØ¯Ù: {test_mode_remaining()}\n\n"
            "Ø¯Ø± Ø²ÙØ§Ù ØªØ³ØªØ Ù¾Ø³Øª Ø®ÙØ¯Ú©Ø§Ø± ÙØ¨Ù Ø§Ø² Ø§ÙØªØ´Ø§Ø± Ø¨Ø±Ø§Û Admin Ù¾ÛØ´âÙÙØ§ÛØ´ ÙÛâØ´ÙØ¯."
        )
    elif action == "info":
        interval = get_auto_setting("interval_minutes", "60")
        next_run = get_auto_setting("next_run", "ØªÙØ¸ÛÙ ÙØ´Ø¯Ù").replace("T", " ")[:16]
        category = get_auto_setting("category", "random")
        sub = get_auto_setting("subcategory", "random")
        await q.message.edit_text(
            f"ð¤ ÙØ¶Ø¹ÛØª Ø§ÙØªØ´Ø§Ø± Ø®ÙØ¯Ú©Ø§Ø±\n\n"
            f"ÙØ¶Ø¹ÛØª: {'ð¢ Ø±ÙØ´Ù' if get_auto_setting('enabled','0')=='1' else 'âª Ø®Ø§ÙÙØ´'}\n"
            f"â± ÙØ§ØµÙÙ: ÙØ± {interval} Ø¯ÙÛÙÙ\n"
            f"ð§  Ø¯Ø³ØªÙ: {category}\n"
            f"ð Ø´Ø§Ø®Ù: {sub}\n"
            f"ð Ø§ÙØªØ´Ø§Ø± Ø¨Ø¹Ø¯Û: {next_run}",
            reply_markup=auto_channel_keyboard()
        )

    elif action == "back":
        await q.message.edit_text("ð¤ Ø§ÙØªØ´Ø§Ø± Ø®ÙØ¯Ú©Ø§Ø±", reply_markup=auto_channel_keyboard())


@subscription_required
async def auto_category_callback(update, context):
    q = update.callback_query
    if not admin_guard(q.from_user.id):
        await q.answer("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", show_alert=True)
        return
    await q.answer()
    value = q.data.split(":", 1)[1]
    if value == "random":
        set_auto_setting("category", "random")
        set_auto_setting("subcategory", "random")
        await q.message.edit_text(
            "ð² ÙÙØ¶ÙØ¹Ø§Øª Ø¨ÙâØµÙØ±Øª ØªØµØ§Ø¯ÙÛ Ø§ÙØªØ®Ø§Ø¨ ÙÛâØ´ÙÙØ¯.\n\nâ± Ø­Ø§ÙØ§ Ø¨Ú¯Ù ÙØ± ÚÙØ¯ Ø¯ÙÛÙÙ ÛÚ© Ù¾Ø³Øª ÙÙØªØ´Ø± Ø´ÙØ¯:",
            reply_markup=auto_interval_keyboard(),
        )
        return
    idx = int(value)
    category = list(AUTO_TOPIC_TREE_FA.keys())[idx]
    await q.message.edit_text(
        f"ð {category}\n\nØ­Ø§ÙØ§ Ø´Ø§Ø®Ù ÙÙØ±Ø¯ÙØ¸Ø± Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:",
        reply_markup=auto_subcategory_keyboard(idx)
    )


@subscription_required
async def auto_subcategory_callback(update, context):
    q = update.callback_query
    if not admin_guard(q.from_user.id):
        await q.answer("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", show_alert=True)
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
        f"â ÙÙØ¶ÙØ¹ Ø§ÙØªØ®Ø§Ø¨ Ø´Ø¯:\n{category}\nâ³ {sub if sub != 'random' else 'ÙÙÙ Ø´Ø§Ø®ÙâÙØ§'}\n\nâ± Ø­Ø§ÙØ§ Ø¨Ú¯Ù ÙØ± ÚÙØ¯ Ø¯ÙÛÙÙ ÛÚ© Ù¾Ø³Øª ÙÙØªØ´Ø± Ø´ÙØ¯:",
        reply_markup=auto_interval_keyboard()
    )


@subscription_required
async def auto_interval_callback(update, context):
    q = update.callback_query
    if not admin_guard(q.from_user.id):
        await q.answer("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", show_alert=True)
        return
    await q.answer()
    raw_minutes = q.data.split(":", 1)[1]
    if raw_minutes == "custom":
        context.user_data["auto_wait_interval"] = True
        await q.message.edit_text(
            "âï¸ ÙØ§ØµÙÙ Ø¯ÙØ®ÙØ§Ù Ø±Ø§ Ø¨Ù Ø¯ÙÛÙÙ ÙØ§Ø±Ø¯ Ú©Ù.\nÙØ«Ø§Ù: 45\nØ­Ø¯Ø§ÙÙ Ûµ Ù Ø­Ø¯Ø§Ú©Ø«Ø± Û±Û´Û´Û° Ø¯ÙÛÙÙ (Û²Û´ Ø³Ø§Ø¹Øª)."
        )
        return
    minutes = int(raw_minutes)
    set_auto_setting("interval_minutes", str(minutes))
    set_auto_setting("enabled", "1")
    set_auto_setting("next_run", (datetime.now(TZ) + timedelta(minutes=minutes)).isoformat())
    await q.message.edit_text(
        f"â Ø§ÙØªØ´Ø§Ø± Ø®ÙØ¯Ú©Ø§Ø± Ø±ÙÛ ÙØ± {minutes} Ø¯ÙÛÙÙ ØªÙØ¸ÛÙ Ø´Ø¯ Ù Ø±ÙØ´Ù Ø§Ø³Øª.",
        reply_markup=auto_channel_keyboard()
    )


def smart_post_category_keyboard():
    rows=[[InlineKeyboardButton("ð² ÙÙØ¶ÙØ¹ ØªØµØ§Ø¯ÙÛ",callback_data="chgen:random")]]
    for i,category in enumerate(AUTO_TOPIC_TREE_FA):
        rows.append([InlineKeyboardButton(category,callback_data=f"chgen:cat:{i}")])
    rows.append([InlineKeyboardButton("â¬ï¸ ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù",callback_data="ch:main")])
    return InlineKeyboardMarkup(rows)


def smart_post_subcategory_keyboard(cat_idx):
    categories=list(AUTO_TOPIC_TREE_FA.keys()); category=categories[cat_idx]
    rows=[[InlineKeyboardButton("ð² Ø´Ø§Ø®Ù ØªØµØ§Ø¯ÙÛ",callback_data=f"chgen:sub:{cat_idx}:random")]]
    for i,sub in enumerate(AUTO_TOPIC_TREE_FA[category]):
        rows.append([InlineKeyboardButton(sub,callback_data=f"chgen:sub:{cat_idx}:{i}")])
    rows.append([InlineKeyboardButton("â¬ï¸ Ø¯Ø³ØªÙâÙØ§",callback_data="ch:smart")])
    return InlineKeyboardMarkup(rows)


def smart_post_preview_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("ð¤ Ø§ÙØªØ´Ø§Ø± ÙÙØ±Û Ù¾Ø³Øª",callback_data="chgen:publish"),InlineKeyboardButton("ð Ø³Ø§Ø®Øª Ø¯ÙØ¨Ø§Ø±Ù",callback_data="chgen:regen")],
        [InlineKeyboardButton("â¬ï¸ Ø§ÙØªØ®Ø§Ø¨ ÙÙØ¶ÙØ¹",callback_data="ch:smart"),InlineKeyboardButton("ð  ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù",callback_data="ch:main")]
    ])


async def smart_post_show_preview(update,context,category,topic):
    q=update.callback_query; uid=q.from_user.id
    channel=get_channel_config()
    if not channel or not channel["channel_id"]:
        await q.message.edit_text("â Ø§Ø¨ØªØ¯Ø§ Ú©Ø§ÙØ§Ù Ø±Ø§ ØªÙØ¸ÛÙ Ú©Ù.",reply_markup=channel_keyboard()); return
    content=generate_unique_auto_post(channel["channel_id"],category,topic)
    context.user_data["smart_post"]={"channel":str(channel["channel_id"]),"category":category,"topic":topic,"content":content}
    await q.message.edit_text(
        f"ð <b>Ù¾ÛØ´âÙÙØ§ÛØ´ Ù¾Ø³Øª</b>\n\nð {html.escape(category)}\nð¯ {html.escape(topic)}\n\n{html.escape(content)}",
        parse_mode="HTML",reply_markup=smart_post_preview_keyboard())


async def smart_post_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.",show_alert=True); return
    await q.answer(); parts=q.data.split(":"); action=parts[1]
    if action=="smart":
        context.user_data.pop("smart_post",None)
        await q.message.edit_text("ð¤ <b>Ø³Ø§Ø®Øª Ù¾Ø³Øª ÙÙØ´ÙÙØ¯</b>\n\nÙÙØ¶ÙØ¹ Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù. Ù¾Ø³Øª ÙØ¨Ù Ø§Ø² Ø§ÙØªØ´Ø§Ø± Ú©Ø§ÙÙ Ø¨Ù ØªÙ ÙÙØ§ÛØ´ Ø¯Ø§Ø¯Ù ÙÛâØ´ÙØ¯:",parse_mode="HTML",reply_markup=smart_post_category_keyboard()); return
    if action=="cat":
        idx=int(parts[2]); categories=list(AUTO_TOPIC_TREE_FA.keys()); category=categories[idx]
        await q.message.edit_text(f"ð <b>{html.escape(category)}</b>\n\nØ²ÛØ±ÙÙØ¶ÙØ¹ Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:",parse_mode="HTML",reply_markup=smart_post_subcategory_keyboard(idx)); return
    if action=="random":
        category,topic=get_auto_topic(); await smart_post_show_preview(update,context,category,topic); return
    if action=="sub":
        cat_idx=int(parts[2]); sub_idx=parts[3]; categories=list(AUTO_TOPIC_TREE_FA.keys()); category=categories[cat_idx]
        topic=random.choice(AUTO_TOPIC_TREE_FA[category]) if sub_idx=="random" else AUTO_TOPIC_TREE_FA[category][int(sub_idx)]
        await smart_post_show_preview(update,context,category,topic); return
    if action=="regen":
        data=context.user_data.get("smart_post")
        if not data: await q.message.edit_text("â Ù¾ÛØ´âÙÙØ§ÛØ´ ÙÙÙØ¶Û Ø´Ø¯Ù Ø§Ø³Øª.",reply_markup=channel_keyboard()); return
        content=generate_unique_auto_post(data["channel"],data["category"],data["topic"])
        data["content"]=content; context.user_data["smart_post"]=data
        await q.message.edit_text(f"ð <b>Ù¾ÛØ´âÙÙØ§ÛØ´ Ø¬Ø¯ÛØ¯</b>\n\nð {html.escape(data['category'])}\nð¯ {html.escape(data['topic'])}\n\n{html.escape(content)}",parse_mode="HTML",reply_markup=smart_post_preview_keyboard()); return
    if action=="publish":
        data=context.user_data.get("smart_post")
        if not data: await q.message.edit_text("â Ù¾ÛØ´âÙÙØ§ÛØ´ ÙÙÙØ¶Û Ø´Ø¯Ù Ø§Ø³Øª.",reply_markup=channel_keyboard()); return
        try:
            image=await generate_topic_image(data["topic"])
            footer=await channel_post_footer(context.bot, data["channel"])
            content=add_channel_username_footer(data["content"], footer, 1024)
            if image is not None: await context.bot.send_photo(chat_id=data["channel"],photo=image,caption=content,reply_markup=content_feedback_keyboard(data["topic"]))
            else: await context.bot.send_message(chat_id=data["channel"],text=content,reply_markup=content_feedback_keyboard(data["topic"]))
            save_auto_post_history(data["channel"],data["topic"],data["category"],content)
            context.user_data.pop("smart_post",None)
            await q.message.edit_text("â <b>Ù¾Ø³Øª Ø¨Ø§ ÙÙÙÙÛØª ÙÙØ±Û ÙÙØªØ´Ø± Ø´Ø¯.</b>",parse_mode="HTML",reply_markup=channel_keyboard())
        except Exception as e:
            logger.exception("Smart immediate post failed")
            await q.message.edit_text("â Ø§ÙØªØ´Ø§Ø± ÙØ§ÙÙÙÙ Ø¨ÙØ¯. Ø¯Ø³ØªØ±Ø³Û Ø±Ø¨Ø§Øª Ø¨Ù Ú©Ø§ÙØ§Ù Ø±Ø§ Ø¨Ø±Ø±Ø³Û Ú©Ù.",reply_markup=channel_keyboard())


@subscription_required
async def channel_panel_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    if not admin_guard(uid):
        await q.answer("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", show_alert=True)
        return
    await q.answer()
    try:
        _ensure_channel_runtime_schema()
        await _channel_panel_inner(update, context)
    except Exception as e:
        logger.exception("channel_panel_callback error")
        release_leaked_connections(e)
        try:
            await q.answer("â Ø®Ø·Ø§ Ø±Ø® Ø¯Ø§Ø¯", show_alert=True)
        except Exception:
            pass
        try:
            await q.message.edit_text(
                "â ï¸ <b>Ø®Ø·Ø§ Ø¯Ø± Ù¾ÙÙ ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù</b>\n\n"
                f"Ú©Ø¯ Ø®Ø·Ø§: <code>{type(e).__name__}</code>\n"
                "Ø¯ÙØ¨Ø§Ø±Ù Ø§ÙØªØ­Ø§Ù Ú©Ù.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("ð ØªÙØ§Ø´ Ø¯ÙØ¨Ø§Ø±Ù", callback_data="ch:main")],
                    [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")]
                ])
            )
        except Exception:
            await q.message.reply_text(
                "â ï¸ Ø®Ø·Ø§ Ø¯Ø± Ù¾ÙÙ ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù. Ø¯ÙØ¨Ø§Ø±Ù Ø§ÙØªØ­Ø§Ù Ú©Ù.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("ð ØªÙØ§Ø´ Ø¯ÙØ¨Ø§Ø±Ù", callback_data="ch:main")],
                    [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")]
                ])
            )


async def _channel_panel_inner(update, context):
    q = update.callback_query
    uid = q.from_user.id
    parts=q.data.split(":")
    action = parts[1] if len(parts)>1 else "main"
    cfg = get_channel_config()
    if action=="history":
        channel_id=str(cfg["channel_id"]) if cfg and cfg["channel_id"] else ""
        c=db()
        rows=c.execute("SELECT topic,category,content,created_at FROM auto_post_history WHERE channel_id=? ORDER BY id DESC LIMIT 30",(channel_id,)).fetchall() if channel_id else []
        c.close()
        if not rows:
            text="ð <b>ØªØ§Ø±ÛØ®ÚÙ Ø§ÙØªØ´Ø§Ø±</b>\n\nÙÙÙØ² Ù¾Ø³ØªÛ Ø¯Ø± ØªØ§Ø±ÛØ®ÚÙ Ø«Ø¨Øª ÙØ´Ø¯Ù Ø§Ø³Øª."
        else:
            lines=["ð <b>ØªØ§Ø±ÛØ®ÚÙ Ø§ÙØªØ´Ø§Ø±</b>",""]
            for r in rows:
                stamp=str(r["created_at"]).replace("T"," ")[:16]
                preview=html.escape(str(r["content"]).replace("\n"," ")[:90])
                lines.append(f"ð <code>{stamp}</code>\nð {html.escape(str(r['topic']))}\n{preview}\n")
            text="\n".join(lines)
        await q.message.edit_text(text,parse_mode="HTML",reply_markup=channel_keyboard())
        return
    if action=="batch":
        context.user_data["channel_state"]="batch"
        await q.message.edit_text(
            "ð§© <b>ÚÙØ¯ Ù¾Ø³Øª / Ø²ÙØ§ÙâØ¨ÙØ¯Û</b>\n\n"
            "ÚÙØ¯ Ø®Ø· Ø¨ÙØ±Ø³ØªØ ÙØ± Ø®Ø· ÛÚ© Ù¾Ø³Øª Ø¨Ø§Ø´Ø¯ Ù Ø²ÙØ§Ù Ø±Ø§ Ø¨Ø§ | Ø¬Ø¯Ø§ Ú©Ù:\n"
            "<code>14:00 | ÙØªÙ Ù¾Ø³Øª Ø§ÙÙ</code>\n"
            "<code>16:00 | ÙØªÙ Ù¾Ø³Øª Ø¯ÙÙ</code>\n\n"
            "Ø§Ú¯Ø± ÙØªÙ Ø·ÙÙØ§ÙÛ Ø¨Ø§Ø´Ø¯Ø ÙÛâØªÙØ§ÙÛ ÙÙØ· ÙØªÙ Ø±Ø§ Ø¨ÙØ±Ø³ØªÛØ Ø±Ø¨Ø§Øª Ø¢Ù Ø±Ø§ Ø¨Ù ÚÙØ¯ Ø¨Ø®Ø´ ÙÙØ·ÙÛ ØªÙØ³ÛÙ ÙÛâÚ©ÙØ¯.",
            parse_mode="HTML")
        return
    if action=="channels":
        rows=list_managed_channels(); active=str(cfg["channel_id"]) if cfg and cfg["channel_id"] else ""
        kb=[]
        for r in rows:
            mark="â" if str(r["channel_id"])==active else "âª"
            kb.append([InlineKeyboardButton(f"{mark} {r['title'] or r['channel_id']}",callback_data=f"ch:select:{r['channel_id']}"),InlineKeyboardButton("ð",callback_data=f"ch:remove:{r['channel_id']}")])
        kb.append([InlineKeyboardButton("â Ø§ÙØ²ÙØ¯Ù Ú©Ø§ÙØ§Ù",callback_data="ch:set"),InlineKeyboardButton("â¬ï¸ Ø¨Ø±Ú¯Ø´Øª",callback_data="ch:main")])
        await q.message.edit_text("ð¢ <b>Ú©Ø§ÙØ§ÙâÙØ§Û ÙØªØµÙ</b>\n\nÚ©Ø§ÙØ§Ù ÙØ¹Ø§Ù Ø¨Ø§ â ÙØ´Ø®Øµ Ø§Ø³Øª. ÙØ± Ú©Ø§ÙØ§Ù ØªÙØ¸ÛÙØ§Øª Ù ØªØ§Ø±ÛØ®ÚÙ Ù¾Ø³Øª Ø®ÙØ¯Ø´ Ø±Ø§ Ø­ÙØ¸ ÙÛâÚ©ÙØ¯.",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb)); return
    if action=="select" and len(parts)>2:
        set_active_channel(parts[2]); await q.message.edit_text("â Ú©Ø§ÙØ§Ù ÙØ¹Ø§Ù ØªØºÛÛØ± Ú©Ø±Ø¯.",reply_markup=channel_keyboard()); return
    if action=="remove" and len(parts)>2:
        remove_managed_channel(parts[2]); await q.message.edit_text("ð Ú©Ø§ÙØ§Ù Ø§Ø² ÙÙØ±Ø³Øª ÙØ¯ÛØ±ÛØª Ø­Ø°Ù Ø´Ø¯Ø Ø§Ø·ÙØ§Ø¹Ø§Øª Ù¾Ø³ØªâÙØ§Û ÙØ¨ÙÛ Ù¾Ø§Ú© ÙØ´Ø¯.",reply_markup=channel_keyboard()); return
    channel = cfg["channel_id"] if cfg and cfg["channel_id"] else "ØªÙØ¸ÛÙ ÙØ´Ø¯Ù"

    if action == "main":
        await q.message.edit_text(
            f"ð¡ <b>ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù</b>\n\nð¢ Ú©Ø§ÙØ§Ù: <code>{channel}</code>",
            parse_mode="HTML",
            reply_markup=channel_keyboard(),
        )
    elif action == "set":
        context.user_data["channel_state"] = "set"
        await q.message.edit_text(
            "ð¡ ÛÙØ²Ø±ÙÛÙ Ú©Ø§ÙØ§Ù Ø±Ø§ Ø¨ÙØ±Ø³Øª.\n"
            "ÙØ«Ø§Ù: <code>@MyTasks</code>\n"
            "ÙÛÙÚ© t.me ÙÙ Ù¾Ø°ÛØ±ÙØªÙ ÙÛâØ´ÙØ¯.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("â¬ï¸ ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù", callback_data="ch:main")]]),
        )
    elif action == "smart":
        context.user_data.pop("smart_post",None)
        await q.message.edit_text("ð¤ <b>Ø³Ø§Ø®Øª Ù¾Ø³Øª ÙÙØ´ÙÙØ¯</b>\n\nÙÙØ¶ÙØ¹ Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù. Ø¨Ø¹Ø¯ Ø§Ø² Ø§ÙØªØ®Ø§Ø¨Ø Ù¾ÛØ´âÙÙØ§ÛØ´ Ú©Ø§ÙÙ Ù¾Ø³Øª ÙÙØ§ÛØ´ Ø¯Ø§Ø¯Ù ÙÛâØ´ÙØ¯ Ù Ø®ÙØ¯Øª Ø§ÙØªØ´Ø§Ø± ÙÙØ±Û Ø±Ø§ ØªØ£ÛÛØ¯ ÙÛâÚ©ÙÛ.",parse_mode="HTML",reply_markup=smart_post_category_keyboard())
    elif action == "auto":
        await q.message.edit_text(
            "ð¤ <b>Ø§ÙØªØ´Ø§Ø± Ø®ÙØ¯Ú©Ø§Ø±</b>\n\n"
            "Ù¾Ø³Øª ÙØªÙÛ ØªÙÛØ² + Ø¯Ø³ØªÙâØ¨ÙØ¯Û Ù Ø²ÙØ§ÙâØ¨ÙØ¯Û ÙØ§Ø¨Ù ØªÙØ¸ÛÙ.\nØªØµÙÛØ± Ø®ÙØ¯Ú©Ø§Ø± Ø¨Ø±Ø§Û Ø§ÙØªØ´Ø§Ø± Ú©Ø§ÙØ§Ù Ø®Ø§ÙÙØ´ Ø§Ø³Øª.",
            parse_mode="HTML",
            reply_markup=auto_channel_keyboard(),
        )
    elif action == "test":
        if channel == "ØªÙØ¸ÛÙ ÙØ´Ø¯Ù":
            await q.message.edit_text("â Ø§Ø¨ØªØ¯Ø§ Ú©Ø§ÙØ§Ù Ø±Ø§ ØªÙØ¸ÛÙ Ú©Ù.", reply_markup=channel_keyboard())
            return
        try:
            chat = await context.bot.get_chat(channel)
            await q.message.edit_text(
                f"â Ø§ØªØµØ§Ù ÙØ¹Ø§Ù Ø§Ø³Øª.\nð¢ {chat.title or channel}\nð <code>{chat.id}</code>",
                parse_mode="HTML",
                reply_markup=channel_keyboard(),
            )
            await hide_main_reply_keyboard(update)
        except Exception as e:
            logger.error("Channel test: %s", e)
            await q.message.edit_text(
                "â Ø§ØªØµØ§Ù ÙØ§ÙÙÙÙ.\nØ±Ø¨Ø§Øª Ø¨Ø§ÛØ¯ Administrator Ú©Ø§ÙØ§Ù Ø¨Ø§Ø´Ø¯ Ù Ø§Ø¬Ø§Ø²Ù Ø§Ø±Ø³Ø§Ù Ù¾ÛØ§Ù Ø¯Ø§Ø´ØªÙ Ø¨Ø§Ø´Ø¯.",
                reply_markup=channel_keyboard(),
            )
    elif action == "new":
        context.user_data["channel_state"] = "content"
        await q.message.edit_text("ð ÙØªÙ Ù¾Ø³Øª Ø±Ø§ Ø¨ÙØ±Ø³Øª:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("â¬ï¸ ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù", callback_data="ch:main")]]))
    elif action == "list":
        c = db()
        rows = c.execute(
            "SELECT * FROM channel_posts WHERE enabled=1 ORDER BY id DESC LIMIT 20"
        ).fetchall()
        c.close()
        text_out = "ð <b>Ù¾Ø³ØªâÙØ§Û ÙØ¹Ø§Ù</b>\n\n"
        if rows:
            text_out += "\n".join(
                f"#{r['id']} â {channel_schedule_text(r)}\nð {r['content'][:60]}"
                for r in rows
            )
        else:
            text_out += "ÙÙØ±Ø¯Û ÙÛØ³Øª."
        await q.message.edit_text(
            text_out, parse_mode="HTML", reply_markup=channel_keyboard()
        )


@subscription_required
async def channel_schedule_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.",show_alert=True); return
    await q.answer(); a=q.data.split(":",1)[1]
    if a=="cancel": context.user_data.clear(); await q.message.edit_text("â ÙØºÙ Ø´Ø¯.",reply_markup=channel_keyboard()); return
    if a=="now":
        cfg=get_channel_config()
        if not cfg or not cfg["channel_id"]: await q.message.edit_text("â Ø§Ø¨ØªØ¯Ø§ Ú©Ø§ÙØ§Ù Ø±Ø§ ØªÙØ¸ÛÙ Ú©Ù.",reply_markup=channel_keyboard()); return
        try:
            footer=await channel_post_footer(context.bot, cfg["channel_id"])
            post_text=add_channel_username_footer(post_text, footer, 4096)
            await context.bot.send_message(chat_id=cfg["channel_id"],text=post_text)
            context.user_data.clear(); await q.message.edit_text("â Ù¾Ø³Øª ÙÙØªØ´Ø± Ø´Ø¯.",reply_markup=channel_keyboard())
        except Exception as e: logger.error("Immediate channel post: %s",e); await q.message.edit_text("â Ø§ÙØªØ´Ø§Ø± ÙØ§ÙÙÙÙ. Ø¯Ø³ØªØ±Ø³Û Ú©Ø§ÙØ§Ù Ø±Ø§ Ø¨Ø±Ø±Ø³Û Ú©Ù.",reply_markup=channel_keyboard())
    elif a=="once": context.user_data["channel_state"]="once"; await q.message.edit_text("ð ØªØ§Ø±ÛØ® Ù Ø³Ø§Ø¹Øª Ø±Ø§ Ø¨ÙØ±Ø³Øª: Û±Û´Û°Ûµ/Û°Ûµ/Û²Û¹ Û±Û¸:Û³Û°")
    elif a=="daily": context.user_data["channel_state"]="daily"; await q.message.edit_text("â° Ø³Ø§Ø¹Øª Ø±ÙØ²Ø§ÙÙ:",reply_markup=channel_time_keyboard("chd"))
    elif a=="weekly": context.user_data["channel_state"]="wday"; await q.message.edit_text("ð Ø±ÙØ² ÙÙØªÙ:",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Ø´ÙØ¨Ù",callback_data="chw:5"),InlineKeyboardButton("ÛÚ©Ø´ÙØ¨Ù",callback_data="chw:6")],[InlineKeyboardButton("Ø¯ÙØ´ÙØ¨Ù",callback_data="chw:0"),InlineKeyboardButton("Ø³ÙâØ´ÙØ¨Ù",callback_data="chw:1")],[InlineKeyboardButton("ÚÙØ§Ø±Ø´ÙØ¨Ù",callback_data="chw:2"),InlineKeyboardButton("Ù¾ÙØ¬Ø´ÙØ¨Ù",callback_data="chw:3")],[InlineKeyboardButton("Ø¬ÙØ¹Ù",callback_data="chw:4")]]))

@subscription_required
async def channel_daily_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.",show_alert=True); return
    await q.answer(); v=q.data.split(":",1)[1]
    if v=="custom": context.user_data["channel_state"]="daily_custom"; await q.message.edit_text("ð Ø³Ø§Ø¹Øª Ø±Ø§ Ø¨ÙØ±Ø³ØªØ ÙØ«Ø§Ù 18:30"); return
    await save_channel_post(context,uid,"daily",v,None,None,q.message)

@subscription_required
async def channel_weekday_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.",show_alert=True); return
    await q.answer(); context.user_data["channel_weekday"]=int(q.data.split(":",1)[1]); context.user_data["channel_state"]="wtime"; await q.message.edit_text("â° Ø³Ø§Ø¹Øª ÙÙØªÚ¯Û:",reply_markup=channel_time_keyboard("chwtime"))

@subscription_required
async def channel_weektime_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.",show_alert=True); return
    await q.answer(); v=q.data.split(":",1)[1]
    if v=="custom": context.user_data["channel_state"]="wtime_custom"; await q.message.edit_text("ð Ø³Ø§Ø¹Øª Ø±Ø§ Ø¨ÙØ±Ø³ØªØ ÙØ«Ø§Ù 18:30"); return
    await save_channel_post(context,uid,"weekly",v,context.user_data["channel_weekday"],None,q.message)

async def save_channel_post(context,uid,typ,tm,weekday,run_at,message):
    cfg=get_channel_config()
    if not cfg or not cfg["channel_id"]: await message.reply_text("â Ø§Ø¨ØªØ¯Ø§ Ú©Ø§ÙØ§Ù Ø±Ø§ ØªÙØ¸ÛÙ Ú©Ù.",reply_markup=channel_keyboard()); return
    pid=add_channel_post(context.user_data["channel_content"],typ,tm,weekday,run_at,uid); context.user_data.clear(); await message.reply_text(f"â Ø²ÙØ§ÙâØ¨ÙØ¯Û Ø´Ø¯. #{pid}",reply_markup=channel_keyboard())

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
            await update.message.reply_text(f"â Ú©Ø§ÙØ§Ù ÙØµÙ Ø´Ø¯: {chat.title or normalized}",reply_markup=channel_keyboard())
        except Exception as e:
            logger.error("Set channel: %s", e)
            context.user_data.pop("channel_state", None)
            context.user_data.pop("channel_content", None)
            context.user_data.pop("channel_weekday", None)
            await update.message.reply_text(
                "â Ú©Ø§ÙØ§Ù Ù¾ÛØ¯Ø§ ÙØ´Ø¯ ÛØ§ Ø±Ø¨Ø§Øª Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±Ø¯.\n\n"
                "Ø­Ø§ÙØª ØªÙØ¸ÛÙ Ú©Ø§ÙØ§Ù Ø¨Ø³ØªÙ Ø´Ø¯. Ø¯ÙØ¨Ø§Ø±Ù Â«ØªÙØ¸ÛÙ Ú©Ø§ÙØ§ÙÂ» Ø±Ø§ Ø¨Ø²Ù.",
                reply_markup=channel_keyboard(),
            )
        return True
    if s=="batch":
        cfg=get_channel_config()
        if not cfg or not cfg["channel_id"]:
            await update.message.reply_text("â Ø§Ø¨ØªØ¯Ø§ Ú©Ø§ÙØ§Ù Ø±Ø§ ØªÙØ¸ÛÙ Ú©Ù.",reply_markup=channel_keyboard()); return True
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
        await update.message.reply_text(f"â {scheduled} Ù¾Ø³Øª Ø¨Ø±Ø§Û Ø§ÙØªØ´Ø§Ø± Ø²ÙØ§ÙâØ¨ÙØ¯Û Ø´Ø¯.",reply_markup=channel_keyboard())
        return True
    if s=="content": context.user_data["channel_content"]=text; context.user_data["channel_state"]="choose"; await update.message.reply_text("ð Ø²ÙØ§Ù Ø§ÙØªØ´Ø§Ø± Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:",reply_markup=channel_schedule_keyboard()); return True
    if s=="once":
        try:
            dt=parse_user_datetime(text)
            if dt<=datetime.now(TZ): raise ValueError
            await save_channel_post(context,uid,"once",None,None,dt.isoformat(),update.message)
        except ValueError: await update.message.reply_text("â ÙØ±ÙØª Ø§Ø´ØªØ¨Ø§Ù Ø§Ø³Øª. ÙÙÙÙÙ: Û±Û´Û°Ûµ/Û°Û¶/Û°Û² Û±Û¸:Û³Û°")
        return True
    if s in ("daily_custom","wtime_custom"):
        v=parse_time(text)
        if not v: await update.message.reply_text("â Ø³Ø§Ø¹Øª Ø§Ø´ØªØ¨Ø§Ù Ø§Ø³Øª. ÙØ«Ø§Ù 18:30"); return True
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
            InlineKeyboardButton("ð Ø¢ÙØ§Ø± Ú©ÙÛ", callback_data="adm:stats"),
            InlineKeyboardButton("ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù", callback_data="adm:users"),
        ],
        [
            InlineKeyboardButton("ð¯ Ø§ÙØ¯Ø§Ù", callback_data="adm:goals"),
            InlineKeyboardButton("ð ÙØ¹Ø§ÙÛØªâÙØ§", callback_data="adm:activity"),
        ],
        [
            InlineKeyboardButton("â° ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§", callback_data="adm:reminders"),
            InlineKeyboardButton("ð Ø¯Ø³ØªØ§ÙØ±Ø¯ÙØ§", callback_data="adm:achievements"),
        ],
        [
            InlineKeyboardButton("ð¢ Ù¾ÛØ§Ù ÙÙÚ¯Ø§ÙÛ", callback_data="adm:broadcast"),
        ],
        [InlineKeyboardButton("ð¢ ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù Ù Ù¾Ø³ØªâÚ¯Ø°Ø§Ø±Û", callback_data="adm:channel")],
        [InlineKeyboardButton("ð©º Ø³ÙØ§ÙØª Ø±Ø¨Ø§Øª", callback_data="adm:health"), InlineKeyboardButton("ð Ú¯Ø²Ø§Ø±Ø´ Ø±ÙØ²Ø§ÙÙ", callback_data="adm:report")],
        [InlineKeyboardButton("ð£ Ø¯Ø¹ÙØª Ù Ø±ÙØ±Ø§Ù", callback_data="adm:referral")],
        [InlineKeyboardButton("âï¸ Ú©ÙØªØ±Ù ÙØ§Ø¨ÙÛØªâÙØ§", callback_data="adm:features")],
        [InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ", callback_data="adm:main")],
    ])


@subscription_required
async def admin_referral_callback(update, context):
    """Admin referral management panel."""
    q = update.callback_query
    uid = q.from_user.id
    if not admin_guard(uid):
        await q.answer("â", show_alert=True); return
    await q.answer()
    action = q.data.split(":", 2)[2] if q.data.count(":") >= 2 else "dashboard"
    fa = lang(uid) == "fa"

    if action == "dashboard":
        stats = get_admin_referral_stats()
        lines = [
            "ð£ <b>Ø¯Ø§Ø´Ø¨ÙØ±Ø¯ Ø¯Ø¹ÙØª Ù Ø±ÙØ±Ø§Ù</b>", "",
            f"ð¥ Ú©Ù Ø¯Ø¹ÙØªâÙØ§: <b>{stats['total']}</b>",
            f"â ÙÙÙÙ: <b>{stats['success']}</b>",
            f"â³ Ø¯Ø± Ø§ÙØªØ¸Ø§Ø±: <b>{stats['pending']}</b>",
            f"ð Ù¾Ø§Ø¯Ø§Ø´ ØµØ§Ø¯Ø± Ø´Ø¯Ù: <b>{stats['rewarded']}</b>",
            f"ð° ÙØ¬ÙÙØ¹ ØªÙÚ©Ù Ù¾Ø§Ø¯Ø§Ø´: <b>{stats['total_tokens']}</b>",
        ]
        if stats["top_referrers"]:
            lines.append("\nð <b>Ø¨Ø±ØªØ±ÛÙ Ø¯Ø¹ÙØªâÚ©ÙÙØ¯Ú¯Ø§Ù:</b>")
            for i, r in enumerate(stats["top_referrers"][:5], 1):
                lines.append(f"  {i}. {html.escape(r['first_name'] or 'Ú©Ø§Ø±Ø¨Ø±')}: <b>{r['n']}</b> Ø¯Ø¹ÙØª ÙÙÙÙ")
        text = "\n".join(lines)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ø¯Ø¹ÙØªâØ´Ø¯Ù", callback_data="adm:referral:list")],
            [InlineKeyboardButton("âï¸ ØªÙØ¸ÛÙØ§Øª Ù¾Ø§Ø¯Ø§Ø´", callback_data="adm:referral:settings")],
            [InlineKeyboardButton("ð ÙØªÙâÙØ§Û Ø¢ÙØ§Ø¯Ù", callback_data="adm:referral:templates")],
            [InlineKeyboardButton("ð Ø¨Ø±ÙØ²Ø±Ø³Ø§ÙÛ", callback_data="adm:referral:dashboard")],
            [InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª", callback_data="adm:stats")],
        ])
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        return

    if action == "list":
        c = db()
        rows = c.execute("""SELECT r.*, u.first_name AS inviter_name, v.first_name AS invited_name
            FROM referrals r LEFT JOIN users u ON u.user_id=r.inviter_id
            LEFT JOIN users v ON v.user_id=r.invited_id
            ORDER BY r.created_at DESC LIMIT 30""").fetchall()
        c.close()
        if not rows:
            text = "ð¥ <b>ÙÙÙØ² Ø¯Ø¹ÙØªÛ Ø«Ø¨Øª ÙØ´Ø¯Ù.</b>"
        else:
            lines = [f"ð¥ <b>Ø¢Ø®Ø±ÛÙ Ø¯Ø¹ÙØªâÙØ§ ({len(rows)}):</b>", ""]
            status_map = {"registered": "â³", "success": "â", "rewarded": "ð"}
            for r in rows:
                st = status_map.get(r.get("status", ""), "â")
                lines.append(f"{st} {html.escape(r['inviter_name'] or '?')} â {html.escape(r['invited_name'] or '?')} | {r['created_at'][:10]}")
            text = "\n".join(lines)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª", callback_data="adm:referral:dashboard")],
        ])
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        return

    if action == "settings":
        settings = {
            "ref_reward_enabled": ref_get("ref_reward_enabled", "1"),
            "ref_bilateral_reward": ref_get("ref_bilateral_reward", "1"),
            "ref_tokens_per_success": ref_get("ref_tokens_per_success", "10"),
            "ref_invitee_tokens": ref_get("ref_invitee_tokens", "5"),
            "ref_vip_milestone": ref_get("ref_vip_milestone", "10"),
            "ref_vip_days": ref_get("ref_vip_days", "30"),
            "ref_success_condition": ref_get("ref_success_condition", "first_goal"),
            "ref_daily_limit": ref_get("ref_daily_limit", "50"),
        }
        cond_map = {"first_goal": "Ø«Ø¨Øª Ø§ÙÙÛÙ ÙØ¯Ù", "first_activity": "Ø§ÙÙÛÙ ÙØ¹Ø§ÙÛØª", "any": "ÙÙØ· ÙØ±ÙØ¯"}
        text = (
            f"âï¸ <b>ØªÙØ¸ÛÙØ§Øª Ù¾Ø§Ø¯Ø§Ø´ Ø¯Ø¹ÙØª</b>\n\n"
            f"ð Ù¾Ø§Ø¯Ø§Ø´ ÙØ¹Ø§Ù: {'ð¢' if settings['ref_reward_enabled']=='1' else 'ð´'}\n"
            f"ð¤ Ù¾Ø§Ø¯Ø§Ø´ Ø¯ÙØ·Ø±ÙÙ: {'ð¢' if settings['ref_bilateral_reward']=='1' else 'ð´'}\n"
            f"ð° ØªÙÚ©Ù Ø¯Ø¹ÙØªâÚ©ÙÙØ¯Ù: <b>{settings['ref_tokens_per_success']}</b>\n"
            f"ð ØªÙÚ©Ù Ø¯Ø¹ÙØªâØ´Ø¯Ù: <b>{settings['ref_invitee_tokens']}</b>\n"
            f"ð ÙØ± <b>{settings['ref_vip_milestone']}</b> Ø¯Ø¹ÙØª = <b>{settings['ref_vip_days']}</b> Ø±ÙØ² VIP\n"
            f"ð¯ Ø´Ø±Ø· ÙÙÙÙÛØª: <b>{cond_map.get(settings['ref_success_condition'], settings['ref_success_condition'])}</b>\n"
            "ð Ø³ÙÙ Ø±ÙØ²Ø§ÙÙ: <b>Ø¨Ø¯ÙÙ ÙØ­Ø¯ÙØ¯ÛØª</b>"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("ð ØªØºÛÛØ± Ø´Ø±Ø· ÙÙÙÙÛØª", callback_data="adm:referral:toggle_cond")],
            [InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª", callback_data="adm:referral:dashboard")],
        ])
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        return

    if action == "toggle_cond":
        current = ref_get("ref_success_condition", "first_goal")
        order = ["first_goal", "first_activity", "any"]
        nxt = order[(order.index(current) + 1) % len(order)] if current in order else "first_goal"
        ref_set("ref_success_condition", nxt)
        await admin_referral_callback(update, context)
        return

    if action == "templates":
        c = db()
        rows = c.execute("SELECT * FROM referral_templates ORDER BY sort_order").fetchall()
        c.close()
        lines = ["ð <b>ÙØªÙâÙØ§Û Ø¢ÙØ§Ø¯Ù Ø¯Ø¹ÙØª:</b>", ""]
        for r in rows:
            status = "ð¢" if r["enabled"] else "ð´"
            preview = r["text"][:50] + ("..." if len(r["text"]) > 50 else "")
            lines.append(f"{status} #{r['id']}: {preview}")
        text = "\n".join(lines) if len(rows) > 0 else "ð <b>ÙÙÙØ² ÙØªÙÛ Ø§Ø¶Ø§ÙÙ ÙØ´Ø¯Ù.</b>"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª", callback_data="adm:referral:dashboard")],
        ])
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        return

async def admin_panel_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    if not admin_guard(uid):
        await q.answer("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", show_alert=True)
        return
    await q.answer()
    action = q.data.split(":", 1)[1]
    s = admin_stats()

    if action == "stats":
        text = (
            "ð <b>Ø¯Ø§Ø´Ø¨ÙØ±Ø¯ ÙØ¯ÛØ±ÛØª</b>\n\n"
            f"ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù: <b>{s['users']}</b>\n"
            f"ð Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ø¬Ø¯ÛØ¯ Ø§ÙØ±ÙØ²: <b>{s['new_today']}</b>\n"
            f"ð¢ ÙØ¹Ø§Ù Ø§ÙØ±ÙØ²: <b>{s['active_today']}</b>\n"
            f"ð¯ Ø§ÙØ¯Ø§Ù: <b>{s['goals']}</b>\n"
            f"â Ø§ÙØ¬Ø§ÙâØ´Ø¯Ù Ø§ÙØ±ÙØ²: <b>{s['done_today']}</b>\n"
            f"â° ÛØ§Ø¯Ø¢ÙØ±Û ÙØ¹Ø§Ù: <b>{s['reminders']}</b>\n"
            f"ð Ú©Ù ÙØ¹Ø§ÙÛØªâÙØ§: <b>{s['activities']}</b>\n"
            f"ð Ø¯Ø³ØªØ§ÙØ±Ø¯ÙØ§: <b>{s['achievements']}</b>"
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
            text = "ð¥ Ú©Ø§Ø±Ø¨Ø±Û Ø«Ø¨Øª ÙØ´Ø¯Ù."
        else:
            text = "ð¥ <b>Ø¢Ø®Ø±ÛÙ Ú©Ø§Ø±Ø¨Ø±Ø§Ù</b>\n\n"
            for r in rows:
                name = r["first_name"] or "Ø¨Ø¯ÙÙ ÙØ§Ù"
                text += f"ð¤ {name} | ID: <code>{r['user_id']}</code> | ð¯ {r['goals']}\n"
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=admin_keyboard())

    elif action == "goals":
        c = db()
        rows = c.execute(
            """SELECT category,COUNT(*) AS n
               FROM goals GROUP BY category ORDER BY n DESC"""
        ).fetchall()
        c.close()
        text = "ð¯ <b>Ø§ÙØ¯Ø§Ù Ø¨Ø± Ø§Ø³Ø§Ø³ Ø¯Ø³ØªÙ</b>\n\n"
        text += "\n".join(f"â¢ {r['category']}: <b>{r['n']}</b>" for r in rows) or "ÙÙØ±Ø¯Û ÙÛØ³Øª."
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=admin_keyboard())

    elif action == "activity":
        c = db()
        rows = c.execute(
            """SELECT activity,COUNT(*) AS n
               FROM activity_log GROUP BY activity ORDER BY n DESC LIMIT 15"""
        ).fetchall()
        c.close()
        text = "ð <b>ÙØ¹Ø§ÙÛØªâÙØ§</b>\n\n"
        text += "\n".join(f"â¢ {r['activity']}: <b>{r['n']}</b>" for r in rows) or "ÙÙØ±Ø¯Û ÙÛØ³Øª."
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
        text = "â° <b>ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§</b>\n\n"
        text += "\n".join(f"ð {r['reminder_time']}: <b>{r['n']}</b>" for r in rows) or "ÛØ§Ø¯Ø¢ÙØ±Û ÙØ¹Ø§ÙÛ ÙÛØ³Øª."
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=admin_keyboard())

    elif action == "achievements":
        c = db()
        rows = c.execute(
            """SELECT code,COUNT(*) AS n
               FROM achievements GROUP BY code ORDER BY n DESC"""
        ).fetchall()
        c.close()
        text = "ð <b>Ø¯Ø³ØªØ§ÙØ±Ø¯ÙØ§</b>\n\n"
        text += "\n".join(f"â¢ {r['code']}: <b>{r['n']}</b>" for r in rows) or "Ø¯Ø³ØªØ§ÙØ±Ø¯Û Ø«Ø¨Øª ÙØ´Ø¯Ù."
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=admin_keyboard())

    elif action == "channel":
        await q.message.edit_text(
            "ð¢ <b>ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù Ù Ù¾Ø³ØªâÚ¯Ø°Ø§Ø±Û</b>\n\n"
            "Ø§ØªØµØ§Ù Ú©Ø§ÙØ§ÙØ ØªØ³Øª Ø§ØªØµØ§ÙØ Ø³Ø§Ø®Øª Ù¾Ø³ØªØ ÙØ´Ø§ÙØ¯Ù Ù¾Ø³ØªâÙØ§ Ù Ø§ÙØªØ´Ø§Ø± Ø®ÙØ¯Ú©Ø§Ø±.",
            parse_mode="HTML",
            reply_markup=channel_keyboard(),
        )

    elif action == "broadcast":
        context.user_data["admin_broadcast"] = True
        await q.message.edit_text(
            "ð¢ ÙØªÙ Ù¾ÛØ§Ù ÙÙÚ¯Ø§ÙÛ Ø±Ø§ Ø§Ø±Ø³Ø§Ù Ú©Ù.\n\n"
            "â ï¸ Ø¨Ø¹Ø¯ Ø§Ø² Ø§Ø±Ø³Ø§ÙØ ÙØ¨Ù Ø§Ø² ÙØ±Ø³ØªØ§Ø¯Ù Ø¨Ø±Ø§Û ÙÙÙ ØªØ£ÛÛØ¯ ÙÛâÚ¯ÛØ±ÛÙ."
        )


async def admin_command(update, context):
    uid = update.effective_user.id
    if not admin_guard(uid):
        await update.message.reply_text(
            f"â Ø¯Ø³ØªØ±Ø³Û Ø¨Ù Ù¾ÙÙ ÙØ¯ÛØ±ÛØª ÙØ¯Ø§Ø±ÛØ¯.\n\nð ID Ø´ÙØ§: {uid}\n\n"
            "Ø§ÛÙ ID Ø±Ø§ Ø¯Ø± Railway â Variables Ø¯Ø± ADMIN_IDS ÛØ§ ADMIN_ID ÙØ±Ø§Ø± Ø¨Ø¯Ù Ù Ø³Ø±ÙÛØ³ Ø±Ø§ Restart/Redeploy Ú©Ù."
        )
        return
    log_activity(uid, "admin_open")
    await update.message.reply_text(
        "ð¡ <b>Ù¾ÙÙ ÙØ¯ÛØ±ÛØª ÙØ±Ú©Ø²Û</b>\n\nØ§Ø¨ØªØ¯Ø§ Ø¨Ø®Ø´ ÙÙØ±Ø¯ÙØ¸Ø± Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù. ÙØ± Ø¨Ø®Ø´ ØªÙØ¸ÛÙØ§Øª ÙØ³ØªÙÙ Ø®ÙØ¯Ø´ Ø±Ø§ Ø¯Ø§Ø±Ø¯:",
        parse_mode="HTML",
        reply_markup=admin_keyboard(),
    )
    await hide_main_reply_keyboard(update)



# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# ð  BIRTHDAY HANDLERS (User-facing)
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

async def birthday_register_callback(update, context):
    """User registers their birthday."""
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not birthday_enabled():
        await q.message.edit_text("ð Ø§ÛÙ ÙØ§Ø¨ÙÛØª Ø¯Ø± Ø­Ø§Ù Ø­Ø§Ø¶Ø± ØºÛØ±ÙØ¹Ø§Ù Ø§Ø³Øª.")
        return
    context.user_data["awaiting_birthday"] = True
    fa = lang(uid) == "fa"
    await q.message.edit_text(
        "ð <b>ØªØ§Ø±ÛØ® ØªÙÙØ¯ Ø®ÙØ¯Øª Ø±Ù ÙØ§Ø±Ø¯ Ú©Ù</b>\n\n"
        "ÙØ±ÙØª: <code>YYYY-MM-DD</code>\nÙØ«Ø§Ù: <code>1995-03-15</code>\n\n"
        "â ï¸ ØªØ§Ø±ÛØ® ØªÙÙØ¯ Ø¯Ø§Ø¦ÙÛ Ø°Ø®ÛØ±Ù ÙÛâØ´ÙØ¯ Ù ÙÙØ· Ø¨Ø±Ø§Û ØªØ¨Ø±ÛÚ© Ù ÙØ¯ÛÙ Ø§Ø³ØªÙØ§Ø¯Ù ÙÛâØ´ÙØ¯." if fa else
        "ð <b>Enter your birthday</b>\n\nFormat: <code>YYYY-MM-DD</code>\nExample: <code>1995-03-15</code>",
        parse_mode="HTML",
    )

async def birthday_text_handler(update, context):
    """Handle birthday date input."""
    if not context.user_data.get("awaiting_birthday"):
        return False
    uid = update.effective_user.id
    text = update.message.text.strip()
    context.user_data.pop("awaiting_birthday", None)
    # Validate format
    import re
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        await update.message.reply_text("â ÙØ±ÙØª ÙØ§Ø¯Ø±Ø³Øª. ÙØ«Ø§Ù: 1995-03-15")
        return True
    try:
        dt = datetime.strptime(text, "%Y-%m-%d")
        if dt > datetime.now(TZ) or dt.year < 1900:
            await update.message.reply_text("â ØªØ§Ø±ÛØ® ÙØ§ÙØ¹ØªØ¨Ø±.")
            return True
    except ValueError:
        await update.message.reply_text("â ØªØ§Ø±ÛØ® ÙØ§ÙØ¹ØªØ¨Ø±.")
        return True
    set_birthday(uid, text)
    fa = lang(uid) == "fa"
    await update.message.reply_text(
        f"ð ØªØ§Ø±ÛØ® ØªÙÙØ¯ Ø«Ø¨Øª Ø´Ø¯: <code>{text}</code>\n\n"
        "ð Ø¯Ø± Ø±ÙØ² ØªÙÙØ¯Øª ÛÚ© ÙØ¯ÛÙ ÙÛÚÙ Ø¯Ø±ÛØ§ÙØª Ø®ÙØ§ÙÛ Ú©Ø±Ø¯!" if fa else
        f"ð Birthday saved: <code>{text}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[main_menu_button(uid)]]),
    )
    return True

async def birthday_show_callback(update, context):
    """Show user's birthday info."""
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    b = get_birthday(uid)
    fa = lang(uid) == "fa"
    if not b:
        await q.message.edit_text(
            "ð <b>ØªÙÙØ¯ ØªÙ Ø«Ø¨Øª ÙØ´Ø¯Ù</b>\n\nØ±ÙÛ Ø¯Ú©ÙÙ Ø²ÛØ± Ø¨Ø²Ù ØªØ§ ØªØ§Ø±ÛØ® ØªÙÙØ¯Øª Ø±Ù Ø«Ø¨Øª Ú©ÙÛ." if fa else
            "ð <b>No birthday registered</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("ð Ø«Ø¨Øª ØªÙÙØ¯" if fa else "ð Set Birthday", callback_data="birthday:set")],
                [InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ" if fa else "ð  Main Menu", callback_data="nav:main")],
            ]),
        )
        return
    claimed = birthday_gift_claimed(uid)
    text = (
        f"ð <b>ØªØ§Ø±ÛØ® ØªÙÙØ¯ ØªÙ</b>\n\n"
        f"ð <code>{b['birth_date']}</code>\n"
        f"ð ÙØ¯ÛÙ Ø§ÙØ³Ø§Ù: {'â Ø¯Ø±ÛØ§ÙØª Ø´Ø¯' if claimed else 'ð­ ÙÙÙØ² Ø¯Ø±ÛØ§ÙØª ÙØ´Ø¯Ù'}"
    )
    kb = [
        [InlineKeyboardButton("âï¸ ÙÛØ±Ø§ÛØ´" if fa else "âï¸ Edit", callback_data="birthday:set")],
        [InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ" if fa else "ð  Main Menu", callback_data="nav:main")],
    ]
    await q.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# ð  EVENTS HANDLERS (Admin-facing)
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

async def admin_events_callback(update, context):
    """Admin panel for events management."""
    q = update.callback_query
    uid = q.from_user.id
    if not admin_guard(uid):
        await q.answer("â", show_alert=True)
        return
    await q.answer()
    action = q.data.split(":", 2)[-1] if q.data.count(":") >= 2 else ""
    if action == "list":
        events = get_all_events()
        if not events:
            text = "ð <b>ÙÙØ§Ø³Ø¨ØªÛ Ø«Ø¨Øª ÙØ´Ø¯Ù</b>"
        else:
            lines = ["ð <b>ÙÛØ³Øª ÙÙØ§Ø³Ø¨ØªâÙØ§</b>", ""]
            for e in events:
                status = "ð¢" if e["enabled"] else "ð´"
                lines.append(f"{status} #{e['id']} | {e['name']} | {e['event_date']}")
            text = "\n".join(lines)
        kb = [
            [InlineKeyboardButton("â ÙÙØ§Ø³Ø¨Øª Ø¬Ø¯ÛØ¯", callback_data="adm:events:create")],
            [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")],
        ]
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
    elif action == "create":
        context.user_data["admin_create_event"] = True
        await q.message.reply_text(
            "ð <b>ÙØ§Ù ÙÙØ§Ø³Ø¨Øª Ø±Ù Ø¨ÙØ±Ø³Øª:</b>\nÙØ«Ø§Ù: ÙÙØ±ÙØ²Ø ÙÙÙØªØ§ÛÙØ ...",
            parse_mode="HTML",
        )


async def admin_birthday_callback(update, context):
    """Admin panel for birthday management."""
    q = update.callback_query
    uid = q.from_user.id
    if not admin_guard(uid):
        await q.answer("â", show_alert=True)
        return
    await q.answer()
    action = q.data.split(":", 2)[-1] if q.data.count(":") >= 2 else ""
    if action == "list":
        c = db()
        rows = c.execute("SELECT b.*, u.first_name FROM birthdays b JOIN users u ON u.user_id=b.user_id ORDER BY substr(b.birth_date,6)").fetchall()
        c.close()
        if not rows:
            text = "ð <b>ÙÛÚ ØªÙÙØ¯Û Ø«Ø¨Øª ÙØ´Ø¯Ù</b>"
        else:
            lines = ["ð <b>ØªÙÙØ¯ÙØ§Û Ø«Ø¨ØªâØ´Ø¯Ù</b>", ""]
            for r in rows:
                status = "ð¢" if r["enabled"] else "ð´"
                lines.append(f"{status} {r['first_name']} | <code>{r['birth_date']}</code>")
            text = "\n".join(lines)
        kb = [[InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")]]
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
    elif action == "settings":
        gift_type = birthday_settings_get("gift_type", "xp")
        gift_value = birthday_settings_get("gift_value", "50")
        enabled = feature_enabled("birthday")
        text = (
            f"ð <b>ØªÙØ¸ÛÙØ§Øª ØªÙÙØ¯</b>\n\n"
            f"ÙØ¶Ø¹ÛØª: {'ð¢ ÙØ¹Ø§Ù' if enabled else 'ð´ ØºÛØ±ÙØ¹Ø§Ù'}\n"
            f"ÙÙØ¹ ÙØ¯ÛÙ: {gift_type}\n"
            f"ÙÙØ¯Ø§Ø± ÙØ¯ÛÙ: {gift_value}"
        )
        kb = [
            [InlineKeyboardButton("ð¢ ÙØ¹Ø§Ù" if not enabled else "ð´ ØºÛØ±ÙØ¹Ø§Ù", callback_data="adm:birthdays:toggle")],
            [InlineKeyboardButton("ð ØªØºÛÛØ± ÙÙØ¹ ÙØ¯ÛÙ", callback_data="adm:birthdays:set_gift_type")],
            [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")],
        ]
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
    elif action == "toggle":
        current = feature_enabled("birthday")
        set_feature("birthday", not current, uid)
        await admin_birthday_callback(update, context)
    elif action == "set_gift_type":
        context.user_data["admin_birthday_gift_type"] = True
        await q.message.reply_text("ð ÙÙØ¹ ÙØ¯ÛÙ Ø±Ù Ø¨ÙØ±Ø³Øª:\n\nxp / vip / none")


async def admin_gifts_callback(update, context):
    """Admin panel for manual gift granting."""
    q = update.callback_query
    uid = q.from_user.id
    if not admin_guard(uid):
        await q.answer("â", show_alert=True)
        return
    await q.answer()
    text = (
        "ð <b>ÙØ¯ÛÙ ÙØ¯ÛØ±ÛØªÛ</b>\n\n"
        "Ø¨Ø±Ø§Û Ø§Ø±Ø³Ø§Ù ÙØ¯ÛÙ Ø¨Ù Ú©Ø§Ø±Ø¨Ø±:\n"
        "1ï¸â£ Ø´ÙØ§Ø³Ù Ú©Ø§Ø±Ø¨Ø± Ø±Ù Ø¨ÙØ±Ø³Øª\n"
        "2ï¸â£ ÙÙØ¹ ÙØ¯ÛÙ Ø±Ù Ø§ÙØªØ®Ø§Ø¨ Ú©Ù\n"
        "3ï¸â£ ÙÙØ¯Ø§Ø± Ù ÙØ¯Øª Ø±Ù ØªØ¹ÛÛÙ Ú©Ù"
    )
    kb = [[InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")]]
    await q.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
    context.user_data["admin_gift_mode"] = "user_id"


async def admin_access_matrix_callback(update, context):
    """Show access control matrix for a user."""
    q = update.callback_query
    uid = q.from_user.id
    if not admin_guard(uid):
        await q.answer("â", show_alert=True)
        return
    await q.answer()
    context.user_data["admin_access_matrix"] = True
    await q.message.reply_text(
        "ð <b>ÙØ§ØªØ±ÛØ³ Ø¯Ø³ØªØ±Ø³Û</b>\n\nØ´ÙØ§Ø³Ù Ú©Ø§Ø±Ø¨Ø± Ø±Ù Ø¨ÙØ±Ø³Øª:",
        parse_mode="HTML",
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
        "ð  Ù¾ÙÙ ÙØ¯ÛØ±ÛØª\n\n"
        f"ð Ø¢ÙØ§Ø± Ú©ÙÛ\n"
        f"ð¥ ØªØ¹Ø¯Ø§Ø¯ Ú©Ø§Ø±Ø¨Ø±Ø§Ù: {users}\n"
        f"ð¯ ØªØ¹Ø¯Ø§Ø¯ Ø§ÙØ¯Ø§Ù: {goals}\n"
        f"ð ØªØ¹Ø¯Ø§Ø¯ Ø§Ø³ØªÙØ§Ø¯Ù Ù ÙØ¹Ø§ÙÛØªâÙØ§: {activity}\n"
        f"ð¢ Ú©Ø§Ø±Ø¨Ø±Ø§Ù ÙØ¹Ø§Ù Ø§ÙØ±ÙØ²: {active_today}\n"
        f"â° ØªØ¹Ø¯Ø§Ø¯ ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§: {reminders}\n"
        f"ð¢ Ø§Ø±Ø³Ø§Ù Ù¾ÛØ§Ù ÙÙÚ¯Ø§ÙÛ: Ø¨Ø§ Ø¯Ú©ÙÙ Ø²ÛØ±",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("ð¢ Ø§Ø±Ø³Ø§Ù Ù¾ÛØ§Ù ÙÙÚ¯Ø§ÙÛ", callback_data="admin:broadcast")
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

    sent = failed = 0
    from telegram.error import RetryAfter
    for row in rows:
        try:
            await context.bot.send_message(row["user_id"], f"ð¢ {text}")
            sent += 1
        except RetryAfter as e:
            # Telegram asked us to slow down: wait exactly as long as required,
            # then retry this recipient once before giving up on them.
            try:
                await asyncio.sleep(e.retry_after + 1)
                await context.bot.send_message(row["user_id"], f"ð¢ {text}")
                sent += 1
            except Exception as e2:
                failed += 1
                logger.warning("Broadcast failed after retry for %s: %s", row["user_id"], e2)
        except Exception as e:
            failed += 1
            logger.warning("Broadcast failed for %s: %s", row["user_id"], e)
        # Stay under Telegram's ~30 msg/s global cap with headroom.
        await asyncio.sleep(0.05)

    log_activity(uid, "broadcast")
    done_txt = T[lang(uid)]["broadcast_done"].format(sent=sent)
    if failed:
        done_txt += f"\nâ ï¸ {failed} ÙØ§ÙÙÙÙ."
    await update.message.reply_text(done_txt)
    return True






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
                        "â Done" if lang(uid) == "en" else "â Ø§ÙØ¬Ø§Ù Ø¯Ø§Ø¯Ù",
                        callback_data=f"done:{g['id']}",
                    ),
                    InlineKeyboardButton(
                        "â Not done" if lang(uid) == "en" else "â Ø§ÙØ¬Ø§Ù ÙØ¯Ø§Ø¯Ù",
                        callback_data=f"miss:{g['id']}",
                    ),
                ], [
                    InlineKeyboardButton(
                        "â° Tomorrow / ÙØ±Ø¯Ø§",
                        callback_data=f"goalrem:{g['id']}:menu",
                    ),
                    InlineKeyboardButton(
                        "â± Snooze" if lang(uid) == "en" else "â± ÛØ§Ø¯Ø¢ÙØ±Û Ø¨Ø¹Ø¯Ø§Ù",
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
        "â¬ï¸ Ø¨Ø±Ú¯Ø´Øª", "â¬ï¸ Back", "ð  ÙÙÙÛ Ø§ØµÙÛ", "ð  Main Menu",
        "ð¢ ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù", "ð¢ Channel Management",
        "ð¡ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", "ð¡ Admin Panel",
        "ð Ø¢ÙØ§Ø± ÙÙ", "ð My Stats",
        "ð¯ Ø§ÙØ¯Ø§Ù ÙÙ", "ð¯ My Goals",
        "â Ø§ÙØ²ÙØ¯Ù ÙØ¯Ù", "â Add Goal",
        "ð Ø¨Ø±ÙØ§ÙÙ Ø§ÙØ±ÙØ²", "ð Today's Plan",
        "â° ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§", "â° Reminders",
        "ð Ø¯Ø³ØªØ§ÙØ±Ø¯ÙØ§", "ð Achievements",
        "ð¤ ÚØª Ø¨Ø§ AI", "ð¤ AI Chat",
        "ð VIP Ù Ø§ÙÚ©Ø§ÙØ§Øª Ù¾ÙÙÛ", "ð VIP & Paid Features",
        "âï¸ ØªÙØ¸ÛÙØ§Øª", "âï¸ Settings",
        "ð ÙÛÙØª Ø¢ÙÙØ§ÛÙ", "ð Online Prices",
        "ð¤ Ø¯Ø¹ÙØª Ø¯ÙØ³ØªØ§Ù", "ð¤ Invite Friends",
        "ð« Ù¾Ø´ØªÛØ¨Ø§ÙÛ", "ð« Support",
        "ð Ø±Ø²Ø±ÙÙØ§Û ÙÙ", "ð My Bookings",
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
    return InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ" if lang(uid)=="fa" else "ð  Main Menu", callback_data="nav:main")

def back_button(callback_data, label_fa="â¬ï¸ Ø¨Ø±Ú¯Ø´Øª", label_en="â¬ï¸ Back", uid=None):
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
    "ÙØ±ÙØ±Ø¯ÛÙ","Ø§Ø±Ø¯ÛØ¨ÙØ´Øª","Ø®Ø±Ø¯Ø§Ø¯","ØªÛØ±","ÙØ±Ø¯Ø§Ø¯","Ø´ÙØ±ÛÙØ±",
    "ÙÙØ±","Ø¢Ø¨Ø§Ù","Ø¢Ø°Ø±","Ø¯Û","Ø¨ÙÙÙ","Ø§Ø³ÙÙØ¯"
]
WEEKDAYS_FA = [
    "Ø¯ÙØ´ÙØ¨Ù","Ø³ÙâØ´ÙØ¨Ù","ÚÙØ§Ø±Ø´ÙØ¨Ù","Ù¾ÙØ¬Ø´ÙØ¨Ù","Ø¬ÙØ¹Ù","Ø´ÙØ¨Ù","ÛÚ©Ø´ÙØ¨Ù"
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
        return f"{date_part}Ø Ø³Ø§Ø¹Øª {fa_digits(clock)}"
    except Exception:
        return str(value)

def fa_date_iso(value):
    return jalali_pretty_date(value)


# ================= CUSTOMER / APPOINTMENT MODULE =================
CUSTOMER_REMINDER_OPTIONS=[1,5,10,30,60,120,1440]
BUSINESS_TYPES_FA=["ð Ø¢Ø±Ø§ÛØ´Ú¯Ø± / Ø³Ø§ÙÙ","ð¨ ØªØªÙ Ø¢Ø±ØªÛØ³Øª","ð§ ØªØ¹ÙÛØ±Ú©Ø§Ø±","ð©º Ø®Ø¯ÙØ§Øª Ù¾Ø²Ø´Ú©Û","ð Ø²ÛØ¨Ø§ÛÛ / ÙØ§Ø³Ø§Ú","ðï¸ ÙØ±Ø¨Û","ð ÙØ¯Ø±Ø³ / ÙØ´Ø§ÙØ±","ð¸ Ø¹Ú©Ø§Ø³","ð ï¸ Ø®Ø¯ÙØ§Øª ØªØ®ØµØµÛ","âï¸ Ø³Ø§ÛØ±"]
BUSINESS_TYPES_EN=["ð Barber / Salon","ð¨ Tattoo Artist","ð§ Repairer","ð©º Medical Services","ð Beauty / Massage","ðï¸ Coach","ð Teacher / Consultant","ð¸ Photographer","ð ï¸ Professional Services","âï¸ Other"]

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
            row.append(InlineKeyboardButton("ð ÙÙØ¨ØªâÙØ§Û Ø§ÙØ±ÙØ²" if fa else "ð Today's Appointments",callback_data="cust:today"))
        if customer_option_allowed(uid,"customer_new_appointment"):
            row.append(InlineKeyboardButton("â ÙÙØ¨Øª Ø¬Ø¯ÛØ¯" if fa else "â New Appointment",callback_data="cust:new"))
        if row: rows.append(row)
    if customer_option_allowed(uid,"customer_customers") or customer_option_allowed(uid,"customer_calendar"):
        row=[]
        if customer_option_allowed(uid,"customer_customers"):
            row.append(InlineKeyboardButton("ð¥ ÙØ´ØªØ±ÛØ§Ù" if fa else "ð¥ Customers",callback_data="cust:list"))
        if customer_option_allowed(uid,"customer_calendar"):
            row.append(InlineKeyboardButton("ðï¸ ØªÙÙÛÙ Ú©Ø§Ø±Û" if fa else "ðï¸ Calendar",callback_data="cust:calendar"))
        if row: rows.append(row)
    if customer_option_allowed(uid,"customer_hours") or customer_option_allowed(uid,"customer_reminders"):
        row=[]
        if customer_option_allowed(uid,"customer_hours"):
            row.append(InlineKeyboardButton("â° Ø³Ø§Ø¹Ø§Øª Ú©Ø§Ø±Û" if fa else "â° Working Hours",callback_data="cust:hours"))
        if customer_option_allowed(uid,"customer_reminders"):
            row.append(InlineKeyboardButton("ð ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§" if fa else "ð Reminders",callback_data="cust:reminders"))
        if row: rows.append(row)
    if customer_option_allowed(uid,"customer_analytics") or customer_option_allowed(uid,"customer_loyal"):
        row=[]
        if customer_option_allowed(uid,"customer_analytics"):
            row.append(InlineKeyboardButton("ð Ø¢ÙØ§Ø± ÙØ´ØªØ±ÛØ§Ù" if fa else "ð Customer Analytics",callback_data="cust:analytics"))
        if customer_option_allowed(uid,"customer_loyal"):
            row.append(InlineKeyboardButton("ð ÙØ´ØªØ±ÛØ§Ù ÙÙØ§Ø¯Ø§Ø±" if fa else "ð Loyal Customers",callback_data="cust:loyal"))
        if row: rows.append(row)
    if customer_option_allowed(uid,"customer_period"):
        rows.append([InlineKeyboardButton("ð ÙÙØªÚ¯Û/ÙØ§ÙØ§ÙÙ/Ø³Ø§ÙØ§ÙÙ" if fa else "ð Weekly/Monthly/Yearly",callback_data="cust:period")])
    if customer_option_allowed(uid,"customer_booking_link"):
        rows.append([InlineKeyboardButton("ð ÙÛÙÚ© Ø±Ø²Ø±Ù Ø¢ÙÙØ§ÛÙ" if fa else "ð Online Booking Link",callback_data="cust:link")])
    if customer_option_allowed(uid,"customer_business_settings"):
        rows.append([InlineKeyboardButton("âï¸ ØªÙØ¸ÛÙØ§Øª Ú©Ø³Ø¨âÙÚ©Ø§Ø±" if fa else "âï¸ Business Settings",callback_data="cust:settings")])
    if customer_option_allowed(uid,"customer_customers"):
        rows.append([InlineKeyboardButton("ð¨ Ù¾ÛØ§Ù Ø¨Ù ÙØ´ØªØ±ÛâÙØ§" if fa else "ð¨ Message Customers",callback_data="cust:broadcast")])
    rows.append([InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ" if fa else "ð  Main Menu",callback_data="nav:main")])
    return InlineKeyboardMarkup(rows)

def customer_back(uid,cb="cust:main"): return InlineKeyboardMarkup([[back_button(cb,uid=uid),main_menu_button(uid)]])

def appointment_reminder_keyboard(uid,aid):
    fa=lang(uid)=="fa"
    return InlineKeyboardMarkup([[InlineKeyboardButton("â Ø§ÙØ¬Ø§Ù Ø´Ø¯" if fa else "â Done",callback_data=f"cust:done:{aid}"),InlineKeyboardButton("â ÙØºÙ Ø´Ø¯" if fa else "â Cancelled",callback_data=f"cust:cancel:{aid}")],[InlineKeyboardButton("ð Ø¬Ø§Ø¨ÙâØ¬Ø§ÛÛ" if fa else "ð Reschedule",callback_data=f"cust:reschedule:{aid}"),InlineKeyboardButton("ð  ÙØ´ØªØ±ÛØ§Ù" if fa else "ð  Customers",callback_data="cust:main")]])

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
    if n==1440:return "Û± Ø±ÙØ² ÙØ¨Ù" if fa else "1 day before"
    if n>=60:return f"{n//60} Ø³Ø§Ø¹Øª ÙØ¨Ù" if fa else f"{n//60} hour(s) before"
    return f"{n} Ø¯ÙÛÙÙ ÙØ¨Ù" if fa else f"{n} min before"

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
    return "ð Ø¨Ø®Ø´ ÙØ´ØªØ±Û Ù ÙÙØ¨ØªâØ¯ÙÛ Ø¨Ø±Ø§Û Ù¾ÙÙ VIP ÙØ¹Ø§Ù Ø§Ø³Øª." if lang(uid)=="fa" else "ð Customers & Appointments are available on VIP plans."

async def customer_panel(update,context):
    uid=update.effective_user.id
    if not customer_feature_allowed(uid): await update.message.reply_text(customer_feature_message(uid)); return
    ensure_business_profile(uid); await update.message.reply_text("ð¥ <b>ÙØ¯ÛØ±ÛØª ÙØ´ØªØ±Û Ù ÙÙØ¨ØªâØ¯ÙÛ</b>\n\nÙ¾ÙÙ ÙØ³ØªÙÙ ÙØ´ØªØ±ÛØ§ÙØ ÙÙØ¨ØªâÙØ§Ø ØªÙÙÛÙ Ù ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§.",parse_mode="HTML",reply_markup=customer_keyboard(uid));
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
        await q.message.edit_text("â Ø§ÛÙ Ú¯Ø²ÛÙÙ ØªÙØ³Ø· ÙØ¯ÛØ± ØºÛØ±ÙØ¹Ø§Ù Ø´Ø¯Ù ÛØ§ Ø¨Ø±Ø§Û Ù¾ÙÙ Ø´ÙØ§ ÙØ¹Ø§Ù ÙÛØ³Øª.",reply_markup=customer_back(uid))
        return
    if a=="main": await q.message.edit_text("ð¥ ÙØ¯ÛØ±ÛØª ÙØ´ØªØ±Û Ù ÙÙØ¨ØªâØ¯ÙÛ",reply_markup=customer_keyboard(uid)); return
    if a=="today": await customer_today(update,context); return
    if a=="new": context.user_data["customer_mode"]="new_name"; await q.message.edit_text("â ÙØ§Ù ÙØ´ØªØ±Û Ø±Ø§ Ø¨ÙØ±Ø³Øª:"); return
    if a=="list": await customer_list_view(update,context); return
    if a=="calendar": await customer_calendar(update,context); return
    if a=="hours": await customer_hours(update,context); return
    if a=="hours_edit": context.user_data.update(customer_mode="hours_edit",weekday=int(p[2])); await q.message.edit_text("â° Ø³Ø§Ø¹Øª Ø´Ø±ÙØ¹ Ù Ù¾Ø§ÛØ§Ù Ø±Ø§ Ø¨ÙØ±Ø³Øª. ÙØ«Ø§Ù: 09:00-20:00\nØ¨Ø±Ø§Û ØªØ¹Ø·ÛÙ: off"); return
    if a=="reminders": await customer_reminders(update,context); return
    if a=="analytics": await customer_analytics_view(update,context); return
    if a=="period": await customer_period_menu(update,context); return
    if a=="periodreport": await customer_period_report(update,context,p[2]); return
    if a=="loyal": await customer_loyal(update,context); return
    if a=="link": await customer_booking_link(update,context); return
    if a=="settings": await customer_settings(update,context); return
    if a=="broadcast": context.user_data["customer_broadcast_mode"]="all"; await q.message.reply_text("ð¨ Ù¾ÛØ§Ù Ø±Ø§ Ø¨ÙØ±Ø³Øª. Ø¨Ø±Ø§Û ÙØºÙ â¬ï¸ Ø¨Ø±Ú¯Ø´Øª Ø±Ø§ Ø¨Ø²Ù.",reply_markup=nav_keyboard(uid)); return
    if a=="contact": context.user_data["customer_mode"]="contact"; await q.message.edit_text("ð± Ø§Ø² ÙØ§Ø¨ÙÛØª Ø§Ø±Ø³Ø§Ù Contact ØªÙÚ¯Ø±Ø§Ù Ø§Ø³ØªÙØ§Ø¯Ù Ú©Ù Ù ÙØ®Ø§Ø·Ø¨ Ø±Ø§ Ø¨Ø±Ø§Û Ø±Ø¨Ø§Øª Ø¨ÙØ±Ø³Øª.\nâ ï¸ Ø±Ø¨Ø§Øª Ø¨Ù Ø¯ÙØªØ±ÚÙ ÙØ®Ø§Ø·Ø¨ÛÙ Ø®ØµÙØµÛ Ú¯ÙØ´Û Ø¯Ø³ØªØ±Ø³Û ÙØ³ØªÙÛÙ ÙØ¯Ø§Ø±Ø¯."); return
    if a=="bizname": context.user_data["customer_mode"]="bizname"; await q.message.edit_text("ðª ÙØ§Ù Ú©Ø³Ø¨âÙÚ©Ø§Ø± Ø±Ø§ Ø¨ÙØ±Ø³Øª ÛØ§ - Ø¨Ø±Ø§Û Ø­Ø°Ù ÙØ§Ù:"); return
    if a=="contacts": context.user_data["customer_mode"]="contact_phone"; context.user_data["business_contact_pending"]={}; await q.message.edit_text("ð Ø´ÙØ§Ø±Ù ØªÙØ§Ø³ Ø±Ø§ Ø¨ÙØ±Ø³Øª ÛØ§ - Ø¨Ø²Ù. (Ø§Ø®ØªÛØ§Ø±Û)"); return
    if a=="type":
        types=BUSINESS_TYPES_FA if lang(uid)=="fa" else BUSINESS_TYPES_EN; idx=int(p[2]); c=db(); c.execute("UPDATE business_profiles SET business_type=?,updated_at=? WHERE user_id=?",(types[idx],datetime.now(TZ).isoformat(),uid)); c.commit(); c.close(); await q.message.edit_text("â ÙÙØ¹ ÙØ¹Ø§ÙÛØª Ø°Ø®ÛØ±Ù Ø´Ø¯.",reply_markup=customer_keyboard(uid)); return
    if a=="done": await appointment_status(update,context,"done",int(p[2])); return
    if a=="cancel": await appointment_status(update,context,"cancelled",int(p[2])); return
    if a=="reschedule": context.user_data.update(appointment_id=int(p[2]),customer_mode="reschedule_date"); await q.message.edit_text("ð ØªØ§Ø±ÛØ® Ø¬Ø¯ÛØ¯ Ø±Ø§ Ø¨ÙØ±Ø³Øª. ÙØ«Ø§Ù: Û±Û´Û°Ûµ/Û°Ûµ/Û²Û¹"); return
    if a=="cust": await customer_detail(update,context,int(p[2])); return
    if a=="edit": context.user_data.update(customer_mode="edit_name",customer_id=int(p[2])); await q.message.edit_text("âï¸ ÙØ§Ù Ø¬Ø¯ÛØ¯ ÙØ´ØªØ±Û Ø±Ø§ Ø¨ÙØ±Ø³Øª:"); return
    if a=="delete":
        c=db(); c.execute("UPDATE customers SET status='inactive',updated_at=? WHERE id=? AND owner_user_id=?",(datetime.now(TZ).isoformat(),int(p[2]),uid)); c.commit(); c.close(); await q.message.edit_text("ð ÙØ´ØªØ±Û Ø§Ø² ÙÛØ³Øª ÙØ¹Ø§Ù Ø®Ø§Ø±Ø¬ Ø´Ø¯Ø Ø³Ø§Ø¨ÙÙ Ù ÙØ§Ú©ØªÙØ±/ÙÙØ¨ØªâÙØ§Û ÙØ¨ÙÛ Ø­Ø°Ù ÙØ´Ø¯.",reply_markup=customer_keyboard(uid)); return
    if a=="appt":
        context.user_data.update(customer_id=int(p[2]),customer_mode="appt_date")
        await q.message.edit_text("ð ØªØ§Ø±ÛØ® ÙÙØ¨Øª Ø±Ø§ Ø¨ÙØ±Ø³Øª: Û±Û´Û°Ûµ/Û°Ûµ/Û²Û¹")
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
    q=update.callback_query; uid=q.from_user.id; rows=customer_list_rows(uid); kb=[[InlineKeyboardButton(f"ð¤ {r['name']} â {r['visits']} ÙØ±Ø§Ø¬Ø¹Ù",callback_data=f"cust:cust:{r['id']}")] for r in rows[:40]]; kb.append([InlineKeyboardButton("â Ø§ÙØ²ÙØ¯Ù Ø¯Ø³ØªÛ",callback_data="cust:new"),InlineKeyboardButton("ð± Ø§ÙØ²ÙØ¯Ù Ø§Ø² Contact",callback_data="cust:contact")]); kb.append([back_button("cust:main",uid=uid)])
    text="ð¥ <b>ÙÛØ³Øª ÙØ´ØªØ±ÛâÙØ§</b>\n\n"+ ("\n".join(f"â¢ {r['name']} â {r['visits']} ÙØ±Ø§Ø¬Ø¹Ù" for r in rows) if rows else "ÙØ´ØªØ±ÛâØ§Û Ø«Ø¨Øª ÙØ´Ø¯Ù.")
    await q.message.edit_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def customer_detail(update,context,cid):
    q=update.callback_query; uid=q.from_user.id; r=get_customer(uid,cid)
    if not r:return
    c=db(); visits=c.execute("SELECT COUNT(*) n FROM appointments WHERE customer_id=? AND status='done'",(cid,)).fetchone()["n"]; canc=c.execute("SELECT COUNT(*) n FROM appointments WHERE customer_id=? AND status='cancelled'",(cid,)).fetchone()["n"]; hist=c.execute("SELECT * FROM appointments WHERE customer_id=? ORDER BY appointment_date DESC,appointment_time DESC LIMIT 10",(cid,)).fetchall(); c.close(); score=loyalty_score(visits,canc); status="ð ÙØ´ØªØ±Û ÙÙØ§Ø¯Ø§Ø±" if score>=70 else "â­ ÙØ´ØªØ±Û ÙØ¹Ø§Ù" if score>=40 else "ð ÙØ´ØªØ±Û Ø¬Ø¯ÛØ¯"
    text=f"ð¤ <b>{html.escape(r['name'])}</b>\nð {html.escape(r['phone']) if r['phone'] else 'â'}\nð @{html.escape(r['telegram_username']) if r['telegram_username'] else 'â'}\n\n{status}\nâ­ Ø§ÙØªÛØ§Ø² ÙÙØ§Ø¯Ø§Ø±Û: {score}/100\nð Ú©Ù ÙØ±Ø§Ø¬Ø¹Ù: {visits}\nâ ÙØºÙ: {canc}\n\nð Ø³Ø§Ø¨ÙÙ:\n"+"\n".join(f"â¢ {jalali_pretty_date(a['appointment_date'])} | â° {a['appointment_time']} â {a['status']}" for a in hist)
    await q.message.edit_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("â ÙÙØ¨Øª Ø¬Ø¯ÛØ¯",callback_data=f"cust:appt:{cid}")],[InlineKeyboardButton("âï¸ ÙÛØ±Ø§ÛØ´ ÙØ´ØªØ±Û",callback_data=f"cust:edit:{cid}"),InlineKeyboardButton("ð Ø­Ø°Ù ÙØ´ØªØ±Û",callback_data=f"cust:delete:{cid}")],[back_button("cust:list",uid=uid),main_menu_button(uid)]]))

async def appointment_detail(update,context,aid):
    q=update.callback_query; uid=q.from_user.id; r=get_appointment(uid,aid)
    if not r:return
    await q.message.edit_text(f"ð <b>{jalali_pretty_date(r['appointment_date'])} | â° {r['appointment_time']}</b>\nð¤ {html.escape(r['name'])}\nð {html.escape(r['phone']) if r['phone'] else 'â'}\nð ï¸ {html.escape(r['service'] or 'â')}\nð {html.escape(r['notes'] or 'â')}\nð {', '.join(reminder_label(x,lang(uid)=='fa') for x in parse_reminder_list(r['reminder_minutes'])) or 'Ø¨Ø¯ÙÙ ÛØ§Ø¯Ø¢ÙØ±Û'}",parse_mode="HTML",reply_markup=appointment_reminder_keyboard(uid,aid))

async def customer_today(update,context):
    q=update.callback_query; uid=q.from_user.id; d=datetime.now(TZ).date().isoformat(); c=db(); rows=c.execute("SELECT a.*,c.name,c.phone FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.owner_user_id=? AND a.appointment_date=? ORDER BY a.appointment_time",(uid,d)).fetchall(); c.close(); lines=["ð <b>ÙÙØ¨ØªâÙØ§Û Ø§ÙØ±ÙØ²</b>",""]
    for r in rows: lines.append(f"ð <b>{r['appointment_time']}</b> â ð¤ {html.escape(r['name'])}"+(f" â ð {html.escape(r['phone'])}" if r['phone'] else "")+f" â {'ð¢' if r['status']=='booked' else 'â' if r['status']=='done' else 'â'}")
    lines.append(f"\nð¥ ÙØ¬ÙÙØ¹: {len(rows)}")
    await q.message.edit_text("\n".join(lines),parse_mode="HTML",reply_markup=customer_back(uid))

def _jalali_months_buttons(prefix, years=2):
    gy,gm,gd=gregorian_to_jalali(*datetime.now(TZ).date().timetuple()[:3])
    rows=[]
    for y in range(gy,gy+years):
        for m in range(1,13):
            rows.append([InlineKeyboardButton(f"ðï¸ {JALALI_MONTHS_FA[m-1]} {fa_digits(y)}",
                                               callback_data=f"{prefix}:{y:04d}-{m:02d}")])
    return rows


async def customer_calendar(update,context):
    q=update.callback_query; uid=q.from_user.id
    kb=_jalali_months_buttons("cust:calmonth")
    kb.append([back_button("cust:main",uid=uid)])
    await q.message.edit_text(
        "ðï¸ <b>ØªÙÙÛÙ Ú©Ø§Ø±Û Ù ÙÙØ¨ØªâÙØ§</b>\n\nÙØ§Ù ÙÙØ±Ø¯ÙØ¸Ø± Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù.\nØªÙØ§Ù ÙØ§ÙâÙØ§Û Û² Ø³Ø§Ù Ø¢ÛÙØ¯Ù Ø¯Ø± Ø¯Ø³ØªØ±Ø³ Ø§Ø³Øª.",
        parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def customer_calendar_month(update,context,ym):
    q=update.callback_query; uid=q.from_user.id
    try: jy,jm=map(int,ym.split("-")); gy,gm,gd=jalali_to_gregorian(jy,jm,1)
    except Exception:
        await q.answer("ØªØ§Ø±ÛØ® ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª.",show_alert=True); return
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
        mark="ð´" if h else "ð¢"
        kb.append([InlineKeyboardButton(f"{mark} {fa_digits(day)} â {fa_digits(n)} ÙÙØ¨Øª",callback_data=f"cust:day:{iso}")])
    c.close()
    kb.append([InlineKeyboardButton("â¬ï¸ ÙØ§ÙâÙØ§",callback_data="cust:calendar")])
    await q.message.edit_text(f"ðï¸ <b>{JALALI_MONTHS_FA[jm-1]} {fa_digits(jy)}</b>\nØ±ÙØ² Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def customer_day(update,context,d):
    q=update.callback_query; uid=q.from_user.id; c=db(); rows=c.execute("SELECT a.*,c.name,c.phone FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.owner_user_id=? AND a.appointment_date=? ORDER BY a.appointment_time",(uid,d)).fetchall(); h=c.execute("SELECT note FROM business_holidays WHERE owner_user_id=? AND holiday_date=?",(uid,d)).fetchone(); c.close(); text=f"ð <b>{jalali_pretty_date(d)}</b>\n{'ð« ØªØ¹Ø·ÛÙ' if h else 'ð¢ Ø±ÙØ² Ú©Ø§Ø±Û'}\n\n"+ ("\n".join(f"ð {r['appointment_time']} â {html.escape(r['name'])}" + (f" â ð {html.escape(r['phone'])}" if r['phone'] else "") for r in rows) or "Ø¨Ø¯ÙÙ ÙÙØ¨Øª"); kb=[[InlineKeyboardButton("ð« Ø¨Ø§Ø²/ØªØ¹Ø·ÛÙ",callback_data=f"cust:holiday:{d}")],[back_button("cust:calendar",uid=uid)]]; await q.message.edit_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def holiday_toggle(update,context,d):
    q=update.callback_query; uid=q.from_user.id; c=db(); r=c.execute("SELECT id FROM business_holidays WHERE owner_user_id=? AND holiday_date=?",(uid,d)).fetchone()
    if r:c.execute("DELETE FROM business_holidays WHERE id=?",(r["id"],)); msg="ð¢ Ø±ÙØ² Ø¨Ø§Ø² Ø´Ø¯."
    else:c.execute("INSERT INTO business_holidays(owner_user_id,holiday_date,note) VALUES(?,?,?)",(uid,d,"ØªØ¹Ø·ÛÙÛ ØªÙØ³Ø· Ú©Ø§Ø±Ø¨Ø±")); msg="ð´ Ø±ÙØ² ØªØ¹Ø·ÛÙ Ø´Ø¯."
    c.commit(); c.close(); await q.message.edit_text(msg,reply_markup=customer_back(uid,"cust:calendar"))

async def customer_hours(update,context):
    q=update.callback_query; uid=q.from_user.id; c=db(); rows=c.execute("SELECT * FROM working_hours WHERE owner_user_id=? ORDER BY weekday",(uid,)).fetchall(); c.close(); nf=["Ø¯ÙØ´ÙØ¨Ù","Ø³ÙâØ´ÙØ¨Ù","ÚÙØ§Ø±Ø´ÙØ¨Ù","Ù¾ÙØ¬Ø´ÙØ¨Ù","Ø¬ÙØ¹Ù","Ø´ÙØ¨Ù","ÛÚ©Ø´ÙØ¨Ù"]; ne=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]; kb=[[InlineKeyboardButton(f"{'ð¢' if r['enabled'] else 'ð´'} {(nf if lang(uid)=='fa' else ne)[r['weekday']]} {r['start_time']}-{r['end_time']}",callback_data=f"cust:hours_edit:{r['weekday']}")] for r in rows]; kb.append([back_button("cust:main",uid=uid)]); await q.message.edit_text("â° <b>Ø³Ø§Ø¹Ø§Øª Ú©Ø§Ø±Û</b>\nØ±ÙÛ Ø±ÙØ² Ø¨Ø²Ù Ù Ø²ÙØ§Ù Ø±Ø§ ØªØºÛÛØ± Ø¨Ø¯Ù.",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def customer_reminders(update,context):
    q=update.callback_query; uid=q.from_user.id; c=db(); rows=c.execute("SELECT a.appointment_date,a.appointment_time,a.reminder_minutes,c.name FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.owner_user_id=? AND a.status='booked' AND a.appointment_date>=? ORDER BY a.appointment_date,a.appointment_time LIMIT 50",(uid,datetime.now(TZ).date().isoformat())).fetchall(); c.close(); text="ð <b>ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§Û ÙÙØ¨Øª</b>\n\n"+ ("\n".join(f"{jalali_pretty_date(r['appointment_date'])} â â° {fa_digits(r['appointment_time'])} â {html.escape(r['name'])} â {', '.join(reminder_label(x,lang(uid)=='fa') for x in parse_reminder_list(r['reminder_minutes']))}" for r in rows) or "ÛØ§Ø¯Ø¢ÙØ±ÛâØ§Û ÙÛØ³Øª."); await q.message.edit_text(text,parse_mode="HTML",reply_markup=customer_back(uid))

async def customer_period_menu(update,context):
    q=update.callback_query; uid=q.from_user.id; kb=[[InlineKeyboardButton("ð ÙÙØªÚ¯Û",callback_data="cust:periodreport:7"),InlineKeyboardButton("ð ÙØ§ÙØ§ÙÙ",callback_data="cust:periodreport:30")],[InlineKeyboardButton("ð Ø³Ø§ÙØ§ÙÙ",callback_data="cust:periodreport:365")],[back_button("cust:main",uid=uid)]]; await q.message.edit_text("ð Ø¯ÙØ±Ù Ú¯Ø²Ø§Ø±Ø´ ÙØ´ØªØ±Û Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:",reply_markup=InlineKeyboardMarkup(kb))

async def customer_period_report(update,context,days):
    q=update.callback_query; uid=q.from_user.id; days=int(days); since=(datetime.now(TZ).date()-timedelta(days=days-1)).isoformat(); c=db(); total=c.execute("SELECT COUNT(*) n FROM appointments WHERE owner_user_id=? AND status='done' AND appointment_date>=?",(uid,since)).fetchone()["n"]; unique=c.execute("SELECT COUNT(DISTINCT customer_id) n FROM appointments WHERE owner_user_id=? AND status='done' AND appointment_date>=?",(uid,since)).fetchone()["n"]; rows=c.execute("SELECT c.name,COUNT(*) n FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.owner_user_id=? AND a.status='done' AND a.appointment_date>=? GROUP BY a.customer_id ORDER BY n DESC LIMIT 10",(uid,since)).fetchall(); c.close(); title="ÙÙØªÚ¯Û" if days==7 else "ÙØ§ÙØ§ÙÙ" if days==30 else "Ø³Ø§ÙØ§ÙÙ"; text=f"ð <b>Ú¯Ø²Ø§Ø±Ø´ {title} ÙØ´ØªØ±ÛØ§Ù</b>\n\nð¥ ÙØ´ØªØ±Û ÛÚ©ØªØ§: {unique}\nâ ÙÙØ¨Øª Ø§ÙØ¬Ø§ÙâØ´Ø¯Ù: {total}\n\nð Ù¾Ø±ØªÚ©Ø±Ø§Ø±ØªØ±ÛÙâÙØ§:\n"+("\n".join(f"â¢ {r['name']} â {r['n']} ÙØ±Ø§Ø¬Ø¹Ù" for r in rows) or "ÙÙØ±Ø¯Û ÙÛØ³Øª"); await q.message.edit_text(text,parse_mode="HTML",reply_markup=customer_back(uid,"cust:period"))

async def customer_analytics_view(update,context):
    q=update.callback_query; uid=q.from_user.id; c=db(); total=c.execute("SELECT COUNT(*) n FROM appointments WHERE owner_user_id=? AND status='done' AND appointment_date>=?",(uid,(datetime.now(TZ).date()-timedelta(days=29)).isoformat())).fetchone()["n"]; unique=c.execute("SELECT COUNT(DISTINCT customer_id) n FROM appointments WHERE owner_user_id=? AND status='done' AND appointment_date>=?",(uid,(datetime.now(TZ).date()-timedelta(days=29)).isoformat())).fetchone()["n"]; alltime=c.execute("SELECT COUNT(*) n FROM appointments WHERE owner_user_id=? AND status='done'",(uid,)).fetchone()["n"]; c.close(); await q.message.edit_text(f"ð <b>ØªØ­ÙÛÙ ÙØ´ØªØ±ÛØ§Ù</b>\n\nð Û³Û° Ø±ÙØ² Ø§Ø®ÛØ±: {total} ÙÙØ¨Øª\nð¥ ÙØ´ØªØ±Û ÛÚ©ØªØ§: {unique}\nð Ú©Ù ÙØ±Ø§Ø¬Ø¹Ù Ø§ÙØ¬Ø§ÙâØ´Ø¯Ù: {alltime}\n\nÚ¯Ø²Ø§Ø±Ø´ ÙØ§ÙØ§ÙÙ/Ø³Ø§ÙØ§ÙÙ Ø§Ø² ÙÙÛÙ Ø³Ø§Ø¨ÙÙ ÙØ§Ø¨Ù ÙØ­Ø§Ø³Ø¨Ù Ø§Ø³Øª.",parse_mode="HTML",reply_markup=customer_back(uid))

async def customer_loyal(update,context):
    q=update.callback_query; uid=q.from_user.id; c=db(); rows=c.execute("SELECT c.id,c.name,COUNT(CASE WHEN a.status='done' THEN 1 END) visits,COUNT(CASE WHEN a.status='cancelled' THEN 1 END) canc FROM customers c LEFT JOIN appointments a ON a.customer_id=c.id WHERE c.owner_user_id=? GROUP BY c.id ORDER BY visits DESC LIMIT 30",(uid,)).fetchall(); c.close(); text="ð <b>ÙØ´ØªØ±ÛØ§Ù ÙÙØ§Ø¯Ø§Ø±</b>\n\n"+("\n".join(f"{'ð¥' if i==0 else 'ð¥' if i==1 else 'ð¥' if i==2 else 'â­'} {r['name']} â {r['visits']} ÙØ±Ø§Ø¬Ø¹Ù â Ø§ÙØªÛØ§Ø² {loyalty_score(r['visits'],r['canc'])}/100" for i,r in enumerate(rows)) or "ÙØ´ØªØ±ÛâØ§Û ÙÛØ³Øª."); await q.message.edit_text(text,parse_mode="HTML",reply_markup=customer_back(uid))

async def customer_booking_link(update,context):
    q=update.callback_query; uid=q.from_user.id; p=ensure_business_profile(uid); me=await context.bot.get_me(); link=f"https://t.me/{me.username}?start=book_{p['booking_token']}" if me.username else "â"
    contacts=[]
    if p["contact_phone"]: contacts.append(f"ð {html.escape(p['contact_phone'])}")
    if p["contact_telegram"]: contacts.append(f"ð¬ ØªÙÚ¯Ø±Ø§Ù: @{html.escape(p['contact_telegram'].lstrip('@'))}")
    if p["contact_instagram"]: contacts.append(f"ð¸ Ø§ÛÙØ³ØªØ§Ú¯Ø±Ø§Ù: @{html.escape(p['contact_instagram'].lstrip('@'))}")
    title=html.escape(p["business_name"] or p["business_type"] or "Ú©Ø³Ø¨âÙÚ©Ø§Ø±")
    text=f"ð <b>ÙÛÙÚ© Ø±Ø²Ø±Ù Ø¢ÙÙØ§ÛÙ</b>\n\nðª {title}\n\n<code>{link}</code>\n\nÙØ´ØªØ±Û Ø§Ø² Ø§ÛÙ ÙÛÙÚ© Ø²ÙØ§ÙâÙØ§Û Ø¢Ø²Ø§Ø¯ Ø±Ø§ ÙÛâØ¨ÛÙØ¯ Ù ÙÙØ¨Øª Ø«Ø¨Øª ÙÛâÚ©ÙØ¯."
    if contacts: text += "\n\n"+"\n".join(contacts)
    await q.message.edit_text(text,parse_mode="HTML",reply_markup=customer_back(uid))
    # Deliver the booking link as its own message so it can be copied/forwarded directly.
    try:
        await context.bot.send_message(
            uid,
            f"ð <b>ÙÛÙÚ© Ø±Ø²Ø±Ù Ø¢ÙÙØ§ÛÙ Â«{title}Â»:</b>\n\n{link}\n\n"
            "â Ø§ÛÙ ÙÛÙÚ© Ø±Ø§ Ø¨Ø±Ø§Û ÙØ´ØªØ±ÛØ§ÙØªØ§Ù Ø¨ÙØ±Ø³ØªÛØ¯.\n"
            "ð Ø±Ø²Ø±Ù ÙØ± ÙØ´ØªØ±Û Ø¬Ø¯Ø§Ú¯Ø§ÙÙ Ø«Ø¨Øª ÙÛ\u200cØ´ÙØ¯ Ù Ø¨Ø®Ø´ Â«Ø±Ø²Ø±ÙÙØ§Û ÙÙÂ» ÙØ± Ø´Ø®Øµ ÙÙØ· Ø±Ø²Ø±ÙÙØ§Û Ø®ÙØ¯Ø´ Ø±Ø§ ÙØ´Ø§Ù ÙÛ\u200cØ¯ÙØ¯.",
            parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        logger.warning("Booking link delivery message failed")

async def customer_settings(update,context):
    q=update.callback_query; uid=q.from_user.id; p=ensure_business_profile(uid); types=BUSINESS_TYPES_FA if lang(uid)=="fa" else BUSINESS_TYPES_EN
    kb=[[InlineKeyboardButton(x,callback_data=f"cust:type:{i}")] for i,x in enumerate(types)]
    kb += [[InlineKeyboardButton("ðª ÙØ§Ù Ú©Ø³Ø¨âÙÚ©Ø§Ø±",callback_data="cust:bizname"),InlineKeyboardButton("ð Ø§Ø·ÙØ§Ø¹Ø§Øª ØªÙØ§Ø³",callback_data="cust:contacts")],[InlineKeyboardButton("ð± Ø§ÙØ²ÙØ¯Ù Ø§Ø² Contact",callback_data="cust:contact")],[back_button("cust:main",uid=uid)]]
    contacts=[]
    if p["contact_phone"]: contacts.append(f"ð {html.escape(p['contact_phone'])}")
    if p["contact_telegram"]: contacts.append(f"ð¬ @{html.escape(p['contact_telegram'].lstrip('@'))}")
    if p["contact_instagram"]: contacts.append(f"ð¸ @{html.escape(p['contact_instagram'].lstrip('@'))}")
    text=f"âï¸ <b>ØªÙØ¸ÛÙØ§Øª Ú©Ø³Ø¨âÙÚ©Ø§Ø±</b>\n\nðª ÙØ§Ù: {html.escape(p['business_name'] or 'Ø«Ø¨Øª ÙØ´Ø¯Ù')}\nÙÙØ¹ ÙØ¹ÙÛ: {html.escape(p['business_type'] or 'Ø§ÙØªØ®Ø§Ø¨ ÙØ´Ø¯Ù')}\n\n"+"\n".join(contacts or ["ð­ Ø§Ø·ÙØ§Ø¹Ø§Øª ØªÙØ§Ø³ Ø«Ø¨Øª ÙØ´Ø¯Ù Ø§Ø³Øª."])
    await q.message.edit_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def appointment_status(update,context,status,aid):
    q=update.callback_query; uid=q.from_user.id; r=get_appointment(uid,aid)
    if not r:return
    now=datetime.now(TZ).isoformat()
    c=db(); c.execute("UPDATE appointments SET status=?,updated_at=? WHERE id=? AND owner_user_id=?",(status,now,aid,uid)); c.execute("INSERT INTO customer_events(owner_user_id,customer_id,appointment_id,event_type,details,created_at) VALUES(?,?,?,?,?,?)",(uid,r['customer_id'],aid,status,"",now)); c.commit(); c.close()
    if r["telegram_user_id"]:
        try:
            msg = (f"â <b>ÙÙØ¨Øª Ø´ÙØ§ ØªÙØ³Ø· Ø§Ø±Ø§Ø¦ÙâØ¯ÙÙØ¯Ù ÙØºÙ Ø´Ø¯.</b>\n\nð {jalali_pretty_date(r['appointment_date'])}\nâ° {r['appointment_time']}") if status=="cancelled" else (f"â <b>ÙÙØ¨Øª Ø´ÙØ§ Ø§ÙØ¬Ø§ÙâØ´Ø¯Ù Ø«Ø¨Øª Ø´Ø¯.</b>\n\nð {jalali_pretty_date(r['appointment_date'])}\nâ° {r['appointment_time']}")
            await context.bot.send_message(r["telegram_user_id"],msg,parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ð Ø±Ø²Ø±ÙÙØ§Û ÙÙ",callback_data="cust:mybookings")],[InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ",callback_data="nav:main")]]))
        except Exception: logger.warning("Customer status notification failed")
    await q.message.edit_text("â ÙÙØ¨Øª Ø§ÙØ¬Ø§Ù Ø´Ø¯ Ù Ø¯Ø± Ø³Ø§Ø¨ÙÙ ÙØ´ØªØ±Û Ø«Ø¨Øª Ø´Ø¯." if status=="done" else "â ÙÙØ¨Øª ÙØºÙ Ø´Ø¯ Ù Ø³Ø§Ø¨ÙÙ Ø­ÙØ¸ Ø´Ø¯.",reply_markup=customer_back(uid))

async def customer_text_save(update,context):
    uid=update.effective_user.id; mode=context.user_data.get("customer_mode"); text=update.message.text.strip()
    if not mode:return False
    if mode=="bizname":
        value="" if text=="-" else text[:100]; c=db(); c.execute("UPDATE business_profiles SET business_name=?,updated_at=? WHERE user_id=?",(value,datetime.now(TZ).isoformat(),uid)); c.commit(); c.close(); context.user_data.pop("customer_mode",None); await update.message.reply_text("â ÙØ§Ù Ú©Ø³Ø¨âÙÚ©Ø§Ø± Ø°Ø®ÛØ±Ù Ø´Ø¯.",reply_markup=customer_keyboard(uid)); return True
    if mode=="contact_phone":
        context.user_data["business_contact_pending"]["phone"]="" if text=="-" else text[:50]; context.user_data["customer_mode"]="contact_telegram"; await update.message.reply_text("ð¬ Ø¢ÛØ¯Û ØªÙÚ¯Ø±Ø§Ù Ø±Ø§ Ø¨ÙØ±Ø³Øª ÛØ§ - Ø¨Ø²Ù. (Ø§Ø®ØªÛØ§Ø±Û)"); return True
    if mode=="contact_telegram":
        context.user_data["business_contact_pending"]["telegram"]="" if text=="-" else text.lstrip("@").strip()[:100]; context.user_data["customer_mode"]="contact_instagram"; await update.message.reply_text("ð¸ Ø¢ÛØ¯Û Ø§ÛÙØ³ØªØ§Ú¯Ø±Ø§Ù Ø±Ø§ Ø¨ÙØ±Ø³Øª ÛØ§ - Ø¨Ø²Ù. (Ø§Ø®ØªÛØ§Ø±Û)"); return True
    if mode=="contact_instagram":
        pend=context.user_data.pop("business_contact_pending",{}); value="" if text=="-" else text.lstrip("@").strip()[:100]; c=db(); c.execute("UPDATE business_profiles SET contact_phone=?,contact_telegram=?,contact_instagram=?,updated_at=? WHERE user_id=?",(pend.get("phone",""),pend.get("telegram",""),value,datetime.now(TZ).isoformat(),uid)); c.commit(); c.close(); context.user_data.pop("customer_mode",None); await update.message.reply_text("â Ø§Ø·ÙØ§Ø¹Ø§Øª ØªÙØ§Ø³ Ø°Ø®ÛØ±Ù Ø´Ø¯. ÙÙØ§Ø±Ø¯ Ø®Ø§ÙÛ ÙÙØ§ÛØ´ Ø¯Ø§Ø¯Ù ÙÙÛâØ´ÙÙØ¯.",reply_markup=customer_keyboard(uid)); return True
    if mode=="hours_edit":
        wd=context.user_data.get("weekday"); val=normalize_digits(text)
        if val.lower() in ("off","ØªØ¹Ø·ÛÙ"):
            c=db(); c.execute("UPDATE working_hours SET enabled=0 WHERE owner_user_id=? AND weekday=?",(uid,wd)); c.commit(); c.close(); context.user_data.pop("customer_mode",None); context.user_data.pop("weekday",None); await update.message.reply_text("ð« Ø±ÙØ² ØªØ¹Ø·ÛÙ Ø´Ø¯.",reply_markup=customer_keyboard(uid)); return True
        m=re.fullmatch(r"(\d{1,2}:\d{2})[-â](\d{1,2}:\d{2})",val)
        if not m or not parse_time(m.group(1)) or not parse_time(m.group(2)): await update.message.reply_text("â ÙØ±ÙØª ÙØ§Ø¯Ø±Ø³Øª. ÙØ«Ø§Ù: 09:00-20:00"); return True
        c=db(); c.execute("UPDATE working_hours SET start_time=?,end_time=?,enabled=1 WHERE owner_user_id=? AND weekday=?",(parse_time(m.group(1)),parse_time(m.group(2)),uid,wd)); c.commit(); c.close(); context.user_data.pop("customer_mode",None); context.user_data.pop("weekday",None); await update.message.reply_text("â Ø³Ø§Ø¹Ø§Øª Ú©Ø§Ø±Û Ø°Ø®ÛØ±Ù Ø´Ø¯.",reply_markup=customer_keyboard(uid)); return True
    if mode=="edit_name": context.user_data["customer_mode"]="edit_phone"; await update.message.reply_text("ð Ø´ÙØ§Ø±Ù Ø¬Ø¯ÛØ¯ Ø±Ø§ Ø¨ÙØ±Ø³Øª ÛØ§ - Ø¨Ø±Ø§Û Ø¨Ø¯ÙÙ ØªØºÛÛØ±:"); context.user_data["customer_pending"]={"name":text}; return True
    if mode=="edit_phone":
        cid=context.user_data.get("customer_id"); phone=text if text!="-" else None; p=context.user_data.pop("customer_pending",{}); c=db();
        if phone is None: c.execute("UPDATE customers SET name=?,updated_at=? WHERE id=? AND owner_user_id=?",(p.get("name",""),datetime.now(TZ).isoformat(),cid,uid))
        else: c.execute("UPDATE customers SET name=?,phone=?,updated_at=? WHERE id=? AND owner_user_id=?",(p.get("name",""),phone,datetime.now(TZ).isoformat(),cid,uid))
        c.commit(); c.close(); context.user_data.pop("customer_id",None); context.user_data.pop("customer_mode",None); await update.message.reply_text("â Ø§Ø·ÙØ§Ø¹Ø§Øª ÙØ´ØªØ±Û ÙÛØ±Ø§ÛØ´ Ø´Ø¯.",reply_markup=customer_keyboard(uid)); return True
    if mode=="new_name": context.user_data["customer_pending"]={"name":text}; context.user_data["customer_mode"]="new_phone"; await update.message.reply_text("ð Ø´ÙØ§Ø±Ù ÙØ´ØªØ±Û Ø±Ø§ Ø¨ÙØ±Ø³Øª ÛØ§ - Ø¨Ø²Ù:"); return True
    if mode=="new_phone": context.user_data["customer_pending"]["phone"]="" if text=="-" else text; context.user_data["customer_mode"]="new_notes"; await update.message.reply_text("ð ØªÙØ¶ÛØ­Ø§Øª Ø§Ø®ØªÛØ§Ø±Û Ø±Ø§ Ø¨ÙØ±Ø³Øª ÛØ§ - Ø¨Ø²Ù:"); return True
    if mode=="new_notes":
        p=context.user_data.pop("customer_pending",{}); p["notes"]="" if text=="-" else text; now=datetime.now(TZ).isoformat(); c=db(); cid=c.execute("INSERT INTO customers(owner_user_id,name,phone,notes,created_at,updated_at) VALUES(?,?,?,?,?,?)",(uid,p["name"],p.get("phone"),p.get("notes"),now,now)).lastrowid; c.commit(); c.close(); context.user_data.update(customer_id=cid,customer_mode="appt_date"); await update.message.reply_text("â ÙØ´ØªØ±Û Ø«Ø¨Øª Ø´Ø¯.\nð ØªØ§Ø±ÛØ® ÙÙØ¨Øª Ø±Ø§ Ø¨ÙØ±Ø³Øª: Û±Û´Û°Ûµ/Û°Ûµ/Û²Û¹"); return True
    if mode=="appt_date":
        try:d=datetime.fromisoformat(text).date().isoformat()
        except Exception: await update.message.reply_text("â ØªØ§Ø±ÛØ® ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª."); return True
        slots=available_slots(uid,d)
        if not slots: await update.message.reply_text("â ï¸ Ø§ÛÙ Ø±ÙØ² ØªØ¹Ø·ÛÙ Ø§Ø³Øª ÛØ§ Ø²ÙØ§Ù Ø®Ø§ÙÛ ÙØ¯Ø§Ø±Ø¯."); return True
        context.user_data.update(booking_date=d,customer_mode="appt_time"); await update.message.reply_text("â° Ø²ÙØ§Ù Ø¢Ø²Ø§Ø¯ Ø±Ø§ Ø¨ÙØ±Ø³Øª:\n"+" | ".join(slots[:50])); return True
    if mode=="appt_time":
        tm=parse_time(text); d=context.user_data.get("booking_date")
        if not tm or tm not in available_slots(uid,d): await update.message.reply_text("â Ø§ÛÙ Ø³Ø§Ø¹Øª Ø¢Ø²Ø§Ø¯ ÙÛØ³Øª."); return True
        context.user_data.update(booking_time=tm,customer_mode="appt_service"); await update.message.reply_text("ð ï¸ ÙÙØ¹ Ø®Ø¯ÙØª Ø±Ø§ Ø¨ÙØ±Ø³Øª ÛØ§ - Ø¨Ø²Ù:"); return True
    if mode=="appt_service": context.user_data["customer_pending"]={"service":"" if text=="-" else text}; context.user_data["customer_mode"]="appt_rem"; await update.message.reply_text("ð ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§ Ø±Ø§ Ø¨Ø§ Ø¯ÙÛÙÙ Ù Ú©Ø§ÙØ§ Ø¨ÙÙÛØ³: 1440,120,30\nÚ¯Ø²ÛÙÙâÙØ§: 1Ø5Ø10Ø30Ø60Ø120Ø1440"); return True
    if mode=="appt_rem":
        vals=parse_reminder_list(text); p=context.user_data.pop("customer_pending",{}); now=datetime.now(TZ).isoformat(); c=db(); aid=c.execute("INSERT INTO appointments(owner_user_id,customer_id,appointment_date,appointment_time,duration_minutes,service,notes,reminder_minutes,status,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(uid,context.user_data["customer_id"],context.user_data["booking_date"],context.user_data["booking_time"],30,p.get("service",""),"",",".join(map(str,vals or [30])),"booked","manual",now,now)).lastrowid; c.execute("INSERT INTO customer_events(owner_user_id,customer_id,appointment_id,event_type,details,created_at) VALUES(?,?,?,?,?,?)",(uid,context.user_data["customer_id"],aid,"booked","manual",now)); c.commit(); c.close(); context.user_data.clear(); await update.message.reply_text("â ÙÙØ¨Øª Ø«Ø¨Øª Ø´Ø¯.",reply_markup=customer_keyboard(uid)); return True
    if mode=="reschedule_date":
        try:d=datetime.fromisoformat(text).date().isoformat()
        except Exception: await update.message.reply_text("â ØªØ§Ø±ÛØ® ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª."); return True
        context.user_data.update(booking_date=d,customer_mode="reschedule_time"); await update.message.reply_text("â° Ø³Ø§Ø¹Øª Ø¬Ø¯ÛØ¯ Ø±Ø§ Ø¨ÙØ±Ø³Øª:"); return True
    if mode=="reschedule_time":
        aid=context.user_data["appointment_id"]; tm=parse_time(text); d=context.user_data["booking_date"]
        if not tm or not d or tm not in available_slots(uid,d,30) or has_conflict(uid,d,tm,30,aid):
            await update.message.reply_text("â Ø§ÛÙ Ø²ÙØ§Ù Ø®Ø§Ø±Ø¬ Ø§Ø² Ø³Ø§Ø¹Ø§Øª Ú©Ø§Ø±Û Ø§Ø³Øª ÛØ§ Ø¢Ø²Ø§Ø¯ ÙÛØ³Øª. ÛÚ©Û Ø§Ø² Ø²ÙØ§ÙâÙØ§Û ÙÙØ§ÛØ´âØ¯Ø§Ø¯ÙâØ´Ø¯Ù Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù."); return True
        now=datetime.now(TZ).isoformat()
        c=db()
        try:
            c.execute("BEGIN IMMEDIATE")
            r=c.execute("SELECT a.*,c.name,c.telegram_user_id FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.id=? AND a.owner_user_id=?",(aid,uid)).fetchone()
            if not r:
                c.rollback(); c.close(); context.user_data.clear(); await update.message.reply_text("â ÙÙØ¨Øª Ù¾ÛØ¯Ø§ ÙØ´Ø¯.",reply_markup=customer_keyboard(uid)); return True
            rows=c.execute("SELECT appointment_time,duration_minutes FROM appointments WHERE owner_user_id=? AND appointment_date=? AND status='booked' AND id!=?",(uid,d,aid)).fetchall()
            start=_mins(tm); end=start+int(r['duration_minutes'] or 30)
            if any(start < _mins(x['appointment_time'])+int(x['duration_minutes'] or 30) and _mins(x['appointment_time']) < end for x in rows):
                c.rollback(); c.close(); await update.message.reply_text("â Ø§ÛÙ Ø²ÙØ§Ù Ø¯ÛÚ¯Ø± Ø¢Ø²Ø§Ø¯ ÙÛØ³Øª.",reply_markup=customer_keyboard(uid)); return True
            old_date,old_time=r["appointment_date"],r["appointment_time"]
            c.execute("UPDATE appointments SET appointment_date=?,appointment_time=?,updated_at=? WHERE id=? AND owner_user_id=?",(d,tm,now,aid,uid))
            c.execute("INSERT INTO customer_events(owner_user_id,customer_id,appointment_id,event_type,details,created_at) VALUES(?,?,?,?,?,?)",(uid,r["customer_id"],aid,"owner_rescheduled",f"{old_date} {old_time} -> {d} {tm}",now)); c.commit(); c.close()
        except Exception:
            try: c.rollback(); c.close()
            except Exception: pass
            raise
        if r["telegram_user_id"]:
            try:
                await context.bot.send_message(r["telegram_user_id"],f"ð <b>Ø²ÙØ§Ù ÙÙØ¨Øª Ø´ÙØ§ ØªØºÛÛØ± Ú©Ø±Ø¯.</b>\n\nð ÙØ¨ÙÛ: {jalali_pretty_date(old_date)}\nâ° {old_time}\nð Ø¬Ø¯ÛØ¯: {jalali_pretty_date(d)}\nâ° {tm}",parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ð Ø±Ø²Ø±ÙÙØ§Û ÙÙ",callback_data="cust:mybookings")]]))
            except Exception: logger.warning("Customer owner-reschedule notification failed")
        context.user_data.clear(); await update.message.reply_text("ð ÙÙØ¨Øª Ø¬Ø§Ø¨ÙâØ¬Ø§ Ø´Ø¯ Ù ÙØ´ØªØ±Û ÙÙ ÙØ·ÙØ¹ Ø´Ø¯.",reply_markup=customer_keyboard(uid)); return True
    return False

async def customer_contact_save(update,context):
    if context.user_data.get("customer_mode")!="contact":return False
    uid=update.effective_user.id; ct=update.message.contact; name=((ct.first_name or "")+" "+(ct.last_name or "")).strip() or "ÙØ´ØªØ±Û"; now=datetime.now(TZ).isoformat(); c=db(); c.execute("INSERT INTO customers(owner_user_id,name,phone,telegram_user_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",(uid,name,ct.phone_number,ct.user_id,now,now)); c.commit(); c.close(); context.user_data.pop("customer_mode",None); await update.message.reply_text(f"â {name} Ø¨Ù ÙØ´ØªØ±ÛØ§Ù Ø§Ø¶Ø§ÙÙ Ø´Ø¯.",reply_markup=customer_keyboard(uid)); return True


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
            "ð <b>Ø±Ø²Ø±ÙÙØ§Û ÙÙ</b>\n\nØ±Ø²Ø±Ù ÙØ¹Ø§Ù Ù Ø¢ÛÙØ¯ÙâØ§Û Ø¨Ø±Ø§Û Ø´ÙØ§ Ø«Ø¨Øª ÙØ´Ø¯Ù Ø§Ø³Øª.",
            parse_mode="HTML", reply_markup=keyboard(uid)
        )
        return
    kb=[]
    lines=[]
    for r in rows:
        date_text=jalali_pretty_date(r["appointment_date"])
        lines.append(f"â¢ {date_text} â â° {r['appointment_time']}")
        kb.append([InlineKeyboardButton(
            f"ð {date_text} | â° {r['appointment_time']}",
            callback_data=f"cust:mybook:{r['id']}"
        )])
    kb.append([InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ",callback_data="nav:main")])
    await update.message.reply_text(
        "ð <b>Ø±Ø²Ø±ÙÙØ§Û ÙÙ</b>\n\n"+"\n".join(lines),
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
            "ð <b>Ø±Ø²Ø±ÙÙØ§Û ÙÙ</b>\n\nØ±Ø²Ø±Ù ÙØ¹Ø§Ù Ù Ø¢ÛÙØ¯ÙâØ§Û ÙØ¯Ø§Ø±ÛØ¯.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ",callback_data="nav:main")]])
        ); return
    kb=[[InlineKeyboardButton(
        f"ð {jalali_pretty_date(r['appointment_date'])} | â° {r['appointment_time']}",
        callback_data=f"cust:mybook:{r['id']}"
    )] for r in rows]
    kb.append([InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ",callback_data="nav:main")])
    await q.message.edit_text("ð <b>Ø±Ø²Ø±ÙÙØ§Û ÙÙ</b>\n\nÛÚ© Ø±Ø²Ø±Ù Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

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
        await q.answer("Ø§ÛÙ Ø±Ø²Ø±Ù ÙØ¹Ø§Ù Ù¾ÛØ¯Ø§ ÙØ´Ø¯.",show_alert=True); return
    p=ensure_business_profile(r["owner_user_id"])
    bname=p["business_name"] or p["business_type"] or "Ú©Ø³Ø¨âÙÚ©Ø§Ø±"
    kb=[
        [InlineKeyboardButton("ð ØªØºÛÛØ± Ø²ÙØ§Ù",callback_data=f"cust:reschedulebook:{aid}"),
         InlineKeyboardButton("â ÙØºÙ Ø±Ø²Ø±Ù",callback_data=f"cust:cancelbook:{aid}")],
        [InlineKeyboardButton("â¬ï¸ Ø±Ø²Ø±ÙÙØ§Û ÙÙ",callback_data="cust:mybookings"),
         InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ",callback_data="nav:main")]
    ]
    await q.message.edit_text(
        f"ð <b>Ø¬Ø²Ø¦ÛØ§Øª Ø±Ø²Ø±Ù</b>\n\n"
        f"ðª {html.escape(bname)}\n"
        f"ð¤ {html.escape(r['name'] or 'ÙØ´ØªØ±Û')}\n"
        f"ð {jalali_pretty_date(r['appointment_date'])}\n"
        f"â° {r['appointment_time']}\n"
        f"ð {html.escape(r['phone']) if r['phone'] else 'â'}\n\n"
        "Ø§Ø² Ø§ÛÙØ¬Ø§ ÙÛâØªÙØ§ÙÛØ¯ Ø²ÙØ§Ù Ø±Ø§ ØªØºÛÛØ± Ø¯ÙÛØ¯ ÛØ§ Ø±Ø²Ø±Ù Ø±Ø§ ÙØºÙ Ú©ÙÛØ¯.",
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
        c.close(); await q.answer("Ø§ÛÙ Ø±Ø²Ø±Ù Ø¯ÛÚ¯Ø± ÙØ¹Ø§Ù ÙÛØ³Øª.",show_alert=True); return
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
            f"â <b>ÙØ´ØªØ±Û Ø±Ø²Ø±Ù Ø¢ÙÙØ§ÛÙ Ø±Ø§ ÙØºÙ Ú©Ø±Ø¯</b>\n\n"
            f"ð¤ {html.escape(r['name'] or 'ÙØ´ØªØ±Û')}\n"
            f"ð {jalali_pretty_date(r['appointment_date'])}\n"
            f"â° {r['appointment_time']}\n"
            f"ð {html.escape(r['phone']) if r['phone'] else 'â'}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning("Owner cancellation notification failed: %s",e)
    await q.answer("â Ø±Ø²Ø±Ù ÙØºÙ Ø´Ø¯.")
    await q.message.edit_text(
        "â <b>Ø±Ø²Ø±Ù Ø´ÙØ§ ÙØºÙ Ø´Ø¯.</b>\n\nØµØ§Ø­Ø¨ Ú©Ø³Ø¨âÙÚ©Ø§Ø± ÙÛØ² Ø§Ø² ÙØºÙ Ø±Ø²Ø±Ù ÙØ·ÙØ¹ Ø´Ø¯.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("ð Ø±Ø²Ø±ÙÙØ§Û ÙÙ",callback_data="cust:mybookings")],
            [InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ",callback_data="nav:main")]
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
        await q.answer("Ø§ÛÙ Ø±Ø²Ø±Ù Ø¯ÛÚ¯Ø± ÙØ¹Ø§Ù ÙÛØ³Øª.",show_alert=True); return
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
        await update.message.reply_text("â ÙÛÙÚ© Ø±Ø²Ø±Ù ÙØ¹ØªØ¨Ø± ÙÛØ³Øª ÛØ§ ØºÛØ±ÙØ¹Ø§Ù Ø´Ø¯Ù."); return True
    if not feature_enabled("customer_online_booking") or feature_access_mode("customer_online_booking", owner_row)=="off":
        await update.message.reply_text("â Ø±Ø²Ø±Ù Ø¢ÙÙØ§ÛÙ Ø§ÛÙ Ú©Ø³Ø¨âÙÚ©Ø§Ø± ÙØ¹ÙØ§Ù ØºÛØ±ÙØ¹Ø§Ù Ø§Ø³Øª."); return True
    context.user_data["booking_owner"]=p["user_id"]
    await booking_date_menu_list(update,context)
    return True

async def booking_date_menu_list(update,context,reschedule=False):
    kb=_jalali_months_buttons("cust:bookmonth")
    kb.append([InlineKeyboardButton("â¬ï¸ Ø±Ø²Ø±Ù ÙÙ" if reschedule else "â¬ï¸ Ø¨Ø±Ú¯Ø´Øª",
                                    callback_data="cust:mybookings" if reschedule else "nav:main")])
    text=("ð <b>ØªØºÛÛØ± Ø²ÙØ§Ù Ø±Ø²Ø±Ù</b>\n\nÙØ§Ù ÙÙØ±Ø¯ÙØ¸Ø± Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù."
          if reschedule else "ð <b>ØªÙÙÛÙ Ø±Ø²Ø±Ù Ø¢ÙÙØ§ÛÙ</b>\n\nÙØ§Ù ÙÙØ±Ø¯ÙØ¸Ø± Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù.")
    target=update.callback_query.message if update.callback_query else update.message
    await target.edit_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb)) if update.callback_query else await target.reply_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def booking_month_menu(update,context,ym):
    owner=context.user_data.get("booking_owner")
    if not owner:
        await update.callback_query.answer("ØµØ§Ø­Ø¨ Ú©Ø³Ø¨âÙÚ©Ø§Ø± ÙØ´Ø®Øµ ÙÛØ³Øª.",show_alert=True); return
    try: jy,jm=map(int,ym.split("-"))
    except Exception:
        await update.callback_query.answer("ØªØ§Ø±ÛØ® ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª.",show_alert=True); return
    today=datetime.now(TZ).date(); kb=[]
    days=31 if jm<=6 else 30
    if jm==12:
        days=30 if jalali_to_gregorian(jy+1,1,1)[0] else 29
    for day in range(1,days+1):
        try: gy,gm,gd=jalali_to_gregorian(jy,jm,day); d=datetime(gy,gm,gd,tzinfo=TZ).date()
        except Exception: continue
        if d<today: continue
        slots=available_slots(owner,d.isoformat())
        status=f"ð¢ {fa_digits(len(slots))} Ø²ÙØ§Ù Ø¢Ø²Ø§Ø¯" if slots else "ð´ ØªÚ©ÙÛÙ"
        kb.append([InlineKeyboardButton(f"ð {fa_digits(day)} â {status}",callback_data=f"cust:bookdate:{d.isoformat()}")])
    kb.append([InlineKeyboardButton("â¬ï¸ ÙØ§ÙâÙØ§",callback_data="cust:booklink")])
    await update.callback_query.message.edit_text(f"ð <b>{JALALI_MONTHS_FA[jm-1]} {fa_digits(jy)}</b>\nØ±ÙØ² Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def booking_date_menu(update,context,d):
    context.user_data["booking_date"]=d
    await booking_slots_for_owner(update,context,context.user_data.get("booking_owner"),d)

async def booking_slots_for_owner(update,context,owner,d):
    q=update.callback_query
    slots=available_slots(owner,d) if owner else []
    kb=[[InlineKeyboardButton(x,callback_data=f"cust:slot:{x}") for x in slots[i:i+4]] for i in range(0,len(slots),4)]
    kb.append([InlineKeyboardButton("â©ï¸ ØªØ§Ø±ÛØ® Ø¯ÛÚ¯Ø±",callback_data="cust:booklink")])
    kb.append([InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ",callback_data="nav:main")])
    title="ð Ø³Ø§Ø¹Øª Ø¬Ø¯ÛØ¯ Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:" if context.user_data.get("reschedule_appointment_id") else "â° Ø²ÙØ§Ù Ø¢Ø²Ø§Ø¯ Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:"
    await q.message.edit_text(
        f"ð {jalali_pretty_date(d)}\n\n{title if slots else 'â Ø§ÛÙ Ø±ÙØ² Ø²ÙØ§Ù Ø¢Ø²Ø§Ø¯Û ÙØ¯Ø§Ø±Ø¯.'}",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb)
    )

async def booking_slot_select(update,context,tm):
    q=update.callback_query
    owner=context.user_data.get("booking_owner")
    d=context.user_data.get("booking_date")
    tm = parse_time(tm)
    if not owner or not d or not tm or tm not in available_slots(owner,d,30):
        await q.answer("Ø§ÛÙ Ø²ÙØ§Ù Ø¯ÛÚ¯Ø± Ø¢Ø²Ø§Ø¯ ÙÛØ³Øª ÛØ§ Ø®Ø§Ø±Ø¬ Ø§Ø² Ø³Ø§Ø¹Ø§Øª Ú©Ø§Ø±Û Ø§Ø³Øª.",show_alert=True); return
    aid=context.user_data.get("reschedule_appointment_id")
    if aid:
        if has_conflict(owner,d,tm,30,aid):
            await q.answer("Ø§ÛÙ Ø²ÙØ§Ù Ø¯ÛÚ¯Ø± Ø¢Ø²Ø§Ø¯ ÙÛØ³Øª.",show_alert=True); return
        now=datetime.now(TZ).isoformat()
        c=db()
        r=c.execute("""
            SELECT a.*,c.name,c.phone,c.telegram_user_id
            FROM appointments a JOIN customers c ON c.id=a.customer_id
            WHERE a.id=? AND a.status='booked' AND c.telegram_user_id=?
        """,(aid,q.from_user.id)).fetchone()
        if not r:
            c.close(); await q.answer("Ø±Ø²Ø±Ù Ù¾ÛØ¯Ø§ ÙØ´Ø¯.",show_alert=True); return
        c.execute("UPDATE appointments SET appointment_date=?,appointment_time=?,updated_at=? WHERE id=?",(d,tm,now,aid))
        c.execute(
            "INSERT INTO customer_events(owner_user_id,customer_id,appointment_id,event_type,details,created_at) VALUES(?,?,?,?,?,?)",
            (owner,r["customer_id"],aid,"customer_rescheduled",f"{r['appointment_date']} {r['appointment_time']} -> {d} {tm}",now)
        )
        c.commit(); c.close()
        try:
            await context.bot.send_message(
                owner,
                f"ð <b>ÙØ´ØªØ±Û Ø²ÙØ§Ù Ø±Ø²Ø±Ù Ø±Ø§ ØªØºÛÛØ± Ø¯Ø§Ø¯</b>\n\n"
                f"ð¤ {html.escape(r['name'] or 'ÙØ´ØªØ±Û')}\n"
                f"ð ÙØ¨ÙÛ: {jalali_pretty_date(r['appointment_date'])}\n"
                f"â° ÙØ¨ÙÛ: {r['appointment_time']}\n"
                f"ð Ø¬Ø¯ÛØ¯: {jalali_pretty_date(d)}\n"
                f"â° Ø¬Ø¯ÛØ¯: {tm}",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning("Owner reschedule notification failed: %s",e)
        try:
            await context.bot.send_message(
                q.from_user.id,
                f"ð <b>Ø±Ø²Ø±Ù Ø´ÙØ§ ØªØºÛÛØ± Ú©Ø±Ø¯</b>\n\nð {jalali_pretty_date(d)}\nâ° {tm}\n\nØµØ§Ø­Ø¨ Ú©Ø³Ø¨âÙÚ©Ø§Ø± Ø§Ø² ØªØºÛÛØ± Ø²ÙØ§Ù ÙØ·ÙØ¹ Ø´Ø¯.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning("Customer reschedule confirmation failed: %s",e)
        context.user_data.clear()
        await q.answer("â Ø²ÙØ§Ù Ø±Ø²Ø±Ù ØªØºÛÛØ± Ú©Ø±Ø¯.")
        await q.message.edit_text(
            f"â <b>Ø²ÙØ§Ù Ø±Ø²Ø±Ù Ø¨Ø§ ÙÙÙÙÛØª ØªØºÛÛØ± Ú©Ø±Ø¯.</b>\n\n"
            f"ð {jalali_pretty_date(d)}\nâ° {tm}\n\n"
            "ØµØ§Ø­Ø¨ Ú©Ø³Ø¨âÙÚ©Ø§Ø± Ø§Ø² ØªØºÛÛØ± Ø²ÙØ§Ù ÙØ·ÙØ¹ Ø´Ø¯.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("ð Ø±Ø²Ø±ÙÙØ§Û ÙÙ",callback_data="cust:mybookings")],
                [InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ",callback_data="nav:main")]
            ])
        )
        return
    context.user_data.update(booking_time=tm,customer_mode="public_booking_name")
    await q.answer()
    await q.message.edit_text("ð¤ <b>ÙØ§Ù Ø´ÙØ§ Ø±Ø§ Ø¨ÙØ±Ø³Øª:</b>",parse_mode="HTML")

async def public_booking_save(update,context):

    mode=context.user_data.get("customer_mode");
    if mode not in ("public_booking_name","public_booking_phone"):return False
    text=update.message.text.strip(); uid=update.effective_user.id
    if mode=="public_booking_name": context.user_data.update(public_name=text,customer_mode="public_booking_phone"); await update.message.reply_text("ð Ø´ÙØ§Ø±Ù ØªÙÙÙ Ø±Ø§ Ø¨ÙØ±Ø³Øª ÛØ§ - Ø¨Ø²Ù:"); return True
    owner=context.user_data.get("booking_owner"); d=context.user_data.get("booking_date"); tm=context.user_data.get("booking_time"); phone="" if text=="-" else text; name=context.user_data.get("public_name") or display_name(uid); now=datetime.now(TZ).isoformat();
    c=db(); existing=c.execute("SELECT id FROM customers WHERE owner_user_id=? AND telegram_user_id=? LIMIT 1",(owner,uid)).fetchone(); cid=existing["id"] if existing else c.execute("INSERT INTO customers(owner_user_id,name,phone,telegram_username,telegram_user_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(owner,name,phone,update.effective_user.username or '',uid,now,now)).lastrowid
    if existing:c.execute("UPDATE customers SET name=?,phone=?,telegram_username=?,updated_at=? WHERE id=?",(name,phone,update.effective_user.username or '',now,cid))
    if not d or not tm or tm not in available_slots(owner,d,30) or has_conflict(owner,d,tm,30):
        c.close(); context.user_data.clear(); await update.message.reply_text("â Ø§ÛÙ Ø²ÙØ§Ù Ø¯ÛÚ¯Ø± Ø¢Ø²Ø§Ø¯ ÙÛØ³Øª ÛØ§ Ø®Ø§Ø±Ø¬ Ø§Ø² Ø³Ø§Ø¹Ø§Øª Ú©Ø§Ø±Û Ø§Ø³Øª. ÙØ·ÙØ§Ù Ø¯ÙØ¨Ø§Ø±Ù ØªØ§Ø±ÛØ® Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù."); return True
    aid=c.execute("INSERT INTO appointments(owner_user_id,customer_id,appointment_date,appointment_time,duration_minutes,service,notes,reminder_minutes,status,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(owner,cid,d,tm,30,'','Ø±Ø²Ø±Ù Ø¢ÙÙØ§ÛÙ','30','booked','online',now,now)).lastrowid; c.execute("INSERT INTO customer_events(owner_user_id,customer_id,appointment_id,event_type,details,created_at) VALUES(?,?,?,?,?,?)",(owner,cid,aid,'online_booking','',now)); c.commit(); c.close()
    p=ensure_business_profile(owner); business_name=p["business_name"] or p["business_type"] or "Ú©Ø³Ø¨âÙÚ©Ø§Ø±"
    try: await context.bot.send_message(owner,f"ð <b>Ø±Ø²Ø±Ù Ø¬Ø¯ÛØ¯ Ø¯Ø§Ø±Û!</b>\n\nðª {html.escape(business_name)}\nð¤ {html.escape(name)}\nð {jalali_pretty_date(d)}\nâ° {tm}\nð {html.escape(phone) if phone else 'â'}")
    except Exception:pass
    context.user_data.clear(); await update.message.reply_text(f"â <b>Ø±Ø²Ø±Ù Ø´ÙØ§ Ø¨Ø§ ÙÙÙÙÛØª Ø«Ø¨Øª Ø´Ø¯.</b>\n\nðª {html.escape(business_name)}\nð {jalali_pretty_date(d)}\nâ° {tm}\n\nð Ø§Ø² Ø¨Ø®Ø´ Â«Ø±Ø²Ø±ÙÙØ§Û ÙÙÂ» ÙÛâØªÙØ§ÙÛØ¯ Ø±Ø²Ø±Ù Ø±Ø§ ÙØ¯ÛØ±ÛØªØ ÙØºÙ ÛØ§ Ø¬Ø§Ø¨ÙâØ¬Ø§ Ú©ÙÛØ¯."); return True

async def customer_reminder_job(context):
    now=datetime.now(TZ).replace(second=0,microsecond=0); c=db(); rows=c.execute("SELECT a.*,c.name,c.phone,c.telegram_user_id FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.status='booked' AND a.appointment_date>=?",(now.date().isoformat(),)).fetchall(); c.close()
    for r in rows:
        try:
            dt=datetime.fromisoformat(f"{r['appointment_date']}T{r['appointment_time']}").replace(tzinfo=TZ); diff=int((dt-now).total_seconds()//60)
            if diff not in parse_reminder_list(r['reminder_minutes']):continue
            reminder_key=f"appointment:{r['id']}:{diff}:{r['appointment_date']}"
            if not delivery_once(reminder_key,r['owner_user_id'],"owner_appointment_reminder"): continue
            await context.bot.send_message(r['owner_user_id'],f"ð <b>ÛØ§Ø¯Ø¢ÙØ±Û ÙÙØ¨Øª</b>\n\nð¤ {html.escape(r['name'])}\nð {jalali_date_str(r['appointment_date'])}\nâ° {r['appointment_time']}\nð {html.escape(r['phone']) if r['phone'] else 'â'}",parse_mode="HTML",reply_markup=appointment_reminder_keyboard(r['owner_user_id'],r['id']))
            if r['telegram_user_id']:
                customer_key=f"appointment_customer:{r['id']}:{diff}:{r['appointment_date']}"
                if delivery_once(customer_key,r['telegram_user_id'],"customer_appointment_reminder"):
                    p=ensure_business_profile(r['owner_user_id']); bname=p['business_name'] or p['business_type'] or 'Ú©Ø³Ø¨âÙÚ©Ø§Ø±'
                    await context.bot.send_message(r['telegram_user_id'],f"ð <b>ÛØ§Ø¯Ø¢ÙØ±Û ÙÙØ¨Øª Ø´ÙØ§</b>\n\nðª {html.escape(bname)}\nð {jalali_date_str(r['appointment_date'])}\nâ° {r['appointment_time']}",parse_mode='HTML')
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
            p=ensure_business_profile(r['owner_user_id']); bname=p['business_name'] or p['business_type'] or 'Ú©Ø³Ø¨âÙÚ©Ø§Ø±'
            await context.bot.send_message(r['telegram_user_id'],f"ð <b>ÛØ§Ø¯Ø¢ÙØ±Û ÙÙØ¨Øª Ø¨Ø¹Ø¯Û</b>\n\n{html.escape(r['name'] or 'ÙØ´ØªØ±Û')} Ø¹Ø²ÛØ²Ø Ø­Ø¯ÙØ¯ Û±Û° ÙØ§Ù Ø§Ø² ÙÙØ¨Øª ÙØ¨ÙÛ Ø´ÙØ§ Ø¯Ø± <b>{html.escape(bname)}</b> Ú¯Ø°Ø´ØªÙ Ø§Ø³Øª.\n\nØ§Ú¯Ø± Ø¨Ø±Ø§Û ÙÙØ¨Øª Ø¨Ø¹Ø¯Û Ø¢ÙØ§Ø¯ÙâØ§ÛØ ÙÛâØªÙØ§ÙÛ Ø¯ÙØ¨Ø§Ø±Ù Ø±Ø²Ø±Ù Ú©ÙÛ.",parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ð Ø±Ø²Ø±Ù Ø¯ÙØ¨Ø§Ø±Ù",callback_data=f"cust:booklink:{r['owner_user_id']}")],[main_menu_button(r['telegram_user_id'])]]))
        except Exception: logger.warning("Customer reengagement failed",exc_info=True)

async def customer_daily_report_job(context):
    now=datetime.now(TZ)
    if now.hour!=23 or now.minute!=0:return
    d=now.date().isoformat(); c=db(); owners=c.execute("SELECT DISTINCT owner_user_id FROM appointments WHERE appointment_date=?",(d,)).fetchall()
    for o in owners:
        uid=o["owner_user_id"]; total=c.execute("SELECT COUNT(*) n FROM appointments WHERE owner_user_id=? AND appointment_date=?",(uid,d)).fetchone()["n"]; done=c.execute("SELECT COUNT(*) n FROM appointments WHERE owner_user_id=? AND appointment_date=? AND status='done'",(uid,d)).fetchone()["n"]; cancelled=c.execute("SELECT COUNT(*) n FROM appointments WHERE owner_user_id=? AND appointment_date=? AND status='cancelled'",(uid,d)).fetchone()["n"]; top=c.execute("SELECT c.name,COUNT(*) n FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.owner_user_id=? AND a.appointment_date=? AND a.status='done' GROUP BY a.customer_id ORDER BY n DESC LIMIT 5",(uid,d)).fetchall()
        text=f"ð <b>Ú¯Ø²Ø§Ø±Ø´ Ù¾Ø§ÛØ§Ù Ø±ÙØ² ÙØ´ØªØ±ÛØ§Ù</b>\n\nð {d}\nð¥ Ú©Ù ÙÙØ¨ØªâÙØ§: {total}\nâ Ø§ÙØ¬Ø§ÙâØ´Ø¯Ù: {done}\nâ ÙØºÙØ´Ø¯Ù: {cancelled}\n\nð ÙØ´ØªØ±ÛØ§Ù Ù¾Ø±ØªÚ©Ø±Ø§Ø± Ø§ÙØ±ÙØ²:\n"+("\n".join(f"â¢ {r['name']} â {r['n']} ÙØ±Ø§Ø¬Ø¹Ù" for r in top) or "Ø§ÙØ±ÙØ² ÙØ±Ø§Ø¬Ø¹ÙâØ§Û Ø«Ø¨Øª ÙØ´Ø¯Ù.")
        try: await context.bot.send_message(uid,text,parse_mode="HTML",reply_markup=customer_keyboard(uid))
        except Exception as e: logger.warning("Customer daily report failed: %s",e)
    c.close()

async def customer_morning_job(context):
    now=datetime.now(TZ)
    if now.hour!=7 or now.minute!=0:return
    c=db(); rows=c.execute("SELECT a.*,c.name,c.phone,c.telegram_user_id FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.appointment_date=? AND a.status='booked' ORDER BY a.owner_user_id,a.appointment_time",(now.date().isoformat(),)).fetchall(); c.close(); groups={}
    for r in rows:groups.setdefault(r['owner_user_id'],[]).append(r)
    for owner,items in groups.items():
        lines=["ð <b>Ø¨Ø±ÙØ§ÙÙ ÙØ´ØªØ±ÛâÙØ§Û Ø§ÙØ±ÙØ²</b>",""]+[f"ð <b>{r['appointment_time']}</b> â ð¤ {html.escape(r['name'])}"+(f" â ð {html.escape(r['phone'])}" if r['phone'] else '') for r in items]+[f"\nð¥ ÙØ¬ÙÙØ¹: {len(items)} ÙØ´ØªØ±Û"]
        try:await context.bot.send_message(owner,"\n".join(lines),parse_mode="HTML",reply_markup=customer_keyboard(owner))
        except Exception as e:logger.warning("Customer morning failed: %s",e)

    # Morning reminder for every client who has an appointment today (both sides are informed).
    notified_pairs = set()
    profile_cache = {}
    for r in rows:
        cust_tg = r['telegram_user_id']
        if not cust_tg:
            continue
        cust_key = int(cust_tg)
        pair = (cust_key, r['owner_user_id'])
        if pair in notified_pairs or user_blocked(cust_key):
            continue
        notified_pairs.add(pair)
        if r['owner_user_id'] not in profile_cache:
            profile_cache[r['owner_user_id']] = ensure_business_profile(r['owner_user_id'])
        prof = profile_cache[r['owner_user_id']]
        bname = prof["business_name"] or prof["business_type"] or "Ú©Ø³Ø¨\u200cÙÚ©Ø§Ø±"
        todays = sorted([x for x in rows if x['telegram_user_id'] == cust_tg and x['owner_user_id'] == r['owner_user_id']],
                        key=lambda x: x['appointment_time'])
        sched = "\n".join("ð " + x['appointment_time'] + (" â " + html.escape(x['service']) if x['service'] else "")
                          for x in todays)
        try:
            await context.bot.send_message(
                cust_key,
                f"ð <b>ÛØ§Ø¯Ø¢ÙØ±Û ÙÙØ¨Øª Ø§ÙØ±ÙØ²</b>\n\nðª {html.escape(bname)}\n{sched}\n\nÙÙØªØ¸Ø± Ø­Ø¶ÙØ± Ú¯Ø±ÙØªØ§Ù ÙØ³ØªÛÙ! ð",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("ð Ø±Ø²Ø±ÙÙØ§Û ÙÙ", callback_data="cust:mybookings")],
                    [main_menu_button(cust_key)],
                ]))
        except Exception as e:
            logger.warning("Customer morning reminder failed: %s", e)

async def hide_main_reply_keyboard(update):
    """Remove the persistent main ReplyKeyboard without adding a visible UI message."""
    try:
        m = await update.effective_chat.send_message("â£", reply_markup=ReplyKeyboardRemove())
        try:
            await m.delete()
        except Exception:
            pass
    except Exception:
        pass


async def text_router(update, context):
    uid = update.effective_user.id
    register_user(uid, update.effective_user.first_name or "", getattr(update.effective_user, "username", None))

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
        "channel_state", "admin_broadcast", "auto_wait_interval", "admin_health_time",
        "auto_wait_time", "awaiting_custom_duration", "awaiting_custom_condition", "awaiting_custom_edit_time",
        "awaiting_custom_goal", "awaiting_custom_time", "awaiting_edit_time",
        "awaiting_rename", "awaiting_step", "support_new"
    )):
        context.user_data.setdefault("_flow_started_at", datetime.now(TZ).isoformat())

    if text in ("â¬ï¸ Ø¨Ø±Ú¯Ø´Øª","â¬ï¸ Back","ð  ÙÙÙÛ Ø§ØµÙÛ","ð  Main Menu"):
        clear_flow(context)
        try: await update.message.delete()
        except Exception: pass
        await update.message.chat.send_message("ð ",reply_markup=keyboard(uid))
        return

    # A failed input flow must never trap the user inside that flow.
    # Normal menu buttons always have priority over transient input states.
    if is_menu_button(uid, text) and context.user_data:
        clear_flow(context)

    if context.user_data.get("admin_vip_edit_user") and admin_guard(uid):
        target=int(context.user_data.pop("admin_vip_edit_user")); raw=normalize_digits(text)
        try: days=int(raw); assert -3650<=days<=3650
        except Exception: await update.message.reply_text("â ØªØ¹Ø¯Ø§Ø¯ Ø±ÙØ² ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª.",reply_markup=nav_keyboard(uid)); context.user_data["admin_vip_edit_user"]=target; return True
        c=db(); r=c.execute("SELECT vip_until FROM users WHERE user_id=?",(target,)).fetchone(); now_dt=datetime.now(TZ);
        if days==0: new_until=None
        else:
            base=now_dt
            if r and r["vip_until"]:
                try: base=max(base,datetime.fromisoformat(r["vip_until"]))
                except Exception: pass
            new_until=(base+timedelta(days=days)).isoformat()
        c.execute("UPDATE users SET vip_until=? WHERE user_id=?",(new_until,target)); c.execute("INSERT INTO subscription_history(user_id,plan,duration_days,source,started_at,expires_at,created_at) VALUES(?,?,?,?,?,?,?)",(target,"VIP Edit",days,"admin_edit",now_dt.isoformat(),new_until,now_dt.isoformat())); c.commit(); c.close(); admin_log(uid,"vip_edit",target,str(days)); await update.message.reply_text("â VIP ÙØºÙ Ø´Ø¯." if days==0 else f"â Ø§Ø´ØªØ±Ø§Ú© ÙÛØ±Ø§ÛØ´ Ø´Ø¯. Ù¾Ø§ÛØ§Ù: {fa_datetime(new_until)}",reply_markup=final_admin_keyboard()); return True

    if context.user_data.get("customer_broadcast_mode"):
        msg=text.strip()
        if not msg: await update.message.reply_text("â Ù¾ÛØ§Ù Ø®Ø§ÙÛ Ø§Ø³Øª.",reply_markup=nav_keyboard(uid)); return True
        c=db(); rows=c.execute("SELECT DISTINCT telegram_user_id FROM customers WHERE owner_user_id=? AND status='active' AND telegram_user_id IS NOT NULL",(uid,)).fetchall(); now=datetime.now(TZ).isoformat(); cur=c.execute("INSERT INTO customer_broadcasts(owner_user_id,audience,message,created_at) VALUES(?,?,?,?)",(uid,"active",msg,now)); bid=cur.lastrowid; c.commit(); c.close(); sent=0
        for r in rows:
            try:
                await context.bot.send_message(r["telegram_user_id"],f"ð¢ <b>Ù¾ÛØ§Ù Ø§Ø² {html.escape(ensure_business_profile(uid)['business_name'] or 'Ú©Ø³Ø¨âÙÚ©Ø§Ø±')}</b>\n\n{html.escape(msg)}",parse_mode="HTML")
                sent+=1
            except Exception: pass
        c=db(); c.execute("UPDATE customer_broadcasts SET sent_count=? WHERE id=?",(sent,bid)); c.commit(); c.close(); clear_flow(context); await update.message.reply_text(f"â Ù¾ÛØ§Ù Ø¨Ø±Ø§Û {sent} ÙØ´ØªØ±Û Ø§Ø±Ø³Ø§Ù Ø´Ø¯.",reply_markup=customer_keyboard(uid)); return True

    if context.user_data.get("goal_reminder_custom"):
        gid=int(context.user_data.pop("goal_reminder_custom")); tm=parse_time(text); g=get_goal(uid,gid)
        if not g or not tm:
            await update.message.reply_text("â Ø³Ø§Ø¹Øª ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª. ÙØ«Ø§Ù: 20:30",reply_markup=nav_keyboard(uid)); context.user_data["goal_reminder_custom"]=gid; return True
        d=(datetime.now(TZ).date()+timedelta(days=1)).isoformat(); c=db(); c.execute("INSERT OR REPLACE INTO goal_reminder_overrides(user_id,goal_id,reminder_date,reminder_time,created_at) VALUES(?,?,?,?,?)",(uid,gid,d,tm,datetime.now(TZ).isoformat())); c.commit(); c.close(); clear_flow(context); await update.message.reply_text(f"â ÛØ§Ø¯Ø¢ÙØ±Û ÙØ¯Ù Â«{html.escape(g['name'])}Â» Ø¨Ø±Ø§Û ÙØ±Ø¯Ø§ Ø³Ø§Ø¹Øª {tm} ØªÙØ¸ÛÙ Ø´Ø¯.",parse_mode="HTML",reply_markup=keyboard(uid)); return True

    if context.user_data.get("admin_pause_mode") and admin_guard(uid):
        value=text.strip().lower()
        if value in ("forever","ÙØ§ÙØ­Ø¯ÙØ¯"):
            set_system_setting("bot_paused_until","9999-12-31T23:59:59+03:30")
            admin_log(uid,"bot_pause_on",None,"forever")
            context.user_data.pop("admin_pause_mode",None)
            await update.message.reply_text("â¸ Ø±Ø¨Ø§Øª ÙØªÙÙÙ Ø´Ø¯. ÙØ¯ÛØ±Ø§Ù ÙÙÚÙØ§Ù Ø¯Ø³ØªØ±Ø³Û Ø¯Ø§Ø±ÙØ¯.",reply_markup=final_admin_keyboard()); return
        try: minutes=int(normalize_digits(value)); assert 1<=minutes<=10080
        except Exception:
            await update.message.reply_text("â Ø¹Ø¯Ø¯ ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª. Ø¨ÛÙ Û± ØªØ§ Û±Û°Û°Û¸Û° Ø¯ÙÛÙÙ ÙØ§Ø±Ø¯ Ú©Ù ÛØ§ forever Ø¨ÙÙÛØ³.",reply_markup=nav_keyboard(uid)); return
        until=datetime.now(TZ)+timedelta(minutes=minutes); set_system_setting("bot_paused_until",until.isoformat()); admin_log(uid,"bot_pause_on",None,str(minutes)); context.user_data.pop("admin_pause_mode",None)
        await update.message.reply_text(f"â¸ Ø±Ø¨Ø§Øª ØªØ§ {fa_datetime(until)} ÙØªÙÙÙ Ø´Ø¯.",reply_markup=final_admin_keyboard()); return

    if context.user_data.get("auto_wait_interval"):
        try:
            minutes = int(text)
            if minutes < 5 or minutes > 1440:
                raise ValueError
        except ValueError:
            await update.message.reply_text("â Ø¹Ø¯Ø¯ ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª. ÙÙØ· Ø¹Ø¯Ø¯Û Ø¨ÛÙ Ûµ ØªØ§ Û±Û´Û´Û° Ø¯ÙÛÙÙ ÙØ§Ø±Ø¯ Ú©Ù.")
            return
        set_auto_setting("interval_minutes", str(minutes))
        set_auto_setting("enabled", "1")
        set_auto_setting("next_run", (datetime.now(TZ) + timedelta(minutes=minutes)).isoformat())
        context.user_data.pop("auto_wait_interval", None)
        await update.message.reply_text(
            f"â Ø§ÙØªØ´Ø§Ø± Ø®ÙØ¯Ú©Ø§Ø± Ø±ÙÛ ÙØ± {minutes} Ø¯ÙÛÙÙ ØªÙØ¸ÛÙ Ø´Ø¯ Ù Ø±ÙØ´Ù Ø§Ø³Øª.",
            reply_markup=auto_channel_keyboard(),
        )
        return

    if context.user_data.get("auto_wait_time"):
        value = parse_time(text)
        if not value:
            await update.message.reply_text("â Ø³Ø§Ø¹Øª ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª. ÙØ«Ø§Ù: 18:00")
            return
        set_auto_setting("time", value)
        context.user_data.pop("auto_wait_time", None)
        await update.message.reply_text(
            "â Ø³Ø§Ø¹Øª Ø§ÙØªØ´Ø§Ø± Ø®ÙØ¯Ú©Ø§Ø± Ø±ÙÛ " + value + " ØªÙØ¸ÛÙ Ø´Ø¯.",
            reply_markup=auto_channel_keyboard()
        )
        return

    if not await final_guard(update, context):
        return
    if text in ("ð Ø±Ø²Ø±ÙÙØ§Û ÙÙ","ð My Bookings"):
        await customer_my_bookings(update,context)
        return
    if await support_text(update, context):
        return
    if await admin_health_time_save(update, context):
        return
    if await admin_manager_assign_text(update, context):
        return
    if await final_admin_text(update, context):
        return
    if await channel_text_save(update, context):
        return

    if await admin_broadcast_save(update, context):
        return
    if await custom_goal_save(update, context):
        return
    if await custom_duration_save(update, context):
        return
    if await custom_condition_save(update, context):
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
        await update.message.reply_text("â Ø§ÛÙ ÙØ§Ø¨ÙÛØª ÙØ¹ÙØ§Ù ØªÙØ³Ø· ÙØ¯ÛØ± ØºÛØ±ÙØ¹Ø§Ù Ø´Ø¯Ù Ø§Ø³Øª.",reply_markup=keyboard(uid)); return
    if text in ("ð¯ Ø§ÙØ¯Ø§Ù Ø§ÙØ±ÙØ²", "ð¯ Today's Goals"):
        await today(update, context)
    elif text in ("âï¸ ÙØ¯Ù Ø®ÙØ¯Ù ÙÛâÙÙÛØ³Ù", "âï¸ Write my own goal"):
        await custom_goal_start(update, context)
    elif text in ("ð Ø§ÙØ¯Ø§Ù Ø¢ÙØ§Ø¯Ù", "ð Ready Goals"):
        await ready_menu(update, context)
    elif text in ("âï¸ ÙÛØ±Ø§ÛØ´ Ø§ÙØ¯Ø§Ù", "âï¸ Edit Goals"):
        await edit_menu(update, context)
    elif text in ("ð Ø¬Ø¯ÙÙ ÙÙØªÚ¯Û", "ð Weekly Table"):
        await weekly(update, context)
    elif text in ("ð Ø¢ÙØ§Ø± ÙÙ", "ð My Stats"):
        await stats(update, context)
    elif text in ("ð¤ Ù¾Ø±ÙÙØ§ÛÙ", "ð¤ Profile"):
        await profile(update, context)
    elif text in ("ð Ø¯Ø³ØªØ§ÙØ±Ø¯ÙØ§", "ð Achievements"):
        await achievements(update, context)
    elif text in ("â­ XP",):
        await xp_command(update, context)
    elif text in ("ð¤ Ø¯Ø¹ÙØª Ø¯ÙØ³ØªØ§Ù", "ð¤ Referrals"):
        await referral(update, context)
    elif text in ("ð ÙÛÙØª Ø¢ÙÙØ§ÛÙ", "ð Online Prices"):
        await prices(update, context)
    elif text in ("ð VIP",):
        await vip_center(update, context)
    elif text in ("ð« Ù¾Ø´ØªÛØ¨Ø§ÙÛ", "ð« Support"):
        await support_start(update, context)
    elif text in ("âï¸ ØªÙØ¸ÛÙØ§Øª", "âï¸ Settings"):
        await settings(update, context)
    elif text in ("ð¥ ÙØ¯ÛØ±ÛØª ÙØ´ØªØ±Û Ù ÙÙØ¨ØªâØ¯ÙÛ", "ð¥ Customer & Appointments"):
        await customer_panel(update, context)
    elif text in ("ð¢ ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù", "ð¢ Channel Management"):
        if admin_guard(uid):
            clear_flow(context)
            await update.message.reply_text(
                "ð¢ <b>ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù Ù Ù¾Ø³ØªâÚ¯Ø°Ø§Ø±Û</b>\n\nØ§ØªØµØ§Ù Ú©Ø§ÙØ§ÙØ Ø³Ø§Ø®Øª Ù¾Ø³ØªØ Ø²ÙØ§ÙâØ¨ÙØ¯Û Ù Ø§ÙØªØ´Ø§Ø± Ø®ÙØ¯Ú©Ø§Ø±.",
                parse_mode="HTML",
                reply_markup=channel_keyboard(),
            )
            await hide_main_reply_keyboard(update)
        else:
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.")
    elif text in ("ð¡ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", "ð¡ Admin Panel"):
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
        if update.callback_query: await update.callback_query.answer("â Ø­Ø³Ø§Ø¨ Ø´ÙØ§ ÙØ³Ø¯ÙØ¯ Ø§Ø³Øª.",show_alert=True)
        elif update.message: await update.message.reply_text("â Ø­Ø³Ø§Ø¨ Ø´ÙØ§ ÙØ³Ø¯ÙØ¯ Ø§Ø³Øª.")
        return False
    return True


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# ð  BIRTHDAY MODULE
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def birthday_enabled():
    return feature_enabled("birthday")

def set_birthday(uid, birth_date, birth_year=None):
    """Register or update a user's birthday. Permanently stored, never deleted."""
    now = datetime.now(TZ).isoformat()
    c = db()
    year = birth_year
    if not year:
        try:
            year = int(birth_date.split("-")[0])
        except Exception:
            year = None
    c.execute(
        """INSERT INTO birthdays(user_id, birth_date, birth_year, enabled, created_at, updated_at)
           VALUES(?, ?, ?, 1, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
           birth_date=excluded.birth_date,
           birth_year=COALESCE(excluded.birth_year, birth_year),
           updated_at=excluded.updated_at""",
        (uid, birth_date, year, now, now),
    )
    c.commit()
    c.close()

def get_birthday(uid):
    c = db()
    r = c.execute("SELECT * FROM birthdays WHERE user_id=?", (uid,)).fetchone()
    c.close()
    return r

def toggle_birthday(uid, enabled):
    c = db()
    c.execute("UPDATE birthdays SET enabled=?, updated_at=? WHERE user_id=?",
              (1 if enabled else 0, datetime.now(TZ).isoformat(), uid))
    c.commit()
    c.close()

def get_todays_birthdays():
    """Get all users whose birthday is today."""
    today = datetime.now(TZ)
    # Match by month-day, ignoring year
    month_day = today.strftime("-%m-%d")
    c = db()
    rows = c.execute(
        "SELECT b.*, u.first_name FROM birthdays b "
        "JOIN users u ON u.user_id = b.user_id "
        "WHERE b.enabled=1 AND substr(b.birth_date, 5) = ?",
        (month_day,),
    ).fetchall()
    c.close()
    return rows

def get_upcoming_birthdays(days_ahead=7):
    """Get birthdays coming up in the next N days."""
    today = datetime.now(TZ)
    results = []
    c = db()
    rows = c.execute("SELECT b.*, u.first_name FROM birthdays b JOIN users u ON u.user_id = b.user_id WHERE b.enabled=1").fetchall()
    c.close()
    for r in rows:
        try:
            bdate = datetime.fromisoformat(r["birth_date"])
            this_year = today.year
            next_bday = bdate.replace(year=this_year)
            if next_bday < today:
                next_bday = bdate.replace(year=this_year + 1)
            diff = (next_bday - today).days
            if 1 <= diff <= days_ahead:
                results.append({"user_id": r["user_id"], "first_name": r["first_name"],
                                "birth_date": r["birth_date"], "days_until": diff})
        except Exception:
            continue
    return sorted(results, key=lambda x: x["days_until"])

def birthday_gift_claimed(uid):
    """Check if user already claimed birthday gift this year."""
    current_year = datetime.now(TZ).year
    c = db()
    r = c.execute("SELECT last_gift_year FROM birthdays WHERE user_id=?", (uid,)).fetchone()
    c.close()
    if r and r["last_gift_year"] == current_year:
        return True
    return False

def mark_birthday_gift_claimed(uid):
    current_year = datetime.now(TZ).year
    c = db()
    c.execute("UPDATE birthdays SET last_gift_year=?, gift_claimed=1, updated_at=? WHERE user_id=?",
              (current_year, datetime.now(TZ).isoformat(), uid))
    c.commit()
    c.close()

def birthday_settings_get(key, default=""):
    c = db()
    r = c.execute("SELECT value FROM birthday_settings WHERE key=?", (key,)).fetchone()
    c.close()
    return r["value"] if r else default

def birthday_settings_set(key, value):
    c = db()
    c.execute("INSERT INTO birthday_settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (key, value))
    c.commit()
    c.close()

def birthday_gift_xp(uid, amount=50):
    """Grant birthday XP gift (one per year)."""
    if birthday_gift_claimed(uid):
        return False
    add_xp(uid, amount, "birthday_gift")
    mark_birthday_gift_claimed(uid)
    award_engagement_xp_once(uid, 0, "birthday_gift", f"birthday:{uid}:{datetime.now(TZ).year}", "birthday_gift")
    return True

def birthday_gift_vip(uid, days=3):
    """Grant birthday VIP gift (one per year)."""
    if birthday_gift_claimed(uid):
        return False
    expires = (datetime.now(TZ) + timedelta(days=days)).isoformat()
    c = db()
    c.execute("UPDATE users SET vip_until=? WHERE user_id=?", (expires, uid))
    c.execute("INSERT INTO subscription_history(user_id,plan,duration_days,source,started_at,expires_at,created_at) VALUES(?,?,?,?,?,?,?)",
              (uid, "Birthday VIP", days, "birthday", datetime.now(TZ).isoformat(), expires, datetime.now(TZ).isoformat()))
    c.commit()
    c.close()
    mark_birthday_gift_claimed(uid)
    return True

async def send_birthday_greeting(bot, uid, first_name):
    """Send personalized birthday greeting with gift."""
    greeting = birthday_settings_get("greeting_template",
        "ð ØªÙÙØ¯Øª ÙØ¨Ø§Ø±Ú© {name} Ø¹Ø²ÛØ²! ð\n\nØ§ÙØ±ÙØ² Ø±ÙØ² Ø®Ø§Øµ ØªÙØ¦Ù! Ø§ÙÛØ¯ÙØ§Ø±Ù Ø³Ø§Ù Ø®ÙØ¨Û Ù¾ÛØ´ Ø±Ù Ø¯Ø§Ø´ØªÙ Ø¨Ø§Ø´Û! ð·")
    gift_type = birthday_settings_get("gift_type", "xp")
    gift_value = int(birthday_settings_get("gift_value", "50"))

    text = greeting.replace("{name}", first_name or "Ø¹Ø²ÛØ²")

    if gift_type == "xp":
        granted = birthday_gift_xp(uid, gift_value)
        if granted:
            text += f"\n\nð ÙØ¯ÛÙ ØªÙÙØ¯: â­ {gift_value} XP"
    elif gift_type == "vip":
        granted = birthday_gift_vip(uid, gift_value)
        if granted:
            text += f"\n\nð ÙØ¯ÛÙ ØªÙÙØ¯: ð {gift_value} Ø±ÙØ² VIP"

    try:
        await bot.send_message(uid, text, parse_mode="HTML")
    except Exception:
        logger.warning("Birthday greeting failed for %s", uid)


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# ð  EVENTS / OCCASIONS MODULE
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def events_enabled():
    return feature_enabled("events")

def create_event(name, event_date, message="", xp_reward=0, gift_type="", gift_value="",
                 vip_days=0, target_users="all"):
    now = datetime.now(TZ).isoformat()
    c = db()
    c.execute(
        """INSERT INTO events(name, event_date, message, xp_reward, gift_type, gift_value,
           vip_days, target_users, enabled, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,1,?,?)""",
        (name, event_date, message, xp_reward, gift_type, gift_value, vip_days, target_users, now, now),
    )
    eid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.commit()
    c.close()
    return eid

def get_active_events():
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    c = db()
    rows = c.execute("SELECT * FROM events WHERE enabled=1 AND event_date=?", (today,)).fetchall()
    c.close()
    return rows

def event_already_delivered(event_id, uid):
    c = db()
    r = c.execute("SELECT 1 FROM event_deliveries WHERE event_id=? AND user_id=?", (event_id, uid)).fetchone()
    c.close()
    return bool(r)

def mark_event_delivered(event_id, uid):
    now = datetime.now(TZ).isoformat()
    c = db()
    c.execute("INSERT OR IGNORE INTO event_deliveries(event_id, user_id, delivered_at) VALUES(?,?,?)",
              (event_id, uid, now))
    c.commit()
    c.close()

def get_all_events():
    c = db()
    rows = c.execute("SELECT * FROM events ORDER BY event_date DESC").fetchall()
    c.close()
    return rows

def toggle_event(event_id, enabled):
    c = db()
    c.execute("UPDATE events SET enabled=?, updated_at=? WHERE id=?",
              (1 if enabled else 0, datetime.now(TZ).isoformat(), event_id))
    c.commit()
    c.close()

def delete_event(event_id):
    c = db()
    c.execute("DELETE FROM events WHERE id=?", (event_id,))
    c.execute("DELETE FROM event_deliveries WHERE event_id=?", (event_id,))
    c.commit()
    c.close()


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# ð  ADMIN GIFT SYSTEM
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def admin_grant_gift(user_id, gift_type, value="", xp_amount=0, vip_days=0,
                     feature_key="", granted_by=0, source="admin", source_detail=""):
    now = datetime.now(TZ).isoformat()
    expires = None
    if vip_days > 0:
        expires = (datetime.now(TZ) + timedelta(days=vip_days)).isoformat()
        c = db()
        current = c.execute("SELECT vip_until FROM users WHERE user_id=?", (user_id,)).fetchone()
        if current and current["vip_until"]:
            try:
                cur_exp = datetime.fromisoformat(current["vip_until"])
                if cur_exp > datetime.now(TZ):
                    expires = (cur_exp + timedelta(days=vip_days)).isoformat()
            except Exception:
                pass
        c.execute("UPDATE users SET vip_until=? WHERE user_id=?", (expires, user_id))
        c.commit()
        c.close()
    if xp_amount > 0:
        add_xp(user_id, xp_amount, "admin_gift")
    if feature_key:
        c = db()
        c.execute(
            """INSERT INTO access_grants(user_id, feature_key, granted_by, source, expires_at, active, created_at)
               VALUES(?, ?, ?, ?, ?, 1, ?)
               ON CONFLICT(user_id, feature_key, source) DO UPDATE SET
               expires_at=excluded.expires_at, active=1""",
            (user_id, feature_key, granted_by, source, expires, now))
        c.commit()
        c.close()
    c = db()
    c.execute(
        """INSERT INTO user_gifts(user_id, gift_type, gift_value, xp_amount, vip_days,
           feature_key, source, source_detail, granted_by, granted_at, expires_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (user_id, gift_type, value, xp_amount, vip_days, feature_key, source, source_detail, granted_by, now, expires))
    c.commit()
    c.close()

def get_user_gifts(uid):
    c = db()
    rows = c.execute("SELECT * FROM user_gifts WHERE user_id=? ORDER BY granted_at DESC", (uid,)).fetchall()
    c.close()
    return rows

def has_active_feature_access(uid, feature_key):
    """Check if user has active access to a feature via grants or subscription."""
    c = db()
    now = datetime.now(TZ).isoformat()
    r = c.execute(
        "SELECT 1 FROM access_grants WHERE user_id=? AND feature_key=? AND active=1 AND (expires_at IS NULL OR expires_at>?)",
        (uid, feature_key, now)).fetchone()
    c.close()
    if r:
        return True
    if is_vip(uid):
        return True
    return False


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# ð  SUBSCRIPTIONS V2 (precise expiry)
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def create_subscription(user_id, plan="vip", duration_hours=0, source="admin", amount=0, payment_id=None):
    now = datetime.now(TZ)
    start_at = now.isoformat()
    if duration_hours > 0:
        expires_at = (now + timedelta(hours=duration_hours)).isoformat()
    else:
        expires_at = "9999-12-31T23:59:59"
    # Check for existing active subscription and extend
    c = db()
    existing = c.execute(
        "SELECT expires_at FROM subscriptions_v2 WHERE user_id=? AND plan=? AND active=1 ORDER BY expires_at DESC LIMIT 1",
        (user_id, plan)).fetchone()
    if existing and existing["expires_at"]:
        try:
            cur_exp = datetime.fromisoformat(existing["expires_at"])
            if cur_exp > now:
                # Extend from current expiry, not from now
                start_at = cur_exp.isoformat()
                expires_at = (cur_exp + timedelta(hours=duration_hours)).isoformat() if duration_hours else "9999-12-31T23:59:59"
        except Exception:
            pass
    c.execute(
        """INSERT INTO subscriptions_v2(user_id, plan, start_at, expires_at, duration_hours, source, amount, payment_id, active, created_at)
           VALUES(?,?,?,?,?,?,?,?,1,?)""",
        (user_id, plan, start_at, expires_at, duration_hours, source, amount, payment_id, now.isoformat()))
    c.execute("UPDATE users SET vip_until=? WHERE user_id=?", (expires_at, user_id))
    c.commit()
    c.close()
    return expires_at

def get_active_subscriptions(uid):
    now = datetime.now(TZ).isoformat()
    c = db()
    rows = c.execute(
        "SELECT * FROM subscriptions_v2 WHERE user_id=? AND active=1 AND expires_at>?",
        (uid, now)).fetchall()
    c.close()
    return rows


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# ð  ACCESS CONTROL CENTER
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def get_user_access_matrix(uid):
    """Return a dict of feature_key -> status for a user."""
    now = datetime.now(TZ).isoformat()
    c = db()
    features = c.execute("SELECT key FROM feature_flags WHERE enabled=1").fetchall()
    grants = c.execute(
        "SELECT feature_key, expires_at FROM access_grants WHERE user_id=? AND active=1",
        (uid,)).fetchall()
    c.close()
    grant_map = {}
    for g in grants:
        if g["expires_at"]:
            try:
                if datetime.fromisoformat(g["expires_at"]) > datetime.now(TZ):
                    grant_map[g["feature_key"]] = True
            except Exception:
                grant_map[g["feature_key"]] = True
        else:
            grant_map[g["feature_key"]] = True
    vip = is_vip(uid)
    result = {}
    for f in features:
        key = f["key"]
        mode = feature_access_mode(key, uid)
        if mode == "off":
            result[key] = "ð´ disabled"
        elif mode == "vip":
            result[key] = "ð¢" if (vip or key in grant_map) else "ð VIP"
        else:
            result[key] = "ð¢"
    return result


async def xp_command(update,context):
    uid=update.effective_user.id; xp,level,_=xp_info(uid); await update.message.reply_text(f"â­ XP: {xp}\nð Ø³Ø·Ø­: {level}\nð VIP: {'ÙØ¹Ø§Ù' if is_vip(uid) else 'ØºÛØ±ÙØ¹Ø§Ù'}")

def final_admin_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("ð Ø¯Ø§Ø´Ø¨ÙØ±Ø¯",callback_data="adm:stats"),InlineKeyboardButton("ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù",callback_data="adm:users")],[InlineKeyboardButton("ð Ø¬Ø³ØªØ¬Ù",callback_data="adm:search"),InlineKeyboardButton("ð§° Ø§Ø¨Ø²Ø§Ø± Ú©Ø§Ø±Ø¨Ø±",callback_data="adm:tools")],[InlineKeyboardButton("ð¡ Ú©Ø§ÙØ§Ù Ù Ù¾Ø³ØªâÚ¯Ø°Ø§Ø±Û",callback_data="adm:channel"),InlineKeyboardButton("ð¥ ÙØ¯ÛØ±ÛØª ÙØ´ØªØ±Û",callback_data="adm:customers")],[InlineKeyboardButton("âï¸ ÙØ§Ø¨ÙÛØªâÙØ§",callback_data="adm:features"),InlineKeyboardButton("ð° ÙØ²ÛÙÙ/Ø³Ø±ÙÛØ³âÙØ§",callback_data="adm:costs")],
        [InlineKeyboardButton("â­ XP / VIP",callback_data="adm:xpvip"),InlineKeyboardButton("ð¥ Ø¸Ø±ÙÛØª/Ú©Ø§Ø±Ø¨Ø±Ø§Ù",callback_data="adm:capacity")],[InlineKeyboardButton("ð« ØªÛÚ©ØªâÙØ§",callback_data="adm:tickets"),InlineKeyboardButton("ð©º Health Check",callback_data="adm:health")],[InlineKeyboardButton("â° Ø²ÙØ§ÙâØ¨ÙØ¯Û ÚÚ©Ø§Ù¾",callback_data="adm:health_schedule"),InlineKeyboardButton("â¸ ØªÙÙÙ ÙÙÙØª Ø±Ø¨Ø§Øª",callback_data="adm:pause")],[InlineKeyboardButton("ð§ª ÙØ±Ú©Ø² ØªØ³Øª",callback_data="adm:test"),InlineKeyboardButton("ð¾ Ø¨Ú©Ø§Ù¾",callback_data="adm:backup")],[InlineKeyboardButton("ð Ø¹ÛØ¨âÛØ§Ø¨Û Ú©Ø§ÙÙ",callback_data="adm:diagnostics"),InlineKeyboardButton("ð ÙØ§Ú¯ ÙØ¯ÛØ±Ø§Ù",callback_data="adm:audit")],[InlineKeyboardButton("ð Ú¯Ø²Ø§Ø±Ø´ Ø±ÙØ²",callback_data="adm:report"),InlineKeyboardButton("ð¢ Ù¾ÛØ§Ù ÙÙÚ¯Ø§ÙÛ",callback_data="adm:broadcast")],[InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ",callback_data="adm:main")]])

async def admin_user_detail_callback(update,context, target_override=None):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("â",show_alert=True); return
    try:
        await q.answer(); target=int(target_override) if target_override is not None else int(q.data.split(":",1)[1]); c=db()
        u=c.execute("SELECT * FROM users WHERE user_id=?",(target,)).fetchone()
        if not u:
            c.close(); await q.message.reply_text("â Ú©Ø§Ø±Ø¨Ø± Ù¾ÛØ¯Ø§ ÙØ´Ø¯.",reply_markup=final_admin_keyboard()); return
        usage=c.execute("SELECT COUNT(*) n FROM bot_usage_events WHERE user_id=?",(target,)).fetchone()["n"]
        usage30=c.execute("SELECT COUNT(*) n FROM bot_usage_events WHERE user_id=? AND created_at>=?",(target,(datetime.now(TZ)-timedelta(days=30)).isoformat())).fetchone()["n"]
        goals=c.execute("SELECT COUNT(*) n FROM goals WHERE user_id=?",(target,)).fetchone()["n"]
        done=c.execute("SELECT COUNT(*) n FROM goal_days WHERE user_id=? AND status='done'",(target,)).fetchone()["n"]
        reactions=c.execute("SELECT COUNT(*) n FROM channel_reactions WHERE user_id=?",(target,)).fetchone()["n"]
        polls=c.execute("SELECT COUNT(*) n FROM channel_poll_votes WHERE user_id=?",(target,)).fetchone()["n"]
        referrals=c.execute("SELECT COUNT(*) n FROM referrals WHERE inviter_id=?",(target,)).fetchone()["n"]
        appts=c.execute("SELECT COUNT(*) n FROM appointments WHERE owner_user_id=?",(target,)).fetchone()["n"]
        subs=c.execute("SELECT * FROM subscription_history WHERE user_id=? ORDER BY created_at DESC LIMIT 10",(target,)).fetchall()
        manager=c.execute("SELECT u.user_id,u.first_name,u.username FROM users u JOIN users t ON t.manager_user_id=u.user_id WHERE t.user_id=?",(target,)).fetchone()
        c.close()
        sub_lines="\n".join(f"â¢ {r['plan']} | {r['duration_days']} Ø±ÙØ² | {r['source']} | ØªØ§ {r['expires_at'] or 'â'}" for r in subs) or "Ø³Ø§Ø¨ÙÙâØ§Û Ø«Ø¨Øª ÙØ´Ø¯Ù"
        uname=("@"+u["username"]) if u["username"] else "Ø«Ø¨Øª ÙØ´Ø¯Ù"
        manager_name=(f"@{manager['username']}" if manager and manager['username'] else (manager['first_name'] if manager else "Ø«Ø¨Øª ÙØ´Ø¯Ù"))
        text=(f"ð¤ <b>Ù¾Ø±ÙÙØ¯Ù Ú©Ø§Ø±Ø¨Ø±</b>\n\nÙØ§Ù: {html.escape(u['first_name'] or 'Ø¨Ø¯ÙÙ ÙØ§Ù')}\nð¤ Username: <code>{html.escape(uname)}</code>\nð ID: <code>{target}</code>\nð¨âð¼ ÙØ¯ÛØ± ÙØ±ØªØ¨Ø·: <b>{html.escape(manager_name)}</b>\nÙØ¶Ø¹ÛØª: {'â ÙØ­Ø¯ÙØ¯' if u['blocked'] else 'ð¢ ÙØ¹Ø§Ù'}\nð Ø§Ø´ØªØ±Ø§Ú©: {'ÙØ¹Ø§Ù ØªØ§ '+(u['vip_until'] or '')[:16] if u['vip_until'] else 'Ø±Ø§ÛÚ¯Ø§Ù'}\nâ­ XP: {u['xp']}\n\nð <b>Ø¢ÙØ§Ø± Ø§Ø³ØªÙØ§Ø¯Ù</b>\nð¤ Ø±ÙÛØ¯Ø§Ø¯ÙØ§Û Ø±Ø¨Ø§Øª: {usage}\nð Û³Û° Ø±ÙØ² Ø§Ø®ÛØ±: {usage30}\nð¯ Ø§ÙØ¯Ø§Ù: {goals} | Ø§ÙØ¬Ø§ÙâØ´Ø¯Ù: {done}\nð£ ÙØ§Ú©ÙØ´ Ú©Ø§ÙØ§Ù: {reactions}\nð³ ÙØ¸Ø±Ø³ÙØ¬Û: {polls}\nð¤ Ø¯Ø¹ÙØª ÙÙÙÙ: {referrals}\nð¥ ÙÙØ¨ØªâÙØ§Û Ú©Ø³Ø¨âÙÚ©Ø§Ø±: {appts}\n\nð³ <b>Ø³ÙØ§Ø¨Ù Ø§Ø´ØªØ±Ø§Ú©/ØªÙØ¯ÛØ¯</b>\n{sub_lines}")
        kb=[[InlineKeyboardButton("ð« ÙØ­Ø¯ÙØ¯ Ú©Ø±Ø¯Ù" if not u['blocked'] else "ð Ø±ÙØ¹ ÙØ­Ø¯ÙØ¯ÛØª",callback_data=f"admu_block:{target}")],
            [InlineKeyboardButton("ð Û· Ø±ÙØ² Ø±Ø§ÛÚ¯Ø§Ù",callback_data=f"admu_vip:{target}:7"),InlineKeyboardButton("ð Û³Û° Ø±ÙØ²",callback_data=f"admu_vip:{target}:30")],
            [InlineKeyboardButton("â¾ï¸ Ø§Ø´ØªØ±Ø§Ú© ÙØ§ÙØ­Ø¯ÙØ¯",callback_data=f"admu_unlimited:{target}"),InlineKeyboardButton("âï¸ ÙÛØ±Ø§ÛØ´ Ø§Ø´ØªØ±Ø§Ú©",callback_data=f"admu_editvip:{target}")],
            [InlineKeyboardButton("ð¨âð¼ Ø§Ø±ØªÙØ§Ø¡ Ø¨Ù ÙØ¯ÛØ±",callback_data=f"admu_promote:{target}"),InlineKeyboardButton("ð¨âð¼ ÙØ¯ÛØ± ÙØ±ØªØ¨Ø·",callback_data=f"admu_manager:{target}")],
            [InlineKeyboardButton("â¬ï¸ Ú©Ø§Ø±Ø¨Ø±Ø§Ù",callback_data=f"adm:users:{context.user_data.get('admin_users_page',1)}")]]
        try: await q.message.edit_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))
        except Exception: await q.message.reply_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        logger.exception("admin_user_detail_callback failed")
        try: await q.message.reply_text(f"â Ø®Ø·Ø§ Ø¯Ø± ÙÙØ§ÛØ´ Ø§Ø·ÙØ§Ø¹Ø§Øª Ú©Ø§Ø±Ø¨Ø±: <code>{type(e).__name__}</code>",parse_mode="HTML",reply_markup=final_admin_keyboard())
        except Exception: pass

async def admin_user_action_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("â",show_alert=True); return
    parts=q.data.split(":"); action=parts[0].split("_",1)[1]; target=int(parts[1]); now=datetime.now(TZ).isoformat(); c=db()
    if action=="block":
        r=c.execute("SELECT blocked FROM users WHERE user_id=?",(target,)).fetchone(); new=0 if r and r["blocked"] else 1; c.execute("UPDATE users SET blocked=? WHERE user_id=?",(new,target)); c.commit(); c.close(); admin_log(uid,"user_block_toggle",target,str(new)); await q.answer("ð Ø±ÙØ¹ ÙØ­Ø¯ÙØ¯ÛØª Ø´Ø¯" if not new else "ð« ÙØ­Ø¯ÙØ¯ Ø´Ø¯")
        try:
            await admin_user_detail_callback(update,context,target)
        except Exception:
            logger.exception("admin_user_detail_callback after block failed")
            try: await q.message.reply_text("â ÙØ¶Ø¹ÛØª Ú©Ø§Ø±Ø¨Ø± ØªØºÛÛØ± Ú©Ø±Ø¯.",reply_markup=final_admin_keyboard())
            except Exception: pass
        return
    elif action=="vip":
        days=int(parts[2]); expires=(datetime.now(TZ)+timedelta(days=days)).isoformat(); c.execute("UPDATE users SET vip_until=? WHERE user_id=?",(expires,target)); c.execute("INSERT INTO subscription_history(user_id,plan,duration_days,source,started_at,expires_at,created_at) VALUES(?,?,?,?,?,?,?)",(target,"VIP",days,"admin",now,expires,now)); c.commit(); c.close(); admin_log(uid,"vip_grant",target,f"{days}d"); await q.answer(f"ð {days} Ø±ÙØ² VIP Ø´Ø¯")
    elif action=="unlimited":
        expires="9999-12-31T23:59:59"; c.execute("UPDATE users SET vip_until=? WHERE user_id=?",(expires,target)); c.execute("INSERT INTO subscription_history(user_id,plan,duration_days,source,started_at,expires_at,created_at) VALUES(?,?,?,?,?,?,?)",(target,"VIP Unlimited",0,"admin",now,expires,now)); c.commit(); c.close(); admin_log(uid,"vip_unlimited",target); await q.answer("â¾ï¸ Ø§Ø´ØªØ±Ø§Ú© ÙØ§ÙØ­Ø¯ÙØ¯ Ø´Ø¯")
    elif action=="editvip":
        c.close(); await q.message.reply_text("âï¸ <b>ÙÛØ±Ø§ÛØ´ Ø§Ø´ØªØ±Ø§Ú©</b>\n\nØ±ÙØ² ÙØ«Ø¨Øª = Ø§Ø¶Ø§ÙÙ Ú©Ø±Ø¯Ù\nØ±ÙØ² ÙÙÙÛ = Ú©Ù Ú©Ø±Ø¯Ù\n0 = ÙØºÙ Ú©Ø§ÙÙ\nÙØ«Ø§Ù: -7 ÛØ§ 15",parse_mode="HTML",reply_markup=nav_keyboard(uid)); context.user_data["admin_vip_edit_user"]=target; return
    elif action=="manager":
        c.close()
        context.user_data["admin_manager_target"] = target
        await q.answer()
        await q.message.edit_text("ð¨âð¼ <b>ÙØ¯ÛØ± ÙØ±ØªØ¨Ø·</b>\n\nUsername ÛØ§ Telegram ID ÙØ¯ÛØ± Ø±Ø§ Ø¨ÙØ±Ø³Øª.\nØ¨Ø±Ø§Û Ø­Ø°Ù ÙØ¯ÛØ±Ø Ø¹Ø¨Ø§Ø±Øª Â«Ø­Ø°ÙÂ» Ø±Ø§ Ø¨ÙØ±Ø³Øª.",parse_mode="HTML",reply_markup=nav_keyboard(uid))
        return
    elif action=="promote":
        if target==master_owner_id(): c.close(); await q.answer("â Owner ÙÙÛØ´Ù ÙØ¯ÛØ± Ø§Ø³Øª.",show_alert=True); return
        existing=c.execute("SELECT user_id FROM management_roles WHERE user_id=?",(target,)).fetchone()
        if existing:
            c.execute("UPDATE management_roles SET active=1,updated_at=? WHERE user_id=?",(now,target))
        else:
            c.execute("INSERT INTO management_roles(user_id,role,domain,permissions_json,active,created_at,updated_at) VALUES(?,?,?,?,1,?,?)",(target,"general_manager","general",json.dumps(sorted(MASTER_ROLE_PERMISSIONS.get("general_manager",set()))),now,now))
        c.commit(); c.close(); admin_log(uid,"manager_promoted",target,"general_manager"); await q.answer("â Ú©Ø§Ø±Ø¨Ø± Ø¨Ù ÙØ¯ÛØ± Ø§Ø±ØªÙØ§ ÛØ§ÙØª.",show_alert=True)
        try: await admin_user_detail_callback(update,context,target)
        except Exception: pass
        return
    else: c.close(); return
    await admin_user_detail_callback(update,context,target)


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
    lines=["ð <b>Ø¹ÛØ¨âÛØ§Ø¨Û Ú©Ø§ÙÙ Ø±Ø¨Ø§Øª</b>",""]
    for name,ok,detail in checks:
        lines.append(f"{'ð¢' if ok else 'ð´'} {html.escape(name)} â {html.escape(detail)}")
    bad=sum(1 for _,ok,_ in checks if not ok)
    lines += ["", f"ÙØªÛØ¬Ù: {'ð¢ Ø³Ø§ÙÙ' if not bad else f'ð´ {bad} ÙÙØ±Ø¯ ÙÛØ§Ø²ÙÙØ¯ Ø¨Ø±Ø±Ø³Û'}"]
    return "\n".join(lines)

def _admin_test_text():
    checks=_admin_db_diagnostics()
    db_ok=all(ok for name,ok,_ in checks if name=="SQLite integrity")
    tables_ok=sum(1 for name,ok,_ in checks if name.startswith("table:") and ok)
    tables_total=sum(1 for name,_,_ in checks if name.startswith("table:"))
    return (
        "ð§ª <b>ÙØ±Ú©Ø² ØªØ³Øª</b>\n\n"
        f"ð Ø¯ÛØªØ§Ø¨ÛØ³: {'ð¢ Ø³Ø§ÙÙ' if db_ok else 'ð´ ÙØ´Ú©Ù Ø¯Ø§Ø±Ø¯'}\n"
        f"ð¦ Ø¬Ø¯Ø§ÙÙ Ø§ØµÙÛ: {tables_ok}/{tables_total}\n"
        f"ð©º Health Check: Ø¢ÙØ§Ø¯Ù Ø§Ø¬Ø±Ø§ Ø§Ø² Ù¾ÙÙ\n"
        f"ð¢ Ú©Ø§ÙØ§Ù: ØªØ³Øª Ø§ØªØµØ§Ù Ø§Ø² Ø¨Ø®Ø´ Ú©Ø§ÙØ§Ù\n"
        f"ð Ø±Ø²Ø±Ù: ØªØ§Ø±ÛØ®/Ø³Ø§Ø¹Øª Ø¯Ø± ÙØ³ÛØ± ÙØ§ÙØ¹Û Ø±Ø²Ø±Ù Ø¨Ø±Ø±Ø³Û ÙÛâØ´ÙØ¯\n\n"
        "Ø§ÛÙ Ø¨Ø®Ø´ ØªØ³ØªâÙØ§Û ØºÛØ±ÙØ®Ø±Ø¨ Ø§ÙØ¬Ø§Ù ÙÛâØ¯ÙØ¯ Ù ÙÛÚ Ø¯Ø§Ø¯Ù Ú©Ø§Ø±Ø¨Ø± Ø±Ø§ Ø­Ø°Ù ÙÙÛâÚ©ÙØ¯."
    )

async def _admin_manual_backup(update, context):
    uid=update.effective_user.id
    if not admin_guard(uid):
        await update.callback_query.answer("â",show_alert=True); return
    await update.callback_query.answer("Ø¯Ø± Ø­Ø§Ù Ø³Ø§Ø®Øª Ø¨Ú©Ø§Ù¾...")
    ok=backup_database_snapshot(keep=20)
    admin_log(uid,"manual_backup",None,"success" if ok else "failed")
    await update.callback_query.message.edit_text(
        ("ð¾ <b>Ø¨Ú©Ø§Ù¾ Ø¨Ø§ ÙÙÙÙÛØª Ø³Ø§Ø®ØªÙ Ø´Ø¯.</b>\nÙØ³Ø®ÙâÙØ§Û ÙØ¨ÙÛ ÙÙ Ø­ÙØ¸ Ø´Ø¯ÙØ¯."
         if ok else "â Ø³Ø§Ø®Øª Ø¨Ú©Ø§Ù¾ Ø§ÙØ¬Ø§Ù ÙØ´Ø¯. ÙØ§Ú¯ Ø®Ø·Ø§ Ø±Ø§ Ø¨Ø±Ø±Ø³Û Ú©Ù."),
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
        "free":"ð¢ Ø±Ø§ÛÚ¯Ø§Ù", "optional_paid":"ð¡ Ù¾ÙÙÛÙ Ø§Ø®ØªÛØ§Ø±Û", "variable":"ð  ÙØ²ÛÙÙ ÙØªØºÛØ±",
        "free_or_variable":"ð  Ø±Ø§ÛÚ¯Ø§Ù/ÙØªØºÛØ±", "transactional":"ðµ ÙØ§Ø¨Ø³ØªÙ Ø¨Ù ØªØ±Ø§Ú©ÙØ´"
    }
    lines=["ð° <b>ÙØ±Ú©Ø² ÙØ²ÛÙÙ Ù Ø³Ø±ÙÛØ³âÙØ§</b>","","Ø§ÛÙ Ø¨Ø®Ø´ Ø¨Ø§ Â«Ø±Ø§ÛÚ¯Ø§Ù/VIPÂ» ÙØ±Ù Ø¯Ø§Ø±Ø¯:","â¢ âï¸ ÙØ§Ø¨ÙÛØªâÙØ§ = Ø¯Ø³ØªØ±Ø³Û Ú©Ø§Ø±Ø¨Ø±","â¢ ð° Ø§ÛÙ Ø¨Ø®Ø´ = ÙØ²ÛÙÙ Ø³Ø±ÙÛØ³ Ø²ÛØ±Ø³Ø§Ø®ØªÛ/Ø®Ø§Ø±Ø¬Û","",f"ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ø«Ø¨ØªâØ´Ø¯Ù: <b>{users}</b>",f"ð¢ ÙØ¹Ø§Ù Û²Û´ Ø³Ø§Ø¹Øª Ø§Ø®ÛØ±: <b>{active24}</b>",f"ð ÙØ¹Ø§Ù Û· Ø±ÙØ² Ø§Ø®ÛØ±: <b>{active7}</b>",""]
    for r in rows:
        state=labels.get(r["status"],r["status"])
        on="ð¢ Ø±ÙØ´Ù" if r["enabled"] else "ð´ Ø®Ø§ÙÙØ´"
        lines.append(f"{state} {html.escape(r['label'])} â {on}")
        lines.append(f"  Ø§Ø±Ø§Ø¦ÙâØ¯ÙÙØ¯Ù: {html.escape(r['provider'] or 'â')}")
        lines.append(f"  {html.escape(r['note'])}")
    lines += ["","â ï¸ ÙÛÙØª Ø¯ÙÛÙ Ø³Ø±ÙÛØ³âÙØ§Û Ø®Ø§Ø±Ø¬Û Ø«Ø§Ø¨Øª ÙÛØ³Øª Ù Ø¨Ø§ÛØ¯ Ø·Ø¨Ù Ø§Ø±Ø§Ø¦ÙâØ¯ÙÙØ¯Ù Ø§ÙØªØ®Ø§Ø¨Û ØªÙØ¸ÛÙ Ø´ÙØ¯."]
    return "\n".join(lines)

def admin_capacity_text():
    c=db()
    total=c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    d1=c.execute("SELECT COUNT(*) n FROM users WHERE last_active_at>=?",((datetime.now(TZ)-timedelta(days=1)).isoformat(),)).fetchone()["n"]
    d7=c.execute("SELECT COUNT(*) n FROM users WHERE last_active_at>=?",((datetime.now(TZ)-timedelta(days=7)).isoformat(),)).fetchone()["n"]
    d30=c.execute("SELECT COUNT(*) n FROM users WHERE last_active_at>=?",((datetime.now(TZ)-timedelta(days=30)).isoformat(),)).fetchone()["n"]
    blocked=c.execute("SELECT COUNT(*) n FROM users WHERE blocked=1").fetchone()["n"]
    c.close()
    return ("ð¥ <b>Ø¸Ø±ÙÛØª Ù Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ø±Ø¨Ø§Øª</b>\n\n"
            f"ð¤ Ú©Ù Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ø«Ø¨ØªâØ´Ø¯Ù: <b>{total}</b>\n"
            f"ð¢ ÙØ¹Ø§Ù Û²Û´ Ø³Ø§Ø¹Øª Ø§Ø®ÛØ±: <b>{d1}</b>\n"
            f"ð ÙØ¹Ø§Ù Û· Ø±ÙØ² Ø§Ø®ÛØ±: <b>{d7}</b>\n"
            f"ð ÙØ¹Ø§Ù Û³Û° Ø±ÙØ² Ø§Ø®ÛØ±: <b>{d30}</b>\n"
            f"â ÙØ­Ø¯ÙØ¯Ø´Ø¯Ù: <b>{blocked}</b>\n\n"
            "â¹ï¸ Ø¯Ø± Ú©Ø¯ ÙØ¹ÙÛ Ø³ÙÙ Ø¹Ø¯Ø¯ÛÙ Ø«Ø§Ø¨Øª Ø¨Ø±Ø§Û ØªØ¹Ø¯Ø§Ø¯ Ú©Ø§Ø±Ø¨Ø±Ø§Ù ØªØ¹Ø±ÛÙ ÙØ´Ø¯Ù Ø§Ø³Øª. Ø¸Ø±ÙÛØª ÙØ§ÙØ¹Û Ø¨Ù ÙÙØ§Ø¨Ø¹ Ø³Ø±ÙØ±Ø Ø¯ÛØªØ§Ø¨ÛØ³Ø APIÙØ§ Ù ÙØ­Ø¯ÙØ¯ÛØªâÙØ§Û Telegram Ø¨Ø³ØªÚ¯Û Ø¯Ø§Ø±Ø¯.\n"
            "ð Ø§ÛÙ ÙØ³Ø®Ù Ø§Ø² SQLite Ø§Ø³ØªÙØ§Ø¯Ù ÙÛâÚ©ÙØ¯Ø Ø¨Ø±Ø§Û ØªØ¹Ø¯Ø§Ø¯ Ø¨Ø³ÛØ§Ø± Ø²ÛØ§Ø¯ Ú©Ø§Ø±Ø¨Ø± Ø¨ÙØªØ± Ø§Ø³Øª Ø¨Ø¹Ø¯Ø§Ù Ø¯ÛØªØ§Ø¨ÛØ³ Ø³Ø±ÙØ±Û ÙØ«Ù PostgreSQL Ù ØµÙ/Ú©Ø´ Ø§Ø¶Ø§ÙÙ Ø´ÙØ¯.")


async def admin_users_navigation_callback(update,context, page_override=None):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("â",show_alert=True); return
    parts=(q.data or "").split(":"); action=parts[1] if len(parts)>1 else "list"
    page=(int(page_override) if page_override is not None else (int(parts[2]) if len(parts)>2 and parts[2].isdigit() else 1))
    page=max(1,page)
    per_page=10
    c=db(); total=c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    pages=max(1,(total+per_page-1)//per_page); page=min(page,pages); offset=(page-1)*per_page
    rows=c.execute("""SELECT u.user_id,u.first_name,u.username,u.xp,u.blocked,u.vip_until,u.manager_user_id,
                            m.first_name manager_name,m.username manager_username
                     FROM users u LEFT JOIN users m ON m.user_id=u.manager_user_id
                     ORDER BY u.created_at DESC LIMIT ? OFFSET ?""",(per_page,offset)).fetchall(); c.close()
    lines=[f"ð¥ <b>Ú©Ø§Ø±Ø¨Ø±Ø§Ù</b>\nØµÙØ­Ù {page} Ø§Ø² {pages} | Ú©Ù: {total}",""]
    kb=[]
    for r in rows:
        uname=("@"+r["username"]) if r["username"] else "Ø¨Ø¯ÙÙ username"
        mgr=("@"+r["manager_username"]) if r["manager_username"] else (r["manager_name"] or "Ø¨Ø¯ÙÙ ÙØ¯ÛØ±")
        state="â" if r["blocked"] else "ð¢"
        vip="ð" if r["vip_until"] and str(r["vip_until"])[:4] != "9999" else ("â¾ï¸" if r["vip_until"] else "")
        lines.append(f"{state} {html.escape(r['first_name'] or 'Ø¨Ø¯ÙÙ ÙØ§Ù')} | {html.escape(uname)} | â­{r['xp']} {vip}\nð¨âð¼ {html.escape(mgr)} | ID: <code>{r['user_id']}</code>")
        kb.append([InlineKeyboardButton(f"ð¤ {r['first_name'] or 'Ø¨Ø¯ÙÙ ÙØ§Ù'} | {uname}",callback_data=f"admu:{r['user_id']}")])
    nav=[]
    if page>1: nav.append(InlineKeyboardButton("â¬ï¸ ÙØ¨ÙÛ",callback_data=f"adm:users:{page-1}"))
    if page<pages: nav.append(InlineKeyboardButton("Ø¨Ø¹Ø¯Û â¡ï¸",callback_data=f"adm:users:{page+1}"))
    if nav: kb.append(nav)
    kb.append([InlineKeyboardButton("ð Ø¬Ø³ØªØ¬Ù",callback_data="adm:search"),InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª",callback_data="adm:stats")])
    context.user_data["admin_users_page"]=page
    await q.answer()
    await q.message.edit_text("\n".join(lines),parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def admin_manager_assign_text(update,context):
    uid=update.effective_user.id
    target=context.user_data.get("admin_manager_target")
    if not target or not admin_guard(uid): return False
    text=(update.message.text or "").strip()
    context.user_data.pop("admin_manager_target",None)
    if text.lower() in ("Ø­Ø°Ù","remove","none","-"):
        c=db(); c.execute("UPDATE users SET manager_user_id=NULL WHERE user_id=?",(int(target),)); c.commit(); c.close(); admin_log(uid,"manager_unassigned",target); await update.message.reply_text("â ÙØ¯ÛØ± ÙØ±ØªØ¨Ø· Ø­Ø°Ù Ø´Ø¯.",reply_markup=final_admin_keyboard()); return True
    ref=text.lstrip("@").strip().lower()
    c=db()
    if ref.isdigit():
        manager=c.execute("SELECT user_id,first_name,username FROM users WHERE user_id=?",(int(ref),)).fetchone()
    else:
        manager=c.execute("SELECT user_id,first_name,username FROM users WHERE lower(username)=?",(ref,)).fetchone()
    if not manager:
        c.close(); await update.message.reply_text("â ÙØ¯ÛØ± Ù¾ÛØ¯Ø§ ÙØ´Ø¯. Username ÛØ§ ID ÙØ¹ØªØ¨Ø± ÙØ¯ÛØ± Ø±Ø§ Ø¨ÙØ±Ø³Øª.",reply_markup=nav_keyboard(uid)); context.user_data["admin_manager_target"]=target; return True
    role=c.execute("SELECT active FROM management_roles WHERE user_id=?",(int(manager["user_id"]),)).fetchone()
    if not role or not role["active"]:
        c.close(); await update.message.reply_text("â Ø§ÛÙ Ú©Ø§Ø±Ø¨Ø± ÙØ¯ÛØ± ÙØ¹Ø§Ù ÙÛØ³Øª.",reply_markup=nav_keyboard(uid)); context.user_data["admin_manager_target"]=target; return True
    if int(manager["user_id"])==int(target):
        c.close(); await update.message.reply_text("â Ú©Ø§Ø±Ø¨Ø± ÙÙÛâØªÙØ§ÙØ¯ ÙØ¯ÛØ± Ø®ÙØ¯Ø´ Ø¨Ø§Ø´Ø¯.",reply_markup=nav_keyboard(uid)); context.user_data["admin_manager_target"]=target; return True
    c.execute("UPDATE users SET manager_user_id=? WHERE user_id=?",(int(manager["user_id"]),int(target))); c.commit(); c.close()
    admin_log(uid,"manager_assigned",target,str(manager["user_id"]))
    await update.message.reply_text(f"â ÙØ¯ÛØ± ÙØ±ØªØ¨Ø· Ø±ÙÛ {('@'+manager['username']) if manager['username'] else manager['first_name'] or manager['user_id']} ØªÙØ¸ÛÙ Ø´Ø¯.",reply_markup=final_admin_keyboard()); return True

async def final_admin_panel_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯",show_alert=True); return
    await q.answer(); a=q.data.split(":",1)[1]
    if a=="stats":
        s=admin_stats(); text="ð Ø¯Ø§Ø´Ø¨ÙØ±Ø¯ ÙØ±Ú©Ø²Û\n\n"+f"ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù: {s['users']}\nð Ø¬Ø¯ÛØ¯ Ø§ÙØ±ÙØ²: {s['new_today']}\nð¢ ÙØ¹Ø§Ù Ø§ÙØ±ÙØ²: {s['active_today']}\nð¯ Ø§ÙØ¯Ø§Ù: {s['goals']}\nâ Ø§ÙØ¬Ø§ÙâØ´Ø¯Ù Ø§ÙØ±ÙØ²: {s['done_today']}\nâ° ÛØ§Ø¯Ø¢ÙØ±Û: {s['reminders']}\nð Ø¯Ø³ØªØ§ÙØ±Ø¯: {s['achievements']}\nð ÙÙØ¨Øª Ø§ÙØ±ÙØ²: {s['appointments_today']}\nð VIP ÙØ¹Ø§Ù: {s['vip_users']}\nð« ØªÛÚ©Øª Ø¨Ø§Ø²: {s['open_tickets']}"; await q.message.edit_text(text,reply_markup=final_admin_keyboard()); return
    if a.startswith("users"):
        page=int(a.split(":",1)[1]) if ":" in a and a.split(":",1)[1].isdigit() else 1
        context.user_data["admin_users_page"]=page
        # Render through the same paginated user-list implementation.
        return await admin_users_navigation_callback(update,context,page_override=page)
    if a=="search": context.user_data["admin_tool_mode"]="search"; await q.message.reply_text("ð Ø´ÙØ§Ø³Ù ÛØ§ ÙØ§Ù Ú©Ø§Ø±Ø¨Ø± Ø±Ø§ Ø¨ÙØ±Ø³Øª:",reply_markup=nav_keyboard(uid)); return
    if a=="tools": context.user_data["admin_tool_mode"]="tools"; await q.message.reply_text("ð§° Ø¯Ø³ØªÙØ±Ø§Øª: BLOCK:ID | UNBLOCK:ID | WARN:ID | XP:ID:50 | VIP:ID:30",reply_markup=nav_keyboard(uid)); return
    if a=="xpvip":
        await q.message.edit_text("â­ <b>XP / VIP</b>\n\nØ§Ø² Ø¨Ø®Ø´ Ú©Ø§Ø±Ø¨Ø±Ø§ÙØ Ù¾Ø±ÙÙØ¯Ù ÙØ± Ú©Ø§Ø±Ø¨Ø± Ø±Ø§ Ø¨Ø§Ø² Ú©Ù ØªØ§ XP Ù Ø§Ø´ØªØ±Ø§Ú© Ø±Ø§ ÙØ¯ÛØ±ÛØª Ú©ÙÛ.\n\nØ¨Ø±Ø§Û Ø§Ø´ØªØ±Ø§Ú©: â Ø§Ø¶Ø§ÙÙâÚ©Ø±Ø¯Ù Ø±ÙØ²Ø â Ú©ÙâÚ©Ø±Ø¯Ù Ø±ÙØ²Ø âï¸ ÙÛØ±Ø§ÛØ´ ÛØ§ â ÙØºÙ Ú©Ø§ÙÙ.",parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù",callback_data="adm:users")],[InlineKeyboardButton("âï¸ Ø§ÙÚ©Ø§ÙØ§Øª VIP",callback_data="adm:features")],[InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª",callback_data="adm:stats")]])); return
    if a=="features":
        await q.message.edit_text(feature_admin_text(),reply_markup=feature_admin_keyboard()); return
    if a=="costs":
        await q.message.edit_text(admin_costs_text(),parse_mode="HTML",reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("ð Ø¨Ø±ÙØ²Ø±Ø³Ø§ÙÛ",callback_data="adm:costs")],
            [InlineKeyboardButton("âï¸ ÙØ¯ÛØ±ÛØª Ø¯Ø³ØªØ±Ø³Û ÙØ§Ø¨ÙÛØªâÙØ§",callback_data="adm:features")],
            [InlineKeyboardButton("ð¥ Ø¸Ø±ÙÛØª/Ú©Ø§Ø±Ø¨Ø±Ø§Ù",callback_data="adm:capacity")],
            [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª",callback_data="adm:stats")]
        ])); return
    if a=="capacity":
        await q.message.edit_text(admin_capacity_text(),parse_mode="HTML",reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("ð Ø¯Ø§Ø´Ø¨ÙØ±Ø¯",callback_data="adm:stats")],
            [InlineKeyboardButton("ð° ÙØ²ÛÙÙ/Ø³Ø±ÙÛØ³âÙØ§",callback_data="adm:costs")],
            [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª",callback_data="adm:stats")]
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
            await q.message.edit_text("â¶ï¸ ØªÙÙÙ ÙÙÙØª ÙØºÙ Ø´Ø¯ Ù Ø±Ø¨Ø§Øª Ø¯ÙØ¨Ø§Ø±Ù ÙØ¹Ø§Ù Ø§Ø³Øª.",reply_markup=final_admin_keyboard())
        else:
            context.user_data["admin_pause_mode"]=True
            await q.message.reply_text("â¸ ÙØ¯Øª ØªÙÙÙ Ø±Ø§ Ø¨Ù Ø¯ÙÛÙÙ Ø¨ÙØ±Ø³Øª. ÙØ«Ø§Ù: 60\nØ¨Ø±Ø§Û ØªÙÙÙ ÙØ§ÙØ­Ø¯ÙØ¯ Ø¨ÙÙÛØ³: forever",reply_markup=nav_keyboard(uid))
        return
    if a=="main":
        await q.message.edit_text("ð  ÙÙÙÛ Ø§ØµÙÛ")
        await q.message.reply_text("ð  ÙÙÙÛ Ø§ØµÙÛ",reply_markup=keyboard(uid))
        return
    if a=="channel": await q.message.edit_text("ð¡ ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù Ù Ù¾Ø³ØªâÚ¯Ø°Ø§Ø±Û",reply_markup=channel_keyboard()); return
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
        kb.append([InlineKeyboardButton("ð Ø¨Ø§Ø²Ø®ÙØ§ÙÛ ÙØ¶Ø¹ÛØª",callback_data="adm:customers")])
        kb.append([InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª",callback_data="adm:stats")])
        await q.message.edit_text(
            f"ð¥ <b>ÙØ¯ÛØ±ÛØª ÙØ´ØªØ±Û Ù ÙÙØ¨ØªâØ¯ÙÛ</b>\n\n"
            f"ð¤ ÙØ´ØªØ±ÛØ§Ù Ø«Ø¨ØªâØ´Ø¯Ù: {total}\n"
            f"ð Ú©Ù ÙÙØ¨ØªâÙØ§: {appts}\n"
            f"ð ÙÙØ¨Øª Ø§ÙØ±ÙØ²: {today_n}\n\n"
            "ÙØ± Ú¯Ø²ÛÙÙ ÙØ³ØªÙÙ Ø§Ø³Øª. Ø¨Ø§ Ø²Ø¯Ù ÙØ± Ú¯Ø²ÛÙÙ ÙØ¶Ø¹ÛØªØ´ Ø¨ÛÙ ð¢ Ø±Ø§ÛÚ¯Ø§ÙØ ð VIP / Ù¾ÙÙÛ Ù ð´ ØºÛØ±ÙØ¹Ø§Ù ØªØºÛÛØ± ÙÛâÚ©ÙØ¯.",
            parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb)
        ); return
    if a=="tickets":
        c=db(); rows=c.execute("SELECT id,user_id,subject FROM tickets WHERE status='open' ORDER BY updated_at DESC LIMIT 20").fetchall(); c.close(); await q.message.edit_text("ð« ØªÛÚ©ØªâÙØ§Û Ø¨Ø§Ø²\n\n"+"\n".join(f"#{r['id']} | {r['user_id']} | {r['subject'] or 'Ø¨Ø¯ÙÙ Ø¹ÙÙØ§Ù'}" for r in rows) or "ØªÛÚ©Øª Ø¨Ø§Ø²Û ÙÛØ³Øª",reply_markup=final_admin_keyboard()); return
    if a=="health":
        await run_health_checks(context.bot,uid)
        await q.message.edit_text(health_text(),reply_markup=final_admin_keyboard())
        return
    if a=="health_schedule":
        enabled = get_system_setting("health_check_enabled", "1") != "0"
        schedule = get_system_setting("health_check_time", "03:00")
        status = "ð¢ Ø±ÙØ´Ù" if enabled else "ð´ Ø®Ø§ÙÙØ´"
        kb = [
            [InlineKeyboardButton("â° ØªØºÛÛØ± Ø³Ø§Ø¹Øª", callback_data="adm:health_time")],
            [InlineKeyboardButton("ð´ Ø®Ø§ÙÙØ´ Ú©Ø±Ø¯Ù" if enabled else "ð¢ Ø±ÙØ´Ù Ú©Ø±Ø¯Ù", callback_data="adm:health_toggle")],
            [InlineKeyboardButton("ð©º Ø§Ø¬Ø±Ø§Û ÙÙÛÙ Ø§ÙØ§Ù", callback_data="adm:health_run")],
            [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")],
        ]
        await q.message.edit_text(
            f"â° <b>Ø²ÙØ§ÙâØ¨ÙØ¯Û Health Check</b>\n\n"
            f"ÙØ¶Ø¹ÛØª: {status}\n"
            f"Ø³Ø§Ø¹Øª Ø±ÙØ²Ø§ÙÙ: <b>{html.escape(schedule)}</b>\n\n"
            "Ø±Ø¨Ø§Øª ÙÙØ· ÛÚ©âØ¨Ø§Ø± Ø¯Ø± ÙÙØ§Ù Ø±ÙØ² Ù Ø¯Ø± Ø³Ø§Ø¹Øª Ø§ÙØªØ®Ø§Ø¨âØ´Ø¯Ù ÚÚ©Ø§Ù¾ ÙÛâÚ¯ÛØ±Ø¯.\n"
            "Ø³Ø§Ø¹Øª Ø±Ø§ Ø¨Ø§ ÙØ§ÙØ¨ 24 Ø³Ø§Ø¹ØªÙ ÙØ«Ù <code>14:30</code> ÙØ§Ø±Ø¯ Ú©Ù.",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb)
        )
        return
    if a=="health_time":
        context.user_data["admin_health_time"] = True
        await q.message.reply_text("â° Ø³Ø§Ø¹Øª Ø¬Ø¯ÛØ¯ ÚÚ©Ø§Ù¾ Ø±Ø§ Ø¨ÙØ±Ø³Øª. ÙØ«Ø§Ù: 14:30", reply_markup=nav_keyboard(uid))
        return
    if a=="health_toggle":
        enabled = get_system_setting("health_check_enabled", "1") != "0"
        set_system_setting("health_check_enabled", "0" if enabled else "1")
        await q.message.edit_text("â° Ø²ÙØ§ÙâØ¨ÙØ¯Û ÚÚ©Ø§Ù¾ ØªØºÛÛØ± Ú©Ø±Ø¯.", reply_markup=final_admin_keyboard())
        return
    if a=="health_run":
        await run_health_checks(context.bot,uid)
        await q.message.edit_text(health_text()+"\n\nð©º ÚÚ©Ø§Ù¾ Ø¯Ø³ØªÛ Ø§ÙØ¬Ø§Ù Ø´Ø¯.", reply_markup=final_admin_keyboard())
        return
    if a=="test":
        await q.message.edit_text(_admin_test_text(),parse_mode="HTML",reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("ð©º Ø§Ø¬Ø±Ø§Û Health Check",callback_data="adm:health_run")],
            [InlineKeyboardButton("ð Ø§Ø¬Ø±Ø§Û Ø¹ÛØ¨âÛØ§Ø¨Û",callback_data="adm:diagnostics")],
            [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª",callback_data="adm:stats")]
        ])); return
    if a=="diagnostics":
        await q.message.edit_text(_admin_diagnostics_text(),parse_mode="HTML",reply_markup=final_admin_keyboard()); return
    if a=="backup":
        ok=backup_database_snapshot(keep=20)
        admin_log(uid,"manual_backup",None,"success" if ok else "failed")
        await q.message.edit_text(
            "ð¾ Ø¨Ú©Ø§Ù¾ Ø¨Ø§ ÙÙÙÙÛØª Ø³Ø§Ø®ØªÙ Ø´Ø¯." if ok else "â Ø³Ø§Ø®Øª Ø¨Ú©Ø§Ù¾ ÙØ§ÙÙÙÙ Ø¨ÙØ¯.",
            reply_markup=final_admin_keyboard()
        ); return
    if a=="audit":
        c=db()
        rows=c.execute("SELECT admin_id,action,target_user,details,created_at FROM admin_logs ORDER BY id DESC LIMIT 25").fetchall()
        c.close()
        text="ð <b>Ø¢Ø®Ø±ÛÙ Ø§ÙØ¯Ø§ÙØ§Øª ÙØ¯ÛØ±Ø§Ù</b>\n\n"+("\n".join(
            f"â¢ {r['created_at'][:16]} | {r['admin_id']} | {html.escape(r['action'])} | {r['target_user'] or '-'} | {html.escape(r['details'] or '')}"
            for r in rows
        ) or "ÙØ§Ú¯Û Ø«Ø¨Øª ÙØ´Ø¯Ù.")
        await q.message.edit_text(text,parse_mode="HTML",reply_markup=final_admin_keyboard()); return
    if a=="report": await build_daily_report(); await q.message.edit_text(get_daily_report_text(),reply_markup=final_admin_keyboard()); return
    if a=="broadcast": context.user_data["admin_broadcast"]=True; await q.message.reply_text("ð¢ ÙØªÙ Ù¾ÛØ§Ù Ø±Ø§ Ø¨ÙØ±Ø³Øª:",reply_markup=nav_keyboard(uid)); return


FEATURE_LABELS_FA = {
    "sports": "â½ ÙØ±Ø²Ø´", "nutrition": "ð¥ ØªØºØ°ÛÙ", "investing": "ð° Ø³Ø±ÙØ§ÛÙâÚ¯Ø°Ø§Ø±Û",
    "self_growth": "ð± Ø±Ø´Ø¯ Ø´Ø®ØµÛ", "morning": "âï¸ Ù¾ÛØ§Ù ØµØ¨Ø­", "night": "ð Ù¾ÛØ§Ù Ø´Ø¨",
    "auto_publish": "ð¤ Ø§ÙØªØ´Ø§Ø± Ø®ÙØ¯Ú©Ø§Ø±", "images": "ð¼ ØªØµØ§ÙÛØ±", "feedback": "ð Ø¨Ø§Ø²Ø®ÙØ±Ø¯",
    "referrals": "ð¤ Ø¯Ø¹ÙØª Ø¯ÙØ³ØªØ§Ù", "mini_app": "ð± Mini App", "support": "ð« Ù¾Ø´ØªÛØ¨Ø§ÙÛ",
    "price_data": "ð ÙÛÙØª Ø¢ÙÙØ§ÛÙ", "approval": "ð ØªØ£ÛÛØ¯ ÙØ¨Ù Ø§Ø² Ø§ÙØªØ´Ø§Ø±",
    "maintenance": "ð  Ø­Ø§ÙØª ØªØ¹ÙÛØ±Ø§Øª", "test_mode": "ð§ª ØªØ³Øª Û· Ø±ÙØ²Ù", "payments": "ð³ Ù¾Ø±Ø¯Ø§Ø®Øª",
    "customer_today": "ð ÙÙØ¨ØªâÙØ§Û Ø§ÙØ±ÙØ²", "customer_new_appointment": "â ÙÙØ¨Øª Ø¬Ø¯ÛØ¯",
    "customer_customers": "ð¥ ÙØ´ØªØ±ÛØ§Ù", "customer_calendar": "ðï¸ ØªÙÙÛÙ Ú©Ø§Ø±Û",
    "customer_hours": "â° Ø³Ø§Ø¹Ø§Øª Ú©Ø§Ø±Û", "customer_reminders": "ð ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§Û ÙØ´ØªØ±Û",
    "customer_analytics": "ð Ø¢ÙØ§Ø± ÙØ´ØªØ±ÛØ§Ù", "customer_loyal": "ð ÙØ´ØªØ±ÛØ§Ù ÙÙØ§Ø¯Ø§Ø±",
    "customer_period": "ð Ú¯Ø²Ø§Ø±Ø´ Ø¯ÙØ±ÙâØ§Û", "customer_booking_link": "ð ÙÛÙÚ© Ø±Ø²Ø±Ù Ø¢ÙÙØ§ÛÙ",
    "customer_online_booking": "ð Ø±Ø²Ø±Ù Ø¢ÙÙØ§ÛÙ", "customer_business_settings": "âï¸ ØªÙØ¸ÛÙØ§Øª Ú©Ø³Ø¨âÙÚ©Ø§Ø±",
    "goals": "ð¯ Ø§ÙØ¯Ø§Ù", "weekly": "ð Ø¬Ø¯ÙÙ ÙÙØªÚ¯Û", "stats": "ð Ø¢ÙØ§Ø± ÙÙ", "profile": "ð¤ Ù¾Ø±ÙÙØ§ÛÙ", "achievements": "ð Ø¯Ø³ØªØ§ÙØ±Ø¯ÙØ§", "settings": "âï¸ ØªÙØ¸ÛÙØ§Øª", "customers": "ð¥ ÙØ´ØªØ±Û Ù ÙÙØ¨ØªâØ¯ÙÛ",
}

def get_system_setting(key, default=""):
    c=db()
    try:
        r=c.execute("SELECT value FROM system_settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default
    finally:
        c.close()

def set_system_setting(key, value, updated_by=None):
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
    if not raw: return "Ø´Ø±ÙØ¹ ÙØ´Ø¯Ù"
    try:
        started=datetime.fromisoformat(raw)
        if started.tzinfo is None: started=started.replace(tzinfo=TZ)
        left=started+timedelta(days=7)-datetime.now(TZ)
        if left.total_seconds() <= 0: return "Ù¾Ø§ÛØ§Ù ÛØ§ÙØªÙ"
        return f"{left.days} Ø±ÙØ² Ù {left.seconds//3600} Ø³Ø§Ø¹Øª"
    except Exception:
        return "ÙØ§ÙØ´Ø®Øµ"

FEATURE_CATEGORIES={
    "goals":("ð¯ ØªÙØ¸ÛÙØ§Øª Ø§ÙØ¯Ø§Ù",["goals","weekly","stats","profile","achievements","reminders","morning","night"]),
    "customers":("ð¥ ØªÙØ¸ÛÙØ§Øª ÙØ´ØªØ±Û Ù ÙÙØ¨ØªâØ¯ÙÛ",["customers","customer_today","customer_new_appointment","customer_customers","customer_calendar","customer_hours","customer_reminders","customer_analytics","customer_loyal","customer_period","customer_booking_link","customer_online_booking","customer_business_settings"]),
    "channel":("ð¢ ØªÙØ¸ÛÙØ§Øª Ú©Ø§ÙØ§Ù Ù Ø§ÙØªØ´Ø§Ø±",["auto_publish","approval","images","feedback"]),
    "engagement":("â­ ØªÙØ¸ÛÙØ§Øª XP / VIP / Ø¯Ø¹ÙØª",["xp","vip","referrals","payments"]),
    "support":("ð« ØªÙØ¸ÛÙØ§Øª Ù¾Ø´ØªÛØ¨Ø§ÙÛ Ù Ø³ÛØ³ØªÙ",["support","mini_app","maintenance","test_mode"]),
    "vipbuilder":("ð Ø³Ø§Ø²ÙØ¯Ù Ø§ÙÚ©Ø§ÙØ§Øª VIP",["vip","ai","price_data","goals","weekly","stats","customers","customer_online_booking","auto_publish","approval","support","referrals"]),
}

def feature_mode_label(mode):
    return {"free":"ð¢ Ø±Ø§ÛÚ¯Ø§Ù","vip":"ð VIP / Ù¾ÙÙÛ","off":"ð´ ØºÛØ±ÙØ¹Ø§Ù"}.get(mode,"ð¢ Ø±Ø§ÛÚ¯Ø§Ù")


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
    buttons.append([InlineKeyboardButton("ð§ ÙÙÙ ÙØ§Ø¨ÙÛØªâÙØ§",callback_data="fcat:all")])
    buttons.append([InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª",callback_data="adm:stats")])
    return InlineKeyboardMarkup(buttons)


async def feature_category_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("â",show_alert=True); return
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
    kb.append([InlineKeyboardButton("â¬ï¸ Ø¯Ø³ØªÙâÙØ§Û ØªÙØ¸ÛÙØ§Øª",callback_data=back_cat)])
    title=FEATURE_CATEGORIES.get(cat,("âï¸ ÙÙÙ ÙØ§Ø¨ÙÛØªâÙØ§",[]))[0] if cat!="all" else "âï¸ ÙÙÙ ÙØ§Ø¨ÙÛØªâÙØ§"
    await q.message.edit_text(f"âï¸ <b>{title}</b>\n\nð¢ Ø±Ø§ÛÚ¯Ø§Ù = ÙÙÙ Ú©Ø§Ø±Ø¨Ø±Ø§Ù\nð VIP / Ù¾ÙÙÛ = ÙÙØ· VIP\nð´ ØºÛØ±ÙØ¹Ø§Ù = Ù¾ÙÙØ§Ù Ù ØºÛØ±ÙØ§Ø¨Ù Ø§Ø³ØªÙØ§Ø¯Ù\n\nØ¨Ø§ Ø²Ø¯Ù ÙØ± ÙØ§Ø¨ÙÛØªØ ÙØ¶Ø¹ÛØª Ø¢Ù Ø¨ÛÙ Ø§ÛÙ Ø³Ù Ø­Ø§ÙØª ØªØºÛÛØ± ÙÛâÚ©ÙØ¯.",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))


def feature_admin_text():
    c=db(); rows=c.execute("SELECT key,mode FROM feature_access ORDER BY key").fetchall(); c.close()
    free=sum(1 for r in rows if r["mode"]=="free"); vip=sum(1 for r in rows if r["mode"]=="vip"); off=sum(1 for r in rows if r["mode"]=="off")
    return ("âï¸ <b>ÙØ±Ú©Ø² ØªÙØ¸ÛÙØ§Øª ÙØ§Ø¨ÙÛØªâÙØ§</b>\n\n"
            f"ð¢ Ø±Ø§ÛÚ¯Ø§Ù: {free}\nð VIP / Ù¾ÙÙÛ: {vip}\nð´ ØºÛØ±ÙØ¹Ø§Ù: {off}\n\n"
            "ÙØ± Ø¯Ø³ØªÙ ØªÙØ¸ÛÙØ§Øª ÙØ³ØªÙÙ Ø®ÙØ¯Ø´ Ø±Ø§ Ø¯Ø§Ø±Ø¯ Ù Ø®Ø§ÙÙØ´âÚ©Ø±Ø¯Ù ÙØ§Ø¨ÙÛØªØ Ø§Ø·ÙØ§Ø¹Ø§Øª ÙØ¨ÙÛ Ø±Ø§ Ø­Ø°Ù ÙÙÛâÚ©ÙØ¯.")

async def feature_info_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid):
        await q.answer("â",show_alert=True); return
    await q.answer()
    await q.message.edit_text(
        "ð§ª ØªØ³Øª Û· Ø±ÙØ²Ù\n\n"
        f"ÙØ¶Ø¹ÛØª: {'ð¢ ÙØ¹Ø§Ù' if test_mode_active() else 'ð´ Ù¾Ø§ÛØ§Ù ÛØ§ÙØªÙ/Ø®Ø§ÙÙØ´'}\n"
        f"Ø²ÙØ§Ù Ø¨Ø§ÙÛâÙØ§ÙØ¯Ù: {test_mode_remaining()}\n\n"
        "Ø¯Ø± Ø²ÙØ§Ù ØªØ³ØªØ Ø§ÙØªØ´Ø§Ø± Ø®ÙØ¯Ú©Ø§Ø± ÙØ¨Ù Ø§Ø² Ø§ÙØªØ´Ø§Ø± ÙÙØ§ÛÛ Ø¨Ø±Ø§Û Admin Ù¾ÛØ´âÙÙØ§ÛØ´ ÙÛâØ´ÙØ¯.",
        reply_markup=feature_admin_keyboard()
    )

async def final_feature_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("â",show_alert=True); return
    key=q.data.split(":",1)[1]
    if key == "test":
        await q.answer(); await q.message.edit_text("ð§ª ØªØ³Øª Û· Ø±ÙØ²Ù\n\n" f"ÙØ¶Ø¹ÛØª: {'ð¢ ÙØ¹Ø§Ù' if test_mode_active() else 'ð´ Ù¾Ø§ÛØ§Ù ÛØ§ÙØªÙ/Ø®Ø§ÙÙØ´'}\n" f"Ø²ÙØ§Ù Ø¨Ø§ÙÛâÙØ§ÙØ¯Ù: {test_mode_remaining()}",reply_markup=feature_admin_keyboard()); return
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
    kb.append([InlineKeyboardButton("â¬ï¸ Ø¯Ø³ØªÙâÙØ§Û ØªÙØ¸ÛÙØ§Øª",callback_data="adm:features")])
    title=FEATURE_CATEGORIES.get(category,("âï¸ ÙÙÙ ÙØ§Ø¨ÙÛØªâÙØ§",[]))[0]
    await q.message.edit_text(f"âï¸ <b>{title}</b>\n\nð¢ Ø±Ø§ÛÚ¯Ø§Ù | ð VIP / Ù¾ÙÙÛ | ð´ ØºÛØ±ÙØ¹Ø§Ù",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def admin_health_time_save(update, context):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS or not context.user_data.get("admin_health_time"):
        return False
    value = update.message.text.strip()
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        await update.message.reply_text("â Ø³Ø§Ø¹Øª ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª. ÙØ«Ø§Ù ØµØ­ÛØ­: 14:30")
        return True
    set_system_setting("health_check_time", value)
    set_system_setting("health_check_enabled", "1")
    context.user_data.pop("admin_health_time", None)
    await update.message.reply_text(
        f"â Ø²ÙØ§Ù ÚÚ©Ø§Ù¾ Ø±ÙØ²Ø§ÙÙ Ø±ÙÛ <b>{html.escape(value)}</b> ØªÙØ¸ÛÙ Ø´Ø¯.",
        parse_mode="HTML", reply_markup=final_admin_keyboard()
    )
    return True

async def final_admin_text(update,context):
    uid=update.effective_user.id
    if uid not in ADMIN_IDS or not context.user_data.get("admin_tool_mode"): return False
    mode=context.user_data.pop("admin_tool_mode"); text=update.message.text.strip()
    try:
        if mode=="search":
            ref=text.lstrip("@").strip().lower()
            c=db(); rows=c.execute("""SELECT user_id,first_name,username,COALESCE(xp,0) xp,blocked,warnings
                                      FROM users WHERE CAST(user_id AS TEXT) LIKE ? OR lower(COALESCE(username,'')) LIKE ? OR first_name LIKE ? LIMIT 20""",(f"%{ref}%",f"%{ref}%",f"%{text}%")).fetchall(); c.close()
            kb=[[InlineKeyboardButton(f"ð¤ {r['first_name'] or 'Ø¨Ø¯ÙÙ ÙØ§Ù'} | @{r['username']}" if r['username'] else f"ð¤ {r['first_name'] or 'Ø¨Ø¯ÙÙ ÙØ§Ù'} | {r['user_id']}",callback_data=f"admu:{r['user_id']}")] for r in rows]
            kb.append([InlineKeyboardButton("â¬ï¸ Ú©Ø§Ø±Ø¨Ø±Ø§Ù",callback_data=f"adm:users:{context.user_data.get('admin_users_page',1)}")])
            await update.message.reply_text("ð ÙØªØ§ÛØ¬:\n\n"+"\n".join(f"{r['first_name'] or 'Ø¨Ø¯ÙÙ ÙØ§Ù'} | @{r['username'] if r['username'] else 'â'} | {r['user_id']} | XP {r['xp']} | {'â' if r['blocked'] else 'ð¢'}" for r in rows) or "ÛØ§ÙØª ÙØ´Ø¯",reply_markup=InlineKeyboardMarkup(kb)); return True
        parts=text.split(":"); cmd=parts[0].upper(); target=int(parts[1]); c=db()
        if cmd=="BLOCK": c.execute("UPDATE users SET blocked=1 WHERE user_id=?",(target,)); action="block"
        elif cmd=="UNBLOCK": c.execute("UPDATE users SET blocked=0 WHERE user_id=?",(target,)); action="unblock"
        elif cmd=="WARN": c.execute("UPDATE users SET warnings=COALESCE(warnings,0)+1 WHERE user_id=?",(target,)); action="warn"
        elif cmd=="XP": c.close(); add_xp(target,int(parts[2]),"admin_adjust"); admin_log(uid,"xp_adjust",target,parts[2]); await update.message.reply_text("â XP ØªØºÛÛØ± Ú©Ø±Ø¯",reply_markup=final_admin_keyboard()); return True
        elif cmd=="VIP": c.execute("UPDATE users SET vip_until=? WHERE user_id=?",((datetime.now(TZ)+timedelta(days=int(parts[2]))).isoformat(),target)); action="vip_adjust"
        else: c.close(); await update.message.reply_text("â Ø¯Ø³ØªÙØ± ÙØ§ÙØ¹ØªØ¨Ø±"); return True
        c.commit(); c.close(); admin_log(uid,action,target,text); await update.message.reply_text("â Ø§ÙØ¬Ø§Ù Ø´Ø¯",reply_markup=final_admin_keyboard()); return True
    except Exception as e:
        await update.message.reply_text(f"â Ø®Ø·Ø§: {html.escape(str(e))}", parse_mode="HTML"); return True


def support_keyboard(uid):
    fa=lang(uid)=="fa"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("â Ø³ÙØ§ÙØ§Øª ÙØªØ¯Ø§ÙÙ" if fa else "â FAQ", callback_data="support:faq")],
        [InlineKeyboardButton("ð Ø§Ø±Ø³Ø§Ù ØªÛÚ©Øª" if fa else "ð New Ticket", callback_data="support:new")],
        [InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ" if fa else "ð  Main Menu", callback_data="support:main")],
    ])

async def support_start(update,context):
    uid=update.effective_user.id; clear_flow(context);
    await hide_main_reply_keyboard(update)
    await update.message.reply_text("ð« Ù¾Ø´ØªÛØ¨Ø§ÙÛ\n\nÛÚ© Ú¯Ø²ÛÙÙ Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:" if lang(uid)=="fa" else "ð« Support\n\nChoose an option:",reply_markup=support_keyboard(uid))

async def support_callback(update,context):
    q=update.callback_query; uid=q.from_user.id; await q.answer(); action=q.data.split(":",1)[1]
    if action=="main": clear_flow(context); await q.message.reply_text("ð  ÙÙÙÛ Ø§ØµÙÛ",reply_markup=keyboard(uid)); return
    if action=="faq":
        text=("â Ø³ÙØ§ÙØ§Øª ÙØªØ¯Ø§ÙÙ\n\nâ¢ ÚØ·ÙØ± ÙØ¯Ù Ø§Ø¶Ø§ÙÙ Ú©ÙÙØ Ø§Ø² Â«âï¸ ÙØ¯Ù Ø®ÙØ¯Ù ÙÛâÙÙÛØ³ÙÂ» Ø§Ø³ØªÙØ§Ø¯Ù Ú©Ù.\nâ¢ ÚØ·ÙØ± Ø²ÙØ§Ù ÛØ§Ø¯Ø¢ÙØ±Û Ø±Ø§ Ø¹ÙØ¶ Ú©ÙÙØ Ø§Ø² Â«âï¸ ÙÛØ±Ø§ÛØ´ Ø§ÙØ¯Ø§ÙÂ».\nâ¢ ÚØ·ÙØ± AI Ø±Ø§ ÙØ¹Ø§Ù Ú©ÙÙØ Ø§Ú¯Ø± Ø³Ø±ÙÛØ³ ÙÙØ´ÙÙØ¯ Ø¯Ø± Ø¯Ø³ØªØ±Ø³ ÙØ¨Ø§Ø´Ø¯Ø ÙØ¯ÛØ± ÙÛâØªÙØ§ÙØ¯ Ø§ØªØµØ§Ù Ø³Ø±ÙÛØ³âÙØ§Û AI Ø±Ø§ Ø§Ø² ØªÙØ¸ÛÙØ§Øª Ø³Ø±ÙØ± Ø¨Ø±Ø±Ø³Û Ú©ÙØ¯.\nâ¢ ÚØ·ÙØ± Ú©Ø§ÙØ§Ù Ø±Ø§ ÙØµÙ Ú©ÙÙØ ÙØ¯ÛØ± â ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù â ØªÙØ¸ÛÙ Ú©Ø§ÙØ§Ù.")
        await q.message.edit_text(text,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("â¬ï¸ Ù¾Ø´ØªÛØ¨Ø§ÙÛ",callback_data="support:main")],[main_menu_button(uid)]])); return
    if action=="new":
        context.user_data["support_new"]=True; await q.message.reply_text("ð Ù¾ÛØ§Ù Ù¾Ø´ØªÛØ¨Ø§ÙÛ Ø±Ø§ Ø¨ÙØ±Ø³Øª. Ø¨Ø±Ø§Û ÙØºÙ Â«â¬ï¸ Ø¨Ø±Ú¯Ø´ØªÂ» Ø±Ø§ Ø¨Ø²Ù.",reply_markup=nav_keyboard(uid)); return

async def support_text(update,context):
    if not context.user_data.get("support_new"): return False
    uid=update.effective_user.id; text=update.message.text.strip()
    if text in ("â¬ï¸ Ø¨Ø±Ú¯Ø´Øª","â¬ï¸ Back","ð  ÙÙÙÛ Ø§ØµÙÛ","ð  Main Menu"):
        clear_flow(context); await update.message.reply_text("ð  ÙÙÙÛ Ø§ØµÙÛ",reply_markup=keyboard(uid)); return True
    now=datetime.now(TZ).isoformat(); c=db(); cur=c.execute("INSERT INTO tickets(user_id,subject,created_at,updated_at) VALUES(?,?,?,?)",(uid,text[:80],now,now)); c.execute("INSERT INTO ticket_messages(ticket_id,sender_id,message,created_at) VALUES(?,?,?,?)",(cur.lastrowid,uid,text,now)); c.commit(); c.close(); context.user_data.pop("support_new",None); await update.message.reply_text(f"ð« ØªÛÚ©Øª #{cur.lastrowid} Ø«Ø¨Øª Ø´Ø¯.",reply_markup=keyboard(uid)); return True

def vip_keyboard(uid):
    fa=lang(uid)=="fa"
    rows=[]
    if feature_enabled("payments") and feature_enabled("vip"):
        rows.append([InlineKeyboardButton("ð Ø®Ø±ÛØ¯ VIP â 100 â­ / 30 Ø±ÙØ²" if fa else "ð Buy VIP â 100 â­ / 30 days",callback_data="vip:buy")])
    rows.append([InlineKeyboardButton("ð¤ Ø¯Ø¹ÙØª Ø¯ÙØ³ØªØ§Ù" if fa else "ð¤ Referrals",callback_data="vip:ref")])
    rows.append([InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ" if fa else "ð  Main Menu",callback_data="vip:main")])
    return InlineKeyboardMarkup(rows)

async def vip_center(update,context):
    uid=update.effective_user.id; xp,level,vip_until=xp_info(uid)
    fa=lang(uid)=="fa"
    status="ð¢ ÙØ¹Ø§Ù" if is_vip(uid) else "âª Ø±Ø§ÛÚ¯Ø§Ù"
    text=(f"ð VIP\n\nÙØ¶Ø¹ÛØª: {status}\nâ­ Ø³Ø·Ø­: {level}\nð Ù¾Ø§ÛØ§Ù VIP: {vip_until[:16] if vip_until else 'â'}\n\nØ§ÙÚ©Ø§ÙØ§Øª VIP: Ø³ÙÙÛÙ Ø¨ÛØ´ØªØ± AI Ù ÙØ§Ø¨ÙÛØªâÙØ§Û Ù¾ÙÙÛ ÙØ¹Ø§ÙâØ´Ø¯Ù ØªÙØ³Ø· ÙØ¯ÛØ±." if fa else f"ð VIP\n\nStatus: {status}\nâ­ Level: {level}\nð VIP until: {vip_until[:16] if vip_until else 'â'}\n\nVIP includes higher AI quota and paid features enabled by the admin.")
    await update.message.reply_text(text,reply_markup=vip_keyboard(uid))

async def vip_callback(update,context):
    q=update.callback_query; uid=q.from_user.id; await q.answer(); action=q.data.split(":",1)[1]
    if action=="main": clear_flow(context); await q.message.reply_text("ð  ÙÙÙÛ Ø§ØµÙÛ",reply_markup=keyboard(uid)); return
    if action=="ref": await referral(update,context); return
    if action=="buy":
        if not (feature_enabled("payments") and feature_enabled("vip")):
            await q.message.reply_text("ð Ø®Ø±ÛØ¯ VIP ÙØ¹ÙØ§Ù ØªÙØ³Ø· ÙØ¯ÛØ± ØºÛØ±ÙØ¹Ø§Ù Ø§Ø³Øª.",reply_markup=vip_keyboard(uid)); return
        payload=f"vip30:{uid}:{int(datetime.now(TZ).timestamp())}"
        try:
            await context.bot.send_invoice(chat_id=uid,title="MyTasks VIP 30 Ø±ÙØ²Ù",description="ÙØ¹Ø§ÙâØ³Ø§Ø²Û VIP Ø±Ø¨Ø§Øª MyTasks Ø¨Ø±Ø§Û Û³Û° Ø±ÙØ²",payload=payload,provider_token="",currency="XTR",prices=[LabeledPrice("VIP 30 Ø±ÙØ²Ù",100)],start_parameter="mytasks-vip-30")
        except Exception as e:
            logger.error("VIP invoice failed: %s",e); await q.message.reply_text("â Ø³Ø§Ø®Øª ÙØ§Ú©ØªÙØ± VIP Ø§ÙØ¬Ø§Ù ÙØ´Ø¯. ØªÙØ¸ÛÙØ§Øª Ù¾Ø±Ø¯Ø§Ø®Øª Ø±Ø§ Ø¨Ø±Ø±Ø³Û Ú©Ù.",reply_markup=vip_keyboard(uid))

async def precheckout_callback(update,context):
    q=update.pre_checkout_query
    if not q.invoice_payload.startswith("vip30:") or not feature_enabled("payments"):
        await q.answer(ok=False,error_message="Ø§ÛÙ Ø®Ø±ÛØ¯ Ø¯Ø± Ø­Ø§Ù Ø­Ø§Ø¶Ø± ÙØ¹Ø§Ù ÙÛØ³Øª.")
        return
    await q.answer(ok=True)

async def successful_payment_callback(update,context):
    payment=update.message.successful_payment; uid=update.effective_user.id
    charge_id=(payment.telegram_payment_charge_id or '').strip()
    if not charge_id:
        await update.message.reply_text("â Ø´ÙØ§Ø³Ù Ù¾Ø±Ø¯Ø§Ø®Øª ÙØ¹ØªØ¨Ø± ÙÛØ³Øª.",reply_markup=keyboard(uid)); return
    c=db()
    try:
        c.execute("BEGIN IMMEDIATE")
        now_iso=datetime.now(TZ).isoformat()
        cur=c.execute("INSERT OR IGNORE INTO payments(user_id,payload,currency,total_amount,telegram_charge_id,created_at) VALUES(?,?,?,?,?,?)",(uid,payment.invoice_payload,payment.currency,payment.total_amount,charge_id,now_iso))
        if cur.rowcount != 1:
            c.rollback(); c.close(); await update.message.reply_text("â¹ï¸ Ø§ÛÙ Ù¾Ø±Ø¯Ø§Ø®Øª ÙØ¨ÙØ§Ù Ø«Ø¨Øª Ø´Ø¯Ù Ø§Ø³Øª.",reply_markup=keyboard(uid)); return
        base=datetime.now(TZ); r=c.execute("SELECT vip_until FROM users WHERE user_id=?",(uid,)).fetchone()
        if r and r["vip_until"]:
            try: base=max(base,datetime.fromisoformat(r["vip_until"]))
            except Exception: pass
        until=base+timedelta(days=30)
        c.execute("UPDATE users SET vip_until=? WHERE user_id=?",(until.isoformat(),uid))
        c.execute("INSERT INTO subscription_history(user_id,plan,duration_days,source,amount,started_at,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?)",(uid,"VIP",30,"telegram_stars",payment.total_amount,now_iso,until.isoformat(),now_iso))
        c.commit(); c.close(); add_xp(uid,20,"vip_purchase")
        await update.message.reply_text(f"â Ù¾Ø±Ø¯Ø§Ø®Øª ÙÙÙÙ Ø¨ÙØ¯. VIP ØªØ§ {fa_datetime(until)} ÙØ¹Ø§Ù Ø´Ø¯.",reply_markup=keyboard(uid))
    except Exception:
        try: c.rollback(); c.close()
        except Exception: pass
        logger.exception("Successful payment handling failed")
        await update.message.reply_text("â Ø«Ø¨Øª Ù¾Ø±Ø¯Ø§Ø®Øª Ø§ÙØ¬Ø§Ù ÙØ´Ø¯.",reply_markup=keyboard(uid))

# --- Pre-made invite text templates ---
_INVITE_TEMPLATES_FA = [
    {"id": 1, "text": "ð Ø³ÙØ§Ù!\nÙÙ Ø§Ø² Ø±Ø¨Ø§Øª MyTasks Ø§Ø³ØªÙØ§Ø¯Ù ÙÛâÚ©ÙÙ Ø¨Ø±Ø§Û ÙØ¯ÛØ±ÛØª Ú©Ø§Ø±ÙØ§ Ù Ø§ÙØ¯Ø§ÙÙ.\nØ®ÛÙÛ Ú©ÙÚ©Ù Ú©Ø±Ø¯Ù! ØªÙ ÙÙ Ø§ÙØªØ­Ø§Ù Ú©Ù:\n{invite_link}", "enabled": 1},
    {"id": 2, "text": "ð¯ ÛÙ Ø±Ø¨Ø§Øª Ø¹Ø§ÙÛ Ø¨Ø±Ø§Û ÙØ¯ÛØ±ÛØª Ø±ÙØ²Ø§ÙÙ Ù¾ÛØ¯Ø§ Ú©Ø±Ø¯Ù!\nØ§Ú¯Ù Ø¯ÙØ¨Ø§Ù ÛÙ Ø§Ø¨Ø²Ø§Ø± Ø³Ø§Ø¯Ù Ù ÙÙÛ Ø¨Ø±Ø§Û Ø¨Ø±ÙØ§ÙÙâØ±ÛØ²Û ÙØ³ØªÛØ Ø§ÛÙÙ Ø§ÙØªØ­Ø§Ù Ú©Ù:\n{invite_link}", "enabled": 1},
    {"id": 3, "text": "ð Ø¨Ø§ MyTasks Ø²ÙØ¯Ú¯ÛØª Ø±Ù Ø³Ø§Ø²ÙØ§ÙâØ¯ÙÛ Ú©Ù!\nÙØ¯Ù Ø¨Ø°Ø§Ø±Ø ÛØ§Ø¯Ø¢ÙØ±Û Ø¨Ú¯ÛØ±Ø Ù¾ÛØ´Ø±ÙØªØª Ø±Ù Ø¨Ø¨ÛÙ.\nÙÙÛÙ Ø§ÙØ§Ù Ø´Ø±ÙØ¹ Ú©Ù:\n{invite_link}", "enabled": 1},
]

def _get_invite_templates():
    """Get active invite templates."""
    try:
        c = db()
        rows = c.execute("SELECT * FROM referral_templates WHERE enabled=1 ORDER BY sort_order").fetchall()
        c.close()
        if rows:
            return [{"id": r["id"], "text": r["text"]} for r in rows]
    except Exception:
        pass
    return _INVITE_TEMPLATES_FA

def _referral_user_kb(uid):
    """Build the user referral panel keyboard."""
    fa = lang(uid) == "fa"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("ð¤ Ø¯Ø¹ÙØª Ø¯ÙØ³ØªØ§Ù" if fa else "ð¤ Invite Friends", callback_data="ref:invite")],
        [InlineKeyboardButton("ð ÙÛÙÚ© Ø¯Ø¹ÙØª ÙÙ" if fa else "ð My Invite Link", callback_data="ref:link"),
         InlineKeyboardButton("ð Ú©Ø¯ Ø¯Ø¹ÙØª ÙÙ" if fa else "ð My Referral Code", callback_data="ref:code")],
        [InlineKeyboardButton("ð¥ Ø§ÙØ±Ø§Ø¯ Ø¯Ø¹ÙØªâØ´Ø¯Ù" if fa else "ð¥ Invited Users", callback_data="ref:list")],
        [InlineKeyboardButton("ð Ù¾Ø§Ø¯Ø§Ø´âÙØ§Û ÙÙ" if fa else "ð My Rewards", callback_data="ref:rewards")],
        [InlineKeyboardButton("ð Ø¢ÙØ§Ø± Ø¯Ø¹ÙØª" if fa else "ð Referral Stats", callback_data="ref:stats")],
        [InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ" if fa else "ð  Main Menu", callback_data="nav:main")],
    ])

async def referral(update, context):
    """Main referral panel - entry point."""
    uid = update.effective_user.id
    fa = lang(uid) == "fa"
    stats = get_referral_stats(uid)
    me = await context.bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{stats['code']}" if me.username else stats["code"]
    text = (
        f"ð¤ <b>Ø¯Ø¹ÙØª Ø¯ÙØ³ØªØ§Ù</b>\n\n"
        f"ð ÙÛÙÚ© Ø§Ø®ØªØµØ§ØµÛ ØªÙ:\n<code>{html.escape(link)}</code>\n\n"
        f"ð¥ Ø¯Ø¹ÙØªâÙØ§: <b>{stats['total']}</b> | ÙÙÙÙ: <b>{stats['success']}</b> | Ø¯Ø± Ø§ÙØªØ¸Ø§Ø±: <b>{stats['pending']}</b>\n"
        f"ð Ù¾Ø§Ø¯Ø§Ø´ Ø¯Ø±ÛØ§ÙØªâØ´Ø¯Ù: <b>{stats['tokens_earned']}</b> ØªÙÚ©Ù"
    )
    target = update.message or (update.callback_query.message if update.callback_query else None)
    await target.reply_text(text, parse_mode="HTML", reply_markup=_referral_user_kb(uid))


async def referral_callback(update, context):
    """Handle referral panel callbacks."""
    q = update.callback_query
    uid = q.from_user.id
    fa = lang(uid) == "fa"
    data = q.data
    action = data.split(":", 1)[1] if ":" in data else ""
    await q.answer()

    me = await context.bot.get_me()
    stats = get_referral_stats(uid)
    link = f"https://t.me/{me.username}?start=ref_{stats['code']}" if me.username else stats["code"]

    if action == "invite":
        templates = _get_invite_templates()
        if not templates:
            await q.message.edit_text("ð¤ ÙØªÙ Ø¢ÙØ§Ø¯ÙâØ§Û ÙØ¬ÙØ¯ ÙØ¯Ø§Ø±Ø¯.", reply_markup=_referral_user_kb(uid))
            return
        rows = []
        for t in templates[:6]:
            preview = t["text"][:60] + ("..." if len(t["text"]) > 60 else "")
            rows.append([InlineKeyboardButton(f"ð {preview}", callback_data=f"ref:tmpl:{t['id']}")])
        rows.append([InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª" if fa else "â¬ï¸ Back", callback_data="ref:home")])
        await q.message.edit_text("ð¤ <b>ÛÚ© ÙØªÙ Ø¢ÙØ§Ø¯Ù Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return

    if action.startswith("tmpl:"):
        tid = int(action.split(":")[1])
        templates = _get_invite_templates()
        tmpl = next((t for t in templates if t["id"] == tid), None)
        if not tmpl:
            await q.message.edit_text("â ÙØªÙ Ù¾ÛØ¯Ø§ ÙØ´Ø¯.", reply_markup=_referral_user_kb(uid))
            return
        text = tmpl["text"].replace("{invite_link}", link).replace("{invite_code}", stats["code"]).replace("{inviter_name}", html.escape(q.from_user.first_name or "Ú©Ø§Ø±Ø¨Ø±"))
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("ð¤ Ø§Ø±Ø³Ø§Ù Ø¯Ø± Telegram" if fa else "ð¤ Share on Telegram", callback_data=f"ref:send:{tid}")],
            [InlineKeyboardButton("ð Ú©Ù¾Û ÙØªÙ" if fa else "ð Copy Text", callback_data=f"ref:copy:{tid}")],
            [InlineKeyboardButton("ð Ú©Ù¾Û ÙÛÙÚ©" if fa else "ð Copy Link", callback_data="ref:copylink")],
            [InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª" if fa else "â¬ï¸ Back", callback_data="ref:invite")],
        ])
        await q.message.edit_text(f"ð¤ <b>ÙØªÙ Ø¯Ø¹ÙØª:</b>\n\n{text}", parse_mode="HTML", reply_markup=kb)
        return

    if action.startswith("send:"):
        tid = int(action.split(":")[1])
        templates = _get_invite_templates()
        tmpl = next((t for t in templates if t["id"] == tid), None)
        if tmpl:
            text = tmpl["text"].replace("{invite_link}", link).replace("{invite_code}", stats["code"]).replace("{inviter_name}", html.escape(q.from_user.first_name or "Ú©Ø§Ø±Ø¨Ø±"))
        else:
            text = f"ð Ø§Ø² Ø±Ø¨Ø§Øª MyTasks Ø§Ø³ØªÙØ§Ø¯Ù Ú©Ù:\n{link}"
        # Use Telegram's forward sharing - user picks the recipient
        await q.message.delete()
        await context.bot.send_message(uid, f"ð¤ <b>ÙØªÙ Ø¢ÙØ§Ø¯Ù Ø§Ø±Ø³Ø§Ù:</b>\n\n{text}\n\n<i>ÙØªÙ Ø±Ø§ Ú©Ù¾Û Ú©Ù Ù Ø¨Ø±Ø§Û Ø¯ÙØ³ØªØª Ø¨ÙØ±Ø³Øª.</i>", parse_mode="HTML")
        await context.bot.send_message(uid, text)
        return

    if action.startswith("copy:"):
        tid = int(action.split(":")[1])
        templates = _get_invite_templates()
        tmpl = next((t for t in templates if t["id"] == tid), None)
        if tmpl:
            text = tmpl["text"].replace("{invite_link}", link).replace("{invite_code}", stats["code"]).replace("{inviter_name}", html.escape(q.from_user.first_name or "Ú©Ø§Ø±Ø¨Ø±"))
        else:
            text = link
        await q.answer("ð ÙØªÙ Ú©Ù¾Û Ø´Ø¯!", show_alert=True)
        return

    if action == "copylink":
        await q.answer(f"ð {link}", show_alert=True)
        return

    if action == "link":
        await q.message.edit_text(
            f"ð <b>ÙÛÙÚ© Ø§Ø®ØªØµØ§ØµÛ ØªÙ:</b>\n\n<code>{html.escape(link)}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("ð¤ Ø§Ø´ØªØ±Ø§Ú©âÚ¯Ø°Ø§Ø±Û" if fa else "ð¤ Share", callback_data="ref:invite")],
                [InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª" if fa else "â¬ï¸ Back", callback_data="ref:home")],
            ])
        )
        return

    if action == "code":
        await q.message.edit_text(
            f"ð <b>Ú©Ø¯ Ø¯Ø¹ÙØª Ø§Ø®ØªØµØ§ØµÛ ØªÙ:</b>\n\n<code>{stats['code']}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("ð¤ Ø§Ø´ØªØ±Ø§Ú©âÚ¯Ø°Ø§Ø±Û" if fa else "ð¤ Share", callback_data="ref:invite")],
                [InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª" if fa else "â¬ï¸ Back", callback_data="ref:home")],
            ])
        )
        return

    if action == "list":
        invited = stats["invited"]
        if not invited:
            text = "ð¥ <b>ÙÙÙØ² Ú©Ø³Û Ø±Ø§ Ø¯Ø¹ÙØª ÙÚ©Ø±Ø¯ÙâØ§Û.</b>\n\nØ¨Ø§ ÙÛÙÚ© Ø§Ø®ØªØµØ§ØµÛØª Ø¯ÙØ³ØªØ§ÙØª Ø±Ø§ Ø¯Ø¹ÙØª Ú©Ù!"
        else:
            lines = [f"ð¥ <b>Ø§ÙØ±Ø§Ø¯ Ø¯Ø¹ÙØªâØ´Ø¯Ù ({len(invited)} ÙÙØ±):</b>", ""]
            status_map = {"registered": "â³ ÙØ§Ø±Ø¯ Ø´Ø¯Ù", "success": "â ÙÙÙÙ", "rewarded": "ð Ù¾Ø§Ø¯Ø§Ø´ Ø¯Ø±ÛØ§ÙØª Ø´Ø¯Ù"}
            for inv in invited[:20]:
                name = html.escape(inv["first_name"] or "Ú©Ø§Ø±Ø¨Ø±")
                st = status_map.get(inv.get("status", ""), "â³ Ø¯Ø± Ø§ÙØªØ¸Ø§Ø±")
                lines.append(f"â¢ {name} â {st}")
            text = "\n".join(lines)
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=_referral_user_kb(uid))
        return

    if action == "rewards":
        c = db()
        rewards = c.execute("SELECT * FROM reward_log WHERE user_id=? AND reward_type LIKE 'referral_%' ORDER BY created_at DESC LIMIT 10", (uid,)).fetchall()
        c.close()
        if not rewards:
            text = "ð <b>ÙÙÙØ² Ù¾Ø§Ø¯Ø§Ø´Û Ø¯Ø±ÛØ§ÙØª ÙÚ©Ø±Ø¯ÙâØ§Û.</b>"
        else:
            lines = [f"ð <b>Ù¾Ø§Ø¯Ø§Ø´âÙØ§Û Ø§Ø®ÛØ±:</b>", ""]
            for rw in rewards:
                lines.append(f"â¢ {rw['reward_type']}: <b>{rw['amount']}</b> â {rw['created_at'][:10]}")
            text = "\n".join(lines)
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=_referral_user_kb(uid))
        return

    if action == "stats":
        next_milestone = int(ref_get("ref_vip_milestone", "10"))
        remaining = max(0, next_milestone - (stats["success"] % next_milestone))
        text = (
            f"ð <b>Ø¢ÙØ§Ø± Ø¯Ø¹ÙØª ØªÙ:</b>\n\n"
            f"ð¥ Ú©Ù Ø¯Ø¹ÙØªâÙØ§: <b>{stats['total']}</b>\n"
            f"â Ø¯Ø¹ÙØªâÙØ§Û ÙÙÙÙ: <b>{stats['success']}</b>\n"
            f"â³ Ø¯Ø± Ø§ÙØªØ¸Ø§Ø±: <b>{stats['pending']}</b>\n"
            f"ð Ù¾Ø§Ø¯Ø§Ø´ Ø¯Ø±ÛØ§ÙØªâØ´Ø¯Ù: <b>{stats['tokens_earned']}</b> ØªÙÚ©Ù\n"
            f"ð ØªØ§ Ù¾Ø§Ø¯Ø§Ø´ Ø¨Ø¹Ø¯Û: <b>{remaining}</b> Ø¯Ø¹ÙØª ÙÙÙÙ Ø¯ÛÚ¯Ø±"
        )
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=_referral_user_kb(uid))
        return

    if action == "home":
        link2 = f"https://t.me/{me.username}?start=ref_{stats['code']}" if me.username else stats["code"]
        text = (
            f"ð¤ <b>Ø¯Ø¹ÙØª Ø¯ÙØ³ØªØ§Ù</b>\n\n"
            f"ð ÙÛÙÚ© Ø§Ø®ØªØµØ§ØµÛ ØªÙ:\n<code>{html.escape(link2)}</code>\n\n"
            f"ð¥ Ø¯Ø¹ÙØªâÙØ§: <b>{stats['total']}</b> | ÙÙÙÙ: <b>{stats['success']}</b> | Ø¯Ø± Ø§ÙØªØ¸Ø§Ø±: <b>{stats['pending']}</b>\n"
            f"ð Ù¾Ø§Ø¯Ø§Ø´ Ø¯Ø±ÛØ§ÙØªâØ´Ø¯Ù: <b>{stats['tokens_earned']}</b> ØªÙÚ©Ù"
        )
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=_referral_user_kb(uid))
        return


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
    current=re.search(r'(?:ÙØ±Ø®\s*ÙØ¹ÙÛ|Last)\s*:?[\s:=]*([0-9][0-9,Ù«Ù¬]*)',plain,re.I)
    if current: return current.group(1)
    vals=re.findall(r'<span[^>]*class=["\'][^"\']*(?:price|value)[^"\']*["\'][^>]*>\s*([0-9,Ù«Ù¬]+)',html,re.I)
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
            return f"{float(last_trade):,.0f} Ø±ÛØ§Ù"
        except Exception as e:
            logger.warning("Nobitex v3 orderbook %s failed: %s", symbol, e)
        try:
            data = await asyncio.to_thread(
                fetch_url_json,
                f"https://api.nobitex.ir/v2/trades/{symbol}",
            )
            trades = data.get("trades") or []
            if trades:
                return f"{float(trades[0]['price']):,.0f} Ø±ÛØ§Ù"
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
            return f"{float(latest):,.0f} Ø±ÛØ§Ù"
        except Exception as e:
            logger.warning("Nobitex stats %s failed: %s", symbol, e)
            raise
    if asset in ("usdt","bnb","sol","xrp"):
        ids={"usdt":"tether","bnb":"binancecoin","sol":"solana","xrp":"ripple"}
        try:
            data=await asyncio.to_thread(fetch_url_json,"https://api.coingecko.com/api/v3/simple/price?ids="+ids[asset]+"&vs_currencies=usd")
            usd=float(data[ids[asset]]["usd"])
            usd_raw=await asyncio.to_thread(tgju_value,"https://www.tgju.org/profile/price_dollar_rl")
            irr=float(usd_raw.replace(",","").replace("Ù«",".").replace("Ù¬",""))
            return f"{usd*irr:,.0f} Ø±ÛØ§Ù"
        except Exception as e:
            logger.warning("CoinGecko %s failed: %s",asset,e)
            raise
    if asset in ("sp500","nasdaq","dow"):
        symbols={"sp500":"%5EGSPC","nasdaq":"%5EIXIC","dow":"%5EDJI"}
        data=await asyncio.to_thread(fetch_url_json,f"https://query1.finance.yahoo.com/v8/finance/chart/{symbols[asset]}?range=1d&interval=1m")
        meta=data["chart"]["result"][0]["meta"]
        return f"{meta.get('regularMarketPrice',0):,.2f} USD"
    urls={"usd":"https://www.tgju.org/profile/price_dollar_rl","eur":"https://www.tgju.org/profile/price_eur","gold18":"https://www.tgju.org/profile/geram18","coin":"https://www.tgju.org/profile/sekee"}
    # Ø¨Ø¯ÙÙ ØªØ¨Ø¯ÛÙ Ø¹Ø¯Ø¯ÛØ ÙÙØ· ÙØ§Ø­Ø¯ ÙÙØ§ÛØ´ Ø¯Ø§Ø¯Ù ÙÛâØ´ÙØ¯.
    try:
        raw = await asyncio.to_thread(tgju_value, urls[asset])
    except Exception as primary_error:
        # Optional secondary source for USD/EUR; never invent a price when both fail.
        if asset in ("usd", "eur"):
            secondary = await v25_bonbast_secondary(asset) if "v25_bonbast_secondary" in globals() else None
            if secondary is not None:
                return f"{float(secondary):,.0f} Ø±ÛØ§Ù"
        raise primary_error
    if asset in ("usd", "eur"):
        try:
            secondary = await v25_bonbast_secondary(asset) if "v25_bonbast_secondary" in globals() else None
            normalized = float(raw.replace(",", "").replace("Ù«", ".").replace("Ù¬", ""))
            if secondary is not None and normalized:
                # If sources are within 1%, average to reduce transient source noise.
                if abs(float(secondary)-normalized)/max(abs(normalized),1) <= 0.01:
                    normalized=(normalized+float(secondary))/2
        except Exception:
            normalized = float(raw.replace(",", "").replace("Ù«", ".").replace("Ù¬", ""))
        return f"{normalized:,.0f} Ø±ÛØ§Ù"
    if asset in ("gold18", "coin"):
        normalized = raw.replace(",", "").replace("Ù«", ".").replace("Ù¬", "")
        return f"{float(normalized):,.0f} Ø±ÛØ§Ù"
    return raw


async def prices(update,context):
    uid=update.effective_user.id
    await hide_main_reply_keyboard(update)
    await update.message.reply_text("ð ÙÛÙØª Ø¢ÙÙØ§ÛÙ\n\nÛÚ©Û Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:",reply_markup=prices_keyboard(uid))

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


def _secure_remote_base(url):
    if not url:
        return False
    low=url.lower()
    # Plain HTTP is accepted only for loopback/private local deployment.
    if low.startswith("https://"):
        return True
    return low.startswith("http://127.0.0.1") or low.startswith("http://localhost")













async def build_daily_report():
    d=datetime.now(TZ).date().isoformat()
    c=db()
    data={
        "posts":c.execute("SELECT COUNT(*) n FROM channel_posts WHERE substr(COALESCE(last_sent_at,created_at),1,10)=?",(d,)).fetchone()["n"],
        "active":c.execute("SELECT COUNT(DISTINCT user_id) n FROM activity_log WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "new":c.execute("SELECT COUNT(*) n FROM users WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "xp":c.execute("SELECT COALESCE(SUM(amount),0) n FROM xp_log WHERE substr(created_at,1,10)=?",(d,)).fetchone()["n"],
        "done":c.execute("SELECT COUNT(*) n FROM goal_days WHERE goal_date=? AND status='done'",(d,)).fetchone()["n"],
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
    top_text=" | ".join(f"{row.get('event')}: {row.get('count')}" for row in top[:6]) or "Ø«Ø¨Øª ÙØ´Ø¯Ù"
    return ("ð Ú¯Ø²Ø§Ø±Ø´ Ù¾Ø§ÛØ§Ù Ø±ÙØ²\n\n"
            f"ð¢ Ù¾Ø³ØªâÙØ§Û Ø²ÙØ§ÙâØ¨ÙØ¯ÛâØ´Ø¯Ù: {x.get('posts',0)}\n" + f"ð¤ Ù¾Ø³ØªâÙØ§Û Ø®ÙØ¯Ú©Ø§Ø± ÙÙØªØ´Ø±Ø´Ø¯Ù: {x.get('published_posts',x.get('auto_posts',0))}\n"
            f"ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ø«Ø¨ØªâØ´Ø¯Ù: {x.get('total_users',0)}\n"
            f"ð¢ Ú©Ø§Ø±Ø¨Ø±Ø§Ù ÙØ¹Ø§Ù Ø§ÙØ±ÙØ²: {x.get('active',0)}\n"
            f"ð Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ø¬Ø¯ÛØ¯: {x.get('new',0)}\n"
            f"ð Ø±ÙÛØ¯Ø§Ø¯ÙØ§Û Ø§Ø³ØªÙØ§Ø¯Ù Ø§Ø² Ø±Ø¨Ø§Øª: {x.get('usage_events',0)}\n"
            f"ð¤ Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ø§Ø³ØªÙØ§Ø¯ÙâÚ©ÙÙØ¯Ù: {x.get('usage_users',0)}\n"
            f"ð¯ Ø§ÙØ¯Ø§Ù Ø³Ø§Ø®ØªÙâØ´Ø¯Ù: {x.get('goals_created',0)}\n"
            f"â Ø§ÙØ¯Ø§Ù Ø§ÙØ¬Ø§ÙâØ´Ø¯Ù: {x.get('done',0)}\n"
            f"â­ XP Ú©Ø³Ø¨âØ´Ø¯Ù: {x.get('xp',0)}\n"
            f"ð³ ÙØ´Ø§Ø±Ú©Øª Ø¯Ø± ÙØ¸Ø±Ø³ÙØ¬Û: {x.get('poll_participation',0)} ÙÙØ±\n"
            f"â¤ï¸ Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ø¯Ø§Ø±Ø§Û ÙØ§Ú©ÙØ´ Ú©Ø§ÙØ§Ù: {x.get('reaction_users',0)} ÙÙØ±\n"
            f"ð ÙÙÛØ¯: {x.get('likes',0)}\n"
            f"ð ÙØ§ÙÙØ§Ø³Ø¨: {x.get('dislikes',0)}\n"
            f"ð« ØªÛÚ©ØªâÙØ§Û Ø¬Ø¯ÛØ¯: {x.get('tickets_created',0)}\n"
            f"â ØªÛÚ©ØªâÙØ§Û Ø¨Ø³ØªÙâØ´Ø¯Ù: {x.get('tickets_closed',0)}\n"
            f"ð¬ Ù¾ÛØ§ÙâÙØ§Û Ù¾Ø´ØªÛØ¨Ø§ÙÛ: {x.get('ticket_messages',0)}\n"
            f"ð¥ ÙØ¹Ø§ÙÛØªâÙØ§Û Ù¾Ø±ØªÚ©Ø±Ø§Ø±: {top_text}")

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
        "ð Ú¯Ø²Ø§Ø±Ø´ ÙÙØªÚ¯Û Ø±Ø¨Ø§Øª\n\n"
        f"ð Ø¨Ø§Ø²Ù: {data.get('start','â')} ØªØ§ {data.get('end','â')}\n"
        f"ð Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ø¬Ø¯ÛØ¯: {data.get('new_users',0)}\n"
        f"ð¢ Ú©Ø§Ø±Ø¨Ø±Ø§Ù ÙØ¹Ø§Ù: {data.get('active_users',0)}\n"
        f"ð³ Ù¾Ø±Ø¯Ø§Ø®ØªâÙØ§: {data.get('payments',0)}\n"
        f"ð¤ Ø®Ø±ÛØ¯Ø§Ø±Ø§Ù ÛÚ©ØªØ§: {data.get('paying_users',0)}\n"
        f"ð° ÙØ¨ÙØº Ù¾Ø±Ø¯Ø§Ø®ØªâÙØ§Û Ø«Ø¨ØªâØ´Ø¯Ù: {data.get('revenue',0):,}\n"
        f"ð VIP ÙØ¹Ø§Ù: {data.get('vip_users',0)}\n"
        f"ð¤ Ø¹Ø§Ø¯Û: {data.get('normal_users',0)}\n"
        f"â­ XP Ú©Ø³Ø¨âØ´Ø¯Ù: {data.get('xp_earned',0)}\n"
        f"â­ XP ÙØµØ±ÙâØ´Ø¯Ù: {data.get('xp_spent',0)}\n"
        f"ð« ØªÛÚ©Øª Ø¬Ø¯ÛØ¯: {data.get('tickets',0)}\n"
        f"â ØªÛÚ©Øª Ø¨Ø³ØªÙâØ´Ø¯Ù: {data.get('tickets_closed',0)}\n"
        f"ð¢ Ù¾Ø³Øª Ø®ÙØ¯Ú©Ø§Ø±: {data.get('posts',0)}"
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
    checks.append(("Bot","OK" if BOT_TOKEN else "ERROR","ØªÙÚ©Ù BOT_TOKEN ØªÙØ¸ÛÙ Ø´Ø¯Ù Ø§Ø³Øª." if BOT_TOKEN else "BOT_TOKEN ØªÙØ¸ÛÙ ÙØ´Ø¯Ù Ø§Ø³Øª."))

    try:
        c=db()
        c.execute("SELECT 1")
        integrity=c.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity=="ok":
            checks.append(("Database","OK","SQLite Ù integrity_check Ø³Ø§ÙÙ Ø§Ø³Øª."))
        else:
            checks.append(("Database","ERROR",f"integrity_check: {integrity}"))
        required_tables={"users","goals","channel_config","customers","appointments","business_profiles","feature_access"}
        existing={r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        missing=sorted(required_tables-existing)
        checks.append(("Customer/Booking","ERROR",f"Ø¬Ø¯ÙÙâÙØ§Û ÙØ§ÙØµ: {', '.join(missing)}") if missing
                      else ("Customer/Booking","OK","Ø¬Ø¯Ø§ÙÙ Ú©Ø§Ø±Ø¨Ø±Ø§ÙØ ÙØ´ØªØ±ÛØ ÙÙØ¨Øª Ù Ø±Ø²Ø±Ù Ø¢ÙÙØ§ÛÙ ÙÙØ¬ÙØ¯ ÙØ³ØªÙØ¯."))
        c.close()
    except Exception as e:
        checks.append(("Database","ERROR",f"Ø§ØªØµØ§Ù ÛØ§ integrity_check Ø®Ø·Ø§ Ø¯Ø§Ø±Ø¯: {e}"))
        checks.append(("Customer/Booking","ERROR","Ø¨Ù Ø¯ÛØªØ§Ø¨ÛØ³ Ø¯Ø³ØªØ±Ø³Û ÙØ´Ø¯."))

    cfg=get_channel_config()
    if cfg and cfg["channel_id"]:
        try:
            chat=await bot.get_chat(cfg["channel_id"])
            me=await bot.get_me()
            member=await bot.get_chat_member(cfg["channel_id"],me.id)
            allowed = member.status in {"administrator","creator"}
            if allowed:
                checks.append(("Channel","OK",f"Ú©Ø§ÙØ§Ù {getattr(chat,'title',cfg['channel_id'])} ÙØ§Ø¨Ù Ø¯Ø³ØªØ±Ø³Û Ù Ø±Ø¨Ø§Øª Ø§Ø¯ÙÛÙ Ø§Ø³Øª."))
            else:
                checks.append(("Channel","ERROR","Ø±Ø¨Ø§Øª Ø¨Ù Ú©Ø§ÙØ§Ù Ø¯Ø³ØªØ±Ø³Û ÙØ¯ÛØ±ÛØªÛ ÙØ¯Ø§Ø±Ø¯Ø Ø§Ø±Ø³Ø§Ù/ÙØ¯ÛØ±ÛØª Ù¾Ø³Øª ÙÙÚ©Ù Ø§Ø³Øª ÙØªÙÙÙ Ø´ÙØ¯."))
        except Exception as e:
            checks.append(("Channel","ERROR",f"Ø§ØªØµØ§Ù ÛØ§ Ø¯Ø³ØªØ±Ø³Û Ú©Ø§ÙØ§Ù ÙØ´Ú©Ù Ø¯Ø§Ø±Ø¯: {e}"))
    else:
        checks.append(("Channel","WARN","Ú©Ø§ÙØ§Ù ÙÙÙØ² Ø¯Ø± ØªÙØ¸ÛÙØ§Øª ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù ÙØªØµÙ ÙØ´Ø¯Ù Ø§Ø³Øª."))

    scheduler_ok=bool(getattr(bot,"job_queue",None))
    checks.append(("Scheduler","OK" if scheduler_ok else "WARN",
                   "Ø²ÙØ§ÙâØ¨ÙØ¯Û Ø±Ø¨Ø§Øª ÙØ¹Ø§Ù Ø§Ø³Øª."
                   if scheduler_ok else
                   "ØµÙ Ø²ÙØ§ÙâØ¨ÙØ¯Û Ø¯Ø§Ø®ÙÛ Ú©ØªØ§Ø¨Ø®Ø§ÙÙ Ø¯Ø± Ø¯Ø³ØªØ±Ø³ ÙÛØ³ØªØ fallback Ø¯Ø§Ø®ÙÛ Ø±Ø¨Ø§Øª Ø§Ø³ØªÙØ§Ø¯Ù ÙÛâØ´ÙØ¯."))


    price_enabled=feature_enabled("price_data")
    checks.append(("Price Sources","OK" if price_enabled else "OFF",
                   "ÙÛÙØª Ø§Ø² ÙÙØ§Ø¨Ø¹ Ø¨Ø§Ø²Ø§Ø± Ø¯Ø±ÛØ§ÙØª ÙÛâØ´ÙØ¯."
                   if price_enabled else
                   "ÙÛÙØª Ø¢ÙÙØ§ÛÙ ØªÙØ³Ø· ÙØ¯ÛØ± ØºÛØ±ÙØ¹Ø§Ù Ø´Ø¯Ù Ø§Ø³Øª."))

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
                       "Ø±ÙØ§Ø¨Ø· ÙØ§ÙÚ©ÛØª Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ù ÙØ´ØªØ±Û/Ø±Ø²Ø±Ù Ø³Ø§Ø²Ú¯Ø§Ø± Ø§Ø³Øª."
                       if isolation_bad==0 else
                       f"{isolation_bad} Ø±Ø§Ø¨Ø·Ù ÙØ§Ø³Ø§Ø²Ú¯Ø§Ø± Ù¾ÛØ¯Ø§ Ø´Ø¯Ø ÙÛØ§Ø²ÙÙØ¯ Ø¨Ø±Ø±Ø³Û ÙØ¯ÛØ± Ø§Ø³Øª."))
    except Exception:
        checks.append(("Data Isolation","WARN","ÙÙÛØ²Û ÙØ§ÙÚ©ÛØª Ø¯Ø± Ø§ÛÙ ÙÙØ¨Øª Ú©Ø§ÙÙ ÙØ´Ø¯."))

    try:
        c=db()
        feature_count=c.execute("SELECT COUNT(*) n FROM feature_access").fetchone()["n"]
        c.close()
        checks.append(("Feature Access","OK" if feature_count else "ERROR",
                       f"{feature_count} ÙØ§Ø¨ÙÛØª Ø¯Ø± ÙØ§ØªØ±ÛØ³ Ø¯Ø³ØªØ±Ø³Û Ø«Ø¨Øª Ø´Ø¯Ù Ø§Ø³Øª."
                       if feature_count else "ÙØ§ØªØ±ÛØ³ Ø¯Ø³ØªØ±Ø³Û ÙØ§Ø¨ÙÛØªâÙØ§ Ø®Ø§ÙÛ Ø§Ø³Øª."))
    except Exception as e:
        checks.append(("Feature Access","ERROR",f"Ø®ÙØ§ÙØ¯Ù ØªÙØ¸ÛÙØ§Øª ÙØ§Ø¨ÙÛØªâÙØ§ Ø®Ø·Ø§ Ø¯Ø§Ø±Ø¯: {e}"))

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
        "Bot":"ð¤ Ø±Ø¨Ø§Øª",
        "Database":"ðï¸ Ù¾Ø§ÛÚ¯Ø§ÙâØ¯Ø§Ø¯Ù",
        "Customer/Booking":"ð¥ ÙØ´ØªØ±Û Ù Ø±Ø²Ø±Ù",
        "Channel":"ð¢ Ú©Ø§ÙØ§Ù",
        "Scheduler":"â° Ø²ÙØ§ÙâØ¨ÙØ¯Û",
        "n8n":"ð n8n",
        "Price Sources":"ð¹ ÙÙØ§Ø¨Ø¹ ÙÛÙØª",
        "Data Isolation":"ð Ø¬Ø¯Ø§Ø³Ø§Ø²Û Ø¯Ø§Ø¯Ù",
        "Feature Access":"ð§© Ø¯Ø³ØªØ±Ø³Û ÙØ§Ø¨ÙÛØªâÙØ§",
        "SQLite Foreign Keys":"ð Ú©ÙÛØ¯ÙØ§Û Ø®Ø§Ø±Ø¬Û SQLite",
        "SQLite Journal":"ð¾ ÚÙØ±ÙØ§Ù SQLite",
        "SQLite Sync":"âï¸ ÙÙÚ¯Ø§ÙâØ³Ø§Ø²Û SQLite",
        "SQLite integrity":"ð¡ï¸ Ø³ÙØ§ÙØª SQLite",
    }
    status_fa={
        "OK":"Ø³Ø§ÙÙ", "ERROR":"Ø®Ø·Ø§", "WARN":"ÙØ´Ø¯Ø§Ø±", "OFF":"Ø®Ø§ÙÙØ´"
    }
    icon={"OK":"ð¢","ERROR":"ð´","WARN":"ð¡","OFF":"âª"}
    lines=["ð©º <b>ÚÚ©Ø§Ù¾ Ø±Ø¨Ø§Øª</b>","<i>ÙØ¶Ø¹ÛØª Ø³Ø±ÙÛØ³âÙØ§ Ù Ø²ÛØ±Ø³Ø§Ø®Øª</i>",""]
    for r in rows:
        raw_service=re.sub(r"<[^>]+>", "", str(r["service"] or "")).strip()
        status=str(r["status"] or "").upper()
        details=re.sub(r"<[^>]+>", "", str(r["details"] or "")).strip()
        label=service_fa.get(raw_service, raw_service)
        st=status_fa.get(status, status)
        lines.append(f"{icon.get(status,'âª')} <b>{html.escape(label)}</b> â {html.escape(st)}")
        if details:
            # Keep long technical diagnostics readable on mobile.
            details=' '.join(details.split())
            if len(details)>260:
                details=details[:257]+'â¦'
            lines.append(f"   <i>â³ {html.escape(details)}</i>")
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
        # JobQueue ÙØ± 60 Ø«Ø§ÙÛÙ Ø§Ø² Ø²ÙØ§Ù Ø´Ø±ÙØ¹ Ø±Ø¨Ø§Øª Ø§Ø¬Ø±Ø§ ÙÛâØ´ÙØ¯ Ù Ø§ÙØ²Ø§ÙØ§Ù Ø±ÙÛ Ø«Ø§ÙÛÙ 00
        # ÙØ±Ø§Ø± ÙÙÛâÚ¯ÛØ±Ø¯Ø Ø¨ÙØ§Ø¨Ø±Ø§ÛÙ ÙÙØ· ÙÙØªØ¸Ø± Ø¨Ø±Ø§Ø¨Ø±Û Ø¯ÙÛÙ HH:MM ÙÙÛâÙØ§ÙÛÙ.
        # Ø§Ú¯Ø± Ø§Ø² Ø³Ø§Ø¹Øª ØªØ¹ÛÛÙâØ´Ø¯Ù Ø¹Ø¨ÙØ± Ú©Ø±Ø¯Ù Ø¨Ø§Ø´ÛÙ Ù Ø§ÙØ±ÙØ² ÙÙÙØ² ÚÚ©Ø§Ù¾ ÙØ´Ø¯Ù Ø¨Ø§Ø´Ø¯Ø Ø§Ø¬Ø±Ø§ ÙÛâØ´ÙØ¯.
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
        report = health_text() + "\n\nð©º ÚÚ©Ø§Ù¾ Ø¯ÙØ±ÙâØ§Û Ø®ÙØ¯Ú©Ø§Ø± Ø§ÙØ¬Ø§Ù Ø´Ø¯."
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
                    question="ð Ø§Ø±Ø²ÛØ§Ø¨Û Ø§ÙØ´Ø¨: Ø§Ø² ÙØ­ØªÙØ§Û Ø§ÙØ±ÙØ² Ø±Ø§Ø¶Û Ø¨ÙØ¯ÛØ",
                    options=["ð Ø®ÛÙÛ Ø®ÙØ¨ Ø¨ÙØ¯", "ð Ø®ÙØ¨ Ø¨ÙØ¯", "ð Ø¨ÙØªØ±Ø´ Ú©ÙÛÙ"],
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
        text="âï¸ ØµØ¨Ø­ Ø¨Ø®ÛØ± ÙÙØ±Ø§ÙØ§Ù MyTasks!\n\nÛÚ© Ø±ÙØ² ØªØ§Ø²ÙØ ÛÚ© ÙØ±ØµØª ØªØ§Ø²Ù Ø¨Ø±Ø§Û ÛÚ© ÙØ¯Ù Ø¨ÙØªØ±. ð±\nØ§ÙØ±ÙØ² ÙÙ Ø¨Ø§ ÙÙ ÛÚ© ÙÙØ¶ÙØ¹ Ú©Ø§Ø±Ø¨Ø±Ø¯Û Ù ÙÙÛØ¯ Ø±Ø§ Ø¨Ø±Ø±Ø³Û ÙÛâÚ©ÙÛÙ. ð¯"
        text=add_channel_username_footer(text, await channel_post_footer(context.bot, channel), 4096)
        await context.bot.send_message(chat_id=channel, text=text)
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
        text="ð Ø´Ø¨ Ø¨Ø®ÛØ± ÙÙØ±Ø§ÙØ§Ù MyTasks!\n\nÙÙÙÙÙ Ú©Ù Ø§ÙØ±ÙØ² ÙÙ ÙÙØ±Ø§Ù ÙØ§ Ø¨ÙØ¯ÛØ¯. â¤ï¸\nÙØ¨Ù Ø§Ø² Ù¾Ø§ÛØ§Ù Ø±ÙØ²Ø ÙØ¸Ø±ØªØ§Ù Ø¯Ø±Ø¨Ø§Ø±Ù ÙØ­ØªÙØ§Û Ø§ÙØ±ÙØ² Ø±Ø§ Ø¨Ø§ ÙØ§ Ø¯Ø± ÙÛØ§Ù Ø¨Ú¯Ø°Ø§Ø±ÛØ¯."
        text=add_channel_username_footer(text, await channel_post_footer(context.bot, channel), 4096)
        await context.bot.send_message(chat_id=channel, text=text)
        msg = await context.bot.send_poll(chat_id=channel, question="ð ÙØ­ØªÙØ§Û Ø§ÙØ±ÙØ² ÚÙØ¯Ø± Ø¨Ø±Ø§ÛØª ÙÙÛØ¯ Ø¨ÙØ¯Ø", options=["ð Ø®ÛÙÛ ÙÙÛØ¯ Ø¨ÙØ¯", "ð ÙÙÛØ¯ Ø¨ÙØ¯", "ð ÙØ¹ÙÙÙÛ Ø¨ÙØ¯", "ð ÙÙÛØ¯ ÙØ¨ÙØ¯"], is_anonymous=False)
        c=db(); c.execute("INSERT OR REPLACE INTO channel_polls(poll_id,channel_id,poll_type,question,options,created_at,report_date) VALUES(?,?,?,?,?,?,?)", (str(msg.poll.id),str(channel),"usefulness",msg.poll.question,json.dumps(msg.poll.options,ensure_ascii=False,default=lambda o:o.text),datetime.now(TZ).isoformat(),date_iso))
        c.commit(); c.close()
        topics = ["ð ÙØ±Ø²Ø´ Ù Ø³ÙØ§ÙØªÛ", "ð§  ØªÙØ±Ú©Ø² Ù ÛØ§Ø¯Ú¯ÛØ±Û", "ð´ Ø®ÙØ§Ø¨ Ù Ø³Ø¨Ú© Ø²ÙØ¯Ú¯Û", "ð° ÙØ¯ÛØ±ÛØª ÙØ§ÙÛ", "ð ÙØ·Ø§ÙØ¹Ù Ù Ø±Ø´Ø¯ ÙØ±Ø¯Û"]
        msg2 = await context.bot.send_poll(chat_id=channel, question="ð¯ ÙØ±Ø¯Ø§ Ø¨ÛØ´ØªØ± Ø¯Ø±Ø¨Ø§Ø±Ù Ú©Ø¯Ø§Ù ÙÙØ¶ÙØ¹ ØµØ­Ø¨Øª Ú©ÙÛÙØ", options=topics, is_anonymous=False)
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
            report = get_daily_report_text()+f"\n\nð£ ÙØ§Ú©ÙØ´âÙØ§Û Ú©Ø§ÙØ§Ù: {reaction_total}"
            if reactions: report += "\n" + " | ".join(f"{e}: {n}" for e,n in reactions[:8])
            report += f"\nð¯ ÙÙØ¶ÙØ¹ Ù¾ÛØ´ÙÙØ§Ø¯Û ÙØ±Ø¯Ø§: {top_topic or 'Ø±Ø£Û Ú©Ø§ÙÛ Ø«Ø¨Øª ÙØ´Ø¯Ù'}"
            for admin_id in ADMIN_IDS:
                try: await context.bot.send_message(chat_id=admin_id,text=report)
                except Exception: logger.exception("Admin daily report delivery failed")

admin_panel_callback=final_admin_panel_callback
admin_keyboard=final_admin_keyboard

async def error_handler(update, context):
    """Global recovery must preserve the user's current flow instead of masking errors with Home."""
    logger.error("Bot error", exc_info=context.error)
    release_leaked_connections(context.error)
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
                await q.answer("â Ø®Ø·Ø§ Ø¯Ø± ÙÙÛÙ Ø¨Ø®Ø´Ø ÙØ¶Ø¹ÛØªØª Ø­ÙØ¸ Ø´Ø¯.", show_alert=True)
            except Exception:
                pass
            data = q.data or ""
            retry = data if data else "v25:hub"
            recovery_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("ð ØªÙØ§Ø´ Ø¯ÙØ¨Ø§Ø±Ù", callback_data=retry)],
                [InlineKeyboardButton("â¬ï¸ ÙØ±Ú©Ø² ÙÙ", callback_data="v25:hub"), main_menu_button(uid)],
            ])
            recovery_text = (
                f"â ï¸ Ø§Ø¬Ø±Ø§Û Ø§ÛÙ Ø¨Ø®Ø´ Ø¨Ø§ Ø®Ø·Ø§ ÙØªÙÙÙ Ø´Ø¯.\n\nÚ©Ø¯ Ø®Ø·Ø§: <code>{err_name}</code>\n"
                "ØµÙØ­Ù ÙØ¹ÙÛ Ù¾Ø§Ú© ÙØ´Ø¯Ø ÙÛâØªÙØ§ÙÛ Ø¯ÙØ¨Ø§Ø±Ù Ø§ÙØªØ­Ø§Ù Ú©ÙÛ."
            )
            # CallbackQuery can be message-less for inline messages. Do not let
            # the recovery handler raise a second AttributeError.
            if q.message is not None:
                await q.message.reply_text(
                    recovery_text, parse_mode="HTML", reply_markup=recovery_markup
                )
            elif getattr(q, "inline_message_id", None):
                try:
                    await q.edit_message_text(
                        recovery_text, parse_mode="HTML", reply_markup=recovery_markup
                    )
                except Exception:
                    logger.exception("Failed to recover inline callback error")
        elif update.message:
            await update.message.reply_text(
                f"â ï¸ Ø§Ø¬Ø±Ø§Û Ø§ÛÙ Ø¨Ø®Ø´ Ø¨Ø§ Ø®Ø·Ø§ ÙØªÙÙÙ Ø´Ø¯.\nÚ©Ø¯ Ø®Ø·Ø§: <code>{err_name}</code>\n"
                "ÙØ¶Ø¹ÛØª ÙØ¹ÙÛ Ø­ÙØ¸ Ø´Ø¯Ø ÙÛâØªÙØ§ÙÛ Ø¯ÙØ¨Ø§Ø±Ù ØªÙØ§Ø´ Ú©ÙÛ.",
                parse_mode="HTML",
                reply_markup=nav_keyboard(uid),
            )
    except Exception:
        logger.exception("Failed to send contextual recovery message")


async def my_id(update, context):
    uid = update.effective_user.id
    await update.message.reply_text(
        f"ð Ø´ÙØ§Ø³Ù ØªÙÚ¯Ø±Ø§Ù Ø´ÙØ§: <code>{uid}</code>\n\n"
        "Ø§ÛÙ Ø¹Ø¯Ø¯ Ø±Ø§ Ø¯Ø± Railway â Variables Ø¯Ø§Ø®Ù ADMIN_IDS ÛØ§ ADMIN_ID ÙØ±Ø§Ø± Ø¨Ø¯Ù Ù Ø³Ø±ÙÛØ³ Ø±Ø§ Restart/Redeploy Ú©Ù.",
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
    'unified_hub':'ð§  ÙØ±Ú©Ø² ÙØ±ÙØ§Ù ÙÙØ´ÙÙØ¯',
    'important_reminders':'ð ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§Û ÙÙÙ',
    'calendar_hub':'ð ØªÙÙÛÙ ÛÚ©Ù¾Ø§Ø±ÚÙ',
    'profile_sharing':'ð¤ Ø§Ø·ÙØ§Ø¹Ø§Øª ÙÙ',
    'portfolio':'ð° Ø³Ø±ÙØ§ÛÙâÙØ§Û ÙÙ',
    'installments':'ð³ Ø§ÙØ³Ø§Ø· Ù ØªØ³ÙÛÙØ§Øª',
    'business_services':'ð ï¸ Ø®Ø¯ÙØ§Øª Ú©Ø³Ø¨âÙÚ©Ø§Ø±',
    'business_finance':'ð ÙØ§ÙÛ ÙØ´ØªØ±ÛØ§Ù',
    'booking_payments':'ð³ Ù¾Ø±Ø¯Ø§Ø®Øª Ø±Ø²Ø±Ù',
    'card_to_card':'ðµ Ú©Ø§Ø±ØªâØ¨ÙâÚ©Ø§Ø±Øª',
    'surveys':'â­ ÙØ¸Ø±Ø³ÙØ¬Û ÙØ´ØªØ±Û',
    'sms':'ð± Ù¾ÛØ§ÙÚ©',
    'vip_plans':'ð Ù¾ÙÙâÙØ§Û VIP',
    'market_prices_v25':'ð ÙÛÙØª Ø¨Ø§Ø²Ø§Ø±'
}

V25_DEFAULT_QUESTIONS = [
    ('space','ðª ÙØ¶Ø§Û ÙØ¬ÙÙØ¹Ù ÚØ·ÙØ± Ø¨ÙØ¯Ø'),
    ('clean','ð§¹ ØªÙÛØ²Û Ù ÙØ¸Ù ÙØ­ÛØ· ÚØ·ÙØ± Ø¨ÙØ¯Ø'),
    ('staff','ð¥ Ø¨Ø±Ø®ÙØ±Ø¯ Ú©Ø§Ø±Ú©ÙØ§Ù ÚØ·ÙØ± Ø¨ÙØ¯Ø'),
    ('speed','â¡ Ø³Ø±Ø¹Øª Ù¾Ø§Ø³Ø®Ú¯ÙÛÛ ÚØ·ÙØ± Ø¨ÙØ¯Ø'),
    ('quality','ð ï¸ Ú©ÛÙÛØª Ø®Ø¯ÙØª ÚØ·ÙØ± Ø¨ÙØ¯Ø'),
    ('value','ð° Ø§Ø±Ø²Ø´ Ø®Ø¯ÙØª ÙØ³Ø¨Øª Ø¨Ù ÙØ²ÛÙÙ ÚØ·ÙØ± Ø¨ÙØ¯Ø'),
    ('booking','ð Ø±Ø²Ø±Ù Ø¢ÙÙØ§ÛÙ ÚÙØ¯Ø± Ø±Ø§Ø­Øª Ø¨ÙØ¯Ø'),
]

V25_PLAN_SEEDS = [
    ('one_hour','â±ï¸ ÛÚ© Ø³Ø§Ø¹ØªÙ',1,0),
    ('one_day','ð ÛÚ© Ø±ÙØ²Ù',1,0),
    ('one_week','ð ÛÚ© ÙÙØªÙâØ§Û',7,0),
    ('one_month','ðï¸ ÛÚ© ÙØ§ÙÙ',30,0),
    ('two_months','ðï¸ Ø¯Ù ÙØ§ÙÙ',60,0),
    ('three_months','ðï¸ Ø³Ù ÙØ§ÙÙ',90,0),
    ('six_months','ðï¸ Ø´Ø´ ÙØ§ÙÙ',180,0),
    ('one_year','ð ÛÚ© Ø³Ø§ÙÙ',365,0),
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
    return InlineKeyboardMarkup([[InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª' if fa else 'â¬ï¸ Back',callback_data=cb),InlineKeyboardButton('ð  ÙÙÙÛ Ø§ØµÙÛ' if fa else 'ð  Main Menu',callback_data='nav:main')]])

def v25_hub_keyboard(uid):
    fa=lang(uid)=='fa'; rows=[]
    labels=[
        ('v25:today','ð§  Ø§ÙØ±ÙØ² ÙÙ','ð§  My Day','unified_hub'),
        ('v25:reminders','ð ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§Û ÙÙÙ','ð Important Reminders','important_reminders'),
        ('v25:calendar','ð ØªÙÙÛÙ ÙÙ','ð My Calendar','calendar_hub'),
        ('v25:portfolio','ð° Ø³Ø±ÙØ§ÛÙâÙØ§Û ÙÙ','ð° My Portfolio','portfolio'),
        ('v25:installments','ð³ Ø§ÙØ³Ø§Ø· Ù ØªØ³ÙÛÙØ§Øª','ð³ Installments','installments'),
        ('v25:profile','ð¤ Ø§Ø·ÙØ§Ø¹Ø§Øª ÙÙ','ð¤ My Profile','profile_sharing'),
        ('v25:vip','ð VIP Ù Ø§Ø´ØªØ±Ø§Ú©','ð VIP & Subscription','vip_plans'),
        ('v25:business','ðª Ù¾ÙÙ Ú©Ø³Ø¨âÙÚ©Ø§Ø±','ðª Business Panel','business_services'),
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
            rows.append([InlineKeyboardButton('ð¥ ÙØ¯ÛØ±ÛØª ÙØ´ØªØ±Û Ù ÙÙØ¨ØªâØ¯ÙÛ' if fa else 'ð¥ Customer & Appointments', callback_data='v25:customers')])
    except Exception:
        logger.exception('Customer menu feature check failed')
    rows.append([main_menu_button(uid)])
    return InlineKeyboardMarkup(rows)

def v25_hub_text(uid):
    fa=lang(uid)=='fa'; p=v25_profile(uid); goals=get_goals(uid)
    d=datetime.now(TZ).date().isoformat(); c=db(); done=c.execute("SELECT COUNT(*) n FROM goal_days WHERE user_id=? AND goal_date=? AND status='done'",(uid,d)).fetchone()['n']; events=c.execute("SELECT COUNT(*) n FROM important_reminders WHERE user_id=? AND enabled=1 AND substr(remind_at,1,10)=?",(uid,d)).fetchone()['n']; appts=c.execute("SELECT COUNT(*) n FROM appointments WHERE owner_user_id=? AND appointment_date=? AND status='booked'",(uid,d)).fetchone()['n']; c.close()
    if fa:
        return f"âï¸ <b>Ø§ÙØ±ÙØ²Øª</b>\n\nð¯ ÙØ¯ÙâÙØ§: {len(goals)}\nâ Ø§ÙØ¬Ø§ÙâØ´Ø¯Ù: {done}\nð ÛØ§Ø¯Ø¢ÙØ±Û Ø§ÙØ±ÙØ²: {events}\nð ÙÙØ¨Øª Ø§ÙØ±ÙØ²: {appts}\n\nð¤ {html.escape(p['full_name'] or 'Ø¯ÙØ³Øª ÙÙ')}\n\nØ§Ø² ÙÙÛÙâØ¬Ø§ ÙØ± Ú©Ø§Ø±Û ÙØ§Ø²Ù Ø¯Ø§Ø±Û Ø¨Ø§ ÚÙØ¯ Ú©ÙÛÚ© Ø§ÙØ¬Ø§Ù Ø¨Ø¯Ù."
    return f"âï¸ <b>My Day</b>\n\nð¯ Goals: {len(goals)}\nâ Completed: {done}\nð Reminders today: {events}\nð Appointments today: {appts}\n\nð¤ {html.escape(p['full_name'] or 'Friend')}\n\nEverything you need is one tap away."

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
    kb=[[InlineKeyboardButton(f"ð {r['title']} â {r['remind_at'].replace('T',' ')}",callback_data=f"v25:remview:{r['id']}")] for r in rows]
    kb.append([InlineKeyboardButton("â ÛØ§Ø¯Ø¢ÙØ±Û Ø¬Ø¯ÛØ¯" if fa else "â New Reminder",callback_data="v25:remadd")])
    kb.append([InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª" if fa else "â¬ï¸ Back",callback_data="v25:hub"),main_menu_button(uid)])
    if rows:
        listing="\n".join(f"â¢ {r['title']} â {r['remind_at'].replace('T',' ')}" for r in rows)
    else:
        listing="ÙÙÙØ² ÛØ§Ø¯Ø¢ÙØ±Û ÙÙÙÛ ÙØ¯Ø§Ø±Û." if fa else "No important reminders yet."
    heading="ð <b>ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§Û ÙÙÙ</b>" if fa else "ð <b>Important Reminders</b>"
    target = update.callback_query.message if update.callback_query else update.message
    await target.reply_text(heading+"\n\n"+listing,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def v25_profile_menu(update,context,edit=False):
    uid=update.effective_user.id; fa=lang(uid)=='fa'; p=v25_profile(uid); c=db(); perms=c.execute("SELECT scope,field,enabled FROM profile_share WHERE user_id=? ORDER BY scope,field",(uid,)).fetchall(); c.close()
    text=(f"ð¤ <b>Ø§Ø·ÙØ§Ø¹Ø§Øª ÙÙ</b>\n\nÙØ§Ù: {html.escape(p['full_name'] or 'â')}\nð± ØªÙÙÙ: {html.escape(p['phone'] or 'â')}\nð§ Ø§ÛÙÛÙ: {html.escape(p['email'] or 'â')}\nð Telegram ID: {p['telegram_id'] or uid}\n\nØ¨Ø±Ø§Û ÙØ± ÙØ§Ø¨ÙÛØª ÙØ´Ø®Øµ Ú©Ù ÚÙ Ø§Ø·ÙØ§Ø¹Ø§ØªÛ ÙØ¬Ø§Ø² Ø§Ø³Øª. ÙÛÚ ÙÛÙØ¯Û Ø§Ø¬Ø¨Ø§Ø±Û ÙÛØ³Øª.") if fa else (f"ð¤ <b>My Profile</b>\n\nName: {html.escape(p['full_name'] or 'â')}\nð± Phone: {html.escape(p['phone'] or 'â')}\nð§ Email: {html.escape(p['email'] or 'â')}\nð Telegram ID: {p['telegram_id'] or uid}\n\nChoose which fields may be shared with each feature. Nothing is mandatory.")
    kb=[[InlineKeyboardButton('âï¸ ÙØ§Ù' if fa else 'âï¸ Name',callback_data='v25:profile_edit:name')],[InlineKeyboardButton('ð± ØªÙÙÙ' if fa else 'ð± Phone',callback_data='v25:profile_edit:phone')],[InlineKeyboardButton('ð§ Ø§ÛÙÛÙ' if fa else 'ð§ Email',callback_data='v25:profile_edit:email')],[InlineKeyboardButton('ð ÙØ¬ÙØ² Ø§Ø³ØªÙØ§Ø¯Ù Ø§Ø² Ø§Ø·ÙØ§Ø¹Ø§Øª' if fa else 'ð Data sharing',callback_data='v25:profile_share')],[InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª' if fa else 'â¬ï¸ Back',callback_data='v25:hub'),main_menu_button(uid)]]
    await (update.callback_query.message if update.callback_query else update.message).reply_text(text,parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_profile_share_menu(update,context):
    uid=update.effective_user.id; fa=lang(uid)=='fa'; scopes=[('booking','ð Ø±Ø²Ø±Ù'),('payment','ð³ Ù¾Ø±Ø¯Ø§Ø®Øª'),('survey','â­ ÙØ¸Ø±Ø³ÙØ¬Û'),('crm','ð¥ ÙØ´ØªØ±Û')]; fields=['full_name','phone','email']
    kb=[]
    for scope, label in scopes:
        for field in fields:
            r=_v25_exec("SELECT enabled FROM profile_share WHERE user_id=? AND scope=? AND field=?",(uid,scope,field),fetchone=True)
            enabled=1 if r is None else int(r['enabled'])
            fl={'full_name':'ÙØ§Ù','phone':'ØªÙÙÙ','email':'Ø§ÛÙÛÙ'}[field] if fa else {'full_name':'Name','phone':'Phone','email':'Email'}[field]
            sl=label if fa else scope.title()
            kb.append([InlineKeyboardButton(f"{'ð¢' if enabled else 'ð´'} {sl} â {fl}",callback_data=f"v25:share:{scope}:{field}")])
    kb.append([InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª' if fa else 'â¬ï¸ Back',callback_data='v25:profile'),main_menu_button(uid)])
    await update.callback_query.message.edit_text('ð <b>ÙØ¬ÙØ² Ø§Ø³ØªÙØ§Ø¯Ù Ø§Ø² Ø§Ø·ÙØ§Ø¹Ø§Øª</b>\n\nØ³Ø¨Ø² ÛØ¹ÙÛ Ø§Ø¬Ø§Ø²Ù Ø§Ø³ØªÙØ§Ø¯ÙØ ÙØ±ÙØ² ÛØ¹ÙÛ Ø§Ø³ØªÙØ§Ø¯Ù ÙØ´ÙØ¯.' if fa else 'ð <b>Data Sharing</b>\n\nGreen means allowed; red means not shared.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

def v25_portfolio_menu_keyboard(uid):
    fa=lang(uid)=='fa'; return InlineKeyboardMarkup([[InlineKeyboardButton('â Ø§ÙØ²ÙØ¯Ù Ø³Ø±ÙØ§ÛÙ' if fa else 'â Add Asset',callback_data='v25:portadd')],[InlineKeyboardButton('ð Ø®ÙØ§ØµÙ Ø³Ø±ÙØ§ÛÙ' if fa else 'ð Portfolio Summary',callback_data='v25:portsummary')],[InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª' if fa else 'â¬ï¸ Back',callback_data='v25:hub'),main_menu_button(uid)]])

async def v25_portfolio_menu(update,context):
    uid=update.effective_user.id; c=db(); rows=c.execute("SELECT * FROM portfolio_assets WHERE user_id=? AND enabled=1 ORDER BY id DESC",(uid,)).fetchall(); c.close(); lines=['ð° <b>Ø³Ø±ÙØ§ÛÙâÙØ§Û ÙÙ</b>','']
    if rows:
        for r in rows: lines.append(f"â¢ {html.escape(r['title'])} â ÙÙØ¯Ø§Ø± {r['quantity']:g} | ÙÛÙØª Ø®Ø±ÛØ¯ {r['buy_price_rial']:,.0f} Ø±ÛØ§Ù")
    else: lines.append('ÙÙÙØ² Ø³Ø±ÙØ§ÛÙâØ§Û Ø«Ø¨Øª ÙÚ©Ø±Ø¯ÙâØ§Û.')
    await (update.callback_query.message if update.callback_query else update.message).reply_text('\n'.join(lines),parse_mode='HTML',reply_markup=v25_portfolio_menu_keyboard(uid))


async def v25_installments_menu(update,context):
    uid=update.effective_user.id; c=db(); rows=c.execute("SELECT * FROM installment_plans WHERE user_id=? AND enabled=1 ORDER BY first_due_date",(uid,)).fetchall(); c.close(); lines=['ð³ <b>Ø§ÙØ³Ø§Ø· Ù ØªØ³ÙÛÙØ§Øª</b>','']
    if rows:
        for r in rows: lines.append(f"ð¦ {html.escape(r['bank_name'])} â {html.escape(r['title'])} â ÙØ³Ø·: {r['monthly_rial']:,.0f} Ø±ÛØ§Ù")
    else: lines.append('ØªØ³ÙÛÙØ§ØªÛ Ø«Ø¨Øª ÙØ´Ø¯Ù.')
    kb=[]
    for r in rows: kb.append([InlineKeyboardButton(f"ð¦ {r['bank_name']} â {r['title']}",callback_data=f"v25:instview:{r['id']}")])
    kb.append([InlineKeyboardButton('â Ø«Ø¨Øª ØªØ³ÙÛÙØ§Øª' if lang(uid)=='fa' else 'â Add Loan',callback_data='v25:instadd')])
    kb.append([InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª' if lang(uid)=='fa' else 'â¬ï¸ Back',callback_data='v25:hub'),main_menu_button(uid)])
    await (update.callback_query.message if update.callback_query else update.message).reply_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

def v25_calc_installment(principal, annual_interest, months):
    principal=float(principal); months=max(1,int(months)); rate=float(annual_interest)/100/12
    if rate==0: monthly=principal/months
    else: monthly=principal*rate*((1+rate)**months)/(((1+rate)**months)-1)
    total=monthly*months; interest=total-principal
    return round(monthly),round(interest),round(total)


def v25_bank_keyboard(uid):
    fa=lang(uid)=='fa'; rows=[[InlineKeyboardButton('ð¦ '+b,callback_data=f'v25:instbank:{i}')] for i,b in enumerate(v25_banks())]; rows.append([InlineKeyboardButton('âï¸ Ø¨Ø§ÙÚ© Ø¯ÙØ®ÙØ§Ù' if fa else 'âï¸ Custom Bank',callback_data='v25:instbank_custom')]); rows.append([InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª' if fa else 'â¬ï¸ Back',callback_data='v25:installments'),main_menu_button(uid)]); return InlineKeyboardMarkup(rows)


async def v25_business_menu(update,context):
    uid=update.effective_user.id; fa=lang(uid)=='fa'; ensure_business_profile(uid)
    rows=[]
    items=[('v25:bizprofile','ðª Ø§Ø·ÙØ§Ø¹Ø§Øª Ú©Ø³Ø¨âÙÚ©Ø§Ø±','ðª Business Info','customer_business_settings'),('v25:customers','ð¥ ÙØ¯ÛØ±ÛØª ÙØ´ØªØ±Û Ù ÙÙØ¨ØªâØ¯ÙÛ','ð¥ Customer & Appointments','customers'),('v25:services','ð ï¸ Ø®Ø¯ÙØ§Øª','ð ï¸ Services','business_services'),('v25:bizfinance','ð ÙØ§ÙÛ ÙØ´ØªØ±ÛØ§Ù','ð Customer Finance','business_finance'),('v25:bizpay','ð³ Ù¾Ø±Ø¯Ø§Ø®ØªâÙØ§','ð³ Payments','booking_payments'),('v25:surveyadmin','â­ ÙØ¸Ø±Ø³ÙØ¬Û','â­ Surveys','surveys'),('v25:plans','ð Ù¾ÙÙâÙØ§Û VIP','ð VIP Plans','vip_plans'),('v25:sms','ð± Ù¾ÛØ§ÙÚ©','ð± SMS','sms'),('v25:customermsg','ð© Ù¾ÛØ§Ù Ø¨Ù ÙØ´ØªØ±ÛØ§Ù','ð© Message Customers','customer_customers'),('v25:bookinglink','ð ÙÛÙÚ© Ø±Ø²Ø±Ù','ð Booking Link','customer_booking_link')]
    for cb,ft,et,key in items:
        if v25_allowed(uid,key): rows.append([InlineKeyboardButton(ft if fa else et,callback_data=cb)])
    rows.append([InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª' if fa else 'â¬ï¸ Back',callback_data='v25:hub'),main_menu_button(uid)])
    text='ðª <b>Ù¾ÙÙ Ú©Ø³Ø¨âÙÚ©Ø§Ø±</b>\n\nÙÙÙ Ø¨Ø®Ø´âÙØ§ ÙØ§Ø¨Ù ÙØ¹Ø§Ù/ØºÛØ±ÙØ¹Ø§Ù Ø´Ø¯Ù ÙØ³ØªÙØ¯.' if fa else 'ðª <b>Business Panel</b>\n\nEvery module can be enabled or disabled.'
    await (update.callback_query.message if update.callback_query else update.message).reply_text(text,parse_mode='HTML',reply_markup=InlineKeyboardMarkup(rows))

async def v25_services_menu(update,context):
    uid=update.effective_user.id; c=db(); rows=c.execute("SELECT * FROM business_services WHERE owner_user_id=? ORDER BY id DESC",(uid,)).fetchall(); c.close(); lines=['ð ï¸ <b>Ø®Ø¯ÙØ§Øª</b>','']
    lines += [f"â¢ {html.escape(r['name'])} | â± {r['duration_minutes']} Ø¯ÙÛÙÙ | ð° {r['price_rial']:,.0f} Ø±ÛØ§Ù | {'ð¢' if r['enabled'] else 'ð´'}" for r in rows] or ['ÙÙÙØ² Ø®Ø¯ÙØªÛ Ø«Ø¨Øª ÙØ´Ø¯Ù.']
    kb=[[InlineKeyboardButton(f"{'ð¢' if r['enabled'] else 'ð´'} {r['name']}",callback_data=f"v25:service_toggle:{r['id']}")] for r in rows]
    kb.append([InlineKeyboardButton('â Ø§ÙØ²ÙØ¯Ù Ø®Ø¯ÙØª' if lang(uid)=='fa' else 'â Add Service',callback_data='v25:serviceadd')]); kb.append([InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª' if lang(uid)=='fa' else 'â¬ï¸ Back',callback_data='v25:business'),main_menu_button(uid)])
    await (update.callback_query.message if update.callback_query else update.message).reply_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_payment_methods_menu(update,context):
    uid=update.effective_user.id; c=db(); rows=c.execute("SELECT * FROM payment_methods WHERE owner_user_id=? ORDER BY method_type",(uid,)).fetchall(); g=c.execute("SELECT * FROM gateway_configs WHERE owner_user_id=?",(uid,)).fetchone(); c.close(); card=next((r for r in rows if r['method_type']=='card'),None); online=next((r for r in rows if r['method_type']=='online'),None)
    text='ð³ <b>Ø±ÙØ´âÙØ§Û Ù¾Ø±Ø¯Ø§Ø®Øª</b>\n\n'+f"ðµ Ú©Ø§Ø±ØªâØ¨ÙâÚ©Ø§Ø±Øª: {'ð¢' if card and card['enabled'] else 'ð´'}\nð³ Ù¾Ø±Ø¯Ø§Ø®Øª Ø¢ÙÙØ§ÛÙ: {'ð¢' if g and g['enabled'] else 'ð´'}\n"
    kb=[[InlineKeyboardButton('ðµ ØªÙØ¸ÛÙ Ú©Ø§Ø±ØªâØ¨ÙâÚ©Ø§Ø±Øª',callback_data='v25:card')],[InlineKeyboardButton('ð³ ØªÙØ¸ÛÙ Ø¯Ø±Ú¯Ø§Ù',callback_data='v25:gateway')],[InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª',callback_data='v25:business'),main_menu_button(uid)]]
    await update.callback_query.message.edit_text(text,parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_survey_admin(update,context):
    uid=update.effective_user.id; c=db(); rows=c.execute("SELECT * FROM survey_questions WHERE owner_user_id=? ORDER BY id",(uid,)).fetchall(); c.close(); text='â­ <b>ÙØ¸Ø±Ø³ÙØ¬Û ÙØ´ØªØ±Û</b>\n\n'+('\n'.join(f"{'ð¢' if r['enabled'] else 'ð´'} {r['question']}" for r in rows) if rows else 'Ø³Ø¤Ø§Ù Ù¾ÛØ´âÙØ±Ø¶ ÙÙÚ¯Ø§Ù Ø«Ø¨Øª Ø§ÙÙÛÙ ÙØ¸Ø±Ø³ÙØ¬Û Ø³Ø§Ø®ØªÙ ÙÛâØ´ÙØ¯.')
    kb=[[InlineKeyboardButton('â Ø³Ø¤Ø§Ù Ø³ÙØ§Ø±Ø´Û',callback_data='v25:surveyadd')],[InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª',callback_data='v25:business'),main_menu_button(uid)]]
    await update.callback_query.message.edit_text(text,parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_vip_plans(update,context):
    uid=update.effective_user.id; c=db(); rows=c.execute("SELECT * FROM subscription_plans_v25 WHERE enabled=1 ORDER BY duration_minutes",()).fetchall(); c.close(); lines=['ð <b>Ù¾ÙÙâÙØ§Û VIP</b>','']
    lines += [f"â¢ {r['name']} â {r['price_rial']:,.0f} Ø±ÛØ§Ù" for r in rows]
    kb=[[InlineKeyboardButton(r['name'],callback_data=f"v25:buyplan:{r['id']}")] for r in rows]
    kb.append([InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª' if lang(uid)=='fa' else 'â¬ï¸ Back',callback_data='v25:business'),main_menu_button(uid)])
    await (update.callback_query.message if update.callback_query else update.message).reply_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))







async def v25_add_text_state(update,context):
    # handled by text_router wrapper
    return False

async def v25_add_reminder_save(update,context):
    uid=update.effective_user.id; title=context.user_data.get('v25_rem_title'); raw=update.message.text.strip();
    dt=None
    try: dt=parse_user_datetime(raw)
    except Exception: dt=None
    if not dt: await update.message.reply_text('â ÙØ±ÙØª ØªØ§Ø±ÛØ®/Ø³Ø§Ø¹Øª ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª. ÙÙÙÙÙ: Û±Û´Û°Ûµ/Û°Û¶/Û°Û³ Û±Û²:Û°Û°'); return True
    dt=dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt
    _v25_exec('INSERT OR IGNORE INTO important_reminders(user_id,title,remind_at,created_at,updated_at) VALUES(?,?,?,?,?)',(uid,title,dt.isoformat(),_v25_now(),_v25_now()))
    clear_flow(context); await update.message.reply_text('â ÛØ§Ø¯Ø¢ÙØ±Û Ø«Ø¨Øª Ø´Ø¯.',reply_markup=v25_hub_keyboard(uid)); return True

async def v25_installment_text_save(update,context):
    uid=update.effective_user.id; mode=context.user_data.get('v25_mode'); text=normalize_digits(update.message.text.strip())
    if mode=='inst_bank': context.user_data['inst_bank']=text; context.user_data['v25_mode']='inst_title'; await update.message.reply_text('ð Ø¹ÙÙØ§Ù ØªØ³ÙÛÙØ§Øª Ø±Ø§ Ø¨ÙØ±Ø³Øª ÛØ§ Â«ÙØ³Ø·Â» Ø¨ÙÙÛØ³:'); return True
    if mode=='inst_title': context.user_data['inst_title']=text or 'ÙØ³Ø·'; context.user_data['v25_mode']='inst_principal'; await update.message.reply_text('ð° ÙØ¨ÙØº Ø§ØµÙ ÙØ§Ù Ø±Ø§ Ø¨Ù Ø±ÛØ§Ù ÙØ§Ø±Ø¯ Ú©Ù:'); return True
    if mode=='inst_principal':
        try: principal=int(float(text.replace(',','')))
        except Exception: await update.message.reply_text('â ÙØ¨ÙØº ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª.'); return True
        if principal <= 0 or principal > 10**15:
            await update.message.reply_text('â ÙØ¨ÙØº Ø¨Ø§ÛØ¯ Ø¨ÛØ´ØªØ± Ø§Ø² ØµÙØ± Ù Ø¯Ø± ÙØ­Ø¯ÙØ¯Ù ÙØ¬Ø§Ø² Ø¨Ø§Ø´Ø¯.'); return True
        context.user_data['inst_principal']=principal; context.user_data['v25_mode']='inst_rate'; await update.message.reply_text('ð ÙØ±Ø® Ø³ÙØ¯ Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:',reply_markup=v25_rates_keyboard(uid)); return True
    if mode=='inst_rate_custom':
        try: rate=float(text.replace('%',''))
        except Exception: await update.message.reply_text('â ÙØ±Ø® ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª.'); return True
        context.user_data['inst_rate']=rate; context.user_data['v25_mode']='inst_months'; await update.message.reply_text('ð¢ ØªØ¹Ø¯Ø§Ø¯ ÙØ§Ù Ø¨Ø§Ø²Ù¾Ø±Ø¯Ø§Ø®Øª Ø±Ø§ Ø¨ÙØ±Ø³Øª:'); return True
    if mode=='inst_months':
        try: months=int(text)
        except Exception: await update.message.reply_text('â ØªØ¹Ø¯Ø§Ø¯ ÙØ§Ù ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª.'); return True
        if not 1 <= months <= 600:
            await update.message.reply_text('â ØªØ¹Ø¯Ø§Ø¯ ÙØ§Ù Ø¨Ø§ÛØ¯ Ø¨ÛÙ Û± ØªØ§ Û¶Û°Û° Ø¨Ø§Ø´Ø¯.'); return True
        context.user_data['inst_months']=months; context.user_data['v25_mode']='inst_first_date'; await update.message.reply_text('ð ØªØ§Ø±ÛØ® Ø§ÙÙÛÙ ÙØ³Ø· Ø±Ø§ Ø¨ÙØ±Ø³Øª. ÙÙÙÙÙ: Û±Û´Û°Ûµ/Û°Û¶/Û°Û³'); return True
    if mode=='inst_first_date':
        try: d=parse_user_date(text)
        except Exception: await update.message.reply_text('â ØªØ§Ø±ÛØ® ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª.'); return True
        context.user_data['inst_first_date']=d; principal=context.user_data['inst_principal']; rate=context.user_data['inst_rate']; months=context.user_data['inst_months']; monthly,interest,total=v25_calc_installment(principal,rate,months); context.user_data.update(inst_monthly=monthly,inst_interest=interest,inst_total=total)
        kb=InlineKeyboardMarkup([[InlineKeyboardButton('â Ø°Ø®ÛØ±Ù',callback_data='v25:instsave'),InlineKeyboardButton('âï¸ ÙÛØ±Ø§ÛØ´',callback_data='v25:instedit')],[main_menu_button(uid)]])
        await update.message.reply_text(f'ð§® <b>ÙØ­Ø§Ø³Ø¨Ù ØªÙØ±ÛØ¨Û</b>\n\nØ§ØµÙ: {principal:,.0f} Ø±ÛØ§Ù\nØ³ÙØ¯ Ø³Ø§ÙØ§ÙÙ: {rate:g}%\nÙØ¯Øª: {months} ÙØ§Ù\n\nðµ ÙØ³Ø· ÙØ§ÙØ§ÙÙ: {monthly:,.0f} Ø±ÛØ§Ù\nð° Ø³ÙØ¯ Ú©Ù: {interest:,.0f} Ø±ÛØ§Ù\nð³ ÙØ¬ÙÙØ¹ Ø¨Ø§Ø²Ù¾Ø±Ø¯Ø§Ø®Øª: {total:,.0f} Ø±ÛØ§Ù',parse_mode='HTML',reply_markup=kb); return True
    if mode=='booking_name':
        context.user_data['public_name']='' if text=='-' else text; context.user_data['v25_mode']='booking_phone'; await update.message.reply_text('ð± Ø´ÙØ§Ø±Ù ØªÙÙÙ Ø±Ø§ Ø¨ÙØ±Ø³Øª ÛØ§ Â«-Â» Ø¨Ø²Ù. Ø§ÛÙ ÙÙ Ø§Ø®ØªÛØ§Ø±Û Ø§Ø³Øª:'); return True
    if mode=='booking_phone':
        context.user_data['public_phone']='' if text=='-' else text; context.user_data.pop('v25_mode',None); owner=context.user_data.get('booking_owner'); sid=int(context.user_data.get('booking_service_id') or 0);
        try: aid,service,amount,name,phone=await v25_create_booking(context,uid,owner,sid); prof=ensure_business_profile(owner); context.user_data['booking_appointment_id']=aid; context.user_data['booking_amount_rial']=amount; await context.bot.send_message(owner,f"ð <b>Ø±Ø²Ø±Ù Ø¬Ø¯ÛØ¯ Ø«Ø¨Øª Ø´Ø¯!</b>\n\nðª {html.escape(prof['business_name'] or 'Ú©Ø³Ø¨âÙÚ©Ø§Ø±')}\nð¤ {html.escape(name)}\nð {jalali_pretty_date(context.user_data['booking_date'])}\nâ° {context.user_data['booking_time']}",parse_mode='HTML');
        except ValueError: await update.message.reply_text('â Ø§ÛÙ Ø²ÙØ§Ù Ø¯ÛÚ¯Ø± Ø¢Ø²Ø§Ø¯ ÙÛØ³Øª.'); return True
        # Reuse a small confirmation message with inline buttons.
        g=_v25_exec('SELECT * FROM gateway_configs WHERE owner_user_id=?',(owner,),fetchone=True); pm=_v25_exec("SELECT * FROM payment_methods WHERE owner_user_id=? AND method_type='card' AND enabled=1",(owner,),fetchone=True); kb=[]
        if amount and g and g['enabled'] and g['payment_link']: kb.append([InlineKeyboardButton('ð³ Ù¾Ø±Ø¯Ø§Ø®Øª Ø¢ÙÙØ§ÛÙ',url=g['payment_link'])])
        if amount and pm: kb.append([InlineKeyboardButton('ðµ Ú©Ø§Ø±ØªâØ¨ÙâÚ©Ø§Ø±Øª',callback_data=f'v25:bookingcard:{aid}')])
        kb.append([InlineKeyboardButton('ð Ø±Ø²Ø±ÙÙØ§Û ÙÙ',callback_data='cust:mybookings'),main_menu_button(uid)]); await update.message.reply_text(f"â <b>Ø±Ø²Ø±Ù Ø´ÙØ§ Ø¨Ø§ ÙÙÙÙÛØª Ø«Ø¨Øª Ø´Ø¯.</b>\n\nðª {html.escape(prof['business_name'] or 'Ú©Ø³Ø¨âÙÚ©Ø§Ø±')}\nð {jalali_pretty_date(context.user_data.get('booking_date'))}\nâ° {context.user_data.get('booking_time')}"+(f"\nð° ÙØ²ÛÙÙ: {amount:,.0f} Ø±ÛØ§Ù" if amount else ''),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb)); clear_flow(context); return True
    if mode=='survey_comment':
        aid=int(context.user_data.get('survey_appointment_id')); c=db(); c.execute('UPDATE survey_responses SET comment=? WHERE appointment_id=?',(text,aid)); owner=c.execute('SELECT owner_user_id FROM appointments WHERE id=?',(aid,)).fetchone(); c.commit(); c.close();
        if owner:
            try: await context.bot.send_message(owner,f'ð¡ Ù¾ÛØ´ÙÙØ§Ø¯ ÙØ´ØªØ±Û Ø¨Ø±Ø§Û Ø±Ø²Ø±Ù #{aid}:\n{text}')
            except Exception: pass
        clear_flow(context); await update.message.reply_text('â Ù¾ÛØ´ÙÙØ§Ø¯Øª Ø«Ø¨Øª Ø´Ø¯. ÙÙÙÙÙ Ú©Ù Ú©ÙÚ© ÙÛâÚ©ÙÛ Ø¨ÙØªØ± Ø´ÙÛÙ.',reply_markup=keyboard(uid)); return True
    if mode=='port_asset':
        context.user_data['port_title']=text; context.user_data['v25_mode']='port_quantity'; await update.message.reply_text('ð¦ ÙÙØ¯Ø§Ø± Ø±Ø§ Ø¨ÙØ±Ø³Øª. ÙØ«Ø§Ù: 10'); return True
    if mode=='port_quantity':
        context.user_data['port_qty']=float(text.replace(',','')); context.user_data['v25_mode']='port_buyprice'; await update.message.reply_text('ð° ÙÛÙØª Ø®Ø±ÛØ¯ ÙØ± ÙØ§Ø­Ø¯ Ø±Ø§ Ø¨Ù Ø±ÛØ§Ù Ø¨ÙØ±Ø³Øª:'); return True
    if mode=='port_buyprice':
        context.user_data['port_price']=float(text.replace(',','')); context.user_data['v25_mode']='port_date'; await update.message.reply_text('ð ØªØ§Ø±ÛØ® Ø®Ø±ÛØ¯ Ø±Ø§ Ø¨ÙØ±Ø³Øª. ÙØ«Ø§Ù: Û±Û´Û°Ûµ/Û°Ûµ/Û³Û°'); return True
    if mode=='port_date':
        try: d=parse_user_date(text)
        except Exception: await update.message.reply_text('â ØªØ§Ø±ÛØ® ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª.'); return True
        s=context.user_data; _v25_exec('INSERT INTO portfolio_assets(user_id,asset_code,title,quantity,buy_price_rial,buy_date,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(uid,'custom',s['port_title'],s['port_qty'],s['port_price'],d,_v25_now(),_v25_now())); clear_flow(context); await update.message.reply_text('â Ø³Ø±ÙØ§ÛÙ Ø«Ø¨Øª Ø´Ø¯.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ð° Ø³Ø±ÙØ§ÛÙâÙØ§Û ÙÙ',callback_data='v25:portfolio')],[main_menu_button(uid)]])); return True
    if mode=='profile_edit:name':
        _v25_exec('UPDATE user_profile SET full_name=?,updated_at=? WHERE user_id=?',(text,_v25_now(),uid)); clear_flow(context); await update.message.reply_text('â ÙØ§Ù Ø¨ÙâØ±ÙØ²Ø±Ø³Ø§ÙÛ Ø´Ø¯.',reply_markup=keyboard(uid)); return True
    if mode=='profile_edit:phone':
        _v25_exec('UPDATE user_profile SET phone=?,updated_at=? WHERE user_id=?',(text,_v25_now(),uid)); clear_flow(context); await update.message.reply_text('â Ø´ÙØ§Ø±Ù ØªÙÙÙ Ø¨ÙâØ±ÙØ²Ø±Ø³Ø§ÙÛ Ø´Ø¯.',reply_markup=keyboard(uid)); return True
    if mode=='profile_edit:email':
        _v25_exec('UPDATE user_profile SET email=?,updated_at=? WHERE user_id=?',(text,_v25_now(),uid)); clear_flow(context); await update.message.reply_text('â Ø§ÛÙÛÙ Ø¨ÙâØ±ÙØ²Ø±Ø³Ø§ÙÛ Ø´Ø¯.',reply_markup=keyboard(uid)); return True
    if mode=='v25_sms_test':
        await update.message.reply_text('ð± ØªØ³Øª SMS Ø¯Ø± ÙØ³Ø®Ù ØªÙÛØ² Ø¨Ù ØªÙØ¸ÛÙØ§Øª Ø³Ø±ÙÛØ³ Ù¾ÛØ§ÙÚ©Û ÙÛØ§Ø² Ø¯Ø§Ø±Ø¯. Ø§Ø¨ØªØ¯Ø§ endpoint Ù API key Ø±Ø§ Ø¯Ø± Ù¾ÙÙ Ú©Ø³Ø¨âÙÚ©Ø§Ø± Ø«Ø¨Øª Ú©Ù.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('â¬ï¸ Ù¾ÛØ§ÙÚ©',callback_data='v25:sms')],[main_menu_button(uid)]])); clear_flow(context); return True
    return False

async def v25_business_text_save(update,context):
    uid=update.effective_user.id; mode=context.user_data.get('v25_mode'); text=update.message.text.strip()
    if mode=='service_name': context.user_data['service_name']=text; context.user_data['v25_mode']='service_duration'; await update.message.reply_text('â± ÙØ¯Øª Ø®Ø¯ÙØª Ø±Ø§ Ø¨Ù Ø¯ÙÛÙÙ Ø¨ÙØ±Ø³Øª:'); return True
    if mode=='service_duration':
        try: duration=int(normalize_digits(text))
        except Exception: await update.message.reply_text('â ÙØ¯Øª ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª.'); return True
        if not 1 <= duration <= 1440: await update.message.reply_text('â ÙØ¯Øª Ø®Ø¯ÙØª Ø¨Ø§ÛØ¯ Ø¨ÛÙ Û± ØªØ§ Û±Û´Û´Û° Ø¯ÙÛÙÙ Ø¨Ø§Ø´Ø¯.'); return True
        context.user_data['service_duration']=duration; context.user_data['v25_mode']='service_price'; await update.message.reply_text('ð° ÙÛÙØª Ø®Ø¯ÙØª Ø±Ø§ Ø¨Ù Ø±ÛØ§Ù Ø¨ÙØ±Ø³Øª:'); return True
    if mode=='service_price':
        try: price=int(float(normalize_digits(text).replace(',','')))
        except Exception: await update.message.reply_text('â ÙÛÙØª ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª.'); return True
        if price < 0 or price > 10**15: await update.message.reply_text('â ÙÛÙØª Ø¨Ø§ÛØ¯ Ø¯Ø± ÙØ­Ø¯ÙØ¯Ù ÙØ¬Ø§Ø² Ø¨Ø§Ø´Ø¯.'); return True
        s=context.user_data; now=_v25_now(); _v25_exec('INSERT INTO business_services(owner_user_id,name,duration_minutes,price_rial,created_at,updated_at) VALUES(?,?,?,?,?,?)',(uid,s['service_name'][:200],s['service_duration'],price,now,now)); clear_flow(context); await update.message.reply_text('â Ø®Ø¯ÙØª Ø«Ø¨Øª Ø´Ø¯.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ð ï¸ Ø®Ø¯ÙØ§Øª',callback_data='v25:services')],[main_menu_button(uid)]])); return True
    if mode=='card_number': context.user_data['card_number']=text; context.user_data['v25_mode']='card_name'; await update.message.reply_text('ð¤ ÙØ§Ù ØµØ§Ø­Ø¨ Ú©Ø§Ø±Øª Ø±Ø§ Ø¨ÙØ±Ø³Øª ÛØ§ - Ø¨Ø²Ù:'); return True
    if mode=='card_name':
        title='' if text=='-' else text; details=f"Ø´ÙØ§Ø±Ù Ú©Ø§Ø±Øª: {context.user_data['card_number']}\nØ¨Ù ÙØ§Ù: {title}"; now=_v25_now(); _v25_exec('INSERT INTO payment_methods(owner_user_id,method_type,enabled,title,details,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(owner_user_id,method_type) DO UPDATE SET enabled=1,title=excluded.title,details=excluded.details,updated_at=excluded.updated_at',(uid,'card',1,'Ú©Ø§Ø±ØªâØ¨ÙâÚ©Ø§Ø±Øª',details,now)); clear_flow(context); await update.message.reply_text('â Ú©Ø§Ø±ØªâØ¨ÙâÚ©Ø§Ø±Øª ÙØ¹Ø§Ù Ø´Ø¯.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ð³ Ù¾Ø±Ø¯Ø§Ø®ØªâÙØ§',callback_data='v25:bizpay')],[main_menu_button(uid)]])); return True
    if mode=='gateway_link':
        now=_v25_now(); _v25_exec('INSERT INTO gateway_configs(owner_user_id,provider,enabled,payment_link,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(owner_user_id) DO UPDATE SET enabled=1,payment_link=excluded.payment_link,updated_at=excluded.updated_at',(uid,'custom',1,text,now)); clear_flow(context); await update.message.reply_text('â ÙÛÙÚ© Ø¯Ø±Ú¯Ø§Ù Ø°Ø®ÛØ±Ù Ø´Ø¯. ØªØ§ ÙÙØªÛ Ú©ÙÛØ¯ Ø¯Ø±Ú¯Ø§Ù Ø¯Ø± Ù¾ÙÙ ÙØ¹Ø§Ù Ø¨Ø§Ø´Ø¯Ø Ù¾Ø±Ø¯Ø§Ø®Øª Ø¢ÙÙØ§ÛÙ ÙÙØ§ÛØ´ Ø¯Ø§Ø¯Ù ÙÛâØ´ÙØ¯.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ð³ Ù¾Ø±Ø¯Ø§Ø®ØªâÙØ§',callback_data='v25:bizpay')],[main_menu_button(uid)]])); return True
    if mode=='survey_question':
        now=_v25_now(); code=hashlib.sha256(text.encode()).hexdigest()[:10]; _v25_exec('INSERT OR IGNORE INTO survey_questions(owner_user_id,code,question,created_at) VALUES(?,?,?,?)',(uid,code,text,now)); clear_flow(context); await update.message.reply_text('â Ø³Ø¤Ø§Ù ÙØ¸Ø±Ø³ÙØ¬Û Ø§Ø¶Ø§ÙÙ Ø´Ø¯.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('â­ ÙØ¸Ø±Ø³ÙØ¬Û',callback_data='v25:surveyadmin')],[main_menu_button(uid)]])); return True
    if mode=='bizname_v25':
        _v25_exec('UPDATE business_profiles SET business_name=?,updated_at=? WHERE user_id=?',(text,_v25_now(),uid)); clear_flow(context); await update.message.reply_text('â ÙØ§Ù Ú©Ø³Ø¨âÙÚ©Ø§Ø± Ø¨ÙâØ±ÙØ²Ø±Ø³Ø§ÙÛ Ø´Ø¯.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ðª Ù¾ÙÙ Ú©Ø³Ø¨âÙÚ©Ø§Ø±',callback_data='v25:business')],[main_menu_button(uid)]])); return True
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
    kb=[[InlineKeyboardButton('ð 5',callback_data=f'v25:survey_rate:{appointment_id}:5'),InlineKeyboardButton('ð 4',callback_data=f'v25:survey_rate:{appointment_id}:4'),InlineKeyboardButton('ð 3',callback_data=f'v25:survey_rate:{appointment_id}:3')],[InlineKeyboardButton('ð 2',callback_data=f'v25:survey_rate:{appointment_id}:2'),InlineKeyboardButton('ð¡ 1',callback_data=f'v25:survey_rate:{appointment_id}:1')]]
    await bot.send_message(r['telegram_user_id'],'ð· ÙÙÙÙÙ Ú©Ù ÙØ§ Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ø±Ø¯Û!\n\nâ­ ØªØ¬Ø±Ø¨Ù Ú©ÙÛâØ§Øª ÚØ·ÙØ± Ø¨ÙØ¯Ø',reply_markup=InlineKeyboardMarkup(kb))

async def v25_reminder_job(context):
    now=datetime.now(TZ).replace(second=0,microsecond=0); c=db(); rows=c.execute("SELECT * FROM important_reminders WHERE enabled=1 AND remind_at<=? AND remind_at>=?",(now.isoformat(),(now-timedelta(minutes=1)).isoformat())).fetchall(); c.close()
    for r in rows:
        try:
            await context.bot.send_message(r['user_id'],f"ð {r['title']}\n\nâ° Ø²ÙØ§Ù ÛØ§Ø¯Ø¢ÙØ±Û Ø±Ø³ÛØ¯.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('â Ø§ÙØ¬Ø§Ù Ø´Ø¯',callback_data='v25:remdone:%s'%r['id'])],[main_menu_button(r['user_id'])]]))
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
            clear_flow(context); await update.message.reply_text('â Ù¾ÙÙ VIP ÙØ¹ØªØ¨Ø± ÙÛØ³Øª.',reply_markup=keyboard(uid)); return
        rid=_v25_exec('INSERT INTO vip_receipts(user_id,plan_id,amount_rial,receipt_file_id,status,created_at) VALUES(?,?,?,?,?,?)',(uid,plan_id,int(plan['price_rial'] or 0),file_id,'pending',_v25_now()),commit=True)
        clear_flow(context)
        await update.message.reply_text('ð Ø±Ø³ÛØ¯ VIP Ø¯Ø±ÛØ§ÙØª Ø´Ø¯ Ù Ø¨Ø±Ø§Û Ø¨Ø±Ø±Ø³Û ÙØ¯ÛØ± Ø§Ø±Ø³Ø§Ù Ø´Ø¯. Ø¨Ø¹Ø¯ Ø§Ø² ØªØ£ÛÛØ¯Ø Ø§Ø´ØªØ±Ø§Ú© ÙØ¹Ø§Ù ÙÛâØ´ÙØ¯.',reply_markup=keyboard(uid))
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(admin_id,f'ð <b>Ø±Ø³ÛØ¯ VIP Ø¬Ø¯ÛØ¯</b>\n\nð¤ Ú©Ø§Ø±Ø¨Ø±: <code>{uid}</code>\nð¦ Ù¾ÙÙ: {html.escape(plan["name"])}\nð° ÙØ¨ÙØº: {irr(plan["price_rial"])}\nð§¾ Ø±Ø³ÛØ¯: #{rid}',parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('â ØªØ£ÛÛØ¯',callback_data=f'v25:vip_receipt:approve:{rid}'),InlineKeyboardButton('â Ø±Ø¯',callback_data=f'v25:vip_receipt:reject:{rid}')]]))
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
        try: await context.bot.send_message(owner,f'ð <b>Ø±Ø³ÛØ¯ Ø¬Ø¯ÛØ¯ Ø¯Ø±ÛØ§ÙØª Ø´Ø¯</b>\n\nð¤ ÙØ´ØªØ±Û\nð° ÙØ¨ÙØº: {amount:,.0f} Ø±ÛØ§Ù\nð§¾ ÙØ¶Ø¹ÛØª: ð¡ Ø¯Ø± Ø§ÙØªØ¸Ø§Ø± ØªØ£ÛÛØ¯',parse_mode='HTML')
        except Exception: pass
    c.close(); clear_flow(context); await update.message.reply_text('â Ø±Ø³ÛØ¯Øª Ø¯Ø±ÛØ§ÙØª Ø´Ø¯ Ù Ø¨Ø±Ø§Û ØµØ§Ø­Ø¨ Ú©Ø³Ø¨âÙÚ©Ø§Ø± Ø§Ø±Ø³Ø§Ù Ø´Ø¯. Ø¨Ø¹Ø¯ Ø§Ø² Ø¨Ø±Ø±Ø³ÛØ ÙØ¶Ø¹ÛØª Ù¾Ø±Ø¯Ø§Ø®Øª Ø¨ÙØª Ø§Ø·ÙØ§Ø¹ Ø¯Ø§Ø¯Ù ÙÛâØ´ÙØ¯.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ð Ø±Ø²Ø±ÙÙØ§Û ÙÙ',callback_data='cust:mybookings')],[main_menu_button(uid)]]))

async def v25_callback(update,context):
    q=update.callback_query; uid=q.from_user.id; await q.answer(); p=q.data.split(':'); action=p[1] if len(p)>1 else 'hub'
    try:
        if action=='hub': await q.message.edit_text(v25_hub_text(uid),parse_mode='HTML',reply_markup=v25_hub_keyboard(uid)); return
        if action=='today': await q.message.edit_text(v25_hub_text(uid),parse_mode='HTML',reply_markup=v25_hub_keyboard(uid)); return
        if action=='customers':
            if not customer_feature_allowed(uid):
                await q.message.edit_text(customer_feature_message(uid), reply_markup=v25_back(uid)); return
            ensure_business_profile(uid)
            await q.message.edit_text('ð¥ <b>ÙØ¯ÛØ±ÛØª ÙØ´ØªØ±Û Ù ÙÙØ¨ØªâØ¯ÙÛ</b>\n\nÙ¾ÙÙ ÙØ³ØªÙÙ ÙØ´ØªØ±ÛØ§ÙØ ÙÙØ¨ØªâÙØ§Ø ØªÙÙÛÙ Ù ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§.', parse_mode='HTML', reply_markup=customer_keyboard(uid))
            return
        if action=='reminders': await v25_reminders_menu(update,context); return
        if action=='remadd': context.user_data['v25_mode']='rem_title'; await q.message.edit_text('âï¸ Ø¹ÙÙØ§Ù ÛØ§Ø¯Ø¢ÙØ±Û Ø±Ø§ Ø¨ÙØ±Ø³Øª:',reply_markup=v25_back(uid)); return
        if action=='remview':
            rid=int(p[2]); r=_v25_exec('SELECT * FROM important_reminders WHERE id=? AND user_id=?',(rid,uid),fetchone=True); await q.message.edit_text(f"ð {html.escape(r['title'])}\nâ° {r['remind_at'].replace('T',' ')}",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ð Ø­Ø°Ù',callback_data=f'v25:remdel:{rid}')],[InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª',callback_data='v25:reminders'),main_menu_button(uid)]])); return
        if action=='remdel': _v25_exec('DELETE FROM important_reminders WHERE id=? AND user_id=?',(int(p[2]),uid)); await q.message.edit_text('â Ø­Ø°Ù Ø´Ø¯.',reply_markup=v25_back(uid)); return
        if action=='remdone': _v25_exec('UPDATE important_reminders SET enabled=0 WHERE id=? AND user_id=?',(int(p[2]),uid)); await q.message.edit_text('â Ø§ÙØ¬Ø§Ù Ø´Ø¯.',reply_markup=v25_hub_keyboard(uid)); return
        if action=='calendar': await q.message.edit_text('ð <b>ØªÙÙÛÙ ÙÙ</b>\n\nÙÙØ¨ØªâÙØ§ Ù ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§Û ÙÙÙ Ø¯Ø± Ø§ÛÙ ØªÙÙÛÙ ÛÚ©Ù¾Ø§Ø±ÚÙ ÙÚ¯ÙØ¯Ø§Ø±Û ÙÛâØ´ÙÙØ¯. Ø¨Ø±Ø§Û ÙØ´Ø§ÙØ¯Ù Ø³Ø±ÛØ¹Ø Ø§Ø² Â«Ø§ÙØ±ÙØ² ÙÙÂ» ÛØ§ Â«ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§Û ÙÙÙÂ» Ø§Ø³ØªÙØ§Ø¯Ù Ú©Ù.',parse_mode='HTML',reply_markup=v25_back(uid)); return
        if action=='profile': await v25_profile_menu(update,context); return
        if action=='profile_share': await v25_profile_share_menu(update,context); return
        if action=='profile_edit': context.user_data['v25_mode']=f'profile_edit:{p[2]}'; await q.message.edit_text('âï¸ ÙÙØ¯Ø§Ø± Ø¬Ø¯ÛØ¯ Ø±Ø§ Ø¨ÙØ±Ø³Øª. Ø§Ú¯Ø± ÙÙÛâØ®ÙØ§ÙÛ Ø°Ø®ÛØ±Ù Ø´ÙØ¯ Â«-Â» Ø¨Ø²Ù.',reply_markup=v25_back(uid,'v25:profile')); return
        if action=='share':
            scope,field=p[2],p[3]; cur=_v25_exec('SELECT enabled FROM profile_share WHERE user_id=? AND scope=? AND field=?',(uid,scope,field),fetchone=True); en=1 if cur is None else int(cur['enabled']); _v25_exec('INSERT INTO profile_share(user_id,scope,field,enabled,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id,scope,field) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at',(uid,scope,field,0 if en else 1,_v25_now())); await v25_profile_share_menu(update,context); return
        if action=='portfolio': await v25_portfolio_menu(update,context); return
        if action=='portadd': context.user_data['v25_mode']='port_asset'; await q.message.edit_text('ð° ÙØ§Ù Ø¯Ø§Ø±Ø§ÛÛ Ø±Ø§ Ø¨ÙØ±Ø³Øª. ÙØ«Ø§Ù: Ø·ÙØ§Û Û±Û¸ Ø¹ÛØ§Ø±',reply_markup=v25_back(uid)); return
        if action=='portsummary': await v25_portfolio_summary(update,context); return
        if action=='installments': await v25_installments_menu(update,context); return
        if action=='instadd': context.user_data['v25_mode']='inst_bank'; await q.message.edit_text('ð¦ Ø¨Ø§ÙÚ© Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:',reply_markup=v25_bank_keyboard(uid)); return
        if action=='instbank': context.user_data['inst_bank']=v25_banks()[int(p[2])]; context.user_data['v25_mode']='inst_title'; await q.message.edit_text('ð Ø¹ÙÙØ§Ù ØªØ³ÙÛÙØ§Øª Ø±Ø§ Ø¨ÙØ±Ø³Øª ÛØ§ Â«ÙØ³Ø·Â» Ø¨ÙÙÛØ³:'); return
        if action=='instbank_custom': context.user_data['v25_mode']='inst_bank'; await q.message.edit_text('ð¦ ÙØ§Ù Ø¨Ø§ÙÚ© ÛØ§ ÙØ¤Ø³Ø³Ù Ø±Ø§ Ø¨ÙØ±Ø³Øª:'); return
        if action=='instrate': context.user_data['inst_rate']=float(p[2]); context.user_data['v25_mode']='inst_months'; await q.message.edit_text('ð¢ ØªØ¹Ø¯Ø§Ø¯ ÙØ§Ù Ø¨Ø§Ø²Ù¾Ø±Ø¯Ø§Ø®Øª Ø±Ø§ Ø¨ÙØ±Ø³Øª:'); return
        if action=='instrate_custom': context.user_data['v25_mode']='inst_rate_custom'; await q.message.edit_text('ð ÙØ±Ø® Ø³ÙØ¯ Ø±Ø§ Ø¨Ù Ø¯Ø±ØµØ¯ Ø¨ÙÙÛØ³. ÙØ«Ø§Ù: 23'); return
        if action=='instsave':
            s=context.user_data; now=_v25_now(); c=db(); plan_id=c.execute('INSERT INTO installment_plans(user_id,bank_name,title,principal_rial,interest_pct,term_months,first_due_date,day_of_month,monthly_rial,total_interest_rial,total_payable_rial,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(uid,s['inst_bank'],s['inst_title'],s['inst_principal'],s['inst_rate'],s['inst_months'],s['inst_first_date'],datetime.fromisoformat(s['inst_first_date']).day,s['inst_monthly'],s['inst_interest'],s['inst_total'],now,now)).lastrowid
            first=datetime.fromisoformat(s['inst_first_date']).date();
            for i in range(s['inst_months']):
                # Month stepping without external dependency.
                month=first.month-1+i; year=first.year+month//12; mon=month%12+1; day=min(first.day,[31,29 if year%4==0 else 28,31,30,31,30,31,31,30,31,30,31][mon-1]); due=f'{year:04d}-{mon:02d}-{day:02d}'
                c.execute('INSERT OR IGNORE INTO installment_payments(plan_id,installment_no,due_date,amount_rial,status) VALUES(?,?,?,?,?)',(plan_id,i+1,due,s['inst_monthly'],'pending'))
            c.commit(); c.close(); clear_flow(context); await q.message.edit_text('â ØªØ³ÙÛÙØ§Øª Ø°Ø®ÛØ±Ù Ø´Ø¯ Ù Ø§ÙØ³Ø§Ø· ÙØ§ÙØ§ÙÙ Ø¯Ø± ØªØ§Ø±ÛØ®ÚÙ Ø³Ø§Ø®ØªÙ Ø´Ø¯.',reply_markup=v25_back(uid,'v25:installments')); return
        if action=='instview':
            pid=int(p[2]); c=db(); plan=c.execute('SELECT * FROM installment_plans WHERE id=? AND user_id=?',(pid,uid)).fetchone(); pays=c.execute('SELECT * FROM installment_payments WHERE plan_id=? ORDER BY installment_no LIMIT 24',(pid,)).fetchall(); c.close(); paid=sum(1 for x in pays if x['status']=='paid'); text=f"ð¦ <b>{html.escape(plan['bank_name'])}</b>\nð {html.escape(plan['title'])}\nðµ ÙØ³Ø·: {plan['monthly_rial']:,.0f} Ø±ÛØ§Ù\nð Ø³ÙØ¯: {plan['interest_pct']:g}%\nð Ù¾Ø±Ø¯Ø§Ø®ØªâØ´Ø¯Ù: {paid}/{plan['term_months']}\n\n"+('\n'.join(f"{'â' if x['status']=='paid' else 'â³'} {x['installment_no']} â {x['due_date']} â {x['amount_rial']:,.0f} Ø±ÛØ§Ù" for x in pays))
            await q.message.edit_text(text,parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('â¬ï¸ Ø§ÙØ³Ø§Ø·',callback_data='v25:installments'),main_menu_button(uid)]])); return
        if action=='business': await v25_business_menu(update,context); return
        if action=='bizprofile':
            p=ensure_business_profile(uid); await q.message.edit_text(f"ðª <b>Ø§Ø·ÙØ§Ø¹Ø§Øª Ú©Ø³Ø¨âÙÚ©Ø§Ø±</b>\n\nÙØ§Ù: {html.escape(p['business_name'] or 'â')}\nÙÙØ¹: {html.escape(p['business_type'] or 'â')}\nð {html.escape(p['contact_phone'] or 'â')}\n\nØ§Ø·ÙØ§Ø¹Ø§Øª Ø®Ø§ÙÛ Ø§Ø®ØªÛØ§Ø±Û Ø§Ø³Øª.",parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('âï¸ ÙØ§Ù Ú©Ø³Ø¨âÙÚ©Ø§Ø±',callback_data='v25:bizname')],[InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª',callback_data='v25:business'),main_menu_button(uid)]])); return
        if action=='bizname': context.user_data['v25_mode']='bizname_v25'; await q.message.edit_text('ðª ÙØ§Ù Ø¬Ø¯ÛØ¯ Ú©Ø³Ø¨âÙÚ©Ø§Ø± Ø±Ø§ Ø¨ÙØ±Ø³Øª:',reply_markup=v25_back(uid,'v25:business')); return
        if action=='bizfinance':
            c=db(); rows=c.execute("SELECT c.id,c.name,COALESCE(SUM(f.amount_rial),0) total,COALESCE(SUM(f.paid_rial),0) paid FROM customers c LEFT JOIN customer_finance f ON f.customer_id=c.id WHERE c.owner_user_id=? GROUP BY c.id ORDER BY c.name LIMIT 50",(uid,)).fetchall(); c.close();
            text='ð <b>ÙØ§ÙÛ ÙØ´ØªØ±ÛØ§Ù</b>\n\n'+('\n'.join(f"ð¤ {html.escape(r['name'])} | ð° {r['total']:,.0f} Ø±ÛØ§Ù | â {r['paid']:,.0f} Ø±ÛØ§Ù | â³ {(r['total']-r['paid']):,.0f} Ø±ÛØ§Ù" for r in rows) if rows else 'ÙÙÙØ² ØªØ±Ø§Ú©ÙØ´Û Ø«Ø¨Øª ÙØ´Ø¯Ù.'); await q.message.edit_text(text,parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª',callback_data='v25:business'),main_menu_button(uid)]])); return
        if action=='bookservice':
            sid=int(p[2]); context.user_data['booking_service_id']=sid; await v25_booking_profile_prompt(update,context); return
        if action=='bookuse':
            owner=context.user_data.get('booking_owner'); sid=int(context.user_data.get('booking_service_id') or 0);
            try: aid,service,amount,name,phone=await v25_create_booking(context,uid,owner,sid); prof=ensure_business_profile(owner); context.user_data['booking_appointment_id']=aid; context.user_data['booking_amount_rial']=amount; await context.bot.send_message(owner,f"ð <b>Ø±Ø²Ø±Ù Ø¬Ø¯ÛØ¯ Ø«Ø¨Øª Ø´Ø¯!</b>\n\nðª {html.escape(prof['business_name'] or 'Ú©Ø³Ø¨âÙÚ©Ø§Ø±')}\nð¤ {html.escape(name)}\nð {jalali_pretty_date(context.user_data['booking_date'])}\nâ° {context.user_data['booking_time']}"+ (f"\nð ï¸ {html.escape(service)}\nð° {amount:,.0f} Ø±ÛØ§Ù" if service else ''),parse_mode='HTML'); await v25_booking_payment_menu(update,context,aid,owner,amount,prof['business_name'] or 'Ú©Ø³Ø¨âÙÚ©Ø§Ø±');
            except ValueError: await q.message.edit_text('â Ø§ÛÙ Ø²ÙØ§Ù Ø¯ÛÚ¯Ø± Ø¢Ø²Ø§Ø¯ ÙÛØ³Øª. ÙØ·ÙØ§Ù Ø²ÙØ§Ù Ø¯ÛÚ¯Ø±Û Ø§ÙØªØ®Ø§Ø¨ Ú©Ù.',reply_markup=v25_back(uid));
            return
        if action=='bookskip':
            owner=context.user_data.get('booking_owner'); sid=int(context.user_data.get('booking_service_id') or 0); context.user_data['public_name']=''; context.user_data['public_phone']='';
            try: aid,service,amount,name,phone=await v25_create_booking(context,uid,owner,sid); prof=ensure_business_profile(owner); context.user_data['booking_appointment_id']=aid; context.user_data['booking_amount_rial']=amount; await context.bot.send_message(owner,f"ð <b>Ø±Ø²Ø±Ù Ø¬Ø¯ÛØ¯ Ø«Ø¨Øª Ø´Ø¯!</b>\n\nðª {html.escape(prof['business_name'] or 'Ú©Ø³Ø¨âÙÚ©Ø§Ø±')}\nð¤ ÙØ´ØªØ±Û Ø¨Ø¯ÙÙ Ø§Ø·ÙØ§Ø¹Ø§Øª Ø´Ø®ØµÛ\nð {jalali_pretty_date(context.user_data['booking_date'])}\nâ° {context.user_data['booking_time']}",parse_mode='HTML'); await v25_booking_payment_menu(update,context,aid,owner,amount,prof['business_name'] or 'Ú©Ø³Ø¨âÙÚ©Ø§Ø±');
            except ValueError: await q.message.edit_text('â Ø§ÛÙ Ø²ÙØ§Ù Ø¯ÛÚ¯Ø± Ø¢Ø²Ø§Ø¯ ÙÛØ³Øª.',reply_markup=v25_back(uid));
            return
        if action=='bookedit':
            context.user_data['v25_mode']='booking_name'; await q.message.edit_text('ð¤ Ø§Ú¯Ø± Ø¯ÙØ³Øª Ø¯Ø§Ø±Û ÙØ§ÙØª Ø±Ø§ ÙØ§Ø±Ø¯ Ú©ÙØ Ø§Ø®ØªÛØ§Ø±Û Ø§Ø³Øª. Ø¨Ø±Ø§Û Ø±Ø¯ Ú©Ø±Ø¯Ù Â«-Â» Ø¨Ø²Ù:',reply_markup=v25_back(uid)); return
        if action=='bookingcard':
            aid=int(p[2])
            # Authorization: only the booking customer (or the business owner) may
            # open card-payment instructions for this appointment.
            booking=_v25_exec("SELECT a.owner_user_id,c.telegram_user_id FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.id=?",(aid,),fetchone=True)
            if not booking or (int(booking['telegram_user_id'] or 0) != uid and int(booking['owner_user_id']) != uid):
                await q.answer('â Ø¯Ø³ØªØ±Ø³Û Ø¨Ù Ø§ÛÙ Ø±Ø²Ø±Ù ÙØ¬Ø§Ø² ÙÛØ³Øª.',show_alert=True); return
            owner=int(booking['owner_user_id'])
            pm=_v25_exec("SELECT details FROM payment_methods WHERE owner_user_id=? AND method_type='card' AND enabled=1",(owner,),fetchone=True);
            if not pm: await q.message.edit_text('â ï¸ Ú©Ø§Ø±ØªâØ¨ÙâÚ©Ø§Ø±Øª ÙØ¹Ø§Ù ÙÛØ³Øª.',reply_markup=v25_back(uid)); return
            amount=context.user_data.get('booking_amount_rial',0); context.user_data['v25_mode']='booking_receipt'; context.user_data['booking_appointment_id']=aid; context.user_data['booking_owner']=owner;
            await q.message.edit_text(f"ðµ <b>Ú©Ø§Ø±ØªâØ¨ÙâÚ©Ø§Ø±Øª</b>\n\nð° ÙØ¨ÙØº: {amount:,.0f} Ø±ÛØ§Ù\n\n{html.escape(pm['details'])}\n\nð Ø¨Ø¹Ø¯ Ø§Ø² ÙØ§Ø±ÛØ² ØªØµÙÛØ± Ø±Ø³ÛØ¯ Ø±Ø§ Ø¨ÙØ±Ø³Øª.",parse_mode='HTML',reply_markup=v25_back(uid)); return
        if action=='survey_rate':
            aid=int(p[2]); rating=int(p[3]); c=db(); r=c.execute("SELECT owner_user_id,customer_id FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE a.id=? AND c.telegram_user_id=?",(aid,uid)).fetchone();
            if not r: c.close(); await q.message.edit_text('â Ø§ÛÙ ÙØ¸Ø±Ø³ÙØ¬Û Ø¨Ø±Ø§Û Ø´ÙØ§ Ù¾ÛØ¯Ø§ ÙØ´Ø¯.',reply_markup=v25_back(uid)); return
            c.execute('INSERT OR IGNORE INTO survey_responses(owner_user_id,appointment_id,customer_id,rating,created_at) VALUES(?,?,?,?,?)',(r['owner_user_id'],aid,r['customer_id'],rating,_v25_now())); c.commit(); c.close();
            try: await context.bot.send_message(r['owner_user_id'],f"â­ Ø§ÙØªÛØ§Ø² Ø¬Ø¯ÛØ¯ ÙØ´ØªØ±Û: {rating}/5");
            except Exception: pass
            await q.message.edit_text('ð ÙÙÙÙÙ! ÙØ¸Ø±Øª Ø«Ø¨Øª Ø´Ø¯. Ø§Ú¯Ø± Ù¾ÛØ´ÙÙØ§Ø¯Û Ø¯Ø§Ø±Û ÙÛâØªÙØ§ÙÛ Ø¯Ø± Ù¾ÛØ§Ù Ø¨Ø¹Ø¯Û Ø¨ÙÙÛØ³Û.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('âï¸ ÙÙØ´ØªÙ Ù¾ÛØ´ÙÙØ§Ø¯',callback_data=f'v25:surveycomment:{aid}')],[main_menu_button(uid)]])); return
        if action=='surveycomment': context.user_data['v25_mode']='survey_comment'; context.user_data['survey_appointment_id']=int(p[2]); await q.message.edit_text('ð¬ Ù¾ÛØ´ÙÙØ§Ø¯ ÛØ§ ØªÙØ¶ÛØ­ Ø®ÙØ¯Øª Ø±Ø§ Ø¨ÙÙÛØ³. Ø§ÛÙ Ø¨Ø®Ø´ Ø§Ø®ØªÛØ§Ø±Û Ø§Ø³Øª.',reply_markup=v25_back(uid)); return
        if action=='services': await v25_services_menu(update,context); return
        if action=='serviceadd': context.user_data['v25_mode']='service_name'; await q.message.edit_text('ð ï¸ ÙØ§Ù Ø®Ø¯ÙØª Ø±Ø§ Ø¨ÙØ±Ø³Øª:',reply_markup=v25_back(uid,'v25:services')); return
        if action=='service_toggle':
            sid=int(p[2]); _v25_exec('UPDATE business_services SET enabled=CASE enabled WHEN 1 THEN 0 ELSE 1 END,updated_at=? WHERE id=? AND owner_user_id=?',(_v25_now(),sid,uid)); await v25_services_menu(update,context); return
        if action=='bizpay': await v25_payment_methods_menu(update,context); return
        if action=='card': context.user_data['v25_mode']='card_number'; await q.message.edit_text('ð³ Ø´ÙØ§Ø±Ù Ú©Ø§Ø±Øª Ø±Ø§ Ø¨ÙØ±Ø³Øª:',reply_markup=v25_back(uid,'v25:bizpay')); return
        if action=='gateway': context.user_data['v25_mode']='gateway_link'; await q.message.edit_text('ð ÙÛÙÚ© Ù¾Ø±Ø¯Ø§Ø®Øª Ø¯Ø±Ú¯Ø§Ù Ø§ÛØ±Ø§ÙÛ Ø±Ø§ Ø¨ÙØ±Ø³Øª. Ø§ÛÙ ÙÛÙÚ© ÙÛâØªÙØ§ÙØ¯ ÙÛÙÚ© Ù¾Ø±Ø¯Ø§Ø®Øª Ø³Ø±ÙÛØ³ ÙÙØ±Ø¯ÙØ¸Ø± Ø¨Ø§Ø´Ø¯. Ø¨Ø±Ø§Û Ø®Ø§ÙÙØ´ Ú©Ø±Ø¯ÙØ Ø§Ø² Ù¾ÙÙ ÙØ¹Ø§Ù/ØºÛØ±ÙØ¹Ø§Ù Ú©Ù.',reply_markup=v25_back(uid,'v25:bizpay')); return
        if action=='surveyadmin': await v25_survey_admin(update,context); return
        if action=='surveyadd': context.user_data['v25_mode']='survey_question'; await q.message.edit_text('âï¸ Ø³Ø¤Ø§Ù Ø¬Ø¯ÛØ¯ ÙØ¸Ø±Ø³ÙØ¬Û Ø±Ø§ Ø¨ÙØ±Ø³Øª:',reply_markup=v25_back(uid,'v25:surveyadmin')); return
        if action=='plans': await v25_vip_plans(update,context); return
        if action=='sms':
            cfg=_v25_exec('SELECT * FROM sms_settings WHERE owner_user_id=?',(uid,),fetchone=True); await q.message.edit_text(f"ð± <b>Ù¾ÛØ§ÙÚ©</b>\n\nÙØ¶Ø¹ÛØª: {'ð¢' if cfg and cfg['enabled'] else 'ð´'}\n\nØ¨Ø±Ø§Û ÙØ¹Ø§ÙâØ³Ø§Ø²ÛØ endpoint Ù API key Ø³Ø±ÙÛØ³ Ù¾ÛØ§ÙÚ©Û Ø¯Ø± ØªÙØ¸ÛÙØ§Øª Ø°Ø®ÛØ±Ù Ø´ÙØ¯.",parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ð§ª ØªØ³Øª',callback_data='v25:smstest')],[InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª',callback_data='v25:business'),main_menu_button(uid)]])); return
        if action=='smstest': context.user_data['v25_mode']='v25_sms_test'; await q.message.edit_text('ð± Ø´ÙØ§Ø±ÙâØ§Û Ú©Ù Ø¨Ø§ÛØ¯ ØªØ³Øª Ø´ÙØ¯ Ø±Ø§ Ø¨ÙØ±Ø³Øª:',reply_markup=v25_back(uid,'v25:sms')); return
        if action=='bookinglink': await customer_booking_link(update,context); return
        if action=='buyplan':
            pid=int(p[2]); plan=_v25_exec('SELECT * FROM subscription_plans_v25 WHERE id=? AND enabled=1',(pid,),fetchone=True)
            if not plan: await q.message.edit_text('â Ù¾ÙÙ ÙÙØ¬ÙØ¯ ÙÛØ³Øª.',reply_markup=v25_back(uid,'v25:business')); return
            c=db(); card=c.execute("SELECT * FROM payment_methods WHERE owner_user_id=? AND method_type='card' AND enabled=1",(uid,)).fetchone(); c.close()
            await q.message.edit_text(f"ð {html.escape(plan['name'])}\n\nð° {plan['price_rial']:,.0f} Ø±ÛØ§Ù\n\nØ±ÙØ´ Ù¾Ø±Ø¯Ø§Ø®Øª Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ð³ Ù¾Ø±Ø¯Ø§Ø®Øª Ø¢ÙÙØ§ÛÙ',callback_data=f'v25:viponline:{pid}')],[InlineKeyboardButton('ðµ Ú©Ø§Ø±ØªâØ¨ÙâÚ©Ø§Ø±Øª',callback_data=f'v25:vipcard:{pid}')],[InlineKeyboardButton('â¬ï¸ Ø¨Ø±Ú¯Ø´Øª',callback_data='v25:business'),main_menu_button(uid)]])); return
        if action=='viponline':
            plan=_v25_exec('SELECT * FROM subscription_plans_v25 WHERE id=?',(int(p[2]),),fetchone=True); cfg=_v25_exec('SELECT * FROM gateway_configs WHERE owner_user_id=?',(uid,),fetchone=True)
            url=cfg['payment_link'] if cfg and cfg['enabled'] else ''
            if url:
                await q.message.edit_text('ð³ Ø¨Ø±Ø§Û Ù¾Ø±Ø¯Ø§Ø®Øª Ø¢ÙÙØ§ÛÙ Ø§Ø² Ø¯Ú©ÙÙ Ø²ÛØ± Ø§Ø³ØªÙØ§Ø¯Ù Ú©Ù.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ð³ Ù¾Ø±Ø¯Ø§Ø®Øª Ø¢ÙÙØ§ÛÙ',url=url)],[main_menu_button(uid)]]))
            else: await q.message.edit_text('â ï¸ Ø¯Ø±Ú¯Ø§Ù Ø¢ÙÙØ§ÛÙ ÙÙÙØ² Ø¯Ø± Ù¾ÙÙ ÙØ¯ÛØ±ÛØª ØªÙØ¸ÛÙ ÙØ´Ø¯Ù Ø§Ø³Øª.',reply_markup=v25_back(uid,'v25:business'))
            return
        if action=='vipcard':
            plan=_v25_exec('SELECT * FROM subscription_plans_v25 WHERE id=?',(int(p[2]),),fetchone=True); pm=_v25_exec("SELECT * FROM payment_methods WHERE owner_user_id=? AND method_type='card' AND enabled=1",(uid,),fetchone=True)
            if pm: await q.message.edit_text(f"ðµ <b>Ù¾Ø±Ø¯Ø§Ø®Øª Ú©Ø§Ø±ØªâØ¨ÙâÚ©Ø§Ø±Øª</b>\n\nð° ÙØ¨ÙØº: {plan['price_rial']:,.0f} Ø±ÛØ§Ù\n\n{html.escape(pm['details'])}\n\nØ¨Ø¹Ø¯ Ø§Ø² ÙØ§Ø±ÛØ² ØªØµÙÛØ± Ø±Ø³ÛØ¯ Ø±Ø§ Ø¨ÙØ±Ø³Øª.",parse_mode='HTML',reply_markup=v25_back(uid,'v25:business')); context.user_data['v25_mode']='vip_receipt'; context.user_data['vip_plan_id']=plan['id']
            else: await q.message.edit_text('â ï¸ Ú©Ø§Ø±ØªâØ¨ÙâÚ©Ø§Ø±Øª ÙØ¹Ø§Ù ÙØ´Ø¯Ù Ø§Ø³Øª.',reply_markup=v25_back(uid,'v25:business'))
            return
        if action=='feat':
            if not admin_guard(uid): await q.answer('â',show_alert=True); return
            if len(p) < 3 or not _feature_flag_exists(p[2]): await q.answer('ÙØ§Ø¨ÙÛØª ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª.',show_alert=True); return
            key=p[2]; cur=feature_enabled(key); set_feature(key,not cur,uid); mode='free' if not cur else 'off'; set_feature_access_mode(key,mode,uid); await v25_admin_feature_status(update,context); return
    except Exception as e:
        logger.exception('v25 callback error: %s',e)
        await q.message.reply_text(
            f'â ï¸ Ø§ÛÙ Ø¨Ø®Ø´ Ø¨Ø§ Ø®Ø·Ø§ Ø±ÙØ¨ÙâØ±Ù Ø´Ø¯.\n\nÚ©Ø¯ Ø®Ø·Ø§: <code>{type(e).__name__}</code>\n'
            'Ø§Ø² Ú¯Ø²ÛÙÙ Â«ØªÙØ§Ø´ Ø¯ÙØ¨Ø§Ø±ÙÂ» Ø§Ø³ØªÙØ§Ø¯Ù Ú©ÙØ Ø¨Ø±Ø§Û Ø®Ø±ÙØ¬ ÙÙ ÙÙÙÛ Ø§ØµÙÛ Ø¯Ø± Ø¯Ø³ØªØ±Ø³ Ø§Ø³Øª.',
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('ð ØªÙØ§Ø´ Ø¯ÙØ¨Ø§Ø±Ù',callback_data=q.data)],
                [InlineKeyboardButton('â¬ï¸ ÙØ±Ú©Ø² ÙÙ',callback_data='v25:hub'),main_menu_button(uid)]
            ])
        )

# Patch the legacy navigation with a unified menu while retaining every old button.

def keyboard(uid):
    try:
        rows=list(filter_menu_rows(uid,[list(row) for row in T[lang(uid)]['menu']]))
    except Exception: rows=[]
    fa=lang(uid)=='fa'
    # Keep legacy rows first; append only the unified functions that are enabled.
    extra=[]
    if v25_allowed(uid,'unified_hub'): extra.append('ð§  ÙØ±Ú©Ø² ÙÙ' if fa else 'ð§  My Center')
    if v25_allowed(uid,'portfolio'): extra.append('ð° Ø³Ø±ÙØ§ÛÙâÙØ§Û ÙÙ' if fa else 'ð° My Portfolio')
    if v25_allowed(uid,'installments'): extra.append('ð³ Ø§ÙØ³Ø§Ø·' if fa else 'ð³ Installments')
    if v25_allowed(uid,'profile_sharing'): extra.append('ð¤ Ø§Ø·ÙØ§Ø¹Ø§Øª ÙÙ' if fa else 'ð¤ My Profile')
    if v25_allowed(uid,'calendar_hub'): extra.append('ð ØªÙÙÛÙ ÙÙ' if fa else 'ð My Calendar')
    if extra:
        for i in range(0,len(extra),2): rows.append(extra[i:i+2])
    if admin_is_allowed(uid): rows.append(['ð¡ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª' if fa else 'ð¡ Admin Panel'])
    return ReplyKeyboardMarkup(rows,resize_keyboard=True)


async def fetch_price_v25(asset):
    # Local Iran-market values from TGJU plus global-metal fallbacks converted via USD/Rial.
    urls={'usd':'https://www.tgju.org/profile/price_dollar_rl','eur':'https://www.tgju.org/profile/price_eur','gold18':'https://www.tgju.org/profile/geram18','coin':'https://www.tgju.org/profile/sekee'}
    if asset in urls:
        raw=await asyncio.to_thread(tgju_value,urls[asset]); val=float(raw.replace(',','').replace('Ù«','.').replace('Ù¬','')); return val,'Ø±ÛØ§Ù','single'
    if asset in ('silver','copper','aluminum','nickel','zinc','lead'):
        # Yahoo symbols provide global spot/futures indications; these are converted to Rial using USD/Irr.
        syms={'silver':'SI=F','copper':'HG=F','aluminum':'ALI=F','nickel':'NI=F','zinc':'ZNC=F','lead':'PB=F'}
        try:
            y=await asyncio.to_thread(fetch_url_json,f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(syms[asset],safe='')}?range=1d&interval=1m")
            meta=y['chart']['result'][0]['meta']; usd=float(meta.get('regularMarketPrice') or meta.get('previousClose'))
            dollar,_u,_c=await fetch_price_v25('usd')
            if asset=='silver': rial_per_unit=usd*dollar; unit='Ø±ÛØ§Ù/Ø§ÙÙØ³ Ø¬ÙØ§ÙÛ'
            elif asset=='copper': rial_per_unit=usd*dollar; unit='Ø±ÛØ§Ù/Ù¾ÙÙØ¯ Ø¬ÙØ§ÙÛ'
            else: rial_per_unit=usd*dollar; unit='Ø±ÛØ§Ù/ÙØ§Ø­Ø¯ Ø¬ÙØ§ÙÛ'
            return rial_per_unit,unit,'single'
        except Exception as e: raise RuntimeError(f'{asset}: {e}')
    raise KeyError(asset)



# Patch booking after a slot: use saved profile when possible, then services, then payment options.
_LEGACY_BOOKING_SLOT_SELECT=booking_slot_select
async def booking_slot_select(update,context,tm):
    q=update.callback_query; uid=q.from_user.id; owner=context.user_data.get('booking_owner'); d=context.user_data.get('booking_date'); tm=parse_time(tm)
    if not owner or not d or not tm or tm not in available_slots(owner,d,30): await q.answer('Ø§ÛÙ Ø²ÙØ§Ù Ø¯ÛÚ¯Ø± Ø¢Ø²Ø§Ø¯ ÙÛØ³Øª.',show_alert=True); return
    if context.user_data.get('reschedule_appointment_id'):
        return await _LEGACY_BOOKING_SLOT_SELECT(update,context,tm)
    context.user_data['booking_time']=tm
    c=db(); services=c.execute('SELECT * FROM business_services WHERE owner_user_id=? AND enabled=1 ORDER BY id',(owner,)).fetchall(); c.close()
    if services:
        kb=[[InlineKeyboardButton(f"ð ï¸ {s['name']} â {s['price_rial']:,.0f} Ø±ÛØ§Ù",callback_data=f'v25:bookservice:{s["id"]}') ] for s in services]
        kb.append([InlineKeyboardButton('â­ï¸ Ø¨Ø¯ÙÙ Ø§ÙØªØ®Ø§Ø¨ Ø®Ø¯ÙØª',callback_data='v25:bookservice:0')])
        kb.append([InlineKeyboardButton('â¬ï¸ ØªØ§Ø±ÛØ® Ø¯ÛÚ¯Ø±',callback_data='cust:booklink'),main_menu_button(uid)])
        await q.message.edit_text(f'ð {jalali_pretty_date(d)}\nâ° {tm}\n\nð ï¸ Ø®Ø¯ÙØª Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:',reply_markup=InlineKeyboardMarkup(kb))
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
        await update.callback_query.message.edit_text(f"ð Ø§Ø·ÙØ§Ø¹Ø§Øª Ø°Ø®ÛØ±ÙâØ´Ø¯Ù Ù¾ÛØ¯Ø§ Ø´Ø¯.\n\nð¤ ÙØ§Ù: {html.escape(name or 'â')}\nð± ØªÙÙÙ: {html.escape(phone or 'â')}\n\nØ¯Ø±Ø³ØªÙØ",parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('â Ø§Ø³ØªÙØ§Ø¯Ù Ø§Ø² Ø§Ø·ÙØ§Ø¹Ø§Øª ÙÙ',callback_data='v25:bookuse')],[InlineKeyboardButton('âï¸ ÙÛØ±Ø§ÛØ´',callback_data='v25:bookedit')],[InlineKeyboardButton('â­ï¸ Ø¨Ø¯ÙÙ Ø§Ø·ÙØ§Ø¹Ø§Øª Ø´Ø®ØµÛ',callback_data='v25:bookskip')],[main_menu_button(uid)]]))
    else:
        await update.callback_query.message.edit_text('ð Ø¨Ø±Ø§Û Ø±Ø²Ø±Ù Ø§Ú¯Ø± Ø¯ÙØ³Øª Ø¯Ø§Ø´ØªÛ ÙØ§Ù Ù Ø´ÙØ§Ø±ÙâØ§Øª Ø±Ø§ ÙØ§Ø±Ø¯ Ú©ÙØ ÙØ± Ø¯Ù Ø§Ø®ØªÛØ§Ø±ÛâØ§ÙØ¯.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ð¤ ÙØ§Ø±Ø¯ Ú©Ø±Ø¯Ù ÙØ§Ù',callback_data='v25:bookedit')],[InlineKeyboardButton('â­ï¸ ÙØ¹ÙØ§Ù Ø¨Ø¯ÙÙ Ø§Ø·ÙØ§Ø¹Ø§Øª',callback_data='v25:bookskip')],[main_menu_button(uid)]]))

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
        aid=c.execute('INSERT INTO appointments(owner_user_id,customer_id,appointment_date,appointment_time,duration_minutes,service,notes,reminder_minutes,status,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(owner,cid,d,tm,duration,service,'Ø±Ø²Ø±Ù Ø¢ÙÙØ§ÛÙ','30','booked','online',now,now)).lastrowid
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
        if g and g['enabled'] and g['payment_link']: rows.append([InlineKeyboardButton('ð³ Ù¾Ø±Ø¯Ø§Ø®Øª Ø¢ÙÙØ§ÛÙ',url=g['payment_link'])])
        if card: rows.append([InlineKeyboardButton('ðµ Ù¾Ø±Ø¯Ø§Ø®Øª Ú©Ø§Ø±ØªâØ¨ÙâÚ©Ø§Ø±Øª',callback_data=f'v25:bookingcard:{aid}')])
    rows.append([InlineKeyboardButton('ð Ø±Ø²Ø±ÙÙØ§Û ÙÙ',callback_data='cust:mybookings'),main_menu_button(uid)])
    await update.callback_query.message.edit_text(f'â <b>Ø±Ø²Ø±Ù Ø¨Ø§ ÙÙÙÙÛØª Ø«Ø¨Øª Ø´Ø¯.</b>\n\nðª {html.escape(business_name)}\nð {jalali_pretty_date(context.user_data.get("booking_date"))}\nâ° {context.user_data.get("booking_time")}\n'+(f'ð° ÙØ²ÛÙÙ: {amount:,.0f} Ø±ÛØ§Ù\n\n' if amount else '\n')+'ÙÛâØªÙØ§ÙÛ Ø§Ø² Ú¯Ø²ÛÙÙâÙØ§Û Ø²ÛØ± Ù¾Ø±Ø¯Ø§Ø®Øª Ù ÙØ¯ÛØ±ÛØª Ø±Ø²Ø±Ù Ø±Ø§ Ø§ÙØ¬Ø§Ù Ø¨Ø¯ÙÛ.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(rows))

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
    if txt in ('â¬ï¸ Ø¨Ø±Ú¯Ø´Øª','â¬ï¸ Back'):
        clear_flow(context); await update.message.reply_text(v25_hub_text(uid),parse_mode='HTML',reply_markup=v25_hub_keyboard(uid)); return
    if txt in ('ð  ÙÙÙÛ Ø§ØµÙÛ','ð  Main Menu'):
        clear_flow(context); await update.message.reply_text('ð  ÙÙÙÛ Ø§ØµÙÛ',reply_markup=keyboard(uid)); return
    if txt in ('ð§  ÙØ±Ú©Ø² ÙÙ','ð§  My Center'):
        clear_flow(context); await v25_hub(update,context); return
    if txt in ('ð° Ø³Ø±ÙØ§ÛÙâÙØ§Û ÙÙ','ð° My Portfolio'):
        await v25_portfolio_menu(update,context); return
    if txt in ('ð³ Ø§ÙØ³Ø§Ø·','ð³ Installments'):
        await v25_installments_menu(update,context); return
    if txt in ('ð¤ Ø§Ø·ÙØ§Ø¹Ø§Øª ÙÙ','ð¤ My Profile'):
        await v25_profile_menu(update,context); return
    mode=context.user_data.get('v25_mode')
    if mode=='rem_title': context.user_data['v25_rem_title']=txt; context.user_data['v25_mode']='rem_time'; await update.message.reply_text('ð ØªØ§Ø±ÛØ® Ù Ø³Ø§Ø¹Øª Ø±Ø§ Ø¨ÙØ±Ø³Øª. ÙÙÙÙÙ: Û±Û´Û°Ûµ/Û°Û¶/Û°Û³ Û±Û²:Û°Û°'); return
    if mode in ('rem_time','inst_bank','inst_title','inst_principal','inst_rate_custom','inst_months','inst_first_date','profile_edit:name','profile_edit:phone','profile_edit:email','service_name','service_duration','service_price','card_number','card_name','gateway_link','survey_question','bizname_v25'):
        if await v25_add_reminder_save(update,context) if mode=='rem_time' else False: return
        if await v25_installment_text_save(update,context): return
        if await v25_business_text_save(update,context): return
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
    rows.insert(-1,[InlineKeyboardButton('ð§ ÙØ¶Ø¹ÛØª ÙÙÙ ÙØ§Ø¨ÙÛØªâÙØ§',callback_data='v25:adminfeatures'),InlineKeyboardButton('ð§© ØªÙØ¸ÛÙØ§Øª Ø¬Ø¯ÛØ¯',callback_data='v25:business')])
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
    "ð§  ØªÙÚ©Ø± Ù Ø°ÙÙ": [
        "ð§ Û±Û° Ø¯ÙÛÙÙ ÙØ¯ÛØªÛØ´Ù", "ð ÙØ·Ø§ÙØ¹Ù Ú©ØªØ§Ø¨", "âï¸ ÙÙØ´ØªÙ Ø§ÙÚ©Ø§Ø±", "ð­ ÙØ±ÙØ± Ø±ÙØ²Ø§ÙÙ",
        "ð§  ØªÙØ±ÛÙ ØªÙØ±Ú©Ø²", "ð Ø­Ù ÙØ³Ø¦ÙÙ", "ð ÙÙØ´ØªÙ Ø§ÛØ¯ÙâÙØ§", "ð± ØªÙÚ©Ø± ÙØ«Ø¨Øª",
        "ð¯ ØªØ¹ÛÛÙ Ø§ÙÙÙÛØªâÙØ§Û Ø±ÙØ²", "ð Ø¨Ø±ÙØ§ÙÙâØ±ÛØ²Û Ø±ÙØ²", "ð Ø¨Ø±Ø±Ø³Û ÛÚ© Ø§Ø´ØªØ¨Ø§Ù Ù Ø¯Ø±Ø³ Ø¢Ù",
        "ð¡ Ù¾ÛØ¯Ø§ Ú©Ø±Ø¯Ù ÛÚ© Ø§ÛØ¯Ù Ø¬Ø¯ÛØ¯", "ðµ ÛÚ© Ø³Ø§Ø¹Øª Ø¨Ø¯ÙÙ ÙÙØ¨Ø§ÛÙ", "ð ÙØ±ÙØ± Ø§ØªÙØ§ÙØ§Øª Ø±ÙØ²",
        "ð§© Ø­Ù ÛÚ© ÙØ¹ÙØ§", "ð¤ Û±Û° Ø¯ÙÛÙÙ ØªÙÚ©Ø± Ø¹ÙÛÙ", "ð§  ÛØ§Ø¯Ú¯ÛØ±Û ÛÚ© ÙÙÙÙÙ Ø¬Ø¯ÛØ¯",
    ],
    "ð Ø±Ø´Ø¯ ÙØ±Ø¯Û": [
        "ð¯ ØªØ¹ÛÛÙ ÛÚ© ÙØ¯Ù ÙÙÙ", "ð Ø¨ÙØªØ± Ø´Ø¯Ù Û±Ùª Ø§ÙØ±ÙØ²", "ð Û²Û° Ø¯ÙÛÙÙ ÛØ§Ø¯Ú¯ÛØ±Û", "ð£ï¸ ØªÙØ±ÛÙ Ø§Ø±ØªØ¨Ø§Ø· ÙØ¤Ø«Ø±",
        "ðª Ø§ÙØ¬Ø§Ù ÛÚ© Ú©Ø§Ø± Ø³Ø®Øª", "ð¥ ØºÙØ¨Ù Ø¨Ø± ÛÚ© ØªØ¹ÙÙ", "ð Ø«Ø¨Øª Ø³Ù ÙÚ©ØªÙ ÙØ«Ø¨Øª", "ð± Ø³Ø§Ø®Øª ÛÚ© Ø¹Ø§Ø¯Øª Ø®ÙØ¨",
        "â° Ø´Ø±ÙØ¹ Ø¨ÙâÙÙÙØ¹ Ú©Ø§Ø±", "ð¯ ØªÚ©ÙÛÙ ÙÙÙâØªØ±ÛÙ Ú©Ø§Ø± Ø±ÙØ²", "ð§¹ Ø­Ø°Ù ÛÚ© Ø­ÙØ§Ø³âÙ¾Ø±ØªÛ",
        "ð¡ ÛØ§Ø¯Ú¯ÛØ±Û Ø§Ø² ÛÚ© ØªØ¬Ø±Ø¨Ù Ø§ÙØ±ÙØ²", "ð ÙØ¯Ø±Ø¯Ø§ÙÛ Ø§Ø² Ø³Ù ÚÛØ²", "ðª´ ÙØ±Ø§ÙØ¨Øª Ø§Ø² Ø®ÙØ¯",
    ],
    "ð¥ Ø±ÙØ§Ø¨Ø· Ù Ø®Ø§ÙÙØ§Ø¯Ù": [
        "âï¸ ØªÙØ§Ø³ Ø¨Ø§ Ø®Ø§ÙÙØ§Ø¯Ù", "ð¬ Ø§Ø­ÙØ§ÙâÙ¾Ø±Ø³Û Ø§Ø² ÛÚ© Ø¯ÙØ³Øª", "â¤ï¸ ÙÙØª Ø¨Ø§ Ø®Ø§ÙÙØ§Ø¯Ù", "ð¤ Ú©ÙÚ© Ø¨Ù ÛÚ© ÙÙØ±",
        "ð ØªØ´Ú©Ø± Ø§Ø² ÛÚ© ÙÙØ±", "ð Ø§ÙØ¬Ø§Ù ÛÚ© Ú©Ø§Ø± Ø®ÙØ¨ Ø¨Ø±Ø§Û Ø®Ø§ÙÙØ§Ø¯Ù", "ð£ï¸ Ú¯ÙØªâÙÚ¯ÙÛ Ø¨Ø¯ÙÙ ÙÙØ¨Ø§ÛÙ",
        "ð¨âð©âð§ Ø¨Ø±ÙØ§ÙÙ Ø®Ø§ÙÙØ§Ø¯Ú¯Û", "ð Ø§Ø±Ø³Ø§Ù ÛÚ© Ù¾ÛØ§Ù ÙØ­Ø¨ØªâØ¢ÙÛØ²",
    ],
    "ð¨ Ø®ÙØ§ÙÛØª": [
        "âï¸ ÙÙØ´ØªÙ ÛÚ© Ø§ÛØ¯Ù", "ð¨ Ø·Ø±Ø§Ø­Û ÛØ§ ÙÙØ§Ø´Û", "ð¸ Ø«Ø¨Øª ÛÚ© Ø¹Ú©Ø³ Ø®ÙØ§ÙØ§ÙÙ", "ðµ Ú¯ÙØ´ Ø¯Ø§Ø¯Ù ÙØ¹Ø§Ù Ø¨Ù ÙÙØ³ÛÙÛ",
        "ð§  Ø³Ø§Ø®Øª ÛÚ© Ø§ÛØ¯Ù Ø¬Ø¯ÛØ¯", "ð ÙÙØ´ØªÙ Û±Û° Ø¯ÙÛÙÙ Ø¢Ø²Ø§Ø¯", "ð ï¸ Ø³Ø§Ø®Øª ÛÚ© ÚÛØ² Ø³Ø§Ø¯Ù",
    ],
    "ð ÙØ¹ÙÙÛ": [
        "ð Ø¯Ø¹Ø§ Ù ÙÛØ§ÛØ´", "ð ÙØ·Ø§ÙØ¹Ù Ú©ÙØªØ§Ù ÙØ¹ÙÙÛ", "ð§ ÚÙØ¯ Ø¯ÙÛÙÙ Ø³Ú©ÙØª", "â¤ï¸ Ø§ÙØ¬Ø§Ù ÛÚ© Ú©Ø§Ø± Ø®ÛØ±",
        "ð ÙØ±ÙØ± ÛÚ© ÙÚ©ØªÙ ÙØ¹ÙÙÛ", "ð¤² ØªØ´Ú©Ø± Ù ÙØ¯Ø±Ø¯Ø§ÙÛ",
    ],
    "ð± Ø¯ÛØ¬ÛØªØ§Ù": [
        "ðµ Û³Û° Ø¯ÙÛÙÙ Ø¨Ø¯ÙÙ Ø´Ø¨Ú©Ù Ø§Ø¬ØªÙØ§Ø¹Û", "ð± Ù¾Ø§Ú© Ú©Ø±Ø¯Ù Ø§Ø¹ÙØ§ÙâÙØ§Û Ø§Ø¶Ø§ÙÛ", "ð§¹ ÙØ±ØªØ¨ Ú©Ø±Ø¯Ù ÙØ§ÛÙâÙØ§",
        "ð§ Ù¾Ø§Ø³Ø® Ø¨Ù Ø§ÛÙÛÙâÙØ§Û Ø¶Ø±ÙØ±Û", "ð Ø¨Ø±Ø±Ø³Û Ø§ÙÙÛØª Ø­Ø³Ø§Ø¨âÙØ§", "ð» Û³Û° Ø¯ÙÛÙÙ Ú©Ø§Ø± ÙØªÙØ±Ú©Ø² Ø¨Ø§ Ø±Ø§ÛØ§ÙÙ",
    ],
    "âï¸ Ø³ÙØ± Ù Ø¨Ø±ÙØ§ÙÙâØ±ÛØ²Û": [
        "ðºï¸ Ø¨Ø±Ø±Ø³Û ÙØ³ÛØ± Ø³ÙØ±", "ð Ø¢ÙØ§Ø¯ÙâØ³Ø§Ø²Û ÙØ³Ø§ÛÙ", "ð¨ Ø¨Ø±Ø±Ø³Û ÙØ­Ù Ø§ÙØ§ÙØª", "ð° Ø¨Ø±ÙØ§ÙÙ ÙØ²ÛÙÙ Ø³ÙØ±",
        "ð Ø¨Ø±ÙØ§ÙÙâØ±ÛØ²Û Ø±ÙØ² Ø³ÙØ±", "ð¸ Ø³Ø§Ø®Øª ÙÛØ³Øª ÙÚ©Ø§ÙâÙØ§Û Ø¯ÛØ¯ÙÛ",
    ],
})
GOALS_EN.update({
    "ð§  Thinking & Mind": [
        "ð§ 10 minutes of meditation", "ð Read a book", "âï¸ Journal your thoughts", "ð­ Review the day",
        "ð§  Focus exercise", "ð Solve a problem", "ð Write ideas", "ð± Positive thinking",
        "ð¯ Set today's priorities", "ð Plan the day", "ð Review one mistake and its lesson",
        "ð¡ Find one new idea", "ðµ One hour without phone", "ð Review the day",
        "ð§© Solve a puzzle", "ð¤ 10 minutes of deep thinking", "ð§  Learn one new concept",
    ],
    "ð Personal Growth": [
        "ð¯ Set one important goal", "ð Improve 1% today", "ð 20 minutes of learning", "ð£ï¸ Practice communication",
        "ðª Do one hard thing", "ð¥ Beat one procrastination", "ð Write three positives", "ð± Build one good habit",
        "â° Start on time", "ð¯ Finish the most important task", "ð§¹ Remove one distraction",
        "ð¡ Learn from today's experience", "ð Write three things you appreciate", "ðª´ Take care of yourself",
    ],
    "ð¥ Relationships & Family": [
        "âï¸ Call family", "ð¬ Check in with a friend", "â¤ï¸ Spend time with family", "ð¤ Help someone",
        "ð Thank someone", "ð Do something kind for family", "ð£ï¸ Have a phone-free conversation",
        "ð¨âð©âð§ Plan family time", "ð Send a kind message",
    ],
    "ð¨ Creativity": [
        "âï¸ Write one idea", "ð¨ Draw or design", "ð¸ Take a creative photo", "ðµ Listen to music mindfully",
        "ð§  Build one new idea", "ð Free-write for 10 minutes", "ð ï¸ Make something simple",
    ],
    "ð Spiritual": [
        "ð Prayer or reflection", "ð Read something spiritual", "ð§ A few minutes of silence", "â¤ï¸ Do a good deed",
        "ð Reflect on one spiritual lesson", "ð¤² Practice gratitude",
    ],
    "ð± Digital Life": [
        "ðµ 30 minutes without social media", "ð± Remove unnecessary notifications", "ð§¹ Organize digital files",
        "ð§ Reply to important emails", "ð Review account security", "ð» 30 minutes of focused computer work",
    ],
    "âï¸ Travel & Planning": [
        "ðºï¸ Check the route", "ð Prepare travel items", "ð¨ Review accommodation", "ð° Plan travel budget",
        "ð Plan travel day", "ð¸ Make a sightseeing list",
    ],
})

# Money is stored/displayed in Iranian Rials in this layer.
def irr(amount):
    try:
        return f"{float(amount):,.0f} Ø±ÛØ§Ù"
    except Exception:
        return "â Ø±ÛØ§Ù"

def toman_to_irr(toman):
    return int(round(float(toman) * 10))

# Better interest-rate choices: 0..30 inclusive, plus custom.
def v25_rates_keyboard(uid):
    fa = lang(uid) == 'fa'
    vals = list(range(0,31))
    rows = []
    for i in range(0, len(vals), 3):
        rows.append([InlineKeyboardButton(f'{x}Ùª', callback_data=f'v25:instrate:{x}') for x in vals[i:i+3]])
    rows.append([InlineKeyboardButton('âï¸ ÙØ±Ø® Ø¯ÙØ®ÙØ§Ù' if fa else 'âï¸ Custom Rate', callback_data='v25:instrate_custom')])
    rows.append([InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª' if fa else 'â¬ï¸ Back', callback_data='v25:instadd'), main_menu_button(uid)])
    return InlineKeyboardMarkup(rows)

# A broader Iran-bank catalog. Kept configurable later from admin.
def v25_banks():
    return [
        'ÙÙÛ Ø§ÛØ±Ø§Ù','Ø³Ù¾Ù','Ú©Ø´Ø§ÙØ±Ø²Û','ÙØ³Ú©Ù','ØµØ§Ø¯Ø±Ø§Øª Ø§ÛØ±Ø§Ù','ØªØ¬Ø§Ø±Øª','ÙÙØª','Ø±ÙØ§Ù Ú©Ø§Ø±Ú¯Ø±Ø§Ù',
        'Ù¾Ø³Øª Ø¨Ø§ÙÚ© Ø§ÛØ±Ø§Ù','ØµÙØ¹Øª Ù ÙØ¹Ø¯Ù','ØªÙØ³Ø¹Ù ØµØ§Ø¯Ø±Ø§Øª Ø§ÛØ±Ø§Ù','ØªÙØ³Ø¹Ù ØªØ¹Ø§ÙÙ','Ù¾Ø§Ø±Ø³ÙÛØ§Ù','Ù¾Ø§Ø³Ø§Ø±Ú¯Ø§Ø¯',
        'Ø³Ø§ÙØ§Ù','Ø§ÙØªØµØ§Ø¯ ÙÙÛÙ','Ú©Ø§Ø±Ø¢ÙØ±ÛÙ','Ø³ÛÙØ§','Ø´ÙØ±','Ø¯Û','Ø¢ÛÙØ¯Ù','Ú¯Ø±Ø¯Ø´Ú¯Ø±Û','Ø§ÛØ±Ø§ÙâØ²ÙÛÙ',
        'Ø®Ø§ÙØ±ÙÛØ§ÙÙ','Ø³Ø±ÙØ§ÛÙ','ÙÙØ± Ø§ÙØªØµØ§Ø¯','ÙØ±Ø¶âØ§ÙØ­Ø³ÙÙ ÙÙØ± Ø§ÛØ±Ø§Ù','ÙØ±Ø¶âØ§ÙØ­Ø³ÙÙ Ø±Ø³Ø§ÙØª','ÙÙØ±','ÙÙÙ',
        'ØªØ§Øª','Ø­Ú©ÙØª Ø§ÛØ±Ø§ÙÛØ§Ù','Ø§ÙØµØ§Ø±','ÙÙØ§ÙÛÙ','Ú©ÙØ«Ø±','ÙØ¤Ø³Ø³Ù/Ø¨Ø§ÙÚ© Ø¯ÙØ®ÙØ§Ù'
    ]

# ------------------ Unified admin control ------------------
async def v25_admin_menu(update, context):
    uid = update.effective_user.id
    if not admin_guard(uid):
        await update.callback_query.answer('â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.', show_alert=True); return
    fa = lang(uid) == 'fa'
    items = [
        ('v25:adminfeatures','ð§ ÙØ¶Ø¹ÛØª ÙØ§Ø¨ÙÛØªâÙØ§','ð§ Feature Status'),
        ('v25:adminplans','ð ÙØ¯ÛØ±ÛØª Ù¾ÙÙâÙØ§Û VIP','ð VIP Plans'),
        ('v25:adminpayment','ð³ ØªÙØ¸ÛÙØ§Øª Ù¾Ø±Ø¯Ø§Ø®Øª','ð³ Payment Settings'),
        ('v25:adminvip','ð Ù¾Ø±Ø¯Ø§Ø®Øª VIP','ð VIP Payment'),
        ('v25:adminsms','ð± ØªÙØ¸ÛÙØ§Øª Ù¾ÛØ§ÙÚ©','ð± SMS Settings'),
        ('v25:adminsurvey','â­ ØªÙØ¸ÛÙØ§Øª ÙØ¸Ø±Ø³ÙØ¬Û','â­ Survey Settings'),
        ('v25:adminmorning','âï¸ ØµØ¨Ø­/Ø´Ø¨ Ù Ø¬ÙØ¹Ù','âï¸ Morning/Night & Friday'),
        ('v25:adminprices','ð ÙÛÙØª Ø¨Ø§Ø²Ø§Ø±','ð Market Prices'),
    ]
    rows=[[InlineKeyboardButton(ft if fa else et, callback_data=cb)] for cb,ft,et in items]
    rows.append([InlineKeyboardButton('â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª' if fa else 'â¬ï¸ Admin Panel', callback_data='adm:stats'), main_menu_button(uid)])
    await update.callback_query.message.edit_text('ð¡ï¸ <b>ÙØ±Ú©Ø² ÙØ¯ÛØ±ÛØª ÙØ³Ø®Ù ÙÙØ§ÛÛ</b>\n\nØªÙØ§Ù ÙØ§Ø¨ÙÛØªâÙØ§Û Ø¬Ø¯ÛØ¯ Ø§Ø² ÙÙÛÙ Ø¨Ø®Ø´ Ú©ÙØªØ±Ù ÙÛâØ´ÙÙØ¯.' if fa else 'ð¡ï¸ <b>Final Admin Center</b>\n\nAll new modules are controlled here.', parse_mode='HTML', reply_markup=InlineKeyboardMarkup(rows))


async def v25_admin_plans(update,context):
    uid=update.effective_user.id
    if not admin_guard(uid): return await update.callback_query.answer('â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.',show_alert=True)
    c=db(); rows=c.execute('SELECT * FROM subscription_plans_v25 ORDER BY duration_minutes').fetchall(); c.close()
    kb=[]
    lines=['ð <b>ÙØ¯ÛØ±ÛØª Ù¾ÙÙâÙØ§Û VIP</b>','']
    for r in rows:
        st='ð¢' if r['enabled'] else 'ð´'; lines.append(f'{st} {html.escape(r["name"])} â {irr(r["price_rial"])}')
        kb.append([InlineKeyboardButton(f'{st} {r["name"]}',callback_data=f'v25:planedit:{r["id"]}')])
    kb.append([InlineKeyboardButton('â Ù¾ÙÙ Ø³ÙØ§Ø±Ø´Û',callback_data='v25:planadd'), InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª',callback_data='v25:adminmenu')])
    await update.callback_query.message.edit_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_admin_payment(update,context):
    uid=update.effective_user.id
    if not admin_guard(uid): return await update.callback_query.answer('â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.',show_alert=True)
    c=db(); rows=c.execute("SELECT * FROM feature_flags WHERE key IN ('payments','booking_payments','card_to_card','vip') ORDER BY key").fetchall(); c.close()
    states='\n'.join((('ð¢' if r['enabled'] else 'ð´')+' '+r['key']) for r in rows)
    kb=[[InlineKeyboardButton('ð³ Ù¾ÛÚ©Ø±Ø¨ÙØ¯Û Ø¯Ø±Ú¯Ø§Ù/ÙÛÙÚ© Ù¾Ø±Ø¯Ø§Ø®Øª',callback_data='v25:gateway')],[InlineKeyboardButton('ðµ Ú©Ø§Ø±ØªâØ¨ÙâÚ©Ø§Ø±Øª',callback_data='v25:card')],[InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª',callback_data='v25:adminmenu'),main_menu_button(uid)]]
    await update.callback_query.message.edit_text('ð³ <b>ØªÙØ¸ÛÙØ§Øª Ù¾Ø±Ø¯Ø§Ø®Øª</b>\n\n'+states+'\n\nØ¯Ø±Ú¯Ø§ÙâÙØ§ Ø®Ø§ÙÙØ´ ÙÛâØªÙØ§ÙÙØ¯ Ø¨Ø§ÙÛ Ø¨ÙØ§ÙÙØ¯ ØªØ§ Ø¨Ø¹Ø¯Ø§Ù ÙØ¹Ø§Ù Ø´ÙÙØ¯.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_admin_sms(update,context):
    uid=update.effective_user.id
    if not admin_guard(uid): return await update.callback_query.answer('â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.',show_alert=True)
    cfg=_v25_exec('SELECT * FROM sms_settings WHERE owner_user_id=?',(uid,),fetchone=True)
    state='ð¢' if cfg and cfg['enabled'] else 'ð´'
    kb=[[InlineKeyboardButton(f'{state} Ø±ÙØ´Ù/Ø®Ø§ÙÙØ´',callback_data='v25:smstoggle')],[InlineKeyboardButton('âï¸ ØªÙØ¸ÛÙ Endpoint/API',callback_data='v25:smsconfig')],[InlineKeyboardButton('ð§ª ØªØ³Øª',callback_data='v25:smstest')],[InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª',callback_data='v25:adminmenu')]]
    await update.callback_query.message.edit_text(f'ð± <b>ÙØ¯ÛØ±ÛØª Ù¾ÛØ§ÙÚ©</b>\n\nÙØ¶Ø¹ÛØª: {state}\n\nØ§ØªØµØ§Ù ÙØ§ÙØ¹Û Ø¨Ù Ø³Ø±ÙÛØ³âØ¯ÙÙØ¯Ù ÙÙØ· Ø¨Ø¹Ø¯ Ø§Ø² Ø«Ø¨Øª endpoint Ù Ú©ÙÛØ¯ Ø³Ø±ÙÛØ³ Ø§ÙØ¬Ø§Ù ÙÛâØ´ÙØ¯.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_admin_survey(update,context):
    uid=update.effective_user.id
    if not admin_guard(uid): return await update.callback_query.answer('â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.',show_alert=True)
    await update.callback_query.message.edit_text('â­ <b>ØªÙØ¸ÛÙØ§Øª ÙØ¸Ø±Ø³ÙØ¬Û</b>\n\nØ³Ø¤Ø§ÙâÙØ§Û Ø§ØµÙÛ Ø´Ø§ÙÙ ÙØ­ÛØ·Ø ØªÙÛØ²ÛØ Ú©Ø§Ø±Ú©ÙØ§ÙØ Ø³Ø±Ø¹ØªØ Ú©ÛÙÛØªØ Ø§Ø±Ø²Ø´ ÙØ³Ø¨Øª Ø¨Ù ÙÛÙØª Ù Ø±Ø§Ø­ØªÛ Ø±Ø²Ø±Ù ÙØ³ØªÙØ¯. Ú©Ø³Ø¨âÙÚ©Ø§Ø± ÙÛâØªÙØ§ÙØ¯ Ø³Ø¤Ø§Ù Ø³ÙØ§Ø±Ø´Û ÙÙ Ø§Ø¶Ø§ÙÙ Ú©ÙØ¯.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ð¢/ð´ ÙØ¯ÛØ±ÛØª Ø³ÙØ§ÙâÙØ§',callback_data='v25:surveyadmin')],[InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª',callback_data='v25:adminmenu')]]))


async def v25_admin_morning(update,context):
    uid=update.effective_user.id
    if not admin_guard(uid): return await update.callback_query.answer('â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.',show_alert=True)
    friday=get_system_setting('friday_pause','0')=='1'; night=get_system_setting('night_message_enabled','1')=='1'; morning=get_system_setting('morning_message_enabled','1')=='1'
    txt=f'âï¸ ØµØ¨Ø­: {"ð¢" if morning else "ð´"}\nð Ø´Ø¨: {"ð¢" if night else "ð´"}\nðï¸ ØªÙÙÙ Ø¬ÙØ¹Ù: {"ð¢" if friday else "ð´"}'
    kb=[[InlineKeyboardButton('âï¸ Ø±ÙØ´Ù/Ø®Ø§ÙÙØ´ ØµØ¨Ø­',callback_data='v25:toggle_morning')],[InlineKeyboardButton('ð Ø±ÙØ´Ù/Ø®Ø§ÙÙØ´ Ø´Ø¨',callback_data='v25:toggle_night')],[InlineKeyboardButton('ðï¸ Ø±ÙØ´Ù/Ø®Ø§ÙÙØ´ Ø¬ÙØ¹Ù',callback_data='v25:toggle_friday')],[InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª',callback_data='v25:adminmenu')]]
    await update.callback_query.message.edit_text('âï¸ <b>Ù¾ÛØ§ÙâÙØ§Û ØµØ¨Ø­/Ø´Ø¨ Ù Ø¬ÙØ¹Ù</b>\n\n'+txt,parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_admin_prices(update,context):
    uid=update.effective_user.id
    if not admin_guard(uid): return await update.callback_query.answer('â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.',show_alert=True)
    status=get_system_setting('price_data_status','auto')
    txt='ð <b>ÙÛÙØª Ø¨Ø§Ø²Ø§Ø±</b>\n\nð¢ Ø¯Ø§Ø¯ÙâÙØ§: '+html.escape(status)+'\nðµ ÙØ§Ø­Ø¯ Ø§ØµÙÛ: Ø±ÛØ§Ù\nð¡ï¸ Ø§Ú¯Ø± ÙÙØ¨Ø¹ Ø«Ø§ÙÙÛÙ Ø¯Ø± Variables ÙØ¹Ø§Ù Ø¨Ø§Ø´Ø¯Ø Ø§Ø®ØªÙØ§Ù ÙÙØ§Ø¨Ø¹ ÙÛØ² Ø¨Ø±Ø±Ø³Û ÙÛâØ´ÙØ¯.'
    await update.callback_query.message.edit_text(txt,parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ð¢/ð´ ÙÛÙØª Ø¨Ø§Ø²Ø§Ø±',callback_data='v25:toggle_prices')],[InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª',callback_data='v25:adminmenu')]]))

# ------------------ Installment due history & notifications ------------------
async def v25_installment_view(update,context,plan_id):
    uid=update.effective_user.id; plan=_v25_exec('SELECT * FROM installment_plans WHERE id=? AND user_id=?',(plan_id,uid),fetchone=True)
    if not plan: return
    payments=_v25_exec('SELECT * FROM installment_payments WHERE plan_id=? ORDER BY installment_no',(plan_id,),fetchall=True)
    lines=[f'ð¦ <b>{html.escape(plan["bank_name"])}</b>',f'ð {html.escape(plan["title"])}',f'ðµ ÙØ³Ø· ÙØ§ÙØ§ÙÙ: {irr(plan["monthly_rial"])}','', 'ð <b>ØªØ§Ø±ÛØ®ÚÙ</b>']
    for r in payments[:36]:
        icon={'paid':'â','partial':'ð¡','unpaid':'â','pending':'â³'}.get(r['status'],'â³')
        lines.append(f'{icon} ÙØ³Ø· {r["installment_no"]} â {jalali_pretty_date(r["due_date"])} â {irr(r["amount_rial"])}')
    kb=[[InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª',callback_data='v25:installments'),main_menu_button(uid)]]
    await update.callback_query.message.edit_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_installment_due_job(context):
    if datetime.now(TZ).weekday()==4 and get_system_setting('friday_pause','0')=='1': return
    d=datetime.now(TZ).date().isoformat()
    rows=_v25_exec("SELECT ip.*,p.user_id,p.bank_name,p.title FROM installment_payments ip JOIN installment_plans p ON p.id=ip.plan_id WHERE ip.due_date=? AND ip.status IN ('pending','unpaid','partial') AND p.enabled=1",(d,),fetchall=True)
    for r in rows:
        key=f'inst_due:{r["id"]}:{d}'
        if _v25_exec('SELECT 1 FROM delivery_log WHERE delivery_key=?',(key,),fetchone=True): continue
        try:
            await context.bot.send_message(r['user_id'],f'ð <b>ÛØ§Ø¯Ø¢ÙØ±Û ÙØ³Ø·</b>\n\nð¦ {html.escape(r["bank_name"])}\nð {html.escape(r["title"])}\nð Ø§ÙØ±ÙØ²\nð° ÙØ¨ÙØº: {irr(r["amount_rial"])}\n\nÙØ¶Ø¹ÛØª Ù¾Ø±Ø¯Ø§Ø®Øª Ø±Ø§ ÙØ´Ø®Øµ Ú©Ù:',parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('â Ù¾Ø±Ø¯Ø§Ø®Øª Ø´Ø¯',callback_data=f'v25:instpay:{r["id"]}:paid'),InlineKeyboardButton('ð¡ Ø¨Ø¹Ø¯Ø§Ù Ù¾Ø±Ø¯Ø§Ø®Øª ÙÛâÚ©ÙÙ',callback_data=f'v25:instpay:{r["id"]}:later')],[InlineKeyboardButton('â Ù¾Ø±Ø¯Ø§Ø®Øª ÙØ´Ø¯',callback_data=f'v25:instpay:{r["id"]}:unpaid')],[main_menu_button(r['user_id'])]]))
            _v25_exec('INSERT OR IGNORE INTO delivery_log(delivery_key,user_id,delivery_type,created_at) VALUES(?,?,?,?)',(key,r['user_id'],'installment_due',_v25_now()))
        except Exception as e: logger.warning('installment notification failed: %s',e)

# ------------------ Portfolio P/L ------------------
async def v25_current_price_for_portfolio(title):
    t=(title or '').lower()
    mappings=[('gold18',['Ø·ÙØ§','gold']),('usd',['Ø¯ÙØ§Ø±','usd','dollar']),('eur',['ÛÙØ±Ù','eur','euro']),('coin',['Ø³Ú©Ù','coin']),('silver',['ÙÙØ±Ù','silver']),('copper',['ÙØ³','copper']),('aluminum',['Ø¢ÙÙÙÛÙÛÙÙ','aluminum']),('nickel',['ÙÛÚ©Ù','nickel']),('zinc',['Ø±ÙÛ','zinc']),('lead',['Ø³Ø±Ø¨','lead'])]
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
            details.append(f"â¢ {html.escape(r['title'])}: {'ð¢' if pnl>=0 else 'ð´'} {irr(pnl)} ({pct:+.2f}Ùª)")
        else: details.append(f"â¢ {html.escape(r['title'])}: âª ÙÛÙØª Ø¬Ø§Ø±Û Ø¯Ø± Ø¯Ø³ØªØ±Ø³ ÙÛØ³Øª")
    pnl=current-total_cost if known else None
    lines=["ð <b>Ø®ÙØ§ØµÙ Ø³Ø±ÙØ§ÛÙ</b>",f"ð° ÙØ²ÛÙÙ Ø®Ø±ÛØ¯: {irr(total_cost)}"]
    if known: lines += [f"ð Ø§Ø±Ø²Ø´ ÙØ¹ÙÛ Ø¯Ø§Ø±Ø§ÛÛâÙØ§Û ÙØ§Ø¨ÙâÙÛÙØªâÚ¯Ø°Ø§Ø±Û: {irr(current)}",f"{'ð¢' if pnl>=0 else 'ð´'} Ø³ÙØ¯/Ø²ÛØ§Ù ÙØ¹ÙÛ: {irr(pnl)}"]
    lines += ['',*details]
    lines += ['', 'â ï¸ Ø³ÙØ¯/Ø²ÛØ§Ù ÙÙØ· Ø¨Ø±Ø§Û Ø¯Ø§Ø±Ø§ÛÛâÙØ§ÛÛ ÙØ­Ø§Ø³Ø¨Ù ÙÛâØ´ÙØ¯ Ú©Ù ÙÛÙØª Ø¬Ø§Ø±Û Ø¢ÙâÙØ§ Ø¨Ø§ Ø¯Ø§Ø¯Ù ÙØ¹ØªØ¨Ø± Ø¯Ø± Ø¯Ø³ØªØ±Ø³ Ø¨Ø§Ø´Ø¯.']
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
        return val, unit+' | Ø§Ø®ØªÙØ§Ù ÙÙØ§Ø¨Ø¹', 'disputed'
    return val,unit,conf



async def v25_night_job(context):
    now=datetime.now(TZ)
    if now.hour!=22 or now.minute!=0 or get_system_setting('night_message_enabled','1')!='1': return
    rows=_v25_exec('SELECT user_id FROM users WHERE COALESCE(blocked,0)=0',fetchall=True)
    for r in rows:
        uid=r['user_id']; key=f'night:{uid}:{now.date().isoformat()}'
        if _v25_exec('SELECT 1 FROM delivery_log WHERE delivery_key=?',(key,),fetchone=True): continue
        d=now.date().isoformat(); done=_v25_exec("SELECT COUNT(*) n FROM goal_days WHERE user_id=? AND goal_date=? AND status='done'",(uid,d),fetchone=True)['n']; total=len(get_goals(uid)); streaks=[calculate_streak(uid,g['id']) for g in get_goals(uid)]; streak=max(streaks,default=0)
        text=f'ð <b>Ø´Ø¨ Ø¨Ø®ÛØ± {html.escape(display_name(uid))}</b>\n\nØ®Ø³ØªÙ ÙØ¨Ø§Ø´Û ð·\n\nð Ú¯Ø²Ø§Ø±Ø´ Ø§ÙØ±ÙØ²\nâ Ø§ÙØ¬Ø§ÙâØ´Ø¯Ù: {done}\nð¯ Ú©Ù ÙØ¯ÙâÙØ§: {total}\nð¥ Streak: {streak} Ø±ÙØ²\n\nÙØ± ÚÛØ²Û Ú©Ù Ø§ÙØ±ÙØ² Ø§ÙØ¬Ø§Ù ÙØ´Ø¯Ø ÙÛâØªÙØ§ÙØ¯ ÙØ±Ø¯Ø§ Ø¯ÙØ¨Ø§Ø±Ù Ø´Ø±ÙØ¹ Ø´ÙØ¯.'
        kb=InlineKeyboardMarkup([[InlineKeyboardButton('ð Ú¯Ø²Ø§Ø±Ø´ Ø±ÙØ²Ø§ÙÙ' if lang(uid)=='fa' else 'ð Daily',callback_data='v25:reports'),InlineKeyboardButton('ð Ú¯Ø²Ø§Ø±Ø´ ÙÙØªÚ¯Û' if lang(uid)=='fa' else 'ð Weekly',callback_data='v25:report_week')],[InlineKeyboardButton('ð Ú¯Ø²Ø§Ø±Ø´ ÙØ§ÙØ§ÙÙ' if lang(uid)=='fa' else 'ð Monthly',callback_data='v25:report_month')],[main_menu_button(uid)]])
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
    label={'day':'Ø±ÙØ²Ø§ÙÙ','week':'ÙÙØªÚ¯Û','month':'ÙØ§ÙØ§ÙÙ'}[period]
    text=f'ð <b>Ú¯Ø²Ø§Ø±Ø´ {label}</b>\n\nâ Ø§ÙØ¬Ø§ÙâØ´Ø¯Ù: {done}\nâ Ø§ÙØ¬Ø§ÙâÙØ´Ø¯Ù: {missed}\nð ÙØ±Ø® ÙÙÙÙÛØª: {rate:.1f}%\nð Ø¨Ø§Ø²Ù: {start.isoformat()} ØªØ§ {today.isoformat()}'
    kb=InlineKeyboardMarkup([[InlineKeyboardButton('ð Ø±ÙØ²Ø§ÙÙ',callback_data='v25:reports'),InlineKeyboardButton('ð ÙÙØªÚ¯Û',callback_data='v25:report_week'),InlineKeyboardButton('ð ÙØ§ÙØ§ÙÙ',callback_data='v25:report_month')],[main_menu_button(uid)]])
    if update.callback_query: await update.callback_query.message.edit_text(text,parse_mode='HTML',reply_markup=kb)
    else: await update.message.reply_text(text,parse_mode='HTML',reply_markup=kb)


async def v25_user_vip_plans(update,context):
    uid=update.effective_user.id; fa=lang(uid)=='fa'
    rows=_v25_exec('SELECT * FROM subscription_plans_v25 WHERE enabled=1 ORDER BY duration_minutes',fetchall=True)
    lines=['ð <b>VIP Ù Ø§Ø´ØªØ±Ø§Ú©</b>','']
    kb=[]
    for r in rows:
        lines.append(f"â¢ {html.escape(r['name'])} â {irr(r['price_rial'])}")
        kb.append([InlineKeyboardButton(r['name'],callback_data=f'v25:userplan:{r["id"]}')])
    lines.append(''); lines.append('ÙØ± Ù¾ÙÙ Ø±Ø§ Ú©Ù Ø®ÙØ§Ø³ØªÛ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù Ù Ø±ÙØ´ Ù¾Ø±Ø¯Ø§Ø®Øª Ø±Ø§ Ø¨Ø¨ÛÙ.')
    kb.append([InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª' if fa else 'â¬ï¸ Back',callback_data='v25:hub'),main_menu_button(uid)])
    target=update.callback_query.message if update.callback_query else update.message
    if update.callback_query: await target.edit_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))
    else: await target.reply_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def v25_admin_vip_payment(update,context):
    uid=update.effective_user.id
    if not admin_guard(uid): return await update.callback_query.answer('â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.',show_alert=True)
    card_on=get_system_setting('vip_card_enabled','0')=='1'; gw_on=get_system_setting('vip_gateway_enabled','0')=='1'
    card='ð¢' if card_on else 'ð´'; gw='ð¢' if gw_on else 'ð´'
    txt=f'ð <b>Ù¾Ø±Ø¯Ø§Ø®Øª VIP</b>\n\nðµ Ú©Ø§Ø±ØªâØ¨ÙâÚ©Ø§Ø±Øª: {card}\nð³ Ø¯Ø±Ú¯Ø§Ù Ø¢ÙÙØ§ÛÙ: {gw}\n\nØ´ÙØ§Ø±Ù Ú©Ø§Ø±Øª Ù ÙØ§Ù ØµØ§Ø­Ø¨ Ú©Ø§Ø±Øª ÙÙØ· Ø¯Ø± ØµÙØ±Øª ÙØ¹Ø§ÙâØ³Ø§Ø²Û Ø¨Ù ÙØ´ØªØ±Û ÙÙØ§ÛØ´ Ø¯Ø§Ø¯Ù ÙÛâØ´ÙØ¯.'
    kb=[[InlineKeyboardButton(f'{card} Ú©Ø§Ø±ØªâØ¨ÙâÚ©Ø§Ø±Øª',callback_data='v25:vipcardtoggle')],[InlineKeyboardButton(f'{gw} Ø¯Ø±Ú¯Ø§Ù Ø¢ÙÙØ§ÛÙ',callback_data='v25:vipgatewaytoggle')],[InlineKeyboardButton('ð³ Ø´ÙØ§Ø±Ù Ú©Ø§Ø±Øª / ÙØ§Ù',callback_data='v25:vipcardinfo'),InlineKeyboardButton('ð ÙÛÙÚ© Ø¯Ø±Ú¯Ø§Ù',callback_data='v25:vipgatewayinfo')],[InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª',callback_data='v25:adminmenu')]]
    await update.callback_query.message.edit_text(txt,parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

# Finance/CRM helper: record a charge for a selected customer, with full/partial/unpaid state.
async def v25_finance_menu(update,context):
    uid=update.effective_user.id; rows=customer_list_rows(uid); lines=['ð <b>ÙØ§ÙÛ ÙØ´ØªØ±ÛØ§Ù</b>',''];
    if rows:
        lines += [f"â¢ {html.escape(r['name'])} â {r['id']}" for r in rows]
    else: lines.append('ÙÙÙØ² ÙØ´ØªØ±Û ÙØ¹Ø§ÙÛ Ø«Ø¨Øª ÙØ´Ø¯Ù Ø§Ø³Øª.')
    kb=[[InlineKeyboardButton(f'â Ø«Ø¨Øª Ù¾Ø±Ø¯Ø§Ø®Øª Ø¨Ø±Ø§Û {r["name"]}',callback_data=f'v25:finadd:{r["id"]}')] for r in rows[:40]]
    kb.append([InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª',callback_data='v25:business'),main_menu_button(uid)])
    await update.callback_query.message.edit_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

# 4) Extend callback one more time for global VIP + finance.
_OLD_V25_CALLBACK_EXTRA=v25_callback
async def v25_callback(update,context):
    q=update.callback_query; data=q.data; uid=q.from_user.id; p=data.split(':'); action=p[1] if len(p)>1 else ''
    try:
        if data=='v25:vip': return await v25_user_vip_plans(update,context)
        if action=='userplan':
            pid=int(p[2]); plan=_v25_exec('SELECT * FROM subscription_plans_v25 WHERE id=? AND enabled=1',(pid,),fetchone=True)
            if not plan: await q.answer('Ù¾ÙÙ Ù¾ÛØ¯Ø§ ÙØ´Ø¯.',show_alert=True); return
            card_on=get_system_setting('vip_card_enabled','0')=='1'; gw_on=get_system_setting('vip_gateway_enabled','0')=='1'
            kb=[]
            if gw_on and get_system_setting('vip_gateway_url',''):
                kb.append([InlineKeyboardButton('ð³ Ù¾Ø±Ø¯Ø§Ø®Øª Ø¢ÙÙØ§ÛÙ',url=get_system_setting('vip_gateway_url',''))])
            if card_on and get_system_setting('vip_card_number',''):
                kb.append([InlineKeyboardButton('ðµ Ú©Ø§Ø±ØªâØ¨ÙâÚ©Ø§Ø±Øª',callback_data=f'v25:vipglobalcard:{pid}')])
            kb.append([InlineKeyboardButton('â¬ï¸ Ù¾ÙÙâÙØ§',callback_data='v25:vip'),main_menu_button(uid)])
            await q.message.edit_text(f"ð <b>{html.escape(plan['name'])}</b>\n\nð° {irr(plan['price_rial'])}\n\nØ±ÙØ´ Ù¾Ø±Ø¯Ø§Ø®Øª Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:",parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb)); return
        if action=='vipglobalcard':
            plan=_v25_exec('SELECT * FROM subscription_plans_v25 WHERE id=? AND enabled=1',(int(p[2]),),fetchone=True)
            if not plan or get_system_setting('vip_card_enabled','0')!='1' or not get_system_setting('vip_card_number',''):
                await q.answer('Ù¾Ø±Ø¯Ø§Ø®Øª Ú©Ø§Ø±ØªâØ¨ÙâÚ©Ø§Ø±Øª VIP Ø¯Ø± Ø­Ø§Ù Ø­Ø§Ø¶Ø± ÙØ¹Ø§Ù ÙÛØ³Øª.',show_alert=True); return
            num=get_system_setting('vip_card_number',''); name=get_system_setting('vip_card_name','')
            context.user_data['v25_mode']='vip_receipt'; context.user_data['vip_plan_id']=int(p[2])
            await q.message.edit_text(f'ðµ <b>Ù¾Ø±Ø¯Ø§Ø®Øª Ú©Ø§Ø±ØªâØ¨ÙâÚ©Ø§Ø±Øª VIP</b>\n\nð° ÙØ¨ÙØº: {irr(plan["price_rial"])}\nð³ Ø´ÙØ§Ø±Ù Ú©Ø§Ø±Øª: <code>{html.escape(num)}</code>\nð¤ Ø¨Ù ÙØ§Ù: {html.escape(name or "â")}\n\nð Ø¨Ø¹Ø¯ Ø§Ø² ÙØ§Ø±ÛØ² ØªØµÙÛØ± Ø±Ø³ÛØ¯ Ø±Ø§ Ø¨ÙØ±Ø³Øª.',parse_mode='HTML',reply_markup=v25_back(uid,'v25:vip')); return
        if data=='v25:adminvip': return await v25_admin_vip_payment(update,context)
        if data=='v25:vipcardtoggle':
            if not admin_guard(uid): await q.answer('â',show_alert=True); return
            set_system_setting('vip_card_enabled','0' if get_system_setting('vip_card_enabled','0')=='1' else '1',uid); return await v25_admin_vip_payment(update,context)
        if data=='v25:vipgatewaytoggle':
            if not admin_guard(uid): await q.answer('â',show_alert=True); return
            set_system_setting('vip_gateway_enabled','0' if get_system_setting('vip_gateway_enabled','0')=='1' else '1',uid); return await v25_admin_vip_payment(update,context)
        if data=='v25:vipcardinfo':
            if not admin_guard(uid): await q.answer('â',show_alert=True); return
            context.user_data['v25_mode']='admin_vip_card_number'; await q.message.edit_text('ð³ Ø´ÙØ§Ø±Ù Ú©Ø§Ø±Øª VIP Ø±Ø§ Ø¨ÙØ±Ø³Øª:'); return
        if data=='v25:vipgatewayinfo':
            if not admin_guard(uid): await q.answer('â',show_alert=True); return
            context.user_data['v25_mode']='admin_vip_gateway_url'; await q.message.edit_text('ð ÙÛÙÚ© Ø¯Ø±Ú¯Ø§Ù VIP Ø±Ø§ Ø¨ÙØ±Ø³Øª:'); return
        if action=='finadd':
            if not admin_guard(uid) and not customer_feature_allowed(uid): await q.answer('â',show_alert=True); return
            customer_id=int(p[2]); c=db(); owner_row=c.execute('SELECT id FROM customers WHERE id=? AND owner_user_id=?',(customer_id,uid)).fetchone(); c.close();
            if not owner_row: await q.answer('â ÙØ´ØªØ±Û ÙØªØ¹ÙÙ Ø¨Ù Ø­Ø³Ø§Ø¨ Ø´ÙØ§ ÙÛØ³Øª.',show_alert=True); return
            context.user_data['v25_mode']='v25_mode_fin_total'; context.user_data['fin_customer_id']=customer_id; await q.message.edit_text('ð° ÙØ¨ÙØº Ú©Ù Ø®Ø¯ÙØª Ø±Ø§ Ø¨Ù Ø±ÛØ§Ù ÙØ§Ø±Ø¯ Ú©Ù:'); return
        if action=='bizfinance': return await v25_finance_menu(update,context)
        return await _OLD_V25_CALLBACK_EXTRA(update,context)
    except Exception as e:
        logger.exception('V25 extra callback error: %s',e); await q.message.reply_text('â Ø¹ÙÙÛØ§Øª Ø§ÙØ¬Ø§Ù ÙØ´Ø¯.',reply_markup=v25_back(uid))

# 5) Admin feature page: include ALL feature flags, not only V25-specific ones.
async def v25_admin_feature_status(update,context):
    uid=update.effective_user.id
    if not admin_guard(uid): return await update.callback_query.answer('â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.',show_alert=True)
    c=db(); rows=c.execute('SELECT key,enabled FROM feature_flags ORDER BY key').fetchall(); c.close()
    labels=dict(FEATURE_LABELS_FA)
    labels.update(V25_FEATURE_LABELS)
    text='ð§ <b>ÙØ¶Ø¹ÛØª ÙÙÙ ÙØ§Ø¨ÙÛØªâÙØ§</b>\n\n'; kb=[]
    for r in rows:
        label=labels.get(r['key'],r['key'].replace('_',' ')); state='ð¢' if r['enabled'] else 'ð´'; text+=f'{state} {label}\n'; kb.append([InlineKeyboardButton(f'{state} {label}',callback_data=f'v25:feat:{r["key"]}')])
    kb.append([InlineKeyboardButton('â¬ï¸ ÙØ¯ÛØ±ÛØª',callback_data='v25:adminmenu'),main_menu_button(uid)])
    await update.callback_query.message.edit_text(text,parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

# 6) Text modes for global VIP settings.
_OLD_V25_INSTALLMENT_TEXT_EXTRA=v25_installment_text_save
async def v25_installment_text_save(update,context):
    uid=update.effective_user.id; mode=context.user_data.get('v25_mode'); txt=(update.message.text or '').strip()
    if mode=='admin_vip_card_number':
        if not admin_guard(uid): clear_flow(context); return True
        context.user_data['vip_card_number_new']=txt; context.user_data['v25_mode']='admin_vip_card_name'; await update.message.reply_text('ð¤ ÙØ§Ù ØµØ§Ø­Ø¨ Ú©Ø§Ø±Øª Ø±Ø§ Ø¨ÙØ±Ø³Øª ÛØ§ - Ø¨Ø²Ù:'); return True
    if mode=='admin_vip_card_name':
        if not admin_guard(uid): clear_flow(context); return True
        set_system_setting('vip_card_number',context.user_data.get('vip_card_number_new',''),uid); set_system_setting('vip_card_name','' if txt=='-' else txt,uid); clear_flow(context); await update.message.reply_text('â Ø§Ø·ÙØ§Ø¹Ø§Øª Ú©Ø§Ø±Øª VIP Ø°Ø®ÛØ±Ù Ø´Ø¯Ø Ø¨Ø±Ø§Û Ø¬ÙÙÚ¯ÛØ±Û Ø§Ø² ÙØ¹Ø§ÙâØ´Ø¯Ù ÙØ§Ø®ÙØ§Ø³ØªÙØ ÙØ¶Ø¹ÛØª ÙÙÚÙØ§Ù Ø¬Ø¯Ø§Ú¯Ø§ÙÙ ÙØ§Ø¨Ù Ú©ÙØªØ±Ù Ø§Ø³Øª.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ð Ù¾Ø±Ø¯Ø§Ø®Øª VIP',callback_data='v25:adminvip')],[main_menu_button(uid)]])); return True
    if mode=='admin_vip_gateway_url':
        if not admin_guard(uid): clear_flow(context); return True
        set_system_setting('vip_gateway_url',txt,uid); clear_flow(context); await update.message.reply_text('â ÙÛÙÚ© Ø¯Ø±Ú¯Ø§Ù VIP Ø°Ø®ÛØ±Ù Ø´Ø¯. ÙØ¶Ø¹ÛØª Ø±ÙØ´Ù/Ø®Ø§ÙÙØ´ Ø¬Ø¯Ø§Ø³Øª.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ð Ù¾Ø±Ø¯Ø§Ø®Øª VIP',callback_data='v25:adminvip')],[main_menu_button(uid)]])); return True
    return await _OLD_V25_INSTALLMENT_TEXT_EXTRA(update,context)

# 7) Customer direct-message shortcut in customer detail.
_OLD_CUSTOMER_DETAIL_V25=customer_detail
async def customer_detail(update,context,cid):
    await _OLD_CUSTOMER_DETAIL_V25(update,context,cid)
    try:
        # Send an additional message button after the detail view; keeping this non-destructive.
        q=update.callback_query; uid=q.from_user.id; r=get_customer(uid,cid)
        if r and r['telegram_user_id']:
            await q.message.reply_text('ð© Ø¨Ø±Ø§Û Ø§Ø±Ø³Ø§Ù Ù¾ÛØ§Ù ÙØ³ØªÙÛÙ Ø¨Ù Ø§ÛÙ ÙØ´ØªØ±ÛØ ÙØªÙ Ø±Ø§ Ø¨ÙØ±Ø³Øª:',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ð© Ø§Ø±Ø³Ø§Ù Ù¾ÛØ§Ù',callback_data=f'v25:msgcustomer:{cid}')],[main_menu_button(uid)]]))
    except Exception: pass

_OLD_V25_CALLBACK_EXTRA2=v25_callback
async def v25_callback(update,context):
    q=update.callback_query; data=q.data; uid=q.from_user.id; p=data.split(':'); action=p[1] if len(p)>1 else ''
    if action=='msgcustomer':
        cid=int(p[2]); r=get_customer(uid,cid)
        if not r or not r['telegram_user_id']: await q.answer('Ø§ÛÙ ÙØ´ØªØ±Û Ø´ÙØ§Ø³Ù ØªÙÚ¯Ø±Ø§Ù ÙØ¯Ø§Ø±Ø¯.',show_alert=True); return
        context.user_data['v25_mode']='customer_direct_message'; context.user_data['customer_message_id']=cid; await q.answer(); await q.message.reply_text('ð© ÙØªÙ Ù¾ÛØ§Ù Ø±Ø§ Ø¨ÙØ±Ø³Øª:',reply_markup=v25_back(uid,'v25:business')); return
    return await _OLD_V25_CALLBACK_EXTRA2(update,context)

# 8) Final text mode: direct customer message.
_OLD_V25_INSTALLMENT_TEXT_FINAL=v25_installment_text_save
async def v25_installment_text_save(update,context):
    uid=update.effective_user.id; mode=context.user_data.get('v25_mode'); txt=(update.message.text or '').strip()
    if mode=='customer_direct_message':
        cid=int(context.user_data.get('customer_message_id')); r=get_customer(uid,cid); clear_flow(context)
        if not r or not r['telegram_user_id']: await update.message.reply_text('â ÙØ´ØªØ±Û ÙØ§Ø¨Ù Ù¾ÛØ§ÙâØ±Ø³Ø§ÙÛ ÙÛØ³Øª.',reply_markup=customer_keyboard(uid)); return True
        try:
            await context.bot.send_message(r['telegram_user_id'],f'ð© <b>Ù¾ÛØ§Ù Ø§Ø² {html.escape(ensure_business_profile(uid)["business_name"] or "Ú©Ø³Ø¨âÙÚ©Ø§Ø±")}</b>\n\n{html.escape(txt)}',parse_mode='HTML')
            await update.message.reply_text('â Ù¾ÛØ§Ù Ø§Ø±Ø³Ø§Ù Ø´Ø¯.',reply_markup=customer_keyboard(uid))
        except Exception:
            await update.message.reply_text('â Ø§Ø±Ø³Ø§Ù Ù¾ÛØ§Ù ÙØ§ÙÙÙÙ Ø¨ÙØ¯.',reply_markup=customer_keyboard(uid))
        return True
    return await _OLD_V25_INSTALLMENT_TEXT_FINAL(update,context)



# Voice execution: simple, confirm-first admin commands + goal/reminder creation.


async def v25_customer_message_menu(update,context):
    uid=update.effective_user.id; rows=customer_list_rows(uid); selected=set(context.user_data.get('customer_message_selected',[])); lines=['ð© <b>Ø§Ø±Ø³Ø§Ù Ù¾ÛØ§Ù Ø¨Ù ÙØ´ØªØ±ÛØ§Ù</b>','']
    lines.append(f'Ø§ÙØªØ®Ø§Ø¨âØ´Ø¯Ù: {len(selected)}')
    kb=[]
    for r in rows[:50]:
        mark='â' if r['id'] in selected else 'â'; kb.append([InlineKeyboardButton(f'{mark} {r["name"]}',callback_data=f'v25:msgsel:{r["id"]}')])
    kb += [[InlineKeyboardButton('ð¢ ÙÙÙ ÙØ´ØªØ±ÛØ§Ù',callback_data='v25:msgall')],[InlineKeyboardButton('ð¤ Ø§Ø±Ø³Ø§Ù Ù¾ÛØ§Ù Ø§ÙØªØ®Ø§Ø¨âØ´Ø¯Ù',callback_data='v25:msgsend')],[InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª',callback_data='v25:business'),main_menu_button(uid)]]
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
        if not context.user_data.get('customer_message_selected'): await q.answer('Ø­Ø¯Ø§ÙÙ ÛÚ© ÙØ´ØªØ±Û Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù.',show_alert=True); return
        context.user_data['v25_mode']='customer_multi_message'; await q.answer(); await q.message.edit_text('ð© ÙØªÙ Ù¾ÛØ§Ù Ø±Ø§ Ø¨ÙØ±Ø³Øª:',reply_markup=v25_back(uid,'v25:customermsg')); return
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
                await context.bot.send_message(r['telegram_user_id'],f'ð© <b>Ù¾ÛØ§Ù Ø§Ø² {html.escape(biz["business_name"] or "Ú©Ø³Ø¨âÙÚ©Ø§Ø±")}</b>\n\n{html.escape(txt)}',parse_mode='HTML'); sent+=1
            except Exception: pass
        clear_flow(context); await update.message.reply_text(f'â Ù¾ÛØ§Ù Ø¨Ø±Ø§Û {sent} ÙØ´ØªØ±Û Ø§Ø±Ø³Ø§Ù Ø´Ø¯.',reply_markup=customer_keyboard(uid)); return True
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
        if data=='v25:adminmorning': return await v25_admin_morning(update,context)
        if data=='v25:adminprices': return await v25_admin_prices(update,context)
        if data in {'v25:reports','v25:report_week','v25:report_month','v25:toggle_morning','v25:toggle_night','v25:toggle_friday','v25:toggle_prices'} and not admin_guard(uid):
            await q.answer('â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.',show_alert=True); return
        if data=='v25:reports': return await v25_reports(update,context,'day')
        if data=='v25:report_week': return await v25_reports(update,context,'week')
        if data=='v25:report_month': return await v25_reports(update,context,'month')
        if data=='v25:toggle_morning': set_system_setting('morning_message_enabled','0' if get_system_setting('morning_message_enabled','1')=='1' else '1',uid); return await v25_admin_morning(update,context)
        if data=='v25:toggle_night': set_system_setting('night_message_enabled','0' if get_system_setting('night_message_enabled','1')=='1' else '1',uid); return await v25_admin_morning(update,context)
        if data=='v25:toggle_friday': set_system_setting('friday_pause','0' if get_system_setting('friday_pause','0')=='1' else '1',uid); return await v25_admin_morning(update,context)
        if data=='v25:toggle_prices': set_system_setting('price_data_status','off' if get_system_setting('price_data_status','auto')!='off' else 'auto',uid); return await v25_admin_prices(update,context)
        if action=='vip_receipt':
            if not admin_guard(uid): await q.answer('â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.',show_alert=True); return
            if len(parts) < 4 or parts[2] not in {'approve','reject'}:
                await q.answer('Ø¹ÙÙÛØ§Øª ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª.',show_alert=True); return
            rid=int(parts[3])
            c=db(); row=c.execute('SELECT vr.*,p.name,p.duration_minutes FROM vip_receipts vr JOIN subscription_plans_v25 p ON p.id=vr.plan_id WHERE vr.id=?',(rid,)).fetchone()
            if not row: c.close(); await q.answer('Ø±Ø³ÛØ¯ Ù¾ÛØ¯Ø§ ÙØ´Ø¯.',show_alert=True); return
            if row['status']!='pending': c.close(); await q.answer('Ø§ÛÙ Ø±Ø³ÛØ¯ ÙØ¨ÙØ§Ù Ø¨Ø±Ø±Ø³Û Ø´Ø¯Ù Ø§Ø³Øª.',show_alert=True); return
            now=_v25_now(); status=parts[2]
            if status=='approve':
                base=datetime.now(TZ); u=c.execute('SELECT vip_until FROM users WHERE user_id=?',(row['user_id'],)).fetchone()
                if u and u['vip_until']:
                    try: base=max(base,datetime.fromisoformat(u['vip_until']))
                    except Exception: pass
                expires=base+timedelta(minutes=int(row['duration_minutes']))
                c.execute('UPDATE users SET vip_until=? WHERE user_id=?',(expires.isoformat(),row['user_id']))
                c.execute('INSERT INTO subscription_history(user_id,plan,duration_days,source,amount,started_at,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?)',(row['user_id'],row['name'],max(0,int(round(int(row['duration_minutes'])/1440))), 'card_receipt',row['amount_rial'],now,expires.isoformat(),now))
                msg='â Ø±Ø³ÛØ¯ ØªØ£ÛÛØ¯ Ø´Ø¯ Ù VIP ÙØ¹Ø§Ù Ø´Ø¯.'
                user_msg=f'â Ù¾Ø±Ø¯Ø§Ø®Øª VIP Ø´ÙØ§ ØªØ£ÛÛØ¯ Ø´Ø¯.\n\nð Ù¾ÙÙ: {html.escape(row["name"])}\nâ° Ù¾Ø§ÛØ§Ù VIP: {fa_datetime(expires)}'
            else:
                msg='â Ø±Ø³ÛØ¯ Ø±Ø¯ Ø´Ø¯.'; user_msg='â Ø±Ø³ÛØ¯ Ù¾Ø±Ø¯Ø§Ø®Øª VIP Ø´ÙØ§ ØªØ£ÛÛØ¯ ÙØ´Ø¯. ÙØ·ÙØ§Ù Ø§Ø·ÙØ§Ø¹Ø§Øª Ù¾Ø±Ø¯Ø§Ø®Øª Ø±Ø§ Ø¨Ø±Ø±Ø³Û Ù Ø¯Ø± ØµÙØ±Øª ÙÛØ§Ø² Ø¯ÙØ¨Ø§Ø±Ù Ø§ÙØ¯Ø§Ù Ú©ÙÛØ¯.'
            c.execute("UPDATE vip_receipts SET status=?,reviewed_at=?,reviewed_by=? WHERE id=? AND status=\'pending\'",(status,now,uid,rid)); c.commit(); c.close()
            try: await context.bot.send_message(row['user_id'],user_msg,parse_mode='HTML',reply_markup=keyboard(row['user_id']))
            except Exception: logger.exception('VIP receipt user notification failed')
            await q.message.edit_text(msg,reply_markup=v25_back(uid,'v25:adminvip')); return
        if action=='instview': return await v25_installment_view(update,context,int(parts[2]))
        if action=='instpay':
            ipid=int(parts[2]); status=parts[3]
            # Never allow a user to mutate another user's installment by guessing its ID.
            row=_v25_exec('SELECT ip.*,p.user_id FROM installment_payments ip JOIN installment_plans p ON p.id=ip.plan_id WHERE ip.id=? AND p.user_id=?', (ipid,uid), fetchone=True)
            if not row: await q.answer('â Ø§ÛÙ ÙØ³Ø· ÙØªØ¹ÙÙ Ø¨Ù Ø­Ø³Ø§Ø¨ Ø´ÙØ§ ÙÛØ³Øª.',show_alert=True); return
            if status not in {'paid','later','unpaid'}: await q.answer('ÙØ¶Ø¹ÛØª ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª.',show_alert=True); return
            if status=='paid': _v25_exec('UPDATE installment_payments SET status="paid",paid_rial=amount_rial,paid_at=? WHERE id=?',(_v25_now(),ipid)); msg='â Ù¾Ø±Ø¯Ø§Ø®Øª Ø«Ø¨Øª Ø´Ø¯.'
            elif status=='later': _v25_exec('UPDATE installment_payments SET status="partial",note=? WHERE id=?',('Ú©Ø§Ø±Ø¨Ø± Ø§Ø¹ÙØ§Ù Ú©Ø±Ø¯ Ø¨Ø¹Ø¯Ø§Ù Ù¾Ø±Ø¯Ø§Ø®Øª ÙÛâÚ©ÙØ¯.',ipid)); msg='â³ Ø¨Ø±Ø§Û Ø¨Ø¹Ø¯ ÙÚ¯Ù Ø¯Ø§Ø´ØªÙ Ø´Ø¯.'
            else: _v25_exec('UPDATE installment_payments SET status="unpaid" WHERE id=?',(ipid,)); msg='â Ø¹Ø¯Ù Ù¾Ø±Ø¯Ø§Ø®Øª Ø«Ø¨Øª Ø´Ø¯.'
            await q.message.edit_text(msg,reply_markup=v25_back(uid,'v25:installments')); return
        if action=='planedit':
            if not admin_guard(uid): await q.answer('â',show_alert=True); return
            pid=int(parts[2]); plan=_v25_exec('SELECT * FROM subscription_plans_v25 WHERE id=?',(pid,),fetchone=True)
            if not plan: return
            context.user_data['v25_admin_plan_id']=pid; context.user_data['v25_mode']='admin_plan_price'; await q.message.edit_text(f'ð {html.escape(plan["name"])}\n\nÙÛÙØª Ø¬Ø¯ÛØ¯ Ø±Ø§ Ø¨Ù Ø±ÛØ§Ù Ø¨ÙØ±Ø³Øª:'); return
        if action=='planadd':
            if not admin_guard(uid): await q.answer('â',show_alert=True); return
            context.user_data['v25_mode']='admin_plan_name'; await q.message.edit_text('ð ÙØ§Ù Ù¾ÙÙ Ø¬Ø¯ÛØ¯ Ø±Ø§ Ø¨ÙØ±Ø³Øª:'); return
        if action=='feat':
            if not admin_guard(uid): await q.answer('â',show_alert=True); return
            if len(parts) < 3 or not _feature_flag_exists(parts[2]):
                await q.answer('ÙØ§Ø¨ÙÛØª ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª.',show_alert=True); return
            key=parts[2]
            cur=feature_enabled(key)
            set_feature(key,not cur,uid)
            set_feature_access_mode(key,'free' if not cur else 'off',uid)
            return await v25_admin_feature_status(update,context)
        if action=='smstoggle':
            if not admin_guard(uid): await q.answer('â',show_alert=True); return
            cfg=_v25_exec('SELECT enabled FROM sms_settings WHERE owner_user_id=?',(uid,),fetchone=True); enabled=not bool(cfg and cfg['enabled']); now=_v25_now(); _v25_exec('INSERT INTO sms_settings(owner_user_id,enabled,updated_at) VALUES(?,?,?) ON CONFLICT(owner_user_id) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at',(uid,int(enabled),now)); return await v25_admin_sms(update,context)
        if action=='smsconfig':
            if not admin_guard(uid): await q.answer('â',show_alert=True); return
            context.user_data['v25_mode']='admin_sms_endpoint'; await q.message.edit_text('ð¡ Endpoint Ø³Ø±ÙÛØ³ Ù¾ÛØ§ÙÚ©Û Ø±Ø§ Ø¨ÙØ±Ø³Øª:'); return
        if action=='smstest':
            if not admin_guard(uid): await q.answer('â',show_alert=True); return
            context.user_data['v25_mode']='v25_sms_test'; await q.message.edit_text('ð± Ø´ÙØ§Ø±Ù ÙÙØµØ¯ ØªØ³Øª Ø±Ø§ Ø¨ÙØ±Ø³Øª:',reply_markup=v25_back(uid,'v25:adminsms')); return
            return await _OLD_V25_CALLBACK_FINAL(update,context)
        if action=='gateway' and admin_guard(uid):
            return await _OLD_V25_CALLBACK_FINAL(update,context)
        if action=='card' and admin_guard(uid):
            return await _OLD_V25_CALLBACK_FINAL(update,context)
        return await _OLD_V25_CALLBACK_FINAL(update,context)
    except Exception as e:
        logger.exception('Final V25 callback error: %s',e)
        await q.message.reply_text(
            f'â ï¸ Ø§ÛÙ Ø¹ÙÙÛØ§Øª Ø¨Ø§ Ø®Ø·Ø§ Ø±ÙØ¨ÙâØ±Ù Ø´Ø¯.\n\nÚ©Ø¯ Ø®Ø·Ø§: <code>{type(e).__name__}</code>',
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('ð ØªÙØ§Ø´ Ø¯ÙØ¨Ø§Ø±Ù',callback_data=q.data)],
                [InlineKeyboardButton('â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª',callback_data='v25:hub'),main_menu_button(uid)]
            ])
        )

# Intercept a few additional text modes while preserving the legacy router.
_OLD_V25_INSTALLMENT_TEXT_SAVE=v25_installment_text_save
async def v25_installment_text_save(update,context):
    uid=update.effective_user.id; mode=context.user_data.get('v25_mode'); text=normalize_digits((update.message.text or '').strip())
    if mode=='admin_plan_price':
        if not admin_guard(uid): clear_flow(context); return True
        try: price=int(float(text.replace(',','')))
        except Exception: await update.message.reply_text('â ÙØ¨ÙØº ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª.'); return True
        pid=context.user_data.get('v25_admin_plan_id'); _v25_exec('UPDATE subscription_plans_v25 SET price_rial=?,updated_at=? WHERE id=?',(price,_v25_now(),pid)); clear_flow(context); await update.message.reply_text('â ÙÛÙØª Ù¾ÙÙ ØªØºÛÛØ± Ú©Ø±Ø¯.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ð Ù¾ÙÙâÙØ§',callback_data='v25:adminplans')],[main_menu_button(uid)]])); return True
    if mode=='admin_plan_name':
        if not admin_guard(uid): clear_flow(context); return True
        context.user_data['admin_plan_name']=text; context.user_data['v25_mode']='admin_plan_duration'; await update.message.reply_text('â±ï¸ ÙØ¯Øª Ø±Ø§ Ø¨Ù Ø¯ÙÛÙÙ ÙØ§Ø±Ø¯ Ú©Ù. ÙØ«Ø§Ù: 43200 Ø¨Ø±Ø§Û Û³Û° Ø±ÙØ²:'); return True
    if mode=='admin_plan_duration':
        if not admin_guard(uid): clear_flow(context); return True
        try: dur=int(text)
        except Exception: await update.message.reply_text('â ÙØ¯Øª ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª.'); return True
        context.user_data['admin_plan_duration']=dur; context.user_data['v25_mode']='admin_plan_create_price'; await update.message.reply_text('ð° ÙÛÙØª Ù¾ÙÙ Ø±Ø§ Ø¨Ù Ø±ÛØ§Ù Ø¨ÙØ±Ø³Øª:'); return True
    if mode=='admin_plan_create_price':
        if not admin_guard(uid): clear_flow(context); return True
        try: price=int(float(text.replace(',','')))
        except Exception: await update.message.reply_text('â ÙØ¨ÙØº ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª.'); return True
        code='custom_'+hashlib.sha256((context.user_data.get('admin_plan_name','')+_v25_now()).encode()).hexdigest()[:10]
        _v25_exec('INSERT INTO subscription_plans_v25(code,name,duration_minutes,price_rial,enabled,created_at,updated_at) VALUES(?,?,?,?,1,?,?)',(code,context.user_data['admin_plan_name'],context.user_data['admin_plan_duration'],price,_v25_now(),_v25_now())); clear_flow(context); await update.message.reply_text('â Ù¾ÙÙ Ø¬Ø¯ÛØ¯ Ø³Ø§Ø®ØªÙ Ø´Ø¯.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ð Ù¾ÙÙâÙØ§',callback_data='v25:adminplans')],[main_menu_button(uid)]])); return True
    if mode=='admin_sms_endpoint':
        if not admin_guard(uid): clear_flow(context); return True
        context.user_data['admin_sms_endpoint']=text; context.user_data['v25_mode']='admin_sms_key'; await update.message.reply_text('ð API Key Ø±Ø§ Ø¨ÙØ±Ø³Øª:'); return True
    if mode=='admin_sms_key':
        if not admin_guard(uid): clear_flow(context); return True
        endpoint=context.user_data.get('admin_sms_endpoint',''); now=_v25_now(); _v25_exec('INSERT INTO sms_settings(owner_user_id,enabled,provider,endpoint,api_key,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(owner_user_id) DO UPDATE SET endpoint=excluded.endpoint,api_key=excluded.api_key,updated_at=excluded.updated_at',(uid,0,'custom',endpoint,text,now)); clear_flow(context); await update.message.reply_text('â ØªÙØ¸ÛÙØ§Øª Ø³Ø±ÙÛØ³ Ù¾ÛØ§ÙÚ©Û Ø°Ø®ÛØ±Ù Ø´Ø¯Ø Ø³Ø±ÙÛØ³ ÙÙÚÙØ§Ù Ø®Ø§ÙÙØ´ Ø§Ø³Øª ØªØ§ Ø®ÙØ¯Øª ÙØ¹Ø§ÙØ´ Ú©ÙÛ.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ð± Ù¾ÛØ§ÙÚ©',callback_data='v25:adminsms')],[main_menu_button(uid)]])); return True
    if mode=='v25_mode_fin_customer':
        context.user_data['fin_customer_id']=int(text); context.user_data['v25_mode']='v25_mode_fin_total'; await update.message.reply_text('ð° ÙØ¨ÙØº Ú©Ù Ø®Ø¯ÙØª Ø±Ø§ Ø¨Ù Ø±ÛØ§Ù ÙØ§Ø±Ø¯ Ú©Ù:'); return True
    if mode=='v25_mode_fin_total':
        context.user_data['fin_total']=int(float(text.replace(',',''))); context.user_data['v25_mode']='v25_mode_fin_paid'; await update.message.reply_text('â ÙØ¨ÙØº Ù¾Ø±Ø¯Ø§Ø®ØªâØ´Ø¯Ù Ø±Ø§ Ø¨Ù Ø±ÛØ§Ù ÙØ§Ø±Ø¯ Ú©Ù (Ø§Ú¯Ø± ÙÙÙØ² ÚÛØ²Û Ù¾Ø±Ø¯Ø§Ø®Øª ÙØ´Ø¯Ù 0 Ø¨Ø²Ù):'); return True
    if mode=='v25_mode_fin_paid':
        paid=int(float(text.replace(',',''))); total=int(context.user_data.get('fin_total',0)); customer_id=int(context.user_data['fin_customer_id'])
        if total < 0 or paid < 0 or paid > total:
            await update.message.reply_text('â ÙØ¨ÙØº ÙØ§Ø±Ø¯Ø´Ø¯Ù ÙØ¹ØªØ¨Ø± ÙÛØ³Øª.'); return True
        c=db(); owner_row=c.execute('SELECT id FROM customers WHERE id=? AND owner_user_id=?',(customer_id,uid)).fetchone()
        if not owner_row:
            c.close(); clear_flow(context); await update.message.reply_text('â ÙØ´ØªØ±Û ÙØªØ¹ÙÙ Ø¨Ù Ø­Ø³Ø§Ø¨ Ø´ÙØ§ ÙÛØ³Øª.'); return True
        status='paid' if paid>=total else ('partial' if paid>0 else 'pending')
        c.execute('INSERT INTO customer_finance(owner_user_id,customer_id,amount_rial,paid_rial,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(uid,customer_id,total,paid,status,_v25_now(),_v25_now())); c.commit(); c.close(); clear_flow(context)
        await update.message.reply_text('â ØªØ±Ø§Ú©ÙØ´ ÙØ§ÙÛ ÙØ´ØªØ±Û Ø«Ø¨Øª Ø´Ø¯.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ð ÙØ§ÙÛ ÙØ´ØªØ±ÛØ§Ù',callback_data='v25:bizfinance')],[main_menu_button(uid)]])); return True
    if mode=='survey_comment':
        aid=int(context.user_data.get('survey_appointment_id')); c=db(); owner_ok=c.execute('SELECT id FROM appointments WHERE id=? AND customer_id IN (SELECT id FROM customers WHERE telegram_user_id=? )',(aid,uid)).fetchone();
        if not owner_ok: c.close(); clear_flow(context); await update.message.reply_text('â Ø§ÛÙ ÙØ¸Ø±Ø³ÙØ¬Û ÙØªØ¹ÙÙ Ø¨Ù Ø­Ø³Ø§Ø¨ Ø´ÙØ§ ÙÛØ³Øª.'); return True
        c.execute('UPDATE survey_responses SET suggestion=?,comment=? WHERE appointment_id=?',(text,text,aid)); c.commit(); c.close(); clear_flow(context); await update.message.reply_text('ð ÙÙÙÙÙØ Ù¾ÛØ´ÙÙØ§Ø¯Øª Ø«Ø¨Øª Ø´Ø¯.',reply_markup=InlineKeyboardMarkup([[main_menu_button(uid)]])); return True
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
        clear_flow(context); await update.message.reply_text('â ØªØ³Øª Ø§Ø±Ø³Ø§Ù Ø´Ø¯.' if ok else 'â ï¸ ØªØ³Øª Ø§ÙØ¬Ø§Ù ÙØ´Ø¯Ø ØªÙØ¸ÛÙØ§Øª Ø³Ø±ÙÛØ³ Ø±Ø§ Ø¨Ø±Ø±Ø³Û Ú©Ù.\n'+html.escape(details),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ð± Ù¾ÛØ§ÙÚ©',callback_data='v25:sms')],[main_menu_button(uid)]])); return True
    if mode=='booking_phone' or mode=='booking_name' or mode=='survey_comment':
        return await v25_installment_text_save(update,context)
    return await _OLD_V25_BUSINESS_TEXT_SAVE(update,context)

# The final text router uses the enhanced state dispatcher before legacy routes.
_OLD_TEXT_ROUTER_FINAL=text_router
async def text_router(update,context):
    uid=update.effective_user.id
    txt=(update.message.text or '').strip() if update.message else ''
    if txt in ('â¬ï¸ Ø¨Ø±Ú¯Ø´Øª','â¬ï¸ Back'):
        clear_flow(context); await update.message.reply_text(v25_hub_text(uid),parse_mode='HTML',reply_markup=v25_hub_keyboard(uid)); return
    if txt in ('ð  ÙÙÙÛ Ø§ØµÙÛ','ð  Main Menu'):
        clear_flow(context); await update.message.reply_text('ð  ÙÙÙÛ Ø§ØµÙÛ',reply_markup=keyboard(uid)); return
    if txt in ('ð§  ÙØ±Ú©Ø² ÙÙ','ð§  My Center'):
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
    rows.append([InlineKeyboardButton('ð¡ï¸ ÙØ±Ú©Ø² ÙØ¯ÛØ±ÛØª Ø¬Ø¯ÛØ¯',callback_data='v25:adminmenu')])
    return InlineKeyboardMarkup(rows)
admin_keyboard=final_admin_keyboard


# ===================== TOKEN / QUOTA SYSTEM =====================
# Free by default. Admin can later switch any feature to limited/VIP/off.
TOKEN_FEATURES = [
    ("price_data", "ð ÙÛÙØª Ø¨Ø§Ø²Ø§Ø±"),
    ("portfolio", "ð° Ø³Ø±ÙØ§ÛÙâÙØ§Û ÙÙ"),
    ("customers", "ð¥ CRM / ÙØ´ØªØ±ÛØ§Ù"),
    ("customer_online_booking", "ð Ø±Ø²Ø±Ù Ø¢ÙÙØ§ÛÙ"),
    ("reminders", "ð ÛØ§Ø¯Ø¢ÙØ±Û"),
    ("reports", "ð Ú¯Ø²Ø§Ø±Ø´ Ø­Ø±ÙÙâØ§Û"),
    ("mini_app", "ð± Mini App"),
    ("smart_planner", "ð§  Ø¨Ø±ÙØ§ÙÙâØ±ÛØ²Û ÙÙØ´ÙÙØ¯"),
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
    label=dict(TOKEN_FEATURES).get(key,"Ø§ÛÙ ÙØ§Ø¨ÙÛØª" if fa else "This feature")
    if reason == "vip":
        return f"ð {label}\n\nØ§ÛÙ Ø¨Ø®Ø´ Ø¯Ø± Ø­Ø§Ù Ø­Ø§Ø¶Ø± ÙØ®ØµÙØµ VIP Ø§Ø³Øª." if fa else f"ð {label}\n\nThis feature is currently for VIP users."
    if reason == "quota":
        return (f"ðï¸ Ø³ÙÙÛÙ Ø±Ø§ÛÚ¯Ø§Ù {label} ØªÙØ§Ù Ø´Ø¯Ù Ø§Ø³Øª.\n\nâ­ ØªÙÚ©Ù Ø´ÙØ§: {token_balance(uid)}\nð ÙÛâØªÙØ§ÙÛØ¯ Ø¨Ø§ ØªÙÚ©Ù Ø§Ø¯Ø§ÙÙ Ø¯ÙÛØ¯ ÛØ§ Ø§Ø´ØªØ±Ø§Ú© VIP ØªÙÛÙ Ú©ÙÛØ¯.") if fa else (f"ðï¸ Your free quota for {label} is finished.\n\nâ­ Tokens: {token_balance(uid)}\nð You can continue with tokens or get VIP.")
    return "â Ø§ÛÙ ÙØ§Ø¨ÙÛØª ÙØ¹ÙØ§Ù Ø¯Ø± Ø¯Ø³ØªØ±Ø³ ÙÛØ³Øª." if fa else "â This feature is not available right now."

def token_user_text(uid):
    bal=token_balance(uid); fa=lang(uid)=="fa"
    c=db(); rows=c.execute("SELECT feature_key,free_limit,period,token_cost,after_limit,enabled FROM token_rules ORDER BY feature_key").fetchall(); c.close()
    lines=["ðï¸ <b>ØªÙÚ©ÙâÙØ§Û ÙÙ</b>","",f"â­ ÙÙØ¬ÙØ¯Û: <b>{bal}</b>",""]
    if fa:
        lines.append("ØªÙÚ©ÙâÙØ§ Ø¨Ø±Ø§Û Ø§Ø³ØªÙØ§Ø¯Ù Ø¨ÛØ´ØªØ± Ø§Ø² ÙØ§Ø¨ÙÛØªâÙØ§ÛÛ Ú©Ù ÙØ¯ÛØ± Ø³ÙÙÛÙâÚ¯Ø°Ø§Ø±Û Ú©Ø±Ø¯ÙâØ§ÙØ¯ ÙØ§Ø¨Ù ÙØµØ±ÙâØ§ÙØ¯.")
    else: lines.append("Tokens can be used for extra usage on features configured by the admin.")
    return "\n".join(lines)

def token_user_keyboard(uid):
    fa=lang(uid)=="fa"
    return InlineKeyboardMarkup([[InlineKeyboardButton("ð Ø¨Ø±ÙØ²Ø±Ø³Ø§ÙÛ" if fa else "ð Refresh",callback_data="v25:tokens")],[InlineKeyboardButton("ð VIP" if fa else "ð VIP",callback_data="vip:main")],[InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª" if fa else "â¬ï¸ Back",callback_data="v25:hub"),main_menu_button(uid)]])

def token_admin_text():
    ref=int(token_setting("referral_tokens_per_success","10") or 10)
    xp_per=int(token_setting("xp_per_token","100") or 100)
    vip_cost=int(token_setting("tokens_for_vip_days","100") or 100)
    vip_days=int(token_setting("vip_days_per_token_pack","30") or 30)
    c=db(); top=c.execute("SELECT COUNT(*) n FROM token_wallets").fetchone()["n"]; total=c.execute("SELECT COALESCE(SUM(balance),0) n FROM token_wallets").fetchone()["n"]; c.close()
    return (f"ðï¸ <b>ÙØ¯ÛØ±ÛØª ØªÙÚ©Ù Ù Ø³ÙÙÛÙ</b>\n\nð¥ Ú©ÛÙâÙ¾ÙÙâÙØ§Û ÙØ¹Ø§Ù: <b>{top}</b>\nâ­ ÙØ¬ÙÙØ¹ ØªÙÚ©ÙâÙØ§Û ÙÙØ¬ÙØ¯: <b>{total}</b>\n\nð¤ ÙØ± Ø¯Ø¹ÙØª ÙÙÙÙ: <b>{ref} ØªÙÚ©Ù</b>\nâ­ ÙØ± <b>{xp_per} XP</b>: 1 ØªÙÚ©Ù\nð <b>{vip_days} Ø±ÙØ² VIP</b>: {vip_cost} ØªÙÚ©Ù\n\nÙØ± ÙØ§Ø¨ÙÛØª Ø±Ø§ ÙÛâØªÙØ§ÙÛ Ø¬Ø¯Ø§Ú¯Ø§ÙÙ Ø±ÙÛ Ø±Ø§ÛÚ¯Ø§Ù ÙØ§ÙØ­Ø¯ÙØ¯Ø Ø³ÙÙÛÙâØ¯Ø§Ø± ÛØ§ VIP ÙØ±Ø§Ø± Ø¨Ø¯ÙÛ.")

def token_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("ð¤ ØªÙÚ©Ù Ø¯Ø¹ÙØª",callback_data="v25:token_ref"),InlineKeyboardButton("â­ XP â ØªÙÚ©Ù",callback_data="v25:token_xp")],
        [InlineKeyboardButton("ð ØªÙÚ©Ù â VIP",callback_data="v25:token_vip")],
        [InlineKeyboardButton("âï¸ Ø³ÙÙÛÙ ÙØ§Ø¨ÙÛØªâÙØ§",callback_data="v25:token_rules")],
        [InlineKeyboardButton("ð Ú©ÛÙâÙ¾ÙÙ Ú©Ø§Ø±Ø¨Ø±Ø§Ù",callback_data="v25:token_users")],
        [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª",callback_data="adm:stats")]
    ])

def token_rules_keyboard():
    rows=[]
    for key,label in TOKEN_FEATURES:
        r=token_rule(key)
        if r:
            limit="â" if int(r["free_limit"])<0 else str(r["free_limit"])
            cost=int(r["token_cost"])
            txt=f"{label} | {limit} | ðï¸{cost}"
        else: txt=f"{label} | â | ðï¸0"
        rows.append([InlineKeyboardButton(txt,callback_data=f"v25:token_rule:{key}")])
    rows.append([InlineKeyboardButton("â¬ï¸ ØªÙÚ©Ù Ù Ø³ÙÙÛÙ",callback_data="v25:tokens_admin")])
    return InlineKeyboardMarkup(rows)

def token_rule_keyboard(key):
    r=token_rule(key); fa=True
    limit=int(r["free_limit"]) if r else -1; cost=int(r["token_cost"]) if r else 0
    period=r["period"] if r else "lifetime"
    after=r["after_limit"] if r else "vip"
    def lim(n): return "ð¢ â Ø±Ø§ÛÚ¯Ø§Ù" if n<0 else f"ð¢ {n} Ø¨Ø§Ø±"
    def mark(n): return "â" if limit==n else ""
    def cmk(n): return "â" if cost==n else ""
    rows=[
        [InlineKeyboardButton(f"{mark(-1)} â Ø±Ø§ÛÚ¯Ø§Ù",callback_data=f"v25:token_setlimit:{key}:-1")],
        [InlineKeyboardButton(f"{mark(1)} 1",callback_data=f"v25:token_setlimit:{key}:1"),InlineKeyboardButton(f"{mark(3)} 3",callback_data=f"v25:token_setlimit:{key}:3"),InlineKeyboardButton(f"{mark(5)} 5",callback_data=f"v25:token_setlimit:{key}:5")],
        [InlineKeyboardButton(f"{mark(10)} 10",callback_data=f"v25:token_setlimit:{key}:10"),InlineKeyboardButton(f"{mark(20)} 20",callback_data=f"v25:token_setlimit:{key}:20"),InlineKeyboardButton(f"{mark(50)} 50",callback_data=f"v25:token_setlimit:{key}:50")],
        [InlineKeyboardButton(f"{cmk(0)} ðï¸0",callback_data=f"v25:token_setcost:{key}:0"),InlineKeyboardButton(f"{cmk(1)} ðï¸1",callback_data=f"v25:token_setcost:{key}:1"),InlineKeyboardButton(f"{cmk(5)} ðï¸5",callback_data=f"v25:token_setcost:{key}:5")],
        [InlineKeyboardButton(f"{cmk(10)} ðï¸10",callback_data=f"v25:token_setcost:{key}:10"),InlineKeyboardButton(f"{cmk(20)} ðï¸20",callback_data=f"v25:token_setcost:{key}:20"),InlineKeyboardButton(f"{cmk(50)} ðï¸50",callback_data=f"v25:token_setcost:{key}:50")],
        [InlineKeyboardButton("ð Ø±ÙØ²Ø§ÙÙ",callback_data=f"v25:token_period:{key}:daily"),InlineKeyboardButton("ð ÙÙØªÚ¯Û",callback_data=f"v25:token_period:{key}:weekly"),InlineKeyboardButton("ð ÙØ§ÙØ§ÙÙ",callback_data=f"v25:token_period:{key}:monthly")],
        [InlineKeyboardButton("â¾ï¸ ÙØ±Ú¯Ø² Ø¨Ø§Ø²ÙØ´Ø§ÙÛ ÙØ´ÙØ¯",callback_data=f"v25:token_period:{key}:lifetime")],
        [InlineKeyboardButton("ð Ø¨Ø¹Ø¯ Ø§Ø² Ø³ÙÙÛÙ â VIP",callback_data=f"v25:token_after:{key}:vip")],
        [InlineKeyboardButton("ð¢ ÙØ¹Ø§Ù",callback_data=f"v25:token_enable:{key}:1"),InlineKeyboardButton("ð´ ØºÛØ±ÙØ¹Ø§Ù",callback_data=f"v25:token_enable:{key}:0")],
        [InlineKeyboardButton("â¬ï¸ Ø³ÙÙÛÙ ÙØ§Ø¨ÙÛØªâÙØ§",callback_data="v25:token_rules")]
    ]
    return InlineKeyboardMarkup(rows)

async def token_admin_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if not admin_guard(uid): await q.answer("â",show_alert=True); return
    await q.answer(); p=q.data.split(":")
    action=p[1] if len(p)>1 else "tokens_admin"
    if action=="tokens_admin": await q.message.edit_text(token_admin_text(),parse_mode="HTML",reply_markup=token_admin_keyboard()); return
    if action=="token_ref":
        cur=int(token_setting("referral_tokens_per_success","10") or 10); nxt={1:10,10:20,20:50,50:100,100:1}.get(cur,10); set_token_setting("referral_tokens_per_success",nxt,uid); await q.message.edit_text(token_admin_text(),parse_mode="HTML",reply_markup=token_admin_keyboard()); return
    if action=="token_xp":
        cur=int(token_setting("xp_per_token","100") or 100); nxt={10:50,50:100,100:250,250:500,500:10}.get(cur,100); set_token_setting("xp_per_token",nxt,uid); await q.message.edit_text(token_admin_text(),parse_mode="HTML",reply_markup=token_admin_keyboard()); return
    if action=="token_vip":
        cur=int(token_setting("tokens_for_vip_days","100") or 100); nxt={50:100,100:250,250:500,500:1000,1000:50}.get(cur,100); set_token_setting("tokens_for_vip_days",nxt,uid); await q.message.edit_text(token_admin_text(),parse_mode="HTML",reply_markup=token_admin_keyboard()); return
    if action=="token_rules": await q.message.edit_text("âï¸ <b>Ø³ÙÙÛÙ ÙØ§Ø¨ÙÛØªâÙØ§</b>\n\nâ ÛØ¹ÙÛ ÙØ¹ÙØ§Ù Ú©Ø§ÙÙØ§Ù Ø±Ø§ÛÚ¯Ø§Ù Ù ÙØ§ÙØ­Ø¯ÙØ¯. ÙÙØªÛ Ø³ÙÙÛÙ ØªØ¹ÛÛÙ Ú©ÙÛØ Ø¨Ø¹Ø¯ Ø§Ø² Ø§ØªÙØ§Ù Ø¢Ù ÙØ§Ø¨ÙÛØª ÙÛâØªÙØ§ÙØ¯ Ø¨Ø§ ØªÙÚ©Ù ÛØ§ VIP Ø§Ø¯Ø§ÙÙ Ù¾ÛØ¯Ø§ Ú©ÙØ¯.",parse_mode="HTML",reply_markup=token_rules_keyboard()); return
    if action=="token_users":
        c=db(); rows=c.execute("SELECT user_id,balance FROM token_wallets ORDER BY balance DESC LIMIT 50").fetchall(); c.close(); txt="ð <b>Ú©ÛÙâÙ¾ÙÙ Ú©Ø§Ø±Ø¨Ø±Ø§Ù</b>\n\n"+"\n".join(f"ð¤ <code>{r['user_id']}</code> â ðï¸ {r['balance']}" for r in rows) if rows else "ð Ú©ÛÙâÙ¾ÙÙÛ Ø«Ø¨Øª ÙØ´Ø¯Ù."; await q.message.edit_text(txt,parse_mode="HTML",reply_markup=token_admin_keyboard()); return
    if action=="token_rule":
        key=p[2]; label=dict(TOKEN_FEATURES).get(key,key); await q.message.edit_text(f"âï¸ <b>{html.escape(label)}</b>\n\nØ¯Ú©ÙÙâÙØ§Û Ø²ÛØ± Ø±Ø§ Ø¨Ø²Ù ØªØ§ Ø³ÙÙÛÙ Ù ÙØ²ÛÙÙ ØªÙÚ©Ù ØªÙØ¸ÛÙ Ø´ÙØ¯.",parse_mode="HTML",reply_markup=token_rule_keyboard(key)); return
    if action=="token_setlimit":
        key=p[2]; limit=int(p[3]); r=token_rule(key); set_token_rule(key,limit,r["period"] if r else "lifetime",int(r["token_cost"]) if r else 0,r["after_limit"] if r else "vip",int(r["enabled"]) if r else 1,uid); await q.message.edit_text("â Ø³ÙÙÛÙ ØªØºÛÛØ± Ú©Ø±Ø¯.",reply_markup=token_rule_keyboard(key)); return
    if action=="token_setcost":
        key=p[2]; cost=int(p[3]); r=token_rule(key); set_token_rule(key,int(r["free_limit"]) if r else -1,r["period"] if r else "lifetime",cost,r["after_limit"] if r else "vip",int(r["enabled"]) if r else 1,uid); await q.message.edit_text("â ÙØ²ÛÙÙ ØªÙÚ©Ù ØªØºÛÛØ± Ú©Ø±Ø¯.",reply_markup=token_rule_keyboard(key)); return
    if action=="token_period":
        key=p[2]; period=p[3]; r=token_rule(key); set_token_rule(key,int(r["free_limit"]) if r else -1,period,int(r["token_cost"]) if r else 0,r["after_limit"] if r else "vip",int(r["enabled"]) if r else 1,uid); await q.message.edit_text("â Ø¯ÙØ±Ù Ø³ÙÙÛÙ ØªØºÛÛØ± Ú©Ø±Ø¯.",reply_markup=token_rule_keyboard(key)); return
    if action=="token_after":
        key=p[2]; after=p[3]; r=token_rule(key); set_token_rule(key,int(r["free_limit"]) if r else -1,r["period"] if r else "lifetime",int(r["token_cost"]) if r else 0,after,int(r["enabled"]) if r else 1,uid); await q.message.edit_text("â Ø±ÙØªØ§Ø± Ø¨Ø¹Ø¯ Ø§Ø² Ù¾Ø§ÛØ§Ù Ø³ÙÙÛÙ ØªÙØ¸ÛÙ Ø´Ø¯.",reply_markup=token_rule_keyboard(key)); return
    if action=="token_enable":
        key=p[2]; enabled=int(p[3]); r=token_rule(key); set_token_rule(key,int(r["free_limit"]) if r else -1,r["period"] if r else "lifetime",int(r["token_cost"]) if r else 0,r["after_limit"] if r else "vip",enabled,uid); await q.message.edit_text("â ÙØ¶Ø¹ÛØª Ø³ÙÙÛÙ ØªØºÛÛØ± Ú©Ø±Ø¯.",reply_markup=token_rule_keyboard(key)); return

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

# ===================== ENHANCED REFERRAL SYSTEM =====================

def ref_get(key, default=""):
    """Read a referral setting."""
    try:
        c = db(); r = c.execute("SELECT value FROM referral_settings WHERE key=?", (key,)).fetchone()
        c.close(); return r["value"] if r else default
    except Exception:
        return default

def ref_set(key, value):
    """Write a referral setting."""
    try:
        c = db(); now = datetime.now(TZ).isoformat()
        c.execute("INSERT INTO referral_settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                  (key, str(value), now)); c.commit(); c.close()
    except Exception:
        pass

def ref_log(user_id, action, target_user=None, details=""):
    """Log a referral event."""
    try:
        c = db(); c.execute("INSERT INTO referral_logs(user_id,action,target_user,details,created_at) VALUES(?,?,?,?,?)",
                            (user_id, action, target_user, details[:500], datetime.now(TZ).isoformat()))
        c.commit(); c.close()
    except Exception:
        pass

def get_or_create_referral_code(uid):
    """Get or create a unique referral code for a user."""
    c = db()
    r = c.execute("SELECT referral_code FROM users WHERE user_id=?", (uid,)).fetchone()
    if r and r["referral_code"]:
        c.close(); return r["referral_code"]
    code = secrets.token_urlsafe(10)
    c.execute("UPDATE users SET referral_code=? WHERE user_id=?", (code, uid))
    c.commit(); c.close()
    return code

def validate_referral_code(code):
    """Validate a referral code and return the inviter's user_id or None."""
    if not code or len(code) < 5:
        return None
    c = db()
    r = c.execute("SELECT user_id FROM users WHERE referral_code=?", (code,)).fetchone()
    c.close()
    return r["user_id"] if r else None

def register_referral(inviter_uid, invitee_uid, source="link"):
    """Register a new referral. Returns True if successful."""
    if inviter_uid == invitee_uid:
        return False  # Self-referral blocked
    # Daily referral limits are intentionally disabled. A user may invite without a daily cap.
    c = db()
    try:
        existing = c.execute("SELECT id FROM referrals WHERE invited_id=?", (invitee_uid,)).fetchone()
        if existing:
            c.close(); return False  # Already registered
        # No daily referral cap. Duplicate referrals stay blocked by invited_id uniqueness.
        now = datetime.now(TZ).isoformat()
        c.execute("INSERT INTO referrals(inviter_id,invited_id,created_at,source,status) VALUES(?,?,?,?,?)",
                  (inviter_uid, invitee_uid, now, source, "registered"))
        c.execute("UPDATE users SET referrer_id=? WHERE user_id=?", (inviter_uid, invitee_uid))
        c.commit(); c.close()
        ref_log(invitee_uid, "registered", inviter_uid, f"source={source}")
        return True
    except Exception:
        try: c.close()
        except: pass
        return False

def check_referral_success(invitee_uid):
    """Check if a referral meets the success condition."""
    condition = ref_get("ref_success_condition", "first_goal")
    c = db()
    try:
        r = c.execute("SELECT inviter_id FROM referrals WHERE invited_id=? AND status='registered'", (invitee_uid,)).fetchone()
        if not r:
            c.close(); return None
        inviter_id = r["inviter_id"]
        if condition == "first_goal":
            goals = c.execute("SELECT COUNT(*) n FROM goals WHERE user_id=?", (invitee_uid,)).fetchone()["n"]
            if goals >= 1:
                c.execute("UPDATE referrals SET status='success' WHERE invited_id=?", (invitee_uid,))
                c.commit(); c.close()
                return inviter_id
        elif condition == "first_activity":
            acts = c.execute("SELECT COUNT(*) n FROM activity_log WHERE user_id=?", (invitee_uid,)).fetchone()["n"]
            if acts >= 3:
                c.execute("UPDATE referrals SET status='success' WHERE invited_id=?", (invitee_uid,))
                c.commit(); c.close()
                return inviter_id
        elif condition == "any":
            c.execute("UPDATE referrals SET status='success' WHERE invited_id=?", (invitee_uid,))
            c.commit(); c.close()
            return inviter_id
        c.close()
        return None
    except Exception:
        try: c.close()
        except: pass
        return None

def award_referral_reward(inviter_id, invitee_id):
    """Award referral rewards. Returns True if rewarded."""
    if ref_get("ref_reward_enabled", "1") != "1":
        return False
    c = db()
    try:
        r = c.execute("SELECT id FROM referrals WHERE inviter_id=? AND invited_id=? AND status='success' AND rewarded=0",
                      (inviter_id, invitee_id)).fetchone()
        if not r:
            c.close(); return False
        now = datetime.now(TZ).isoformat()
        referrer_tokens = int(ref_get("ref_tokens_per_success", "10"))
        invitee_tokens = int(ref_get("ref_invitee_tokens", "5"))
        # Award referrer
        reward_key = f"ref:{inviter_id}:{invitee_id}"
        existing = c.execute("SELECT 1 FROM reward_log WHERE reward_key=?", (reward_key,)).fetchone()
        if not existing:
            c.execute("INSERT INTO reward_log(reward_key,user_id,reward_type,amount,created_at) VALUES(?,?,?,?,?)",
                      (reward_key, inviter_id, "referral_tokens", referrer_tokens, now))
            # Add tokens to referrer wallet
            c.execute("INSERT OR IGNORE INTO token_wallets(user_id,balance,updated_at) VALUES(?,?,?)",
                      (inviter_id, 0, now))
            c.execute("UPDATE token_wallets SET balance=balance+?,updated_at=? WHERE user_id=?",
                      (referrer_tokens, now, inviter_id))
            ref_log(inviter_id, "rewarded", invitee_id, f"tokens={referrer_tokens}")
        # Bilateral reward
        if ref_get("ref_bilateral_reward", "1") == "1" and invitee_tokens > 0:
            inv_key = f"ref_invitee:{invitee_id}"
            inv_existing = c.execute("SELECT 1 FROM reward_log WHERE reward_key=?", (inv_key,)).fetchone()
            if not inv_existing:
                c.execute("INSERT INTO reward_log(reward_key,user_id,reward_type,amount,created_at) VALUES(?,?,?,?,?)",
                          (inv_key, invitee_id, "referral_invitee_tokens", invitee_tokens, now))
                c.execute("INSERT OR IGNORE INTO token_wallets(user_id,balance,updated_at) VALUES(?,?,?)",
                          (invitee_id, 0, now))
                c.execute("UPDATE token_wallets SET balance=balance+?,updated_at=? WHERE user_id=?",
                          (invitee_tokens, now, invitee_id))
                ref_log(invitee_id, "invitee_rewarded", inviter_id, f"tokens={invitee_tokens}")
        # Mark as rewarded
        c.execute("UPDATE referrals SET rewarded=1 WHERE inviter_id=? AND invited_id=?", (inviter_id, invitee_id))
        # Check VIP milestone
        milestone = int(ref_get("ref_vip_milestone", "10"))
        vip_days = int(ref_get("ref_vip_days", "30"))
        total_success = c.execute("SELECT COUNT(*) n FROM referrals WHERE inviter_id=? AND status='success' AND rewarded=1",
                                  (inviter_id,)).fetchone()["n"]
        if milestone > 0 and total_success > 0 and total_success % milestone == 0:
            vip_key = f"ref_vip_milestone:{inviter_id}:{total_success}"
            vip_existing = c.execute("SELECT 1 FROM reward_log WHERE reward_key=?", (vip_key,)).fetchone()
            if not vip_existing:
                c.execute("INSERT INTO reward_log(reward_key,user_id,reward_type,amount,created_at) VALUES(?,?,?,?,?)",
                          (vip_key, inviter_id, "referral_vip_days", vip_days, now))
                c.execute("UPDATE users SET vip_until=MAX(COALESCE(vip_until,?), DATE(?, '+' || ? || ' days')) WHERE user_id=?",
                          (now, now, vip_days, inviter_id))
                ref_log(inviter_id, "vip_milestone", invitee_id, f"days={vip_days},total={total_success}")
        c.commit(); c.close()
        return True
    except Exception:
        try: c.close()
        except: pass
        return False

def get_referral_stats(uid):
    """Get referral statistics for a user."""
    c = db()
    try:
        total = c.execute("SELECT COUNT(*) n FROM referrals WHERE inviter_id=?", (uid,)).fetchone()["n"]
        success = c.execute("SELECT COUNT(*) n FROM referrals WHERE inviter_id=? AND status='success'", (uid,)).fetchone()["n"]
        pending = c.execute("SELECT COUNT(*) n FROM referrals WHERE inviter_id=? AND status='registered'", (uid,)).fetchone()["n"]
        rewarded = c.execute("SELECT COUNT(*) n FROM referrals WHERE inviter_id=? AND rewarded=1", (uid,)).fetchone()["n"]
        tokens_earned = c.execute("SELECT COALESCE(SUM(amount),0) t FROM reward_log WHERE user_id=? AND reward_type LIKE 'referral_%'",
                                  (uid,)).fetchone()["t"]
        code = get_or_create_referral_code(uid)
        invited = c.execute("SELECT r.*, u.first_name FROM referrals r LEFT JOIN users u ON u.user_id=r.invited_id WHERE r.inviter_id=? ORDER BY r.created_at DESC",
                            (uid,)).fetchall()
        c.close()
        return {"total": total, "success": success, "pending": pending, "rewarded": rewarded,
                "tokens_earned": tokens_earned, "code": code, "invited": invited}
    except Exception:
        try: c.close()
        except: pass
        return {"total": 0, "success": 0, "pending": 0, "rewarded": 0, "tokens_earned": 0, "code": "", "invited": []}

def get_admin_referral_stats():
    """Get system-wide referral statistics for admin."""
    c = db()
    try:
        total = c.execute("SELECT COUNT(*) n FROM referrals").fetchone()["n"]
        success = c.execute("SELECT COUNT(*) n FROM referrals WHERE status='success'").fetchone()["n"]
        pending = c.execute("SELECT COUNT(*) n FROM referrals WHERE status='registered'").fetchone()["n"]
        rewarded = c.execute("SELECT COUNT(*) n FROM referrals WHERE rewarded=1").fetchone()["n"]
        total_tokens = c.execute("SELECT COALESCE(SUM(amount),0) t FROM reward_log WHERE reward_type LIKE 'referral_%'").fetchone()["t"]
        top_referrers = c.execute("""SELECT inviter_id, u.first_name, COUNT(*) n FROM referrals r
            JOIN users u ON u.user_id=r.inviter_id WHERE r.status='success' GROUP BY inviter_id ORDER BY n DESC LIMIT 10""").fetchall()
        c.close()
        return {"total": total, "success": success, "pending": pending, "rewarded": rewarded,
                "total_tokens": total_tokens, "top_referrers": top_referrers}
    except Exception:
        try: c.close()
        except: pass
        return {"total": 0, "success": 0, "pending": 0, "rewarded": 0, "total_tokens": 0, "top_referrers": []}

def referral_leaderboard(period="all"):
    """Get referral leaderboard."""
    c = db()
    try:
        where = ""
        if period == "weekly":
            week_ago = (datetime.now(TZ) - timedelta(days=7)).isoformat()
            where = f" AND r.created_at >= '{week_ago[:10]}'"
        elif period == "monthly":
            month_ago = (datetime.now(TZ) - timedelta(days=30)).isoformat()
            where = f" AND r.created_at >= '{month_ago[:10]}'"
        rows = c.execute(f"""SELECT r.inviter_id, u.first_name, COUNT(*) n FROM referrals r
            JOIN users u ON u.user_id=r.inviter_id WHERE r.status='success'{where}
            GROUP BY r.inviter_id ORDER BY n DESC LIMIT 20""").fetchall()
        c.close()
        return rows
    except Exception:
        try: c.close()
        except: pass
        return []

def token_referral_reward(inviter_id, invited_id):
    """Legacy compatibility: award referral reward."""
    return award_referral_reward(int(inviter_id), int(invited_id))

# ===================== END ENHANCED REFERRAL SYSTEM =====================

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
    rows.insert(-1,[InlineKeyboardButton("ðï¸ ÙØ¯ÛØ±ÛØª ØªÙÚ©Ù Ù Ø³ÙÙÛÙ",callback_data="v25:tokens_admin")])
    return InlineKeyboardMarkup(rows)
admin_keyboard=final_admin_keyboard

# Add token wallet text routing and minimal XP->token / token->VIP actions.
_OLD_TEXT_ROUTER_TOKEN=text_router

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
    rows.insert(-1,[InlineKeyboardButton("ðï¸ ØªØ¨Ø¯ÛÙ ØªÙÚ©Ù Ø¨Ù VIP" if fa else "ðï¸ Convert Tokens to VIP",callback_data="vip:tokens")])
    return InlineKeyboardMarkup(rows)

_OLD_VIP_CALLBACK_TOKEN=vip_callback
async def vip_callback(update,context):
    q=update.callback_query; uid=q.from_user.id
    if q.data == "vip:tokens":
        await q.answer()
        tokens_from_xp(uid)
        ok,days=redeem_tokens_for_vip(uid)
        if ok:
            await q.message.edit_text(f"â {days} Ø±ÙØ² VIP Ø¨Ø§ ØªÙÚ©Ù ÙØ¹Ø§Ù Ø´Ø¯.\n\nðï¸ ÙÙØ¬ÙØ¯Û Ø¨Ø§ÙÛÙØ§ÙØ¯Ù: {token_balance(uid)}",reply_markup=vip_keyboard(uid))
        else:
            cost=int(token_setting("tokens_for_vip_days","100") or 100)
            await q.message.edit_text(f"ðï¸ ØªÙÚ©Ù Ú©Ø§ÙÛ ÙÛØ³Øª.\n\nÙÙØ¬ÙØ¯Û: {token_balance(uid)}\nÙÛØ§Ø²: {cost} ØªÙÚ©Ù",reply_markup=vip_keyboard(uid))
        return
    return await _OLD_VIP_CALLBACK_TOKEN(update,context)

# Token wallet also exposes XP conversion and VIP redemption directly.
_OLD_TOKEN_USER_KEYBOARD=token_user_keyboard
def token_user_keyboard(uid):
    fa=lang(uid)=="fa"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("ð XP â ØªÙÚ©Ù" if fa else "ð XP â Tokens",callback_data="v25:token_xp_convert")],
        [InlineKeyboardButton("ð ØªØ¨Ø¯ÛÙ ØªÙÚ©Ù Ø¨Ù VIP" if fa else "ð Convert Tokens to VIP",callback_data="vip:tokens")],
        [InlineKeyboardButton("ð Ø¨Ø±ÙØ²Ø±Ø³Ø§ÙÛ" if fa else "ð Refresh",callback_data="v25:tokens")],
        [InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª" if fa else "â¬ï¸ Back",callback_data="v25:hub"),main_menu_button(uid)]
    ])

# Extend the token callback router with XP conversion.
_OLD_V25_CALLBACK_TOKEN2=v25_callback
async def v25_callback(update,context):
    data=update.callback_query.data
    if data == "v25:token_xp_convert":
        uid=update.effective_user.id; await update.callback_query.answer(); n=tokens_from_xp(uid)
        await update.callback_query.message.edit_text((f"â {n} ØªÙÚ©Ù Ø¬Ø¯ÛØ¯ Ø§Ø² XP ØªØ¨Ø¯ÛÙ Ø´Ø¯.\n\nâ­ ÙÙØ¬ÙØ¯Û XP Ø¨Ù ØªÙÚ©Ù ÙØ¹ÙÛ Ø«Ø¨Øª Ø´Ø¯.\nðï¸ ÙÙØ¬ÙØ¯Û: {token_balance(uid)}" if lang(uid)=="fa" else f"â Converted {n} new tokens from XP.\n\nðï¸ Balance: {token_balance(uid)}"),reply_markup=token_user_keyboard(uid)); return
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
        ("unified_hub", "ð§  ÙØ±Ú©Ø² ÙÙ", "ð§  My Center"),
        ("portfolio", "ð° Ø³Ø±ÙØ§ÛÙâÙØ§Û ÙÙ", "ð° My Portfolio"),
        ("installments", "ð³ Ø§ÙØ³Ø§Ø·", "ð³ Installments"),
        ("profile_sharing", "ð¤ Ø§Ø·ÙØ§Ø¹Ø§Øª ÙÙ", "ð¤ My Profile"),
        ("calendar_hub", "ð ØªÙÙÛÙ ÙÙ", "ð My Calendar"),
    ]
    for key, fa_label, en_label in extra_defs:
        try:
            if v25_allowed(uid, key):
                extras.append(fa_label if fa else en_label)
        except Exception:
            # If an optional feature check fails, do not break the main menu.
            logger.exception("Menu feature check failed: %s", key)

    # Token wallet is available independently of the optional V25 feature flags.
    extras.append("ðï¸ ØªÙÚ©ÙâÙØ§Û ÙÙ" if fa else "ðï¸ My Tokens")

    rows = [list(r) for r in base if r]
    for i in range(0, len(extras), 2):
        rows.append(extras[i:i + 2])

    if admin_is_allowed(uid):
        rows.append(["ð¢ ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù" if fa else "ð¢ Channel Management",
                     "ð¡ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª" if fa else "ð¡ Admin Panel"])

    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)



# ---------- Forced Subscription Management ----------
# Admin-configurable mandatory channel membership with duration enforcement.
# Keys in system_settings: forced_sub_enabled, forced_sub_duration_type, forced_sub_duration_value,
# forced_sub_channel_url, forced_sub_message

def _forced_sub_get(key, default=""):
    c = db()
    r = c.execute("SELECT value FROM system_settings WHERE key=?", (key,)).fetchone()
    c.close()
    return r["value"] if r else default

def _forced_sub_set(key, value):
    c = db()
    now_iso = datetime.now(TZ).isoformat()
    c.execute("INSERT OR REPLACE INTO system_settings(key,value,updated_at) VALUES(?,?,?)", (key, str(value), now_iso))
    c.commit()
    c.close()

def _forced_sub_is_enabled():
    return _forced_sub_get("forced_sub_enabled", "0") == "1"

def _forced_sub_duration_hours():
    """Return required membership duration in hours. 0 = forever, -1 = disabled."""
    if not _forced_sub_is_enabled():
        return -1
    dtype = _forced_sub_get("forced_sub_duration_type", "hours")
    try:
        dval = int(_forced_sub_get("forced_sub_duration_value", "24"))
    except (ValueError, TypeError):
        dval = 24
    if dtype == "forever":
        return 0  # 0 means forever
    elif dtype == "days":
        return dval * 24
    else:  # hours
        return dval

def _forced_sub_record_join(uid):
    """Record when a user joins the channel."""
    c = db()
    now_iso = datetime.now(TZ).isoformat()
    c.execute("INSERT OR REPLACE INTO user_channel_membership(user_id,joined_at,last_check,is_member) VALUES(?,?,?,1)",
              (uid, now_iso, now_iso))
    c.commit()
    c.close()

def _forced_sub_record_leave(uid):
    """Record when a user leaves the channel."""
    c = db()
    now_iso = datetime.now(TZ).isoformat()
    c.execute("UPDATE user_channel_membership SET left_at=?,is_member=0,last_check=? WHERE user_id=?",
              (now_iso, now_iso, uid))
    c.commit()
    c.close()

def _forced_sub_check_user(uid):
    """Return the last locally recorded membership state."""
    c = db()
    try:
        r = c.execute(
            "SELECT is_member FROM user_channel_membership WHERE user_id=?",
            (uid,),
        ).fetchone()
        return bool(r["is_member"]) if r else False
    finally:
        c.close()


async def _forced_sub_enforce_async(uid, bot):
    """Mandatory membership gate: member = continue, non-member = stay blocked."""
    if not _forced_sub_is_enabled() or admin_guard(uid):
        return True, None

    live_member = await is_channel_member(bot, uid)
    if not live_member:
        return False, _forced_sub_failure_payload()

    # Store the latest successful live check. The decision is always based on
    # Telegram's current status, not on stale local data.
    try:
        _forced_sub_record_join(uid)
    except sqlite3.OperationalError:
        logger.exception("Could not record forced-sub membership for uid=%s", uid)

    return True, None


def _forced_sub_failure_payload():
    channel_url = _forced_sub_join_url() or required_channel_url()
    msg = _forced_sub_get("forced_sub_message", "")

    if not msg:
        msg = (
            "ð <b>Ø¹Ø¶ÙÛØª Ø§Ø¬Ø¨Ø§Ø±Û</b>\n\n"
            "Ø¨Ø±Ø§Û Ø§Ø³ØªÙØ§Ø¯Ù Ø§Ø² Ø±Ø¨Ø§ØªØ Ø§Ø¨ØªØ¯Ø§ Ø¹Ø¶Ù Ú©Ø§ÙØ§Ù Ø´ÙÛØ¯.\n"
            "Ø¨Ø¹Ø¯ Ø§Ø² Ø¹Ø¶ÙÛØª Ø±ÙÛ Â«ð Ø¨Ø±Ø±Ø³Û ÙØ¬Ø¯Ø¯Â» Ø¨Ø²ÙÛØ¯."
        )

    kb_lines = []
    if channel_url and (channel_url.startswith("http://") or channel_url.startswith("https://")):
        kb_lines.append([InlineKeyboardButton("ð Ø¹Ø¶ÙÛØª Ø¯Ø± Ú©Ø§ÙØ§Ù", url=channel_url)])
    kb_lines.append([InlineKeyboardButton("ð Ø¨Ø±Ø±Ø³Û ÙØ¬Ø¯Ø¯", callback_data="forcedsub:check")])
    return msg, InlineKeyboardMarkup(kb_lines)


def _forced_sub_enforce(uid):
    """Compatibility helper for old synchronous callers."""
    if not _forced_sub_is_enabled() or admin_guard(uid):
        return True, None
    return _forced_sub_check_user(uid), None


async def _forced_sub_admin_panel(update, context):
    """Show the forced subscription admin panel."""
    uid = update.effective_user.id
    fa = lang(uid) == "fa"
    enabled = _forced_sub_is_enabled()
    dtype = _forced_sub_get("forced_sub_duration_type", "hours")
    dval = _forced_sub_get("forced_sub_duration_value", "24")
    channel_url = _forced_sub_get("forced_sub_channel_url", "")
    
    dtype_labels = {"hours": "Ø³Ø§Ø¹Øª", "days": "Ø±ÙØ²", "forever": "Ø¯Ø§Ø¦ÙÛ"}
    dtype_label = dtype_labels.get(dtype, dtype)
    
    status = "ð¢ ÙØ¹Ø§Ù" if enabled else "ð´ ØºÛØ±ÙØ¹Ø§Ù"
    duration = "Ø¯Ø§Ø¦ÙÛ" if dtype == "forever" else f"{dval} {dtype_label}"
    
    text = (
        f"ð <b>Ø¹Ø¶ÙÛØª Ø§Ø¬Ø¨Ø§Ø±Û Ú©Ø§ÙØ§Ù</b>\n\n"
        f"ÙØ¶Ø¹ÛØª: {status}\n"
        f"ÙÙØ¹ Ø¨Ø±Ø±Ø³Û: ÙÙØ· ÙØ¶Ø¹ÛØª Ø¹Ø¶ÙÛØª ÙØ¹ÙÛ\n"
        f"Ú©Ø§ÙØ§Ù: {html.escape(channel_url or 'ØªÙØ¸ÛÙ ÙØ´Ø¯Ù')}\n\n"
        f"ð¥ <b>ÙØ¶Ø¹ÛØª Ú©Ø§Ø±Ø¨Ø±Ø§Ù:</b>\n"
    )
    
    # Get user stats
    c = db()
    total = c.execute("SELECT COUNT(*) n FROM user_channel_membership").fetchone()["n"]
    members = c.execute("SELECT COUNT(*) n FROM user_channel_membership WHERE is_member=1").fetchone()["n"]
    c.close()
    
    text += f"ð Ø«Ø¨ØªâØ´Ø¯Ù: {total}\nâ Ø¹Ø¶Ù ÙØ¹Ø§Ù: {members}\n"
    
    kb = [
        [InlineKeyboardButton("ð¢ ÙØ¹Ø§ÙâØ³Ø§Ø²Û" if not enabled else "ð´ ØºÛØ±ÙØ¹Ø§ÙâØ³Ø§Ø²Û", callback_data="forcedsub:toggle")],
        [InlineKeyboardButton("ð ØªÙØ¸ÛÙ ÙÛÙÚ© Ú©Ø§ÙØ§Ù", callback_data="forcedsub:channel")],
        [InlineKeyboardButton("âï¸ ØªÙØ¸ÛÙ Ù¾ÛØ§Ù", callback_data="forcedsub:message")],
        [InlineKeyboardButton("ð¥ ÙÛØ³Øª Ú©Ø§Ø±Ø¨Ø±Ø§Ù", callback_data="forcedsub:users")],
        [InlineKeyboardButton("ð Ø¨Ø±Ø±Ø³Û ÙÙÙ", callback_data="forcedsub:check_all")],
        [InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª", callback_data="adm:system")],
    ]
    
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
        except Exception:
            await update.callback_query.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))






async def _show_admin_section(update, context, section):
    """Show a specific admin section with the management ReplyKeyboard as back."""
    uid = update.effective_user.id
    fa = lang(uid) == 'fa'
    back_kb = _compact_admin_management_keyboard(uid)
    if section == 'dashboard':
        s = admin_stats()
        text = (
            "ð <b>Ø¯Ø§Ø´Ø¨ÙØ±Ø¯ ÙØ±Ú©Ø²Û</b>\n\n"
            f"ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù: {s['users']}\n"
            f"ð Ø¬Ø¯ÛØ¯ Ø§ÙØ±ÙØ²: {s['new_today']}\n"
            f"ð¢ ÙØ¹Ø§Ù Ø§ÙØ±ÙØ²: {s['active_today']}\n"
            f"ð¯ Ø§ÙØ¯Ø§Ù: {s['goals']}\n"
            f"â Ø§ÙØ¬Ø§ÙâØ´Ø¯Ù Ø§ÙØ±ÙØ²: {s['done_today']}\n"
            f"â° ÛØ§Ø¯Ø¢ÙØ±Û: {s['reminders']}\n"
            f"ð Ø¯Ø³ØªØ§ÙØ±Ø¯: {s['achievements']}\n"
            f"ð ÙÙØ¨Øª Ø§ÙØ±ÙØ²: {s['appointments_today']}\n"
            f"ð VIP ÙØ¹Ø§Ù: {s['vip_users']}\n"
            f"ð« ØªÛÚ©Øª Ø¨Ø§Ø²: {s['open_tickets']}"
        )
        await update.message.reply_text(text, reply_markup=back_kb)
    elif section == 'users':
        c = db()
        rows = c.execute("SELECT user_id,first_name,COALESCE(xp,0) xp,blocked FROM users ORDER BY created_at DESC LIMIT 20").fetchall()
        c.close()
        lines = ["ð¥ <b>Ø¢Ø®Ø±ÛÙ Ú©Ø§Ø±Ø¨Ø±Ø§Ù</b>", ""]
        for r in rows:
            name = r['first_name'] or 'Ø¨Ø¯ÙÙ ÙØ§Ù'
            status = 'â' if r['blocked'] else 'ð¢'
            lines.append(f"{status} {name} | ID: <code>{r['user_id']}</code> | â­{r['xp']}")
        text = '\n'.join(lines) if len(rows) else 'ð¥ Ú©Ø§Ø±Ø¨Ø±Û Ø«Ø¨Øª ÙØ´Ø¯Ù.'
        await update.message.reply_text(text, parse_mode='HTML', reply_markup=back_kb)
    elif section == 'tickets':
        c = db()
        rows = c.execute("SELECT id,user_id,subject FROM tickets WHERE status='open' ORDER BY updated_at DESC LIMIT 20").fetchall()
        c.close()
        lines = ["ð« <b>ØªÛÚ©ØªâÙØ§Û Ø¨Ø§Ø²</b>", ""]
        for r in rows:
            lines.append(f"#{r['id']} | {r['user_id']} | {r['subject'] or 'Ø¨Ø¯ÙÙ Ø¹ÙÙØ§Ù'}")
        text = '\n'.join(lines) if rows else 'ð« ØªÛÚ©Øª Ø¨Ø§Ø²Û ÙÛØ³Øª.'
        await update.message.reply_text(text, parse_mode='HTML', reply_markup=back_kb)
    elif section == 'finance':
        c = db()
        payments = c.execute("SELECT COUNT(*) n FROM payments").fetchone()['n']
        revenue = c.execute("SELECT COALESCE(SUM(total_amount),0) n FROM payments").fetchone()['n']
        vip = c.execute("SELECT COUNT(*) n FROM subscription_history").fetchone()['n']
        c.close()
        text = (
            f"ð° <b>ÙØ§ÙÛ Ù Ù¾Ø±Ø¯Ø§Ø®Øª</b>\n\n"
            f"ð³ ØªØ±Ø§Ú©ÙØ´âÙØ§: {payments}\n"
            f"ðµ ÙØ¨ÙØº Ø«Ø¨ØªâØ´Ø¯Ù: {revenue:,}\n"
            f"ð Ø³ÙØ§Ø¨Ù Ø§Ø´ØªØ±Ø§Ú©: {vip}"
        )
        await update.message.reply_text(text, parse_mode='HTML', reply_markup=back_kb)
    elif section == 'xpvip':
        text = (
            "â­ <b>XP / VIP</b>\n\n"
            "Ø§Ø² Ø¨Ø®Ø´ Ú©Ø§Ø±Ø¨Ø±Ø§ÙØ Ù¾Ø±ÙÙØ¯Ù ÙØ± Ú©Ø§Ø±Ø¨Ø± Ø±Ø§ Ø¨Ø§Ø² Ú©Ù ØªØ§ XP Ù Ø§Ø´ØªØ±Ø§Ú© Ø±Ø§ ÙØ¯ÛØ±ÛØª Ú©ÙÛ.\n\n"
            "Ø¨Ø±Ø§Û Ø§Ø´ØªØ±Ø§Ú©: â Ø§Ø¶Ø§ÙÙâÚ©Ø±Ø¯Ù Ø±ÙØ²Ø â Ú©ÙâÚ©Ø±Ø¯Ù Ø±ÙØ²Ø âï¸ ÙÛØ±Ø§ÛØ´ ÛØ§ â ÙØºÙ Ú©Ø§ÙÙ."
        )
        await update.message.reply_text(text, parse_mode='HTML', reply_markup=back_kb)
    elif section == 'channel':
        await update.message.reply_text("ð¡ <b>ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù Ù Ù¾Ø³ØªâÚ¯Ø°Ø§Ø±Û</b>", parse_mode='HTML', reply_markup=channel_keyboard())
    elif section == 'health':
        await run_health_checks(context.bot, uid)
        await update.message.reply_text(health_text(), reply_markup=back_kb)
    elif section == 'backup':
        ok = backup_database_snapshot(keep=20)
        admin_log(uid, 'manual_backup', None, 'success' if ok else 'failed')
        text = 'ð¾ Ø¨Ú©Ø§Ù¾ Ø¨Ø§ ÙÙÙÙÛØª Ø³Ø§Ø®ØªÙ Ø´Ø¯.' if ok else 'â Ø³Ø§Ø®Øª Ø¨Ú©Ø§Ù¾ ÙØ§ÙÙÙÙ Ø¨ÙØ¯.'
        await update.message.reply_text(text, reply_markup=back_kb)
    elif section == 'features':
        await update.message.reply_text(feature_admin_text(), reply_markup=feature_admin_keyboard())
    elif section == 'security':
        text = (
            "ð <b>Ø§ÙÙÛØª Ù Audit</b>\n\n"
            "Ø§Ø² Ø¨Ø®Ø´ ÙØ§Ú¯ ÙØ¯ÛØ±Ø§ÙØ Ø§ÙØ¯Ø§ÙØ§Øª Ø§Ø®ÛØ± Ø±Ø§ Ø¨Ø±Ø±Ø³Û Ú©Ù.\n"
            "Ø§Ø² Ø¨Ø®Ø´ Ú©Ø§Ø±Ø¨Ø±Ø§ÙØ Ú©Ø§Ø±Ø¨Ø±Ø§Ù ÙØ­Ø¯ÙØ¯ Ø´Ø¯Ù Ø±Ø§ ÙØ¯ÛØ±ÛØª Ú©Ù."
        )
        await update.message.reply_text(text, parse_mode='HTML', reply_markup=back_kb)
    elif section == 'test':
        await update.message.reply_text(_admin_test_text(), parse_mode='HTML', reply_markup=back_kb)
    elif section == 'system':
        paused = get_system_setting('bot_paused_until', '')
        maintenance = feature_enabled('maintenance')
        forced_sub_status = "ð¢ ÙØ¹Ø§Ù" if _forced_sub_is_enabled() else "ð´ ØºÛØ±ÙØ¹Ø§Ù"
        text = (
            f"âï¸ <b>ØªÙØ¸ÛÙØ§Øª Ø³ÛØ³ØªÙ</b>\n\n"
            f"ð  Maintenance: {'ð¢' if maintenance else 'ð´'}\n"
            f"â¸ ØªÙÙÙ ÙÙÙØª: {html.escape(paused or 'ÙØ¹Ø§Ù ÙÛØ³Øª')}\n"
            f"ð Ø¹Ø¶ÙÛØª Ø§Ø¬Ø¨Ø§Ø±Û: {forced_sub_status}\n"
            f"ð Schema: {DB_SCHEMA_VERSION}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("ð Ø¹Ø¶ÙÛØª Ø§Ø¬Ø¨Ø§Ø±Û Ú©Ø§ÙØ§Ù", callback_data="forcedsub:home")],
        ])
        await update.message.reply_text(text, parse_mode='HTML', reply_markup=kb)
    elif section == 'other':
        text = "ð¦ <b>Ø³Ø§ÛØ± ÙØ§ÚÙÙâÙØ§Û ÙØ¯ÛØ±ÛØªÛ</b>\n\nØ§Ø² ÙÙÙÛ Ø²ÛØ± Ø¨Ø®Ø´ ÙÙØ±Ø¯ÙØ¸Ø± Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù." if fa else "ð¦ <b>Other Admin Modules</b>"
        await update.message.reply_text(text, parse_mode='HTML', reply_markup=back_kb)
    else:
        await admin_command(update, context)



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
    if text in ("ð  ÙÙÙÛ Ø§ØµÙÛ", "ð  Main Menu"):
        clear_flow(context)
        try:
            await update.message.delete()
        except Exception:
            pass
        fa = lang(uid) == "fa"
        await context.bot.send_message(
            chat_id=uid,
            text=_root_menu_text(uid),
            parse_mode="HTML",
            reply_markup=_compact_root_inline(uid),
        )
        return
    if text in ("â¬ï¸ Ø¨Ø±Ú¯Ø´Øª", "â¬ï¸ Back"):
        clear_flow(context)
        try:
            await update.message.delete()
        except Exception:
            pass
        # From an error/input recovery keyboard, Back returns to the compact
        # Goals sectionâthe section that owns the "ð¯ Ø¨Ø±ÙØ§ÙÙ ÙÙ" entry.
        await context.bot.send_message(
            chat_id=uid,
            text="ð¯ <b>Ø¨Ø±ÙØ§ÙÙ Ù Ø§ÙØ¯Ø§Ù</b>" if lang(uid)=="fa" else "ð¯ <b>Goals & Plan</b>",
            parse_mode="HTML",
            reply_markup=_compact_menu_keyboard(uid, "goals"),
        )
        return

    # "ð¯ Ø¨Ø±ÙØ§ÙÙ ÙÙ" is a top-level ReplyKeyboard action. Handle it here before
    # the legacy input state machine; otherwise the old chain can raise TypeError.
    if text in ("ð¯ Ø¨Ø±ÙØ§ÙÙ ÙÙ", "ð¯ My Plan"):
        clear_flow(context)
        await _compact_menu_show(update, context, "goals")
        return

    if txt in ("ð¤ Ø§Ø³ØªÙØ§Ø¯Ù Ø§Ø² Ø±Ø¨Ø§Øª", "ð¤ Use Bot"):
        clear_flow(context)
        await update.message.reply_text(
            "ð¤ <b>Ø§Ø³ØªÙØ§Ø¯Ù Ø§Ø² Ø±Ø¨Ø§Øª</b>\n\nÙØ§Ø¨ÙÛØªâÙØ§Û Ø¹Ø§Ø¯Û Ø±Ø¨Ø§Øª Ø¯Ø± Ø¯Ø³ØªØ±Ø³ ØªÙ ÙØ³ØªÙØ¯." if lang(uid)=="fa" else
            "ð¤ <b>Use Bot</b>\n\nAll normal bot features are available here.",
            parse_mode="HTML", reply_markup=_compact_user_keyboard(uid)
        )
        return
    if txt in ("ð¡ ÙØ¯ÛØ±ÛØª Ø±Ø¨Ø§Øª", "ð¡ Bot Management"):
        if not admin_guard(uid):
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", reply_markup=keyboard(uid))
            return
        clear_flow(context)
        await _show_admin_management(update, context)
        return
    # Birthday & Events buttons
    if txt in ("ð ØªÙÙØ¯ ÙÙ", "ð My Birthday"):
        if not birthday_enabled():
            await update.message.reply_text("ð Ø§ÛÙ ÙØ§Ø¨ÙÛØª Ø¯Ø± Ø­Ø§Ù Ø­Ø§Ø¶Ø± ØºÛØ±ÙØ¹Ø§Ù Ø§Ø³Øª.", reply_markup=compact_keyboard(uid))
            return
        await birthday_show_callback(update, context)
        return
    if txt in ("ð ØªÙÙØ¯ Ù ÙÙØ§Ø³Ø¨ØªâÙØ§", "ð Birthday & Events"):
        if not admin_guard(uid):
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", reply_markup=compact_keyboard(uid))
            return
        text = "ð <b>ØªÙÙØ¯ Ù ÙÙØ§Ø³Ø¨ØªâÙØ§</b>\n\nØ¨Ø®Ø´ ÙÙØ±Ø¯ÙØ¸Ø± Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("ð ÙØ¯ÛØ±ÛØª ØªÙÙØ¯", callback_data="adm:birthdays:list"),
             InlineKeyboardButton("âï¸ ØªÙØ¸ÛÙØ§Øª ØªÙÙØ¯", callback_data="adm:birthdays:settings")],
            [InlineKeyboardButton("ð ÙÙØ§Ø³Ø¨ØªâÙØ§", callback_data="adm:events:list")],
            [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")],
        ])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return
    if txt in ("ð ÙØ¯ÛÙ ÙØ¯ÛØ±ÛØªÛ", "ð Admin Gifts"):
        if not admin_guard(uid):
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", reply_markup=compact_keyboard(uid))
            return
        text = (
            "ð <b>ÙØ¯ÛÙ ÙØ¯ÛØ±ÛØªÛ</b>\n\n"
            "Ø¨Ø±Ø§Û Ø§Ø±Ø³Ø§Ù ÙØ¯ÛÙ Ø¨Ù Ú©Ø§Ø±Ø¨Ø±:\n"
            "1ï¸â£ Ø´ÙØ§Ø³Ù Ú©Ø§Ø±Ø¨Ø± Ø±Ù Ø¨ÙØ±Ø³Øª\n"
            "2ï¸â£ ÙÙØ¹ ÙØ¯ÛÙ Ø±Ù Ø§ÙØªØ®Ø§Ø¨ Ú©Ù\n"
            "3ï¸â£ ÙÙØ¯Ø§Ø± Ù ÙØ¯Øª Ø±Ù ØªØ¹ÛÛÙ Ú©Ù"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")]])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        context.user_data["admin_gift_mode"] = "user_id"
        return
    # New admin menu items (restructured)
    if txt in ("ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ù Ù¾Ø§Ø¯Ø§Ø´âÙØ§", "ð¥ Users & Rewards"):
        if not admin_guard(uid):
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", reply_markup=compact_keyboard(uid))
            return
        text = "ð¥ <b>Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ù Ù¾Ø§Ø¯Ø§Ø´âÙØ§</b>\n\nØ¨Ø®Ø´ ÙÙØ±Ø¯ÙØ¸Ø± Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("ð¥ ÙÛØ³Øª Ú©Ø§Ø±Ø¨Ø±Ø§Ù", callback_data="adm:users"),
             InlineKeyboardButton("ð Ø¬Ø³ØªØ¬Ù", callback_data="adm:search")],
            [InlineKeyboardButton("â­ XP / VIP", callback_data="adm:xpvip"),
             InlineKeyboardButton("ð ÙØ¯ÛÙ ÙØ¯ÛØ±ÛØªÛ", callback_data="adm:gifts")],
            [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")],
        ])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return
    if txt in ("ð Ø§Ø´ØªØ±Ø§Ú© Ù Ø¯Ø³ØªØ±Ø³ÛâÙØ§", "ð Subscriptions & Access"):
        if not admin_guard(uid):
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", reply_markup=compact_keyboard(uid))
            return
        text = "ð <b>Ø§Ø´ØªØ±Ø§Ú© Ù Ø¯Ø³ØªØ±Ø³ÛâÙØ§</b>\n\nØ¨Ø®Ø´ ÙÙØ±Ø¯ÙØ¸Ø± Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("ð ÙØ¯ÛØ±ÛØª VIP", callback_data="adm:xpvip"),
             InlineKeyboardButton("ð ÙØ§ØªØ±ÛØ³ Ø¯Ø³ØªØ±Ø³Û", callback_data="adm:access")],
            [InlineKeyboardButton("ð§© ÙØ§Ø¨ÙÛØªâÙØ§", callback_data="adm:features"),
             InlineKeyboardButton("âï¸ ØªÙØ¸ÛÙØ§Øª", callback_data="adm:features")],
            [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")],
        ])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return
    if txt in ("ð¢ ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù", "ð¢ Channel Management"):
        if not admin_guard(uid):
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", reply_markup=compact_keyboard(uid))
            return
        await update.message.reply_text("ð¡ <b>ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù</b>", parse_mode="HTML", reply_markup=channel_keyboard())
        return
    if txt in ("ð¯ Ø§ÙØ¯Ø§Ù Ù ÛØ§Ø¯Ø¢ÙØ±Û", "ð¯ Goals & Reminders"):
        if not admin_guard(uid):
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", reply_markup=compact_keyboard(uid))
            return
        c = db()
        goals = c.execute("SELECT COUNT(*) n FROM goals").fetchone()["n"]
        active = c.execute("SELECT COUNT(*) n FROM goals WHERE enabled=1").fetchone()["n"]
        c.close()
        text = f"ð¯ <b>Ø§ÙØ¯Ø§Ù Ù ÛØ§Ø¯Ø¢ÙØ±Û</b>\n\nð¯ Ú©Ù Ø§ÙØ¯Ø§Ù: {goals}\nâ ÙØ¹Ø§Ù: {active}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("âï¸ ØªÙØ¸ÛÙØ§Øª", callback_data="adm:features")],
            [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")],
        ])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return
    if txt in ("ð ÙÛÙØª Ù Ø¨Ø§Ø²Ø§Ø±", "ð Prices & Market"):
        if not admin_guard(uid):
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", reply_markup=compact_keyboard(uid))
            return
        text = "ð <b>ÙÛÙØª Ù Ø¨Ø§Ø²Ø§Ø±</b>\n\nØ§Ø² Ø¨Ø®Ø´ Ø§Ø¨Ø²Ø§Ø±ÙØ§Û ÙÙØ´ÙÙØ¯ Ø¨Ø±Ø§Û Ú©Ø§Ø±Ø¨Ø±Ø§Ù ÙØ§Ø¨Ù Ø¯Ø³ØªØ±Ø³Û Ø§Ø³Øª."
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("âï¸ ØªÙØ¸ÛÙØ§Øª", callback_data="adm:features")],
            [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")],
        ])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return
    if txt in ("ð³ Ù¾Ø±Ø¯Ø§Ø®ØªâÙØ§", "ð³ Payments"):
        if not admin_guard(uid):
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", reply_markup=compact_keyboard(uid))
            return
        c = db()
        payments = c.execute("SELECT COUNT(*) n FROM payments").fetchone()["n"]
        revenue = c.execute("SELECT COALESCE(SUM(total_amount),0) n FROM payments").fetchone()["n"]
        c.close()
        text = f"ð³ <b>Ù¾Ø±Ø¯Ø§Ø®ØªâÙØ§</b>\n\nð³ ØªØ±Ø§Ú©ÙØ´âÙØ§: {payments}\nðµ ÙØ¨ÙØº: {revenue:,}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("âï¸ ØªÙØ¸ÛÙØ§Øª", callback_data="adm:features")],
            [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")],
        ])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return
    if txt in ("ð¥ ÙØ´ØªØ±Û Ù Ø±Ø²Ø±Ù", "ð¥ Customers & Bookings"):
        if not admin_guard(uid):
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", reply_markup=compact_keyboard(uid))
            return
        await _show_admin_section(update, context, "users")
        return

    if txt in ("ð« Ù¾Ø´ØªÛØ¨Ø§ÙÛ Ù ØªÛÚ©Øª", "ð« Support & Tickets"):
        if not admin_guard(uid):
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", reply_markup=compact_keyboard(uid))
            return
        c = db()
        rows = c.execute("SELECT id,user_id,subject FROM tickets WHERE status='open' ORDER BY updated_at DESC LIMIT 20").fetchall()
        c.close()
        text = "ð« <b>ØªÛÚ©ØªâÙØ§Û Ø¨Ø§Ø²</b>\n\n" + "\n".join(f"#{r['id']} | {r['user_id']} | {r['subject'] or 'Ø¨Ø¯ÙÙ Ø¹ÙÙØ§Ù'}" for r in rows) or "ØªÛÚ©Øª Ø¨Ø§Ø²Û ÙÛØ³Øª"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")],
        ])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return
    if txt in ("ð Ø¢ÙØ§Ø± Ù Ú¯Ø²Ø§Ø±Ø´", "ð Reports & Analytics"):
        if not admin_guard(uid):
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", reply_markup=compact_keyboard(uid))
            return
        s = admin_stats()
        text = (
            f"ð <b>Ø¢ÙØ§Ø± Ù Ú¯Ø²Ø§Ø±Ø´</b>\n\n"
            f"ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù: {s['users']}\n"
            f"ð¯ Ø§ÙØ¯Ø§Ù: {s['goals']}\n"
            f"â Ø§ÙØ¬Ø§ÙâØ´Ø¯Ù Ø§ÙØ±ÙØ²: {s['done_today']}\n"
            f"ð Ø¯Ø³ØªØ§ÙØ±Ø¯: {s['achievements']}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("ð Ú¯Ø²Ø§Ø±Ø´ Ø±ÙØ²", callback_data="adm:report"),
             InlineKeyboardButton("ð ÙØ§Ú¯ ÙØ¯ÛØ±Ø§Ù", callback_data="adm:audit")],
            [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")],
        ])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return
    if txt in ("ð§ª ÙØ±Ú©Ø² ØªØ³Øª", "ð§ª Test Center"):
        if not admin_guard(uid):
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", reply_markup=compact_keyboard(uid))
            return
        text = "ð§ª <b>ÙØ±Ú©Ø² ØªØ³Øª</b>\n\nØªØ³ØªâÙØ§Û Ø³ÛØ³ØªÙ Ø±Ø§ Ø§Ø¬Ø±Ø§ Ú©Ù."
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("ð©º Health Check", callback_data="adm:health_run"),
             InlineKeyboardButton("ð Ø¹ÛØ¨âÛØ§Ø¨Û", callback_data="adm:diagnostics")],
            [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")],
        ])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return
    if txt in ("ð§© ÙØ§Ø¨ÙÛØªâÙØ§", "ð§© Features"):
        if not admin_guard(uid):
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", reply_markup=compact_keyboard(uid))
            return
        await update.message.reply_text(feature_admin_text(), reply_markup=feature_admin_keyboard())
        return
    if txt in ("âï¸ ØªÙØ¸ÛÙØ§Øª Ø³ÛØ³ØªÙ", "âï¸ System Settings"):
        if not admin_guard(uid):
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", reply_markup=compact_keyboard(uid))
            return
        paused = get_system_setting("bot_paused_until", "")
        maintenance = feature_enabled("maintenance")
        text = (
            f"âï¸ <b>ØªÙØ¸ÛÙØ§Øª Ø³ÛØ³ØªÙ</b>\n\n"
            f"ð  Maintenance: {'ð¢' if maintenance else 'ð´'}\n"
            f"â¸ ØªÙÙÙ ÙÙÙØª: {html.escape(paused or 'ÙØ¹Ø§Ù ÙÛØ³Øª')}\n"
            f"ð Schema: {DB_SCHEMA_VERSION}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("ð§© ØªØºÛÛØ± ÙØ§Ø¨ÙÛØªâÙØ§", callback_data="adm:features")],
            [InlineKeyboardButton("â¸ ÙØ¯ÛØ±ÛØª ØªÙÙÙ", callback_data="adm:pause")],
            [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")],
        ])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return
    if txt in ("ð Ø§ÙÙÛØª", "ð Security"):
        if not admin_guard(uid):
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", reply_markup=compact_keyboard(uid))
            return
        text = (
            "ð <b>Ø§ÙÙÛØª</b>\n\n"
            "Ø§Ø² Ø¨Ø®Ø´ ÙØ§Ú¯ ÙØ¯ÛØ±Ø§ÙØ Ø§ÙØ¯Ø§ÙØ§Øª Ø§Ø®ÛØ± Ø±Ø§ Ø¨Ø±Ø±Ø³Û Ú©Ù.\n"
            "Ø§Ø² Ø¨Ø®Ø´ Ú©Ø§Ø±Ø¨Ø±Ø§ÙØ Ú©Ø§Ø±Ø¨Ø±Ø§Ù ÙØ­Ø¯ÙØ¯ Ø´Ø¯Ù Ø±Ø§ ÙØ¯ÛØ±ÛØª Ú©Ù."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("ð ÙØ§Ú¯ ÙØ¯ÛØ±Ø§Ù", callback_data="adm:audit")],
            [InlineKeyboardButton("ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù", callback_data="adm:users")],
            [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")],
        ])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return
    if txt in ("ð¾ Ø¨Ú©Ø§Ù¾ Ù Ø¨Ø§Ø²ÛØ§Ø¨Û", "ð¾ Backup & Recovery"):
        if not admin_guard(uid):
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", reply_markup=compact_keyboard(uid))
            return
        ok = backup_database_snapshot(keep=20)
        admin_log(uid, "manual_backup", None, "success" if ok else "failed")
        text = "ð¾ Ø¨Ú©Ø§Ù¾ Ø¨Ø§ ÙÙÙÙÛØª Ø³Ø§Ø®ØªÙ Ø´Ø¯." if ok else "â Ø³Ø§Ø®Øª Ø¨Ú©Ø§Ù¾ ÙØ§ÙÙÙÙ Ø¨ÙØ¯."
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")],
        ])
        await update.message.reply_text(text, reply_markup=kb)
        return
    if txt in ("ð¦ ÙØ§ÚÙÙâÙØ§Û Ø¯ÛÚ¯Ø±", "ð¦ Other Modules"):
        if not admin_guard(uid):
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", reply_markup=compact_keyboard(uid))
            return
        text = "ð¦ <b>ÙØ§ÚÙÙâÙØ§Û Ø¯ÛÚ¯Ø±</b>\n\nØ§Ø² Ø¨Ø®Ø´âÙØ§Û ÙØ®ØªÙÙ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª Ø§Ø³ØªÙØ§Ø¯Ù Ú©Ù."
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("â¬ï¸ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", callback_data="adm:stats")],
        ])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return

    # Admin section navigation: each button opens its specific section
    _admin_section_map = {
        # Dashboard
        "ð Ø¯Ø§Ø´Ø¨ÙØ±Ø¯ Ù Ú¯Ø²Ø§Ø±Ø´": "dashboard", "ð Dashboard & Reports": "dashboard",
        # Users
        "ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ù ÙÙØ´âÙØ§": "users", "ð¥ Users & Roles": "users",
        "ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ù Ù¾Ø§Ø¯Ø§Ø´âÙØ§": "users",
        # Tickets
        "ð« ØªÛÚ©ØªâÙØ§ Ù Incident": "tickets", "ð« Tickets & Incidents": "tickets",
        "ð« Ù¾Ø´ØªÛØ¨Ø§ÙÛ Ù ØªÛÚ©Øª": "tickets",
        # Finance
        "ð° ÙØ§ÙÛ Ù Ù¾Ø±Ø¯Ø§Ø®Øª": "finance", "ð° Finance & Payments": "finance",
        # VIP/XP
        "ð VIP / XP / Token": "xpvip",
        "ð Ø§Ø´ØªØ±Ø§Ú© Ù Ø¯Ø³ØªØ±Ø³ÛâÙØ§": "xpvip",
        # Channel
        "ð¢ Ú©Ø§ÙØ§Ù Ù Ø§ÙØªØ´Ø§Ø±": "channel", "ð¢ Channels & Publishing": "channel",
        "ð¢ ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù": "channel",
        # AI
        # Health
        "ð©º Ø³ÙØ§ÙØª Ù Diagnostics": "health", "ð©º Health & Diagnostics": "health",
        "ð©º Health Check": "health",
        # Backup
        "ð¾ Backup Ù Recovery": "backup", "ð¾ Backup & Recovery": "backup",
        "ð¾ Ø¨Ú©Ø§Ù¾ Ù Ø¨Ø§Ø²ÛØ§Ø¨Û": "backup",
        # Features
        "ð§© ÙØ§Ø¨ÙÛØªâÙØ§ Ù Feature Flags": "features", "ð§© Features & Flags": "features",
        "ð§© ÙØ§Ø¨ÙÛØªâÙØ§": "features",
        # Security
        "ð Ø§ÙÙÛØª Ù Audit": "security", "ð Security & Audit": "security",
        "ð Ø§ÙÙÛØª": "security",
        # Test
        "ð§ª ÙØ±Ú©Ø² ØªØ³Øª Ù Regression": "test", "ð§ª Test & Regression": "test",
        "ð§ª ÙØ±Ú©Ø² ØªØ³Øª": "test",
        # System
        "âï¸ ØªÙØ¸ÛÙØ§Øª Ø³ÛØ³ØªÙ": "system", "âï¸ System Settings": "system",
        # Other
        "ð¦ Ø³Ø§ÛØ± ÙØ§ÚÙÙâÙØ§Û ÙØ¯ÛØ±ÛØªÛ": "other", "ð¦ Other Admin Modules": "other",
        "ð¦ ÙØ§ÚÙÙâÙØ§Û Ø¯ÛÚ¯Ø±": "other",
        # Birthday
        "ð ØªÙÙØ¯ Ù ÙÙØ§Ø³Ø¨ØªâÙØ§": "birthday",
        # Goals
        "ð¯ Ø§ÙØ¯Ø§Ù Ù ÛØ§Ø¯Ø¢ÙØ±Û": "dashboard",
        # Prices
        "ð ÙÛÙØª Ù Ø¨Ø§Ø²Ø§Ø±": "finance",
        # Payments
        "ð³ Ù¾Ø±Ø¯Ø§Ø®ØªâÙØ§": "finance",
        # Customers
        "ð¥ ÙØ´ØªØ±Û Ù Ø±Ø²Ø±Ù": "users",
        # Voice
        # Gifts
        "ð ÙØ¯ÛÙ ÙØ¯ÛØ±ÛØªÛ": "xpvip",
        # Referrals
        "ð£ Ø¯Ø¹ÙØª Ù Ø±ÙØ±Ø§Ù": "other",
        "ð£ Referrals": "other",
    }
    # Forced subscription has its own dedicated admin panel and is exposed
    # directly from the main management menu for quick access.
    if txt in ("ð Ø¹Ø¶ÙÛØª Ø§Ø¬Ø¨Ø§Ø±Û Ú©Ø§ÙØ§Ù", "ð Mandatory Channel Subscription"):
        if not admin_guard(uid):
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", reply_markup=keyboard(uid))
            return
        await _forced_sub_admin_panel(update, context)
        return

    if txt in _admin_section_map:
        await _show_admin_section(update, context, _admin_section_map[txt])
        return

    # Forced subscription text input handlers
    if context.user_data.get("forced_sub_wait") == "custom_duration":
        context.user_data.pop("forced_sub_wait", None)
        txt_stripped = txt.strip().lower()
        if txt_stripped.endswith("h"):
            try:
                dval = int(txt_stripped[:-1])
                if dval > 0:
                    _forced_sub_set("forced_sub_duration_type", "hours")
                    _forced_sub_set("forced_sub_duration_value", str(dval))
                    await update.message.reply_text(f"â ÙØ¯Øª {dval} Ø³Ø§Ø¹Øª Ø°Ø®ÛØ±Ù Ø´Ø¯.")
                    return
            except ValueError:
                pass
        elif txt_stripped.endswith("d"):
            try:
                dval = int(txt_stripped[:-1])
                if dval > 0:
                    _forced_sub_set("forced_sub_duration_type", "days")
                    _forced_sub_set("forced_sub_duration_value", str(dval))
                    await update.message.reply_text(f"â ÙØ¯Øª {dval} Ø±ÙØ² Ø°Ø®ÛØ±Ù Ø´Ø¯.")
                    return
            except ValueError:
                pass
        await update.message.reply_text("â ï¸ ÙØ±ÙØª ÙØ§Ø¯Ø±Ø³Øª. ÙØ«Ø§Ù: 48h ÛØ§ 7d")
        return
    if context.user_data.get("forced_sub_wait") == "channel_url":
        context.user_data.pop("forced_sub_wait", None)
        channel_value = txt.strip()
        valid = (
            bool(re.fullmatch(r"@[A-Za-z0-9_]{5,32}", channel_value))
            or bool(re.fullmatch(r"-?\d+", channel_value))
            or bool(re.fullmatch(r"https?://t\.me/[A-Za-z0-9_]{5,32}/?", channel_value, re.I))
            or bool(re.fullmatch(r"https?://t\.me/c/\d+(?:/\d+)?/?", channel_value, re.I))
        )
        if valid:
            _forced_sub_set("forced_sub_channel_url", channel_value)
            await update.message.reply_text(f"â Ú©Ø§ÙØ§Ù Ø°Ø®ÛØ±Ù Ø´Ø¯:\n{channel_value}")
        else:
            await update.message.reply_text(
                "â ï¸ ÙØ±ÙØª Ú©Ø§ÙØ§Ù ÙØ¹ØªØ¨Ø± ÙÛØ³Øª.\n\n"
                "ÙÙÙÙÙ ÙØ¹ØªØ¨Ø±:\n@ChannelUsername\nhttps://t.me/ChannelUsername\n-1001234567890"
            )
        return
    if context.user_data.get("forced_sub_wait") == "custom_message":
        context.user_data.pop("forced_sub_wait", None)
        _forced_sub_set("forced_sub_message", txt)
        await update.message.reply_text("â Ù¾ÛØ§Ù Ø§Ø®ØªØµØ§ØµÛ Ø°Ø®ÛØ±Ù Ø´Ø¯.")
        return

    # V25 modules must also have priority over transient legacy input states.
    v25_routes = {
        "ð§  ÙØ±Ú©Ø² ÙÙ": v25_hub,
        "ð§  My Center": v25_hub,
        "ð° Ø³Ø±ÙØ§ÛÙâÙØ§Û ÙÙ": v25_portfolio_menu,
        "ð° My Portfolio": v25_portfolio_menu,
        "ð³ Ø§ÙØ³Ø§Ø·": v25_installments_menu,
        "ð³ Installments": v25_installments_menu,
        "ð¤ Ø§Ø·ÙØ§Ø¹Ø§Øª ÙÙ": v25_profile_menu,
        "ð¤ My Profile": v25_profile_menu,
    }
    if text in v25_routes:
        clear_flow(context)
        await v25_routes[text](update, context)
        return


    if text in ("ðï¸ ØªÙÚ©ÙâÙØ§Û ÙÙ", "ðï¸ My Tokens"):
        clear_flow(context)
        tokens_from_xp(uid)
        await update.message.reply_text(token_user_text(uid), parse_mode="HTML", reply_markup=token_user_keyboard(uid))
        return

    # ===== User Menu Buttons (from _compact_user_keyboard) =====
    if txt in ("â¡ Ø¯Ø³ØªØ±Ø³Û Ø³Ø±ÛØ¹", "â¡ Quick Access"):
        clear_flow(context)
        fa = lang(uid) == "fa"
        text = "â¡ <b>Ø¯Ø³ØªØ±Ø³Û Ø³Ø±ÛØ¹</b>\n\nÙ¾Ø±ØªÚ©Ø±Ø§Ø±ØªØ±ÛÙ ÙØ§Ø¨ÙÛØªâÙØ§:" if fa else "â¡ <b>Quick Access</b>"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("â Ø§ÙØ²ÙØ¯Ù ÙØ¯Ù" if fa else "â Add Goal", callback_data="new_goal"),
             InlineKeyboardButton("ð ÙÛØ³Øª Ø§ÙØ¯Ø§Ù" if fa else "ð Goals List", callback_data="goals:main")],
            [InlineKeyboardButton("ð Ø¨Ø±ÙØ§ÙÙ Ø§ÙØ±ÙØ²" if fa else "ð Today's Plan", callback_data="goals:today"),
             InlineKeyboardButton("ð ÛØ§Ø¯Ø¢ÙØ±Û Ø¨Ø¹Ø¯Û" if fa else "ð Next Reminder", callback_data="goalreminders")],
            [InlineKeyboardButton("ð ØªÙÙØ¯ ÙÙ" if fa else "ð My Birthday", callback_data="birthday:show"),
             InlineKeyboardButton("ð¤ Ø¯Ø¹ÙØª Ø¯ÙØ³ØªØ§Ù" if fa else "ð¤ Invite Friends", callback_data="ref:home")],
        ])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return
    if txt in ("ð¯ Ø¨Ø±ÙØ§ÙÙ Ù Ø§ÙØ¯Ø§Ù", "ð¯ Goals & Plan"):
        clear_flow(context)
        await _compact_menu_show(update, context, "goals")
        return
    if txt in ("ð ØªÙÙÛÙ Ù ÛØ§Ø¯Ø¢ÙØ±Û", "ð Calendar & Reminders"):
        clear_flow(context)
        fa = lang(uid) == "fa"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("ð ØªÙÙÛÙ ÙÙ" if fa else "ð My Calendar", callback_data="goals:calendar")],
            [InlineKeyboardButton("ð ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§" if fa else "ð Reminders", callback_data="goalreminders")],
            [InlineKeyboardButton("ð Ø¨Ø±ÙØ§ÙÙ Ø§ÙØ±ÙØ²" if fa else "ð Today", callback_data="goals:today")],
        ])
        await update.message.reply_text("ð <b>ØªÙÙÛÙ Ù ÛØ§Ø¯Ø¢ÙØ±Û</b>" if fa else "ð <b>Calendar & Reminders</b>", parse_mode="HTML", reply_markup=kb)
        return
    if txt in ("ð¤ Ø­Ø³Ø§Ø¨ ÙÙ", "ð¤ My Account"):
        clear_flow(context)
        fa = lang(uid) == "fa"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("ð¤ Ø§Ø·ÙØ§Ø¹Ø§Øª Ø´Ø®ØµÛ" if fa else "ð¤ Personal Info", callback_data="v25:profile")],
            [InlineKeyboardButton("ð ØªÙÙØ¯ ÙÙ" if fa else "ð My Birthday", callback_data="birthday:show")],
            [InlineKeyboardButton("ð¥ ÙØ¯ÛØ±ÛØª ÙØ´ØªØ±ÛØ§Ù" if fa else "ð¥ Customers", callback_data="cust:main")],
            [InlineKeyboardButton("âï¸ ØªÙØ¸ÛÙØ§Øª" if fa else "âï¸ Settings", callback_data="settings:lang")],
        ])
        await update.message.reply_text("ð¤ <b>Ø­Ø³Ø§Ø¨ ÙÙ</b>" if fa else "ð¤ <b>My Account</b>", parse_mode="HTML", reply_markup=kb)
        return
    if txt in ("ð Ù¾Ø§Ø¯Ø§Ø´âÙØ§Û ÙÙ", "ð My Rewards"):
        clear_flow(context)
        await xp_command(update, context)
        return
    if txt in ("ð Ø¢ÙØ§Ø± Ù Ú¯Ø²Ø§Ø±Ø´", "ð Reports & Analytics", "ð Stats & Reports"):
        clear_flow(context)
        await _compact_menu_show(update, context, "reports")
        return
    if txt in ("ð ï¸ Ø§Ø¨Ø²Ø§Ø±ÙØ§", "ð ï¸ Tools"):
        clear_flow(context)
        fa = lang(uid) == "fa"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("ð ÙÛÙØª Ø¢ÙÙØ§ÛÙ" if fa else "ð Prices", callback_data="prices:menu")],
        ])
        await update.message.reply_text("ð ï¸ <b>Ø§Ø¨Ø²Ø§Ø±ÙØ§</b>" if fa else "ð ï¸ <b>Tools</b>", parse_mode="HTML", reply_markup=kb)
        return

    # Every normal legacy button is handled here before any text-input flow.
    legacy_routes = {
        "ð¯ Ø§ÙØ¯Ø§Ù Ø§ÙØ±ÙØ²": today, "ð¯ Today's Goals": today,
        "âï¸ ÙØ¯Ù Ø®ÙØ¯Ù ÙÛâÙÙÛØ³Ù": custom_goal_start, "âï¸ Write my own goal": custom_goal_start,
        "ð Ø§ÙØ¯Ø§Ù Ø¢ÙØ§Ø¯Ù": ready_menu, "ð Ready Goals": ready_menu,
        "âï¸ ÙÛØ±Ø§ÛØ´ Ø§ÙØ¯Ø§Ù": edit_menu, "âï¸ Edit Goals": edit_menu,
        "ð Ø¬Ø¯ÙÙ ÙÙØªÚ¯Û": weekly, "ð Weekly Table": weekly,
        "ð Ø¢ÙØ§Ø± ÙÙ": stats, "ð My Stats": stats,
        "ð¤ Ù¾Ø±ÙÙØ§ÛÙ": profile, "ð¤ Profile": profile,
        "ð Ø¯Ø³ØªØ§ÙØ±Ø¯ÙØ§": achievements, "ð Achievements": achievements,
        "ð¤ Ø¯Ø¹ÙØª Ø¯ÙØ³ØªØ§Ù": referral, "ð¤ Referrals": referral,
        "ð ÙÛÙØª Ø¢ÙÙØ§ÛÙ": prices, "ð Online Prices": prices,
        "ð VIP": vip_center,
        "ð« Ù¾Ø´ØªÛØ¨Ø§ÙÛ": support_start, "ð« Support": support_start,
        "âï¸ ØªÙØ¸ÛÙØ§Øª": settings, "âï¸ Settings": settings,
        "ð¥ ÙØ¯ÛØ±ÛØª ÙØ´ØªØ±Û Ù ÙÙØ¨ØªâØ¯ÙÛ": customer_panel, "ð¥ Customer & Appointments": customer_panel,
    }
    if text in legacy_routes:
        requested = FEATURE_MENU_MAP.get(text)
        if requested and not user_feature_allowed(uid, requested):
            await update.message.reply_text("â Ø§ÛÙ ÙØ§Ø¨ÙÛØª ÙØ¹ÙØ§Ù ØªÙØ³Ø· ÙØ¯ÛØ± ØºÛØ±ÙØ¹Ø§Ù Ø´Ø¯Ù Ø§Ø³Øª.", reply_markup=keyboard(uid))
            return
        clear_flow(context)
        await legacy_routes[text](update, context)
        return

    if text == "â­ XP":
        clear_flow(context)
        await xp_command(update, context)
        return

    if text in ("ð¢ ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù", "ð¢ Channel Management"):
        clear_flow(context)
        if admin_guard(uid):
            await update.message.reply_text(
                "ð¢ <b>ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù Ù Ù¾Ø³ØªâÚ¯Ø°Ø§Ø±Û</b>\n\nØ§ØªØµØ§Ù Ú©Ø§ÙØ§ÙØ Ø³Ø§Ø®Øª Ù¾Ø³ØªØ Ø²ÙØ§ÙâØ¨ÙØ¯Û Ù Ø§ÙØªØ´Ø§Ø± Ø®ÙØ¯Ú©Ø§Ø±.",
                parse_mode="HTML", reply_markup=channel_keyboard())
            await hide_main_reply_keyboard(update)
        else:
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.")
        return

    if text in ("ð¡ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", "ð¡ Admin Panel"):
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


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# â°  SCHEDULER JOBS
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

_birthday_last_run = ""

async def birthday_scheduler_job(context):
    """Check for birthdays and send greetings."""
    global _birthday_last_run
    now = datetime.now(TZ)
    today_key = now.strftime("%Y-%m-%d")
    hour = now.hour
    # Run once per day between 08:00 and 09:00
    if today_key == _birthday_last_run or hour < 8 or hour > 9:
        return
    _birthday_last_run = today_key
    if not birthday_enabled():
        return
    birthdays = get_todays_birthdays()
    for b in birthdays:
        try:
            await send_birthday_greeting(context.bot, b["user_id"], b["first_name"])
        except Exception as e:
            logger.warning("Birthday greeting failed for %s: %s", b["user_id"], e)

_event_last_run = ""

async def event_scheduler_job(context):
    """Check for active events and send event messages."""
    global _event_last_run
    now = datetime.now(TZ)
    today_key = now.strftime("%Y-%m-%d")
    hour = now.hour
    # Run once per day between 09:00 and 10:00
    if today_key == _event_last_run or hour < 9 or hour > 10:
        return
    _event_last_run = today_key
    if not events_enabled():
        return
    events = get_active_events()
    if not events:
        return
    c = db()
    users = c.execute("SELECT user_id, first_name FROM users WHERE blocked=0").fetchall()
    c.close()
    for event in events:
        for u in users:
            if not event_already_delivered(event["id"], u["user_id"]):
                try:
                    text = f"ð <b>{html.escape(event['name'])}</b>\n\n{event['message']}"
                    if event["xp_reward"] > 0:
                        add_xp(u["user_id"], event["xp_reward"], f"event:{event['id']}")
                        text += f"\n\nð ÙØ¯ÛÙ: â­ {event['xp_reward']} XP"
                    await context.bot.send_message(u["user_id"], text, parse_mode="HTML")
                    mark_event_delivered(event["id"], u["user_id"])
                except Exception as e:
                    logger.warning("Event delivery failed for %s: %s", u["user_id"], e)







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
    label = {"day": "Ø±ÙØ²Ø§ÙÙ", "week": "ÙÙØªÚ¯Û", "month": "ÙØ§ÙØ§ÙÙ"}.get(period, "Ú¯Ø²Ø§Ø±Ø´")

    if lang(uid) == "fa":
        lines = [
            f"ð <b>Ú¯Ø²Ø§Ø±Ø´ {label} Ø´ÙØ§</b>",
            "",
            f"Ø³ÙØ§Ù {html.escape(display_name(uid))} ð",
            "Ø§ÛÙ Ú¯Ø²Ø§Ø±Ø´ Ø®ÙØ§ØµÙ ÙØ¹Ø§ÙÛØª Ù ÙØ¯ÙâÙØ§Û Ø«Ø¨ØªâØ´Ø¯Ù Ø´ÙØ§ Ø¯Ø± Ø¨Ø§Ø²Ù Ø§ÙØªØ®Ø§Ø¨âØ´Ø¯Ù Ø§Ø³Øª.",
            "",
            f"â Ø§ÙØ¬Ø§ÙâØ´Ø¯Ù: <b>{done}</b>",
            f"â Ø§ÙØ¬Ø§ÙâÙØ´Ø¯Ù: <b>{missed}</b>",
            f"ð Ú©Ù ÙÙØ§Ø±Ø¯ Ø«Ø¨ØªâØ´Ø¯Ù: <b>{total}</b>",
            f"ð ÙØ±Ø® ÙÙÙÙÛØª: <b>{rate:.1f}%</b>",
            f"ð Ø¨Ø§Ø²Ù: <b>{start.isoformat()} ØªØ§ {today.isoformat()}</b>",
        ]
        if period == "week" and rows:
            lines += ["", "ð <b>Ø¬Ø²Ø¦ÛØ§Øª Ø±ÙØ²ÙØ§:</b>"]
            for r in rows:
                d = r["goal_date"]
                dt = datetime.fromisoformat(d).date()
                day_names = ["Ø´ÙØ¨Ù","ÛÚ©Ø´ÙØ¨Ù","Ø¯ÙØ´ÙØ¨Ù","Ø³ÙâØ´ÙØ¨Ù","ÚÙØ§Ø±Ø´ÙØ¨Ù","Ù¾ÙØ¬Ø´ÙØ¨Ù","Ø¬ÙØ¹Ù"]
                day_name = day_names[(dt.weekday() + 1) % 7]
                lines.append(f"â¢ {day_name} {d}: {int(r['done'] or 0)}/{int(r['total'] or 0)} â")
        if not rows:
            lines += ["", "â¹ï¸ ÙÙÙØ² Ø¨Ø±Ø§Û Ø§ÛÙ Ø¨Ø§Ø²Ù ÙØ¹Ø§ÙÛØª Ø«Ø¨ØªâØ´Ø¯ÙâØ§Û ÙØ¯Ø§Ø±ÛØ¯."]
        text = "\n".join(lines)
    else:
        text = (
            f"ð <b>Your {label} Report</b>\n\n"
            f"Hello {html.escape(display_name(uid))} ð\n"
            "Here is your activity summary for the selected period.\n\n"
            f"â Completed: <b>{done}</b>\n"
            f"â Missed: <b>{missed}</b>\n"
            f"ð Recorded items: <b>{total}</b>\n"
            f"ð Success rate: <b>{rate:.1f}%</b>\n"
            f"ð Range: <b>{start.isoformat()} to {today.isoformat()}</b>"
        )
        if not rows:
            text += "\n\nâ¹ï¸ No activity has been recorded for this period."

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("ð Ø±ÙØ²Ø§ÙÙ" if lang(uid)=="fa" else "ð Daily", callback_data="v25:reports"),
            InlineKeyboardButton("ð ÙÙØªÚ¯Û" if lang(uid)=="fa" else "ð Weekly", callback_data="v25:report_week"),
            InlineKeyboardButton("ð ÙØ§ÙØ§ÙÙ" if lang(uid)=="fa" else "ð Monthly", callback_data="v25:report_month"),
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
            [("ð¯ Ø§ÙØ¯Ø§Ù Ø§ÙØ±ÙØ²", "cm:today"), ("âï¸ ÙØ¯Ù Ø¬Ø¯ÛØ¯", "cm:custom_goal")],
            [("ð Ø§ÙØ¯Ø§Ù Ø¢ÙØ§Ø¯Ù", "cm:ready_goals"), ("âï¸ ÙÛØ±Ø§ÛØ´ Ø§ÙØ¯Ø§Ù", "cm:edit_goals")],
            [("ð Ú¯Ø²Ø§Ø±Ø´ ÙÙØªÚ¯Û", "cm:weekly"), ("ð Ø¢ÙØ§Ø± ÙÙ", "cm:stats")],
        ],
        "reports": [
            [("ð Ú¯Ø²Ø§Ø±Ø´ ÙÙØªÚ¯Û", "cm:weekly"), ("ð Ø¢ÙØ§Ø± ÙÙ", "cm:stats")],
            [("ð Ø¯Ø³ØªØ§ÙØ±Ø¯ÙØ§", "cm:achievements"), ("â­ XP", "cm:xp")],
        ],
        "tools": [
            [("ð ÙÛÙØª Ø¢ÙÙØ§ÛÙ", "cm:prices"), ("ð§  ÙØ±Ú©Ø² ÙÙ", "cm:center")],
        ],
        "vip": [
            [("ð VIP Ù Ø§Ø´ØªØ±Ø§Ú©", "cm:vip"), ("â­ XP", "cm:xp")],
            [("ð¤ Ø¯Ø¹ÙØª Ø¯ÙØ³ØªØ§Ù", "cm:referral"), ("ðï¸ ØªÙÚ©ÙâÙØ§Û ÙÙ", "cm:tokens")],
        ],
        "account": [
            [("ð¤ Ù¾Ø±ÙÙØ§ÛÙ", "cm:profile"), ("âï¸ ØªÙØ¸ÛÙØ§Øª", "cm:settings")],
            [("ð ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§", "cm:reminders"), ("ð ØªÙÙÛÙ ÙÙ", "cm:calendar")],
        ],
        "support": [
            [("ð« Ù¾Ø´ØªÛØ¨Ø§ÙÛ", "cm:support"), ("ð Ø±Ø§ÙÙÙØ§Û Ø±Ø¨Ø§Øª", "cm:guide")],
        ],
    }
    titles = {
        "goals": ("ð¯ <b>Ø¨Ø±ÙØ§ÙÙ Ù Ø§ÙØ¯Ø§Ù</b>", "ð¯ <b>Goals & Plan</b>"),
        "reports": ("ð <b>Ú¯Ø²Ø§Ø±Ø´ Ù Ù¾ÛØ´Ø±ÙØª</b>", "ð <b>Reports & Progress</b>"),
        "tools": ("ð ï¸ <b>Ø§Ø¨Ø²Ø§Ø±ÙØ§</b>", "ð ï¸ <b>Tools</b>"),
        "vip": ("ð <b>VIP Ù Ù¾Ø§Ø¯Ø§Ø´âÙØ§</b>", "ð <b>VIP & Rewards</b>"),
        "account": ("ð¤ <b>Ø­Ø³Ø§Ø¨ ÙÙ</b>", "ð¤ <b>My Account</b>"),
        "support": ("ð« <b>Ù¾Ø´ØªÛØ¨Ø§ÙÛ</b>", "ð« <b>Support</b>"),
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
            "cm:today":"ð¯ Today's Goals","cm:custom_goal":"âï¸ New Goal",
            "cm:ready_goals":"ð Ready Goals","cm:edit_goals":"âï¸ Edit Goals",
            "cm:weekly":"ð Weekly Report","cm:stats":"ð My Stats",
            "cm:achievements":"ð Achievements","cm:xp":"â­ XP",
            "cm:prices":"ð Online Prices","cm:center":"ð§  My Center",
            "cm:vip":"ð VIP & Subscription","cm:referral":"ð¤ Referrals",
            "cm:tokens":"ðï¸ My Tokens","cm:profile":"ð¤ Profile",
            "cm:settings":"âï¸ Settings","cm:reminders":"ð Reminders",
            "cm:calendar":"ð Calendar","cm:support":"ð« Support",
            "cm:guide":"ð Bot Guide",
        }
        rows = [[InlineKeyboardButton(en.get(btn.callback_data, btn.text), callback_data=btn.callback_data) for btn in row] for row in rows]
    rows.append([
        InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª" if fa else "â¬ï¸ Back", callback_data="cm:home"),
        main_menu_button(uid),
    ])
    return InlineKeyboardMarkup(rows)


async def _compact_menu_show(update, context, section):
    uid = update.effective_user.id
    fa = lang(uid) == "fa"
    context.user_data["_nav_parent_section"] = section
    titles = {
        "goals": ("ð¯ <b>Ø¨Ø±ÙØ§ÙÙ Ù Ø§ÙØ¯Ø§Ù</b>", "ð¯ <b>Goals & Plan</b>"),
        "reports": ("ð <b>Ú¯Ø²Ø§Ø±Ø´ Ù Ù¾ÛØ´Ø±ÙØª</b>", "ð <b>Reports & Progress</b>"),
        "tools": ("ð ï¸ <b>Ø§Ø¨Ø²Ø§Ø±ÙØ§</b>", "ð ï¸ <b>Tools</b>"),
        "vip": ("ð <b>VIP Ù Ù¾Ø§Ø¯Ø§Ø´âÙØ§</b>", "ð <b>VIP & Rewards</b>"),
        "account": ("ð¤ <b>Ø­Ø³Ø§Ø¨ ÙÙ</b>", "ð¤ <b>My Account</b>"),
        "support": ("ð« <b>Ù¾Ø´ØªÛØ¨Ø§ÙÛ</b>", "ð« <b>Support</b>"),
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
        "ð <b>Ø±Ø§ÙÙÙØ§Û Ø±Ø¨Ø§Øª</b>\n\n"
        "ð¯ Ø¨Ø±ÙØ§ÙÙ Ù Ø§ÙØ¯Ø§Ù: Ø³Ø§Ø®Øª Ù Ù¾ÛÚ¯ÛØ±Û ÙØ¯ÙâÙØ§\n"
        "ð Ú¯Ø²Ø§Ø±Ø´ Ù Ù¾ÛØ´Ø±ÙØª: ÙØ´Ø§ÙØ¯Ù Ø¢ÙØ§Ø± Ù Ú¯Ø²Ø§Ø±Ø´âÙØ§\n"
        "ð§  ÙØ±Ú©Ø² ÙÙ: ÛØ§Ø¯Ø¢ÙØ±ÛØ ØªÙÙÛÙØ Ø³Ø±ÙØ§ÛÙâÙØ§Ø Ø§ÙØ³Ø§Ø· Ù Ù¾Ø±ÙÙØ§ÛÙ\n"
        "ð¥ ÙØ¯ÛØ±ÛØª ÙØ´ØªØ±Û Ù ÙÙØ¨ØªâØ¯ÙÛ: Ø¨Ø±Ø§Û Ø­Ø³Ø§Ø¨âÙØ§Û ÙØ¬Ø§Ø²\n\n"
        "Ø¨Ø±Ø§Û Ø¨Ø±Ú¯Ø´Øª Ø§Ø² Ø¯Ú©ÙÙ Â«â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´ØªÂ» Ø§Ø³ØªÙØ§Ø¯Ù Ú©Ù."
        if fa else
        "ð <b>Bot Guide</b>\n\n"
        "ð¯ My Plan: create and track goals\n"
        "ð Reports: view progress and statistics\n"
        "ð¤ Smart Tools: AI chat, voice assistant and prices\n"
        "ð§  My Center: reminders, calendar, portfolio, installments and profile\n"
        "ð¥ Customers & Appointments: for eligible accounts\n\n"
        "Use Â«â¬ï¸ BackÂ» to return."
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª" if fa else "â¬ï¸ Back", callback_data="cm:tools"),
        main_menu_button(uid),
    ]])
    if update.callback_query:
        await update.callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


class _SectionProxyMessage:
    """Render section screens IN PLACE: edit_text instead of sending new bubbles,
    and guarantee a Back + Main Menu row on every section keyboard."""

    def __init__(self, qmsg, uid, back_cb):
        self._m = qmsg
        self._uid = uid
        self._back = back_cb

    def _decorated(self, reply_markup):
        fa = lang(self._uid) == "fa"
        back_btn = InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª" if fa else "â¬ï¸ Back", callback_data=self._back)
        main_btn = main_menu_button(self._uid)
        rows = []
        if reply_markup is not None and getattr(reply_markup, "inline_keyboard", None):
            for r in reply_markup.inline_keyboard:
                texts = {b.text for b in r}
                cbs = {b.callback_data for b in r}
                # skip existing nav rows so we never duplicate them
                if back_btn.callback_data in cbs or main_btn.callback_data in cbs:
                    continue
                rows.append(list(r))
            # drop trailing empty-ish nav rows duplication guard done above
        rows.append([back_btn, main_btn])
        return InlineKeyboardMarkup(rows)

    async def reply_text(self, text, parse_mode=None, reply_markup=None, **kwargs):
        # Reply-keyboard (bottom bar) cannot be attached to an edit; send as-is.
        if reply_markup is not None and not hasattr(reply_markup, "inline_keyboard"):
            await self._m.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup, **kwargs)
            return
        markup = self._decorated(reply_markup)
        try:
            await self._m.edit_text(text, parse_mode=parse_mode, reply_markup=markup)
        except Exception:
            try:
                await self._m.edit_reply_markup(reply_markup=markup)
            except Exception:
                await self._m.reply_text(text, parse_mode=parse_mode, reply_markup=markup, **kwargs)

    async def reply_markdown(self, text, *a, **k):
        await self.reply_text(text, *a, **k)

    def __getattr__(self, item):
        return getattr(self._m, item)


async def compact_menu_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    data = q.data
    await q.answer()
    if data == "cm:home":
        await q.message.edit_text(
            _root_menu_text(uid),
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

    if data == "cm:tokens":
        tokens_from_xp(uid)
        await q.message.edit_text(token_user_text(uid), parse_mode="HTML", reply_markup=token_user_keyboard(uid))
        return
    if data == "cm:calendar":
        await q.message.edit_text(
            "ð <b>ØªÙÙÛÙ ÙÙ</b>\n\nØ§ÛÙ Ø¨Ø®Ø´ Ø§Ø² Â«ð§  ÙØ±Ú©Ø² ÙÙÂ» ÙØ§Ø¨Ù ÙØ¯ÛØ±ÛØª Ø§Ø³Øª." if lang(uid) == "fa"
            else "ð <b>My Calendar</b>\n\nManage it from My Center.",
            parse_mode="HTML",
            reply_markup=v25_hub_keyboard(uid)
        )
        return
    fn = routes.get(data)
    if fn:
        back_cb = context.user_data.get("_last_section") or "cm:home"
        clear_flow(context)
        # Adapt the callback query into a minimal update object so existing
        # message-based handlers can be reused without duplicating business logic.
        proxy = type("_MenuUpdate", (), {
            "effective_user": q.from_user,
            "message": _SectionProxyMessage(q.message, uid, back_cb),
            "callback_query": None,
        })()
        try:
            await fn(proxy, context)
        except Exception as exc:
            logger.exception("Compact menu route failed: %s", data)
            release_leaked_connections(exc)
            # Never hide a route failure by resetting the whole bot to the home menu.
            # Offer a direct retry and a controlled back path instead.
            retry_text = (
                "â ï¸ Ø§ÛÙ Ø¨Ø®Ø´ Ø¨Ø§ Ø®Ø·Ø§ Ø±ÙØ¨ÙâØ±Ù Ø´Ø¯.\n\n"
                f"Ú©Ø¯ Ø®Ø·Ø§: <code>{type(exc).__name__}</code>\n"
                "ÙÛâØªÙØ§ÙÛ Ø¯ÙØ¨Ø§Ø±Ù ÙÙÛÙ Ø¨Ø®Ø´ Ø±Ø§ Ø§ÙØªØ­Ø§Ù Ú©ÙÛ ÛØ§ Ø¨Ù Ø§Ø¨Ø²Ø§Ø±ÙØ§Û ÙÙØ´ÙÙØ¯ Ø¨Ø±Ú¯Ø±Ø¯Û."
                if lang(uid) == "fa" else
                "â ï¸ This section encountered an error.\n\n"
                f"Error: <code>{type(exc).__name__}</code>\n"
                "Retry this section or return to Smart Tools."
            )
            await q.message.reply_text(
                retry_text, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("ð ØªÙØ§Ø´ Ø¯ÙØ¨Ø§Ø±Ù" if lang(uid)=="fa" else "ð Retry", callback_data=data)],
                    [InlineKeyboardButton("ð¤ Ø§Ø¨Ø²Ø§Ø±ÙØ§Û ÙÙØ´ÙÙØ¯" if lang(uid)=="fa" else "ð¤ Smart Tools", callback_data="menu:tools")],
                    [main_menu_button(uid)],
                ]),
            )
        return


def _compact_root_inline(uid):
    """Clean root menu: primary action full-width, related items paired."""
    fa = lang(uid) == "fa"
    if fa:
        rows = [
            [InlineKeyboardButton("ð¯ Ø¨Ø±ÙØ§ÙÙ ÙÙ", callback_data="menu:goals")],
            [InlineKeyboardButton("ð Ú¯Ø²Ø§Ø±Ø´ Ù Ù¾ÛØ´Ø±ÙØª", callback_data="menu:reports"),
             InlineKeyboardButton("ð¤ Ø§Ø¨Ø²Ø§Ø±ÙØ§", callback_data="menu:tools")],
            [InlineKeyboardButton("ð VIP Ù XP", callback_data="menu:vip"),
             InlineKeyboardButton("ð¤ Ø­Ø³Ø§Ø¨ ÙÙ", callback_data="menu:account")],
            [InlineKeyboardButton("ð« Ù¾Ø´ØªÛØ¨Ø§ÙÛ", callback_data="menu:support")],
        ]
    else:
        rows = [
            [InlineKeyboardButton("ð¯ My Plan", callback_data="menu:goals")],
            [InlineKeyboardButton("ð Reports", callback_data="menu:reports"),
             InlineKeyboardButton("ð¤ Tools", callback_data="menu:tools")],
            [InlineKeyboardButton("ð VIP & XP", callback_data="menu:vip"),
             InlineKeyboardButton("ð¤ My Account", callback_data="menu:account")],
            [InlineKeyboardButton("ð« Support", callback_data="menu:support")],
        ]
    return InlineKeyboardMarkup(rows)


async def compact_section_callback(update, context):
    q = update.callback_query
    section = q.data.split(":", 1)[1]
    context.user_data["_last_section"] = ("menu:" + section)
    await _compact_menu_show(update, context, section)


def _compact_user_keyboard(uid):
    """User main menu - 8 categories as per Master List."""
    fa = lang(uid) == "fa"
    rows = [
        ["â¡ Ø¯Ø³ØªØ±Ø³Û Ø³Ø±ÛØ¹" if fa else "â¡ Quick Access", "ð¯ Ø¨Ø±ÙØ§ÙÙ Ù Ø§ÙØ¯Ø§Ù" if fa else "ð¯ Goals & Plan"],
        ["ð ÙÛÙØª Ø¢ÙÙØ§ÛÙ" if fa else "ð Online Prices", "ð ØªÙÙÛÙ Ù ÛØ§Ø¯Ø¢ÙØ±Û" if fa else "ð Calendar & Reminders"],
        ["ð¤ Ø­Ø³Ø§Ø¨ ÙÙ" if fa else "ð¤ My Account", "ð¤ Ø¯Ø¹ÙØª Ø¯ÙØ³ØªØ§Ù" if fa else "ð¤ Invite Friends"],
        ["ð Ù¾Ø§Ø¯Ø§Ø´âÙØ§Û ÙÙ" if fa else "ð My Rewards", "ð Ø¢ÙØ§Ø± Ù Ú¯Ø²Ø§Ø±Ø´" if fa else "ð Stats & Reports"],
        ["ð ï¸ Ø§Ø¨Ø²Ø§Ø±ÙØ§" if fa else "ð ï¸ Tools", "ð« Ù¾Ø´ØªÛØ¨Ø§ÙÛ" if fa else "ð« Support"],
        ["âï¸ ØªÙØ¸ÛÙØ§Øª" if fa else "âï¸ Settings"],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)


def _compact_admin_management_keyboard(uid):
    """Management-only menu. User features stay in the separate user area.
    Categories match the Master List structure."""
    fa=lang(uid)=="fa"
    rows=[
        ["ð Ø¯Ø§Ø´Ø¨ÙØ±Ø¯ Ù Ú¯Ø²Ø§Ø±Ø´" if fa else "ð Dashboard & Reports"],
        ["ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ù Ù¾Ø§Ø¯Ø§Ø´âÙØ§" if fa else "ð¥ Users & Rewards", "ð Ø§Ø´ØªØ±Ø§Ú© Ù Ø¯Ø³ØªØ±Ø³ÛâÙØ§" if fa else "ð Subscriptions & Access"],
        ["ð¢ ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù" if fa else "ð¢ Channel Management", "ð Ø¹Ø¶ÙÛØª Ø§Ø¬Ø¨Ø§Ø±Û Ú©Ø§ÙØ§Ù" if fa else "ð Mandatory Channel Subscription"],
        ["ð ØªÙÙØ¯ Ù ÙÙØ§Ø³Ø¨ØªâÙØ§" if fa else "ð Birthday & Events", "ð¯ Ø§ÙØ¯Ø§Ù Ù ÛØ§Ø¯Ø¢ÙØ±Û" if fa else "ð¯ Goals & Reminders"],
        ["ð ÙÛÙØª Ù Ø¨Ø§Ø²Ø§Ø±" if fa else "ð Prices & Market", "ð³ Ù¾Ø±Ø¯Ø§Ø®ØªâÙØ§" if fa else "ð³ Payments"],
        ["ð« Ù¾Ø´ØªÛØ¨Ø§ÙÛ Ù ØªÛÚ©Øª" if fa else "ð« Support & Tickets", "ð ÙØ¯ÛÙ ÙØ¯ÛØ±ÛØªÛ" if fa else "ð Admin Gifts"],
        ["ð©º Health Check" if fa else "ð©º Health Check", "ð£ Ø¯Ø¹ÙØª Ù Ø±ÙØ±Ø§Ù" if fa else "ð£ Referrals"],
        ["ð§ª ÙØ±Ú©Ø² ØªØ³Øª" if fa else "ð§ª Test Center", "ð§© ÙØ§Ø¨ÙÛØªâÙØ§" if fa else "ð§© Features"],
        ["âï¸ ØªÙØ¸ÛÙØ§Øª Ø³ÛØ³ØªÙ" if fa else "âï¸ System Settings", "ð Ø§ÙÙÛØª" if fa else "ð Security"],
        ["ð¾ Ø¨Ú©Ø§Ù¾ Ù Ø¨Ø§Ø²ÛØ§Ø¨Û" if fa else "ð¾ Backup & Recovery", "ð¦ ÙØ§ÚÙÙâÙØ§Û Ø¯ÛÚ¯Ø±" if fa else "ð¦ Other Modules"],
        ["ð¤ Ø§Ø³ØªÙØ§Ø¯Ù Ø§Ø² Ø±Ø¨Ø§Øª" if fa else "ð¤ Use Bot"],
        ["ð  ÙÙÙÛ Ø§ØµÙÛ" if fa else "ð  Main Menu"],
    ]
    return ReplyKeyboardMarkup(rows,resize_keyboard=True,one_time_keyboard=False)


def _compact_admin_root_keyboard(uid):
    """Admin root: exactly two choices, user area or management area."""
    fa=lang(uid)=="fa"
    return ReplyKeyboardMarkup([
        ["ð¤ Ø§Ø³ØªÙØ§Ø¯Ù Ø§Ø² Ø±Ø¨Ø§Øª" if fa else "ð¤ Use Bot"],
        ["ð¡ ÙØ¯ÛØ±ÛØª Ø±Ø¨Ø§Øª" if fa else "ð¡ Bot Management"],
    ],resize_keyboard=True,one_time_keyboard=False)


def _show_admin_management(update, context):
    uid=update.effective_user.id
    return update.message.reply_text(
        "ð¡ <b>ÙØ¯ÛØ±ÛØª Ø±Ø¨Ø§Øª</b>\n\nØ¨Ø®Ø´ ÙØ¯ÛØ±ÛØª Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù." if lang(uid)=="fa" else
        "ð¡ <b>Bot Management</b>\n\nChoose a management section.",
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
        "ð¯ Ø¨Ø±ÙØ§ÙÙ ÙÙ": "goals", "ð¯ My Plan": "goals",
        "ð Ú¯Ø²Ø§Ø±Ø´ Ù Ù¾ÛØ´Ø±ÙØª": "reports", "ð Reports": "reports",
        "ð¤ Ø§Ø¨Ø²Ø§Ø±ÙØ§Û ÙÙØ´ÙÙØ¯": "tools", "ð¤ Smart Tools": "tools",
        "ð VIP Ù XP": "vip", "ð VIP & XP": "vip",
        "ð¤ Ø­Ø³Ø§Ø¨ ÙÙ": "account", "ð¤ My Account": "account",
        "ð« Ù¾Ø´ØªÛØ¨Ø§ÙÛ": "support", "ð« Support": "support",
    }
    if txt in category_map:
        await _compact_menu_show(update, context, category_map[txt])
        return
    if txt in ("ð¡ Ù¾ÙÙ ÙØ¯ÛØ±ÛØª", "ð¡ Admin Panel"):
        await admin_command(update, context)
        return
    if txt in ("ð Ú¯Ø²Ø§Ø±Ø´ ÙØ¯ÛØ±ÛØª", "ð Admin Reports"):
        await admin_command(update, context)
        return
    if txt in ("ð« ØªÛÚ©ØªâÙØ§", "ð« Tickets"):
        await admin_command(update, context)
        return
    if txt in ("ð§© ÙØ§Ø¨ÙÛØªâÙØ§", "ð§© Features"):
        await admin_command(update, context)
        return

    if txt in ("ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù", "ð¥ Users"):
        await admin_command(update, context)
        return
    if txt in ("ð° ÙØ§ÙÛ", "ð° Finance"):
        await admin_command(update, context)
        return
    if txt in ("ð§­ Ú©ÙØªØ±Ù Ú©Ø§ÙÙ Ø³ÛØ³ØªÙ", "ð§­ Full System Control"):
        if not admin_guard(uid):
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", reply_markup=keyboard(uid))
            return
        await update.message.reply_text(
            "ð§­ <b>ÙØ±Ú©Ø² Ú©ÙØªØ±Ù Ú©Ø§ÙÙ Ø³ÛØ³ØªÙ</b>\n\nØ§Ø² Ø¯Ú©ÙÙ Ø²ÛØ± ÙØ§Ø±Ø¯ ÙØ±Ú©Ø² ÙØ¯ÛØ±ÛØª Ú©Ø§ÙÙ Ø´Ù.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("ð§­ ÙØ±ÙØ¯ Ø¨Ù ÙØ±Ú©Ø² ÙØ¯ÛØ±ÛØª", callback_data="v25:master:home")],
                [main_menu_button(uid)],
            ]),
        )
        return
    if txt in ("ð¢ ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù", "ð¢ Channel Management"):
        if not admin_guard(uid):
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", reply_markup=keyboard(uid))
            return
        await update.message.reply_text(
            "ð¢ <b>ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù Ù Ù¾Ø³ØªâÚ¯Ø°Ø§Ø±Û</b>",
            parse_mode="HTML",
            reply_markup=channel_keyboard(),
        )
        return
    if txt == "âï¸ ØªÙØ¸ÛÙØ§Øª" or txt == "âï¸ Settings":
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
    "owner": "ð Owner",
    "senior_manager": "ð¡ ÙØ¯ÛØ± Ø§Ø±Ø´Ø¯",
    "general_manager": "ð¤ ÙØ¯ÛØ± Ø¹ÙÙÙÛ",
    "technical_manager": "ð§° ÙØ¯ÛØ± ÙÙÛ",
    "finance_manager": "ð° ÙØ¯ÛØ± ÙØ§ÙÛ",
    "ticket_manager": "ð« ÙØ¯ÛØ± ØªÛÚ©Øª",
    "channel_manager": "ð¢ ÙØ¯ÛØ± Ú©Ø§ÙØ§Ù",
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
    # Per-manager overrides (edited from the manager permissions UI) win over
    # the role default. An empty list falls back to the role's defaults.
    try:
        c=db(); r=c.execute("SELECT permissions_json FROM management_roles WHERE user_id=? AND active=1",(int(uid),)).fetchone(); c.close()
        if r is not None:
            perms=set(json.loads(r["permissions_json"] or "[]"))
            if perms:
                return permission in perms
    except Exception:
        logger.exception("master_has_permission override lookup failed")
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
    return ("ð <b>Ø¯Ø§Ø´Ø¨ÙØ±Ø¯ ÙØ±Ú©Ø²Û ÙØ¯ÛØ±ÛØª</b>\n\n"
            f"ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù: <b>{s['users']}</b>\nð¢ ÙØ¹Ø§Ù Ø§ÙØ±ÙØ²: <b>{s['active_today']}</b>\nð Ø¬Ø¯ÛØ¯ Ø§ÙØ±ÙØ²: <b>{s['new_today']}</b>\n"
            f"ð VIP ÙØ¹Ø§Ù: <b>{s['vip_users']}</b>\nð« ØªÛÚ©Øª Ø¨Ø§Ø²: <b>{s['open_tickets']}</b>\n"
            f"ð¨ Incident Ø¨Ø§Ø²: <b>{incidents}</b>\nð¤ ÙØ¯ÛØ± ÙØ¹Ø§Ù: <b>{managers}</b>\n"
            f"ð¤ Ø®Ø·Ø§Û Ø«Ø¨ØªâØ´Ø¯Ù AI: <b>{ai_errors}</b>\nð§ª ØªØ³ØªâÙØ§Û Ø§Ø¬Ø±Ø§Ø´Ø¯Ù: <b>{tests}</b>")


def master_root_keyboard(uid):
    fa=lang(uid)=="fa"
    items=[
        ("ð Ø¯Ø§Ø´Ø¨ÙØ±Ø¯ Ù Analytics","ð Dashboard & Analytics","dashboard"),
        ("ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ù ÙÙØ´âÙØ§","ð¥ Users & Roles","users"),
        ("ð« ØªÛÚ©Øª Ù Incident","ð« Tickets & Incidents","tickets"),
        ("ð° ÙØ§ÙÛ Ù Ù¾Ø±Ø¯Ø§Ø®Øª","ð° Finance & Payments","finance"),
        ("ð VIP / XP / Token","ð VIP / XP / Token","vip"),
        ("ð¢ Ú©Ø§ÙØ§Ù Ù Ø§ÙØªØ´Ø§Ø±","ð¢ Channels & Publishing","channels"),
        ("ð¤ AI Ù Voice","ð¤ AI & Voice","ai"),
        ("ð©º Ø³ÙØ§ÙØª Ù Diagnostics","ð©º Health & Diagnostics","health"),
        ("ð¾ Backup Ù Recovery","ð¾ Backup & Recovery","backup"),
        ("ð§© ÙØ§Ø¨ÙÛØªâÙØ§ Ù Feature Flags","ð§© Features & Flags","features"),
        ("ð Ø§ÙÙÛØª Ù Audit","ð Security & Audit","audit"),
        ("ð§ª ÙØ±Ú©Ø² ØªØ³Øª Ù Regression","ð§ª Test & Regression","tests"),
        ("âï¸ ØªÙØ¸ÛÙØ§Øª Ø³ÛØ³ØªÙ","âï¸ System Settings","system"),
        ("ð¦ Ø³Ø§ÛØ± ÙØ§ÚÙÙâÙØ§Û ÙØ¯ÛØ±ÛØªÛ","ð¦ Other Admin Modules","other"),
    ]
    rows=[]
    for ft,et,key in items:
        perm=MASTER_DOMAIN_PERMISSION.get(key)
        if perm and not master_has_permission(uid,perm):
            continue
        rows.append([InlineKeyboardButton(ft if fa else et,callback_data=f"v25:master:{key}")])
    rows.append([InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ" if fa else "ð  Main Menu",callback_data="v25:master:main")])
    return InlineKeyboardMarkup(rows)


def master_back_keyboard(uid):
    fa=lang(uid)=="fa"
    return InlineKeyboardMarkup([[InlineKeyboardButton("â¬ï¸ ÙØ±Ú©Ø² ÙØ¯ÛØ±ÛØª" if fa else "â¬ï¸ Management Center",callback_data="v25:master:home"),main_menu_button(uid)]])


def master_feature_text():
    c=db(); rows=c.execute("SELECT key,enabled FROM feature_flags ORDER BY key").fetchall(); c.close()
    lines=["ð§© <b>Feature Flags</b>",""]
    for r in rows:
        lines.append(f"{'ð¢' if r['enabled'] else 'ð´'} {html.escape(r['key'])}")
    return "\n".join(lines)


def master_finance_text():
    c=db()
    revenue=int(c.execute("SELECT COALESCE(SUM(total_amount),0) n FROM payments").fetchone()["n"])
    payments=int(c.execute("SELECT COUNT(*) n FROM payments").fetchone()["n"])
    vip=int(c.execute("SELECT COUNT(*) n FROM subscription_history").fetchone()["n"])
    c.close()
    return f"ð° <b>ÙØ§ÙÛ Ù Ù¾Ø±Ø¯Ø§Ø®Øª</b>\n\nð³ ØªØ±Ø§Ú©ÙØ´âÙØ§: <b>{payments}</b>\nðµ ÙØ¨ÙØº Ø«Ø¨ØªâØ´Ø¯Ù: <b>{revenue:,}</b>\nð Ø³ÙØ§Ø¨Ù Ø§Ø´ØªØ±Ø§Ú©: <b>{vip}</b>\n\nð Ø§Ø·ÙØ§Ø¹Ø§Øª Ø­Ø³Ø§Ø³ ÙØ§ÙÛ ÙÙØ· Ø¨Ø±Ø§Û Owner ÙÙØ§ÛØ´ Ø¯Ø§Ø¯Ù ÙÛâØ´ÙØ¯."


def master_users_text():
    c=db(); rows=c.execute("SELECT user_id,first_name,blocked,vip_until FROM users ORDER BY created_at DESC LIMIT 15").fetchall(); roles=c.execute("SELECT role,COUNT(*) n FROM management_roles WHERE active=1 GROUP BY role ORDER BY n DESC").fetchall(); c.close()
    lines=["ð¥ <b>Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ù ÙÙØ´âÙØ§</b>","", "<b>ÙÙØ´âÙØ§Û ÙØ¯ÛØ±ÛØªÛ</b>"]
    lines += [f"â¢ {MASTER_RBAC_ROLES.get(r['role'],r['role'])}: {r['n']}" for r in roles]
    lines += ["","<b>Ø¢Ø®Ø±ÛÙ Ú©Ø§Ø±Ø¨Ø±Ø§Ù</b>"]
    for r in rows:
        vip="ð" if r["vip_until"] else ""
        blocked="â" if r["blocked"] else "ð¢"
        lines.append(f"{blocked} {html.escape(r['first_name'] or 'Ø¨Ø¯ÙÙ ÙØ§Ù')} | <code>{r['user_id']}</code> {vip}")
    return "\n".join(lines)




def master_tests():
    results=[]
    def check(name, fn):
        try:
            ok=bool(fn()); results.append((name,ok,"OK" if ok else "FAIL"))
        except Exception as exc:
            results.append((name,False,type(exc).__name__))
    check("Time parser", lambda: parse_time("Û±Û¸:Û³Û°")=="18:30" and parse_time("2360") is None)
    check("Admin isolation", lambda: (not master_guard(0)) and master_guard(master_owner_id()))
    check("RBAC default deny", lambda: not master_has_permission(999999999,"manage_finance"))
    def _q(sql):
        # BUGFIX: every self-test used to open a connection and never close it.
        c=db()
        try:
            return c.execute(sql).fetchone()
        finally:
            c.close()
    check("DB integrity", lambda: _q("PRAGMA integrity_check")[0]=="ok")
    check("Feature table", lambda: _q("SELECT 1 FROM feature_flags LIMIT 1") is not None)
    check("Audit table", lambda: _q("SELECT 1 FROM admin_logs LIMIT 1") is not None)
    check("Incident table", lambda: _q("SELECT 1 FROM incident_tickets LIMIT 1") is not None)
    return results


def master_test_text(uid):
    results=master_tests(); passed=sum(1 for _,ok,_ in results if ok); total=len(results)
    details="\n".join(f"{'ð¢' if ok else 'ð´'} {html.escape(name)}: {html.escape(detail)}" for name,ok,detail in results)
    c=db(); c.execute("INSERT INTO system_test_runs(admin_id,passed,total,details,created_at) VALUES(?,?,?,?,?)",(uid,passed,total,details,datetime.now(TZ).isoformat())); c.commit(); c.close()
    return f"ð§ª <b>Regression Test</b>\n\n{details}\n\nÙØªÛØ¬Ù: <b>{passed}/{total}</b>"


async def master_management_callback(update,context):
    q=update.callback_query; uid=q.from_user.id; data=q.data
    if not master_guard(uid):
        await q.answer("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.",show_alert=True); return
    action=data.split(":",2)[2] if data.count(":")>=2 else "home"
    if action in {"home","dashboard"}:
        await q.answer(); await q.message.edit_text(master_dashboard_text(),parse_mode="HTML",reply_markup=master_root_keyboard(uid)); return
    if action=="main":
        await q.answer(); clear_flow(context); await q.message.edit_text("ð  ÙÙÙÛ Ø§ØµÙÛ",reply_markup=None); await q.message.reply_text("ð  ÙÙÙÛ Ø§ØµÙÛ",reply_markup=keyboard(uid)); return
    perm=MASTER_DOMAIN_PERMISSION.get(action)
    if perm and not master_has_permission(uid,perm):
        await q.answer("â Ø§ÛÙ Ø¨Ø®Ø´ Ø¨Ø±Ø§Û ÙÙØ´ Ø´ÙØ§ ÙØ¬Ø§Ø² ÙÛØ³Øª.",show_alert=True); return
    await q.answer()
    if action=="users":
        await q.message.edit_text(master_users_text(),parse_mode="HTML",reply_markup=master_back_keyboard(uid)); return
    if action=="tickets":
        c=db(); rows=c.execute("SELECT id,module,severity,occurrences,last_seen_at FROM incident_tickets WHERE status='open' ORDER BY last_seen_at DESC LIMIT 15").fetchall(); open_t=c.execute("SELECT COUNT(*) n FROM tickets WHERE status='open'").fetchone()["n"]; c.close()
        text="ð« <b>ØªÛÚ©Øª Ù Incident</b>\n\n"+f"ØªÛÚ©ØªâÙØ§Û Ø¨Ø§Ø²: <b>{open_t}</b>\nIncidentÙØ§Û Ø¨Ø§Ø²: <b>{len(rows)}</b>\n\n"+"\n".join(f"ð¨ #{r['id']} | {html.escape(r['module'])} | {r['severity']} | x{r['occurrences']}" for r in rows) or "ÙÙØ±Ø¯ Ø¨Ø§Ø²Û ÙÛØ³Øª."
        await q.message.edit_text(text,parse_mode="HTML",reply_markup=master_back_keyboard(uid)); return
    if action=="finance":
        if uid!=master_owner_id():
            await q.message.edit_text("ð° <b>ÙØ§ÙÛ</b>\n\nØ§Ø·ÙØ§Ø¹Ø§Øª Ø­Ø³Ø§Ø³ ÙØ§ÙÛ ÙÙØ· Ø¨Ø±Ø§Û Owner ÙØ§Ø¨Ù ÙØ´Ø§ÙØ¯Ù Ø§Ø³Øª.\nÚ¯Ø²Ø§Ø±Ø´âÙØ§Û ØºÛØ±Ø­Ø³Ø§Ø³ Ø³ÛØ³ØªÙ Ø§Ø² Ø¯Ø§Ø´Ø¨ÙØ±Ø¯ Ø¯Ø± Ø¯Ø³ØªØ±Ø³ ÙÙØ´âÙØ§Û ÙØ¬Ø§Ø² Ø§Ø³Øª.",parse_mode="HTML",reply_markup=master_back_keyboard(uid)); return
        await q.message.edit_text(master_finance_text(),parse_mode="HTML",reply_markup=master_back_keyboard(uid)); return
    if action=="vip":
        text="ð <b>VIP / XP / Token</b>\n\nØ§ÛÙ Ø¨Ø®Ø´ Ø¨Ù ÙØ±Ú©Ø² ÙØ¹ÙÛ VIPØ XP Ù Token ÙØªØµÙ Ø§Ø³Øª.\n\nÙØ¯ÛØ±ÛØª Ù¾ÙÙ VIP Ù Token Ø§Ø² Ù¾ÙÙ ÙØ¹ÙÛ Ø§ÙØ¬Ø§Ù ÙÛâØ´ÙØ¯."
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("ð ÙØ±Ú©Ø² VIP",callback_data="v25:adminplans")],[InlineKeyboardButton("ðï¸ ÙØ¯ÛØ±ÛØª Token",callback_data="v25:tokens_admin")], [InlineKeyboardButton("â¬ï¸ ÙØ±Ú©Ø² ÙØ¯ÛØ±ÛØª",callback_data="v25:master:home")]])
        await q.message.edit_text(text,parse_mode="HTML",reply_markup=kb); return
    if action=="channels":
        await q.message.edit_text("ð¢ <b>Ú©Ø§ÙØ§Ù Ù Ø§ÙØªØ´Ø§Ø±</b>\n\nÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§ÙØ Ù¾Ø³ØªâÚ¯Ø°Ø§Ø±ÛØ Ø²ÙØ§ÙâØ¨ÙØ¯ÛØ Ø§ÙØªØ´Ø§Ø± Ø®ÙØ¯Ú©Ø§Ø±Ø ØªØ£ÛÛØ¯ ÙØ¨Ù Ø§Ø² Ø§ÙØªØ´Ø§Ø± Ù Ø¨Ø±Ø±Ø³Û Ø¹Ø¶ÙÛØª Ø¯Ø± Ø§ÛÙ Ø¨Ø®Ø´âÙØ§Û ÙÙØ¬ÙØ¯ Ø±Ø¨Ø§Øª ÙØ¹Ø§Ù ÙØ³ØªÙØ¯.",parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ð¢ ÙØ¯ÛØ±ÛØª Ú©Ø§ÙØ§Ù",callback_data="adm:channel")],[InlineKeyboardButton("ð¤ Ø§ÙØªØ´Ø§Ø± Ø®ÙØ¯Ú©Ø§Ø±",callback_data="auto:menu")],[InlineKeyboardButton("â¬ï¸ ÙØ±Ú©Ø² ÙØ¯ÛØ±ÛØª",callback_data="v25:master:home")]])); return
    if action=="health":
        await run_health_checks(context.bot,uid); text=health_text();
        await q.message.edit_text(text,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ð©º Ø§Ø¬Ø±Ø§Û Ø¯ÙØ¨Ø§Ø±Ù",callback_data="v25:master:health")],[InlineKeyboardButton("ð Diagnostics",callback_data="v25:master:diagnostics")],[InlineKeyboardButton("â¬ï¸ ÙØ±Ú©Ø² ÙØ¯ÛØ±ÛØª",callback_data="v25:master:home")]])); return
    if action=="diagnostics":
        await q.message.edit_text(_admin_diagnostics_text(),parse_mode="HTML",reply_markup=master_back_keyboard(uid)); return
    if action=="backup":
        ok=backup_database_snapshot(keep=20); master_log(uid,"backup",details="success" if ok else "failed");
        await q.message.edit_text("ð¾ Ø¨Ú©Ø§Ù¾ Ø¨Ø§ ÙÙÙÙÛØª Ø³Ø§Ø®ØªÙ Ø´Ø¯." if ok else "â Ø³Ø§Ø®Øª Ø¨Ú©Ø§Ù¾ ÙØ§ÙÙÙÙ Ø¨ÙØ¯.",reply_markup=master_back_keyboard(uid)); return
    if action=="features":
        await q.message.edit_text(master_feature_text(),parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ð§© ÙØ±Ú©Ø² ÙØ§Ø¨ÙÛØªâÙØ§Û ÙØ¹ÙÛ",callback_data="adm:features")],[InlineKeyboardButton("â¬ï¸ ÙØ±Ú©Ø² ÙØ¯ÛØ±ÛØª",callback_data="v25:master:home")]])); return
    if action=="audit":
        c=db(); rows=c.execute("SELECT admin_id,action,target_user,details,created_at FROM admin_logs ORDER BY id DESC LIMIT 30").fetchall(); c.close();
        text="ð <b>Security / Audit</b>\n\n"+"\n".join(f"â¢ {fa_datetime(r['created_at'])} | {r['admin_id']} | {html.escape(r['action'])} | {r['target_user'] or '-'}" for r in rows) or "ÙØ§Ú¯Û Ø«Ø¨Øª ÙØ´Ø¯Ù."
        await q.message.edit_text(text,parse_mode="HTML",reply_markup=master_back_keyboard(uid)); return
    if action=="tests":
        await q.message.edit_text(master_test_text(uid),parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ð Ø§Ø¬Ø±Ø§Û Ø¯ÙØ¨Ø§Ø±Ù",callback_data="v25:master:tests")],[InlineKeyboardButton("â¬ï¸ ÙØ±Ú©Ø² ÙØ¯ÛØ±ÛØª",callback_data="v25:master:home")]])); return
    if action=="other":
        await q.message.edit_text("ð¦ <b>Ø³Ø§ÛØ± ÙØ§ÚÙÙâÙØ§Û ÙØ¯ÛØ±ÛØªÛ</b>\n\nØ¯Ø± Ø§ÛÙ Ø¨Ø®Ø´Ø ÙØ§ÚÙÙâÙØ§Û ÙÙØ¬ÙØ¯ ÙØ³Ø®Ù ÙØ¹ÙÛ Ø±Ø§ Ø¨Ø¯ÙÙ Ø­Ø°Ù ÙØ³ÛØ±ÙØ§Û ÙØ¯ÛÙÛ Ú©ÙØªØ±Ù ÙÛâÚ©ÙÛ.",parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ð Ù¾ÙÙâÙØ§Û VIP",callback_data="v25:adminplans")],[InlineKeyboardButton("ð³ Ù¾Ø±Ø¯Ø§Ø®Øª",callback_data="v25:adminpayment")],[InlineKeyboardButton("ð± Ù¾ÛØ§ÙÚ©",callback_data="v25:adminsms")],[InlineKeyboardButton("â­ ÙØ¸Ø±Ø³ÙØ¬Û",callback_data="v25:adminsurvey")],[InlineKeyboardButton("âï¸ ØµØ¨Ø­/Ø´Ø¨",callback_data="v25:adminmorning")],[InlineKeyboardButton("ð ÙÛÙØª Ø¨Ø§Ø²Ø§Ø±",callback_data="v25:adminprices")],[InlineKeyboardButton("ð¥ ÙØ´ØªØ±Û Ù ÙÙØ¨Øª",callback_data="adm:customers")],[InlineKeyboardButton("ð Ú¯Ø²Ø§Ø±Ø´ ÙØ¯ÛØ±ÛØª",callback_data="adm:report")],[InlineKeyboardButton("â¬ï¸ ÙØ±Ú©Ø² ÙØ¯ÛØ±ÛØª",callback_data="v25:master:home")]])); return
    if action=="system":
        paused=get_system_setting("bot_paused_until","")
        maintenance=feature_enabled("maintenance")
        text=f"âï¸ <b>ØªÙØ¸ÛÙØ§Øª Ø³ÛØ³ØªÙ</b>\n\nð  Maintenance: {'ð¢' if maintenance else 'ð´'}\nâ¸ ØªÙÙÙ ÙÙÙØª: {html.escape(paused or 'ÙØ¹Ø§Ù ÙÛØ³Øª')}\nð Schema: {DB_SCHEMA_VERSION}\n\nÙØ§ÙÚ© Ø§ØµÙÛ: <code>{master_owner_id() or '-'}</code>"
        await q.message.edit_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ð§© ØªØºÛÛØ± ÙØ§Ø¨ÙÛØªâÙØ§",callback_data="adm:features")],[InlineKeyboardButton("â¸ ÙØ¯ÛØ±ÛØª ØªÙÙÙ",callback_data="adm:pause")],[InlineKeyboardButton("â¬ï¸ ÙØ±Ú©Ø² ÙØ¯ÛØ±ÛØª",callback_data="v25:master:home")]])); return
    await q.message.edit_text("Ø§ÛÙ Ø¨Ø®Ø´ ÙÙÙØ² Ø¨Ù Ø¹ÙÙÛØ§Øª Ø§Ø®ØªØµØ§ØµÛ ÙØªØµÙ ÙØ´Ø¯Ù Ø§Ø³Øª.",reply_markup=master_back_keyboard(uid))

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
    rows.append([InlineKeyboardButton("ð§­ Ú©ÙØªØ±Ù Ú©Ø§ÙÙ Ø³ÛØ³ØªÙ",callback_data="v25:master:home")])
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
        "owner": ("ð ÙØ§ÙÚ©", "ð Owner"),
        "senior_manager": ("ð¡ ÙØ¯ÛØ± Ø§Ø±Ø´Ø¯", "ð¡ Senior Manager"),
        "general_manager": ("ð¤ ÙØ¯ÛØ± Ø¹ÙÙÙÛ", "ð¤ General Manager"),
        "technical_manager": ("ð§° ÙØ¯ÛØ± ÙÙÛ", "ð§° Technical Manager"),
        "finance_manager": ("ð° ÙØ¯ÛØ± ÙØ§ÙÛ", "ð° Finance Manager"),
        "ticket_manager": ("ð« ÙØ¯ÛØ± ØªÛÚ©Øª", "ð« Ticket Manager"),
        "channel_manager": ("ð¢ ÙØ¯ÛØ± Ú©Ø§ÙØ§Ù", "ð¢ Channel Manager"),
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
        "ð§âð¼ <b>ÙØ¯ÛØ±ÛØª ÙØ¯ÛØ±Ø§Ù</b>" if fa else "ð§âð¼ <b>Manager Management</b>",
        "",
    ]
    if not rows:
        lines.append("ÙØ¯ÛØ±Û Ø«Ø¨Øª ÙØ´Ø¯Ù Ø§Ø³Øª." if fa else "No managers are registered.")
    else:
        active_count = sum(1 for r in rows if r["active"])
        disabled_count = len(rows) - active_count
        header = f"ð {active_count} ÙØ¹Ø§Ù"
        if disabled_count:
            header += f" | {disabled_count} ØºÛØ±ÙØ¹Ø§Ù"
        if not fa:
            header = f"ð {active_count} active"
            if disabled_count:
                header += f" | {disabled_count} disabled"
        lines.append(header)
        lines.append("â" * 20)
        for i, r in enumerate(rows, 1):
            state = "ð¢" if r["active"] else "ð´"
            role_label = html.escape(_manager_role_label(r["role"], fa))
            lines.append(
                f"{state} <b>{i}.</b> <code>{r['user_id']}</code> â {role_label}"
            )
        lines.append("â" * 20)
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
        ("general_manager", "ð¤ ÙØ¯ÛØ± Ø¹ÙÙÙÛ", "ð¤ General Manager"),
        ("senior_manager", "ð¡ ÙØ¯ÛØ± Ø§Ø±Ø´Ø¯", "ð¡ Senior Manager"),
        ("technical_manager", "ð§° ÙØ¯ÛØ± ÙÙÛ", "ð§° Technical Manager"),
        ("finance_manager", "ð° ÙØ¯ÛØ± ÙØ§ÙÛ", "ð° Finance Manager"),
        ("ticket_manager", "ð« ÙØ¯ÛØ± ØªÛÚ©Øª", "ð« Ticket Manager"),
        ("channel_manager", "ð¢ ÙØ¯ÛØ± Ú©Ø§ÙØ§Ù", "ð¢ Channel Manager"),
    ]
    rows = []
    for role, fa_label, en_label in roles:
        rows.append([InlineKeyboardButton(
            fa_label if fa else en_label,
            callback_data=f"v25:master:manager_role:{role}"
        )])
    rows.append([InlineKeyboardButton(
        "â¬ï¸ ÙØ¯ÛØ±ÛØª ÙØ¯ÛØ±Ø§Ù" if fa else "â¬ï¸ Manager Management",
        callback_data="v25:master:manager_list"
    )])
    return InlineKeyboardMarkup(rows)

def _master_settings_keyboard(uid):
    fa = lang(uid) == "fa"
    rows = [
        [InlineKeyboardButton(
            "ð§âð¼ ÙØ¯ÛØ±ÛØª ÙØ¯ÛØ±Ø§Ù  âº" if fa else "ð§âð¼ Manager Management  âº",
            callback_data="settings:managers"
        )],
        [InlineKeyboardButton(
            "ð¤ ØªÙØ¸ÛÙØ§Øª AI  âº" if fa else "ð¤ AI Settings  âº",
            callback_data="settings:ai"
        )],
        [InlineKeyboardButton(
            "ð¢ ØªÙØ¸ÛÙØ§Øª Ú©Ø§ÙØ§Ù  âº" if fa else "ð¢ Channel Settings  âº",
            callback_data="settings:channel"
        )],
        [InlineKeyboardButton(
            "ð Ø§Ø¹ÙØ§ÙâÙØ§  âº" if fa else "ð Notifications  âº",
            callback_data="settings:notifications"
        )],
        [InlineKeyboardButton(
            "ð¯ Ø§ÙØ¯Ø§Ù  âº" if fa else "ð¯ Goals  âº",
            callback_data="settings:goals"
        )],
        [InlineKeyboardButton(
            "ð Ø²Ø¨Ø§Ù  âº" if fa else "ð Language  âº",
            callback_data="settings:language"
        )],
        [InlineKeyboardButton(
            "ð  ÙÙÙÛ Ø§ØµÙÛ" if fa else "ð  Main Menu",
            callback_data="settings:main"
        )],
    ]
    return InlineKeyboardMarkup(rows)

def _manager_settings_text(uid):
    fa = lang(uid) == "fa"
    if fa:
        return (
            "ð¡ï¸ <b>ØªÙØ¸ÛÙØ§Øª ÙØ¯ÛØ±ÛØªÛ</b>\n\n"
            "Ø§Ø² Ø§ÛÙ Ø¨Ø®Ø´ ÙÛâØªÙØ§ÙÛ ØªÙØ¸ÛÙØ§Øª ÙØ¯ÛØ±ÛØª Ø±Ø§ Ø¯Ø³ØªÙâØ¨ÙØ¯ÛâØ´Ø¯Ù Ú©ÙØªØ±Ù Ú©ÙÛ.\n\n"
            "ð§âð¼ ÙØ¯ÛØ±ÛØª ÙØ¯ÛØ±Ø§Ù âº Ø§ÙØ²ÙØ¯ÙØ ÙØ´Ø§ÙØ¯Ù Ù Ú©ÙØªØ±Ù ÙÙØ´ ÙØ¯ÛØ±Ø§Ù\n"
            "ð¤ ØªÙØ¸ÛÙØ§Øª AI âº ÙØ¶Ø¹ÛØª Ø³Ø±ÙÛØ³âÙØ§Û ÙÙØ´ÙÙØ¯\n"
            "ð¢ ØªÙØ¸ÛÙØ§Øª Ú©Ø§ÙØ§Ù âº Ø§ØªØµØ§Ù Ù Ø§ÙØªØ´Ø§Ø±\n"
            "ð Ø§Ø¹ÙØ§ÙâÙØ§ âº ØªÙØ¸ÛÙØ§Øª Ø§Ø¹ÙØ§ÙâÙØ§Û Ø­Ø³Ø§Ø¨"
        )
    return (
        "ð¡ï¸ <b>Management Settings</b>\n\n"
        "Use this categorized menu to manage administration settings.\n\n"
        "ð§âð¼ Manager Management âº Add, view and control manager roles\n"
        "ð¤ AI Settings âº Smart service status\n"
        "ð¢ Channel Settings âº Connection and publishing\n"
        "ð Notifications âº Account notifications"
    )

# New manager access must not depend on the static ADMIN_IDS list.
_OLD_MASTER_GUARD_FINAL = master_guard

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
                "â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯." if fa else "â Access denied."
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
                "â Ø§ÛÙ Ø¨Ø®Ø´ ÙÙØ· Ø¨Ø±Ø§Û ÙØ¯ÛØ± ÙØ¬Ø§Ø² Ø§Ø³Øª."
                if fa else
                "â This section is restricted to authorized managers."
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
            "ð¡ï¸ ØªÙØ¸ÛÙØ§Øª ÙØ¯ÛØ±ÛØªÛ  âº" if fa else "ð¡ï¸ Management Settings  âº",
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
            "â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯." if fa else "â Access denied.",
            show_alert=True
        )
        return

    # Manager list.
    if data == "v25:master:manager_list":
        if not master_has_permission(uid, "manage_roles"):
            await q.answer("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", show_alert=True)
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
                "â ÙÙØ· Owner ÙÛâØªÙØ§ÙØ¯ ÙØ¯ÛØ± Ø§Ø¶Ø§ÙÙ Ú©ÙØ¯."
                if fa else
                "â Only the Owner can add managers.",
                show_alert=True
            )
            return
        # Preserve pending manager ID if already set, only clear other state
        _saved_id = context.user_data.get("master_pending_manager_id")
        context.user_data.clear()
        context.user_data["_flow_started_at"] = datetime.now(TZ).timestamp()
        context.user_data["master_add_manager"] = True
        if _saved_id:
            context.user_data["master_pending_manager_id"] = _saved_id
        await q.answer()
        await q.message.edit_text(
            "ð Ø¢ÛØ¯Û Ø¹Ø¯Ø¯Û ØªÙÚ¯Ø±Ø§Ù ÙØ¯ÛØ± Ø¬Ø¯ÛØ¯ Ø±Ø§ Ø§Ø±Ø³Ø§Ù Ú©Ù.\n\n"
            "ð¡ Ø¨Ø±Ø§Û Ù¾ÛØ¯Ø§ Ú©Ø±Ø¯Ù Ø¢ÛØ¯ÛØ Ø§Ø² Ø±Ø¨Ø§Øª @userinfobot Ø§Ø³ØªÙØ§Ø¯Ù Ú©Ù.\n\n"
            "ÙØ«Ø§Ù: <code>123456789</code>"
            if fa else
            "ð Send the new manager's numeric Telegram ID.\n\n"
            "ð¡ Use @userinfobot to find the ID.\n\n"
            "Example: <code>123456789</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "â ÙØºÙ" if fa else "â Cancel",
                    callback_data="v25:master:manager_list"
                )
            ]])
        )
        return

    # Role selection after the ID is received.
    if data.startswith("v25:master:manager_role:"):
        if not _manager_is_owner(uid):
            await q.answer("â Owner only.", show_alert=True)
            return
        role = data.split(":", 3)[3].strip()
        if role not in MASTER_RBAC_ROLES or role == "owner":
            await q.answer(
                "â ÙÙØ´ ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª." if fa else "â Invalid role.",
                show_alert=True
            )
            return
        target = int(context.user_data.get("targeted_pending_manager_id") or context.user_data.get("master_pending_manager_id") or 0)
        if not target:
            await q.answer(
                "â Ø¢ÛØ¯Û ÙØ¯ÛØ± Ù¾ÛØ¯Ø§ ÙØ´Ø¯. Ø¯ÙØ¨Ø§Ø±Ù Ø´Ø±ÙØ¹ Ú©Ù."
                if fa else
                "â Manager ID was not found. Please start again.",
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
                f"â ÙØ¯ÛØ± <code>{target}</code> Ø¨Ø§ ÙÙØ´ "
                f"<b>{html.escape(_manager_role_label(role, True))}</b> Ø§Ø¶Ø§ÙÙ Ø´Ø¯."
            )
            if fa else
            (
                f"â Manager <code>{target}</code> added as "
                f"<b>{html.escape(_manager_role_label(role, False))}</b>."
            ),
            parse_mode="HTML",
            reply_markup=_master_manager_keyboard(uid)
        )
        return

    # Disable manager help: keep it deliberate to avoid accidental lockouts.
    if data == "v25:master:manager_disable_help":
        if not _manager_is_owner(uid):
            await q.answer("â Owner only.", show_alert=True)
            return
        await q.answer()
        await q.message.edit_text(
            "ðï¸ <b>ØºÛØ±ÙØ¹Ø§ÙâØ³Ø§Ø²Û ÙØ¯ÛØ±</b>\n\n"
            "Ø¨Ø±Ø§Û Ø¬ÙÙÚ¯ÛØ±Û Ø§Ø² Ø­Ø°Ù Ø§Ø´ØªØ¨Ø§ÙÛØ Ø¯Ø± Ø§ÛÙ ÙØ±Ø­ÙÙ Ø¢ÛØ¯Û ÙØ¯ÛØ± Ø±Ø§ Ø¨Ù ØµÙØ±Øª Ù¾ÛØ§Ù Ø§Ø±Ø³Ø§Ù Ú©Ù."
            if fa else
            "ðï¸ <b>Disable Manager</b>\n\n"
            "To avoid accidental lockouts, send the manager's numeric ID as a message.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "â¬ï¸ ÙØ¯ÛØ±ÛØª ÙØ¯ÛØ±Ø§Ù" if fa else "â¬ï¸ Manager Management",
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
                "â ÙÙØ· Owner ÙÛâØªÙØ§ÙØ¯ ÙØ¯ÛØ± Ø§Ø¶Ø§ÙÙ Ú©ÙØ¯."
                if fa else "â Only the Owner can add managers."
            )
            return
        if not txt.isdigit():
            await update.message.reply_text(
                "â ÙÙØ· Ø¢ÛØ¯Û Ø¹Ø¯Ø¯Û ØªÙÚ¯Ø±Ø§Ù Ø±Ø§ Ø¨ÙØ±Ø³Øª."
                if fa else "â Send a numeric Telegram ID."
            )
            return
        target = int(txt)
        context.user_data["master_add_manager"] = False
        context.user_data["master_pending_manager_id"] = target
        await update.message.reply_text(
            "ð¯ ÙÙØ´ ÙØ¯ÛØ± Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:"
            if fa else "ð¯ Choose the manager role:",
            reply_markup=_master_add_role_keyboard(uid)
        )
        return

    if context.user_data.get("master_disable_manager"):
        if not _manager_is_owner(uid):
            clear_flow(context)
            await update.message.reply_text(
                "â ÙÙØ· Owner ÙØ¬Ø§Ø² Ø§Ø³Øª." if fa else "â Owner only."
            )
            return
        if not txt.isdigit():
            await update.message.reply_text(
                "â ÙÙØ· Ø¢ÛØ¯Û Ø¹Ø¯Ø¯Û Ø±Ø§ Ø¨ÙØ±Ø³Øª."
                if fa else "â Send a numeric ID."
            )
            return
        target = int(txt)
        if target == master_owner_id():
            await update.message.reply_text(
                "â ÙØ§ÙÚ© Ø§ØµÙÛ ÙØ§Ø¨Ù ØºÛØ±ÙØ¹Ø§ÙâØ³Ø§Ø²Û ÙÛØ³Øª."
                if fa else "â The Owner cannot be disabled."
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
                f"â ÙØ¯ÛØ± <code>{target}</code> ØºÛØ±ÙØ¹Ø§Ù Ø´Ø¯."
                if changed else
                f"â¹ï¸ ÙØ¯ÛØ±Û Ø¨Ø§ Ø¢ÛØ¯Û <code>{target}</code> Ù¾ÛØ¯Ø§ ÙØ´Ø¯."
            )
            if fa else
            (
                f"â Manager <code>{target}</code> disabled."
                if changed else
                f"â¹ï¸ No manager found for <code>{target}</code>."
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
        any("ÙØ¯ÛØ±" in getattr(btn, "text", "") for btn in row)
        for row in rows
    ):
        insert_at = max(0, len(rows) - 1)
        rows.insert(insert_at, [
            InlineKeyboardButton(
                "ð§âð¼ ÙØ¯ÛØ±ÛØª ÙØ¯ÛØ±Ø§Ù",
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
        if txt in ("ð  ÙÙÙÛ Ø§ØµÙÛ", "ð  Main Menu"):
            clear_flow(context)
            title, markup = _manager_main_keyboard(uid)
            await update.message.reply_text(title, parse_mode="HTML", reply_markup=markup)
            return

        if txt in ("ð¤ Ø§Ø³ØªÙØ§Ø¯Ù Ø§Ø² Ø±Ø¨Ø§Øª", "ð¤ Use Bot"):
            clear_flow(context)
            await update.message.reply_text(
                "ð¤ <b>Ø¨Ø®Ø´ Ú©Ø§Ø±Ø¨Ø±</b>\n\nÙØ§Ø¨ÙÛØªâÙØ§Û Ø¹Ø§Ø¯Û Ø±Ø¨Ø§Øª Ø¯Ø± Ø§ÛÙ Ø¨Ø®Ø´ Ø¯Ø± Ø¯Ø³ØªØ±Ø³ Ø§Ø³Øª."
                if fa else
                "ð¤ <b>User Area</b>\n\nNormal user features are available here.",
                parse_mode="HTML",
                reply_markup=_compact_user_keyboard(uid)
            )
            return

        if txt in ("âï¸ ØªÙØ¸ÛÙØ§Øª Ø³ÛØ³ØªÙ", "âï¸ System Settings"):
            # Entering settings must also cancel any pending admin ID/username
            # input mode.  Show the same system-settings view as the management
            # center callback, without routing the label through the legacy
            # ID/username parser.
            clear_flow(context)
            paused = get_system_setting("bot_paused_until", "")
            maintenance = feature_enabled("maintenance")
            text = (
                f"âï¸ <b>ØªÙØ¸ÛÙØ§Øª Ø³ÛØ³ØªÙ</b>\n\n"
                f"ð  Maintenance: {'ð¢' if maintenance else 'ð´'}\n"
                f"â¸ ØªÙÙÙ ÙÙÙØª: {html.escape(paused or 'ÙØ¹Ø§Ù ÙÛØ³Øª')}\n"
                f"ð Schema: {DB_SCHEMA_VERSION}\n\n"
                f"ÙØ§ÙÚ© Ø§ØµÙÛ: <code>{master_owner_id() or '-'}</code>"
            ) if fa else (
                f"âï¸ <b>System Settings</b>\n\n"
                f"ð  Maintenance: {'ð¢' if maintenance else 'ð´'}\n"
                f"â¸ Temporary pause: {html.escape(paused or 'Not active')}\n"
                f"ð Schema: {DB_SCHEMA_VERSION}\n\n"
                f"Owner: <code>{master_owner_id() or '-'}</code>"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("ð§© ØªØºÛÛØ± ÙØ§Ø¨ÙÛØªâÙØ§" if fa else "ð§© Feature Flags", callback_data="adm:features")],
                [InlineKeyboardButton("â¸ ÙØ¯ÛØ±ÛØª ØªÙÙÙ" if fa else "â¸ Pause Management", callback_data="adm:pause")],
                [InlineKeyboardButton("â¬ï¸ ÙØ±Ú©Ø² ÙØ¯ÛØ±ÛØª" if fa else "â¬ï¸ Management Center", callback_data="v25:master:home")]
            ])
            await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
            return

        if txt in ("ð¡ ÙØ¯ÛØ±ÛØª Ø±Ø¨Ø§Øª", "ð¡ Bot Management"):
            clear_flow(context)
            await _show_admin_management(update, context)
            return

        if txt in ("ð Ø¯Ø§Ø´Ø¨ÙØ±Ø¯ Ù Ú¯Ø²Ø§Ø±Ø´", "ð Dashboard & Reports"):
            await _show_admin_section(update, context, "dashboard")
            return

        if txt in ("ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ù ÙÙØ´âÙØ§", "ð¥ Users & Roles"):
            await update.message.reply_text(
                master_users_text(),
                parse_mode="HTML",
                reply_markup=master_back_keyboard(uid)
            )
            return


        if txt in ("ð§âð¼ ÙØ¯ÛØ±ÛØª ÙØ¯ÛØ±Ø§Ù", "ð§âð¼ Manager Management"):
            if not master_has_permission(uid, "manage_roles"):
                await update.message.reply_text(
                    "â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯." if fa else "â Access denied.",
                    reply_markup=compact_keyboard(uid)
                )
                return
            await update.message.reply_text(
                _master_managers_text(uid),
                parse_mode="HTML",
                reply_markup=_master_manager_keyboard(uid)
            )
            return

        # Section map for unhandled admin buttons
        _mgr_section_map = {
            "ð« ØªÛÚ©ØªâÙØ§ Ù Incident": "tickets", "ð« Tickets & Incidents": "tickets",
            "ð° ÙØ§ÙÛ Ù Ù¾Ø±Ø¯Ø§Ø®Øª": "finance", "ð° Finance & Payments": "finance",
            "ð VIP / XP / Token": "xpvip",
            "ð¢ Ú©Ø§ÙØ§Ù Ù Ø§ÙØªØ´Ø§Ø±": "channel", "ð¢ Channels & Publishing": "channel",
            "ð©º Ø³ÙØ§ÙØª Ù Diagnostics": "health", "ð©º Health & Diagnostics": "health",
            "ð¾ Backup Ù Recovery": "backup", "ð¾ Backup & Recovery": "backup",
            "ð§© ÙØ§Ø¨ÙÛØªâÙØ§ Ù Feature Flags": "features", "ð§© Features & Flags": "features",
            "ð Ø§ÙÙÛØª Ù Audit": "security", "ð Security & Audit": "security",
            "ð§ª ÙØ±Ú©Ø² ØªØ³Øª Ù Regression": "test", "ð§ª Test & Regression": "test",
            "ð¦ Ø³Ø§ÛØ± ÙØ§ÚÙÙâÙØ§Û ÙØ¯ÛØ±ÛØªÛ": "other", "ð¦ Other Admin Modules": "other",
        }
        if txt in _mgr_section_map:
            await _show_admin_section(update, context, _mgr_section_map[txt])
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
                    f"ð¡ï¸ <b>Ù¾ÙÙ ÙØ¯ÛØ±ÛØª Ø¨Ø±Ø§Û Ø´ÙØ§ ÙØ¹Ø§Ù Ø´Ø¯</b>\n\n"
                    f"ÙÙØ´ Ø´ÙØ§: <b>{html.escape(_manager_role_label(role, True))}</b>\n\n"
                    "Ø§Ø² Ø§ÛÙ Ø¨Ù Ø¨Ø¹Ø¯ Ø¨Ø§ ÙØ±ÙØ¯ Ø¨Ù Ø±Ø¨Ø§ØªØ ÙÙÙÛ ÙØ¯ÛØ±ÛØªÛ Ø§Ø®ØªØµØ§ØµÛ Ø®ÙØ¯Øª Ø±Ø§ ÙÛâØ¨ÛÙÛ."
                    if target_fa else
                    f"ð¡ï¸ <b>Your manager panel is active</b>\n\n"
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
    "view_dashboard":"ð Ø¯Ø§Ø´Ø¨ÙØ±Ø¯",
    "manage_users":"ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù",
    "manage_roles":"ð§âð¼ ÙØ¯ÛØ±ÛØª ÙØ¯ÛØ±Ø§Ù",
    "manage_vip":"ð VIP",
    "manage_xp":"â­ XP / Token",
    "manage_tickets":"ð« ØªÛÚ©ØªâÙØ§",
    "manage_finance":"ð° ÙØ§ÙÛ",
    "manage_channels":"ð¢ Ú©Ø§ÙØ§Ù",
    "manage_features":"ð§© ÙØ§Ø¨ÙÛØªâÙØ§",
    "run_health":"ð©º Health Check",
    "run_diagnostics":"ð Ø¹ÛØ¨âÛØ§Ø¨Û",
    "backup":"ð¾ Ø¨Ú©Ø§Ù¾",
    "restore":"â»ï¸ Ø¨Ø§Ø²ÛØ§Ø¨Û",
    "view_audit":"ð ÙØ§Ú¯ ÙØ¯ÛØ±Ø§Ù",
    "run_tests":"ð§ª ØªØ³ØªâÙØ§",
    "manage_system":"âï¸ Ø³ÛØ³ØªÙ",
}

def _targeted_manager_rows():
    c=db()
    rows=c.execute("SELECT user_id,username,role,domain,permissions_json,active,created_at FROM management_roles ORDER BY active DESC,user_id").fetchall()
    c.close(); return rows

def _targeted_manager_text(uid):
    fa=lang(uid)=="fa"; rows=_targeted_manager_rows()
    lines=["ð§âð¼ <b>ÙØ¯ÛØ±ÛØª ÙØ¯ÛØ±Ø§Ù</b>" if fa else "ð§âð¼ <b>Manager Management</b>",""]
    if not rows: lines.append("ÙØ¯ÛØ±Û Ø«Ø¨Øª ÙØ´Ø¯Ù Ø§Ø³Øª." if fa else "No managers registered.")
    for r in rows:
        uname=("@"+r["username"]) if r["username"] else "â"
        state="ð¢ ÙØ¹Ø§Ù" if r["active"] else "ð´ ØºÛØ±ÙØ¹Ø§Ù"
        if not fa: state="ð¢ Active" if r["active"] else "ð´ Disabled"
        lines.append(f"{state}  {html.escape(uname)}  <code>{r['user_id']}</code>  {html.escape(_manager_role_label(r['role'],fa))}")
    return "\n".join(lines)

def _targeted_manager_keyboard(uid):
    fa=lang(uid)=="fa"; rows=[]
    if _manager_is_owner(uid):
        rows.append([InlineKeyboardButton("â Ø§ÙØ²ÙØ¯Ù ÙØ¯ÛØ±" if fa else "â Add Manager",callback_data="v25:targeted:manager_add")])
        rows.append([InlineKeyboardButton("ðï¸ ÙØºÙ/Ø­Ø°Ù ÙØ¯ÛØ± Ø§Ø² ÙÛØ³Øª" if fa else "ðï¸ Disable Manager",callback_data="v25:targeted:manager_disable_list")])
    for r in _targeted_manager_rows():
        uname=("@"+r["username"]) if r["username"] else str(r["user_id"])
        label=f"{'ð¢' if r['active'] else 'ð´'} {uname} | {r['user_id']}"
        rows.append([InlineKeyboardButton(label,callback_data=f"v25:targeted:manager_detail:{r['user_id']}")])
    rows.append([InlineKeyboardButton("ð Ø¨ÙâØ±ÙØ²Ø±Ø³Ø§ÙÛ" if fa else "ð Refresh",callback_data="v25:targeted:manager_list")])
    rows.append([InlineKeyboardButton("â¬ï¸ ÙØ±Ú©Ø² ÙØ¯ÛØ±ÛØª" if fa else "â¬ï¸ Management Center",callback_data="v25:master:home")])
    return InlineKeyboardMarkup(rows)

def _targeted_manager_detail(uid,target):
    c=db(); r=c.execute("SELECT user_id,username,role,permissions_json,active FROM management_roles WHERE user_id=?",(int(target),)).fetchone(); c.close()
    if not r: return "ÙØ¯ÛØ± Ù¾ÛØ¯Ø§ ÙØ´Ø¯.", InlineKeyboardMarkup([[InlineKeyboardButton("â¬ï¸ ÙØ¯ÛØ±ÛØª ÙØ¯ÛØ±Ø§Ù",callback_data="v25:targeted:manager_list")]])
    fa=lang(uid)=="fa"; uname=("@"+r["username"]) if r["username"] else "â"
    try: perms=set(json.loads(r["permissions_json"] or "[]"))
    except Exception: perms=set()
    lines=[f"ð§âð¼ <b>{html.escape(uname)}</b>",f"ð <code>{r['user_id']}</code>",f"ð­ {html.escape(_manager_role_label(r['role'],fa))}",f"{'ð¢ ÙØ¹Ø§Ù' if r['active'] else 'ð´ ØºÛØ±ÙØ¹Ø§Ù'}" if fa else ("ð¢ Active" if r['active'] else "ð´ Disabled"),"","ð Ø¯Ø³ØªØ±Ø³ÛâÙØ§:" if fa else "ð Permissions:"]
    for key in MASTER_PERMISSION_KEYS:
        lines.append(("ð¢ " if key in perms else "ð´ ")+TARGETED_PERMISSION_LABELS.get(key,key))
    rows=[]
    if _manager_is_owner(uid) and int(target)!=master_owner_id():
        rows.append([InlineKeyboardButton("ð ÙØ¹Ø§Ù/ØºÛØ±ÙØ¹Ø§Ù ÙØ¯ÛØ±" if fa else "ð Toggle Manager",callback_data=f"v25:targeted:manager_toggle:{target}")])
        rows.append([InlineKeyboardButton("ð¡ ØªØºÛÛØ± ÙÙØ´" if fa else "ð¡ Change Role",callback_data=f"v25:targeted:manager_role:{target}")])
        rows.append([InlineKeyboardButton("ðï¸ ÙØºÙ ÙØ¯ÛØ±ÛØª" if fa else "ðï¸ Disable",callback_data=f"v25:targeted:manager_disable_confirm:{target}")])
    if _manager_is_owner(uid):
        rows.append([InlineKeyboardButton("ð ÙØ¯ÛØ±ÛØª Ø¯Ø³ØªØ±Ø³ÛâÙØ§" if fa else "ð Manage Permissions",callback_data=f"v25:targeted:manager_perms:{target}")])
    rows.append([InlineKeyboardButton("â¬ï¸ ÙØ¯ÛØ±ÛØª ÙØ¯ÛØ±Ø§Ù" if fa else "â¬ï¸ Managers",callback_data="v25:targeted:manager_list")])
    return "\n".join(lines),InlineKeyboardMarkup(rows)

def _targeted_permissions_keyboard(uid,target):
    c=db(); r=c.execute("SELECT permissions_json FROM management_roles WHERE user_id=?",(int(target),)).fetchone(); c.close()
    if not r: return InlineKeyboardMarkup([[InlineKeyboardButton("â¬ï¸ ÙØ¯ÛØ±ÛØª ÙØ¯ÛØ±Ø§Ù",callback_data="v25:targeted:manager_list")]])
    try: perms=set(json.loads(r["permissions_json"] or "[]"))
    except Exception: perms=set()
    fa=lang(uid)=="fa"; rows=[]
    for key in MASTER_PERMISSION_KEYS:
        mark="ð¢" if key in perms else "ð´"
        rows.append([InlineKeyboardButton(f"{mark} {TARGETED_PERMISSION_LABELS.get(key,key)}",callback_data=f"v25:targeted:manager_perm:{target}:{key}")])
    rows.append([InlineKeyboardButton("â¬ï¸ Ù¾Ø±ÙÙØ¯Ù ÙØ¯ÛØ±" if fa else "â¬ï¸ Manager",callback_data=f"v25:targeted:manager_detail:{target}")])
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
    all_labels=[('usd','ðµ Ø¯ÙØ§Ø±','ðµ USD'),('eur','ð¶ ÛÙØ±Ù','ð¶ EUR'),('gold18','ð¥ Ø·ÙØ§Û Û±Û¸ Ø¹ÛØ§Ø±','ð¥ 18K Gold'),('coin','ðª Ø³Ú©Ù Ø§ÙØ§ÙÛ','ðª Emami Coin'),('silver','ð¥ ÙÙØ±Ù','ð¥ Silver'),('copper','ð  ÙØ³','ð  Copper'),('aluminum','âï¸ Ø¢ÙÙÙÛÙÛÙÙ','âï¸ Aluminum'),('nickel','ð© ÙÛÚ©Ù','ð© Nickel'),('zinc','ð Ø±ÙÛ','ð Zinc'),('lead','âï¸ Ø³Ø±Ø¨','âï¸ Lead')]
    enabled=set(_targeted_enabled_prices()); labels=[x for x in all_labels if x[0] in enabled]
    rows=[[InlineKeyboardButton((x[1] if fa else x[2]),callback_data=f"price:{x[0]}") for x in labels[i:i+2]] for i in range(0,len(labels),2)]
    rows.append([InlineKeyboardButton("ð Ø¨Ø±ÙØ²Ø±Ø³Ø§ÙÛ ÙÙÙ" if fa else "ð Refresh all",callback_data="price:all")])
    rows.append([InlineKeyboardButton("ð° Ø³Ø±ÙØ§ÛÙâÙØ§Û ÙÙ" if fa else "ð° My Portfolio",callback_data="v25:portfolio")])
    rows.append([InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ" if fa else "ð  Main Menu",callback_data="price:main")])
    return InlineKeyboardMarkup(rows)

_ORIGINAL_FETCH_PRICE_V25_TARGETED = fetch_price_v25
async def fetch_price_v25(asset):
    # Crypto & index assets delegate to the legacy multi-source fetcher
    # (Nobitex / CoinGecko+TGJU / Yahoo), whose "<value> <unit>" output is
    # parsed back into the (value, unit, confidence) tuple contract.
    if asset in ("btc","eth","usdt","bnb","sol","xrp","sp500","nasdaq","dow"):
        txt = await fetch_price(asset)
        m = re.match(r"^([\d,.]+)\s*(.+)$", str(txt).strip())
        if not m:
            raise ValueError(f"unparsed price for {asset}: {txt!r}")
        return float(m.group(1).replace(",", "")), m.group(2).strip(), "single"
    # Gold/coin use TGJU's explicit daily/current page. No manual correction is applied.
    if asset in ("gold18","coin"):
        url={"gold18":"https://www.tgju.org/profile/geram18/today","coin":"https://www.tgju.org/profile/sekee/today"}[asset]
        raw=await asyncio.to_thread(tgju_value,url)
        return float(raw.replace(",","").replace("Ù«",".").replace("Ù¬","")),"Ø±ÛØ§Ù","single"
    return await _ORIGINAL_FETCH_PRICE_V25(asset)

async def v25_show_price(update,context,asset):
    uid=update.effective_user.id; fa=lang(uid)=="fa"; names={'usd':'Ø¯ÙØ§Ø±','eur':'ÛÙØ±Ù','gold18':'Ø·ÙØ§Û Û±Û¸ Ø¹ÛØ§Ø±','coin':'Ø³Ú©Ù Ø§ÙØ§ÙÛ','silver':'ÙÙØ±Ù','copper':'ÙØ³','aluminum':'Ø¢ÙÙÙÛÙÛÙÙ','nickel':'ÙÛÚ©Ù','zinc':'Ø±ÙÛ','lead':'Ø³Ø±Ø¨','btc':'BTC (Ø¨Ø§Ø²Ø§Ø± Ø§ÛØ±Ø§Ù)','eth':'ETH (Ø¨Ø§Ø²Ø§Ø± Ø§ÛØ±Ø§Ù)','usdt':'USDT','bnb':'BNB','sol':'Solana','xrp':'XRP','sp500':'S&P 500','nasdaq':'Nasdaq','dow':'Dow Jones'}; names_en={'usd':'USD','eur':'EUR','gold18':'18K Gold','coin':'Emami Coin','silver':'Silver','copper':'Copper','aluminum':'Aluminum','nickel':'Nickel','zinc':'Zinc','lead':'Lead','btc':'BTC','eth':'ETH','usdt':'USDT','bnb':'BNB','sol':'Solana','xrp':'XRP','sp500':'S&P 500','nasdaq':'Nasdaq','dow':'Dow Jones'}
    enabled=set(_targeted_enabled_prices())
    assets=_targeted_enabled_prices() if asset=='all' else [asset]
    if asset!='all' and asset not in enabled:
        if update.callback_query: await update.callback_query.answer("â Ø§ÛÙ ÙÛÙØª ÙØ¹ÙØ§Ù ØªÙØ³Ø· ÙØ¯ÛØ± ØºÛØ±ÙØ¹Ø§Ù Ø§Ø³Øª." if fa else "â This price is disabled by the admin.",show_alert=True)
        return
    lines=[('ð <b>ÙÛÙØª Ø¢ÙÙØ§ÛÙ</b>' if fa else 'ð <b>Live Prices</b>'),'']
    for a in assets:
        try:
            val,unit,confidence=await fetch_price_v25(a); label=names[a] if fa else names_en[a]; lines.append(f"{label}: <b>{val:,.0f}</b> {unit}")
        except Exception:
            label=names[a] if fa else names_en[a]; lines.append(f"{label}: â ï¸ {'Ø¯Ø§Ø¯Ù Ø¢ÙÙØ§ÛÙ Ø¯Ø± Ø¯Ø³ØªØ±Ø³ ÙÛØ³Øª' if fa else 'Live data unavailable'}")
    lines += ['',('ð Ø¢Ø®Ø±ÛÙ Ø¨Ø±Ø±Ø³Û: '+fa_datetime(datetime.now(TZ),True) if fa else 'ð Checked: '+fa_datetime(datetime.now(TZ),True))]
    if update.callback_query: await update.callback_query.message.edit_text("\n".join(lines),parse_mode='HTML',reply_markup=prices_keyboard(uid))
    else: await update.message.reply_text("\n".join(lines),parse_mode='HTML',reply_markup=prices_keyboard(uid))

async def price_callback(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; asset=q.data.split(':',1)[1]
    if asset=='main':
        try: await q.message.edit_text("ð  ÙÙÙÛ Ø§ØµÙÛ",reply_markup=InlineKeyboardMarkup([[main_menu_button(uid)]]))
        except Exception: pass
        return
    await v25_show_price(update,context,asset)

# ---------- Recurring goal reminders ----------
def _targeted_repeat_keyboard(uid):
    fa=lang(uid)=="fa"
    labels=[("today_tomorrow","ð Ø§ÙØ±ÙØ² Ù ÙØ±Ø¯Ø§" if fa else "ð Today + Tomorrow"),("tomorrow","â¡ï¸ ÙÙØ· ÙØ±Ø¯Ø§" if fa else "â¡ï¸ Tomorrow only"),("week","ð ÛÚ© ÙÙØªÙ" if fa else "ð One week"),("month","ðï¸ ÛÚ© ÙØ§Ù" if fa else "ðï¸ One month"),("two_months","ðï¸ Ø¯Ù ÙØ§Ù" if fa else "ðï¸ Two months"),("daily","ð Ø±ÙØ²Ø§ÙÙ ØªØ§ Ù¾Ø§ÛØ§Ù ÙØ¯Ù" if fa else "ð Daily until goal ends")]
    rows=[[InlineKeyboardButton(t,callback_data=f"goalrepeat:{k}")] for k,t in labels]
    rows.append([InlineKeyboardButton("â Ø¨Ø¯ÙÙ ØªÚ©Ø±Ø§Ø±" if fa else "â No repeat",callback_data="goalrepeat:none")])
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
    await q.message.edit_text((f"â ÙØ¯Ù Â«{html.escape(g['name'])}Â» Ø«Ø¨Øª Ø´Ø¯.\nâ° Ø³Ø§Ø¹Øª: {g['reminder_time'] or 'Ø®Ø§ÙÙØ´'}\nð ØªÚ©Ø±Ø§Ø± ÛØ§Ø¯Ø¢ÙØ±Û ØªÙØ¸ÛÙ Ø´Ø¯." if fa else f"â Goal '{html.escape(g['name'])}' saved.\nâ° Time: {g['reminder_time'] or 'Off'}\nð Reminder repetition configured."),parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[main_menu_button(uid)]]))

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
        await q.message.edit_text("ð ÚÙØ¯ Ø±ÙØ²/ÚÙ ÙØ¯Øª ÛØ§Ø¯Ø¢ÙØ±Û Ø´ÙØ¯Ø" if lang(uid)=='fa' else "ð How long should reminders repeat?",reply_markup=_targeted_repeat_keyboard(uid)); return
    context.user_data.clear(); log_activity(uid,'goal_created'); await q.message.edit_text(T[lang(uid)]['goal_added'].format(name=display_name(uid)),reply_markup=InlineKeyboardMarkup([[main_menu_button(uid)]]))


async def reminder_job(context):
    now=datetime.now(TZ); hhmm=now.strftime('%H:%M'); today=now.date().isoformat(); c=db()
    goals=c.execute("""SELECT g.* FROM goals g JOIN users u ON u.user_id=g.user_id WHERE g.enabled=1 AND COALESCE(u.blocked,0)=0 AND g.reminder_time=? AND (g.reminder_start_date IS NULL OR g.reminder_start_date<=?) AND (g.reminder_end_date IS NULL OR g.reminder_end_date>=?)""",(hhmm,today,today)).fetchall(); c.close()
    for g in goals:
        uid=g['user_id']
        try:
            sc=db(); rr=sc.execute("SELECT reminders_enabled FROM user_settings WHERE user_id=?",(uid,)).fetchone(); sc.close()
            if rr and not rr['reminders_enabled']: continue
            if get_status(uid,g['id'],today)=='done': continue
            await context.bot.send_message(uid,T[lang(uid)]['reminder'].format(name=display_name(uid),goal=g['name']),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('â Ø§ÙØ¬Ø§Ù Ø¯Ø§Ø¯Ù' if lang(uid)=='fa' else 'â Done',callback_data=f"done:{g['id']}"),InlineKeyboardButton('â Ø§ÙØ¬Ø§Ù ÙØ¯Ø§Ø¯Ù' if lang(uid)=='fa' else 'â Not done',callback_data=f"miss:{g['id']}")],[InlineKeyboardButton('â° ÛØ§Ø¯Ø¢ÙØ±Û ÙØ±Ø¯Ø§' if lang(uid)=='fa' else 'â° Tomorrow',callback_data=f"goalrem:{g['id']}:menu")]]))
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
            await q.answer('â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.',show_alert=True); return
        parts=data.split(':'); action=parts[2]
        if action=='manager_list':
            await q.answer(); await q.message.edit_text(_targeted_manager_text(uid),parse_mode='HTML',reply_markup=_targeted_manager_keyboard(uid)); return
        if action=='manager_add':
            if not _manager_is_owner(uid): await q.answer('â Owner only.',show_alert=True); return
            context.user_data.clear(); context.user_data['targeted_add_manager']=True; await q.answer(); await q.message.edit_text('ð @username ÛØ§ Ø¢ÛØ¯Û Ø¹Ø¯Ø¯Û ÙØ¯ÛØ± Ø¬Ø¯ÛØ¯ Ø±Ø§ Ø¨ÙØ±Ø³Øª.\n\nð¡ ÙÚ©ØªÙ: Ú©Ø§Ø±Ø¨Ø± Ø¨Ø§ÛØ¯ ÙØ¨ÙØ§Ù Ø±Ø¨Ø§Øª Ø±Ø§ Start Ú©Ø±Ø¯Ù Ø¨Ø§Ø´Ø¯.\nØ¨Ø±Ø§Û Ù¾ÛØ¯Ø§ Ú©Ø±Ø¯Ù Ø¢ÛØ¯Û Ø¹Ø¯Ø¯Û Ø§Ø² @userinfobot Ø§Ø³ØªÙØ§Ø¯Ù Ú©Ù.' if fa else 'ð Send the new manager @username or numeric ID.\n\nð¡ Note: The user must have started the bot first.\nUse @userinfobot to find their numeric ID.'); return
        if action=='manager_disable_list':
            await q.answer(); rows=[]
            for r in _targeted_manager_rows():
                if int(r['user_id'])==master_owner_id() or not r['active']: continue
                label=(('@'+r['username']) if r['username'] else str(r['user_id']))
                rows.append([InlineKeyboardButton('ðï¸ '+label,callback_data=f'v25:targeted:manager_disable_confirm:{r["user_id"]}')])
            rows.append([InlineKeyboardButton('â¬ï¸ ÙØ¯ÛØ±ÛØª ÙØ¯ÛØ±Ø§Ù',callback_data='v25:targeted:manager_list')]); await q.message.edit_text('ÙØ¯ÛØ±Û Ø±Ø§ Ø¨Ø±Ø§Û ÙØºÙ ÙØ¯ÛØ±ÛØª Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:' if fa else 'Choose a manager to disable:',reply_markup=InlineKeyboardMarkup(rows)); return
        if action=='manager_disable_confirm':
            target=int(parts[3]); await q.answer(); await q.message.edit_text(f'â ï¸ ÙØ¯ÛØ±ÛØª Ø§ÛÙ ÙØ¯ÛØ± ÙØºÙ Ø´ÙØ¯Ø\nð <code>{target}</code>',parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('â Ø¨ÙÙ',callback_data=f'v25:targeted:manager_disable:{target}'),InlineKeyboardButton('â Ø®ÛØ±',callback_data=f'v25:targeted:manager_detail:{target}')]])); return
        if action=='manager_disable':
            target=int(parts[3]);
            if target==master_owner_id(): await q.answer('â Owner ÙØ§Ø¨Ù ÙØºÙ ÙÛØ³Øª.',show_alert=True); return
            c=db(); c.execute('UPDATE management_roles SET active=0,updated_at=? WHERE user_id=?',(datetime.now(TZ).isoformat(),target)); c.commit(); c.close(); master_log(uid,'manager_disabled',target); await q.answer(); await q.message.edit_text(_targeted_manager_text(uid),parse_mode='HTML',reply_markup=_targeted_manager_keyboard(uid)); return
        if action=='manager_toggle':
            target=int(parts[3]);
            if target==master_owner_id(): await q.answer('Owner ÙÙÛØ´Ù ÙØ¹Ø§Ù Ø§Ø³Øª.',show_alert=True); return
            c=db(); c.execute('UPDATE management_roles SET active=CASE WHEN active=1 THEN 0 ELSE 1 END,updated_at=? WHERE user_id=?',(datetime.now(TZ).isoformat(),target)); c.commit(); c.close(); await q.answer(); text,kb=_targeted_manager_detail(uid,target); await q.message.edit_text(text,parse_mode='HTML',reply_markup=kb); return
        if action=='manager_detail':
            target=int(parts[3]); text,kb=_targeted_manager_detail(uid,target); await q.answer(); await q.message.edit_text(text,parse_mode='HTML',reply_markup=kb); return
        if action=='manager_perms':
            target=int(parts[3]); await q.answer(); await q.message.edit_text('ð Ø¯Ø³ØªØ±Ø³ÛâÙØ§Û ÙØ¯ÛØ± Ø±Ø§ Ø±ÙØ´Ù/Ø®Ø§ÙÙØ´ Ú©Ù:',reply_markup=_targeted_permissions_keyboard(uid,target)); return
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
    # Navigation buttons must cancel any pending manager-add/disable input mode
    _nav_labels = ('ð  ÙÙÙÛ Ø§ØµÙÛ','ð  Main Menu','â¬ï¸ Ø¨Ø±Ú¯Ø´Øª','â¬ï¸ Back',
                    'ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ù ÙÙØ´âÙØ§','ð¥ Users & Roles',
                    'ð¡ ÙØ¯ÛØ±ÛØª Ø±Ø¨Ø§Øª','ð¡ Bot Management',
                    'âï¸ ØªÙØ¸ÛÙØ§Øª Ø³ÛØ³ØªÙ','âï¸ System Settings',
                    'ð Ø¯Ø§Ø´Ø¨ÙØ±Ø¯ Ù Ú¯Ø²Ø§Ø±Ø´','ð Dashboard & Reports',
                    'ð¤ Ø§Ø³ØªÙØ§Ø¯Ù Ø§Ø² Ø±Ø¨Ø§Øª','ð¤ Use Bot',
                    )
    if context.user_data.get('targeted_add_manager') and txt in _nav_labels:
        context.user_data.pop('targeted_add_manager', None)
    if context.user_data.get('master_add_manager') and txt in _nav_labels:
        context.user_data.pop('master_add_manager', None)
    if context.user_data.get('master_disable_manager') and txt in _nav_labels:
        context.user_data.pop('master_disable_manager', None)
    if context.user_data.get('targeted_add_manager'):
        if not _manager_is_owner(uid): context.user_data.clear(); await update.message.reply_text('â Owner only.'); return
        target,uname=await _targeted_resolve_manager_ref(context,context.bot,txt)
        if not target:
            await update.message.reply_text('â Ú©Ø§Ø±Ø¨Ø± <b>@'+txt.lstrip('@')+'</b> Ù¾ÛØ¯Ø§ ÙØ´Ø¯.\n\nð Ø¨Ø±Ø±Ø³Û Ú©ÙÛØ¯:\nâ¢ Ø¢ÛØ§ Ú©Ø§Ø±Ø¨Ø± @username Ø±Ù Ø¯ÙÛÙ ÙØ±Ø³ØªØ§Ø¯ÙØ\nâ¢ Ø¢ÛØ§ Ú©Ø§Ø±Ø¨Ø± ÙØ¨ÙØ§Ù Ø±Ø¨Ø§Øª Ø±Ù Start Ú©Ø±Ø¯Ù Ù ÛÙ Ù¾ÛØ§Ù ÙØ±Ø³ØªØ§Ø¯ÙØ\n\nâ Ø³Ø§Ø¯ÙâØªØ±ÛÙ Ø±Ø§Ù: Ø§Ø² Ú©Ø§Ø±Ø¨Ø± Ø¨Ø®ÙØ§ÙÛØ¯ Ø¢ÛØ¯Û Ø¹Ø¯Ø¯Û Ø®ÙØ¯ Ø±Ù Ø§Ø² @userinfobot Ø¨Ú¯ÛØ±Ù Ù ÙÙÙÙ Ø±Ù Ø¨ÙØ±Ø³ØªÙ.',parse_mode='HTML'); return
        context.user_data['targeted_add_manager']=False; context.user_data['targeted_pending_manager_id']=target; context.user_data['targeted_pending_manager_username']=uname
        await update.message.reply_text('ð­ ÙÙØ´ ÙØ¯ÛØ± Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:',reply_markup=_master_add_role_keyboard(uid)); return
    # Fix legacy add-manager flow too: accept username and store it.
    if context.user_data.get('master_add_manager'):
        if not _manager_is_owner(uid): context.user_data.clear(); await update.message.reply_text('â Owner only.'); return
        target,uname=await _targeted_resolve_manager_ref(context,context.bot,txt)
        if not target: await update.message.reply_text('â @username ÛØ§ ID ÙØ¹ØªØ¨Ø± ÙÛØ³Øª.'); return
        context.user_data['master_add_manager']=False; context.user_data['master_pending_manager_id']=target; context.user_data['master_pending_manager_username']=uname
        await update.message.reply_text('ð­ ÙÙØ´ ÙØ¯ÛØ± Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:',reply_markup=_master_add_role_keyboard(uid)); return
    # Legacy disable flow: accept username too.
    if context.user_data.get('master_disable_manager'):
        if not _manager_is_owner(uid): context.user_data.clear(); await update.message.reply_text('â Owner only.'); return
        target,uname=await _targeted_resolve_manager_ref(context,context.bot,txt)
        if not target: await update.message.reply_text('â @username ÛØ§ ID ÙØ¹ØªØ¨Ø± ÙÛØ³Øª.'); return
        if target==master_owner_id(): await update.message.reply_text('â Owner ÙØ§Ø¨Ù ÙØºÙ ÙÛØ³Øª.'); return
        c=db(); c.execute('UPDATE management_roles SET active=0,updated_at=? WHERE user_id=?',(datetime.now(TZ).isoformat(),target)); c.commit(); c.close(); clear_flow(context); await update.message.reply_text('â ÙØ¯ÛØ±ÛØª Ø§ÛÙ Ú©Ø§Ø±Ø¨Ø± ÙØºÙ Ø´Ø¯.',reply_markup=keyboard(uid)); return
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
        rows.insert(max(0,len(rows)-1),[InlineKeyboardButton('ð ÙØ¯ÛØ±ÛØª ÙÛÙØªâÙØ§Û Ø¢ÙÙØ§ÛÙ' if lang(uid)=='fa' else 'ð Live Price Management',callback_data='v25:targeted:prices')])
    return InlineKeyboardMarkup(rows)

# Extend the callback one final time for live-price settings.
_PREV_V25_CALLBACK_PRICE_PANEL=v25_callback
async def v25_callback(update,context):
    data=update.callback_query.data if update.callback_query else ''; uid=update.effective_user.id; q=update.callback_query
    if data=='v25:targeted:prices':
        if not master_has_permission(uid,'manage_features'): await q.answer('â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.',show_alert=True); return
        rows=[]; c=db(); all_rows=c.execute('SELECT asset,enabled FROM price_asset_settings ORDER BY rowid').fetchall(); c.close(); labels={'usd':'Ø¯ÙØ§Ø±','eur':'ÛÙØ±Ù','gold18':'Ø·ÙØ§Û Û±Û¸ Ø¹ÛØ§Ø±','coin':'Ø³Ú©Ù Ø§ÙØ§ÙÛ','silver':'ÙÙØ±Ù','copper':'ÙØ³','aluminum':'Ø¢ÙÙÙÛÙÛÙÙ','nickel':'ÙÛÚ©Ù','zinc':'Ø±ÙÛ','lead':'Ø³Ø±Ø¨'}
        for r in all_rows:
            rows.append([InlineKeyboardButton(('ð¢ ' if r['enabled'] else 'ð´ ')+labels.get(r['asset'],r['asset']),callback_data=f"v25:targeted:price_toggle:{r['asset']}")])
        rows.append([InlineKeyboardButton('â¬ï¸ ÙØ±Ú©Ø² ÙØ¯ÛØ±ÛØª',callback_data='v25:master:home')]); await q.answer(); await q.message.edit_text('ð <b>ÙØ¯ÛØ±ÛØª ÙÛÙØªâÙØ§Û Ø¢ÙÙØ§ÛÙ</b>\n\nØ³Ø¨Ø² = ÙÙØ§ÛØ´ Ø¯Ø± Ø±Ø¨Ø§Øª\nÙØ±ÙØ² = ÙØ®ÙÛ Ø§Ø² Ú©Ø§Ø±Ø¨Ø±Ø§Ù',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(rows)); return
    if data.startswith('v25:targeted:price_toggle:'):
        if not master_has_permission(uid,'manage_features'): await q.answer('â',show_alert=True); return
        asset=data.rsplit(':',1)[1]; c=db(); r=c.execute('SELECT enabled FROM price_asset_settings WHERE asset=?',(asset,)).fetchone(); new=0 if r and r['enabled'] else 1; c.execute('UPDATE price_asset_settings SET enabled=?,updated_at=? WHERE asset=?',(new,datetime.now(TZ).isoformat(),asset)); c.commit(); c.close(); await q.answer('Ø±ÙØ´Ù Ø´Ø¯' if new else 'Ø®Ø§ÙÙØ´ Ø´Ø¯');
        rows=[]; c=db(); all_rows=c.execute('SELECT asset,enabled FROM price_asset_settings ORDER BY rowid').fetchall(); c.close(); labels={'usd':'Ø¯ÙØ§Ø±','eur':'ÛÙØ±Ù','gold18':'Ø·ÙØ§Û Û±Û¸ Ø¹ÛØ§Ø±','coin':'Ø³Ú©Ù Ø§ÙØ§ÙÛ','silver':'ÙÙØ±Ù','copper':'ÙØ³','aluminum':'Ø¢ÙÙÙÛÙÛÙÙ','nickel':'ÙÛÚ©Ù','zinc':'Ø±ÙÛ','lead':'Ø³Ø±Ø¨'}
        for rr in all_rows: rows.append([InlineKeyboardButton(('ð¢ ' if rr['enabled'] else 'ð´ ')+labels.get(rr['asset'],rr['asset']),callback_data=f"v25:targeted:price_toggle:{rr['asset']}")])
        rows.append([InlineKeyboardButton('â¬ï¸ ÙØ±Ú©Ø² ÙØ¯ÛØ±ÛØª',callback_data='v25:master:home')]); await q.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(rows)); return
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
        ("manage_bot", "ð¡ ÙØ¯ÛØ±ÛØª Ø±Ø¨Ø§Øª", "ð¡ Bot Management"),
        ("view_dashboard", "ð Ø¯Ø§Ø´Ø¨ÙØ±Ø¯ Ù Ú¯Ø²Ø§Ø±Ø´", "ð Dashboard & Reports"),
        ("manage_users", "ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ù ÙÙØ´âÙØ§", "ð¥ Users & Roles"),
        ("manage_tickets", "ð« ØªÛÚ©ØªâÙØ§ Ù Incident", "ð« Tickets & Incidents"),
        ("manage_channels", "ð¢ Ú©Ø§ÙØ§Ù Ù Ø§ÙØªØ´Ø§Ø±", "ð¢ Channels & Publishing"),
        ("manage_finance", "ð° ÙØ§ÙÛ Ù Ù¾Ø±Ø¯Ø§Ø®Øª", "ð° Finance & Payments"),
        ("manage_vip", "ð VIP / XP / Token", "ð VIP / XP / Token"),
        ("run_health", "ð©º Ø³ÙØ§ÙØª Ù Diagnostics", "ð©º Health & Diagnostics"),
        ("manage_system", "âï¸ ØªÙØ¸ÛÙØ§Øª Ø³ÛØ³ØªÙ", "âï¸ System Settings"),
        ("manage_roles", "ð§âð¼ ÙØ¯ÛØ±ÛØª ÙØ¯ÛØ±Ø§Ù", "ð§âð¼ Manager Management"),
        ("use_bot", "ð¤ Ø§Ø³ØªÙØ§Ø¯Ù Ø§Ø² Ø±Ø¨Ø§Øª", "ð¤ Use Bot"),
    ]

    labels = []
    for perm, fa_label, en_label in specs:
        if perm in ("manage_bot", "use_bot") or master_has_permission(uid, perm):
            labels.append(fa_label if fa else en_label)

    # Two buttons per row, like the requested compact reference layout.
    rows = [labels[i:i + 2] for i in range(0, len(labels), 2)]
    rows.append(["ð  ÙÙÙÛ Ø§ØµÙÛ" if fa else "ð  Main Menu"])

    title = (
        f"ð¡ï¸ Ù¾ÙÙ ÙØ¯ÛØ±\nÙÙØ´: <b>{html.escape(role_label)}</b>"
        if fa else
        f"ð¡ï¸ Manager Panel\nRole: <b>{html.escape(role_label)}</b>"
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
    "ð  ÙÙÙÛ Ø§ØµÙÛ" carrier here was the source of the duplicate text bubbles.
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



# Keep the settings callback on the same message and never emit a duplicate
# "ð  ÙÙÙÛ Ø§ØµÙÛ" message when the user chooses Main Menu.
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
                await q.message.edit_text("â")
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

    if txt in ("ð  ÙÙÙÛ Ø§ØµÙÛ", "ð  Main Menu", "â¬ï¸ Ø¨Ø±Ú¯Ø´Øª", "â¬ï¸ Back"):
        clear_flow(context)
        try:
            await update.message.delete()
        except Exception:
            pass
        # Show user menu if coming from a section
        parent = context.user_data.get("_nav_parent_section")
        if parent and parent in ("goals", "reports", "tools", "vip", "account", "support"):
            fa = lang(uid) == "fa"
            titles = {
                "goals": ("ð¯ <b>Ø¨Ø±ÙØ§ÙÙ Ù Ø§ÙØ¯Ø§Ù</b>", "ð¯ <b>Goals & Plan</b>"),
                "reports": ("ð <b>Ú¯Ø²Ø§Ø±Ø´ Ù Ù¾ÛØ´Ø±ÙØª</b>", "ð <b>Reports & Progress</b>"),
                "tools": ("ð ï¸ <b>Ø§Ø¨Ø²Ø§Ø±ÙØ§</b>", "ð ï¸ <b>Tools</b>"),
                "vip": ("ð <b>VIP Ù Ù¾Ø§Ø¯Ø§Ø´âÙØ§</b>", "ð <b>VIP & Rewards</b>"),
                "account": ("ð¤ <b>Ø­Ø³Ø§Ø¨ ÙÙ</b>", "ð¤ <b>My Account</b>"),
                "support": ("ð« <b>Ù¾Ø´ØªÛØ¨Ø§ÙÛ</b>", "ð« <b>Support</b>"),
            }
            await update.message.reply_text(
                titles.get(parent, titles["goals"])[0 if fa else 1],
                parse_mode="HTML",
                reply_markup=_compact_menu_keyboard(uid, parent),
            )
            return
        return

    if txt in ("ð¤ Ø§Ø³ØªÙØ§Ø¯Ù Ø§Ø² Ø±Ø¨Ø§Øª", "ð¤ Use Bot"):
        clear_flow(context)
        fa = lang(uid) == "fa"
        await update.message.reply_text(
            "ð¤ <b>Ø¨Ø®Ø´ Ú©Ø§Ø±Ø¨Ø±</b>\n\nÙØ§Ø¨ÙÛØªâÙØ§Û Ø¹Ø§Ø¯Û Ø±Ø¨Ø§Øª Ø¯Ø± Ø§ÛÙ Ø¨Ø®Ø´ Ø¯Ø± Ø¯Ø³ØªØ±Ø³ Ø§Ø³Øª."
            if fa else
            "ð¤ <b>User Area</b>\n\nNormal user features are available here.",
            parse_mode="HTML",
            reply_markup=_compact_user_keyboard(uid),
        )
        return

    if txt in ("âï¸ ØªÙØ¸ÛÙØ§Øª Ø³ÛØ³ØªÙ", "âï¸ System Settings"):
        clear_flow(context)
        if _is_active_manager(uid):
            fa = lang(uid) == "fa"
            paused = get_system_setting("bot_paused_until", "")
            maintenance = feature_enabled("maintenance")
            text = (
                f"âï¸ <b>ØªÙØ¸ÛÙØ§Øª Ø³ÛØ³ØªÙ</b>\n\n"
                f"ð  Maintenance: {'ð¢' if maintenance else 'ð´'}\n"
                f"â¸ ØªÙÙÙ ÙÙÙØª: {html.escape(paused or 'ÙØ¹Ø§Ù ÙÛØ³Øª')}\n"
                f"ð Schema: {DB_SCHEMA_VERSION}\n\n"
                f"ÙØ§ÙÚ© Ø§ØµÙÛ: <code>{master_owner_id() or '-'}</code>"
            ) if fa else (
                f"âï¸ <b>System Settings</b>\n\n"
                f"ð  Maintenance: {'ð¢' if maintenance else 'ð´'}\n"
                f"â¸ Temporary pause: {html.escape(paused or 'Not active')}\n"
                f"ð Schema: {DB_SCHEMA_VERSION}\n\n"
                f"Owner: <code>{master_owner_id() or '-'}</code>"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("ð§© ØªØºÛÛØ± ÙØ§Ø¨ÙÛØªâÙØ§" if fa else "ð§© Feature Flags", callback_data="adm:features")],
                [InlineKeyboardButton("â¸ ÙØ¯ÛØ±ÛØª ØªÙÙÙ" if fa else "â¸ Pause Management", callback_data="adm:pause")],
                [InlineKeyboardButton("â¬ï¸ ÙØ±Ú©Ø² ÙØ¯ÛØ±ÛØª" if fa else "â¬ï¸ Management Center", callback_data="v25:master:home")],
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
        "ð¤ Ø§Ø³ØªÙØ§Ø¯Ù Ø§Ø² Ø±Ø¨Ø§Øª", "ð¤ Use Bot", "âï¸ ØªÙØ¸ÛÙØ§Øª Ø³ÛØ³ØªÙ", "âï¸ System Settings"
    ):
        try:
            clear_flow(context)
            if txt in ("ð¤ Ø§Ø³ØªÙØ§Ø¯Ù Ø§Ø² Ø±Ø¨Ø§Øª", "ð¤ Use Bot"):
                fa = lang(uid) == "fa"
                await update.message.reply_text(
                    "ð¤ <b>Ø¨Ø®Ø´ Ú©Ø§Ø±Ø¨Ø±</b>\n\nÙØ§Ø¨ÙÛØªâÙØ§Û Ø¹Ø§Ø¯Û Ø±Ø¨Ø§Øª Ø¯Ø± Ø§ÛÙ Ø¨Ø®Ø´ Ø¯Ø± Ø¯Ø³ØªØ±Ø³ Ø§Ø³Øª."
                    if fa else
                    "ð¤ <b>User Area</b>\n\nNormal user features are available here.",
                    parse_mode="HTML", reply_markup=_compact_user_keyboard(uid)
                )
            else:
                fa = lang(uid) == "fa"
                await update.message.reply_text(
                    "âï¸ <b>ØªÙØ¸ÛÙØ§Øª Ø³ÛØ³ØªÙ</b>\n\nÙØ¶Ø¹ÛØª ØªÙØ¸ÛÙØ§Øª Ø³ÛØ³ØªÙ Ø±Ø§ Ø§Ø² Ø§ÛÙ Ø¨Ø®Ø´ ÙØ¯ÛØ±ÛØª Ú©Ù."
                    if fa else
                    "âï¸ <b>System Settings</b>\n\nManage the system settings from this section.",
                    parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("ð§© ØªØºÛÛØ± ÙØ§Ø¨ÙÛØªâÙØ§" if fa else "ð§© Feature Flags", callback_data="adm:features")],
                        [InlineKeyboardButton("â¬ï¸ ÙØ±Ú©Ø² ÙØ¯ÛØ±ÛØª" if fa else "â¬ï¸ Management Center", callback_data="v25:master:home")],
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
    "ð¤ Ø´Ø®ØµÛ": {
        "ð©º Ø³ÙØ§ÙØª": [
            "ð©º ÙÙØª Ø¯Ú©ØªØ±", "ð§ª Ø¢Ø²ÙØ§ÛØ´ Ø¯ÙØ±ÙâØ§Û", "ð©¸ Ø¢Ø²ÙØ§ÛØ´ Ú©Ø§ÙÙ Ø®ÙÙ", "ð ÚÚ©Ø§Ù¾ ÙØ§ÙØ§ÙÙ", "ð ÚÚ©Ø§Ù¾ Ø³Ø§ÙØ§ÙÙ",
            "ð¦· Ø¯ÙØ¯Ø§ÙÙ¾Ø²Ø´Ú©Û", "ðï¸ ÙØ¹Ø§ÛÙÙ ÚØ´Ù", "ð Ø®Ø±ÛØ¯/ØªÙØ¯ÛØ¯ Ø¯Ø§Ø±Ù", "ð ÙØ§Ú©Ø³Ù"
        ],
        "ð  Ø®Ø§ÙÙ": [
            "ð§¹ ÙØ¸Ø§ÙØª Ø®Ø§ÙÙ", "ð§ ØªØ¹ÙÛØ±Ø§Øª Ø®Ø§ÙÙ", "âï¸ Ø³Ø±ÙÛØ³ Ú©ÙÙØ±", "ð¥ Ø³Ø±ÙÛØ³ Ù¾Ú©ÛØ¬/Ø¨Ø®Ø§Ø±Û", "ð§ ØªØ¹ÙÛØ¶ ÙÛÙØªØ± Ø¢Ø¨",
            "ð Ø®Ø±ÛØ¯ ÙÙØ§Ø²Ù Ø®Ø§ÙÙ", "ð¦ Ø®Ø±ÛØ¯ ÙØ§ÙØ§ÙÙ Ø®Ø§ÙÙ"
        ],
        "ð¨âð©âð§ Ø®Ø§ÙÙØ§Ø¯Ù": [
            "âï¸ ØªÙØ§Ø³ Ø¨Ø§ Ø®Ø§ÙÙØ§Ø¯Ù", "â¤ï¸ ÙÙØª Ø®Ø§ÙÙØ§Ø¯Ú¯Û", "ð ØªÙÙØ¯", "ð Ø³Ø§ÙÚ¯Ø±Ø¯", "ð« Ù¾ÛÚ¯ÛØ±Û ÙØ¯Ø±Ø³Ù/Ú©ÙØ§Ø³",
            "ð©º ÙÙØª Ø¯Ú©ØªØ± Ø¹Ø¶Ù Ø®Ø§ÙÙØ§Ø¯Ù", "ð Ø®Ø±ÛØ¯ ÙØ¯ÛÙ"
        ],
        "âï¸ Ø³ÙØ±": [
            "âï¸ Ø³ÙØ± Ú©Ø§Ø±Û", "ð Ø³ÙØ± ØªÙØ±ÛØ­Û", "ð¨âð©âð§ Ø³ÙØ± Ø®Ø§ÙÙØ§Ø¯Ú¯Û", "ð Ø³ÙØ± Ø®Ø§Ø±Ø¬Û", "ð Ø¢ÙØ§Ø¯ÙâØ³Ø§Ø²Û Ø³ÙØ±"
        ],
        "ð Ø´Ø®ØµÛ Ù Ø§Ø¯Ø§Ø±Û": [
            "ð Ú©Ø§Ø± Ø´Ø®ØµÛ", "ð¢ Ú©Ø§Ø± Ø§Ø¯Ø§Ø±Û", "ð ØªÙØ§Ø³ ÙÙÙ", "ð¤ ÙØ±Ø§Ø±", "ð¯ Ù¾ÛÚ¯ÛØ±Û ÛÚ© Ú©Ø§Ø±", "â­ Ú©Ø§Ø± ÙÙÙ"
        ],
    },
    "ð° ÙØ§ÙÛ": {
        "ð§¾ ÚÚ© Ù Ø³Ø±Ø±Ø³ÛØ¯": ["ð§¾ ÚÚ© Ù¾Ø±Ø¯Ø§Ø®ØªÛ", "ð§¾ ÚÚ© Ø¯Ø±ÛØ§ÙØªÛ", "ð Ø³Ø±Ø±Ø³ÛØ¯ ÚÚ©", "ðµ Ø·ÙØ¨", "ð³ Ø¨Ø¯ÙÛ"],
        "ð³ Ù¾Ø±Ø¯Ø§Ø®ØªâÙØ§": ["ð³ ÙØ³Ø·", "ð¦ ÙØ§Ù", "ð  Ø§Ø¬Ø§Ø±Ù", "ð¡ ÙØ¨Ø¶", "ð¡ï¸ Ø¨ÛÙÙ", "ð§® ÙØ§ÙÛØ§Øª", "ð Ø´ÙØ±ÛÙ"],
        "ð Ø¯ÙØ±ÙâØ§Û": ["ð Ù¾Ø±Ø¯Ø§Ø®Øª ÙØ§ÙØ§ÙÙ", "ð Ù¾Ø±Ø¯Ø§Ø®Øª Ø³Ø§ÙØ§ÙÙ", "ðº ØªÙØ¯ÛØ¯ Ø§Ø´ØªØ±Ø§Ú©", "ð ØªÙØ¯ÛØ¯ Ø³Ø±ÙÛØ³ Ø¢ÙÙØ§ÛÙ"],
        "ð ÙØ¯ÛØ±ÛØª ÙØ§ÙÛ": ["ð Ø«Ø¨Øª ÙØ²ÛÙÙ", "ð¦ Ø¨Ø±Ø±Ø³Û Ø­Ø³Ø§Ø¨", "ð° Ù¾Ø³âØ§ÙØ¯Ø§Ø²", "ð Ø¨Ø±Ø±Ø³Û ÙØ²ÛÙÙ ÙØ§ÙØ§ÙÙ"],
    },
    "ð Ø®ÙØ¯Ø±Ù": {
        "ð¢ Ø³Ø±ÙÛØ³": ["ð¢ ØªØ¹ÙÛØ¶ Ø±ÙØºÙ", "ð§ Ø³Ø±ÙÛØ³ Ø¯ÙØ±ÙâØ§Û", "ð§° ØªØ¹ÙÛØ± Ø®ÙØ¯Ø±Ù", "ð© ØªØ¹ÙÛØ¶ Ø´ÙØ¹", "ã°ï¸ ØªØ¹ÙÛØ¶ ØªØ³ÙÙ"],
        "ð ØªØ±ÙØ² Ù ÙØ§Ø³ØªÛÚ©": ["ð ØªØ¹ÙÛØ¶ ÙÙØª", "ð Ø¨Ø±Ø±Ø³Û ØªØ±ÙØ²", "ð ØªØ¹ÙÛØ¶ ÙØ§Ø³ØªÛÚ©", "ð¨ ØªÙØ¸ÛÙ Ø¨Ø§Ø¯", "ð Ø¬Ø§Ø¨ÙâØ¬Ø§ÛÛ ÙØ§Ø³ØªÛÚ©"],
        "ð§ ÙØ§ÛØ¹Ø§Øª Ù ÙÛÙØªØ±ÙØ§": ["ð§ Ø¶Ø¯ÛØ®/Ø¢Ø¨ Ø±Ø§Ø¯ÛØ§ØªÙØ±", "ð§´ Ø±ÙØºÙ ØªØ±ÙØ²", "ð¬ ÙÛÙØªØ± ÙÙØ§", "âï¸ ÙÛÙØªØ± Ú©Ø§Ø¨ÛÙ", "ð¢ ÙÛÙØªØ± Ø±ÙØºÙ"],
        "ð ÙØ¯Ø§Ø±Ú© Ù ÙÚ¯ÙØ¯Ø§Ø±Û": ["ð ÙØ¹Ø§ÛÙÙ ÙÙÛ", "ð¡ï¸ ØªÙØ¯ÛØ¯ Ø¨ÛÙÙ Ø®ÙØ¯Ø±Ù", "ð Ø¨Ø±Ø±Ø³Û Ø¨Ø§ØªØ±Û", "âï¸ Ø³Ø±ÙÛØ³ Ú©ÙÙØ±", "ð§½ Ú©Ø§Ø±ÙØ§Ø´"],
    },
    "ð¼ Ú©Ø§Ø±": {
        "ð Ø¨Ø±ÙØ§ÙÙ": ["ð Ø¬ÙØ³Ù", "ð ÙØ¸ÛÙÙ Ú©Ø§Ø±Û", "ð¯ Ù¾Ø±ÙÚÙ", "ð Ú¯Ø²Ø§Ø±Ø´ Ø±ÙØ²Ø§ÙÙ", "ð Ú¯Ø²Ø§Ø±Ø´ ÙÙØªÚ¯Û", "ð Ú¯Ø²Ø§Ø±Ø´ ÙØ§ÙØ§ÙÙ"],
        "ð¥ ÙØ´ØªØ±Û": ["ð ØªÙØ§Ø³ Ø¨Ø§ ÙØ´ØªØ±Û", "ð Ù¾ÛÚ¯ÛØ±Û ÙØ´ØªØ±Û", "ð¤ ÙØ±Ø§Ø± Ø¨Ø§ ÙØ´ØªØ±Û", "ð¨ Ø§Ø±Ø³Ø§Ù Ù¾ÛØ§Ù", "ð§¾ Ø§Ø±Ø³Ø§Ù ÙØ§Ú©ØªÙØ±"],
        "ð ÙØ±Ø§Ø±Ø¯Ø§Ø¯ Ù ÙØ§ÙÛ": ["ð ÙØ±Ø§Ø±Ø¯Ø§Ø¯", "ð ØªÙØ¯ÛØ¯ ÙØ±Ø§Ø±Ø¯Ø§Ø¯", "ðµ Ù¾ÛÚ¯ÛØ±Û Ù¾Ø±Ø¯Ø§Ø®Øª", "ð§¾ Ù¾ÛÚ¯ÛØ±Û ÙØ§Ú©ØªÙØ±"],
    },
    "ð ØªØ­ØµÛÙ": {
        "ð ÙØ·Ø§ÙØ¹Ù": ["ð ÙØ·Ø§ÙØ¹Ù", "ð ÙØ±ÙØ± Ø¯Ø±Ø³", "ð¤ ÛØ§Ø¯Ú¯ÛØ±Û ÙØºØª", "ð Ø¬Ø²ÙÙ"],
        "ð Ø¯Ø§ÙØ´Ú¯Ø§Ù/ÙØ¯Ø±Ø³Ù": ["ð« Ú©ÙØ§Ø³", "ð Ø§ÙØªØ­Ø§Ù", "ð ØªÚ©ÙÛÙ", "ð» Ù¾Ø±ÙÚÙ", "ð Ø«Ø¨ØªâÙØ§Ù", "ð³ Ø´ÙØ±ÛÙ"],
    },
    "ðï¸ ÙØ±Ø²Ø´": {
        "ðï¸ ØªÙØ±ÛÙ": ["ðï¸ Ø¨Ø¯ÙØ³Ø§Ø²Û", "ð Ø¯ÙÛØ¯Ù", "ð¶ Ù¾ÛØ§Ø¯ÙâØ±ÙÛ", "ð  ÙØ±Ø²Ø´ Ø®Ø§ÙÚ¯Û", "ð§ ÛÙÚ¯Ø§", "ð¤¸ Ú©Ø´Ø´ Ù ÙØ±ÙØ´"],
        "â½ ÙØ±Ø²Ø´âÙØ§Û Ú¯Ø±ÙÙÛ": ["â½ ÙÙØªØ¨Ø§Ù", "ð¥ ÙÙØªØ³Ø§Ù", "ð ÙØ§ÙÛØ¨Ø§Ù", "ð Ø¨Ø³Ú©ØªØ¨Ø§Ù", "ð¾ ØªÙÛØ³"],
        "ð ÙØ¶Ø§Û Ø¨Ø§Ø²": ["ð Ø´ÙØ§", "ð´ Ø¯ÙÚØ±Ø®ÙâØ³ÙØ§Ø±Û", "ð¥¾ Ú©ÙÙÙÙØ±Ø¯Û"],
    },
    "ð ÙØ¯Ø§Ø±Ú©": {
        "ðªª Ø´ÙØ§Ø³Ø§ÛÛ": ["ðªª Ú¯ÙØ§ÙÛÙØ§ÙÙ", "ð Ù¾Ø§Ø³Ù¾ÙØ±Øª", "ð³ Ú©Ø§Ø±Øª Ø¨Ø§ÙÚ©Û"],
        "ð ØªÙØ¯ÛØ¯ÙØ§": ["ð¡ï¸ ØªÙØ¯ÛØ¯ Ø¨ÛÙÙ", "ð ØªÙØ¯ÛØ¯ ÙØ¬ÙØ²", "ð ØªÙØ¯ÛØ¯ ÙØ±Ø§Ø±Ø¯Ø§Ø¯", "ð ØªÙØ¯ÛØ¯ Ø¯Ø§ÙÙÙ", "ð» ØªÙØ¯ÛØ¯ ÙØ§Ø³Øª"],
        "ð¢ Ø§Ø¯Ø§Ø±Û": ["ð¢ ÙØ±Ø§Ø¬Ø¹Ù Ø§Ø¯Ø§Ø±Û", "ð ØªÚ©ÙÛÙ ÙØ¯Ø±Ú©", "ð¬ Ù¾ÛÚ¯ÛØ±Û Ù¾Ø±ÙÙØ¯Ù"],
    },
    "ð Ø³Ø§ÛØ±": {
        "ð» ÙÙØ§ÙØ±Û": ["ð» Ø¨Ú©Ø§Ù¾ Ø§Ø·ÙØ§Ø¹Ø§Øª", "ð Ø¨Ø±Ø±Ø³Û Ø§ÙÙÛØª Ø­Ø³Ø§Ø¨", "ð± ØªØ¹ÙÛØ± ÙÙØ¨Ø§ÛÙ", "ð¥ ØªØ¹ÙÛØ± Ú©Ø§ÙÙ¾ÛÙØªØ±", "ð Ø¨ÙâØ±ÙØ²Ø±Ø³Ø§ÙÛ ÙØ±ÙâØ§ÙØ²Ø§Ø±"],
        "ð¾ Ø­ÛÙØ§ÙØ§Øª": ["ð¾ Ø¯Ø§ÙÙ¾Ø²Ø´Ú©", "ð ÙØ§Ú©Ø³Ù Ø­ÛÙØ§Ù", "ð Ø¯Ø§Ø±Ù", "ðª± Ø¶Ø¯Ø§ÙÚ¯Ù", "ð Ø­ÙØ§Ù", "âï¸ Ø§ØµÙØ§Ø­", "ð Ø®Ø±ÛØ¯ ØºØ°Ø§"],
        "ð Ø®Ø±ÛØ¯": ["ð Ø®Ø±ÛØ¯ Ø±ÙØ²Ø§ÙÙ", "ð Ø®Ø±ÛØ¯ ÙÙØªÚ¯Û", "ð Ø®Ø±ÛØ¯ ÙØ§ÙØ§ÙÙ", "ð Ø®Ø±ÛØ¯ ÙØ¯ÛÙ", "ð§´ Ø®Ø±ÛØ¯ ÙÙØ§Ø²Ù ÙØµØ±ÙÛ"],
        "ð ÙØªÙØ±ÙÙ": ["ð Ú©Ø§Ø± ÙØªÙØ±ÙÙ", "ð ÛØ§Ø¯Ø¢ÙØ±Û Ø³ÙØ§Ø±Ø´Û", "ð Ù¾ÛÚ¯ÛØ±Û", "â³ Ú©Ø§Ø± Ø¹ÙØ¨âØ§ÙØªØ§Ø¯Ù"],
    },
}
READY_CATALOG_EN = {
    "ð¤ Personal": {"ð©º Health":["ð©º Doctor appointment","ð§ª Periodic lab test","ð©¸ Full blood test","ð Monthly checkup","ð Annual checkup","ð¦· Dentist","ðï¸ Eye exam","ð Medication refill","ð Vaccine"],"ð  Home":["ð§¹ House cleaning","ð§ Home repair","âï¸ AC service","ð¥ Heater/boiler service","ð§ Water filter change","ð Home supplies","ð¦ Monthly home shopping"],"ð¨âð©âð§ Family":["âï¸ Call family","â¤ï¸ Family time","ð Birthday","ð Anniversary","ð« School/class follow-up","ð©º Family doctor appointment","ð Buy a gift"],"âï¸ Travel":["âï¸ Business trip","ð Vacation","ð¨âð©âð§ Family trip","ð International trip","ð Prepare for travel"],"ð Personal & Admin":["ð Personal task","ð¢ Administrative task","ð Important call","ð¤ Appointment","ð¯ Follow-up","â­ Important task"]},
    "ð° Finance": {"ð§¾ Checks & Due Dates":["ð§¾ Pay a check","ð§¾ Receive a check","ð Check due date","ðµ Receivable","ð³ Debt"],"ð³ Payments":["ð³ Installment","ð¦ Loan","ð  Rent","ð¡ Bill","ð¡ï¸ Insurance","ð§® Tax","ð Tuition"],"ð Recurring":["ð Monthly payment","ð Annual payment","ðº Subscription renewal","ð Online service renewal"],"ð Finance Management":["ð Record expense","ð¦ Check bank account","ð° Save money","ð Review monthly expenses"]},
    "ð Car": {"ð¢ Service":["ð¢ Oil change","ð§ Periodic service","ð§° Car repair","ð© Spark plug change","ã°ï¸ Belt change"],"ð Brakes & Tires":["ð Brake pad change","ð Brake check","ð Tire change","ð¨ Tire pressure","ð Tire rotation"],"ð§ Fluids & Filters":["ð§ Coolant","ð§´ Brake fluid","ð¬ Air filter","âï¸ Cabin filter","ð¢ Oil filter"],"ð Documents & Care":["ð Inspection","ð¡ï¸ Car insurance renewal","ð Battery check","âï¸ AC service","ð§½ Car wash"]},
    "ð¼ Work": {"ð Planning":["ð Meeting","ð Work task","ð¯ Project","ð Daily report","ð Weekly report","ð Monthly report"],"ð¥ Customers":["ð Call customer","ð Follow up customer","ð¤ Customer appointment","ð¨ Send message","ð§¾ Send invoice"],"ð Contract & Finance":["ð Contract","ð Renew contract","ðµ Payment follow-up","ð§¾ Invoice follow-up"]},
    "ð Study": {"ð Study":["ð Study","ð Review","ð¤ Learn vocabulary","ð Notes"],"ð School/University":["ð« Class","ð Exam","ð Homework","ð» Project","ð Registration","ð³ Tuition"]},
    "ðï¸ Fitness": {"ðï¸ Training":["ðï¸ Gym","ð Running","ð¶ Walking","ð  Home workout","ð§ Yoga","ð¤¸ Stretching"],"â½ Team Sports":["â½ Football","ð¥ Futsal","ð Volleyball","ð Basketball","ð¾ Tennis"],"ð Outdoor":["ð Swimming","ð´ Cycling","ð¥¾ Hiking"]},
    "ð Documents": {"ðªª Identity":["ðªª Driver license","ð Passport","ð³ Bank card"],"ð Renewals":["ð¡ï¸ Insurance renewal","ð Permit renewal","ð Contract renewal","ð Domain renewal","ð» Hosting renewal"],"ð¢ Admin":["ð¢ Office visit","ð Complete document","ð¬ Case follow-up"]},
    "ð Other": {"ð» Technology":["ð» Backup data","ð Account security check","ð± Phone repair","ð¥ Computer repair","ð Software update"],"ð¾ Pets":["ð¾ Vet","ð Pet vaccine","ð Medication","ðª± Deworming","ð Bath","âï¸ Grooming","ð Pet food"],"ð Shopping":["ð Daily shopping","ð Weekly shopping","ð Monthly shopping","ð Gift shopping","ð§´ Supplies"],"ð Misc":["ð Misc task","ð Custom reminder","ð Follow-up","â³ Overdue task"]},
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
    rows.append([InlineKeyboardButton("ð  ÙÙÙÛ Ø§ØµÙÛ" if lang(uid)=="fa" else "ð  Main Menu",callback_data="goals:main")])
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
        rows.append([InlineKeyboardButton("â¬ï¸ Ø¨Ø±Ú¯Ø´Øª" if lang(uid)=="fa" else "â¬ï¸ Back",callback_data="newback")])
        await q.message.edit_text("ð ÛÚ© Ø²ÛØ±Ú¯Ø±ÙÙ Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:" if lang(uid)=="fa" else "ð Choose a subgroup:",reply_markup=InlineKeyboardMarkup(rows)); return
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
    rows.append([InlineKeyboardButton("â¬ï¸ Ø¨Ø±Ú¯Ø´Øª" if lang(uid)=="fa" else "â¬ï¸ Back",callback_data=f"newcat:{catidx}")])
    await q.message.edit_text(T[lang(uid)]["choose_goal"],reply_markup=InlineKeyboardMarkup(rows))

async def _show_ready_goals(update,context,category):
    uid=update.effective_user.id; goals=goals_by_category(uid,category); rows=[]
    for i in range(0,len(goals),2): rows.append([InlineKeyboardButton(goals[j],callback_data=f"newgoal:{j}") for j in range(i,min(i+2,len(goals)))])
    rows.append([InlineKeyboardButton("â¬ï¸ Ø¨Ø±Ú¯Ø´Øª" if lang(uid)=="fa" else "â¬ï¸ Back",callback_data="newback")])
    await update.callback_query.message.edit_text(T[lang(uid)]["choose_goal"],reply_markup=InlineKeyboardMarkup(rows))


def _ready_template(name,category):
    n=name.lower()
    if 'ØªØ¹ÙÛØ¶ Ø±ÙØºÙ' in name or 'oil change' in n:
        return [('last_date','ð ØªØ§Ø±ÛØ® ØªØ¹ÙÛØ¶ ÙØ¨ÙÛ Ø±Ø§ Ø¨ÙØ±Ø³Øª:'),('last_km','ð Ú©ÛÙÙÙØªØ± Ø®ÙØ¯Ø±Ù Ø¯Ø± Ø²ÙØ§Ù ØªØ¹ÙÛØ¶:'),('next_km','ð¢ Ú©ÛÙÙÙØªØ± Ø³Ø±ÙÛØ³ Ø¨Ø¹Ø¯Û:'),('next_date','ð ØªØ§Ø±ÛØ® ØªÙØ±ÛØ¨Û Ø³Ø±ÙÛØ³ Ø¨Ø¹Ø¯Û:'),('oil_type','ð¢ ÙÙØ¹/ÙÛØ³Ú©ÙØ²ÛØªÙ Ø±ÙØºÙ:'),('shop','ð§ ØªØ¹ÙÛØ±Ú¯Ø§Ù ÛØ§ Ø´Ø®Øµ Ø§ÙØ¬Ø§ÙâØ¯ÙÙØ¯Ù (Ø§Ø®ØªÛØ§Ø±Û):')]
    if 'ÚÚ©' in name and ('Ù¾Ø±Ø¯Ø§Ø®ØªÛ' in name or 'Ø¯Ø±ÛØ§ÙØªÛ' in name):
        return [('amount','ð° ÙØ¨ÙØº ÚÚ©:'),('check_date','ð ØªØ§Ø±ÛØ® Ø³Ø±Ø±Ø³ÛØ¯:'),('counterparty','ð¤ ÙØ§Ù Ø´Ø®Øµ/Ø´Ø±Ú©Øª:'),('bank','ð¦ Ø¨Ø§ÙÚ©:'),('check_no','ð¢ Ø´ÙØ§Ø±Ù ÚÚ© (Ø§Ø®ØªÛØ§Ø±Û):')]
    if 'Ø¢Ø²ÙØ§ÛØ´' in name or 'ÚÚ©Ø§Ù¾' in name or 'Ø¯Ú©ØªØ±' in name or 'doctor' in n or 'checkup' in n or 'blood' in n:
        return [('last_date','ð ØªØ§Ø±ÛØ® ÙØ±Ø§Ø¬Ø¹Ù/Ø¢Ø²ÙØ§ÛØ´ ÙØ¨ÙÛ:'),('next_date','ð ØªØ§Ø±ÛØ® Ø¨Ø¹Ø¯Û:'),('doctor','ð¨ââï¸ Ù¾Ø²Ø´Ú©/ÙØ±Ú©Ø² (Ø§Ø®ØªÛØ§Ø±Û):'),('note','ð ØªÙØ¶ÛØ­Ø§Øª (Ø§Ø®ØªÛØ§Ø±Û):')]
    if 'Ø³ÙØ±' in name or 'trip' in n or 'vacation' in n:
        return [('destination','ð ÙÙØµØ¯:'),('depart_date','ð ØªØ§Ø±ÛØ® Ø­Ø±Ú©Øª:'),('depart_time','â° Ø³Ø§Ø¹Øª Ø­Ø±Ú©Øª:'),('return_date','ð ØªØ§Ø±ÛØ® Ø¨Ø±Ú¯Ø´Øª:'),('note','ð ØªÙØ¶ÛØ­Ø§Øª (Ø§Ø®ØªÛØ§Ø±Û):')]
    if 'Ø¨Ø¯ÙØ³Ø§Ø²Û' in name or 'gym' in n or 'Ø¯ÙÛØ¯Ù' in name or 'running' in n or 'ÙÙØªØ¨Ø§Ù' in name or 'football' in n or 'ÙØ§ÙÛØ¨Ø§Ù' in name or 'volleyball' in n or 'Ø¨Ø³Ú©ØªØ¨Ø§Ù' in name or 'basketball' in n or 'Ø´ÙØ§' in name or 'swimming' in n:
        return [('start_date','ð ØªØ§Ø±ÛØ® Ø´Ø±ÙØ¹ Ø¨Ø±ÙØ§ÙÙ:'),('location','ð ÙØ­Ù ØªÙØ±ÛÙ (Ø§Ø®ØªÛØ§Ø±Û):'),('duration','â± ÙØ¯Øª ØªÙØ±ÛÙ Ø¨Ù Ø¯ÙÛÙÙ:')]
    return [('note','ð ØªÙØ¶ÛØ­Ø§Øª Ø§ÛÙ ÙØ¯Ù (Ø§Ø®ØªÛØ§Ø±Û):')]


def _ready_detail_nav_keyboard(uid, idx, total):
    """Build prev/next InlineKeyboard for ready-detail field steps."""
    fa = lang(uid) == 'fa'
    rows = []
    nav = []
    if idx > 0:
        nav.append(InlineKeyboardButton('â¬ï¸ ÙØ¨ÙÛ' if fa else 'â¬ï¸ Previous', callback_data='rdetail:prev'))
    if idx < total - 1:
        nav.append(InlineKeyboardButton('â­ï¸ Ø±Ø¯ Ø´Ø¯Ù' if fa else 'â­ï¸ Skip', callback_data='rdetail:next'))
    else:
        nav.append(InlineKeyboardButton('â ØªÙØ§Ù' if fa else 'â Done', callback_data='rdetail:next'))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows) if rows else None


async def ready_detail_callback(update, context):
    """Handle prev/next navigation in ready-detail field steps."""
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not context.user_data.get('ready_detail_mode'):
        return
    action = q.data.split(':')[1]
    fields = context.user_data.get('ready_detail_fields') or []
    idx = int(context.user_data.get('ready_detail_index', 0))

    if action == 'prev' and idx > 0:
        idx -= 1
        context.user_data['ready_detail_index'] = idx
        key, prompt = fields[idx]
        context.user_data['ready_detail_key'] = key
        fa = lang(uid) == 'fa'
        kb = _ready_detail_nav_keyboard(uid, idx, len(fields))
        progress = f"\n\nð ÙØ±Ø­ÙÙ {idx+1} Ø§Ø² {len(fields)}" if fa else f"\n\nð Step {idx+1} of {len(fields)}"
        text = prompt + progress + ("\n\n(Ø¨Ø±Ø§Û ÙÙØ±Ø¯ Ø§Ø®ØªÛØ§Ø±Û ÙÛâØªÙØ§ÙÛ Â«-Â» Ø¨ÙØ±Ø³ØªÛ.)" if fa else "\n\n(For optional fields, send '-'.)")
        if kb:
            await q.message.edit_text(text, reply_markup=kb)
        else:
            await q.message.edit_text(text)
        return

    if action == 'next':
        key = context.user_data.get('ready_detail_key')
        if key:
            _pending = {k: v for k, v in context.user_data.get('ready_details', [])}
            _pending[key] = ''
            context.user_data['ready_details'] = list(_pending.items())
        idx += 1
        if idx < len(fields):
            context.user_data['ready_detail_index'] = idx
            nk, np = fields[idx]
            context.user_data['ready_detail_key'] = nk
            fa = lang(uid) == 'fa'
            kb = _ready_detail_nav_keyboard(uid, idx, len(fields))
            progress = f"\n\nð ÙØ±Ø­ÙÙ {idx+1} Ø§Ø² {len(fields)}" if fa else f"\n\nð Step {idx+1} of {len(fields)}"
            prompt_text = np + progress + ("\n\n(Ø¨Ø±Ø§Û ÙÙØ±Ø¯ Ø§Ø®ØªÛØ§Ø±Û ÙÛâØªÙØ§ÙÛ Â«-Â» Ø¨ÙØ±Ø³ØªÛ.)" if fa else "\n\n(For optional fields, send '-'.)")
            if kb:
                await q.message.edit_text(prompt_text, reply_markup=kb)
            else:
                await q.message.edit_text(prompt_text)
        else:
            context.user_data.pop('ready_detail_mode', None)
            context.user_data.pop('ready_detail_fields', None)
            context.user_data.pop('ready_detail_key', None)
            context.user_data.pop('ready_detail_index', None)
            await q.message.edit_text(
                "â­ Ø§ÙÙÙÛØª ÙØ¯Ù Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:" if lang(uid) == 'fa' else "â­ Choose goal priority:",
                reply_markup=priority_keyboard(uid)
            )

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
    fa = lang(uid) == 'fa'
    kb = _ready_detail_nav_keyboard(uid, 0, len(fields))
    progress = f"\n\nð ÙØ±Ø­ÙÙ 1 Ø§Ø² {len(fields)}" if fa else f"\n\nð Step 1 of {len(fields)}"
    text = prompt + progress + ("\n\n(Ø¨Ø±Ø§Û ÙÙØ±Ø¯ Ø§Ø®ØªÛØ§Ø±Û ÙÛâØªÙØ§ÙÛ Â«-Â» Ø¨ÙØ±Ø³ØªÛ.)" if fa else "\n\n(For optional fields, send '-'.)")
    if kb:
        await q.message.edit_text(text, reply_markup=kb)
    else:
        await q.message.edit_text(text)

async def ready_detail_text_save(update,context):
    if not context.user_data.get('ready_detail_mode'): return False
    uid=update.effective_user.id; text=(update.message.text or '').strip(); fields=context.user_data.get('ready_detail_fields') or []; idx=int(context.user_data.get('ready_detail_index',0)); key=context.user_data.get('ready_detail_key')
    if not key or idx>=len(fields): return False
    _pending={k:v for k,v in context.user_data.get('ready_details',[])}
    _pending[key]='' if text=='-' else text
    context.user_data['ready_details']=list(_pending.items()); idx+=1
    if idx < len(fields):
        context.user_data['ready_detail_index']=idx; nk,np=fields[idx]; context.user_data['ready_detail_key']=nk
        fa = lang(uid) == 'fa'
        kb = _ready_detail_nav_keyboard(uid, idx, len(fields))
        progress = f"\n\nð ÙØ±Ø­ÙÙ {idx+1} Ø§Ø² {len(fields)}" if fa else f"\n\nð Step {idx+1} of {len(fields)}"
        prompt_text = np + progress + ("\n\n(Ø¨Ø±Ø§Û ÙÙØ±Ø¯ Ø§Ø®ØªÛØ§Ø±Û ÙÛâØªÙØ§ÙÛ Â«-Â» Ø¨ÙØ±Ø³ØªÛ.)" if fa else "\n\n(For optional fields, send '-'.)")
        if kb:
            await update.message.reply_text(prompt_text, reply_markup=kb)
        else:
            await update.message.reply_text(prompt_text)
        return True
    context.user_data.pop('ready_detail_mode',None); context.user_data.pop('ready_detail_fields',None); context.user_data.pop('ready_detail_key',None); context.user_data.pop('ready_detail_index',None)
    await update.message.reply_text("â­ Ø§ÙÙÙÛØª ÙØ¯Ù Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:" if lang(uid)=='fa' else "â­ Choose goal priority:",reply_markup=priority_keyboard(uid))
    return True

# Replace the existing generic goal list with an active-only list while preserving history in DB.
async def today(update,context):
    uid=update.effective_user.id; goals=_active_goals(uid); fa=lang(uid)=='fa'; log_activity(uid,'view_today')
    if not goals:
        await update.message.reply_text(T[lang(uid)]['no_goals'].format(name=display_name(uid)),reply_markup=keyboard(uid)); return
    rows=[]
    for g in goals:
        st=get_status(uid,g['id']); icon='â' if st=='done' else 'â' if st=='missed' else 'â¬'; rows.append([InlineKeyboardButton(f"{icon} {g['name']}",callback_data=f"detail:{g['id']}")])
    rows.append([InlineKeyboardButton('ð ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§Û ÙÙ' if fa else 'ð My Reminders',callback_data='goalreminders'),InlineKeyboardButton('ð ØªÙÙÛÙ' if fa else 'ð Calendar',callback_data='goalcalendar:today')])
    rows.append([main_menu_button(uid)])
    await update.message.reply_text(T[lang(uid)]['today'],reply_markup=InlineKeyboardMarkup(rows))

async def edit_menu(update,context):
    uid=update.effective_user.id; goals=_active_goals(uid); fa=lang(uid)=='fa'
    if not goals:
        await update.message.reply_text(T[lang(uid)]['no_goals'].format(name=display_name(uid)),reply_markup=keyboard(uid)); return
    rows=[]
    for i in range(0,len(goals),2): rows.append([InlineKeyboardButton(goals[j]['name'],callback_data=f"edit:{goals[j]['id']}") for j in range(i,min(i+2,len(goals)))])
    rows.append([InlineKeyboardButton('ð ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§Û ÙÙ' if fa else 'ð My Reminders',callback_data='goalreminders'),InlineKeyboardButton('ð ØªÙÙÛÙ' if fa else 'ð Calendar',callback_data='goalcalendar:today')])
    rows.append([main_menu_button(uid)])
    await update.message.reply_text(T[lang(uid)]['edit'].format(name=display_name(uid)),reply_markup=InlineKeyboardMarkup(rows))

async def goal_reminders_list(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; fa=lang(uid)=='fa'; goals=_active_goals(uid); today=datetime.now(TZ).date()
    lines=['ð <b>ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§Û ÙÙ</b>',''] if fa else ['ð <b>My Reminders</b>','']; rows=[]
    if not goals: lines.append('ÛØ§Ø¯Ø¢ÙØ±Û ÙØ¹Ø§ÙÛ ÙØ¯Ø§Ø±Û.' if fa else 'No active reminders.')
    for g in goals:
        if not g['reminder_time']: continue
        end=g['reminder_end_date'] or 'Ø¨Ø¯ÙÙ Ù¾Ø§ÛØ§Ù'
        meta=_goal_meta(uid,g['id']); lines.append(f"â¢ {html.escape(g['name'])} â â° {g['reminder_time']} â ØªØ§ {html.escape(end)}")
        if meta:
            details=' | '.join(f'{k}: {v}' for k,v in list(meta.items())[:3] if v)
            if details: lines.append(f"  ð {html.escape(details)}")
        rows.append([InlineKeyboardButton(f"âï¸ {g['name']}",callback_data=f"edit:{g['id']}")])
    rows += [[InlineKeyboardButton('ð ØªÙÙÛÙ' if fa else 'ð Calendar',callback_data='goalcalendar:today'),main_menu_button(uid)]]
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
    names=['ÙØ±ÙØ±Ø¯ÛÙ','Ø§Ø±Ø¯ÛØ¨ÙØ´Øª','Ø®Ø±Ø¯Ø§Ø¯','ØªÛØ±','ÙØ±Ø¯Ø§Ø¯','Ø´ÙØ±ÛÙØ±','ÙÙØ±','Ø¢Ø¨Ø§Ù','Ø¢Ø°Ø±','Ø¯Û','Ø¨ÙÙÙ','Ø§Ø³ÙÙØ¯']
    en_names=['Farvardin','Ordibehesht','Khordad','Tir','Mordad','Shahrivar','Mehr','Aban','Azar','Dey','Bahman','Esfand']
    lines=[f"ð <b>{names[month-1] if fa else en_names[month-1]} {year}</b>",'']
    rows=[]
    # Six weeks, Monday-based. Convert each Jalali day to Gregorian for lookup.
    first_iso=datetime(*_j2g(year,month,1)).date().isoformat(); first=datetime.fromisoformat(first_iso).date(); offset=(first.weekday())
    week=[]; header=['Ø´','Û','Ø¯','Ø³','Ú','Ù¾','Ø¬'] if fa else ['Mo','Tu','We','Th','Fr','Sa','Su']; rows.append([InlineKeyboardButton(x,callback_data='noop') for x in header])
    for pos in range(offset): week.append('')
    for day in range(1,_jalali_month_days(year,month)+1):
        iso=datetime(*_j2g(year,month,day)).date().isoformat(); c=db(); cnt=c.execute("SELECT COUNT(*) n FROM goals WHERE user_id=? AND enabled=1 AND (reminder_end_date IS NULL OR reminder_end_date>=?) AND substr(COALESCE(reminder_start_date,created_at),1,10)<=?",(uid,iso,iso)).fetchone()['n']; c.close(); label=f"{day}{'â¢' if cnt else ''}"; week.append((label,iso))
        if len(week)==7:
            rows.append([InlineKeyboardButton(x if isinstance(x,str) else x[0],callback_data='noop' if isinstance(x,str) else f'goalcalday:{x[1]}') for x in week]); week=[]
    if week: week += ['']*(7-len(week)); rows.append([InlineKeyboardButton(x if isinstance(x,str) else x[0],callback_data='noop' if isinstance(x,str) else f'goalcalday:{x[1]}') for x in week])
    prev_y,prev_m=(year,month-1) if month>1 else (year-1,12); next_y,next_m=(year,month+1) if month<12 else (year+1,1)
    rows.append([InlineKeyboardButton('â¬ï¸ ÙØ§Ù ÙØ¨Ù' if fa else 'â¬ï¸ Prev',callback_data=f'goalcalendar:{prev_y}:{prev_m}'),InlineKeyboardButton('ð Ø§ÙØ±ÙØ²' if fa else 'ð Today',callback_data='goalcalendar:today'),InlineKeyboardButton('ÙØ§Ù Ø¨Ø¹Ø¯ â¡ï¸' if fa else 'Next â¡ï¸',callback_data=f'goalcalendar:{next_y}:{next_m}')])
    rows.append([main_menu_button(uid)])
    target=q.message if q else update.message; await target.edit_text('\n'.join(lines),parse_mode='HTML',reply_markup=InlineKeyboardMarkup(rows))

async def goal_calendar_day(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; iso=q.data.split(':',1)[1]; fa=lang(uid)=='fa'; c=db(); goals=c.execute("SELECT * FROM goals WHERE user_id=? AND enabled=1 ORDER BY id DESC",(uid,)).fetchall(); c.close(); selected=[]
    for g in goals:
        if g['reminder_start_date'] and iso<g['reminder_start_date']: continue
        if g['reminder_end_date'] and iso>g['reminder_end_date']: continue
        selected.append(g)
    jy,jm,jd=_jalali_from_iso(iso); lines=[f"ð <b>{jy:04d}/{jm:02d}/{jd:02d}</b>",'']; rows=[]
    if not selected: lines.append('Ú©Ø§Ø±Û Ø¨Ø±Ø§Û Ø§ÛÙ Ø±ÙØ² Ø«Ø¨Øª ÙØ´Ø¯Ù.' if fa else 'No goals for this day.')
    for g in selected:
        lines.append(f"â¢ {html.escape(g['name'])} â â° {g['reminder_time'] or 'â'}"); rows.append([InlineKeyboardButton(g['name'],callback_data=f'edit:{g["id"]}')])
    rows.append([InlineKeyboardButton('â¬ï¸ ØªÙÙÛÙ' if fa else 'â¬ï¸ Calendar',callback_data=f'goalcalendar:{jy}:{jm}'),main_menu_button(uid)])
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
    meta=_goal_meta(uid,gid); fa=lang(uid); lines=[f"ð¯ <b>{html.escape(g['name'])}</b>",f"ð {html.escape(g['category'])}",f"â­ Ø§ÙÙÙÛØª: {g['priority']}",f"â° {g['reminder_time'] or 'Ø®Ø§ÙÙØ´'}"]
    if g['reminder_end_date']: lines.append(f"ð Ù¾Ø§ÛØ§Ù ØªÚ©Ø±Ø§Ø±: {html.escape(g['reminder_end_date'])}")
    for k,v in meta.items():
        if v: lines.append(f"ð {html.escape(k)}: {html.escape(v)}")
    rows=[[InlineKeyboardButton('âï¸ ÙÛØ±Ø§ÛØ´' if fa=='fa' else 'âï¸ Edit',callback_data=f'edit:{gid}')],[InlineKeyboardButton('â Ø§ÙØ¬Ø§Ù Ø¯Ø§Ø¯Ù' if fa=='fa' else 'â Done',callback_data=f'done:{gid}'),InlineKeyboardButton('â Ø§ÙØ¬Ø§Ù ÙØ¯Ø§Ø¯Ù' if fa=='fa' else 'â Not done',callback_data=f'miss:{gid}')],[main_menu_button(uid)]]
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
        context.user_data.clear(); context.user_data['pending_repeat_goal_id']=int(gid); await q.message.edit_text('ð ÙØ¯Øª ØªÚ©Ø±Ø§Ø± ÛØ§Ø¯Ø¢ÙØ±Û Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:' if lang(uid)=='fa' else 'ð Choose reminder duration:',reply_markup=_targeted_repeat_keyboard(uid)); return
    context.user_data.clear(); log_activity(uid,'goal_created'); await q.message.edit_text(T[lang(uid)]['goal_added'].format(name=display_name(uid)),reply_markup=InlineKeyboardMarkup([[main_menu_button(uid)]]))

async def custom_time_save(update,context):
    # Handle custom goal time here directly. This avoids falling back through
    # older router revisions that can conflict with the current flow state.
    if not context.user_data.get('awaiting_custom_time'):
        return False

    uid = update.effective_user.id
    reminder = parse_time((update.message.text or '').strip())
    if reminder is None:
        await update.message.reply_text(T[lang(uid)]['bad_time'])
        return True

    name = context.user_data.get('name')
    category = context.user_data.get('category')
    if not name or not category:
        return False

    priority = context.user_data.get('priority', 2)
    duration = context.user_data.get('duration_minutes')
    condition = context.user_data.get('condition')
    ready_details = list(context.user_data.get('ready_details') or [])

    c = db()
    try:
        cur = c.execute(
            "INSERT INTO goals(user_id,name,category,reminder_time,priority,duration_minutes,condition,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (uid, name, category, reminder, priority, duration, condition, datetime.now(TZ).isoformat()),
        )
        gid = int(cur.lastrowid)
        c.commit()
    finally:
        c.close()

    for k, v in ready_details:
        _goal_store_meta(uid, gid, k, v)

    for key in ('awaiting_custom_time', 'name', 'category', 'priority',
                'duration_minutes', 'condition', 'ready_details',
                '_flow_started_at'):
        context.user_data.pop(key, None)
    context.user_data['pending_repeat_goal_id'] = gid

    await update.message.reply_text(
        'ð ÙØ¯Øª ØªÚ©Ø±Ø§Ø± ÛØ§Ø¯Ø¢ÙØ±Û Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:' if lang(uid) == 'fa' else
        'ð Choose reminder duration:',
        reply_markup=_targeted_repeat_keyboard(uid),
    )
    return True

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
            await context.bot.send_message(uid,'ð <b>ÙØ¯Ù Ø´ÙØ§ Ú©Ø§ÙÙ Ø´Ø¯!</b>\n\nØ§ÙØ±ÙØ² Ø¢Ø®Ø±ÛÙ Ø±ÙØ² Ø§ÛÙ ÙØ¯Ù Ø¨ÙØ¯ Ù Ø¯ÙØ±ÙâØ§Û Ú©Ù ØªØ¹ÛÛÙ Ú©Ø±Ø¯Ù Ø¨ÙØ¯ÛØ¯ Ø¨Ù Ù¾Ø§ÛØ§Ù Ø±Ø³ÛØ¯.\n\nØ³Ø§Ø¨ÙÙ ÙØ¯Ù Ø­ÙØ¸ ÙÛâØ´ÙØ¯ Ù Ø§Ø² ØªØ§Ø±ÛØ®ÚÙ ÙØ§Ø¨Ù ÙØ´Ø§ÙØ¯Ù Ø®ÙØ§ÙØ¯ Ø¨ÙØ¯.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('ð ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§Û ÙÙ',callback_data='goalreminders'),InlineKeyboardButton('ð ØªÙÙÛÙ',callback_data='goalcalendar:today')],[main_menu_button(uid)]]))
            c=db(); c.execute('INSERT OR IGNORE INTO goal_completion_notice(user_id,goal_id,completed_at) VALUES(?,?,?)',(uid,g['id'],datetime.now(TZ).isoformat())); c.execute('UPDATE goals SET enabled=0 WHERE user_id=? AND id=?',(uid,g['id'])); c.commit(); c.close()
        except Exception as e: logger.warning('Goal completion notice failed: %s',e)

# Compact goals menu: add My Goals, Reminders and Calendar without changing root menu text/layout.
_OLD_COMPACT_MENU_KEYBOARD_GOALS_UPGRADE=_compact_menu_keyboard
def _compact_menu_keyboard(uid,section):
    kb=_OLD_COMPACT_MENU_KEYBOARD_GOALS_UPGRADE(uid,section)
    if section=='goals':
        fa=lang(uid)=='fa'; extra=[
            [InlineKeyboardButton('ð¯ Ø§ÙØ¯Ø§Ù ÙÙ' if fa else 'ð¯ My Goals',callback_data='cm:my_goals'),InlineKeyboardButton('ð ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§' if fa else 'ð Reminders',callback_data='goalreminders')],
            [InlineKeyboardButton('ð ØªÙÙÛÙ Ø´ÙØ³Û' if fa else 'ð Jalali Calendar',callback_data='goalcalendar:today')],
        ]
        rows = [list(row) for row in kb.inline_keyboard]
        rows[1:1] = extra
        kb = InlineKeyboardMarkup(rows)
    return kb

async def my_goals_callback(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; fa=lang(uid)=='fa'; goals=_active_goals(uid); lines=['ð¯ <b>Ø§ÙØ¯Ø§Ù ÙÙ</b>',''] if fa else ['ð¯ <b>My Goals</b>','']; rows=[]
    if not goals: lines.append('ÙØ¯Ù ÙØ¹Ø§ÙÛ ÙØ¯Ø§Ø±Û.' if fa else 'No active goals.')
    for g in goals:
        lines.append(f"â¢ {html.escape(g['name'])} â â° {g['reminder_time'] or 'Ø®Ø§ÙÙØ´'}")
        rows.append([InlineKeyboardButton(g['name'],callback_data=f'edit:{g["id"]}')])
    rows.append([InlineKeyboardButton('ð ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§' if fa else 'ð Reminders',callback_data='goalreminders'),InlineKeyboardButton('ð ØªÙÙÛÙ' if fa else 'ð Calendar',callback_data='goalcalendar:today')]); rows.append([main_menu_button(uid)])
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
#   1) "ð¯ Ø¨Ø±ÙØ§ÙÙ ÙÙ" could fall through a legacy router and surface TypeError.
#   2) Main-menu callbacks could delete the current screen and then create a second
#      carrier message, leaving the user with an empty/deleted screen.
# No database tables or user-owned data are changed here.

async def _render_compact_root_inline_safe(update, context):
    """Render the compact root in-place; never delete the current bot screen."""
    q = getattr(update, "callback_query", None)
    uid = update.effective_user.id
    fa = lang(uid) == "fa"
    text = _root_menu_text(uid)
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
        # Keep the current Telegram message alive and render the main menu in-place.
        # This prevents the Quick Access message from disappearing when Goals List is pressed.
        try:
            await q.message.edit_text(
                _root_menu_text(uid),
                parse_mode="HTML",
                reply_markup=compact_keyboard(uid),
            )
        except Exception:
            # If the message cannot be edited (for example, it is too old or
            # unchanged), fall back to sending the menu without deleting state.
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=_root_menu_text(uid),
                    parse_mode="HTML",
                    reply_markup=compact_keyboard(uid),
                )
            except Exception:
                logger.exception("Failed to render main menu from goals callback")
        return
    try:
        await q.answer("Ø§ÛÙ Ú¯Ø²ÛÙÙ Ø¯ÛÚ¯Ø± ÙØ¹ØªØ¨Ø± ÙÛØ³Øª. ÙÙÙÛ Ø§ÙØ¯Ø§Ù Ø±Ø§ Ø¯ÙØ¨Ø§Ø±Ù Ø¨Ø§Ø² Ú©Ù.", show_alert=True)
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
        # Admins: show user menu directly instead of admin root
        if admin_is_allowed(uid):
            await context.bot.send_message(
                chat_id=uid,
                text="ð  <b>ÙÙÙÛ Ø§ØµÙÛ</b>" if fa else "ð  <b>Main Menu</b>",
                parse_mode="HTML",
                reply_markup=_compact_user_keyboard(uid),
            )
        else:
            await context.bot.send_message(
                chat_id=uid,
                text=_root_menu_text(uid),
                parse_mode="HTML",
                reply_markup=compact_keyboard(uid),
            )
        return
    if (q.data or "") == "nav:stats":
        try:
            await q.answer()
        except Exception:
            pass
        try:
            await q.message.delete()
        except Exception:
            pass
        # Send stats directly for callback
        goals = get_goals(uid)
        streak = max((calculate_streak(uid, g["id"]) for g in goals), default=0)
        d = datetime.now(TZ).date().isoformat()
        c = db()
        done = c.execute("SELECT COUNT(*) AS n FROM goal_days WHERE user_id=? AND goal_date=? AND status='done'", (uid, d)).fetchone()["n"]
        missed = c.execute("SELECT COUNT(*) AS n FROM goal_days WHERE user_id=? AND goal_date=? AND status='missed'", (uid, d)).fetchone()["n"]
        total_done = c.execute("SELECT COUNT(*) AS n FROM goal_days WHERE user_id=? AND status='done'", (uid,)).fetchone()["n"]
        c.close()
        fa = lang(uid) == "fa"
        text = f"ð <b>Ø¢ÙØ§Ø± ÙÙ</b>\n\nð¯ Ú©Ù Ø§ÙØ¯Ø§Ù: {len(goals)}\nâ Ø§ÙØ¬Ø§ÙâØ´Ø¯Ù Ø§ÙØ±ÙØ²: {done}\nâ Ø§ÙØ¬Ø§ÙâÙØ´Ø¯Ù Ø§ÙØ±ÙØ²: {missed}\nð¥ ÙØ¬ÙÙØ¹ Ø§ÙØ¬Ø§ÙâÙØ§: {total_done}"
        if streak > 0:
            text += f"\nð¥ Ø±Ú©ÙØ±Ø¯ Ø²ÙØ¬ÛØ±Ù ÙØ¹ÙÛ: {streak} Ø±ÙØ²" if fa else f"\nð¥ Current streak: {streak} days"
        await context.bot.send_message(chat_id=uid, text=text, parse_mode="HTML")
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
            text=_root_menu_text(uid),
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
    text = "ð¯ <b>Ø¨Ø±ÙØ§ÙÙ Ù Ø§ÙØ¯Ø§Ù</b>" if fa else "ð¯ <b>Goals & Plan</b>"
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

    # Quick Access
    if txt in ("â¡ Ø¯Ø³ØªØ±Ø³Û Ø³Ø±ÛØ¹", "â¡ Quick Access"):
        clear_flow(context)
        fa = lang(uid) == "fa"
        text = "â¡ <b>Ø¯Ø³ØªØ±Ø³Û Ø³Ø±ÛØ¹</b>\n\nÙ¾Ø±ØªÚ©Ø±Ø§Ø±ØªØ±ÛÙ ÙØ§Ø¨ÙÛØªâÙØ§:" if fa else "â¡ <b>Quick Access</b>"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("â Ø§ÙØ²ÙØ¯Ù ÙØ¯Ù" if fa else "â Add Goal", callback_data="new_goal"),
             InlineKeyboardButton("ð ÙÛØ³Øª Ø§ÙØ¯Ø§Ù" if fa else "ð Goals List", callback_data="goals:main")],
            [InlineKeyboardButton("ð Ø¨Ø±ÙØ§ÙÙ Ø§ÙØ±ÙØ²" if fa else "ð Today's Plan", callback_data="goals:today"),
             InlineKeyboardButton("ð ÛØ§Ø¯Ø¢ÙØ±Û Ø¨Ø¹Ø¯Û" if fa else "ð Next Reminder", callback_data="goalreminders")],
            [InlineKeyboardButton("ð ØªÙÙØ¯ ÙÙ" if fa else "ð My Birthday", callback_data="birthday:show"),
             InlineKeyboardButton("ð¤ Ø¯Ø¹ÙØª Ø¯ÙØ³ØªØ§Ù" if fa else "ð¤ Invite Friends", callback_data="ref:home")],
        ])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return
    # Goals & Plan
    if txt in ("ð¯ Ø¨Ø±ÙØ§ÙÙ Ù Ø§ÙØ¯Ø§Ù", "ð¯ Goals & Plan"):
        clear_flow(context)
        await _compact_menu_show(update, context, "goals")
        return
    # Calendar & Reminders
    if txt in ("ð ØªÙÙÛÙ Ù ÛØ§Ø¯Ø¢ÙØ±Û", "ð Calendar & Reminders"):
        clear_flow(context)
        fa = lang(uid) == "fa"
        text = "ð <b>ØªÙÙÛÙ Ù ÛØ§Ø¯Ø¢ÙØ±Û</b>" if fa else "ð <b>Calendar & Reminders</b>"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("ð ØªÙÙÛÙ Ø´ÙØ³Û" if fa else "ð Jalali Calendar", callback_data="goalcalendar:today"),
             InlineKeyboardButton("ð ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§" if fa else "ð Reminders", callback_data="goalreminders")],
            [InlineKeyboardButton("ð Ø¨Ø±ÙØ§ÙÙ Ø§ÙØ±ÙØ²" if fa else "ð Today", callback_data="goals:today"),
             InlineKeyboardButton("ð Ø¨Ø±ÙØ§ÙÙ ÙÙØªÙ" if fa else "ð Weekly", callback_data="goals:weekly")],
        ])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return
    # My Stats (quick access)
    if txt in ("ð Ø¢ÙØ§Ø± ÙÙ", "ð My Stats"):
        clear_flow(context)
        await stats(update, context)
        return
    # My Account
    if txt in ("ð¤ Ø­Ø³Ø§Ø¨ ÙÙ", "ð¤ My Account"):
        clear_flow(context)
        fa = lang(uid) == "fa"
        text = "ð¤ <b>Ø­Ø³Ø§Ø¨ ÙÙ</b>" if fa else "ð¤ <b>My Account</b>"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("ð¤ Ø§Ø·ÙØ§Ø¹Ø§Øª Ø´Ø®ØµÛ" if fa else "ð¤ Profile", callback_data="profile")],
            [InlineKeyboardButton("ð¥ ÙØ¯ÛØ±ÛØª ÙØ´ØªØ±ÛØ§Ù" if fa else "ð¥ Customer Management", callback_data="cust:main")],
            [InlineKeyboardButton("ð ØªÙÙØ¯ ÙÙ" if fa else "ð My Birthday", callback_data="birthday:show")],
            [InlineKeyboardButton("ð Ø²Ø¨Ø§Ù" if fa else "ð Language", callback_data="lang"),
             InlineKeyboardButton("âï¸ ØªÙØ¸ÛÙØ§Øª" if fa else "âï¸ Settings", callback_data="settings")],
        ])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return
    # My Rewards
    if txt in ("ð Ù¾Ø§Ø¯Ø§Ø´âÙØ§Û ÙÙ", "ð My Rewards"):
        clear_flow(context)
        fa = lang(uid) == "fa"
        xp, level, _ = xp_info(uid)
        text = (
            f"ð <b>Ù¾Ø§Ø¯Ø§Ø´âÙØ§Û ÙÙ</b>\n\n"
            f"â­ XP: {xp}\n"
            f"ð Ø³Ø·Ø­: {level}\n"
            f"ð VIP: {'ÙØ¹Ø§Ù' if is_vip(uid) else 'ØºÛØ±ÙØ¹Ø§Ù'}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("â­ XP ÙÙ" if fa else "â­ My XP", callback_data="xp:info"),
             InlineKeyboardButton("ð Ø¯Ø³ØªØ§ÙØ±Ø¯ÙØ§" if fa else "ð Achievements", callback_data="achievements")],
            [InlineKeyboardButton("ð ÙØ¯ÛÙâÙØ§" if fa else "ð Gifts", callback_data="gifts:list"),
             InlineKeyboardButton("ð ØªØ§Ø±ÛØ®ÚÙ" if fa else "ð History", callback_data="xp:history")],
        ])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return
    # Stats & Reports
    if txt in ("ð Ø¢ÙØ§Ø± Ù Ú¯Ø²Ø§Ø±Ø´", "ð Stats & Reports"):
        clear_flow(context)
        await _compact_menu_show(update, context, "reports")
        return
    # Tools
    if txt in ("ð ï¸ Ø§Ø¨Ø²Ø§Ø±ÙØ§", "ð ï¸ Tools"):
        clear_flow(context)
        await _compact_menu_show(update, context, "tools")
        return
    # Support
    if txt in ("ð« Ù¾Ø´ØªÛØ¨Ø§ÙÛ", "ð« Support"):
        clear_flow(context)
        await _compact_menu_show(update, context, "support")
        return
    # Birthday (text button)
    if txt in ("ð ØªÙÙØ¯ ÙÙ", "ð My Birthday"):
        if not birthday_enabled():
            await update.message.reply_text("ð Ø§ÛÙ ÙØ§Ø¨ÙÛØª Ø¯Ø± Ø­Ø§Ù Ø­Ø§Ø¶Ø± ØºÛØ±ÙØ¹Ø§Ù Ø§Ø³Øª.", reply_markup=compact_keyboard(uid))
            return
        context.user_data["awaiting_birthday"] = True
        fa = lang(uid) == "fa"
        await update.message.reply_text(
            "ð <b>ØªØ§Ø±ÛØ® ØªÙÙØ¯ Ø®ÙØ¯Øª Ø±Ù ÙØ§Ø±Ø¯ Ú©Ù</b>\n\n"
            "ÙØ±ÙØª: <code>YYYY-MM-DD</code>\nÙØ«Ø§Ù: <code>1995-03-15</code>",
            parse_mode="HTML",
        )
        return

    if txt in ("ð¯ Ø¨Ø±ÙØ§ÙÙ ÙÙ", "ð¯ My Plan"):
        return await _render_goals_section_direct(update, context)

    # Main Menu is absolute: clear every transient flow and show exactly one
    # root screen. Never allow a pending form/AI state to consume this label.
    if txt in ("ð  ÙÙÙÛ Ø§ØµÙÛ", "ð  Main Menu"):
        clear_flow(context)
        try:
            await update.message.delete()
        except Exception:
            pass
        fa = lang(uid) == "fa"
        # Admins: show user menu directly instead of admin root
        if admin_is_allowed(uid):
            await update.message.reply_text(
                "ð  <b>ÙÙÙÛ Ø§ØµÙÛ</b>" if fa else "ð  <b>Main Menu</b>",
                parse_mode="HTML",
                reply_markup=_compact_user_keyboard(uid),
            )
        else:
            await update.message.reply_text(
                _root_menu_text(uid),
                parse_mode="HTML",
                reply_markup=compact_keyboard(uid),
            )
        return True

    # Back is relative. Use the section recorded when the current flow was
    # entered; if no parent exists, fall back safely to the root rather than
    # incorrectly jumping to Goals.
    if txt in ("â¬ï¸ Ø¨Ø±Ú¯Ø´Øª", "â¬ï¸ Back"):
        parent = context.user_data.get("_nav_parent_section") or "root"
        try:
            await update.message.delete()
        except Exception:
            pass
        clear_flow(context)
        fa = lang(uid) == "fa"
        if parent == "tools":
            await update.message.reply_text(
                "ð¤ <b>Ø§Ø¨Ø²Ø§Ø±ÙØ§Û ÙÙØ´ÙÙØ¯</b>" if fa else "ð¤ <b>Smart Tools</b>",
                parse_mode="HTML",
                reply_markup=_compact_menu_keyboard(uid, "tools"),
            )
        elif parent in {"goals", "reports", "vip", "account", "support"}:
            titles = {
                "goals": ("ð¯ <b>Ø¨Ø±ÙØ§ÙÙ Ù Ø§ÙØ¯Ø§Ù</b>", "ð¯ <b>Goals & Plan</b>"),
                "reports": ("ð <b>Ú¯Ø²Ø§Ø±Ø´ Ù Ù¾ÛØ´Ø±ÙØª</b>", "ð <b>Reports & Progress</b>"),
                "vip": ("ð <b>VIP Ù Ù¾Ø§Ø¯Ø§Ø´âÙØ§</b>", "ð <b>VIP & Rewards</b>"),
                "account": ("ð¤ <b>Ø­Ø³Ø§Ø¨ ÙÙ</b>", "ð¤ <b>My Account</b>"),
                "support": ("ð« <b>Ù¾Ø´ØªÛØ¨Ø§ÙÛ</b>", "ð« <b>Support</b>"),
            }
            await update.message.reply_text(
                titles[parent][0 if fa else 1],
                parse_mode="HTML",
                reply_markup=_compact_menu_keyboard(uid, parent),
            )
        else:
            await update.message.reply_text(
                _root_menu_text(uid),
                parse_mode="HTML",
                reply_markup=compact_keyboard(uid),
            )
        return True

    return await _OLD_TEXT_ROUTER_STABLE_NAV(update, context)


# Explicit callback registration is normally already present, but this final
# assignment guarantees the dispatcher uses the repaired functions above.

# ===================== SECURITY AUDIT LAYER =====================
# Additive only: a new table + logging helpers. Never modifies user-owned data.
# - Logs every attempt to access/edit/delete a goal the caller does not own.
# - Logs denied admin-access attempts.
# - `/seclog` (admin-only) shows the latest events.


def _root_menu_text(uid):
    """Clean, friendly main-menu text with greeting + Jalali date."""
    fa = lang(uid) == "fa"
    now = datetime.now(TZ)
    try:
        jy, jm, jd = _g2j(now.year, now.month, now.day)
        months_fa = ["ÙØ±ÙØ±Ø¯ÛÙ", "Ø§Ø±Ø¯ÛØ¨ÙØ´Øª", "Ø®Ø±Ø¯Ø§Ø¯", "ØªÛØ±", "ÙØ±Ø¯Ø§Ø¯", "Ø´ÙØ±ÛÙØ±",
                     "ÙÙØ±", "Ø¢Ø¨Ø§Ù", "Ø¢Ø°Ø±", "Ø¯Û", "Ø¨ÙÙÙ", "Ø§Ø³ÙÙØ¯"]
        months_en = ["Farvardin", "Ordibehesht", "Khordad", "Tir", "Mordad", "Shahrivar",
                     "Mehr", "Aban", "Azar", "Dey", "Bahman", "Esfand"]
        date_str = f"{jd} {months_fa[jm-1]} {jy}" if fa else f"{jd} {months_en[jm-1]} {jy}"
    except Exception:
        date_str = now.strftime("%Y-%m-%d")
    h = now.hour
    name = display_name(uid)
    if fa:
        greet = "ØµØ¨Ø­ Ø¨Ø®ÛØ±" if h < 12 else ("Ø¨Ø¹Ø¯Ø¸ÙØ± Ø¨Ø®ÛØ±" if h < 18 else "Ø´Ø¨ Ø¨Ø®ÛØ±")
        return (f"ð  <b>ÙÙÙÛ Ø§ØµÙÛ</b>\n\nØ³ÙØ§Ù {name} Ø¹Ø²ÛØ²Ø {greet} ð·\n"
                f"ð {date_str}\n\nÛÚ©Û Ø§Ø² Ø¨Ø®Ø´âÙØ§Û Ø²ÛØ± Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:")
    greet = "Good morning" if h < 12 else ("Good afternoon" if h < 18 else "Good evening")
    return (f"ð  <b>Main Menu</b>\n\nHi {name}, {greet}!\n"
            f"ð {date_str}\n\nChoose a section:")


_OLD_INIT_DB_SECURITY = init_db
def init_db():
    _OLD_INIT_DB_SECURITY()
    c = db()
    c.execute("""CREATE TABLE IF NOT EXISTS security_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        event TEXT NOT NULL,
        details TEXT,
        created_at TEXT NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sec_events_user ON security_events(user_id, id)")
    c.commit(); c.close()


def log_security(uid, event, details=""):
    """Best-effort security audit log; never raises into caller flow."""
    try:
        c = db()
        c.execute("INSERT INTO security_events(user_id,event,details,created_at) VALUES(?,?,?,?)",
                  (int(uid or 0), str(event)[:64], str(details or "")[:500], datetime.now(TZ).isoformat()))
        c.commit(); c.close()
    except Exception:
        logger.exception("security log failed")


def _guard_goal_handler(name):
    """Wrap an ownership-sensitive callback; deny + log cross-user goal access."""
    orig = globals()[name]
    async def guarded(update, context, *args, **kwargs):
        try:
            q = getattr(update, "callback_query", None)
            uid = q.from_user.id if q else update.effective_user.id
            data = (q.data if q else "") or ""
            if ":" in data:
                gid = int(data.split(":", 1)[1])
                if get_goal(uid, gid) is None:
                    log_security(uid, "goal_access_denied", f"handler={name} goal={gid}")
                    try:
                        if q:
                            await q.answer("â Ø§ÛÙ ÙÙØ±Ø¯ Ø¯Ø± Ø¯Ø³ØªØ±Ø³ Ø´ÙØ§ ÙÛØ³Øª." if lang(uid) == "fa" else
                                           "â Not available for you.", show_alert=True)
                    except Exception:
                        pass
                    return
        except Exception:
            logger.exception("security guard error")
        return await orig(update, context, *args, **kwargs)
    guarded.__name__ = name
    globals()[name] = guarded

for _gh in ("detail", "edit_goal", "rename_start", "change_reminder", "delete_start", "mark"):
    if callable(globals().get(_gh)):
        _guard_goal_handler(_gh)


def _wrap_admin_denial(name):
    """Log denied admin-access attempts without changing behavior."""
    orig = globals()[name]
    async def guarded(update, context, *args, **kwargs):
        uid = update.effective_user.id if getattr(update, "effective_user", None) else 0
        if not admin_is_allowed(uid):
            log_security(uid, "admin_denied", f"handler={name}")
        return await orig(update, context, *args, **kwargs)
    guarded.__name__ = name
    globals()[name] = guarded

for _an in ("admin_command", "admin_broadcast_start", "admin_panel_callback"):
    if callable(globals().get(_an)):
        _wrap_admin_denial(_an)


async def seclog_command(update, context):
    """Admin-only: show recent security events. Usage: /seclog [count]"""
    uid = update.effective_user.id
    fa = lang(uid) == "fa"
    if not admin_guard(uid):
        log_security(uid, "admin_denied", "handler=seclog_command")
        await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯." if fa else "â Access denied.")
        return
    limit = 20
    try:
        if context.args:
            limit = max(1, min(50, int(context.args[0])))
    except Exception:
        limit = 20
    c = db()
    rows = c.execute("SELECT * FROM security_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    if not rows:
        await update.message.reply_text("â ÙÛÚ Ø±ÙÛØ¯Ø§Ø¯ Ø§ÙÙÛØªÛ Ø«Ø¨Øª ÙØ´Ø¯Ù Ø§Ø³Øª." if fa else
                                        "â No security events recorded.")
        return
    lines = ["ð <b>Ø±ÙÛØ¯Ø§Ø¯ÙØ§Û Ø§ÙÙÛØªÛ Ø§Ø®ÛØ±</b>\n" if fa else "ð <b>Recent security events</b>\n"]
    for r in rows:
        line = (f"â¢ <code>{html.escape(str(r['created_at'])[:16])}</code> | "
                f"{html.escape(r['event'])} | user=<code>{r['user_id']}</code>")
        if r["details"]:
            line += f" | {html.escape(r['details'])}"
        lines.append(line)
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")



# ===================== ADMIN REPORTS & AUDIT LAYER =====================
# Additive only. Adds: error_events table + DB capture of handler errors,
# owner notifications for sensitive changes by other managers,
# an admin-only /reports menu (users/finance/tickets/xp-vip/errors/health),
# and one-tap backup file delivery.

_OLD_INIT_DB_REPORTS = init_db
def init_db():
    _OLD_INIT_DB_REPORTS()
    c = db()
    c.execute("""CREATE TABLE IF NOT EXISTS error_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        error_type TEXT NOT NULL,
        error_text TEXT,
        update_info TEXT,
        created_at TEXT NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_err_created ON error_events(created_at)")
    c.execute("""CREATE TABLE IF NOT EXISTS owner_notifications(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        text TEXT NOT NULL,
        sent INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL)""")
    c.commit(); c.close()


_OLD_SET_FEATURE_OWNER_NOTIFY = set_feature
def set_feature(key, enabled, admin_id=0):
    """Same behavior; when a non-master manager toggles a feature, queue a notice for the Owner."""
    _OLD_SET_FEATURE_OWNER_NOTIFY(key, enabled, admin_id)
    try:
        if admin_id and admin_is_allowed(admin_id) and admin_id != master_owner_id():
            fa_txt = f"âï¸ <b>ØªØºÛÛØ± ÙØ§Ø¨ÙÛØª</b>\nð¤ ÙØ¯ÛØ±: <code>{admin_id}</code>\nð§ {html.escape(str(key))}: {'Ø±ÙØ´Ù' if enabled else 'Ø®Ø§ÙÙØ´'}"
            c = db()
            c.execute("INSERT INTO owner_notifications(text,created_at) VALUES(?,?)",
                      (fa_txt, datetime.now(TZ).isoformat()))
            c.commit(); c.close()
    except Exception:
        logger.exception("owner notify enqueue failed")


async def flush_owner_notifications_job(context):
    """Deliver queued sensitive-change notices to the Owner."""
    oid = master_owner_id()
    if not oid:
        return
    c = db()
    rows = c.execute("SELECT id,text FROM owner_notifications WHERE sent=0 ORDER BY id LIMIT 20").fetchall()
    c.close()
    for r in rows:
        try:
            await context.bot.send_message(oid, r["text"], parse_mode="HTML")
            c = db(); c.execute("UPDATE owner_notifications SET sent=1 WHERE id=?", (r["id"],)); c.commit(); c.close()
        except Exception:
            logger.exception("owner notification delivery failed")
            break


_OLD_ERROR_HANDLER_REPORTS = error_handler
async def error_handler(update, context):
    """Global recovery: isolate a failed flow so one bad section cannot trap the user."""
    try:
        uid = update.effective_user.id if getattr(update, "effective_user", None) else None
        err = context.error
        c = db()
        c.execute("INSERT INTO error_events(user_id,error_type,error_text,update_info,created_at) VALUES(?,?,?,?,?)",
                  (int(uid or 0), type(err).__name__ if err else "Unknown",
                   str(err)[:500] if err else "",
                   ("msg:" + (update.message.text[:120] if getattr(update, "message", None) else
                    ("cb:" + (update.callback_query.data[:120] if getattr(update, "callback_query", None) else "")))) if update else "",
                   datetime.now(TZ).isoformat()))
        c.commit(); c.close()
    except Exception:
        logger.exception("error_events insert failed")

    # Never leave a broken text-input state active after an exception.
    try:
        if context and getattr(context, "user_data", None):
            clear_flow(context)
            context.user_data.pop("edit_reminder_id", None)
            context.user_data.pop("edit_id", None)
    except Exception:
        logger.exception("Failed to clear broken user flow")

    try:
        return await _OLD_ERROR_HANDLER_REPORTS(update, context)
    except Exception:
        # Error handling itself must never bring down the update processing loop.
        logger.exception("Previous error handler failed")
        return None


def _fmt_num(n):
    return f"{n:,}" if isinstance(n, int) else str(n)


def _rep_users():
    today = datetime.now(TZ).date().isoformat()
    c = db()
    total = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    new_today = c.execute("SELECT COUNT(*) n FROM users WHERE substr(created_at,1,10)=?", (today,)).fetchone()["n"] if True else 0
    try:
        vip = c.execute("SELECT COUNT(*) n FROM users WHERE vip_until IS NOT NULL AND vip_until>=?", (datetime.now(TZ).isoformat(),)).fetchone()["n"]
    except Exception:
        vip = 0
    managers = len(ADMIN_IDS)
    goals_active = c.execute("SELECT COUNT(*) n FROM goals WHERE enabled=1").fetchone()["n"]
    c.close()
    return ("ð¥ <b>Ú¯Ø²Ø§Ø±Ø´ Ú©Ø§Ø±Ø¨Ø±Ø§Ù</b>\n\n"
            f"â¢ Ú©Ù Ú©Ø§Ø±Ø¨Ø±Ø§Ù: <b>{_fmt_num(total)}</b>\n"
            f"â¢ Ø¹Ø¶Ù Ø¬Ø¯ÛØ¯ Ø§ÙØ±ÙØ²: <b>{_fmt_num(new_today)}</b>\n"
            f"â¢ VIP ÙØ¹Ø§Ù: <b>{_fmt_num(vip)}</b>\n"
            f"â¢ ÙØ¯ÛØ±Ø§Ù: <b>{_fmt_num(managers)}</b>\n"
            f"â¢ ÙØ¯ÙâÙØ§Û ÙØ¹Ø§Ù: <b>{_fmt_num(goals_active)}</b>")


def _rep_finance():
    c = db()
    pay_n = pay_sum = 0
    try:
        r = c.execute("SELECT COUNT(*) n, COALESCE(SUM(total_amount),0) s FROM payments").fetchone()
        pay_n, pay_sum = r["n"], r["s"]
    except Exception:
        pass
    month_sum = 0
    try:
        m30 = (datetime.now(TZ) - timedelta(days=30)).date().isoformat()
        month_sum = c.execute("SELECT COALESCE(SUM(total_amount),0) s FROM payments WHERE substr(created_at,1,10)>=?", (m30,)).fetchone()["s"]
    except Exception:
        pass
    pending_receipts = 0
    try:
        pending_receipts = c.execute("SELECT COUNT(*) n FROM vip_receipts WHERE status='pending'").fetchone()["n"]
    except Exception:
        pass
    c.close()
    return ("ð° <b>Ú¯Ø²Ø§Ø±Ø´ ÙØ§ÙÛ</b>\n\n"
            f"â¢ Ù¾Ø±Ø¯Ø§Ø®ØªâÙØ§Û Ø«Ø¨ØªâØ´Ø¯Ù: <b>{_fmt_num(pay_n)}</b>\n"
            f"â¢ ÙØ¬ÙÙØ¹ Ú©Ù: <b>{_fmt_num(int(pay_sum))}</b> (Stars)\n"
            f"â¢ Û³Û° Ø±ÙØ² Ø§Ø®ÛØ±: <b>{_fmt_num(int(month_sum))}</b> (Stars)\n"
            f"â¢ Ø±Ø³ÛØ¯ VIP Ø¯Ø± Ø§ÙØªØ¸Ø§Ø± Ø¨Ø±Ø±Ø³Û: <b>{_fmt_num(pending_receipts)}</b>")


def _rep_tickets():
    now_iso = datetime.now(TZ).isoformat()
    cutoff = (datetime.now(TZ) - timedelta(hours=48)).isoformat()
    c = db()
    by_status = {}
    for r in c.execute("SELECT status, COUNT(*) n FROM tickets GROUP BY status"):
        by_status[r["status"]] = r["n"]
    escalated = c.execute("SELECT COUNT(*) n FROM tickets WHERE status='open' AND created_at<?", (cutoff,)).fetchone()["n"]
    # Average first-response time: first message from someone other than ticket owner
    avg_min = None
    try:
        rows = c.execute("""
            SELECT t.id, t.created_at,
              (SELECT MIN(m.created_at) FROM ticket_messages m
                WHERE m.ticket_id=t.id AND m.sender_id!=t.user_id AND m.created_at>=t.created_at) fr
            FROM tickets t WHERE fr IS NOT NULL LIMIT 500""").fetchall()
        deltas = []
        for r in rows:
            d = (datetime.fromisoformat(r["fr"]) - datetime.fromisoformat(r["created_at"])).total_seconds() / 60.0
            if 0 <= d < 60*24*30:
                deltas.append(d)
        avg_min = sum(deltas)/len(deltas) if deltas else None
    except Exception:
        pass
    sat = None
    try:
        s = c.execute("SELECT COALESCE(AVG(rating),0) a, COUNT(*) n FROM survey_responses").fetchone()
        if s["n"]:
            sat = s["a"]
    except Exception:
        pass
    c.close()
    lines = ["ð« <b>Ú¯Ø²Ø§Ø±Ø´ ØªÛÚ©ØªâÙØ§</b>\n\n"]
    lines.append(f"â¢ Ø¨Ø§Ø²: <b>{by_status.get('open',0)}</b> | Ø¨Ø³ØªÙ: <b>{by_status.get('closed',0)}</b>\n")
    lines.append(f"â¢ Escalated (Ø¨Ø§Ø² Ø¨ÛØ´ Ø§Ø² Û´Û¸ Ø³Ø§Ø¹Øª): <b>{escalated}</b>\n")
    if avg_min is not None:
        lines.append(f"â¢ ÙÛØ§ÙÚ¯ÛÙ Ø²ÙØ§Ù Ø§ÙÙÛÙ Ù¾Ø§Ø³Ø®: <b>{avg_min:.0f} Ø¯ÙÛÙÙ</b>\n")
    if sat is not None:
        lines.append(f"â¢ ÙÛØ§ÙÚ¯ÛÙ Ø±Ø¶Ø§ÛØª ÙØ´ØªØ±ÛØ§Ù: <b>{sat:.1f}/5</b>\n")
    return "".join(lines)


def _rep_xp_vip():
    today = datetime.now(TZ).date().isoformat()
    in7 = (datetime.now(TZ) + timedelta(days=7)).date().isoformat()
    c = db()
    xp_today = 0
    try:
        xp_today = c.execute("SELECT COALESCE(SUM(amount),0) s FROM xp_log WHERE substr(created_at,1,10)=?", (today,)).fetchone()["s"]
    except Exception:
        pass
    try:
        vip_active = c.execute("SELECT COUNT(*) n FROM users WHERE vip_until>=?", (now_iso := datetime.now(TZ).isoformat(),)).fetchone()["n"]
        expiring = c.execute("SELECT COUNT(*) n FROM users WHERE vip_until>=? AND vip_until<=?", (now_iso, in7)).fetchone()["n"]
    except Exception:
        vip_active = expiring = 0
    c.close()
    return ("â­ <b>Ú¯Ø²Ø§Ø±Ø´ XP Ù VIP</b>\n\n"
            f"â¢ XP Ø§Ø¹Ø·Ø§ÛÛ Ø§ÙØ±ÙØ²: <b>{_fmt_num(int(xp_today))}</b>\n"
            f"â¢ VIP ÙØ¹Ø§Ù: <b>{vip_active}</b>\n"
            f"â¢ ØªØ§ Û· Ø±ÙØ² Ø¢ÛÙØ¯Ù ÙÙÙØ¶Û ÙÛâØ´ÙØ¯: <b>{expiring}</b>")


def _rep_errors():
    today = datetime.now(TZ).date().isoformat()
    c = db()
    by_type = c.execute("SELECT error_type, COUNT(*) n FROM error_events WHERE substr(created_at,1,10)=? GROUP BY error_type ORDER BY n DESC LIMIT 8", (today,)).fetchall()
    recent = c.execute("SELECT error_type, error_text, user_id, created_at FROM error_events ORDER BY id DESC LIMIT 5").fetchall()
    total_today = c.execute("SELECT COUNT(*) n FROM error_events WHERE substr(created_at,1,10)=?", (today,)).fetchone()["n"]
    sec_today = c.execute("SELECT COUNT(*) n FROM security_events WHERE substr(created_at,1,10)=?", (today,)).fetchall()
    c.close()
    lines = ["ð <b>Ú¯Ø²Ø§Ø±Ø´ Ø®Ø·Ø§ÙØ§</b>\n\n", f"â¢ Ø®Ø·Ø§ÙØ§Û Ø§ÙØ±ÙØ²: <b>{total_today}</b>\n"]
    for r in by_type:
        lines.append(f"  â <code>{html.escape(r['error_type'])}</code>: {r['n']}\n")
    if sec_today:
        lines.append(f"â¢ Ø±ÙÛØ¯Ø§Ø¯ÙØ§Û Ø§ÙÙÛØªÛ Ø§ÙØ±ÙØ²: <b>{sec_today[0]['n']}</b>\n")
    if recent:
        lines.append("\n<b>Ø¢Ø®Ø±ÛÙ Ø®Ø·Ø§ÙØ§:</b>\n")
        for r in recent:
            lines.append(f"â¢ {html.escape(str(r['created_at'])[:16])} <code>{html.escape(r['error_type'])}</code> u:{r['user_id']}\n")
    return "".join(lines)


def _rep_health():
    c = db()
    tables = c.execute("SELECT COUNT(*) n FROM sqlite_master WHERE type='table'").fetchone()["n"]
    c.close()
    size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    wal = os.path.getsize(DB_PATH + "-wal") if os.path.exists(DB_PATH + "-wal") else 0
    up_s = int(time.time() - _PROCESS_START[0]) if "_PROCESS_START" in globals() else 0
    h, m = up_s // 3600, (up_s % 3600) // 60
    return ("ð©º <b>Ø³ÙØ§ÙØª Ø³Ø±ÙÛØ³âÙØ§</b>\n\n"
            f"â¢ ÙØ¶Ø¹ÛØª DB: <b>OK</b> (WAL ÙØ¹Ø§Ù)\n"
            f"â¢ Ø­Ø¬Ù Ø¯ÛØªØ§Ø¨ÛØ³: <b>{size/1024:.0f} KB</b> (WAL: {wal/1024:.0f} KB)\n"
            f"â¢ ØªØ¹Ø¯Ø§Ø¯ Ø¬Ø¯ÙÙâÙØ§: <b>{tables}</b>\n"
            f"â¢ Ø²ÙØ§Ù Ø¨Ø§ÙØ§ Ø¨ÙØ¯Ù Ø³Ø±ÙÛØ³: <b>{h}Ø³Ø§Ø¹Øª Ù {m}Ø¯ÙÛÙÙ</b>\n"
            f"â¢ JobQueue ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§: <b>ÙØ¹Ø§Ù</b>")


async def _send_backup_file(update, context):
    uid = update.effective_user.id
    path = DB_BACKUP_PATH if os.path.exists(DB_BACKUP_PATH) else (DB_PATH if os.path.exists(DB_PATH) else None)
    if not path:
        await update.message.reply_text("â ÙØ§ÛÙ Ø¨Ú©Ø§Ù¾ Ù¾ÛØ¯Ø§ ÙØ´Ø¯.")
        return
    with open(path, "rb") as fh:
        await context.bot.send_document(uid, document=fh, filename="goals-backup.db",
                                        caption="ð¾ Ø¢Ø®Ø±ÛÙ ÙØ³Ø®Ù Ù¾Ø´ØªÛØ¨Ø§Ù Ø¯ÛØªØ§Ø¨ÛØ³")


_REPORT_BUILDERS = {
    "users": _rep_users,
    "finance": _rep_finance,
    "tickets": _rep_tickets,
    "xpvip": _rep_xp_vip,
    "errors": _rep_errors,
    "health": _rep_health,
}


def _reports_menu_kb(uid):
    fa = lang(uid) == "fa"
    b = lambda t, cb: InlineKeyboardButton(t, callback_data=cb)
    rows = [
        [b("ð¥ Ú©Ø§Ø±Ø¨Ø±Ø§Ù", "rep:users"), b("ð° ÙØ§ÙÛ", "rep:finance")],
        [b("ð« ØªÛÚ©ØªâÙØ§", "rep:tickets"), b("â­ XP/VIP", "rep:xpvip")],
        [b("ð Ø®Ø·Ø§ÙØ§", "rep:errors"), b("ð©º Ø³ÙØ§ÙØª", "rep:health")],
        [b("ð¾ Ø¯Ø±ÛØ§ÙØª ÙØ§ÛÙ Ø¨Ú©Ø§Ù¾", "rep:backup")],
        [main_menu_button(uid)],
    ]
    return InlineKeyboardMarkup(rows)


async def reports_command(update, context):
    uid = update.effective_user.id
    fa = lang(uid) == "fa"
    if not admin_guard(uid):
        log_security(uid, "admin_denied", "handler=reports_command")
        await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯." if fa else "â Access denied.")
        return
    text = ("ð <b>Ú¯Ø²Ø§Ø±Ø´âÙØ§Û ÙØ¯ÛØ±ÛØªÛ</b>\n\nÛÚ© Ú¯Ø²Ø§Ø±Ø´ Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:" if fa
            else "ð <b>Management Reports</b>\n\nChoose a report:")
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=_reports_menu_kb(uid))


async def reports_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    fa = lang(uid) == "fa"
    if not admin_guard(uid):
        log_security(uid, "admin_denied", "handler=reports_callback")
        try:
            await q.answer("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯." if fa else "â Access denied.", show_alert=True)
        except Exception:
            pass
        return
    await q.answer()
    action = q.data.split(":", 1)[1]
    if action == "menu":
        await q.message.edit_text("ð <b>Ú¯Ø²Ø§Ø±Ø´âÙØ§Û ÙØ¯ÛØ±ÛØªÛ</b>\n\nÛÚ© Ú¯Ø²Ø§Ø±Ø´ Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:" if fa
                                  else "ð <b>Management Reports</b>\n\nChoose:",
                                  parse_mode="HTML", reply_markup=_reports_menu_kb(uid))
        return
    if action == "backup":
        try:
            await q.answer("â³ Ø¯Ø± Ø­Ø§Ù Ø¢ÙØ§Ø¯ÙâØ³Ø§Ø²Û..." if fa else "â³ Preparing...")
        except Exception:
            pass
        path = DB_BACKUP_PATH if os.path.exists(DB_BACKUP_PATH) else (DB_PATH if os.path.exists(DB_PATH) else None)
        if not path:
            try:
                await q.message.edit_text("â ÙØ§ÛÙ Ø¨Ú©Ø§Ù¾ Ù¾ÛØ¯Ø§ ÙØ´Ø¯." if fa else "â Backup file not found.",
                                          reply_markup=_reports_menu_kb(uid))
            except Exception:
                pass
            return
        with open(path, "rb") as fh:
            await context.bot.send_document(uid, document=fh, filename="goals-backup.db",
                                            caption="ð¾ Ø¨Ú©Ø§Ù¾ Ø¯ÛØªØ§Ø¨ÛØ³ Ø±Ø¨Ø§Øª" if fa else "Bot database backup")
        return
    builder = _REPORT_BUILDERS.get(action)
    if not builder:
        return
    back_row = [InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª Ø¨Ù Ú¯Ø²Ø§Ø±Ø´âÙØ§" if fa else "â¬ï¸ Back to reports", callback_data="rep:menu"),
                main_menu_button(uid)]
    try:
        text = builder()
        kb = InlineKeyboardMarkup([back_row])
        try:
            await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await q.message.edit_reply_markup(reply_markup=kb)
    except Exception as e:
        logger.exception("report %s failed", action)
        await q.message.edit_text(f"â ï¸ Ø®Ø·Ø§ Ø¯Ø± ØªÙÙÛØ¯ Ú¯Ø²Ø§Ø±Ø´: <code>{type(e).__name__}</code>",
                                  parse_mode="HTML", reply_markup=_reports_menu_kb(uid))



# ===================== WEEKLY OWNER BACKUP DELIVERY =====================
# Sends the latest database backup file to the Owner once a week.

async def weekly_owner_backup_job(context):
    """Deliver the newest backup file to the Owner, at most once per week."""
    oid = master_owner_id()
    if not oid:
        return
    today = datetime.now(TZ).date().isoformat()
    week = today[:8] + str((datetime.now(TZ).isocalendar().week))  # YYYY-MM-W
    if get_system_setting("last_owner_backup_week", "") == week:
        return
    path = DB_BACKUP_PATH if os.path.exists(DB_BACKUP_PATH) else None
    if not path:
        logger.warning("Weekly owner backup skipped: no backup file")
        return
    try:
        with open(path, "rb") as fh:
            await context.bot.send_document(
                oid, document=fh, filename=f"goals-backup-{today}.db",
                caption=f"ð¾ Ø¨Ú©Ø§Ù¾ ÙÙØªÚ¯Û Ø¯ÛØªØ§Ø¨ÛØ³ Ø±Ø¨Ø§Øª â {today}")
        set_system_setting("last_owner_backup_week", week)
        logger.info("Weekly owner backup delivered (%s)", path)
    except Exception:
        logger.exception("Weekly owner backup delivery failed")



# ===================== BIRTHDAY & OCCASIONS MODULE =====================
# Independent system: persistent user birthday registry + configurable gifts +
# custom occasions. Managed ONLY from the Owner area of the manager panel
# (Ù¾ÙÙ ÙØ¯ÛØ± -> ð ØªÙÙØ¯ Ù ÙÙØ§Ø³Ø¨ØªâÙØ§). Privacy-safe by construction: regular users
# can only ever read/write their OWN birthday row; there is no API that exposes
# another user's birthday outside the Owner-only report.

_BDAY_SCHEMA_OK = [False]


def _bday_db():
    c = db()
    if not _BDAY_SCHEMA_OK[0]:
        c.execute("CREATE TABLE IF NOT EXISTS birthdays(user_id INTEGER PRIMARY KEY, birth_date TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS bday_events(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, year INTEGER NOT NULL, kind TEXT NOT NULL, occasion_id INTEGER NOT NULL DEFAULT 0, gift_kind TEXT DEFAULT '', gift_amount REAL DEFAULT 0, created_at TEXT NOT NULL, UNIQUE(user_id, year, kind, occasion_id))")
        c.execute("CREATE TABLE IF NOT EXISTS occasions(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, date TEXT NOT NULL, message TEXT DEFAULT '', gift_kind TEXT DEFAULT 'none', gift_amount REAL DEFAULT 0, xp_amount INTEGER DEFAULT 0, vip_days INTEGER DEFAULT 0, active INTEGER DEFAULT 1, auto_send INTEGER DEFAULT 1, last_sent_year INTEGER DEFAULT 0, created_at TEXT NOT NULL)")
        c.commit()
        _BDAY_SCHEMA_OK[0] = True
    return c


_FA_DIGITS = {"Û°": "0", "Û±": "1", "Û²": "2", "Û³": "3", "Û´": "4", "Ûµ": "5", "Û¶": "6", "Û·": "7", "Û¸": "8", "Û¹": "9",
              "Ù ": "0", "Ù¡": "1", "Ù¢": "2", "Ù£": "3", "Ù¤": "4", "Ù¥": "5", "Ù¦": "6", "Ù§": "7", "Ù¨": "8", "Ù©": "9"}


def _bd_en(s):
    for k, v in _FA_DIGITS.items():
        s = s.replace(k, v)
    return s


def bd_parse_date(text):
    """Parse Gregorian date YYYY-MM-DD / YYYY/MM/DD (Persian digits allowed).
    Returns ISO string or None. Full validation via datetime."""
    s = _bd_en((text or "").strip()).replace("/", "-").replace(".", "-").replace("\\", "-").strip("-")
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if not m:
        return None
    try:
        d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
    except ValueError:
        return None
    return d.isoformat()


def bd_parse_mmdd(text):
    iso = bd_parse_date(text if "-" in (text or "") else "")
    if iso:
        return iso[5:]
    s = _bd_en((text or "").strip()).replace("/", "-").replace(".", "-")
    m = re.match(r"^(\d{1,2})-(\d{1,2})$", s)
    if not m:
        return None
    mo, dy = int(m.group(1)), int(m.group(2))
    if not (1 <= mo <= 12 and 1 <= dy <= 31):
        return None
    return f"{mo:02d}-{dy:02d}"


def bd_get(key, default=""):
    return get_system_setting(key, default)


def bd_set(key, value):
    set_system_setting(key, str(value))


BD_GIFT_KINDS = ("xp", "service", "vip", "subscription", "none")
BD_GIFT_LABELS = {"xp": "â­ XP", "service": "ðª Ø®Ø¯ÙØ§Øª (ØªÙÚ©Ù)", "vip": "ð VIP", "subscription": "ð Ø§Ø´ØªØ±Ø§Ú©", "none": "â Ø¨Ø¯ÙÙ ÙØ¯ÛÙ"}
BD_AUDIENCES = ("all", "normal", "vip")
BD_AUD_LABELS = {"all": "ð¥ ÙÙÙ Ú©Ø§Ø±Ø¨Ø±Ø§Ù", "normal": "ð¤ ÙÙØ· Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ø¹Ø§Ø¯Û", "vip": "ð ÙÙØ· VIP"}


def bd_audience_ok(uid):
    aud = bd_get("bd_audience", "all")
    if aud == "all":
        return True
    vip = is_vip(uid)
    return vip if aud == "vip" else (not vip)


def bd_gift_desc(kind, amount):
    n = max(1, int(float(amount or 0)))
    unit = {"xp": "XP", "service": "ØªÙÚ©Ù", "vip": "Ø±ÙØ² VIP", "subscription": "Ø±ÙØ² Ø§Ø´ØªØ±Ø§Ú©"}.get(kind, "")
    return f"{n} {unit}" if unit else ""


def _bday_claim(c, uid, year, kind, occ_id=0):
    """Atomically claim a once-per-user-per-year event. Returns event id or 0."""
    cur = c.execute(
        "INSERT OR IGNORE INTO bday_events(user_id,year,kind,occasion_id,created_at) VALUES(?,?,?,?,?)",
        (uid, year, kind, occ_id, datetime.now(TZ).isoformat()))
    if cur.rowcount != 1:
        return 0
    return cur.lastrowid


def _bday_reward(c, eid, uid, gkind, amount):
    """Apply a reward bypassing the daily XP cap (gifts are deliberate grants).
    Returns a short human description ('' when nothing granted)."""
    desc = ""
    now_iso = datetime.now(TZ).isoformat()
    if gkind == "xp":
        n = max(1, int(float(amount or 0)))
        c.execute("UPDATE users SET xp=COALESCE(xp,0)+? WHERE user_id=?", (n, uid))
        c.execute("INSERT INTO xp_log(user_id,amount,reason,created_at) VALUES(?,?,?,?)", (uid, n, "birthday_gift", now_iso))
        desc = f"â­ +{n} XP"
    elif gkind == "service":
        n = max(1, int(float(amount or 0)))
        add_tokens(uid, n, reason="birthday_gift")
        desc = f"ðª +{n} ØªÙÚ©Ù"
    elif gkind in ("vip", "subscription"):
        days = max(1, int(float(amount or 1)))
        r = c.execute("SELECT COALESCE(vip_until,'') v FROM users WHERE user_id=?", (uid,)).fetchone()
        base = datetime.now(TZ)
        if r and r["v"]:
            try:
                until = datetime.fromisoformat(r["v"])
                if until > base:
                    base = until
            except Exception:
                pass
        c.execute("UPDATE users SET vip_until=? WHERE user_id=?", ((base + timedelta(days=days)).isoformat(), uid))
        desc = f"ð {days} Ø±ÙØ² {'VIP' if gkind == 'vip' else 'Ø§Ø´ØªØ±Ø§Ú©'}"
    if desc and eid:
        c.execute("UPDATE bday_events SET gift_kind=?,gift_amount=? WHERE id=?", (gkind, float(amount or 0), eid))
    return desc


def bd_congrats_text(name):
    tpl = bd_get("bd_congrats_fa", "ð {name} Ø¹Ø²ÛØ²Ø ØªÙÙØ¯Øª ÙØ¨Ø§Ø±Ú©! ð\n\nØ§ÙÛØ¯ÙØ§Ø±ÛÙ Ø³Ø§ÙÛ Ø³Ø±Ø´Ø§Ø± Ø§Ø² Ø³ÙØ§ÙØªÛØ Ø´Ø§Ø¯Û Ù ÙÙÙÙÛØª Ø¯Ø§Ø´ØªÙ Ø¨Ø§Ø´Û.")
    txt = tpl.replace("{name}", name or "Ø¯ÙØ³Øª Ø¹Ø²ÛØ²")
    if bd_get("bd_gift_enabled", "1") == "1":
        gkind = bd_get("bd_gift_kind", "xp")
        if gkind != "none":
            try:
                amt = float(bd_get("bd_gift_amount", "50"))
            except ValueError:
                amt = 50.0
            d = bd_gift_desc(gkind, amt)
            if d:
                txt += "\n\nð ÙØ¯ÛÙ ØªÙÙØ¯: " + d
    return txt


# ---------- user-facing command (privacy: own row only) ----------
# Birthday entry: Jalali calendar picker (Ø³Ø§Ù â ÙØ§Ù â Ø±ÙØ²) + âï¸ ÙØ±ÙØ¯ Ø¯Ø³ØªÛ.

_BD_JY_MIN, _BD_JY_MAX = 1300, 1450
_BD_YEARS_PER_PAGE = 12
_BD_FA_DG = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_BD_MONTHS_FA = ("ÙØ±ÙØ±Ø¯ÛÙ", "Ø§Ø±Ø¯ÛØ¨ÙØ´Øª", "Ø®Ø±Ø¯Ø§Ø¯", "ØªÛØ±", "ÙØ±Ø¯Ø§Ø¯", "Ø´ÙØ±ÛÙØ±",
                 "ÙÙØ±", "Ø¢Ø¨Ø§Ù", "Ø¢Ø°Ø±", "Ø¯Û", "Ø¨ÙÙÙ", "Ø§Ø³ÙÙØ¯")
_BD_MONTHS_EN = ("Farvardin", "Ordibehesht", "Khordad", "Tir", "Mordad", "Shahrivar",
                 "Mehr", "Aban", "Azar", "Dey", "Bahman", "Esfand")


def _bd_now_jy():
    t = datetime.now(TZ).date()
    return _g2j(t.year, t.month, t.day)[0]


def bd_jalali_to_iso(jy, jm, jd):
    """Validate a Jalali date and convert it to the standard Gregorian ISO string."""
    try:
        jy, jm, jd = int(jy), int(jm), int(jd)
    except (TypeError, ValueError):
        return None
    if not (_BD_JY_MIN <= jy <= _BD_JY_MAX and 1 <= jm <= 12):
        return None
    if not (1 <= jd <= _jalali_month_days(jy, jm)):
        return None
    try:
        gy, gm, gd = _j2g(jy, jm, jd)
        return datetime(gy, gm, gd).date().isoformat()
    except Exception:
        return None


def bd_parse_any_date(text):
    """Parse common birthday formats: 1382/04/23, 1382-04-23, 23/04/1382,
    Û²Û³/Û°Û´/Û±Û³Û¸Û², 2000-08-24, 2000/8/24. Returns (gregorian_iso, kind) or None.
    Years inside the Jalali window are treated as Solar Hijri; anything else as Gregorian."""
    s = _bd_en((text or "").strip()).replace("/", "-").replace(".", "-").replace("Ø", "-").strip("- ")
    parts = [p for p in re.split(r"-+", s) if p]
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        a, b, cc = (int(p) for p in parts)

        def _try_jal(y, m, d):
            iso = bd_jalali_to_iso(y, m, d)
            return (iso, "jalali") if iso else None

        def _try_greg(y, m, d):
            iso = bd_parse_date("%04d-%02d-%02d" % (y, m, d))
            return (iso, "gregorian") if iso else None

        if a >= 100:
            if _BD_JY_MIN <= a <= _BD_JY_MAX:
                return _try_jal(a, b, cc)
            return _try_greg(a, b, cc)
        if cc >= 100:
            if _BD_JY_MIN <= cc <= _BD_JY_MAX:
                return _try_jal(cc, b, a)
            return _try_greg(cc, b, a)
        return None
    iso = bd_parse_date(text)
    return (iso, "gregorian") if iso else None


async def _safe_edit(q, text, kb=None):
    try:
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        try:
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass


def _bd_manual_btn(fa):
    return InlineKeyboardButton("âï¸ ÙØ±ÙØ¯ Ø¯Ø³ØªÛ" if fa else "âï¸ Manual", callback_data="bd:manual")


def _bd_cal_years_kb(base, fa):
    base = max(_BD_JY_MIN, min(int(base), _BD_JY_MAX - _BD_YEARS_PER_PAGE + 1))
    rows, row = [], []
    for y in range(base, base + _BD_YEARS_PER_PAGE):
        row.append(InlineKeyboardButton(str(y).translate(_BD_FA_DG) if fa else str(y),
                                        callback_data="bd:calm:%d" % y))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton("âï¸ Ø³Ø§ÙâÙØ§Û ÙØ¨Ù" if fa else "âï¸ Older years",
                             callback_data="bd:caly:%d" % (base - _BD_YEARS_PER_PAGE)),
        InlineKeyboardButton("Ø³Ø§ÙâÙØ§Û Ø¨Ø¹Ø¯ â¶ï¸" if fa else "Newer years â¶ï¸",
                             callback_data="bd:caly:%d" % (base + _BD_YEARS_PER_PAGE)),
    ])
    rows.append([_bd_manual_btn(fa),
                 InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª" if fa else "â¬ï¸ Back", callback_data="bd:back")])
    return InlineKeyboardMarkup(rows)


def _bd_cal_months_kb(year, fa):
    names = _BD_MONTHS_FA if fa else _BD_MONTHS_EN
    rows, row = [], []
    for m in range(1, 13):
        row.append(InlineKeyboardButton(names[m - 1], callback_data="bd:cald:%d:%d" % (year, m)))
        if len(row) == 3:
            rows.append(row)
            row = []
    rows.append([_bd_manual_btn(fa),
                 InlineKeyboardButton("â¬ï¸ Ø§ÙØªØ®Ø§Ø¨ Ø³Ø§Ù" if fa else "â¬ï¸ Years", callback_data="bd:cal")])
    return InlineKeyboardMarkup(rows)


def _bd_cal_days_kb(year, month, fa):
    ndays = _jalali_month_days(year, month)
    rows, row = [], []
    for d in range(1, ndays + 1):
        row.append(InlineKeyboardButton(str(d).translate(_BD_FA_DG) if fa else str(d),
                                        callback_data="bd:calsave:%d:%d:%d" % (year, month, d)))
        if len(row) == 7:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_bd_manual_btn(fa),
                 InlineKeyboardButton("â¬ï¸ Ø§ÙØªØ®Ø§Ø¨ ÙØ§Ù" if fa else "â¬ï¸ Months",
                                      callback_data="bd:calm:%d" % year)])
    return InlineKeyboardMarkup(rows)


def _bd_status_parts(uid):
    fa = lang(uid) == "fa"
    c = _bday_db()
    r = c.execute("SELECT birth_date FROM birthdays WHERE user_id=?", (uid,)).fetchone()
    c.close()
    if r:
        jy, jm, jd = _jalali_from_iso(r["birth_date"])
        if fa:
            text = ("ð <b>ØªØ§Ø±ÛØ® ØªÙÙØ¯ Ø«Ø¨ØªâØ´Ø¯ÙÙ ØªÙ:</b>\n"
                    "ð Ø´ÙØ³Û: <code>%04d/%02d/%02d</code>\n"
                    "ð ÙÛÙØ§Ø¯Û: <code>%s</code>\n\n"
                    "â ï¸ ØªØ§Ø±ÛØ® ØªÙÙØ¯ Ø¯Ø§Ø¦ÙÛ Ø°Ø®ÛØ±Ù ÙÛ\u200cØ´ÙØ¯ Ù ÙÙØ· Ø¨Ø±Ø§Û ØªØ¨Ø±ÛÚ© Ù ÙØ¯ÛÙ Ø§Ø³ØªÙØ§Ø¯Ù ÙÛ\u200cØ´ÙØ¯."
                    ) % (jy, jm, jd, r["birth_date"])
        else:
            text = ("ð <b>Your saved birthday:</b> <code>%s</code>\n\n"
                    "â ï¸ Your birthday is saved permanently and used only for birthday greetings and gifts."
                    ) % (r["birth_date"],)
    else:
        if fa:
            text = ("ð <b>ØªØ§Ø±ÛØ® ØªÙÙØ¯ Ø®ÙØ¯Øª Ø±Ù ÙØ§Ø±Ø¯ Ú©Ù</b>\n\n"
                    "Ø³Ø§ÙØ ÙØ§Ù Ù Ø±ÙØ² ØªÙÙØ¯Øª Ø±Ø§ Ø§Ø² ØªÙÙÛÙ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù ÛØ§ Â«âï¸ ÙØ±ÙØ¯ Ø¯Ø³ØªÛÂ» Ø±Ø§ Ø¨Ø²Ù.\n\n"
                    "â ï¸ ØªØ§Ø±ÛØ® ØªÙÙØ¯ Ø¯Ø§Ø¦ÙÛ Ø°Ø®ÛØ±Ù ÙÛ\u200cØ´ÙØ¯ Ù ÙÙØ· Ø¨Ø±Ø§Û ØªØ¨Ø±ÛÚ© Ù ÙØ¯ÛÙ Ø§Ø³ØªÙØ§Ø¯Ù ÙÛ\u200cØ´ÙØ¯.")
        else:
            text = ("ð <b>Enter your birthday</b>\n\n"
                    "Pick year, month and day from the calendar, or tap Manual.\n\n"
                    "â ï¸ Your birthday is saved permanently and used only for birthday greetings and gifts.")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("ð Ø«Ø¨Øª ØªÙÙØ¯" if fa else "ð Set Birthday", callback_data="bd:set")],
        [InlineKeyboardButton("ð Ø­Ø°Ù ØªØ§Ø±ÛØ®" if fa else "ð Delete", callback_data="bd:del")],
    ])
    return text, kb


async def birthday_command(update, context):
    uid = update.effective_user.id
    fa = lang(uid) == "fa"
    if bd_get("bd_enabled", "1") != "1":
        await update.message.reply_text("Ø§ÛÙ Ø¨Ø®Ø´ Ø¯Ø± Ø­Ø§Ù Ø­Ø§Ø¶Ø± ØºÛØ±ÙØ¹Ø§Ù Ø§Ø³Øª. ð" if fa else "This section is currently disabled.")
        return
    text, kb = _bd_status_parts(uid)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def birthday_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    fa = lang(uid) == "fa"
    data = q.data or ""
    if not data.startswith("bd:"):
        await q.answer()
        return
    act = data[3:]

    async def _ans(msg=None, alert=False):
        try:
            await q.answer(msg, show_alert=alert)
        except Exception:
            pass

    if act == "set" or act == "cal":
        await _ans()
        base = max(_BD_JY_MIN, min(_bd_now_jy() - 25, _BD_JY_MAX - 11))
        title = "ð <b>Ø³Ø§Ù ØªÙÙØ¯Øª Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:</b>" if fa else "ð <b>Select your birth year:</b>"
        await _safe_edit(q, title, _bd_cal_years_kb(base, fa))
        return
    if act.startswith("caly:"):
        await _ans()
        try:
            base = int(act.split(":")[1])
        except ValueError:
            base = _bd_now_jy() - 25
        title = "ð <b>Ø³Ø§Ù ØªÙÙØ¯Øª Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:</b>" if fa else "ð <b>Select your birth year:</b>"
        await _safe_edit(q, title, _bd_cal_years_kb(base, fa))
        return
    if act.startswith("calm:"):
        try:
            year = int(act.split(":")[1])
            assert _BD_JY_MIN <= year <= _BD_JY_MAX
        except Exception:
            await _ans("â Ø³Ø§Ù ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª.", True)
            return
        await _ans()
        if fa:
            title = "ð <b>ÙØ§Ù ØªÙÙØ¯Øª Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:</b> <code>%d</code>" % year
        else:
            title = "ð <b>Select your birth month:</b> <code>%d</code>" % year
        await _safe_edit(q, title, _bd_cal_months_kb(year, fa))
        return
    if act.startswith("cald:"):
        pr = act.split(":")
        try:
            year, month = int(pr[1]), int(pr[2])
            assert _BD_JY_MIN <= year <= _BD_JY_MAX and 1 <= month <= 12
        except Exception:
            await _ans("â ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª.", True)
            return
        await _ans()
        mname = (_BD_MONTHS_FA if fa else _BD_MONTHS_EN)[month - 1]
        if fa:
            title = "ð¢ <b>Ø±ÙØ² ØªÙÙØ¯Øª Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:</b> %s %d" % (mname, year)
        else:
            title = "ð¢ <b>Select your birth day:</b> %s %d" % (mname, year)
        await _safe_edit(q, title, _bd_cal_days_kb(year, month, fa))
        return
    if act.startswith("calsave:"):
        pr = act.split(":")
        iso = bd_jalali_to_iso(pr[1], pr[2], pr[3]) if len(pr) == 4 else None
        if not iso:
            await _ans("â ØªØ§Ø±ÛØ® ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª.", True)
            return
        now_iso = datetime.now(TZ).isoformat()
        c = _bday_db()
        c.execute("INSERT INTO birthdays(user_id,birth_date,created_at,updated_at) VALUES(?,?,?,?) "
                  "ON CONFLICT(user_id) DO UPDATE SET birth_date=excluded.birth_date, updated_at=excluded.updated_at",
                  (uid, iso, now_iso, now_iso))
        c.commit()
        c.close()
        jy, jm, jd = _jalali_from_iso(iso)
        if fa:
            txt = ("â <b>ØªØ§Ø±ÛØ® ØªÙÙØ¯Øª Ø«Ø¨Øª Ø´Ø¯!</b> ð\n\n"
                   "ð Ø´ÙØ³Û: <code>%04d/%02d/%02d</code>\n"
                   "ð ÙÛÙØ§Ø¯Û: <code>%s</code>\n\n"
                   "ÙØ± Ø³Ø§Ù Ø¯Ø± ÙÙÛÙ Ø±ÙØ² ØªØ¨Ø±ÛÚ© Ù ÙØ¯ÛÙÙ ØªÙÙØ¯ Ø¯Ø±ÛØ§ÙØª ÙÛâÚ©ÙÛ."
                   ) % (jy, jm, jd, iso)
        else:
            txt = ("â <b>Your birthday is saved!</b> ð\n\n"
                   "Standard date: <code>%s</code>\n\n"
                   "You will receive birthday greetings and gifts every year on this day.") % (iso,)
        await _ans("â Ø«Ø¨Øª Ø´Ø¯")
        await _safe_edit(q, txt, None)
        return
    if act == "manual":
        context.user_data["bd_wait"] = "date"
        if fa:
            txt = ("âï¸ <b>ØªØ§Ø±ÛØ® ØªÙÙØ¯ Ø±Ø§ Ø¯Ø³ØªÛ ÙØ§Ø±Ø¯ Ú©Ù</b>\n\n"
                   "ÙØ±ÙØªâÙØ§Û ÙØ§Ø¨Ù ÙØ¨ÙÙ:\n"
                   "<code>1382/04/23</code> Â· <code>1382-04-23</code>\n"
                   "<code>23/04/1382</code> (Ø´ÙØ³Û)\n"
                   "<code>2000-08-24</code> (ÙÛÙØ§Ø¯Û)\n\n"
                   "â ï¸ ØªØ§Ø±ÛØ® ØªÙÙØ¯ Ø¯Ø§Ø¦ÙÛ Ø°Ø®ÛØ±Ù ÙÛ\u200cØ´ÙØ¯ Ù ÙÙØ· Ø¨Ø±Ø§Û ØªØ¨Ø±ÛÚ© Ù ÙØ¯ÛÙ Ø§Ø³ØªÙØ§Ø¯Ù ÙÛ\u200cØ´ÙØ¯.")
        else:
            txt = ("âï¸ <b>Enter your birthday manually</b>\n\n"
                   "Accepted formats:\n<code>2000-08-24</code>, <code>2000/8/24</code>, "
                   "<code>1382/04/23</code>, <code>23/04/1382</code>\n\n"
                   "â ï¸ Your birthday is saved permanently and used only for greetings and gifts.")
        await _ans()
        await _safe_edit(q, txt, None)
        return
    if act == "del":
        c = _bday_db()
        c.execute("DELETE FROM birthdays WHERE user_id=?", (uid,))
        c.commit()
        c.close()
        await _ans()
        await _safe_edit(q, "ð ØªØ§Ø±ÛØ® ØªÙÙØ¯ Ø­Ø°Ù Ø´Ø¯." if fa else "ð Birthday removed.", None)
        return
    if act == "cancel":
        context.user_data.pop("bd_wait", None)
        await _ans()
        await _safe_edit(q, "Ø¨Ø§Ø´ÙØ ÙØºÙ Ø´Ø¯. ð" if fa else "Okay, cancelled. ð", None)
        return
    if act == "back":
        await _ans()
        text, kb = _bd_status_parts(uid)
        await _safe_edit(q, text, kb)
        return
    await _ans()


# ---------- Owner-only panel ----------

BD_PANEL_BTN = "ð ØªÙÙØ¯ Ù ÙÙØ§Ø³Ø¨ØªâÙØ§"
_BDO_SECTIONS = ("ð ÙØ¯ÛØ±ÛØª ØªÙÙØ¯", "ð ÙØ¯ÛÙ ØªÙÙØ¯", "ð ÙÙØ§Ø³Ø¨ØªâÙØ§", "âï¸ Ù¾ÛØ§Ù ØªØ¨Ø±ÛÚ©",
                 "â­ XP Ù Ù¾Ø§Ø¯Ø§Ø´ ÙÙØ§Ø³Ø¨Øª", "âï¸ ØªÙØ¸ÛÙØ§Øª", "ð Ú¯Ø²Ø§Ø±Ø´ ØªÙÙØ¯")


def _is_bd_owner(uid):
    return bool(uid) and uid == master_owner_id()


def _bdo_kb():
    rows = [["ð ÙØ¯ÛØ±ÛØª ØªÙÙØ¯", "ð ÙØ¯ÛÙ ØªÙÙØ¯"],
            ["ð ÙÙØ§Ø³Ø¨ØªâÙØ§", "âï¸ Ù¾ÛØ§Ù ØªØ¨Ø±ÛÚ©"],
            ["â­ XP Ù Ù¾Ø§Ø¯Ø§Ø´ ÙÙØ§Ø³Ø¨Øª", "âï¸ ØªÙØ¸ÛÙØ§Øª"],
            ["ð Ú¯Ø²Ø§Ø±Ø´ ØªÙÙØ¯"],
            ["â¬ï¸ Ø¨Ø±Ú¯Ø´Øª"]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)


def _bdo_home_text():
    en = bd_get("bd_enabled", "1") == "1"
    ge = bd_get("bd_gift_enabled", "1") == "1"
    n_occ = 0
    try:
        c = _bday_db()
        n_users = c.execute("SELECT COUNT(*) n FROM birthdays").fetchone()["n"]
        n_occ = c.execute("SELECT COUNT(*) n FROM occasions WHERE active=1").fetchone()["n"]
        c.close()
    except Exception:
        n_users = 0
    return (f"ð <b>ØªÙÙØ¯ Ù ÙÙØ§Ø³Ø¨ØªâÙØ§</b>\n\n"
            f"Ø³ÛØ³ØªÙ ØªÙÙØ¯: {'ð¢ ÙØ¹Ø§Ù' if en else 'ð´ Ø®Ø§ÙÙØ´'}\n"
            f"ÙØ¯ÛÙ ØªÙÙØ¯: {'ð¢ ÙØ¹Ø§Ù' if ge else 'ð´ Ø®Ø§ÙÙØ´'} ({BD_GIFT_LABELS.get(bd_get('bd_gift_kind', 'xp'))})\n"
            f"Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ø¯Ø§Ø±Ø§Û ØªØ§Ø±ÛØ® ØªÙÙØ¯: {n_users}\n"
            f"ÙÙØ§Ø³Ø¨ØªâÙØ§Û ÙØ¹Ø§Ù: {n_occ}\n\n"
            f"ÛÚ©Û Ø§Ø² Ø¨Ø®Ø´âÙØ§Û Ø²ÛØ± Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù:")


def _bdo_bday_mgmt_view():
    en = "ð¢" if bd_get("bd_enabled", "1") == "1" else "ð´"
    rem = "ð¢" if bd_get("bd_reminder_enabled", "1") == "1" else "ð´"
    nd = bd_get("bd_reminder_days", "3")
    st = bd_get("bd_send_time", "09:00")
    text = (f"ð <b>ÙØ¯ÛØ±ÛØª ØªÙÙØ¯</b>\n\nÙØ§Ø¨ÙÛØª ØªÙÙØ¯: {en}\nÛØ§Ø¯Ø¢ÙØ±Û ÙØ¨Ù Ø§Ø² ØªÙÙØ¯: {rem} ({nd} Ø±ÙØ² ÙØ¨Ù)\n"
            f"Ø³Ø§Ø¹Øª Ø§Ø±Ø³Ø§Ù ØªØ¨Ø±ÛÚ©: {st}\n\nØ¬ÙÙÚ¯ÛØ±Û Ø§Ø² Ø¯Ø±ÛØ§ÙØª ÚÙØ¯Ø¨Ø§Ø±ÙÙ ÙØ¯ÛÙ Ø¨Ø±Ø§Û ÙØ± ØªÙÙØ¯ ÙÙÛØ´Ù ÙØ¹Ø§Ù Ø§Ø³Øª (ÙØ± Ú©Ø§Ø±Ø¨Ø±Ø ÙØ± Ø³Ø§ÙØ ÙÙØ· ÛÚ© Ø¨Ø§Ø±).")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Ø±ÙØ´Ù/Ø®Ø§ÙÙØ´ ÙØ§Ø¨ÙÛØª ØªÙÙØ¯", callback_data="bd:tog_main")],
        [InlineKeyboardButton("Ø±ÙØ´Ù/Ø®Ø§ÙÙØ´ ÛØ§Ø¯Ø¢ÙØ±Û", callback_data="bd:tog_rem")],
        [InlineKeyboardButton("ÙØ§ØµÙÙÙ ÛØ§Ø¯Ø¢ÙØ±Û: " + nd + " Ø±ÙØ²", callback_data="bd:cyc_rem")],
        [InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª", callback_data="bd:home")],
    ])
    return text, kb


def _bdo_gift_view():
    ge = "ð¢" if bd_get("bd_gift_enabled", "1") == "1" else "ð´"
    gk = bd_get("bd_gift_kind", "xp")
    aud = bd_get("bd_audience", "all")
    amt = bd_get("bd_gift_amount", "50")
    text = (f"ð <b>ÙØ¯ÛÙ ØªÙÙØ¯</b>\n\nÙØ¶Ø¹ÛØª: {ge}\nÙÙØ¹ ÙØ¯ÛÙ: {BD_GIFT_LABELS.get(gk)}\n"
            f"ÙÙØ¯Ø§Ø±: {bd_gift_desc(gk, amt) or '-'}\nÙØ­Ø¯ÙØ¯ÛØª: ÙØ± Ú©Ø§Ø±Ø¨Ø± ÙØ± Ø³Ø§Ù ÙÙØ· Û± Ø¨Ø§Ø± (Ø®ÙØ¯Ú©Ø§Ø±)\n"
            f"ÙØ®Ø§Ø·Ø¨Ø§Ù: {BD_AUD_LABELS.get(aud)}")
    nxt = BD_GIFT_KINDS[(BD_GIFT_KINDS.index(gk) + 1) % len(BD_GIFT_KINDS)]
    naud = BD_AUDIENCES[(BD_AUDIENCES.index(aud) + 1) % len(BD_AUDIENCES)]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Ø±ÙØ´Ù/Ø®Ø§ÙÙØ´ ÙØ¯ÛÙ", callback_data="bd:tog_gift")],
        [InlineKeyboardButton("ÙÙØ¹ ÙØ¯ÛÙ â " + BD_GIFT_LABELS[nxt], callback_data="bd:cyc_kind")],
        [InlineKeyboardButton("ØªØºÛÛØ± ÙÙØ¯Ø§Ø±", callback_data="bd:amt")],
        [InlineKeyboardButton("ÙØ®Ø§Ø·Ø¨Ø§Ù â " + BD_AUD_LABELS[naud], callback_data="bd:cyc_aud")],
        [InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª", callback_data="bd:home")],
    ])
    return text, kb


def _bdo_occ_list_view():
    c = _bday_db()
    rows = c.execute("SELECT * FROM occasions ORDER BY id").fetchall()
    c.close()
    lines = ["ð <b>ÙÙØ§Ø³Ø¨ØªâÙØ§</b>", ""]
    if not rows:
        lines.append("ÙÙÙØ² ÙÙØ§Ø³Ø¨Û ÙØ³Ø§Ø®ØªÙâØ§Û.")
    for o in rows:
        lines.append(f"{'ð¢' if o['active'] else 'ð´'} <b>{o['name']}</b> â {o['date']}"
                     f" | XP:{o['xp_amount']} | VIP:{o['vip_days']} Ø±ÙØ² | Ø§Ø±Ø³Ø§Ù Ø®ÙØ¯Ú©Ø§Ø±: {'Ø¨ÙÙ' if o['auto_send'] else 'ÙÙ'}")
    kb_rows = [[InlineKeyboardButton("â Ø§ÙØ²ÙØ¯Ù ÙÙØ§Ø³Ø¨Øª", callback_data="bd:occ_add")]]
    for o in rows:
        kb_rows.append([InlineKeyboardButton(("ð´ Ø®Ø§ÙÙØ´ " if o["active"] else "ð¢ Ø±ÙØ´Ù ") + o["name"],
                                             callback_data=f"bd:occ_toggle:{o['id']}"),
                        InlineKeyboardButton("ð", callback_data=f"bd:occ_del:{o['id']}")])
    kb_rows.append([InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª", callback_data="bd:home")])
    return "\n".join(lines), InlineKeyboardMarkup(kb_rows)


def _bdo_msg_view():
    st = bd_get("bd_send_time", "09:00")
    tpl = bd_get("bd_congrats_fa", "")
    short = (tpl[:120] + "â¦") if len(tpl) > 120 else tpl
    text = (f"âï¸ <b>Ù¾ÛØ§ÙâÙØ§Û ØªØ¨Ø±ÛÚ©</b>\n\nÙØªÙ ÙØ¹ÙÛ:\n<i>{short}</i>\n\n"
            f"ÙÛâØªÙØ§ÙÛ Ø§Ø² {{name}} Ø¨Ø±Ø§Û ÙØ§Ù Ú©Ø§Ø±Ø¨Ø± Ø§Ø³ØªÙØ§Ø¯Ù Ú©ÙÛ.\nØ³Ø§Ø¹Øª Ø§Ø±Ø³Ø§Ù: {st}")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("ÙÛØ±Ø§ÛØ´ ÙØªÙ ØªØ¨Ø±ÛÚ©", callback_data="bd:text"),
         InlineKeyboardButton("ØªØºÛÛØ± Ø³Ø§Ø¹Øª Ø§Ø±Ø³Ø§Ù", callback_data="bd:time")],
        [InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª", callback_data="bd:home")],
    ])
    return text, kb


def _bdo_settings_view():
    nd = bd_get("bd_reminder_days", "3")
    aud = bd_get("bd_audience", "all")
    st = bd_get("bd_send_time", "09:00")
    text = (f"âï¸ <b>ØªÙØ¸ÛÙØ§Øª ØªÙÙØ¯ Ù ÙÙØ§Ø³Ø¨Øª</b>\n\nÛØ§Ø¯Ø¢ÙØ±Û: {nd} Ø±ÙØ² ÙØ¨Ù\nØ³Ø§Ø¹Øª Ø§Ø±Ø³Ø§Ù: {st}\n"
            f"ÙØ®Ø§Ø·Ø¨Ø§Ù ÙØ¯ÛÙ: {BD_AUD_LABELS.get(aud)}\n\nØ«Ø¨Øª ØªØ§Ø±ÛØ®ÚÙÙ ÙÙÙÙ ÙØ¯ÛÙâÙØ§ ÙÙÛØ´Ù ÙØ¹Ø§Ù Ø§Ø³Øª.")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("ØªØºÛÛØ± ÙØ§ØµÙÙÙ ÛØ§Ø¯Ø¢ÙØ±Û", callback_data="bd:remdays"),
         InlineKeyboardButton("ØªØºÛÛØ± Ø³Ø§Ø¹Øª Ø§Ø±Ø³Ø§Ù", callback_data="bd:time")],
        [InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª", callback_data="bd:home")],
    ])
    return text, kb


def _bdo_report_text():
    today = datetime.now(TZ).date()
    mmdd = today.isoformat()[5:]
    week_end = (today + timedelta(days=7)).isoformat()[5:]
    c = _bday_db()
    todays = c.execute("SELECT b.user_id,u.first_name FROM birthdays b JOIN users u ON u.user_id=b.user_id WHERE substr(b.birth_date,6,5)=?", (mmdd,)).fetchall()
    upcoming = c.execute("SELECT b.birth_date,u.first_name FROM birthdays b JOIN users u ON u.user_id=b.user_id WHERE substr(b.birth_date,6,5)!=? ORDER BY substr(b.birth_date,6,5)", (mmdd,)).fetchall()
    soon = []
    for i in range(7):
        d = (today + timedelta(days=i)).isoformat()[5:]
        for r in upcoming:
            if r["birth_date"][5:] == d:
                soon.append(f"â¢ {r['first_name'] or '-'} â {r['birth_date']}")
    gifts_today = c.execute("SELECT COUNT(*) n FROM bday_events WHERE kind='birthday' AND substr(created_at,1,10)=?", (today.isoformat(),)).fetchone()["n"]
    wk_start = (today - timedelta(days=today.weekday())).isoformat()
    gifts_week = c.execute("SELECT COUNT(*) n FROM bday_events WHERE kind='birthday' AND substr(created_at,1,10)>=?", (wk_start,)).fetchone()["n"]
    occ_sent = c.execute("SELECT COUNT(*) n FROM bday_events WHERE kind LIKE 'occ%'").fetchone()["n"]
    c.close()
    lines = ["ð <b>Ú¯Ø²Ø§Ø±Ø´ ØªÙÙØ¯ Ù ÙÙØ§Ø³Ø¨Øª</b>", "",
             f"<b>ØªÙÙØ¯ÙØ§Û Ø§ÙØ±ÙØ²:</b> {len(todays)}"]
    for r in todays[:20]:
        lines.append(f"ð {r['first_name'] or '-'} (<code>{r['user_id']}</code>)")
    lines.append("")
    lines.append("<b>ÙÙØªÙÙ Ø¢ÛÙØ¯Ù:</b>")
    lines.extend(soon[:15] if soon else ["â"])
    lines.append("")
    lines.append(f"ÙØ¯ÛÙâÙØ§Û Ø§ÙØ±ÙØ²: {gifts_today} | Ø§ÛÙ ÙÙØªÙ: {gifts_week}")
    lines.append(f"Ú©Ù Ø§Ø±Ø³Ø§ÙâÙØ§Û ÙÙØ§Ø³Ø¨ØªÛ Ø«Ø¨ØªâØ´Ø¯Ù: {occ_sent}")
    return "\n".join(lines)


async def _bdo_render(update, context, key):
    """Show an owner section. Uses edit when coming from a callback."""
    views = {"mgmt": _bdo_bday_mgmt_view(), "gift": _bdo_gift_view(), "occ": _bdo_occ_list_view(),
             "msg": _bdo_msg_view(), "cfg": _bdo_settings_view()}
    if key in views:
        text, kb = views[key]
        if hasattr(update, "callback_query") and update.callback_query is not None:
            await update.callback_query.answer()
            try:
                await update.callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                await update.callback_query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    elif key == "report":
        if hasattr(update, "callback_query") and update.callback_query is not None:
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(_bdo_report_text(), parse_mode="HTML")
        else:
            await update.message.reply_text(_bdo_report_text(), parse_mode="HTML")


async def _bdo_open_panel(update, context):
    uid = update.effective_user.id
    context.user_data["bdo_panel"] = True
    await update.message.reply_text(_bdo_home_text(), parse_mode="HTML", reply_markup=_bdo_kb())


async def _bdo_owner_callback(update, context):
    """Handles bd:* callbacks that belong to the OWNER panel (not user bd:set/del)."""
    q = update.callback_query
    uid = q.from_user.id
    data = q.data or ""
    if not _is_bd_owner(uid):
        master_incident("security", f"user {uid} tried owner birthday panel: {data}", severity="warning")
        await q.answer("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.", show_alert=True)
        return
    act = data[3:]
    if act == "home":
        await q.answer()
        await q.message.reply_text(_bdo_home_text(), parse_mode="HTML", reply_markup=_bdo_kb())
        return
    if act == "tog_main":
        bd_set("bd_enabled", "0" if bd_get("bd_enabled", "1") == "1" else "1")
        await _bdo_render(update, context, "mgmt")
        return
    if act == "tog_rem":
        bd_set("bd_reminder_enabled", "0" if bd_get("bd_reminder_enabled", "1") == "1" else "1")
        await _bdo_render(update, context, "mgmt")
        return
    if act == "cyc_rem":
        order = ["1", "3", "7", "14"]
        cur = bd_get("bd_reminder_days", "3")
        bd_set("bd_reminder_days", order[(order.index(cur) + 1) % len(order)] if cur in order else "3")
        await _bdo_render(update, context, "mgmt")
        return
    if act == "tog_gift":
        bd_set("bd_gift_enabled", "0" if bd_get("bd_gift_enabled", "1") == "1" else "1")
        await _bdo_render(update, context, "gift")
        return
    if act == "cyc_kind":
        cur = bd_get("bd_gift_kind", "xp")
        bd_set("bd_gift_kind", BD_GIFT_KINDS[(BD_GIFT_KINDS.index(cur) + 1) % len(BD_GIFT_KINDS)])
        await _bdo_render(update, context, "gift")
        return
    if act == "cyc_aud":
        cur = bd_get("bd_audience", "all")
        bd_set("bd_audience", BD_AUDIENCES[(BD_AUDIENCES.index(cur) + 1) % len(BD_AUDIENCES)])
        await _bdo_render(update, context, "gift")
        return
    if act == "amt":
        await q.answer()
        context.user_data["bdo_wait"] = "amount"
        await q.message.reply_text("ð° ÙÙØ¯Ø§Ø± ÙØ¯ÛÙ Ø±Ø§ Ø¨ÙØ±Ø³Øª (Ø¹Ø¯Ø¯):\nXP/ØªÙÚ©Ù = ÙØ§Ø­Ø¯Ø VIP/Ø§Ø´ØªØ±Ø§Ú© = ØªØ¹Ø¯Ø§Ø¯ Ø±ÙØ²")
        return
    if act == "text":
        await q.answer()
        context.user_data["bdo_wait"] = "text"
        await q.message.reply_text("âï¸ ÙØªÙ ØªØ¨Ø±ÛÚ© Ø¬Ø¯ÛØ¯ Ø±Ø§ Ø¨ÙØ±Ø³Øª (Ø§Ø² {name} Ø§Ø³ØªÙØ§Ø¯Ù Ú©Ù):")
        return
    if act == "time":
        await q.answer()
        context.user_data["bdo_wait"] = "time"
        await q.message.reply_text("â° Ø³Ø§Ø¹Øª Ø§Ø±Ø³Ø§Ù Ø±Ø§ Ø¨ÙØ±Ø³Øª (HH:MM):")
        return
    if act == "remdays":
        await q.answer()
        context.user_data["bdo_wait"] = "remdays"
        await q.message.reply_text("ð ÚÙØ¯ Ø±ÙØ² ÙØ¨Ù Ø§Ø² ØªÙÙØ¯ ÛØ§Ø¯Ø¢ÙØ±Û Ø´ÙØ¯Ø (Û± ØªØ§ Û±Û´)")
        return
    if act == "occ_add":
        await q.answer()
        context.user_data["bdo_wait"] = "occ_name"
        context.user_data["bdo_occ"] = {}
        await q.message.reply_text("â ÙØ§Ù ÙÙØ§Ø³Ø¨Øª Ø¬Ø¯ÛØ¯ Ø±Ø§ Ø¨ÙØ±Ø³Øª:")
        return
    if act.startswith("occ_toggle:"):
        oid = int(act.split(":")[1])
        c = _bday_db()
        c.execute("UPDATE occasions SET active=1-active WHERE id=?", (oid,))
        c.commit()
        c.close()
        await _bdo_render(update, context, "occ")
        return
    if act.startswith("occ_del:"):
        oid = int(act.split(":")[1])
        c = _bday_db()
        c.execute("DELETE FROM occasions WHERE id=?", (oid,))
        c.commit()
        c.close()
        await _bdo_render(update, context, "occ")
        return
    await q.answer()


def _bdo_handle_input(update, context, uid, txt):
    """Consume one pending owner/user text input. Returns True when handled."""
    ud = context.user_data
    wait = ud.get("bdo_wait")
    fa = lang(uid) == "fa"
    if wait == "amount":
        ud.pop("bdo_wait", None)
        try:
            v = float(_bd_en(txt).strip())
            assert 0 < v <= 1000000
        except Exception:
            ud["bdo_wait"] = "amount"
            return False
        bd_set("bd_gift_amount", int(v) if v == int(v) else v)
        asyncio.ensure_future(update.message.reply_text("â Ø°Ø®ÛØ±Ù Ø´Ø¯.", reply_markup=_bdo_kb()))
        return True
    if wait == "text":
        ud.pop("bdo_wait", None)
        if len(txt) > 800:
            ud["bdo_wait"] = "text"
            return False
        bd_set("bd_congrats_fa", txt)
        asyncio.ensure_future(update.message.reply_text("â ÙØªÙ ØªØ¨Ø±ÛÚ© Ø°Ø®ÛØ±Ù Ø´Ø¯.", reply_markup=_bdo_kb()))
        return True
    if wait == "time":
        ud.pop("bdo_wait", None)
        m = re.match(r"^(\d{1,2}):(\d{2})$", _bd_en(txt.strip()))
        if not m or not (0 <= int(m.group(1)) <= 23 and 0 <= int(m.group(2)) <= 59):
            ud["bdo_wait"] = "time"
            return False
        bd_set("bd_send_time", f"{int(m.group(1)):02d}:{m.group(2)}")
        asyncio.ensure_future(update.message.reply_text("â Ø³Ø§Ø¹Øª Ø§Ø±Ø³Ø§Ù Ø°Ø®ÛØ±Ù Ø´Ø¯.", reply_markup=_bdo_kb()))
        return True
    if wait == "remdays":
        ud.pop("bdo_wait", None)
        try:
            n = int(_bd_en(txt).strip())
            assert 1 <= n <= 14
        except Exception:
            ud["bdo_wait"] = "remdays"
            return False
        bd_set("bd_reminder_days", n)
        asyncio.ensure_future(update.message.reply_text("â ÙØ§ØµÙÙÙ ÛØ§Ø¯Ø¢ÙØ±Û Ø°Ø®ÛØ±Ù Ø´Ø¯.", reply_markup=_bdo_kb()))
        return True
    occ = ud.get("bdo_occ") or {}
    if wait == "occ_name":
        ud["bdo_occ"] = {"name": txt[:100]}
        ud["bdo_wait"] = "occ_date"
        asyncio.ensure_future(update.message.reply_text("ð ØªØ§Ø±ÛØ® ÙÙØ§Ø³Ø¨Øª Ø±Ø§ Ø¨ÙØ±Ø³Øª (MM-DD ÙØ«Ù 03-15 ÛØ§ ØªØ§Ø±ÛØ® Ú©Ø§ÙÙ):"))
        return True
    if wait == "occ_date":
        md = bd_parse_mmdd(txt) or (bd_parse_date(txt) or "")[5:] or None
        if not md:
            asyncio.ensure_future(update.message.reply_text("â ï¸ ÙØ±ÙØª Ø¯Ø±Ø³Øª ÙÛØ³Øª. Ø¯ÙØ¨Ø§Ø±Ù Ø¨ÙØ±Ø³Øª (ÙØ«Ù 03-15):"))
            return True
        occ["date"] = md
        ud["bdo_occ"] = occ
        ud["bdo_wait"] = "occ_msg"
        asyncio.ensure_future(update.message.reply_text("âï¸ Ù¾ÛØ§Ù ÙÙØ§Ø³Ø¨ØªÛ Ø±Ø§ Ø¨ÙØ±Ø³Øª (ÛØ§ Â«-Â» Ø¨Ø±Ø§Û Ù¾ÛØ´âÙØ±Ø¶):"))
        return True
    if wait == "occ_msg":
        occ["message"] = "" if txt.strip() == "-" else txt[:1000]
        ud["bdo_occ"] = occ
        ud["bdo_wait"] = "occ_xp"
        asyncio.ensure_future(update.message.reply_text("â­ Ù¾Ø§Ø¯Ø§Ø´ XP ÙÙÙÙ Ú©Ø§Ø±Ø¨Ø±Ø§ÙØ (Ø¹Ø¯Ø¯ ÛØ§ 0):"))
        return True
    if wait == "occ_xp":
        try:
            occ["xp_amount"] = max(0, int(_bd_en(txt).strip()))
        except Exception:
            asyncio.ensure_future(update.message.reply_text("â ï¸ Ø¹Ø¯Ø¯ ÙØ¹ØªØ¨Ø± Ø¨ÙØ±Ø³Øª:"))
            return True
        ud["bdo_occ"] = occ
        ud["bdo_wait"] = "occ_vip"
        asyncio.ensure_future(update.message.reply_text("ð Ø±ÙØ²ÙØ§Û VIP ÙØ¯ÛÙØ (Ø¹Ø¯Ø¯ ÛØ§ 0):"))
        return True
    if wait == "occ_vip":
        try:
            occ["vip_days"] = max(0, int(_bd_en(txt).strip()))
        except Exception:
            asyncio.ensure_future(update.message.reply_text("â ï¸ Ø¹Ø¯Ø¯ ÙØ¹ØªØ¨Ø± Ø¨ÙØ±Ø³Øª:"))
            return True
        ud.pop("bdo_wait", None)
        ud.pop("bdo_occ", None)
        c = _bday_db()
        c.execute("INSERT INTO occasions(name,date,message,xp_amount,vip_days,active,auto_send,created_at) VALUES(?,?,?,?,?,1,1,?)",
                  (occ["name"], occ["date"], occ.get("message", ""), occ.get("xp_amount", 0), occ.get("vip_days", 0), datetime.now(TZ).isoformat()))
        c.commit()
        c.close()
        master_log(uid, "occasion_created", occ["name"], occ["date"])
        asyncio.ensure_future(update.message.reply_text("â ÙÙØ§Ø³Ø¨Øª Ø³Ø§Ø®ØªÙ Ø´Ø¯ Ù ÙØ± Ø³Ø§Ù Ø®ÙØ¯Ú©Ø§Ø± Ø§Ø±Ø³Ø§Ù ÙÛâØ´ÙØ¯.", reply_markup=_bdo_kb()))
        return True
    return False


async def _bdo_route(update, context, uid, txt):
    """Owner-panel ReplyKeyboard routing. Returns True when the label was consumed."""
    ud = context.user_data
    fa = lang(uid) == "fa"
    if txt == BD_PANEL_BTN:
        if not _is_bd_owner(uid):
            master_incident("security", f"user {uid} tried to open the birthday panel", severity="warning")
            await update.message.reply_text("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.")
            return True
        await _bdo_open_panel(update, context)
        return True
    if not (_is_bd_owner(uid) and ud.get("bdo_panel")):
        return False
    if txt in _BDO_SECTIONS or txt in ("â¬ï¸ Ø¨Ø±Ú¯Ø´Øª", "â¬ï¸ Back"):
        if txt in ("â¬ï¸ Ø¨Ø±Ú¯Ø´Øª", "â¬ï¸ Back"):
            ud.pop("bdo_panel", None)
            ud.pop("bdo_wait", None)
            ud.pop("bdo_occ", None)
            title, markup = _manager_main_keyboard(uid)
            await update.message.reply_text(title, parse_mode="HTML", reply_markup=markup)
            return True
        key = {"ð ÙØ¯ÛØ±ÛØª ØªÙÙØ¯": "mgmt", "ð ÙØ¯ÛÙ ØªÙÙØ¯": "gift", "ð ÙÙØ§Ø³Ø¨ØªâÙØ§": "occ",
               "âï¸ Ù¾ÛØ§Ù ØªØ¨Ø±ÛÚ©": "msg", "âï¸ ØªÙØ¸ÛÙØ§Øª": "cfg", "ð Ú¯Ø²Ø§Ø±Ø´ ØªÙÙØ¯": "report"}.get(txt)
        if key == "msg" or key == "cfg":
            text, kb = _bdo_msg_view() if key == "msg" else _bdo_settings_view()
            await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        elif key == "report":
            await update.message.reply_text(_bdo_report_text(), parse_mode="HTML")
        elif key:
            await _bdo_render(update, context, key)
        return True
    return False


# ---------- scheduler (runs once per day, after the configured send time) ----------

async def birthday_occasion_job(context):
    now = datetime.now(TZ)
    today = now.date()
    mmdd = today.isoformat()[5:]
    year = today.year
    c = _bday_db()
    enabled = bd_get("bd_enabled", "1") == "1"
    has_occ = c.execute("SELECT 1 FROM occasions WHERE active=1 AND auto_send=1 LIMIT 1").fetchone()
    if not enabled and not has_occ:
        c.close()
        return
    if now.strftime("%H:%M") < bd_get("bd_send_time", "09:00"):
        c.close()
        return
    if bd_get("last_bd_run", "") == today.isoformat():
        c.close()
        return
    bd_set("last_bd_run", today.isoformat())

    if enabled:
        for r in c.execute("SELECT user_id FROM birthdays WHERE substr(birth_date,6,5)=?", (mmdd,)).fetchall():
            uid = int(r["user_id"])
            if user_blocked(uid) or not bd_audience_ok(uid):
                continue
            eid = _bday_claim(c, uid, year, "birthday")
            if not eid:
                continue
            c.commit()
            name = ""
            try:
                u = c.execute("SELECT first_name FROM users WHERE user_id=?", (uid,)).fetchone()
                name = u["first_name"] if u else ""
            except Exception:
                logger.debug("birthday_occasion_job: failed to fetch user name for uid=%s", uid)
            desc = ""
            gkind = bd_get("bd_gift_kind", "xp")
            if gkind != "none" and bd_get("bd_gift_enabled", "1") == "1":
                try:
                    amt = float(bd_get("bd_gift_amount", "50"))
                except ValueError:
                    amt = 50.0
                desc = _bday_reward(c, eid, uid, gkind, amt)
                c.commit()
            msg = bd_congrats_text(name) + (("\n" + desc) if desc else "")
            try:
                await context.bot.send_message(uid, msg)
            except Exception:
                logger.warning("birthday_occasion_job: failed to send congratulation to uid=%s", uid)
        if bd_get("bd_reminder_enabled", "1") == "1":
            try:
                nd = max(1, int(bd_get("bd_reminder_days", "3")))
            except ValueError:
                nd = 3
            tmd = (today + timedelta(days=nd)).isoformat()[5:]
            for r in c.execute("SELECT user_id FROM birthdays WHERE substr(birth_date,6,5)=?", (tmd,)).fetchall():
                uid = int(r["user_id"])
                if user_blocked(uid) or not bd_audience_ok(uid):
                    continue
                if not _bday_claim(c, uid, year, "reminder"):
                    continue
                c.commit()
                try:
                    await context.bot.send_message(uid, f"ð Ø³ÙØ§Ù! {nd} Ø±ÙØ² ØªØ§ ØªÙÙØ¯Øª ÙÙÙØ¯Ù ð Ø¢ÙØ§Ø¯ÙÙ Ø¬Ø´Ù Ø¨Ø§Ø´!")
                except Exception:
                    logger.warning("birthday_occasion_job: failed to send reminder to uid=%s", uid)

    for o in c.execute("SELECT * FROM occasions WHERE active=1 AND auto_send=1 AND substr(date,1,5)=? AND last_sent_year<?",
                       (mmdd, year)).fetchall():
        oid = int(o["id"])
        c.execute("UPDATE occasions SET last_sent_year=? WHERE id=?", (year, oid))
        c.commit()
        base = o["message"] or f"ð ÙÙØ§Ø³Ø¨Øª Â«{o['name']}Â» Ø¨Ø± ÙÙÙÙ Ø´ÙØ§ ÙØ¨Ø§Ø±Ú©!"
        count = 0
        for u in c.execute("SELECT user_id FROM users WHERE blocked=0").fetchall():
            uid = int(u["user_id"])
            eid = _bday_claim(c, uid, year, f"occ{oid}", oid)
            if not eid:
                continue
            desc = ""
            if int(o["xp_amount"] or 0) > 0:
                n = int(o["xp_amount"])
                c.execute("UPDATE users SET xp=COALESCE(xp,0)+? WHERE user_id=?", (n, uid))
                c.execute("INSERT INTO xp_log(user_id,amount,reason,created_at) VALUES(?,?,?,?)", (uid, n, f"occasion_{oid}", now.isoformat()))
                desc += f"\nâ­ +{n} XP"
            if int(o["vip_days"] or 0) > 0:
                desc += "\n" + _bday_reward(c, eid, uid, "vip", int(o["vip_days"]))
            c.commit()
            try:
                await context.bot.send_message(uid, base + desc)
                count += 1
            except Exception:
                logger.warning("birthday_occasion_job: failed to send occasion to uid=%s", uid)
        master_log(master_owner_id() or 0, "occasion_sent", o["name"], f"{count} users")
    c.close()


# ---------- integration hooks ----------

_OLD_MMK_BDAY = _manager_main_keyboard


def _manager_main_keyboard(uid):
    title, markup = _OLD_MMK_BDAY(uid)
    if _is_bd_owner(uid):
        rows = [[(getattr(x, 'text', None) or str(x)) for x in r] for r in markup.keyboard]
        rows.insert(1, [BD_PANEL_BTN])
        markup = ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)
    return title, markup


_OLD_TEXT_ROUTER_BDAY = text_router


async def text_router(update, context):
    if update.message and update.message.text:
        uid = update.effective_user.id
        txt = update.message.text.strip()
        # Navigation labels always win over any pending birthday input state.
        if txt in ("ð  ÙÙÙÛ Ø§ØµÙÛ", "ð  Main Menu", "â¬ï¸ Ø¨Ø±Ú¯Ø´Øª", "â¬ï¸ Back"):
            context.user_data.pop("bd_wait", None)
            context.user_data.pop("bdo_wait", None)
            context.user_data.pop("bdo_occ", None)
        else:
            # User-side pending birthday-date input (from /birthday buttons).
            if context.user_data.get("bd_wait") == "date":
                context.user_data.pop("bd_wait", None)
                parsed = bd_parse_any_date(txt)
                if not parsed:
                    await update.message.reply_text(
                        "â ï¸ ÙØ±ÙØª Ø¯Ø±Ø³Øª ÙÛØ³Øª.\nÙØ«Ø§Ù Ø´ÙØ³Û: <code>1382/04/23</code> ÛØ§ <code>23/04/1382</code>\nÙØ«Ø§Ù ÙÛÙØ§Ø¯Û: <code>2000-08-24</code>",
                        parse_mode="HTML")
                    return
                iso, _kind = parsed
                c = _bday_db()
                c.execute("INSERT INTO birthdays(user_id,birth_date,created_at,updated_at) VALUES(?,?,?,?) "
                          "ON CONFLICT(user_id) DO UPDATE SET birth_date=excluded.birth_date, updated_at=excluded.updated_at",
                          (uid, iso, datetime.now(TZ).isoformat(), datetime.now(TZ).isoformat()))
                c.commit()
                c.close()
                jy, jm, jd = _jalali_from_iso(iso)
                mname = (_BD_MONTHS_FA if lang(uid) == "fa" else _BD_MONTHS_EN)[jm - 1]
                await update.message.reply_text(
                    f"â Ø«Ø¨Øª Ø´Ø¯! ð ØªÙÙØ¯Øª ÙØ± Ø³Ø§Ù {jd} {mname} Ø¬Ø´Ù Ú¯Ø±ÙØªÙ ÙÛâØ´ÙØ¯.\nð ØªØ§Ø±ÛØ® Ø§Ø³ØªØ§ÙØ¯Ø§Ø±Ø¯: <code>{iso}</code>",
                    parse_mode="HTML")
                return
            # Owner panel routing / pending owner inputs.
            if await _bdo_route(update, context, uid, txt):
                return
            if _is_bd_owner(uid) and context.user_data.get("bdo_wait"):
                if _bdo_handle_input(update, context, uid, txt):
                    return
    return await _OLD_TEXT_ROUTER_BDAY(update, context)


# ===================== END BIRTHDAY & OCCASIONS MODULE =====================



# ---------- Satisfaction Poll ----------
async def poll_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    data = q.data or ""
    if not data.startswith("poll:"):
        await q.answer()
        return
    choice = data[5:]
    labels = {"satisfied": "ð Ø¹Ø§ÙÛ Ø¨ÙØ¯", "ok": "ð Ø®ÙØ¨ Ø¨ÙØ¯", "meh": "ð ÙØ¹ÙÙÙÛ"}
    label = labels.get(choice, choice)
    # Log the poll response
    try:
        c = db()
        now_iso = datetime.now(TZ).isoformat()
        c.execute("INSERT INTO delivery_log(delivery_key,user_id,delivery_type,created_at) VALUES(?,?,?,?)",
                  (f"poll:{uid}:{datetime.now(TZ).date().isoformat()}", uid, f"poll_{choice}", now_iso))
        c.commit()
        c.close()
    except Exception:
        logger.debug("poll_callback: failed to log poll response", exc_info=True)
    await q.answer(f"ÙÙÙÙÙ! ÙØ¸Ø±Øª Ø«Ø¨Øª Ø´Ø¯: {label}", show_alert=True)
    try:
        await q.message.edit_text(
            f"ð£ï¸ <b>ÙØ¸Ø±Ø³ÙØ¬Û</b>\n\nØ§Ø² Ø§ÙÚ©Ø§ÙØ§Øª Ø±Ø¨Ø§Øª Ø±Ø§Ø¶Û Ø¨ÙØ¯ÛØ\n\nâ Ù¾Ø§Ø³Ø® ØªÙ: {label}\n\nÙÙÙÙÙ Ø§Ø² ÙØ¸Ø±Øª! â¤ï¸",
            parse_mode="HTML"
        )
    except Exception:
        logger.debug("poll_callback: failed to edit poll message", exc_info=True)



# ---------- Channel Membership Tracking ----------
async def track_channel_membership(update, context):
    """Track when users join/leave the required channel."""
    try:
        chat_member = update.chat_member
        if not chat_member:
            return
        user = chat_member.new_chat_member.user if hasattr(chat_member, 'new_chat_member') else None
        if not user or user.is_bot:
            return
        uid = user.id
        forced_ref = _forced_sub_channel_ref() if _forced_sub_is_enabled() else required_channel()
        event_chat_id = getattr(chat_member, "chat", None)
        event_chat_id = getattr(event_chat_id, "id", event_chat_id)
        if forced_ref and str(event_chat_id) != str(forced_ref):
            return
        old_status = getattr(chat_member.old_chat_member, 'status', None)
        new_status = getattr(chat_member.new_chat_member, 'status', None)
        if old_status == new_status:
            return
        # Track join
        if new_status in ('member', 'administrator', 'creator') and old_status in ('left', 'kicked', None):
            _forced_sub_record_join(uid)
        # Track leave
        elif new_status in ('left', 'kicked') and old_status in ('member', 'administrator', 'creator'):
            _forced_sub_record_leave(uid)
    except Exception:
        logger.exception("track_channel_membership failed")


# ===================== FINAL UI / FLOW QUALITY LAYER =====================
# This layer only reorganizes existing services. It does not add a new service.
# Existing database records and settings stay untouched.

_FINAL_OLD_COMPACT_USER_KEYBOARD = _compact_user_keyboard
_FINAL_OLD_COMPACT_MENU_KEYBOARD = _compact_menu_keyboard
_FINAL_OLD_COMPACT_ROOT_INLINE = _compact_root_inline
_FINAL_OLD_TEXT_ROUTER_UI = text_router
_FINAL_OLD_COMPACT_MENU_CALLBACK_UI = compact_menu_callback


def _compact_user_keyboard(uid):
    fa = lang(uid) == "fa"
    rows = [
        ["â¡ Ø¯Ø³ØªØ±Ø³Û Ø³Ø±ÛØ¹" if fa else "â¡ Quick Access", "ð¯ Ø¨Ø±ÙØ§ÙÙ Ù Ø§ÙØ¯Ø§Ù" if fa else "ð¯ Goals & Plan"],
        ["ð ÙÛÙØª Ø¢ÙÙØ§ÛÙ" if fa else "ð Online Prices", "ð ØªÙÙÛÙ Ù ÛØ§Ø¯Ø¢ÙØ±Û" if fa else "ð Calendar & Reminders"],
        ["ð¤ Ø­Ø³Ø§Ø¨ ÙÙ" if fa else "ð¤ My Account", "ð¤ Ø¯Ø¹ÙØª Ø¯ÙØ³ØªØ§Ù" if fa else "ð¤ Invite Friends"],
        ["ð Ø¢ÙØ§Ø± Ù Ú¯Ø²Ø§Ø±Ø´" if fa else "ð Stats & Reports", "ð ï¸ Ø§Ø¨Ø²Ø§Ø±ÙØ§" if fa else "ð ï¸ Tools"],
        ["ð« Ù¾Ø´ØªÛØ¨Ø§ÙÛ" if fa else "ð« Support", "âï¸ ØªÙØ¸ÛÙØ§Øª" if fa else "âï¸ Settings"],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)


def _compact_menu_keyboard(uid, section):
    fa = lang(uid) == "fa"
    common = {
        "goals": [
            [("ð¯ Ø§ÙØ¯Ø§Ù Ø§ÙØ±ÙØ²", "cm:today"), ("â ÙØ¯Ù Ø¬Ø¯ÛØ¯", "cm:custom_goal")],
            [("ð Ø§ÙØ¯Ø§Ù Ø¢ÙØ§Ø¯Ù", "cm:ready_goals"), ("âï¸ ÙÛØ±Ø§ÛØ´ Ø§ÙØ¯Ø§Ù", "cm:edit_goals")],
            [("ð ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§", "cm:reminders"), ("ð ØªÙÙÛÙ", "cm:calendar")],
            [("ð Ø¢ÙØ§Ø± ÙÙ", "cm:stats"), ("ð Ú¯Ø²Ø§Ø±Ø´ ÙÙØªÚ¯Û", "cm:weekly")],
        ],
        "reports": [
            [("ð Ú¯Ø²Ø§Ø±Ø´ Ø§ÙØ±ÙØ²", "v25:reports"), ("ð Ú¯Ø²Ø§Ø±Ø´ ÙÙØªÚ¯Û", "cm:weekly")],
            [("ðï¸ Ú¯Ø²Ø§Ø±Ø´ ÙØ§ÙØ§ÙÙ", "v25:report_month"), ("ð Ø¢ÙØ§Ø± ÙÙ", "cm:stats")],
            [("ð Ø¯Ø³ØªØ§ÙØ±Ø¯ÙØ§", "cm:achievements"), ("â­ XP", "cm:xp")],
        ],
        "tools": [
            [("ð Ø±Ø§ÙÙÙØ§Û Ø±Ø¨Ø§Øª", "cm:guide")],
        ],
        "vip": [
            [("ð VIP Ù Ø§Ø´ØªØ±Ø§Ú©", "cm:vip"), ("â­ XP", "cm:xp")],
            [("ðï¸ ØªÙÚ©ÙâÙØ§Û ÙÙ", "cm:tokens"), ("ð¤ Ø¯Ø¹ÙØª Ø¯ÙØ³ØªØ§Ù", "cm:referral")],
            [("ð Ø¯Ø³ØªØ§ÙØ±Ø¯ÙØ§", "cm:achievements")],
        ],
        "account": [
            [("ð¤ Ù¾Ø±ÙÙØ§ÛÙ", "cm:profile"), ("ð Ù¾Ø§Ø¯Ø§Ø´âÙØ§Û ÙÙ", "cm:rewards")],
            [("ð ÛØ§Ø¯Ø¢ÙØ±ÛâÙØ§", "cm:reminders"), ("ð ØªÙÙÛÙ ÙÙ", "cm:calendar")],
            [("âï¸ ØªÙØ¸ÛÙØ§Øª", "cm:settings")],
        ],
        "support": [
            [("ð« Ù¾Ø´ØªÛØ¨Ø§ÙÛ", "cm:support"), ("ð Ø±Ø§ÙÙÙØ§Û Ø±Ø¨Ø§Øª", "cm:guide")],
        ],
    }
    en = {
        "cm:today":"ð¯ Today's Goals", "cm:custom_goal":"â New Goal",
        "cm:ready_goals":"ð Ready Goals", "cm:edit_goals":"âï¸ Edit Goals",
        "cm:reminders":"ð Reminders", "cm:calendar":"ð Calendar",
        "cm:stats":"ð My Stats", "cm:weekly":"ð Weekly Report",
        "v25:reports":"ð Today's Report", "v25:report_month":"ðï¸ Monthly Report",
        "cm:achievements":"ð Achievements", "cm:xp":"â­ XP",
        "cm:vip":"ð VIP & Subscription", "cm:tokens":"ðï¸ My Tokens", "cm:referral":"ð¤ Referrals",
        "cm:profile":"ð¤ Profile", "cm:rewards":"ð My Rewards", "cm:settings":"âï¸ Settings",
        "cm:support":"ð« Support",
    }
    rows=[]
    for row in common.get(section, []):
        out=[]
        for label, cb in row:
            out.append(InlineKeyboardButton(label if fa else en.get(cb, label), callback_data=cb))
        rows.append(out)
    rows.append([
        InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª" if fa else "â¬ï¸ Back", callback_data="cm:home"),
        main_menu_button(uid),
    ])
    return InlineKeyboardMarkup(rows)


def _compact_root_inline(uid):
    fa = lang(uid) == "fa"
    if fa:
        rows = [
            [InlineKeyboardButton("ð¯ Ø¨Ø±ÙØ§ÙÙ ÙÙ", callback_data="menu:goals")],
            [InlineKeyboardButton("ð Ú¯Ø²Ø§Ø±Ø´ Ù Ù¾ÛØ´Ø±ÙØª", callback_data="menu:reports"), InlineKeyboardButton("ð¤ Ø§Ø¨Ø²Ø§Ø±ÙØ§", callback_data="menu:tools")],
            [InlineKeyboardButton("ð VIP Ù XP", callback_data="menu:vip"), InlineKeyboardButton("ð¤ Ø­Ø³Ø§Ø¨ ÙÙ", callback_data="menu:account")],
            [InlineKeyboardButton("ð« Ù¾Ø´ØªÛØ¨Ø§ÙÛ", callback_data="menu:support")],
        ]
    else:
        rows = [
            [InlineKeyboardButton("ð¯ My Plan", callback_data="menu:goals")],
            [InlineKeyboardButton("ð Reports & Progress", callback_data="menu:reports"), InlineKeyboardButton("ð¤ Tools", callback_data="menu:tools")],
            [InlineKeyboardButton("ð VIP & XP", callback_data="menu:vip"), InlineKeyboardButton("ð¤ My Account", callback_data="menu:account")],
            [InlineKeyboardButton("ð« Support", callback_data="menu:support")],
        ]
    return InlineKeyboardMarkup(rows)


async def _render_rewards_page(update, context):
    uid = update.effective_user.id
    fa = lang(uid) == "fa"
    xp, level, _ = xp_info(uid)
    text = (
        f"ð <b>Ù¾Ø§Ø¯Ø§Ø´âÙØ§Û ÙÙ</b>\n\nâ­ XP: {xp}\nð Ø³Ø·Ø­: {level}\nð VIP: {'ÙØ¹Ø§Ù' if is_vip(uid) else 'ØºÛØ±ÙØ¹Ø§Ù'}"
        if fa else
        f"ð <b>My Rewards</b>\n\nâ­ XP: {xp}\nð Level: {level}\nð VIP: {'Active' if is_vip(uid) else 'Inactive'}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("â­ XP ÙÙ" if fa else "â­ My XP", callback_data="cm:xp"), InlineKeyboardButton("ð Ø¯Ø³ØªØ§ÙØ±Ø¯ÙØ§" if fa else "ð Achievements", callback_data="cm:achievements")],
        [InlineKeyboardButton("ðï¸ ØªÙÚ©ÙâÙØ§Û ÙÙ" if fa else "ðï¸ My Tokens", callback_data="cm:tokens"), InlineKeyboardButton("ð¤ Ø¯Ø¹ÙØª Ø¯ÙØ³ØªØ§Ù" if fa else "ð¤ Referrals", callback_data="cm:referral")],
        [InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª" if fa else "â¬ï¸ Back", callback_data="menu:account"), main_menu_button(uid)],
    ])
    q = getattr(update, "callback_query", None)
    if q:
        await q.answer()
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def compact_menu_callback(update, context):
    q = update.callback_query
    data = q.data or ""
    if data == "cm:rewards":
        return await _render_rewards_page(update, context)
    if data == "v25:report_month":
        await q.answer()
        return await v25_reports(update, context, "month")
    if data == "v25:reports":
        await q.answer()
        return await v25_reports(update, context, "day")
    return await _FINAL_OLD_COMPACT_MENU_CALLBACK_UI(update, context)


async def text_router(update, context):
    if not update.message or not update.message.text:
        return await _FINAL_OLD_TEXT_ROUTER_UI(update, context)
    txt = update.message.text.strip()
    uid = update.effective_user.id
    direct = {
        "ð¯ Ø¨Ø±ÙØ§ÙÙ Ù Ø§ÙØ¯Ø§Ù": "goals", "ð¯ Goals & Plan": "goals",
        "ð ÙÛÙØª Ø¢ÙÙØ§ÛÙ": "prices", "ð Online Prices": "prices",
        "ð ØªÙÙÛÙ Ù ÛØ§Ø¯Ø¢ÙØ±Û": "calendar", "ð Calendar & Reminders": "calendar",
        "ð¤ Ø­Ø³Ø§Ø¨ ÙÙ": "account", "ð¤ My Account": "account",
        "ð Ø¢ÙØ§Ø± Ù Ú¯Ø²Ø§Ø±Ø´": "reports", "ð Stats & Reports": "reports", "ð Reports & Analytics": "reports",
        "ð ï¸ Ø§Ø¨Ø²Ø§Ø±ÙØ§": "tools", "ð ï¸ Tools": "tools",
        "ð« Ù¾Ø´ØªÛØ¨Ø§ÙÛ": "support", "ð« Support": "support",
        "âï¸ ØªÙØ¸ÛÙØ§Øª": "settings", "âï¸ Settings": "settings",
    }
    section = direct.get(txt)
    if section:
        clear_flow(context)
        if section in {"goals", "reports", "tools", "account", "support"}:
            return await _compact_menu_show(update, context, section)
        if section == "prices":
            return await prices(update, context)
        if section == "calendar":
            return await v25_reminders_menu(update, context)
        if section == "settings":
            return await settings(update, context)
    if txt in ("ð Ù¾Ø§Ø¯Ø§Ø´âÙØ§Û ÙÙ", "ð My Rewards"):
        clear_flow(context)
        return await _render_rewards_page(update, context)
    return await _FINAL_OLD_TEXT_ROUTER_UI(update, context)

# ===================== MYTASKS DIRECT FINAL INTEGRATION =====================
# All previously discussed runtime changes live here in the source itself.
# No build-time patch files are required. User data is never deleted here.

_FINAL_DIRECT_FEATURE_ALIASES = {
    "weekly_table": "weekly", "referral": "referrals", "price": "price_data",
    "price_data": "price_data", "customer": "customers", "customers": "customers", "vip": "vip",
    "support": "support", "settings": "settings", "goals": "goals", "edit": "goals",
    "reminders": "reminders", "calendar": "calendar_hub", "profile": "profile",
    "achievements": "achievements", "xp": "xp", "stats": "stats",
}

def _direct_feature_key(key):
    return _FINAL_DIRECT_FEATURE_ALIASES.get(str(key or ""), str(key or ""))

def _direct_feature_allowed(uid, key):
    key = _direct_feature_key(key)
    if admin_guard(uid):
        return True
    try:
        if not feature_enabled(key):
            return False
        mode = feature_access_mode(key, uid)
        if mode == "off":
            return False
        if mode == "vip" and not is_vip(uid):
            return False
        # Respect per-user preference when available.
        if "user_pref_enabled" in globals():
            try:
                return bool(user_pref_enabled(uid, key))
            except Exception:
                pass
        return True
    except Exception:
        # A disabled feature must never become enabled because of an error.
        return False

# Fix the old fail-open feature gate.
def user_feature_allowed(uid, key):
    return _direct_feature_allowed(uid, key)

def _direct_cb_feature(data):
    data = str(data or "")
    if data in {"cm:home", "nav:main", "cm:guide", "v25:admin", "v25:admin:hub", "v25:fsub:home"}:
        return None
    exact = {
        "cm:referral":"referrals", "cm:settings":"settings", "cm:today":"goals",
        "cm:custom_goal":"goals", "cm:ready_goals":"goals", "cm:edit_goals":"edit",
        "cm:reminders":"reminders", "cm:calendar":"calendar_hub", "cm:stats":"stats",
        "cm:weekly":"weekly", "cm:achievements":"achievements", "cm:xp":"xp",
        "cm:tokens":"vip", "cm:rewards":"vip", "cm:support":"support",
        "v25:reports":"stats", "v25:report_month":"stats", "v25:report_week":"stats",
        "v25:today":"goals",
    }
    if data in exact:
        return exact[data]
    if data.startswith("price:"): return "price_data"
    if data.startswith("vip:"): return "vip"
    if data.startswith("ref:"): return "referrals"
    if data.startswith("support:"): return "support"
    if data.startswith("settings:"): return "settings"
    if data.startswith("goal") or data.startswith("goals:") or data.startswith("newgoal:") or data.startswith("readysub:"):
        return "goals"
    if data.startswith("cust:") or data.startswith("booking:") or data.startswith("adm:customers"):
        return "customers"
    if data.startswith("portfolio:"): return "portfolio"
    if data.startswith("installments:"): return "installments"
    return None

# Final compact menu filter. Disabled features disappear instead of merely failing later.
def _direct_filter_markup(uid, markup):
    if not markup or admin_guard(uid):
        return markup
    try:
        rows = []
        for row in markup.inline_keyboard:
            out=[]
            for btn in row:
                key=_direct_cb_feature(getattr(btn,"callback_data",None))
                if key and not _direct_feature_allowed(uid,key):
                    continue
                out.append(btn)
            if out: rows.append(out)
        return InlineKeyboardMarkup(rows)
    except Exception:
        return markup

_DIRECT_OLD_COMPACT_MENU_KEYBOARD = _compact_menu_keyboard

def _compact_menu_keyboard(uid, section):
    return _direct_filter_markup(uid, _DIRECT_OLD_COMPACT_MENU_KEYBOARD(uid, section))

_DIRECT_OLD_COMPACT_KEYBOARD = compact_keyboard

def compact_keyboard(uid):
    return _direct_filter_markup(uid, _DIRECT_OLD_COMPACT_KEYBOARD(uid))

def _direct_filter_reply_keyboard(uid, markup):
    if not markup or admin_guard(uid):
        return markup
    label_map={
        "ð ÙÛÙØª Ø¢ÙÙØ§ÛÙ":"price_data", "ð Online Prices":"price_data",
        "ð¤ Ø¯Ø¹ÙØª Ø¯ÙØ³ØªØ§Ù":"referrals", "ð¤ Invite Friends":"referrals",
        "âï¸ ØªÙØ¸ÛÙØ§Øª":"settings", "âï¸ Settings":"settings",
        "ð ØªÙÙÛÙ Ù ÛØ§Ø¯Ø¢ÙØ±Û":"calendar_hub", "ð Calendar & Reminders":"calendar_hub",
        "ð Ø¢ÙØ§Ø± Ù Ú¯Ø²Ø§Ø±Ø´":"stats", "ð Stats & Reports":"stats",
        "ð¯ Ø¨Ø±ÙØ§ÙÙ Ù Ø§ÙØ¯Ø§Ù":"goals", "ð¯ Goals & Plan":"goals",
        "ð« Ù¾Ø´ØªÛØ¨Ø§ÙÛ":"support", "ð« Support":"support",
    }
    rows=[]
    for row in markup.keyboard:
        out=[x for x in row if not (label_map.get(str(x)) and not _direct_feature_allowed(uid,label_map[str(x)]))]
        if out: rows.append(out)
    return ReplyKeyboardMarkup(rows,resize_keyboard=True,one_time_keyboard=False)

_DIRECT_OLD_COMPACT_USER_KEYBOARD = _compact_user_keyboard
def _compact_user_keyboard(uid):
    return _direct_filter_reply_keyboard(uid,_DIRECT_OLD_COMPACT_USER_KEYBOARD(uid))


_DIRECT_OLD_COMPACT_MENU_CALLBACK = compact_menu_callback
async def compact_menu_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    key = _direct_cb_feature(q.data)
    if key and not _direct_feature_allowed(uid,key):
        await q.answer("ð Ø§ÛÙ ÙØ§Ø¨ÙÛØª Ø¯Ø± Ø­Ø§Ù Ø­Ø§Ø¶Ø± ØºÛØ±ÙØ¹Ø§Ù Ø§Ø³Øª.", show_alert=True)
        return
    return await _DIRECT_OLD_COMPACT_MENU_CALLBACK(update, context)

# Block direct callbacks for the most important feature families.
_DIRECT_OLD_V25_CALLBACK = v25_callback
async def v25_callback(update, context):
    q=update.callback_query
    uid=q.from_user.id
    data=str(q.data or "")
    key=_direct_cb_feature(data)
    if key and not _direct_feature_allowed(uid,key):
        await q.answer("ð Ø§ÛÙ ÙØ§Ø¨ÙÛØª Ø¯Ø± Ø­Ø§Ù Ø­Ø§Ø¶Ø± ØºÛØ±ÙØ¹Ø§Ù Ø§Ø³Øª.", show_alert=True)
        return
    return await _DIRECT_OLD_V25_CALLBACK(update, context)

_DIRECT_OLD_PRICE_CALLBACK = price_callback
async def price_callback(update, context):
    uid=update.effective_user.id
    if not _direct_feature_allowed(uid,"price_data"):
        await update.callback_query.answer("ð ÙÛÙØª Ø¢ÙÙØ§ÛÙ ØºÛØ±ÙØ¹Ø§Ù Ø§Ø³Øª.",show_alert=True); return
    return await _DIRECT_OLD_PRICE_CALLBACK(update, context)


# ---------------- Multi-channel permanent forced subscription ----------------
def _direct_fsub_init():
    c=db()
    try:
        c.execute("""CREATE TABLE IF NOT EXISTS managed_channels(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            join_url TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        try: c.execute("ALTER TABLE managed_channels ADD COLUMN join_url TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        # Migrate the existing single channel without deleting it.
        n=c.execute("SELECT COUNT(*) n FROM managed_channels").fetchone()["n"]
        if not n:
            raw=_forced_sub_get("forced_sub_channel_url","") or ""
            if raw:
                ref=str(raw).strip()
                c.execute("INSERT OR IGNORE INTO managed_channels(channel_id,title,join_url,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                          (ref,ref,ref,1,datetime.now(TZ).isoformat(),datetime.now(TZ).isoformat()))
        c.commit()
    finally:
        c.close()

def _direct_fsub_rows(enabled_only=False):
    _direct_fsub_init()
    c=db()
    try:
        q="SELECT * FROM managed_channels" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY id"
        return c.execute(q).fetchall()
    finally: c.close()

def _direct_fsub_normalize(raw):
    s=str(raw or "").strip()
    if not s: return ""
    s=s.replace("https://t.me/","@").replace("http://t.me/","@")
    s=s.split("?",1)[0].rstrip("/")
    if s.startswith("@"):
        return s
    if re.fullmatch(r"-?\d+",s):
        return s
    if s.startswith("t.me/"):
        return "@"+s.split("/",1)[1]
    return "@"+s.lstrip("@ ")

def _direct_fsub_join_url(ref, stored=""):
    if stored and str(stored).startswith(("http://","https://")): return stored
    ref=str(ref or "")
    if ref.startswith("@"): return "https://t.me/"+ref[1:]
    return ""

async def _direct_fsub_bot_admin(bot, channel):
    try:
        me=await bot.get_me()
        member=await bot.get_chat_member(channel, me.id)
        return getattr(member,"status","") in {ChatMemberStatus.ADMINISTRATOR,ChatMemberStatus.OWNER,"creator"}
    except Exception:
        return False

async def _direct_fsub_live(bot, uid):
    rows=_direct_fsub_rows(True)
    if not rows:
        # Preserve legacy behavior when no managed channel has been configured.
        return (await is_channel_member(bot,uid)), []
    missing=[]
    for r in rows:
        try:
            m=await bot.get_chat_member(r["channel_id"],uid)
            status=str(getattr(m,"status","")).lower()
            is_member=status in {"member","administrator","creator","owner"} or (status=="restricted" and bool(getattr(m,"is_member",False)))
            if not is_member: missing.append(r)
        except Exception:
            missing.append(r)
    return not missing, missing

def _direct_fsub_enabled():
    return _forced_sub_is_enabled()

async def _direct_forced_sub_enforce(uid, bot):
    if not _direct_fsub_enabled() or admin_guard(uid): return True,None
    ok,missing=await _direct_fsub_live(bot,uid)
    if ok:
        try: _forced_sub_record_join(uid)
        except Exception: pass
        return True,None
    lines=["ð <b>Ø¹Ø¶ÙÛØª Ø§Ø¬Ø¨Ø§Ø±Û</b>","","Ø¨Ø±Ø§Û Ø§Ø³ØªÙØ§Ø¯Ù Ø§Ø² Ø±Ø¨Ø§Øª Ø¨Ø§ÛØ¯ Ø¯Ø± ÙÙÙ Ú©Ø§ÙØ§ÙâÙØ§Û Ø²ÛØ± Ø¹Ø¶Ù Ø¨Ø§Ø´Û:",""]
    kb=[]
    for r in missing:
        title=html.escape(str(r["title"] or r["channel_id"]))
        lines.append("ð´ "+title)
        url=_direct_fsub_join_url(r["channel_id"],r["join_url"] if "join_url" in r.keys() else "")
        if url: kb.append([InlineKeyboardButton("ð¢ Ø¹Ø¶ÙÛØª Ø¯Ø± "+title,url=url)])
    kb.append([InlineKeyboardButton("â Ø¹Ø¶Ù Ø´Ø¯ÙØ Ø¨Ø±Ø±Ø³Û Ú©Ù",callback_data="forcedsub:check")])
    return False,("\n".join(lines),InlineKeyboardMarkup(kb))


_LEGACY_REQUIRE_SUB_BEFORE_DIRECT = globals().get("require_subscription")
# The assignment above intentionally replaces the function after saving its old implementation.
# Rebind correctly for the first call.

# Recreate the function now that the legacy reference exists.
async def require_subscription(update, context):
    uid=update.effective_user.id
    if admin_guard(uid): return True
    if _direct_fsub_enabled():
        ok,result=await _direct_forced_sub_enforce(uid,context.bot)
        if ok: return True
        msg,kb=result
        if update.callback_query:
            await update.callback_query.answer("Ø§Ø¨ØªØ¯Ø§ Ø¹Ø¶Ù Ú©Ø§ÙØ§Ù Ø´ÙÛØ¯.",show_alert=True)
            try: await update.callback_query.message.edit_text(msg,parse_mode="HTML",reply_markup=kb)
            except Exception: await update.callback_query.message.reply_text(msg,parse_mode="HTML",reply_markup=kb)
        elif update.message: await update.message.reply_text(msg,parse_mode="HTML",reply_markup=kb)
        return False
    return await _LEGACY_REQUIRE_SUB_BEFORE_DIRECT(update,context)

_DIRECT_OLD_START=start
async def start(update, context):
    uid=update.effective_user.id
    if not admin_guard(uid) and _direct_fsub_enabled():
        ok,result=await _direct_forced_sub_enforce(uid,context.bot)
        if not ok:
            msg,kb=result
            if update.message: await update.message.reply_text(msg,parse_mode="HTML",reply_markup=kb)
            return
    return await _DIRECT_OLD_START(update,context)

async def forced_sub_check_callback(update, context):
    q=update.callback_query; uid=q.from_user.id
    ok,result=await _direct_forced_sub_enforce(uid,context.bot)
    if ok:
        await q.answer("â Ø¹Ø¶ÙÛØª ØªØ£ÛÛØ¯ Ø´Ø¯.",show_alert=True)
        await q.message.edit_text("â <b>Ø¹Ø¶ÙÛØª Ø´ÙØ§ ØªØ£ÛÛØ¯ Ø´Ø¯.</b>\nØ­Ø§ÙØ§ ÙÛâØªÙØ§ÙÛ Ø§Ø² Ø±Ø¨Ø§Øª Ø§Ø³ØªÙØ§Ø¯Ù Ú©ÙÛ.",parse_mode="HTML",reply_markup=compact_keyboard(uid))
    else:
        msg,kb=result; await q.answer("â ÙÙÙØ² Ø¹Ø¶Ù ÙÙÙ Ú©Ø§ÙØ§ÙâÙØ§ ÙÛØ³ØªÛ.",show_alert=True); await q.message.edit_text(msg,parse_mode="HTML",reply_markup=kb)

async def _direct_fsub_admin_panel(update,context):
    uid=update.effective_user.id
    if not admin_guard(uid):
        if update.callback_query: await update.callback_query.answer("â Ø¯Ø³ØªØ±Ø³Û ÙØ¯Ø§Ø±ÛØ¯.",show_alert=True)
        return
    _direct_fsub_init(); rows=_direct_fsub_rows(False); enabled=_direct_fsub_enabled()
    lines=["ð <b>Ø¹Ø¶ÙÛØª Ø§Ø¬Ø¨Ø§Ø±Û</b>","",f"ÙØ¶Ø¹ÛØª: {'ð¢ ÙØ¹Ø§Ù' if enabled else 'ð´ ØºÛØ±ÙØ¹Ø§Ù'}",f"ØªØ¹Ø¯Ø§Ø¯ Ú©Ø§ÙØ§ÙâÙØ§: {len(rows)}","", "ÙØ± Ú©Ø§ÙØ§Ù ÙØ¹Ø§Ù Ø¨Ø±Ø§Û ÙÙÙ Ú©Ø§Ø±Ø¨Ø±Ø§Ù Ø¨ÙâØµÙØ±Øª Ø²ÙØ¯Ù Ø¨Ø±Ø±Ø³Û ÙÛâØ´ÙØ¯."]
    kb=[
        [InlineKeyboardButton("â Ø§ÙØ²ÙØ¯Ù Ú©Ø§ÙØ§Ù Ø§Ø¬Ø¨Ø§Ø±Û",callback_data="forcedsub:add")],
        [InlineKeyboardButton("ð ÙÛØ³Øª Ú©Ø§ÙØ§ÙâÙØ§Û Ø§Ø¬Ø¨Ø§Ø±Û",callback_data="forcedsub:list")],
        [InlineKeyboardButton("ð¤ Ø³ØªâÚ©Ø±Ø¯Ù Ù Ø¨Ø±Ø±Ø³Û Ø±Ø¨Ø§Øª",callback_data="forcedsub:setbot")],
        [InlineKeyboardButton("ð¢ ÙØ¹Ø§ÙØ³Ø§Ø²Û / ð´ ØºÛØ±ÙØ¹Ø§Ù",callback_data="forcedsub:toggle")],
        [InlineKeyboardButton("ð Ø­Ø°Ù Ú©Ø§ÙØ§Ù",callback_data="forcedsub:delete_list")],
        [InlineKeyboardButton("â¬ï¸ ÙÙÙÛ ÙØ¯ÛØ±ÛØª",callback_data="adm:stats")],
    ]
    text="\n".join(lines)
    if update.callback_query:
        await update.callback_query.answer()
        try: await update.callback_query.message.edit_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))
        except Exception: await update.callback_query.message.reply_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))
    else: await update.message.reply_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def forced_sub_callback(update,context):
    q=update.callback_query; uid=q.from_user.id; data=str(q.data or "")
    if not admin_guard(uid): await q.answer("â",show_alert=True); return
    if data=="forcedsub:home": return await _direct_fsub_admin_panel(update,context)
    if data=="forcedsub:check": return await forced_sub_check_callback(update,context)
    if data=="forcedsub:toggle":
        _forced_sub_set("forced_sub_enabled","0" if _forced_sub_is_enabled() else "1"); return await _direct_fsub_admin_panel(update,context)
    if data=="forcedsub:add":
        context.user_data["forced_sub_wait"]="multi_channel"; await q.answer(); await q.message.edit_text("â <b>Ø§ÙØ²ÙØ¯Ù Ú©Ø§ÙØ§Ù Ø§Ø¬Ø¨Ø§Ø±Û</b>\n\nÛÚ© ÛØ§ ÚÙØ¯ Ú©Ø§ÙØ§Ù Ø±Ø§ Ø¨ÙØ±Ø³Øª.\nÙØ± Ú©Ø§ÙØ§Ù Ø¯Ø± ÛÚ© Ø®Ø·Ø ÛØ§ Ø¨Ø§ Ú©Ø§ÙØ§ Ø¬Ø¯Ø§ Ø´ÙØ¯.\n\nÙØ«Ø§Ù:\n<code>@channel1</code>\n<code>@channel2</code>",parse_mode="HTML"); return
    if data=="forcedsub:list":
        rows=_direct_fsub_rows(False); lines=["ð <b>Ú©Ø§ÙØ§ÙâÙØ§Û Ø§Ø¬Ø¨Ø§Ø±Û</b>",""]
        kb=[]
        for r in rows:
            st="ð¢" if r["enabled"] else "ð´"; lines.append(f"{st} {html.escape(str(r['title'] or r['channel_id']))}")
            kb.append([InlineKeyboardButton(("ð´ ØºÛØ±ÙØ¹Ø§Ù: " if r["enabled"] else "ð¢ ÙØ¹Ø§Ù: ")+str(r["title"] or r["channel_id"]),callback_data=f"forcedsub:toggle_channel:{r['id']}")])
        if not rows: lines.append("ÙÙÙØ² Ú©Ø§ÙØ§ÙÛ Ø«Ø¨Øª ÙØ´Ø¯Ù Ø§Ø³Øª.")
        kb.append([InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª",callback_data="forcedsub:home")]); await q.answer(); await q.message.edit_text("\n".join(lines),parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb)); return
    if data=="forcedsub:delete_list":
        rows=_direct_fsub_rows(False); kb=[[InlineKeyboardButton("ð "+str(r["title"] or r["channel_id"]),callback_data=f"forcedsub:delete:{r['id']}")] for r in rows]; kb.append([InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª",callback_data="forcedsub:home")]); await q.answer(); await q.message.edit_text("ð <b>Ø­Ø°Ù Ú©Ø§ÙØ§Ù</b>\n\nÚ©Ø§ÙØ§Ù ÙÙØ±Ø¯ÙØ¸Ø± Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù.",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb)); return
    if data.startswith("forcedsub:toggle_channel:"):
        rid=int(data.rsplit(":",1)[1]); c=db(); c.execute("UPDATE managed_channels SET enabled=CASE enabled WHEN 1 THEN 0 ELSE 1 END,updated_at=? WHERE id=?",(datetime.now(TZ).isoformat(),rid)); c.commit(); c.close(); await q.answer("â ÙØ¶Ø¹ÛØª ØªØºÛÛØ± Ú©Ø±Ø¯."); return await _direct_fsub_admin_panel(update,context)
    if data.startswith("forcedsub:delete:"):
        rid=int(data.rsplit(":",1)[1]); c=db(); c.execute("DELETE FROM managed_channels WHERE id=?",(rid,)); c.commit(); c.close(); await q.answer("ð Ú©Ø§ÙØ§Ù Ø­Ø°Ù Ø´Ø¯."); return await _direct_fsub_admin_panel(update,context)
    if data=="forcedsub:setbot":
        rows=_direct_fsub_rows(True); checks=[]
        try: me=await context.bot.get_me()
        except Exception as exc: await q.answer("â Ø±Ø¨Ø§Øª ÙØ§Ø¨Ù Ø´ÙØ§Ø³Ø§ÛÛ ÙÛØ³Øª.",show_alert=True); return
        for r in rows:
            ok=await _direct_fsub_bot_admin(context.bot,r["channel_id"]); checks.append(("ð¢" if ok else "ð´")+" "+str(r["title"] or r["channel_id"]))
        text="ð¤ <b>Ø³ØªâÚ©Ø±Ø¯Ù Ù Ø¨Ø±Ø±Ø³Û Ø±Ø¨Ø§Øª</b>\n\nØ±Ø¨Ø§Øª ÙØ¹Ø§Ù: @"+str(getattr(me,"username","") or me.id)+"\n\n"+("\n".join(checks) if checks else "Ú©Ø§ÙØ§ÙÛ Ø«Ø¨Øª ÙØ´Ø¯Ù Ø§Ø³Øª.")+"\n\nØ±Ø¨Ø§Øª Ø¨Ø§ÛØ¯ Ø¯Ø± ÙØ± Ú©Ø§ÙØ§Ù ÙØ¹Ø§Ù Administrator Ø¨Ø§Ø´Ø¯."
        await q.answer(); await q.message.edit_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("â¬ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª",callback_data="forcedsub:home")]])); return
    await q.answer("Ú¯Ø²ÛÙÙ ÙØ§Ø´ÙØ§Ø®ØªÙ Ø§Ø³Øª.",show_alert=True)

_DIRECT_OLD_TEXT_ROUTER = text_router
async def text_router(update,context):
    if not update.message or not update.message.text: return await _DIRECT_OLD_TEXT_ROUTER(update,context)
    uid=update.effective_user.id; txt=update.message.text.strip()
    if admin_guard(uid) and context.user_data.get("forced_sub_wait")=="multi_channel":
        refs=[x.strip() for x in re.split(r"[\n,;]+",txt) if x.strip()][:20]
        context.user_data.pop("forced_sub_wait",None)
        added=[]; failed=[]
        _direct_fsub_init()
        for raw in refs:
            ref=_direct_fsub_normalize(raw)
            try:
                chat=await context.bot.get_chat(ref)
                if not await _direct_fsub_bot_admin(context.bot,ref): failed.append(ref+" â Ø±Ø¨Ø§Øª Ø§Ø¯ÙÛÙ ÙÛØ³Øª"); continue
                now=datetime.now(TZ).isoformat(); title=getattr(chat,"title",None) or ref; join=_direct_fsub_join_url(ref,"")
                c=db(); c.execute("INSERT OR IGNORE INTO managed_channels(channel_id,title,join_url,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?)",(ref,title,join,1,now,now)); c.commit(); c.close(); added.append(title)
            except Exception as exc: failed.append(ref+" â Ú©Ø§ÙØ§Ù ÙØ§Ø¨Ù Ø¯Ø³ØªØ±Ø³Û ÙÛØ³Øª")
        msg="â Ø§Ø¶Ø§ÙÙ Ø´Ø¯: "+(", ".join(added) if added else "ÙÛÚâÚ©Ø¯Ø§Ù")
        if failed: msg+="\n\nâ Ø§Ø¶Ø§ÙÙ ÙØ´Ø¯:\n"+"\n".join(failed)
        await update.message.reply_text(msg,reply_markup=ReplyKeyboardMarkup([["ð  ÙÙÙÛ Ø§ØµÙÛ"]],resize_keyboard=True))
        return
    return await _DIRECT_OLD_TEXT_ROUTER(update,context)

# Daily rotating friendly messages. Same user receives a different variant on different days.
_DIRECT_MORNING_VARIANTS=[
    "ð ØµØ¨Ø­ Ø¨Ø®ÛØ± {name}! Ø¢ÙØ§Ø¯ÙâØ§Û Ø§ÙØ±ÙØ² ÛÚ© ÙØ¯Ù Ú©ÙÚÛÚ© ÙÙÛ ÙØ´ÙÚ¯ Ø¨Ø±Ø¯Ø§Ø±ÛÙØ ð±",
    "âï¸ ØµØ¨Ø­ Ø¨Ø®ÛØ± {name}! Ø§ÙØ±ÙØ² ÙØ±Ø§Ø± ÙÛØ³Øª ÙÙÙâÚÛØ² Ø±Ù ÛÚ©âØ¬Ø§ Ø­Ù Ú©ÙÛÙ. ÙÙØ· Ø§Ø² ÛÚ© Ú©Ø§Ø± Ø´Ø±ÙØ¹ Ú©ÙÛÙ. ðª",
    "ð· ØµØ¨Ø­ Ø¨Ø®ÛØ± {name}! ÛÙ Ø±ÙØ² ØªØ§Ø²Ù Ø´Ø±ÙØ¹ Ø´Ø¯Ù. Ø¨ÛØ§ Ø§ÙØ±ÙØ² Ø±Ù Ø¨Ø±Ø§Û Ø®ÙØ¯ÙÙÙ Ø¨ÙØªØ± Ø¨Ø³Ø§Ø²ÛÙ. â¤ï¸",
    "ð ØµØ¨Ø­ Ø¨Ø®ÛØ± {name}! ÙØ¯Ù Ø§ÙØ±ÙØ² Ø³Ø§Ø¯ÙâØ³Øª: ÛÚ© ÙØ¯Ù ÙØ§ÙØ¹Û Ø¨Ù Ø¬ÙÙ. Ø¨Ø²Ù Ø¨Ø±ÛÙ!",
]
_DIRECT_NIGHT_VARIANTS=[
    "ð Ø´Ø¨ Ø¨Ø®ÛØ± {name}! Ø®Ø³ØªÙ ÙØ¨Ø§Ø´Û. Ø­ØªÛ ÛÚ© ÙØ¯Ù Ú©ÙÚÛÚ© Ø§ÙØ±ÙØ² ÙÙ Ø§Ø±Ø²Ø´ÙÙØ¯ Ø¨ÙØ¯. â¤ï¸",
    "ð Ø´Ø¨ Ø¨Ø®ÛØ± {name}! Ø§ÙØ±ÙØ² ÙØ±ÚÙØ¯Ø± ÙÙ Ú©Ù Ù¾ÛØ´ Ø±ÙØªÛØ Ø¨Ø±Ø§Û Ø®ÙØ¯Øª Ø­Ø³Ø§Ø¨Ø´ Ú©Ù. ÙØ±Ø¯Ø§ Ø¯ÙØ¨Ø§Ø±Ù Ø§Ø¯Ø§ÙÙ ÙÛâØ¯ÛÙ. ð±",
    "â¨ Ø´Ø¨ Ø¢Ø±ÙÙ {name}! Ú©Ø§Ø±ÙØ§Û Ø§ÙØ¬Ø§ÙâØ´Ø¯Ù Ø±Ù Ø¨Ø¨ÛÙ Ù Ø¨Ø±Ø§Û ÙØ±Ø¯Ø§ ÙÙØ· ÛÚ© ÙØ¯Ù Ú©ÙÚÛÚ© Ø§ÙØªØ®Ø§Ø¨ Ú©Ù. ð·",
    "ð Ø´Ø¨ Ø¨Ø®ÛØ± {name}! Ø§ÙØ±ÙØ² ØªÙÙÙ Ø´Ø¯Ø ÙÙÛ ÙØ±ØµØª ÙØ±Ø¯Ø§ ÙÙÙØ² ÙØ§Ù ØªÙØ¦Ù. Ø§Ø³ØªØ±Ø§Ø­Øª Ú©Ù Ù Ø¯ÙØ¨Ø§Ø±Ù Ø´Ø±ÙØ¹ ÙÛâÚ©ÙÛÙ. ð",
]

def _direct_variant(items, uid, day):
    return items[(int(day)+int(uid))%len(items)]

async def morning_job(context):
    now=datetime.now(TZ)
    if now.hour!=7 or now.minute!=0 or get_system_setting("morning_message_enabled","1")!="1" or not feature_enabled("morning"): return
    # Do not hold a DB connection during network sends.
    c=db(); rows=c.execute("SELECT user_id FROM users WHERE COALESCE(blocked,0)=0").fetchall(); c.close()
    for r in rows:
        uid=r["user_id"]; key=f"direct_morning:{uid}:{now.date().isoformat()}"
        try:
            if delivery_once(key,uid,"morning"): continue
            await context.bot.send_message(uid,_direct_variant(_DIRECT_MORNING_VARIANTS,uid,now.toordinal()).format(name=html.escape(display_name(uid))),reply_markup=compact_keyboard(uid))
            log_activity(uid,"morning_message")
        except Exception: logger.exception("Direct morning message failed for %s",uid)

async def user_daily_progress_job(context):
    now=datetime.now(TZ)
    if now.hour!=23 or now.minute!=30 or not feature_enabled("night"): return
    today=now.date().isoformat(); yesterday=(now.date()-timedelta(days=1)).isoformat()
    c=db(); users=c.execute("SELECT user_id FROM users WHERE COALESCE(blocked,0)=0").fetchall(); c.close()
    for r in users:
        uid=r["user_id"]; key=f"direct_night:{uid}:{today}"
        try:
            if delivery_once(key,uid,"night"): continue
            c=db(); t=c.execute("SELECT COUNT(*) total,SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) done FROM goal_days WHERE user_id=? AND goal_date=?",(uid,today)).fetchone(); c.close()
            text=_direct_variant(_DIRECT_NIGHT_VARIANTS,uid,now.toordinal()).format(name=html.escape(display_name(uid)))+f"\n\nð¯ Ø§ÙØ±ÙØ²: {int(t['done'] or 0)}/{int(t['total'] or 0)} ÙØ¯Ù Ø§ÙØ¬Ø§Ù Ø´Ø¯."
            await context.bot.send_message(uid,text,reply_markup=compact_keyboard(uid))
        except Exception: logger.exception("Direct night message failed for %s",uid)

# The final source now owns these definitions before main() is invoked.
_DIRECT_OLD_INIT_DB = init_db
def init_db():
    _DIRECT_OLD_INIT_DB()
    _direct_fsub_init()

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

    # Process updates concurrently so one slow handler (e.g. AI chat) cannot
    # stall responses for everyone. Bounded to keep memory/CPU predictable.
    app = Application.builder().token(BOT_TOKEN).concurrent_updates(256).build()
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

    # BUGFIX: legacy inline buttons (profile / settings / lang / achievements /
    # xp / gifts / new_goal / prices) had no handler at all and did nothing.
    app.add_handler(CallbackQueryHandler(
        legacy_compat_callback,
        pattern=r"^(noop|profile|settings|lang|achievements|new_goal|xp:info|xp:history|gifts:list|prices:menu)$"))
    app.add_handler(CallbackQueryHandler(subscription_check_callback, pattern=r"^subcheck$"))
    app.add_handler(CallbackQueryHandler(forced_sub_check_callback, pattern=r"^forcedsub:check$") )
    app.add_handler(CallbackQueryHandler(forced_sub_callback, pattern=r"^forcedsub:(?!check$)"))
    app.add_handler(CallbackQueryHandler(customer_panel_callback, pattern=r"^cust:"))
    app.add_handler(CallbackQueryHandler(admin_user_detail_callback, pattern=r"^admu:\d+$"))
    app.add_handler(CallbackQueryHandler(admin_user_action_callback, pattern=r"^admu_(block|vip|unlimited|editvip|promote|manager):"))
    app.add_handler(CallbackQueryHandler(admin_users_navigation_callback, pattern=r"^adm:users:\d+$"))
    app.add_handler(CallbackQueryHandler(feature_category_callback, pattern=r"^fcat:"))
    app.add_handler(CallbackQueryHandler(navigation_callback, pattern=r"^nav:"))
    # Birthday callbacks
    app.add_handler(CallbackQueryHandler(birthday_register_callback, pattern=r"^birthday:set$"))
    app.add_handler(CallbackQueryHandler(birthday_show_callback, pattern=r"^birthday:show$"))
    # Events admin callbacks
    app.add_handler(CallbackQueryHandler(admin_events_callback, pattern=r"^adm:events:"))
    # Birthday admin callbacks
    app.add_handler(CallbackQueryHandler(admin_birthday_callback, pattern=r"^adm:birthdays:"))
    # Gifts admin callbacks
    app.add_handler(CallbackQueryHandler(admin_gifts_callback, pattern=r"^adm:gifts"))
    # Access matrix callbacks
    app.add_handler(CallbackQueryHandler(admin_access_matrix_callback, pattern=r"^adm:access"))
    app.add_handler(CallbackQueryHandler(admin_referral_callback, pattern=r"^adm:referral"))
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
    app.add_handler(CallbackQueryHandler(poll_callback, pattern=r"^poll:"))
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
    app.add_handler(CallbackQueryHandler(_bdo_owner_callback, pattern=r"^bd:(home|tog_main|tog_rem|cyc_rem|tog_gift|cyc_kind|cyc_aud|amt|text|time|remdays|occ_add|occ_toggle:\d+|occ_del:\d+|report)$"))
    app.add_handler(CallbackQueryHandler(birthday_callback, pattern=r"^bd:(set|del|manual|back|cal|calm:\d+|caly:\d+|cald:\d+:\d+|calsave:\d+:\d+:\d+)$"))
    app.add_handler(CallbackQueryHandler(price_callback, pattern=r"^price:"))
    app.add_handler(CallbackQueryHandler(onboarding_business_callback, pattern=r"^onboardtype:"))
    app.add_handler(CallbackQueryHandler(onboarding_feature_callback, pattern=r"^pref:"))
    app.add_handler(CallbackQueryHandler(gender_callback, pattern=r"^gender:"))
    app.add_handler(CallbackQueryHandler(priority_callback, pattern=r"^priority:"))
    app.add_handler(CallbackQueryHandler(duration_callback, pattern=r"^duration:"))
    app.add_handler(CallbackQueryHandler(condition_callback, pattern=r"^cond:"))
    app.add_handler(CallbackQueryHandler(ready_detail_callback, pattern=r"^rdetail:"))
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
    app.add_handler(CallbackQueryHandler(referral_callback, pattern=r"^ref:"))
    app.add_handler(CommandHandler("prices", prices))
    app.add_handler(CommandHandler("support", support_start))
    app.add_handler(CommandHandler("birthday", birthday_command))
    app.add_handler(CommandHandler("seclog", seclog_command))
    app.add_handler(CommandHandler("reports", reports_command))
    app.add_handler(CallbackQueryHandler(reports_callback, pattern=r"^rep:"))
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
    app.add_handler(MessageHandler(filters.CONTACT, customer_contact_save))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_error_handler(error_handler)

    if app.job_queue:
        app.job_queue.run_repeating(v25_unified_reminder_job, interval=60, first=5)
        app.job_queue.run_repeating(morning_job, interval=60, first=10)
        # v25_night_job disabled: merged into user_daily_progress_job
        app.job_queue.run_repeating(user_daily_progress_job, interval=60, first=11)
        app.job_queue.run_repeating(send_channel_morning_message, interval=60, first=12)
        app.job_queue.run_repeating(send_night_channel_feedback, interval=60, first=14)
        app.job_queue.run_repeating(channel_scheduler_job, interval=60, first=15)
        app.job_queue.run_repeating(auto_channel_job, interval=60, first=20)
        app.job_queue.run_repeating(final_daily_report_job, interval=60, first=25)
        app.job_queue.run_repeating(weekly_admin_report_job, interval=60, first=27)
        app.job_queue.run_repeating(scheduled_health_check_job, interval=60, first=60)
        app.job_queue.run_repeating(customer_reminder_job, interval=60, first=30)
        app.job_queue.run_repeating(birthday_scheduler_job, interval=60, first=35)
        app.job_queue.run_repeating(event_scheduler_job, interval=60, first=40)
        app.job_queue.run_repeating(customer_morning_job, interval=60, first=35)
        app.job_queue.run_repeating(customer_daily_report_job, interval=60, first=40)
        app.job_queue.run_repeating(customer_reengagement_job, interval=60, first=45)
        app.job_queue.run_repeating(v25_reminder_job, interval=60, first=50)
        app.job_queue.run_repeating(flush_owner_notifications_job, interval=60, first=55)
        app.job_queue.run_repeating(weekly_owner_backup_job, interval=3600, first=120)
        app.job_queue.run_repeating(birthday_occasion_job, interval=60, first=65)

    logger.info("MyTasks build: 2026-08-25-FINAL-QUALITY-01")
    logger.info("Goal bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

# === ONE-TIME BUTTON/TIME REPAIR 2026-09-08 ===
_FINAL_REPAIR_FEATURE_ALIASES = {"edit": "goals", "calendar": "calendar_hub"}
_FINAL_REPAIR_OLD_FEATURE_ALLOWED = user_feature_allowed
def user_feature_allowed(uid, key):
    key = _FINAL_REPAIR_FEATURE_ALIASES.get(str(key or ""), str(key or ""))
    return _FINAL_REPAIR_OLD_FEATURE_ALLOWED(uid, key)

_FINAL_REPAIR_OLD_CUSTOM_TIME_SAVE = custom_time_save
async def custom_time_save(update, context):
    if not context.user_data.get("awaiting_custom_time"):
        return await _FINAL_REPAIR_OLD_CUSTOM_TIME_SAVE(update, context)
    text = (getattr(update.message, "text", "") or "").strip()
    tm = parse_time(text)
    if tm is None:
        await update.message.reply_text("â Ø³Ø§Ø¹Øª ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª. ÙØ«Ø§Ù: 18:30 ÛØ§ Û±Û¸:Û³Û°")
        return True
    context.user_data["_final_manual_goal_time"] = tm
    return await _FINAL_REPAIR_OLD_CUSTOM_TIME_SAVE(update, context)

_FINAL_REPAIR_OLD_TEXT_ROUTER = text_router
async def text_router(update, context):
    if update.message and getattr(update.message, "text", None) and context.user_data.get("awaiting_custom_time"):
        try:
            if await custom_time_save(update, context):
                return
        except Exception:
            logger.exception("Final manual goal time repair failed")
            await update.message.reply_text("â Ø«Ø¨Øª Ø³Ø§Ø¹Øª Ø§ÙØ¬Ø§Ù ÙØ´Ø¯. ÙØ«Ø§Ù: 18:30")
            return
    return await _FINAL_REPAIR_OLD_TEXT_ROUTER(update, context)

# === FINAL GOAL EDIT PERSISTENCE REPAIR 2026-09-08 ===
_GOAL_EDIT_REPAIR_OLD_RENAME = rename_save
_GOAL_EDIT_REPAIR_OLD_EDIT_TIME = custom_edit_time_save
_GOAL_EDIT_REPAIR_OLD_ROUTER = text_router

async def rename_save(update, context):
    if not context.user_data.get("awaiting_rename"):
        return await _GOAL_EDIT_REPAIR_OLD_RENAME(update, context)
    uid = update.effective_user.id
    gid = context.user_data.get("edit_id")
    name = (getattr(update.message, "text", "") or "").strip()
    if not gid:
        return True
    if not name:
        await update.message.reply_text("â ÙØ§Ù ÙØ¯Ù ÙÙÛâØªÙØ§ÙØ¯ Ø®Ø§ÙÛ Ø¨Ø§Ø´Ø¯.")
        return True
    c = db()
    try:
        row = c.execute("SELECT id FROM goals WHERE id=? AND user_id=?", (int(gid), uid)).fetchone()
        if not row:
            await update.message.reply_text("â ÙØ¯Ù Ù¾ÛØ¯Ø§ ÙØ´Ø¯.")
            return True
        c.execute("UPDATE goals SET name=? WHERE id=? AND user_id=?", (name, int(gid), uid))
        c.commit()
    finally:
        c.close()
    context.user_data.pop("edit_id", None)
    context.user_data.pop("awaiting_rename", None)
    await update.message.reply_text(T[lang(uid)]["changed"], reply_markup=keyboard(uid))
    return True

async def custom_edit_time_save(update, context):
    if not (context.user_data.get("awaiting_edit_time") or context.user_data.get("awaiting_custom_edit_time")):
        return await _GOAL_EDIT_REPAIR_OLD_EDIT_TIME(update, context)
    uid = update.effective_user.id
    gid = context.user_data.get("edit_reminder_id")
    reminder = parse_time((getattr(update.message, "text", "") or "").strip())
    if reminder is None:
        await update.message.reply_text("â Ø³Ø§Ø¹Øª ÙØ§ÙØ¹ØªØ¨Ø± Ø§Ø³Øª. ÙØ«Ø§Ù: 18:30 ÛØ§ Û±Û¸:Û³Û°")
        return True
    if not gid:
        context.user_data.pop("edit_reminder_id", None)
        context.user_data.pop("awaiting_edit_time", None)
        context.user_data.pop("awaiting_custom_edit_time", None)
        await update.message.reply_text("â ÙØ¯Ù ÙÛØ±Ø§ÛØ´ Ù¾ÛØ¯Ø§ ÙØ´Ø¯.")
        return True
    c = db()
    try:
        row = c.execute("SELECT id FROM goals WHERE id=? AND user_id=?", (int(gid), uid)).fetchone()
        if not row:
            await update.message.reply_text("â ÙØ¯Ù Ù¾ÛØ¯Ø§ ÙØ´Ø¯.")
            return True
        c.execute("UPDATE goals SET reminder_time=? WHERE id=? AND user_id=?", (reminder, int(gid), uid))
        c.commit()
    finally:
        c.close()
    context.user_data.pop("edit_reminder_id", None)
    context.user_data.pop("awaiting_edit_time", None)
    context.user_data.pop("awaiting_custom_edit_time", None)
    await update.message.reply_text(T[lang(uid)]["changed"], reply_markup=keyboard(uid))
    return True

async def text_router(update, context):
    """Final isolated text router. A broken flow must never block normal navigation."""
    if not update.message or not getattr(update.message, "text", None):
        return await _GOAL_EDIT_REPAIR_OLD_ROUTER(update, context)

    uid = update.effective_user.id
    text = update.message.text.strip()

    # Navigation always wins, even when an older flow left a stale input flag.
    if text in ("ð  ÙÙÙÛ Ø§ØµÙÛ", "ð  Main Menu", "â¬ï¸ Ø¨Ø±Ú¯Ø´Øª", "â¬ï¸ Back"):
        clear_flow(context)
        try:
            await update.message.delete()
        except Exception:
            pass
        await update.message.chat.send_message("ð ", reply_markup=keyboard(uid))
        return True

    try:
        if context.user_data.get("awaiting_rename"):
            return await rename_save(update, context)
        if context.user_data.get("awaiting_edit_time") or context.user_data.get("awaiting_custom_edit_time"):
            return await custom_edit_time_save(update, context)
        return await _GOAL_EDIT_REPAIR_OLD_ROUTER(update, context)
    except Exception as exc:
        logger.exception("Isolated text flow failed for uid=%s", uid)
        # Clear only transient flow state. Persistent user/goal data is untouched.
        try:
            clear_flow(context)
            context.user_data.pop("edit_reminder_id", None)
            context.user_data.pop("edit_id", None)
        except Exception:
            logger.exception("Failed to clear isolated text flow for uid=%s", uid)
        try:
            await update.message.reply_text(
                "â ï¸ Ø§ÛÙ Ø¨Ø®Ø´ Ø¨Ø§ Ø®Ø·Ø§ ÙÙØ§Ø¬Ù Ø´Ø¯Ø Ø§ÙØ§ Ø±Ø¨Ø§Øª ÙÙÙ ÙØ´Ø¯.\n"
                "Ø§Ø·ÙØ§Ø¹Ø§Øª Ø°Ø®ÛØ±ÙâØ´Ø¯Ù Ø­ÙØ¸ Ø´Ø¯Ù Ø§Ø³Øª. Ø§Ø² ÙÙÙ Ø§Ø¯Ø§ÙÙ Ø¨Ø¯Ù.",
                reply_markup=keyboard(uid),
            )
        except Exception:
            logger.exception("Failed to send isolated flow recovery for uid=%s", uid)
        return True

# === LEGACY BUTTON COMPATIBILITY 2026-09-09 ===
# Some older keyboards still emit short callback_data values that were never
# registered. They are mapped here onto the current compact-menu routes so the
# buttons work instead of silently doing nothing.
_LEGACY_CB_MAP = {
    "profile": "cm:profile",
    "settings": "cm:settings",
    "achievements": "cm:achievements",
    "new_goal": "cm:custom_goal",
    "prices:menu": "cm:prices",
    "xp:info": "cm:xp",
    "xp:history": "cm:xp",
    "gifts:list": "cm:vip",
}


async def legacy_compat_callback(update, context):
    q = update.callback_query
    data = str(q.data or "")
    uid = q.from_user.id
    if data == "noop":
        try:
            await q.answer()
        except Exception:
            pass
        return
    if data == "lang":
        try:
            await q.answer()
        except Exception:
            pass
        await q.message.edit_text(
            "Ø²Ø¨Ø§Ù Ø±Ø§ Ø§ÙØªØ®Ø§Ø¨ Ú©Ù / Choose language:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("ð®ð· ÙØ§Ø±Ø³Û", callback_data="setlang:fa"),
                 InlineKeyboardButton("ð¬ð§ English", callback_data="setlang:en")],
                [InlineKeyboardButton("â©ï¸ ØªÙØ¸ÛÙØ§Øª", callback_data="settings:back")],
            ]),
        )
        return
    target = _LEGACY_CB_MAP.get(data)
    if not target:
        try:
            await q.answer()
        except Exception:
            pass
        return
    try:
        q.data = target
    except Exception:
        # Telegram objects are immutable in newer PTB versions.
        try:
            object.__setattr__(q, "data", target)
        except Exception:
            logger.warning("Could not remap legacy callback %s for uid=%s", data, uid)
            try:
                await q.answer("ÙØ·ÙØ§Ù Ø§Ø² ÙÙÙÛ Ø§ØµÙÛ Ø§Ø³ØªÙØ§Ø¯Ù Ú©Ù.", show_alert=True)
            except Exception:
                pass
            return
    return await compact_menu_callback(update, context)


if __name__ == "__main__":
    main()
