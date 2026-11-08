from __future__ import annotations

import unittest
from datetime import datetime

from rule_engine.backtest import (
    BetRecord,
    _confidence_bucket,
    _group_stats,
    _line_bucket,
    summarize,
)


def record(profit: float, outcome: str, **overrides) -> BetRecord:
    base = dict(
        fixture_id=1,
        match_time=datetime(2026, 11, 5, 20, 15),
        league="测试联赛",
        home="主队",
        away="客队",
        home_score=1,
        away_score=0,
        direction="home",
        confidence=46,
        confidence_label="低-中",
        bet=1,
        engine_line=-0.25,
        bet_line=-0.25,
        side_line=-0.25,
        water=0.90,
        company="Bet365",
        profit=profit,
        outcome=outcome,
        rule_codes="T02_RETREAT_LOW_WATER",
        conflicts="",
        closing_line=-0.25,
        closing_water=0.88,
        clv_favorable=True,
    )
    base.update(overrides)
    return BetRecord(**base)


class BacktestTests(unittest.TestCase):
    def test_group_stats(self):
        stats = _group_stats(
            [
                record(0.9, "win"),
                record(-1.0, "loss"),
                record(0.45, "half_win"),
                record(0.0, "push"),
            ]
        )
        self.assertEqual(stats["n"], 4)
        self.assertEqual(stats["wins"], 2)
        self.assertEqual(stats["losses"], 1)
        self.assertEqual(stats["pushes"], 1)
        self.assertAlmostEqual(stats["profit"], 0.35)
        self.assertAlmostEqual(stats["roi"], 0.0875)
        self.assertAlmostEqual(stats["hit_rate"], round(2 / 3, 4))

    def test_summary_counts(self):
        records = [
            record(0.9, "win"),
            record(-1.0, "loss"),
            record(0.0, "no_bet", bet=0),
            record(0.0, "error", bet=0, confidence=0),
        ]
        summary = summarize(
            records,
            period={"from": "2026-11-01", "to": "2026-11-05"},
            decision_minutes=60,
            min_age_hours=3,
        )
        self.assertEqual(summary["matches"], 4)
        self.assertEqual(summary["bets"], 2)
        self.assertEqual(summary["no_bets"], 1)
        self.assertEqual(summary["errors"], 1)
        self.assertAlmostEqual(summary["profit"], -0.1)
        self.assertAlmostEqual(summary["roi"], -0.05)
        self.assertIn("T02_RETREAT_LOW_WATER", summary["by_rule"])
        self.assertIn("测试联赛", summary["by_league"])

    def test_buckets(self):
        self.assertEqual(_confidence_bucket(44), "40-44")
        self.assertEqual(_confidence_bucket(47), "45-49")
        self.assertEqual(_confidence_bucket(55), "50-55")
        self.assertEqual(_line_bucket(-1.5), "让一球或更深")
        self.assertEqual(_line_bucket(-0.25), "让平半到半一")
        self.assertEqual(_line_bucket(0.0), "平手")
        self.assertEqual(_line_bucket(0.75), "受让平半到半一")


if __name__ == "__main__":
    unittest.main()
