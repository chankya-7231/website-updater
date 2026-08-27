#!/usr/bin/env python3
"""
Watchdog: alerts if the main monitor hasn't successfully checked a
website in longer than expected.

GitHub's own `schedule:` cron trigger can silently delay or skip very
frequent scheduled workflows for hours under platform load - this has
actually happened (a 10-hour gap that swallowed a daily status update
and any real site changes published during it). This script runs on
its own, separate, less-frequent schedule so it isn't subject to the
same deprioritization, and its only job is to notice a gap and say so,
instead of it going silently unnoticed.
"""

import glob
import json
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:  # pragma: no cover - fallback for very old Python
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# The main schedule runs every 22 minutes; alert if nothing has
# succeeded in over ~3.5x that. Wide enough to absorb normal jitter
# without false alarms, tight enough to catch a real multi-hour gap.
GAP_ALERT_MINUTES = 80


def now_ist() -> datetime:
    return datetime.now(IST)


def latest_checked_at():
    latest = None
    for path in glob.glob(os.path.join(DATA_DIR, "snapshot_*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        checked_at = data.get("checked_at")
        if not checked_at:
            continue
        try:
            dt = datetime.fromisoformat(checked_at)
        except ValueError:
            continue
        if latest is None or dt > latest:
            latest = dt
    return latest


def send_alert(subject: str, body: str) -> None:
    gmail_address = os.environ.get("GMAIL_ADDRESS", "").strip()
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    recipients = [os.environ.get(f"RECIPIENT_EMAIL_{i}", "").strip() for i in (1, 2, 3)]
    recipients = [r for r in recipients if r]

    if not gmail_address or not gmail_app_password:
        print("Watchdog: Gmail credentials not configured - cannot send alert.")
        return
    if not recipients:
        print("Watchdog: no email recipients configured - cannot send alert.")
        return

    message = MIMEText(body)
    message["From"] = gmail_address
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.starttls()
            server.login(gmail_address, gmail_app_password)
            server.sendmail(gmail_address, recipients, message.as_string())
        print("Watchdog: alert email sent.")
    except smtplib.SMTPException as exc:
        print(f"Watchdog: failed to send alert email: {exc}")


def main() -> None:
    latest = latest_checked_at()
    now = now_ist()

    if latest is None:
        print("Watchdog: no snapshot data yet - nothing to check.")
        return

    gap_minutes = (now - latest).total_seconds() / 60
    print(f"Watchdog: last successful check was {gap_minutes:.0f} minute(s) ago.")

    if gap_minutes > GAP_ALERT_MINUTES:
        subject = "NEET Counselling Monitor - Scheduler Gap Detected"
        body = (
            "The monitor hasn't completed a successful website check in "
            f"about {gap_minutes:.0f} minutes (last one: "
            f"{latest.strftime('%d %b %Y, %I:%M %p')} IST).\n\n"
            "This usually means GitHub's own scheduler delayed or skipped "
            "the regular checks - a known GitHub Actions limitation, not a "
            "problem with the monitored website or this code. It should "
            "recover on its own; this message exists so you find out now "
            "instead of silently missing updates during the gap."
        )
        print("Watchdog: gap exceeds threshold, sending alert.")
        send_alert(subject, body)


if __name__ == "__main__":
    main()
