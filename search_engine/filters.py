"""
search_engine/filters.py — Phases 4, 5, 6, 7, 9: Hard Filters (Negative Keywords, Experience, Job Type, Location, Age Cutoff).
"""

from __future__ import annotations
import re
import time
from typing import Optional
from search_engine.config import DEFAULT_NEGATIVE_KEYWORDS, MAX_JOB_AGE_DAYS, EXPERIENCE_BUCKETS, EMPLOYMENT_TYPES
from search_engine.normalizer import normalize_title


def has_negative_keyword(job_title: str, query_title: str = "", custom_negative_keywords: Optional[set[str]] = None) -> bool:
    """Phase 4: Reject titles containing negative keywords unless user explicitly searched for them."""
    if not job_title:
        return False

    negatives = custom_negative_keywords or DEFAULT_NEGATIVE_KEYWORDS
    q_low = (query_title or "").lower()
    j_low = job_title.lower()

    for kw in negatives:
        # If user explicitly searched for this negative word (e.g. "Sales Developer" or "HR Manager"), don't filter it out
        if kw in q_low:
            continue
        if re.search(r"\b" + re.escape(kw) + r"\b", j_low):
            return True
            
    return False


def is_job_expired(posted_date: str, max_days: int = MAX_JOB_AGE_DAYS) -> bool:
    """Phase 9: Discard jobs older than max_days (45 days default)."""
    if not posted_date or not posted_date.strip():
        return False
    try:
        date_str = posted_date.strip()[:10]
        posted_ts = time.mktime(time.strptime(date_str, "%Y-%m-%d"))
        days_old = (time.time() - posted_ts) / 86400.0
        return days_old > max_days
    except Exception:
        return False


def extract_required_years(text: str) -> Optional[int]:
    """Phase 5: Extract required years of experience from title or description."""
    if not text:
        return None
    low = text.lower()
    m = re.search(r"(\d{1,2})\s*\+?\s*(?:years|yrs|yr)\b", low)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None


def is_experience_mismatched(user_years: Optional[int], query_title: str, job_title: str, job_description: str) -> bool:
    """Phase 5: Reject severe experience mismatches (e.g. Junior searching for Principal/Director/10+ yrs)."""
    q_low = (query_title or "").lower()
    j_low = (job_title or "").lower()
    combined = f"{j_low} {job_description.lower()}"

    is_junior_query = "junior" in q_low or "jr" in q_low or (user_years is not None and user_years <= 1)
    is_senior_query = "senior" in q_low or "sr" in q_low or (user_years is not None and user_years >= 5)

    # If user searched Junior, reject Lead/Director/Principal/10+ yrs
    if is_junior_query:
        if any(term in j_low for term in ["principal", "director", "head of", "vp", "chief"]):
            return True
        req_yrs = extract_required_years(combined)
        if req_yrs is not None and req_yrs >= 8:
            return True

    # If user searched Senior, reject Intern/Trainee
    if is_senior_query:
        if "intern" in j_low or "trainee" in j_low:
            return True

    return False


def is_employment_type_mismatched(wanted_work_types: list[str], job_remote_type: str, job_type: str) -> bool:
    """Phase 6: Reject job type mismatch (e.g. if user requested Internship, don't return full-time jobs)."""
    if not wanted_work_types:
        return False

    wanted_low = [w.lower() for w in wanted_work_types]
    job_type_low = (job_type or "").lower()

    if any("intern" in w for w in wanted_low):
        if "full" in job_type_low and "intern" not in job_type_low:
            return True

    return False


def normalize_location_string(loc: str) -> str:
    """Phase 7: Normalize remote & location names."""
    if not loc:
        return ""
    l = loc.strip()
    low = l.lower()

    # Aliases
    if low in ("wfh", "work from home", "anywhere", "worldwide"):
        return "Remote"
    if "bangalore" in low:
        return "Bengaluru, India"
    if "london" in low:
        return "London, UK"

    return l
