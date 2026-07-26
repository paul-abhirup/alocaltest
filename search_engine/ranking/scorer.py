"""
search_engine/ranking/scorer.py — Phase 13: Modular Composite Scoring Orchestrator.
Applies modular weights and computes composite match score.
"""

from __future__ import annotations
from typing import Any, Optional
from search_engine.config import DEFAULT_WEIGHTS, MIN_DESC_LENGTH_FOR_CONFIDENCE
from search_engine.ranking.title_score import score_title
from search_engine.ranking.skill_score import score_skills
from search_engine.ranking.freshness import score_freshness
from search_engine.ranking.company_score import score_company
from search_engine.ranking.resume_score import score_resume
from search_engine.ranking.semantic_score import SemanticScorer


def calculate_composite_score(
    query_title: str,
    job: Any,
    resume_text: Optional[str] = None,
    custom_weights: Optional[dict[str, float]] = None
) -> tuple[int, float, list[str]]:
    """Compute weighted composite relevance score (0 - 100).
    
    Returns:
        (final_score_integer, title_similarity_float, matched_skills_list)
    """
    weights = custom_weights or DEFAULT_WEIGHTS

    # 1. Title Similarity (30%)
    title_sim = score_title(query_title, job.title)

    # 2. Skill Match (20%)
    skill_score, matched_skills = score_skills(f"{query_title} {job.title}", job.description)

    # 3. Resume Match (15%)
    res_score = score_resume(resume_text, job.description)

    # 4. Semantic Similarity (10%)
    sem_score = SemanticScorer().score(query_title, job)

    # 5. Freshness Decay (10%)
    fresh_score = score_freshness(getattr(job, "posted_date", ""))

    # 6. Company Quality (5%)
    comp_score = score_company(job.company)

    # 7. Salary Match (5%)
    salary_score = 100.0 if getattr(job, "salary", None) else 50.0

    # 8. Description Quality (3%)
    desc_len = len(getattr(job, "description", "") or "")
    desc_score = 100.0 if desc_len >= MIN_DESC_LENGTH_FOR_CONFIDENCE else 40.0

    # 9. Personalization (2%)
    pers_score = 50.0

    # Weighted Sum
    total_score = (
        weights["title_match"] * title_sim +
        weights["query_skill_match"] * skill_score +
        weights["resume_match"] * res_score +
        weights["semantic_similarity"] * sem_score +
        weights["freshness"] * fresh_score +
        weights["company_quality"] * comp_score +
        weights["salary_match"] * salary_score +
        weights["description_quality"] * desc_score +
        weights["personalization"] * pers_score
    )

    final_int_score = max(0, min(100, int(round(total_score))))
    return final_int_score, title_sim, matched_skills
