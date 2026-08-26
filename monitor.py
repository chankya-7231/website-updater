#!/usr/bin/env python3
"""
NEET / MBBS Counselling Website Monitor
-----------------------------------------
This script checks a list of government counselling websites for ANY
change (new text, new links, new PDFs, new announcements) and sends
alerts by WhatsApp (via CallMeBot) and Email (via Gmail) to three
people whenever something changes.

You do not need to understand every line of this file to use it.
Read SETUP_GUIDE.md for the step-by-step instructions. This file is
heavily commented so that even if you are new to Python you can
follow along.
"""

import hashlib
import json
import logging
import os
import smtplib
import sys
import urllib.parse
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from bs4 import BeautifulSoup

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:  # pragma: no cover - fallback for very old Python
    import pytz
    IST = pytz.timezone("Asia/Kolkata")


# ---------------------------------------------------------------------------
# 1. CONFIGURATION
# ---------------------------------------------------------------------------

# The websites we want to watch for updates.
# "name" is just a friendly label used in alert messages.
# "use_browser": True fetches with a real headless browser (Playwright),
# which executes JavaScript so it can see content that's added to the page
# after the initial load - needed for KNRUHS, whose real notices only
# appear this way. "use_browser": False uses a plain HTTP request instead,
# which never renders JavaScript (so it can miss JS-added content) but is
# far less likely to be flagged as a bot - needed for MCC, whose Akamai
# bot-protection started hard-blocking every request once we switched it
# to browser-based fetching, even with a browser fingerprint disguised.
WEBSITES = [
    {
        "name": "KNRUHS Telangana Admission Notifications",
        "url": "https://www.knruhs.telangana.gov.in/admission-notification/",
        "use_browser": True,
    },
    {
        "name": "MCC UG Medical Counselling",
        "url": "https://mcc.nic.in/ug-medical-counselling/",
        "use_browser": False,
    },
    {
        "name": "MCC E-Services Schedule (UG)",
        "url": "https://mcc.nic.in/eservices-schedule-ug/",
        "use_browser": False,
    },
    {
        "name": "MCC News & Events (UG Medical)",
        "url": "https://mcc.nic.in/news-events-ug-medical/",
        "use_browser": False,
    },
    {
        "name": "MCC Important Links (UG)",
        "url": "https://mcc.nic.in/important-link-ug/",
        "use_browser": False,
    },
    {
        "name": "MCC Current Events (UG)",
        "url": "https://mcc.nic.in/current-events-ug/",
        "use_browser": False,
    },
]

# Folder where we keep a "snapshot" (a saved copy) of each website's
# content so we can compare today's version with the last version we saw.
# This folder is committed back to the GitHub repository after every run,
# so the history is remembered between runs (GitHub Actions itself gives
# us a brand-new blank computer every single time it runs, so we must save
# our own memory to disk/git).
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

INITIALIZED_FLAG_FILE = os.path.join(DATA_DIR, "initialized.flag")
DAILY_STATUS_FILE = os.path.join(DATA_DIR, "last_daily_status.json")
FETCH_HEALTH_FILE = os.path.join(DATA_DIR, "fetch_health.json")

# If a page fails to fetch this many checks IN A ROW (~2 hours at the
# 15-minute schedule), send one alert saying the site is unreachable, so a
# broken URL or a new bot-block gets noticed within hours instead of silently
# going undetected for days or weeks.
FETCH_FAILURE_ALERT_THRESHOLD = 8

# The daily "everything is fine, no changes found" message is sent once a
# day, close to 8:00 AM IST. Because the workflow runs every 15 minutes,
# we send it the first time we notice the clock has reached this window
# on a new calendar day (IST).
DAILY_STATUS_HOUR_IST = 8

# HTTP settings used when downloading each page.
REQUEST_TIMEOUT_SECONDS = 30
# NOTE: this deliberately does NOT contain the word "bot" or any custom
# suffix. Many government-site WAFs pattern-match "bot"/"crawler"/"spider"
# in the User-Agent (case-insensitive) and silently 403 the request -
# that was happening here (our old UA ended in "...NEETCounsellingMonitorBot/1.0")
# even though the fetch code itself was working fine.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Lines of visible text shorter than this are ignored when looking for new
# content - short fragments (page numbers, single dates, nav labels) are the
# most common source of noisy false-positive alerts.
MIN_TEXT_CHUNK_LENGTH = 20


# ---------------------------------------------------------------------------
# 2. LOGGING (so we can see what happened in the GitHub Actions log)
# ---------------------------------------------------------------------------

class ISTFormatter(logging.Formatter):
    """Makes log timestamps show Indian Standard Time instead of UTC."""

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, IST)
        return dt.strftime("%Y-%m-%d %H:%M:%S IST")


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(ISTFormatter("%(asctime)s [%(levelname)s] %(message)s"))
logger = logging.getLogger("monitor")
logger.setLevel(logging.INFO)
logger.addHandler(handler)


def now_ist() -> datetime:
    return datetime.now(IST)


# ---------------------------------------------------------------------------
# 3. FETCHING AND ANALYSING A PAGE
# ---------------------------------------------------------------------------

_playwright = None
_browser = None
_browser_context = None


def _get_browser_context():
    """
    Lazily launches one shared headless Chromium browser, reused for every
    monitored page in this run. These government sites render their actual
    notice/PDF listings with client-side JavaScript after the initial page
    load (confirmed: a plain HTTP GET returns only the generic page shell -
    nav menu, footer, unrelated static PDFs - never the real notices, even
    though the request itself succeeds with a 200). A real browser executes
    that JavaScript exactly like a human visitor's browser would, so it
    sees the same content a person checking the site manually would see.
    """
    global _playwright, _browser, _browser_context
    if _browser_context is None:
        from playwright.sync_api import sync_playwright

        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(
            headless=True,
            # These two flags remove the most common signals bot-detection
            # (e.g. Akamai) uses to tell headless Chromium apart from a
            # real browser: an automation-controlled banner/CDP fingerprint
            # and a visibly small default window size.
            args=["--disable-blink-features=AutomationControlled"],
        )
        _browser_context = _browser.new_context(
            user_agent=USER_AGENT,
            locale="en-US",
            viewport={"width": 1366, "height": 768},
        )
        # navigator.webdriver is normally True in an automated browser and
        # False in a real one - this is one of the simplest, most common
        # checks bot-detection scripts run. Override it before any page
        # script gets a chance to read it.
        _browser_context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
    return _browser_context


def close_browser() -> None:
    """Shuts down the shared browser at the end of a run."""
    global _playwright, _browser, _browser_context
    if _browser is not None:
        _browser.close()
    if _playwright is not None:
        _playwright.stop()
    _browser = None
    _browser_context = None
    _playwright = None


# Text signatures of common CDN/WAF block/error pages. These pages often
# embed a fresh random request/reference ID every single time, which would
# otherwise look like genuine "new content" on every check.
CDN_BLOCK_PAGE_SIGNATURES = (
    "errors.edgesuite.net",
    "you don't have permission to access",
    "access denied",
    "request unsuccessful. incapsula incident id",
    "attention required! | cloudflare",
)


def is_cdn_block_page(html: str) -> bool:
    lowered = html.lower()
    return any(sig in lowered for sig in CDN_BLOCK_PAGE_SIGNATURES)


def fetch_page_browser(url: str) -> str:
    """Downloads the fully rendered HTML of a page, including anything
    added to it by JavaScript after the initial load."""
    context = _get_browser_context()
    page = context.new_page()
    try:
        page.goto(url, timeout=REQUEST_TIMEOUT_SECONDS * 1000, wait_until="domcontentloaded")
        # Give client-side JavaScript (e.g. an AJAX-loaded notices list)
        # a moment to finish populating the page after the initial HTML.
        page.wait_for_timeout(3000)
        return page.content()
    finally:
        page.close()


def fetch_page_http(url: str) -> str:
    """Downloads the raw HTML of a page with a plain HTTP request (no
    JavaScript execution). Used for sites whose bot-protection blocks a
    real browser harder than it blocks a plain request."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
    }
    response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.text


def fetch_page(site: dict) -> str:
    if site.get("use_browser", False):
        return fetch_page_browser(site["url"])
    return fetch_page_http(site["url"])


def extract_snapshot(url: str, html: str) -> dict:
    """
    Turns raw HTML into a simple "snapshot" we can compare over time:
      - text_chunks: distinct meaningful lines of visible text on the page
      - links: every link (href) found on the page, as full URLs
      - pdfs: every link that points to a PDF (or other downloadable file)

    Many government sites show a rotating/reshuffled "latest news" widget,
    so the exact same content can appear in a different order (or only a
    random subset at a time) on every single load. Comparing a hash of the
    WHOLE page, or comparing only against the immediately previous check,
    made already-seen notices get flagged as "new" again every time they
    cycled back into view. Splitting into individual text lines/links and
    tracking everything ever seen (see compute_new_items) fixes that: an
    item is only "new" the first time it's ever observed, no matter how
    much the page reshuffles afterwards.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove tags that don't contain real content, so they don't cause
    # false alarms (e.g. ad scripts, tracking pixels, style blocks).
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text_chunks = set()
    for line in soup.get_text(separator="\n").split("\n"):
        line = " ".join(line.split())
        if len(line) >= MIN_TEXT_CHUNK_LENGTH:
            text_chunks.add(line)

    links = set()
    pdfs = set()
    downloadable_extensions = (".pdf", ".doc", ".docx", ".xls", ".xlsx")

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        if not href or href.startswith("javascript:") or href.startswith("#"):
            continue
        absolute_url = urllib.parse.urljoin(url, href)
        links.add(absolute_url)
        if absolute_url.lower().endswith(downloadable_extensions):
            pdfs.add(absolute_url)

    return {
        "links": sorted(links),
        "pdfs": sorted(pdfs),
        "text_chunks": sorted(text_chunks),
        "checked_at": now_ist().isoformat(),
    }


def snapshot_file_for(url: str) -> str:
    """Each monitored URL gets its own small JSON file under data/."""
    safe_name = hashlib.md5(url.encode("utf-8")).hexdigest()
    return os.path.join(DATA_DIR, f"snapshot_{safe_name}.json")


def load_previous_snapshot(url: str):
    path = snapshot_file_for(url)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read previous snapshot for %s: %s", url, exc)
        return None


def save_snapshot(url: str, snapshot: dict) -> None:
    path = snapshot_file_for(url)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)


def compute_new_items(previous_snapshot: dict, current_snapshot: dict):
    """
    Compares the current snapshot against EVERYTHING ever seen on this page
    (not just the last check) and returns only genuinely new items.

    We deliberately never report "removed" items: on these government sites
    a link/PDF/text line disappearing is almost always just the page's
    rotating widget cycling it out of view, not a real removal, so treating
    disappearance as noteworthy was a major source of misleading alerts.
    """
    known_links = set(previous_snapshot.get("known_links", previous_snapshot.get("links", [])))
    known_pdfs = set(previous_snapshot.get("known_pdfs", previous_snapshot.get("pdfs", [])))
    # Older snapshots (before this fix) never tracked text_chunks at all.
    # is_text_baseline_only tells the caller to adopt this run's text as the
    # starting point rather than reporting every line as "new".
    is_text_baseline_only = "known_text_chunks" not in previous_snapshot
    known_text_chunks = set(previous_snapshot.get("known_text_chunks", []))

    current_links = set(current_snapshot["links"])
    current_pdfs = set(current_snapshot["pdfs"])
    current_text_chunks = set(current_snapshot["text_chunks"])

    new_links = sorted(current_links - known_links)
    new_pdfs = sorted(current_pdfs - known_pdfs)
    new_text_chunks = [] if is_text_baseline_only else sorted(current_text_chunks - known_text_chunks)

    updated_known = {
        "known_links": sorted(known_links | current_links),
        "known_pdfs": sorted(known_pdfs | current_pdfs),
        "known_text_chunks": sorted(known_text_chunks | current_text_chunks),
    }
    return new_links, new_pdfs, new_text_chunks, updated_known


# ---------------------------------------------------------------------------
# 4. SENDING ALERTS (WhatsApp via CallMeBot, and Email via Gmail)
# ---------------------------------------------------------------------------

def get_whatsapp_recipients() -> list:
    """
    Reads up to 3 WhatsApp recipients from environment variables / secrets.
    Each recipient needs their OWN phone number + their OWN CallMeBot API
    key (this is what keeps everyone's number private - nobody shares
    their key with anyone else, they each get it directly from CallMeBot).
    """
    recipients = []
    for i in (1, 2, 3):
        phone = os.environ.get(f"WHATSAPP_PHONE_{i}", "").strip()
        api_key = os.environ.get(f"WHATSAPP_APIKEY_{i}", "").strip()
        if phone and api_key:
            recipients.append({"phone": phone, "api_key": api_key})
        elif phone or api_key:
            logger.warning(
                "WhatsApp recipient %d is only half-configured "
                "(need both phone and api key) - skipping.", i
            )
    return recipients


def get_email_recipients() -> list:
    recipients = []
    for i in (1, 2, 3):
        email_addr = os.environ.get(f"RECIPIENT_EMAIL_{i}", "").strip()
        if email_addr:
            recipients.append(email_addr)
    return recipients


def send_whatsapp_message(text: str) -> None:
    """Sends the same message to every configured WhatsApp recipient."""
    recipients = get_whatsapp_recipients()
    if not recipients:
        logger.warning("No WhatsApp recipients configured - skipping WhatsApp.")
        return

    for recipient in recipients:
        try:
            params = {
                "phone": recipient["phone"],
                "text": text,
                "apikey": recipient["api_key"],
            }
            response = requests.get(
                "https://api.callmebot.com/whatsapp.php",
                params=params,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 200:
                logger.info("WhatsApp message sent to %s...", recipient["phone"][:6])
            else:
                logger.error(
                    "CallMeBot returned status %s for %s: %s",
                    response.status_code, recipient["phone"][:6], response.text[:200],
                )
        except requests.RequestException as exc:
            # We deliberately do NOT crash the whole script if one
            # message fails to send - the others should still go out.
            logger.error("Failed to send WhatsApp to %s...: %s", recipient["phone"][:6], exc)


def send_email_message(subject: str, body: str) -> None:
    """
    Sends one email to all configured recipients, with every recipient
    address directly in the "To" header. Putting real recipients in "To"
    (rather than BCC-ing them while "To" is the sender's own address) is
    deliberate: mail providers commonly score BCC-only mail as more
    spam-like since the visible recipient doesn't match who received it,
    which was causing test emails to go missing/land in spam.
    """
    gmail_address = os.environ.get("GMAIL_ADDRESS", "").strip()
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    recipients = get_email_recipients()

    if not gmail_address or not gmail_app_password:
        logger.warning("Gmail credentials not configured - skipping email.")
        return
    if not recipients:
        logger.warning("No email recipients configured - skipping email.")
        return

    message = MIMEMultipart()
    message["From"] = gmail_address
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=REQUEST_TIMEOUT_SECONDS) as server:
            server.starttls()
            server.login(gmail_address, gmail_app_password)
            server.sendmail(gmail_address, recipients, message.as_string())
        logger.info("Email sent to %d recipient(s).", len(recipients))
    except smtplib.SMTPException as exc:
        logger.error("Failed to send email: %s", exc)


def send_alert(subject: str, body: str) -> None:
    """Sends the same alert on both channels."""
    logger.info("Dispatching alert: %s", subject)
    send_whatsapp_message(f"{subject}\n\n{body}")
    send_email_message(subject, body)


# ---------------------------------------------------------------------------
# 5. DAILY "NO CHANGES FOUND" STATUS MESSAGE
# ---------------------------------------------------------------------------

def load_last_daily_status_date() -> str:
    if not os.path.exists(DAILY_STATUS_FILE):
        return ""
    try:
        with open(DAILY_STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("date", "")
    except (json.JSONDecodeError, OSError):
        return ""


def save_last_daily_status_date(date_str: str) -> None:
    with open(DAILY_STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump({"date": date_str}, f)


def maybe_send_daily_status(any_changes_today: bool) -> None:
    """
    Sends one "no changes found, system is running fine" message per day,
    around 8:00 AM IST. If changes WERE found and already alerted during
    today's run, we still record today's date so we don't ALSO send a
    separate "no changes" message later the same day.
    """
    current = now_ist()
    today_str = current.strftime("%Y-%m-%d")
    last_sent_date = load_last_daily_status_date()

    if last_sent_date == today_str:
        return  # Already handled today.

    if current.hour != DAILY_STATUS_HOUR_IST:
        return  # Not our daily window yet.

    if not any_changes_today:
        subject = "NEET Counselling Monitor - Daily Status: No Changes Found"
        body = (
            f"Good morning! As of {current.strftime('%d %b %Y, %I:%M %p')} IST, "
            f"the automated monitor checked all {len(WEBSITES)} counselling websites "
            "and found no changes since the last alert.\n\n"
            "This is just a daily confirmation that the system is up and running. "
            "You will be alerted immediately (any time, day or night) the moment "
            "a real change is detected.\n\nMonitored pages:\n"
            + "\n".join(f"- {site['name']}: {site['url']}" for site in WEBSITES)
        )
        send_alert(subject, body)

    save_last_daily_status_date(today_str)


# ---------------------------------------------------------------------------
# 6. TEST MESSAGE ON FIRST DEPLOYMENT (or when manually requested)
# ---------------------------------------------------------------------------

def send_test_message() -> None:
    current = now_ist()
    subject = "NEET Counselling Monitor - Test Message (Setup Successful)"
    body = (
        "This is a test message confirming your NEET/MBBS counselling website "
        f"monitor is set up correctly and running as of {current.strftime('%d %b %Y, %I:%M %p')} IST.\n\n"
        "From now on, you will receive:\n"
        "- An immediate alert whenever any of the 4 monitored pages changes\n"
        "- One daily status update at around 8:00 AM IST confirming the system is active\n\n"
        "Monitored pages:\n"
        + "\n".join(f"- {site['name']}: {site['url']}" for site in WEBSITES)
    )
    send_alert(subject, body)


# ---------------------------------------------------------------------------
# 7. MAIN LOGIC
# ---------------------------------------------------------------------------

def load_fetch_health() -> dict:
    if not os.path.exists(FETCH_HEALTH_FILE):
        return {}
    try:
        with open(FETCH_HEALTH_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_fetch_health(health: dict) -> None:
    with open(FETCH_HEALTH_FILE, "w", encoding="utf-8") as f:
        json.dump(health, f, indent=2)


def record_fetch_failure(name: str, url: str, error: str) -> None:
    """
    Tracks consecutive fetch failures per site. If a site has been
    unreachable for a sustained run of checks (not just one transient
    blip), sends a one-time alert - so a broken URL or a new bot-block
    gets noticed within hours instead of silently going undetected for
    days or weeks, which is what happened before this existed.
    """
    health = load_fetch_health()
    entry = health.get(url, {"consecutive_failures": 0, "alerted": False})
    entry["consecutive_failures"] += 1
    entry["last_error"] = error
    entry["last_checked_at"] = now_ist().isoformat()

    if entry["consecutive_failures"] >= FETCH_FAILURE_ALERT_THRESHOLD and not entry["alerted"]:
        logger.warning("Sending sustained-failure alert for %s", name)
        subject = f"NEET Counselling Monitor - Site Unreachable: {name}"
        body = (
            f"The monitor has failed to reach this page for "
            f"{entry['consecutive_failures']} checks in a row:\n\n"
            f"{name}\n{url}\n\nLatest error: {error}\n\n"
            "This usually means the page's URL changed, or the site is "
            "blocking automated requests. The monitor will keep retrying "
            "and will send another message once it's reachable again."
        )
        send_alert(subject, body)
        entry["alerted"] = True

    health[url] = entry
    save_fetch_health(health)


def record_fetch_success(name: str, url: str) -> None:
    health = load_fetch_health()
    entry = health.get(url)
    if entry and entry.get("alerted"):
        logger.info("Sending recovery alert for %s", name)
        subject = f"NEET Counselling Monitor - Site Reachable Again: {name}"
        body = (
            "Good news - the monitor can reach this page again after "
            f"{entry['consecutive_failures']} failed check(s):\n\n"
            f"{name}\n{url}"
        )
        send_alert(subject, body)
    if url in health:
        del health[url]
        save_fetch_health(health)


def process_website(site: dict) -> bool:
    """
    Checks a single website. Returns True if a change alert was sent.
    Any error (site down, network issue, etc.) is logged and swallowed so
    it never crashes the run or triggers a false "changed" alert.
    """
    name, url = site["name"], site["url"]
    logger.info("Checking: %s (%s)", name, url)

    try:
        html = fetch_page(site)
    except Exception as exc:  # noqa: BLE001 - Playwright/requests each raise their own types
        logger.error("Could not fetch %s: %s", name, exc)
        record_fetch_failure(name, url, str(exc))
        return False

    if is_cdn_block_page(html):
        # A CDN/WAF (e.g. Akamai) blocked the request and served an error
        # page instead of the real page. These include a random reference
        # ID on every single request, which would otherwise look like
        # "new text" on every check and spam an alert every run. Treat
        # this exactly like a failed fetch instead.
        logger.error("Could not fetch %s: blocked by site's CDN/WAF (error page returned)", name)
        record_fetch_failure(name, url, "CDN/WAF block page returned instead of real content")
        return False

    record_fetch_success(name, url)

    try:
        current_snapshot = extract_snapshot(url, html)
    except Exception as exc:  # noqa: BLE001 - parsing must never crash the run
        logger.error("Could not parse %s: %s", name, exc)
        return False

    previous_snapshot = load_previous_snapshot(url)

    if previous_snapshot is None:
        # First time we've ever seen this page - just save the baseline.
        # We do NOT send a "changed" alert here, because there is nothing
        # to compare against yet (that would be a false positive).
        logger.info("No previous snapshot for %s - saving baseline.", name)
        baseline = dict(current_snapshot)
        baseline["known_links"] = current_snapshot["links"]
        baseline["known_pdfs"] = current_snapshot["pdfs"]
        baseline["known_text_chunks"] = current_snapshot["text_chunks"]
        save_snapshot(url, baseline)
        return False

    new_links, new_pdfs, new_text_chunks, updated_known = compute_new_items(
        previous_snapshot, current_snapshot
    )

    updated_snapshot = dict(current_snapshot)
    updated_snapshot.update(updated_known)

    if not (new_links or new_pdfs or new_text_chunks):
        logger.info("No changes for %s.", name)
        save_snapshot(url, updated_snapshot)
        return False

    changes = []
    if new_pdfs:
        changes.append(f"{len(new_pdfs)} new PDF/document link(s) added.")
    if new_links:
        changes.append(f"{len(new_links)} new link(s) added.")
    if new_text_chunks:
        changes.append(f"{len(new_text_chunks)} new text line(s) added.")

    logger.info("CHANGE DETECTED for %s: %s", name, "; ".join(changes))

    current_time = now_ist().strftime("%d %b %Y, %I:%M %p")
    body_lines = [
        f"A change was detected on: {name}",
        f"Time: {current_time} IST",
        f"Change type: {'; '.join(changes)}",
        f"Visit: {url}",
    ]
    if new_pdfs:
        body_lines.append("\nNew PDF/document link(s):")
        body_lines.extend(f"- {pdf}" for pdf in new_pdfs[:10])
    if new_links:
        body_lines.append("\nNew link(s):")
        body_lines.extend(f"- {link}" for link in new_links[:10])
    if new_text_chunks:
        body_lines.append("\nNew text on the page:")
        body_lines.extend(f"- {chunk}" for chunk in new_text_chunks[:10])

    subject = f"NEET Counselling Update Detected - {name}"
    send_alert(subject, "\n".join(body_lines))

    save_snapshot(url, updated_snapshot)
    return True


def main() -> None:
    logger.info("=== Monitor run started ===")

    is_first_ever_run = not os.path.exists(INITIALIZED_FLAG_FILE)
    force_test = os.environ.get("SEND_TEST_MESSAGE", "false").strip().lower() == "true"

    any_changes = False
    try:
        for site in WEBSITES:
            try:
                changed = process_website(site)
                any_changes = any_changes or changed
            except Exception as exc:  # noqa: BLE001 - one bad site must not stop the others
                logger.error("Unexpected error while processing %s: %s", site["name"], exc)
    finally:
        close_browser()

    if is_first_ever_run:
        logger.info("First-ever run detected - sending welcome/test message.")
        send_test_message()
        with open(INITIALIZED_FLAG_FILE, "w", encoding="utf-8") as f:
            f.write(now_ist().isoformat())
    elif force_test:
        logger.info("Manual test message requested - sending test message.")
        send_test_message()

    maybe_send_daily_status(any_changes)

    logger.info("=== Monitor run finished ===")


if __name__ == "__main__":
    main()
