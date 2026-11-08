from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from rule_engine import scheduler
from rule_engine.scheduler import (
    due_matches,
    last_refresh_point,
    load_or_refresh_schedule,
    settle_tracker,
)


def write_cache(path: Path, fetched_at: str, matches: list[dict] | None = None) -> None:
    path.write_text(
        json.dumps({"fetched_at": fetched_at, "matches": matches or []}),
        encoding="utf-8",
    )


class SchedulerTests(unittest.TestCase):
    def test_last_refresh_point(self):
        self.assertEqual(
            last_refresh_point(datetime(2026, 11, 8, 10, 30)),
            datetime(2026, 11, 7, 16, 0),
        )
        self.assertEqual(
            last_refresh_point(datetime(2026, 11, 8, 11, 30)),
            datetime(2026, 11, 8, 11, 0),
        )
        self.assertEqual(
            last_refresh_point(datetime(2026, 11, 8, 18, 30)),
            datetime(2026, 11, 8, 16, 0),
        )
        self.assertEqual(
            last_refresh_point(datetime(2026, 11, 8, 0, 5)),
            datetime(2026, 11, 7, 16, 0),
        )

    def test_due_matches_window_and_dedup(self):
        matches = [
            {"fixture_id": 1, "kickoff": "2026-11-08 11:00"},
            {"fixture_id": 2, "kickoff": "2026-11-08 12:00"},
            {"fixture_id": 3, "kickoff": "2026-11-08 10:00"},
            {"fixture_id": 4, "kickoff": "2026-11-08 11:30"},
        ]
        due = due_matches(matches, {4}, datetime(2026, 11, 8, 10, 30))
        self.assertEqual([m["fixture_id"] for m in due], [1])
        self.assertAlmostEqual(due[0]["minutes_to_kickoff"], 30.0)

    def test_valid_cache_does_not_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            write_cache(
                cache_path,
                "2026-11-08T11:30:00",
                [{"fixture_id": 1}],
            )
            with patch.object(scheduler, "CACHE_PATH", cache_path):
                matches, refreshed = load_or_refresh_schedule(
                    datetime(2026, 11, 8, 12, 0)
                )
        self.assertFalse(refreshed)
        self.assertEqual(matches, [{"fixture_id": 1}])

    def test_stale_cache_refreshes(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            write_cache(cache_path, "2026-11-08T10:30:00")
            with patch.object(scheduler, "CACHE_PATH", cache_path), patch.object(
                scheduler, "collect_schedule", return_value=[{"fixture_id": 2}]
            ) as collect:
                matches, refreshed = load_or_refresh_schedule(
                    datetime(2026, 11, 8, 12, 0)
                )
        self.assertTrue(refreshed)
        self.assertEqual(matches, [{"fixture_id": 2}])
        collect.assert_called_once()

    def test_failed_refresh_keeps_stale_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            write_cache(
                cache_path,
                "2026-11-08T10:30:00",
                [{"fixture_id": 3}],
            )
            with patch.object(scheduler, "CACHE_PATH", cache_path), patch.object(
                scheduler, "collect_schedule", side_effect=RuntimeError("boom")
            ):
                matches, refreshed = load_or_refresh_schedule(
                    datetime(2026, 11, 8, 12, 0)
                )
        self.assertFalse(refreshed)
        self.assertEqual(matches, [{"fixture_id": 3}])

    def test_settle_tracker_calls_settlement(self):
        now = datetime(2026, 11, 8, 11, 5)
        with patch("rule_engine.settle.run") as run:
            settle_tracker(now)
        run.assert_called_once_with(now)

    def test_collect_schedule_includes_league_and_team_names(self):
        html = """
        <table id="table_live">
          <tr sId="3041395" name="273,1">
            <td>澳超</td><td>11-8 12:00</td><td></td>
            <td align="right">[6] 麦克阿瑟FC</td><td>-</td>
            <td align="left">布里斯班狮吼 [8]</td><td></td>
          </tr>
          <tr sId="9999999" name="999,1">
            <td>不在白名单</td><td>11-8 13:00</td><td></td>
            <td>[1] A</td><td>-</td><td>B [2]</td><td></td>
          </tr>
        </table>
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.object(scheduler, "DATA_DIR", tmp_path), patch.object(
                scheduler, "CACHE_PATH", tmp_path / "schedule_cache.json"
            ), patch.object(
                scheduler.crawl, "find_openssl", return_value=None
            ), patch.object(
                scheduler.crawl,
                "fetch_url",
                return_value=(None, html.encode("utf-8")),
            ):
                matches = scheduler.collect_schedule(
                    datetime(2026, 11, 8, 9, 0)
                )
        self.assertEqual(len(matches), 1)
        match = matches[0]
        self.assertEqual(match["fixture_id"], 3041395)
        self.assertEqual(match["league_id"], 273)
        self.assertEqual(match["league"], "澳超")
        self.assertEqual(match["home"], "麦克阿瑟FC")
        self.assertEqual(match["away"], "布里斯班狮吼")
        self.assertEqual(match["kickoff"], "2026-11-08T12:00:00")


if __name__ == "__main__":
    unittest.main()
