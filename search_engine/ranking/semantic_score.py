"""
search_engine/ranking/semantic_score.py — Phase 17: Future Semantic Search Interface.
Pluggable interface for semantic embeddings & vector search.
"""

from typing import Any
import google.generativeai as genai
import numpy as np
from functools import lru_cache

@lru_cache(maxsize=1024)
def get_gemini_embedding(text: str) -> list[float]:
    """Get Gemini embedding for text, cached in memory."""
    if not text or not text.strip():
        return []
    try:
        result = genai.embed_content(
            model="models/text-embedding-004",
            content=text,
            task_type="retrieval_document"
        )
        return result.get('embedding', [])
    except Exception as e:
        print(f"Error getting Gemini embedding: {e}")
        return []

def compute_cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    if not vec1 or not vec2:
        return 50.0
    try:
        norm_v1 = np.linalg.norm(vec1)
        norm_v2 = np.linalg.norm(vec2)
        if norm_v1 == 0 or norm_v2 == 0:
            return 50.0
        return float(np.dot(vec1, vec2) / (norm_v1 * norm_v2))
    except Exception:
        return 50.0

class SemanticScorer:
    """Pluggable semantic embedding scorer interface using Gemini."""
    
    def score(self, query_title: str, job: Any, candidate_skills: str = "") -> float:
        """
        Returns semantic similarity score (0.0 to 100.0).
        Uses Gemini embeddings for deeper context matching.
        """
        if not query_title:
            return 50.0
            
        # Enrich the query text with candidate skills if provided
        query_text = f"{query_title} {candidate_skills}".strip()
        
        # Embed role title + company snippet for fast, cacheable semantic matching
        job_role_text = f"{getattr(job, 'title', '')} {getattr(job, 'company', '')}".strip()
        if not job_role_text:
            job_role_text = job_text[:300]
            
        q_emb = get_gemini_embedding(query_text[:1000])
        j_emb = get_gemini_embedding(job_role_text)
        
        if not q_emb or not j_emb:
            return 50.0
            
        sim = compute_cosine_similarity(q_emb, j_emb)
        # Cosine sim is -1 to 1. Map to 0-100 score.
        score = ((sim + 1) / 2.0) * 100
        return max(0.0, min(100.0, score))
