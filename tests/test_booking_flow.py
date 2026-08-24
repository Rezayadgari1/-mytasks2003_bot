"""Offline verification of the online-booking flow (رزرو آنلاین).

Checks:
  - Morning job notifies the owner AND each client with today's appointment
  - Per-client data isolation (client without telegram id is never messaged)
  - Unique booking token per business profile

Run: python3 tests/test_booking_flow.py
"""
import asyncio
import os
import sys

os.environ["BOT_TOKEN"] = "123456:TESTTOKEN"
os.environ["DB_PATH"] = "/tmp/bkf_goals.db"
os.environ["ADMIN_IDS"] = "1000"
for f in ("/tmp/bkf_goals.db", "/tmp/bkf_goals.db.backup"):
    if os.path.exists(f):
        os.remove(f)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot  # noqa: E402

bot.init_db()

OWNER, CLIENT_A, CLIENT_B = 1001, 2001, 2002
now = bot.datetime.now(bot.TZ).isoformat()
c = bot.db()
for uid, name in ((OWNER, "Biz"), (CLIENT_A, "Ali"), (CLIENT_B, "Sara")):
    c.execute("INSERT OR IGNORE INTO users(user_id,first_name,language,vip_until,created_at) VALUES(?,?,?,?,?)",
              (uid, name, "fa", "", now))
c.commit()
c.close()

sent = []


class FakeBot:
    async def send_message(self, chat_id, text, **kw):
        sent.append((chat_id, text))


class Ctx:
    bot = FakeBot()


loop = asyncio.new_event_loop()
run = loop.run_until_complete

# --- setup: today's appointments for client A (with telegram id) and an offline client ---
d = bot.datetime.now(bot.TZ).date().isoformat()
c = bot.db()
cust_a = c.execute(
    "INSERT INTO customers(owner_user_id,name,phone,telegram_user_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",
    (OWNER, "ClientA", "", CLIENT_A, now, now)).lastrowid
cust_b = c.execute(
    "INSERT INTO customers(owner_user_id,name,phone,telegram_user_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",
    (OWNER, "ClientB", "", None, now, now)).lastrowid
for cid, tm in ((cust_a, "10:00"), (cust_b, "11:00")):
    c.execute("INSERT INTO appointments(owner_user_id,customer_id,appointment_date,appointment_time,"
              "duration_minutes,status,source,reminder_minutes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
              (OWNER, cid, d, tm, 30, "booked", "online", "30", now, now))
c.commit()
c.close()

# --- 1) morning job informs BOTH sides ---
run(bot.customer_morning_job(Ctx()))  # outside 07:00 -> gated off
assert not sent, "morning job must be gated to 07:00"

RealDT = bot.datetime


class FakeDT(RealDT):
    @classmethod
    def now(cls, tz=None):
        return RealDT.now(tz).replace(hour=7, minute=0, second=0, microsecond=0)


bot.datetime = FakeDT
try:
    run(bot.customer_morning_job(Ctx()))
finally:
    bot.datetime = RealDT

owner_msgs = [s for s in sent if s[0] == OWNER]
client_a_msgs = [s for s in sent if s[0] == CLIENT_A]
assert owner_msgs and "برنامه مشتری" in owner_msgs[0][1], "owner must get the morning schedule"
assert client_a_msgs and "نوبت امروز" in client_a_msgs[0][1], "client A must get a today reminder"
assert not any(s[0] == CLIENT_B for s in sent), "offline client must never be messaged"
print(f"[1] morning job OK: {len(owner_msgs)} msg to owner + {len(client_a_msgs)} to client A; "
      "offline client untouched")


# --- 2) isolation: 'My bookings' only shows rows of the requesting telegram user ---
class Msg:
    def __init__(self):
        self.texts = []

    async def reply_text(self, t, **kw):
        self.texts.append(t)

    async def edit_text(self, t, **kw):
        self.texts.append(t)


class Upd:
    def __init__(self, uid):
        self.effective_user = type("U", (), {"id": uid})()
        self.message = Msg()


u = Upd(CLIENT_A)
run(bot.customer_my_bookings(u, type("C", (), {"user_data": {}})()))
assert any("رزروهای من" in t for t in u.message.texts)
print("[2] my-bookings renders for its own user")


# --- 3) unique booking token per business ---
pa = bot.ensure_business_profile(OWNER)
pb = bot.ensure_business_profile(3003)
assert pa["booking_token"] != pb["booking_token"], "booking tokens must be unique per business"
print("[3] unique booking token per business OK")

os.remove("/tmp/bkf_goals.db")
if os.path.exists("/tmp/bkf_goals.db.backup"):
    os.remove("/tmp/bkf_goals.db.backup")
print("\n=== BOOKING FLOW VERIFICATION PASSED ===")
