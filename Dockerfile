FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py config.py database.py ai_runtime_patch.py feature_visibility_patch.py forced_subscription_patch.py forced_subscription_patch_v2.py admin_management_patch.py feature_control_final_patch.py feature_control_final_v3_patch.py goals_quality_patch.py quality_ux_security_patch.py quality_ux_security_patch_v2.py ./

RUN python3 ai_runtime_patch.py
RUN python3 feature_visibility_patch.py
RUN python3 forced_subscription_patch.py
RUN python3 forced_subscription_patch_v2.py
RUN python3 admin_management_patch.py
RUN python3 feature_control_final_patch.py
RUN python3 feature_control_final_v3_patch.py
RUN python3 goals_quality_patch.py
RUN python3 quality_ux_security_patch.py
RUN python3 quality_ux_security_patch_v2.py

RUN python3 -m py_compile bot.py config.py database.py ai_runtime_patch.py feature_visibility_patch.py forced_subscription_patch.py forced_subscription_patch_v2.py admin_management_patch.py feature_control_final_patch.py feature_control_final_v3_patch.py goals_quality_patch.py quality_ux_security_patch.py quality_ux_security_patch_v2.py

HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
    CMD python3 -c "import os,sqlite3; p=os.environ.get('DB_PATH','goals.db'); c=sqlite3.connect(p,timeout=10); c.execute('SELECT 1'); c.close()" || exit 1

CMD ["python3", "bot.py"]