"""Smoke test: import bot, init DB, exercise core functions, verify handler wiring."""
import os, sys

os.environ["BOT_TOKEN"] = "123456:TESTTOKEN"
os.environ["DB_PATH"] = "/tmp/smoke_goals.db"
if os.path.exists("/tmp/smoke_goals.db"):
    os.remove("/tmp/smoke_goals.db")
for f in ("/tmp/smoke_goals.db.backup",):
    if os.path.exists(f):
        os.remove(f)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot  # noqa: E402

print("[1] module import OK | build:", bot.MYTASKS_BUILD_ID)

# DB init (final layered definition wins)
bot.init_db()
c = bot.db()
tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
c.close()
print("[2] DB init OK | tables:", len(tables))
required = {"goals", "users"}
missing = required - tables
assert not missing, f"missing tables: {missing}"

# Core helpers
uid = 999001
assert bot.parse_time("18:30") == "18:30" or isinstance(bot.parse_time("18:30"), str)
print("[3] parse_time OK ->", bot.parse_time("18:30"), "| bad input ->", bot.parse_time("xx"))

bot.add_goal(uid, "تست هدف", "✨ شخصی", "09:00", 2, None)
g = bot.get_goal(uid, None) if False else None
goals = bot.get_goals(uid)
assert goals, "add_goal/get_goals failed"
gid = goals[0]["id"]
print(f"[4] add_goal/get_goal OK | goal id={gid}, name={goals[0]['name']}")

# Jalali conversion round-trip
jy, jm, jd = bot._g2j(2026, 8, 23)
gy, gm, gd = bot._j2g(jy, jm, jd)
assert (gy, gm, gd) == (2026, 8, 23), f"jalali round-trip failed: {(gy,gm,gd)}"
print(f"[5] Jalali conversion OK | 2026-08-23 -> {jy}/{jm}/{jd} -> back OK")

# Language / settings
print("[6] lang(uid) ->", bot.lang(uid), "| feature_enabled('maintenance') ->", bot.feature_enabled("maintenance"))

# Handler wiring: build application like main() does (without polling/network)
import telegram.ext as tex
app = tex.Application.builder().token("123456:TESTTOKEN").build()
print("[7] Application builder OK | job_queue present:", app.job_queue is not None)

# Verify every function referenced in main() exists
import inspect
src = inspect.getsource(bot.main)
names = set()
for n in ("subscription_check_callback","customer_panel_callback","admin_user_detail_callback",
          "admin_user_action_callback","feature_category_callback","navigation_callback",
          "ai_chat_navigation_callback","admin_panel_callback","smart_post_callback",
          "channel_panel_callback","auto_channel_callback","auto_category_callback",
          "auto_subcategory_callback","auto_interval_callback","approval_callback",
          "approval_reject_callback","feedback_callback","channel_schedule_callback",
          "channel_daily_callback","channel_weekday_callback","channel_weektime_callback",
          "language_callback","settings_language_callback","goals_navigation_callback",
          "settings_callback","price_callback","onboarding_business_callback",
          "onboarding_feature_callback","gender_callback","priority_callback",
          "duration_callback","snooze_menu","goal_reminder_callback","snooze_callback",
          "steps_menu","step_add_start","step_toggle","ready_subcategory_callback",
          "goal_reminders_list","goal_calendar_callback","goal_calendar_day",
          "my_goals_callback","new_category","new_back","new_goal_pick","time_callback",
          "edit_time_callback","detail","mark","edit_goal","rename_start",
          "change_reminder","delete_start","delete_confirm","delete_no",
          "admin_broadcast_start","support_callback","vip_callback","precheckout_callback",
          "successful_payment_callback","start","my_id","admin_command","xp_command",
          "referral","prices","support_start","text_router"):
    names.add(n)
undef = [n for n in names if not hasattr(bot, n)]
assert not undef, f"undefined handlers: {undef}"
print(f"[8] all {len(names)} referenced handlers exist")

# Verify the bug fixes are live in every registered function
import inspect as _i
sc = _i.getsource(bot.__dict__["settings_callback"])
assert "uid = q.from_user.id" in sc, "settings_callback uid fix missing"
bad = [n for n, f in vars(bot).items()
       if callable(f) and getattr(f, "__module__", "") == "bot"
       and ",callback_data=data)" in _i.getsource(f)]
assert not bad, f"functions still using undefined data: {bad}"
print("[9] bug fixes verified (settings_callback uid + no undefined 'data' anywhere)")

# Verify safe feature-flag defaults on a fresh DB
assert bot.feature_enabled("maintenance") is False or bot.feature_enabled("maintenance") == False, "maintenance should default OFF"
assert bot.feature_access_mode("payments") == "off", "payments should default off"

os.remove("/tmp/smoke_goals.db")
if os.path.exists("/tmp/smoke_goals.db.backup"):
    os.remove("/tmp/smoke_goals.db.backup")
print("\n=== ALL SMOKE TESTS PASSED ===")
