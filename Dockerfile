FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY bot.py config.py database.py ./

HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
    CMD python3 -c "import sqlite3; c=sqlite3.connect('goals.db',timeout=5); c.execute('SELECT 1'); c.close()" || exit 1

# Run the bot
CMD ["python3", "bot.py"]
