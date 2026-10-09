"""Account-isolated Aspen dashboard. Run with Gunicorn or python3 app.py."""
import argparse
import atexit
from datetime import timedelta
from functools import cache
import hashlib
import os
from pathlib import Path
import re
import secrets
import signal
import time
from urllib.parse import urlsplit

from flask import Flask, Response, g, jsonify, redirect, request, session
from authlib.integrations.flask_client import OAuth
from authlib.integrations.base_client.errors import OAuthError
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from joserfc.errors import JoseError
from werkzeug.local import LocalProxy
from werkzeug.middleware.proxy_fix import ProxyFix
import requests

from accounts import AccountDatabase, private_file
from ai_chat import ChatError, academic_context, ask, validate_messages
from runtime import RefreshRuntime
from push_notifications import PushNotifications
from aspen import AspenError, CookieImportError, demo_snapshot, cookies_from_values
from store import REFRESH_SECONDS, Store

ROOT = Path(__file__).resolve().parent

def create_app(directory=None, config=None):
    if not (config and config.get("TESTING")):
        load_dotenv(ROOT / ".env", override=False, interpolate=False)
    app = Flask(__name__, static_folder=str(ROOT / "static"), static_url_path="")
    public_url = os.environ.get("BETTERASPEN_PUBLIC_URL", "").rstrip("/")
    app.config.update(
        MAX_CONTENT_LENGTH=512 * 1024,
        PUBLIC_URL=public_url,
        GOOGLE_CLIENT_ID=os.environ.get("GOOGLE_CLIENT_ID", ""),
        GOOGLE_CLIENT_SECRET=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        SESSION_COOKIE_NAME="betteraspen_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=public_url.startswith("https://"),
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        START_REFRESH=True,
        AI_API_ENDPOINT=os.environ.get("BETTERASPEN_AI_API_ENDPOINT", "https://api.openai.com/v1/responses"),
        AI_API_KEY=os.environ.get("BETTERASPEN_AI_API_KEY", ""),
        AI_WHITELIST=os.environ.get("BETTERASPEN_AI_WHITELIST", ""),
    )
    if config:
        app.config.update(config)
    ai_endpoint = urlsplit(app.config["AI_API_ENDPOINT"])
    if (ai_endpoint.scheme not in {"https", "http"} or not ai_endpoint.netloc
            or ai_endpoint.username or ai_endpoint.password or ai_endpoint.fragment
            or (ai_endpoint.scheme == "http" and ai_endpoint.hostname not in {"localhost", "127.0.0.1", "::1"})):
        raise ValueError("BETTERASPEN_AI_API_ENDPOINT must be HTTPS (or HTTP on localhost).")
    public = app.config["PUBLIC_URL"]
    if public:
        parsed = urlsplit(public)
        if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username
                or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("BETTERASPEN_PUBLIC_URL must be a web origin, such as https://grades.example.com")
    if not config or "SESSION_COOKIE_SECURE" not in config:
        app.config["SESSION_COOKIE_SECURE"] = public.startswith("https://")
    directory = Path(directory or os.environ.get("BETTERASPEN_STATE_DIR", ROOT / ".state"))
    accounts = AccountDatabase(directory)
    push = PushNotifications(accounts, app.config["PUBLIC_URL"] or "https://localhost")
    app.extensions["push_notifications"] = push
    app.secret_key = private_file(directory / "web-secret", secrets.token_bytes(32))
    cipher = Fernet(private_file(directory / "session-key", Fernet.generate_key()))
    runtime = RefreshRuntime(REFRESH_SECONDS, auto_resume=app.config["START_REFRESH"], on_stop=push.stop)
    app.extensions["accounts"] = accounts
    app.extensions["refresh_runtime"] = runtime
    proxy_hops = int(os.environ.get("BETTERASPEN_PROXY_HOPS", "0"))
    if proxy_hops:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=proxy_hops, x_host=proxy_hops)
    oauth = OAuth(app)
    google = oauth.register(
        "google", client_id=app.config["GOOGLE_CLIENT_ID"],
        client_secret=app.config["GOOGLE_CLIENT_SECRET"],
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
    )
    # Build the versioned shell once; asset changes take effect on app restart.
    def version(match):
        path = ROOT / "static" / match[2].lstrip("/")
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
            return f'{match[1]}="{match[2]}?v={digest}"'
        return match[0]

    @cache
    def index_html():
        return re.sub(r'(href|src)="(/[^"?#]+)"', version, (ROOT / "static" / "index.html").read_text())

    def account_store(subject):
        def make_store():
            return Store(accounts.account_directory(subject), cipher=cipher,
                         claim_student=lambda oid: accounts.claim_student(subject, oid),
                         on_snapshot=lambda snapshot: push.changed(subject, snapshot))
        return runtime.add(subject, make_store)

    app.extensions["account_store"] = account_store
    store = LocalProxy(lambda: g.aspen_store)

    def chat_allowed():
        whitelist = {email.strip().casefold() for email in app.config["AI_WHITELIST"].split(",") if email.strip()}
        return bool(g.account and g.account["email"].casefold() in whitelist)

    def view():
        with store.lock:
            state = store.view()
            if request.args.get("revision") == store.snapshot_revision:
                state.pop("snapshot")
            elif request.args.get("compact") == "1":
                state["snapshot"] = store.packed_snapshot
        return {**state, "account": {"email": g.account["email"], "name": g.account["name"]},
                "aiChatEnabled": chat_allowed() and bool(app.config["AI_API_KEY"]),
                "pushPublicKey": push.public_key,
                "signedIn": True, "googleConfigured": bool(app.config["GOOGLE_CLIENT_ID"] and app.config["GOOGLE_CLIENT_SECRET"])}

    @app.before_request
    def protect_accounts():
        public = app.config["PUBLIC_URL"]
        if public and request.host != urlsplit(public).netloc:
            return jsonify(error="Use the configured BetterAspen address."), 403
        origin = request.headers.get("Origin")
        if origin and origin != (public or request.host_url.rstrip("/")):
            return jsonify(error="Cross-origin requests are disabled."), 403
        g.account = None
        if request.path.startswith("/api/") or request.path == "/auth/logout":
            g.account = accounts.account(session.get("login"))
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
        push.revoke_login(session.get("login"))
        accounts.logout(session.get("login"))
        session.clear()
        session["login"] = login
        session.permanent = True
        return redirect("/")

    @app.post("/auth/logout")
    def logout():
        push.revoke_login(session.get("login"))
        accounts.logout(session.get("login"))
        session.clear()
        return jsonify(signedIn=False)

    @app.after_request
    def headers(response):
        if request.endpoint == "static":
            # Revalidate unversioned URLs; fingerprinted assets can be reused.
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if request.args.get("v") else "no-cache"
        else:
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.get("/")
    def index():
        return Response(index_html(), mimetype="text/html")

    @app.get("/api/state")
    def state():
        if not g.account:
            return jsonify(snapshot=None, signedIn=False, account=None, connected=False, aiChatEnabled=False,
                           canRetry=False,
                           needsAuth=True, syncing=False, stale=True, error=None,
                           googleConfigured=bool(app.config["GOOGLE_CLIENT_ID"] and app.config["GOOGLE_CLIENT_SECRET"]))
        return jsonify(view())

    @app.post("/api/push/subscribe")
    def push_subscribe():
        try:
            with store.lock:
                push.subscribe(g.account["subject"], session["login"], store.snapshot, request.get_json(silent=True))
        except ValueError as error:
            return jsonify(error=str(error)), 400
        return jsonify(enabled=True)

    @app.post("/api/push/unsubscribe")
    def push_unsubscribe():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("endpoint"), str):
            return jsonify(error="Invalid push endpoint."), 400
        push.remove(g.account["subject"], body["endpoint"])
        return jsonify(enabled=False)

    @app.post("/api/push/read")
    def push_read():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("endpoint"), str):
            return jsonify(error="Invalid push endpoint."), 400
        push.read(g.account["subject"], body["endpoint"])
        return jsonify(read=True)

    @app.post("/api/chat")
    def chat():
        if not chat_allowed():
            return jsonify(error="AI chat is not enabled for this account."), 403
        if not app.config["AI_API_KEY"]:
            return jsonify(error="AI chat is not configured on the server."), 503
        body = request.get_json(silent=True)
        try:
            messages = validate_messages(body)
        except ChatError as error:
            return jsonify(error=str(error)), 400
        if not store.chat_lock.acquire(blocking=False):
            return jsonify(error="Wait for your current reply to finish."), 429
        try:
            if time.monotonic() - store.chat_last_attempt < 3:
                return jsonify(error="Wait a few seconds before asking another question."), 429
            with store.lock:
                if not store.snapshot:
                    return jsonify(error="Connect Aspen or load sample data before asking a question."), 409
                context = academic_context(store.snapshot, store.needs_auth or store.error is not None)
            store.chat_last_attempt = time.monotonic()
            try:
                reply = ask(app.config["AI_API_ENDPOINT"], app.config["AI_API_KEY"], messages, context)
            except ChatError as error:
                return jsonify(error=str(error)), 502
            return jsonify(reply=reply)
        finally:
            store.chat_lock.release()

    @app.post("/api/session")
    def connect():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error="Invalid session connection."), 400
        with store.lock:
            if store.syncing:
                return jsonify(error="Wait for the current sync to finish."), 409
            version = store.connection_version
        try:
            client, student = store.verify_session(cookies_from_values(body.get("cookieValues")))
        except CookieImportError as error:
            return jsonify(error=str(error)), 400
        except (ValueError, TypeError, AttributeError):
            return jsonify(error="Aspen's session could not be read. Sign into Aspen again."), 400
        except AspenError as error:
            return jsonify(error=str(error)), 401
        except (KeyError, requests.RequestException):
            return jsonify(error="Aspen did not return a valid student account."), 400
        with store.lock:
            if store.syncing or store.connection_version != version:
                return jsonify(error="Connection changed. Submit the cookie values again."), 409
            try:
                store.connect_session(client, student)
            except ValueError as error:
                return jsonify(error=str(error)), 409
            except OSError:
                return jsonify(error="Your Aspen session could not be saved. Check the server's data directory."), 500
        return jsonify(view()), 202

    @app.post("/api/refresh")
    def refresh():
        with store.lock:
            if not store.can_retry():
                return jsonify(error="Connect an Aspen session first."), 401
            store.start_refresh(full=True)
        return jsonify(view()), 202

    @app.post("/api/demo")
    def demo():
        with store.lock:
            if store.syncing:
                return jsonify(error="Wait for the current sync to finish."), 409
            store.reset_connection(snapshot=demo_snapshot())
        return jsonify(view())

    @app.post("/api/disconnect")
    def disconnect():
        with store.lock:
            if store.syncing:
                return jsonify(error="Wait for the current sync to finish."), 409
            store.reset_connection()
        return jsonify(view())

    @app.post("/api/clear")
    def clear():
        with store.lock:
            if store.syncing:
                return jsonify(error="Wait for the current sync to finish."), 409
            store.reset_connection(clear=True)
        return jsonify(view())

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error="The chat request is too large." if request.path == "/api/chat" else "The cookie export is too large."), 413

    if app.config["START_REFRESH"]:
        push.start()
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
