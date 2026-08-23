"""Verify admin reports layer: error capture, owner notifications, reports menu, backup delivery."""
import os, sys, asyncio, time

os.environ["BOT_TOKEN"] = "123456:TESTTOKEN"
os.environ["DB_PATH"] = "/tmp/rep_goals.db"
os.environ["ADMIN_IDS"] = "1000"          # master owner = min(ADMIN_IDS) = 1000
for f in ("/tmp/rep_goals.db", "/tmp/rep_goals.db.backup"):
    if os.path.exists(f):
        os.remove(f)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot

print("[1] import OK | ADMIN_IDS:", bot.ADMIN_IDS)
bot.init_db()

c = bot.db()
tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
assert {"error_events", "owner_notifications"} <= tables
c.close()
print("[2] error_events + owner_notifications tables created")

# --- error capture via wrapped error_handler ---
class FakeErr(Exception): pass
class U: id = 5555
class M:
    text = "/something"
    async def reply_text(self, *a, **k): pass
class Upd:
    effective_user = U(); message = M(); callback_query = None
class Ctx:
    class bot: pass
    error = FakeErr("boom")
asyncio.get_event_loop().run_until_complete(bot.error_handler(Upd(), Ctx()))
c = bot.db()
r = c.execute("SELECT * FROM error_events ORDER BY id DESC LIMIT 1").fetchone()
n_err = c.execute("SELECT COUNT(*) n FROM error_events").fetchone()["n"]
c.close()
assert n_err == 1 and r["error_type"] == "FakeErr" and r["user_id"] == 5555
print("[3] errors captured to DB |", r["error_type"], "|", r["error_text"])

# --- owner notification on feature toggle by another manager ---
os.environ["ADMIN_IDS"] = "1000,2000"     # 2000 is a non-master manager
import importlib
bot.ADMIN_IDS = bot._parse_admin_ids() or {1000, 2000}
bot.ADMIN_IDS = {1000, 2000}
bot.set_feature("ai", False, 2000)
c = bot.db()
rows = c.execute("SELECT * FROM owner_notifications WHERE sent=0").fetchall()
flag = c.execute("SELECT enabled FROM feature_flags WHERE key='ai'").fetchone()["enabled"]
c.close()
assert rows and "2000" in rows[-1]["text"], rows
assert flag == 0
print("[4] sensitive change queued for owner |", rows[-1]["text"].replace("\n", " ")[:60])

# master toggling must NOT queue a notice
before = len(rows)
bot.set_feature("ai", True, 1000)
c = bot.db(); after = c.execute("SELECT COUNT(*) n FROM owner_notifications").fetchone()["n"]; c.close()
assert after == before, "master toggle should not notify"
print("[5] master toggle does not self-notify")

# --- reports builders return sensible HTML ---
for key in ("users", "finance", "tickets", "xpvip", "errors", "health"):
    txt = bot._REPORT_BUILDERS[key]()
    assert "<b>" in txt and len(txt) > 30, key
print("[6] all 6 report builders OK")

# --- reports callback renders a report in place (edit) with back row ---
class U2: id = 1000
class QMsg:
    def __init__(self): self.edits = []
    async def edit_text(self, text=None, parse_mode=None, reply_markup=None, **k):
        self.edits.append((text, reply_markup))
    async def edit_reply_markup(self, **k): pass
    async def reply_text(self, *a, **k): pass
class Q:
    def __init__(self, data):
        self.data = data; self.from_user = U2(); self.message = QMsg()
    async def answer(self, *a, **k): pass
class Upd2:
    def __init__(self, q): self.callback_query = q; self.effective_user = U2(); self.message = q.message
class Ctx2:
    user_data = {}
    class bot:
        sent = []
        @classmethod
        async def send_document(cls, uid, document=None, filename=None, caption=None):
            cls.sent.append(filename)
q = Q("rep:finance")
asyncio.get_event_loop().run_until_complete(bot.reports_callback(Upd2(q), Ctx2()))
t, kb = q.message.edits[-1]
last = kb.inline_keyboard[-1]
assert "مالی" in t and any(b.callback_data == "rep:menu" for b in last) and any(b.callback_data == "nav:main" for b in last)
print("[7] finance report rendered in place with back row")

# non-admin denied + logged
class U3: id = 9999
q2 = Q("rep:users"); q2.from_user = U3()
u3 = Upd2(q2); u3.effective_user = U3()
asyncio.get_event_loop().run_until_complete(bot.reports_callback(u3, Ctx2()))
c = bot.db()
denied = c.execute("SELECT COUNT(*) n FROM security_events WHERE event='admin_denied' AND user_id=9999").fetchone()["n"]
c.close()
assert denied >= 1
print("[8] non-admin denied & logged")

# backup file delivery
open("/tmp/rep_goals.db.backup", "wb").write(b"backup-bytes-here")
q3 = Q("rep:backup")
asyncio.get_event_loop().run_until_complete(bot.reports_callback(Upd2(q3), Ctx2()))
assert Ctx2.bot.sent == ["goals-backup.db"], Ctx2.bot.sent
print("[9] backup file delivered to admin chat")

for f in ("/tmp/rep_goals.db", "/tmp/rep_goals.db.backup"):
    if os.path.exists(f):
        os.remove(f)
print("\n=== REPORTS LAYER TESTS PASSED ===")
