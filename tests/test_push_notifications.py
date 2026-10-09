import base64
from contextlib import closing
import copy
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException
import requests

from app import Store, create_app
from push_notifications import PushNotifications, validate_subscription


def subscription(name="device", host="web.push.apple.com"):
    key = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    encode = lambda data: base64.urlsafe_b64encode(data).rstrip(b"=").decode()
    return {"endpoint": f"https://{host}/{name}", "keys": {"p256dh": encode(key), "auth": encode(b"a" * 16)}}


def snapshot(events=None):
    return {"mode": "live", "student": {"studentOid": "student"},
            "activityFeed": {"available": True, "events": events if events is not None else [
                {"type": "grade", "oid": "one", "grade": "8"}]}}


class PushTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(self.directory.name, {"TESTING": True, "START_REFRESH": False})
        self.accounts = self.app.extensions["accounts"]
        self.push = self.app.extensions["push_notifications"]
        self.client, self.store, self.headers, self.token = self.login("alice")
        self.store.save(snapshot())
        self.device = subscription()

    def tearDown(self):
        self.app.extensions["refresh_runtime"].stop()
        self.directory.cleanup()

    def login(self, subject):
        token = self.accounts.login({"sub": subject, "email": f"{subject}@example.com", "email_verified": True})
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["login"] = token
        store = self.app.extensions["account_store"](subject)
        return client, store, {"X-CSRF-Token": store.csrf}, token

    def subscribe(self, device=None):
        response = self.client.post("/api/push/subscribe", json=device or self.device, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.json)

    def row(self):
        with closing(self.accounts.connect()) as db:
            return db.execute("SELECT * FROM push_subscriptions WHERE endpoint=?", (self.device["endpoint"],)).fetchone()

    @patch("push_notifications.webpush")
    def test_background_refresh_sends_without_a_page_and_does_not_repeat(self, send):
        self.subscribe()
        self.store.client = Mock()
        self.store.client.session = requests.Session()
        changed = snapshot([{"type": "grade", "oid": "one", "grade": "9"},
                            {"type": "dailyAttendance", "oid": "two", "code": "T"}])
        self.store.client.sync.return_value = changed
        self.store.refresh()
        send.assert_called_once()
        payload = json.loads(send.call_args.kwargs["data"])
        self.assertEqual(payload, {"type": "betteraspen:activity", "count": 2, "badge": 2})
        self.assertNotIn("grade", send.call_args.kwargs["data"])
        self.assertIsNone(self.store.error)
        self.store.refresh()
        restarted = PushNotifications(self.accounts, "https://localhost")
        self.assertEqual(restarted.public_key, self.push.public_key)
        restarted.changed("alice", changed)
        send.assert_called_once()
        self.assertEqual(self.row()["unread"], 2)
        self.client.post("/api/push/read", json={"endpoint": self.device["endpoint"]}, headers=self.headers)
        self.assertEqual(self.row()["unread"], 0)

    @patch("push_notifications.webpush")
    def test_stale_demo_failed_and_first_load_are_quiet(self, send):
        self.store.save({**snapshot(), "activityFeed": {"available": False}})
        self.subscribe()
        self.push.changed("alice", snapshot())
        extra = snapshot([{"type": "grade", "oid": "new"}])
        for modified in ({**extra, "mode": "demo"}, {**extra, "student": {"studentOid": "other"}}):
            self.push.changed("alice", modified)
        stale = copy.deepcopy(extra)
        stale["activityFeed"]["stale"] = True
        self.push.changed("alice", stale)
        unavailable = copy.deepcopy(extra)
        unavailable["activityFeed"]["available"] = False
        self.push.changed("alice", unavailable)
        send.assert_not_called()
        self.push.changed("alice", extra)
        send.assert_called_once()

    @patch("push_notifications.webpush")
    def test_logout_expiry_disable_and_account_isolation(self, send):
        self.subscribe()
        bob, _, headers, _ = self.login("bob")
        bob.post("/api/push/unsubscribe", json={"endpoint": self.device["endpoint"]}, headers=headers)
        self.assertIsNotNone(self.row())
        self.push.changed("bob", snapshot([{"oid": "private"}]))
        send.assert_not_called()
        self.client.post("/auth/logout", headers=self.headers)
        self.assertIsNone(self.row())
        self.push.changed("alice", snapshot([{"oid": "new"}]))
        send.assert_not_called()
        self.client, self.store, self.headers, self.token = self.login("alice")
        self.subscribe()
        with closing(self.accounts.connect()) as db, db:
            db.execute("UPDATE sessions SET expires=1 WHERE subject='alice'")
        self.push.changed("alice", snapshot([{"oid": "newer"}]))
        self.assertIsNone(self.row())
        send.assert_not_called()

    @patch("push_notifications.webpush")
    def test_multiple_devices_badges_retry_and_expired_endpoint(self, send):
        self.subscribe()
        self.subscribe(subscription("desktop", "fcm.googleapis.com"))
        response = requests.Response()
        response.status_code = 503
        send.side_effect = WebPushException("Temporary error", response=response)
        new = snapshot([{"oid": "new"}])
        with self.assertLogs("push_notifications", level="WARNING"):
            self.push.changed("alice", new)
        self.assertEqual(self.row()["pending"], 1)
        send.reset_mock()
        send.side_effect = None
        self.push.changed("alice", new)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(self.row()["pending"], 0)
        response.status_code = 410
        send.side_effect = WebPushException("Gone", response=response)
        self.push.changed("alice", snapshot([{"oid": "new"}, {"oid": "next"}]))
        self.assertIsNone(self.row())

    def test_routes_require_login_csrf_live_account_and_valid_provider(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.post("/api/push/subscribe", json=self.device).status_code, 401)
        self.assertEqual(self.client.post("/api/push/subscribe", json=self.device).status_code, 403)
        for invalid in (None, {}, subscription(host="localhost"), subscription(host="web.push.apple.com.evil.test"),
                        {**self.device, "endpoint": "https://web.push.apple.com:444/test"},
                        {**self.device, "keys": {"auth": "bad", "p256dh": "bad"}}):
            self.assertEqual(self.client.post("/api/push/subscribe", json=invalid, headers=self.headers).status_code, 400)
        for host in ("web.push.apple.com", "fcm.googleapis.com", "updates.push.services.mozilla.com", "wns.notify.windows.com"):
            self.assertEqual(validate_subscription(subscription(host=host))["endpoint"], f"https://{host}/device")
        self.store.save({**snapshot(), "mode": "demo"})
        self.assertEqual(self.client.post("/api/push/subscribe", json=self.device, headers=self.headers).status_code, 400)

    def test_manifest_icons_and_public_key_are_served(self):
        manifest = self.client.get("/manifest.webmanifest")
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest.json["display"], "standalone")
        for icon in manifest.json["icons"]:
            with self.client.get(icon["src"]) as response:
                self.assertEqual(response.status_code, 200)
        manifest.close()
        self.assertEqual(self.client.get("/api/state").json["pushPublicKey"], self.push.public_key)
        self.assertEqual(len(base64.urlsafe_b64decode(self.push.public_key + "=" * (-len(self.push.public_key) % 4))), 65)

    def test_push_outage_does_not_break_saved_grades(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory, on_snapshot=Mock(side_effect=RuntimeError("push unavailable")))
            store.client = Mock()
            store.client.sync.return_value = snapshot()
            with self.assertLogs("app", level="ERROR"):
                store.refresh()
            self.assertIsNone(store.error)
            self.assertEqual(store.snapshot, snapshot())
