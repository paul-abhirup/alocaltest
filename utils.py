import re
import os
import time
from typing import Dict, List, Any

# Safe import for Gemini / google generative SDK
# We try to import compatible modules in a forgiving way so the server
# does not crash at import time if the SDK is missing or the API changed.
gemini_client = None
genai_types = None

try:
    # try the modern 'genai' package first (some projects use genai.GenerativeModel)
    import genai as _genai  # type: ignore
    try:
        # prefer the high-level client if available
        if hasattr(_genai, "GenerativeModel"):
            # some code uses: genai.GenerativeModel(api_key=...)
            # create an instance if key provided; otherwise keep module
            api_key = os.getenv("GEMINI_API_KEY")
            try:
                gemini_client = _genai.GenerativeModel(api_key=api_key) if api_key else _genai.GenerativeModel()
            except Exception:
                # fallback to module if instantiation signature differs
                gemini_client = _genai
        else:
            gemini_client = _genai
        # genai may not expose a 'types' object; leave genai_types as None
        genai_types = getattr(_genai, "types", None)
    except Exception:
        gemini_client = _genai
        genai_types = getattr(_genai, "types", None)
except Exception:
    # try the google.generativeai package next
    try:
        import google.generativeai as _gai  # type: ignore
        # Newer google.generativeai may require configure(API_KEY) instead of a Client class
        try:
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
            if hasattr(_gai, "configure"):
                # configure the module (no client instance returned)
                if api_key:
                    try:
                        _gai.configure(api_key=api_key)
                    except Exception:
                        # some versions expect api_key via environment; ignore minor failure
                        pass
                gemini_client = _gai
            elif hasattr(_gai, "Client"):
                # older style with Client class
                try:
                    gemini_client = _gai.Client(api_key=api_key) if api_key else _gai.Client()
                except Exception:
                    # fallback to module object
                    gemini_client = _gai
            else:
                gemini_client = _gai
            genai_types = getattr(_gai, "types", None)
        except Exception:
            gemini_client = _gai
            genai_types = getattr(_gai, "types", None)
    except Exception:
        # No compatible gemini SDK found — continue without crashing
        gemini_client = None
        genai_types = None

# If you previously used `client` and `types` variable names, keep backward-compatible aliases:
client = gemini_client
types = genai_types

# continue with streamlit and other imports
import streamlit as st
from dotenv import load_dotenv
import phonenumbers
import pycountry

load_dotenv()

def get_gemini_response(prompt: str, model: str = "gemini-2.5-flash") -> str:
    """Get response from Gemini AI with error handling"""
    try:
        if not client:
            # Keep existing behavior: use Streamlit to show error; fall back to empty string
            try:
                st.error("Gemini AI client not initialized")
            except Exception:
                print("Gemini AI client not initialized")
            return ""
        
        # Attempt to use the available client API; adapt to common API shapes.
        # We try a few common call patterns and handle failures gracefully.
        try:
            if hasattr(client, "GenerativeModel") and callable(client.GenerativeModel):
                try:
                    _model_instance = client.GenerativeModel(model)
                    response = _model_instance.generate_content(
                        prompt,
                        generation_config={"temperature": 0.7, "max_output_tokens": 4096}
                    )
                    # Extract text safely via candidates path first
                    if hasattr(response, "candidates") and response.candidates:
                        for cand in response.candidates:
                            if hasattr(cand, "content") and cand.content:
                                for part in cand.content.parts:
                                    if hasattr(part, "text") and part.text:
                                        return part.text
                    # Fallback to .text shortcut
                    if hasattr(response, "text") and response.text:
                        return response.text
                    return ""
                except Exception:
                    pass  # Fall through to the other patterns below
            # Pattern 1: google.generativeai or module-style with models.generate_content
            if hasattr(client, "models") and hasattr(client.models, "generate_content"):
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=(types.GenerateContentConfig(max_output_tokens=4096, temperature=0.7) if types else None)
                )
                # In some SDK versions result text is under response.text or response.content[0].text
                if hasattr(response, "text"):
                    return response.text or ""
                if hasattr(response, "content") and isinstance(response.content, (list, tuple)) and len(response.content):
                    first = response.content[0]
                    return getattr(first, "text", "") or getattr(first, "output", "") or ""
                return ""
            
            # Pattern 2: genai.GenerativeModel instance: .generate() or .generate_text(...)
            if hasattr(client, "generate") or hasattr(client, "generate_text"):
                # try generic call
                try:
                    if hasattr(client, "generate"):
                        out = client.generate(model=model, prompt=prompt)
                    else:
                        out = client.generate_text(model=model, prompt=prompt)
                except TypeError:
                    # some signatures differ; try other kw names
                    out = client.generate(model=model, contents=prompt)
                
                # extract text from response
                if isinstance(out, dict):
                    return out.get("text", "") or out.get("output", "") or ""
                if hasattr(out, "text"):
                    return out.text or ""
                # fallback
                return str(out)
            
            # Pattern 3: module object with a top-level generate function
            if hasattr(client, "generate_text") and callable(client.generate_text):
                r = client.generate_text(model=model, input=prompt)
                if isinstance(r, dict):
                    return r.get("candidates", [{}])[0].get("content", "") or r.get("output", "")
                if hasattr(r, "candidates"):
                    c = r.candidates
                    if c and len(c):
                        return getattr(c[0], "content", "") or ""
                return str(r)
            
            # Unknown client object shape: try to call `str()` as fallback
            print("Gemini client present but unknown API shape; returning empty response.")
            return ""
        except Exception as e:
            # Log the inner error but fail gracefully
            try:
                st.error(f"AI processing error: {str(e)}")
            except Exception:
                print("AI processing error:", str(e))
            return ""
    except Exception as e:
        try:
            st.error(f"AI processing error: {str(e)}")
        except Exception:
            print("AI processing error:", str(e))
        return ""

# ---------------------------
# Rest of your original utils.py follows unchanged
# (from filter_keywords onward). I paste the rest verbatim to keep file self-contained.
# ---------------------------

def filter_keywords(keywords):
    """Remove generic and stop words from keyword list.
    
    Excludes non-discriminative filler terms and common workplace vocabulary
    (e.g., 'team', 'management', 'communication', 'experience', 'skills') to prevent
    inflated overlap scores across unrelated job categories.
    """
    stop_words = {
        "the", "and", "is", "in", "of", "for", "to", "with", "on", "at", "by", "an", "be",
        "from", "that", "this", "it", "as", "are", "or", "have", "has", "was", "were", "will",
        "a", "i", "you", "your", "we", "our", "can", "able", "aptitude",
        "dynamic", "motivated", "great", "capable", "good", "proficient", "hardworking", "dedicated", "excellent",
        "role", "work", "job", "candidate", "company", "business", "support", "help",
        "looking", "requirement", "requirements", "required", "knowledge",
        "qualification", "qualifications", "duties", "environment", "ability", "working",
        "seeking", "description", "location", "full-time", "part-time", "contract", "salary", "apply",
        "please", "send", "cv", "resume", "years", "year", "must", "should", "join", "grow",
        "etc", "using", "uses", "used", "ideal", "position", "opportunity", "well", "plus",
        # Generic organizational & process words that cause false matches:
        "team", "management", "experience", "skills", "communication", "process", "develop",
        "project", "ensure", "provide", "strong", "including", "understanding", "responsible",
        "quality", "preferred", "time", "make", "new", "also", "best", "high", "key",
        "demonstrated", "proven", "track", "record", "across", "within", "relevant", "field",
        "degree", "bachelor", "master", "related", "level", "person", "people", "staff",
        "member", "members", "day", "daily", "overall", "general", "various", "multiple",
        "internal", "external", "key", "successful", "focus", "focused", "solutions",
    }
    return [kw for kw in keywords if kw.lower() not in stop_words and len(kw) > 2]

def extract_ats_phrases(text: str) -> List[str]:
    """Extract technical acronyms, multi-word skills, and recognized technology terms from text.

    Unlike a naive token extractor, this function only returns terms that are
    real ATS-relevant keywords: recognized technologies, domain-specific
    multi-word phrases, and technical acronyms. Generic single words are
    excluded unless they match known skill patterns.
    """
    if not text:
        return []

    phrases = []
    non_tech = {
        "hands-on", "handson", "full-time", "part-time", "end-to-end", "day-to-day",
        "cross-functional", "well-known", "self-starter", "fast-paced", "out-of-the-box"
    }

    # --- 1. Recognized technology skills (from search_engine/skills.py patterns) ---
    try:
        from search_engine.skills import extract_skills
        tech_skills = extract_skills(text)
        phrases.extend([s.lower() for s in tech_skills])
    except ImportError:
        pass

    # --- 2. Tech acronyms and hyphenated/slashed terms (CI/CD, A/B, PySpark, Node.js) ---
    tech_patterns = re.findall(r'\b[A-Za-z0-9]+[/\-\.][A-Za-z0-9/\-\.]+\b', text)
    phrases.extend([p.lower() for p in tech_patterns if len(p) >= 2 and p.lower() not in non_tech])

    # --- 3. Capitalized multi-word phrases (Machine Learning, Cloud Infrastructure) ---
    cap_phrases = re.findall(r'\b[A-Z][a-z]+(?:[^\S\r\n]+[A-Z][a-z]+)+\b', text)
    phrases.extend([p.lower() for p in cap_phrases if p.lower() not in non_tech])

    # --- 4. Curated domain bigrams & trigrams ---
    text_low = text.lower()
    domain_phrases = [
        "machine learning", "deep learning", "data science", "data engineering",
        "data pipeline", "data analytics", "data warehouse", "data infrastructure",
        "cloud computing", "cloud infrastructure", "cloud native",
        "micro services", "microservices", "distributed systems",
        "version control", "code review", "unit test", "integration test",
        "test driven", "agile methodology", "scrum master",
        "rest api", "restful api", "graphql api",
        "natural language", "computer vision", "speech recognition",
        "continuous integration", "continuous deployment", "continuous delivery",
        "infrastructure as code", "monitoring and observability",
        "object oriented", "functional programming",
        "technical debt", "design patterns", "system design",
        "user experience", "user interface", "responsive design",
        "project management", "stakeholder management", "team leadership",
        "problem solving", "analytical skills", "communication skills",
    ]
    for phrase in domain_phrases:
        if phrase in text_low:
            phrases.append(phrase)

    # --- 5. Deduplicate while preserving lowercased uniqueness ---
    seen = set()
    unique_phrases = []
    for item in phrases:
        item_clean = item.strip().lower()
        if item_clean and item_clean not in seen and item_clean not in non_tech and len(item_clean) >= 2:
            seen.add(item_clean)
            unique_phrases.append(item_clean)

    return unique_phrases


def _extract_jd_title(jd_text: str) -> str:
    """Extract the job title from a job description.

    Most JDs have the title as the first non-empty line, or embedded in
    common patterns like "Job Title: ..." or "We're looking for a ...".
    Returns the extracted title string, or empty string if not found.
    """
    if not jd_text:
        return ""

    lines = [ln.strip() for ln in jd_text.splitlines() if ln.strip()]
    if not lines:
        return ""

    # Pattern 1: Explicit "Job Title:" / "Position:" / "Title:" labels
    label_match = re.search(
        r'(?i)^(?:job\s+title|position|role)\s*[:\-]\s*(.{3,80})',
        lines[0]
    )
    if label_match:
        return label_match.group(1).strip()

    # Pattern 2: "We're looking for a [Title]" / "Hiring a [Title]"
    looking_match = re.search(
        r'(?i)(?:we(?:\'re| are)?\s+(?:looking|seeking|hiring)\s+(?:for|a|an)\s+|hiring\s+a[n]?\s+)(.{3,80})',
        lines[0]
    )
    if looking_match:
        title_candidate = looking_match.group(1).strip()
        # Trim trailing sentence words that aren't part of a title
        trim_words = {"to", "who", "with", "in", "at", "for", "and", "or", "the", "a", "an",
                       "join", "our", "we", "you", "your", "their", "this", "that"}
        words = title_candidate.split()
        trimmed = []
        for w in words:
            w_clean = w.rstrip(".,:;!?")
            if w_clean.lower() in trim_words and trimmed:
                break
            trimmed.append(w_clean)
        return " ".join(trimmed).rstrip(".,:;") if trimmed else title_candidate.rstrip(".,:;")

    # Pattern 3: First line is likely the title if it's short and title-like
    first_line = lines[0]
    # Title-like: 2-8 words, mostly alphabetic, no sentence verbs
    words = first_line.split()
    sentence_indicators = {"we", "our", "the", "is", "are", "will", "you", "your", "this", "that", "who", "how"}
    if 2 <= len(words) <= 8 and len(first_line) < 80:
        lower_words = {w.lower().rstrip(".,:;!?") for w in words}
        if not lower_words & sentence_indicators:
            return first_line.rstrip(".,:;")

    # Pattern 4: Check first 3 lines for a title-like line
    for line in lines[:3]:
        words = line.split()
        if 2 <= len(words) <= 8 and len(line) < 80:
            lower_words = {w.lower().rstrip(".,:;!?") for w in words}
            if not lower_words & sentence_indicators:
                return line.rstrip(".,:;")

    return ""


# Suffix-stripping stemmer for ATS keyword matching (no external dependencies)
_SUFFIX_RULES = [
    ("ational", "ate"), ("tional", "tion"), ("enci", "ence"),
    ("anci", "ance"), ("izer", "ize"), ("abli", "able"),
    ("alli", "al"), ("entli", "ent"), ("eli", "e"),
    ("ousli", "ous"), ("ization", "ize"), ("ation", "ate"),
    ("ator", "ate"), ("alism", "al"), ("iveness", "ive"),
    ("fulness", "ful"), ("ousness", "ous"), ("aliti", "al"),
    ("iviti", "ive"), ("biliti", "ble"), ("ness", ""),
    ("ment", ""), ("able", ""), ("ible", ""),
    ("ing", ""), ("tion", "t"), ("sion", "s"),
    ("ies", "y"), ("ive", ""), ("ful", ""),
    ("less", ""), ("ly", ""), ("ed", ""), ("er", ""),
    ("es", ""), ("s", ""),
]


def _simple_stem(word: str) -> str:
    """Lightweight English suffix-stripping stemmer for ATS keyword normalization."""
    if len(word) <= 3:
        return word
    w = word.lower()
    for suffix, replacement in _SUFFIX_RULES:
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[:-len(suffix)] + replacement
    return w


def _stemmed_text(text: str) -> str:
    """Return a space-joined stemmed version of all words in text for fuzzy matching."""
    tokens = re.findall(r'\b[a-zA-Z][a-zA-Z0-9\-]*\b', text.lower())
    return " ".join(_simple_stem(t) for t in tokens)


def optimize_keywords(cv_content: str, job_description: str = None, target_match: int = None) -> Dict[str, Any]:
    """Improved ATS score checker with multi-word phrase matching and domain/title alignment"""

    if not job_description:
        return get_default_analysis()

    # Normalize texts
    cv_norm = cv_content.lower()
    jd_norm = job_description.lower()

    # Multi-word & technical keyword extraction from JD
    jd_phrases = extract_ats_phrases(job_description)
    if not jd_phrases:
        # Fallback to single token extraction if phrase extraction returned nothing
        tokens = filter_keywords(re.findall(r'\b[a-zA-Z][a-zA-Z0-9\-]+\b', jd_norm))
        jd_phrases = list(set(tokens))

    # Match extracted phrases against CV content with term equivalence + stemming
    matched_phrases = []
    missing_phrases = []
    role_synonyms = {"developer", "engineer", "programmer", "specialist", "architect"}
    cv_stemmed = _stemmed_text(cv_norm)

    for phrase in jd_phrases:
        # Direct substring match
        if phrase in cv_norm:
            matched_phrases.append(phrase)
            continue
        # Multi-word phrase: check if core technical terms exist in CV
        words = phrase.split()
        if len(words) > 1:
            core_words = [w for w in words if w not in role_synonyms]
            if core_words and all(w in cv_norm for w in core_words):
                matched_phrases.append(phrase)
                continue
        # Stemmed match: "automation" matches "automated", "management" matches "managing"
        phrase_stemmed = " ".join(_simple_stem(w) for w in words)
        if phrase_stemmed in cv_stemmed:
            matched_phrases.append(phrase)
            continue
        missing_phrases.append(phrase)

    keyword_match_pct = round(len(matched_phrases) / len(jd_phrases) * 100) if jd_phrases else 0
    keyword_score = min(55, round(keyword_match_pct * 0.55))  # Max 55 pts for keyword alignment

    # Quantification score (Max 15 pts)
    quantitative_pct = calculate_quantitative_percentage(cv_content)
    quantitative_score = min(15, round(quantitative_pct * 0.25))

    # Formatting score (Max 15 pts)
    validation = validate_cv_format(cv_content)
    format_score = 15 if validation["valid"] else 10

    # Job title & domain matching (Max 15 pts)
    title_match = 5
    domain_score = 10

    # Job title extraction & matching against top section of CV
    job_title = _extract_jd_title(job_description)
    if job_title:
        title_lower = job_title.strip().lower()
        # Check if the extracted title (or its significant tokens) appears in the CV header
        if title_lower in cv_norm[:600]:
            title_match = 15
        else:
            # Check individual meaningful tokens (3+ chars) of the title
            title_tokens = [t for t in re.findall(r'\b[a-zA-Z]{3,}\b', title_lower)]
            matching_title_tokens = [t for t in title_tokens if t in cv_norm[:600]]
            if len(matching_title_tokens) >= 2:
                title_match = 15
            elif len(matching_title_tokens) == 1:
                title_match = 10
    else:
        # Fallback: check first few lines of JD for title-like content
        jd_first_lines = " ".join(job_description.splitlines()[:5]).lower()
        title_tokens = filter_keywords(re.findall(r'\b[a-zA-Z]{3,}\b', jd_first_lines))
        matching_title_tokens = [t for t in title_tokens[:4] if t in cv_norm[:400]]
        if len(matching_title_tokens) >= 2:
            title_match = 15
        elif len(matching_title_tokens) == 1:
            title_match = 10

    # Domain relevance
    domain_terms = extract_domain_keywords(job_description)
    if domain_terms:
        domain_overlap = [d for d in domain_terms if d.lower() in cv_norm]
        if len(domain_overlap) >= max(1, len(domain_terms) * 0.3):
            domain_score = 10
    else:
        domain_score = 10

    # Total ATS Score
    ats_score = keyword_score + quantitative_score + format_score + title_match + domain_score
    ats_score = min(100, max(0, ats_score))

    # Final suggestion block
    suggestions = []
    if quantitative_pct < 30:
        suggestions.append("Add more quantifiable achievements with specific numbers and percentages")
    if not validation["valid"]:
        for issue in validation.get("issues", []):
            suggestions.append(f"Format suggestion: {issue}")
    if title_match < 15:
        suggestions.append("Include exact target job title in the professional summary or header")
    if keyword_match_pct < 85 and missing_phrases:
        top_missing = missing_phrases[:3]
        suggestions.append(f"Surface relevant experience with key JD terms: {', '.join(top_missing)}")

    return {
        "score": ats_score,
        "keyword_match": keyword_match_pct,
        "suggestions": suggestions,
        "missing_keywords": missing_phrases[:10],
        "strengths": validation.get("strengths", ["Standard structure", "Relevant domain terms"])
    }

def get_default_analysis():
    """Return default analysis when AI fails"""
    return {
        'score': 70,
        'keyword_match': 65,
        'suggestions': ['Enhance technical skills section', 'Add more quantifiable results'],
        'missing_keywords': ['industry-specific terms'],
        'strengths': ['Good structure', 'Clear formatting']
    }

def extract_keywords_from_text(text: str) -> List[str]:
    """Extract potential keywords from text"""
    # Remove common words and extract meaningful terms
    words = re.findall(r'\b[A-Za-z]{3,}\b', text.lower())
    
    # Filter out common words
    common_words = {'the', 'and', 'for', 'with', 'from', 'that', 'this', 'have', 'was', 'were', 'been', 'are', 'will', 'would', 'could', 'should'}
    keywords = [word for word in set(words) if word not in common_words]
    
    return keywords[:20]  # Return top 20 keywords

def keyword_overlap_score(resume_text: str, jd_text: str) -> int:
    """Local resume↔job match score (0–100). Zero LLM calls.

    Percentage of *meaningful* job-description keywords (stop-words removed via
    filter_keywords) that also appear in the resume. Deterministic — same inputs
    always yield the same score. Used by the job aggregator (Phase 1).
    """
    if not resume_text or not jd_text:
        return 0

    token = r'\b[a-zA-Z][a-zA-Z0-9\-]+\b'
    jd_keywords = set(filter_keywords(re.findall(token, jd_text.lower())))
    if not jd_keywords:
        return 0

    cv_keywords = set(re.findall(token, resume_text.lower()))
    overlap = jd_keywords & cv_keywords
    return round(len(overlap) / len(jd_keywords) * 100)

def enforce_page_limit(content: str, max_pages: int = 2) -> str:
    """Enforce page limit by trimming content intelligently while preserving keywords"""
    
    if not content:
        return "Error: No content to limit"
    
    lines = content.split('\n')
    non_empty_lines = [line for line in lines if line.strip()]
    
    # Estimate lines per page (approximately 50 lines per page)
    lines_per_page = 50
    max_lines = max_pages * lines_per_page
    
    if len(non_empty_lines) <= max_lines:
        return content
    
    # Parse into sections
    sections = parse_content_sections(content)
    
    # Priority order for sections (higher priority = never trimmed)
    priority_sections = [
        'professional summary',
        'key skills',
        'work experience',
        'education',
        'certifications',
        'projects',
        'awards',
        'languages',
        'hobbies'
    ]
    
    # Sections that should NEVER be trimmed (preserve keywords)
    never_trim_sections = {'key skills'}
    
    # Rebuild content with priority
    rebuilt_lines = []
    current_line_count = 0
    
    # Add name/header first
    name_section = get_name_section(sections)
    if name_section:
        rebuilt_lines.extend(name_section)
        current_line_count += len(name_section)
    
    # Add sections by priority
    for priority in priority_sections:
        if current_line_count >= max_lines:
            break
            
        section_content = get_section_by_priority(sections, priority)
        if section_content:
            available_lines = max_lines - current_line_count
            
            # Check if this section should never be trimmed
            if priority in never_trim_sections:
                # Add entire section without trimming
                rebuilt_lines.extend(section_content)
                current_line_count += len(section_content)
            elif available_lines > 0:
                # Trim section if necessary, but preserve minimum content
                trimmed_section = trim_section_content_smart(
                    section_content, 
                    available_lines,
                    preserve_min_bullets=2 if priority == 'work experience' else 0
                )
                rebuilt_lines.extend(trimmed_section)
                current_line_count += len(trimmed_section)
    
    return '\n'.join(rebuilt_lines)

def parse_content_sections(content: str) -> Dict[str, List[str]]:
    """Parse content into sections"""
    sections = {}
    current_section = None
    current_content = []
    
    lines = content.split('\n')
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # Check if this is a section header
        if line.endswith(':') and len(line) > 1 and line.isupper():
            # Save previous section
            if current_section:
                sections[current_section.lower()] = current_content
            
            # Start new section
            current_section = line[:-1]  # Remove colon
            current_content = []
        elif ':' in line and len(line.split(':')) == 2:
            # Alternative section header format
            if current_section:
                sections[current_section.lower()] = current_content
            
            current_section = line.split(':')[0].strip()
            current_content = [line.split(':')[1].strip()] if line.split(':')[1].strip() else []
        else:
            if current_section:
                current_content.append(line)
            else:
                # This might be the name or first line
                sections['header'] = sections.get('header', []) + [line]
    
    # Save last section
    if current_section:
        sections[current_section.lower()] = current_content
    
    return sections

def get_name_section(sections: Dict[str, List[str]]) -> List[str]:
    """Get name/header section"""
    if 'header' in sections:
        return sections['header']
    return []

def get_section_by_priority(sections: Dict[str, List[str]], priority: str) -> List[str]:
    """Get section content by priority keyword"""
    for section_name, content in sections.items():
        if priority in section_name.lower():
            return [f"{section_name.upper()}:"] + content
    return []

def trim_section_content(section_content: List[str], max_lines: int) -> List[str]:
    """Trim section content to fit within line limit"""
    if len(section_content) <= max_lines:
        return section_content
    
    # Keep header and trim content
    if section_content and section_content[0].endswith(':'):
        header = [section_content[0]]
        content = section_content[1:max_lines-1]
        return header + content
    else:
        return section_content[:max_lines]

def trim_section_content_smart(section_content: List[str], max_lines: int, preserve_min_bullets: int = 0) -> List[str]:
    """Smart trim that preserves minimum bullets and technology mentions"""
    if len(section_content) <= max_lines:
        return section_content
    
    if not section_content:
        return []
    
    # If we have a header, keep it
    header = []
    content = section_content
    if section_content[0].endswith(':'):
        header = [section_content[0]]
        content = section_content[1:]
    
    available_for_content = max_lines - len(header)
    
    if available_for_content <= 0:
        return header
    
    # If we need to preserve minimum bullets, find bullet lines
    if preserve_min_bullets > 0:
        bullet_lines = []
        non_bullet_lines = []
        
        for line in content:
            stripped = line.strip()
            if stripped.startswith('•') or stripped.startswith('-') or stripped.startswith('*'):
                bullet_lines.append(line)
            else:
                non_bullet_lines.append(line)
        
        # Keep minimum bullets + fill remaining space with other content
        kept_bullets = bullet_lines[:preserve_min_bullets]
        remaining_bullets = bullet_lines[preserve_min_bullets:]
        
        # Calculate remaining space
        space_for_other = available_for_content - len(kept_bullets)
        
        # Add non-bullet content first (like company headers, dates)
        kept_non_bullet = non_bullet_lines[:space_for_other]
        
        # Combine and trim to fit
        result = header + kept_non_bullet + kept_bullets
        
        # If still over limit, trim from the end (remove later bullets)
        if len(result) > max_lines:
            result = result[:max_lines]
        
        return result
    else:
        # Simple trim
        return header + content[:available_for_content]

def calculate_quantitative_percentage(content: str) -> float:
    """Calculate percentage of quantitative content"""
    lines = content.split('\n')
    quantitative_lines = 0
    total_content_lines = 0
    
    # Pattern to match numbers, percentages, and quantifiable metrics
    quantitative_pattern = r'(\d+(?:\.\d+)?(?:%|K|M|B|k|m|b|\+|,\d+)*|\$\d+|increased?|decreased?|improved?|reduced?|saved?|generated?|achieved?)'
    
    for line in lines:
        line = line.strip()
        if line and not line.endswith(':'):  # Skip empty lines and headers
            total_content_lines += 1
            if re.search(quantitative_pattern, line, re.IGNORECASE):
                quantitative_lines += 1
    
    if total_content_lines == 0:
        return 0.0
    
    return (quantitative_lines / total_content_lines) * 100

def enhance_with_action_verbs(content: str, intensity: str = "High") -> str:
    """Enhance content with action verbs"""
    
    action_verbs = {
        "Moderate": [
            "managed", "developed", "created", "implemented", "led", "coordinated",
            "designed", "analyzed", "improved", "organized", "planned", "supervised"
        ],
        "High": [
            "spearheaded", "orchestrated", "revolutionized", "transformed", "pioneered",
            "architected", "optimized", "streamlined", "accelerated", "amplified",
            "executed", "delivered", "achieved", "established", "initiated"
        ],
        "Very High": [
            "spearheaded", "orchestrated", "pioneered", "revolutionized", "transformed",
            "engineered", "architected", "optimized", "streamlined", "accelerated",
            "masterminded", "championed", "galvanized", "maximized", "propelled"
        ]
    }
    
    # This is a simplified implementation
    # In practice, you'd use more sophisticated NLP to replace verbs contextually
    weak_verbs = ["worked", "did", "made", "helped", "was responsible for", "handled"]
    replacement_verbs = action_verbs.get(intensity, action_verbs["High"])
    
    enhanced_content = content
    for i, weak_verb in enumerate(weak_verbs):
        if i < len(replacement_verbs):
            enhanced_content = re.sub(
                r'\b' + weak_verb + r'\b',
                replacement_verbs[i],
                enhanced_content,
                flags=re.IGNORECASE
            )
    
    return enhanced_content

def validate_cv_format(content: str) -> Dict[str, Any]:
    """Validate CV format and structure"""
    issues = []
    suggestions = []
    
    # Check for essential sections
    essential_sections = ['professional summary', 'experience', 'skills', 'education']
    content_lower = content.lower()
    
    for section in essential_sections:
        if section not in content_lower:
            issues.append(f"Missing {section} section")
    
    # Check for contact information
    if not re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', content):
        issues.append("Missing email address")
    
    # Check for phone number (support flexible local and international phone formats)
    if not re.search(r'(?:\+\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b|\+\d{7,15}\b', content):
        issues.append("Missing phone number")
    
    # Check for quantifiable achievements
    quantitative_percent = calculate_quantitative_percentage(content)
    if quantitative_percent < 30:
        suggestions.append("Add more quantifiable achievements with numbers and percentages")
    
    # Check content length
    word_count = len(content.split())
    if word_count < 100:
        issues.append("CV content is too short")
    elif word_count > 800:
        suggestions.append("Consider condensing content for better readability")
    
    return {
        'valid': len(issues) == 0,
        'issues': issues,
        'suggestions': suggestions,
        'quantitative_percentage': quantitative_percent,
        'word_count': word_count
    }

def format_processing_time(seconds: float) -> str:
    """Format processing time for display"""
    if seconds < 1:
        return f"{seconds*1000:.0f}ms"
    elif seconds < 60:
        return f"{seconds:.1f}s"
    else:
        minutes = int(seconds // 60)
        remaining_seconds = seconds % 60
        return f"{minutes}m {remaining_seconds:.1f}s"

def sanitize_filename(filename: str) -> str:
    """Sanitize filename for download"""
    # Remove invalid characters
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # Remove multiple underscores
    sanitized = re.sub(r'_+', '_', sanitized)
    # Remove leading/trailing underscores
    sanitized = sanitized.strip('_')
    return sanitized

def estimate_reading_time(content: str) -> int:
    """Estimate reading time in seconds"""
    words = len(content.split())
    # Average reading speed: 200-250 words per minute
    reading_speed = 225  # words per minute
    return int((words / reading_speed) * 60)

def extract_contact_info(content: str) -> Dict[str, str]:
    """Extract contact information from CV"""
    contact_info = {}
    
    # Extract email
    email_match = re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', content)
    if email_match:
        contact_info['email'] = email_match.group()
    
    # Extract phone
    phone_match = re.search(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', content)
    if phone_match:
        contact_info['phone'] = phone_match.group()
    
    # Extract LinkedIn
    linkedin_match = re.search(r'linkedin\.com/in/[\w-]+', content, re.IGNORECASE)
    if linkedin_match:
        contact_info['linkedin'] = linkedin_match.group()
    
    return contact_info

def calculate_ats_score(cv_content: str, job_description: str) -> int:
    """Calculate ATS compatibility score"""
    try:
        # Use keyword analysis
        analysis = optimize_keywords(cv_content, job_description)
        return analysis.get('score', 75)
    except:
        # Fallback calculation
        jd_words = set(job_description.lower().split())
        cv_words = set(cv_content.lower().split())
        
        common_words = jd_words.intersection(cv_words)
        if len(jd_words) > 0:
            match_ratio = len(common_words) / len(jd_words)
            return min(100, int(match_ratio * 100) + 30)  # Add base score
        
        return 75  # Default score

def get_improvement_suggestions(cv_content: str, job_description: str) -> List[str]:
    """Get specific improvement suggestions"""
    suggestions = []
    
    # Check quantitative content
    quant_percent = calculate_quantitative_percentage(cv_content)
    if quant_percent < 50:
        suggestions.append("Add more quantifiable achievements with specific numbers and percentages")
    
    # Check for action verbs
    weak_verbs = ["worked", "did", "made", "helped", "was responsible for"]
    for verb in weak_verbs:
        if verb in cv_content.lower():
            suggestions.append(f"Replace weak verbs like '{verb}' with stronger action verbs")
            break
    
    # Check for job-specific keywords
    if job_description:
        analysis = optimize_keywords(cv_content, job_description)
        missing_keywords = analysis.get('missing_keywords', [])
        if missing_keywords:
            suggestions.append(f"Include these important keywords: {', '.join(missing_keywords[:3])}")
    
    # Check formatting
    validation = validate_cv_format(cv_content)
    suggestions.extend(validation['suggestions'])
    
    return suggestions[:5]  # Return top 5 suggestions

def extract_domain_keywords(job_description: str) -> List[str]:
    """Extract domain-relevant keywords from JD"""
    clinical_terms = ['clinic', 'dental', 'oral', 'patient', 'surgery', 'anesthesia', 'teeth', 'hygiene', 'prosthodontics', 'radiographs']
    tech_terms = ['sql', 'python', 'tableau', 'pipeline', 'dashboard', 'kafka', 'data engineering']

    # You can expand this logic to classify JD domain more smartly
    if any(word in job_description.lower() for word in clinical_terms):
        return clinical_terms
    elif any(word in job_description.lower() for word in tech_terms):
        return tech_terms
    return []

def get_all_country_dial_codes(default_region="IN"):
    """Return (label, region, dial_code) tuples for all supported regions."""
    items = []
    for region in sorted(phonenumbers.SUPPORTED_REGIONS):
        try:
            code = phonenumbers.country_code_for_region(region)
            country = pycountry.countries.get(alpha_2=region)
            name = getattr(country, "name", region)
            label = f"{name} (+{code})"
            items.append((label, region, f"+{code}"))
        except Exception:
            continue
    # Put default region (e.g., India) at the top
    items.sort(key=lambda x: (x[1] != default_region, x[0]))
    return items