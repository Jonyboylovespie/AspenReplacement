import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import lz4.block

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from session_profiles import ExistingBrowserLogin, existing_profile, read_aspen_session
from browsers import BrowserChoice


class ExistingSessionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.profile = Path(self.directory.name)
        (self.profile / "sessionstore-backups").mkdir()

    def tearDown(self):
        self.directory.cleanup()

    def write_session(self, cookies):
        data = json.dumps({"cookies": cookies, "windows": []}).encode()
        (self.profile / "sessionstore-backups/recovery.jsonlz4").write_bytes(b"mozLz40\0" + lz4.block.compress(data))

    def cookies(self):
        return [
            {"name": "JSESSIONID", "value": "api", "host": "aspen.darienps.org", "path": "/app", "httponly": True},
            {"name": "JSESSIONID", "value": "desktop", "host": "aspen.darienps.org", "path": "/aspen"},
            {"name": "VITHAR_CSRF", "value": "csrf", "host": "aspen.darienps.org", "path": "/"},
            {"name": "SID", "value": "unrelated-google-secret", "host": ".google.com", "path": "/"},
        ]

    def test_session_restore_captures_both_paths_and_excludes_other_sites(self):
        self.write_session(self.cookies())
        raw = read_aspen_session(self.profile)
        self.assertNotIn("unrelated-google-secret", raw)
        self.assertEqual({cookie["path"] for cookie in json.loads(raw) if cookie["name"] == "JSESSIONID"}, {"/app", "/aspen"})

    def test_logout_does_not_resurrect_an_older_backup(self):
        self.write_session(self.cookies())
        recovery = self.profile / "sessionstore-backups/recovery.jsonlz4"
        recovery.rename(self.profile / "sessionstore-backups/recovery.baklz4")
        self.write_session([])
        self.assertIsNone(read_aspen_session(self.profile))

    def test_persistent_cookies_are_scoped_and_original_database_is_untouched(self):
        database = self.profile / "cookies.sqlite"
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE moz_cookies (name,value,host,path,expiry,isSecure,isHttpOnly)")
            connection.executemany("INSERT INTO moz_cookies VALUES (?,?,?,?,?,?,?)", [
                ("cf_clearance", "clearance", ".darienps.org", "/", 2000000000, 1, 1),
                ("SID", "unrelated-google-secret", ".google.com", "/", 2000000000, 1, 1),
            ])
        connection.close()
        before = database.read_bytes()
        self.write_session(self.cookies())
        raw = read_aspen_session(self.profile)
        self.assertIn("clearance", raw)
        self.assertNotIn("unrelated-google-secret", raw)
        self.assertEqual(database.read_bytes(), before)

    def test_existing_login_does_not_launch_or_close_a_browser(self):
        self.write_session(self.cookies())
        verify = Mock(return_value=("client", {"studentOid": "own-student"}))
        with patch("subprocess.Popen") as launch:
            result = ExistingBrowserLogin(profile=self.profile)(threading.Event(), Mock(), verify)
        launch.assert_not_called()
        self.assertEqual(result, ("client", {"studentOid": "own-student"}))
        verify.assert_called_once()

    def test_install_profile_takes_priority_over_legacy_default(self):
        root = self.profile / ".var/app/app.zen_browser.zen/.zen"
        root.mkdir(parents=True)
        for name in ("old", "current"):
            (root / name).mkdir()
            (root / name / "cookies.sqlite").touch()
        (root / "profiles.ini").write_text("[Profile0]\nPath=old\nIsRelative=1\nDefault=1\n[InstallABC]\nDefault=current\n")
        with patch("session_profiles.Path.home", return_value=self.profile), \
                patch("session_profiles.default_browser", return_value=BrowserChoice(("zen",), "firefox", "Zen Browser")):
            self.assertEqual(existing_profile(), root / "current")


if __name__ == "__main__":
    unittest.main()
