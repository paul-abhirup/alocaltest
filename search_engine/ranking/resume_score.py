"""
search_engine/ranking/resume_score.py — Phase 16: Resume Keyword Overlap Scoring.
Applied ONLY AFTER hard title, experience, and job type filters pass.
"""

from typing import Optional
from utils import keyword_overlap_score


def score_resume(resume_text: Optional[str], job_description: str) -> float:
    """Returns keyword overlap score (0.0 to 100.0) between candidate resume and JD."""
    if not resume_text or not job_description:
        return 50.0  # Neutral score if resume text is missing
    return float(keyword_overlap_score(resume_text, job_description))
