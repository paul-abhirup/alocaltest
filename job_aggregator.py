"""
job_aggregator.py — Phase 1 Job Aggregator engine (framework-agnostic).

Compliant job search over official/free APIs only (NO scraping), with ZERO LLM calls.
The Streamlit UI (app.py) imports `search_jobs()`; `api_server.py` can reuse it later
without a rewrite. Design & test matrix: docs/JOB_AGGREGATOR_PLAN.md.

Sources:
  Tier A (keyless, always on):  Remotive, Arbeitnow, The Muse
  Tier B (key-gated):           Adzuna  (ADZUNA_APP_ID + ADZUNA_APP_KEY)
                                JSearch (JSEARCH_API_KEY)
                                Jooble   (JOOBLE_API_KEY)
                                Findwork (FINDWORK_API_KEY)
"""
from __future__ import annotations

import os
import dotenv
dotenv.load_dotenv()
import re
import copy
import time
import html
import hashlib
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Optional

import requests

from utils import keyword_overlap_score

from search_engine.normalizer import generate_search_variants, normalize_title
from search_engine.title_match import is_title_relevant, compute_title_similarity
from search_engine.filters import (
    has_negative_keyword, is_job_expired, is_experience_mismatched,
    is_employment_type_mismatched, is_location_mismatched, normalize_location_string,
)
from search_engine.deduplication import deduplicate_jobs
from search_engine.explainability import generate_explainability
from search_engine.ranking.scorer import calculate_composite_score
from search_engine.resume_match import is_resume_job_mismatched
from search_engine.config import GENERIC_ROLE_NOUNS

logger = logging.getLogger(__name__)

# ----------------------------- Tunables ---------------------------------------
DEFAULT_TIMEOUT = 12           # seconds per source request (multi-page sources need more)
MAX_RESULTS_PER_SOURCE = 150
MAX_VARIANTS_PER_SOURCE = 2    # search variants fan-out per source (recall boost)
MAX_DESC_CHARS = 8000         # cap description length before scoring (perf)
USER_AGENT = "CVOLVE-PRO-JobAggregator/1.0 (+https://cvolvepro.com)"

# ----------------------------- Work-type labels -------------------------------
REMOTE_IN_COUNTRY = "Remote (in-country)"
REMOTE_WORLDWIDE = "Remote (worldwide)"
REMOTE_UNKNOWN = "Remote — check eligibility"
ONSITE_HYBRID = "Onsite/Hybrid"
CONTRACT = "Contract/Project"
_REMOTE_FAMILY = {REMOTE_IN_COUNTRY, REMOTE_WORLDWIDE, REMOTE_UNKNOWN}

# ----------------------------- Country & Currency Mappings -------------------
# Markets queried when the user picks "All Countries" (Adzuna fan-out).
_ADZUNA_ALL_COUNTRIES = ["us", "gb", "in", "de", "ca", "au", "fr", "es", "nl", "br"]

_ADZUNA_CURRENCY = {
    "gb": "£", "us": "$", "ca": "C$", "au": "A$", "in": "₹",
    "de": "€", "fr": "€", "nl": "€", "es": "€", "it": "€",
    "ie": "€", "at": "€", "be": "€", "pt": "€", "fi": "€", "gr": "€",
    "cy": "€", "ee": "€", "lt": "€", "lv": "€", "lu": "€", "mt": "€",
    "sk": "€", "si": "€", "hr": "€",
    "br": "R$", "mx": "Mex$", "pl": "zł", "za": "R", "nz": "NZ$", "sg": "S$",
    "ch": "CHF ", "se": "kr", "no": "kr", "dk": "kr", "is": "kr",
    "jp": "¥", "hk": "HK$", "ae": "AED ", "sa": "SAR ", "qa": "QAR ", "kw": "KWD ",
    "bh": "BHD ", "om": "OMR ", "jo": "JOD ", "eg": "EGP ", "ng": "₦", "ke": "KSh ",
    "gh": "GH₵", "pk": "PKR ", "bd": "৳", "lk": "LKR ", "np": "NPR ",
    "ph": "₱", "my": "RM", "id": "Rp", "th": "฿", "vn": "₫", "uy": "$U",
    "kr": "₩", "tw": "NT$", "tr": "TRY ", "il": "₪", "ru": "₽",
    "ar": "ARG$", "cl": "CLP$", "co": "COL$", "pe": "S/", "py": "₲", "bo": "Bs.",
    "cr": "₡", "do": "RD$", "ec": "$", "gt": "Q", "pa": "B/.", "pr": "$",
    "cz": "Kč", "ro": "lei", "hu": "Ft", "bg": "лв", "rs": "дин.", "ua": "₴",
    "ma": "MAD ", "tn": "TND ", "kz": "₸", "ge": "₾", "mu": "₨",
}

ADZUNA_SUPPORTED_COUNTRIES = {
    "at", "au", "be", "br", "ca", "ch", "de", "es", "fr", "gb",
    "in", "it", "mx", "nl", "nz", "pl", "ru", "sg", "us", "za",
}

COUNTRY_FULL_NAMES = {
    "ae": "United Arab Emirates",
    "ar": "Argentina",
    "at": "Austria",
    "au": "Australia",
    "bd": "Bangladesh",
    "be": "Belgium",
    "bg": "Bulgaria",
    "bh": "Bahrain",
    "bo": "Bolivia",
    "br": "Brazil",
    "ca": "Canada",
    "ch": "Switzerland",
    "cl": "Chile",
    "co": "Colombia",
    "cr": "Costa Rica",
    "cy": "Cyprus",
    "cz": "Czech Republic",
    "de": "Germany",
    "dk": "Denmark",
    "do": "Dominican Republic",
    "ec": "Ecuador",
    "ee": "Estonia",
    "eg": "Egypt",
    "es": "Spain",
    "fi": "Finland",
    "fr": "France",
    "gb": "United Kingdom",
    "ge": "Georgia",
    "gh": "Ghana",
    "gr": "Greece",
    "gt": "Guatemala",
    "hk": "Hong Kong",
    "hr": "Croatia",
    "hu": "Hungary",
    "id": "Indonesia",
    "ie": "Ireland",
    "il": "Israel",
    "in": "India",
    "is": "Iceland",
    "it": "Italy",
    "jo": "Jordan",
    "jp": "Japan",
    "ke": "Kenya",
    "kr": "South Korea",
    "kw": "Kuwait",
    "kz": "Kazakhstan",
    "lk": "Sri Lanka",
    "lt": "Lithuania",
    "lu": "Luxembourg",
    "lv": "Latvia",
    "ma": "Morocco",
    "mt": "Malta",
    "mu": "Mauritius",
    "mx": "Mexico",
    "my": "Malaysia",
    "ng": "Nigeria",
    "nl": "Netherlands",
    "no": "Norway",
    "np": "Nepal",
    "nz": "New Zealand",
    "om": "Oman",
    "pa": "Panama",
    "pe": "Peru",
    "ph": "Philippines",
    "pk": "Pakistan",
    "pl": "Poland",
    "pr": "Puerto Rico",
    "pt": "Portugal",
    "py": "Paraguay",
    "qa": "Qatar",
    "ro": "Romania",
    "rs": "Serbia",
    "sa": "Saudi Arabia",
    "se": "Sweden",
    "sg": "Singapore",
    "si": "Slovenia",
    "sk": "Slovakia",
    "th": "Thailand",
    "tn": "Tunisia",
    "tr": "Turkey",
    "tw": "Taiwan",
    "ua": "Ukraine",
    "us": "United States",
    "uy": "Uruguay",
    "vn": "Vietnam",
    "za": "South Africa",
}

# Sorted alphabetically by country name; "all" stays on top as the default option.
SUPPORTED_COUNTRIES = {
    "all": "🌍 All Countries (Global)",
    "ar": "🇦🇷 Argentina",
    "au": "🇦🇺 Australia",
    "at": "🇦🇹 Austria",
    "bh": "🇧🇭 Bahrain",
    "bd": "🇧🇩 Bangladesh",
    "be": "🇧🇪 Belgium",
    "bo": "🇧🇴 Bolivia",
    "br": "🇧🇷 Brazil",
    "bg": "🇧🇬 Bulgaria",
    "ca": "🇨🇦 Canada",
    "cl": "🇨🇱 Chile",
    "co": "🇨🇴 Colombia",
    "cr": "🇨🇷 Costa Rica",
    "hr": "🇭🇷 Croatia",
    "cy": "🇨🇾 Cyprus",
    "cz": "🇨🇿 Czech Republic",
    "dk": "🇩🇰 Denmark",
    "do": "🇩🇴 Dominican Republic",
    "ec": "🇪🇨 Ecuador",
    "eg": "🇪🇬 Egypt",
    "ee": "🇪🇪 Estonia",
    "fi": "🇫🇮 Finland",
    "fr": "🇫🇷 France",
    "ge": "🇬🇪 Georgia",
    "de": "🇩🇪 Germany",
    "gh": "🇬🇭 Ghana",
    "gr": "🇬🇷 Greece",
    "gt": "🇬🇹 Guatemala",
    "hk": "🇭🇰 Hong Kong",
    "hu": "🇭🇺 Hungary",
    "is": "🇮🇸 Iceland",
    "in": "🇮🇳 India",
    "id": "🇮🇩 Indonesia",
    "ie": "🇮🇪 Ireland",
    "il": "🇮🇱 Israel",
    "it": "🇮🇹 Italy",
    "jp": "🇯🇵 Japan",
    "jo": "🇯🇴 Jordan",
    "kz": "🇰🇿 Kazakhstan",
    "ke": "🇰🇪 Kenya",
    "kw": "🇰🇼 Kuwait",
    "lv": "🇱🇻 Latvia",
    "lt": "🇱🇹 Lithuania",
    "lu": "🇱🇺 Luxembourg",
    "my": "🇲🇾 Malaysia",
    "mt": "🇲🇹 Malta",
    "mu": "🇲🇺 Mauritius",
    "mx": "🇲🇽 Mexico",
    "ma": "🇲🇦 Morocco",
    "np": "🇳🇵 Nepal",
    "nl": "🇳🇱 Netherlands",
    "nz": "🇳🇿 New Zealand",
    "ng": "🇳🇬 Nigeria",
    "no": "🇳🇴 Norway",
    "om": "🇴🇲 Oman",
    "pk": "🇵🇰 Pakistan",
    "pa": "🇵🇦 Panama",
    "py": "🇵🇾 Paraguay",
    "pe": "🇵🇪 Peru",
    "ph": "🇵🇭 Philippines",
    "pl": "🇵🇱 Poland",
    "pt": "🇵🇹 Portugal",
    "pr": "🇵🇷 Puerto Rico",
    "qa": "🇶🇦 Qatar",
    "ro": "🇷🇴 Romania",
    "sa": "🇸🇦 Saudi Arabia",
    "rs": "🇷🇸 Serbia",
    "sg": "🇸🇬 Singapore",
    "sk": "🇸🇰 Slovakia",
    "si": "🇸🇮 Slovenia",
    "za": "🇿🇦 South Africa",
    "kr": "🇰🇷 South Korea",
    "es": "🇪🇸 Spain",
    "lk": "🇱🇰 Sri Lanka",
    "se": "🇸🇪 Sweden",
    "ch": "🇨🇭 Switzerland",
    "tw": "🇹🇼 Taiwan",
    "th": "🇹🇭 Thailand",
    "tn": "🇹🇳 Tunisia",
    "tr": "🇹🇷 Turkey",
    "ae": "🇦🇪 UAE",
    "ua": "🇺🇦 Ukraine",
    "gb": "🇬🇧 United Kingdom",
    "us": "🇺🇸 United States",
    "uy": "🇺🇾 Uruguay",
    "vn": "🇻🇳 Vietnam",
}


class SourceAuthError(Exception):
    """Raised when a keyed source rejects our credentials (401/403)."""


# ============================== Data models ===================================
@dataclass
class SearchQuery:
    title: str
    years_experience: Optional[int] = None
    location: str = ""
    geography: str = ""                    # free-text region hint (display/context)
    work_types: list[str] = field(default_factory=list)   # filter; empty = all
    country: str = "gb"                    # Adzuna country code
    limit: int = MAX_RESULTS_PER_SOURCE

    def cache_key(self, source_ids: list[str]) -> str:
        """User-independent key. Excludes work_types (filtered post-cache) and YoE
        (a scoring nudge, not a fetch parameter) so filter tweaks reuse the cache."""
        raw = "|".join([
            self.title.strip().lower(),
            self.location.strip().lower(),
            self.geography.strip().lower(),
            self.country.lower(),
            ",".join(sorted(source_ids)),
        ])
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class Job:
    title: str
    company: str
    location: str
    remote_type: str
    job_type: str
    url: str
    source: str
    source_name: str
    posted_date: str = ""
    description: str = ""             # HTML-stripped plain text
    salary: Optional[str] = None
    match_score: Optional[int] = None
    seniority: Optional[str] = None   # inferred level hint (YoE soft signal)
    also_on: list[str] = field(default_factory=list)  # other sources it appeared on
    why_matched: list[str] = field(default_factory=list)
    matched_skills: list[str] = field(default_factory=list)
    relevance_score: Optional[int] = None
    title_similarity: Optional[float] = None

    @property
    def id(self) -> str:
        return self.dedupe_key()

    def dedupe_key(self) -> str:
        u = _normalize_url(self.url)
        if u:
            return "url:" + u
        return "tc:" + self.title.strip().lower() + "|" + self.company.strip().lower()

    def completeness(self) -> int:
        return sum(bool(x) for x in (self.salary, self.posted_date, self.description))


# ============================== Text helpers ==================================
class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data):
        self._parts.append(data)

    def text(self):
        return "".join(self._parts)


def strip_html(raw: str) -> str:
    """Convert source HTML (Remotive etc.) to clean plain text, entity-decoded and
    whitespace-collapsed, capped at MAX_DESC_CHARS. Never let raw HTML reach the
    scorer (pollutes keywords) or the UI (XSS)."""
    if not raw:
        return ""
    try:
        parser = _HTMLStripper()
        parser.feed(raw)
        text = parser.text()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_DESC_CHARS]


def _normalize_url(url: str) -> str:
    if not url:
        return ""
    return url.split("?", 1)[0].split("#", 1)[0].rstrip("/").lower()


# ============================== YoE soft signal ===============================
_SENIORITY_PATTERNS = [
    (r"\b(intern|internship)\b", "Intern"),
    (r"\b(junior|jr\.?|entry[- ]level|graduate|associate)\b", "Junior"),
    (r"\b(senior|sr\.?|lead|principal|staff)\b", "Senior"),
    (r"\b(head of|director|\bvp\b|chief|c-level)\b", "Lead+"),
]


def _parse_required_years(text: str) -> Optional[int]:
    """Best-effort 'N+ years' extraction from a JD. Local string parsing only."""
    m = re.search(r"(\d{1,2})\s*\+?\s*(?:years|yrs|yr)\b", text.lower())
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def infer_seniority(text: str) -> Optional[str]:
    """Rough level hint for display (never used to hide jobs)."""
    yrs = _parse_required_years(text)
    if yrs is not None:
        if yrs <= 1:
            return "Junior"
        if yrs <= 4:
            return "Mid"
        if yrs <= 7:
            return "Senior"
        return "Senior+"
    low = text.lower()
    for pattern, label in _SENIORITY_PATTERNS:
        if re.search(pattern, low):
            return label
    return None


def _yoe_adjustment(user_years: Optional[int], jd_text: str) -> int:
    """Small bounded nudge (−10..+5) so the match score stays the dominant signal."""
    if user_years is None:
        return 0
    required = _parse_required_years(jd_text)
    if required is None:
        return 0
    gap = required - user_years
    if gap <= 0:
        return 5 if gap >= -3 else 2      # meets/slightly exceeds vs very overqualified
    if gap <= 2:
        return 0
    return -min(10, (gap - 2) * 3)        # job wants notably more experience



# ============================== Source adapters ===============================
class SourceAdapter:
    source = ""
    source_name = ""
    # How many title variants this source searches. Slow / high-fan-out sources
    # cap this to stay responsive; cheap single-request sources use the default.
    max_variants = MAX_VARIANTS_PER_SOURCE + 1

    def enabled(self) -> bool:
        return True

    def fetch(self, query: SearchQuery) -> list[dict]:
        raise NotImplementedError

    def normalize(self, raw: dict) -> Optional[Job]:
        raise NotImplementedError

    def _request(self, url, *, params=None, method="GET", json_body=None,
                  headers=None, max_retries: int = 2) -> dict:
        req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if headers:
            req_headers.update(headers)
        last_exc = None
        for attempt in range(max_retries + 1):
            try:
                resp = requests.request(method, url, params=params, json=json_body,
                                        headers=req_headers, timeout=DEFAULT_TIMEOUT)
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.HTTPError as e:
                last_exc = e
                status = getattr(e.response, "status_code", None) if e.response is not None else None
                if status in (429, 500, 502, 503, 504) and attempt < max_retries:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                raise
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                last_exc = e
                if attempt < max_retries:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                raise
        raise last_exc  # unreachable but satisfies type checker


class RemotiveAdapter(SourceAdapter):
    source = "remotive"
    source_name = "Remotive"
    BASE = "https://remotive.com/api/remote-jobs"

    def fetch(self, query):
        # Remotive is a remote-only board: nothing to offer when the user
        # explicitly wants only Onsite/Hybrid jobs.
        wants = set(query.work_types or [])
        if wants and ONSITE_HYBRID in wants and not (wants & _REMOTE_FAMILY):
            return []
        data = self._request(self.BASE, params={"search": query.title, "limit": query.limit})
        return (data.get("jobs") or [])[: query.limit * 2]

    def normalize(self, raw):
        title = (raw.get("title") or "").strip()
        if not title:
            return None
        cand = (raw.get("candidate_required_location") or "").strip()
        remote_type = REMOTE_UNKNOWN
        low = cand.lower()
        if any(k in low for k in ("worldwide", "anywhere", "global", "emea")):
            remote_type = REMOTE_WORLDWIDE
        elif cand and "remote" not in low and cand != "—":
            remote_type = REMOTE_IN_COUNTRY
        job_type = (raw.get("job_type") or "").replace("_", " ").strip() or "—"
        return Job(
            title=title,
            company=(raw.get("company_name") or "").strip(),
            location=cand or "Remote",
            remote_type=remote_type,
            job_type=job_type,
            url=raw.get("url") or "",
            source=self.source, source_name=self.source_name,
            posted_date=(raw.get("publication_date") or "")[:10],
            description=strip_html(raw.get("description") or ""),
            salary=(raw.get("salary") or None),
        )


class ArbeitnowAdapter(SourceAdapter):
    source = "arbeitnow"
    source_name = "Arbeitnow"
    BASE = "https://www.arbeitnow.com/api/job-board-api"

    def fetch(self, query):
        # Arbeitnow has no server-side search param → page the whole board and
        # filter client-side by title/tags.
        all_jobs = []
        q = query.title.lower().strip()
        tokens = [t for t in re.split(r"[\s/,-]+", q) if len(t) > 2]
        if not tokens:
            tokens = [q]
        # Match on ANY distinctive (non-generic) token — e.g. just "python" from
        # "python developer" — so jobs whose tags carry the tech still surface.
        # Generic role nouns ("developer") alone never drive a match.
        distinctive = [t for t in tokens if t not in GENERIC_ROLE_NOUNS] or tokens
        for page in range(1, 4):
            try:
                data = self._request(self.BASE, params={"page": page})
            except requests.HTTPError as e:
                code = e.response.status_code if e.response is not None else None
                if code in (429, 500, 502, 503, 504):
                    logger.warning("Arbeitnow rate-limited/overloaded on page %d — keeping page(s) so far", page)
                    break        # rate-limited mid-pagination: keep what we already fetched
                raise
            except Exception as e:
                logger.warning("Arbeitnow fetch failed page %d: %s", page, e)
                break
            jobs = data.get("data") or []
            if not jobs:
                break
            all_jobs.extend(jobs)
            if not (data.get("links") or {}).get("next"):
                break

        def hay(j):
            return (j.get("title", "") + " " + " ".join(j.get("tags", []) or [])).lower()
        matched = [j for j in all_jobs if any(t in hay(j) for t in distinctive)]
        return matched[: query.limit * 2]

    def normalize(self, raw):
        title = (raw.get("title") or "").strip()
        if not title:
            return None
        is_remote = bool(raw.get("remote"))
        job_types = raw.get("job_types") or []
        is_contract = any(t.lower() in ("contract", "freelance") for t in job_types)
        if is_remote:
            remote_type = REMOTE_UNKNOWN
        elif is_contract:
            remote_type = CONTRACT
        else:
            remote_type = ONSITE_HYBRID
        created = raw.get("created_at")
        if isinstance(created, (int, float)):
            posted = time.strftime("%Y-%m-%d", time.gmtime(created))
        else:
            posted = (created or "")[:10]
        return Job(
            title=title,
            company=(raw.get("company_name") or "").strip(),
            location=(raw.get("location") or "").strip() or ("Remote" if is_remote else "—"),
            remote_type=remote_type,
            job_type=", ".join(job_types) if job_types else "—",
            url=raw.get("url") or "",
            source=self.source, source_name=self.source_name,
            posted_date=posted,
            description=strip_html(raw.get("description") or ""),
        )


class AdzunaAdapter(SourceAdapter):
    source = "adzuna"
    source_name = "Adzuna"
    max_variants = 1      # All-Countries fan-out (10 markets × pages) is already expensive; main title only
    BASE = "https://api.adzuna.com/v1/api/jobs"

    def __init__(self):
        self.app_id = os.getenv("ADZUNA_APP_ID")
        self.app_key = os.getenv("ADZUNA_APP_KEY")
        self._currency = ""

    def enabled(self):
        return bool(self.app_id and self.app_key)

    def fetch(self, query):
        country = (query.country or "gb").lower()
        if country != "all" and country not in ADZUNA_SUPPORTED_COUNTRIES:
            # Adzuna API only operates in 20 supported countries. Skip to avoid redundant 400/404 errors.
            return []
        # "All Countries" queries top regional markets.
        target_countries = _ADZUNA_ALL_COUNTRIES if country == "all" else [country]
        per_page = min(50, max(20, query.limit // len(target_countries)))
        # Single-country searches page through 3 pages for volume; the All-Countries
        # fan-out already spreads thin across 10 markets, so keep it at 2 pages there
        # to bound request count and latency.
        pages = 2 if country == "all" else 3
        all_results = []
        wants = set(query.work_types or [])
        wants_remote = bool(wants & _REMOTE_FAMILY) and ONSITE_HYBRID not in wants
        what = query.title
        if wants_remote:
            what = f"{what} remote"
        for c in target_countries:
            self._currency = _ADZUNA_CURRENCY.get(c, "")
            for page in range(1, pages + 1):
                params = {
                    "app_id": self.app_id, "app_key": self.app_key,
                    "results_per_page": per_page,
                    "what": what,
                    "content-type": "application/json",
                }
                if query.location and query.location.lower() not in ("remote", "worldwide"):
                    params["where"] = query.location
                url = f"{self.BASE}/{c}/search/{page}"
                try:
                    data = self._request(url, params=params)
                    all_results.extend(data.get("results") or [])
                except requests.HTTPError as e:
                    code = e.response.status_code if e.response is not None else None
                    if code in (401, 403):
                        raise SourceAuthError(f"Adzuna auth failed ({code}) — check API key") from e
                    logger.warning("Adzuna fetch failed for country %s page %d: %s", c, page, e)
                    break
        return all_results[: query.limit * 2]

    def _fmt_salary(self, smin, smax):
        cur = self._currency
        try:
            smin = int(smin) if smin else 0
            smax = int(smax) if smax else 0
        except (TypeError, ValueError):
            return None
        if smin and smax and smin != smax:
            return f"{cur}{smin:,}–{cur}{smax:,}"
        if smin:
            return f"{cur}{smin:,}+"
        if smax:
            return f"up to {cur}{smax:,}"
        return None

    def normalize(self, raw):
        title = (raw.get("title") or "").strip()
        if not title:
            return None
        location = ((raw.get("location") or {}).get("display_name") or "").strip()
        contract_time = (raw.get("contract_time") or "")   # full_time / part_time
        contract_type = (raw.get("contract_type") or "")   # permanent / contract
        job_type = " ".join(x.replace("_", " ") for x in (contract_time, contract_type) if x).strip() or "—"
        if "remote" in (title + " " + location).lower():
            remote_type = REMOTE_UNKNOWN
        elif "contract" in contract_type.lower():
            remote_type = CONTRACT
        else:
            remote_type = ONSITE_HYBRID
        return Job(
            title=title,
            company=((raw.get("company") or {}).get("display_name") or "").strip(),
            location=location or "—",
            remote_type=remote_type,
            job_type=job_type,
            url=raw.get("redirect_url") or "",
            source=self.source, source_name=self.source_name,
            posted_date=(raw.get("created") or "")[:10],
            description=strip_html(raw.get("description") or ""),
            salary=self._fmt_salary(raw.get("salary_min"), raw.get("salary_max")),
        )


class JSearchAdapter(SourceAdapter):
    source = "jsearch"
    source_name = "JSearch"
    max_variants = 2      # each request is slow (8–13s); avoid the 3rd variant
    BASE = "https://jsearch.p.rapidapi.com/search-v2"

    def __init__(self):
        self.api_key = os.getenv("JSEARCH_API_KEY")

    def enabled(self):
        if not self.api_key:
            self.api_key = os.getenv("JSEARCH_API_KEY")
        return bool(self.api_key)

    # Language hints for non-English markets — improves JSearch recall by
    # requesting results in the local language alongside English.
    _JSEARCH_LANG = {
        "de": "de", "fr": "fr", "es": "es", "it": "it", "pt": "pt",
        "nl": "nl", "pl": "pl", "br": "pt", "mx": "es", "ar": "es",
        "cl": "es", "co": "es", "pe": "es", "ec": "es", "cr": "es",
        "gt": "es", "do": "es", "bo": "es", "py": "es", "uy": "es",
        "jp": "ja", "kr": "ko", "th": "th", "vn": "vi", "id": "id",
        "tr": "tr", "cz": "cs", "ro": "ro", "hu": "hu", "bg": "bg",
        "rs": "sr", "ua": "uk", "se": "sv", "no": "no", "dk": "da",
        "fi": "fi", "gr": "el", "il": "he", "eg": "ar", "sa": "ar",
        "ae": "ar", "qa": "ar", "kw": "ar", "bh": "ar", "om": "ar",
        "jo": "ar", "ma": "fr", "tn": "fr",
    }

    def fetch(self, query):
        if not self.api_key:
            self.api_key = os.getenv("JSEARCH_API_KEY")
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "X-RapidAPI-Key": self.api_key,
            "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
        }
        q_text = query.title
        if query.location:
            q_text += f" in {query.location}"

        # Request up to 5 pages (≈50 results) per query variant via a single request so
        # JSearch volume isn't capped at 10 rows while keeping each request fast enough.
        params = {"query": q_text, "page": "1", "num_pages": "5"}
        cc = (query.country or "").lower()
        if cc and cc != "all":
            wants = set(query.work_types or [])
            # When only worldwide remote is selected, don't lock by country
            if not (REMOTE_WORLDWIDE in wants and REMOTE_IN_COUNTRY not in wants
                    and ONSITE_HYBRID not in wants):
                params["country"] = cc
            # Add language hint for non-English markets
            lang = self._JSEARCH_LANG.get(cc)
            if lang:
                params["language"] = lang
        wants = set(query.work_types or [])
        if wants & _REMOTE_FAMILY and ONSITE_HYBRID not in wants:
            params["remote_jobs_only"] = "true"
        try:
            resp = requests.get(self.BASE, headers=headers, params=params, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"JSearch API fetch failed: {e}")
            return []
        data = resp.json()
        raw_data = data.get("data")
        if isinstance(raw_data, dict):
            jobs = raw_data.get("jobs") or []
        elif isinstance(raw_data, list):
            jobs = raw_data
        else:
            jobs = []
        return jobs[: max(query.limit * 2, 20)]

    def normalize(self, raw):
        title = (raw.get("job_title") or "").strip()
        if not title:
            return None
        company = (raw.get("employer_name") or "").strip()
        city = raw.get("job_city") or ""
        country = raw.get("job_country") or ""
        location = ", ".join(filter(None, [city, country])) or "—"
        desc = strip_html(raw.get("job_description") or "")
        
        is_remote = bool(raw.get("job_is_remote")) or "remote" in title.lower() or "remote" in location.lower() or "remote" in desc[:300].lower()
        if is_remote:
            loc_low = location.lower()
            has_city = bool(raw.get("job_city"))
            has_country = bool(raw.get("job_country"))
            if not has_city and not has_country:
                remote_type = REMOTE_WORLDWIDE
            elif any(k in loc_low for k in ("worldwide", "anywhere", "global", "multiple countries", "emea")):
                remote_type = REMOTE_WORLDWIDE
            else:
                remote_type = REMOTE_IN_COUNTRY
        else:
            remote_type = ONSITE_HYBRID
        posted = (raw.get("job_posted_at_datetime_utc") or "")[:10]

        salary = None
        min_s = raw.get("job_min_salary")
        max_s = raw.get("job_max_salary")
        cur = raw.get("job_salary_currency") or "$"
        if min_s and max_s:
            salary = f"{cur}{int(min_s):,}–{cur}{int(max_s):,}"

        url = raw.get("job_apply_link") or raw.get("job_google_link") or ""

        return Job(
            title=title,
            company=company,
            location=location,
            remote_type=remote_type,
            job_type=(raw.get("job_employment_type") or "").title() or "—",
            url=url,
            source=self.source,
            source_name=self.source_name,
            posted_date=posted,
            description=desc,
            salary=salary,
        )


JOOBLE_COUNTRY_DOMAINS = {
    "us": "https://jooble.org",
    "gb": "https://uk.jooble.org",
    "uk": "https://uk.jooble.org",
}


class JoobleAdapter(SourceAdapter):
    source = "jooble"
    source_name = "Jooble"
    BASE = "https://jooble.org/api"

    def __init__(self):
        self.api_key = os.getenv("JOOBLE_API_KEY")

    def enabled(self):
        return bool(self.api_key)

    def _get_api_url(self, country_code: str) -> str:
        cc = (country_code or "").lower().strip()
        if not cc or cc in ("all", "us"):
            domain = "https://jooble.org"
        elif cc in JOOBLE_COUNTRY_DOMAINS:
            domain = JOOBLE_COUNTRY_DOMAINS[cc]
        else:
            domain = f"https://{cc}.jooble.org"
        return f"{domain}/api/{self.api_key}"

    def fetch(self, query):
        country_raw = (query.country or "").lower().strip()
        c_name = COUNTRY_FULL_NAMES.get(country_raw, query.country or "")
        wants = set(query.work_types or [])
        # When only worldwide remote is selected, don't force a country in location
        if REMOTE_WORLDWIDE in wants and REMOTE_IN_COUNTRY not in wants and ONSITE_HYBRID not in wants:
            loc = query.location or ""
        else:
            loc = query.location or (c_name if query.country and country_raw != "all" else "")
        # "All Countries" fans out across the biggest markets — Jooble returns a
        # far larger, location-sorted pool this way (empty location alone is thin).
        if country_raw == "all":
            buckets = [
                ("https://jooble.org/api", ""),
                ("https://jooble.org/api", "United States"),
                ("https://uk.jooble.org/api", "United Kingdom"),
                ("https://in.jooble.org/api", "India"),
                ("https://de.jooble.org/api", "Germany"),
                ("https://ca.jooble.org/api", "Canada"),
                ("https://au.jooble.org/api", "Australia"),
            ]
        else:
            base_url = self._get_api_url(country_raw).rsplit("/", 1)[0]
            buckets = [(base_url, loc)]
        results = []
        for base_url, b in buckets:
            url = f"{base_url}/{self.api_key}"
            payload = {"keywords": query.title, "location": b, "ResultOnPage": 100}
            try:
                data = self._request(url, method="POST", json_body=payload)
                results.extend(data.get("jobs") or [])
            except Exception as e:
                # If regional domain fails, try fallback to main domain
                if base_url != "https://jooble.org/api":
                    fb_url = f"{self.BASE}/{self.api_key}"
                    fb_payload = {"keywords": query.title, "location": query.location or c_name, "ResultOnPage": 100}
                    try:
                        data = self._request(fb_url, method="POST", json_body=fb_payload)
                        results.extend(data.get("jobs") or [])
                    except Exception as fb_err:
                        logger.warning("Jooble fallback fetch failed (location %r): %s", b, fb_err)
                else:
                    logger.warning("Jooble fetch failed (location %r): %s", b, e)
                continue
        return results[: query.limit * 2]

    def normalize(self, raw):
        title = (raw.get("title") or "").strip()
        if not title:
            return None
        company = (raw.get("company") or "").strip()
        location = (raw.get("location") or "").strip() or "—"
        posted = (raw.get("updated") or "")[:10]
        desc = strip_html(raw.get("snippet") or "")
        salary = raw.get("salary") or None

        return Job(
            title=title,
            company=company,
            location=location,
            remote_type=REMOTE_UNKNOWN if "remote" in location.lower() else ONSITE_HYBRID,
            job_type="Full-time",
            url=raw.get("link") or "",
            source=self.source,
            source_name=self.source_name,
            posted_date=posted,
            description=desc,
            salary=salary,
        )


class FindworkAdapter(SourceAdapter):
    source = "findwork"
    source_name = "Findwork.dev"
    BASE = "https://findwork.dev/api/jobs/"

    def __init__(self):
        self.api_key = os.getenv("FINDWORK_API_KEY")

    def enabled(self):
        return bool(self.api_key)

    def fetch(self, query):
        headers = {"Authorization": f"Token {self.api_key}"}
        params = {"search": query.title, "sort_by": "relevance"}
        wants = set(query.work_types or [])
        if wants & _REMOTE_FAMILY and ONSITE_HYBRID not in wants:
            params["remote"] = "true"
        if query.location and query.location.strip().lower() not in ("remote", "worldwide", "anywhere"):
            params["location"] = query.location
        results = []
        url = self.BASE
        # Paginate via the API's next-links (100 results/page) for real volume.
        for _ in range(3):
            try:
                data = self._request(url, params=params, headers=headers)
            except requests.HTTPError as e:
                code = e.response.status_code if e.response is not None else None
                if code in (401, 403):
                    raise SourceAuthError(f"Findwork auth failed ({code}) — check API key") from e
                if code in (429, 500, 502, 503, 504):
                    logger.warning("Findwork rate-limited/overloaded on %s — keeping page(s) so far", url)
                    break        # rate-limited mid-pagination: keep what we already fetched
                raise
            else:
                results.extend(data.get("results") or [])
                next_url = data.get("next")
                if not next_url:
                    break
                url = next_url           # the next URL already carries its own query params
                params = None
        return results[: query.limit * 2]

    def normalize(self, raw):
        title = (raw.get("role") or "").strip()
        if not title:
            return None
        company = (raw.get("company_name") or "").strip()
        location = (raw.get("location") or "").strip()
        employment = (raw.get("employment_type") or "").strip()
        is_remote = bool(raw.get("remote"))
        posted = (raw.get("date_posted") or "")[:10]
        desc = strip_html(raw.get("text") or "")

        loc_low = location.lower()
        if any(k in loc_low for k in ("worldwide", "anywhere", "global", "emea")):
            remote_type = REMOTE_WORLDWIDE
        elif "remote" in loc_low and re.sub(r"[^a-z]+", "", loc_low) != "remote":
            remote_type = REMOTE_IN_COUNTRY   # geo-bound remote (e.g. "REMOTE (US)")
        elif is_remote:
            remote_type = REMOTE_UNKNOWN      # flagged remote, but doesn't state worldwide
        elif "contract" in employment.lower() or "freelance" in employment.lower():
            remote_type = CONTRACT
        else:
            remote_type = ONSITE_HYBRID

        return Job(
            title=title,
            company=company,
            location=location or "Remote",
            remote_type=remote_type,
            job_type=employment.replace("_", " ").strip() or "—",
            url=raw.get("url") or "",
            source=self.source,
            source_name=self.source_name,
            posted_date=posted,
            description=desc,
        )


class TheMuseAdapter(SourceAdapter):
    source = "themuse"
    source_name = "The Muse"
    BASE = "https://www.themuse.com/api/public/jobs"

    def fetch(self, query):
        # Keyless public API. Searches are token-based server-side; page through a
        # couple of pages for volume.
        all_results = []
        for page in range(1, 5):
            params = {
                "search": query.title,
                "page": str(page),
                "items_per_page": 50,
            }
            if query.location and query.location.lower() not in ("remote", "worldwide"):
                params["location"] = query.location
            try:
                data = self._request(self.BASE, params=params)
            except Exception as e:
                logger.warning("The Muse fetch failed page %d: %s", page, e)
                break
            results = data.get("results") or []
            all_results.extend(results)
            if not results:
                break
        return all_results[: query.limit * 2]

    def normalize(self, raw):
        title = (raw.get("name") or "").strip()
        if not title:
            return None
        company = ((raw.get("company") or {}).get("name") or "").strip()
        locations = [l.get("name", "") for l in raw.get("locations") or [] if l.get("name")]
        location = ", ".join(locations) or "—"
        remote_type = REMOTE_UNKNOWN if any("remote" in l.lower() for l in locations) else ONSITE_HYBRID
        levels = [l.get("name", "") for l in raw.get("levels") or [] if l.get("name")]
        seniority = (levels[0] if levels else None)
        desc = strip_html(raw.get("contents") or "")
        posted = (raw.get("publication_date") or "")[:10]
        return Job(
            title=title,
            company=company,
            location=location,
            remote_type=remote_type,
            job_type="—",
            url=(raw.get("refs") or {}).get("landing_page") or "",
            source=self.source, source_name=self.source_name,
            posted_date=posted,
            description=desc,
            seniority=seniority,
        )


def default_adapters() -> list[SourceAdapter]:
    return [
        JSearchAdapter(),
        AdzunaAdapter(),
        JoobleAdapter(),
        RemotiveAdapter(),
        ArbeitnowAdapter(),
        FindworkAdapter(),
        TheMuseAdapter(),
    ]


# ============================== Caching =======================================
# Module-level, user-independent cache: holds normalized jobs WITHOUT match scores.
_CACHE: dict[str, tuple[float, list[Job]]] = {}
_CACHE_LOCK = threading.Lock()


def _cache_ttl_seconds() -> int:
    try:
        return int(float(os.getenv("JOB_CACHE_TTL_MIN", "30")) * 60)
    except (TypeError, ValueError):
        return 1800


# Fast-path result cache: full scored/filtered/sorted search results, so an
# identical repeat search (same query + same resume) skips re-fetch, re-scoring
# and the LLM re-ranker entirely. Keyed on query + resume content hash (never on
# user identity), so identical users share a hit but different resumes don't.
_RESULT_CACHE: dict[str, tuple[float, dict]] = {}
_RESULT_LOCK = threading.Lock()
_RESULT_CACHE_MAX = 128   # bound memory (each entry holds full job descriptions)


def _result_cache_key(query: SearchQuery, resume_text: Optional[str], target_role: Optional[str]) -> str:
    raw = "|".join([
        query.title.strip().lower(),
        query.location.strip().lower(),
        query.geography.strip().lower(),
        query.country.lower(),
        ",".join(sorted(query.work_types or [])),
        str(query.years_experience if query.years_experience is not None else ""),
        str(query.limit),
        hashlib.sha256((resume_text or "").encode()).hexdigest(),
        (target_role or "").strip().lower(),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
    with _RESULT_LOCK:
        _RESULT_CACHE.clear()


# ============================== URL resolution ================================
# Aggregator redirect domains that forward to arbitrary third-party boards
# (some of which are paywalled). We resolve these at display time so the user
# lands on the actual vacancy/application page.
_REDIRECT_REDUCE_SOURCES = {"adzuna", "jooble"}
_RESOLVED_URL_CACHE: dict[str, str] = {}
_RESOLVED_URL_LOCK = threading.Lock()


def is_paywall_url(url: str) -> bool:
    """True if the URL points at a known aggregator/paywall-style intermediate."""
    if not url:
        return True
    low = url.lower()
    hints = (
        "ladders.", "jobleads.", "ziprecruiter.", "indeed.com", "glassdoor.",
        "adzuna.", "jooble.", "simplyhired.", "monster.com", "careerbuilder.",
        "google.com/search", "nationwidecareers",
    )
    return any(h in low for h in hints)


def _resolve_apply_url(url: str, timeout: int = 6) -> str:
    """Follow redirects and return the final destination URL (cached per URL).

    Uses a light HEAD first (cheap); falls back to GET when the server rejects
    HEAD. Any failure keeps the original URL so a click still works.
    """
    if not url:
        return url
    with _RESOLVED_URL_LOCK:
        cached = _RESOLVED_URL_CACHE.get(url)
    if cached:
        return cached
    final = url
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    for method in ("HEAD", "GET"):
        try:
            resp = requests.request(method, url, headers=headers, timeout=timeout,
                                    allow_redirects=True, stream=True)
            resp.close()
            if resp.status_code < 400 and resp.url:
                final = resp.url
            break
        except Exception:
            continue
    with _RESOLVED_URL_LOCK:
        _RESOLVED_URL_CACHE[url] = final
    return final


def resolve_display_urls(jobs: list[Job], max_jobs: int = 40) -> list[Job]:
    """Resolve redirect URLs for the top jobs in parallel (cheap HEAD requests).

    Only touches sources known to return aggregator redirect links, keeping the
    network cost small. Non-redirect URLs are left untouched. Mutates the passed
    jobs in place and returns them.
    """
    targets = [j for j in jobs if j.source in _REDIRECT_REDUCE_SOURCES and j.url]
    if not targets:
        return jobs
    targets = targets[:max_jobs]
    with ThreadPoolExecutor(max_workers=min(len(targets), 20)) as ex:
        futures = {ex.submit(_resolve_apply_url, j.url): j for j in targets}
        for fut in as_completed(futures):
            job = futures[fut]
            try:
                resolved = fut.result()
            except Exception:
                continue
            if resolved and resolved != job.url:
                if job.url not in job.also_on:
                    job.also_on.insert(0, job.url)
                job.url = resolved
    return jobs


# ============================== Orchestration =================================
def _dedupe(jobs: list[Job]) -> list[Job]:
    kept: dict[str, Job] = {}
    for job in jobs:
        key = job.dedupe_key()
        existing = kept.get(key)
        if existing is None:
            kept[key] = job
        else:
            if job.source_name not in existing.also_on and job.source != existing.source:
                existing.also_on.append(job.source_name)
            # Prefer the richer record; on ties prefer the non-paywall URL.
            job_better = (
                job.completeness() > existing.completeness()
                or (
                    job.completeness() == existing.completeness()
                    and not is_paywall_url(job.url)
                    and is_paywall_url(existing.url)
                )
            )
            if job_better:
                job.also_on = existing.also_on
                if existing.source != job.source and existing.source_name not in job.also_on:
                    job.also_on.append(existing.source_name)
                kept[key] = job
    return list(kept.values())


def _matches_work_type(job: Job, wanted: set[str]) -> bool:
    if job.remote_type in wanted:
        return True
    # "Remote — check eligibility" satisfies any remote request (honest inclusion)
    if job.remote_type == REMOTE_UNKNOWN and (wanted & _REMOTE_FAMILY):
        return True
    # "Remote (in-country)" satisfies "Remote (worldwide)" — when a user
    # wants worldwide remote they expect ALL remote jobs, not exclusively borderless ones.
    if REMOTE_WORLDWIDE in wanted and job.remote_type == REMOTE_IN_COUNTRY:
        return True
    # "Contract/Project" is a valid hit when any remote family is selected
    # (contract roles are often remote)
    if CONTRACT in wanted and job.remote_type == REMOTE_UNKNOWN:
        return True
    return False


def _classify_remote_from_text(title: str, description: str, location: str) -> str:
    """Guess remote type from job text when source doesn't provide a clear signal.

    Only reclassifies when the title or description strongly indicates remote work
    (not just the location field, which sometimes defaults to 'Remote').
    """
    combined = f"{title} {description[:600]}".lower()
    loc_low = (location or "").lower()
    has_remote = "remote" in combined
    has_remote_loc = "remote" in loc_low

    # Strong worldwide signals in title/description
    is_worldwide = has_remote and any(k in combined for k in (
        "worldwide", "anywhere", "global", "multiple countries",
        "work from anywhere", "no location", "fully remote", "100% remote",
        "entirely remote", "completely remote"
    ))
    has_hybrid = "hybrid" in combined
    has_onsite = "on" in combined and ("site" in combined or "premises" in combined)

    if is_worldwide:
        return REMOTE_WORLDWIDE
    if has_remote and not has_hybrid and not has_onsite:
        return REMOTE_UNKNOWN  # title/desc mentions remote, not hybrid/onsite
    if has_remote_loc and not has_remote and not has_hybrid and not has_onsite:
        return REMOTE_IN_COUNTRY  # location says remote, title/desc silent
    return ""


def _title_variants(query: SearchQuery) -> list[str]:
    """Main query title + up to MAX_VARIANTS_PER_SOURCE aliases for recall."""
    variants = generate_search_variants(query.title)
    deduped: list[str] = []
    seen: set[str] = set()
    for v in variants:
        v = (v or "").strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            deduped.append(v)
    return deduped[: MAX_VARIANTS_PER_SOURCE + 1] or [query.title]


def _fetch_source(adapter: SourceAdapter, query: SearchQuery, ttl: int, now: float, variants: Optional[list[str]] = None) -> tuple[list[Job], str]:
    """Fetch a single source across search variants.

    Returns (jobs, status) where status ∈ {"ok", "cached", "stale", "error", "auth"}.
    Each variant is cached under its own key; per-request copies avoid mutating
    the shared cache.
    """
    collected: list[Job] = []
    status = "error"
    max_variants = getattr(adapter, "max_variants", MAX_VARIANTS_PER_SOURCE + 1)
    query_variants = (variants or _title_variants(query))[: max_variants]
    for v_title in query_variants:
        vq = copy.copy(query)
        vq.title = v_title
        ckey = f"{adapter.source}:{vq.cache_key([adapter.source])}"
        with _CACHE_LOCK:
            cached = _CACHE.get(ckey)
        if cached and (now - cached[0]) < ttl:
            collected.extend(copy.deepcopy(cached[1]))
            if status == "error":
                status = "cached"
            continue
        try:
            raw_items = adapter.fetch(vq)
            jobs: list[Job] = []
            for raw in raw_items:
                try:
                    job = adapter.normalize(raw)
                except Exception:
                    logger.exception("normalize failed for %s", adapter.source)
                    continue
                if job and job.title:
                    jobs.append(job)
            with _CACHE_LOCK:
                _CACHE[ckey] = (now, jobs)
            collected.extend(copy.deepcopy(jobs))
            if status in ("error", "cached", "stale"):
                status = "ok"
        except SourceAuthError as e:
            return collected, "auth"
        except Exception as e:
            with _CACHE_LOCK:
                cached = _CACHE.get(ckey)
            if cached:
                collected.extend(copy.deepcopy(cached[1]))
                if status == "error":
                    status = "stale"
            logger.warning("fetch failed for %s (variant %r): %s", adapter.source, v_title, e)
    return collected, status


def search_jobs(query: SearchQuery, resume_text: Optional[str] = None,
                sources: Optional[list[SourceAdapter]] = None,
                target_role: Optional[str] = None) -> dict:
    """Fan out to enabled sources (in parallel, with search variants), dedupe,
    score (per-user), filter, sort.

    Returns:
        {
          "jobs":   list[Job] (filtered + sorted),
          "status": {source_id: "ok"|"cached"|"stale"|"error"|"auth"},
          "counts": {"total": int, "shown": int},
          "empty_reason": None | "no_sources" | "unreachable" | "no_results" | "filtered_out",
        }
    """
    adapters = [a for a in (sources or default_adapters()) if a.enabled()]
    ttl = _cache_ttl_seconds()
    now = time.time()

    # Normalize the request: a "Remote"/"Worldwide" string typed into the Location
    # box is really a work-type intent. Convert it and clear it so sources don't
    # receive it as a bogus geography.
    query = copy.copy(query)
    if query.location and query.location.strip().lower() in ("remote", "worldwide", "anywhere", "work from home", "wfh"):
        work_types = list(query.work_types or [])
        if REMOTE_WORLDWIDE not in work_types:
            work_types.append(REMOTE_WORLDWIDE)
        query.work_types = work_types
        query.location = ""

    # Fast path: an identical repeat search (same query + resume) within the TTL
    # returns the already-scored result as-is. Only used on the default-adapters
    # path — explicit `sources=` (tests, custom sets) always run fresh.
    if sources is None:
        res_key = _result_cache_key(query, resume_text, target_role)
        with _RESULT_LOCK:
            cached = _RESULT_CACHE.get(res_key)
        if cached and (now - cached[0]) < ttl:
            return copy.deepcopy(cached[1])

    collected: list[Job] = []
    status: dict[str, str] = {}
    query_variants = _title_variants(query)

    max_workers = max(4, min(len(adapters), 8))
    if len(adapters) <= 1:
        for adapter in adapters:
            jobs, st = _fetch_source(adapter, query, ttl, now, variants=query_variants)
            collected.extend(jobs)
            status[adapter.source] = st
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_fetch_source, a, query, ttl, now, query_variants): a for a in adapters}
            for fut in as_completed(futures):
                adapter = futures[fut]
                try:
                    jobs, st = fut.result()
                except Exception as e:
                    jobs, st = [], "error"
                    logger.warning("fetch worker failed for %s: %s", adapter.source, e)
                collected.extend(jobs)
                status[adapter.source] = st

    deduped = deduplicate_jobs(_dedupe(collected))

    # Post-classify remote types for jobs where source didn't detect remote keywords.
    # Only touch jobs from sources known to under-report remote, and only when
    # the current classification is ONSITE_HYBRID (meaning no remote signal detected).
    _SOURCES_NEEDING_REMOTE_CHECK = {"adzuna", "jooble", "themuse", "arbeitnow", "findwork"}
    for job in deduped:
        if job.remote_type == ONSITE_HYBRID and job.source in _SOURCES_NEEDING_REMOTE_CHECK:
            guessed = _classify_remote_from_text(job.title, job.description, job.location)
            if guessed:
                job.remote_type = guessed

    # Phase 3-9: Hard Filters (Age Cutoff, Negative Keywords, Title Guardrail, Experience Mismatch, Job Type Mismatch, Domain Mismatch)
    surviving_jobs: list[Job] = []
    for job in deduped:
        if is_job_expired(job.posted_date):
            continue
        if has_negative_keyword(job.title, query.title):
            continue
        if not is_title_relevant(query.title, job.title):
            continue
        if is_experience_mismatched(query.years_experience, query.title, job.title, job.description):
            continue
        if is_employment_type_mismatched(query.work_types, job.remote_type, job.job_type):
            continue
        if is_location_mismatched(job.location, query.location, query.country, job.remote_type):
            continue
        if is_resume_job_mismatched(resume_text, job.title, job.description):
            continue

        surviving_jobs.append(job)

    # Phase 12-16: Modular Composite Scoring & Explainability
    for job in surviving_jobs:
        score_int, title_sim, matched_skills = calculate_composite_score(
            query_title=query.title,
            job=job,
            resume_text=resume_text,
            target_role=target_role
        )
        job.match_score = score_int
        job.relevance_score = score_int
        job.title_similarity = title_sim
        job.matched_skills = matched_skills
        job.seniority = infer_seniority(f"{job.title} {job.description}")
        job.why_matched = generate_explainability(
            job_title=job.title,
            query_title=query.title,
            title_similarity=title_sim,
            matched_skills=matched_skills,
            posted_date=job.posted_date,
            remote_type=job.remote_type,
            match_score=score_int,
            seniority=job.seniority or ""
        )

    if query.work_types:
        wanted = set(query.work_types)
        filtered = [j for j in surviving_jobs if _matches_work_type(j, wanted)]
    else:
        filtered = list(surviving_jobs)

    # Resolve aggregator redirect URLs (Adzuna/Jooble) so users land on the
    # real vacancy page instead of a third-party paywall intermediate.
    resolve_display_urls(filtered, max_jobs=30)

    filtered.sort(
        key=lambda j: (j.match_score if j.match_score is not None else -1, j.posted_date),
        reverse=True,
    )

    # Phase 2 Re-ranking: LLM Top-K Re-ranker
    # Take top 20 candidate jobs and run a batch LLM prompt
    from search_engine.ranking.llm_reranker import evaluate_job_fit_batch
    top_candidates = filtered[:20]
    if top_candidates and resume_text and resume_text.strip():
        llm_scores = evaluate_job_fit_batch(resume_text, query.title, top_candidates, target_role=target_role or "")
        for i, job in enumerate(top_candidates):
            job_id = str(getattr(job, "id", None) or getattr(job, "url", None) or f"job_{i}")
            if job_id in llm_scores:
                # Combine hybrid match score and LLM re-rank score
                llm_score = llm_scores[job_id]
                if isinstance(llm_score, (int, float)):
                    # Let LLM score have a 50% weight on the final match score for the top 20
                    current = job.match_score if job.match_score is not None else 50
                    job.match_score = int(round((current * 0.5) + (llm_score * 0.5)))
                    
        # Re-sort after LLM re-ranking
        filtered.sort(
            key=lambda j: (j.match_score if j.match_score is not None else -1, j.posted_date),
            reverse=True,
        )

    # Distinguish the three empty states so the UI can guide the user.
    empty_reason = None
    if not adapters:
        empty_reason = "no_sources"
    elif not filtered:
        reachable = any(s in ("ok", "cached", "stale") for s in status.values())
        if not reachable:
            empty_reason = "unreachable"
        elif deduped:
            empty_reason = "filtered_out"
        else:
            empty_reason = "no_results"

    result = {
        "jobs": filtered,
        "status": status,
        "counts": {"total": len(deduped), "shown": len(filtered)},
        "empty_reason": empty_reason,
    }

    # Cache the finished result for the fast path. Skip transient empties so a
    # sources-unreachable moment isn't frozen for the whole TTL.
    if sources is None and not (empty_reason == "unreachable" or empty_reason == "no_sources"):
        with _RESULT_LOCK:
            _RESULT_CACHE[res_key] = (now, copy.deepcopy(result))
            if len(_RESULT_CACHE) > _RESULT_CACHE_MAX:
                # Evict the oldest entry — the cache is content-keyed, order is arbitrary.
                oldest = min(_RESULT_CACHE, key=lambda k: _RESULT_CACHE[k][0])
                del _RESULT_CACHE[oldest]

    return result


# ============================== HTML Scraper ===================================
class JobDescriptionHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_blocks: list[str] = []
        self.ignore_tags = {"script", "style", "head", "title", "meta", "link", "noscript", "header", "footer", "nav"}
        self.current_tag_stack: list[str] = []
        self.should_ignore = False

    def handle_starttag(self, tag, attrs):
        self.current_tag_stack.append(tag)
        if tag in self.ignore_tags:
            self.should_ignore = True

    def handle_endtag(self, tag):
        if self.current_tag_stack:
            self.current_tag_stack.pop()
        self.should_ignore = any(t in self.ignore_tags for t in self.current_tag_stack)

    def handle_data(self, data):
        if not self.should_ignore:
            stripped = data.strip()
            if stripped:
                self.text_blocks.append(stripped)

    def get_text(self) -> str:
        return "\n".join(self.text_blocks)


def fetch_full_job_description(url: str) -> Optional[str]:
    """Fetch the original job posting URL and extract readable text from it.
    Returns None if fetching fails or if extracted text is too short.
    """
    if not url:
        return None
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        }
        resp = requests.get(url, headers=headers, timeout=8, allow_redirects=True)
        if resp.status_code != 200:
            return None
        
        parser = JobDescriptionHTMLParser()
        parser.feed(resp.text)
        text = parser.get_text()
        
        # Clean up excessive newlines and whitespace
        text = re.sub(r'\n+', '\n', text)
        text = re.sub(r' +', ' ', text)
        
        final_text = text.strip()[:MAX_DESC_CHARS]
        if len(final_text) > 200:
            return final_text
    except Exception as e:
        logger.warning("Failed to fetch full job description from %s: %s", url, e)
    return None

