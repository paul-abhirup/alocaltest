"""
Hermetic unit tests for Phase 2 CV↔JD alignment engine (no network, no DB).
Run:  .venv/bin/python -m unittest discover -s tests -v
Covers docs/CV_JD_ALIGNMENT_PLAN.md §10 (the app-independent rows).
"""
import os
import re
import sys
import json
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cv_generator import (
    parse_gap_analysis,
    build_verified_context_block,
    hash_jd,
    TRUTHFULNESS_GUARDRAIL,
)

CV_GENERATOR_SRC = open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cv_generator.py")
).read()


class TestParseGapAnalysis(unittest.TestCase):
    def _one_gap(self):
        return {
            "sufficient": False,
            "overall_match": 62,
            "gaps": [{"id": "k8s", "area": "Kubernetes", "why": "JD needs it",
                      "question": "Have you used K8s?", "example": "e.g. ran EKS"}],
        }

    def test_clean_json(self):
        out = parse_gap_analysis(json.dumps(self._one_gap()))
        self.assertFalse(out["sufficient"])
        self.assertEqual(out["overall_match"], 62)
        self.assertEqual(len(out["gaps"]), 1)
        self.assertEqual(out["gaps"][0]["id"], "k8s")

    def test_code_fenced_json(self):
        fenced = "```json\n" + json.dumps(self._one_gap()) + "\n```"
        self.assertEqual(len(parse_gap_analysis(fenced)["gaps"]), 1)

    def test_json_embedded_in_prose(self):
        prose = "Sure! Here is the analysis:\n" + json.dumps(self._one_gap()) + "\nHope it helps."
        self.assertEqual(len(parse_gap_analysis(prose)["gaps"]), 1)

    def test_malformed_json_fails_open(self):
        out = parse_gap_analysis("{ this is not json ")
        self.assertTrue(out["sufficient"])        # fail-open → never blocks generation
        self.assertEqual(out["gaps"], [])

    def test_empty_input_fails_open(self):
        self.assertTrue(parse_gap_analysis("")["sufficient"])
        self.assertTrue(parse_gap_analysis(None)["sufficient"])

    def test_truncates_to_max_gaps(self):
        many = {"sufficient": False, "gaps": [
            {"id": f"g{i}", "area": f"a{i}", "question": f"q{i}?", "example": "e"}
            for i in range(10)
        ]}
        self.assertEqual(len(parse_gap_analysis(json.dumps(many), max_gaps=5)["gaps"]), 5)

    def test_gap_without_question_dropped(self):
        data = {"sufficient": False, "gaps": [
            {"id": "a", "area": "A", "question": "real?", "example": "e"},
            {"id": "b", "area": "B", "question": "", "example": "e"},   # dropped
        ]}
        gaps = parse_gap_analysis(json.dumps(data))["gaps"]
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["id"], "a")

    def test_no_gaps_forces_sufficient(self):
        data = {"sufficient": False, "gaps": []}   # contradiction → resolve to sufficient
        self.assertTrue(parse_gap_analysis(json.dumps(data))["sufficient"])

    def test_bad_overall_match_becomes_none(self):
        data = {"sufficient": True, "overall_match": "N/A", "gaps": []}
        self.assertIsNone(parse_gap_analysis(json.dumps(data))["overall_match"])


class TestVerifiedContextBlock(unittest.TestCase):
    def test_dict_with_answers(self):
        block = build_verified_context_block({"Kubernetes": "Ran 12 EKS services", "Terraform": ""})
        self.assertIn("VERIFIED EXPERIENCE", block)
        self.assertIn("Kubernetes: Ran 12 EKS services", block)
        self.assertNotIn("Terraform", block)          # empty answer omitted

    def test_dict_all_empty_returns_empty(self):
        self.assertEqual(build_verified_context_block({"a": "", "b": "   "}), "")

    def test_string_passthrough(self):
        self.assertIn("my real experience", build_verified_context_block("my real experience"))

    def test_empty_inputs(self):
        self.assertEqual(build_verified_context_block(""), "")
        self.assertEqual(build_verified_context_block(None), "")
        self.assertEqual(build_verified_context_block({}), "")


class TestHashJd(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(hash_jd("Python Developer"), hash_jd("Python Developer"))

    def test_whitespace_and_case_insensitive(self):
        self.assertEqual(hash_jd("  Python   Developer "), hash_jd("python developer"))

    def test_different_jds_differ(self):
        self.assertNotEqual(hash_jd("Python Developer"), hash_jd("Java Developer"))


class TestGuardrail(unittest.TestCase):
    def test_guardrail_has_key_phrases(self):
        g = TRUTHFULNESS_GUARDRAIL.lower()
        # New guardrail uses "must not" instead of "do not"
        self.assertTrue("must not" in g or "do not" in g)
        self.assertTrue("invent" in g or "fabricate" in g)
        # New guardrail emphasizes surfacing skills rather than omitting
        self.assertTrue("surface" in g or "omit" in g)


class TestFabricationRemoved(unittest.TestCase):
    """Regression guard: the source must never re-introduce fabrication instructions."""
    FORBIDDEN = [
        "Fabricate work experience to better align",
        "fabricated from JD",
        "4 original + 2 fabricated",
        "4 original + 3 fabricated",
        "Total Roles between 26-30",
        "Create 26 roles across all companies",
    ]

    def test_no_fabrication_instructions_in_source(self):
        for phrase in self.FORBIDDEN:
            self.assertNotIn(phrase, CV_GENERATOR_SRC, f"fabrication instruction resurfaced: {phrase!r}")

    def test_guardrail_referenced_by_all_three_generators(self):
        # guardrail constant must be injected into each generator's prompt
        self.assertGreaterEqual(CV_GENERATOR_SRC.count("{TRUTHFULNESS_GUARDRAIL}"), 3)
        self.assertGreaterEqual(CV_GENERATOR_SRC.count("build_verified_context_block(extra_context)"), 3)


class TestApiServerWiring(unittest.TestCase):
    def test_build_generate_args_maps_model_choice_and_extra_context(self):
        from api_server import _build_generate_args, GenerateCVRequest
        req = GenerateCVRequest(
            job_description="Python Dev",
            model="premium_classic",
            extras={"extra_context": {"React": "5 years experience"}}
        )
        args = _build_generate_args(req, resume_text="My Resume")
        self.assertEqual(args.get("model_choice"), "premium_classic")
        self.assertEqual(args.get("extra_context"), {"React": "5 years experience"})


class TestSafeSessionAccess(unittest.TestCase):
    def test_get_session_ai_model_outside_streamlit(self):
        from cv_generator import _get_session_ai_model
        self.assertIsNone(_get_session_ai_model())


if __name__ == "__main__":
    unittest.main()
