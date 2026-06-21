"""Tests for shapeitup.ml.path_classifier"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import unittest
from shapeitup.ml.path_classifier import classify_path, extract_features

_SIMPLE_STORY = """
## Story: Add a helper utility

Add a small helper function to format currency values.

### Acceptance criteria
- Given a float, when formatted, then returns a string like "$1,234.56"
- Given a negative value, then returns "($1,234.56)"
"""

_FLAGGED_STORY = """
## Story: Implement OAuth2 token refresh

Add JWT refresh token rotation with Redis-backed token store.

### Acceptance criteria
- Given a valid refresh token, when the access token expires, then issue new tokens
- Given an invalid or revoked token, when presented, then return 401 Unauthorized
- Integration test coverage for token rotation flow
- Auth service endpoint: POST /auth/refresh

Depends on: Story 1 (Redis setup), Story 3 (JWT middleware)
Allowed Write Paths:
- src/auth/
- tests/auth/
"""

class TestSimpleStory(unittest.TestCase):
    def test_simple_classified(self):
        r = classify_path(_SIMPLE_STORY)
        self.assertEqual(r.path_type, "simple")

    def test_simple_confidence_reasonable(self):
        r = classify_path(_SIMPLE_STORY)
        self.assertGreater(r.confidence, 0.5)

    def test_rationale_non_empty(self):
        self.assertTrue(classify_path(_SIMPLE_STORY).rationale)


class TestFlaggedStory(unittest.TestCase):
    def test_flagged_classified(self):
        r = classify_path(_FLAGGED_STORY)
        self.assertEqual(r.path_type, "flagged")

    def test_full_review_flow_needed(self):
        r = classify_path(_FLAGGED_STORY)
        self.assertTrue(r.needs_full_review_flow)

    def test_security_signal_detected(self):
        f = extract_features(_FLAGGED_STORY)
        self.assertTrue(f.security_signal)

    def test_dep_count(self):
        f = extract_features(_FLAGGED_STORY)
        self.assertGreaterEqual(f.dep_count, 1)


class TestFeatureExtraction(unittest.TestCase):
    def test_word_count_positive(self):
        f = extract_features(_SIMPLE_STORY)
        self.assertGreater(f.word_count, 0)

    def test_interface_signals(self):
        f = extract_features("Call the REST API endpoint and persist to the database")
        self.assertGreaterEqual(f.interface_signals, 2)

    def test_multi_service_signal(self):
        f = extract_features("This story involves cross-service communication between microservices")
        self.assertTrue(f.multi_service_signal)

    def test_empty_story(self):
        r = classify_path("")
        self.assertIn(r.path_type, ("simple", "flagged"))


if __name__ == "__main__":
    unittest.main()
