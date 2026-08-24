import os, traceback
os.environ["BOT_TOKEN"] = "123456:TESTTOKEN"
os.environ["DB_PATH"] = "/tmp/kbtest4.db"
os.environ["ADMIN_IDS"] = "1000"
for f in ("/tmp/kbtest4.db", "/tmp/kbtest4.db.backup"):
    if os.path.exists(f):
        os.remove(f)
import sys
sys.path.insert(0, ".")
import bot
bot.init_db()
try:
    kb = bot.compact_keyboard(3001)
    print("OK", type(kb))
except Exception:
    tb = traceback.format_exc()
    print(tb)
