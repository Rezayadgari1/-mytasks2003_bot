"""Verify the new security layer and menu redesign work end-to-end."""
import os, sys, asyncio

os.environ["BOT_TOKEN"] = "123456:TESTTOKEN"
os.environ["DB_PATH"] = "/tmp/sec_goals.db"
for f in ("/tmp/sec_goals.db", "/tmp/sec_goals.db.backup"):
    if os.path.exists(f):
        os.remove(f)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot

print("[1] import OK")

# init_db creates security_events
bot.init_db()
c = bot.db()
tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
assert "security_events" in tables, "security_events table missing"
c.close()
print("[2] security_events table created")

# log_security writes rows
bot.log_security(111, "goal_access_denied", "handler=edit_goal goal=42")
bot.log_security(222, "admin_denied", "handler=admin_command")
c = bot.db()
n = c.execute("SELECT COUNT(*) n FROM security_events").fetchone()["n"]
row = c.execute("SELECT * FROM security_events WHERE user_id=111").fetchone()
c.close()
assert n == 2 and row["event"] == "goal_access_denied"
print("[3] log_security works |", row["event"], row["details"])

# Ownership guard: live 'edit_goal' must refuse a goal owned by someone else
async def run_guard():
    # create goal for owner 777
    bot.add_goal(777, "مالک", "✨ شخصی", None, 2, None)
    gid = bot.get_goals(777)[0]["id"]

    class FakeUser: id = 888
    class FakeMsg: pass
    class FakeQ:
        def __init__(self): self.data = f"edit:{gid}"; self.from_user = FakeUser(); self.answered = None
        async def answer(self, text=None, show_alert=False): self.answered = text
    class FakeUpdate:
        def __init__(self, q): self.callback_query = q; self.effective_user = FakeUser()
    class FakeCtx: pass

    q = FakeQ()
    await bot.edit_goal(FakeUpdate(q), FakeCtx())
    return gid, q

gid, q = asyncio.get_event_loop().run_until_complete(run_guard())
# attacker got an alert instead of the edit screen
assert q.answered and "دسترس" in q.answered, f"expected denial alert, got {q.answered!r}"
# event logged
c = bot.db()
r = c.execute("SELECT * FROM security_events WHERE event='goal_access_denied'").fetchall()
c.close()
assert r, "goal_access_denied not logged"
print("[4] cross-user edit blocked & logged |", r[-1]["details"])

# Owner can still open their own goal (guard passes through)
async def run_owner():
    class U: id = 777
    class M:
        async def edit_text(self, *a, **k): self.called = True
    class Q:
        data = f"edit:{gid}"; from_user = U()
        async def answer(self, *a, **k): pass
    class Upd:
        callback_query = Q(); effective_user = U()
    m = M(); upd = Upd(); upd.callback_query.message = m
    try:
        await bot.edit_goal(upd, object())  # context without .bot -> subscription layer raises
        reached_subscription = False
    except AttributeError:
        reached_subscription = True   # passing the guard means flow continued past it
    return reached_subscription

owner_ok = asyncio.get_event_loop().run_until_complete(run_owner())
assert owner_ok, "owner could not open own goal"
print("[5] owner access still works")

# Menu redesign checks
kb = bot._compact_root_inline(999)
labels_fa = [b.text for row in kb.inline_keyboard for b in row]
assert labels_fa[0] == "🎯 برنامه من" and len(kb.inline_keyboard) == 4
sec_kb = bot._compact_menu_keyboard(999, "support")
last = sec_kb.inline_keyboard[-1]
texts = [b.text for b in last]
assert any("بازگشت" in t for t in texts) and any("منوی اصلی" in t for t in texts), texts
print("[6] menu redesign OK | root rows:", len(kb.inline_keyboard), "| back-row:", texts)

txt = bot._root_menu_text(999)
assert "<b>منوی اصلی</b>" in txt and "📅" in txt
print("[7] _root_menu_text OK ->", txt.split(chr(10))[2][:40], "...")

for f in ("/tmp/sec_goals.db", "/tmp/sec_goals.db.backup"):
    if os.path.exists(f):
        os.remove(f)
print("\n=== ALL NEW-FEATURE TESTS PASSED ===")
