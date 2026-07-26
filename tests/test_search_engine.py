"""
tests/test_search_engine.py — Unit tests for 17-Phase Job Search Relevance Engine.
Run: .venv/bin/python -m unittest discover -s tests -v
"""

import unittest
import time
from dataclasses import dataclass, field
from search_engine.normalizer import normalize_title, generate_search_variants
from search_engine.title_match import compute_title_similarity, is_title_relevant, token_set_ratio
from search_engine.filters import (
    has_negative_keyword, is_job_expired, is_experience_mismatched,
    is_employment_type_mismatched, normalize_location_string
)
from search_engine.deduplication import compute_job_fingerprint, deduplicate_jobs
from search_engine.skills import extract_skills, compute_skill_match_score
from search_engine.explainability import generate_explainability
from search_engine.ranking.scorer import calculate_composite_score
from search_engine.ranking.freshness import score_freshness
from search_engine.ranking.company_score import score_company


@dataclass
class DummyJob:
    title: str
    company: str
    location: str
    remote_type: str = "Remote"
    job_type: str = "Full-time"
    url: str = "https://example.com/job/1"
    source: str = "fake"
    source_name: str = "Fake"
    posted_date: str = "2026-07-25"
    description: str = "Python Developer with FastAPI, PostgreSQL, Docker, AWS"
    salary: str = "$120,000"
    also_on: list = field(default_factory=list)

    def dedupe_key(self):
        return f"url:{self.url}"

    def completeness(self):
        return sum(bool(x) for x in (self.salary, self.posted_date, self.description))


class TestQueryNormalizer(unittest.TestCase):
    def test_normalize_title(self):
        self.assertEqual(normalize_title("Sr. React.js Dev"), "Senior React Developer")
        self.assertEqual(normalize_title("Jr. Py Dev"), "Junior Python Developer")

    def test_generate_search_variants(self):
        vars_py = generate_search_variants("Python Developer")
        self.assertIn("Python Developer", vars_py)
        self.assertIn("Python Engineer", vars_py)


class TestTitleGuardrail(unittest.TestCase):
    def test_title_similarity_accepts_valid_roles(self):
        self.assertGreaterEqual(compute_title_similarity("Python Developer", "Senior Python Engineer"), 40.0)
        self.assertTrue(is_title_relevant("Python Developer", "Backend Python Developer"))
        self.assertTrue(is_title_relevant("Python Developer", "Software Engineer (Python)"))

    def test_title_similarity_rejects_unrelated_roles(self):
        self.assertLess(compute_title_similarity("Python Developer", "Sales Executive"), 40.0)
        self.assertFalse(is_title_relevant("Python Developer", "Marketing Manager"))
        self.assertFalse(is_title_relevant("Data Analyst", "Customer Support Representative"))


class TestHardFilters(unittest.TestCase):
    def test_negative_keywords(self):
        self.assertTrue(has_negative_keyword("Sales Operations Executive", "Python Developer"))
        self.assertTrue(has_negative_keyword("HR Recruiter", "Software Engineer"))
        # If explicitly searched, negative keyword is not triggered
        self.assertFalse(has_negative_keyword("Sales Manager", "Sales Manager"))

    def test_job_expired(self):
        today_str = time.strftime("%Y-%m-%d")
        self.assertFalse(is_job_expired(today_str))
        self.assertTrue(is_job_expired("2020-01-01"))

    def test_experience_mismatch(self):
        self.assertTrue(is_experience_mismatched(1, "Junior Python Dev", "Principal Architect", "10+ years required"))
        self.assertFalse(is_experience_mismatched(2, "Python Developer", "Mid-level Python Engineer", "3 years required"))

    def test_employment_type_mismatch(self):
        self.assertTrue(is_employment_type_mismatched(["Internship"], "Onsite", "Full-time"))
        self.assertFalse(is_employment_type_mismatched(["Contract"], "Remote", "Contract / Project"))


class TestDeduplication(unittest.TestCase):
    def test_fingerprint_merging(self):
        j1 = DummyJob(title="Python Developer", company="Acme Corp", location="London", url="https://a.com/1", source="s1")
        j2 = DummyJob(title="Python Developer", company="Acme Corp", location="London", url="https://b.com/2", source="s2")
        deduped = deduplicate_jobs([j1, j2])
        self.assertEqual(len(deduped), 1)


class TestSkillsExtraction(unittest.TestCase):
    def test_extract_skills(self):
        text = "We use Python, FastAPI, PostgreSQL, Docker, and AWS."
        skills = extract_skills(text)
        self.assertIn("Python", skills)
        self.assertIn("FastAPI", skills)
        self.assertIn("PostgreSQL", skills)
        self.assertIn("AWS", skills)

    def test_skill_match_score(self):
        q = "Python FastAPI PostgreSQL"
        j = "Looking for a Python developer with FastAPI, PostgreSQL, and Docker."
        score, matched = compute_skill_match_score(q, j)
        self.assertEqual(score, 100.0)
        self.assertIn("Python", matched)


class TestScoringAndExplainability(unittest.TestCase):
    def test_freshness_scoring(self):
        today_str = time.strftime("%Y-%m-%d")
        self.assertEqual(score_freshness(today_str), 100.0)

    def test_company_scoring(self):
        self.assertEqual(score_company("Google"), 100.0)
        self.assertEqual(score_company("Acme Recruitment Agency"), 50.0)

    def test_composite_scoring_and_explainability(self):
        job = DummyJob(title="Senior Python Engineer", company="Acme", location="London")
        score, sim, skills = calculate_composite_score("Python Developer", job)
        self.assertGreaterEqual(score, 50)
        explain = generate_explainability(job.title, "Python Developer", sim, skills, job.posted_date, job.remote_type, score)
        self.assertTrue(len(explain) > 0)


if __name__ == "__main__":
    unittest.main()
