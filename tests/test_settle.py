from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from rule_engine import settle
from rule_engine.tracker import COLUMNS


def make_tracker(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([label for _key, label in COLUMNS])
        for row in rows:
            writer.writerow([row.get(key, "") for key, _label in COLUMNS])


class SettleTests(unittest.TestCase):
    def test_pending_fixture_ids(self):
        now = datetime(2026, 11, 8, 12, 0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper_tracking.csv"
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
            tracker = tracking / "paper_tracking.csv"
            make_tracker(
                tracker,
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
                settle, "TRACKER_PATH", tracker
            ):
                changed = settle.sync_actionable_json(datetime(2026, 11, 8, 13, 0))
            self.assertEqual(changed, 1)
            data = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(data[0]["result"], "win")
            self.assertEqual(data[0]["profit"], 0.9)


if __name__ == "__main__":
    unittest.main()
