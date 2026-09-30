"""Local single-user prototype. Run: python3 app.py"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import signal
import sqlite3
import tempfile
import threading

from flask import Flask, jsonify, request, send_from_directory
import requests

from aspen import AspenClient, AspenError, AuthenticationRequired, CookieImportError, demo_snapshot, parse_cookies
from sign_in import SignInManager
from session_profiles import existing_profile, read_aspen_session

ROOT = Path(__file__).resolve().parent
REFRESH_SECONDS = 300


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.path = self.directory / "snapshot.json"
        self.lock = threading.RLock()
        self.sync_lock = threading.Lock()
        self.client = None
        self.csrf = secrets.token_urlsafe(32)
        self.syncing = False
        self.error = None
        self.needs_auth = True
        self.last_attempt = None
        self.sign_in = SignInManager(self.directory / "aspen-browser", self.lock,
                                     self.verify_session, self.connect_session)
        try:
            self.snapshot = json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self.snapshot = None

    def save(self, snapshot):
        fd, temp = tempfile.mkstemp(dir=self.directory)
        try:
            with os.fdopen(fd, "w") as file:
                json.dump(snapshot, file)
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        self.snapshot = snapshot

    def view(self):
        with self.lock:
            return {"snapshot": self.snapshot, "syncing": self.syncing,
                    "connected": self.client is not None and not self.needs_auth,
                    "needsAuth": self.needs_auth, "error": self.error,
                    "stale": self.needs_auth or self.error is not None,
                    "lastAttempt": self.last_attempt, "refreshSeconds": REFRESH_SECONDS,
                    "csrfToken": self.csrf, "signIn": self.sign_in.view()}

    def verify_session(self, raw):
        jar = parse_cookies(raw if isinstance(raw, str) else json.dumps(raw))
        client = AspenClient(jar)
        # Verify identity before replacing a session or showing another student's cache.
        user = client.api("/users/current")
        student = client.api("/students/studentByPersonOid", {"personOid": user["personOid"]})
        if not isinstance(student, dict) or not student.get("studentOid"):
            raise AspenError("Aspen did not return a valid student account.")
        return client, student

    def connect_session(self, client, student):
        with self.lock:
            if self.snapshot and self.snapshot.get("student", {}).get("studentOid") != student["studentOid"]:
                self.save(None)
            self.client = client
            self.needs_auth = False
            self.error = None
            self.start_refresh()

    def resume_session(self, refresh=True):
        """Restore the saved student's existing login without replacing accounts."""
        with self.lock:
            saved = self.snapshot
            cancelled = self.sign_in.cancelled
            if not saved or saved.get("mode") != "live" or self.syncing or self.sign_in.view()["active"]:
                return False
            if cancelled.is_set():
                return False
            if self.client is not None and not self.needs_auth:
                return True
        try:
            raw = read_aspen_session(existing_profile())
            if not raw:
                return False
            client, student = self.verify_session(raw)
        except (AspenError, CookieImportError, ValueError, TypeError, KeyError, OSError, sqlite3.DatabaseError, requests.RequestException):
            return False
        with self.lock:
            # A late verification must not override a new login, clear, demo,
            # or a snapshot belonging to another student.
            if (self.snapshot is not saved or self.syncing or self.sign_in.view()["active"]
                    or self.sign_in.cancelled is not cancelled or cancelled.is_set()):
                return False
            if student["studentOid"] != saved.get("student", {}).get("studentOid"):
                return False
            if self.client is not None and not self.needs_auth:
                return True
            self.client = client
            self.needs_auth = False
            self.error = None
            if refresh:
                self.start_refresh()
            return True

    def refresh(self, year=None, quarter=None):
        if not self.sync_lock.acquire(blocking=False):
            return False
        try:
            with self.lock:
                client = self.client
                if not client:
                    return False
                self.syncing = True
                self.last_attempt = datetime.now(timezone.utc).isoformat()
            try:
                filters = (self.snapshot or {}).get("gradeFilters", {})
                year = year if year is not None else filters.get("year", "current")
                quarter = quarter if quarter is not None else filters.get("quarter", "current")
                snapshot = client.sync() if (year, quarter) == ("current", "current") else client.sync(year=year, quarter=quarter)
                with self.lock:
                    previous = self.snapshot or {}
                    if previous.get("mode") == snapshot.get("mode") and previous.get("student") == snapshot.get("student"):
                        for feature in ("attendance", "activityFeed"):
                            fresh = snapshot.get(feature, {})
                            saved = previous.get(feature, {})
                            if not fresh.get("available") and saved.get("available"):
                                snapshot[feature] = {**saved, "stale": True, "error": fresh.get("error", "Refresh failed.")}
                    self.save(snapshot)
                    self.error = None
                    self.needs_auth = False
            except AuthenticationRequired as error:
                with self.lock:
                    self.error = str(error)
                    self.needs_auth = True
            except AspenError as error:
                with self.lock:
                    self.error = str(error)
            except (KeyError, TypeError, ValueError, OSError):
                with self.lock:
                    self.error = "Sync failed. Your last successful data remains available. Try refreshing again."
            finally:
                with self.lock:
                    self.syncing = False
            return True
        finally:
            self.sync_lock.release()

    def start_refresh(self, year=None, quarter=None):
        with self.lock:
            if self.syncing:
                return False
            self.syncing = True
        threading.Thread(target=self.refresh, args=(year, quarter), daemon=True).start()
        return True


def create_app(directory=None):
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 512 * 1024
    store = Store(directory or ROOT / ".state")
    app.extensions["aspen_store"] = store

    @app.before_request
    def local_only():
        if request.host.split(":")[0] not in {"127.0.0.1", "localhost"}:
            return jsonify(error="This prototype accepts only localhost requests."), 403
        origin = request.headers.get("Origin")
        if origin and origin != request.host_url.rstrip("/"):
            return jsonify(error="Cross-origin requests are disabled."), 403
        if request.method == "POST" and not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), store.csrf):
            return jsonify(error="Reload this page before trying again."), 403

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.get("/")
    def index():
        return send_from_directory(ROOT / "static", "index.html")

    @app.get("/app.js")
    def javascript():
        return send_from_directory(ROOT / "static", "app.js")

    @app.get("/styles.css")
    def stylesheet():
        return send_from_directory(ROOT / "static", "styles.css")

    @app.get("/fonts/<path:filename>")
    def font(filename):
        return send_from_directory(ROOT / "static" / "fonts", filename)

    @app.get("/icons/<path:filename>")
    def icon(filename):
        return send_from_directory(ROOT / "static" / "icons", filename)

    @app.get("/api/state")
    def state():
        return jsonify(store.view())

    @app.post("/api/sign-in")
    def sign_in():
        with store.lock:
            if store.syncing:
                return jsonify(error="Wait for the current sync to finish."), 409
            if not store.sign_in.start():
                return jsonify(error="A sign-in window is already open or closing."), 409
        return jsonify(store.view()), 202

    @app.post("/api/sign-in/cancel")
    def cancel_sign_in():
        store.sign_in.cancel()
        return jsonify(store.view())

    @app.post("/api/session")
    def connect():
        with store.lock:
            if store.syncing or store.sign_in.view()["active"]:
                return jsonify(error="Wait for the current sync to finish."), 409
            body = request.get_json(silent=True) or {}
            try:
                raw = body.get("cookies", "")
                client, student = store.verify_session(raw)
            except CookieImportError as error:
                return jsonify(error=str(error)), 400
            except (ValueError, TypeError, AttributeError):
                return jsonify(error="Invalid cookie export. Use an Aspen JSON or Netscape cookie export."), 400
            except AspenError as error:
                return jsonify(error=str(error)), 401
            except (KeyError, requests.RequestException):
                return jsonify(error="Aspen did not return a valid student account."), 400
            store.connect_session(client, student)
        return jsonify(store.view()), 202

    @app.post("/api/refresh")
    def refresh():
        if store.sign_in.view()["active"]:
            return jsonify(error="Finish or cancel Google sign-in before refreshing."), 409
        if store.client is None or store.needs_auth:
            return jsonify(error="Connect an Aspen session first."), 401
        store.start_refresh()
        return jsonify(store.view()), 202

    @app.post("/api/demo")
    def demo():
        with store.lock:
            if store.syncing:
                return jsonify(error="Wait for the current sync to finish."), 409
            store.sign_in.cancel()
            store.client = None
            store.needs_auth = False
            store.error = None
            store.save(demo_snapshot())
        return jsonify(store.view())

    @app.post("/api/grades")
    def grade_period():
        with store.lock:
            if not store.snapshot:
                return jsonify(error="Connect Aspen or load sample data first."), 400
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                return jsonify(error="Choose a school year and quarter."), 400
            year, quarter = body.get("year"), body.get("quarter")
            if not isinstance(year, str) or not isinstance(quarter, str):
                return jsonify(error="Choose a school year and quarter."), 400
            if year == "previous" and quarter == "current":
                quarter = "all"
            periods = store.snapshot.get("gradePeriods", {})
            period = periods.get(f"{year}:{quarter}")
            if not period:
                return jsonify(error="That period is not cached. Refresh Aspen to download all school years and quarters."), 400
            # Compatibility for existing callers: selection only changes the
            # local view. It never reconnects or sends a request to Aspen.
            store.save({**store.snapshot, **period})
        return jsonify(store.view())

    @app.post("/api/disconnect")
    def disconnect():
        with store.lock:
            if store.syncing:
                return jsonify(error="Wait for the current sync to finish."), 409
            store.sign_in.cancel()
            store.client = None
            store.needs_auth = True
            store.error = None
        return jsonify(store.view())

    @app.post("/api/clear")
    def clear():
        with store.lock:
            if store.syncing:
                return jsonify(error="Wait for the current sync to finish."), 409
            store.sign_in.cancel()
            store.client = None
            store.needs_auth = True
            store.error = None
            store.save(None)
        return jsonify(store.view())

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error="The cookie export is too large."), 413

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5173)
    args = parser.parse_args()
    app = create_app()
    store = app.extensions["aspen_store"]

    threading.Thread(target=store.resume_session, daemon=True).start()

    def periodically_refresh():
        while True:
            threading.Event().wait(REFRESH_SECONDS)
            with store.lock:
                ready = store.client is not None and not store.needs_auth and not store.sign_in.view()["active"]
            if ready:
                store.refresh()

    threading.Thread(target=periodically_refresh, daemon=True).start()
    def stop_server(_signum, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop_server)
    try:
        app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)
    finally:
        store.sign_in.cancel()
        if store.sign_in.thread:
            store.sign_in.thread.join(timeout=5)
