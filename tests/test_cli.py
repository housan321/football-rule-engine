from __future__ import annotations

import unittest
from types import SimpleNamespace

from rule_engine.cli import _build_ingest_argv, _parse_ids, build_parser


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

    def test_analyze_parser_strategies_and_price(self):
        args = build_parser().parse_args(
            ["analyze", "--fixture-id", "1", "--strategies", "all",
             "--price", "best"]
        )
        self.assertEqual(args.strategies, ["all"])
        self.assertEqual(args.price, "best")
        self.assertEqual(args.min_asian_companies, 2)

    def test_analyze_parser_accepts_multiple_strategies(self):
        args = build_parser().parse_args(
            ["analyze", "--ids", "1", "2",
             "--strategies", "conflict_t10", "water_t60"]
        )
        self.assertEqual(args.strategies, ["conflict_t10", "water_t60"])

    def test_scan_parser(self):
        args = build_parser().parse_args(["scan", "--date", "2026-11-05"])
        self.assertEqual(args.date, "2026-11-05")
        self.assertEqual(args.strategies, ["all"])

    def test_run_parser_accepts_dates_and_leagues(self):
        args = build_parser().parse_args(
            [
                "run",
                "--dates",
                "2026-11-05,2026-11-06",
                "--leagues",
                "350,103",
                "--strategies",
                "conflict_t10",
            ]
        )
        self.assertEqual(args.dates, "2026-11-05,2026-11-06")
        self.assertEqual(args.leagues, "350,103")
        self.assertIsNone(args.ids)
        self.assertIsNone(args.fixture_id)
        self.assertEqual(args.strategies, ["conflict_t10"])

    def test_run_parser_accepts_within_minutes(self):
        args = build_parser().parse_args(
            ["run", "--within-minutes", "120", "--all-leagues"]
        )
        self.assertEqual(args.within_minutes, 120)
        self.assertTrue(args.all_leagues)
        self.assertFalse(args.skip_import)

    def test_run_parser_ci_mode(self):
        args = build_parser().parse_args(["run", "--ids", "1", "--ci"])
        self.assertTrue(args.ci)
        self.assertFalse(args.no_report)

    def test_backtest_parser(self):
        args = build_parser().parse_args(
            ["backtest", "--from", "2025-08-01", "--to", "2026-11-01",
             "--strategies", "all"]
        )
        self.assertEqual(args.start, "2025-08-01")
        self.assertEqual(args.end, "2026-11-01")
        self.assertEqual(args.strategies, ["all"])

    def test_track_parser(self):
        args = build_parser().parse_args(
            ["track", "--ids", "1", "--strategies", "water_t60"]
        )
        self.assertEqual(args.strategies, ["water_t60"])
        self.assertFalse(args.include_non_actionable)

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

    def test_run_parser_tracks_csv_by_default(self):
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
