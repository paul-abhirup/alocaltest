"""
search_engine/ranking/skill_score.py — Skill Match Scoring Component.
"""

from search_engine.skills import compute_skill_match_score


def score_skills(query_text: str, job_text: str) -> tuple[float, list[str]]:
    """Returns skill match score (0.0 to 100.0) and matched skills list."""
    return compute_skill_match_score(query_text, job_text)
