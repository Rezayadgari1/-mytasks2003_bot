"""Verify scale & reliability: concurrent updates, flood-safe broadcast, weekly owner backup."""
import os, sys, asyncio

os.environ["BOT_TOKEN"] = "123456:TESTTOKEN"
os.environ["DB_PATH"] = "/tmp/scale_goals.db"
os.environ["ADMIN_IDS"] = "1000"
for f in ("/tmp/scale_goals.db", "/tmp/scale_goals.db.backup"):
    if os.path.exists(f):
        os.remove(f)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot
from telegram.error import RetryAfter

print("[1] import OK")
bot.init_db()

# --- concurrent_updates configured in main() ---
import inspect
mainsrc = inspect.getsource(bot.main)
assert "concurrent_updates(256)" in mainsrc, "concurrent updates not enabled"
app_builder = bot.Application.builder().token("123456:TESTTOKEN").concurrent_updates(256).build()
print("[2] concurrent_updates(256) present and builder works")

# --- flood-safe broadcast ---
# seed users + admin flow state
for uid in (11, 22, 33):
    bot.register_user(uid, f"u{uid}")

class U: id = 1000  # master admin
class M:
    text = "پیام همگانی تست"
    async def reply_text(self, *a, **k): self.replied = a and a[0] or (k.get("text"))
class B:
    """Fake bot: first send to user 22 raises RetryAfter, then succeeds."""
    def __init__(self): self.sent = []; self.fail_once = {22}
    async def send_message(self, uid, text, **k):
        if uid in self.fail_once:
            self.fail_once.discard(uid)
            raise RetryAfter(retry_after=0)
        self.sent.append(uid)
class Ctx:
    def __init__(self):
        self.user_data = {"admin_broadcast": True}
        self.bot = B()
        self.error = None
class Upd:
    effective_user = U()
    message = M()

upd = Upd(); m = upd.message; ctx = Ctx()
ok = asyncio.get_event_loop().run_until_complete(bot.admin_broadcast_save(upd, ctx))
assert ok is True
assert sorted(ctx.bot.sent) == [11, 22, 33], ctx.bot.sent   # retried user got it too
assert "2 ناموفق" not in str(m.replied)
print(f"[3] broadcast OK with RetryAfter retry | sent to: {ctx.bot.sent}")

# hard-failure path: user always failing counts as failed, no crash
class B2(B):
    def __init__(self):
        super().__init__()
        self.fail_once = set()
        self.always_fail = {99}
    async def send_message(self, uid, text, **k):
        if uid in self.always_fail:
            raise RuntimeError("blocked")
        self.sent.append(uid)
bot.register_user(99, "u99")
ctx2 = Ctx(); ctx2.bot = B2()
asyncio.get_event_loop().run_until_complete(bot.admin_broadcast_save(upd, ctx2))
assert "1 ناموفق" in str(m.replied), m.replied
print("[4] failed recipients counted & reported")

# --- weekly owner backup: sends once per week, then dedupes ---
os.makedirs("/tmp", exist_ok=True)
open("/tmp/scale_goals.db.backup", "wb").write(b"weekly-backup")
sent_docs = []
class B3:
    async def send_document(self, uid, document=None, filename=None, caption=None):
        sent_docs.append((uid, filename))
class Ctx3:
    class bot:
        pass
ctx3 = Ctx(); ctx3.bot = B3()
asyncio.get_event_loop().run_until_complete(bot.weekly_owner_backup_job(ctx3))
assert len(sent_docs) == 1 and sent_docs[0][0] == 1000, sent_docs
assert sent_docs[0][1].startswith("goals-backup-")
# second call same week: no duplicate
asyncio.get_event_loop().run_until_complete(bot.weekly_owner_backup_job(ctx3))
assert len(sent_docs) == 1
c = bot.db()
wk = c.execute("SELECT value FROM system_settings WHERE key='last_owner_backup_week'").fetchall() if True else []
c.close()
print(f"[5] weekly owner backup delivered once | docs: {sent_docs}")

for f in ("/tmp/scale_goals.db", "/tmp/scale_goals.db.backup"):
    if os.path.exists(f):
        os.remove(f)
print("\n=== SCALE/RELIABILITY TESTS PASSED ===")
