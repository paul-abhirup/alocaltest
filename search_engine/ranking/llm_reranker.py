"""
search_engine/ranking/llm_reranker.py — Pillar 3: Soft AI Guardrails & Dynamic Re-Ranking.
Pass 2 LLM Re-ranker.
"""

from utils import get_gemini_response
import json

def evaluate_job_fit_batch(resume_text: str, query_title: str, jobs: list) -> dict:
    """
    Evaluates top jobs against the resume and query.
    Takes up to 20 jobs, sends a batch prompt to Gemini, and returns a dict mapping job.id to a new score.
    """
    if not jobs:
        return {}

    # Create a truncated payload for the prompt to fit within context and reduce latency.
    jobs_payload = []
    for i, job in enumerate(jobs):
        job_id = getattr(job, "id", None) or getattr(job, "url", None) or f"job_{i}"
        desc_snippet = (getattr(job, "description", "") or "")[:400]
        jobs_payload.append({
            "index": i,
            "id": str(job_id),
            "title": getattr(job, "title", ""),
            "company": getattr(job, "company", ""),
            "description_snippet": desc_snippet
        })

    prompt = f"""
    You are an expert technical recruiter and career coach.
    Evaluate the fit between the candidate's resume/query and the following list of job postings.
    Score from 0 to 100 based on experience level alignment, key responsibility fit, and tech stack compatibility.
    Be strict: heavily penalize jobs that require much more experience than the candidate has, or fundamentally different skills.
    
    Candidate Query Title: {query_title}
    Candidate Resume: {resume_text[:2000] if resume_text else "Not provided (base it purely on query title)"}
    
    Jobs:
    {json.dumps(jobs_payload, indent=2)}
    
    Respond ONLY in valid JSON format mapping the job 'id' to an integer score:
    {{
        "job_id_1": 85,
        "job_id_2": 42
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
            
        scores = json.dumps(clean_text)
        return json.loads(clean_text)
    except Exception as e:
        print(f"Error in LLM re-ranking: {e}")
        return {}
