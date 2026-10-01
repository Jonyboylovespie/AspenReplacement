"""Read only Aspen cookies from the user's existing Firefox/Zen profile."""
import configparser
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time

from aspen import AuthenticationRequired, COOKIE_NAMES, CookieImportError
from browsers import default_browser


def existing_profile():
    configured = os.environ.get("BETTERASPEN_FIREFOX_PROFILE")
    if configured:
        path = Path(configured).expanduser()
        if path.is_dir():
            return path
        raise ValueError("The configured browser profile was not found.")
    choice = default_browser()
    name = choice.name.lower() if choice else "firefox"
    home = Path.home()
    if "zen" in name:
        roots = [home / ".var/app/app.zen_browser.zen/.zen", home / ".zen", home / ".config/zen"]
    elif choice is None or "firefox" in name:
        roots = [home / ".mozilla/firefox", home / ".var/app/org.mozilla.firefox/.mozilla/firefox"]
    elif "librewolf" in name:
        roots = [home / ".librewolf", home / ".var/app/io.gitlab.librewolf-community/.librewolf"]
    else:
        raise ValueError("Automatic connection from an existing browser profile currently supports Zen and Firefox. Open BetterAspen in one of those browsers.")
    for root in roots:
        parser = configparser.ConfigParser(interpolation=None)
        parser.read([root / "profiles.ini", root / "installs.ini"], encoding="utf-8")
        candidates = []
        for section in parser.sections():
            if not section.startswith("Profile") and parser.has_option(section, "Default"):
                candidates.append(root / parser.get(section, "Default"))
        profiles = [section for section in parser.sections() if section.startswith("Profile")]
        profiles.sort(key=lambda section: parser.get(section, "Default", fallback="0") != "1")
        for section in profiles:
            path = Path(parser.get(section, "Path"))
            candidates.append(root / path if parser.get(section, "IsRelative", fallback="1") == "1" else path)
        for path in candidates:
            if path.is_dir() and ((path / "cookies.sqlite").exists() or (path / "sessionstore-backups").exists()):
                return path
    raise ValueError("Your existing browser profile couldn't be found. Open Zen or Firefox once, then try again.")


def persistent_cookies(profile):
    database = profile / "cookies.sqlite"
    if not database.is_file():
        return []
    # Firefox can hold an exclusive database lock. Read a short-lived snapshot,
    # including its WAL, without touching the browser's database or lock files.
    with tempfile.TemporaryDirectory(prefix="betteraspen-session-") as directory:
        target = Path(directory) / "cookies.sqlite"
        for suffix in ("", "-wal"):
            source = profile / ("cookies.sqlite" + suffix)
            if source.exists():
                shutil.copyfile(source, str(target) + suffix)
        with closing(sqlite3.connect(target)) as connection:
            placeholders = ",".join("?" for _ in COOKIE_NAMES)
            rows = connection.execute(
                "SELECT name,value,host,path,expiry,isSecure,isHttpOnly FROM moz_cookies "
                "WHERE ltrim(host,'.') IN ('aspen.darienps.org','darienps.org') "
                f"AND name IN ({placeholders})", tuple(COOKIE_NAMES)).fetchall()
    return [{"name": name, "value": value, "domain": host, "path": path,
             "expires": expiry, "secure": bool(secure), "httpOnly": bool(http_only)}
            for name, value, host, path, expiry, secure, http_only in rows]


def session_cookies(profile):
    import lz4.block
    files = [profile / "sessionstore-backups/recovery.jsonlz4",
             profile / "sessionstore.jsonlz4", profile / "sessionstore-backups/recovery.baklz4"]
    files = sorted((path for path in files if path.is_file()), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    for path in files:
        try:
            raw = path.read_bytes()
            if raw[:8] != b"mozLz40\0":
                continue
            data = json.loads(lz4.block.decompress(raw[8:]))
        except (OSError, ValueError, json.JSONDecodeError, lz4.block.LZ4BlockError):
            continue
        cookies = list(data.get("cookies", []))
        for window in data.get("windows", []):
            cookies.extend(window.get("cookies", []))
        # Only the newest valid session is authoritative, including an empty
        # cookie list after logout. Never resurrect cookies from a prior session.
        return [{"name": cookie["name"], "value": cookie["value"],
                 "domain": cookie["host"], "path": cookie.get("path", "/"),
                 "secure": bool(cookie.get("secure", False)),
                 "httpOnly": bool(cookie.get("httponly", False))}
                for cookie in cookies
                if cookie.get("name") in COOKIE_NAMES
                and str(cookie.get("host", "")).lstrip(".") in {"aspen.darienps.org", "darienps.org"}]
    return []


def read_aspen_session(profile):
    cookies = {}
    for source in (persistent_cookies(profile), session_cookies(profile)):
        for cookie in source:
            cookies[(cookie["name"], cookie["domain"], cookie["path"])] = cookie
    paths = {cookie["path"] for cookie in cookies.values() if cookie["name"] == "JSESSIONID"}
    # Grades can load with the app session alone. A desktop session adds
    # attendance and category tables, but is not required to reconnect.
    if not any(path.startswith("/app") for path in paths):
        return None
    if not any(cookie["name"] == "VITHAR_CSRF" for cookie in cookies.values()):
        return None
    return json.dumps(list(cookies.values()))


class ExistingBrowserLogin:
    def __init__(self, timeout=600, profile=None):
        self.timeout = timeout
        self.profile = Path(profile) if profile is not None else None

    def __call__(self, cancelled, report, verify):
        # Imported here to keep the error type shared without an import cycle.
        from sign_in import SignInError
        try:
            profile = self.profile or existing_profile()
        except ValueError as error:
            raise SignInError(str(error)) from None
        report("waiting", "Finish Google sign-in in the Aspen tab, then return here. Your dashboard connects automatically.")
        deadline = time.monotonic() + self.timeout
        while not cancelled.is_set():
            if time.monotonic() >= deadline:
                raise SignInError("Sign-in timed out. Click Sign in with Google to try again.")
            try:
                cookies = read_aspen_session(profile)
                if cookies:
                    verified = verify(cookies)
                    if cancelled.is_set():
                        return None
                    report("connecting", "Signed in. Connecting your Aspen dashboard…")
                    return verified
            except (AuthenticationRequired, CookieImportError, OSError, sqlite3.DatabaseError):
                pass
            cancelled.wait(2)
        return None
