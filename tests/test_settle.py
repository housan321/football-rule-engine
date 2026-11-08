from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from rule_engine import settle
from rule_engine.tracker import COLUMNS, SHEET


def make_tracker(path: Path, rows: list[dict]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    col = {key: idx for idx, (key, _label) in enumerate(COLUMNS, 1)}
    for key, idx in col.items():
        ws.cell(1, idx, dict(COLUMNS)[key])
    for row_no, row in enumerate(rows, 2):
        for key, value in row.items():
            ws.cell(row_no, col[key], value)
    wb.save(path)


class SettleTests(unittest.TestCase):
    def test_pending_fixture_ids(self):
        now = datetime(2026, 11, 8, 12, 0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper_tracking.xlsx"
            make_tracker(
                path,
                [
                    {
                        "fixture_id": 1,
                        "kickoff": (now - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M"),
                        "analysis_at": "2026-11-08 10:00:00",
                        "result": "",
                    },
                    {
                        "fixture_id": 2,
                        "kickoff": (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M"),
                        "analysis_at": "2026-11-08 10:00:00",
                        "result": "",
                    },
                    {
                        "fixture_id": 3,
                        "kickoff": (now - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M"),
                        "analysis_at": "2026-11-08 10:00:00",
                        "result": "win",
                    },
                    {
                        "fixture_id": 4,
                        "kickoff": (now - timedelta(days=5)).strftime("%Y-%m-%d %H:%M"),
                        "analysis_at": "2026-11-03 10:00:00",
                        "result": "",
                    },
                    {
                        "fixture_id": 5,
                        "kickoff": (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M"),
                        "analysis_at": "2026-11-06 10:00:00",
                        "result": "",
                    },
                ],
            )
            ids = settle.pending_fixture_ids(path, now)
            self.assertEqual(ids, [1, 5])

    def test_sync_actionable_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracking = Path(tmp)
            excel = tracking / "paper_tracking.xlsx"
            make_tracker(
                excel,
                [
                    {
                        "fixture_id": 1,
                        "analysis_at": "2026-11-08 10:00:00",
                        "result": "win",
                        "profit": 0.9,
                        "clv_line": 0.25,
                        "clv_water": 0.02,
                        "clv_favorable": "是",
                        "updated_at": "2026-11-08 13:00:00",
                    }
                ],
            )
            json_path = tracking / "actionable_2026-11-08.json"
            json_path.write_text(
                json.dumps(
                    [
                        {
                            "fixture_id": 1,
                            "analysis_at": "2026-11-08 10:00:00",
                            "result": "",
                            "profit": "",
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch.object(settle, "TRACKING_DIR", tracking), patch.object(
                settle, "EXCEL_PATH", excel
            ):
                changed = settle.sync_actionable_json(datetime(2026, 11, 8, 13, 0))
            self.assertEqual(changed, 1)
            data = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(data[0]["result"], "win")
            self.assertEqual(data[0]["profit"], 0.9)


if __name__ == "__main__":
    unittest.main()
