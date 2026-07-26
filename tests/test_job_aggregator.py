"""
Hermetic unit tests for the Phase 1 job aggregator engine (no network).
Run:  .venv/bin/python -m unittest discover -s tests -v
Covers the test matrix in docs/JOB_AGGREGATOR_PLAN.md §12.
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import job_aggregator as ja
from job_aggregator import (
    Job, SearchQuery, RemotiveAdapter, ArbeitnowAdapter, AdzunaAdapter,
    SourceAdapter, SourceAuthError, strip_html, infer_seniority,
    _parse_required_years, _yoe_adjustment, search_jobs, clear_cache,
    REMOTE_WORLDWIDE, REMOTE_UNKNOWN, ONSITE_HYBRID, CONTRACT,
)
from utils import keyword_overlap_score


# --------------------------- fixtures (real API shapes) -----------------------
REMOTIVE_RAW = {
    "id": 123,
    "url": "https://remotive.com/remote-jobs/software-dev/python-dev-123",
    "title": "Senior Python Developer",
    "company_name": "Acme",
    "candidate_required_location": "Worldwide",
    "job_type": "full_time",
    "publication_date": "2026-07-01T12:00:00",
    "salary": "$100k",
    "description": "<p>We need <strong>Python</strong> &amp; Django. 5+ years experience.</p>",
}

ARBEITNOW_RAW = {
    "company_name": "Beta GmbH",
    "title": "Frontend Engineer",
    "description": "<p>React &amp; TypeScript</p>",
    "remote": True,
    "url": "https://arbeitnow.com/view/frontend-engineer-beta",
    "tags": ["react"],
    "job_types": ["full_time"],
    "location": "Berlin",
    "created_at": 1751371200,   # 2025-07-01
}

ADZUNA_RAW = {
    "title": "Python Developer",
    "company": {"display_name": "Barclays"},
    "location": {"display_name": "London, UK"},
    "redirect_url": "https://www.adzuna.co.uk/land/ad/123?utm=x",
    "created": "2026-07-05T09:00:00Z",
    "salary_min": 72437.22,
    "salary_max": 90000,
    "contract_time": "full_time",
    "contract_type": "permanent",
    "description": "Python developer with Django. 3 years experience required.",
}


class FakeAdapter(SourceAdapter):
    """Passthrough adapter: fetch returns pre-built Job objects; normalize is identity."""
    def __init__(self, source, jobs=None, error=None, enabled=True):
        self.source = source
        self.source_name = source.title()
        self._jobs = jobs or []
        self._error = error
        self._enabled = enabled

    def enabled(self):
        return self._enabled

    def fetch(self, query):
        if self._error:
            raise self._error
        return list(self._jobs)

    def normalize(self, raw):
        return raw


def make_job(**kw):
    base = dict(title="Dev", company="Co", location="Remote",
                remote_type=REMOTE_UNKNOWN, job_type="full time",
                url="https://x/1", source="fake", source_name="Fake",
                description="python django")
    base.update(kw)
    return Job(**base)


class TestTextHelpers(unittest.TestCase):
    def test_strip_html_removes_tags_and_entities(self):
        out = strip_html("<p>We need <strong>Python</strong> &amp; Django.</p>")
        self.assertEqual(out, "We need Python & Django.")

    def test_strip_html_empty(self):
        self.assertEqual(strip_html(""), "")
        self.assertEqual(strip_html(None), "")

    def test_strip_html_caps_length(self):
        self.assertLessEqual(len(strip_html("x" * 20000)), ja.MAX_DESC_CHARS)


class TestScoring(unittest.TestCase):
    def test_overlap_score_range_and_determinism(self):
        resume = "experienced python developer with django and postgres"
        jd = "Looking for a python django engineer"
        s1 = keyword_overlap_score(resume, jd)
        s2 = keyword_overlap_score(resume, jd)
        self.assertEqual(s1, s2)
        self.assertTrue(0 <= s1 <= 100)
        self.assertGreater(s1, 0)

    def test_overlap_score_empty_inputs(self):
        self.assertEqual(keyword_overlap_score("", "python"), 0)
        self.assertEqual(keyword_overlap_score("python", ""), 0)


class TestYoE(unittest.TestCase):
    def test_parse_required_years(self):
        self.assertEqual(_parse_required_years("5+ years experience"), 5)
        self.assertEqual(_parse_required_years("min 3 yrs"), 3)
        self.assertIsNone(_parse_required_years("no number here"))

    def test_infer_seniority(self):
        self.assertEqual(infer_seniority("Senior Python Developer"), "Senior")  # no year → title match
        self.assertEqual(infer_seniority("Engineer with 3 years"), "Mid")       # explicit years win
        self.assertEqual(infer_seniority("Junior Analyst"), "Junior")
        self.assertEqual(infer_seniority("Engineer, 8+ years required"), "Senior+")
        self.assertIsNone(infer_seniority("Some Role"))

    def test_yoe_adjustment_bounded(self):
        self.assertEqual(_yoe_adjustment(None, "5 years"), 0)
        self.assertEqual(_yoe_adjustment(6, "5 years"), 5)      # meets requirement
        self.assertLess(_yoe_adjustment(1, "10 years"), 0)      # underqualified → penalty
        self.assertGreaterEqual(_yoe_adjustment(1, "10 years"), -10)


class TestNormalize(unittest.TestCase):
    def test_remotive_worldwide(self):
        j = RemotiveAdapter().normalize(REMOTIVE_RAW)
        self.assertEqual(j.title, "Senior Python Developer")
        self.assertEqual(j.company, "Acme")
        self.assertEqual(j.remote_type, REMOTE_WORLDWIDE)
        self.assertEqual(j.posted_date, "2026-07-01")
        self.assertNotIn("<", j.description)          # HTML stripped
        self.assertIn("Python", j.description)

    def test_remotive_unknown_location(self):
        raw = dict(REMOTIVE_RAW, candidate_required_location="USA")
        self.assertEqual(RemotiveAdapter().normalize(raw).remote_type, REMOTE_UNKNOWN)

    def test_remotive_missing_title(self):
        self.assertIsNone(RemotiveAdapter().normalize({"title": ""}))

    def test_arbeitnow_remote(self):
        j = ArbeitnowAdapter().normalize(ARBEITNOW_RAW)
        self.assertEqual(j.remote_type, REMOTE_UNKNOWN)
        self.assertEqual(j.posted_date, "2025-07-01")
        self.assertNotIn("<", j.description)

    def test_arbeitnow_onsite(self):
        raw = dict(ARBEITNOW_RAW, remote=False, job_types=["full_time"])
        self.assertEqual(ArbeitnowAdapter().normalize(raw).remote_type, ONSITE_HYBRID)

    def test_arbeitnow_contract(self):
        raw = dict(ARBEITNOW_RAW, remote=False, job_types=["contract"])
        self.assertEqual(ArbeitnowAdapter().normalize(raw).remote_type, CONTRACT)

    def test_adzuna_salary_and_location(self):
        a = AdzunaAdapter()
        a._currency = "£"
        j = a.normalize(ADZUNA_RAW)
        self.assertEqual(j.company, "Barclays")
        self.assertEqual(j.location, "London, UK")
        self.assertEqual(j.salary, "£72,437–£90,000")
        self.assertEqual(j.posted_date, "2026-07-05")
        self.assertEqual(j.remote_type, ONSITE_HYBRID)

    def test_adzuna_missing_salary(self):
        a = AdzunaAdapter(); a._currency = "£"
        raw = dict(ADZUNA_RAW); raw.pop("salary_min"); raw.pop("salary_max")
        self.assertIsNone(a.normalize(raw).salary)


class TestOrchestration(unittest.TestCase):
    def setUp(self):
        clear_cache()

    def test_dedupe_keeps_more_complete(self):
        thin = make_job(url="https://job/1", source="remotive", source_name="Remotive",
                        description="python", salary=None, posted_date="")
        rich = make_job(url="https://job/1/", source="adzuna", source_name="Adzuna",
                        description="python django", salary="£50,000", posted_date="2026-07-01")
        res = search_jobs(SearchQuery(title="dev"),
                          sources=[FakeAdapter("remotive", [thin]),
                                   FakeAdapter("adzuna", [rich])])
        self.assertEqual(res["counts"]["total"], 1)              # deduped to one
        self.assertEqual(res["jobs"][0].salary, "£50,000")       # richer copy kept
        self.assertIn("Remotive", res["jobs"][0].also_on)        # cross-source noted

    def test_scoring_applied_and_sorted(self):
        clear_cache()
        good = make_job(url="https://job/a", company="Company A", description="python django postgres aws")
        weak = make_job(url="https://job/b", company="Company B", description="documentation notes writing")
        res = search_jobs(SearchQuery(title="dev"),
                          resume_text="python django postgres backend engineer",
                          sources=[FakeAdapter("s_score", [weak, good])])
        self.assertEqual(res["jobs"][0].url, "https://job/a")    # higher match first
        self.assertGreater(res["jobs"][0].match_score, res["jobs"][1].match_score)

    def test_cache_holds_no_scores(self):
        job = make_job(url="https://job/a", description="python django")
        search_jobs(SearchQuery(title="dev"), resume_text="python django",
                    sources=[FakeAdapter("s", [job])])
        # inspect what got cached — must be user-independent (no match_score)
        cached_entries = [v for k, v in ja._CACHE.items()]
        self.assertTrue(cached_entries)
        for _, jobs in cached_entries:
            for cj in jobs:
                self.assertIsNone(cj.match_score)

    def test_work_type_filter(self):
        remote = make_job(url="https://job/r", remote_type=REMOTE_WORLDWIDE)
        onsite = make_job(url="https://job/o", remote_type=ONSITE_HYBRID)
        res = search_jobs(SearchQuery(title="dev", work_types=[REMOTE_WORLDWIDE]),
                          sources=[FakeAdapter("s", [remote, onsite])])
        self.assertEqual(res["counts"]["shown"], 1)
        self.assertEqual(res["jobs"][0].remote_type, REMOTE_WORLDWIDE)
        self.assertEqual(res["empty_reason"], None)

    def test_empty_filtered_out(self):
        onsite = make_job(url="https://job/o", remote_type=ONSITE_HYBRID)
        res = search_jobs(SearchQuery(title="dev", work_types=[REMOTE_WORLDWIDE]),
                          sources=[FakeAdapter("s", [onsite])])
        self.assertEqual(res["empty_reason"], "filtered_out")

    def test_empty_no_results(self):
        res = search_jobs(SearchQuery(title="dev"), sources=[FakeAdapter("s", [])])
        self.assertEqual(res["empty_reason"], "no_results")

    def test_all_sources_unreachable(self):
        res = search_jobs(SearchQuery(title="dev"),
                          sources=[FakeAdapter("s", error=RuntimeError("boom"))])
        self.assertEqual(res["status"]["s"], "error")
        self.assertEqual(res["empty_reason"], "unreachable")

    def test_auth_error_status(self):
        res = search_jobs(SearchQuery(title="dev"),
                          sources=[FakeAdapter("adzuna", error=SourceAuthError("bad key"))])
        self.assertEqual(res["status"]["adzuna"], "auth")

    def test_one_source_fails_others_survive(self):
        ok = make_job(url="https://job/ok")
        res = search_jobs(SearchQuery(title="dev"),
                          sources=[FakeAdapter("bad", error=RuntimeError("x")),
                                   FakeAdapter("good", [ok])])
        self.assertEqual(res["status"]["bad"], "error")
        self.assertEqual(res["status"]["good"], "ok")
        self.assertEqual(res["counts"]["shown"], 1)

    def test_stale_while_error(self):
        q = SearchQuery(title="dev")
        job = make_job(url="https://job/a")
        # Seed cache as EXPIRED for source "s"
        ckey = f"s:{q.cache_key(['s'])}"
        ja._CACHE[ckey] = (time.time() - 10 ** 6, [job])
        res = search_jobs(q, sources=[FakeAdapter("s", error=RuntimeError("boom"))])
        self.assertEqual(res["status"]["s"], "stale")           # served stale copy
        self.assertEqual(res["counts"]["shown"], 1)


class TestHTMLScraper(unittest.TestCase):
    def test_parser_ignores_boilerplate(self):
        html_content = """
        <html>
            <head><title>Job Title</title></head>
            <body>
                <header><nav>Nav Link</nav></header>
                <style>.foo { color: red; }</style>
                <script>console.log("hello");</script>
                <div class="content">
                    <h1>Software Engineer</h1>
                    <p>Required skills: Python, SQL</p>
                </div>
                <footer>Footer content</footer>
            </body>
        </html>
        """
        parser = ja.JobDescriptionHTMLParser()
        parser.feed(html_content)
        text = parser.get_text()
        self.assertIn("Software Engineer", text)
        self.assertIn("Required skills: Python, SQL", text)
        self.assertNotIn("Nav Link", text)
        self.assertNotIn("console.log", text)
        self.assertNotIn("Footer content", text)

    def test_fetch_full_description_none_on_empty(self):
        self.assertIsNone(ja.fetch_full_job_description(""))


class TestTitleGuardrailAndRecency(unittest.TestCase):
    def test_title_relevance_score(self):
        # Exact/synonym match
        self.assertEqual(ja.compute_title_relevance_score("Python Developer", "Senior Python Developer"), 100.0)
        self.assertEqual(ja.compute_title_relevance_score("React Dev", "Frontend React Engineer"), 100.0)

        # Irrelevant titles
        self.assertEqual(ja.compute_title_relevance_score("Python Developer", "Sales Operations Executive"), 0.0)
        self.assertEqual(ja.compute_title_relevance_score("Data Analyst", "Customer Support Specialist"), 0.0)

    def test_is_title_relevant_drops_hoguspogus(self):
        self.assertTrue(ja.is_title_relevant("Python Developer", "Senior Python Developer"))
        self.assertFalse(ja.is_title_relevant("Python Developer", "Marketing Manager"))

    def test_recency_scoring(self):
        today_str = time.strftime("%Y-%m-%d")
        self.assertEqual(ja.compute_recency_score(today_str), 100.0)
        self.assertEqual(ja.compute_recency_score("2020-01-01"), 20.0)

    def test_search_jobs_filters_irrelevant_jobs(self):
        relevant = make_job(url="https://job/1", title="Senior Python Engineer")
        irrelevant = make_job(url="https://job/2", title="Customer Support Lead")
        res = search_jobs(SearchQuery(title="Python Developer"),
                          sources=[FakeAdapter("s", [relevant, irrelevant])])
        # Only relevant job should be shown
        self.assertEqual(res["counts"]["shown"], 1)
        self.assertEqual(res["jobs"][0].title, "Senior Python Engineer")


if __name__ == "__main__":
    unittest.main()
