"""
search_engine/ranking/gemini_ats_scorer.py — Gemini AI ATS Relevance Scorer.
Provides accurate CV↔JD alignment scoring using Gemini AI with domain alignment checks.
"""

from __future__ import annotations
import json
import hashlib
import time
from typing import Any, Dict, Optional
from utils import get_gemini_response

# In-memory score cache with 10-minute TTL
_score_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}
_CACHE_TTL = 600  # 10 minutes in seconds


def gemini_ats_score(
    resume_text: str,
    job_title: str,
    job_description: str,
    candidate_target_role: str = ""
) -> Dict[str, Any]:
    """
    Evaluates CV↔JD alignment using Gemini AI.
    
    Returns:
        {
            "ats_score": int (0-100),
            "role_alignment": int (0-100),
            "skill_match": int (0-100),
            "experience_fit": int (0-100),
            "reasoning": str,
            "is_relevant_domain": bool
        }
    """
    if not resume_text or not job_description:
        return {
            "ats_score": 0,
            "role_alignment": 0,
            "skill_match": 0,
            "experience_fit": 0,
            "reasoning": "Missing resume or job description",
            "is_relevant_domain": True
        }

    # MD5 Cache Key
    cache_key = hashlib.md5(
        f"{resume_text[:500]}|{job_title}|{job_description[:500]}|{candidate_target_role}".encode()
    ).hexdigest()

    now = time.time()
    if cache_key in _score_cache:
        cached_time, cached_result = _score_cache[cache_key]
        if now - cached_time < _CACHE_TTL:
            return cached_result

    prompt = f"""You are an expert ATS (Applicant Tracking System) scoring engine.
Evaluate how well the candidate's resume matches this job posting.
Score STRICTLY and ACCURATELY. Do NOT inflate scores for irrelevant job roles.

CRITICAL SCORING RULES:
1. **Role Alignment (0-100)**: Is this job in the candidate's professional domain/career path?
   - Score 0-15 if completely different fields (e.g. Software Developer CV applying for Marketing, HR, Accounting, or Sales Manager)
   - Score 15-45 if adjacent fields with minor transferable skills
   - Score 45-75 if same broad field but different specialization
   - Score 75-100 if direct role match
   
2. **Skill Match (0-100)**: What percentage of the job's REQUIRED technical and core skills are explicitly present in the candidate's resume?
   - Do NOT count generic buzzwords (e.g. "teamwork", "management", "communication").

3. **Experience Fit (0-100)**: Seniority level and responsibility fit.

4. **Final ATS Score (0-100)**:
   - If Role Alignment < 30, Final ATS Score MUST NOT exceed 25.
   - Otherwise, weighted: Role Alignment (40%) + Skill Match (35%) + Experience Fit (25%).

Candidate Target Role: {candidate_target_role or "Not specified"}
Candidate Resume:
{resume_text[:2500]}

Job Title: {job_title}
Job Description:
{job_description[:2500]}

Respond ONLY in valid JSON format:
{{
    "ats_score": <int 0-100>,
    "role_alignment": <int 0-100>,
    "skill_match": <int 0-100>,
    "experience_fit": <int 0-100>,
    "reasoning": "<1 sentence concise explanation>",
    "is_relevant_domain": <bool, false if role_alignment < 30 else true>
}}
"""

    try:
        response = get_gemini_response(prompt, model="gemini-2.5-flash")
        clean = response.strip()
        if clean.startswith("```json"):
            clean = clean[7:]
        if clean.startswith("```"):
            clean = clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]
        
        result = json.loads(clean.strip())

        # Sanitize and clamp values
        ats_score = max(0, min(100, int(result.get("ats_score", 0))))
        role_alignment = max(0, min(100, int(result.get("role_alignment", 0))))
        skill_match = max(0, min(100, int(result.get("skill_match", 0))))
        experience_fit = max(0, min(100, int(result.get("experience_fit", 0))))
        is_relevant = bool(result.get("is_relevant_domain", role_alignment >= 30))

        # Enforce domain penalty safety check
        if role_alignment < 30 or not is_relevant:
            ats_score = min(ats_score, 25)
            is_relevant = False

        sanitized_result = {
            "ats_score": ats_score,
            "role_alignment": role_alignment,
            "skill_match": skill_match,
            "experience_fit": experience_fit,
            "reasoning": str(result.get("reasoning", "")),
            "is_relevant_domain": is_relevant,
            "success": True
        }

        _score_cache[cache_key] = (now, sanitized_result)
        return sanitized_result

    except Exception as e:
        print(f"Gemini ATS Scorer error: {e}")
        return {
            "ats_score": 0,
            "role_alignment": 50,
            "skill_match": 50,
            "experience_fit": 50,
            "reasoning": "AI scoring fallback",
            "is_relevant_domain": True,
            "success": False
        }

