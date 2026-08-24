import os, traceback
os.environ["BOT_TOKEN"] = "123456:TESTTOKEN"
os.environ["DB_PATH"] = "/tmp/kbtest5.db"
os.environ["ADMIN_IDS"] = "1000"
for f in ("/tmp/kbtest5.db", "/tmp/kbtest5.db.backup"):
    if os.path.exists(f):
        os.remove(f)
import sys
sys.path.insert(0, ".")
import bot
bot.init_db()

c = bot.db()
now = bot.datetime.now(bot.TZ).isoformat()
for uid, name in ((3001, "A"), (3002, "B"), (1000, "O")):
    c.execute("INSERT OR IGNORE INTO users(user_id,first_name,language,vip_until,created_at) VALUES(?,?,?,?,?)", (uid, name, "fa", "", now))
c.commit(); c.close()

class U: 
    def __init__(s,i): s.id=i
class M:
    async def reply_text(s,*a,**k): pass
class UP:
    def __init__(s,i,t): s.effective_user=U(i); s.message=M()
ctx = type("C", (), {"user_data": {}})()

# mimic test: plain user tries panel (denied), then owner opens panel
loop = bot.asyncio.new_event_loop()
loop.run_until_complete(bot._bdo_route(UP(3001, bot.BD_PANEL_BTN), type("C", (), {"user_data": {}})(), 3001, bot.BD_PANEL_BTN))
loop.run_until_complete(bot._bdo_route(UP(1000, bot.BD_PANEL_BTN), ctx, 1000, bot.BD_PANEL_BTN))
t, kb = bot._manager_main_keyboard(1000)
print("owner kb ok")
try:
    kbu = bot.compact_keyboard(3001)
    print("user kb ok")
except Exception:
    traceback.print_exc()
