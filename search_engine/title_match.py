"""
search_engine/title_match.py — Phase 3: Mandatory Title Guardrail & Similarity Scoring.
Enforces a hard title filter to reject irrelevant jobs immediately.

Key rules:
  1. Bidirectional core-token coverage: for multi-token queries, a minimum fraction of
     *distinctive* tokens must be found in the job title AND vice versa, or the role
     must be covered by synonym expansion. Generic role nouns ("developer", "manager",
     "designer") are excluded to prevent false matches.
  2. Stem matching: light suffix stripping so "design" ≈ "designer" ≈ "designing".
  3. Role synonym expansion: queries matching a ROLE_SYNONYMS key gain an expanded
     vocabulary so that related terms (e.g. "learning designer") count as hits.
  4. Full-phrase substring bonus: when the full multi-word query appears verbatim
     inside the job title, similarity is boosted to 100%.
"""
from __future__ import annotations
import re

from search_engine.config import (
    TITLE_SIMILARITY_THRESHOLD, STOP_WORDS,
    GENERIC_ROLE_NOUNS, ROLE_SYNONYMS, STEM_RULES, MIN_CORE_TOKEN_COVERAGE,
)
from search_engine.normalizer import normalize_title


def _stem_token(token: str) -> str:
    """Light suffix-stripping so tokens like 'designer' and 'designing' reduce to 'design'."""
    t = token.lower().strip(".,-")
    for pattern, replacement in STEM_RULES:
        if re.search(pattern, t):
            t = re.sub(pattern, replacement, t)
    return t


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    # Normalize hyphenated terms: "full-stack" -> "full stack", "front-end" -> "frontend"
    hyphenated = re.sub(r"(full[\s-]+stack)", "fullstack", text.lower())
    hyphenated = re.sub(r"(front[\s-]+end)", "frontend", hyphenated)
    hyphenated = re.sub(r"(back[\s-]+end)", "backend", hyphenated)
    hyphenated = re.sub(r"(ml[\s-]+ops)", "mlops", hyphenated)
    cleaned = re.sub(r"[^\w\s\.-]", " ", hyphenated)
    words = re.split(r"[\s/]+", cleaned)
    tokens = []
    for w in words:
        w = w.strip(".,-")
        if w and w not in STOP_WORDS:
            tokens.append(w)
    return tokens


def _distinctive_tokens(tokens: list[str]) -> list[str]:
    """Filter out generic role nouns so they don't drive false matches."""
    return [t for t in tokens if t.lower() not in GENERIC_ROLE_NOUNS]


def _stem_set(tokens: list[str]) -> set[str]:
    return {_stem_token(t) for t in tokens}


def _get_synonym_stems(query_title: str) -> set[str]:
    """Return stemmed tokens from all synonym entries for this query, if any."""
    q_low = normalize_title(query_title).lower().strip()
    syn_stems: set[str] = set()
    for key, synonyms in ROLE_SYNONYMS.items():
        if key in q_low or any(s in q_low for s in synonyms):
            for s in synonyms:
                for t in _tokenize(s):
                    syn_stems.add(_stem_token(t))
            break
    return syn_stems


def _token_hit(qt: str, j_stems: set[str], syn_stems: set[str]) -> bool:
    """True if query token qt matches a job stem directly or via synonym expansion."""
    if qt in j_stems:
        return True
    if syn_stems:
        if j_stems & syn_stems:
            return True
    return False


def token_set_ratio(s1: str, s2: str) -> float:
    """Calculate token set similarity ratio (0.0 to 100.0)."""
    t1 = set(_tokenize(s1))
    t2 = set(_tokenize(s2))
    if not t1 or not t2:
        return 0.0
    intersection = t1.intersection(t2)
    if not intersection:
        return 0.0
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

    if len(q_tokens) == 1 and score < 100.0:
        q_raw = query_title.strip().lower()
        j_raw = job_title.strip().lower()
        if re.search(r"\b" + re.escape(q_raw) + r"\b", j_raw):
            score = max(score, 80.0)

    return min(100.0, score)


def core_token_coverage(query_title: str, job_title: str) -> float:
    """Bidirectional core-token coverage (0.0-1.0).

    Returns the *minimum* of:
     - Fraction of query's distinctive tokens found in job.
     - Fraction of job's distinctive tokens found in query.
    This ensures bidirectional overlap: a job like 'Graphic Designer' won't
    match 'Instructional Designer' just because both share the generic 'designer'.
    When a query matches a ROLE_SYNONYMS key, the synonym vocabulary counts for both sides.

    Returns 1.0 if either side has no distinctive tokens.
    """
    q_all = _tokenize(normalize_title(query_title))
    j_all = _tokenize(normalize_title(job_title))
    q_distinctive = _distinctive_tokens(q_all)
    j_distinctive = _distinctive_tokens(j_all)

    # If either side has no distinctive tokens, soft-pass (rely on similarity)
    if not q_distinctive or not j_distinctive:
        return 1.0

    q_stems = _stem_set(q_distinctive)
    j_stems = _stem_set(j_distinctive)
    syn_stems = _get_synonym_stems(query_title)

    q_hits = sum(1 for qt in q_stems if _token_hit(qt, j_stems, syn_stems))
    j_hits = sum(1 for jt in j_stems if _token_hit(jt, q_stems, syn_stems))

    q_cov = q_hits / len(q_stems) if q_stems else 1.0
    j_cov = j_hits / len(j_stems) if j_stems else 1.0

    return min(q_cov, j_cov)


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
    similarity = max(overlap, set_ratio)

    # Full-phrase substring bonus: exact multi-word query inside job title
    q_low = norm_q.lower().strip()
    j_low = norm_j.lower().strip()
    if len(q_low.split()) >= 2 and q_low in j_low:
        similarity = max(similarity, 100.0)

    return min(100.0, max(0.0, similarity))


def is_title_relevant(query_title: str, job_title: str,
                      threshold: float = TITLE_SIMILARITY_THRESHOLD,
                      min_core: float = MIN_CORE_TOKEN_COVERAGE) -> bool:
    """Hard filter: True if job title passes both similarity AND core-token guardrails."""
    if not query_title or not query_title.strip():
        return True

    sim = compute_title_similarity(query_title, job_title)

    # Full-phrase match or high direct token overlap — always accept
    if sim >= threshold:
        cov = core_token_coverage(query_title, job_title)
        if cov >= min_core:
            return True

    # Zero direct token overlap but both sides fully covered by synonyms?
    # e.g. "Project Manager" ↔ "Scrum Master" has zero direct token overlap
    # but ROLE_SYNONYMS justifies it.  Allow these when core coverage is 1.0.
    if sim < threshold:
        cov = core_token_coverage(query_title, job_title)
        if cov >= 1.0:
            return True

    return False