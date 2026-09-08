FROM python:3.12-slim

WORKDIR /app

ARG FORCE_REBUILD=2026-09-08-menu-speed-back-v5
RUN echo "MyTasks rebuild: ${FORCE_REBUILD}"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py config.py database.py ./

RUN python3 -c "import base64;exec(base64.b64decode('CmltcG9ydCByZQpmcm9tIHBhdGhsaWIgaW1wb3J0IFBhdGgKcD1QYXRoKCJib3QucHkiKQpzPXAucmVhZF90ZXh0KGVuY29kaW5nPSJ1dGYtOCIpCnM9cmUuc3ViKHInXG4jID09PT09PT09PT09PT09PT09PT09PSBGSU5BTCBVSSAvIEZMT1cgUVVBTElUWSBMQVlFUiA9PT09PT09PT09PT09PT09PT09PT1cbi4qP1xuaWYgX19uYW1lX18gPT0gIl9fbWFpbl9fIjpccypcblxzKm1haW5cKFwpXHMqJCcsICdcblxuaWYgX19uYW1lX18gPT0gIl9fbWFpbl9fIjpcbiAgICBtYWluKClcbicsIHMsIGZsYWdzPXJlLlMpCm5ld19kYj1cIlwiXCJkZWYgZGIoKToKICAgIFwiXCJcIk9wZW4gU1FMaXRlIHF1aWNrbHkuIFdBTCBpcyBpbml0aWFsaXplZCBkdXJpbmcgc3RhcnR1cCwgbm90IG9uIGV2ZXJ5IGJ1dHRvbiB0YXAuXCJcIlwiCiAgICBjID0gc3FsaXRlMy5jb25uZWN0KERCX1BBVEgsIHRpbWVvdXQ9MzApCiAgICBjLnJvd19mYWN0b3J5ID0gc3FsaXRlMy5Sb3cKICAgIGMuZXhlY3V0ZShcIlBSQUdNQSBidXN5X3RpbWVvdXQ9MzAwMDBcIilcXG4gICAgYy5leGVjdGUoXCJQUkFHTUEgZm9yZWlnbl9rZXlzPU9OXCIpXFxuICAgIGMuZXhlY3V0ZShcIlBSQUdNQSBzeW5jaHJvbm91cz1OT1JNQUxcIilcXG4gICAgcmV0dXJuIGNcXG5cIlwiXCIKcyxuPXJlLnN1Ym5yKCd0XFxuJykK'))" 

RUN python3 -m py_compile bot.py config.py database.py

HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
    CMD python3 -c "import os,sqlite3; p=os.environ.get('DB_PATH','goals.db'); c=sqlite3.connect(p,timeout=10); c.execute('SELECT 1'); c.close()" || exit 1

CMD ["python3", "bot.py"]
