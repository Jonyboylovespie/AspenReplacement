import tempfile
import threading
import unittest
from unittest.mock import patch

import requests
from cryptography.fernet import Fernet
import json

from app import Store
from helpers import create_test_app as create_app
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
                return [{"studentScheduleOid": "schedule", "courseName": "Math", "courseNumber": "PRIVATE-101", "percentageValue": 0,
                         "sectionTermAverage": "A+", "displayLetterGradesOnly": True, "letterGrade": "A+"}]
            if path == "/assignments":
                return [{"oid": "assignment", "name": "Quiz", "letterGrade": "A+",
                         "scoreLightModels": [{"score": "0", "letterGrade": "A+", "specialCode": "M"}]}]
            self.fail(f"Unexpected endpoint: {path}")

        with patch.object(client, "api", side_effect=api):
            data = client.sync_period(quarter="term-q2")
        self.assertEqual(data["classes"][0]["displayGrade"], "0")
        self.assertEqual(data["classes"][0]["gradeSource"], "Aspen API")
        self.assertNotIn("A+", json.dumps(data))
        self.assertNotIn("sectionTermAverage", data["classes"][0])
        self.assertNotIn("displayLetterGradesOnly", data["classes"][0])
        self.assertNotIn("courseNumber", data["classes"][0])
        self.assertNotIn("PRIVATE-101", json.dumps(data))
        score = data["classes"][0]["assignments"][0]["scoreLightModels"][0]
        self.assertEqual(score, {"score": "0", "specialCode": "M"})
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
            data = client.sync_period(year="previous")
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

    def test_refresh_keeps_old_cache_until_sync_completes(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory)
            store = app.extensions["aspen_store"]
            saved = demo_snapshot()
            saved["mode"] = "live"
            store.save(saved)
            requested = threading.Event()
            finish = threading.Event()
            published = threading.Event()

            def sync(year, quarter):
                self.assertEqual((year, quarter), ("previous", "all"))
                requested.set()
                if not finish.wait(2):
                    raise AspenError("Test sync timed out.")
                snapshot = demo_snapshot(year, quarter)
                snapshot["mode"] = "live"
                return snapshot

            client = unittest.mock.Mock()
            client.sync.side_effect = sync
            client.session = requests.Session()
            store.client = client
            store.needs_auth = False
            original_save = store.save

            def save(snapshot):
                original_save(snapshot)
                published.set()

            with patch.object(store, "save", side_effect=save):
                store.start_refresh(year="previous", quarter="all")
                try:
                    self.assertTrue(requested.wait(2))
                    self.assertTrue(store.view()["syncing"])
                    self.assertEqual(store.view()["snapshot"], saved)
                finally:
                    finish.set()
                self.assertTrue(published.wait(2))
                with store.lock:
                    self.assertEqual(store.snapshot["gradeFilters"]["year"], "previous")
                    self.assertEqual(store.snapshot["gradeFilters"]["quarter"], "all")
                # Publication precedes push delivery; finish the refresh before removing its state directory.
                with store.sync_lock:
                    pass

    def test_restart_restores_only_saved_students_session(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory, cipher=Fernet(Fernet.generate_key()))
            store.save({"mode": "live", "student": {"studentOid": "saved-student"}, "classes": []})
            store._save_session(store.cipher.encrypt(json.dumps({"cookies": [], "studentOid": "saved-student"}).encode()))
            for student_oid in ("other-student", "saved-student"):
                client = unittest.mock.Mock()
                with patch.object(store, "verify_session", return_value=(client, {"studentOid": student_oid})), \
                     patch.object(store, "start_refresh") as refresh:
                    restored = store.resume_session()
                self.assertEqual(restored, student_oid == "saved-student")
                self.assertEqual(store.snapshot["student"]["studentOid"], "saved-student")
                if restored:
                    self.assertIs(store.client, client)
                    self.assertFalse(store.needs_auth)
                    refresh.assert_called_once()
                else:
                    self.assertIsNone(store.client)
                    refresh.assert_not_called()

    def test_resume_does_not_override_disconnect_during_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory)
            store = app.extensions["aspen_store"]
            store.save({"mode": "live", "student": {"studentOid": "saved-student"}, "classes": []})

            def verify(raw):
                app.test_client().post("/api/disconnect", headers={"X-CSRF-Token": store.csrf})
                return unittest.mock.Mock(), {"studentOid": "saved-student"}

            store._save_session(store.cipher.encrypt(json.dumps({"cookies": [], "studentOid": "saved-student"}).encode()))
            with patch.object(store, "verify_session", side_effect=verify):
                self.assertFalse(store.resume_session())
            self.assertIsNone(store.client)
            self.assertTrue(store.needs_auth)

    def test_full_sync_caches_both_years_and_reads_assignments_once(self):
        client = AspenClient(requests.cookies.RequestsCookieJar())
        calls = []

        def terms(year):
            return [{"oid": f"{year}-q{i}", "gradeTermId": f"Q{i}"} for i in (1, 2)]

        def api(path, params=None):
            calls.append((path, params))
            if path == "/users/current":
                return {"personOid": "person"}
            if path == "/students/studentByPersonOid":
                return {"studentOid": "student", "schoolOid": "school", "name": "Test"}
            if path == "/schools/forStudent":
                return [{"oid": "old-school"}]
            if path == "/gradeTerm/student":
                return terms(params["schoolYearContext"])
            if path.startswith("/classes/"):
                year = params["districtContext"]
                grade = "80" if path.endswith("q2") else "95"
                return [{"studentScheduleOid": f"{year}-class", "courseName": "Math", "percentageValue": grade}]
            if path.startswith("/gradeTerm/class/"):
                return terms(path.split("/")[-1].replace("-class", ""))
            if path == "/assignments":
                return [{"oid": params["gradeTermOid"], "name": "Quiz", "scoreLightModels": [{"score": "0"}]}]
            self.fail(f"Unexpected endpoint: {path}")

        with patch.object(client, "api", side_effect=api):
            data = client.sync()
            self.assertEqual(set(data["gradePeriods"]), {
                "current:current", "current:all", "current:current-q1", "current:current-q2",
                "previous:all", "previous:previous-q1", "previous:previous-q2"})
            self.assertEqual(data["gradePeriods"]["previous:previous-q2"]["classes"][0]["displayGrade"], "80")
            self.assertEqual(sum(path == "/assignments" for path, _ in calls), 4)
            self.assertEqual(sum(path == "/users/current" for path, _ in calls), 1)
            calls.clear()
            client.sync()
            self.assertEqual(sum(path == "/assignments" for path, _ in calls), 4)


if __name__ == "__main__":
    unittest.main()
