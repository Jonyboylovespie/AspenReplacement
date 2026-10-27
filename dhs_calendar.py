"""DHS day types, regenerated from the school's published calendars.

Jupitr's bundled letter-day calendar is the offline seed. The DHS one-page
letter-day PDF marks advisory days, and the district website calendar carries
changes made during the year: moved letter days, PLC schedules and closures.
"""
from datetime import date, datetime, timezone
import html
import json
import logging
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import requests

from storage import atomic_write

logger = logging.getLogger(__name__)
DATA = Path(__file__).resolve().parent / "schedule"
DHS_PAGE = "https://dhs.darienps.org/about/calendars-and-bell-schedules"
DISTRICT_CALENDAR = "https://www.darienps.org/district-information/district-calendar"
HEADERS = {"User-Agent": "BetterAspen schedule updater"}
REFRESH_SECONDS = 6 * 60 * 60
RETRY_SECONDS = 30 * 60
MONTHS = {name: number for number, name in enumerate((
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December"), 1)}
PDF_DAY = re.compile(r"(?<!\d)(\d{1,2})([A-H])([*]?)")
PDF_MONTH = re.compile(r"(" + "|".join(MONTHS) + r") [‘']?\d{2}")
SCHOOL_YEAR = re.compile(r"(20\d{2})\s*[-–]\s*(20\d{2})")


def get(url, **kwargs):
    response = requests.get(url, headers=HEADERS, timeout=30, **kwargs)
    response.raise_for_status()
    return response


def fetch_letter_days():
    """Read letter and advisory days from the DHS one-page calendar PDF."""
    soup = BeautifulSoup(get(DHS_PAGE).text, "html.parser")
    url = next((urljoin(DHS_PAGE, link["href"]) for link in soup.find_all("a", href=True)
                if "one page letter day" in link.get_text(" ", strip=True).lower()), None)
    match = re.search(r'"download_url":"([^"]+)"', get(url).text) if url else None
    if not match:
        raise RuntimeError("The DHS letter-day PDF was not found.")
    pdf = get(html.unescape(match[1]).replace("\\u0026", "&").replace("\\/", "/")).content
    with tempfile.TemporaryDirectory() as directory:
        source, text = Path(directory) / "calendar.pdf", Path(directory) / "calendar.txt"
        source.write_bytes(pdf)
        subprocess.run(["pdftotext", "-raw", str(source), str(text)], check=True, timeout=60)
        return parse_letter_days(text.read_text(errors="replace"))


def parse_letter_days(text):
    """Letter days appear as day-number tokens such as 14C, with * marking advisory."""
    year = SCHOOL_YEAR.search(text)
    start = int(year[1]) if year else date.today().year
    month, days = None, {}
    for line in text.splitlines():
        heading = PDF_MONTH.search(line)
        if heading:
            month = MONTHS[heading[1]]
            continue
        if month is None:
            continue
        for token in PDF_DAY.finditer(line):
            try:
                key = date(start if month >= 8 else start + 1, month, int(token[1])).isoformat()
            except ValueError:
                continue
            days[key] = f"{token[2]} Day" + (" - Advisory Schedule" if token[3] else "")
    if not days:
        raise RuntimeError("No letter days were found in the DHS PDF.")
    return days


def fetch_district_events(today=None):
    """Return the DHS School Calendar event titles for each date of the school year."""
    today = today or date.today()
    start = today.year if today.month >= 7 else today.year - 1
    element = BeautifulSoup(get(DISTRICT_CALENDAR).text, "html.parser").select_one(".fsCalendar[id^=fsEl_]")
    if not element:
        raise RuntimeError("The district calendar was not found.")
    element_id = element["id"].removeprefix("fsEl_")
    events = {}
    for year, month in [(start, month) for month in range(8, 13)] + [(start + 1, month) for month in range(1, 7)]:
        events.update(parse_district_events(get(
            urljoin(DISTRICT_CALENDAR, f"/fs/elements/{element_id}"),
            params={"cal_date": f"{year}-{month:02d}-01", "is_draft": "false",
                    "is_load_more": "true", "parent_id": element_id}).text))
    if not any(events.values()):
        raise RuntimeError("The district calendar had no DHS events.")
    return events


def parse_district_events(body):
    events = {}
    for box in BeautifulSoup(body, "html.parser").select(".fsCalendarDaybox"):
        stamp = box.select_one(".fsCalendarDate[data-year][data-month][data-day]")
        try:
            # Finalsite months are zero-based.
            key = date(int(stamp["data-year"]), int(stamp["data-month"]) + 1, int(stamp["data-day"])).isoformat()
        except (TypeError, ValueError):
            continue
        titles = events.setdefault(key, [])
        for info in box.select(".fsCalendarInfo"):
            calendar = info.select_one(".fsElementEventColorIcon")
            title = info.select_one(".fsCalendarEventTitle")
            if calendar and title and calendar.get("title") == "DHS School Calendar":
                titles.append(" ".join((title.get("title") or title.get_text()).split()))
    return events


def district_day_type(titles, current):
    """Apply one date's district events to its day type. None means no school."""
    if any(re.match(r"no school\b|.*\brecess\b", title, re.I) for title in titles):
        return None
    letter = suffix = None
    for title in titles:
        if match := re.fullmatch(r"([A-H]) Day", title):
            letter = match[1]
        elif match := re.search(r'"([A-H])" Day.*All Periods Meet', title, re.I):
            letter, suffix = match[1], "All Periods Meet"
    for title in titles:
        if "PLC" in title and re.search(r"delay|late start", title, re.I):
            suffix = "PLC Late Start Schedule"
        elif re.search(r"early dismissal", title, re.I):
            suffix = ("PLC " if "PLC" in title else "") + "Early Dismissal Schedule"
    # Advisory and first-day labels belong to the date, so they stay with it.
    base = re.fullmatch(r"([A-H]) Day(?: - (.+))?", current or "")
    letter = letter or (base[1] if base else None)
    suffix = suffix or (base[2] if base else None)
    if not letter:
        return current
    return f"{letter} Day" + (f" - {suffix}" if suffix else "")


def merge_calendar(seed, letter_days=None, events=None):
    days = dict(seed)
    for key, label in (letter_days or {}).items():
        # The PDF has letters and advisory marks, but no PLC or first-day labels.
        current = days.get(key, "")
        keep = current.startswith(label + " - ") and "Advisory" not in current
        days[key] = current if keep else label
    for key, titles in (events or {}).items():
        label = district_day_type(titles, days.get(key, ""))
        if label is None:
            days.pop(key, None)
        elif label:
            days[key] = label
    return dict(sorted(days.items()))


class ScheduleCalendar:
    """Serve the newest calendar and regenerate it in the background."""

    def __init__(self, directory, auto_refresh=True):
        self.path = Path(directory) / "dhs-calendar.json"
        self.bell = json.loads((DATA / "bell-schedule.json").read_text())
        self.seed = json.loads((DATA / "letter-day-calendar.json").read_text())["days"]
        self.auto_refresh = auto_refresh
        self.lock = threading.Lock()
        self.refreshing = False
        self.next_refresh = 0
        try:
            saved = json.loads(self.path.read_text())
        except (OSError, ValueError):
            saved = {}
        # Keep each source's last good copy so one failing site never drops the other.
        self.sources = {key: saved.get(key) for key in ("letterDays", "districtEvents", "generatedAt")} \
            if isinstance(saved, dict) else {}

    def view(self):
        with self.lock:
            if self.auto_refresh and not self.refreshing and time.monotonic() >= self.next_refresh:
                self.refreshing = True
                threading.Thread(target=self.refresh, daemon=True, name="dhs-calendar").start()
            sources = dict(self.sources)
        return {"calendar": merge_calendar(self.seed, sources.get("letterDays"), sources.get("districtEvents")),
                "bell": self.bell, "generatedAt": sources.get("generatedAt")}

    def refresh(self):
        updated = {}
        for key, fetch in (("letterDays", fetch_letter_days), ("districtEvents", fetch_district_events)):
            try:
                updated[key] = fetch()
            except Exception:
                logger.warning("Unable to refresh DHS %s", key, exc_info=True)
        with self.lock:
            if updated:
                self.sources = {**self.sources, **updated, "generatedAt": datetime.now(timezone.utc).isoformat()}
                try:
                    atomic_write(self.path, json.dumps(self.sources).encode())
                except OSError:
                    logger.warning("Unable to save the DHS calendar", exc_info=True)
            self.next_refresh = time.monotonic() + (REFRESH_SECONDS if len(updated) == 2 else RETRY_SECONDS)
            self.refreshing = False
