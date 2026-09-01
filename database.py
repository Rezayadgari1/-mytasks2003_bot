"""Database connection, schema management, and CRUD operations.

Extracted from bot.py to improve maintainability.
All functions are pure data-access — no Telegram handler logic.
"""

import os
import sqlite3
import secrets
from contextlib import contextmanager
from datetime import datetime
from config import DB_PATH, DB_BACKUP_PATH, DB_SCHEMA_VERSION, TZ

logger = __import__("logging").getLogger(__name__)


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
        folder = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "backups")
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
        target = os.path.join(folder, f"goals_{stamp}.db")
        src_conn = sqlite3.connect(DB_PATH, timeout=30)
        dst_conn = sqlite3.connect(target, timeout=30)
        with dst_conn:
            src_conn.backup(dst_conn)
        dst_conn.close(); src_conn.close()
        try:
            if os.name == "posix":
                os.chmod(target, 0o600)
        except OSError:
            pass
        files = sorted(
            [os.path.join(folder, x) for x in os.listdir(folder) if x.endswith(".db")],
            key=lambda x: os.path.getmtime(x), reverse=True
        )
        for old in files[keep:]:
            try: os.remove(old)
            except OSError: pass
        return True
    except Exception as e:
        logger.error("Timestamped database backup failed: %s", e)
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
    if version < 26:
        c.execute("""CREATE TABLE IF NOT EXISTS user_channel_membership(
            user_id INTEGER PRIMARY KEY,
            joined_at TEXT,
            left_at TEXT,
            is_member INTEGER DEFAULT 0,
            last_check TEXT
        )""")
        set_schema_version(c, 26)
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


@contextmanager
def db_context():
    """Context manager that guarantees the connection is closed.

    Usage::
        with db_context() as c:
            c.execute("SELECT …")
            c.commit()  # commit is NOT automatic
    """
    c = db()
    try:
        yield c
    finally:
        try:
            c.close()
        except Exception:
            logger.debug("db_context close failed", exc_info=True)


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
    user_cols = {r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()}
    for col, ddl in [("xp", "INTEGER NOT NULL DEFAULT 0"), ("blocked", "INTEGER NOT NULL DEFAULT 0"),
                     ("warnings", "INTEGER NOT NULL DEFAULT 0"), ("vip_until", "TEXT"),
                     ("referrer_id", "INTEGER"), ("referral_code", "TEXT")]:
        if col not in user_cols:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
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
        "ref_daily_limit": "50", "ref_weekly_limit": "200",
        "ref_monthly_limit": "800", "ref_auto_approve": "1",
        "ref_leaderboard_enabled": "1", "ref_campaign_active": "0",
        "ref_custom_invite_text": "👋 من از ربات MyTasks استفاده می‌کنم. تو هم امتحان کن!",
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
    # ── Birthday module ──────────────────────────────────────────────
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
    # ── Events / Occasions ───────────────────────────────────────────
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
    # ── Gift definitions & tracking ──────────────────────────────────
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
    # ── Subscriptions v2 (precise expiry) ────────────────────────────
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
    # ── Access grants (central access control) ───────────────────────
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
    # ── Birthday & event settings ────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS birthday_settings(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")
    now_iso = datetime.now(TZ).isoformat()
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
    for key, (label, status, provider, note) in service_cost_defaults.items():
        c.execute("INSERT OR IGNORE INTO service_costs(key,label,status,provider,note,enabled,updated_at) VALUES(?,?,?,?,?,?,?)", (key, label, status, provider, note, 1, now_iso))
    access_defaults = {
        "customers": "vip",
        "ai": "free", "vip": "free", "reminders": "free", "sports": "free",
        "nutrition": "free", "investing": "free", "self_growth": "free", "morning": "free", "night": "free",
        "auto_publish": "free", "images": "free", "feedback": "free", "referrals": "free",
        "mini_app": "free", "support": "free", "price_data": "free", "approval": "free",
        "goals": "free", "weekly": "free", "stats": "free", "profile": "free", "achievements": "free",
        "settings": "free", "xp": "free", "payments": "off", "maintenance": "off", "test_mode": "free",
        "customer_today": "free", "customer_new_appointment": "free", "customer_customers": "free",
        "customer_calendar": "free", "customer_hours": "free", "customer_reminders": "free",
        "customer_analytics": "free", "customer_loyal": "free", "customer_period": "free",
        "customer_booking_link": "free", "customer_online_booking": "free", "customer_business_settings": "free",
        "birthday": "free", "events": "free", "admin_gifts": "free",
    }
    for key, mode in access_defaults.items():
        c.execute("INSERT OR IGNORE INTO feature_flags(key,enabled,updated_at) VALUES(?,?,?)", (key, 1 if mode != "off" else 0, now_iso))
        c.execute("INSERT OR IGNORE INTO feature_access(key,mode,updated_at) VALUES(?,?,?)", (key, mode, now_iso))

    for key in ["ai", "vip", "reminders", "sports", "nutrition", "investing", "self_growth", "morning", "night", "auto_publish", "images", "feedback", "referrals", "mini_app", "support", "price_data", "approval", "goals", "weekly", "stats", "profile", "achievements", "settings"]:
        c.execute("INSERT OR IGNORE INTO feature_flags(key,enabled,updated_at) VALUES(?,?,?)", (key, 1, now_iso))
    c.execute("INSERT OR IGNORE INTO feature_flags(key,enabled,updated_at) VALUES('payments',0,?)", (now_iso,))
    c.execute("INSERT OR IGNORE INTO feature_flags(key,enabled,updated_at) VALUES('maintenance',0,?)", (now_iso,))
    c.execute("INSERT OR IGNORE INTO feature_flags(key,enabled,updated_at) VALUES('test_mode',1,?)", (now_iso,))
    migrate_database(c)
    c.commit()
    c.close()


# ── Simple CRUD helpers ────────────────────────────────────────────

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
    c.execute("UPDATE users SET referral_code=COALESCE(referral_code,?) WHERE user_id=?", (secrets.token_urlsafe(12), uid))
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
        "SELECT COUNT(*) AS n FROM goal_days WHERE user_id=? AND status='done'", (uid,),
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
        return "🏆 هنوز دستاوردی نداری."
    return "\n".join(f"{labels.get(r['code'], r['code'])} — {r['unlocked_at'][:10]}" for r in rows)
