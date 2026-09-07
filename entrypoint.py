import os
import subprocess
import sys

os.chdir('/app')

PATCHES = [
    'ai_runtime_patch.py',
    'feature_visibility_patch.py',
    'forced_subscription_patch.py',
    'forced_subscription_patch_v2.py',
    'admin_management_patch.py',
    'feature_control_final_patch.py',
    'feature_control_final_v3_patch.py',
    'goals_quality_patch.py',
    'quality_ux_security_patch.py',
    'quality_ux_security_patch_v2.py',
]

for patch in PATCHES:
    path = os.path.join('/app', patch)
    if os.path.exists(path):
        subprocess.run([sys.executable, path], check=True)

os.execv(sys.executable, [sys.executable, '/app/bot.py'])
