from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from rule_engine.features import (
    build_consensus_snapshot,
    build_snapshot_delta,
)
from rule_engine.models import AsianRow, CompanySeries

KICKOFF = datetime(2026, 11, 5, 20, 15)


def series(company_id, name, entries):
    rows = []
    for minutes, line, home_water, away_water in entries:
        rows.append(
            AsianRow(
                company_id,
                name,
                KICKOFF - timedelta(minutes=minutes),
                home_water,
                line,
                away_water,
            )
        )
    return CompanySeries(company_id, name, rows)


class SnapshotTests(unittest.TestCase):
    def test_modal_line_and_average_water(self):
        companies = [
            series(2, "Bet365", [(30, -0.5, 0.90, 0.96)]),
            series(3, "皇冠", [(30, -0.5, 1.00, 0.86)]),
            series(7, "澳门", [(30, -0.25, 0.80, 1.06)]),
        ]
        snapshot = build_consensus_snapshot(
            companies, KICKOFF - timedelta(minutes=30)
        )
        self.assertEqual(snapshot.line, -0.5)
        self.assertEqual(snapshot.companies, 2)
        self.assertEqual(snapshot.total_companies, 3)
        self.assertAlmostEqual(snapshot.home_water, 0.95)
        self.assertAlmostEqual(snapshot.away_water, 0.91)

    def test_cutoff_is_inclusive(self):
        companies = [series(2, "Bet365", [(30, -0.5, 0.95, 0.90)])]
        at_cutoff = build_consensus_snapshot(
            companies, KICKOFF - timedelta(minutes=30)
        )
        before = build_consensus_snapshot(
            companies, KICKOFF - timedelta(minutes=31)
        )
        self.assertIsNotNone(at_cutoff)
        self.assertIsNone(before)

    def test_conflict_delta(self):
        companies = [
            series(cid, name, [(30, -0.5, 0.95, 0.90), (10, -0.75, 1.02, 0.82)])
            for cid, name in [(2, "Bet365"), (3, "皇冠"), (7, "澳门"), (11, "韦德")]
        ]
        delta = build_snapshot_delta(
            companies,
            KICKOFF - timedelta(minutes=30),
            KICKOFF - timedelta(minutes=10),
        )
        self.assertAlmostEqual(delta.d_line, -0.25)
        self.assertAlmostEqual(delta.rel_water, 0.15)
        self.assertEqual(delta.line_direction, "home")
        self.assertEqual(delta.water_direction, "away")
        self.assertTrue(delta.conflict)

    def test_water_only_delta_is_not_conflict(self):
        companies = [
            series(cid, name, [(120, -0.5, 0.95, 0.90), (60, -0.5, 1.01, 0.84)])
            for cid, name in [(2, "Bet365"), (3, "皇冠")]
        ]
        delta = build_snapshot_delta(
            companies,
            KICKOFF - timedelta(minutes=120),
            KICKOFF - timedelta(minutes=60),
        )
        self.assertAlmostEqual(delta.d_line, 0.0)
        self.assertAlmostEqual(delta.rel_water, 0.12)
        self.assertEqual(delta.line_direction, "flat")
        self.assertEqual(delta.water_direction, "away")
        self.assertFalse(delta.conflict)


if __name__ == "__main__":
    unittest.main()
