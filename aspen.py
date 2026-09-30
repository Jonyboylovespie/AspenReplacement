"""Request-based, read-only access to the logged-in student's Aspen portal."""
from datetime import datetime, timezone
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote, urljoin

from bs4 import BeautifulSoup
import requests

ORIGIN = "https://aspen.darienps.org"
COOKIE_NAMES = {"JSESSIONID", "VITHAR_CSRF", "deploymentId", "locale", "cf_clearance"}
ACTIVITY_PREFERENCES = '<?xml version="1.0" encoding="UTF-8"?><preference-set><pref id="dateRange" type="int">4</pref></preference-set>'
CLASS_LIST_PATH = "/aspen/portalClassList.do?navkey=academics.classes.list"


class AspenError(Exception):
    pass


class AuthenticationRequired(AspenError):
    pass


class CookieImportError(ValueError):
    """A cookie-import error safe to display without including credentials."""


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
                raise CookieImportError("Expected a Netscape cookie file or a JSON cookie array. In Cookie-Editor, choose Export → JSON.")
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


def read_class_list(html):
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", attrs={"name": "classListForm"})
    if not form:
        raise AuthenticationRequired("The desktop session has expired. Reconnect Aspen to load its displayed averages.")
    grades = {}
    for link in soup.find_all("a", href=re.compile(r"doParamSubmit\(2100")):
        match = re.search(r"SSC[A-Za-z0-9]+", link["href"])
        row = link.find_parent("tr")
        if not match or not row:
            continue
        table = row.find_parent("table")
        header = next((r for r in table.find_all("tr") if "Term Performance" in text_of(r)
                       and not r.find("a", href=re.compile(r"doParamSubmit\(2100"))), None)
        cells = row.find_all(["td", "th"], recursive=False)
        if header:
            headings = [text_of(c) for c in header.find_all(["td", "th"], recursive=False)]
            if "Term Performance" in headings:
                index = headings.index("Term Performance")
                if index < len(cells):
                    grades[match[0]] = text_of(cells[index])
    return soup, form, grades


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
    soup = BeautifulSoup(html, "html.parser")
    label = soup.find(lambda t: t.name in {"td", "th"} and text_of(t) == "Gradebook average")
    if not label:
        return []
    table = label.find_parent("table")
    rows = []
    for row in table.find_all("tr"):
        if row.find_parent("table") != table:
            continue
        cells = row.find_all(["td", "th"], recursive=False)
        if cells:
            rows.append([dict(text=text_of(c), header=c.name == "th",
                              rowspan=int(c.get("rowspan", 1)), colspan=int(c.get("colspan", 1))) for c in cells])
    return rows


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
    kinds = {"gradebookScore": "grade", "attendance": "dailyAttendance", "periodAttendance": "classAttendance"}
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
                     "assignmentName": item.get("assignmentname", ""), "grade": item.get("grade"),
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
            "classes": snapshot["classes"], "gradeFilters": filters}
        warnings.extend(snapshot.get("warnings", []))

    add(baseline)
    for choice in baseline["gradeFilters"]["years"]:
        context = choice["value"]
        first = baseline if context == "current" else load_period(context, "all")
        add(first)
        for term in first["gradeFilters"]["quarters"]:
            if f"{context}:{term['value']}" not in periods:
                add(load_period(context, term["value"]))
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
            # Class navigation legitimately redirects to the desktop detail endpoint.
            if method == "POST" and target.split("?")[0] == ORIGIN + "/aspen/portalClassDetail.do":
                return self.request("GET", target)
            raise AuthenticationRequired("Aspen redirected to login. Reconnect your session.")
        if response.status_code in {401, 403}:
            raise AuthenticationRequired("Your Aspen session expired or access was denied. Reconnect Aspen.")
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
        shared = {"api": {}, "summaries": {}}
        return cache_grade_periods(lambda context, term: self.sync_period(context, term, shared), year, quarter)

    def sync_period(self, year="current", quarter="current", shared=None):
        # Reuse assignment/term reads across quarter views within this refresh.
        # The cache is discarded between refreshes, so data is always re-fetched.
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
        canonical = {}
        use_desktop = self.desktop_available and year == "current"
        if use_desktop and quarter == "current":
            try:
                _, _, canonical = read_class_list(self.request("GET", CLASS_LIST_PATH).text)
                if not canonical:
                    warnings.append("Aspen's desktop grade columns could not be read. Averages are from the API.")
            except AspenError as error:
                warnings.append(str(error))
        elif not self.desktop_available:
            warnings.append("The desktop cookie is missing. Course averages are from the API and may differ from Aspen's desktop view.")
        if shared is not None and "features" in shared:
            attendance, activity = shared["features"]
        elif self.desktop_available:
            for name, path, parser in [
                ("Attendance", "/aspen/studentAttendanceList.do?navkey=myInfo.att.list", read_attendance),
                ("Recent activity", "/aspen/studentRecentActivityWidget.do", lambda body: read_activity(body, student_oid)),
            ]:
                try:
                    params = {"preferences": ACTIVITY_PREFERENCES} if name == "Recent activity" else None
                    data = parser(self.request("GET", path, params=params).text)
                    data["fetchedAt"] = datetime.now(timezone.utc).isoformat()
                except AspenError as error:
                    data = {"available": False, "error": str(error), "records" if name == "Attendance" else "events": []}
                    warnings.append(f"{name}: {error}")
                if name == "Attendance":
                    attendance = data
                    if data.get("partial"):
                        warnings.append("Attendance is a partial Aspen page; additional records are not included.")
                else:
                    activity = data
                    if data.get("unsupportedTypes"):
                        warnings.append("Some Aspen activity types are not supported: " + ", ".join(data["unsupportedTypes"]))
                    if data.get("available") and not (data["gradesEnabled"] and data["attendanceEnabled"]):
                        warnings.append("Aspen has disabled grades or attendance in its recent activity feed.")
        if shared is not None:
            shared["features"] = attendance, activity
        result = []
        for course in classes:
            schedule = course["studentScheduleOid"]
            terms = read("/gradeTerm/class/" + quote(schedule, safe=""))
            assignments = []
            for term in terms:
                items = read("/assignments", {"studentOid": student_oid, "studentScheduleOid": schedule,
                                 "gradeTermOid": term["oid"], "districtContextOid": year})
                for item in items:
                    assignments.append({**item, "termOid": term["oid"], "termName": term["gradeTermId"]})
            summary = []
            if use_desktop and shared is not None and schedule in shared["summaries"]:
                summary = shared["summaries"][schedule]
            elif use_desktop:
                try:
                    # Obtain a fresh form/token for each navigation. No grade edits are sent.
                    _, form, _ = read_class_list(self.request("GET", CLASS_LIST_PATH).text)
                    fields = [(k, v) for k, v in form_fields(form) if k not in {"userEvent", "userParam"}]
                    fields.extend([("userEvent", "2100"), ("userParam", schedule)])
                    summary = read_average_summary(self.request("POST", form.get("action"), data=fields).text)
                except AspenError as error:
                    warnings.append(f"{course['courseName']}: {error}")
                if shared is not None:
                    shared["summaries"][schedule] = summary
            fallback = course.get("sectionTermAverage") if course.get("displayLetterGradesOnly") else " ".join(
                str(v) for v in [course.get("percentageValue"), course.get("sectionTermAverage")] if v not in (None, ""))
            result.append({**course, "displayGrade": canonical.get(schedule, fallback or ""),
                           "gradeSource": "Aspen desktop" if schedule in canonical else "Aspen API",
                           "terms": terms, "assignments": assignments, "averageSummary": summary})
        return {"student": {"name": student["name"], "studentOid": student_oid},
                "classes": result, "attendance": attendance, "activityFeed": activity,
                "gradeFilters": {"year": year, "quarter": quarter, "years": years,
                                 "quarters": ([{"value": "current", "label": "Current quarter"}] if year == "current" else []) +
                                             [{"value": "all", "label": "All quarters"}] +
                                             [{"value": term["oid"], "label": term["gradeTermId"]} for term in quarters]},
                "syncedAt": datetime.now(timezone.utc).isoformat(),
                "mode": "live", "warnings": warnings, "timeoutInSeconds": user.get("timeoutInSeconds")}


def demo_snapshot(year="current", quarter="current"):
    return cache_grade_periods(demo_period, year, quarter)


def demo_period(year="current", quarter="current"):
    """A fictional school week for comparing the dashboard designs."""
    subjects = [
        ("Algebra II", "MATH-201", "Ms. Bennett", "93.4 A", "Quadratic functions", "Problem set: parabolas", "93.4", "46.7", "50"),
        ("English Literature", "ENG-210", "Mr. Rivera", "88.5 B+", "Close reading: The Great Gatsby", "Chapter 4 annotations", "88.5", "17.7", "20"),
        ("Chemistry", "SCI-220", "Dr. Chen", "91.2 A-", "Atomic structure lab", "Balancing equations", "91.2", "45.6", "50"),
        ("U.S. History", "HIST-201", "Ms. Brooks", "95.0 A", "Primary source analysis", "The Federalist Papers", "95.0", "19", "20"),
        ("Spanish III", "LANG-303", "Señora Torres", "89.0 B+", "Conversación: mi comunidad", "Vocabulary practice", "89.0", "44.5", "50"),
        ("Visual Arts", "ART-110", "Mr. Ellis", "97.0 A+", "Still life study", "Sketchbook reflection", "97.0", "48.5", "50"),
    ]
    classes, events = [], []
    for i, (name, number, teacher, grade, assignment, practice, average, score, possible) in enumerate(subjects):
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
        classes.append({"studentScheduleOid": oid, "courseName": name, "courseNumber": number, "teacherName": teacher,
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
            course["displayGrade"] = "90.0 A−"
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
    return {"mode": "demo", "student": {"name": "Alex Morgan", "studentOid": "demo"},
            "gradeFilters": {"year": year, "quarter": quarter, "years": years, "quarters": quarters},
            "syncedAt": datetime.now(timezone.utc).isoformat(), "warnings": ["Sample data, not your Aspen grades."],
            "attendance": {"available": True, "scope": "Current school year", "partial": False,
                           "summary": "Absences: 1.0. Tardies: 1 (0 unexcused).",
                           "records": [{"oid": "demo-att-1", "date": "2026-09-25", "code": "T-E", "reason": "Appointment"},
                                       {"oid": "demo-att-2", "date": "2026-09-22", "code": "A-E", "reason": "Sick"}]},
            "activityFeed": {"available": True, "scope": "Last 60 days", "datePrecision": "day", "events": events},
            "classes": classes}
