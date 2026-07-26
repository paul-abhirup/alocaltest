"""
search_engine/ranking/freshness.py — Phase 9: Posting Freshness Decay Scoring.
"""

import time


def score_freshness(posted_date: str) -> float:
    """Calculate freshness decay score (0.0 to 100.0) based on posting date."""
    if not posted_date or not posted_date.strip():
        return 50.0

    try:
        date_str = posted_date.strip()[:10]
        posted_ts = time.mktime(time.strptime(date_str, "%Y-%m-%d"))
        days_old = max(0.0, (time.time() - posted_ts) / 86400.0)

        if days_old <= 0.5:
            return 100.0
        if days_old <= 1.0:
            return 98.0
        if days_old <= 3.0:
            return 95.0
        if days_old <= 7.0:
            return 85.0
        if days_old <= 14.0:
            return 65.0
        if days_old <= 30.0:
            return 35.0
        if days_old <= 45.0:
            return 10.0
        return 0.0
    except Exception:
        return 50.0
