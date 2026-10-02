import json
from pathlib import Path
import tempfile
import unittest

from ai_chat import academic_context
from app import Store
from aspen import demo_snapshot


class CourseNumberTests(unittest.TestCase):
    def legacy_snapshot(self):
        return {
            "classes": [{"courseName": "Math", "courseNumber": "PRIVATE-101", "displayGrade": "93"}],
            "gradePeriods": {"previous:all": {
                "classes": [{"courseName": "English", "courseNumber": "PRIVATE-202", "displayGrade": "88"}]
            }},
        }

    def test_saved_course_numbers_are_removed_on_load_and_from_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text(json.dumps(self.legacy_snapshot()))
            store = Store(directory)
            self.assertNotIn("courseNumber", json.dumps(store.view()))
            self.assertNotIn("PRIVATE-", path.read_text())
            self.assertEqual(store.snapshot["classes"][0]["courseName"], "Math")
            self.assertEqual(store.snapshot["gradePeriods"]["previous:all"]["classes"][0]["displayGrade"], "88")

    def test_save_never_persists_course_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.save(self.legacy_snapshot())
            self.assertNotIn("courseNumber", json.dumps(store.snapshot))
            self.assertNotIn("PRIVATE-", store.path.read_text())

    def test_ai_context_excludes_legacy_course_numbers(self):
        context = json.dumps(academic_context(self.legacy_snapshot(), False))
        self.assertNotIn("courseNumber", context)
        self.assertNotIn("PRIVATE-", context)
        self.assertIn("Math", context)
        self.assertIn("English", context)

    def test_sample_data_has_no_course_numbers(self):
        self.assertNotIn("courseNumber", json.dumps(demo_snapshot()))
