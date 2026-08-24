"""Run the full bot test suite in one command:
    python3 tests/run_all.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SUITES = [
    "smoke_test.py",
    "test_new_features.py",
    "test_inplace_nav.py",
    "test_reports_layer.py",
    "test_scale_reliability.py",
    "test_prices_ai.py",
    "test_access_control.py",
    "test_birthday.py",
    "test_birthday_calendar.py",
    "test_booking_flow.py",
]


def main() -> int:
    os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")
    failures = []
    for suite in SUITES:
        path = os.path.join(HERE, suite)
        print(f"\n===== {suite} =====")
        proc = subprocess.run([sys.executable, path], cwd=ROOT)
        if proc.returncode != 0:
            failures.append(suite)

    print("\n================ SUMMARY ================")
    if failures:
        print("FAILED:", ", ".join(failures))
        return 1
    print(f"ALL {len(SUITES)} SUITES PASSED ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
