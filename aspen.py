"""Request-based, read-only access to the logged-in student's Aspen portal."""
from datetime import datetime, timezone
import json
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote, urljoin

from bs4 import BeautifulSoup
import requests

ORIGIN = "https://aspen.darienps.org"
COOKIE_NAMES = {"JSESSIONID", "VITHAR_CSRF", "deploymentId", "locale", "cf_clearance"}
ACTIVITY_PREFERENCES = '<?xml version="1.0" encoding="UTF-8"?><preference-set><pref id="dateRange" type="int">4</pref></preference-set>'
CLASS_LIST_PATH = "/aspen/portalClassList.do?navkey=academics.classes.list"
# Aspen serves the matrix only after this "Current Schedule" navigation, which
# redirects to it. Its default term is the current quarter.
SCHEDULE_PATH = "/aspen/studentScheduleContextList.do?navkey=myInfo.sch.list"
SCHEDULE_PERIODS = range(1, 9)
HISTORY_REFRESH_SECONDS = 60 * 60


class AspenError(Exception):
    pass


class AuthenticationRequired(AspenError):
    pass


class CookieImportError(ValueError):
    """A cookie-import error safe to display without including credentials."""


def cookies_from_values(values):
    """Build scoped Aspen cookies from manually entered values."""
    if not isinstance(values, dict):
        raise CookieImportError("Enter your Aspen cookie values.")
    fields = {
        "appSession": ("JSESSIONID", "/app", True),
        "csrf": ("VITHAR_CSRF", "/", True),
        "desktopSession": ("JSESSIONID", "/aspen", False),
        "clearance": ("cf_clearance", "/", False),
    }
    cookies = []
    for field, (name, path, required) in fields.items():
        value = values.get(field, "")
        if not isinstance(value, str):
            raise CookieImportError(f"Enter a valid {name} value for {path}.")
        value = value.strip()
        if required and not value:
            raise CookieImportError(f"Enter the {name} value for {path}.")
        if len(value) > 8192 or any(ord(c) < 33 or ord(c) > 126 or c in ';,' for c in value):
            raise CookieImportError(f"The {name} value for {path} is invalid. Copy only its value.")
        if value:
            cookies.append({"name": name, "value": value, "domain": "aspen.darienps.org",
                            "path": path, "secure": True})
    cookies.extend({"name": name, "value": value, "domain": "aspen.darienps.org",
                    "path": "/", "secure": True}
                   for name, value in (("deploymentId", "x2sis"), ("locale", "en_US")))
    return cookies


def parse_cookies(text):
    """Accept browser-export JSON or Netscape cookies, and keep only Aspen cookies."""
    import json
    text = text.strip()
    if text.startswith(("[", "{")):
        try:
            source = json.loads(text)
        except json.JSONDecodeError:
            raise CookieImportError("The cookie JSON is incomplete or invalid. Export as JSON and paste the entire export.") from None
        source = source.get("cookies", []) if isinstance(source, dict) else source
    else:
        source = []
        for line in text.splitlines():
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
            elif line.startswith("#") or not line.strip():
                continue
            fields = line.split("\t")
            if len(fields) != 7:
                raise CookieImportError("Expected a Netscape cookie file or a JSON cookie array.")
            domain, _, path, secure, expires, name, value = fields
            try:
                expiration = int(expires) or None
            except ValueError:
                raise CookieImportError("The Netscape export has an invalid expiration. Export the cookies again.") from None
            source.append(dict(domain=domain, path=path, secure=secure == "TRUE",
                               expirationDate=expiration, name=name, value=value))
    if not isinstance(source, list):
        raise CookieImportError("Expected a JSON cookie array or an object with a cookies array.")
    jar = requests.cookies.RequestsCookieJar()
    for item in source:
        if not isinstance(item, dict):
            raise CookieImportError("Each JSON cookie must be an object with name, value, domain, and path fields.")
        domain = str(item.get("domain", "")).lower()
        name = item.get("name")
        if domain.lstrip(".") not in {"aspen.darienps.org", "darienps.org"} or name not in COOKIE_NAMES:
            continue
        path = str(item.get("path", "/"))
        if not path.startswith("/"):
            raise CookieImportError("A cookie has an invalid path. Export the cookies again without editing their paths.")
        expiration = item.get("expires", item.get("expirationDate"))
        try:
            expiration = int(expiration) if expiration and float(expiration) > 0 else None
        except (ValueError, TypeError, OverflowError):
            raise CookieImportError("A cookie has an invalid expiration. Export the cookies again.") from None
        jar.set(name, str(item.get("value", "")), domain=domain, path=path,
                secure=bool(item.get("secure", False)), expires=expiration)
    if not any(c.name == "JSESSIONID" and c.path.startswith("/app") for c in jar):
        raise CookieImportError("The export is missing Aspen's JSESSIONID cookie for /app. Open Aspen's app portal, sign in if prompted, then export its cookies again. Keep the /aspen cookie too.")
    return jar


def text_of(tag):
    return " ".join(" ".join(tag.stripped_strings).split())


def numeric_grade(value, *, points=False):
    """Read only the numeric portion of a grade source, never its letter."""
    if value is None or isinstance(value, bool):
        return None
    number = r"\d+(?:\.\d+)?"
    pattern = rf"({number}\s*/\s*{number}|{number})" if points else rf"({number}\s*%?)"
    match = re.match(pattern + r"(?=\s|$)", str(value).strip())
    return match[1].strip() if match else None


def numeric_summary(rows):
    """Collect table labels, weights and numeric averages by column position."""
    result, rowspans = [], {}
    for row in rows:
        cells, column = [], 0
        for cell in row:
            while rowspans.get(column, 0):
                column += 1
            text = cell.get("text", "")
            label = column == 0 or cell.get("header") or text in {"Weight", "Avg", "Average"}
            cells.append({**cell, "text": text if label else numeric_grade(text) or ""})
            width = cell.get("colspan", 1)
            for offset in range(width):
                rowspans[column + offset] = cell.get("rowspan", 1)
            column += width
        rowspans = {col: remaining - 1 for col, remaining in rowspans.items() if remaining > 1}
        result.append(cells)
    return result


def academic_fields(data, keys):
    return {key: data[key] for key in keys.split() if key in data}


def collect_assignment(item):
    assignment = academic_fields(item, "oid name categoryName assignedDate dueDate totalPoints description")
    assignment["scoreLightModels"] = []
    for source in item.get("scoreLightModels", []):
        score = academic_fields(source, "specialCode behavior dropped exempt missing late incomplete comment")
        if "score" in source:
            score["score"] = numeric_grade(source["score"])
        assignment["scoreLightModels"].append(score)
    return assignment


def read_class_list_form(html):
    """Read the desktop class-list form without importing Aspen's grade column."""
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", attrs={"name": "classListForm"})
    if not form:
        raise AuthenticationRequired("The desktop session has expired. Reconnect Aspen to load its displayed averages.")
    return soup, form


def form_fields(form):
    fields = []
    for element in form.find_all(["input", "select", "textarea"]):
        name = element.get("name")
        if not name or element.has_attr("disabled"):
            continue
        kind = element.get("type", "text").lower()
        if kind in {"submit", "button", "reset", "file", "image"}:
            continue
        if kind in {"checkbox", "radio"} and not element.has_attr("checked"):
            continue
        if element.name == "select":
            options = element.find_all("option", selected=True) or element.find_all("option")[:1]
            fields.extend((name, o.get("value", text_of(o))) for o in options)
        else:
            fields.append((name, element.get("value", "") if element.name == "input" else element.text))
    return fields


def read_average_summary(html):
    return read_average_summary_details(html)[0]


def read_average_summary_details(html):
    """Read the display summary and any explicit category weights.

    Aspen's desktop summary is retained for explanation, but its Term
    Performance column is deliberately ignored.  The weights are keyed by
    category and quarter so course grades can be calculated from assignments.
    """
    soup = BeautifulSoup(html, "html.parser")
    label = soup.find(lambda t: t.name in {"td", "th"} and text_of(t) == "Gradebook average")
    if not label:
        return [], {}
    table = label.find_parent("table")
    rows = []
    for row in table.find_all("tr"):
        if row.find_parent("table") != table:
            continue
        cells = row.find_all(["td", "th"], recursive=False)
        if cells:
            rows.append([dict(text=text_of(c), header=c.name == "th",
                              rowspan=int(c.get("rowspan", 1)), colspan=int(c.get("colspan", 1))) for c in cells])
    summary = numeric_summary(rows)

    # Resolve the logical table columns, including row/col spans, so a
    # category's Weight cell can be associated with the right quarter.
    placed, spans = [], {}
    for row in rows:
        cells, column = [], 0
        for cell in row:
            while spans.get(column, 0):
                column += 1
            cells.append((column, cell))
            width = cell.get("colspan", 1)
            for offset in range(width):
                spans[column + offset] = cell.get("rowspan", 1)
            column += width
        spans = {col: remaining - 1 for col, remaining in spans.items() if remaining > 1}
        placed.append(cells)
    headers = {}
    for column, cell in placed[0] if placed else []:
        value = cell["text"]
        if re.fullmatch(r"Q[1-4]", value):
            headers[value] = column
    weights = {}
    active_category = ""
    labels = {"Category", "Weight", "Avg", "Average", "Gradebook average", "Last posted grade"}
    for cells in placed[1:]:
        by_column = {column: cell["text"] for column, cell in cells}
        first = by_column.get(0, "")
        if first and first not in labels:
            active_category = first
        weight_values = [(column, cell["text"]) for column, cell in cells
                         if re.fullmatch(r"\d+(?:\.\d+)?\s*%", cell["text"])]
        if not weight_values:
            continue
        category = active_category
        if not category:
            continue
        # A compact summary has one Weight column; a quarter matrix may have
        # one percentage under each quarter heading.
        for column, raw in weight_values:
            quarter = next((name for name, index in headers.items() if index == column), "all")
            weights.setdefault(category, {})[quarter] = float(numeric_grade(raw).rstrip("%"))
    return summary, weights


def calculated_grade(assignments, category_weights=None, quarter=None):
    """Calculate a percentage from scored assignments and category weights."""
    categories = {}
    for assignment in assignments or []:
        if (quarter not in (None, "current", "all") and assignment.get("termOid")
                and assignment.get("termOid") != quarter):
            continue
        possible = numeric_grade(assignment.get("totalPoints"))
        if possible is None or float(possible) <= 0:
            continue
        scores = assignment.get("scoreLightModels") or []
        active = [score for score in scores if not score.get("dropped") and not score.get("exempt")]
        numeric_scores = [numeric_grade(score.get("score")) for score in active]
        numeric_scores = [score for score in numeric_scores if score is not None]
        if not numeric_scores:
            continue
        score = float(numeric_scores[-1])
        category = assignment.get("categoryName") or "Assignment"
        earned, total = categories.setdefault(category, [0.0, 0.0])
        categories[category] = [earned + score, total + float(possible)]
    if not categories:
        return ""

    quarter_name = None
    if quarter and re.fullmatch(r"GTM[A-Za-z0-9]+", str(quarter)):
        # The caller supplies the term ID; category weights are also retained
        # by quarter label, so use the first available quarter when no label
        # is available in the assignment records.
        quarter_name = None
    selected_weights = {}
    if category_weights:
        for category, values in category_weights.items():
            if quarter_name and quarter_name in values:
                selected_weights[category] = values[quarter_name]
            elif values:
                selected_weights[category] = next(iter(values.values()))
    if selected_weights:
        weighted = [(earned / total * 100, selected_weights.get(category))
                    for category, (earned, total) in categories.items()
                    if category in selected_weights and total > 0]
        weight_total = sum(weight for _, weight in weighted)
        if weighted and weight_total > 0:
            return f"{sum(average * weight for average, weight in weighted) / weight_total:.2f}".rstrip("0").rstrip(".")
    earned = sum(values[0] for values in categories.values())
    total = sum(values[1] for values in categories.values())
    return f"{earned / total * 100:.2f}".rstrip("0").rstrip(".") if total > 0 else ""


def activity_date(value, pattern="%Y-%m-%d"):
    try:
        return datetime.strptime(value, pattern).date().isoformat()
    except (TypeError, ValueError):
        raise AspenError("Aspen returned an unreadable attendance or activity date.") from None


def read_attendance(html):
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", attrs={"name": "studentAttendanceListForm"})
    if not form:
        raise AuthenticationRequired("Reconnect the desktop Aspen session to load attendance.")
    header = next((row for row in form.select("tr.listHeader")
                   if {"Date", "Code", "Reason"}.issubset(
                       {text_of(c) for c in row.find_all(["td", "th"], recursive=False)})), None)
    if not header:
        raise AspenError("Aspen's attendance table could not be read.")
    table = header.find_parent("table")
    headings = [text_of(c) for c in header.find_all(["td", "th"], recursive=False)]
    indices = [headings.index(name) for name in ("Date", "Code", "Reason")]
    records = []
    for row in table.find_all("tr"):
        if row.find_parent("table") != table or "listCell" not in row.get("class", []):
            continue
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) <= max(indices):
            raise AspenError("Aspen returned an incomplete attendance row.")
        date, code, reason = [text_of(cells[i]) for i in indices]
        link = row.find("a", href=re.compile(r"doParamSubmit\(2100"))
        oid = re.search(r"ATT[A-Za-z0-9]+", link["href"]) if link else None
        records.append({"oid": oid[0] if oid else None, "date": activity_date(date, "%m/%d/%Y"),
                        "code": code, "reason": reason})
    fields = dict(form_fields(form))
    current_year = fields.get("filterDefinitionId") == "###currentYear"
    # totalRecords is a grid count; also flag visible paging controls rather than
    # claiming that a single returned page is necessarily the whole history.
    counts = re.findall(r"\btotalRecords\s*=\s*(\d+)\s*;", html)
    has_next = False
    for control in form.find_all(["a", "input", "img", "button"]):
        label = " ".join([control.get("alt", ""), control.get("title", ""),
                          control.get("value", ""), text_of(control)])
        if re.search(r"\bnext\b", label, re.I) and not control.has_attr("disabled"):
            if control.get("href") or control.get("onclick") or control.find_parent("a", href=True):
                has_next = True
    partial = has_next or any(int(count) > len(records) for count in counts)
    return {"available": True, "scope": "Current school year" if current_year else "Aspen's selected attendance filter",
            "summary": fields.get("userParam", "") if "Absences:" in fields.get("userParam", "") else "",
            "partial": partial, "records": sorted(records, key=lambda r: r["date"], reverse=True)}


def read_schedule_matrix(html):
    """Read the A–H rotation from Aspen's schedule matrix as eight period slots per day."""
    soup = BeautifulSoup(html, "html.parser")
    if not soup.find("form", attrs={"name": "scheduleMatrixForm"}):
        raise AuthenticationRequired("Reconnect the desktop Aspen session to load your schedule.")
    header = next((row for row in soup.find_all("tr")
                   if sum(bool(re.fullmatch(r"[A-H] - .+", text_of(c)))
                          for c in row.find_all(["td", "th"], recursive=False)) >= 2), None)
    if not header:
        raise AspenError("Aspen's schedule matrix could not be read.")
    table = header.find_parent("table")
    letters = {}
    for column, cell in enumerate(header.find_all(["td", "th"], recursive=False)):
        match = re.fullmatch(r"([A-H]) - .+", text_of(cell))
        if match:
            letters[column] = match[1]
    days = {letter: [None] * len(SCHEDULE_PERIODS) for letter in letters.values()}
    found, carried = False, {}
    for row in table.find_all("tr"):
        if row is header or row.find_parent("table") != table:
            continue
        # A class spanning two periods is one cell with rowspan="2"; carry it
        # into the rows it covers so every period sees its class.
        cells, spans, column = {c: cell for c, (_, cell) in carried.items()}, {}, 0
        for cell in row.find_all(["td", "th"], recursive=False):
            while column in carried:
                column += 1
            cells[column] = cell
            if int(cell.get("rowspan", 1) or 1) > 1:
                spans[column] = (int(cell["rowspan"]) - 1, cell)
            column += int(cell.get("colspan", 1) or 1)
        carried = {c: (left - 1, cell) for c, (left, cell) in carried.items() if left > 1} | spans
        period = re.fullmatch(r"\d+-Period (\d+)", text_of(cells[0])) if 0 in cells else None
        if not period or int(period[1]) not in SCHEDULE_PERIODS:
            continue
        found = True
        for column, letter in letters.items():
            days[letter][int(period[1]) - 1] = schedule_course(cells.get(column))
    if not found:
        raise AspenError("Aspen's schedule matrix did not include periods 1–8.")
    return {"available": True, "days": days}


def schedule_course(cell):
    """Read one matrix cell: section, course, teacher and room are separated by <br>.

    Only the section's department prefix (HST502-520A-002 → HST) is kept; DHS
    lunch waves depend on it.
    """
    content = cell.find("td") if cell else None
    if not content:
        return None
    lines = [text_of(BeautifulSoup(part, "html.parser"))
             for part in re.split(r"<br\s*/?>", content.decode_contents())]
    lines += [""] * (4 - len(lines))
    if not lines[1]:
        return None
    department = re.match(r"[A-Z]+", lines[0])
    return {"courseName": lines[1], "teacherName": lines[2], "room": lines[3],
            "department": department[0] if department else ""}


def read_activity(xml, student_oid):
    try:
        if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
            raise ET.ParseError("Unexpected XML declaration")
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise AspenError("Aspen's recent activity response could not be read. Reconnect if your desktop session expired.") from None
    if root.tag != "recent-activity-list":
        raise AspenError("Aspen did not return its recent activity feed.")
    groups = [g for g in root.findall("recent-activity") if g.get("studentoid") == student_oid]
    if not groups:
        raise AspenError("Aspen did not return recent activity for the signed-in student.")
    events, unsupported = [], set()
    kinds = {"gradebookScore": "grade", "gradePost": "grade", "attendance": "dailyAttendance", "periodAttendance": "classAttendance"}
    for group in groups:
        for item in group:
            if item.tag not in kinds:
                unsupported.add(item.tag)
                continue
            grade = item.tag == "gradebookScore"
            event = {"oid": item.get("oid"), "type": kinds[item.tag],
                     "date": activity_date(item.get("date")), "datePrecision": "day",
                     "dateMeaning": "posted" if grade else "attendance",
                     "className": item.get("classname", ""), "studentScheduleOid": item.get("sscoid"),
                     "assignmentOid": item.get("assignmentoid"), "termOid": item.get("gtmoid"),
                     "assignmentName": item.get("assignmentname", ""), "grade": numeric_grade(item.get("grade"), points=True) if grade else None,
                     "code": item.get("code", ""), "period": item.get("period", "")}
            for flag in ("absent", "tardy", "dismissed", "excused"):
                event[flag] = {"true": True, "false": False}.get(item.get(flag))
            events.append(event)
    # ISO calendar dates sort chronologically. Python's stable sort preserves
    # Aspen's order for ties; no hour or minute is inferred.
    events.sort(key=lambda e: e["date"], reverse=True)
    return {"available": True, "scope": "Last 60 days" if root.get("daterange") == "4" else "Aspen's recent activity window",
            "datePrecision": "day", "events": events, "unsupportedTypes": sorted(unsupported),
            "gradesEnabled": root.get("grades") == "true", "attendanceEnabled": root.get("attendance") == "true"}


def cache_grade_periods(load_period, year="current", quarter="current"):
    """Publish a complete set of period views after every required read succeeds."""
    baseline = load_period("current", "current")
    periods = {}
    warnings = list(baseline.get("warnings", []))

    def add(snapshot):
        filters = snapshot["gradeFilters"]
        periods[f"{filters['year']}:{filters['quarter']}"] = {
            "classes": snapshot["classes"], "gradeFilters": filters,
            "fetchedAt": snapshot.get("syncedAt")}
        warnings.extend(snapshot.get("warnings", []))

    add(baseline)
    for choice in baseline["gradeFilters"]["years"]:
        context = choice["value"]
        first = baseline if context == "current" else load_period(context, "all")
        add(first)
        for term in first["gradeFilters"]["quarters"]:
            if f"{context}:{term['value']}" not in periods:
                add(load_period(context, term["value"]))
    # Aspen's `current` classes endpoint identifies the active roster but does
    # not include a quarter ID. Match that roster against the explicit quarter
    # views so Current quarter uses Q2 (or whichever term Aspen has activated)
    # instead of combining scores from every term with posted grades.
    current_ids = {course.get("studentScheduleOid") for course in baseline["classes"]}
    current_matches = []
    if current_ids:
        for key, period in periods.items():
            if not key.startswith("current:") or key.endswith((":current", ":all")):
                continue
            ids = {course.get("studentScheduleOid") for course in period["classes"]}
            if ids == current_ids:
                current_matches.append(key)
    if len(current_matches) == 1:
        active_key = current_matches[0]
        active = periods[active_key]
        current_filters = {**baseline["gradeFilters"], "quarter": "current"}
        periods["current:current"] = {**active, "gradeFilters": current_filters}
        baseline = {**baseline, **periods["current:current"]}
    if year == "previous" and quarter == "current":
        quarter = "all"
    selected = periods.get(f"{year}:{quarter}") or periods["current:current"]
    return {**baseline, **selected, "gradePeriods": periods,
            "warnings": list(dict.fromkeys(warnings)),
            "syncedAt": datetime.now(timezone.utc).isoformat()}


class AspenClient:
    def __init__(self, cookies):
        self.session = requests.Session()
        self.session.cookies.update(cookies)
        self.session.headers.update({"Accept": "application/json", "deploymentId": "x2sis"})
        self.desktop_available = any(c.name == "JSESSIONID" and c.path.startswith("/aspen") for c in cookies)
        self.history_cache = {}
        self.history_updated_at = None

    def invalidate_history(self):
        self.history_updated_at = None

    def request(self, method, path, **kwargs):
        url = urljoin(ORIGIN, path)
        if not url.startswith(ORIGIN + "/"):
            raise AspenError("Only the configured Aspen host may be requested.")
        csrf = next((unquote(c.value) for c in self.session.cookies if c.name == "VITHAR_CSRF"), None)
        headers = {"X-CSRF-Token": csrf} if csrf else {}
        try:
            response = self.session.request(method, url, headers=headers, timeout=30,
                                            allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise AspenError("Aspen could not be reached. Your previous data is still available.") from None
        if response.status_code in {301, 302, 303, 307, 308}:
            target = urljoin(url, response.headers.get("Location", ""))
            # Class and schedule navigation legitimately redirect to these desktop pages.
            if target.split("?")[0] != url.split("?")[0] and (method, target.split("?")[0]) in {
                    ("POST", ORIGIN + "/aspen/portalClassDetail.do"), ("GET", ORIGIN + "/aspen/studentScheduleMatrix.do")}:
                return self.request("GET", target)
            raise AuthenticationRequired("Aspen redirected to login. Reconnect your session.")
        if response.status_code in {401, 403}:
            raise AuthenticationRequired("Aspen rejected this session or temporarily denied access. Retry your saved session, or reconnect Aspen if it keeps failing.")
        if not response.ok:
            raise AspenError(f"Aspen returned HTTP {response.status_code}. Your previous data is still available.")
        return response

    def api(self, path, params=None):
        response = self.request("GET", "/app/rest" + path, params=params)
        if not response.content:
            return None
        try:
            return response.json()
        except requests.exceptions.JSONDecodeError:
            raise AuthenticationRequired("Aspen returned its login page. Reconnect your session.") from None

    def sync(self, year="current", quarter="current"):
        shared = {"api": {}, "summaries": {}, "courses": {}}
        expired = self.history_updated_at is None or time.monotonic() - self.history_updated_at >= HISTORY_REFRESH_SECONDS
        history = {} if expired else dict(self.history_cache)

        def load(context, term):
            key = context, term
            historical = key not in {("current", "current"), ("current", "all")}
            if historical and key in history:
                return history[key]
            snapshot = self.sync_period(context, term, shared)
            if historical:
                history[key] = snapshot
            return snapshot

        snapshot = cache_grade_periods(load, year, quarter)
        # Publish the history cache only after all required reads succeed.
        self.history_cache = history
        if expired:
            self.history_updated_at = time.monotonic()
        return snapshot

    def sync_period(self, year="current", quarter="current", shared=None):
        # Reuse assignment/term reads across quarter views within this refresh.
        # These shared reads expire after each sync; sync() caches historical views separately.
        def read(path, params=None):
            if shared is None:
                return self.api(path, params)
            key = (path, tuple(sorted((params or {}).items())))
            if key not in shared["api"]:
                shared["api"][key] = self.api(path, params)
            return shared["api"][key]

        user = read("/users/current")
        student = read("/students/studentByPersonOid", {"personOid": user["personOid"]})
        student_oid = student["studentOid"]
        warnings = []
        attendance = {"available": False, "records": [], "error": "Import the /aspen desktop cookie to load attendance."}
        activity = {"available": False, "events": [], "error": "Import the /aspen desktop cookie to load recent activity."}
        schedule = {"available": False, "days": {}, "error": "Import the /aspen desktop cookie to load your schedule."}
        years = [{"value": "current", "label": "Current school year"},
                 {"value": "previous", "label": "Previous school year"}]
        if year not in {option["value"] for option in years}:
            raise AspenError("Choose the current or previous school year.")
        # Aspen Go uses these context aliases and omits Current Term for the
        # previous year. All terms is the default when switching to that year.
        if year == "previous" and quarter == "current":
            quarter = "all"
        schools = [{"oid": student["schoolOid"]}] if year == "current" else read(
            "/schools/forStudent", {"studentOid": student_oid, "districtContext": year}) or []
        quarters, classes = [], []
        for school in schools:
            school_terms = read("/gradeTerm/" + quote(student_oid, safe=""),
                                    {"schoolYearContext": year, "schoolOid": school["oid"]}) or []
            for term in school_terms:
                if not any(existing["oid"] == term["oid"] for existing in quarters):
                    quarters.append(term)
            if quarter not in {"current", "all"} and quarter not in {term["oid"] for term in school_terms}:
                continue
            classes.extend(read("/classes/" + quote(quarter, safe=""), {"studentOid": student_oid,
                           "schoolOid": school["oid"], "districtContext": year}) or [])
        if quarter not in {"current", "all"} and quarter not in {term["oid"] for term in quarters}:
            raise AspenError("That quarter is not available for this school year. Choose another quarter.")
        use_desktop = self.desktop_available and year == "current"
        if use_desktop and quarter == "current":
            # Prime the desktop session before attendance/activity reads. This
            # validates the session and supplies no course-grade data.
            try:
                read_class_list_form(self.request("GET", CLASS_LIST_PATH).text)
            except AspenError as error:
                warnings.append(str(error))
        if shared is not None and "features" in shared:
            attendance, activity, schedule = shared["features"]
        elif self.desktop_available:
            for name, path, parser in [
                ("Attendance", "/aspen/studentAttendanceList.do?navkey=myInfo.att.list", read_attendance),
                ("Recent activity", "/aspen/studentRecentActivityWidget.do", lambda body: read_activity(body, student_oid)),
                ("Schedule", SCHEDULE_PATH, read_schedule_matrix),
            ]:
                try:
                    params = {"preferences": ACTIVITY_PREFERENCES} if name == "Recent activity" else None
                    data = parser(self.request("GET", path, params=params).text)
                    data["fetchedAt"] = datetime.now(timezone.utc).isoformat()
                except AspenError as error:
                    data = {"available": False, "error": str(error),
                            **{"Attendance": {"records": []}, "Recent activity": {"events": []}, "Schedule": {"days": {}}}[name]}
                    warnings.append(f"{name}: {error}")
                if name == "Attendance":
                    attendance = data
                    if data.get("partial"):
                        warnings.append("Attendance is a partial Aspen page; additional records are not included.")
                elif name == "Schedule":
                    schedule = data
                else:
                    activity = data
                    if data.get("unsupportedTypes"):
                        warnings.append("Some Aspen activity types are not supported: " + ", ".join(data["unsupportedTypes"]))
                    if data.get("available") and not (data["gradesEnabled"] and data["attendanceEnabled"]):
                        warnings.append("Aspen has disabled grades or attendance in its recent activity feed.")
        if shared is not None:
            shared["features"] = attendance, activity, schedule
        result = []
        for course in classes:
            schedule_oid = course["studentScheduleOid"]
            key = year, schedule_oid
            details = shared.get("courses", {}).get(key) if shared is not None else None
            if details is None:
                terms = [academic_fields(term, "oid gradeTermId") for term in read("/gradeTerm/class/" + quote(schedule_oid, safe=""))]
                assignments = []
                for term in terms:
                    items = read("/assignments", {"studentOid": student_oid, "studentScheduleOid": schedule_oid,
                                     "gradeTermOid": term["oid"], "districtContextOid": year})
                    assignments.extend({**collect_assignment(item), "termOid": term["oid"], "termName": term["gradeTermId"]}
                                       for item in items)
                details = terms, assignments
                if shared is not None:
                    shared.setdefault("courses", {})[key] = details
            terms, assignments = details
            summary, category_weights = [], {}
            if use_desktop and shared is not None and schedule_oid in shared["summaries"]:
                summary, category_weights = shared["summaries"][schedule_oid]
            elif use_desktop:
                try:
                    # Obtain a fresh form/token for each navigation. No grade edits are sent.
                    _, form = read_class_list_form(self.request("GET", CLASS_LIST_PATH).text)
                    fields = [(k, v) for k, v in form_fields(form) if k not in {"userEvent", "userParam"}]
                    fields.extend([("userEvent", "2100"), ("userParam", schedule_oid)])
                    summary, category_weights = read_average_summary_details(
                        self.request("POST", form.get("action"), data=fields).text)
                except AspenError as error:
                    warnings.append(f"{course['courseName']}: {error}")
                if shared is not None:
                    shared["summaries"][schedule_oid] = summary, category_weights
            percentage = calculated_grade(assignments, category_weights, quarter)
            result.append({**academic_fields(course, "studentScheduleOid courseName teacherName teacherEmail meetingTime startTime endTime period periodNumber"),
                           "displayGrade": percentage,
                           "gradeSource": "Calculated from assignments",
                           "terms": terms, "assignments": assignments, "averageSummary": summary})
        return {"student": {"name": student["name"], "studentOid": student_oid},
                "classes": result, "attendance": attendance, "activityFeed": activity,
                "schedule": schedule,
                "gradeFilters": {"year": year, "quarter": quarter, "years": years,
                                 "quarters": ([{"value": "current", "label": "Current quarter"}] if year == "current" else []) +
                                             [{"value": "all", "label": "All quarters"}] +
                                             [{"value": term["oid"], "label": term["gradeTermId"]} for term in quarters]},
                "syncedAt": datetime.now(timezone.utc).isoformat(),
                "mode": "live", "warnings": warnings, "timeoutInSeconds": user.get("timeoutInSeconds")}


DEMO_ROTATION = {"A": (2, 4, 6, 8), "B": (1, 3, 5, 7), "C": (4, 2, 8, 6), "D": (3, 1, 7, 5),
                 "E": (2, 8, 4, 6), "F": (1, 7, 3, 5), "G": (4, 6, 2, 8), "H": (3, 5, 1, 7)}
DEMO_DEPARTMENTS = ("MAT", "ENG", "SCI", "HST", "WL", "ART")


def demo_snapshot(year="current", quarter="current"):
    return cache_grade_periods(demo_period, year, quarter)


def demo_period(year="current", quarter="current"):
    """A fictional school week for comparing the dashboard designs."""
    subjects = [
        ("Algebra II", "Ms. Bennett", "93.4", "Quadratic functions", "Problem set: parabolas", "93.4", "46.7", "50"),
        ("English Literature", "Mr. Rivera", "88.5", "Close reading: The Great Gatsby", "Chapter 4 annotations", "88.5", "17.7", "20"),
        ("Chemistry", "Dr. Chen", "91.2", "Atomic structure lab", "Balancing equations", "91.2", "45.6", "50"),
        ("U.S. History", "Ms. Brooks", "95.0", "Primary source analysis", "The Federalist Papers", "95.0", "19", "20"),
        ("Spanish III", "Señora Torres", "89.0", "Conversación: mi comunidad", "Vocabulary practice", "89.0", "44.5", "50"),
        ("Visual Arts", "Mr. Ellis", "97.0", "Still life study", "Sketchbook reflection", "97.0", "48.5", "50"),
    ]
    classes, events = [], []
    for i, (name, teacher, grade, assignment, practice, average, score, possible) in enumerate(subjects):
        oid = f"demo-class-{i}"
        summary = [
            [{"text": value, "header": True} for value in ["Category", "Weight", "Average"]],
            [{"text": value} for value in ["Assessments", "60%", average]],
            [{"text": value} for value in ["Practice", "40%", average]],
        ]
        assignments = [
            {"oid": f"{oid}-1", "name": assignment, "categoryName": "Assessments", "termOid": "demo-q1", "termName": "Q1",
             "assignedDate": "2026-09-21", "dueDate": "2026-09-28", "totalPoints": possible,
             "description": "Sample assessment. Review your work and the feedback discussed in class.",
             "scoreLightModels": [{"score": score, "dropped": False, "exempt": False}]},
            {"oid": f"{oid}-2", "name": practice, "categoryName": "Practice", "termOid": "demo-q1", "termName": "Q1",
             "assignedDate": "2026-09-22", "dueDate": "2026-09-25", "totalPoints": "100", "description": "Sample practice assignment.",
             "scoreLightModels": [{"score": average, "dropped": False, "exempt": False}]},
            {"oid": f"{oid}-3", "name": "Next unit preparation", "categoryName": "Practice", "termOid": "demo-q2", "termName": "Q2",
             "assignedDate": "2026-10-26", "dueDate": "2026-11-02", "totalPoints": "10", "description": "Read the next unit introduction.",
             "scoreLightModels": []},
        ]
        classes.append({"studentScheduleOid": oid, "courseName": name, "teacherName": teacher,
                        "teacherEmail": "", "meetingTime": f"Period {i + 1}", "displayGrade": grade, "gradeSource": "Sample data",
                        "averageSummary": summary, "terms": [{"oid": "demo-q1", "gradeTermId": "Q1"}, {"oid": "demo-q2", "gradeTermId": "Q2"}],
                        "assignments": assignments})
        events.append({"oid": f"demo-score-{i}", "type": "grade", "date": f"2026-09-{29 - i // 2}", "dateMeaning": "posted",
                       "className": name, "assignmentName": assignment, "grade": f"{score} / {possible}"})
    events.extend([
        {"oid": "demo-att-1", "type": "dailyAttendance", "date": "2026-09-25", "dateMeaning": "attendance", "code": "T-E", "tardy": True, "excused": True},
        {"oid": "demo-period", "type": "classAttendance", "date": "2026-09-22", "dateMeaning": "attendance",
         "className": "Algebra II", "period": "1", "code": "A-E", "absent": True, "excused": True},
        {"oid": "demo-att-2", "type": "dailyAttendance", "date": "2026-09-22", "dateMeaning": "attendance", "code": "A-E", "absent": True, "excused": True},
    ])
    years = [{"value": "current", "label": "2026–2027"}, {"value": "previous", "label": "2025–2026"}]
    if year == "previous" and quarter == "current":
        quarter = "all"
    quarters = ([{"value": "current", "label": "Current quarter (Q1)"}] if year == "current" else []) + [{"value": "all", "label": "All quarters"}] + [
        {"value": f"demo-q{i}", "label": f"Q{i}"} for i in range(1, 5)]
    if year not in {option["value"] for option in years} or quarter not in {option["value"] for option in quarters}:
        raise AspenError("Choose one of the available sample years and quarters.")
    for course in classes:
        course["terms"] = [{"oid": f"demo-q{i}", "gradeTermId": f"Q{i}"} for i in range(1, 5)]
        if year == "previous":
            course["courseName"] = course["courseName"].replace("Algebra II", "Algebra I").replace("Spanish III", "Spanish II")
            course["displayGrade"] = "90.0"
            for row in course["averageSummary"][1:]:
                row[-1]["text"] = "90.0"
            for assignment in course["assignments"]:
                for key in ("assignedDate", "dueDate"):
                    assignment[key] = assignment[key].replace("2026", "2025")
        if quarter not in {"current", "all", "demo-q1"}:
            course["displayGrade"] = ""
            course["averageSummary"] = []
        course["assignments"] = [item for item in course["assignments"]
                                 if quarter in {"current", "all"} or item["termOid"] == quarter]
    # Each letter day meets four of the eight periods, two periods per block.
    demo_schedule = {letter: [None] * len(SCHEDULE_PERIODS) for letter in DEMO_ROTATION}
    for letter, periods in DEMO_ROTATION.items():
        for block, period in enumerate(periods):
            if period <= len(subjects):
                course = {"courseName": subjects[period - 1][0], "teacherName": subjects[period - 1][1],
                          "room": f"Room {100 + period}", "department": DEMO_DEPARTMENTS[period - 1]}
                demo_schedule[letter][block * 2:block * 2 + 2] = [course, course]
    return {"mode": "demo", "student": {"name": "Alex Morgan", "studentOid": "demo"},
            "gradeFilters": {"year": year, "quarter": quarter, "years": years, "quarters": quarters},
            "syncedAt": datetime.now(timezone.utc).isoformat(), "warnings": ["Sample data, not your Aspen grades."],
            "attendance": {"available": True, "scope": "Current school year", "partial": False,
                           "summary": "Absences: 1.0. Tardies: 1 (0 unexcused).",
                           "records": [{"oid": "demo-att-1", "date": "2026-09-25", "code": "T-E", "reason": "Appointment"},
                                       {"oid": "demo-att-2", "date": "2026-09-22", "code": "A-E", "reason": "Sick"}]},
            "activityFeed": {"available": True, "scope": "Last 60 days", "datePrecision": "day", "events": events},
            "schedule": {"available": True, "days": demo_schedule, "fetchedAt": datetime.now(timezone.utc).isoformat()},
            "classes": classes}
