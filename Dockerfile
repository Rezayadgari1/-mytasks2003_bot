FROM python:3.12-slim

WORKDIR /app

ARG FORCE_REBUILD=2026-09-08-menu-speed-back-v7
RUN echo "MyTasks rebuild: ${FORCE_REBUILD}"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py config.py database.py ./

RUN python3 - <<'PY'
from pathlib import Path
import re

p = Path('bot.py')
s = p.read_text(encoding='utf-8')

# Never truncate bot.py. Remove early startup calls so the complete file loads,
# including its final navigation fixes, then start once at the very end.
s = re.sub(r'\nif __name__\s*==\s*["\']__main__["\']\s*:\s*\n\s*main\(\)\s*\n?', '\n', s)

# If an old final UI marker exists, keep the code after it. It contains runtime
# navigation/feature definitions and must not be discarded at build time.

s = s.rstrip() + '\n\n# Final startup after every function/router definition.\nif __name__ == "__main__":\n    main()\n'
p.write_text(s, encoding='utf-8')
PY

RUN python3 -m py_compile bot.py config.py database.py
RUN python3 - <<'PY'
from pathlib import Path
import ast
s=Path('bot.py').read_text(encoding='utf-8')
t=ast.parse(s)
calls=[n.lineno for n in ast.walk(t) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='main']
assert len(calls)==1, calls
assert calls[0] == len(s.splitlines()), (calls, len(s.splitlines()))
print('bot.py startup order OK')
PY

HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
    CMD python3 -c "import os,sqlite3; p=os.environ.get('DB_PATH','goals.db'); c=sqlite3.connect(p,timeout=10); c.execute('SELECT 1'); c.close()" || exit 1

CMD ["python3", "bot.py"]
