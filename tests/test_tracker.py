from __future__ import annotations

import csv
import io
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rule_engine.engine import AnalysisResult
from rule_engine.models import Match
from rule_engine.tracker import (
    append_rows,
    build_actionable_csv,
    result_to_tracker_row,
    update_tracking_results,
)


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


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


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
            path = Path(tmp) / "tracking.csv"
            row = result_to_tracker_row(make_result())
            added, updated = append_rows(path, [row])
            self.assertEqual((added, updated), (1, 0))

            rows = read_csv(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["fixture_id"], "1")
            self.assertEqual(rows[0]["置信度"], "50")

            row2 = result_to_tracker_row(make_result(confidence=55))
            added, updated = append_rows(path, [row2])
            self.assertEqual((added, updated), (0, 1))
            rows = read_csv(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["置信度"], "55")

            row3 = result_to_tracker_row(make_result(fixture_id=2))
            added, updated = append_rows(path, [row3])
            self.assertEqual((added, updated), (1, 0))
            self.assertEqual(len(read_csv(path)), 2)

    def test_update_preserves_settled_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tracking.csv"
            row = result_to_tracker_row(make_result())
            append_rows(path, [row])
            rows = read_csv(path)
            rows[0]["结果"] = "win"
            rows[0]["利润"] = "0.9"
            with path.open("w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)

            append_rows(path, [result_to_tracker_row(make_result(confidence=55))])
            rows = read_csv(path)
            self.assertEqual(rows[0]["结果"], "win")
            self.assertEqual(rows[0]["利润"], "0.9")
            self.assertEqual(rows[0]["置信度"], "55")

    def test_update_tracking_results_fills_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tracking.csv"
            append_rows(path, [result_to_tracker_row(make_result())])
            match = SimpleNamespace(home_score=2, away_score=0)
            with patch(
                "rule_engine.tracker.fetch_match", return_value=match
            ), patch(
                "rule_engine.tracker._closing_quote", return_value=(None, None)
            ):
                updated, skipped, errors = update_tracking_results(object(), path)
            self.assertEqual((updated, skipped, errors), (1, 0, 0))
            rows = read_csv(path)
            self.assertEqual(rows[0]["结果"], "win")
            self.assertEqual(float(rows[0]["利润"]), 0.9)

    def test_build_actionable_csv_only_contains_given_rows(self):
        row = result_to_tracker_row(make_result(fixture_id=7))
        payload = build_actionable_csv([row])
        self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fixture_id"], "7")
        self.assertEqual(rows[0]["可下注"], "是")


if __name__ == "__main__":
    unittest.main()
