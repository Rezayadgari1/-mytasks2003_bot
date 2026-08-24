import os, traceback
os.environ["BOT_TOKEN"] = "123456:TESTTOKEN"
os.environ["DB_PATH"] = "/tmp/kbtest2.db"
for f in ("/tmp/kbtest2.db", "/tmp/kbtest2.db.backup"):
    if os.path.exists(f):
        os.remove(f)
import sys
sys.path.insert(0, ".")
import bot
bot.init_db()
print("owner id:", bot.master_owner_id(), "ADMIN_IDS:", bot.ADMIN_IDS)
t, kb = bot._manager_main_keyboard(1000)
rows = [str(x) for r in kb.keyboard for x in r]
print("panel btn const:", repr(bot.BD_PANEL_BTN))
for x in rows:
    print("row:", repr(x))
