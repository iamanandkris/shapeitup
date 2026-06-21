"""Tests for shapeitup.ml.failure_classifier"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import unittest
from shapeitup.ml.failure_classifier import classify_failure, FAILURE_CLASSES

class TestPatternMatching(unittest.TestCase):
    def test_timeout(self):
        r = classify_failure("Job timed out after 60 minutes")
        self.assertEqual(r.failure_class, "timeout")
        self.assertEqual(r.method, "pattern")

    def test_flaky(self):
        r = classify_failure("Flaky test detected, retry attempt 2 of 3")
        self.assertEqual(r.failure_class, "flaky")

    def test_compilation_error(self):
        r = classify_failure("SyntaxError: unexpected token at line 42")
        self.assertEqual(r.failure_class, "compilation_error")

    def test_test_failure(self):
        r = classify_failure("3 tests failed, 10 passed\nAssertionError: expected 5 but got 3")
        self.assertEqual(r.failure_class, "test_failure")

    def test_dependency_conflict(self):
        r = classify_failure("ModuleNotFoundError: No module named 'requests'")
        self.assertEqual(r.failure_class, "dependency_conflict")

    def test_type_error(self):
        r = classify_failure("mypy found 5 errors in 3 files")
        self.assertEqual(r.failure_class, "type_error")

    def test_lint_error(self):
        r = classify_failure("ruff check failed: 12 violations found")
        self.assertEqual(r.failure_class, "lint_error")

    def test_environment_error(self):
        r = classify_failure("OOMKilled: container exceeded memory limit 512Mi")
        self.assertEqual(r.failure_class, "environment_error")

    def test_empty_text_returns_unknown(self):
        r = classify_failure("")
        self.assertEqual(r.failure_class, "unknown")

    def test_retryable_classes(self):
        for text, cls in [
            ("Job timed out", "timeout"),
            ("Flaky test retry attempt 2", "flaky"),
            ("OOMKilled container", "environment_error"),
        ]:
            r = classify_failure(text)
            self.assertTrue(r.is_retryable, f"{cls} should be retryable")

    def test_non_retryable_test_failure(self):
        r = classify_failure("2 tests failed assertionerror")
        self.assertFalse(r.is_retryable)

    def test_severity_mapping(self):
        self.assertEqual(classify_failure("build failed").severity, "high")
        self.assertEqual(classify_failure("ruff check failed").severity, "low")

    def test_result_has_confidence(self):
        r = classify_failure("SyntaxError: invalid syntax")
        self.assertGreater(r.confidence, 0.5)

    def test_all_classes_valid(self):
        r = classify_failure("some unrecognised error xyz_qrz")
        self.assertIn(r.failure_class, FAILURE_CLASSES)

    def test_case_insensitive(self):
        r = classify_failure("ASSERTIONERROR: EXPECTED TRUE")
        self.assertEqual(r.failure_class, "test_failure")


class TestModelFallback(unittest.TestCase):
    def test_model_returns_valid_class(self):
        # Ambiguous text — pattern won't match, model handles it
        r = classify_failure("process exited unexpectedly with code 137")
        self.assertIn(r.failure_class, FAILURE_CLASSES)
        self.assertGreater(r.confidence, 0.0)


if __name__ == "__main__":
    unittest.main()
