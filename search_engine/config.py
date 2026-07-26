"""
search_engine/config.py — Centralized configuration for Job Search Relevance Engine.
Avoids magic numbers and hardcoded strings across the search pipeline.
"""

# Hard thresholds
TITLE_SIMILARITY_THRESHOLD = 40.0   # Reject job immediately if title similarity < 40%
MAX_JOB_AGE_DAYS = 45               # Discard jobs posted > 45 days ago
MIN_DESC_LENGTH_FOR_CONFIDENCE = 200 # Short descriptions get lower confidence

# Composite scoring weights (must sum to 1.0)
DEFAULT_WEIGHTS = {
    "title_match": 0.30,
    "query_skill_match": 0.20,
    "resume_match": 0.15,
    "semantic_similarity": 0.10,
    "freshness": 0.10,
    "company_quality": 0.05,
    "salary_match": 0.05,
    "description_quality": 0.03,
    "personalization": 0.02,
}

# Negative keywords: Automatically reject titles containing these words unless user explicitly searched for them
DEFAULT_NEGATIVE_KEYWORDS = {
    "faculty", "trainer", "teacher", "tutor", "counsellor", "counselor",
    "sales", "marketing", "recruiter", "hr", "business development",
    "customer support", "call center", "telecaller"
}

# Synonyms and abbreviations for query normalization
ABBREVIATIONS = {
    "sr.": "senior",
    "sr": "senior",
    "jr.": "junior",
    "jr": "junior",
    "react.js": "react",
    "reactjs": "react",
    "node js": "node.js",
    "nodejs": "node.js",
    "py dev": "python developer",
    "py engineer": "python engineer",
    "js": "javascript",
    "ts": "typescript",
    "fe": "frontend",
    "be": "backend",
    "fs": "fullstack",
    "qa": "quality assurance",
    "dev": "developer",
    "mgr": "manager",
}

# Technology standardization dictionary
TECH_STANDARDIZATION = {
    "python": "Python",
    "django": "Django",
    "fastapi": "FastAPI",
    "flask": "Flask",
    "react": "React",
    "react.js": "React",
    "reactjs": "React",
    "vue": "Vue.js",
    "vue.js": "Vue.js",
    "angular": "Angular",
    "node": "Node.js",
    "node.js": "Node.js",
    "nodejs": "Node.js",
    "express": "Express.js",
    "typescript": "TypeScript",
    "javascript": "JavaScript",
    "aws": "AWS",
    "gcp": "GCP",
    "azure": "Azure",
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "postgresql": "PostgreSQL",
    "postgres": "PostgreSQL",
    "mongodb": "MongoDB",
    "mysql": "MySQL",
    "redis": "Redis",
    "graphql": "GraphQL",
    "rest": "REST API",
    "git": "Git",
    "linux": "Linux",
}

# Stop words ignored during title comparison
STOP_WORDS = {
    "a", "an", "the", "and", "or", "in", "for", "with", "at", "to", "of",
    "on", "by", "from", "is", "be", "job", "jobs", "hiring", "wanted", "needed", "opportunity"
}

# Experience buckets
EXPERIENCE_BUCKETS = {
    "intern": (0, 0),
    "junior": (0, 2),
    "mid": (2, 5),
    "senior": (5, 8),
    "lead": (8, 50),
}

# Employment types
EMPLOYMENT_TYPES = {
    "full-time": ["full_time", "full time", "permanent"],
    "contract": ["contract", "freelance", "temporary", "project"],
    "part-time": ["part_time", "part time"],
    "internship": ["internship", "intern", "graduate"],
}
