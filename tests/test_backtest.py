from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from rule_engine.backtest import (
    BetRecord,
    _bootstrap_ci,
    _group_stats,
    _record,
    _season_label,
    summarize,
)
from rule_engine.engine import analyze
from rule_engine.models import AsianRow, CompanySeries, Match
from rule_engine.rules import get_strategy


def record(**overrides) -> BetRecord:
    base = dict(
        fixture_id=1,
        match_time=datetime(2026, 11, 5, 20, 15),
        league="测试联赛",
        home="主队",
        away="客队",
        home_score=1,
        away_score=0,
        strategy="conflict_t10",
        direction="home",
        bet=1,
        prev_line=-0.5,
        line=-0.75,
        line_delta=-0.25,
        home_water_delta=0.07,
        away_water_delta=-0.08,
        rel_water=0.15,
        companies_prev=4,
        companies_decision=4,
        bet_line=-0.75,
        water_consensus=0.82,
        water_best=0.86,
        best_company="皇冠",
        outcome="win",
        profit_consensus=0.82,
        outcome_best="win",
        profit_best=0.86,
        outcome_opposite="loss",
        profit_opposite=-1.0,
    )
    base.update(overrides)
    return BetRecord(**base)


class BacktestTests(unittest.TestCase):
    def test_season_label(self):
        self.assertEqual(_season_label(datetime(2026, 11, 5)), "2026-27")
        self.assertEqual(_season_label(datetime(2026, 5, 5)), "2025-26")

    def test_bootstrap_ci_brackets_mean(self):
        ci = _bootstrap_ci([1.0, -1.0, 1.0, -1.0])
        self.assertIsNotNone(ci[0])
        self.assertIsNotNone(ci[1])
        self.assertLessEqual(ci[0], ci[1])
        self.assertLessEqual(ci[0], 0.0)
        self.assertGreaterEqual(ci[1], 0.0)

    def test_group_stats(self):
        stats = _group_stats(
            [
                record(),
                record(outcome="loss", profit_consensus=-1.0, profit_best=-1.0,
                       outcome_best="loss", profit_opposite=0.8,
                       outcome_opposite="win"),
                record(outcome="push", profit_consensus=0.0, profit_best=0.0,
                       outcome_best="push", profit_opposite=0.0,
                       outcome_opposite="push"),
            ]
        )
        self.assertEqual(stats["n"], 3)
        self.assertEqual(stats["wins"], 1)
        self.assertEqual(stats["losses"], 1)
        self.assertEqual(stats["pushes"], 1)
        self.assertAlmostEqual(stats["hit_rate"], 0.5)
        self.assertAlmostEqual(stats["profit_consensus"], -0.18)
        self.assertEqual(stats["opposite"]["n"], 3)

    def test_summarize_groups_by_strategy(self):
        records = [
            record(),
            record(
                fixture_id=2,
                strategy="water_t60",
                direction="away",
                bet_line=0.5,
                outcome="loss",
                profit_consensus=-1.0,
                outcome_best="loss",
                profit_best=-1.0,
            ),
            record(fixture_id=3, outcome="no_bet", bet=0,
                   profit_consensus=0.0, profit_best=0.0,
                   outcome_best="no_bet", profit_opposite=0.0,
                   outcome_opposite="no_bet"),
        ]
        summary = summarize(
            records,
            period={"from": "2026-11-01", "to": "2026-11-05"},
            strategies=["conflict_t10", "water_t60"],
        )
        self.assertEqual(summary["matches"], 3)
        c10 = summary["by_strategy"]["conflict_t10"]
        self.assertEqual(c10["matches"], 2)
        self.assertEqual(c10["bets"], 1)
        self.assertEqual(c10["no_bets"], 1)
        self.assertAlmostEqual(c10["roi_consensus"], 0.82)
        self.assertIn("测试联赛", c10["by_league"])
        self.assertIn("home", c10["by_direction"])
        self.assertIn("2026-27", c10["by_season"])
        water = summary["by_strategy"]["water_t60"]
        self.assertEqual(water["bets"], 1)
        self.assertAlmostEqual(water["roi_consensus"], -1.0)

    def test_record_settles_consensus_best_and_opposite(self):
        kickoff = datetime(2026, 11, 5, 20, 15)
        match = Match(1, kickoff, home="主队", away="客队", league="测试",
                      home_score=0, away_score=1)
        companies = []
        for cid, name in [(2, "Bet365"), (3, "皇冠"), (7, "澳门")]:
            companies.append(
                CompanySeries(
                    cid,
                    name,
                    [
                        AsianRow(cid, name, kickoff - timedelta(minutes=30),
                                 0.95, -0.5, 0.90),
                        AsianRow(cid, name, kickoff - timedelta(minutes=10),
                                 1.02, -0.75, 0.82),
                    ],
                )
            )
        result = analyze(match, "conflict_t10", companies)
        row = _record(match, get_strategy("conflict_t10"), result)
        self.assertEqual(row.bet, 1)
        self.assertEqual(row.direction, "away")
        self.assertAlmostEqual(row.bet_line, 0.75)
        # Away +0.75 with a 1-0 home win: away wins.
        self.assertEqual(row.outcome, "win")
        self.assertAlmostEqual(row.profit_consensus, 0.82)
        self.assertEqual(row.outcome_opposite, "loss")
        self.assertAlmostEqual(row.profit_opposite, -1.0)


if __name__ == "__main__":
    unittest.main()
