"""
search_engine/ranking/semantic_score.py — Phase 17: Future Semantic Search Interface.
Pluggable interface for semantic embeddings & vector search.
"""

from typing import Any


class SemanticScorer:
    """Pluggable semantic embedding scorer interface."""
    
    def score(self, query_title: str, job: Any) -> float:
        """Returns semantic similarity score (0.0 to 100.0). Placeholder returns neutral score."""
        return 50.0
