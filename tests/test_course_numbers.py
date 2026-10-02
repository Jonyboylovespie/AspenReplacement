import json
import unittest

from ai_chat import academic_context
from aspen import demo_snapshot


class CourseNumberTests(unittest.TestCase):
    def test_ai_context_excludes_uncollected_course_numbers(self):
        snapshot = demo_snapshot()
        for period in snapshot["gradePeriods"].values():
            for course in period["classes"]:
                course["courseNumber"] = "PRIVATE-101"
        context = json.dumps(academic_context(snapshot, False))
        self.assertNotIn("courseNumber", context)
        self.assertNotIn("PRIVATE-", context)
        self.assertIn("Algebra II", context)
        self.assertIn("English Literature", context)

    def test_sample_data_has_no_course_numbers(self):
        self.assertNotIn("courseNumber", json.dumps(demo_snapshot()))
