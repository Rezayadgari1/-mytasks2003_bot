# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

ARG FORCE_REBUILD=2026-09-08-direct-botpy-v1
RUN echo "MyTasks rebuild: ${FORCE_REBUILD}"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py config.py database.py ./

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
