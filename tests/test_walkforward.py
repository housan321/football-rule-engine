from __future__ import annotations

import unittest

from rule_engine.walkforward import build_summary, monthly_variant_table, walk_forward


def row(month: str, strategy: str, profit: float, outcome: str) -> dict:
    return {
        "match_time": f"{month}-05 12:00:00",
        "strategy": strategy,
        "profit_consensus": profit,
        "outcome": outcome,
        "bet": "1",
    }


ROWS = [
    row("2026-08", "conflict_t10", 0.9, "win"),
    row("2026-08", "conflict_t10", -1.0, "loss"),
    row("2026-08", "water_t60", 0.8, "win"),
    row("2026-09", "conflict_t10", 1.0, "win"),
    row("2026-09", "conflict_t10", -1.0, "loss"),
    row("2026-09", "water_t60", -1.0, "loss"),
]


class WalkforwardTests(unittest.TestCase):
    def test_monthly_variants(self):
        table = monthly_variant_table(ROWS)
        self.assertEqual(table["months"], ["2026-08", "2026-09"])
        aug = table["variants"]["conflict_t10"]["2026-08"]
        self.assertEqual(aug["n"], 2)
        self.assertAlmostEqual(aug["profit"], -0.1)
        self.assertAlmostEqual(aug["roi"], -0.05)
        self.assertAlmostEqual(aug["hit_rate"], 0.5)

    def test_walk_forward_reports_each_strategy(self):
        wf = walk_forward(ROWS)
        self.assertEqual(wf["months"], ["2026-08", "2026-09"])
        self.assertEqual(wf["steps"][0]["strategies"]["conflict_t10"]["n"], 2)
        self.assertEqual(wf["stitched"]["conflict_t10"]["n"], 4)
        self.assertAlmostEqual(wf["stitched"]["conflict_t10"]["profit"], -0.1)
        self.assertEqual(wf["stitched"]["water_t60"]["n"], 2)

    def test_summary_shape(self):
        summary = build_summary(ROWS, source="synthetic")
        self.assertEqual(summary["source"], "synthetic")
        self.assertEqual(summary["total_bets"], 6)
        self.assertIn("conflict_t10", summary["monthly_variants"])
        self.assertIn("walk_forward", summary)


if __name__ == "__main__":
    unittest.main()
