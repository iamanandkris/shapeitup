"""
failure_classifier.py
---------------------
Classifies CI/test failure text into typed failure classes without an LLM call.

Two-stage approach:
  1. Fast pattern matching (regex) — handles 90%+ of real-world failures instantly.
  2. TF-IDF + LogisticRegression fallback — for ambiguous cases the regex misses.

Failure classes:
  compilation_error   build/compile phase failed (syntax, import, missing symbol)
  test_failure        one or more tests failed (assert, expect, FAIL)
  type_error          static type checker failed (mypy, pyright, tsc)
  lint_error          linter/formatter violation (ruff, eslint, flake8)
  dependency_conflict missing package, version conflict, lock file mismatch
  timeout             job/step exceeded time limit
  flaky               known-flaky signal (intermittent, retry, flakytest tag)
  environment_error   infra/runner issue (OOM, disk full, network, docker)
  unknown             none of the above patterns match
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

# ── Failure classes ────────────────────────────────────────────────────────────

FAILURE_CLASSES: Final[tuple[str, ...]] = (
    "compilation_error",
    "test_failure",
    "type_error",
    "lint_error",
    "dependency_conflict",
    "timeout",
    "flaky",
    "environment_error",
    "unknown",
)

# ── Pattern table ──────────────────────────────────────────────────────────────
# Each entry: (failure_class, list_of_regex_patterns)
# Patterns are tried in order; first match wins.

_PATTERNS: Final[list[tuple[str, list[str]]]] = [
    ("timeout", [
        r"(?i)timed?\s*out",
        r"(?i)exceeded\s+(?:time|deadline|timeout)",
        r"(?i)job\s+cancelled\s+after",
        r"(?i)step\s+took\s+longer\s+than",
    ]),
    ("flaky", [
        r"(?i)flaky\s+test",
        r"(?i)intermittent\s+fail",
        r"(?i)flakytest",
        r"(?i)retry\s+attempt\s+\d",
        r"(?i)transient\s+(error|failure)",
    ]),
    ("environment_error", [
        r"(?i)out\s+of\s+memory",
        r"(?i)oomkilled",
        r"(?i)no\s+space\s+left\s+on\s+device",
        r"(?i)connection\s+refused",
        r"(?i)network\s+(error|unreachable|timeout)",
        r"(?i)docker\s+(daemon|error|not\s+found)",
        r"(?i)runner\s+(?:exited|lost|offline)",
    ]),
    ("dependency_conflict", [
        r"(?i)module\s+not\s+found",
        r"(?i)cannot\s+find\s+module",
        r"(?i)no\s+matching\s+distribution",
        r"(?i)could\s+not\s+resolve\s+dep",
        r"(?i)version\s+conflict",
        r"(?i)incompatible\s+(?:version|requirement)",
        r"(?i)lock\s+file\s+(?:out\s+of\s+date|mismatch)",
        r"(?i)pip\s+install.*error",
        r"(?i)npm\s+(?:err|error)\s+code\s+e(?:noent|notfound|resolve)",
        r"(?i)importerror:\s+cannot\s+import",
        r"(?i)modulenotfounderror",
    ]),
    ("type_error", [
        r"(?i)mypy\s+(?:error|failed)",
        r"(?i)pyright.*error",
        r"(?i)type\s+error:\s+",
        r"(?i)ts\(\d{4}\)",          # TypeScript error codes
        r"(?i)tsc.*error\s+ts",
        r"(?i)typechecking\s+failed",
        r"(?i)found\s+\d+\s+errors?\s+in\s+\d+\s+files?",  # mypy summary
    ]),
    ("lint_error", [
        r"(?i)ruff\s+(?:check\s+)?failed",
        r"(?i)eslint.*error",
        r"(?i)flake8.*[EW]\d{3}",
        r"(?i)pylint.*(?:convention|warning|error|refactor)",
        r"(?i)prettier.*(?:failed|check)",
        r"(?i)linting\s+failed",
        r"(?i)\d+\s+(?:lint\s+)?error[s]?\s+found",
    ]),
    ("compilation_error", [
        r"(?i)syntaxerror:",
        r"(?i)compileerror",
        r"(?i)build\s+failed",
        r"(?i)error:\s+could\s+not\s+compile",
        r"(?i)gradle\s+build\s+fail",
        r"(?i)maven.*build\s+failure",
        r"(?i)make.*error\s+\d",
        r"(?i)cargo\s+build.*error",
        r"(?i)error\[e\d+\]:",        # Rust compiler errors
        r"(?i)nameerror:\s+name\s+",
        r"(?i)undefined\s+reference\s+to",
    ]),
    ("test_failure", [
        r"(?i)\d+\s+(?:test[s]?\s+)?fail(?:ed|ure)",
        r"(?i)assert(?:ion)?(?:error|failed)",
        r"(?i)expected\s+.*\s+(?:but\s+)?(?:got|received|was)",
        r"(?i)FAILED\s+[\w/]+\.py::",
        r"(?i)● .*",                  # Jest failure marker
        r"(?i)✗\s+",                  # mocha failure marker
        r"(?i)tests?\s+(?:did\s+not\s+pass|failed)",
        r"(?i)pytest.*\d+\s+failed",
        r"(?i)test\s+suite\s+failed\s+to\s+run",
    ]),
]

# Pre-compile all patterns
_COMPILED: list[tuple[str, list[re.Pattern[str]]]] = [
    (cls, [re.compile(p) for p in pats])
    for cls, pats in _PATTERNS
]


# ── Result type ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FailureResult:
    failure_class: str
    confidence: float          # 0.0–1.0
    method: str                # "pattern" | "model" | "default"
    matched_pattern: str = ""  # the regex that fired, if method == "pattern"
    signals: list[str] = field(default_factory=list)  # supporting evidence

    @property
    def is_retryable(self) -> bool:
        return self.failure_class in {"flaky", "timeout", "environment_error"}

    @property
    def severity(self) -> str:
        return {
            "compilation_error": "high",
            "test_failure": "high",
            "type_error": "medium",
            "lint_error": "low",
            "dependency_conflict": "high",
            "timeout": "medium",
            "flaky": "low",
            "environment_error": "medium",
            "unknown": "medium",
        }.get(self.failure_class, "medium")


# ── Pattern-based classifier ───────────────────────────────────────────────────

def _classify_by_pattern(text: str) -> FailureResult | None:
    """Return the first pattern match, or None."""
    for cls, compiled_pats in _COMPILED:
        for pat in compiled_pats:
            m = pat.search(text)
            if m:
                return FailureResult(
                    failure_class=cls,
                    confidence=0.92,
                    method="pattern",
                    matched_pattern=pat.pattern,
                    signals=[m.group(0)],
                )
    return None


# ── TF-IDF model (lazy-loaded) ─────────────────────────────────────────────────

_MODEL: "FailureClassifierModel | None" = None


class FailureClassifierModel:
    """
    Lightweight TF-IDF + LogisticRegression classifier.
    Trained on a small synthetic seed corpus embedded here.
    Intended for ambiguous cases the regex table misses.
    """

    # Minimal seed corpus: (text_snippet, label)
    # Enough to initialise; can be extended with real labelled data.
    _SEED: list[tuple[str, str]] = [
        ("error: could not find package lodash", "dependency_conflict"),
        ("modulenotfounderror: no module named requests", "dependency_conflict"),
        ("version conflict detected between package a 1.0 and b 2.0", "dependency_conflict"),
        ("mypy found 3 errors in 2 files", "type_error"),
        ("type error argument of type str is not assignable", "type_error"),
        ("ruff check failed 12 violations", "lint_error"),
        ("eslint 5 problems 3 errors 2 warnings", "lint_error"),
        ("syntaxerror unexpected token at line 42", "compilation_error"),
        ("build failed with exit code 1", "compilation_error"),
        ("2 tests failed 1 passed", "test_failure"),
        ("assertionerror expected 5 but got 3", "test_failure"),
        ("pytest 4 failed 10 passed", "test_failure"),
        ("job timed out after 60 minutes", "timeout"),
        ("step exceeded deadline", "timeout"),
        ("flaky test detected retry attempt 2", "flaky"),
        ("intermittent failure on ci retrying", "flaky"),
        ("oomkilled container exceeded memory limit", "environment_error"),
        ("connection refused to database host", "environment_error"),
        ("no space left on device", "environment_error"),
        ("unrecognized error message format", "unknown"),
        ("process exited with code 1", "unknown"),
    ]

    def __init__(self) -> None:
        from sklearn.linear_model import LogisticRegression
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.pipeline import Pipeline

        self._pipeline = Pipeline([
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), max_features=2000)),
            ("clf", LogisticRegression(max_iter=500, random_state=42)),
        ])
        texts = [t for t, _ in self._SEED]
        labels = [l for _, l in self._SEED]
        self._pipeline.fit(texts, labels)
        self._classes: list[str] = list(self._pipeline.classes_)

    def predict(self, text: str) -> FailureResult:
        proba = self._pipeline.predict_proba([text])[0]
        best_idx = int(proba.argmax())
        confidence = float(proba[best_idx])
        return FailureResult(
            failure_class=self._classes[best_idx],
            confidence=confidence,
            method="model",
        )


def _get_model() -> FailureClassifierModel:
    global _MODEL
    if _MODEL is None:
        _MODEL = FailureClassifierModel()
    return _MODEL


# ── Public API ─────────────────────────────────────────────────────────────────

def classify_failure(text: str) -> FailureResult:
    """
    Classify a CI/test failure log into a typed failure class.

    Args:
        text: Raw failure output (stdout, stderr, CI log excerpt).

    Returns:
        FailureResult with failure_class, confidence, method, and helpers.
    """
    if not text or not text.strip():
        return FailureResult(failure_class="unknown", confidence=1.0, method="default")

    # Stage 1: fast pattern match
    result = _classify_by_pattern(text)
    if result is not None:
        return result

    # Stage 2: TF-IDF model (lazy-loaded)
    try:
        return _get_model().predict(text)
    except Exception:
        pass

    # Stage 3: default fallback
    return FailureResult(failure_class="unknown", confidence=0.5, method="default")


def classify_failures(texts: list[str]) -> list[FailureResult]:
    """Classify multiple failure texts in one call."""
    return [classify_failure(t) for t in texts]
