"""Account-isolated Aspen dashboard. Run with Gunicorn or python3 app.py."""
import argparse
import atexit
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import signal
import sqlite3
import tempfile
import threading
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, redirect, request, send_from_directory, session
from authlib.integrations.flask_client import OAuth
from authlib.integrations.base_client.errors import OAuthError
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from joserfc.errors import JoseError
from werkzeug.local import LocalProxy
from werkzeug.middleware.proxy_fix import ProxyFix
import requests

from accounts import AccountDatabase, private_file
from runtime import RefreshRuntime
from aspen import AspenClient, AspenError, AuthenticationRequired, CookieImportError, demo_snapshot, parse_cookies
from sign_in import ExtensionSignIn, SignInManager
from session_profiles import existing_profile, read_aspen_session

ROOT = Path(__file__).resolve().parent
REFRESH_SECONDS = 300


class Store:
    def __init__(self, directory, cipher=None, claim_student=None, account_email=None, email_field=None):
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
        self.cipher = cipher
        self.claim_student = claim_student
        self.account_email = account_email
        self.email_field = email_field
        self.session_path = self.directory / "aspen-session.enc"
        self.sign_in = (ExtensionSignIn(self.lock) if cipher else
                        SignInManager(self.directory / "aspen-browser", self.lock,
                                      self.verify_session, self.connect_session))
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
        if self.account_email:
            identity = user
            if self.email_field:
                identity = {"user": user, "student": student}
                for key in self.email_field.split("."):
                    identity = identity.get(key) if isinstance(identity, dict) else None
                emails = [identity]
            else:
                emails = [user.get(key) for key in ("email", "emailAddress", "username", "userName", "loginName")]
            emails = [value.strip().casefold() for value in emails if isinstance(value, str) and "@" in value]
            if not emails:
                raise AspenError("Aspen didn't provide a school email to verify this account. Configure BETTERASSPEN_ASPEN_EMAIL_FIELD to its verified identity field.")
            if self.account_email.casefold() not in emails:
                raise AspenError("Sign into Aspen with the same school Google account you used for BetterASSpen.")
        return client, student

    def connect_session(self, client, student):
        with self.lock:
            if self.claim_student:
                self.claim_student(student["studentOid"])
            self.persist_session(client, student["studentOid"])
            if self.snapshot and self.snapshot.get("student", {}).get("studentOid") != student["studentOid"]:
                self.save(None)
            self.client = client
            self.needs_auth = False
            self.error = None
            if isinstance(self.sign_in, ExtensionSignIn):
                self.sign_in.connected()
            self.start_refresh()

    def persist_session(self, client, student_oid):
        if self.cipher:
            cookies = [{"name": cookie.name, "value": cookie.value, "domain": cookie.domain,
                        "path": cookie.path, "secure": cookie.secure, "expires": cookie.expires}
                       for cookie in client.session.cookies]
            payload = json.dumps({"cookies": cookies, "studentOid": student_oid}).encode()
            self._save_session(self.cipher.encrypt(payload))

    def _save_session(self, payload):
        fd, temporary = tempfile.mkstemp(dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(payload)
            os.replace(temporary, self.session_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def forget_session(self):
        self.session_path.unlink(missing_ok=True)

    def resume_session(self, refresh=True):
        """Restore the saved student's existing login without replacing accounts."""
        with self.lock:
            saved = self.snapshot
            cancelled = self.sign_in.cancelled
            if (self.syncing or self.sign_in.view()["active"]
                    or (saved and saved.get("mode") != "live")
                    or (not saved and not self.cipher)):
                return False
            if cancelled.is_set():
                return False
            if self.client is not None and not self.needs_auth:
                return True
        try:
            if self.cipher:
                stored = json.loads(self.cipher.decrypt(self.session_path.read_bytes()))
                raw = json.dumps(stored["cookies"])
            else:
                raw = read_aspen_session(existing_profile())
            if not raw:
                return False
            client, student = self.verify_session(raw)
        except (AspenError, CookieImportError, ValueError, TypeError, KeyError, OSError,
                InvalidToken, sqlite3.DatabaseError, requests.RequestException):
            return False
        with self.lock:
            # A late verification must not override a new login, clear, demo,
            # or a snapshot belonging to another student.
            if (self.snapshot is not saved or self.syncing or self.sign_in.view()["active"]
                    or self.sign_in.cancelled is not cancelled or cancelled.is_set()):
                return False
            expected = stored["studentOid"] if self.cipher else saved.get("student", {}).get("studentOid")
            if student["studentOid"] != expected:
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
                    if self.claim_student:
                        try:
                            self.claim_student(snapshot["student"]["studentOid"])
                        except ValueError:
                            raise AuthenticationRequired("Aspen returned a different student's session. Reconnect with your school account.") from None
                    previous = self.snapshot or {}
                    if self.cipher:
                        self.persist_session(client, snapshot["student"]["studentOid"])
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


def create_app(directory=None, config=None):
    if not (config and config.get("TESTING")):
        load_dotenv(ROOT / ".env", override=False, interpolate=False)
    app = Flask(__name__, static_folder=None)
    public_url = os.environ.get("BETTERASSPEN_PUBLIC_URL", "").rstrip("/")
    app.config.update(
        MAX_CONTENT_LENGTH=512 * 1024,
        PUBLIC_URL=public_url,
        GOOGLE_CLIENT_ID=os.environ.get("GOOGLE_CLIENT_ID", ""),
        GOOGLE_CLIENT_SECRET=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        SESSION_COOKIE_NAME="betterasspen_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=public_url.startswith("https://"),
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        START_REFRESH=True,
    )
    if config:
        app.config.update(config)
    public = app.config["PUBLIC_URL"]
    if public:
        parsed = urlsplit(public)
        if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username
                or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("BETTERASSPEN_PUBLIC_URL must be a web origin, such as https://grades.example.com")
    if not config or "SESSION_COOKIE_SECURE" not in config:
        app.config["SESSION_COOKIE_SECURE"] = public.startswith("https://")
    directory = Path(directory or os.environ.get("BETTERASSPEN_STATE_DIR", ROOT / ".state"))
    accounts = AccountDatabase(directory)
    app.secret_key = private_file(directory / "web-secret", secrets.token_bytes(32))
    cipher = Fernet(private_file(directory / "session-key", Fernet.generate_key()))
    runtime = RefreshRuntime(REFRESH_SECONDS, auto_resume=app.config["START_REFRESH"])
    app.extensions["accounts"] = accounts
    app.extensions["refresh_runtime"] = runtime
    proxy_hops = int(os.environ.get("BETTERASSPEN_PROXY_HOPS", "0"))
    if proxy_hops:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=proxy_hops, x_host=proxy_hops)
    oauth = OAuth(app)
    google = oauth.register(
        "google", client_id=app.config["GOOGLE_CLIENT_ID"],
        client_secret=app.config["GOOGLE_CLIENT_SECRET"],
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
    )

    def account_store(subject):
        def make_store():
            from contextlib import closing
            with closing(accounts.connect()) as db:
                account = db.execute("SELECT email FROM accounts WHERE subject=?", (subject,)).fetchone()
            return Store(accounts.account_directory(subject), cipher=cipher,
                         account_email=account[0],
                         email_field=os.environ.get("BETTERASSPEN_ASPEN_EMAIL_FIELD"),
                         claim_student=lambda oid: accounts.claim_student(subject, oid))
        return runtime.add(subject, make_store)

    app.extensions["account_store"] = account_store
    store = LocalProxy(lambda: g.aspen_store)

    def view():
        return {**store.view(), "account": {"email": g.account["email"], "name": g.account["name"]},
                "signedIn": True, "googleConfigured": bool(app.config["GOOGLE_CLIENT_ID"] and app.config["GOOGLE_CLIENT_SECRET"])}

    @app.before_request
    def protect_accounts():
        public = app.config["PUBLIC_URL"]
        if public and request.host != urlsplit(public).netloc:
            return jsonify(error="Use the configured BetterASSpen address."), 403
        origin = request.headers.get("Origin")
        if origin and origin != (public or request.host_url.rstrip("/")):
            return jsonify(error="Cross-origin requests are disabled."), 403
        g.account = accounts.account(session.get("login"))
        if request.path.startswith("/api/") or request.path == "/auth/logout":
            if not g.account:
                if request.path != "/api/state":
                    return jsonify(error="Sign in with Google first."), 401
            else:
                g.aspen_store = account_store(g.account["subject"])
                if request.method == "POST" and not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), store.csrf):
                    return jsonify(error="Reload this page before trying again."), 403

    @app.get("/auth/google")
    def google_login():
        if not app.config["GOOGLE_CLIENT_ID"] or not app.config["GOOGLE_CLIENT_SECRET"]:
            return jsonify(error="Configure GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET on the server."), 503
        callback = (app.config["PUBLIC_URL"] or request.host_url.rstrip("/")) + "/auth/google/callback"
        return google.authorize_redirect(callback)

    @app.get("/auth/google/callback")
    def google_callback():
        try:
            # Authlib validates OAuth state, the OIDC nonce, issuer, audience,
            # signature and expiry before supplying the ID token's userinfo.
            token = google.authorize_access_token()
            login = accounts.login(token["userinfo"])
        except (OAuthError, JoseError, ValueError, KeyError, requests.RequestException):
            return redirect("/?login=failed")
        accounts.logout(session.get("login"))
        session.clear()
        session["login"] = login
        session.permanent = True
        return redirect("/")

    @app.post("/auth/logout")
    def logout():
        accounts.logout(session.get("login"))
        session.clear()
        return jsonify(signedIn=False)

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.get("/")
    def index():
        return send_from_directory(ROOT / "static", "index.html")

    @app.get("/app.js")
    def javascript():
        return send_from_directory(ROOT / "static", "app.js")

    @app.get("/connect.js")
    def connector_script():
        return send_from_directory(ROOT / "static", "connect.js")

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
        if not g.account:
            return jsonify(snapshot=None, signedIn=False, account=None, connected=False,
                           needsAuth=True, syncing=False, stale=True, error=None,
                           signIn={"phase": "idle", "active": False, "url": "/auth/google"},
                           googleConfigured=bool(app.config["GOOGLE_CLIENT_ID"] and app.config["GOOGLE_CLIENT_SECRET"]))
        return jsonify(view())

    @app.post("/api/sign-in")
    def sign_in():
        with store.lock:
            if store.syncing:
                return jsonify(error="Wait for the current sync to finish."), 409
            if not store.sign_in.start():
                return jsonify(error="A sign-in window is already open or closing."), 409
        return jsonify(view()), 202

    @app.post("/api/sign-in/cancel")
    def cancel_sign_in():
        store.sign_in.cancel()
        return jsonify(view())

    @app.post("/api/session")
    def connect():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error="Invalid session connection."), 400
        with store.lock:
            remote = isinstance(store.sign_in, ExtensionSignIn)
            if store.syncing or (store.sign_in.view()["active"] and not remote):
                return jsonify(error="Wait for the current sync to finish."), 409
            attempt = store.sign_in.attempt if remote else None
            cancelled = store.sign_in.cancelled
            if remote and (not store.sign_in.view()["active"] or body.get("connectionAttempt") != attempt):
                return jsonify(error="Start Aspen connection again before connecting this session."), 409
        try:
            client, student = store.verify_session(body.get("cookies", ""))
        except CookieImportError as error:
            return jsonify(error=str(error)), 400
        except (ValueError, TypeError, AttributeError):
            return jsonify(error="Aspen's session could not be read. Sign into Aspen again."), 400
        except AspenError as error:
            return jsonify(error=str(error)), 401
        except (KeyError, requests.RequestException):
            return jsonify(error="Aspen did not return a valid student account."), 400
        with store.lock:
            if (store.syncing or store.sign_in.cancelled is not cancelled or cancelled.is_set()
                    or (remote and (not store.sign_in.view()["active"] or store.sign_in.attempt != attempt))):
                return jsonify(error="Connection was cancelled. Start Aspen connection again."), 409
            try:
                store.connect_session(client, student)
            except ValueError as error:
                return jsonify(error=str(error)), 409
            except OSError:
                return jsonify(error="Your Aspen session could not be saved. Check the server's data directory."), 500
        return jsonify(view()), 202

    @app.post("/api/refresh")
    def refresh():
        if store.sign_in.view()["active"]:
            return jsonify(error="Finish or cancel Google sign-in before refreshing."), 409
        if store.client is None or store.needs_auth:
            return jsonify(error="Connect an Aspen session first."), 401
        store.start_refresh()
        return jsonify(view()), 202

    @app.post("/api/demo")
    def demo():
        with store.lock:
            if store.syncing:
                return jsonify(error="Wait for the current sync to finish."), 409
            store.sign_in.cancel()
            store.forget_session()
            store.client = None
            store.needs_auth = False
            store.error = None
            store.save(demo_snapshot())
        return jsonify(view())

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
        return jsonify(view())

    @app.post("/api/disconnect")
    def disconnect():
        with store.lock:
            if store.syncing:
                return jsonify(error="Wait for the current sync to finish."), 409
            store.sign_in.cancel()
            store.forget_session()
            store.client = None
            store.needs_auth = True
            store.error = None
        return jsonify(view())

    @app.post("/api/clear")
    def clear():
        with store.lock:
            if store.syncing:
                return jsonify(error="Wait for the current sync to finish."), 409
            store.sign_in.cancel()
            store.forget_session()
            store.client = None
            store.needs_auth = True
            store.error = None
            store.save(None)
        return jsonify(view())

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error="The cookie export is too large."), 413

    if app.config["START_REFRESH"]:
        # Restore every previously linked account after a server restart.
        from contextlib import closing
        with closing(accounts.connect()) as db:
            subjects = [row[0] for row in db.execute("SELECT subject FROM accounts WHERE student_oid IS NOT NULL")]
        for subject in subjects:
            account_store(subject)
        runtime.start()
        atexit.register(runtime.stop)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5173)
    args = parser.parse_args()
    app = create_app()

    def stop_server(_signum, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop_server)
    try:
        app.run(host="0.0.0.0", port=args.port, debug=False, threaded=True)
    finally:
        app.extensions["refresh_runtime"].stop()
