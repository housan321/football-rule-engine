from __future__ import annotations

import unittest

from rule_engine.walkforward import build_summary, monthly_variant_table, walk_forward


def row(month: str, conf: int, side_line: float, profit: float, outcome: str) -> dict:
    return {
        "match_time": f"{month}-05 12:00:00",
        "confidence": conf,
        "side_line": side_line,
        "profit": profit,
        "outcome": outcome,
    }


ROWS = [
    row("2026-08", 50, -0.25, 1.0, "win"),
    row("2026-08", 50, -0.5, 1.0, "win"),
    row("2026-08", 50, 0.25, -1.0, "loss"),
    row("2026-08", 50, 0.5, -1.0, "loss"),
    row("2026-09", 50, -0.25, 1.0, "win"),
    row("2026-09", 50, -0.25, -1.0, "loss"),
]


class WalkforwardTests(unittest.TestCase):
    def test_monthly_variants(self):
        table = monthly_variant_table(ROWS)
        self.assertEqual(table["months"], ["2026-08", "2026-09"])
        aug_giving = table["variants"]["conf50_giving"]["2026-08"]
        self.assertEqual(aug_giving["n"], 2)
        self.assertAlmostEqual(aug_giving["profit"], 2.0)
        self.assertAlmostEqual(aug_giving["roi"], 1.0)
        aug_receiving = table["variants"]["conf50_receiving"]["2026-08"]
        self.assertAlmostEqual(aug_receiving["profit"], -2.0)
        self.assertAlmostEqual(aug_receiving["roi"], -1.0)

    def test_walk_forward_selects_giving_after_positive_train(self):
        wf = walk_forward(ROWS)
        self.assertEqual(wf["steps"][0]["chosen"], "conf50_all")
        self.assertEqual(wf["steps"][1]["chosen"], "conf50_giving")
        self.assertEqual(wf["steps"][1]["test"]["n"], 2)
        self.assertAlmostEqual(wf["steps"][1]["test"]["profit"], 0.0)
        self.assertEqual(wf["stitched"]["n"], 6)

    def test_summary_shape(self):
        summary = build_summary(ROWS, source="synthetic")
        self.assertEqual(summary["source"], "synthetic")
        self.assertEqual(summary["total_bets"], 6)
        self.assertIn("conf50_giving", summary["monthly_variants"])
        self.assertIn("walk_forward", summary)


if __name__ == "__main__":
    unittest.main()
