"""
search_engine/ranking/llm_reranker.py — Pillar 3: Soft AI Guardrails & Dynamic Re-Ranking.
Pass 2 LLM Re-ranker.
"""

from utils import get_gemini_response
import json

def evaluate_job_fit_batch(resume_text: str, query_title: str, jobs: list, target_role: str = "") -> dict:
    """
    Evaluates top jobs against the resume and query.
    Takes up to 20 jobs, sends a batch prompt to Gemini, and returns a dict mapping job.id to a new score.
    """
    if not jobs:
        return {}

    # Create an expanded payload for the prompt to include more technical details
    jobs_payload = []
    for i, job in enumerate(jobs):
        job_id = getattr(job, "id", None) or getattr(job, "url", None) or f"job_{i}"
        desc_snippet = (getattr(job, "description", "") or "")[:800]
        jobs_payload.append({
            "index": i,
            "id": str(job_id),
            "title": getattr(job, "title", ""),
            "company": getattr(job, "company", ""),
            "description_snippet": desc_snippet
        })

    prompt = f"""You are an expert recruiter evaluating candidate-job fit.
Score each job posting from 0 to 100 based on fit with the candidate's resume and target role.

STRICT DOMAIN ALIGNMENT RULES:
1. **Irrelevant Domain Mismatch**: If the job is in a COMPLETELY DIFFERENT career field than the candidate's background/target role (e.g., Software Engineer CV applying for Marketing, HR, Finance, Sales, or Hospitality jobs), you MUST score it between 0 and 15. DO NOT evaluate keywords for domain-mismatched roles.
2. **Adjacent Field**: Score 20 to 45 for adjacent fields with minor transferable skills.
3. **Same Field / Related Specialization**: Score 50 to 75.
4. **Strong Direct Match**: Score 75 to 100.

Candidate Target Role: {target_role or query_title}
Candidate Resume Summary:
{resume_text[:2500] if resume_text else "Not provided (base strictly on query title: " + query_title + ")"}

Job Postings to Evaluate:
{json.dumps(jobs_payload, indent=2)}

Respond ONLY in valid JSON mapping job 'id' string to an integer score (0-100):
{{
    "job_id_1": 85,
    "job_id_2": 10
}}
"""

    try:
        response_text = get_gemini_response(prompt, model="gemini-2.5-flash")
        
        # Clean markdown code block if present
        clean_text = response_text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        if clean_text.startswith("```"):
            clean_text = clean_text[3:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
            
        parsed = json.loads(clean_text.strip())
        return parsed if isinstance(parsed, dict) else {}
    except Exception as e:
        print(f"Error in LLM re-ranking: {e}")
        return {}

