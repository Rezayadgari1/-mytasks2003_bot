# -*- coding: utf-8 -*-
"""One-shot: Jalali birthday calendar picker (سال → ماه → روز) + multi-format
manual entry, applied identically to bot.py and _bday_layer.py.

- bd:set / bd:cal        -> year picker (paged)
- bd:caly:<base>         -> page years
- bd:calm:<y>            -> month picker (all 12 months)
- bd:cald:<y>:<m>        -> day picker (only valid days of that month)
- bd:calsave:<y>:<m>:<d> -> validate + convert to standard ISO and save
- bd:manual              -> manual entry (Jalali & Gregorian formats, Persian digits)
- bd:back                -> back to status screen

No new handlers are registered and nothing is re-wrapped, so there is no way to
re-introduce the previous RecursionError from nested text_router wrapping.
"""

NEW_BLOCK = r'''# ---------- user-facing command (privacy: own row only) ----------
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


'''

REPL_A = [
    '                parsed = bd_parse_any_date(txt)',
    '                if not parsed:',
    '                    await update.message.reply_text(',
    '                        "⚠️ فرمت درست نیست.\\nمثال شمسی: <code>1382/04/23</code> یا <code>23/04/1382</code>\\nمثال میلادی: <code>2000-08-24</code>",',
    '                        parse_mode="HTML")',
    '                    return',
    '                iso, _kind = parsed',
]

REPL_B = [
    '                jy, jm, jd = _jalali_from_iso(iso)',
    '                mname = (_BD_MONTHS_FA if lang(uid) == "fa" else _BD_MONTHS_EN)[jm - 1]',
    '                await update.message.reply_text(',
    '                    f"✅ ثبت شد! 🎂 تولدت هر سال {jd} {mname} جشن گرفته می\u200cشود.\\n📅 تاریخ استاندارد: <code>{iso}</code>",',
    '                    parse_mode="HTML")',
]


def patch(path):
    lines = open(path, encoding='utf-8').read().split('\n')
    starts = [i for i, l in enumerate(lines) if l.strip().startswith('# ---------- user-facing command')]
    ends = [i for i, l in enumerate(lines) if 'Owner-only panel' in l and l.strip().startswith('#')]
    assert len(starts) == 1 and len(ends) == 1 and starts[0] < ends[0], (path, starts, ends)
    new_lines = NEW_BLOCK.split('\n')
    while new_lines[-1] == '':
        new_lines.pop()
    new_lines += ['', '']
    lines[starts[0]:ends[0]] = new_lines
    src = '\n'.join(lines)

    lines = src.split('\n')
    ia = [i for i, l in enumerate(lines) if l.strip() == 'iso = bd_parse_date(txt)']
    assert len(ia) == 1, (path, 'branch A', ia)
    i = ia[0]
    assert 'if not iso:' in lines[i + 1] and 'فرمت درست نیست' in lines[i + 2] and lines[i + 3].strip() == 'return', path
    lines[i:i + 4] = REPL_A
    src = '\n'.join(lines)

    ib = [i for i, l in enumerate(lines) if '✅ ثبت شد!' in l and 'reply_text' in l]
    assert len(ib) == 1, (path, 'branch B', ib)
    lines[ib[0]:ib[0] + 1] = REPL_B
    src = '\n'.join(lines)

    open(path, 'w', encoding='utf-8').write(src)
    print('patched', path)


patch('bot.py')
patch('_bday_layer.py')
print('birthday calendar patch OK')
