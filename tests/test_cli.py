from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace

from rule_engine.cli import _build_ingest_argv, _parse_ids, _resolve_analysis_time
from rule_engine.models import Match


class CliTests(unittest.TestCase):
    def test_comma_and_space(self):
        self.assertEqual(_parse_ids(["3068888,3069034"]), [3068888, 3069034])
        self.assertEqual(_parse_ids(["3068888", "3069034"]), [3068888, 3069034])

    def test_dedup_preserves_order(self):
        self.assertEqual(
            _parse_ids(["3069034", "3068888", "3069034"]),
            [3069034, 3068888],
        )

    def test_invalid_id(self):
        with self.assertRaises(ValueError):
            _parse_ids(["abc"])

    def test_empty(self):
        with self.assertRaises(ValueError):
            _parse_ids([",", " "])

    def test_decision_minutes_is_per_match(self):
        kickoff = datetime(2026, 11, 5, 20, 15)
        match = Match(fixture_id=1, match_time=kickoff)
        self.assertEqual(
            _resolve_analysis_time(match, None, 60),
            datetime(2026, 11, 5, 19, 15),
        )
        self.assertEqual(
            _resolve_analysis_time(match, None, 120),
            datetime(2026, 11, 5, 18, 15),
        )

    def test_fixed_as_of_is_used_without_decision_minutes(self):
        kickoff = datetime(2026, 11, 5, 20, 15)
        match = Match(fixture_id=1, match_time=kickoff)
        fixed = datetime(2026, 11, 5, 18, 0)
        self.assertEqual(_resolve_analysis_time(match, fixed, None), fixed)

    def test_run_parser_accepts_dates_and_leagues(self):
        from rule_engine.cli import build_parser

        args = build_parser().parse_args(
            [
                "run",
                "--dates",
                "2026-11-05,2026-11-06",
                "--leagues",
                "350,103",
                "--decision-minutes",
                "60",
            ]
        )
        self.assertEqual(args.dates, "2026-11-05,2026-11-06")
        self.assertEqual(args.leagues, "350,103")
        self.assertIsNone(args.ids)
        self.assertIsNone(args.fixture_id)

    def test_run_parser_accepts_within_minutes(self):
        from rule_engine.cli import build_parser

        args = build_parser().parse_args(
            ["run", "--within-minutes", "60", "--experimental-default"]
        )
        self.assertEqual(args.within_minutes, 60)
        self.assertIsNone(args.ids)
        self.assertIsNone(args.fixture_id)
        self.assertEqual(args.min_asian_companies, 2)
        self.assertEqual(args.min_eu_companies, 2)
        self.assertFalse(args.all_leagues)

        args2 = build_parser().parse_args(
            ["run", "--within-minutes", "60", "--all-leagues"]
        )
        self.assertTrue(args2.all_leagues)

    def test_run_parser_ci_mode(self):
        from rule_engine.cli import build_parser

        args = build_parser().parse_args(
            ["run", "--ids", "1", "--ci"]
        )
        self.assertTrue(args.ci)
        self.assertFalse(args.no_report)

    def test_build_ingest_argv_ci_disables_js_save(self):
        args = SimpleNamespace(
            import_dry_run=False,
            database=None,
            eu_companies="2,3",
            timeout=30,
            retries=1,
            log_level="INFO",
            no_save=False,
            save_js="data",
            status_id=1,
            openssl_bin=None,
            ci=True,
        )
        argv = _build_ingest_argv(args, [1])
        self.assertIn("--no-save", argv)

    def test_run_parser_tracks_excel_by_default(self):
        from rule_engine.cli import build_parser
        from rule_engine.tracker import DEFAULT_TRACKER_PATH

        args = build_parser().parse_args(["run", "--ids", "1"])
        self.assertEqual(args.excel, DEFAULT_TRACKER_PATH)
        self.assertFalse(args.no_track)

    def test_build_ingest_argv(self):
        args = SimpleNamespace(
            import_dry_run=True,
            database="football",
            eu_companies="2,3,5,7,9,22",
            timeout=30,
            retries=1,
            log_level="INFO",
            no_save=True,
            save_js="data",
            status_id=1,
            openssl_bin=None,
        )
        argv = _build_ingest_argv(args, [3010473, 3073201])
        self.assertEqual(argv[:2], ["--ids", "3010473,3073201"])
        self.assertIn("--dry-run", argv)
        self.assertIn("--no-save", argv)
        self.assertIn("--database", argv)


if __name__ == "__main__":
    unittest.main()
