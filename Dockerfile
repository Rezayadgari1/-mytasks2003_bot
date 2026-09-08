# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

ARG FORCE_REBUILD=2026-09-08-goal-time-v2
RUN echo "MyTasks rebuild: ${FORCE_REBUILD}"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py config.py database.py ./

RUN python3 - <<'PY'
from pathlib import Path
p=Path('bot.py')
s=p.read_text(encoding='utf-8')
marker='\n# === DOCKER FINAL ROBUST GOAL TIME PARSER ===\n'
if marker not in s:
    s += r'''

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
    p.write_text(s,encoding='utf-8')
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
print(f'bot.py startup check OK: main defined at line {main_defs[0].lineno}, called at line {main_calls[0].lineno}')
PY

HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
    CMD python3 -c "import os,sqlite3; p=os.environ.get('DB_PATH','goals.db'); c=sqlite3.connect(p,timeout=10); c.execute('SELECT 1'); c.close()" || exit 1

CMD ["python3", "bot.py"]
