"""
search_engine/config.py — Centralized configuration for Job Search Relevance Engine.
Avoids magic numbers and hardcoded strings across the search pipeline.
"""

# Hard thresholds
TITLE_SIMILARITY_THRESHOLD = 30.0   # Reject job immediately if title similarity < 30%
MIN_CORE_TOKEN_COVERAGE = 0.40      # Multi-token queries must match at least this fraction of distinctive tokens
MAX_JOB_AGE_DAYS = 90               # Discard jobs posted > 90 days ago
MIN_DESC_LENGTH_FOR_CONFIDENCE = 200 # Short descriptions get lower confidence

# Generic role nouns that carry low signal when matched alone
GENERIC_ROLE_NOUNS = {
    "developer", "engineer", "designer", "manager", "specialist", "analyst",
    "consultant", "coordinator", "associate", "lead", "head", "director",
    "officer", "executive", "administrator", "assistant", "representative",
    "technician", "architect", "scientist", "strategist", "planner", "writer",
    "editor", "producer", "tester", "programmer",
}

# Role synonym map: query title → expanded acceptable job title tokens
ROLE_SYNONYMS = {
    "instructional designer": ["instructional designer", "instructional design", "learning designer",
                                "learning experience designer", "lxd", "elearning designer",
                                "curriculum designer", "learning and development",
                                "training designer", "education designer"],
    "ux designer": ["ux designer", "user experience designer", "ux/ui designer",
                    "product designer", "interaction designer"],
    "ui designer": ["ui designer", "ux/ui designer", "user interface designer",
                    "visual designer", "interface designer", "product designer"],
    "data analyst": ["data analyst", "data analysis", "business analyst", "analytics",
                     "data analytics", "data scientist", "data engineer",
                     "reporting analyst", "bi analyst", "insights analyst",
                     "business intelligence", "quantitative analyst"],
    "reporting analyst": ["reporting analyst", "data analyst", "analytics",
                          "data analytics", "bi analyst", "insights analyst",
                          "business intelligence analyst"],
    "project manager": ["project manager", "program manager", "delivery manager", "project lead",
                        "project coordinator", "pmo", "scrum master",
                        "technical project manager", "agile coach",
                        "it project manager"],
    "product manager": ["product manager", "product owner", "product lead", "product management",
                        "product director", "chief product", "product strategy",
                        "product development"],
    "marketing": ["marketing", "marketing manager", "marketing coordinator",
                  "marketing specialist", "marketing director", "brand marketing",
                  "growth marketing", "marketing lead", "digital marketing"],
    "frontend": ["frontend", "front end", "front-end", "react developer", "react engineer",
                 "ui developer", "ui engineer", "angular developer",
                 "javascript developer", "typescript developer"],
    "backend": ["backend", "back-end", "back end", "server-side", "api developer",
                "microservices developer", "backend engineer", "django developer",
                "python developer", "fastapi developer", "flask developer",
                "spring developer", "go developer", "ruby developer", "php developer",
                "nodejs developer", "node.js developer"],
    "full stack": ["fullstack", "full-stack", "full stack", "fullstack engineer",
                   "full stack engineer", "full-stack engineer"],
    "fullstack": ["fullstack", "full-stack", "full stack", "fullstack engineer",
                  "full stack engineer", "full-stack engineer"],
    "full stack developer": ["fullstack", "full-stack", "full stack", "fullstack engineer",
                              "full stack engineer", "full-stack engineer"],
    "devops": ["devops", "devops engineer", "site reliability", "sre", "cloud engineer",
               "infrastructure engineer", "platform engineer", "sre engineer",
               "cloud devops engineer", "infrastructure", "site reliability engineer",
               "infra engineer"],
    "devops engineer": ["devops", "devops engineer", "site reliability", "sre", "cloud engineer",
                        "infrastructure engineer", "platform engineer", "sre engineer",
                        "cloud devops engineer", "infrastructure", "site reliability engineer",
                        "infra engineer"],
    "machine learning": ["machine learning", "ml engineer", "ai engineer", "ml ops",
                         "artificial intelligence", "deep learning", "data scientist ml",
                         "machine learning engineer", "data scientist", "mlops",
                         "llm engineer", "nlp engineer"],
    "machine learning engineer": ["machine learning", "ml engineer", "ai engineer", "ml ops",
                                   "artificial intelligence", "deep learning", "data scientist ml",
                                   "machine learning engineer", "data scientist", "mlops",
                                   "llm engineer", "nlp engineer"],
    "ml engineer": ["machine learning", "ml engineer", "ai engineer", "ml ops",
                    "artificial intelligence", "deep learning", "data scientist ml",
                    "machine learning engineer", "data scientist", "mlops",
                    "llm engineer", "nlp engineer"],
    "data engineer": ["data engineer", "etl developer", "data engineering",
                      "big data engineer", "data pipeline engineer"],
    "data scientist": ["data scientist", "machine learning", "data engineer",
                       "data analyst", "ai engineer", "ml engineer",
                       "applied scientist", "research scientist"],
}

# Stemming rules: normalize plural/suffix variants for token comparison
STEM_RULES = [
    (r"ers$", "er"),
    (r"eering$", "eer"),
    (r"eers$", "eer"),
    (r"ing$", ""),
    (r"ed$", ""),
    (r"ional$", "ion"),
    (r"ly$", ""),
    (r"ies$", "y"),
    (r"esses$", "ess"),
    (r"ments?$", ""),
    (r"ance$", "e"),
    (r"ence$", "e"),
    (r"or$", "or"),
    (r"ist$", "ist"),
    (r"ian$", "ian"),
    (r"bility$", "bl"),
    (r"gence$", "g"),
    (r"istics$", "istic"),
]

# Composite scoring weights (must sum to 1.0)
DEFAULT_WEIGHTS = {
    "title_match": 0.28,
    "query_skill_match": 0.27,
    "resume_match": 0.25,
    "freshness": 0.10,
    "company_quality": 0.04,
    "salary_match": 0.03,
    "description_quality": 0.03,
}

# Career Domain Mapping for Domain Alignment Gating
CAREER_DOMAINS = {
    "software_engineering": [
        "developer", "engineer", "software", "frontend", "backend", "fullstack", "full-stack", "full stack",
        "web developer", "mobile developer", "ios", "android", "devops", "sre", "cloud engineer",
        "system architect", "solution architect", "qa engineer", "test engineer", "automation engineer",
        "react", "node", "python", "java", "c++", "golang", "ruby", "php", "typescript"
    ],
    "data_ai": [
        "data analyst", "data scientist", "data engineer", "machine learning", "ml engineer",
        "ai engineer", "deep learning", "nlp", "computer vision", "analytics engineer", "bi analyst",
        "business intelligence", "big data", "statist", "quantitative analyst"
    ],
    "design_ux": [
        "ux designer", "ui designer", "product designer", "user experience", "user interface",
        "graphic designer", "visual designer", "interaction designer", "creative director",
        "animator", "instructional designer", "learning designer"
    ],
    "marketing_growth": [
        "marketing", "digital marketing", "seo", "sem", "content manager", "growth marketer",
        "social media", "brand manager", "copywriter", "marketing specialist", "email marketing"
    ],
    "sales_bizdev": [
        "sales", "account executive", "account manager", "business development", "bdr", "sdr",
        "sales director", "client partner", "relationship manager"
    ],
    "hr_recruiting": [
        "recruiter", "talent acquisition", "human resources", "hr manager", "people ops",
        "hr business partner", "headhunter", "talent manager"
    ],
    "finance_accounting": [
        "accountant", "financial analyst", "auditor", "controller", "bookkeeper",
        "tax specialist", "payroll", "finance manager"
    ],
    "product_management": [
        "product manager", "product owner", "product lead", "chief product officer",
        "head of product", "technical product manager"
    ],
    "project_operations": [
        "project manager", "program manager", "scrum master", "agile coach", "operations manager",
        "pmo", "supply chain", "logistics coordinator"
    ],
}

def detect_career_domain(text: str) -> str:
    """Classify text (resume or job title/description) into a career domain based on keyword matching."""
    if not text:
        return "unknown"
    t_lower = text.lower()
    scores = {}
    for domain, keywords in CAREER_DOMAINS.items():
        score = sum(1 for kw in keywords if kw in t_lower)
        if score > 0:
            scores[domain] = score
    if not scores:
        return "unknown"
    return max(scores, key=scores.get)


# Negative keywords: Automatically reject titles containing these words unless user explicitly searched for them.
# Kept focused on education/recruiting roles that don't fit the platform's job-match intent.
DEFAULT_NEGATIVE_KEYWORDS = {
    "faculty", "trainer", "teacher", "tutor", "counsellor", "counselor",
    "recruiter", "hr", "business development",
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
