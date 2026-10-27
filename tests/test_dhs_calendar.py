import json
import tempfile
import unittest
from unittest.mock import patch

from dhs_calendar import ScheduleCalendar, merge_calendar, parse_district_events, parse_letter_days
from helpers import create_test_app as create_app


class CalendarTests(unittest.TestCase):
    def test_pdf_letter_days_and_advisory_marks(self):
        days = parse_letter_days("DHS 2026-2027\nOctober '26\n1A 2B 5C*\nJanuary '27\n4D\nFebruary '27\n30E\n")
        self.assertEqual(days, {"2026-10-01": "A Day", "2026-10-02": "B Day",
                                "2026-10-05": "C Day - Advisory Schedule", "2027-01-04": "D Day"})

    def test_district_events_use_zero_based_months_and_dhs_calendar_only(self):
        body = '''<div class="fsCalendarDaybox"><div class="fsCalendarDate" data-day="9" data-month="9" data-year="2026"></div>
        <div class="fsCalendarInfo"><span class="fsElementEventColorIcon" title="DHS School Calendar"></span>
        <a class="fsCalendarEventTitle" title="2 Hour Delay-PLC Day">2 Hour Delay-PLC Day</a></div>
        <div class="fsCalendarInfo"><span class="fsElementEventColorIcon" title="BOE Meeting Calendar"></span>
        <a class="fsCalendarEventTitle" title="A Day">A Day</a></div>
        <div class="fsCalendarInfo"><span class="fsElementEventColorIcon" title="DHS School Calendar"></span>
        <a class="fsCalendarEventTitle" title="G  Day">G  Day</a></div></div>'''
        self.assertEqual(parse_district_events(body), {"2026-10-09": ["2 Hour Delay-PLC Day", "G Day"]})

    def test_merge_order_and_district_changes(self):
        seed = {"2026-09-18": "A Day - PLC Late Start Schedule", "2026-10-07": "E Day - Advisory Schedule",
                "2026-10-08": "F Day - Advisory Schedule", "2026-11-25": "B Day", "2026-12-04": "C Day",
                "2026-12-23": "H Day"}
        letter_days = {"2026-09-18": "A Day", "2026-10-07": "E Day - Advisory Schedule", "2026-10-08": "F Day"}
        events = {"2026-10-07": ["E Day"], "2026-11-25": ["Thanksgiving Recess"], "2026-12-04": ["Day"],
                  "2026-12-23": ["H Day", "Early Dismissal"], "2027-01-07": ["D Day", "Q2 Ends"],
                  "2027-02-05": ["2 Hour Delay-PLC Day", "C Day"],
                  "2026-08-26": ['Special "A" Day Schedule-All Periods Meet', "Students Return"],
                  "2027-01-12": ["REVIEW - E Day"]}
        self.assertEqual(merge_calendar(seed, letter_days, events), {
            "2026-08-26": "A Day - All Periods Meet",
            "2026-09-18": "A Day - PLC Late Start Schedule",   # the PDF has no PLC labels
            "2026-10-07": "E Day - Advisory Schedule",         # advisory stays with its date
            "2026-10-08": "F Day",                             # the PDF removed this advisory
            "2026-12-04": "C Day",                             # an unlabeled district event changes nothing
            "2026-12-23": "H Day - Early Dismissal Schedule",
            "2027-01-07": "D Day",
            "2027-02-05": "C Day - PLC Late Start Schedule",
        })

    def test_sources_survive_failures_and_restarts(self):
        with tempfile.TemporaryDirectory() as directory:
            calendar = ScheduleCalendar(directory, auto_refresh=False)
            with patch("dhs_calendar.fetch_letter_days", return_value={"2026-10-27": "C Day - Advisory Schedule"}), \
                 patch("dhs_calendar.fetch_district_events", side_effect=RuntimeError("offline")):
                calendar.refresh()
            with patch("dhs_calendar.fetch_letter_days", side_effect=RuntimeError("offline")), \
                 patch("dhs_calendar.fetch_district_events", return_value={"2026-10-28": ["NO SCHOOL-Snow Day"]}):
                calendar.refresh()
            view = ScheduleCalendar(directory, auto_refresh=False).view()
            self.assertEqual(view["calendar"]["2026-10-27"], "C Day - Advisory Schedule")
            self.assertNotIn("2026-10-28", view["calendar"])
            self.assertIn("regular", view["bell"])
            self.assertTrue(view["generatedAt"])

    def test_schedule_endpoint_requires_sign_in_and_serves_bundled_calendar(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory)
            data = app.test_client().get("/api/schedule").get_json()
            self.assertEqual(data["calendar"]["2026-10-27"], "C Day")
            self.assertEqual(data["bell"]["regular"]["blocks"][0]["start"], "07:40")
            anonymous = app.test_client()
            with anonymous.session_transaction() as state:
                state.clear()
            self.assertEqual(anonymous.get("/api/schedule").status_code, 401)


if __name__ == "__main__":
    unittest.main()
