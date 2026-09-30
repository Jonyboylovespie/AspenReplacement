"""Google SSO in a local browser, with automatic Aspen session capture."""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import threading
import time
from urllib.parse import urlencode

import requests

from aspen import AuthenticationRequired, CookieImportError, ORIGIN
from browsers import BrowserChoice, FirefoxSession, default_browser, firefox_command, profile_command
from session_profiles import ExistingBrowserLogin

SIGN_IN_URL = ORIGIN + "/aspen-login/?" + urlencode({
    "deploymentId": "x2sis",
    "districtIdSSO": "*dst",
    "idpName": "Aspen Darien Google SAML",
})
SIGN_IN_TIMEOUT = 600
ACTIVE_PHASES = {"starting", "waiting", "connecting", "cancelling"}


class SignInError(Exception):
    """A sign-in problem safe to display to the user."""


def stop_browser(process):
    if process is None:
        return
    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    # Each sign-in instance has its own process group, including Flatpak wrappers.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def browser_executable():
    configured = os.environ.get("BETTERASSPEN_BROWSER")
    if configured:
        executable = shutil.which(configured)
        if executable:
            return executable
        raise SignInError("The configured sign-in browser was not found. Check BETTERASSPEN_BROWSER and try again.")
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "msedge"):
        executable = shutil.which(name)
        if executable:
            return executable
    for path in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 os.path.expandvars(r"${PROGRAMFILES}/Google/Chrome/Application/chrome.exe")):
        if Path(path).is_file():
            return path
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            executable = playwright.chromium.executable_path
        if Path(executable).is_file():
            return executable
    except ImportError:
        pass
    raise SignInError("Install Chrome or Chromium on this computer, then try signing in again.")


def capture_session(context):
    """Read both Aspen paths; filtering later excludes Google and other cookies."""
    cookies = context.cookies([ORIGIN + "/app/", ORIGIN + "/aspen/"])
    paths = {cookie["path"] for cookie in cookies if cookie["name"] == "JSESSIONID"}
    if not any(path.startswith("/app") for path in paths):
        return None
    if not any(path.startswith("/aspen") for path in paths):
        return None
    if not any(cookie["name"] == "VITHAR_CSRF" for cookie in cookies):
        return None
    return json.dumps(cookies)


class BrowserLogin:
    def __init__(self, profile, timeout=SIGN_IN_TIMEOUT, headless=False, browser=None):
        self.profile = Path(profile)
        self.timeout = timeout
        self.headless = headless
        self.browser = browser

    def __call__(self, cancelled, report, verify):
        try:
            choice = self.browser or default_browser() or BrowserChoice((browser_executable(),), "chromium", "Chromium")
        except ValueError as error:
            raise SignInError(str(error)) from None
        if choice.engine == "firefox":
            return self.firefox_login(choice, cancelled, report, verify)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise SignInError("The sign-in browser support is missing. Install the project's requirements and restart BetterASSpen.") from None
        self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.profile, 0o700)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        debugger_url = f"http://127.0.0.1:{port}"
        process = None
        browser = None
        playwright = None
        try:
            # Launch an ordinary browser, without Playwright's automation launch flags.
            # A numbered port also avoids Chromium's webdriver flag for port zero.
            arguments = [
                *profile_command(choice, self.profile), "--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={port}",
                "--user-data-dir=" + str(self.profile.resolve()), "--no-first-run",
                "--no-default-browser-check", "--new-window", SIGN_IN_URL,
            ]
            if self.headless:
                arguments.append("--headless=new")
            process = subprocess.Popen(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            launch_deadline = time.monotonic() + 30
            while not cancelled.is_set():
                if process.poll() is not None or time.monotonic() >= launch_deadline:
                    raise SignInError("The sign-in browser couldn't open. Close any earlier BetterASSpen sign-in window and try again.")
                try:
                    response = requests.get(debugger_url + "/json/version", timeout=0.5)
                    if response.ok:
                        break
                except requests.RequestException:
                    pass
                cancelled.wait(0.2)
            if cancelled.is_set():
                return None
            playwright = sync_playwright().start()
            browser = playwright.chromium.connect_over_cdp(debugger_url, timeout=15000)
            context = browser.contexts[0]
            report("waiting", "Finish signing in with your school Google account in the browser window.")
            return self.wait_for_session(context, cancelled, report, verify,
                                         lambda: process.poll() is None and bool(context.pages))
        except SignInError:
            raise
        except Exception:
            raise SignInError("Sign-in couldn't finish. Check your connection and try again.") from None
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            if playwright is not None:
                try:
                    playwright.stop()
                except Exception:
                    pass
            stop_browser(process)

    def wait_for_session(self, context, cancelled, report, verify, is_open):
        deadline = time.monotonic() + self.timeout
        next_attempt = 0
        while not cancelled.is_set():
            if not is_open():
                raise SignInError("The sign-in window was closed. Click Sign in with Google to try again.")
            now = time.monotonic()
            if now >= deadline:
                raise SignInError("Sign-in timed out. Click Sign in with Google to open a new window.")
            cookies = capture_session(context)
            if cookies and now >= next_attempt:
                next_attempt = now + 4
                try:
                    verified = verify(cookies)
                except (AuthenticationRequired, CookieImportError):
                    # Session cookies exist before login; verify the student first.
                    pass
                else:
                    report("connecting", "Signed in. Connecting your Aspen dashboard…")
                    return verified
            cancelled.wait(0.5)
        return None

    def firefox_login(self, choice, cancelled, report, verify):
        try:
            from websockets.sync.client import connect
            from websockets.exceptions import InvalidHandshake
        except ImportError:
            raise SignInError("Firefox sign-in support is missing. Install the project's requirements and restart BetterASSpen.") from None
        profile = self.profile / "firefox"
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(profile, 0o700)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = None
        session = None
        try:
            process = subprocess.Popen(firefox_command(choice, profile, port, SIGN_IN_URL, self.headless),
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            deadline = time.monotonic() + 30
            while not cancelled.is_set():
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise SignInError(f"{choice.name} couldn't open for sign-in. Close an earlier sign-in window and try again.")
                try:
                    websocket = connect(f"ws://127.0.0.1:{port}/session", open_timeout=0.5, proxy=None)
                except (OSError, TimeoutError, InvalidHandshake):
                    cancelled.wait(0.2)
                    continue
                session = FirefoxSession(websocket)
                break
            if cancelled.is_set():
                return None
            session.start(SIGN_IN_URL)
            report("waiting", f"Finish signing in with your school Google account in the {choice.name} window.")
            return self.wait_for_session(session, cancelled, report, verify,
                                         lambda: process.poll() is None and session.is_open())
        except SignInError:
            raise
        except Exception:
            raise SignInError(f"Sign-in in {choice.name} couldn't finish. Check your connection and try again.") from None
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
            stop_browser(process)


class SignInManager:
    def __init__(self, profile, lock, verify, connected, driver=None):
        self.lock = lock
        self.verify = verify
        self.connected = connected
        self.driver = driver or ExistingBrowserLogin()
        self.phase = "idle"
        self.message = ""
        self.error = None
        self.cancelled = threading.Event()
        self.thread = None

    def view(self):
        with self.lock:
            return {"phase": self.phase, "message": self.message, "error": self.error,
                    "active": self.phase in ACTIVE_PHASES, "url": SIGN_IN_URL}

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return False
            self.cancelled = threading.Event()
            self.phase = "starting"
            self.message = "Opening Google sign-in…"
            self.error = None
            self.thread = threading.Thread(target=self._run, args=(self.cancelled,), daemon=True)
            self.thread.start()
            return True

    def cancel(self):
        with self.lock:
            self.cancelled.set()
            if self.thread and self.thread.is_alive():
                self.phase = "cancelling"
                self.message = "Cancelling sign-in…"
            else:
                self.phase = "idle"
                self.message = ""
            self.error = None

    def _run(self, cancelled):
        def report(phase, message):
            with self.lock:
                if not cancelled.is_set():
                    self.phase, self.message = phase, message
        try:
            verified = self.driver(cancelled, report, self.verify)
            with self.lock:
                if verified is not None and not cancelled.is_set():
                    self.connected(*verified)
                    self.phase = "connected"
                    self.message = "Connected to Aspen."
        except SignInError as error:
            with self.lock:
                if not cancelled.is_set():
                    self.phase = "error"
                    self.error = str(error)
                    self.message = self.error
        except Exception:
            with self.lock:
                if not cancelled.is_set():
                    self.phase = "error"
                    self.error = "Your Aspen account couldn't be connected. Try signing in again."
                    self.message = self.error
        finally:
            with self.lock:
                if cancelled.is_set():
                    self.phase = "idle"
                    self.message = ""
