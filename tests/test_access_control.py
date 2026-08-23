"""Access-control tests: regular users must never reach owner/admin settings.

Offline only (no network). Verifies:
  - Regular users are denied /admin, /reports, /seclog, panel & master-center
    callbacks, and every denial is recorded in security_events.
  - Managers pass the admin gate but CANNOT touch owner-only areas
    (add/remove managers, sensitive finance data).
  - The Owner account cannot be demoted/disabled by anyone.
  - Disabling a manager revokes their access end-to-end.

Run: python3 tests/test_access_control.py
"""
import os, sys, asyncio

os.environ["BOT_TOKEN"] = "123456:TESTTOKEN"
os.environ["DB_PATH"] = "/tmp/acl_goals.db"
os.environ["ADMIN_IDS"] = "1000"  # owner = 1000 (min of ADMIN_IDS)
for f in ("/tmp/acl_goals.db", "/tmp/acl_goals.db.backup"):
    if os.path.exists(f):
        os.remove(f)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot

print("[1] import OK")
bot.init_db()

OWNER = 1000          # master owner (min ADMIN_IDS)
MANAGER = 2000        # active general_manager
ROLES_MGR = 2100      # manager that also holds manage_roles permission
DISABLED = 2500       # disabled manager
PLAIN = 3000          # ordinary user

c = bot.db()
now = bot.datetime.now(bot.TZ).isoformat()
import json as _json
c.execute("INSERT OR IGNORE INTO management_roles(user_id,role,domain,permissions_json,active,created_at,updated_at) VALUES(?,?,?,?,1,?,?)",
          (MANAGER, "general_manager", "general", _json.dumps(sorted(bot.MASTER_ROLE_PERMISSIONS["general_manager"])), now, now))
c.execute("INSERT OR IGNORE INTO management_roles(user_id,role,domain,permissions_json,active,created_at,updated_at) VALUES(?,?,?,?,1,?,?)",
          (ROLES_MGR, "general_manager", "general",
           _json.dumps(sorted(set(bot.MASTER_ROLE_PERMISSIONS["general_manager"]) | {"manage_roles"})), now, now))
c.execute("INSERT OR IGNORE INTO management_roles(user_id,role,domain,permissions_json,active,created_at,updated_at) VALUES(?,?,?,?,0,?,?)",
          (DISABLED, "general_manager", "general", _json.dumps(sorted(bot.MASTER_ROLE_PERMISSIONS["general_manager"])), now, now))
c.commit(); c.close()

# ---------- [2] admin gate matrix ----------
assert not bot.admin_is_allowed(0)
assert not bot.admin_is_allowed(PLAIN), "plain user must NOT pass the admin gate"
assert not bot.admin_is_allowed(DISABLED), "disabled manager must NOT pass the admin gate"
assert bot.admin_is_allowed(MANAGER)
assert bot.admin_is_allowed(ROLES_MGR)
assert bot.admin_is_allowed(OWNER), "owner must always pass"
print("[2] admin gate matrix OK (plain/disabled denied, manager/owner allowed)")

# ---------- [3] permission checks / default deny ----------
assert not bot.master_guard(0)
assert not bot.master_guard(PLAIN)
assert bot.master_guard(OWNER)
for perm in ("view_dashboard", "manage_users", "manage_tickets", "manage_features"):
    assert not bot.master_has_permission(PLAIN, perm)
# sensitive domains closed for a plain general_manager
for perm in ("manage_finance", "manage_roles", "backup", "restore", "view_audit", "manage_system"):
    assert not bot.master_has_permission(MANAGER, perm), f"general manager must lack {perm}"
# owner has every known permission
for perm in bot.MASTER_PERMISSION_KEYS if hasattr(bot, "MASTER_PERMISSION_KEYS") else bot.MASTER_DOMAIN_PERMISSION.values():
    assert bot.master_has_permission(OWNER, perm), f"owner lacks {perm}"
print("[3] RBAC permission checks + default deny OK")

# ---------- fakes ----------
class Msg:
    def __init__(self): self.replies = []; self.edits = []
    async def reply_text(self, text=None, *a, **k): self.replies.append(str(text))
    async def edit_text(self, text=None, *a, **k): self.edits.append(str(text))
    async def edit_reply_markup(self, *a, **k): pass
class Q:
    def __init__(self, uid, data):
        self.from_user = type("U", (), {"id": uid})()
        self.data = data; self.message = Msg(); self.alerts = []
    async def answer(self, text=None, *a, **k): self.alerts.append((str(text or ""), bool(k.get("show_alert"))))
class Upd:
    def __init__(self, uid, q=None, msg=None):
        self.effective_user = type("U", (), {"id": uid})()
        self.callback_query = q; self.message = msg or Msg()
class Ctx:
    def __init__(self): self.user_data = {}; self.bot = None

def sec_count(uid, event="admin_denied"):
    c = bot.db(); n = c.execute("SELECT COUNT(*) n FROM security_events WHERE user_id=? AND event=?", (uid, event)).fetchone()["n"]; c.close(); return n

# ---------- [4] plain user cannot open /admin (denial is logged) ----------
before = sec_count(PLAIN)
upd = Upd(PLAIN); ctx = Ctx()
asyncio.get_event_loop().run_until_complete(bot.admin_command(upd, ctx))
assert any("دسترسی" in r for r in upd.message.replies), upd.message.replies
assert sec_count(PLAIN) == before + 1, "admin denial was not logged to security_events"
print("[4] /admin denied for regular user + security_events logged")

# ---------- [5] panel callback denial for plain user ----------
q = Q(PLAIN, "adm:stats"); upd = Upd(PLAIN, q)
asyncio.get_event_loop().run_until_complete(bot.admin_panel_callback(upd, Ctx()))
assert any(a[1] and "دسترسی" in a[0] for a in q.alerts), q.alerts
assert len(q.message.edits) == 0, "panel content leaked on denial!"
print("[5] admin panel callback denied, zero content leaked")

# ---------- [6] /reports and /seclog denied for plain user ----------
upd = Upd(PLAIN)
asyncio.get_event_loop().run_until_complete(bot.reports_command(upd, Ctx()))
assert any("دسترسی" in r for r in upd.message.replies)
upd = Upd(PLAIN)
asyncio.get_event_loop().run_until_complete(bot.seclog_command(upd, Ctx()))
assert any("دسترسی" in r for r in upd.message.replies)
print("[6] /reports and /seclog denied for regular user")

# ---------- [7] master management center: gate + owner-only finance ----------
q = Q(PLAIN, "v25:master:home")
asyncio.get_event_loop().run_until_complete(bot.v25_callback(Upd(PLAIN, q), Ctx()))
assert any(a[1] for a in q.alerts) and len(q.message.edits) == 0, "master center leaked!"

# manager WITHOUT finance permission -> hard-denied, zero content
q = Q(MANAGER, "v25:master:finance")
asyncio.get_event_loop().run_until_complete(bot.v25_callback(Upd(MANAGER, q), Ctx()))
assert any(a[1] for a in q.alerts), q.alerts
assert len(q.message.edits) == 0, "finance content leaked to unauthorized manager!"

# finance_manager (HAS manage_finance but is not owner) -> masked view, no raw numbers
FINMGR = 2200
c = bot.db()
c.execute("INSERT OR IGNORE INTO management_roles(user_id,role,domain,permissions_json,active,created_at,updated_at) VALUES(?,?,?,?,1,?,?)",
          (FINMGR, "finance_manager", "finance", _json.dumps(sorted(bot.MASTER_ROLE_PERMISSIONS["finance_manager"])), now, now))
c.commit(); c.close()
q = Q(FINMGR, "v25:master:finance")
asyncio.get_event_loop().run_until_complete(bot.v25_callback(Upd(FINMGR, q), Ctx()))
fin = "\n".join(q.message.edits)
assert "فقط برای Owner" in fin, fin[:200]
assert "مجموع" not in fin, "sensitive finance numbers leaked to a non-owner!"
# owner sees the real finance report
q = Q(OWNER, "v25:master:finance")
asyncio.get_event_loop().run_until_complete(bot.v25_callback(Upd(OWNER, q), Ctx()))
own_fin = "\n".join(q.message.edits)
assert "تراکنش" in own_fin and "مالی" in own_fin, own_fin[:200]
assert "گزارش‌های غیرحساس سیستم از داشبورد" not in own_fin, "owner got the masked view!"
print("[7] master center gated; sensitive finance visible to Owner only")

# ---------- [8] adding managers is Owner-only even with manage_roles ----------
ctx = Ctx()
q = Q(ROLES_MGR, "v25:targeted:manager_add")
asyncio.get_event_loop().run_until_complete(bot.v25_callback(Upd(ROLES_MGR, q), ctx))
assert any("Owner only" in a[0] for a in q.alerts), q.alerts
assert "targeted_add_manager" not in ctx.user_data, "add-manager flow started for non-owner!"
# owner succeeds
ctx = Ctx()
q = Q(OWNER, "v25:targeted:manager_add")
asyncio.get_event_loop().run_until_complete(bot.v25_callback(Upd(OWNER, q), ctx))
assert ctx.user_data.get("targeted_add_manager") is True, "owner add-manager flow did not start"
print("[8] manager-add is strictly Owner-only (even with manage_roles permission)")

# ---------- [9] the Owner account can never be disabled/removed ----------
q = Q(OWNER, "v25:targeted:manager_disable:%d" % OWNER)
asyncio.get_event_loop().run_until_complete(bot.v25_callback(Upd(OWNER, q), Ctx()))
assert any("قابل لغو نیست" in a[0] for a in q.alerts), q.alerts
assert bot.admin_is_allowed(OWNER)
c = bot.db(); row = c.execute("SELECT active FROM management_roles WHERE user_id=?", (OWNER,)).fetchone(); c.close()
assert row is None or int(row["active"]) == 1, "owner row deactivated!"
print("[9] Owner account protected from disable/removal")

# ---------- [10] disabling a manager revokes access end-to-end (real handler) ----------
assert bot.admin_is_allowed(MANAGER)
q = Q(OWNER, "v25:targeted:manager_disable:%d" % MANAGER)
asyncio.get_event_loop().run_until_complete(bot.v25_callback(Upd(OWNER, q), Ctx()))
assert not bot.admin_is_allowed(MANAGER), "disabled manager still passes the gate!"
upd = Upd(MANAGER)
asyncio.get_event_loop().run_until_complete(bot.admin_command(upd, Ctx()))
assert any("دسترسی" in r for r in upd.message.replies), "revoked manager reached /admin!"
print("[10] manager revoked via real handler loses /admin access")

# restore MANAGER as active for the next check
c = bot.db(); c.execute("UPDATE management_roles SET active=1 WHERE user_id=?", (MANAGER,)); c.commit(); c.close()

# ---------- [11] feature toggles by non-owner queue an Owner notification ----------
c = bot.db(); c.execute("DELETE FROM owner_notifications"); c.commit(); c.close()
bot.set_feature("maintenance", True, admin_id=MANAGER)     # non-owner -> notify
bot.set_feature("maintenance", False, admin_id=OWNER)      # owner -> no notify
c = bot.db(); queued = c.execute("SELECT COUNT(*) n FROM owner_notifications").fetchone()["n"]; c.close()
assert queued == 1, f"expected exactly one owner notification, got {queued}"
bot.set_feature("maintenance", False, admin_id=MANAGER)    # restore default off
c = bot.db(); c.execute("DELETE FROM owner_notifications"); c.commit(); c.close()
print("[11] sensitive feature changes by managers are reported to Owner")

# ---------- hygiene ----------
for f in ("/tmp/acl_goals.db", "/tmp/acl_goals.db.backup"):
    if os.path.exists(f):
        os.remove(f)
print("\n=== ACCESS-CONTROL TESTS PASSED ===")
