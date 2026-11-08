from __future__ import annotations

import unittest
from datetime import datetime

from rule_engine.scheduler import due_matches, last_refresh_point


class SchedulerTests(unittest.TestCase):
    def test_last_refresh_point(self):
        self.assertEqual(
            last_refresh_point(datetime(2026, 11, 8, 10, 30)),
            datetime(2026, 11, 7, 18, 0),
        )
        self.assertEqual(
            last_refresh_point(datetime(2026, 11, 8, 11, 30)),
            datetime(2026, 11, 8, 11, 0),
        )
        self.assertEqual(
            last_refresh_point(datetime(2026, 11, 8, 18, 30)),
            datetime(2026, 11, 8, 18, 0),
        )
        self.assertEqual(
            last_refresh_point(datetime(2026, 11, 8, 0, 5)),
            datetime(2026, 11, 7, 18, 0),
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


if __name__ == "__main__":
    unittest.main()
