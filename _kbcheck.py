import os, traceback
os.environ["BOT_TOKEN"] = "123456:TESTTOKEN"
os.environ["DB_PATH"] = "/tmp/kbtest.db"
if os.path.exists("/tmp/kbtest.db"):
    os.remove("/tmp/kbtest.db")
import sys
sys.path.insert(0, ".")
import bot
bot.init_db()
try:
    t, kb = bot._manager_main_keyboard(1000)
    r0 = list(kb.keyboard)[0]
    x = list(r0)[0]
    print("MMK OK:", type(x).__name__, repr(str(x)))
except Exception:
    traceback.print_exc()
