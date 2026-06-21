"""
context_ranker.py
-----------------
Ranks context slots by relevance to the current story/objective using TF-IDF,
replacing the fixed priority order in wrkflw's ContextBudget.

The fixed order (design-seed=0, capabilities=1, ..., memory=9) is good as a
baseline but ignores *what the current synthesis is actually about*.  If the
active story is about authentication, the security-related memory entries are
more valuable than an unrelated design-seed section.

This ranker:
  1. Vectorises all slot texts + the query (objective) with TF-IDF.
  2. Ranks slots by cosine similarity to the query.
  3. Optionally blends the similarity rank with the static priority tier
     so high-priority slots are never completely displaced.

Usage:
    from shapeitup.ml.context_ranker import rank_slots, ContextSlot

    slots = [
        ContextSlot(name="capabilities", text="...", static_priority=1),
        ContextSlot(name="memory",       text="...", static_priority=9),
    ]
    ranked = rank_slots(slots, objective="implement connection pooling")
    # slots now ordered by relevance to the objective
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class ContextSlot:
    name: str
    text: str
    static_priority: int = 50   # lower = higher base priority (matches wrkflw)
    char_limit: int | None = None


@dataclass(frozen=True)
class RankedSlot:
    name: str
    text: str
    static_priority: int
    similarity: float       # 0.0 – 1.0 cosine similarity to objective
    blended_score: float    # final sort key (lower = include first)
    char_limit: int | None = None

    @property
    def chars(self) -> int:
        return len(self.text)


def rank_slots(
    slots: list[ContextSlot],
    objective: str,
    blend_weight: float = 0.4,
) -> list[RankedSlot]:
    """
    Rank context slots by relevance to objective.

    Args:
        slots:         List of ContextSlot objects.
        objective:     The current story/synthesis objective text.
        blend_weight:  0.0 = pure similarity ranking,
                       1.0 = pure static priority order,
                       0.4 = default blend (similarity matters more).

    Returns:
        Slots sorted by blended_score ascending (most relevant first).
    """
    if not slots:
        return []

    if not objective.strip():
        # No objective — fall back to static priority order
        return [
            RankedSlot(
                name=s.name, text=s.text,
                static_priority=s.static_priority,
                similarity=0.0,
                blended_score=float(s.static_priority),
                char_limit=s.char_limit,
            )
            for s in sorted(slots, key=lambda x: x.static_priority)
        ]

    # Build corpus: objective first, then each slot
    texts = [objective] + [s.text for s in slots]
    similarities = _tfidf_similarities(texts)

    # Normalise static priorities to [0, 1]
    max_priority = max(s.static_priority for s in slots) or 1
    results: list[RankedSlot] = []

    for slot, sim in zip(slots, similarities):
        norm_priority = slot.static_priority / max_priority
        # Lower blended_score = include first
        # (1 - sim) because high similarity should come first
        blended = blend_weight * norm_priority + (1 - blend_weight) * (1.0 - sim)
        results.append(RankedSlot(
            name=slot.name,
            text=slot.text,
            static_priority=slot.static_priority,
            similarity=sim,
            blended_score=blended,
            char_limit=slot.char_limit,
        ))

    return sorted(results, key=lambda r: r.blended_score)


def _tfidf_similarities(texts: list[str]) -> list[float]:
    """
    Return cosine similarity of texts[1:] against texts[0] (the query).
    Uses scikit-learn TF-IDF; falls back to Jaccard on import error.
    """
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        vec = TfidfVectorizer(ngram_range=(1, 2), min_df=1)
        matrix = vec.fit_transform(texts)
        arr = matrix.toarray()
        query_vec = arr[0]
        sims: list[float] = []
        for doc_vec in arr[1:]:
            dot = float(query_vec @ doc_vec)
            nq = math.sqrt(float(query_vec @ query_vec))
            nd = math.sqrt(float(doc_vec @ doc_vec))
            sims.append(dot / (nq * nd) if (nq and nd) else 0.0)
        return sims
    except ImportError:
        # Jaccard fallback (no sklearn)
        q_tokens = set(texts[0].lower().split())
        sims = []
        for doc in texts[1:]:
            d_tokens = set(doc.lower().split())
            if not q_tokens and not d_tokens:
                sims.append(0.0)
            else:
                sims.append(len(q_tokens & d_tokens) / len(q_tokens | d_tokens))
        return sims


def fill_budget(
    slots: list[ContextSlot],
    objective: str,
    total_limit: int = 20_000,
    blend_weight: float = 0.4,
) -> tuple[str, list[dict]]:
    """
    Fill a character budget with the most relevant slots first.

    Returns:
        (assembled_text, manifest_entries)
        manifest_entries: list of dicts with name/status/chars/similarity
    """
    ranked = rank_slots(slots, objective, blend_weight)
    remaining = total_limit
    parts: list[str] = []
    manifest: list[dict] = []

    for rs in ranked:
        if not rs.text.strip():
            manifest.append({"name": rs.name, "status": "skipped",
                              "chars": 0, "similarity": rs.similarity})
            continue
        if remaining <= 0:
            manifest.append({"name": rs.name, "status": "omitted",
                              "chars": len(rs.text), "similarity": rs.similarity})
            continue
        if len(rs.text) <= remaining:
            parts.append(rs.text)
            manifest.append({"name": rs.name, "status": "included",
                              "chars": len(rs.text), "similarity": rs.similarity})
            remaining -= len(rs.text)
        else:
            truncated = rs.text[:remaining] + "\n...[truncated]"
            parts.append(truncated)
            manifest.append({"name": rs.name, "status": "truncated",
                              "chars": remaining, "similarity": rs.similarity})
            remaining = 0

    return "\n\n".join(parts), manifest
