"""
search_engine/ranking/scorer.py — Phase 13: Modular Composite Scoring Orchestrator.
Applies modular weights and computes composite match score.
"""

from __future__ import annotations
from typing import Any, Optional
from search_engine.config import DEFAULT_WEIGHTS, MIN_DESC_LENGTH_FOR_CONFIDENCE, detect_career_domain
from search_engine.ranking.title_score import score_title
from search_engine.ranking.skill_score import score_skills
from search_engine.ranking.freshness import score_freshness
from search_engine.ranking.company_score import score_company
from search_engine.ranking.resume_score import score_resume



def calculate_composite_score(
    query_title: str,
    job: Any,
    resume_text: Optional[str] = None,
    custom_weights: Optional[dict[str, float]] = None,
    target_role: Optional[str] = None
) -> tuple[int, float, list[str]]:
    """Compute weighted composite relevance score (0 - 100).
    
    Returns:
        (final_score_integer, title_similarity_float, matched_skills_list)
    """
    weights = custom_weights or DEFAULT_WEIGHTS
    job_title = getattr(job, "title", "") or ""
    job_desc = getattr(job, "description", "") or ""

    # 1. Title Similarity (28%)
    title_sim = score_title(query_title, job_title)

    # 2. Skill Match (27%)
    skill_score, matched_skills = score_skills(f"{query_title} {job_title}", job_desc)

    # 3. Resume Match (25%) - Integrated Keyword Overlap, Semantic Similarity, and Gemini AI ATS
    res_score = score_resume(resume_text, job_desc)
    domain_mismatch = False

    if resume_text and resume_text.strip():
        from search_engine.ranking.semantic_score import SemanticScorer
        semantic_scorer = SemanticScorer()
        semantic_sim = semantic_scorer.score(resume_text, job)

        # Call Gemini AI ATS Scorer for high-fidelity evaluation
        from search_engine.ranking.gemini_ats_scorer import gemini_ats_score
        gemini_res = gemini_ats_score(
            resume_text=resume_text,
            job_title=job_title,
            job_description=job_desc,
            candidate_target_role=target_role or query_title
        )
        
        gemini_score = gemini_res.get("ats_score", 0)
        if not gemini_res.get("is_relevant_domain", True):
            domain_mismatch = True

        # Tri-hybrid: 30% Keyword Overlap + 30% Vector Semantic + 40% Gemini ATS Score
        hybrid_resume_score = (res_score * 0.3) + (semantic_sim * 0.3) + (gemini_score * 0.4)
    else:
        hybrid_resume_score = 0.0

    # 4. Role Domain Gate Check (Deterministic Backup)
    resume_domain = detect_career_domain(f"{target_role or ''} {query_title} {resume_text or ''}")
    job_domain = detect_career_domain(f"{job_title} {job_desc[:500]}")
    
    if (resume_domain != "unknown" and job_domain != "unknown" and resume_domain != job_domain):
        domain_mismatch = True

    # 5. Freshness Decay (10%)
    fresh_score = score_freshness(getattr(job, "posted_date", ""))

    # 6. Company Quality (4%)
    comp_score = score_company(job.company)

    # 7. Salary Match (3%) - 0.0 if missing (no unearned bonus)
    salary_score = 100.0 if getattr(job, "salary", None) else 0.0

    # 8. Description Quality (3%) - 0.0 if short (no unearned bonus)
    desc_len = len(job_desc)
    desc_score = 100.0 if desc_len >= MIN_DESC_LENGTH_FOR_CONFIDENCE else 0.0

    # Weighted Sum
    total_score = (
        weights["title_match"] * title_sim +
        weights["query_skill_match"] * skill_score +
        weights["resume_match"] * hybrid_resume_score +
        weights["freshness"] * fresh_score +
        weights["company_quality"] * comp_score +
        weights["salary_match"] * salary_score +
        weights["description_quality"] * desc_score
    )

    final_int_score = max(0, min(100, int(round(total_score))))

    # Apply strict Domain Gate penalty multiplier (0.4x) if domains mismatch
    if domain_mismatch:
        final_int_score = min(25, int(round(final_int_score * 0.4)))

    return final_int_score, title_sim, matched_skills

