FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY bot.py config.py database.py ./

# Run the bot
CMD ["python3", "bot.py"]
