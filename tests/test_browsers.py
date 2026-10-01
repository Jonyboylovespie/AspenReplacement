import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browsers import BrowserChoice, FirefoxSession, default_browser, desktop_browser, firefox_command


class BrowserPreferenceTests(unittest.TestCase):
    def test_system_default_is_used_instead_of_installed_chromium(self):
        choice = BrowserChoice(("flatpak", "run", "app.zen_browser.zen"), "firefox", "Zen")
        result = subprocess.CompletedProcess([], 0, stdout="app.zen_browser.zen.desktop\n")
        with patch.dict(os.environ, {}, clear=True), patch("browsers.shutil.which", return_value="/usr/bin/xdg-settings"), \
                patch("browsers.subprocess.run", return_value=result), patch("browsers.desktop_browser", return_value=choice) as desktop:
            self.assertEqual(default_browser(), choice)
            desktop.assert_called_once_with("app.zen_browser.zen.desktop")

    def test_desktop_entry_preserves_flatpak_launcher_and_removes_url_forwarding(self):
        with tempfile.TemporaryDirectory() as directory:
            applications = Path(directory) / "applications"
            applications.mkdir()
            (applications / "app.zen_browser.zen.desktop").write_text(
                "[Desktop Entry]\nName=Zen Browser\nExec=/usr/bin/flatpak run --command=launch-script.sh --file-forwarding app.zen_browser.zen @@u %u @@\n")
            with patch.dict(os.environ, {"XDG_DATA_HOME": directory}), patch("browsers.shutil.which", return_value="/usr/bin/flatpak"):
                choice = desktop_browser("app.zen_browser.zen.desktop")
            self.assertEqual(choice.engine, "firefox")
            self.assertEqual(choice.command, ("/usr/bin/flatpak", "run", "--command=launch-script.sh", "app.zen_browser.zen"))
            command = firefox_command(choice, Path(directory) / "profile with spaces", 12345, "https://example.org/")
            self.assertIn("--filesystem=" + str(Path(directory) / "profile with spaces"), command)
            self.assertIn("--no-remote", command)
            self.assertNotIn("--user-data-dir", command)

    def test_unsupported_default_is_not_silently_replaced(self):
        result = subprocess.CompletedProcess([], 0, stdout="webkit.desktop\n")
        with patch.dict(os.environ, {}, clear=True), patch("browsers.shutil.which", return_value="/usr/bin/xdg-settings"), \
                patch("browsers.subprocess.run", return_value=result), patch("browsers.desktop_browser", side_effect=ValueError("Unsupported default browser")):
            with self.assertRaisesRegex(ValueError, "Unsupported default"):
                default_browser()

    def test_explicit_browser_override_takes_priority(self):
        with patch.dict(os.environ, {"BETTERASPEN_BROWSER": "/usr/bin/firefox"}), \
                patch("browsers.shutil.which", return_value="/usr/bin/firefox"), patch("browsers.subprocess.run") as query:
            self.assertEqual(default_browser().engine, "firefox")
            query.assert_not_called()

    def test_firefox_capture_excludes_google_and_preserves_duplicate_cookie_names(self):
        session = FirefoxSession(Mock())
        def cookie(name, value, domain, path, **extra):
            return {"name": name, "value": {"type": "string", "value": value}, "domain": domain,
                    "path": path, "httpOnly": True, "secure": True, **extra}
        session.command = Mock(return_value={"cookies": [
            cookie("JSESSIONID", "api", "aspen.darienps.org", "/app"),
            cookie("JSESSIONID", "desktop", "aspen.darienps.org", "/aspen"),
            cookie("VITHAR_CSRF", "csrf", "aspen.darienps.org", "/", expiry=2000000000),
            cookie("SID", "google-secret", ".google.com", "/"),
            cookie("JSESSIONID", "wrong-path", "aspen.darienps.org", "/aspen-other"),
        ]})
        captured = session.cookies(["https://aspen.darienps.org/app/", "https://aspen.darienps.org/aspen/"])
        self.assertEqual({(cookie["name"], cookie["path"]) for cookie in captured},
                         {("JSESSIONID", "/app"), ("JSESSIONID", "/aspen"), ("VITHAR_CSRF", "/")})
        self.assertEqual(captured[-1]["expires"], 2000000000)
        self.assertNotIn("google-secret", json.dumps(captured))


if __name__ == "__main__":
    unittest.main()
