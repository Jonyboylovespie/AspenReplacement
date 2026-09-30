import tempfile
import unittest
from unittest.mock import patch

import requests

from app import Store, create_app
from aspen import AspenClient, AspenError, demo_snapshot


class GradePeriodTests(unittest.TestCase):
    def test_quarter_queries_use_aspen_term_id_and_preserve_zero(self):
        client = AspenClient(requests.cookies.RequestsCookieJar())
        calls = []

        def api(path, params=None):
            calls.append((path, params))
            if path == "/users/current":
                return {"personOid": "person"}
            if path == "/students/studentByPersonOid":
                return {"studentOid": "student", "schoolOid": "school", "name": "Test"}
            if path in {"/gradeTerm/student", "/gradeTerm/class/schedule"}:
                return [{"oid": "term-q2", "gradeTermId": "Q2"}]
            if path == "/classes/term-q2":
                return [{"studentScheduleOid": "schedule", "courseName": "Math", "percentageValue": 0}]
            if path == "/assignments":
                return [{"oid": "assignment", "name": "Quiz"}]
            self.fail(f"Unexpected endpoint: {path}")

        with patch.object(client, "api", side_effect=api):
            data = client.sync(quarter="term-q2")
        self.assertEqual(data["classes"][0]["displayGrade"], "0")
        self.assertEqual(data["classes"][0]["gradeSource"], "Aspen API")
        self.assertEqual(data["gradeFilters"]["quarter"], "term-q2")
        self.assertIn(("/classes/term-q2", {"studentOid": "student", "schoolOid": "school", "districtContext": "current"}), calls)
        self.assertIn(("/assignments", {"studentOid": "student", "studentScheduleOid": "schedule",
                                       "gradeTermOid": "term-q2", "districtContextOid": "current"}), calls)

    def test_previous_year_uses_previous_school_and_all_quarters(self):
        client = AspenClient(requests.cookies.RequestsCookieJar())
        with patch.object(client, "api", side_effect=[
            {"personOid": "person"}, {"studentOid": "student", "schoolOid": "new-school", "name": "Test"},
            [{"oid": "old-school"}], [{"oid": "old-q1", "gradeTermId": "Q1"}], [],
        ]) as api:
            data = client.sync(year="previous")
        self.assertEqual(data["gradeFilters"]["quarter"], "all")
        self.assertNotIn("current", [option["value"] for option in data["gradeFilters"]["quarters"]])
        self.assertEqual(api.call_args_list[3].args, ("/gradeTerm/student", {"schoolYearContext": "previous", "schoolOid": "old-school"}))
        self.assertEqual(api.call_args_list[4].args, ("/classes/all", {"studentOid": "student", "schoolOid": "old-school", "districtContext": "previous"}))

    def test_selection_survives_refresh_and_failed_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            snapshot = demo_snapshot("previous", "demo-q1")
            snapshot["mode"] = "live"
            store.save(snapshot)
            client = unittest.mock.Mock()
            client.sync.return_value = snapshot
            store.client = client
            store.needs_auth = False
            store.refresh()
            client.sync.assert_called_once_with(year="previous", quarter="demo-q1")
            client.sync.side_effect = AspenError("Quarter unavailable.")
            store.refresh(year="current", quarter="demo-q2")
            self.assertEqual(store.snapshot, snapshot)
            self.assertEqual(store.error, "Quarter unavailable.")
            self.assertFalse(store.syncing)

    def test_grade_endpoint_demo_changes_year_and_quarter(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory)
            client = app.test_client()
            headers = {"X-CSRF-Token": client.get("/api/state").json["csrfToken"]}
            client.post("/api/demo", headers=headers)
            response = client.post("/api/grades", json={"year": "previous", "quarter": "current"}, headers=headers)
            self.assertEqual(response.status_code, 200)
            snapshot = response.json["snapshot"]
            self.assertEqual(snapshot["gradeFilters"]["year"], "previous")
            self.assertEqual(snapshot["gradeFilters"]["quarter"], "all")
            self.assertEqual(snapshot["classes"][0]["courseName"], "Algebra I")
            response = client.post("/api/grades", json={"year": "previous", "quarter": "demo-q2"}, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["snapshot"]["classes"][0]["displayGrade"], "")
            self.assertEqual(response.json["snapshot"]["classes"][0]["assignments"][0]["termName"], "Q2")

    def test_invalid_disconnected_and_conflicting_requests_keep_data(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory)
            client = app.test_client()
            store = app.extensions["aspen_store"]
            store.save(demo_snapshot())
            saved = store.snapshot
            headers = {"X-CSRF-Token": store.csrf}
            for body in [{"year": "invalid", "quarter": "current"}, {"year": [], "quarter": "current"}, []]:
                self.assertEqual(client.post("/api/grades", json=body, headers=headers).status_code, 400)
                self.assertEqual(store.snapshot, saved)
            self.assertEqual(client.post("/api/grades", json={"year": "current", "quarter": "current"}).status_code, 403)
            store.snapshot["mode"] = "live"
            self.assertEqual(client.post("/api/grades", json={"year": "current", "quarter": "current"}, headers=headers).status_code, 401)
            store.syncing = True
            self.assertEqual(client.post("/api/grades", json={"year": "current", "quarter": "current"}, headers=headers).status_code, 409)


if __name__ == "__main__":
    unittest.main()
