# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

ARG FORCE_REBUILD=2026-09-08-menu-speed-back-v8
RUN echo "MyTasks rebuild: ${FORCE_REBUILD}"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py config.py database.py ./

RUN python3 - <<'PY'
from pathlib import Path
p = Path('bot.py')
s = p.read_text(encoding='utf-8')
marker = '# ===================== FINAL UI / FLOW QUALITY LAYER ====================='
if marker in s:
    s = s.split(marker, 1)[0].rstrip() + '\n\nif __name__ == "__main__":\n    main()\n'

a = s.index('def db():')
b = s.index('\ndef init_db():', a)
new_db = '''def db():
    # Fast connection path. WAL is initialized during startup.
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA synchronous=NORMAL")
    return c
'''
s = s[:a] + new_db + s[b+1:]

a = s.index('async def navigation_callback(update, context):')
b = s.index('\n# Keep the settings callback', a)
new_nav = '''async def navigation_callback(update, context):
    # Reliable inline navigation to the real root menu.
    q = update.callback_query
    if not q:
        return
    uid = q.from_user.id
    try:
        await q.answer()
    except Exception:
        pass
    clear_flow(context)
    if (q.data or "") == "nav:main":
        await q.message.edit_text(
            _root_menu_text(uid),
            parse_mode="HTML",
            reply_markup=_compact_root_inline(uid),
        )
        return
'''
s = s[:a] + new_nav + s[b:]
p.write_text(s, encoding='utf-8')
print('direct bot.py UI repair applied')
PY

RUN python3 -m py_compile bot.py config.py database.py
RUN python3 - <<'PY'
from pathlib import Path
import ast
s = Path('bot.py').read_text(encoding='utf-8')
t = ast.parse(s)
assert '# ===================== FINAL UI / FLOW QUALITY LAYER =====================' not in s
calls = [n for n in ast.walk(t) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'main']
assert len(calls) == 1
assert calls[0].lineno == len(s.splitlines())
print('bot.py syntax and startup checks OK')
PY

HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
    CMD python3 -c "import os,sqlite3; p=os.environ.get('DB_PATH','goals.db'); c=sqlite3.connect(p,timeout=10); c.execute('SELECT 1'); c.close()" || exit 1

CMD ["python3", "bot.py"]
