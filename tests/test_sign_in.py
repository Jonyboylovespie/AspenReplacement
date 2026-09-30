import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpers import create_test_app as create_app
from aspen import AuthenticationRequired, demo_snapshot
from sign_in import ORIGIN, SignInError, capture_session


class SignInTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(self.directory.name)
        self.store = self.app.extensions["aspen_store"]
        self.client = self.app.test_client()
        self.headers = {"X-CSRF-Token": self.store.csrf}

    def tearDown(self):
        self.store.sign_in.cancel()
        if self.store.sign_in.thread:
            self.store.sign_in.thread.join(timeout=2)
        self.directory.cleanup()

    def test_capture_reads_both_paths_and_requires_complete_session(self):
        cookies = [
            {"name": "JSESSIONID", "value": "api-secret", "path": "/app"},
            {"name": "JSESSIONID", "value": "desktop-secret", "path": "/aspen"},
            {"name": "VITHAR_CSRF", "value": "csrf-secret", "path": "/"},
        ]
        context = Mock()
        context.cookies.return_value = cookies
        self.assertEqual(json.loads(capture_session(context)), cookies)
        context.cookies.assert_called_with([ORIGIN + "/app/", ORIGIN + "/aspen/"])
        for missing in range(3):
            context.cookies.return_value = [cookie for index, cookie in enumerate(cookies) if index != missing]
            self.assertIsNone(capture_session(context))

    def test_start_is_protected_and_duplicate_sign_in_is_rejected(self):
        waiting = threading.Event()
        def driver(cancelled, report, verify):
            report("waiting", "Finish Google sign-in.")
            waiting.set()
            cancelled.wait(2)
        self.store.sign_in.driver = driver
        self.assertEqual(self.client.post("/api/sign-in").status_code, 403)
        self.assertEqual(self.client.post("/api/sign-in", headers=self.headers).status_code, 202)
        self.assertTrue(waiting.wait(1))
        self.assertEqual(self.client.get("/api/state").json["signIn"]["phase"], "waiting")
        self.assertEqual(self.client.post("/api/sign-in", headers=self.headers).status_code, 409)
        self.assertEqual(self.client.post("/api/sign-in/cancel", headers=self.headers).status_code, 200)
        self.store.sign_in.thread.join(timeout=1)
        self.assertEqual(self.store.sign_in.view()["phase"], "idle")

    def test_success_verifies_identity_clears_other_student_and_starts_sync(self):
        self.store.save({"student": {"studentOid": "previous-student"}, "classes": ["private-old-data"]})
        client = Mock()
        client.api.side_effect = [{"personOid": "new-person"}, {"studentOid": "new-student"}]
        def driver(cancelled, report, verify):
            return verify(json.dumps([
                {"name": "JSESSIONID", "value": "api-secret", "domain": "aspen.darienps.org", "path": "/app"},
                {"name": "JSESSIONID", "value": "desktop-secret", "domain": "aspen.darienps.org", "path": "/aspen"},
                {"name": "VITHAR_CSRF", "value": "csrf-secret", "domain": "aspen.darienps.org", "path": "/"},
            ]))
        self.store.sign_in.driver = driver
        with patch("app.AspenClient", return_value=client), patch.object(self.store, "start_refresh") as refresh:
            self.client.post("/api/sign-in", headers=self.headers)
            self.store.sign_in.thread.join(timeout=1)
            self.assertEqual(client.api.call_args_list[1].args,
                             ("/students/studentByPersonOid", {"personOid": "new-person"}))
            refresh.assert_called_once()
        state = self.client.get("/api/state").json
        self.assertTrue(state["connected"])
        self.assertEqual(state["signIn"]["phase"], "connected")
        self.assertIsNone(state["snapshot"])
        self.assertNotIn("secret", json.dumps(state))

    def test_cancelled_login_cannot_replace_demo_disconnect_or_clear(self):
        for endpoint in ("/api/sign-in/cancel", "/api/demo", "/api/disconnect", "/api/clear"):
            with self.subTest(endpoint=endpoint):
                waiting = threading.Event()
                def driver(cancelled, report, verify):
                    waiting.set()
                    cancelled.wait(2)
                    # Simulate verification finishing after cancellation.
                    return Mock(), {"studentOid": "late-student"}
                self.store.sign_in.driver = driver
                self.client.post("/api/sign-in", headers=self.headers)
                self.assertTrue(waiting.wait(1))
                self.assertEqual(self.client.post(endpoint, headers=self.headers).status_code, 200)
                self.store.sign_in.thread.join(timeout=1)
                self.assertIsNone(self.store.client)
                self.assertFalse(self.store.sign_in.view()["active"])
                if endpoint == "/api/demo":
                    self.assertEqual(self.store.snapshot["mode"], "demo")
                if endpoint == "/api/clear":
                    self.assertIsNone(self.store.snapshot)

    def test_failure_keeps_saved_data_and_allows_retry(self):
        snapshot = demo_snapshot()
        self.store.save(snapshot)
        self.store.sign_in.driver = Mock(side_effect=SignInError("The sign-in window was closed."))
        self.client.post("/api/sign-in", headers=self.headers)
        self.store.sign_in.thread.join(timeout=1)
        self.assertEqual(self.store.sign_in.view()["error"], "The sign-in window was closed.")
        self.assertEqual(self.store.snapshot, snapshot)
        self.assertIsNone(self.store.client)
        self.assertEqual(self.client.post("/api/sign-in", headers=self.headers).status_code, 202)
        self.store.sign_in.thread.join(timeout=1)

    def test_unverified_session_never_connects(self):
        self.store.sign_in.driver = lambda cancelled, report, verify: verify("[]")
        with patch.object(self.store, "verify_session", side_effect=AuthenticationRequired("Expired")):
            # The manager keeps the verifier bound at construction.
            self.store.sign_in.verify = self.store.verify_session
            self.client.post("/api/sign-in", headers=self.headers)
            self.store.sign_in.thread.join(timeout=1)
        self.assertIsNone(self.store.client)
        self.assertEqual(self.store.sign_in.view()["phase"], "error")


if __name__ == "__main__":
    unittest.main()
