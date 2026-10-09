"""Persistent Web Push subscriptions and activity baselines for background refresh."""
import base64
from contextlib import closing
import hashlib
import json
import logging
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush
import requests

from accounts import private_file

logger = logging.getLogger(__name__)
EVENT_FIELDS = ("type", "oid", "date", "studentScheduleOid", "assignmentOid", "termOid",
                "className", "assignmentName", "grade", "code", "period", "absent",
                "tardy", "dismissed", "excused")


def activity(snapshot):
    if not snapshot or snapshot.get("mode") != "live":
        return None
    student = snapshot.get("student", {}).get("studentOid")
    feed = snapshot.get("activityFeed", {})
    if not student or not feed.get("available") or feed.get("stale") or not isinstance(feed.get("events"), list):
        return None
    events = {hashlib.sha256(json.dumps([event.get(key) for key in EVENT_FIELDS],
                                      ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
              for event in feed["events"] if isinstance(event, dict)}
    return student, events


def validate_subscription(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid push subscription.")
    endpoint = value.get("endpoint", "")
    if not isinstance(endpoint, str) or len(endpoint) > 4096:
        raise ValueError("Invalid push endpoint.")
    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname or ""
        trusted = host in {"fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com"} or (
            host.endswith(".push.apple.com") or host.endswith(".notify.windows.com"))
        if (parsed.scheme != "https" or not trusted or parsed.port not in (None, 443)
                or parsed.username or parsed.password or parsed.fragment):
            raise ValueError("Unsupported push service.")
        keys = value.get("keys", {})
        decoded = {}
        for name in ("p256dh", "auth"):
            key = keys.get(name)
            if not isinstance(key, str) or len(key) > 128:
                raise ValueError("Invalid push keys.")
            decoded[name] = base64.b64decode(key + "=" * (-len(key) % 4), altchars=b"-_", validate=True)
        if len(decoded["auth"]) != 16:
            raise ValueError("Invalid push keys.")
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decoded["p256dh"])
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Invalid or unsupported push subscription.") from error
    return {"endpoint": endpoint, "keys": {name: keys[name] for name in ("p256dh", "auth")}}


class PushNotifications:
    def __init__(self, accounts, contact):
        self.accounts = accounts
        self.contact = contact
        self.key_path = accounts.directory / "push-vapid.pem"
        generated = ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        key = serialization.load_pem_private_key(private_file(self.key_path, generated), password=None)
        public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.public_key = base64.urlsafe_b64encode(public).rstrip(b"=").decode()
        with closing(accounts.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS push_subscriptions (
                    endpoint TEXT PRIMARY KEY, subject TEXT NOT NULL, student TEXT NOT NULL,
                    login_digest TEXT NOT NULL, subscription TEXT NOT NULL,
                    unread INTEGER NOT NULL DEFAULT 0, pending INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS push_baselines (
                    subject TEXT NOT NULL, student TEXT NOT NULL, PRIMARY KEY(subject, student)
                );
                CREATE TABLE IF NOT EXISTS push_seen (
                    subject TEXT NOT NULL, student TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    PRIMARY KEY(subject, student, fingerprint)
                );
            """)

    @staticmethod
    def _baseline(db, subject, current):
        student, events = current
        initialized = db.execute("SELECT 1 FROM push_baselines WHERE subject=? AND student=?",
                                 (subject, student)).fetchone()
        added = 0
        for fingerprint in events:
            added += db.execute("INSERT OR IGNORE INTO push_seen VALUES(?,?,?)",
                                (subject, student, fingerprint)).rowcount
        db.execute("INSERT OR IGNORE INTO push_baselines VALUES(?,?)", (subject, student))
        return added if initialized else 0

    def subscribe(self, subject, login, snapshot, value):
        subscription = validate_subscription(value)
        student = (snapshot or {}).get("student", {}).get("studentOid")
        if not student or snapshot.get("mode") != "live":
            raise ValueError("Connect your Aspen account before enabling notifications.")
        with closing(self.accounts.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT subject,student FROM push_subscriptions WHERE endpoint=?",
                                  (subscription["endpoint"],)).fetchone()
            total = db.execute("SELECT COUNT(*) FROM push_subscriptions WHERE subject=?", (subject,)).fetchone()[0]
            if total >= 20 and not existing:
                raise ValueError("Too many notification devices. Disable notifications on an unused device first.")
            current = activity(snapshot)
            if current and not db.execute("SELECT 1 FROM push_baselines WHERE subject=? AND student=?",
                                          (subject, student)).fetchone():
                self._baseline(db, subject, current)
            if existing and (existing["subject"], existing["student"]) != (subject, student):
                db.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (subscription["endpoint"],))
            db.execute("INSERT INTO push_subscriptions(endpoint,subject,student,login_digest,subscription) "
                       "VALUES(?,?,?,?,?) ON CONFLICT(endpoint) DO UPDATE SET login_digest=excluded.login_digest, "
                       "subscription=excluded.subscription",
                       (subscription["endpoint"], subject, student, self.accounts.digest(login), json.dumps(subscription)))

    def remove(self, subject, endpoint):
        with closing(self.accounts.connect()) as db, db:
            db.execute("DELETE FROM push_subscriptions WHERE endpoint=? AND subject=?", (endpoint, subject))

    def read(self, subject, endpoint):
        with closing(self.accounts.connect()) as db, db:
            db.execute("UPDATE push_subscriptions SET unread=0,pending=0 WHERE endpoint=? AND subject=?", (endpoint, subject))

    def revoke_login(self, login):
        if login:
            with closing(self.accounts.connect()) as db, db:
                db.execute("DELETE FROM push_subscriptions WHERE login_digest=?", (self.accounts.digest(login),))

    def changed(self, subject, snapshot):
        current = activity(snapshot)
        if not current:
            return
        student, _ = current
        with closing(self.accounts.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM push_subscriptions WHERE login_digest NOT IN "
                       "(SELECT digest FROM sessions WHERE expires>?)", (int(time.time()),))
            added = self._baseline(db, subject, current)
            if added:
                db.execute("UPDATE push_subscriptions SET unread=unread+?,pending=pending+? WHERE subject=? AND student=?",
                           (added, added, subject, student))
            queued = db.execute("SELECT * FROM push_subscriptions WHERE subject=? AND student=? AND pending>0",
                                (subject, student)).fetchall()
        for row in queued:
            # A device may be disabled or its login revoked while another push is being sent.
            with closing(self.accounts.connect()) as db:
                active = db.execute("SELECT p.pending,p.unread FROM push_subscriptions p JOIN sessions s "
                                    "ON p.login_digest=s.digest AND p.subject=s.subject "
                                    "WHERE p.endpoint=? AND p.subject=? AND p.login_digest=? AND s.expires>?",
                                    (row["endpoint"], subject, row["login_digest"], int(time.time()))).fetchone()
            if not active or not active["pending"]:
                continue
            count = active["pending"]
            payload = {"type": "betteraspen:activity", "count": count, "badge": active["unread"]}
            try:
                webpush(subscription_info=json.loads(row["subscription"]), data=json.dumps(payload),
                        vapid_private_key=str(self.key_path), vapid_claims={"sub": self.contact},
                        ttl=3600, timeout=10)
            except WebPushException as error:
                if error.response is not None and error.response.status_code in (404, 410):
                    self.remove(subject, row["endpoint"])
                else:
                    logger.warning("Activity push failed; will retry on the next refresh")
            except (requests.RequestException, ValueError, OSError):
                logger.warning("Activity push failed; will retry on the next refresh")
            else:
                with closing(self.accounts.connect()) as db, db:
                    db.execute("UPDATE push_subscriptions SET pending=MAX(0,pending-?) "
                               "WHERE endpoint=? AND subject=? AND login_digest=?",
                               (count, row["endpoint"], subject, row["login_digest"]))
