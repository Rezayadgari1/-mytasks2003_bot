"""Offline tests for the Jalali birthday calendar & manual-entry parser.

No network calls. Run: python3 tests/test_birthday_calendar.py
"""
import asyncio
import os
import sys

os.environ["BOT_TOKEN"] = "123456:TESTTOKEN"
os.environ["DB_PATH"] = "/tmp/bdc_goals.db"
for f in ("/tmp/bdc_goals.db", "/tmp/bdc_goals.db.backup"):
    if os.path.exists(f):
        os.remove(f)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot  # noqa: E402

bot.init_db()

# ============ 1) Jalali -> standard Gregorian conversion ============
iso = bot.bd_jalali_to_iso(1382, 4, 23)
assert iso and iso.startswith("2003-"), iso
assert bot._g2j(*[int(x) for x in iso.split("-")]) == (1382, 4, 23), "round-trip failed"
print("[1] bd_jalali_to_iso OK: 1382/04/23 ->", iso)

# Leap-year Esfand handling (1383 is a leap Jalali year, 1382 is not)
assert bot.bd_jalali_to_iso(1383, 12, 30) is not None, "1383/12/30 should be valid (leap)"
assert bot.bd_jalali_to_iso(1382, 12, 30) is None, "1382/12/30 must be rejected (not leap)"
assert bot.bd_jalali_to_iso(1382, 7, 31) is None, "month 7 has only 30 days"
assert bot.bd_jalali_to_iso(1382, 13, 1) is None, "month 13 invalid"
assert bot.bd_jalali_to_iso("abc", 1, 1) is None, "non-numeric input rejected"
print("[2] validation OK (leap year, month/day bounds)")

# ============ 2) Manual-entry parser: all common formats ============
expected = bot.bd_jalali_to_iso(1382, 4, 23)
for txt in ("1382/04/23", "1382-04-23", "1382/4/23", "23/04/1382",
            "۲۳/۰۴/۱۳۸۲", "۱۳۸۲-۰۴-۲۳"):
    got = bot.bd_parse_any_date(txt)
    assert got == (expected, "jalali"), (txt, got)
print("[3] Jalali manual formats OK:", expected)

got = bot.bd_parse_any_date("2000-08-24")
assert got == ("2000-08-24", "gregorian"), got
assert bot.bd_parse_any_date("2000/8/24")[0] == "2000-08-24"
assert bot.bd_parse_any_date("15/03/1995") == ("1995-03-15", "gregorian")
assert bot.bd_parse_any_date("۱۹۹۵/۰۳/۱۵")[0] == "1995-03-15"
print("[4] Gregorian manual formats OK")

# Invalid inputs are rejected outright
for bad in ("garbage", "1382/12/30", "1382/07/31", "1382/13/01", "", "13/13"):
    assert bot.bd_parse_any_date(bad) is None, bad
print("[5] invalid inputs rejected OK")

# ============ 3) Calendar keyboards ============
ky = bot._bd_cal_years_kb(1380, True)
cbs = [b.callback_data for row in ky.inline_keyboard for b in row]
year_btns = [c for c in cbs if c.startswith("bd:calm:")]
assert len(year_btns) == 12, year_btns
assert any(c.startswith("bd:caly:") for c in cbs) and "bd:manual" in cbs and "bd:back" in cbs

km = bot._bd_cal_months_kb(1382, True)
mcb = [b.callback_data for row in km.inline_keyboard for b in row]
assert len([c for c in mcb if c.startswith("bd:cald:1382:")]) == 12, mcb

kd = bot._bd_cal_days_kb(1382, 4, True)
dcb = [b.callback_data for row in kd.inline_keyboard for b in row]
assert len([c for c in dcb if c.startswith("bd:calsave:1382:4:")]) == 31
kd12 = bot._bd_cal_days_kb(1382, 12, False)
dcb12 = [b.callback_data for row in kd12.inline_keyboard for b in row]
assert len([c for c in dcb12 if c.startswith("bd:calsave:1382:12:")]) == 29, dcb12
print("[6] calendars OK (12 years/page, 12 months, valid days per month)")

# ============ 4) Full callback flow simulation (set -> year -> month -> day -> saved) ============
uid = 777001
c = bot.db()
now = bot.datetime.now(bot.TZ).isoformat()
c.execute("INSERT OR IGNORE INTO users(user_id,first_name,language,vip_until,created_at) VALUES(?,?,?,?,?)",
          (uid, "Tester", "fa", "", now))
c.commit(); c.close()

captured = []


class Msg:
    async def edit_text(self, text=None, **kw):
        captured.append(("edit", text))
        return True

    async def reply_text(self, text=None, **kw):
        captured.append(("reply", text))
        return True


class Q:
    def __init__(self, data):
        self.from_user = type("U", (), {"id": uid})()
        self.data = data
        self.message = Msg()

    async def answer(self, text=None, show_alert=False):
        return True


class Up:
    def __init__(self, q):
        self.callback_query = q


ctx = type("C", (), {"user_data": {}})()
loop = asyncio.new_event_loop()
run = loop.run_until_complete

for step in ("bd:set", "bd:calm:1382", "bd:cald:1382:4", "bd:calsave:1382:4:23"):
    run(bot.birthday_callback(Up(Q(step)), ctx))

c = bot.db()
row = c.execute("SELECT birth_date FROM birthdays WHERE user_id=?", (uid,)).fetchone()
c.close()
assert row and row["birth_date"] == iso, (row, iso)
assert any("ثبت شد" in t for _, t in captured if t), captured[-3:]
print("[7] callback flow saved birthday:", row["birth_date"])

# Invalid day click must NOT write to DB
before = row["birth_date"]
run(bot.birthday_callback(Up(Q("bd:calsave:1382:12:30")), ctx))
c = bot.db()
after = c.execute("SELECT birth_date FROM birthdays WHERE user_id=?", (uid,)).fetchone()["birth_date"]
c.close()
assert after == before, "invalid date must not overwrite stored value"
print("[8] invalid calendar day rejected without saving")

# Manual-entry state is set by bd:manual and cleared on cancel
ctx.user_data.clear()
run(bot.birthday_callback(Up(Q("bd:manual")), ctx))
assert ctx.user_data.get("bd_wait") == "date"
run(bot.birthday_callback(Up(Q("bd:cancel")), ctx))
assert "bd_wait" not in ctx.user_data
print("[9] manual entry / cancel state OK")

# No recursion risk: text_router must not be re-wrapped by the birthday layer
import inspect
tr_src = inspect.getsource(bot.text_router)
assert tr_src.count("_OLD_TEXT_ROUTER_BDAY(update, context)") <= 1
print("[10] no recursive router wrapping")

os.remove("/tmp/bdc_goals.db")
if os.path.exists("/tmp/bdc_goals.db.backup"):
    os.remove("/tmp/bdc_goals.db.backup")
print("\n=== ALL BIRTHDAY CALENDAR TESTS PASSED ===")
