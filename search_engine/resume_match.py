from __future__ import annotations
import re
from collections import Counter
from typing import Optional

_GENERIC_WORDS: set[str] = {
    "the", "and", "for", "are", "was", "were", "will", "has", "had", "have",
    "this", "that", "with", "from", "they", "their", "them", "been", "being",
    "a", "an", "is", "in", "it", "of", "to", "on", "at", "by", "or", "be",
    "as", "but", "not", "we", "our", "you", "your", "all", "can", "do", "no",

    "job", "jobs", "work", "works", "worked", "working", "role", "position",
    "candidate", "company", "companies", "department", "division",
    "hire", "hired", "hiring", "apply", "applicant", "recruit",

    "team", "teams", "experience", "experienced", "experiences",
    "management", "manage", "managed", "manager", "managers", "managing",
    "skill", "skills", "skilled", "training", "trained", "train",
    "service", "services", "process", "processes",
    "quality", "safety", "customer", "customers", "client", "clients",
    "operation", "operations", "operational", "operate",
    "project", "projects", "planning", "plan", "plans", "planned",
    "development", "develop", "developed", "developing",
    "support", "supported", "supporting", "supports",
    "time", "area", "level", "levels", "standard", "standards",
    "system", "systems", "program", "programs",

    "resource", "resources", "strategy", "strategic", "goal", "goals",
    "objective", "objectives", "task", "tasks",
    "duty", "duties", "responsibility", "responsibilities",
    "function", "functions", "functional",
    "improvement", "improve", "improved", "improving",
    "performance", "perform", "performed", "performing",
    "efficiency", "efficient", "productivity", "productive",
    "growth", "relationship", "relationships",
    "communication", "communicate", "collaboration", "collaborate",
    "coordination", "coordinate", "coordinated",
    "implementation", "implement", "implemented", "implementing",
    "execution", "execute", "delivery", "deliver", "delivered",
    "maintenance", "maintain", "maintained", "maintaining",
    "monitoring", "monitor", "monitored",
    "evaluation", "evaluate", "evaluated", "assessment", "assess",
    "analysis", "analyze", "analytics", "reporting", "report",
    "documentation", "document", "documents",

    "leadership", "lead", "leads", "led", "leader", "leaders",
    "supervision", "supervise", "supervised", "supervisor",
    "direction", "direct", "directed", "guidance", "guide",
    "mentor", "mentoring", "coach", "coaching",
    "organization", "organize", "organized", "organizational",
    "administration", "administer", "administrative",
    "compliance", "comply", "regulation", "regulatory",

    "responsible", "accountable", "oversee", "overseeing",
    "various", "multiple", "diverse", "extensive",
    "proven", "demonstrated", "strong", "excellent",
    "proficient", "effective", "advanced",
    "ability", "abilities", "capability", "capabilities",
    "competency", "competencies", "qualification", "qualifications",
    "qualified", "knowledge", "knowledgeable", "expertise",

    "year", "years", "month", "months", "day", "days", "hour", "hours",
    "fulltime", "full-time", "parttime", "part-time",
    "permanent", "temporary", "contractor",

    "including", "includes", "related", "etc",
    "well", "plus", "good", "great", "highly",
    "looking", "seeking", "required", "requirement", "requirements",
    "must", "should", "will", "may", "within",
    "description", "location", "salary",
    "please", "send", "cv", "resume", "resumes",

    "new", "first", "last", "best", "top", "key", "main", "primary",
    "current", "previous", "prior", "recent",
    "successful", "successfully",
    "assist", "assistance", "assisted",
    "conduct", "conducted", "conducting",
    "identify", "identified", "identifying",
    "provide", "provided", "provides", "providing",
    "review", "reviewed", "reviewing", "reviews",
    "ensure", "ensures", "ensured", "ensuring",
    "prepare", "prepared", "prepares", "preparing",
    "complete", "completed", "completing",
    "active", "actively", "consistently",
    "dedicated", "committed", "passionate",
    "detail-oriented", "detailoriented", "resultdriven", "result-driven",
    "oral", "written", "interpersonal",
    "fast-paced", "fastpaced", "dynamic", "busy",
    "environment", "environments", "setting", "settings",
    "flexible", "adaptable", "selfmotivated", "self-motivated",
    "initiative", "initiatives", "proactive",
    "problem-solving", "problemsolving", "problem",
    "decision-making", "decisionmaking", "decision",
}


def extract_distinctive_terms(text: str, top_n: int = 30) -> set[str]:
    if not text:
        return set()

    tokens = re.findall(r"\b[a-zA-Z][a-zA-Z0-9\-]+\b", text.lower())
    tokens = [t for t in tokens if t not in _GENERIC_WORDS and len(t) > 2]

    if not tokens:
        return set()

    freq = Counter(tokens)
    return {term for term, _ in freq.most_common(top_n)}


def is_resume_job_mismatched(
    resume_text: Optional[str],
    job_title: str,
    job_description: str,
    threshold: float = 0.05,
    min_signal_terms: int = 5,
) -> bool:
    if not resume_text:
        return False

    resume_terms = extract_distinctive_terms(resume_text)
    if len(resume_terms) < min_signal_terms:
        return False

    job_text = f"{job_title} {job_description}"
    job_terms = extract_distinctive_terms(job_text)
    if len(job_terms) < min_signal_terms:
        return False

    overlap = resume_terms & job_terms

    # Very lenient: only drop when there is ZERO distinctive-term overlap with
    # the resume, so strong-but-not-verbatim matches still surface.
    min_required = max(1, int(len(resume_terms) * 0.03))

    return len(overlap) < min_required

def generate_gap_analysis(resume_text: str, job_description: str) -> dict:
    """
    Pillar 4: AI Resume-to-Job Deep Match & Gap Analysis.
    Evaluates the candidate's resume against the job description to output structured JSON:
    - match_score_percentage
    - key_strengths
    - missing_skills_gaps
    - application_tip
    """
    if not resume_text or not job_description:
        return {
            "match_score_percentage": 50,
            "key_strengths": [],
            "missing_skills_gaps": [],
            "application_tip": "Provide a resume and job description to get a detailed gap analysis."
        }
        
    try:
        from utils import get_gemini_response
        import json
        
        prompt = f"""
        Act as a career coach. Compare this resume to the job description.
        Provide JSON output with:
        - match_score_percentage (integer)
        - key_strengths (list of up to 3 short strings)
        - missing_skills_gaps (list of up to 3 short strings)
        - application_tip (1 short sentence)
        
        Resume: {resume_text[:2000]}
        Job Description: {job_description[:2000]}
        
        Respond ONLY with the JSON object.
        """
        
        response_text = get_gemini_response(prompt, model="gemini-2.5-flash")
        
        clean_text = response_text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        if clean_text.startswith("```"):
            clean_text = clean_text[3:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
            
        return json.loads(clean_text.strip())
    except Exception as e:
        print(f"Error generating gap analysis: {e}")
        return {
            "match_score_percentage": 50,
            "key_strengths": ["Analysis temporarily unavailable"],
            "missing_skills_gaps": ["Analysis temporarily unavailable"],
            "application_tip": "Please try again later."
        }
