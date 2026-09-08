FROM python:3.12-slim

WORKDIR /app

ARG FORCE_REBUILD=2026-09-08-menu-speed-back-v6
RUN echo "MyTasks rebuild: ${FORCE_REBUILD}"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py config.py database.py ./

RUN python3 -c 'from pathlib import Path; p=Path("bot.py"); s=p.read_text(encoding="utf-8"); marker="# ===================== FINAL UI / FLOW QUALITY LAYER ====================="; s=(s.split(marker,1)[0].rstrip()+chr(10)+chr(10)+"if __name__ == \"__main__\":\n    main()\n") if marker in s else s; p.write_text(s,encoding="utf-8")'

RUN python3 -c 'from pathlib import Path; p=Path("bot.py"); s=p.read_text(encoding="utf-8"); a=s.index("def db():"); b=s.index("\ndef init_db():",a); new="def db():\n    \"\"\"Open SQLite quickly. WAL is initialized during startup, not on every button tap.\"\"\"\n    c=sqlite3.connect(DB_PATH,timeout=30)\n    c.row_factory=sqlite3.Row\n    c.execute("PRAGMA busy_timeout=30000")\n    c.execute("PRAGMA foreign_keys=ON")\n    c.execute("PRAGMA synchronous=NORMAL")\n    return c\n"; s=s[:a]+new+s[b+1:]; p.write_text(s,encoding="utf-8")'

RUN python3 -c 'from pathlib import Path; p=Path("bot.py"); s=p.read_text(encoding="utf-8"); a=s.index("async def navigation_callback(update, context):"); b=s.index("\n# Keep the settings callback",a); new="async def navigation_callback(update, context):\n    \"\"\"Reliable inline navigation to the real root menu.\"\"\"\n    q=update.callback_query\n    if not q:\n        return\n    uid=q.from_user.id\n    try:\n        await q.answer()\n    except Exception:\n        pass\n    clear_flow(context)\n    if (q.data or \"\")==\"nav:main\":\n        await q.message.edit_text(_root_menu_text(uid),parse_mode=\"HTML\",reply_markup=_compact_root_inline(uid))\n        return\n"; s=s[:a]+new+s[b:]; p.write_text(s,encoding="utf-8")'

RUN python3 -m py_compile bot.py config.py database.py

HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
    CMD python3 -c "import os,sqlite3; p=os.environ.get('DB_PATH','goals.db'); c=sqlite3.connect(p,timeout=10); c.execute('SELECT 1'); c.close()" || exit 1

CMD ["python3", "bot.py"]
