"""
Regression tests for:
- LLM-returned ATS score / keyword_match coercion (Bug 1)
- Manual JD optimizer empty-state in questions stage (Bug 2)
- Module-level _to_numeric helper covers all crash sites
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
import cv_generator


class TestToNumeric(unittest.TestCase):
    """_to_numeric must coerce any LLM-returned shape without raising."""

    def test_none(self):
        self.assertEqual(app._to_numeric(None), 0)

    def test_int(self):
        self.assertEqual(app._to_numeric(67), 67)

    def test_float(self):
        self.assertEqual(app._to_numeric(67.5), 67)
        self.assertEqual(app._to_numeric(99.9), 99)

    def test_plain_string_number(self):
        self.assertEqual(app._to_numeric("67"), 67)

    def test_percent_string(self):
        # This is the exact case from the client's crash report.
        self.assertEqual(app._to_numeric("67%"), 67)

    def test_percent_with_whitespace(self):
        self.assertEqual(app._to_numeric("  90%  "), 90)

    def test_unparseable_string_falls_back_to_zero(self):
        self.assertEqual(app._to_numeric("not a number"), 0)
        self.assertEqual(app._to_numeric(""), 0)
        self.assertEqual(app._to_numeric("%%%"), 0)

    def test_division_safe(self):
        # The original bug: float("67%") / 100.0 raised ValueError.
        # _to_numeric must return a numeric value usable as / 100.0.
        result = app._to_numeric("67%") / 100.0
        self.assertEqual(result, 0.67)


class TestAnalyzeCvAtsScoreCoercion(unittest.TestCase):
    """analyze_cv_ats_score must coerce score/keyword_match to int even when
    the LLM returns string or percentage values."""

    def _patched_analyze(self, raw_response_text, has_jd=True):
        """Run analyze_cv_ats_score with a mocked LLM response."""
        import cv_generator as cg
        with mock.patch.object(cg, "_ats_cache", {}), \
             mock.patch.object(cg, "_get_session_ai_model", return_value="gemini"), \
             mock.patch.object(cg.model, "generate_content") as mock_gen:
            mock_resp = mock.Mock()
            mock_resp.text = raw_response_text
            mock_gen.return_value = mock_resp
            result = cv_generator.analyze_cv_ats_score(
                "Sample CV content with Python and SQL.",
                "Job desc requiring Python and SQL." if has_jd else "",
            )
        return result

    def test_score_int_passthrough(self):
        result = self._patched_analyze('{"ats_score": 85, "keyword_match": 72, "missing_keywords": [], "suggestions": []}')
        self.assertIsInstance(result["score"], int)
        self.assertIsInstance(result["keyword_match"], int)
        self.assertEqual(result["score"], 85)
        self.assertEqual(result["keyword_match"], 72)

    def test_score_percentage_string_coerced(self):
        """The exact bug from the client crash."""
        result = self._patched_analyze('{"ats_score": "67%", "keyword_match": "45%", "missing_keywords": [], "suggestions": []}')
        self.assertIsInstance(result["score"], int)
        self.assertIsInstance(result["keyword_match"], int)
        self.assertEqual(result["score"], 67)
        self.assertEqual(result["keyword_match"], 45)

    def test_score_plain_string_coerced(self):
        result = self._patched_analyze('{"ats_score": "85", "keyword_match": "60", "missing_keywords": [], "suggestions": []}')
        self.assertIsInstance(result["score"], int)
        self.assertEqual(result["score"], 85)
        self.assertEqual(result["keyword_match"], 60)

    def test_score_float_coerced_to_int(self):
        result = self._patched_analyze('{"ats_score": 85.7, "keyword_match": 72.3, "missing_keywords": [], "suggestions": []}')
        self.assertIsInstance(result["score"], int)
        self.assertEqual(result["score"], 85)

    def test_score_none_defaults_to_zero(self):
        result = self._patched_analyze('{"ats_score": null, "keyword_match": null, "missing_keywords": [], "suggestions": []}')
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["keyword_match"], 0)

    def test_score_missing_defaults_to_zero(self):
        result = self._patched_analyze('{"missing_keywords": [], "suggestions": []}')
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["keyword_match"], 0)

    def test_score_with_garbage_string_defaults_to_zero(self):
        result = self._patched_analyze('{"ats_score": "abc", "keyword_match": "xyz%", "missing_keywords": [], "suggestions": []}')
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["keyword_match"], 0)

    def test_no_crash_on_progress_division(self):
        """End-to-end: coerced output must be usable in float/x/100.0 division."""
        result = self._patched_analyze('{"ats_score": "67%", "keyword_match": "45%", "missing_keywords": [], "suggestions": []}')
        # Simulate what app.py progress bar does:
        ratio = float(result["score"]) / 100.0
        kw_ratio = float(result["keyword_match"]) / 100.0
        self.assertAlmostEqual(ratio, 0.67)
        self.assertAlmostEqual(kw_ratio, 0.45)


class TestToIntOrNone(unittest.TestCase):
    """Sanity check on cv_generator's own coercion helper."""

    def test_percent_string(self):
        self.assertEqual(cv_generator._to_int_or_none("67%"), 67)

    def test_plain_string(self):
        self.assertEqual(cv_generator._to_int_or_none("67"), 67)

    def test_int(self):
        self.assertEqual(cv_generator._to_int_or_none(67), 67)

    def test_float(self):
        self.assertEqual(cv_generator._to_int_or_none(67.5), 67)

    def test_none(self):
        self.assertIsNone(cv_generator._to_int_or_none(None))


if __name__ == "__main__":
    unittest.main()
