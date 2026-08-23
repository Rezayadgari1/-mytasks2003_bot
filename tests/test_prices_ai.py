"""Offline tests for Online Prices and the AI text gateway.

No real network calls: every remote source is monkeypatched.
Run: python3 tests/test_prices_ai.py
"""
import os, sys, asyncio

os.environ["BOT_TOKEN"] = "123456:TESTTOKEN"
os.environ["DB_PATH"] = "/tmp/pa_goals.db"
os.environ.pop("OPENAI_API_KEY", None)
for f in ("/tmp/pa_goals.db", "/tmp/pa_goals.db.backup"):
    if os.path.exists(f):
        os.remove(f)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot

print("[1] import OK")
bot.init_db()

# ============================ AI GATEWAY ============================

# Configure three providers via their config globals (OpenAI left unset).
bot.N8N_WEBHOOK_URL = "https://n8n.example.com/webhook/ai"
bot.N8N_API_KEY = "k"
bot.OMNIROUTE_BASE_URL = "https://omni.example.com"
bot.OMNIROUTE_API_KEY = "k"
bot.GEMINI_API_KEY = "gkey"

calls = []
def reset_patches():
    async def _noop(): pass
    bot._n8n_ai_fallback_sync = lambda p: (_calls.append("n8n"), "ANSWER-N8N")[1]
    bot._omniroute_ai_sync = lambda p: (_calls.append("omni"), "ANSWER-OMNI")[1]
    bot._gemini_generate_text = lambda p: (_calls.append("gemini"), "ANSWER-GEMINI")[1]

_calls = []
reset_patches()

# 2) Priority order: n8n -> OmniRoute -> Gemini
ans = bot.ai_text_generate("سلام", purpose="test")
assert ans == "ANSWER-N8N", ans
assert _calls == ["n8n"], _calls
print("[2] provider priority: first configured wins |", ans)

# 3) Failover: n8n fails -> OmniRoute answers
_calls.clear()
def boom(p):
    _calls.append("boom")
    raise RuntimeError("provider down")
bot._n8n_ai_fallback_sync = boom
ans = bot.ai_text_generate("سلام")
assert ans == "ANSWER-OMNI" and _calls == ["boom", "omni"], (ans, _calls)
print("[3] failover works: n8n down -> OmniRoute answered")

# 4) Two providers down -> Gemini answers
_calls.clear()
bot._omniroute_ai_sync = boom
ans = bot.ai_text_generate("سلام")
assert ans == "ANSWER-GEMINI" and _calls.count("boom") == 2 and _calls[-1] == "gemini", (ans, _calls)
print("[4] double failover -> Gemini answered")

# 5) Empty answer counts as failure and falls through
_calls.clear()
bot._gemini_generate_text = lambda p: "   "
ans = bot.ai_text_generate("سلام")
assert not ans, repr(ans)
print("[5] empty responses fall through -> graceful empty result")

# 6) No provider configured at all
saved = (bot.N8N_WEBHOOK_URL, bot.N8N_API_KEY, bot.OMNIROUTE_BASE_URL,
         bot.OMNIROUTE_API_KEY, bot.GEMINI_API_KEY, bot._n8n_ai_fallback_sync,
         bot._omniroute_ai_sync, bot._gemini_generate_text)
bot.N8N_WEBHOOK_URL = ""; bot.N8N_API_KEY = ""
bot.OMNIROUTE_BASE_URL = ""; bot.OMNIROUTE_API_KEY = ""
bot.GEMINI_API_KEY = ""
os.environ.pop("OPENAI_API_KEY", None)
ans = bot.ai_text_generate("سلام")
assert not ans
print("[6] no providers -> clean empty result (chat shows polite error)")

# restore
(bot.N8N_WEBHOOK_URL, bot.N8N_API_KEY, bot.OMNIROUTE_BASE_URL,
 bot.OMNIROUTE_API_KEY, bot.GEMINI_API_KEY, bot._n8n_ai_fallback_sync,
 bot._omniroute_ai_sync, bot._gemini_generate_text) = saved

# 7) Security: OmniRoute base URL must be HTTPS or loopback-only HTTP
assert bot._secure_remote_base("https://x.com") is True
assert bot._secure_remote_base("http://127.0.0.1:3000") is True
assert bot._secure_remote_base("http://localhost:3000") is True
assert bot._secure_remote_base("http://evil.com") is False
assert bot._secure_remote_base("") is False
print("[7] _secure_remote_base policy OK")

# ============================ ONLINE PRICES ============================

real_tgju, real_json, real_json_post = (bot.tgju_value, bot.fetch_url_json, bot.fetch_url_json_post)

# 8) USD from TGJU scrape, normalized + formatted
bot.tgju_value = lambda url: "610,500"
ans = asyncio.get_event_loop().run_until_complete(bot.fetch_price("usd"))
assert ans == "610,500 ریال", ans
print("[8] usd price OK ->", ans)

# 9) BTC from Nobitex v3 orderbook (Rial, single Toman conversion avoided)
bot.fetch_url_json = lambda url: {"lastTradePrice": "2543210000"}
ans = asyncio.get_event_loop().run_until_complete(bot.fetch_price("btc"))
assert ans == "2,543,210,000 ریال", ans
print("[9] btc price OK ->", ans)

# 10) BTC fallback chain: broken v3 -> trades endpoint
def bad_v3(url):
    if "/v3/" in url:
        raise RuntimeError("down")
    return {"trades": [{"price": "2500000000"}]}
bot.fetch_url_json = bad_v3
ans = asyncio.get_event_loop().run_until_complete(bot.fetch_price("eth"))
assert ans == "2,500,000,000 ریال", ans
print("[10] eth fallback chain OK ->", ans)

# 11) USDT = CoinGecko USD x TGJU dollar rate
bot.fetch_url_json = lambda url: {"tether": {"usd": 1.0}}
ans = asyncio.get_event_loop().run_until_complete(bot.fetch_price("usdt"))
assert ans == "610,500 ریال", ans   # 1.0 * 610500
print("[11] usdt cross-rate OK ->", ans)

# 12) S&P 500 from Yahoo chart meta
bot.fetch_url_json = lambda url: {"chart": {"result": [{"meta": {"regularMarketPrice": 5123.45}}]}}
ans = asyncio.get_event_loop().run_until_complete(bot.fetch_price("sp500"))
assert ans == "5,123.45 USD", ans
print("[12] index price OK ->", ans)

# 13) Gold normalization
bot.tgju_value = lambda url: "4,150,000"
ans = asyncio.get_event_loop().run_until_complete(bot.fetch_price("gold18"))
assert ans == "4,150,000 ریال", ans
print("[13] gold18 OK ->", ans)

# 14) Source totally down -> raises (callback will show ❌ دریافت نشد)
def dead(url): raise RuntimeError("tgju down")
bot.tgju_value = dead
try:
    asyncio.get_event_loop().run_until_complete(bot.fetch_price("eur"))
    raised = False
except Exception:
    raised = True
assert raised, "expected failure when source is down"
print("[14] source-down raises cleanly")

# 15) price_callback end-to-end: renders in place, failures shown politely
bot.tgju_value = lambda url: {"price_dollar_rl": "610,500", "price_eur": "660,000"}[
    "price_dollar_rl" if "dollar" in url else "price_eur"]

class U: id = 313
class QMsg:
    def __init__(self): self.edits = []; self.replies = []
    async def edit_text(self, text=None, reply_markup=None, **k): self.edits.append(text)
    async def reply_text(self, text=None, reply_markup=None, **k): self.replies.append(text)
class Q:
    data = "price:all"; from_user = U(); message = QMsg()
    async def answer(self, *a, **k): pass
class Upd:
    callback_query = Q(); effective_user = U(); message = Q.message
class Ctx: pass

asyncio.get_event_loop().run_until_complete(bot.price_callback(Upd(), Ctx()))
out = "\n".join(Q.message.edits)
assert len(Q.message.replies) == 0, "sent a new bubble instead of editing"
assert "دلار: <b>610,500</b> ریال" in out and "یورو: <b>660,000</b> ریال" in out
assert "آخرین بررسی" in out
print("[15] price_callback renders ALL prices in place, zero new messages")

# 16) source failure view (final v25 renderer marks dead sources politely)
_orig_fpv25 = bot.fetch_price_v25
async def _dead_price(asset): raise RuntimeError("all sources down")
bot.fetch_price_v25 = _dead_price
# single-asset view requires the asset to be admin-enabled first
bot._targeted_set_price_enabled("btc", True)
Q2 = Q(); Q2.data = "price:btc"
u2 = Upd(); u2.callback_query = Q2; u2.message = Q2.message
asyncio.get_event_loop().run_until_complete(bot.price_callback(u2, Ctx()))
out2 = Q2.message.edits[-1]
assert "⚠️ داده آنلاین در دسترس نیست" in out2 or "❌ دریافت نشد" in out2, out2[:300]
assert "BTC" in out2
bot.fetch_price_v25 = _orig_fpv25
print("[16] failed asset shows friendly ❌ line")

# restore originals (hygiene)
bot.tgju_value, bot.fetch_url_json, bot.fetch_url_json_post = real_tgju, real_json, real_json_post

for f in ("/tmp/pa_goals.db", "/tmp/pa_goals.db.backup"):
    if os.path.exists(f):
        os.remove(f)
print("\n=== PRICES & AI TESTS PASSED ===")
