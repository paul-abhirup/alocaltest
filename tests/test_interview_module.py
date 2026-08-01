"""
Hermetic unit tests for the AI Interview Practice module (demo mode, no network, no DB).
Run:  .venv/bin/python -m unittest discover -s tests -v
Covers question-bank generation, flattening, evaluation, feedback report, and exports.
"""

import os
import sys
import unittest
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import interview_module as im
import cv_generator
from tts_utils import tts_component_html

RESUME = (
    "Software Engineer with 4 years in Python, FastAPI, React, AWS, SQL. "
    "Built a real-time analytics platform. Led a team of 3. "
    "Improved API latency by 40%."
)

JD = (
    "Senior Python Developer. Requirements: Python, FastAPI, AWS, Docker, "
    "Kubernetes, microservices, CI/CD, PostgreSQL, Redis, machine learning, "
    "data analysis, project management, communication, leadership."
)

REQUIRED_QUESTION_KEYS = {"question", "ideal_answer", "key_points", "section", "difficulty"}
REQUIRED_EVAL_KEYS = {
    "score", "meaning_match", "keyword_coverage", "keywords_covered",
    "keywords_missed", "structure_score", "completeness_score", "clarity_score",
    "relevance_score", "depth_score", "confidence_indicators", "strengths",
    "improvements", "improved_answer", "brief_feedback",
}
REQUIRED_REPORT_KEYS = {
    "overall_score", "performance_band", "general_score", "technical_score",
    "resume_score", "total_questions", "well_answered", "keywords_covered",
    "keywords_missed", "jd_keywords", "resume_covered_kw", "resume_missing_kw",
    "session_results", "overall_summary", "key_strengths", "weak_areas",
    "general_feedback", "technical_feedback", "resume_feedback",
    "recommendations", "next_steps",
}


class TestInterviewModuleDemo(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._saved_env = {}
        for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "DEEPGRAM_API_KEY"):
            cls._saved_env[key] = os.environ.pop(key, None)
        im._DEMO_MODE = True
        cls._fake_session = {"ai_model": "gemini", "interview_type": "behavioral", "interview_difficulty": "medium"}
        im.st_session = cls._fake_session
        import streamlit as st
        cls._orig_session_state = st.session_state
        st.session_state = cls._fake_session

    @classmethod
    def tearDownClass(cls):
        import streamlit as st
        st.session_state = cls._orig_session_state
        for key, value in cls._saved_env.items():
            if value is not None:
                os.environ[key] = value

    def test_demo_mode_active(self):
        self.assertTrue(im._check_demo_mode())

    def test_generate_behavioral_qa(self):
        qa = im.generate_structured_interview_qa(RESUME, JD, "30 minutes", "behavioral", "medium")
        self.assertIsInstance(qa, dict)
        self.assertIn("behavioral", qa)
        self.assertIn("resume", qa)
        self.assertIsInstance(qa["behavioral"], list)
        self.assertIsInstance(qa["resume"], list)
        self.assertEqual(len(im.flatten_questions(qa)), 15)

    def test_generate_technical_qa(self):
        qa = im.generate_structured_interview_qa(RESUME, JD, "30 minutes", "technical", "hard")
        flat = im.flatten_questions(qa)
        self.assertEqual(len(flat), 15)
        self.assertTrue(any(q["section"] == "technical" for q in flat))
        self.assertTrue(any(q["section"] == "resume" for q in flat))

    def test_demo_generates_advertised_count_all_configs(self):
        for interview_type in ("behavioral", "technical"):
            for difficulty in ("easy", "medium", "hard"):
                qa = im.generate_structured_interview_qa(RESUME, JD, "30 minutes", interview_type, difficulty)
                flat = im.flatten_questions(qa)
                self.assertEqual(len(flat), 15, f"{interview_type}/{difficulty}: {len(flat)}")
                self.assertTrue(any(q["section"] == interview_type for q in flat), interview_type)
                self.assertTrue(any(q["section"] == "resume" for q in flat), interview_type)

    def test_flatten_questions_shape(self):
        qa = im.generate_structured_interview_qa(RESUME, JD, "15 minutes", "behavioral", "easy")
        flat = im.flatten_questions(qa)
        for q in flat:
            self.assertTrue(REQUIRED_QUESTION_KEYS <= set(q), q)
            self.assertTrue(q["question"])
            self.assertTrue(q["ideal_answer"])
            self.assertTrue(q["key_points"])

    def test_qa_bank_to_text(self):
        qa = im.generate_structured_interview_qa(RESUME, JD, "30 minutes", "behavioral", "medium")
        text = im._qa_bank_to_text(qa)
        self.assertIsInstance(text, str)
        self.assertIn("===", text)
        self.assertIn("1.", text)

    def test_evaluate_answer_full(self):
        ev = im.evaluate_answer(
            "Tell me about a time you led a team under tight deadlines.",
            "Describe a situation, task, action, and result.",
            ["Leadership", "Team management", "Deadlines"],
            "In my last role I led a team of three to ship a platform two weeks early. "
            "I planned sprints, delegated tasks, and resolved conflicts. "
            "The result was a 40% latency improvement.",
            "behavioral", "medium",
        )
        self.assertTrue(REQUIRED_EVAL_KEYS <= set(ev), ev)
        self.assertGreaterEqual(ev["score"], 0)
        self.assertLessEqual(ev["score"], 100)
        for k in ("meaning_match", "keyword_coverage", "structure_score",
                  "completeness_score", "clarity_score", "relevance_score", "depth_score"):
            self.assertGreaterEqual(ev[k], 0, k)
            self.assertLessEqual(ev[k], 100, k)

    def test_evaluate_answer_empty(self):
        ev = im.evaluate_answer("Any question?", "Ideal", ["A", "B"], "", "general", "medium")
        self.assertEqual(ev["score"], 0)
        self.assertEqual(ev["keywords_missed"], ["A", "B"])

    def test_generate_feedback_report(self):
        qa = im.generate_structured_interview_qa(RESUME, JD, "30 minutes", "behavioral", "medium")
        flat = im.flatten_questions(qa)
        ev = im.evaluate_answer(
            flat[0]["question"], flat[0]["ideal_answer"], flat[0]["key_points"],
            "I led a team, used the STAR format, and delivered a 40% improvement.",
            "behavioral", "medium",
        )
        results = [{"question_obj": q, "user_answer": "STAR example", "evaluation": ev} for q in flat]
        report = im.generate_feedback_report(results, "30 minutes", RESUME, JD)
        self.assertTrue(REQUIRED_REPORT_KEYS <= set(report), report)
        self.assertEqual(report["total_questions"], len(results))
        self.assertIn(report["performance_band"], ("Excellent", "Good", "Average", "Needs Improvement"))
        self.assertIsInstance(report["jd_keywords"], list)
        self.assertIsInstance(report["recommendations"], list)

    def test_export_feedback_report_pdf_and_docx(self):
        qa = im.generate_structured_interview_qa(RESUME, JD, "15 minutes", "behavioral", "medium")
        flat = im.flatten_questions(qa)
        ev = im.evaluate_answer(flat[0]["question"], flat[0]["ideal_answer"], flat[0]["key_points"],
                                "Detailed answer with STAR structure and metrics.", "behavioral", "medium")
        results = [{"question_obj": q, "user_answer": "answer", "evaluation": ev} for q in flat]
        report = im.generate_feedback_report(results, "15 minutes", RESUME, JD)
        pdf_buf, docx_buf = im.export_feedback_report(report)
        self.assertIsInstance(pdf_buf, BytesIO)
        self.assertTrue(pdf_buf.getvalue().startswith(b"%PDF"))
        self.assertTrue(docx_buf.getvalue().startswith(b"PK"))

    def test_export_interview_qa_pdf_and_docx(self):
        pdf_buf, docx_buf = cv_generator.export_interview_qa("1. Question\nAnswer: text")
        self.assertTrue(pdf_buf.getvalue().startswith(b"%PDF"))
        self.assertTrue(docx_buf.getvalue().startswith(b"PK"))

    def test_ai_call_json_round_trip(self):
        raw = im._ai_call(
            "generate_structured_interview_qa request JOB DESCRIPTION (must anchor EVERY question): "
            "python developer. CANDIDATE RESUME: python",
            json_mode=True,
        )
        data = im.json.loads(raw)
        self.assertIsInstance(data, dict)

    def test_tts_component_html_escaping(self):
        html = tts_component_html('Use `format` with ${value}.')
        self.assertIn("\\`format\\`", html)
        self.assertIn("\\${value}", html)
        self.assertNotIn("${value}", html.replace("\\${value}", ""))

    def test_reset_interview_session_cleans_audio_and_answers(self):
        import streamlit as st
        st.session_state["voice_answer_0"] = "Old answer Q1"
        st.session_state["typed_answer_0"] = "Typed answer Q1"
        st.session_state["audio_voice_answer_0"] = b"audiobytes"
        st.session_state["_dg_voice_answer_0"] = 12345
        st.session_state["f2f_voice_answer_0"] = "F2F answer Q1"

        im._reset_interview_session()

        self.assertNotIn("voice_answer_0", st.session_state)
        self.assertNotIn("typed_answer_0", st.session_state)
        self.assertNotIn("audio_voice_answer_0", st.session_state)
        self.assertNotIn("_dg_voice_answer_0", st.session_state)
        self.assertNotIn("f2f_voice_answer_0", st.session_state)


if __name__ == "__main__":
    unittest.main()
