from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from rule_engine.engine import analyze
from rule_engine.models import AsianRow, CompanySeries, Match

KICKOFF = datetime(2026, 11, 5, 20, 15)
MATCH = Match(1, KICKOFF, home="主队", away="客队", league="测试")


def series(company_id, name, entries):
    return CompanySeries(
        company_id,
        name,
        [
            AsianRow(
                company_id,
                name,
                KICKOFF - timedelta(minutes=minutes),
                home_water,
                line,
                away_water,
            )
            for minutes, line, home_water, away_water in entries
        ],
    )


def conflict_series(decision_home=1.02, decision_away=0.82, decision_line=-0.75):
    return [
        series(
            cid,
            name,
            [
                (30, -0.5, 0.95, 0.90),
                (10, decision_line, decision_home, decision_away),
            ],
        )
        for cid, name in [(2, "Bet365"), (3, "皇冠"), (7, "澳门"), (11, "韦德")]
    ]


class EngineTests(unittest.TestCase):
    def test_conflict_t10_document_example(self):
        result = analyze(MATCH, "conflict_t10", conflict_series())
        self.assertTrue(result.triggered)
        self.assertTrue(result.actionable)
        self.assertEqual(result.direction, "away")
        self.assertAlmostEqual(result.side_line, 0.75)
        self.assertAlmostEqual(result.consensus_water, 0.82)
        self.assertAlmostEqual(result.water, 0.82)
        self.assertEqual(result.stake, "1单位")
        self.assertEqual(result.analysis_time, KICKOFF - timedelta(minutes=10))
        self.assertEqual(result.prev_time, KICKOFF - timedelta(minutes=30))
        self.assertEqual(result.companies_prev, 4)
        self.assertEqual(result.companies_decision, 4)

    def test_best_price_is_selected(self):
        companies = [
            series(2, "Bet365", [(30, -0.5, 0.95, 0.90), (10, -0.75, 1.02, 0.82)]),
            series(3, "皇冠", [(30, -0.5, 0.95, 0.90), (10, -0.75, 1.00, 0.86)]),
            series(7, "澳门", [(30, -0.5, 0.95, 0.90), (10, -0.75, 0.98, 0.84)]),
        ]
        result = analyze(MATCH, "conflict_t10", companies, price="best")
        self.assertTrue(result.triggered)
        self.assertAlmostEqual(result.consensus_water, 0.84)
        self.assertAlmostEqual(result.best_water, 0.86)
        self.assertAlmostEqual(result.water, 0.86)
        self.assertEqual(result.best_company, "皇冠")

    def test_no_signal_when_directions_agree(self):
        companies = [
            series(
                cid,
                name,
                [(30, -0.5, 0.95, 0.90), (10, -0.75, 0.88, 1.04)],
            )
            for cid, name in [(2, "Bet365"), (3, "皇冠")]
        ]
        result = analyze(MATCH, "conflict_t10", companies)
        self.assertFalse(result.triggered)
        self.assertEqual(result.direction, "observe")
        self.assertIsNone(result.side_line)

    def test_insufficient_companies(self):
        companies = [
            series(2, "Bet365", [(30, -0.5, 0.95, 0.90), (10, -0.75, 1.02, 0.82)])
        ]
        result = analyze(MATCH, "conflict_t10", companies)
        self.assertFalse(result.triggered)
        self.assertIn("共识公司不足", result.reason)
        self.assertEqual(result.direction, "observe")

    def test_to_dict_has_audit_fields(self):
        result = analyze(MATCH, "conflict_t10", conflict_series())
        data = result.to_dict()
        self.assertEqual(data["strategy"], "conflict_t10")
        self.assertEqual(data["strategy_label"], "conflict T-10m")
        self.assertIn("asian", data["features"])
        self.assertIn("prev", data["features"]["asian"])
        self.assertIn("decision", data["features"]["asian"])
        self.assertEqual(data["rule_hits"][0]["code"], "CONFLICT_T10")


if __name__ == "__main__":
    unittest.main()
