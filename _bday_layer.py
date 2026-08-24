# ===================== BIRTHDAY & OCCASIONS MODULE =====================
# Independent system: persistent user birthday registry + configurable gifts +
# custom occasions. Managed ONLY from the Owner area of the manager panel
# (پنل مدیر -> 🎂 تولد و مناسبت‌ها). Privacy-safe by construction: regular users
# can only ever read/write their OWN birthday row; there is no API that exposes
# another user's birthday outside the Owner-only report.

_BDAY_SCHEMA_OK = [False]


def _bday_db():
    c = db()
    if not _BDAY_SCHEMA_OK[0]:
        c.execute("CREATE TABLE IF NOT EXISTS birthdays(user_id INTEGER PRIMARY KEY, birth_date TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS bday_events(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, year INTEGER NOT NULL, kind TEXT NOT NULL, occasion_id INTEGER NOT NULL DEFAULT 0, gift_kind TEXT DEFAULT '', gift_amount REAL DEFAULT 0, created_at TEXT NOT NULL, UNIQUE(user_id, year, kind, occasion_id))")
        c.execute("CREATE TABLE IF NOT EXISTS occasions(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, date TEXT NOT NULL, message TEXT DEFAULT '', gift_kind TEXT DEFAULT 'none', gift_amount REAL DEFAULT 0, xp_amount INTEGER DEFAULT 0, vip_days INTEGER DEFAULT 0, active INTEGER DEFAULT 1, auto_send INTEGER DEFAULT 1, last_sent_year INTEGER DEFAULT 0, created_at TEXT NOT NULL)")
        c.commit()
        _BDAY_SCHEMA_OK[0] = True
    return c


_FA_DIGITS = {"۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4", "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
              "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9"}


def _bd_en(s):
    for k, v in _FA_DIGITS.items():
        s = s.replace(k, v)
    return s


def bd_parse_date(text):
    """Parse Gregorian date YYYY-MM-DD / YYYY/MM/DD (Persian digits allowed).
    Returns ISO string or None. Full validation via datetime."""
    s = _bd_en((text or "").strip()).replace("/", "-").replace(".", "-").replace("\\", "-").strip("-")
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if not m:
        return None
    try:
        d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
    except ValueError:
        return None
    return d.isoformat()


def bd_parse_mmdd(text):
    iso = bd_parse_date(text if "-" in (text or "") else "")
    if iso:
        return iso[5:]
    s = _bd_en((text or "").strip()).replace("/", "-").replace(".", "-")
    m = re.match(r"^(\d{1,2})-(\d{1,2})$", s)
    if not m:
        return None
    mo, dy = int(m.group(1)), int(m.group(2))
    if not (1 <= mo <= 12 and 1 <= dy <= 31):
        return None
    return f"{mo:02d}-{dy:02d}"


def bd_get(key, default=""):
    return get_system_setting(key, default)


def bd_set(key, value):
    set_system_setting(key, str(value))


BD_GIFT_KINDS = ("xp", "service", "vip", "subscription", "none")
BD_GIFT_LABELS = {"xp": "⭐ XP", "service": "🪙 خدمات (توکن)", "vip": "💎 VIP", "subscription": "📜 اشتراک", "none": "⛔ بدون هدیه"}
BD_AUDIENCES = ("all", "normal", "vip")
BD_AUD_LABELS = {"all": "👥 همه کاربران", "normal": "👤 فقط کاربران عادی", "vip": "💎 فقط VIP"}


def bd_audience_ok(uid):
    aud = bd_get("bd_audience", "all")
    if aud == "all":
        return True
    vip = is_vip(uid)
    return vip if aud == "vip" else (not vip)


def bd_gift_desc(kind, amount):
    n = max(1, int(float(amount or 0)))
    unit = {"xp": "XP", "service": "توکن", "vip": "روز VIP", "subscription": "روز اشتراک"}.get(kind, "")
    return f"{n} {unit}" if unit else ""


def _bday_claim(c, uid, year, kind, occ_id=0):
    """Atomically claim a once-per-user-per-year event. Returns event id or 0."""
    cur = c.execute(
        "INSERT OR IGNORE INTO bday_events(user_id,year,kind,occasion_id,created_at) VALUES(?,?,?,?,?)",
        (uid, year, kind, occ_id, datetime.now(TZ).isoformat()))
    if cur.rowcount != 1:
        return 0
    return cur.lastrowid


def _bday_reward(c, eid, uid, gkind, amount):
    """Apply a reward bypassing the daily XP cap (gifts are deliberate grants).
    Returns a short human description ('' when nothing granted)."""
    desc = ""
    now_iso = datetime.now(TZ).isoformat()
    if gkind == "xp":
        n = max(1, int(float(amount or 0)))
        c.execute("UPDATE users SET xp=COALESCE(xp,0)+? WHERE user_id=?", (n, uid))
        c.execute("INSERT INTO xp_log(user_id,amount,reason,created_at) VALUES(?,?,?,?)", (uid, n, "birthday_gift", now_iso))
        desc = f"⭐ +{n} XP"
    elif gkind == "service":
        n = max(1, int(float(amount or 0)))
        add_tokens(uid, n, reason="birthday_gift")
        desc = f"🪙 +{n} توکن"
    elif gkind in ("vip", "subscription"):
        days = max(1, int(float(amount or 1)))
        r = c.execute("SELECT COALESCE(vip_until,'') v FROM users WHERE user_id=?", (uid,)).fetchone()
        base = datetime.now(TZ)
        if r and r["v"]:
            try:
                until = datetime.fromisoformat(r["v"])
                if until > base:
                    base = until
            except Exception:
                pass
        c.execute("UPDATE users SET vip_until=? WHERE user_id=?", ((base + timedelta(days=days)).isoformat(), uid))
        desc = f"💎 {days} روز {'VIP' if gkind == 'vip' else 'اشتراک'}"
    if desc and eid:
        c.execute("UPDATE bday_events SET gift_kind=?,gift_amount=? WHERE id=?", (gkind, float(amount or 0), eid))
    return desc


def bd_congrats_text(name):
    tpl = bd_get("bd_congrats_fa", "🎉 {name} عزیز، تولدت مبارک! 🎂\n\nامیدواریم سالی سرشار از سلامتی، شادی و موفقیت داشته باشی.")
    txt = tpl.replace("{name}", name or "دوست عزیز")
    if bd_get("bd_gift_enabled", "1") == "1":
        gkind = bd_get("bd_gift_kind", "xp")
        if gkind != "none":
            try:
                amt = float(bd_get("bd_gift_amount", "50"))
            except ValueError:
                amt = 50.0
            d = bd_gift_desc(gkind, amt)
            if d:
                txt += "\n\n🎁 هدیه تولد: " + d
    return txt


# ---------- user-facing command (privacy: own row only) ----------
# Birthday entry: Jalali calendar picker (سال ← ماه ← روز) + ✍️ ورود دستی.

_BD_JY_MIN, _BD_JY_MAX = 1300, 1450
_BD_YEARS_PER_PAGE = 12
_BD_FA_DG = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_BD_MONTHS_FA = ("فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
                 "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند")
_BD_MONTHS_EN = ("Farvardin", "Ordibehesht", "Khordad", "Tir", "Mordad", "Shahrivar",
                 "Mehr", "Aban", "Azar", "Dey", "Bahman", "Esfand")


def _bd_now_jy():
    t = datetime.now(TZ).date()
    return _g2j(t.year, t.month, t.day)[0]


def bd_jalali_to_iso(jy, jm, jd):
    """Validate a Jalali date and convert it to the standard Gregorian ISO string."""
    try:
        jy, jm, jd = int(jy), int(jm), int(jd)
    except (TypeError, ValueError):
        return None
    if not (_BD_JY_MIN <= jy <= _BD_JY_MAX and 1 <= jm <= 12):
        return None
    if not (1 <= jd <= _jalali_month_days(jy, jm)):
        return None
    try:
        gy, gm, gd = _j2g(jy, jm, jd)
        return datetime(gy, gm, gd).date().isoformat()
    except Exception:
        return None


def bd_parse_any_date(text):
    """Parse common birthday formats: 1382/04/23, 1382-04-23, 23/04/1382,
    ۲۳/۰۴/۱۳۸۲, 2000-08-24, 2000/8/24. Returns (gregorian_iso, kind) or None.
    Years inside the Jalali window are treated as Solar Hijri; anything else as Gregorian."""
    s = _bd_en((text or "").strip()).replace("/", "-").replace(".", "-").replace("،", "-").strip("- ")
    parts = [p for p in re.split(r"-+", s) if p]
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        a, b, cc = (int(p) for p in parts)

        def _try_jal(y, m, d):
            iso = bd_jalali_to_iso(y, m, d)
            return (iso, "jalali") if iso else None

        def _try_greg(y, m, d):
            iso = bd_parse_date("%04d-%02d-%02d" % (y, m, d))
            return (iso, "gregorian") if iso else None

        if a >= 100:
            if _BD_JY_MIN <= a <= _BD_JY_MAX:
                return _try_jal(a, b, cc)
            return _try_greg(a, b, cc)
        if cc >= 100:
            if _BD_JY_MIN <= cc <= _BD_JY_MAX:
                return _try_jal(cc, b, a)
            return _try_greg(cc, b, a)
        return None
    iso = bd_parse_date(text)
    return (iso, "gregorian") if iso else None


async def _safe_edit(q, text, kb=None):
    try:
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        try:
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass


def _bd_manual_btn(fa):
    return InlineKeyboardButton("✍️ ورود دستی" if fa else "✍️ Manual", callback_data="bd:manual")


def _bd_cal_years_kb(base, fa):
    base = max(_BD_JY_MIN, min(int(base), _BD_JY_MAX - _BD_YEARS_PER_PAGE + 1))
    rows, row = [], []
    for y in range(base, base + _BD_YEARS_PER_PAGE):
        row.append(InlineKeyboardButton(str(y).translate(_BD_FA_DG) if fa else str(y),
                                        callback_data="bd:calm:%d" % y))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton("◀️ سال‌های قبل" if fa else "◀️ Older years",
                             callback_data="bd:caly:%d" % (base - _BD_YEARS_PER_PAGE)),
        InlineKeyboardButton("سال‌های بعد ▶️" if fa else "Newer years ▶️",
                             callback_data="bd:caly:%d" % (base + _BD_YEARS_PER_PAGE)),
    ])
    rows.append([_bd_manual_btn(fa),
                 InlineKeyboardButton("⬅️ بازگشت" if fa else "⬅️ Back", callback_data="bd:back")])
    return InlineKeyboardMarkup(rows)


def _bd_cal_months_kb(year, fa):
    names = _BD_MONTHS_FA if fa else _BD_MONTHS_EN
    rows, row = [], []
    for m in range(1, 13):
        row.append(InlineKeyboardButton(names[m - 1], callback_data="bd:cald:%d:%d" % (year, m)))
        if len(row) == 3:
            rows.append(row)
            row = []
    rows.append([_bd_manual_btn(fa),
                 InlineKeyboardButton("⬅️ انتخاب سال" if fa else "⬅️ Years", callback_data="bd:cal")])
    return InlineKeyboardMarkup(rows)


def _bd_cal_days_kb(year, month, fa):
    ndays = _jalali_month_days(year, month)
    rows, row = [], []
    for d in range(1, ndays + 1):
        row.append(InlineKeyboardButton(str(d).translate(_BD_FA_DG) if fa else str(d),
                                        callback_data="bd:calsave:%d:%d:%d" % (year, month, d)))
        if len(row) == 7:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_bd_manual_btn(fa),
                 InlineKeyboardButton("⬅️ انتخاب ماه" if fa else "⬅️ Months",
                                      callback_data="bd:calm:%d" % year)])
    return InlineKeyboardMarkup(rows)


def _bd_status_parts(uid):
    fa = lang(uid) == "fa"
    c = _bday_db()
    r = c.execute("SELECT birth_date FROM birthdays WHERE user_id=?", (uid,)).fetchone()
    c.close()
    if r:
        jy, jm, jd = _jalali_from_iso(r["birth_date"])
        if fa:
            text = ("🎂 <b>تاریخ تولد ثبت‌شدهٔ تو:</b>\n"
                    "🗓 شمسی: <code>%04d/%02d/%02d</code>\n"
                    "📅 میلادی: <code>%s</code>\n\n"
                    "⚠️ تاریخ تولد دائمی ذخیره می\u200cشود و فقط برای تبریک و هدیه استفاده می\u200cشود."
                    ) % (jy, jm, jd, r["birth_date"])
        else:
            text = ("🎂 <b>Your saved birthday:</b> <code>%s</code>\n\n"
                    "⚠️ Your birthday is saved permanently and used only for birthday greetings and gifts."
                    ) % (r["birth_date"],)
    else:
        if fa:
            text = ("🎂 <b>تاریخ تولد خودت رو وارد کن</b>\n\n"
                    "سال، ماه و روز تولدت را از تقویم انتخاب کن یا «✍️ ورود دستی» را بزن.\n\n"
                    "⚠️ تاریخ تولد دائمی ذخیره می\u200cشود و فقط برای تبریک و هدیه استفاده می\u200cشود.")
        else:
            text = ("🎂 <b>Enter your birthday</b>\n\n"
                    "Pick year, month and day from the calendar, or tap Manual.\n\n"
                    "⚠️ Your birthday is saved permanently and used only for birthday greetings and gifts.")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎂 ثبت تولد" if fa else "🎂 Set Birthday", callback_data="bd:set")],
        [InlineKeyboardButton("🗑 حذف تاریخ" if fa else "🗑 Delete", callback_data="bd:del")],
    ])
    return text, kb


async def birthday_command(update, context):
    uid = update.effective_user.id
    fa = lang(uid) == "fa"
    if bd_get("bd_enabled", "1") != "1":
        await update.message.reply_text("این بخش در حال حاضر غیرفعال است. 🙏" if fa else "This section is currently disabled.")
        return
    text, kb = _bd_status_parts(uid)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def birthday_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    fa = lang(uid) == "fa"
    data = q.data or ""
    if not data.startswith("bd:"):
        await q.answer()
        return
    act = data[3:]

    async def _ans(msg=None, alert=False):
        try:
            await q.answer(msg, show_alert=alert)
        except Exception:
            pass

    if act == "set" or act == "cal":
        await _ans()
        base = max(_BD_JY_MIN, min(_bd_now_jy() - 25, _BD_JY_MAX - 11))
        title = "📅 <b>سال تولدت را انتخاب کن:</b>" if fa else "📅 <b>Select your birth year:</b>"
        await _safe_edit(q, title, _bd_cal_years_kb(base, fa))
        return
    if act.startswith("caly:"):
        await _ans()
        try:
            base = int(act.split(":")[1])
        except ValueError:
            base = _bd_now_jy() - 25
        title = "📅 <b>سال تولدت را انتخاب کن:</b>" if fa else "📅 <b>Select your birth year:</b>"
        await _safe_edit(q, title, _bd_cal_years_kb(base, fa))
        return
    if act.startswith("calm:"):
        try:
            year = int(act.split(":")[1])
            assert _BD_JY_MIN <= year <= _BD_JY_MAX
        except Exception:
            await _ans("⛔ سال نامعتبر است.", True)
            return
        await _ans()
        if fa:
            title = "🗓 <b>ماه تولدت را انتخاب کن:</b> <code>%d</code>" % year
        else:
            title = "🗓 <b>Select your birth month:</b> <code>%d</code>" % year
        await _safe_edit(q, title, _bd_cal_months_kb(year, fa))
        return
    if act.startswith("cald:"):
        pr = act.split(":")
        try:
            year, month = int(pr[1]), int(pr[2])
            assert _BD_JY_MIN <= year <= _BD_JY_MAX and 1 <= month <= 12
        except Exception:
            await _ans("⛔ نامعتبر است.", True)
            return
        await _ans()
        mname = (_BD_MONTHS_FA if fa else _BD_MONTHS_EN)[month - 1]
        if fa:
            title = "🔢 <b>روز تولدت را انتخاب کن:</b> %s %d" % (mname, year)
        else:
            title = "🔢 <b>Select your birth day:</b> %s %d" % (mname, year)
        await _safe_edit(q, title, _bd_cal_days_kb(year, month, fa))
        return
    if act.startswith("calsave:"):
        pr = act.split(":")
        iso = bd_jalali_to_iso(pr[1], pr[2], pr[3]) if len(pr) == 4 else None
        if not iso:
            await _ans("⛔ تاریخ نامعتبر است.", True)
            return
        now_iso = datetime.now(TZ).isoformat()
        c = _bday_db()
        c.execute("INSERT INTO birthdays(user_id,birth_date,created_at,updated_at) VALUES(?,?,?,?) "
                  "ON CONFLICT(user_id) DO UPDATE SET birth_date=excluded.birth_date, updated_at=excluded.updated_at",
                  (uid, iso, now_iso, now_iso))
        c.commit()
        c.close()
        jy, jm, jd = _jalali_from_iso(iso)
        if fa:
            txt = ("✅ <b>تاریخ تولدت ثبت شد!</b> 🎂\n\n"
                   "🗓 شمسی: <code>%04d/%02d/%02d</code>\n"
                   "📅 میلادی: <code>%s</code>\n\n"
                   "هر سال در همین روز تبریک و هدیهٔ تولد دریافت می‌کنی."
                   ) % (jy, jm, jd, iso)
        else:
            txt = ("✅ <b>Your birthday is saved!</b> 🎂\n\n"
                   "Standard date: <code>%s</code>\n\n"
                   "You will receive birthday greetings and gifts every year on this day.") % (iso,)
        await _ans("✅ ثبت شد")
        await _safe_edit(q, txt, None)
        return
    if act == "manual":
        context.user_data["bd_wait"] = "date"
        if fa:
            txt = ("✍️ <b>تاریخ تولد را دستی وارد کن</b>\n\n"
                   "فرمت‌های قابل قبول:\n"
                   "<code>1382/04/23</code> · <code>1382-04-23</code>\n"
                   "<code>23/04/1382</code> (شمسی)\n"
                   "<code>2000-08-24</code> (میلادی)\n\n"
                   "⚠️ تاریخ تولد دائمی ذخیره می\u200cشود و فقط برای تبریک و هدیه استفاده می\u200cشود.")
        else:
            txt = ("✍️ <b>Enter your birthday manually</b>\n\n"
                   "Accepted formats:\n<code>2000-08-24</code>, <code>2000/8/24</code>, "
                   "<code>1382/04/23</code>, <code>23/04/1382</code>\n\n"
                   "⚠️ Your birthday is saved permanently and used only for greetings and gifts.")
        await _ans()
        await _safe_edit(q, txt, None)
        return
    if act == "del":
        c = _bday_db()
        c.execute("DELETE FROM birthdays WHERE user_id=?", (uid,))
        c.commit()
        c.close()
        await _ans()
        await _safe_edit(q, "🗑 تاریخ تولد حذف شد." if fa else "🗑 Birthday removed.", None)
        return
    if act == "cancel":
        context.user_data.pop("bd_wait", None)
        await _ans()
        await _safe_edit(q, "باشه، لغو شد. 👍" if fa else "Okay, cancelled. 👍", None)
        return
    if act == "back":
        await _ans()
        text, kb = _bd_status_parts(uid)
        await _safe_edit(q, text, kb)
        return
    await _ans()


# ---------- Owner-only panel ----------

BD_PANEL_BTN = "🎂 تولد و مناسبت‌ها"
_BDO_SECTIONS = ("🎂 مدیریت تولد", "🎁 هدیه تولد", "📅 مناسبت‌ها", "✉️ پیام تبریک",
                 "⭐ XP و پاداش مناسبت", "⚙️ تنظیمات", "📊 گزارش تولد")


def _is_bd_owner(uid):
    return bool(uid) and uid == master_owner_id()


def _bdo_kb():
    rows = [["🎂 مدیریت تولد", "🎁 هدیه تولد"],
            ["📅 مناسبت‌ها", "✉️ پیام تبریک"],
            ["⭐ XP و پاداش مناسبت", "⚙️ تنظیمات"],
            ["📊 گزارش تولد"],
            ["⬅️ برگشت"]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)


def _bdo_home_text():
    en = bd_get("bd_enabled", "1") == "1"
    ge = bd_get("bd_gift_enabled", "1") == "1"
    n_occ = 0
    try:
        c = _bday_db()
        n_users = c.execute("SELECT COUNT(*) n FROM birthdays").fetchone()["n"]
        n_occ = c.execute("SELECT COUNT(*) n FROM occasions WHERE active=1").fetchone()["n"]
        c.close()
    except Exception:
        n_users = 0
    return (f"🎂 <b>تولد و مناسبت‌ها</b>\n\n"
            f"سیستم تولد: {'🟢 فعال' if en else '🔴 خاموش'}\n"
            f"هدیه تولد: {'🟢 فعال' if ge else '🔴 خاموش'} ({BD_GIFT_LABELS.get(bd_get('bd_gift_kind', 'xp'))})\n"
            f"کاربران دارای تاریخ تولد: {n_users}\n"
            f"مناسبت‌های فعال: {n_occ}\n\n"
            f"یکی از بخش‌های زیر را انتخاب کن:")


def _bdo_bday_mgmt_view():
    en = "🟢" if bd_get("bd_enabled", "1") == "1" else "🔴"
    rem = "🟢" if bd_get("bd_reminder_enabled", "1") == "1" else "🔴"
    nd = bd_get("bd_reminder_days", "3")
    st = bd_get("bd_send_time", "09:00")
    text = (f"🎂 <b>مدیریت تولد</b>\n\nقابلیت تولد: {en}\nیادآوری قبل از تولد: {rem} ({nd} روز قبل)\n"
            f"ساعت ارسال تبریک: {st}\n\nجلوگیری از دریافت چندبارهٔ هدیه برای هر تولد همیشه فعال است (هر کاربر، هر سال، فقط یک بار).")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("روشن/خاموش قابلیت تولد", callback_data="bd:tog_main")],
        [InlineKeyboardButton("روشن/خاموش یادآوری", callback_data="bd:tog_rem")],
        [InlineKeyboardButton("فاصلهٔ یادآوری: " + nd + " روز", callback_data="bd:cyc_rem")],
        [InlineKeyboardButton("⬅️ بازگشت", callback_data="bd:home")],
    ])
    return text, kb


def _bdo_gift_view():
    ge = "🟢" if bd_get("bd_gift_enabled", "1") == "1" else "🔴"
    gk = bd_get("bd_gift_kind", "xp")
    aud = bd_get("bd_audience", "all")
    amt = bd_get("bd_gift_amount", "50")
    text = (f"🎁 <b>هدیه تولد</b>\n\nوضعیت: {ge}\nنوع هدیه: {BD_GIFT_LABELS.get(gk)}\n"
            f"مقدار: {bd_gift_desc(gk, amt) or '-'}\nمحدودیت: هر کاربر هر سال فقط ۱ بار (خودکار)\n"
            f"مخاطبان: {BD_AUD_LABELS.get(aud)}")
    nxt = BD_GIFT_KINDS[(BD_GIFT_KINDS.index(gk) + 1) % len(BD_GIFT_KINDS)]
    naud = BD_AUDIENCES[(BD_AUDIENCES.index(aud) + 1) % len(BD_AUDIENCES)]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("روشن/خاموش هدیه", callback_data="bd:tog_gift")],
        [InlineKeyboardButton("نوع هدیه ← " + BD_GIFT_LABELS[nxt], callback_data="bd:cyc_kind")],
        [InlineKeyboardButton("تغییر مقدار", callback_data="bd:amt")],
        [InlineKeyboardButton("مخاطبان ← " + BD_AUD_LABELS[naud], callback_data="bd:cyc_aud")],
        [InlineKeyboardButton("⬅️ بازگشت", callback_data="bd:home")],
    ])
    return text, kb


def _bdo_occ_list_view():
    c = _bday_db()
    rows = c.execute("SELECT * FROM occasions ORDER BY id").fetchall()
    c.close()
    lines = ["📅 <b>مناسبت‌ها</b>", ""]
    if not rows:
        lines.append("هنوز مناسبی نساخته‌ای.")
    for o in rows:
        lines.append(f"{'🟢' if o['active'] else '🔴'} <b>{o['name']}</b> — {o['date']}"
                     f" | XP:{o['xp_amount']} | VIP:{o['vip_days']} روز | ارسال خودکار: {'بله' if o['auto_send'] else 'نه'}")
    kb_rows = [[InlineKeyboardButton("➕ افزودن مناسبت", callback_data="bd:occ_add")]]
    for o in rows:
        kb_rows.append([InlineKeyboardButton(("🔴 خاموش " if o["active"] else "🟢 روشن ") + o["name"],
                                             callback_data=f"bd:occ_toggle:{o['id']}"),
                        InlineKeyboardButton("🗑", callback_data=f"bd:occ_del:{o['id']}")])
    kb_rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="bd:home")])
    return "\n".join(lines), InlineKeyboardMarkup(kb_rows)


def _bdo_msg_view():
    st = bd_get("bd_send_time", "09:00")
    tpl = bd_get("bd_congrats_fa", "")
    short = (tpl[:120] + "…") if len(tpl) > 120 else tpl
    text = (f"✉️ <b>پیام‌های تبریک</b>\n\nمتن فعلی:\n<i>{short}</i>\n\n"
            f"می‌توانی از {{name}} برای نام کاربر استفاده کنی.\nساعت ارسال: {st}")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("ویرایش متن تبریک", callback_data="bd:text"),
         InlineKeyboardButton("تغییر ساعت ارسال", callback_data="bd:time")],
        [InlineKeyboardButton("⬅️ بازگشت", callback_data="bd:home")],
    ])
    return text, kb


def _bdo_settings_view():
    nd = bd_get("bd_reminder_days", "3")
    aud = bd_get("bd_audience", "all")
    st = bd_get("bd_send_time", "09:00")
    text = (f"⚙️ <b>تنظیمات تولد و مناسبت</b>\n\nیادآوری: {nd} روز قبل\nساعت ارسال: {st}\n"
            f"مخاطبان هدیه: {BD_AUD_LABELS.get(aud)}\n\nثبت تاریخچهٔ همهٔ هدیه‌ها همیشه فعال است.")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("تغییر فاصلهٔ یادآوری", callback_data="bd:remdays"),
         InlineKeyboardButton("تغییر ساعت ارسال", callback_data="bd:time")],
        [InlineKeyboardButton("⬅️ بازگشت", callback_data="bd:home")],
    ])
    return text, kb


def _bdo_report_text():
    today = datetime.now(TZ).date()
    mmdd = today.isoformat()[5:]
    week_end = (today + timedelta(days=7)).isoformat()[5:]
    c = _bday_db()
    todays = c.execute("SELECT b.user_id,u.first_name FROM birthdays b JOIN users u ON u.user_id=b.user_id WHERE substr(b.birth_date,6,5)=?", (mmdd,)).fetchall()
    upcoming = c.execute("SELECT b.birth_date,u.first_name FROM birthdays b JOIN users u ON u.user_id=b.user_id WHERE substr(b.birth_date,6,5)!=? ORDER BY substr(b.birth_date,6,5)", (mmdd,)).fetchall()
    soon = []
    for i in range(7):
        d = (today + timedelta(days=i)).isoformat()[5:]
        for r in upcoming:
            if r["birth_date"][5:] == d:
                soon.append(f"• {r['first_name'] or '-'} — {r['birth_date']}")
    gifts_today = c.execute("SELECT COUNT(*) n FROM bday_events WHERE kind='birthday' AND substr(created_at,1,10)=?", (today.isoformat(),)).fetchone()["n"]
    wk_start = (today - timedelta(days=today.weekday())).isoformat()
    gifts_week = c.execute("SELECT COUNT(*) n FROM bday_events WHERE kind='birthday' AND substr(created_at,1,10)>=?", (wk_start,)).fetchone()["n"]
    occ_sent = c.execute("SELECT COUNT(*) n FROM bday_events WHERE kind LIKE 'occ%'").fetchone()["n"]
    c.close()
    lines = ["📊 <b>گزارش تولد و مناسبت</b>", "",
             f"<b>تولدهای امروز:</b> {len(todays)}"]
    for r in todays[:20]:
        lines.append(f"🎂 {r['first_name'] or '-'} (<code>{r['user_id']}</code>)")
    lines.append("")
    lines.append("<b>هفتهٔ آینده:</b>")
    lines.extend(soon[:15] if soon else ["—"])
    lines.append("")
    lines.append(f"هدیه‌های امروز: {gifts_today} | این هفته: {gifts_week}")
    lines.append(f"کل ارسال‌های مناسبتی ثبت‌شده: {occ_sent}")
    return "\n".join(lines)


async def _bdo_render(update, context, key):
    """Show an owner section. Uses edit when coming from a callback."""
    views = {"mgmt": _bdo_bday_mgmt_view(), "gift": _bdo_gift_view(), "occ": _bdo_occ_list_view(),
             "msg": _bdo_msg_view(), "cfg": _bdo_settings_view()}
    if key in views:
        text, kb = views[key]
        if hasattr(update, "callback_query") and update.callback_query is not None:
            await update.callback_query.answer()
            try:
                await update.callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                await update.callback_query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    elif key == "report":
        if hasattr(update, "callback_query") and update.callback_query is not None:
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(_bdo_report_text(), parse_mode="HTML")
        else:
            await update.message.reply_text(_bdo_report_text(), parse_mode="HTML")


async def _bdo_open_panel(update, context):
    uid = update.effective_user.id
    context.user_data["bdo_panel"] = True
    await update.message.reply_text(_bdo_home_text(), parse_mode="HTML", reply_markup=_bdo_kb())


async def _bdo_owner_callback(update, context):
    """Handles bd:* callbacks that belong to the OWNER panel (not user bd:set/del)."""
    q = update.callback_query
    uid = q.from_user.id
    data = q.data or ""
    if not _is_bd_owner(uid):
        master_incident("security", f"user {uid} tried owner birthday panel: {data}", severity="warning")
        await q.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    act = data[3:]
    if act == "home":
        await q.answer()
        await q.message.edit_text(_bdo_home_text(), parse_mode="HTML")
        return
    if act == "tog_main":
        bd_set("bd_enabled", "0" if bd_get("bd_enabled", "1") == "1" else "1")
        await _bdo_render(update, context, "mgmt")
        return
    if act == "tog_rem":
        bd_set("bd_reminder_enabled", "0" if bd_get("bd_reminder_enabled", "1") == "1" else "1")
        await _bdo_render(update, context, "mgmt")
        return
    if act == "cyc_rem":
        order = ["1", "3", "7", "14"]
        cur = bd_get("bd_reminder_days", "3")
        bd_set("bd_reminder_days", order[(order.index(cur) + 1) % len(order)] if cur in order else "3")
        await _bdo_render(update, context, "mgmt")
        return
    if act == "tog_gift":
        bd_set("bd_gift_enabled", "0" if bd_get("bd_gift_enabled", "1") == "1" else "1")
        await _bdo_render(update, context, "gift")
        return
    if act == "cyc_kind":
        cur = bd_get("bd_gift_kind", "xp")
        bd_set("bd_gift_kind", BD_GIFT_KINDS[(BD_GIFT_KINDS.index(cur) + 1) % len(BD_GIFT_KINDS)])
        await _bdo_render(update, context, "gift")
        return
    if act == "cyc_aud":
        cur = bd_get("bd_audience", "all")
        bd_set("bd_audience", BD_AUDIENCES[(BD_AUDIENCES.index(cur) + 1) % len(BD_AUDIENCES)])
        await _bdo_render(update, context, "gift")
        return
    if act == "amt":
        await q.answer()
        context.user_data["bdo_wait"] = "amount"
        await q.message.reply_text("💰 مقدار هدیه را بفرست (عدد):\nXP/توکن = واحد، VIP/اشتراک = تعداد روز")
        return
    if act == "text":
        await q.answer()
        context.user_data["bdo_wait"] = "text"
        await q.message.reply_text("✍️ متن تبریک جدید را بفرست (از {name} استفاده کن):")
        return
    if act == "time":
        await q.answer()
        context.user_data["bdo_wait"] = "time"
        await q.message.reply_text("⏰ ساعت ارسال را بفرست (HH:MM):")
        return
    if act == "remdays":
        await q.answer()
        context.user_data["bdo_wait"] = "remdays"
        await q.message.reply_text("🔔 چند روز قبل از تولد یادآوری شود؟ (۱ تا ۱۴)")
        return
    if act == "occ_add":
        await q.answer()
        context.user_data["bdo_wait"] = "occ_name"
        context.user_data["bdo_occ"] = {}
        await q.message.reply_text("➕ نام مناسبت جدید را بفرست:")
        return
    if act.startswith("occ_toggle:"):
        oid = int(act.split(":")[1])
        c = _bday_db()
        c.execute("UPDATE occasions SET active=1-active WHERE id=?", (oid,))
        c.commit()
        c.close()
        await _bdo_render(update, context, "occ")
        return
    if act.startswith("occ_del:"):
        oid = int(act.split(":")[1])
        c = _bday_db()
        c.execute("DELETE FROM occasions WHERE id=?", (oid,))
        c.commit()
        c.close()
        await _bdo_render(update, context, "occ")
        return
    await q.answer()


def _bdo_handle_input(update, context, uid, txt):
    """Consume one pending owner/user text input. Returns True when handled."""
    ud = context.user_data
    wait = ud.get("bdo_wait")
    fa = lang(uid) == "fa"
    if wait == "amount":
        ud.pop("bdo_wait", None)
        try:
            v = float(_bd_en(txt).strip())
            assert 0 < v <= 1000000
        except Exception:
            ud["bdo_wait"] = "amount"
            return False
        bd_set("bd_gift_amount", int(v) if v == int(v) else v)
        asyncio.ensure_future(update.message.reply_text("✅ ذخیره شد.", reply_markup=_bdo_kb()))
        return True
    if wait == "text":
        ud.pop("bdo_wait", None)
        if len(txt) > 800:
            ud["bdo_wait"] = "text"
            return False
        bd_set("bd_congrats_fa", txt)
        asyncio.ensure_future(update.message.reply_text("✅ متن تبریک ذخیره شد.", reply_markup=_bdo_kb()))
        return True
    if wait == "time":
        ud.pop("bdo_wait", None)
        m = re.match(r"^(\d{1,2}):(\d{2})$", _bd_en(txt.strip()))
        if not m or not (0 <= int(m.group(1)) <= 23 and 0 <= int(m.group(2)) <= 59):
            ud["bdo_wait"] = "time"
            return False
        bd_set("bd_send_time", f"{int(m.group(1)):02d}:{m.group(2)}")
        asyncio.ensure_future(update.message.reply_text("✅ ساعت ارسال ذخیره شد.", reply_markup=_bdo_kb()))
        return True
    if wait == "remdays":
        ud.pop("bdo_wait", None)
        try:
            n = int(_bd_en(txt).strip())
            assert 1 <= n <= 14
        except Exception:
            ud["bdo_wait"] = "remdays"
            return False
        bd_set("bd_reminder_days", n)
        asyncio.ensure_future(update.message.reply_text("✅ فاصلهٔ یادآوری ذخیره شد.", reply_markup=_bdo_kb()))
        return True
    occ = ud.get("bdo_occ") or {}
    if wait == "occ_name":
        ud["bdo_occ"] = {"name": txt[:100]}
        ud["bdo_wait"] = "occ_date"
        asyncio.ensure_future(update.message.reply_text("📅 تاریخ مناسبت را بفرست (MM-DD مثل 03-15 یا تاریخ کامل):"))
        return True
    if wait == "occ_date":
        md = bd_parse_mmdd(txt) or (bd_parse_date(txt) or "")[5:] or None
        if not md:
            asyncio.ensure_future(update.message.reply_text("⚠️ فرمت درست نیست. دوباره بفرست (مثل 03-15):"))
            return True
        occ["date"] = md
        ud["bdo_occ"] = occ
        ud["bdo_wait"] = "occ_msg"
        asyncio.ensure_future(update.message.reply_text("✉️ پیام مناسبتی را بفرست (یا «-» برای پیش‌فرض):"))
        return True
    if wait == "occ_msg":
        occ["message"] = "" if txt.strip() == "-" else txt[:1000]
        ud["bdo_occ"] = occ
        ud["bdo_wait"] = "occ_xp"
        asyncio.ensure_future(update.message.reply_text("⭐ پاداش XP همهٔ کاربران؟ (عدد یا 0):"))
        return True
    if wait == "occ_xp":
        try:
            occ["xp_amount"] = max(0, int(_bd_en(txt).strip()))
        except Exception:
            asyncio.ensure_future(update.message.reply_text("⚠️ عدد معتبر بفرست:"))
            return True
        ud["bdo_occ"] = occ
        ud["bdo_wait"] = "occ_vip"
        asyncio.ensure_future(update.message.reply_text("💎 روزهای VIP هدیه؟ (عدد یا 0):"))
        return True
    if wait == "occ_vip":
        try:
            occ["vip_days"] = max(0, int(_bd_en(txt).strip()))
        except Exception:
            asyncio.ensure_future(update.message.reply_text("⚠️ عدد معتبر بفرست:"))
            return True
        ud.pop("bdo_wait", None)
        ud.pop("bdo_occ", None)
        c = _bday_db()
        c.execute("INSERT INTO occasions(name,date,message,xp_amount,vip_days,active,auto_send,created_at) VALUES(?,?,?,?,?,1,1,?)",
                  (occ["name"], occ["date"], occ.get("message", ""), occ.get("xp_amount", 0), occ.get("vip_days", 0), datetime.now(TZ).isoformat()))
        c.commit()
        c.close()
        master_log(uid, "occasion_created", occ["name"], occ["date"])
        asyncio.ensure_future(update.message.reply_text("✅ مناسبت ساخته شد و هر سال خودکار ارسال می‌شود.", reply_markup=_bdo_kb()))
        return True
    return False


async def _bdo_route(update, context, uid, txt):
    """Owner-panel ReplyKeyboard routing. Returns True when the label was consumed."""
    ud = context.user_data
    fa = lang(uid) == "fa"
    if txt == BD_PANEL_BTN:
        if not _is_bd_owner(uid):
            master_incident("security", f"user {uid} tried to open the birthday panel", severity="warning")
            await update.message.reply_text("⛔ دسترسی ندارید.")
            return True
        await _bdo_open_panel(update, context)
        return True
    if not (_is_bd_owner(uid) and ud.get("bdo_panel")):
        return False
    if txt in _BDO_SECTIONS or txt in ("⬅️ برگشت", "⬅️ Back"):
        if txt in ("⬅️ برگشت", "⬅️ Back"):
            ud.pop("bdo_panel", None)
            ud.pop("bdo_wait", None)
            ud.pop("bdo_occ", None)
            title, markup = _manager_main_keyboard(uid)
            await update.message.reply_text(title, parse_mode="HTML", reply_markup=markup)
            return True
        key = {"🎂 مدیریت تولد": "mgmt", "🎁 هدیه تولد": "gift", "📅 مناسبت‌ها": "occ",
               "✉️ پیام تبریک": "msg", "⚙️ تنظیمات": "cfg", "📊 گزارش تولد": "report"}.get(txt)
        if key == "msg" or key == "cfg":
            text, kb = _bdo_msg_view() if key == "msg" else _bdo_settings_view()
            await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        elif key == "report":
            await update.message.reply_text(_bdo_report_text(), parse_mode="HTML")
        elif key:
            await _bdo_render(update, context, key)
        return True
    return False


# ---------- scheduler (runs once per day, after the configured send time) ----------

async def birthday_occasion_job(context):
    now = datetime.now(TZ)
    today = now.date()
    mmdd = today.isoformat()[5:]
    year = today.year
    c = _bday_db()
    enabled = bd_get("bd_enabled", "1") == "1"
    has_occ = c.execute("SELECT 1 FROM occasions WHERE active=1 AND auto_send=1 LIMIT 1").fetchone()
    if not enabled and not has_occ:
        c.close()
        return
    if now.strftime("%H:%M") < bd_get("bd_send_time", "09:00"):
        c.close()
        return
    if bd_get("last_bd_run", "") == today.isoformat():
        c.close()
        return
    bd_set("last_bd_run", today.isoformat())

    if enabled:
        for r in c.execute("SELECT user_id FROM birthdays WHERE substr(birth_date,6,5)=?", (mmdd,)).fetchall():
            uid = int(r["user_id"])
            if user_blocked(uid) or not bd_audience_ok(uid):
                continue
            eid = _bday_claim(c, uid, year, "birthday")
            if not eid:
                continue
            c.commit()
            name = ""
            try:
                u = c.execute("SELECT first_name FROM users WHERE user_id=?", (uid,)).fetchone()
                name = u["first_name"] if u else ""
            except Exception:
                pass
            desc = ""
            gkind = bd_get("bd_gift_kind", "xp")
            if gkind != "none" and bd_get("bd_gift_enabled", "1") == "1":
                try:
                    amt = float(bd_get("bd_gift_amount", "50"))
                except ValueError:
                    amt = 50.0
                desc = _bday_reward(c, eid, uid, gkind, amt)
                c.commit()
            msg = bd_congrats_text(name) + (("\n" + desc) if desc else "")
            try:
                await context.bot.send_message(uid, msg)
            except Exception:
                pass
        if bd_get("bd_reminder_enabled", "1") == "1":
            try:
                nd = max(1, int(bd_get("bd_reminder_days", "3")))
            except ValueError:
                nd = 3
            tmd = (today + timedelta(days=nd)).isoformat()[5:]
            for r in c.execute("SELECT user_id FROM birthdays WHERE substr(birth_date,6,5)=?", (tmd,)).fetchall():
                uid = int(r["user_id"])
                if user_blocked(uid) or not bd_audience_ok(uid):
                    continue
                if not _bday_claim(c, uid, year, "reminder"):
                    continue
                c.commit()
                try:
                    await context.bot.send_message(uid, f"🔔 سلام! {nd} روز تا تولدت مونده 🎂 آمادهٔ جشن باش!")
                except Exception:
                    pass

    for o in c.execute("SELECT * FROM occasions WHERE active=1 AND auto_send=1 AND substr(date,1,5)=? AND last_sent_year<?",
                       (mmdd, year)).fetchall():
        oid = int(o["id"])
        c.execute("UPDATE occasions SET last_sent_year=? WHERE id=?", (year, oid))
        c.commit()
        base = o["message"] or f"🎊 مناسبت «{o['name']}» بر همهٔ شما مبارک!"
        count = 0
        for u in c.execute("SELECT user_id FROM users WHERE blocked=0").fetchall():
            uid = int(u["user_id"])
            eid = _bday_claim(c, uid, year, f"occ{oid}", oid)
            if not eid:
                continue
            desc = ""
            if int(o["xp_amount"] or 0) > 0:
                n = int(o["xp_amount"])
                c.execute("UPDATE users SET xp=COALESCE(xp,0)+? WHERE user_id=?", (n, uid))
                c.execute("INSERT INTO xp_log(user_id,amount,reason,created_at) VALUES(?,?,?,?)", (uid, n, f"occasion_{oid}", now.isoformat()))
                desc += f"\n⭐ +{n} XP"
            if int(o["vip_days"] or 0) > 0:
                desc += "\n" + _bday_reward(c, eid, uid, "vip", int(o["vip_days"]))
            c.commit()
            try:
                await context.bot.send_message(uid, base + desc)
                count += 1
            except Exception:
                pass
        master_log(master_owner_id() or 0, "occasion_sent", o["name"], f"{count} users")
    c.close()


# ---------- integration hooks ----------

_OLD_MMK_BDAY = _manager_main_keyboard


def _manager_main_keyboard(uid):
    title, markup = _OLD_MMK_BDAY(uid)
    if _is_bd_owner(uid):
        rows = [[str(x) for x in r] for r in markup.keyboard]
        rows.insert(1, [BD_PANEL_BTN])
        markup = ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)
    return title, markup


_OLD_TEXT_ROUTER_BDAY = text_router


async def text_router(update, context):
    if update.message and update.message.text:
        uid = update.effective_user.id
        txt = update.message.text.strip()
        # Navigation labels always win over any pending birthday input state.
        if txt in ("🏠 منوی اصلی", "🏠 Main Menu", "⬅️ برگشت", "⬅️ Back"):
            context.user_data.pop("bd_wait", None)
            context.user_data.pop("bdo_wait", None)
            context.user_data.pop("bdo_occ", None)
        else:
            # User-side pending birthday-date input (from /birthday buttons).
            if context.user_data.get("bd_wait") == "date":
                context.user_data.pop("bd_wait", None)
                parsed = bd_parse_any_date(txt)
                if not parsed:
                    await update.message.reply_text(
                        "⚠️ فرمت درست نیست.\nمثال شمسی: <code>1382/04/23</code> یا <code>23/04/1382</code>\nمثال میلادی: <code>2000-08-24</code>",
                        parse_mode="HTML")
                    return
                iso, _kind = parsed
                c = _bday_db()
                c.execute("INSERT INTO birthdays(user_id,birth_date,created_at,updated_at) VALUES(?,?,?,?) "
                          "ON CONFLICT(user_id) DO UPDATE SET birth_date=excluded.birth_date, updated_at=excluded.updated_at",
                          (uid, iso, datetime.now(TZ).isoformat(), datetime.now(TZ).isoformat()))
                c.commit()
                c.close()
                jy, jm, jd = _jalali_from_iso(iso)
                mname = (_BD_MONTHS_FA if lang(uid) == "fa" else _BD_MONTHS_EN)[jm - 1]
                await update.message.reply_text(
                    f"✅ ثبت شد! 🎂 تولدت هر سال {jd} {mname} جشن گرفته می‌شود.\n📅 تاریخ استاندارد: <code>{iso}</code>",
                    parse_mode="HTML")
                return
            # Owner panel routing / pending owner inputs.
            if await _bdo_route(update, context, uid, txt):
                return
            if _is_bd_owner(uid) and context.user_data.get("bdo_wait"):
                if _bdo_handle_input(update, context, uid, txt):
                    return
    return await _OLD_TEXT_ROUTER_BDAY(update, context)


# ===================== END BIRTHDAY & OCCASIONS MODULE =====================

