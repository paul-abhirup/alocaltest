"""
search_engine/normalizer.py — Phase 1 & 2: Query Normalization & Search Variant Generation.
"""

from __future__ import annotations
import re
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


def generate_search_variants(query_title: str) -> list[str]:
    """Generate search title variants/aliases to improve recall across external APIs."""
    if not query_title or not query_title.strip():
        return []

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

    return deduped_variants
