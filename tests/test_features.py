from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from rule_engine.features import build_asian_features, build_company_views
from rule_engine.models import AsianRow, CompanySeries

KICKOFF = datetime(2026, 11, 5, 20, 15)


def make_series(company_id: int, name: str, open_line: float, latest_line: float,
                open_water: float, latest_water: float) -> CompanySeries:
    rows = [
        AsianRow(company_id, name, KICKOFF - timedelta(hours=24), open_water, open_line, 1.0 - open_water),
        AsianRow(company_id, name, KICKOFF - timedelta(hours=5), (open_water + latest_water) / 2,
                 (open_line + latest_line) / 2, 1.0 - (open_water + latest_water) / 2),
        AsianRow(company_id, name, KICKOFF - timedelta(hours=2), latest_water, latest_line, 1.0 - latest_water),
    ]
    return CompanySeries(company_id, name, rows)


class FeatureTests(unittest.TestCase):
    def test_retreat_and_water_down(self):
        series = [
            make_series(2, "Bet365", -0.5, -0.25, 0.95, 0.85),
            make_series(3, "皇冠", -0.5, -0.25, 0.94, 0.84),
            make_series(7, "澳门", -0.5, -0.25, 0.93, 0.83),
            make_series(11, "韦德", -0.5, -0.25, 0.92, 0.82),
        ]
        views = build_company_views(series, KICKOFF)
        feat = build_asian_features(views, "pre", KICKOFF - timedelta(minutes=10))
        self.assertEqual(feat.coverage, 4)
        self.assertAlmostEqual(feat.median_line_delta, 0.25, places=3)
        self.assertEqual(feat.upper_side, "home")
        self.assertAlmostEqual(feat.retreat, 0.25, places=3)
        self.assertAlmostEqual(feat.upper_water_delta, -0.10, places=3)
        self.assertEqual(feat.consensus_direction, "home")

    def test_deepening(self):
        series = [
            make_series(2, "Bet365", -0.25, -0.5, 0.90, 0.95),
            make_series(3, "皇冠", -0.25, -0.5, 0.91, 0.96),
            make_series(7, "澳门", -0.25, -0.5, 0.92, 0.97),
        ]
        feat = build_asian_features(
            build_company_views(series, KICKOFF), "pre", KICKOFF - timedelta(minutes=10)
        )
        self.assertAlmostEqual(feat.deepening, 0.25, places=3)
        self.assertAlmostEqual(feat.upper_water_delta, 0.05, places=3)

    def test_n1_stale_is_excluded(self):
        series = [
            CompanySeries(
                9,
                "威廉希尔",
                [AsianRow(9, "威廉希尔", KICKOFF - timedelta(days=3), 0.8, -1.0, 1.0)],
            )
        ]
        views = build_company_views(series, KICKOFF)
        self.assertTrue(views[0].excluded)


if __name__ == "__main__":
    unittest.main()
