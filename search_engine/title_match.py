"""
search_engine/title_match.py — Phase 3: Mandatory Title Guardrail & Similarity Scoring.
Enforces a hard title filter to reject irrelevant jobs immediately.
"""

from __future__ import annotations
import re
from search_engine.config import TITLE_SIMILARITY_THRESHOLD, STOP_WORDS, ABBREVIATIONS
from search_engine.normalizer import normalize_title


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    cleaned = re.sub(r"[^\w\s\.-]", " ", text.lower())
    words = re.split(r"[\s/]+", cleaned)
    tokens = []
    for w in words:
        w = w.strip(".,-")
        if w and w not in STOP_WORDS:
            tokens.append(w)
    return tokens


def token_set_ratio(s1: str, s2: str) -> float:
    """Calculate token set similarity ratio (0.0 to 100.0)."""
    t1 = set(_tokenize(s1))
    t2 = set(_tokenize(s2))
    if not t1 or not t2:
        return 0.0
    intersection = t1.intersection(t2)
    if not intersection:
        return 0.0
    # Overlap relative to smaller set and average set size
    overlap_small = len(intersection) / min(len(t1), len(t2))
    overlap_avg = (2.0 * len(intersection)) / (len(t1) + len(t2))
    return max(overlap_small, overlap_avg) * 100.0


def token_overlap_ratio(query_title: str, job_title: str) -> float:
    """Calculate token overlap ratio relative to the query title tokens."""
    q_tokens = _tokenize(normalize_title(query_title))
    if not q_tokens:
        return 100.0
    j_tokens = set(_tokenize(normalize_title(job_title)))
    if not j_tokens:
        return 0.0
    matches = sum(1 for qt in q_tokens if qt in j_tokens)
    score = (matches / len(q_tokens)) * 100.0
    
    # Substring fallback for single-word queries (e.g. "Python")
    if len(q_tokens) == 1 and score < 100.0:
        q_raw = query_title.strip().lower()
        j_raw = job_title.strip().lower()
        if re.search(r"\b" + re.escape(q_raw) + r"\b", j_raw):
            score = max(score, 80.0)
            
    return min(100.0, score)


def compute_title_similarity(query_title: str, job_title: str) -> float:
    """Compute overall title similarity score (0.0 to 100.0)."""
    if not query_title or not query_title.strip():
        return 100.0
    if not job_title or not job_title.strip():
        return 0.0

    norm_q = normalize_title(query_title)
    norm_j = normalize_title(job_title)

    overlap = token_overlap_ratio(norm_q, norm_j)
    set_ratio = token_set_ratio(norm_q, norm_j)

    # Maximum of overlap ratio and set ratio
    similarity = max(overlap, set_ratio)
    return min(100.0, max(0.0, similarity))


def is_title_relevant(query_title: str, job_title: str, threshold: float = TITLE_SIMILARITY_THRESHOLD) -> bool:
    """Hard filter: Returns True if job title passes title similarity threshold, False otherwise."""
    if not query_title or not query_title.strip():
        return True
    sim = compute_title_similarity(query_title, job_title)
    return sim >= threshold
