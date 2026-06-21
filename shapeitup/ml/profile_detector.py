"""
profile_detector.py
-------------------
Semantic profile detection using sentence-transformer embeddings.

Replaces pure keyword scoring with vector similarity so that
"a REST API for employee records" matches 'product-service' even
without any of the exact keywords in the YAML file.

Two-stage approach:
  1. Load YAML profiles (same schema as wrkflw profiles/).
  2. Encode profile descriptions + input text.
  3. Cosine similarity → highest-scoring non-general profile wins.
  4. Falls back to keyword scoring when embeddings unavailable.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ProfileMatch:
    profile_id: str
    mode: str
    rationale: str
    delivery_kind: str
    runtime_surface: str
    assurance_level: str
    workflow_strategy: str
    domain_packs: list[str]
    score: float           # 0.0 – 1.0 (semantic similarity or keyword score)
    method: str            # "embedding" | "keyword" | "fallback"


class SemanticProfileDetector:
    """
    Load YAML profile files and rank them by semantic similarity to input text.
    """

    def __init__(
        self,
        profiles_dir: Path | None = None,
        model_name: str = "all-MiniLM-L6-v2",
    ) -> None:
        default_dir = Path(__file__).resolve().parents[3] / "profiles"
        self._profiles_dir = profiles_dir or default_dir
        self._model_name = model_name
        self._encoder: Any = None
        self._profiles: list[dict[str, Any]] = []
        self._profile_embeddings: list[list[float]] = []
        self._loaded = False

    # ── Loading ────────────────────────────────────────────────────────────────

    def _load_profiles(self) -> None:
        if self._loaded:
            return
        self._profiles = []
        if not self._profiles_dir.exists():
            self._loaded = True
            return
        for path in sorted(self._profiles_dir.glob("*.yaml")):
            try:
                data = _load_yaml(path.read_text(encoding="utf-8"))
                self._profiles.append(data)
            except Exception:
                pass
        self._loaded = True

    def _get_encoder(self) -> Any:
        if self._encoder is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._encoder = SentenceTransformer(self._model_name)
                self._encoder_type = "sentence-transformers"
            except ImportError:
                self._encoder = _TfidfEncoder()
                self._encoder_type = "tfidf"
        return self._encoder

    # ── Embedding generation ───────────────────────────────────────────────────

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb) if (na and nb) else 0.0

    def _profile_description(self, p: dict[str, Any]) -> str:
        """Synthesise a rich text description of the profile for embedding."""
        parts = [
            p.get("rationale", ""),
            p.get("delivery_kind", ""),
            p.get("runtime_surface", ""),
            " ".join(p.get("domain_packs") or []),
            p.get("workflow_strategy", ""),
        ]
        # Include required_all terms as context
        for group in p.get("required_all") or []:
            parts.extend(group if isinstance(group, list) else [])
        # Include scored_terms
        for entry in p.get("scored_terms") or []:
            if isinstance(entry, dict):
                parts.extend(entry.get("terms") or [])
        return " ".join(str(x) for x in parts if x)

    def _ensure_embeddings(self) -> None:
        """Lazy-compute profile embeddings on first detect() call."""
        if self._profile_embeddings:
            return
        if not self._profiles:
            return
        enc = self._get_encoder()
        descriptions = [self._profile_description(p) for p in self._profiles]
        self._profile_embeddings = enc.encode(descriptions)

    # ── Detection ──────────────────────────────────────────────────────────────

    def detect(self, text: str) -> ProfileMatch:
        """Return the best-matching profile for text."""
        self._load_profiles()

        if not self._profiles:
            return _general_fallback("embedding", 0.0)

        try:
            self._ensure_embeddings()
            enc = self._get_encoder()
            text_emb = enc.encode([text])[0]

            best_score = 0.0
            best_profile: dict[str, Any] | None = None

            for prof, prof_emb in zip(self._profiles, self._profile_embeddings):
                pid = str(prof.get("id", ""))
                if pid.startswith("general"):
                    continue
                sim = self._cosine(text_emb, prof_emb)
                if sim > best_score:
                    best_score = sim
                    best_profile = prof

            method = getattr(self, "_encoder_type", "embedding")

            if best_profile is not None and best_score >= 0.30:
                return ProfileMatch(
                    profile_id=str(best_profile.get("id", "")),
                    mode=str(best_profile.get("mode", "general-delivery")),
                    rationale=str(best_profile.get("rationale", "")),
                    delivery_kind=str(best_profile.get("delivery_kind", "general")),
                    runtime_surface=str(best_profile.get("runtime_surface", "unspecified")),
                    assurance_level=str(best_profile.get("assurance_level", "normal")),
                    workflow_strategy=str(best_profile.get("workflow_strategy", "simple")),
                    domain_packs=list(best_profile.get("domain_packs") or ["general"]),
                    score=best_score,
                    method=method,
                )

        except Exception:
            pass

        # Fallback: keyword scoring from profiles
        return self._keyword_fallback(text)

    def _keyword_fallback(self, text: str) -> ProfileMatch:
        """Keyword-based scoring as fallback when embeddings fail."""
        lowered = text.lower()
        best_score = 0
        best_profile: dict[str, Any] | None = None

        for prof in self._profiles:
            if str(prof.get("id", "")).startswith("general"):
                continue
            score = 0
            for group in prof.get("required_all") or []:
                if not any(t.lower() in lowered for t in (group or [])):
                    score = 0
                    break
            else:
                for entry in prof.get("scored_terms") or []:
                    if isinstance(entry, dict):
                        terms = entry.get("terms") or []
                        weight = int(entry.get("weight", 1))
                        if any(t.lower() in lowered for t in terms):
                            score += weight
            threshold = int(prof.get("threshold", 1))
            if score >= threshold and score > best_score:
                best_score = score
                best_profile = prof

        if best_profile is not None:
            return ProfileMatch(
                profile_id=str(best_profile.get("id", "")),
                mode=str(best_profile.get("mode", "general-delivery")),
                rationale=str(best_profile.get("rationale", "")),
                delivery_kind=str(best_profile.get("delivery_kind", "general")),
                runtime_surface=str(best_profile.get("runtime_surface", "unspecified")),
                assurance_level=str(best_profile.get("assurance_level", "normal")),
                workflow_strategy=str(best_profile.get("workflow_strategy", "simple")),
                domain_packs=list(best_profile.get("domain_packs") or ["general"]),
                score=float(best_score),
                method="keyword",
            )

        return _general_fallback("keyword", 0.0)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _general_fallback(method: str, score: float) -> ProfileMatch:
    return ProfileMatch(
        profile_id="general-delivery",
        mode="general-delivery",
        rationale="No specialized profile matched; treating as general staged delivery.",
        delivery_kind="general",
        runtime_surface="unspecified",
        assurance_level="normal",
        workflow_strategy="simple",
        domain_packs=["general"],
        score=score,
        method=method,
    )


def _load_yaml(text: str) -> dict[str, Any]:
    try:
        import yaml
        result = yaml.safe_load(text)
        return result if isinstance(result, dict) else {}
    except ImportError:
        return _minimal_yaml(text)


def _minimal_yaml(text: str) -> dict[str, Any]:
    """Minimal YAML parser for scalar fields only (no nested structures)."""
    result: dict[str, Any] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith(" ") and ":" in stripped:
            key, _, val = stripped.partition(":")
            val = val.strip().strip('"\'')
            if val:
                result[key.strip()] = val
    return result


class _TfidfEncoder:
    """TF-IDF fallback encoder when sentence-transformers is unavailable."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        from sklearn.feature_extraction.text import TfidfVectorizer
        vec = TfidfVectorizer()
        matrix = vec.fit_transform(texts)
        return matrix.toarray().tolist()  # type: ignore[return-value]


# ── Module-level convenience ───────────────────────────────────────────────────

_detector: SemanticProfileDetector | None = None


def detect_profile(
    text: str,
    profiles_dir: Path | None = None,
) -> ProfileMatch:
    """Module-level convenience function — reuses a singleton detector."""
    global _detector
    if _detector is None or profiles_dir is not None:
        _detector = SemanticProfileDetector(profiles_dir=profiles_dir)
    return _detector.detect(text)
