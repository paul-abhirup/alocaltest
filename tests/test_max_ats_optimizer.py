"""
Hermetic tests for the Max ATS closed-loop optimizer.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv_generator


GOOD_CV = """
Jane Candidate
555-0100 | jane@example.com | London

PROFESSIONAL SUMMARY:
Software Engineer with Python, AWS, REST API, CI/CD, Docker, Kubernetes, and data pipeline experience.

KEY SKILLS:
• Technical Skills: Python, AWS, REST API, CI/CD, Docker, Kubernetes, data pipeline
• Tools & Platforms: Git, PostgreSQL, cloud infrastructure
• Core Competencies: Agile methodology, monitoring and observability

WORK EXPERIENCE:
Acme | Software Engineer | 2021 - Present
• Engineered Python REST API services on AWS, improving release reliability by 25%.
• Implemented CI/CD pipelines with Docker and Kubernetes, reducing deployment time by 30%.
• Optimized data pipeline monitoring and observability, cutting incident response time by 20%.
• Collaborated in Agile methodology rituals to improve delivery predictability by 15%.
• Built PostgreSQL-backed reporting services that improved analytics refresh speed by 18%.
• Reviewed cloud infrastructure changes with Git workflows, reducing escaped defects by 12%.

EDUCATION:
• BSc Computer Science | Example University | 2020

PROJECTS:
Platform Automation
• Built cloud infrastructure automation with Python and PostgreSQL for reporting workflows.
• Documented deployment playbooks for AWS services, improving onboarding completion by 20%.

CERTIFICATIONS:
• AWS Cloud Practitioner - Amazon Web Services (2023)
"""


class TestKeywordPlan(unittest.TestCase):
    def test_classifies_supported_verified_and_missing_terms(self):
        resume = "Built Python REST API services on AWS."
        jd = "Software Engineer required: Python, REST API, AWS, Kubernetes, Terraform, CI/CD."
        answers = {"Kubernetes": "Ran Kubernetes deployments for internal services."}

        plan = cv_generator.build_jd_keyword_plan(resume, jd, answers)

        self.assertIn("python", plan["supported"])
        self.assertIn("aws", plan["supported"])
        self.assertIn("kubernetes", plan["candidate_verified"])
        self.assertIn("kubernetes", plan["required_keywords"])
        self.assertIn("terraform", plan["missing_evidence"])

    def test_keyword_plan_dedupes_terms(self):
        jd = "Python Python python REST API REST API cloud infrastructure."
        plan = cv_generator.build_jd_keyword_plan("Python REST API", jd)
        lowered = [x.lower() for x in plan["required_keywords"]]
        self.assertEqual(len(lowered), len(set(lowered)))


class TestOptimizationLoop(unittest.TestCase):
    def test_no_repair_when_first_pass_meets_thresholds(self):
        with patch.object(cv_generator, "_generate_cv_once", return_value=GOOD_CV) as gen, \
             patch.object(cv_generator, "_measure_cv_against_jd", return_value={
                 "score": 92, "keyword_match": 94, "missing_keywords": [], "suggestions": []
             }):
            result = cv_generator.generate_cv(
                "Python AWS REST API", "Python AWS REST API", return_metadata=True
            )

        self.assertEqual(gen.call_count, 1)
        self.assertEqual(result["repair_passes_used"], 0)
        self.assertEqual(result["ats_score"], 92)

    def test_max_ats_allows_two_repairs(self):
        repaired_once = GOOD_CV.replace("Kubernetes", "containers")
        measurements = [
            {"score": 72, "keyword_match": 60, "missing_keywords": ["kubernetes"], "suggestions": []},
            {"score": 82, "keyword_match": 84, "missing_keywords": ["kubernetes"], "suggestions": []},
            {"score": 91, "keyword_match": 93, "missing_keywords": [], "suggestions": []},
        ]
        drafts = ["bad cv", repaired_once, GOOD_CV]

        with patch.object(cv_generator, "_generate_cv_once", side_effect=drafts) as gen, \
             patch.object(cv_generator, "_measure_cv_against_jd", side_effect=measurements):
            result = cv_generator.generate_cv(
                "Kubernetes Python AWS", "Kubernetes Python AWS", return_metadata=True
            )

        self.assertEqual(gen.call_count, 3)
        self.assertEqual(result["repair_passes_used"], 2)
        self.assertEqual(result["keyword_match"], 93)

    def test_fast_mode_allows_one_repair(self):
        measurements = [
            {"score": 60, "keyword_match": 55, "missing_keywords": ["aws"], "suggestions": []},
            {"score": 80, "keyword_match": 82, "missing_keywords": ["aws"], "suggestions": []},
        ]
        with patch.object(cv_generator, "_generate_cv_once", side_effect=["bad cv", GOOD_CV]) as gen, \
             patch.object(cv_generator, "_measure_cv_against_jd", side_effect=measurements):
            result = cv_generator.generate_cv(
                "Python AWS", "Python AWS", optimization_depth="fast", return_metadata=True
            )

        self.assertEqual(gen.call_count, 2)
        self.assertEqual(result["repair_passes_used"], 1)


class TestScoreDisplayRegression(unittest.TestCase):
    def test_streamlit_boost_formula_removed(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")) as f:
            app_src = f.read()
        self.assertNotIn("int(raw_score * 1.4)", app_src)
        self.assertNotIn("int(raw_kw * 1.3)", app_src)
        self.assertIn("Target ATS", app_src)
        self.assertIn("Measured ATS Score", app_src)


if __name__ == "__main__":
    unittest.main()
