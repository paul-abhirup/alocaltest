"""
search_engine/ranking/title_score.py — Title Similarity Scoring Component.
"""

from search_engine.title_match import compute_title_similarity


def score_title(query_title: str, job_title: str) -> float:
    """Returns title similarity score (0.0 to 100.0)."""
    return compute_title_similarity(query_title, job_title)
