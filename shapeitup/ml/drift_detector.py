"""
drift_detector.py
-----------------
Detects semantic drift between workflow/design artifacts and source code
using sentence-transformer embeddings — no LLM API call required.

Drift types detected:
  design_ahead     design describes features not yet visible in code
  code_ahead       code has moved beyond what design/workflow artifacts describe
  aligned          artifacts and code are semantically consistent
  insufficient     not enough text to make a determination

Usage:
    from shapeitup.ml.drift_detector import DriftDetector

    detector = DriftDetector()
    result = detector.detect(
        design_text="We need an MCP server with SQL Server connection pooling...",
        code_snippets=["class MCPServer:", "def connect(self, dsn: str):"],
    )
    print(result.drift_type, result.score, result.explanation)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

DriftType = Literal["design_ahead", "code_ahead", "aligned", "insufficient"]

# Threshold tuning
_ALIGNED_THRESHOLD = 0.60      # cosine similarity ≥ this → aligned
_DRIFT_THRESHOLD = 0.35        # cosine similarity < this → significant drift
_MIN_CHARS = 50                # minimum text length to attempt detection


@dataclass(frozen=True)
class DriftResult:
    drift_type: DriftType
    score: float                        # 0.0 (no overlap) – 1.0 (identical)
    explanation: str
    design_terms: list[str] = field(default_factory=list)   # key terms in design not in code
    code_terms: list[str] = field(default_factory=list)     # key terms in code not in design

    @property
    def needs_reconciliation(self) -> bool:
        return self.drift_type in {"design_ahead", "code_ahead"}

    @property
    def severity(self) -> str:
        if self.score >= _ALIGNED_THRESHOLD:
            return "none"
        if self.score >= _DRIFT_THRESHOLD:
            return "minor"
        return "significant"


class DriftDetector:
    """
    Embedding-based drift detector.

    Lazy-loads sentence-transformers on first use so import is instant.
    Falls back gracefully to TF-IDF cosine similarity when
    sentence-transformers is unavailable.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self._model_name = model_name
        self._encoder: object | None = None   # lazy

    def _encode(self, texts: list[str]) -> "list[list[float]]":
        """Encode texts to embeddings. Lazy-loads the model."""
        if self._encoder is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._encoder = SentenceTransformer(self._model_name)
                self._backend = "sentence-transformers"
            except ImportError:
                self._encoder = _TfidfFallback()
                self._backend = "tfidf"
        return self._encoder.encode(texts)  # type: ignore[union-attr]

    @staticmethod
    def _cosine(a: "list[float]", b: "list[float]") -> float:
        import math
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    @staticmethod
    def _key_terms(text: str, other: str, top_n: int = 8) -> list[str]:
        """Return terms prominent in text but not in other (simple frequency diff)."""
        def tokenise(t: str) -> list[str]:
            return re.findall(r"\b[a-z][a-z0-9_]{3,}\b", t.lower())

        text_tokens = tokenise(text)
        other_tokens = set(tokenise(other))
        from collections import Counter
        counts = Counter(text_tokens)
        return [w for w, _ in counts.most_common(20) if w not in other_tokens][:top_n]

    def detect(
        self,
        design_text: str,
        code_snippets: list[str],
        workflow_text: str = "",
    ) -> DriftResult:
        """
        Compare design/workflow artifacts against code snippets.

        Args:
            design_text:    Content of design.md, capabilities.md, stories.md, etc.
            code_snippets:  List of source code strings (file contents, summaries).
            workflow_text:  Optional additional workflow artifact text.

        Returns:
            DriftResult describing alignment state.
        """
        combined_design = "\n\n".join(filter(None, [design_text, workflow_text]))
        combined_code = "\n\n".join(code_snippets)

        if len(combined_design.strip()) < _MIN_CHARS or len(combined_code.strip()) < _MIN_CHARS:
            return DriftResult(
                drift_type="insufficient",
                score=0.0,
                explanation="Not enough text in design or code to determine drift.",
            )

        try:
            embeddings = self._encode([combined_design, combined_code])
            score = float(self._cosine(embeddings[0], embeddings[1]))
        except Exception as exc:
            return DriftResult(
                drift_type="insufficient",
                score=0.0,
                explanation=f"Embedding failed: {exc}",
            )

        design_terms = self._key_terms(combined_design, combined_code)
        code_terms = self._key_terms(combined_code, combined_design)

        if score >= _ALIGNED_THRESHOLD:
            drift_type: DriftType = "aligned"
            explanation = (
                f"Design and code are semantically aligned (similarity={score:.2f}). "
                "No reconciliation needed."
            )
        elif design_terms and len(design_terms) > len(code_terms):
            drift_type = "design_ahead"
            explanation = (
                f"Design mentions concepts not yet visible in code (similarity={score:.2f}). "
                f"Missing in code: {', '.join(design_terms[:4])}. "
                "Consider running wrkflw:reconcile before planning new stories."
            )
        elif code_terms and len(code_terms) >= len(design_terms):
            drift_type = "code_ahead"
            explanation = (
                f"Code has moved beyond design/workflow artifacts (similarity={score:.2f}). "
                f"In code but not design: {', '.join(code_terms[:4])}. "
                "Recommend reconciling workflow metadata with implemented reality."
            )
        else:
            drift_type = "design_ahead"
            explanation = (
                f"Low semantic overlap between design and code (similarity={score:.2f}). "
                "Manual review recommended."
            )

        return DriftResult(
            drift_type=drift_type,
            score=score,
            explanation=explanation,
            design_terms=design_terms,
            code_terms=code_terms,
        )


class _TfidfFallback:
    """TF-IDF cosine fallback when sentence-transformers is unavailable."""

    def __init__(self) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer
        self._vec = TfidfVectorizer()
        self._fitted = False
        self._corpus: list[str] = []

    def encode(self, texts: list[str]) -> list[list[float]]:
        from sklearn.feature_extraction.text import TfidfVectorizer
        vec = TfidfVectorizer()
        matrix = vec.fit_transform(texts)
        return matrix.toarray().tolist()  # type: ignore[return-value]
