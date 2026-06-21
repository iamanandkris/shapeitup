"""Tests for shapeitup.ml.context_ranker"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import unittest
from shapeitup.ml.context_ranker import rank_slots, fill_budget, ContextSlot

_SLOTS = [
    ContextSlot("memory",       "past failures related to auth token expiry",       static_priority=9),
    ContextSlot("capabilities", "the system needs user authentication capabilities", static_priority=1),
    ContextSlot("design-seed",  "high-level system design with database schema",     static_priority=0),
    ContextSlot("stories",      "Story: implement JWT refresh token rotation",       static_priority=3),
]

class TestRankSlots(unittest.TestCase):
    def test_returns_all_slots(self):
        ranked = rank_slots(_SLOTS, "implement authentication")
        self.assertEqual(len(ranked), 4)

    def test_auth_relevant_slots_rank_higher(self):
        ranked = rank_slots(_SLOTS, "implement JWT token authentication")
        names = [r.name for r in ranked]
        # auth-related slots (capabilities, stories, memory) should rank before design-seed
        auth_idx = min(names.index(n) for n in ("capabilities", "stories", "memory"))
        design_idx = names.index("design-seed")
        # at least some auth slot should outrank design-seed on this objective
        self.assertLess(auth_idx, design_idx)

    def test_no_objective_falls_back_to_static_priority(self):
        ranked = rank_slots(_SLOTS, "")
        # should be sorted by static_priority
        priorities = [r.static_priority for r in ranked]
        self.assertEqual(priorities, sorted(priorities))

    def test_similarity_scores_are_floats(self):
        for r in rank_slots(_SLOTS, "token authentication"):
            self.assertIsInstance(r.similarity, float)

    def test_empty_slots_returns_empty(self):
        self.assertEqual(rank_slots([], "anything"), [])


class TestFillBudget(unittest.TestCase):
    def test_fills_within_limit(self):
        assembled, manifest = fill_budget(_SLOTS, "authentication", total_limit=500)
        self.assertLessEqual(len(assembled), 500 + 20)  # small truncation marker tolerance

    def test_manifest_has_all_slots(self):
        _, manifest = fill_budget(_SLOTS, "authentication", total_limit=10_000)
        self.assertEqual(len(manifest), len(_SLOTS))

    def test_manifest_statuses_valid(self):
        _, manifest = fill_budget(_SLOTS, "authentication", total_limit=200)
        valid = {"included", "truncated", "omitted", "skipped"}
        for entry in manifest:
            self.assertIn(entry["status"], valid)

    def test_omits_when_over_budget(self):
        _, manifest = fill_budget(_SLOTS, "authentication", total_limit=10)
        omitted = [e for e in manifest if e["status"] == "omitted"]
        self.assertGreater(len(omitted), 0)

    def test_empty_text_slot_skipped(self):
        slots = [ContextSlot("empty", "", static_priority=0)]
        _, manifest = fill_budget(slots, "anything", total_limit=1000)
        self.assertEqual(manifest[0]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
