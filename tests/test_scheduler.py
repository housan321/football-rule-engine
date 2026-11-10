from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rule_engine import scheduler
from rule_engine.scheduler import (
    _seed_recent_tracker_state,
    due_lanes,
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

    def test_due_lanes_split_t60_t30(self):
        now = datetime(2026, 11, 8, 10, 30)
        matches = [
            {"fixture_id": 1, "kickoff": "2026-11-08 11:00"},  # 30 min
            {"fixture_id": 2, "kickoff": "2026-11-08 11:20"},  # 50 min
            {"fixture_id": 3, "kickoff": "2026-11-08 11:40"},  # 70 min
            {"fixture_id": 4, "kickoff": "2026-11-08 10:50"},  # 20 min
        ]
        with patch.object(scheduler, "ENABLE_T30", True):
            t60, t30, blocked = due_lanes(matches, {}, now)
        self.assertEqual([m["fixture_id"] for m in t60], [2])
        self.assertEqual([m["fixture_id"] for m in t30], [1, 4])
        self.assertEqual(blocked, [])
        self.assertEqual(t60[0]["stage"], "T-60")
        self.assertEqual(t60[0]["decision_minutes"], 60)
        self.assertEqual(t30[0]["stage"], "T-30")
        self.assertEqual(t30[0]["decision_minutes"], 30)

    def test_due_lanes_t60_priority_blocks_t30(self):
        now = datetime(2026, 11, 8, 11, 35)
        matches = [{"fixture_id": 1, "kickoff": "2026-11-08 12:00"}]
        state = {"analyzed": [1], "t30_analyzed": [], "t60_actionable": [1]}
        with patch.object(scheduler, "ENABLE_T30", True):
            t60, t30, blocked = due_lanes(matches, state, now)
        self.assertEqual(t60, [])
        self.assertEqual(t30, [])
        self.assertEqual([m["fixture_id"] for m in blocked], [1])

    def test_due_lanes_disabled_t30_keeps_legacy_window(self):
        now = datetime(2026, 11, 8, 11, 35)
        matches = [{"fixture_id": 1, "kickoff": "2026-11-08 12:00"}]
        with patch.object(scheduler, "ENABLE_T30", False):
            t60, t30, blocked = due_lanes(matches, {}, now)
        self.assertEqual([m["fixture_id"] for m in t60], [1])
        self.assertEqual(t30, [])
        self.assertEqual(blocked, [])

    def test_due_lanes_respects_done_sets(self):
        now = datetime(2026, 11, 8, 10, 30)
        matches = [
            {"fixture_id": 1, "kickoff": "2026-11-08 11:20"},  # T-60
            {"fixture_id": 2, "kickoff": "2026-11-08 11:00"},  # T-30
        ]
        state = {"analyzed": [1], "t30_analyzed": [2], "t60_actionable": []}
        with patch.object(scheduler, "ENABLE_T30", True):
            t60, t30, blocked = due_lanes(matches, state, now)
        self.assertEqual(t60, [])
        self.assertEqual(t30, [])
        self.assertEqual(blocked, [])

    def test_seed_recent_tracker_state_recovers_t60_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tracking.csv"
            path.write_text(
                "analysis_at,fixture_id,kickoff\n"
                "2026-11-08 11:00:00,1,2026-11-08 12:00\n"
                "2026-11-08 11:30:00,2,2026-11-08 12:00\n"
                "2026-11-08 08:00:00,3,2026-11-08 09:00\n",
                encoding="utf-8-sig",
            )
            state = {
                "date": "2026-11-08",
                "analyzed": [],
                "t30_analyzed": [],
                "t60_actionable": [],
            }
            with patch.object(scheduler, "TRACKER_PATH", path):
                _seed_recent_tracker_state(
                    state, datetime(2026, 11, 8, 11, 35)
                )
        self.assertEqual(state["analyzed"], [1])
        self.assertEqual(state["t60_actionable"], [1])
        self.assertEqual(state["t30_analyzed"], [2])

    def test_run_once_blocks_t30_when_t60_actionable(self):
        now = datetime(2026, 11, 8, 11, 35)
        matches = [
            {"fixture_id": 1, "kickoff": "2026-11-08 12:00", "league_id": 1}
        ]
        state = {
            "date": "2026-11-08",
            "analyzed": [1],
            "t30_analyzed": [],
            "t60_actionable": [1],
        }
        with patch.object(scheduler, "ENABLE_T30", True), patch.object(
            scheduler, "load_state", return_value=state
        ), patch.object(
            scheduler,
            "load_or_refresh_schedule",
            return_value=(matches, False),
        ), patch.object(scheduler, "save_state") as save, patch.object(
            scheduler, "ingest_main"
        ) as ingest, patch.object(
            scheduler, "_analyze_one"
        ) as analyze:
            rc = scheduler.run_once(now)
        self.assertEqual(rc, 0)
        ingest.assert_not_called()
        analyze.assert_not_called()
        self.assertIn(1, save.call_args[0][0]["t30_analyzed"])

    def test_run_once_analyzes_t30_when_t60_had_no_bet(self):
        now = datetime(2026, 11, 8, 11, 35)
        matches = [
            {"fixture_id": 1, "kickoff": "2026-11-08 12:00", "league_id": 1}
        ]
        state = {
            "date": "2026-11-08",
            "analyzed": [1],
            "t30_analyzed": [],
            "t60_actionable": [],
        }
        result = SimpleNamespace(
            actionable=False,
            direction_name="观察",
            confidence=40,
            stake="仅观察",
        )
        conn = MagicMock()
        with patch.object(scheduler, "ENABLE_T30", True), patch.object(
            scheduler, "load_state", return_value=state
        ), patch.object(
            scheduler,
            "load_or_refresh_schedule",
            return_value=(matches, False),
        ), patch.object(scheduler, "save_state") as save, patch.object(
            scheduler, "ingest_main", return_value=0
        ) as ingest, patch.object(
            scheduler, "load_config", return_value=object()
        ), patch.object(
            scheduler, "connect", return_value=conn
        ), patch.object(
            scheduler, "_analyze_one", return_value=(result, None)
        ) as analyze:
            rc = scheduler.run_once(now)
        self.assertEqual(rc, 1)
        ingest.assert_called_once()
        args, kwargs = analyze.call_args
        self.assertEqual(args[3], 30)
        self.assertTrue(kwargs["only_giving"])
        self.assertIn(1, save.call_args[0][0]["t30_analyzed"])
        conn.close.assert_called_once()

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
