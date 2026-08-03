"""
search_engine/deduplication.py — Phase 8: Fingerprint Deduplication.
Groups cross-posted listings into a single Job record, preserving multi-apply URLs.
"""

from __future__ import annotations
import hashlib
import time
from typing import Any
from search_engine.normalizer import normalize_title


def compute_job_fingerprint(company: str, title: str, location: str, posted_date: str = "") -> str:
    """Compute sha256 fingerprint from (normalized_company, normalized_title, normalized_location, posting_week)."""
    norm_comp = (company or "").strip().lower()
    norm_tit = normalize_title(title).lower()
    norm_loc = (location or "").strip().lower()

    # Calculate posting week string YYYY-WW
    week_str = ""
    if posted_date:
        try:
            date_str = posted_date.strip()[:10]
            struct_t = time.strptime(date_str, "%Y-%m-%d")
            week_str = time.strftime("%Y-%U", struct_t)
        except Exception:
            week_str = ""

    raw = f"{norm_comp}|{norm_tit}|{norm_loc}|{week_str}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def deduplicate_jobs(jobs: list[Any]) -> list[Any]:
    """Deduplicate jobs by URL and fingerprint, merging multi-provider apply URLs into also_on list.

    Preference order when two records share a fingerprint:
    1) higher completeness (salary + date + description)
    2) non-paywall URL over a known aggregator/paywall intermediate
    """
    kept_by_fingerprint: dict[str, Any] = {}

    def _is_paywall(url: str) -> bool:
        if not url:
            return True
        low = url.lower()
        return any(h in low for h in (
            "ladders.", "jobleads.", "ziprecruiter.", "indeed.com", "glassdoor.",
            "adzuna.", "jooble.", "simplyhired.", "monster.com", "careerbuilder.",
            "google.com/search", "nationwidecareers",
        ))

    for job in jobs:
        if not hasattr(job, "dedupe_key"):
            continue

        fp = compute_job_fingerprint(job.company, job.title, job.location, getattr(job, "posted_date", ""))
        existing = kept_by_fingerprint.get(fp)

        if existing is None:
            kept_by_fingerprint[fp] = job
        else:
            if hasattr(existing, "also_on") and job.source_name not in existing.also_on and job.source != existing.source:
                existing.also_on.append(job.source_name)
            if hasattr(job, "completeness") and hasattr(existing, "completeness"):
                better = (
                    job.completeness() > existing.completeness()
                    or (
                        job.completeness() == existing.completeness()
                        and not _is_paywall(job.url)
                        and _is_paywall(existing.url)
                    )
                )
                if better:
                    job.also_on = getattr(existing, "also_on", [])
                    if existing.source != job.source and existing.source_name not in job.also_on:
                        job.also_on.append(existing.source_name)
                    kept_by_fingerprint[fp] = job

    return list(kept_by_fingerprint.values())
