"""
search_engine/filters.py — Phases 4, 5, 6, 7, 9: Hard Filters (Negative Keywords, Experience, Job Type, Location, Age Cutoff).
"""

from __future__ import annotations
import re
import time
from typing import Optional
from search_engine.config import DEFAULT_NEGATIVE_KEYWORDS, MAX_JOB_AGE_DAYS, EXPERIENCE_BUCKETS, EMPLOYMENT_TYPES
from search_engine.normalizer import normalize_title
from search_engine.resume_match import is_resume_job_mismatched


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
    """Phase 6: Reject job type mismatch."""
    if not wanted_work_types:
        return False

    wanted_low = [w.lower() for w in wanted_work_types]
    job_type_low = (job_type or "").lower()

    # Intern filter: if user wants Internship only, reject full-time non-intern jobs
    if any("intern" in w for w in wanted_low):
        if len(wanted_low) == 1 and "full" in job_type_low and "intern" not in job_type_low:
            return True

    # Contract filter: if user wants Contract only, reject full-time non-contract jobs
    if any("contract" in w for w in wanted_low):
        if len(wanted_low) == 1 and "full" in job_type_low and "contract" not in job_type_low:
            return True

    return False


def is_location_mismatched(job_location: str, query_location: str = "", query_country: str = "", job_remote_type: str = "") -> bool:
    """Phase 7: Location mismatch check.
    Remote/Contract jobs or queries for 'all' countries bypass this filter.
    Onsite/Hybrid jobs with a distinct country mismatch are rejected.
    """
    if not query_country or query_country.lower() == "all":
        return False

    # Remote and Contract jobs are accessible globally or have their own work-type filters
    remote_types = {"Remote (in-country)", "Remote (worldwide)", "Remote — check eligibility", "Contract/Project"}
    if job_remote_type in remote_types:
        return False

    loc_clean = (job_location or "").strip()
    if not loc_clean or loc_clean == "—":
        return False

    low_loc = loc_clean.lower()
    if any(k in low_loc for k in ("worldwide", "anywhere", "remote", "global")):
        return False

    # Check if job location has an explicit country indicator that mismatches query_country
    q_c = query_country.lower().strip()

    # Common country name and major city indicators
    country_indicators = {
        "us": ["united states", "usa", "u.s.", "u.s.a.", ", us", " us", "california", "texas", "new york", "austin", "san francisco", "seattle"],
        "in": ["india", "bengaluru", "bangalore", "mumbai", "delhi", "hyderabad", "pune", "chennai", "noida", "gurgaon"],
        "gb": ["united kingdom", "uk", "u.k.", "england", "scotland", "wales", "london", "manchester", "birmingham"],
        "ca": ["canada", "toronto", "vancouver", "montreal", "ontario", "quebec", "ottawa", "calgary"],
        "de": ["germany", "deutschland", "berlin", "munich", "münchen", "frankfurt", "hamburg", "cologne", "köln"],
        "au": ["australia", "sydney", "melbourne", "brisbane", "perth", "adelaide"],
        "fr": ["france", "paris", "lyon", "marseille", "toulouse"],
        "es": ["spain", "españa", "madrid", "barcelona", "valencia", "seville"],
        "it": ["italy", "italia", "rome", "roma", "milan", "milano"],
        "br": ["brazil", "brasil", "são paulo", "sao paulo", "rio de janeiro"],
        "mx": ["mexico", "méxico", "mexico city", "guadalajara", "monterrey"],
        "nl": ["netherlands", "amsterdam", "rotterdam", "the hague", "utrecht"],
        "pl": ["poland", "polska", "warsaw", "warszawa", "krakow", "kraków", "wroclaw"],
        "sg": ["singapore"],
        "za": ["south africa", "johannesburg", "cape town", "durban", "pretoria"],
    }

    # Check if loc indicates a foreign country that is known and distinct from query_country
    for c_code, indicators in country_indicators.items():
        if c_code != q_c:
            for ind in indicators:
                if re.search(r"\b" + re.escape(ind) + r"\b", low_loc):
                    # Make sure the current query country's indicators aren't also present
                    q_inds = country_indicators.get(q_c, [q_c])
                    if not any(re.search(r"\b" + re.escape(qi) + r"\b", low_loc) for qi in q_inds):
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
