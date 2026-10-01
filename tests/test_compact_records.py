import copy
import json
import random
import unittest
from unittest.mock import Mock, patch

from ai_chat import ChatError, academic_context, ask
from aspen import demo_snapshot
from compact_records import FORMAT, FORMAT_GUIDE, compact_records, dumps, expand_records, identity


class CompactRecordTests(unittest.TestCase):
    def assert_lossless(self, records):
        original = copy.deepcopy(records)
        compact = compact_records(records)
        # Compare JSON identities too: Python equality equates False and 0.
        self.assertEqual(identity(expand_records(json.loads(dumps(compact)))), identity(records))
        self.assertEqual(identity(records), identity(original))
        return compact

    def test_all_demo_details_preserved_and_payload_substantially_smaller(self):
        context = academic_context(demo_snapshot(), True)
        compact = self.assert_lossless(context)
        self.assertEqual(compact["format"], FORMAT)
        self.assertLess(len(dumps(compact)) + len(FORMAT_GUIDE), len(dumps(context)) * .4)

    def test_differences_identical_names_and_duplicate_events_not_lost(self):
        snapshot = demo_snapshot()
        course = snapshot["gradePeriods"]["current:demo-q1"]["classes"][0]
        course["assignments"] = copy.deepcopy(course["assignments"])
        original = course["assignments"][0]
        original["description"] = "Full description.\nKeep <b>every</b> detail, punctuation, and whitespace. 🎒"
        original["scoreLightModels"] = [
            {"score": "0", "dropped": False, "exempt": True, "specialCode": "M", "comment": "Not submitted"},
            {"score": None, "missing": True, "late": True, "incomplete": False, "behavior": "Late"}]
        different = copy.deepcopy(original)
        different["scoreLightModels"][0]["score"] = "45.5"
        course["assignments"].append(different)
        snapshot["attendance"]["records"].append(copy.deepcopy(snapshot["attendance"]["records"][0]))
        snapshot["activityFeed"]["events"].append(copy.deepcopy(snapshot["activityFeed"]["events"][0]))
        snapshot["attendance"].update(partial=True, stale=True, error="Partial records", fetchedAt="2026-10-01T12:00:00Z")
        self.assert_lossless(academic_context(snapshot, True))

    def test_tables_preserve_absent_null_zero_false_empty_and_literal_markers(self):
        records = {"rows": [
            {"label": "Repeated label " * 8, "value": 0, "flag": False, "empty": ""},
            {"label": "Repeated label " * 8, "value": False, "flag": 0, "empty": None},
            {"label": "Repeated label " * 8, "value": None, "flag": True, "empty": []},
            {"label": "Repeated label " * 8, "value": 1, "flag": 1},
            {"label": "Repeated label " * 8, "value": 1.0},
            {"label": "Repeated label " * 8, "value": {"$ref": 5}, "flag": {"$absent": True}},
        ] * 30, "literal": {"$table": {"columns": ["do not interpret this"]}}}
        packed = self.assert_lossless(records)
        self.assertEqual(packed["format"], FORMAT)

    def test_random_mixed_schema_tables_roundtrip(self):
        rng = random.Random(61)
        values = [None, False, True, 0, 1, 1.0, "", [], {}, {"$ref": 0}, "Description " * 50]
        for _ in range(30):
            rows = [{key: copy.deepcopy(rng.choice(values)) for key in ("name", "score", "flag", "details")
                     if rng.randrange(4)} for _ in range(50)]
            self.assert_lossless({"rows": rows, "copy": copy.deepcopy(rows)})

    def test_small_payload_does_not_pay_encoding_overhead(self):
        records = {"gradePeriods": {}, "attendance": {"available": False}}
        self.assertEqual(compact_records(records), records)

    def test_sync_timestamp_changes_only_suffix(self):
        context = academic_context(demo_snapshot(), False)
        first = dumps(compact_records(context))
        context["syncedAt"] = "different retrieval time"
        second = dumps(compact_records(context))
        self.assertEqual(first.split('"syncedAt":')[0], second.split('"syncedAt":')[0])

    @patch("ai_chat.requests.post")
    def test_limit_applies_after_lossless_compaction(self, upstream):
        snapshot = demo_snapshot()
        for period in snapshot["gradePeriods"].values():
            for course in period["classes"]:
                for assignment in course["assignments"]:
                    assignment["description"] = "Full assignment instructions and feedback. " * 150
        context = academic_context(snapshot, False)
        self.assertGreater(len(dumps(context)), 300000)
        upstream.return_value = Mock(status_code=200, json=Mock(return_value={"output": [
            {"type": "message", "content": [{"type": "output_text", "text": "Reply"}]}]}))
        self.assertEqual(ask("https://ai.example/responses", "test-key", [{"role": "user", "content": "Question"}], context), "Reply")
        request = upstream.call_args.kwargs["json"]
        sent = request["input"][0]["content"].split("\n", 1)[1]
        self.assertLess(len(sent), 300000)
        self.assertEqual(identity(expand_records(json.loads(sent))), identity(context))
        self.assertIn(FORMAT_GUIDE, request["instructions"])
        self.assertFalse(request["store"])

    @patch("ai_chat.requests.post")
    def test_unique_details_over_limit_rejected_without_truncation(self, upstream):
        records = {"description": "x" * 300001}
        with self.assertRaises(ChatError):
            ask("https://ai.example/responses", "test-key", [], records)
        upstream.assert_not_called()


if __name__ == "__main__":
    unittest.main()
