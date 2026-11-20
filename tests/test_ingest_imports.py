from __future__ import annotations

import unittest


class IngestImportTests(unittest.TestCase):
    def test_import_stack_loads(self):
        from rule_engine.ingest import all as ingest_all

        self.assertTrue(callable(ingest_all.main))
        self.assertTrue(callable(ingest_all.parse_args))

    def test_ingest_modules_are_self_contained(self):
        import rule_engine.ingest.crawl  # noqa: F401
        import rule_engine.ingest.db  # noqa: F401
        import rule_engine.ingest.eu  # noqa: F401
        import rule_engine.ingest.parser  # noqa: F401
        import rule_engine.ingest.persist  # noqa: F401
        import rule_engine.ingest.pipeline  # noqa: F401

    def test_schedule_row_parser(self):
        from rule_engine.ingest.crawl import _iter_schedule_rows

        html = """
        <html><body><table id="table_live">
          <tr sid="123" name="192,League A"><td>x</td></tr>
          <tr sid="456" name="350,League B"><td>y</td></tr>
        </table></body></html>
        """
        rows = list(_iter_schedule_rows(html))
        self.assertIn(("123", "192,League A"), rows)
        self.assertIn(("456", "350,League B"), rows)

    def test_schedule_dates_include_previous_day(self):
        from datetime import datetime

        from rule_engine.ingest.crawl import _schedule_dates

        dates = _schedule_dates(datetime(2026, 11, 8, 0, 19), 60)
        self.assertIn("20261107", dates)
        self.assertIn("20261108", dates)

    def test_schedule_time_parser(self):
        from datetime import datetime

        from rule_engine.ingest.crawl import (
            _iter_schedule_rows_full,
            _schedule_kickoff,
            clean_team_cell,
        )

        html = """
        <tr id='tr1_1' name='30,1' sId='3026782'>
          <td>League</td><td>11-8 00:00</td><td>Home</td><td>Away</td>
        </tr>
        """
        rows = list(_iter_schedule_rows_full(html))
        self.assertEqual(rows[0][0], "3026782")
        self.assertEqual(rows[0][3], ["League", "11-8 00:00", "Home", "Away"])
        self.assertEqual(
            _schedule_kickoff(rows[0][2], "20261107"),
            datetime(2026, 11, 8, 0, 0),
        )
        self.assertEqual(clean_team_cell("[6] Home"), "Home")
        self.assertEqual(clean_team_cell("Away [8]"), "Away")
        self.assertEqual(
            _schedule_kickoff("1-1 00:00", "20261231"),
            datetime(2027, 1, 1, 0, 0),
        )

    def test_openssl3_client_is_flagged(self):
        from unittest.mock import patch

        from rule_engine.ingest import crawl

        class _Proc:
            stdout = "OpenSSL 3.0.13 30 Jan 2024 (Library: OpenSSL 3.0.13)\n"

        with patch.object(crawl.subprocess, "run", return_value=_Proc()):
            with self.assertLogs("titan007.openssl", level="WARNING") as logs:
                crawl._check_openssl_version("openssl")
        self.assertIn("OpenSSL 3.x", "\n".join(logs.output))

    def test_openssl11_client_is_not_flagged(self):
        from unittest.mock import patch

        from rule_engine.ingest import crawl

        class _Proc:
            stdout = "OpenSSL 1.1.1w  11 Sep 2023\n"

        with patch.object(crawl.subprocess, "run", return_value=_Proc()):
            with self.assertLogs("titan007.openssl", level="DEBUG") as logs:
                crawl._check_openssl_version("openssl")
        self.assertNotIn("WARNING", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
