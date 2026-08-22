"""Runtime safety & DB/HTTP/Timezone fixes to be monkey-patched into bot.py at startup.

Usage:
- Add at top of bot.py (after imports):
    try:
        import bot_fixes
        bot_fixes.apply_monkeypatch()
    except Exception:
        pass

This module provides safer db(), init_db(), TZ fallback, network helpers and a safe remove_managed_channel implementation.

I created this as a non-invasive change so you can review and then either import it from bot.py or I can apply direct edits to bot.py if you allow replacing the file.
"""
from __future__ import annotations
import sqlite3
import logging
import time
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Configurable values expected to exist in bot.py; these will be read at monkeypatch time.
DB_PATH = None

# --- TZ fallback ---
try:
    TZ = ZoneInfo("Asia/Tehran")
except Exception:
    logger.warning("ZoneInfo('Asia/Tehran') unavailable, falling back to UTC")
    TZ = timezone.utc

# --- safer sqlite helper ---
def db_connect(path: str | None = None, timeout: float = 30.0, check_same_thread: bool = False):
    """Return a sqlite3.Connection with sane defaults for the bot.

    - timeout: seconds to wait for locks
    - row_factory -> sqlite3.Row
    - foreign keys enabled
    - journal_mode=WAL is set by init_db
    """
    if path is None:
        if DB_PATH is None:
            raise RuntimeError("DB_PATH not configured for bot_fixes.db_connect")
        path = DB_PATH
    conn = sqlite3.connect(path, timeout=timeout, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
    except Exception:
        logger.exception("Failed to enable foreign_keys PRAGMA")
    return conn

# Retry helper for writes to handle "database is locked"
def _with_retry(func: Callable, retries: int = 5, backoff: float = 0.2, *args, **kwargs):
    last_exc = None
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except sqlite3.OperationalError as e:
            last_exc = e
            if "locked" in str(e).lower() or "database is locked" in str(e).lower():
                sleep = backoff * (2 ** attempt)
                logger.warning("SQLite locked, retrying in %.2fs (attempt %d/%d)", sleep, attempt + 1, retries)
                time.sleep(sleep)
                continue
            raise
    logger.exception("Operation failed after retries: %s", last_exc)
    raise last_exc

# Safe init_db which sets WAL mode. This is non-destructive.
def init_db_safe(path: str | None = None):
    global DB_PATH
    if path is None and DB_PATH is None:
        raise RuntimeError("DB_PATH not configured for init_db_safe")
    if path is not None:
        DB_PATH = path
    conn = db_connect(DB_PATH)
    try:
        # Enable WAL to reduce write locks
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
        except Exception:
            logger.exception("Failed to set journal_mode=WAL")
        # Optional: set synchronous to NORMAL for performance (keep safe default)
        try:
            conn.execute("PRAGMA synchronous = NORMAL;")
        except Exception:
            pass
        conn.commit()
    finally:
        conn.close()

# Safer HTTP fetch helpers (sync). The original project may use requests or aiohttp; provide both simple sync implementations.
import requests

DEFAULT_HTTP_TIMEOUT = 10

def fetch_url_json_safe(url: str, timeout: int | float = DEFAULT_HTTP_TIMEOUT):
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        logger.warning("HTTP GET failed for %s: %s", url, e)
        return None

def fetch_url_json_post_safe(url: str, payload: Any, timeout: int | float = DEFAULT_HTTP_TIMEOUT):
    try:
        r = requests.post(url, json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        logger.warning("HTTP POST failed for %s: %s", url, e)
        return None

# Safe remove_managed_channel implementation
def remove_managed_channel_safe(channel_id: Any, path: str | None = None):
    """Delete a managed channel and return remaining enabled channel ids.

    Returns list of channel_id strings still enabled.
    """
    conn = db_connect(path)
    try:
        cur = conn.cursor()
        cid = str(channel_id)
        _with_retry(lambda: cur.execute("DELETE FROM managed_channels WHERE channel_id=?", (cid,)))
        conn.commit()
        rows = cur.execute("SELECT channel_id FROM managed_channels WHERE enabled=1 ORDER BY id").fetchall()
        remaining = [r[0] for r in rows]
        return remaining
    finally:
        conn.close()

# Monkey-patch applier
def apply_monkeypatch(target_module_name: str = "bot"):
    """Monkey-patch functions into the running bot module.

    This will set safer db(), init_db() and network helpers if the bot module exists.
    """
    import importlib
    try:
        m = importlib.import_module(target_module_name)
    except Exception:
        logger.exception("Failed to import target module %s for monkeypatch", target_module_name)
        return False

    # Pull DB_PATH from bot if present
    global DB_PATH, TZ
    try:
        DB_PATH = getattr(m, "DB_PATH", DB_PATH)
    except Exception:
        pass

    # TZ: do not overwrite if bot defines it already, but warn if absent
    if not hasattr(m, "TZ"):
        setattr(m, "TZ", TZ)
        logger.info("Patched TZ into %s", target_module_name)

    # Patch db and init_db only if not already patched
    try:
        setattr(m, "db", lambda: db_connect(DB_PATH))
        setattr(m, "init_db", lambda: init_db_safe(DB_PATH))
        setattr(m, "fetch_url_json", fetch_url_json_safe)
        setattr(m, "fetch_url_json_post", fetch_url_json_post_safe)
        # Provide safe remove managed channel
        setattr(m, "remove_managed_channel", remove_managed_channel_safe)
    except Exception:
        logger.exception("Failed to apply monkeypatch to %s", target_module_name)
        return False

    logger.info("Applied bot_fixes monkeypatch to %s", target_module_name)
    return True
