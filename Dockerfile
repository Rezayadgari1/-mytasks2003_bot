FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy deployment-time runtime patches
COPY bot.py config.py database.py ai_runtime_patch.py feature_visibility_patch.py forced_subscription_patch.py ./

# Apply runtime resilience, feature visibility and forced-subscription controls before startup.
RUN python3 ai_runtime_patch.py
RUN python3 feature_visibility_patch.py
RUN python3 forced_subscription_patch.py

# Health check the same database path used by the bot.
HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
    CMD python3 -c "import os,sqlite3; p=os.environ.get('DB_PATH','goals.db'); c=sqlite3.connect(p,timeout=10); c.execute('SELECT 1'); c.close()" || exit 1

CMD ["python3", "bot.py"]