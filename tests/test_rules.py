from __future__ import annotations

import unittest
from datetime import datetime

from rule_engine.features import AsianFeatures, EuroFeatures
from rule_engine.models import Match, SupInfo
from rule_engine.rules import evaluate

NOW = datetime(2026, 11, 5, 20, 5)
MATCH = Match(
    fixture_id=1,
    match_time=datetime(2026, 11, 5, 20, 15),
    home="主队",
    away="客队",
)


def asian(**overrides) -> AsianFeatures:
    base = dict(
        coverage=4,
        latest_time=NOW,
        data_age_minutes=1.0,
        median_line_delta=0.25,
        median_home_water_delta=-0.05,
        median_away_water_delta=0.05,
        line_agreement=0.75,
        water_agreement=0.75,
        line_dispersion=0.0,
        latest_line=-0.25,
        upper_side="home",
        upper_sign=1,
        deepening=-0.25,
        retreat=0.25,
        upper_water_delta=-0.05,
        lower_water_delta=0.05,
        upper_latest_water=0.85,
        lower_latest_water=1.03,
        early_change_ratio=0.8,
        volatility=1.0,
        consensus_direction="home",
        consensus_reason="盘口/水位净向主队",
    )
    base.update(overrides)
    return AsianFeatures(**base)


def euro(**overrides) -> EuroFeatures:
    base = dict(
        companies=5,
        latest_time=NOW,
        data_age_minutes=2.0,
        open_home=0.40,
        open_draw=0.28,
        open_away=0.32,
        latest_home=0.42,
        latest_draw=0.27,
        latest_away=0.31,
        home_prob_delta=0.02,
        away_prob_delta=-0.01,
        overround=0.05,
        favorite_side="home",
        movement="home",
    )
    base.update(overrides)
    return EuroFeatures(**base)


class RuleTests(unittest.TestCase):
    def test_t02_retreat_low_water(self):
        report = evaluate(MATCH, "pre", asian(), euro(), SupInfo(False), NOW)
        codes = {h.code for h in report.hits}
        self.assertIn("T02_RETREAT_LOW_WATER", codes)
        hit = next(h for h in report.hits if h.code == "T02_RETREAT_LOW_WATER")
        self.assertEqual(hit.direction, "away")

    def test_t03_high_water_is_observe(self):
        report = evaluate(
            MATCH,
            "pre",
            asian(upper_latest_water=1.02, median_line_delta=0.0, retreat=0.0, deepening=0.0),
            euro(movement="observe"),
            SupInfo(False),
            NOW,
        )
        hit = next(h for h in report.hits if h.code == "WL_UPPER_HIGH")
        self.assertEqual(hit.direction, "observe")
        self.assertTrue(any("T-03" in c for c in report.conflicts))

    def test_euro_asia_divergence(self):
        report = evaluate(
            MATCH,
            "pre",
            asian(),
            euro(movement="away", away_prob_delta=0.03, home_prob_delta=-0.02),
            SupInfo(False),
            NOW,
        )
        hit = next(h for h in report.hits if h.code == "EURO_ASIA_DIVERGE")
        self.assertEqual(hit.direction, "observe")
        self.assertTrue(any("欧亚背离" in c for c in report.conflicts))

    def test_confidence_caps_present(self):
        report = evaluate(MATCH, "pre", asian(), euro(), SupInfo(False), NOW)
        caps = [cap for cap, _ in report.caps]
        self.assertIn(55, caps)
        self.assertIn(55, caps)


if __name__ == "__main__":
    unittest.main()
