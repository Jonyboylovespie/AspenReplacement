"""Account snapshot persistence and Aspen connection lifecycle."""
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import threading

from cryptography.fernet import InvalidToken
import requests

from aspen import AspenClient, AspenError, AuthenticationRequired, CookieImportError, parse_cookies
from snapshots import expand_snapshot, pack_snapshot
from storage import atomic_write

REFRESH_SECONDS = 60


class Store:
    def __init__(self, directory, cipher=None, claim_student=None, on_snapshot=None):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.path = self.directory / "snapshot.json"
        self.lock = threading.RLock()
        self.sync_lock = threading.Lock()
        self.chat_lock = threading.Lock()
        self.chat_last_attempt = 0
        self.client = None
        self.csrf = secrets.token_urlsafe(32)
        self.syncing = False
        self.error = None
        self.needs_auth = True
        self.last_attempt = None
        self.cipher = cipher
        self.claim_student = claim_student
        self.on_snapshot = on_snapshot
        self.session_path = self.directory / "aspen-session.enc"
        self.connection_version = 0
        try:
            self.snapshot = expand_snapshot(json.loads(self.path.read_text()))
        except (FileNotFoundError, json.JSONDecodeError):
            self.snapshot = None

        self.packed_snapshot = pack_snapshot(self.snapshot)
        self.snapshot = expand_snapshot(self.packed_snapshot)
        self.snapshot_revision = secrets.token_urlsafe(16)

    def save(self, snapshot):
        packed = pack_snapshot(snapshot)
        atomic_write(self.path, json.dumps(packed, ensure_ascii=False, separators=(",", ":")).encode())
        self.packed_snapshot = packed
        self.snapshot = expand_snapshot(packed)
        self.snapshot_revision = secrets.token_urlsafe(16)

    def view(self):
        with self.lock:
            return {"snapshot": self.snapshot, "snapshotRevision": self.snapshot_revision, "syncing": self.syncing,
                    "connected": self.client is not None and not self.needs_auth,
                    "canRetry": self.can_retry(),
                    "needsAuth": self.needs_auth, "error": self.error,
                    "stale": self.needs_auth or self.error is not None,
                    "lastAttempt": self.last_attempt, "refreshSeconds": REFRESH_SECONDS,
                    "csrfToken": self.csrf}

    def can_retry(self):
        return self.client is not None or (self.cipher is not None and self.session_path.is_file())

    def verify_session(self, raw):
        jar = parse_cookies(raw if isinstance(raw, str) else json.dumps(raw))
        client = AspenClient(jar)
        # Verify identity before replacing a session or showing another student's cache.
        user = client.api("/users/current")
        if (not isinstance(user, dict) or not isinstance(user.get("personOid"), str)
                or not user["personOid"].strip()):
            raise AspenError("Aspen did not return a valid signed-in account. Sign into Aspen again.")
        student = client.api("/students/studentByPersonOid", {"personOid": user["personOid"]})
        if (not isinstance(student, dict) or not isinstance(student.get("studentOid"), str)
                or not student["studentOid"].strip()):
            raise AspenError("Aspen did not return a valid student account.")
        # Possession of an authenticated Aspen session establishes access. The
        # first successful connection binds this student to the Google subject;
        # Google and school email addresses do not need to match.
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
            self.connection_version += 1
            self.start_refresh()

    def persist_session(self, client, student_oid):
        if self.cipher:
            cookies = [{"name": cookie.name, "value": cookie.value, "domain": cookie.domain,
                        "path": cookie.path, "secure": cookie.secure, "expires": cookie.expires}
                       for cookie in client.session.cookies]
            payload = json.dumps({"cookies": cookies, "studentOid": student_oid}).encode()
            self._save_session(self.cipher.encrypt(payload))

    def _save_session(self, payload):
        atomic_write(self.session_path, payload)

    def forget_session(self):
        self.connection_version += 1
        self.session_path.unlink(missing_ok=True)

    def load_session(self):
        stored = json.loads(self.cipher.decrypt(self.session_path.read_bytes()))
        client, student = self.verify_session(json.dumps(stored["cookies"]))
        if student["studentOid"] != stored["studentOid"]:
            raise AuthenticationRequired("Aspen returned a different student's session. Reconnect with your school account.")
        return client

    def resume_session(self, refresh=True):
        """Restore the saved student's existing login without replacing accounts."""
        with self.lock:
            saved = self.snapshot
            version = self.connection_version
            if self.syncing or not self.cipher or (saved and saved.get("mode") != "live"):
                return False
            if self.client is not None and not self.needs_auth:
                return True
        try:
            client = self.load_session()
        except (AspenError, CookieImportError, ValueError, TypeError, KeyError, OSError,
                InvalidToken, sqlite3.DatabaseError, requests.RequestException):
            return False
        with self.lock:
            # A late verification must not override a new login, clear, demo,
            # or a snapshot belonging to another student.
            if self.snapshot is not saved or self.syncing or self.connection_version != version:
                return False
            if self.client is not None and not self.needs_auth:
                return True
            self.client = client
            self.needs_auth = False
            self.error = None
            if refresh:
                self.start_refresh()
            return True

    def refresh(self, year=None, quarter=None, full=False):
        if not self.sync_lock.acquire(blocking=False):
            return False
        try:
            with self.lock:
                client = self.client
                if not self.can_retry():
                    self.syncing = False
                    return False
                self.syncing = True
                self.last_attempt = datetime.now(timezone.utc).isoformat()
            try:
                if client is None:
                    client = self.load_session()
                    with self.lock:
                        self.client = client
                if full:
                    client.invalidate_history()
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
                if self.on_snapshot:
                    try:
                        self.on_snapshot(snapshot)
                    except Exception:
                        # Push outages must not make a successful Aspen sync fail.
                        logging.getLogger("store").exception("Background activity notifications failed")
            except AuthenticationRequired as error:
                with self.lock:
                    self.error = str(error)
                    self.needs_auth = True
            except AspenError as error:
                with self.lock:
                    self.error = str(error)
            except (KeyError, TypeError, ValueError, OSError, InvalidToken):
                with self.lock:
                    self.error = "Sync failed. Your last successful data remains available. Try refreshing again."
            finally:
                with self.lock:
                    self.syncing = False
            return True
        finally:
            self.sync_lock.release()

    def start_refresh(self, year=None, quarter=None, full=False):
        with self.lock:
            if self.syncing:
                return False
            self.syncing = True
        threading.Thread(target=self.refresh, args=(year, quarter, full), daemon=True).start()
        return True

    def reset_connection(self, *, clear=False, snapshot=None):
        """Disconnect, clear, and sample mode share the same connection reset."""
        self.forget_session()
        self.client = None
        self.needs_auth = snapshot is None
        self.error = None
        if clear or snapshot is not None:
            self.save(snapshot)

