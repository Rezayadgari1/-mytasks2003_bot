# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

ARG FORCE_REBUILD=2026-09-08-goal-time-v3
RUN echo "MyTasks rebuild: ${FORCE_REBUILD}"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py config.py database.py ./

RUN python3 - <<'PY'
from pathlib import Path
p = Path('bot.py')
s = p.read_text(encoding='utf-8')

# Install the robust time parser BEFORE main() is called.
parser_marker = '# === DOCKER FINAL ROBUST GOAL TIME PARSER ==='
parser_block = r'''
# === DOCKER FINAL ROBUST GOAL TIME PARSER ===
def parse_time(value):
    """Parse ASCII, Persian and Arabic clock input as HH:MM."""
    if value is None:
        return None
    text = str(value).strip()
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    text = text.translate(trans)
    text = text.replace("：", ":").replace("٫", ":").replace("،", ":").replace("．", ".")
    import re
    text = re.sub(r"\s+", "", text)
    m = re.fullmatch(r"([01]?\d|2[0-3])[:.]([0-5]\d)", text)
    if not m:
        m = re.fullmatch(r"([01]?\d|2[0-3])([0-5]\d)", text)
    if not m:
        return None
    return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
'''
if parser_marker in s:
    start = s.index(parser_marker)
    s = s[:start].rstrip() + "\n"

# Replace the edit-time callback with a final implementation. It supports
# all common callback shapes used by the time buttons and persists the value
# against the authenticated user's own goal only.
repair_marker = '# === DOCKER FINAL EDIT TIME BUTTON REPAIR ==='
repair_block = r'''
# === DOCKER FINAL EDIT TIME BUTTON REPAIR ===
_OLD_EDIT_TIME_CALLBACK = edit_time_callback

async def edit_time_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    data = str(q.data or "")
    try:
        await q.answer()
    except Exception:
        pass

    raw = data[len("edit_time:"):] if data.startswith("edit_time:") else ""
    if raw == "custom":
        context.user_data["awaiting_edit_time"] = True
        context.user_data["awaiting_custom_edit_time"] = True
        context.user_data["_flow_started_at"] = datetime.now(TZ).isoformat()
        await q.message.reply_text(
            "🕐 ساعت جدید را بفرست. مثال: 18:30 یا ۱۸:۳۰",
            reply_markup=nav_keyboard(uid),
        )
        return

    parts = raw.split(":") if raw else []
    gid = context.user_data.get("edit_reminder_id") or context.user_data.get("edit_id")
    tm = None

    # Supported forms:
    # edit_time:18:00
    # edit_time:<goal_id>:18:00
    # edit_time:18:00:<goal_id>
    if len(parts) == 2:
        candidate = f"{parts[0]}:{parts[1]}"
        parsed = parse_time(candidate)
        if parsed is not None:
            tm = parsed
        elif parts[0].isdigit():
            gid = int(parts[0])
            tm = parse_time(parts[1])
    elif len(parts) == 3:
        a, b, c = parts
        if a.isdigit():
            gid = int(a)
            tm = parse_time(f"{b}:{c}")
        elif c.isdigit():
            gid = int(c)
            tm = parse_time(f"{a}:{b}")

    if tm is None:
        # If the original callback can handle this exact legacy shape, keep it.
        try:
            return await _OLD_EDIT_TIME_CALLBACK(update, context)
        except Exception:
            await q.message.reply_text("❌ ساعت نامعتبر است. مثال: 18:30 یا ۱۸:۳۰")
            return

    if not gid:
        await q.message.reply_text("❌ هدف برای ویرایش پیدا نشد.")
        return

    c = db()
    try:
        row = c.execute(
            "SELECT id FROM goals WHERE id=? AND user_id=?",
            (int(gid), uid),
        ).fetchone()
        if not row:
            await q.message.reply_text("❌ هدف پیدا نشد.")
            return
        c.execute(
            "UPDATE goals SET reminder_time=? WHERE id=? AND user_id=?",
            (tm, int(gid), uid),
        )
        c.commit()
    finally:
        c.close()

    context.user_data.pop("edit_reminder_id", None)
    context.user_data.pop("awaiting_edit_time", None)
    context.user_data.pop("awaiting_custom_edit_time", None)
    context.user_data.pop("_flow_started_at", None)
    await q.message.edit_text(
        f"✅ ساعت یادآوری هدف روی {tm} تنظیم شد.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ بازگشت", callback_data=f"edit:{int(gid)}")],
            [main_menu_button(uid)],
        ]),
    )
'''
if repair_marker in s:
    start = s.index(repair_marker)
    s = s[:start].rstrip() + "\n"

# Put both runtime overrides before the startup call.
main_marker = '\nif __name__ == "__main__":\n'
if main_marker not in s:
    raise SystemExit('main startup marker not found')
insert = "\n" + parser_block + "\n" + repair_block + "\n"
s = s.replace(main_marker, insert + main_marker, 1)
p.write_text(s, encoding='utf-8')
PY

RUN python3 -m py_compile bot.py config.py database.py

RUN python3 - <<'PY'
from pathlib import Path
import ast
s = Path('bot.py').read_text(encoding='utf-8')
t = ast.parse(s)
main_defs = [n for n in t.body if isinstance(n, ast.FunctionDef) and n.name == 'main']
assert main_defs, 'main() definition missing'
assert len(main_defs) == 1, 'duplicate main() definitions'
main_calls = [n for n in ast.walk(t) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'main']
assert len(main_calls) == 1, 'unexpected main() call count'
assert main_calls[0].lineno > main_defs[0].lineno, 'main() is called before its definition'
parser_defs = [n for n in t.body if isinstance(n, ast.FunctionDef) and n.name == 'parse_time']
edit_defs = [n for n in t.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'edit_time_callback']
assert parser_defs and edit_defs, 'required runtime overrides missing'
assert parser_defs[-1].lineno < main_calls[0].lineno, 'parse_time override must be before main() call'
assert edit_defs[-1].lineno < main_calls[0].lineno, 'edit_time_callback override must be before main() call'
print(f'goal time repair OK: parse_time={parser_defs[-1].lineno}, edit_time_callback={edit_defs[-1].lineno}, main_call={main_calls[0].lineno}')
PY

HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
    CMD python3 -c "import os,sqlite3; p=os.environ.get('DB_PATH','goals.db'); c=sqlite3.connect(p,timeout=10); c.execute('SELECT 1'); c.close()" || exit 1

CMD ["python3", "bot.py"]
