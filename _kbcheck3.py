import os
os.environ["BOT_TOKEN"] = "123456:TESTTOKEN"
os.environ["DB_PATH"] = "/tmp/kbtest3.db"
os.environ["ADMIN_IDS"] = "1000"
for f in ("/tmp/kbtest3.db", "/tmp/kbtest3.db.backup"):
    if os.path.exists(f):
        os.remove(f)
import sys
sys.path.insert(0, ".")
import bot
bot.init_db()
print("owner:", bot.master_owner_id(), "| bd owner?", bot._is_bd_owner(1000))
t, kb = bot._manager_main_keyboard(1000)
for r in kb.keyboard:
    print([repr(x) for x in r])
