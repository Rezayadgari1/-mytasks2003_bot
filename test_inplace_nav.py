"""Verify in-place rendering + back buttons on compact menu screens."""
import os, sys, asyncio

os.environ["BOT_TOKEN"] = "123456:TESTTOKEN"
os.environ["DB_PATH"] = "/tmp/nav_goals.db"
for f in ("/tmp/nav_goals.db", "/tmp/nav_goals.db.backup"):
    if os.path.exists(f):
        os.remove(f)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bot

print("[1] import OK")
bot.init_db()

class U: id = 4242
class QMsg:
    def __init__(self):
        self.edits = []; self.replies = []
    async def edit_text(self, text=None, parse_mode=None, reply_markup=None, **k):
        self.edits.append((text, reply_markup))
    async def edit_reply_markup(self, **k): pass
    async def reply_text(self, text=None, parse_mode=None, reply_markup=None, **k):
        self.replies.append((text, reply_markup))

class Q:
    def __init__(self):
        self.data = "cm:stats"; self.from_user = U(); self.message = QMsg()
        self.answered = False
    async def answer(self, *a, **k): self.answered = True

class Upd:
    def __init__(self, q): self.callback_query = q; self.effective_user = U(); self.message = q.message
class Ctx:
    def __init__(self): self.user_data = {}

async def run():
    # Simulate entering from the Reports section so Back should target it.
    ctx = Ctx(); ctx.user_data["_last_section"] = "menu:reports"
    q = Q(); upd = Upd(q)
    await bot.compact_menu_callback(upd, ctx)
    return q, ctx

q, ctx = asyncio.get_event_loop().run_until_complete(run())

# In place: edited the SAME message, no new bubbles sent
assert len(q.message.edits) >= 1, "screen was not rendered via edit (in-place failed)"
assert len(q.message.replies) == 0, f"sent {len(q.message.replies)} new messages instead of editing"
print("[2] in-place render OK | edits:", len(q.message.edits), "| new msgs:", len(q.message.replies))

# Back button present and targets the originating section
kb = q.message.edits[-1][1]
rows = kb.inline_keyboard
all_btns = [b for r in rows for b in r]
back = [b for b in all_btns if b.callback_data.startswith(("menu:", "cm:home")) and "بازگشت" in b.text]
main = [b for b in all_btns if b.callback_data == "nav:main"]
assert back and back[0].callback_data == "menu:reports", [b.text for b in all_btns]
assert main, "main menu button missing"
print("[3] back row OK |", [b.text for b in rows[-1]], "-> back to", back[0].callback_data)

# No duplicate nav rows when a screen already provides one
dup_rows = [r for r in rows if sum(1 for b in r if b.callback_data == "nav:main") > 0]
assert len(dup_rows) <= 1, "duplicated main-menu row"
print("[4] no duplicate nav rows")

# Proxy decoration unit check: keyboard without nav gets one row appended
pm = bot._SectionProxyMessage(QMsg(), 4242, "menu:goals")
import telegram
plain = telegram.InlineKeyboardMarkup([[telegram.InlineKeyboardButton("X", callback_data="x")]])
dec = pm._decorated(plain)
last = dec.inline_keyboard[-1]
assert any(b.callback_data == "menu:goals" for b in last) and any(b.callback_data == "nav:main" for b in last)
print("[5] proxy decoration OK")

for f in ("/tmp/nav_goals.db", "/tmp/nav_goals.db.backup"):
    if os.path.exists(f):
        os.remove(f)
print("\n=== NAV TESTS PASSED ===")
