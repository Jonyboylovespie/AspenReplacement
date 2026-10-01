"""Optional browser integration checks using a local SSO fixture, never Google."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import lz4.block

from flask import make_response
from werkzeug.serving import make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aspen import AuthenticationRequired
from aspen import demo_snapshot
from helpers import create_test_app as create_app
from sign_in import BrowserLogin, SignInError, browser_executable
from browsers import BrowserChoice
from session_profiles import ExistingBrowserLogin


@unittest.skipUnless(os.environ.get("BETTERASPEN_BROWSER_TESTS") == "1", "Set BETTERASPEN_BROWSER_TESTS=1 to run browser integration checks.")
class BrowserLoginTests(unittest.TestCase):
    def setUp(self):
        # Use an isolated Store, including its cache and sign-in profile.
        self.directory = tempfile.TemporaryDirectory()
        fixture = create_app(self.directory.name)
        self.store = fixture.extensions["aspen_store"]
        self.existing_session_profile = None
        @fixture.get("/login")
        def login():
            response = make_response("<h1>Local school sign-in fixture</h1>")
            response.set_cookie("JSESSIONID", "fixture-api", path="/app", httponly=True)
            response.set_cookie("JSESSIONID", "fixture-desktop", path="/aspen", httponly=True)
            response.set_cookie("VITHAR_CSRF", "fixture-csrf", path="/")
            if self.existing_session_profile is not None:
                backup = self.existing_session_profile / "sessionstore-backups"
                backup.mkdir(parents=True, exist_ok=True)
                cookies = [{"name": name, "value": value, "host": "aspen.darienps.org", "path": path}
                           for name, value, path in [("JSESSIONID", "fixture-api", "/app"),
                                                    ("JSESSIONID", "fixture-desktop", "/aspen"),
                                                    ("VITHAR_CSRF", "fixture-csrf", "/")]]
                data = json.dumps({"cookies": cookies}).encode()
                (backup / "recovery.jsonlz4").write_bytes(b"mozLz40\0" + lz4.block.compress(data))
            return response
        self.server = make_server("127.0.0.1", 0, fixture, threaded=True)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.patches = [patch("sign_in.ORIGIN", self.origin),
                        patch("sign_in.SIGN_IN_URL", self.origin + "/login")]
        for item in self.patches:
            item.start()

    def tearDown(self):
        self.store.sign_in.cancel()
        if self.store.sign_in.thread:
            self.store.sign_in.thread.join(timeout=2)
        for item in reversed(self.patches):
            item.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.directory.cleanup()

    def test_real_browser_captures_http_only_cookies_and_waits_for_authentication(self):
        attempts = []
        phases = []
        def verify(raw):
            cookies = json.loads(raw)
            sessions = {cookie["path"]: cookie["value"] for cookie in cookies if cookie["name"] == "JSESSIONID"}
            self.assertEqual(sessions, {"/app": "fixture-api", "/aspen": "fixture-desktop"})
            self.assertTrue(all(cookie["httpOnly"] for cookie in cookies if cookie["name"] == "JSESSIONID"))
            attempts.append(raw)
            if len(attempts) == 1:
                raise AuthenticationRequired("Session cookies exist before Google returns.")
            return "verified-client", {"studentOid": "fixture-student"}
        result = BrowserLogin(self.directory.name, timeout=12, headless=True)(
            threading.Event(), lambda phase, message: phases.append(phase), verify)
        self.assertEqual(result, ("verified-client", {"studentOid": "fixture-student"}))
        self.assertEqual(len(attempts), 2)
        self.assertEqual(phases, ["waiting", "connecting"])
        self.assertEqual(Path(self.directory.name).stat().st_mode & 0o777, 0o700)

    def test_timeout_never_accepts_an_unauthenticated_session(self):
        def verify(raw):
            raise AuthenticationRequired("Not authenticated.")
        with self.assertRaisesRegex(SignInError, "timed out"):
            BrowserLogin(self.directory.name, timeout=0.5, headless=True)(
                threading.Event(), lambda phase, message: None, verify)

    def test_cancel_closes_browser_without_connecting(self):
        cancelled = threading.Event()
        def report(phase, message):
            cancelled.set()
        def verify(raw):
            self.fail("A cancelled sign-in must not verify or connect.")
        self.assertIsNone(BrowserLogin(self.directory.name, timeout=5, headless=True)(cancelled, report, verify))

    def test_chromium_capture_remains_supported(self):
        choice = BrowserChoice((browser_executable(),), "chromium", "Chromium")
        def verify(raw):
            self.assertEqual(sum(cookie["name"] == "JSESSIONID" for cookie in json.loads(raw)), 2)
            return "verified", {"studentOid": "fixture-student"}
        result = BrowserLogin(self.directory.name, timeout=5, headless=True, browser=choice)(
            threading.Event(), lambda phase, message: None, verify)
        self.assertEqual(result, ("verified", {"studentOid": "fixture-student"}))



if __name__ == "__main__":
    unittest.main()
