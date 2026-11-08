from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from rule_engine.engine import AnalysisResult
from rule_engine.models import Match
from rule_engine.tracker import append_rows, result_to_tracker_row


def make_result(fixture_id: int = 1, confidence: int = 50) -> AnalysisResult:
    match = Match(
        fixture_id=fixture_id,
        match_time=datetime(2026, 11, 7, 12, 0),
        league="测试联赛",
        home="主队",
        away="客队",
    )
    return AnalysisResult(
        match=match,
        mode="pre",
        analysis_time=datetime(2026, 11, 7, 11, 0),
        direction="home",
        confidence=confidence,
        confidence_label="中低",
        stake="小注",
        score_home=2.0,
        score_away=0.0,
        conflict_ratio=0.0,
        summary="",
        features={
            "asian": {
                "latest_line": -0.5,
                "home_latest_water": 0.90,
                "away_latest_water": 1.00,
            }
        },
        side_line=-0.5,
        is_giving=True,
        experimental_default=True,
        actionable=True,
    )


class TrackerTests(unittest.TestCase):
    def test_row_fields(self):
        row = result_to_tracker_row(make_result())
        self.assertEqual(row["fixture_id"], 1)
        self.assertEqual(row["side_line"], -0.5)
        self.assertEqual(row["is_giving"], "是")
        self.assertEqual(row["actionable"], "是")
        self.assertEqual(row["experimental_default"], "是")
        self.assertAlmostEqual(row["water"], 0.90)

    def test_append_and_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tracking.xlsx"
            row = result_to_tracker_row(make_result())
            added, updated = append_rows(path, [row])
            self.assertEqual((added, updated), (1, 0))

            wb = load_workbook(path)
            self.assertIn("paper_tracking", wb.sheetnames)
            self.assertIn("说明", wb.sheetnames)
            ws = wb["paper_tracking"]
            self.assertEqual(ws.max_row, 2)

            row2 = result_to_tracker_row(make_result(confidence=55))
            added, updated = append_rows(path, [row2])
            self.assertEqual((added, updated), (0, 1))
            wb = load_workbook(path)
            ws = wb["paper_tracking"]
            self.assertEqual(ws.max_row, 2)
            header = {cell.value: cell.column for cell in ws[1]}
            self.assertEqual(ws.cell(2, header["置信度"]).value, 55)

            row3 = result_to_tracker_row(make_result(fixture_id=2))
            added, updated = append_rows(path, [row3])
            self.assertEqual((added, updated), (1, 0))
            wb = load_workbook(path)
            self.assertEqual(wb["paper_tracking"].max_row, 3)


if __name__ == "__main__":
    unittest.main()
