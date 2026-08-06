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
from search_engine.resume_match import extract_distinctive_terms, is_resume_job_mismatched
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

    # --- Core-token & synonym regression tests (Issue #3 fix) ---

    def test_core_token_rejects_generic_role_only_match(self):
        """Instructional Designer should NOT match generic Designer roles."""
        self.assertFalse(is_title_relevant("Instructional Designer", "UI/UX Designer"))
        self.assertFalse(is_title_relevant("Instructional Designer", "Graphic Designer"))
        self.assertFalse(is_title_relevant("Instructional Designer", "UX Designer"))

    def test_core_token_accepts_same_domain_roles(self):
        """Instructional Designer should match instructional/learning design variants."""
        self.assertTrue(is_title_relevant("Instructional Designer", "Senior Instructional Designer"))
        self.assertTrue(is_title_relevant("Instructional Designer", "Instructional Design Specialist"))
        self.assertTrue(is_title_relevant("Instructional Designer", "Learning Experience Designer"))
        self.assertTrue(is_title_relevant("Instructional Designer", "Curriculum Designer"))

    def test_core_token_rejects_cross_role_matches(self):
        """Different roles sharing generic title nouns should not match."""
        self.assertFalse(is_title_relevant("Python Developer", "Java Developer"))
        self.assertFalse(is_title_relevant("Product Manager", "Project Manager"))
        self.assertFalse(is_title_relevant("Marketing Manager", "Sales Manager"))
        self.assertFalse(is_title_relevant("Frontend Developer", "Backend Developer"))
        self.assertFalse(is_title_relevant("Full Stack Developer", "Frontend Developer"))
        self.assertFalse(is_title_relevant("Data Analyst", "Marketing Analyst"))

    def test_synonym_expansion_accepts_related_roles(self):
        self.assertTrue(is_title_relevant("DevOps Engineer", "Site Reliability Engineer"))
        self.assertTrue(is_title_relevant("DevOps Engineer", "Cloud Engineer"))
        self.assertTrue(is_title_relevant("Machine Learning Engineer", "ML Engineer"))
        self.assertTrue(is_title_relevant("Machine Learning Engineer", "AI Engineer"))
        self.assertTrue(is_title_relevant("Data Analyst", "Reporting Analyst"))
        self.assertTrue(is_title_relevant("Data Analyst", "Business Analyst"))
        self.assertTrue(is_title_relevant("Project Manager", "Scrum Master"))
        self.assertTrue(is_title_relevant("UI Designer", "Product Designer"))
        self.assertTrue(is_title_relevant("Backend Developer", "Python Developer"))
        self.assertTrue(is_title_relevant("Full Stack Developer", "Full-Stack Engineer"))

    def test_hyphenated_variants_match(self):
        """full-stack/fullstack should be equivalent for matching."""
        self.assertTrue(is_title_relevant("Full Stack Developer", "Full Stack Developer"))
        self.assertTrue(is_title_relevant("Full Stack Developer", "Full-Stack Engineer"))
        self.assertFalse(is_title_relevant("Full Stack Developer", "Frontend Developer"))

    def test_substring_match_accepted(self):
        """Exact multi-word query inside job title should always pass."""
        self.assertTrue(is_title_relevant("Python Developer", "Backend Python Developer"))
        self.assertTrue(is_title_relevant("UI Designer", "Senior UI Designer"))


class TestHardFilters(unittest.TestCase):
    def test_negative_keywords(self):
        self.assertTrue(has_negative_keyword("Private Tutor", "Python Developer"))
        self.assertTrue(has_negative_keyword("HR Recruiter", "Software Engineer"))
        # Sales / marketing / support are no longer auto-rejected (volume fix)
        self.assertFalse(has_negative_keyword("Sales Operations Executive", "Python Developer"))
        self.assertFalse(has_negative_keyword("Marketing Manager", "Marketing"))
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


class TestResumeMatch(unittest.TestCase):
    def test_distinctive_terms_culinary(self):
        resume = "Commi Chef with kitchen experience and food safety training. Commi Chef at fine dining restaurant."
        terms = extract_distinctive_terms(resume, top_n=10)
        self.assertIn("chef", terms)
        self.assertIn("commi", terms)
        self.assertIn("kitchen", terms)
        self.assertNotIn("team", terms)
        self.assertNotIn("experience", terms)
        self.assertNotIn("training", terms)

    def test_distinctive_terms_tech(self):
        resume = "Python developer with AWS and Docker. Python backend developer."
        terms = extract_distinctive_terms(resume, top_n=10)
        self.assertIn("python", terms)
        self.assertIn("docker", terms)
        self.assertIn("backend", terms)
        self.assertIn("aws", terms)

    def test_mismatch_detects_cross_domain(self):
        resume = "Worked as Commi Chef in busy kitchen. Responsible for food prep, cooking, menu planning, hygiene standards. Trained junior staff on kitchen safety."
        job_title = "Senior Chef Developer"
        job_desc = "Looking for a Chef Developer with CI/CD, Go, Java, SQL, Terraform."
        # Shares the "chef" term so it is NOT hard-dropped (lenient volume fix)
        self.assertFalse(is_resume_job_mismatched(resume, job_title, job_desc))

    def test_mismatch_detects_zero_overlap(self):
        resume = "Accounting and payroll management, financial reporting, tax filing, QuickBooks, invoicing."
        job_title = "Backend Python Developer"
        job_desc = "Building REST APIs with Python, Django, PostgreSQL, Docker."
        self.assertTrue(is_resume_job_mismatched(resume, job_title, job_desc))

    def test_mismatch_allows_same_domain(self):
        resume = "Worked as Commi Chef in busy kitchen. Responsible for food prep, cooking, menu planning, hygiene standards. Trained junior staff on kitchen safety."
        job_title = "Sous Chef"
        job_desc = "Looking for an experienced Sous Chef for fine dining restaurant. Food preparation, cooking, menu planning, team leadership, kitchen hygiene."
        self.assertFalse(is_resume_job_mismatched(resume, job_title, job_desc))

    def test_no_resume_passes(self):
        self.assertFalse(is_resume_job_mismatched(None, "Software Engineer", "Java, Python"))

    def test_empty_resume_passes(self):
        self.assertFalse(is_resume_job_mismatched("", "Software Engineer", "Java, Python"))

    def test_generic_resume_passes(self):
        resume = "Hardworking team player with communication skills and experience."
        job_title = "Software Engineer"
        job_desc = "Java, Python, team player."
        self.assertFalse(is_resume_job_mismatched(resume, job_title, job_desc))


class TestPhantomSkillMatchFix(unittest.TestCase):
    def test_no_query_skills_returns_empty_matched(self):
        q = "commi chef"
        j = "Looking for a Go developer with CI/CD, Terraform, Java, SQL."
        score, matched = compute_skill_match_score(q, j)
        self.assertEqual(score, 50.0)
        self.assertEqual(matched, [])

    def test_explainability_no_skills_bullet_when_no_match(self):
        bullets = generate_explainability(
            "Senior Chef Developer", "commi chef", 50.0, [],
            "2026-07-11", "Onsite", 55, "Senior"
        )
        skill_bullets = [b for b in bullets if "Skills match" in b]
        self.assertEqual(len(skill_bullets), 0)

    def test_culinary_skills_extracted(self):
        text = "Culinary Arts degree, HACCP certified, food preparation and kitchen management."
        skills = extract_skills(text)
        self.assertIn("Culinary Arts", skills)
        self.assertIn("Food Safety", skills)
        self.assertIn("Food Preparation", skills)
        self.assertIn("Kitchen Management", skills)

    def test_cross_domain_skill_match_zero(self):
        q = "commi chef"
        j = "DevOps engineer with Go, Terraform, CI/CD, Kubernetes."
        score, matched = compute_skill_match_score(q, j)
        self.assertEqual(score, 50.0)
        self.assertEqual(matched, [])


class TestIrrelevantRoleATSScoreFix(unittest.TestCase):
    def test_irrelevant_job_role_score_is_penalized(self):
        dev_resume = "Senior Python Developer with 5 years experience in Django, FastAPI, Docker, PostgreSQL, AWS, CI/CD."
        marketing_job = DummyJob(
            title="Marketing Manager",
            company="BrandCorp",
            location="Remote",
            description="We are looking for a Marketing Manager to lead growth marketing, SEO, SEM, social media campaigns, and brand management."
        )
        score, title_sim, matched = calculate_composite_score(
            query_title="Python Developer",
            job=marketing_job,
            resume_text=dev_resume,
            target_role="Python Developer"
        )
        self.assertLessEqual(score, 25, f"Expected score for irrelevant job role to be <= 25, got {score}")

    def test_relevant_job_role_score_is_high(self):
        dev_resume = "Senior Python Developer with 5 years experience in Django, FastAPI, Docker, PostgreSQL, AWS, CI/CD."
        dev_job = DummyJob(
            title="Python Engineer",
            company="TechCorp",
            location="Remote",
            description="Looking for a Python Engineer skilled in Django, FastAPI, Docker, PostgreSQL, AWS, REST APIs."
        )
        score, title_sim, matched = calculate_composite_score(
            query_title="Python Developer",
            job=dev_job,
            resume_text=dev_resume,
            target_role="Python Developer"
        )
        self.assertGreaterEqual(score, 65, f"Expected score for relevant job role to be >= 65, got {score}")

    def test_noise_words_filtered_from_keyword_overlap(self):
        from utils import keyword_overlap_score
        # A generic description containing mostly noise words should not yield high overlap score
        generic_jd = "Great team working environment. We require experience and communication skills to support company projects."
        resume = "Python developer with technical skills in software engineering."
        score = keyword_overlap_score(resume, generic_jd)
        self.assertLessEqual(score, 30, f"Expected low overlap score for generic noise words, got {score}")


if __name__ == "__main__":
    unittest.main()

