"""
path_classifier.py
------------------
Predicts whether a story should be classified as 'simple' or 'flagged'
using extracted numeric/boolean features — no LLM call required.

Features extracted from story text:
  - word_count             total words in story
  - ac_count               number of acceptance criteria bullets
  - dep_count              number of "Depends on:" lines
  - interface_signals      count of API/DB/auth/network keyword hits
  - file_path_count        number of file path mentions
  - test_signal            mentions unit/integration/e2e test
  - security_signal        mentions auth/permission/secret/token
  - multi_service_signal   mentions multiple services/microservices
  - complexity_score       composite score

A LogisticRegression trained on a seed corpus gives probability.
Above 0.55 probability → flagged. Otherwise → simple.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

PathType = Literal["simple", "flagged"]

# Keyword groups for feature extraction
_INTERFACE_TERMS = re.compile(
    r"\b(?:api|endpoint|rest|grpc|graphql|database|db|sql|redis|"
    r"kafka|queue|webhook|http|https|socket|rpc)\b", re.I
)
_SECURITY_TERMS = re.compile(
    r"\b(?:auth|oauth|jwt|token|secret|permission|role|acl|encrypt|"
    r"tls|ssl|credential|sensitive|pii|gdpr)\b", re.I
)
_TEST_TERMS = re.compile(
    r"\b(?:unit\s+test|integration\s+test|e2e|end.to.end|acceptance\s+test|"
    r"pytest|jest|mocha|cypress|playwright)\b", re.I
)
_MULTI_SERVICE = re.compile(
    r"\b(?:microservice|service\s+mesh|inter.service|downstream|upstream|"
    r"multiple\s+service|cross.service|distributed)\b", re.I
)
_FILE_PATH = re.compile(r"(?:src|tests?|lib|pkg)/[\w/]+\.\w+")
_AC_BULLET = re.compile(r"^(?:\s*[-*•]\s+(?:given|when|then|should|must|the system|verify))",
                         re.I | re.M)
_DEP_LINE = re.compile(r"depends?\s+on\s*:", re.I)


@dataclass
class StoryFeatures:
    word_count: int = 0
    ac_count: int = 0
    dep_count: int = 0
    interface_signals: int = 0
    file_path_count: int = 0
    test_signal: bool = False
    security_signal: bool = False
    multi_service_signal: bool = False

    def to_vector(self) -> list[float]:
        return [
            float(self.word_count),
            float(self.ac_count),
            float(self.dep_count),
            float(self.interface_signals),
            float(self.file_path_count),
            float(self.test_signal),
            float(self.security_signal),
            float(self.multi_service_signal),
        ]

    @property
    def complexity_score(self) -> float:
        return (
            min(self.word_count / 200, 3.0)
            + self.ac_count * 0.3
            + self.dep_count * 0.5
            + min(self.interface_signals * 0.4, 2.0)
            + self.file_path_count * 0.2
            + float(self.security_signal) * 1.0
            + float(self.multi_service_signal) * 1.5
        )


@dataclass(frozen=True)
class PathResult:
    path_type: PathType
    confidence: float
    features: StoryFeatures
    rationale: str

    @property
    def needs_full_review_flow(self) -> bool:
        return self.path_type == "flagged"


def extract_features(story_text: str) -> StoryFeatures:
    """Extract numeric features from story markdown text."""
    words = story_text.split()
    return StoryFeatures(
        word_count=len(words),
        ac_count=len(_AC_BULLET.findall(story_text)),
        dep_count=len(_DEP_LINE.findall(story_text)),
        interface_signals=len(_INTERFACE_TERMS.findall(story_text)),
        file_path_count=len(_FILE_PATH.findall(story_text)),
        test_signal=bool(_TEST_TERMS.search(story_text)),
        security_signal=bool(_SECURITY_TERMS.search(story_text)),
        multi_service_signal=bool(_MULTI_SERVICE.search(story_text)),
    )


# ── Rule-based fast path (before model) ───────────────────────────────────────

def _classify_by_rules(f: StoryFeatures) -> PathResult | None:
    """High-confidence rule shortcuts."""
    reasons: list[str] = []

    # Definitely flagged
    if f.security_signal:
        reasons.append("security/auth signals present")
    if f.multi_service_signal:
        reasons.append("cross-service dependency")
    if f.dep_count >= 3:
        reasons.append(f"{f.dep_count} story dependencies")
    if f.interface_signals >= 4:
        reasons.append(f"{f.interface_signals} API/DB interface touches")

    if reasons:
        return PathResult(
            path_type="flagged",
            confidence=0.90,
            features=f,
            rationale="Flagged: " + "; ".join(reasons) + ".",
        )

    # Definitely simple
    if (f.word_count <= 80 and f.ac_count <= 3 and f.dep_count == 0
            and f.interface_signals <= 1 and not f.security_signal):
        return PathResult(
            path_type="simple",
            confidence=0.88,
            features=f,
            rationale="Simple: small story, minimal dependencies, no security signals.",
        )

    return None


# ── Model (lazy) ───────────────────────────────────────────────────────────────

_PATH_MODEL: "_PathModel | None" = None


class _PathModel:
    _SEED: list[tuple[list[float], str]] = [
        # [word_count, ac_count, dep_count, iface, file_paths, test, sec, multi], label
        ([60,  2, 0, 0, 0, False, False, False], "simple"),
        ([75,  3, 0, 1, 1, True,  False, False], "simple"),
        ([90,  2, 1, 0, 0, False, False, False], "simple"),
        ([120, 4, 1, 2, 2, True,  False, False], "simple"),
        ([200, 5, 2, 3, 3, True,  False, False], "flagged"),
        ([180, 6, 3, 4, 4, True,  True,  False], "flagged"),
        ([300, 8, 2, 5, 6, True,  False, True],  "flagged"),
        ([150, 5, 0, 4, 2, True,  True,  False], "flagged"),
        ([100, 3, 2, 2, 1, False, False, False], "flagged"),
        ([250, 7, 3, 3, 5, True,  True,  True],  "flagged"),
        ([50,  2, 0, 1, 0, False, False, False], "simple"),
        ([80,  3, 1, 1, 1, True,  False, False], "simple"),
    ]

    def __init__(self) -> None:
        from sklearn.linear_model import LogisticRegression
        import numpy as np
        X = np.array([v for v, _ in self._SEED], dtype=float)
        y = [l for _, l in self._SEED]
        self._clf = LogisticRegression(random_state=42, max_iter=200)
        self._clf.fit(X, y)

    def predict(self, features: StoryFeatures) -> tuple[PathType, float]:
        import numpy as np
        X = np.array([features.to_vector()])
        proba = self._clf.predict_proba(X)[0]
        classes: list[str] = list(self._clf.classes_)
        best_idx = int(proba.argmax())
        return classes[best_idx], float(proba[best_idx])  # type: ignore[return-value]


def _get_path_model() -> _PathModel:
    global _PATH_MODEL
    if _PATH_MODEL is None:
        _PATH_MODEL = _PathModel()
    return _PATH_MODEL


# ── Public API ─────────────────────────────────────────────────────────────────

def classify_path(story_text: str) -> PathResult:
    """
    Classify a story as 'simple' or 'flagged' without an LLM call.

    Simple stories can go through a lightweight review flow.
    Flagged stories require full multi-role review, merge gate, etc.
    """
    features = extract_features(story_text)

    # Fast rule-based path
    rule_result = _classify_by_rules(features)
    if rule_result is not None:
        return rule_result

    # Model-based classification
    try:
        path_type, confidence = _get_path_model().predict(features)
        label = "Flagged" if path_type == "flagged" else "Simple"
        rationale = (
            f"{label}: complexity_score={features.complexity_score:.1f}, "
            f"words={features.word_count}, deps={features.dep_count}, "
            f"interfaces={features.interface_signals} (model confidence={confidence:.0%})."
        )
        return PathResult(
            path_type=path_type,
            confidence=confidence,
            features=features,
            rationale=rationale,
        )
    except Exception:
        pass

    # Fallback: threshold on complexity score
    flagged = features.complexity_score >= 2.5
    return PathResult(
        path_type="flagged" if flagged else "simple",
        confidence=0.60,
        features=features,
        rationale=f"Heuristic: complexity_score={features.complexity_score:.1f}.",
    )
