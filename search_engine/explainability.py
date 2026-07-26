"""
search_engine/explainability.py — Phase 14: Explainability Engine.
Generates human-readable why_matched bullet points and metadata for UI rendering.
"""

from __future__ import annotations


def generate_explainability(
    job_title: str,
    query_title: str,
    title_similarity: float,
    matched_skills: list[str],
    posted_date: str,
    remote_type: str,
    match_score: int,
    seniority: str = ""
) -> list[str]:
    """Generate user-facing 'Why this job?' bullet points."""
    bullets = []

    # Title match reason
    if title_similarity >= 80.0:
        bullets.append(f"✔ High title match for '{query_title or job_title}' ({int(title_similarity)}%)")
    elif title_similarity >= 40.0:
        bullets.append(f"✔ Relevant title match ({int(title_similarity)}%)")

    # Skills reason
    if matched_skills:
        bullets.append(f"✔ Skills match: {', '.join(matched_skills[:5])}")

    # Freshness reason
    if posted_date:
        bullets.append(f"✔ Posted {posted_date}")

    # Remote reason
    if remote_type and "Remote" in remote_type:
        bullets.append(f"✔ {remote_type}")

    # Seniority/Level reason
    if seniority:
        bullets.append(f"✔ Level: {seniority}")

    if not bullets:
        bullets.append("✔ Passed relevance filters")

    return bullets
