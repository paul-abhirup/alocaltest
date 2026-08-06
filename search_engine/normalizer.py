from __future__ import annotations
import re
from functools import lru_cache
from search_engine.config import ABBREVIATIONS, TECH_STANDARDIZATION, STOP_WORDS


def normalize_title(raw_title: str) -> str:
    """Normalize job titles: expand abbreviations, clean punctuation, standardize tech names."""
    if not raw_title:
        return ""
    
    text = raw_title.strip()
    
    # Pre-process abbreviations
    text = re.sub(r"\bsr\b\.?", "Senior", text, flags=re.IGNORECASE)
    text = re.sub(r"\bjr\b\.?", "Junior", text, flags=re.IGNORECASE)
    text = re.sub(r"\breact\.?js\b", "React", text, flags=re.IGNORECASE)
    text = re.sub(r"\bnode\.?js\b", "Node.js", text, flags=re.IGNORECASE)
    text = re.sub(r"\bpy dev\b", "Python Developer", text, flags=re.IGNORECASE)
    text = re.sub(r"\bdev\b", "Developer", text, flags=re.IGNORECASE)
    
    # Clean up redundant spaces & punctuation
    text = re.sub(r"[^\w\s\.-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    
    # Standardize tech names (e.g. reactjs -> React)
    words = text.split()
    normalized_words = []
    for w in words:
        clean_w = w.lower().strip(".,-")
        if clean_w in TECH_STANDARDIZATION:
            normalized_words.append(TECH_STANDARDIZATION[clean_w])
        else:
            normalized_words.append(w.capitalize() if w.islower() else w)
            
    return " ".join(normalized_words)


@lru_cache(maxsize=128)
def _generate_search_variants_cached(query_title: str) -> tuple[str, ...]:
    """Internal cached helper for generating title variants."""
    if not query_title or not query_title.strip():
        return ()

    try:
        from utils import get_gemini_response
        import json

        prompt = f"""
        You are an expert technical recruiter. Analyze the following job search query and generate:
        1. A normalized primary title.
        2. Up to 3 alternative job titles or search variants.
        
        Query: "{query_title}"
        
        Respond ONLY in valid JSON format exactly like this:
        {{
            "primary_title": "...",
            "variants": ["...", "...", "..."]
        }}
        """
        
        response_text = get_gemini_response(prompt, model="gemini-2.5-flash")
        
        # Clean markdown code block if present
        clean_text = response_text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        if clean_text.startswith("```"):
            clean_text = clean_text[3:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
            
        data = json.loads(clean_text.strip())
        
        variants = [query_title.strip()]
        if "primary_title" in data and data["primary_title"]:
            variants.append(data["primary_title"])
        if "variants" in data and isinstance(data["variants"], list):
            variants.extend(data["variants"])
            
    except Exception as e:
        print(f"Error calling Gemini for query expansion: {e}")
        # Fallback to the old logic
        norm = normalize_title(query_title)
        variants = [query_title.strip(), norm]
        
        low = norm.lower()
        
        # Dev / Engineer variations
        if "developer" in low:
            variants.append(re.sub(r"\bdeveloper\b", "Engineer", norm, flags=re.IGNORECASE))
            variants.append(re.sub(r"\bdeveloper\b", "Software Engineer", norm, flags=re.IGNORECASE))
        elif "engineer" in low:
            variants.append(re.sub(r"\bengineer\b", "Developer", norm, flags=re.IGNORECASE))
            variants.append(re.sub(r"\bsoftware engineer\b", "Developer", norm, flags=re.IGNORECASE))
    
        # Tech-specific role aliases
        if "python" in low:
            variants.extend(["Python Developer", "Python Engineer", "Backend Python Engineer", "Software Engineer Python"])
        elif "react" in low:
            variants.extend(["React Developer", "React Engineer", "Frontend React Developer"])
        elif "node" in low:
            variants.extend(["Node.js Developer", "Node Developer", "Backend Node Engineer"])
        elif "java" in low and "javascript" not in low:
            variants.extend(["Java Developer", "Java Software Engineer", "Backend Java Engineer"])

    # Deduplicate preserving order
    seen = set()
    deduped_variants = []
    for v in variants:
        clean_v = v.strip()
        if clean_v and clean_v.lower() not in seen:
            seen.add(clean_v.lower())
            deduped_variants.append(clean_v)

    return tuple(deduped_variants)


def generate_search_variants(query_title: str) -> list[str]:
    """Generate search title variants/aliases to improve recall across external APIs using Gemini."""
    return list(_generate_search_variants_cached(query_title))
