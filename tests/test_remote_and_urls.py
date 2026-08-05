import unittest
from unittest import mock

import job_aggregator as ja
from job_aggregator import (
    JSearchAdapter, RemotiveAdapter, SearchQuery,
    REMOTE_WORLDWIDE, REMOTE_IN_COUNTRY, REMOTE_UNKNOWN, ONSITE_HYBRID, CONTRACT,
    is_paywall_url, _resolve_apply_url, resolve_display_urls, _title_variants,
)


class TestRemoteLabeling(unittest.TestCase):
    def test_jsearch_remote_with_city_country_is_in_country(self):
        raw = {
            "job_title": "Python Developer",
            "employer_name": "Acme",
            "job_city": "Austin",
            "job_country": "US",
            "job_is_remote": True,
            "job_description": "Remote role...",
        }
        j = JSearchAdapter().normalize(raw)
        self.assertEqual(j.remote_type, REMOTE_IN_COUNTRY)

    def test_jsearch_remote_no_location_is_worldwide(self):
        raw = {
            "job_title": "Python Developer",
            "employer_name": "Acme",
            "job_is_remote": True,
            "job_description": "Remote role...",
        }
        j = JSearchAdapter().normalize(raw)
        self.assertEqual(j.remote_type, REMOTE_WORLDWIDE)

    def test_jsearch_onsite(self):
        raw = {
            "job_title": "Python Developer",
            "employer_name": "Acme",
            "job_city": "Austin",
            "job_country": "US",
            "job_is_remote": False,
            "job_description": "Office-based role.",
        }
        j = JSearchAdapter().normalize(raw)
        self.assertEqual(j.remote_type, ONSITE_HYBRID)

    def test_remotive_worldwide(self):
        raw = {
            "title": "Python Developer",
            "company_name": "Acme",
            "candidate_required_location": "Worldwide",
        }
        j = RemotiveAdapter().normalize(raw)
        self.assertEqual(j.remote_type, REMOTE_WORLDWIDE)

    def test_remotive_named_country_is_in_country(self):
        raw = {
            "title": "Python Developer",
            "company_name": "Acme",
            "candidate_required_location": "Germany",
        }
        j = RemotiveAdapter().normalize(raw)
        self.assertEqual(j.remote_type, REMOTE_IN_COUNTRY)

    def test_remotive_empty_location_is_unknown(self):
        raw = {
            "title": "Python Developer",
            "company_name": "Acme",
            "candidate_required_location": "",
        }
        j = RemotiveAdapter().normalize(raw)
        self.assertEqual(j.remote_type, REMOTE_UNKNOWN)

    def test_remotive_skipped_for_onsite_only(self):
        adapter = RemotiveAdapter()
        q = SearchQuery(title="Python Developer", work_types=[ONSITE_HYBRID])
        self.assertEqual(adapter.fetch(q), [])


class TestLocationFilterRemoteAwareness(unittest.TestCase):
    def test_worldwide_remote_matches_any_country(self):
        from search_engine.filters import is_location_mismatched
        self.assertFalse(
            is_location_mismatched("Austin, US", query_country="in",
                                   job_remote_type=REMOTE_WORLDWIDE)
        )

    def test_in_country_remote_passes_any_country(self):
        from search_engine.filters import is_location_mismatched
        # Redesigned: ALL remote jobs bypass country filter. Work-type
        # filtering separates REMOTE_IN_COUNTRY vs REMOTE_WORLDWIDE.
        self.assertFalse(
            is_location_mismatched("Austin, US", query_country="in",
                                   job_remote_type=REMOTE_IN_COUNTRY)
        )
        self.assertFalse(
            is_location_mismatched("Austin, US", query_country="us",
                                   job_remote_type=REMOTE_IN_COUNTRY)
        )

    def test_onsite_jobs_filtered_by_country(self):
        from search_engine.filters import is_location_mismatched
        # Onsite/Hybrid jobs MUST match the selected country
        self.assertTrue(
            is_location_mismatched("Austin, US", query_country="in",
                                   job_remote_type=ONSITE_HYBRID)
        )
        self.assertFalse(
            is_location_mismatched("Austin, US", query_country="us",
                                   job_remote_type=ONSITE_HYBRID)
        )

    def test_contract_jobs_bypass_country(self):
        from search_engine.filters import is_location_mismatched
        self.assertFalse(
            is_location_mismatched("Austin, US", query_country="in",
                                   job_remote_type=CONTRACT)
        )


class TestRemoteInLocationBox(unittest.TestCase):
    def test_remote_location_converted_to_worktype(self):
        q = SearchQuery(title="Python Developer", location="Remote", country="all")
        # Capture the normalized query via a fake adapter
        captured = {}

        class FakeAdapter(ja.SourceAdapter):
            source = "fake"
            source_name = "Fake"
            def fetch(self, query):
                captured["location"] = query.location
                captured["work_types"] = list(query.work_types or [])
                return []
            def normalize(self, raw):
                return None

        ja.clear_cache()
        ja.search_jobs(q, sources=[FakeAdapter()])
        self.assertEqual(captured["location"], "")
        self.assertIn(REMOTE_WORLDWIDE, captured["work_types"])


class TestWorkTypeMatch(unittest.TestCase):
    """Regression tests for _matches_work_type & remote work type filtering."""

    def test_worldwide_matches_worldwide(self):
        self.assertTrue(ja._matches_work_type(
            ja.Job(title="T", company="C", location="—", remote_type=REMOTE_WORLDWIDE,
                   job_type="", url="x", source="x", source_name="x"),
            {REMOTE_WORLDWIDE}))

    def test_worldwide_matches_in_country(self):
        # Worldwide filter should also show in-country remote jobs
        self.assertTrue(ja._matches_work_type(
            ja.Job(title="T", company="C", location="US", remote_type=REMOTE_IN_COUNTRY,
                   job_type="", url="x", source="x", source_name="x"),
            {REMOTE_WORLDWIDE}))

    def test_worldwide_does_not_match_onsite(self):
        self.assertFalse(ja._matches_work_type(
            ja.Job(title="T", company="C", location="US", remote_type=ONSITE_HYBRID,
                   job_type="", url="x", source="x", source_name="x"),
            {REMOTE_WORLDWIDE}))

    def test_in_country_does_not_match_onsite(self):
        self.assertFalse(ja._matches_work_type(
            ja.Job(title="T", company="C", location="US", remote_type=ONSITE_HYBRID,
                   job_type="", url="x", source="x", source_name="x"),
            {REMOTE_IN_COUNTRY}))

    def test_unknown_remote_satisfies_any(self):
        self.assertTrue(ja._matches_work_type(
            ja.Job(title="T", company="C", location="", remote_type=REMOTE_UNKNOWN,
                   job_type="", url="x", source="x", source_name="x"),
            {REMOTE_WORLDWIDE}))
        self.assertTrue(ja._matches_work_type(
            ja.Job(title="T", company="C", location="", remote_type=REMOTE_UNKNOWN,
                   job_type="", url="x", source="x", source_name="x"),
            {REMOTE_IN_COUNTRY}))

    def test_onsite_hybrid_matches_onsite_only(self):
        self.assertTrue(ja._matches_work_type(
            ja.Job(title="T", company="C", location="US", remote_type=ONSITE_HYBRID,
                   job_type="", url="x", source="x", source_name="x"),
            {ONSITE_HYBRID}))


class TestTitleVariants(unittest.TestCase):
    def test_python_developer_variants(self):
        q = SearchQuery(title="Python Developer")
        variants = _title_variants(q)
        self.assertIn("Python Developer", variants)
        self.assertIn("Python Engineer", variants)
        self.assertLessEqual(len(variants), ja.MAX_VARIANTS_PER_SOURCE + 1)

    def test_empty_title(self):
        q = SearchQuery(title="")
        self.assertEqual(_title_variants(q), [""])


class TestPaywallUrl(unittest.TestCase):
    def test_paywall_domains_detected(self):
        self.assertTrue(is_paywall_url("https://www.adzuna.com/details/123"))
        self.assertTrue(is_paywall_url("https://jooble.org/away/456"))
        self.assertTrue(is_paywall_url("https://www.indeed.com/viewjob?jk=x"))
        self.assertTrue(is_paywall_url("https://www.ladders.com/job/x"))
        self.assertTrue(is_paywall_url("https://ziprecruiter.com/c/x"))

    def test_direct_urls_not_paywall(self):
        self.assertFalse(is_paywall_url("https://remotive.com/jobs/123"))
        self.assertFalse(is_paywall_url("https://acme.com/careers/python"))
        self.assertFalse(is_paywall_url("https://www.themuse.com/jobs/acme/role-1"))

    def test_empty_url_is_paywall(self):
        self.assertTrue(is_paywall_url(""))
        self.assertTrue(is_paywall_url(None))


class TestResolveApplyUrl(unittest.TestCase):
    def test_cached_result(self):
        ja._RESOLVED_URL_CACHE.clear()
        url = "https://example.com/job/1"
        with mock.patch("job_aggregator.requests.request") as mock_req:
            resp = mock.Mock()
            resp.status_code = 200
            resp.url = "https://acme.com/careers/1"
            mock_req.return_value = resp
            out1 = _resolve_apply_url(url)
            out2 = _resolve_apply_url(url)
            self.assertEqual(out1, "https://acme.com/careers/1")
            self.assertEqual(out2, "https://acme.com/careers/1")
            mock_req.assert_called_once()  # second call served from cache

    def test_failure_keeps_original(self):
        ja._RESOLVED_URL_CACHE.clear()
        url = "https://broken.example.com/job/1"
        with mock.patch("job_aggregator.requests.request", side_effect=Exception("boom")):
            self.assertEqual(_resolve_apply_url(url), url)

    def test_403_keeps_original(self):
        ja._RESOLVED_URL_CACHE.clear()
        url = "https://www.adzuna.com/details/123"
        with mock.patch("job_aggregator.requests.request") as mock_req:
            resp = mock.Mock()
            resp.status_code = 403
            resp.url = url
            mock_req.return_value = resp
            self.assertEqual(_resolve_apply_url(url), url)


if __name__ == "__main__":
    unittest.main()
