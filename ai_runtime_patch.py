from pathlib import Path
import re

BOT = Path("/app/bot.py")
s = BOT.read_text(encoding="utf-8")

if "AI_SQLITE_PATCH_V1" not in s:
    start = s.index("async def ai_chat_text(update,context):")
    end = s.index("\n\nasync def build_daily_report():", start)
    new_function = '''# AI_SQLITE_PATCH_V1
async def ai_chat_text(update,context):
    if not context.user_data.get("ai_chat"):
        return False
    uid=update.effective_user.id
    text=update.message.text.strip()
    if text in ("⬅️ برگشت","⬅️ Back","🏠 منوی اصلی","🏠 Main Menu"):
        clear_flow(context)
        await update.message.reply_text("🏠 منوی اصلی",reply_markup=keyboard(uid))
        return True
    api_key=os.environ.get("OPENAI_API_KEY","").strip()
    if not api_key and not omniroute_configured() and not n8n_configured() and not GEMINI_API_KEY:
        clear_flow(context)
        await update.message.reply_text("⚠️ در حال حاضر دستیار هوشمند موقتاً در دسترس نیست.\\nلطفاً کمی بعد دوباره تلاش بفرمایید. 🌷", reply_markup=keyboard(uid))
        return True
    today=datetime.now(TZ).date().isoformat()
    limit=100 if is_vip(uid) else 10
    def _quota_read():
        last_error=None
        for attempt in range(5):
            c=None
            try:
                c=db()
                c.execute("INSERT OR IGNORE INTO user_settings(user_id) VALUES(?)",(uid,))
                return c.execute("SELECT ai_daily_used,ai_used_date FROM user_settings WHERE user_id=?",(uid,)).fetchone()
            except sqlite3.OperationalError as exc:
                last_error=exc
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower(): raise
                time.sleep(0.15*(attempt+1))
            finally:
                if c is not None:
                    try: c.rollback()
                    except Exception: pass
                    try: c.close()
                    except Exception: pass
        raise last_error
    try:
        row=_quota_read()
        used=row["ai_daily_used"] if row and row["ai_used_date"]==today else 0
    except sqlite3.OperationalError as exc:
        logger.exception("AI quota read failed: %s", exc)
        await update.message.reply_text("⚠️ دیتابیس موقتاً مشغول است. دوباره تلاش کن." if lang(uid)=="fa" else "⚠️ The database is temporarily busy. Please try again.", reply_markup=nav_keyboard(uid))
        return True
    if used>=limit:
        await update.message.reply_text("⛔ سهمیه AI امروز تمام شده است." if lang(uid)=="fa" else "⛔ Your AI quota for today is used up.", reply_markup=nav_keyboard(uid))
        return True
    try:
        prompt=("پاسخ کوتاه، مفید، مودبانه و امن به این سوال کاربر بده. اگر موضوع پزشکی یا مالی است، پاسخ عمومی و غیرقطعی نگه دار.\\n\\n"+text)
        answer=ai_text_generate(prompt, max_output_tokens=500, purpose="chat")
        if not answer: raise RuntimeError("No AI provider returned a response")
        quota_saved=False
        last_quota_error=None
        for attempt in range(5):
            c=None
            try:
                c=db()
                c.execute("INSERT OR IGNORE INTO user_settings(user_id) VALUES(?)",(uid,))
                c.execute("UPDATE user_settings SET ai_daily_used=?,ai_used_date=? WHERE user_id=?",(used+1,today,uid))
                c.commit(); quota_saved=True; break
            except sqlite3.OperationalError as exc:
                last_quota_error=exc
                try:
                    if c is not None: c.rollback()
                except Exception: pass
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower(): break
                time.sleep(0.15*(attempt+1))
            finally:
                if c is not None:
                    try: c.close()
                    except Exception: pass
        if not quota_saved: logger.error("AI quota update failed after successful AI response: %s", last_quota_error)
        await update.message.reply_text(answer,reply_markup=nav_keyboard(uid))
    except Exception as e:
        logger.exception("AI chat failed: %s",e)
        clear_flow(context)
        await update.message.reply_text(("⚠️ متأسفانه در حال حاضر پاسخ هوش مصنوعی دریافت نشد. لطفاً کمی بعد دوباره تلاش بفرمایید." if lang(uid)=="fa" else "⚠️ The AI provider did not return a response right now. Please try again in a little while."), reply_markup=keyboard(uid))
    return True
'''
    s = s[:start] + new_function + s[end:]

if "SQLITE_RESILIENT_CONNECTION_V1" not in s:
    start = s.index("def db():")
    end = s.index("\n\ndef init_db():", start)
    new_db = '''# SQLITE_RESILIENT_CONNECTION_V1
class _ResilientSQLiteConnection(sqlite3.Connection):
    @staticmethod
    def _retryable(exc):
        msg=str(exc).lower()
        return "locked" in msg or "busy" in msg
    def execute(self, sql, parameters=()):
        last=None
        for attempt in range(6):
            try: return super().execute(sql, parameters)
            except sqlite3.OperationalError as exc:
                last=exc
                if not self._retryable(exc): raise
                time.sleep(0.15*(attempt+1))
        raise last
    def executemany(self, sql, seq_of_parameters):
        last=None
        for attempt in range(6):
            try: return super().executemany(sql, seq_of_parameters)
            except sqlite3.OperationalError as exc:
                last=exc
                if not self._retryable(exc): raise
                time.sleep(0.15*(attempt+1))
        raise last
    def executescript(self, sql_script):
        last=None
        for attempt in range(6):
            try: return super().executescript(sql_script)
            except sqlite3.OperationalError as exc:
                last=exc
                if not self._retryable(exc): raise
                time.sleep(0.15*(attempt+1))
        raise last
    def commit(self):
        last=None
        for attempt in range(6):
            try: return super().commit()
            except sqlite3.OperationalError as exc:
                last=exc
                if not self._retryable(exc): raise
                time.sleep(0.15*(attempt+1))
        raise last

def db():
    """Open SQLite safely under concurrent Telegram updates."""
    last_error=None
    for delay in (0.0,0.05,0.15,0.35,0.75,1.25):
        if delay: time.sleep(delay)
        c=None
        try:
            c=sqlite3.connect(DB_PATH,timeout=60,check_same_thread=False,factory=_ResilientSQLiteConnection)
            c.row_factory=sqlite3.Row
            c.execute("PRAGMA busy_timeout=60000")
            c.execute("PRAGMA foreign_keys=ON")
            with _DB_PRAGMA_LOCK:
                try: c.execute("PRAGMA journal_mode=WAL")
                except sqlite3.OperationalError: pass
            c.execute("PRAGMA synchronous=NORMAL")
            return c
        except sqlite3.OperationalError as exc:
            last_error=exc
            try:
                if c is not None: c.close()
            except Exception: pass
    raise last_error
'''
    s = s[:start] + new_db + s[end:]

if "SYSTEM_SETTINGS_RESILIENCE_V1" not in s:
    helper = '''# SYSTEM_SETTINGS_RESILIENCE_V1
_SYSTEM_SETTINGS_LOCK=threading.RLock()
def _ensure_system_settings_table():
    c=None
    try:
        c=db()
        with _SYSTEM_SETTINGS_LOCK:
            c.execute("""CREATE TABLE IF NOT EXISTS system_settings(
                key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT ''
            )""")
            c.commit()
    finally:
        if c is not None:
            try: c.close()
            except Exception: pass

'''
    marker="def _forced_sub_get("
    if marker in s: s=s.replace(marker,helper+marker,1)

def replace_function(source,name,new_text):
    pattern=rf"def {re.escape(name)}\(.*?\n(?=def |async def |\Z)"
    m=re.search(pattern,source,flags=re.S)
    if not m: return source
    return source[:m.start()]+new_text.rstrip()+"\n\n"+source[m.end():]

forced_get='''def _forced_sub_get(key, default=""):
    last=None
    for attempt in range(5):
        c=None
        try:
            _ensure_system_settings_table()
            c=db(); r=c.execute("SELECT value FROM system_settings WHERE key=?",(key,)).fetchone()
            return r["value"] if r else default
        except sqlite3.OperationalError as exc:
            last=exc
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower(): break
            time.sleep(0.15*(attempt+1))
        finally:
            if c is not None:
                try: c.close()
                except Exception: pass
    logger.error("Forced subscription setting read failed for %s: %s",key,last)
    return default
'''
forced_set='''def _forced_sub_set(key, value):
    last=None
    for attempt in range(5):
        c=None
        try:
            _ensure_system_settings_table()
            c=db(); now_iso=datetime.now(TZ).isoformat()
            c.execute("INSERT OR REPLACE INTO system_settings(key,value,updated_at) VALUES(?,?,?)",(key,str(value),now_iso)); c.commit()
            return True
        except sqlite3.OperationalError as exc:
            last=exc
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower(): break
            try:
                if c is not None: c.rollback()
            except Exception: pass
            time.sleep(0.15*(attempt+1))
        finally:
            if c is not None:
                try: c.close()
                except Exception: pass
    logger.error("Forced subscription setting write failed for %s: %s",key,last)
    return False
'''
generic_get='''def get_system_setting(key, default=""):
    last=None
    for attempt in range(5):
        c=None
        try:
            _ensure_system_settings_table()
            c=db(); r=c.execute("SELECT value FROM system_settings WHERE key=?",(key,)).fetchone()
            return r["value"] if r else default
        except sqlite3.OperationalError as exc:
            last=exc
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower(): break
            time.sleep(0.15*(attempt+1))
        finally:
            if c is not None:
                try: c.close()
                except Exception: pass
    logger.error("System setting read failed for %s: %s",key,last)
    return default
'''
generic_set='''def set_system_setting(key, value, updated_by=None):
    last=None
    for attempt in range(5):
        c=None
        try:
            _ensure_system_settings_table()
            c=db(); c.execute("""INSERT INTO system_settings(key,value,updated_at) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",(key,str(value),datetime.now(TZ).isoformat())); c.commit()
            return True
        except sqlite3.OperationalError as exc:
            last=exc
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower(): break
            try:
                if c is not None: c.rollback()
            except Exception: pass
            time.sleep(0.15*(attempt+1))
        finally:
            if c is not None:
                try: c.close()
                except Exception: pass
    logger.error("System setting write failed for %s: %s",key,last)
    return False
'''
s=replace_function(s,"_forced_sub_get",forced_get)
s=replace_function(s,"_forced_sub_set",forced_set)
s=replace_function(s,"get_system_setting",generic_get)
s=replace_function(s,"set_system_setting",generic_set)

if "GLOBAL_SQLITE_ERROR_RECOVERY_V1" not in s:
    old="release_leaked_connections(context.error)"
    if old in s:
        new='''# GLOBAL_SQLITE_ERROR_RECOVERY_V1
    release_leaked_connections(context.error)
    if isinstance(context.error, sqlite3.OperationalError):
        logger.error("SQLite OperationalError recovered at global boundary: %s",context.error)
        try: _ensure_system_settings_table()
        except Exception: pass'''
        s=s.replace(old,new,1)

BOT.write_text(s,encoding="utf-8")
print("MyTasks resilience patch applied")
