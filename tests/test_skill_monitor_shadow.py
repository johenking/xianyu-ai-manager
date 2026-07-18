import json
import unittest
from pathlib import Path

from skill_monitor_shadow import ShadowThresholds, compare_shadow_results


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "skill_monitor_mtop"


class SkillMonitorShadowComparatorTests(unittest.TestCase):
    def test_normalized_overlap_passes_without_retaining_source_payloads(self):
        playwright = json.loads(
            (FIXTURE_ROOT / "playwright-normalized.json").read_text()
        )
        mtop = [
            {
                "item_id": "synthetic-item-3",
                "price": 4500,
                "region": "杭州",
                "full_response": "must-not-survive",
            },
            {
                "item_id": "synthetic-item-1",
                "price": 5200,
                "region": "杭州",
                "cookie": "must-not-survive",
            },
        ]

        comparison = compare_shadow_results(
            playwright,
            mtop,
            expected_non_empty=True,
        )

        self.assertTrue(comparison["passed"])
        self.assertEqual(comparison["metrics"]["recall_vs_playwright"], 1.0)
        self.assertEqual(comparison["metrics"]["jaccard"], 1.0)
        self.assertNotIn("full_response", str(comparison))
        self.assertNotIn("cookie", str(comparison))
        self.assertEqual(comparison["evidence_scope"], "normalized_allowlist_only")

    def test_differences_have_explainable_threshold_failures(self):
        comparison = compare_shadow_results(
            [
                {"item_id": "a", "price": 100, "region": "杭州"},
                {"item_id": "b", "price": 200, "region": "上海"},
                {"item_id": "c", "price": 300, "region": "北京"},
            ],
            [
                {"item_id": "a", "price": 150, "region": "深圳"},
                {"item_id": "x", "price": 200, "region": "上海"},
            ],
            thresholds=ShadowThresholds(
                minimum_recall=0.7,
                minimum_jaccard=0.5,
                maximum_price_mismatch_ratio=0,
                maximum_region_mismatch_ratio=0,
            ),
            expected_non_empty=True,
        )

        self.assertFalse(comparison["passed"])
        self.assertIn("recall_below_threshold", comparison["reasons"])
        self.assertIn("jaccard_below_threshold", comparison["reasons"])
        self.assertIn("price_mismatch_above_threshold", comparison["reasons"])
        self.assertIn("region_mismatch_above_threshold", comparison["reasons"])

    def test_empty_only_passes_when_query_is_declared_legally_empty(self):
        blocked = compare_shadow_results([], [])
        allowed = compare_shadow_results([], [], allow_empty=True)
        canary = compare_shadow_results(
            [],
            [],
            allow_empty=True,
            expected_non_empty=True,
        )

        self.assertFalse(blocked["passed"])
        self.assertTrue(allowed["passed"])
        self.assertFalse(canary["passed"])
        self.assertIn("reference_expected_non_empty", canary["reasons"])


if __name__ == "__main__":
    unittest.main()
