"""
tests/test_templates.py — Unit tests for pristine PDF template export engine.
Run: .venv/bin/python -m unittest discover -s tests -v
"""

import unittest
from io import BytesIO
from resume_schema import ResumeData, ContactInfo, ExperienceItem, EducationItem, SkillCategory, ProjectItem
from templates import apply_template, create_classic_serif_template, create_modern_sans_template


class TestResumeTemplates(unittest.TestCase):
    def setUp(self):
        self.resume = ResumeData(
            header=ContactInfo(
                name="Abhirup Paul",
                target_title="Backend Software Engineer",
                email="abhiruppaul1249@gmail.com",
                phone="+91-9874645757",
                location="Kolkata, India",
                github="github.com/paul-abhirup",
                linkedin="linkedin.com/in/abhirup-paul",
            ),
            summary="Backend software engineer building scalable solutions in Golang, Python, and C++.",
            education=[
                EducationItem(
                    institution="GKV FET, Haridwar",
                    degree="B.Tech, Electronics and Computer Engineering",
                    dates="2022 – 2026",
                    details="CGPA: 8.5 / 10.0",
                )
            ],
            experience=[
                ExperienceItem(
                    role="R&D Engineer",
                    company="SCAN Lab, IIT Bombay",
                    location="Mumbai, India",
                    dates="Jul 2025 – Present",
                    bullets=[
                        "Architected Neuroscan360 platform live in two major Mumbai hospitals.",
                        "Built Python and Golang data analytics microservices containerised with Docker.",
                    ],
                )
            ],
            projects=[
                ProjectItem(
                    title="Distributed Analytics Pipeline",
                    tech_stack="Golang, PostgreSQL, Docker",
                    bullets=["Built high-throughput analytics service processing 10,000+ events/day."],
                )
            ],
            skills=[
                SkillCategory(category_name="Languages", skills=["Golang", "Python", "C++", "SQL"]),
                SkillCategory(category_name="DevOps", skills=["Docker", "Kubernetes", "GitLab CI"]),
            ],
            achievements=["Co-inventor — Neuroscan360 patent (IIT Bombay)"],
        )

    def test_classic_serif_template_renders_pdf_bytes(self):
        buf = apply_template(self.resume, "classic_serif")
        self.assertIsInstance(buf, BytesIO)
        pdf_bytes = buf.getvalue()
        self.assertTrue(len(pdf_bytes) > 500)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_modern_sans_template_renders_pdf_bytes(self):
        buf = apply_template(self.resume, "modern_sans")
        self.assertIsInstance(buf, BytesIO)
        pdf_bytes = buf.getvalue()
        self.assertTrue(len(pdf_bytes) > 500)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_raw_text_fallback_parsing(self):
        raw_text = """Abhirup Paul
abhirup@gmail.com | +91-9874645757 | github.com/paul-abhirup

SUMMARY:
Experienced software engineer.

WORK EXPERIENCE:
Software Engineer | Acme Corp
- Built REST APIs in Python.
"""
        buf1 = create_classic_serif_template(raw_text)
        buf2 = create_modern_sans_template(raw_text)
        self.assertTrue(len(buf1.getvalue()) > 500)
        self.assertTrue(len(buf2.getvalue()) > 500)


if __name__ == "__main__":
    unittest.main()
