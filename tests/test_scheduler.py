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
    collect_schedule,
    due_pairs,
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

    def test_due_pairs_at_each_window(self):
        now = datetime(2026, 11, 8, 10, 0)
        matches = [
            {"fixture_id": 1, "kickoff": "2026-11-08 12:00"},  # 120 min
            {"fixture_id": 2, "kickoff": "2026-11-08 11:00"},  # 60 min
            {"fixture_id": 3, "kickoff": "2026-11-08 10:10"},  # 10 min
            {"fixture_id": 4, "kickoff": "2026-11-08 12:10"},  # 130 min
            {"fixture_id": 5, "kickoff": "2026-11-08 09:50"},  # started
        ]
        pairs = due_pairs(matches, {"done": {}}, now)
        got = {(p["fixture_id"], p["strategy"]) for p in pairs}
        self.assertEqual({s for fid, s in got if fid == 1}, {"conflict_t120"})
        self.assertEqual({s for fid, s in got if fid == 2}, {"water_t60"})
        self.assertEqual({s for fid, s in got if fid == 3}, {"conflict_t10"})
        self.assertFalse(any(fid == 4 for fid, _ in got))
        self.assertFalse(any(fid == 5 for fid, _ in got))

    def test_due_pairs_respects_done(self):
        now = datetime(2026, 11, 8, 10, 0)
        matches = [{"fixture_id": 1, "kickoff": "2026-11-08 11:00"}]  # 60 min
        state = {"done": {"conflict_t10": [], "conflict_t120": [], "water_t60": [1]}}
        pairs = due_pairs(matches, state, now)
        self.assertEqual(pairs, [])
        pairs = due_pairs(matches, {"done": {}}, now)
        self.assertEqual({p["strategy"] for p in pairs}, {"water_t60"})

    def test_seed_recent_tracker_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tracking.csv"
            path.write_text(
                "analysis_at,fixture_id,kickoff,stage\n"
                "2026-11-08 11:50:00,1,2026-11-08 12:00,T-10\n"
                "2026-11-08 10:00:00,2,2026-11-08 12:00,T-120\n"
                "2026-11-08 08:00:00,3,2026-11-08 09:00,T-10\n",
                encoding="utf-8-sig",
            )
            state = {"date": "2026-11-08", "done": {}}
            with patch.object(scheduler, "TRACKER_PATH", path):
                _seed_recent_tracker_state(
                    state, datetime(2026, 11, 8, 11, 55)
                )
        self.assertIn(1, state["done"]["conflict_t10"])
        self.assertIn(2, state["done"]["conflict_t120"])
        self.assertNotIn(3, state["done"]["conflict_t10"])

    def test_load_state_drops_legacy_keys_and_unknown_strategies(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scheduler_state.json"
            path.write_text(
                json.dumps(
                    {
                        "date": "2026-11-20",
                        "analyzed": [1],
                        "t30_analyzed": [2],
                        "t60_actionable": [3],
                        "done": {
                            "conflict_t10": [10, 10],
                            "water_t60": ["20"],
                            "removed_t30": [30],
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(scheduler, "STATE_PATH", path), patch.object(
                scheduler, "TRACKER_PATH", Path(tmp) / "missing.csv"
            ):
                state = scheduler.load_state(datetime(2026, 11, 20, 12, 0))
        self.assertEqual(set(state), {"date", "done"})
        self.assertEqual(set(state["done"]), set(scheduler.STRATEGIES))
        self.assertEqual(state["done"]["conflict_t10"], [10])
        self.assertEqual(state["done"]["water_t60"], [20])
        self.assertNotIn("removed_t30", state["done"])

    def test_run_once_marks_coverage_and_saves(self):
        now = datetime(2026, 11, 8, 11, 50)
        matches = [
            {"fixture_id": 1, "kickoff": "2026-11-08 12:00", "league_id": 1}
        ]
        state = {"date": "2026-11-08", "done": {}}
        result = SimpleNamespace(
            actionable=False,
            triggered=False,
            direction_name="观察",
            stake="仅观察",
            data_quality={"coverage_ok": True},
        )
        conn = MagicMock()
        with patch.object(scheduler, "load_state", return_value=state), patch.object(
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
            scheduler,
            "fetch_match",
            return_value=SimpleNamespace(match_time=datetime(2026, 11, 8, 12, 0)),
        ), patch.object(
            scheduler, "fetch_asian", return_value=[object()]
        ), patch.object(
            scheduler, "analyze", return_value=result
        ) as analyze:
            rc = scheduler.run_once(now)
        self.assertEqual(rc, 1)  # only the T-10m window is inside the grace band
        ingest.assert_called_once()
        self.assertEqual(analyze.call_count, 1)
        self.assertIn(1, state["done"]["conflict_t10"])
        save.assert_called_once()
        conn.close.assert_called_once()

    def test_run_once_writes_csv_and_sends_email(self):
        now = datetime(2026, 11, 8, 11, 50)
        matches = [
            {"fixture_id": 1, "kickoff": "2026-11-08 12:00", "league_id": 1}
        ]
        state = {"date": "2026-11-08", "done": {}}
        kickoff = datetime(2026, 11, 8, 12, 0)
        result = SimpleNamespace(
            match=SimpleNamespace(
                fixture_id=1,
                match_time=kickoff,
                league="L",
                home="H",
                away="A",
            ),
            strategy="conflict_t10",
            strategy_label="conflict T-10m",
            decision_minutes=10,
            analysis_time=datetime(2026, 11, 8, 11, 50),
            direction="away",
            direction_name="A",
            triggered=True,
            side_line=0.75,
            is_giving=False,
            confidence=100,
            confidence_label="触发",
            experimental_default=True,
            actionable=True,
            stake="1单位",
            line=-0.75,
            water=0.82,
            reason="test",
            data_quality={"coverage_ok": True},
        )
        email_cfg = SimpleNamespace(
            host="smtp.example.com", port=587, use_ssl=False, use_tls=True,
            mail_to=["a@example.com"],
        )
        conn = MagicMock()
        with patch.object(scheduler, "load_state", return_value=state), patch.object(
            scheduler,
            "load_or_refresh_schedule",
            return_value=(matches, False),
        ), patch.object(scheduler, "save_state"), patch.object(
            scheduler, "ingest_main", return_value=0
        ), patch.object(
            scheduler, "load_config", return_value=object()
        ), patch.object(
            scheduler, "connect", return_value=conn
        ), patch.object(
            scheduler, "fetch_match", return_value=SimpleNamespace(match_time=kickoff)
        ), patch.object(
            scheduler, "fetch_asian", return_value=[object()]
        ), patch.object(
            scheduler, "analyze", return_value=result
        ), patch.object(
            scheduler, "append_rows", return_value=(1, 0)
        ) as append, patch.object(
            scheduler, "email_config_from_env", return_value=email_cfg
        ), patch.object(
            scheduler, "send_actionable_email", return_value=1
        ) as send:
            rc = scheduler.run_once(now)
        self.assertEqual(rc, 1)
        append.assert_called_once()
        send.assert_called_once()
        self.assertFalse(hasattr(scheduler, "append_actionable_json"))

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
                matches = collect_schedule(
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
