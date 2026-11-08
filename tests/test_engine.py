from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from rule_engine.engine import AnalysisResult, analyze, apply_action_filters
from rule_engine.models import AsianRow, CompanySeries, EuroRow, EuroSeries, Match, SupInfo

KICKOFF = datetime(2026, 11, 5, 20, 15)
NOW = KICKOFF - timedelta(minutes=10)
MATCH = Match(1, KICKOFF, home="主队", away="客队", league="测试")


def asian_series(company_id: int, name: str, open_line: float, latest_line: float,
                 open_water: float, latest_water: float) -> CompanySeries:
    return CompanySeries(
        company_id,
        name,
        [
            AsianRow(company_id, name, KICKOFF - timedelta(hours=24), open_water, open_line, 1.0 - open_water),
            AsianRow(company_id, name, KICKOFF - timedelta(hours=2), latest_water, latest_line, 1.0 - latest_water),
        ],
    )


def euro_series(company_id: int, name: str, open_odds, latest_odds) -> EuroSeries:
    return EuroSeries(
        company_id,
        name,
        [
            EuroRow(company_id, name, KICKOFF - timedelta(hours=24), *open_odds),
            EuroRow(company_id, name, KICKOFF - timedelta(hours=1), *latest_odds),
        ],
    )


def make_result(direction, confidence, stake, latest_line):
    return AnalysisResult(
        match=MATCH,
        mode="pre",
        analysis_time=NOW,
        direction=direction,
        confidence=confidence,
        confidence_label="低-中",
        stake=stake,
        score_home=0.0,
        score_away=0.0,
        conflict_ratio=0.0,
        summary="",
        features={"asian": {"latest_line": latest_line}},
    )


class ActionFilterTests(unittest.TestCase):
    def test_giving_side_conf50_is_experimental(self):
        result = apply_action_filters(
            make_result("home", 50, "小注", -0.5),
            only_giving=True,
            min_confidence=50,
        )
        self.assertTrue(result.is_giving)
        self.assertAlmostEqual(result.side_line, -0.5)
        self.assertTrue(result.experimental_default)
        self.assertTrue(result.actionable)
        self.assertEqual(result.stake, "小注")

    def test_receiving_side_is_suppressed(self):
        result = apply_action_filters(
            make_result("away", 55, "小注", -0.5),
            only_giving=True,
            min_confidence=50,
        )
        self.assertFalse(result.is_giving)
        self.assertAlmostEqual(result.side_line, 0.5)
        self.assertFalse(result.experimental_default)
        self.assertFalse(result.actionable)
        self.assertEqual(result.stake, "仅观察")
        self.assertTrue(result.filter_notes)

    def test_low_confidence_is_suppressed(self):
        result = apply_action_filters(
            make_result("home", 45, "小注", -0.5),
            only_giving=True,
            min_confidence=50,
        )
        self.assertTrue(result.is_giving)
        self.assertFalse(result.experimental_default)
        self.assertFalse(result.actionable)
        self.assertEqual(result.stake, "仅观察")

    def test_observe_is_not_actionable(self):
        result = apply_action_filters(
            make_result("observe", 40, "仅观察", 0.0),
            only_giving=True,
            min_confidence=50,
        )
        self.assertFalse(result.experimental_default)
        self.assertFalse(result.actionable)


class EngineTests(unittest.TestCase):
    def test_result_is_bounded(self):
        asian = [
            asian_series(2, "Bet365", -0.5, -0.25, 0.95, 0.85),
            asian_series(3, "皇冠", -0.5, -0.25, 0.94, 0.84),
            asian_series(7, "澳门", -0.5, -0.25, 0.93, 0.83),
            asian_series(11, "韦德", -0.5, -0.25, 0.92, 0.82),
        ]
        euro = [
            euro_series(2, "Bet365", (2.05, 3.5, 3.3), (2.0, 3.5, 3.4)),
            euro_series(3, "皇冠", (2.1, 3.6, 3.2), (2.05, 3.6, 3.3)),
            euro_series(5, "立博", (2.1, 3.5, 3.3), (2.0, 3.5, 3.4)),
            euro_series(7, "澳门", (2.0, 3.6, 3.2), (1.98, 3.6, 3.3)),
            euro_series(9, "威廉希尔", (2.1, 3.5, 3.3), (2.0, 3.5, 3.4)),
            euro_series(22, "平博", (2.05, 3.5, 3.3), (2.0, 3.5, 3.4)),
        ]
        result = analyze(MATCH, "pre", NOW, asian, euro, SupInfo(False))
        self.assertIn(result.direction, ("home", "away", "observe"))
        self.assertLessEqual(result.confidence, 55)
        self.assertIn(result.stake, ("仅观察", "小注"))
        self.assertTrue(result.rule_hits)
        self.assertTrue(result.invalidation)


if __name__ == "__main__":
    unittest.main()
