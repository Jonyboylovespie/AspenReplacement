import copy
import json
from pathlib import Path
import tempfile
import unittest

from app import Store
from aspen import demo_snapshot
from helpers import create_test_app
from snapshots import FORMAT, expand_snapshot, pack_snapshot


class SnapshotTests(unittest.TestCase):
    def test_incomplete_or_reserved_records_keep_legacy_representation(self):
        for course in ({"assignments": None}, {"assignmentsRef": "original"},
                       {"termsRef": "original"}, {"summaryRef": "original"}):
            snapshot = {"classes": [course], "gradePeriods": {"current:current": {"classes": [course]}}}
            self.assertEqual(expand_snapshot(pack_snapshot(snapshot)), snapshot)

    def test_pooling_preserves_period_differences_and_shares_identical_records(self):
        snapshot = demo_snapshot()
        period = snapshot["gradePeriods"]["current:demo-q1"]
        period["classes"] = copy.deepcopy(period["classes"])
        period["classes"][0]["assignments"][0]["scoreLightModels"] = [{"score": "0", "exempt": False, "comment": None}]
        original = json.dumps(snapshot, sort_keys=True)
        packed = pack_snapshot(snapshot)
        restored = expand_snapshot(json.loads(json.dumps(packed)))
        self.assertEqual(json.dumps(restored, sort_keys=True), original)
        self.assertEqual(json.dumps(snapshot, sort_keys=True), original)
        self.assertEqual(packed["snapshotFormat"], FORMAT)
        self.assertLess(len(json.dumps(packed)), len(original) * .6)
        self.assertIs(restored["classes"][1], restored["gradePeriods"]["current:current"]["classes"][1])
        self.assertIs(restored["classes"][1]["assignments"][0], restored["gradePeriods"]["current:all"]["classes"][1]["assignments"][0])
        self.assertIsNot(restored["classes"][0]["assignments"][0], restored["gradePeriods"]["current:demo-q1"]["classes"][0]["assignments"][0])

    def test_legacy_caches_upgrade_and_packed_caches_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = demo_snapshot()
            path = Path(directory) / "snapshot.json"
            path.write_text(json.dumps(snapshot))
            store = Store(directory)
            self.assertEqual(store.snapshot, snapshot)
            revision = store.snapshot_revision
            store.save(snapshot)
            self.assertNotEqual(store.snapshot_revision, revision)
            self.assertEqual(json.loads(path.read_text())["snapshotFormat"], FORMAT)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            restarted = Store(directory)
            self.assertEqual(restarted.snapshot, snapshot)
            self.assertNotEqual(restarted.snapshot_revision, store.snapshot_revision)
            store.save(None)
            self.assertIsNone(Store(directory).snapshot)

    def test_status_only_polls_and_compact_downloads_are_account_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_test_app(directory)
            store = app.extensions["aspen_store"]
            store.save(demo_snapshot())
            client = app.test_client()
            first = client.get("/api/state?compact=1")
            self.assertEqual(expand_snapshot(first.json["snapshot"]), store.snapshot)
            revision = first.json["snapshotRevision"]
            status = client.get("/api/state", query_string={"compact": "1", "revision": revision})
            self.assertNotIn("snapshot", status.json)
            self.assertLess(len(status.data), 1000)
            store.syncing = True
            self.assertTrue(client.get("/api/state", query_string={"revision": revision}).json["syncing"])
            store.save(None)
            self.assertIsNone(client.get("/api/state", query_string={"revision": revision}).json["snapshot"])
            # A revision from another account must never suppress its first download.
            token = app.extensions["accounts"].login({"sub": "other", "email": "other@example.com", "email_verified": True})
            with client.session_transaction() as session:
                session["login"] = token
            self.assertIn("snapshot", client.get("/api/state", query_string={"revision": revision}).json)
            with client.session_transaction() as session:
                session.clear()
            anonymous = client.get("/api/state", query_string={"revision": revision}).json
            self.assertIsNone(anonymous["snapshot"])
            self.assertNotIn("snapshotRevision", anonymous)

    def test_static_assets_revalidate_and_versioned_assets_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_test_app(directory)
            client = app.test_client()
            for path in ("/styles.css", "/formatting.js", "/rendering.js", "/fonts/cantarell.otf", "/notification-worker.js"):
                with client.get(path) as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["Cache-Control"], "no-cache")
                    etag = response.headers["ETag"]
                with client.get(path, headers={"If-None-Match": etag}) as response:
                    self.assertEqual(response.status_code, 304)
            with client.get("/styles.css?v=fingerprint") as response:
                self.assertIn("immutable", response.headers["Cache-Control"])
            response = client.get("/")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertRegex(response.text, r'/app\.js\?v=[0-9a-f]{12}')
            self.assertNotIn("fonts/site-icons", response.text)
