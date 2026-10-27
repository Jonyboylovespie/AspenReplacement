import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Store
from helpers import create_test_app as create_app
from aspen import (AspenError, AuthenticationRequired, AspenClient, form_fields, parse_cookies,
                   read_activity, read_attendance, read_average_summary, read_class_list,
                   read_schedule_matrix)


class ScraperTests(unittest.TestCase):
    def test_cookie_scopes_and_unrelated_accounts(self):
        jar = parse_cookies(json.dumps([
            {"name": "JSESSIONID", "value": "api", "domain": "aspen.darienps.org", "path": "/app"},
            {"name": "JSESSIONID", "value": "desktop", "domain": "aspen.darienps.org", "path": "/aspen"},
            {"name": "SID", "value": "unrelated", "domain": ".google.com", "path": "/"},
        ]))
        self.assertEqual({(c.name, c.path) for c in jar}, {("JSESSIONID", "/app"), ("JSESSIONID", "/aspen")})
        with self.assertRaises(ValueError):
            parse_cookies('[{"name":"JSESSIONID","value":"desktop","domain":"aspen.darienps.org","path":"/aspen"}]')

    def test_netscape_http_only_cookies(self):
        jar = parse_cookies("# Netscape HTTP Cookie File\n#HttpOnly_aspen.darienps.org\tFALSE\t/app\tTRUE\t0\tJSESSIONID\tsession")
        self.assertEqual(next(iter(jar)).value, "session")

    def test_cookie_editor_session_export(self):
        jar = parse_cookies(json.dumps([
            {"name": "JSESSIONID", "value": "api", "domain": "aspen.darienps.org",
             "path": "/app", "secure": True, "httpOnly": True, "session": True,
             "expirationDate": None, "firstPartyDomain": "", "partitionKey": None, "storeId": None},
            {"name": "JSESSIONID", "value": "desktop", "domain": "aspen.darienps.org",
             "path": "/aspen", "expirationDate": -1},
        ]))
        self.assertEqual({c.path for c in jar}, {"/app", "/aspen"})
        self.assertTrue(all(c.expires is None for c in jar))

    def test_desktop_grade_and_live_form_fields(self):
        html = '''<form name="classListForm" action="/aspen/portalClassList.do">
        <input type="hidden" name="org.apache.struts.taglib.html.TOKEN" value="live-token">
        <input type="hidden" name="userEvent" value="0"><input type="checkbox" name="skip" value="1">
        <select name="termFilter"><option value="current" selected>Current Term</option></select>
        <table><tr><th>Description</th><th>Term&nbsp;Performance</th></tr>
        <tr><td><a href="javascript:doParamSubmit(2100, document.forms['classListForm'], 'SSC123')">Physics</a></td><td>97.9 A</td></tr></table></form>'''
        _, form, grades = read_class_list(html)
        self.assertEqual(grades, {"SSC123": "97.9"})
        values = dict(form_fields(form))
        self.assertEqual(values["org.apache.struts.taglib.html.TOKEN"], "live-token")
        self.assertEqual(values["termFilter"], "current")
        self.assertNotIn("skip", values)

    def test_category_summary_preserves_blank_terms_and_rowspans(self):
        html = '''<table><tr><th>Category</th><th>Q1</th><th>Q2</th></tr>
        <tr><td rowspan="2">Tests</td><td>Weight</td><td>70%</td></tr>
        <tr><td>Avg</td><td>97.5 A</td></tr>
        <tr><td>Gradebook average</td><td>97.9 A</td><td></td></tr></table>'''
        rows = read_average_summary(html)
        self.assertEqual(rows[1][0]["rowspan"], 2)
        self.assertEqual(rows[2][-1]["text"], "97.5")
        self.assertEqual(rows[-1][1]["text"], "97.9")
        self.assertEqual(rows[-1][-1]["text"], "")

    def attendance_html(self, rows="", count=0, paging=""):
        return f'''<form name="studentAttendanceListForm">
        <input name="filterDefinitionId" value="###currentYear">
        <input name="userParam" value="Absences: 1.0 Tardies: 0">
        <table><tr class="listHeader"><th></th><th>Date</th><th>Code</th><th>Reason</th></tr>
        {rows}</table>{paging}</form><script>totalRecords = {count};</script>'''

    def test_attendance_dates_reason_and_partial_page(self):
        rows = '''<tr class="listCell listRowHeight"><td></td><td>
        <a href="javascript:doParamSubmit(2100, f, 'ATT123')">9/30/2026</a></td><td>T-E</td><td>Dr.&nbsp;Appointment</td></tr>
        <tr class="listCell"><td></td><td>10/1/2026</td><td>A-E</td><td></td></tr>'''
        data = read_attendance(self.attendance_html(rows, 2))
        self.assertEqual(data["scope"], "Current school year")
        self.assertEqual([r["date"] for r in data["records"]], ["2026-10-01", "2026-09-30"])
        self.assertEqual(data["records"][1]["reason"], "Dr. Appointment")
        self.assertEqual(data["records"][1]["oid"], "ATT123")
        self.assertFalse(data["partial"])
        self.assertTrue(read_attendance(self.attendance_html(rows, 3))["partial"])
        self.assertTrue(read_attendance(self.attendance_html(rows, 2, '<a href="javascript:nextPage()">Next</a>'))["partial"])

    def test_empty_attendance_is_distinct_from_unreadable_or_expired(self):
        data = read_attendance(self.attendance_html())
        self.assertTrue(data["available"])
        self.assertEqual(data["records"], [])
        with self.assertRaises(AuthenticationRequired):
            read_attendance('<html><form name="loginForm"></form></html>')
        with self.assertRaises(AspenError):
            read_attendance('<form name="studentAttendanceListForm">Unexpected structure</form>')

    def test_activity_ownership_date_order_and_precision(self):
        xml = '''<recent-activity-list daterange="4" grades="true" attendance="true">
        <recent-activity studentoid="other"><gradebookScore date="2027-01-01" grade="100"/></recent-activity>
        <recent-activity studentoid="own">
          <attendance oid="older" date="2025-12-31" code="A-E" absent="true" excused="true"/>
          <periodAttendance oid="tie-first" date="2026-10-01" period="1,2" classname="Math" code="T" tardy="true" excused="false"/>
          <gradebookScore oid="sept" date="2026-09-30" grade="5" assignmentname="Practice"/>
          <gradebookScore oid="tie-second" date="2026-10-01" grade="0" assignmentname="Quiz"/>
          <unknownActivity date="2026-10-01"/>
        </recent-activity></recent-activity-list>'''
        data = read_activity(xml, "own")
        self.assertEqual([e["oid"] for e in data["events"]], ["tie-first", "tie-second", "sept", "older"])
        self.assertEqual(data["events"][1]["grade"], "0")
        self.assertEqual(data["events"][1]["dateMeaning"], "posted")
        self.assertEqual(data["events"][0]["dateMeaning"], "attendance")
        self.assertEqual(data["events"][0]["period"], "1,2")
        self.assertFalse(data["events"][0]["excused"])
        self.assertEqual(data["events"][1]["excused"], None)
        self.assertTrue(all(e["datePrecision"] == "day" for e in data["events"]))
        self.assertEqual(data["unsupportedTypes"], ["unknownActivity"])

    def test_activity_rejects_wrong_student_bad_dates_and_login(self):
        for body in ['<html>Login</html>', '<recent-activity-list>',
                     '<recent-activity-list><recent-activity studentoid="other"/></recent-activity-list>',
                     '<recent-activity-list><recent-activity studentoid="own"><attendance date="bad"/></recent-activity></recent-activity-list>']:
            with self.subTest(body=body), self.assertRaises(AspenError):
                read_activity(body, "own")
        self.assertEqual(read_activity('<recent-activity-list><recent-activity studentoid="own"/></recent-activity-list>', "own")["events"], [])

    def test_sync_fetches_desktop_attendance_and_posting_feed(self):
        from types import SimpleNamespace
        jar = parse_cookies(json.dumps([
            {"name": "JSESSIONID", "value": "api", "domain": "aspen.darienps.org", "path": "/app"},
            {"name": "JSESSIONID", "value": "desktop", "domain": "aspen.darienps.org", "path": "/aspen"}]))
        client = AspenClient(jar)
        with patch.object(client, "api", side_effect=[{"personOid": "person"}, {"studentOid": "own", "schoolOid": "school", "name": "Test"}, [], []]), \
             patch.object(client, "request", side_effect=[SimpleNamespace(text='<form name="classListForm"></form>'),
                        SimpleNamespace(text=self.attendance_html()),
                        SimpleNamespace(text='<recent-activity-list daterange="4" grades="true" attendance="true"><recent-activity studentoid="own"><gradebookScore date="2026-10-01" grade="0"/></recent-activity></recent-activity-list>'),
                        SimpleNamespace(text=self.schedule_matrix_html())]) as request:
            data = client.sync_period()
        self.assertTrue(data["attendance"]["available"])
        self.assertEqual(data["activityFeed"]["events"][0]["grade"], "0")
        self.assertEqual(data["schedule"]["days"]["A"][0]["courseName"], "400 AP Microeconomics")
        self.assertEqual(request.call_args_list[0].args[1], "/aspen/portalClassList.do?navkey=academics.classes.list")
        self.assertEqual(request.call_args_list[2].kwargs["params"]["preferences"].count('id="dateRange"'), 1)
        self.assertEqual(request.call_args_list[-1].args[1], "/aspen/studentScheduleContextList.do?navkey=myInfo.sch.list")

    def test_schedule_navigation_follows_only_its_matrix_redirect(self):
        from types import SimpleNamespace
        client = AspenClient(requests.cookies.RequestsCookieJar())
        def redirect(location):
            return SimpleNamespace(status_code=302, headers={"Location": location})
        matrix = "/aspen/studentScheduleMatrix.do?navkey=myInfo.sch.matrix&viewDate=10/27/2026&userEvent=0"
        page = SimpleNamespace(status_code=200, ok=True, headers={}, text="matrix")
        with patch.object(client.session, "request", side_effect=[redirect(matrix), page]) as request:
            self.assertEqual(client.request("GET", "/aspen/studentScheduleContextList.do?navkey=myInfo.sch.list").text, "matrix")
        self.assertEqual(request.call_args_list[1].args, ("GET", "https://aspen.darienps.org" + matrix))
        for location in ("/aspen/logon.do", matrix):
            with self.subTest(location=location), \
                 patch.object(client.session, "request", return_value=redirect(location)), \
                 self.assertRaises(AuthenticationRequired):
                client.request("GET", "/aspen/studentScheduleMatrix.do" if location == matrix else "/aspen/studentScheduleContextList.do")

    @staticmethod
    def schedule_matrix_html():
        """Aspen's matrix markup, trimmed to days A and B and periods 1–4, 9 and advisory."""
        def course(span, number, name, teacher, room, periods):
            return (f'<td style="background-color:#F5F5DC;" rowspan="{span}"><table><tr><td align="center">'
                    f'{number}<br>{name}<br>{teacher}<br>{room}<br> </td></tr>'
                    f'<tr><td align="right" style="font-weight: bold;"> {periods} </td></tr></table></td>')
        def label(text):
            return f'<td><table><tr><th nowrap> {text} </th></tr></table></td>'
        empty = '<td style="background-color:#FFFFFF;" rowspan="1"> </td>'
        return ('<form name="scheduleMatrixForm"><table cellspacing="1"><tbody>'
                '<tr class="listHeader"><th>&nbsp;</th><th> A&nbsp;-&nbsp;A </th><th> B&nbsp;-&nbsp;B </th></tr>'
                '<tr>' + label("1-Period 1") + course(2, "HST502-520A-002", "400 AP Microeconomics", "Balazs, Stephen", "G209", "2(A-H)")
                + course(2, "MUS603-599A-003", "400 Wind Ensemble Honors PT", "Grauer, Jonathan", "F013", "1(A-C,") + '</tr>'
                '<tr>' + label("2-Period 2") + '</tr>'
                '<tr>' + label("3-Period 3") + course(1, "MUS603-599A-003", "400 Wind Ensemble Honors PT", "Grauer, Jonathan", "F013", "1(A-C,")
                + empty + '</tr>'
                '<tr>' + label("4-Period 4") + course(1, "SCI714-544A-001", "400 AP Physics C: Electricity and Magnetism", "Nesteruk, Kurt", "C107", "1(D,H) 8(A")
                + course(1, "PE314-590X-012", "12th Grade Health", "Chase, Timothy", "", "6(B,D,F,H)") + '</tr>'
                '<tr>' + label("9-Period 9") + empty + empty + '</tr>'
                '<tr>' + label("10-Period 10") + course(1, "HRM101-102X-039", "Advisory 12", "Casey, Kelly", "B315", "10(A-H)") * 2 + '</tr>'
                '</tbody></table></form>')

    def test_schedule_matrix_spans_splits_and_ignored_periods(self):
        days = read_schedule_matrix(self.schedule_matrix_html())["days"]
        self.assertEqual(set(days), {"A", "B"})
        micro = {"courseName": "400 AP Microeconomics", "teacherName": "Balazs, Stephen", "room": "G209"}
        wind = {"courseName": "400 Wind Ensemble Honors PT", "teacherName": "Grauer, Jonathan", "room": "F013"}
        self.assertEqual(days["A"][:4], [micro, micro, wind,
                                         {"courseName": "400 AP Physics C: Electricity and Magnetism",
                                          "teacherName": "Nesteruk, Kurt", "room": "C107"}])
        self.assertEqual(days["B"][:4], [wind, wind, None,
                                         {"courseName": "12th Grade Health", "teacherName": "Chase, Timothy", "room": ""}])
        # Periods 5–8 are absent from this fixture; period 9 and advisory are never read.
        self.assertEqual(days["A"][4:], [None] * 4)
        self.assertNotIn("Advisory 12", json.dumps(days))

    def test_schedule_matrix_requires_desktop_session_and_grid(self):
        with self.assertRaises(AuthenticationRequired):
            read_schedule_matrix("<html>Login</html>")
        with self.assertRaises(AspenError):
            read_schedule_matrix('<form name="scheduleMatrixForm"><table><tr><td>Nothing</td></tr></table></form>')


class StateTests(unittest.TestCase):
    def test_expiration_preserves_last_successful_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            snapshot = {"mode": "live", "student": {"studentOid": "own"}, "classes": [], "syncedAt": "previous"}
            store.save(snapshot)
            class ExpiredClient:
                def sync(self):
                    raise AuthenticationRequired("Reconnect Aspen.")
            store.client = ExpiredClient()
            store.needs_auth = False
            store.refresh()
            state = store.view()
            self.assertEqual(state["snapshot"], snapshot)
            self.assertTrue(state["needsAuth"])
            self.assertTrue(state["stale"])
            self.assertFalse(state["syncing"])
            self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)

    def test_mutations_require_local_origin_and_token(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory)
            client = app.test_client()
            self.assertEqual(client.post("/api/demo").status_code, 403)
            token = client.get("/api/state").json["csrfToken"]
            self.assertEqual(client.post("/api/demo", headers={"X-CSRF-Token": token, "Origin": "https://evil.example"}).status_code, 403)
            app.config["PUBLIC_URL"] = "http://localhost"
            self.assertEqual(client.get("/api/state", headers={"Host": "evil.example"}).status_code, 403)
            response = client.post("/api/demo", headers={"X-CSRF-Token": token})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["snapshot"]["mode"], "demo")
            self.assertNotIn("cookies", response.json)
            page = client.get("/")
            self.assertIn("style-src 'self'", page.headers["Content-Security-Policy"])
            page.close()

    def test_cookie_import_reports_specific_errors_without_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory)
            client = app.test_client()
            token = client.get("/api/state").json["csrfToken"]
            exports = [
                ({"appSession": "", "csrf": "private-cookie-value"}, "Enter the JSESSIONID"),
                ({"appSession": "private-cookie-value", "csrf": ""}, "Enter the VITHAR_CSRF"),
                ({"appSession": "private-cookie-value;bad", "csrf": "csrf"}, "invalid"),
                ({"appSession": [], "csrf": "private-cookie-value"}, "valid JSESSIONID"),
            ]
            with patch("store.AspenClient") as aspen_client:
                for raw, expected in exports:
                    with self.subTest(expected=expected):
                        response = client.post("/api/session", json={"cookieValues": raw},
                                               headers={"X-CSRF-Token": token})
                        self.assertEqual(response.status_code, 400)
                        self.assertIn(expected, response.json["error"])
                        self.assertNotIn("private-cookie-value", response.json["error"])
                aspen_client.assert_not_called()

    def test_failed_sync_does_not_publish_partial_data(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.save({"syncedAt": "previous", "classes": ["saved"]})
            class BrokenClient:
                def sync(self):
                    raise ValueError("Unexpected response")
            store.client = BrokenClient()
            store.refresh()
            self.assertEqual(store.snapshot["classes"], ["saved"])
            self.assertIsNotNone(store.view()["error"])

    def test_feature_refresh_failure_keeps_saved_data_with_stale_label(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            identity = {"mode": "live", "student": {"studentOid": "own"}}
            store.save({**identity, "attendance": {"available": True, "records": ["saved"], "fetchedAt": "earlier"}})
            class PartialClient:
                def sync(self):
                    return {**identity, "attendance": {"available": False, "records": [], "error": "Desktop expired"}}
            store.client = PartialClient()
            store.refresh()
            data = store.snapshot["attendance"]
            self.assertEqual(data["records"], ["saved"])
            self.assertTrue(data["stale"])
            self.assertEqual(data["fetchedAt"], "earlier")
            self.assertEqual(data["error"], "Desktop expired")


if __name__ == "__main__":
    unittest.main()
