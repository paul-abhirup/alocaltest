"""
search_engine/ranking/company_score.py — Phase 10: Company Quality Scoring.
Prioritizes direct employers over recruiting agencies.
"""

import re

_RECRUITER_PATTERNS = [
    r"\brecruit(ing|ment|er|ers)?\b",
    r"\bstaffing\b",
    r"\bagency\b",
    r"\bconsulting\b",
    r"\bheadhunt(ers|ing)?\b",
    r"\btalent acquisition\b",
    r"\bresource management\b"
]


def score_company(company_name: str) -> float:
    """Returns company quality score (50.0 for agency, 100.0 for direct employer)."""
    if not company_name or not company_name.strip():
        return 70.0

    comp_low = company_name.lower()
    for pat in _RECRUITER_PATTERNS:
        if re.search(pat, comp_low):
            return 50.0   # Lower ranking for recruiters/agencies, but do not remove

    return 100.0          # Direct employer / verified company
