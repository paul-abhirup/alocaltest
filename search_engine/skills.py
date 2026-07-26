"""
search_engine/skills.py — Phase 12: Skill & Technology Extraction.
Extracts tech stacks (languages, frameworks, cloud, DBs, DevOps, ML) from text.
"""

from __future__ import annotations
import re
from search_engine.config import TECH_STANDARDIZATION

_SKILL_PATTERNS = {
    # Languages
    "Python": r"\bpython3?\b",
    "Java": r"\bjava\b(?!script)",
    "JavaScript": r"\b(javascript|js|es6)\b",
    "TypeScript": r"\b(typescript|ts)\b",
    "C++": r"\bc\+\+\b",
    "C#": r"\bc#\b",
    "Go": r"\bgolang\b|\bgo\b",
    "Rust": r"\brust\b",
    "PHP": r"\bphp\b",
    "Ruby": r"\bruby\b",
    "SQL": r"\bsql\b",

    # Frameworks
    "React": r"\b(react|reactjs|react\.js)\b",
    "Vue.js": r"\b(vue|vuejs|vue\.js)\b",
    "Angular": r"\bangular\b",
    "Django": r"\bdjango\b",
    "FastAPI": r"\bfastapi\b",
    "Flask": r"\bflask\b",
    "Spring Boot": r"\bspring\s*boot\b",
    "Express.js": r"\b(express|expressjs)\b",
    "Node.js": r"\b(node|nodejs|node\.js)\b",

    # Cloud & DevOps
    "AWS": r"\b(aws|amazon\s*web\s*services)\b",
    "GCP": r"\b(gcp|google\s*cloud)\b",
    "Azure": r"\bazure\b",
    "Docker": r"\bdocker\b",
    "Kubernetes": r"\b(k8s|kubernetes)\b",
    "CI/CD": r"\b(ci/cd|jenkins|github\s*actions)\b",
    "Terraform": r"\bterraform\b",

    # Databases
    "PostgreSQL": r"\b(postgres|postgresql)\b",
    "MongoDB": r"\b(mongo|mongodb)\b",
    "MySQL": r"\bmysql\b",
    "Redis": r"\bredis\b",
    "Elasticsearch": r"\belasticsearch\b",

    # AI / ML
    "PyTorch": r"\bpytorch\b",
    "TensorFlow": r"\btensorflow\b",
    "Scikit-Learn": r"\bscikit-learn\b|\bsklearn\b",
    "LLMs": r"\b(llm|llms|gpt|transformers|rag)\b",
}


def extract_skills(text: str) -> list[str]:
    """Extract recognized technologies and skills from text."""
    if not text:
        return []

    found = []
    low = text.lower()

    for skill_name, pattern in _SKILL_PATTERNS.items():
        if re.search(pattern, low):
            found.append(skill_name)

    return found


def compute_skill_match_score(query_text: str, job_text: str) -> tuple[float, list[str]]:
    """Compute skill overlap score (0.0 to 100.0) and list of matched skills."""
    q_skills = set(extract_skills(query_text))
    j_skills = set(extract_skills(job_text))

    if not q_skills:
        return 50.0, list(j_skills)[:5]

    if not j_skills:
        return 0.0, []

    matched = q_skills.intersection(j_skills)
    score = (len(matched) / len(q_skills)) * 100.0
    return min(100.0, score), sorted(list(matched))
