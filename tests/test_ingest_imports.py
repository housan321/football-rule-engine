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

        from rule_engine.ingest.crawl import _iter_schedule_rows_full, _schedule_kickoff

        html = """
        <tr id='tr1_1' name='30,1' sId='3026782'>
          <td>League</td><td>11-8 00:00</td><td>Home</td><td>Away</td>
        </tr>
        """
        rows = list(_iter_schedule_rows_full(html))
        self.assertEqual(rows[0][0], "3026782")
        self.assertEqual(
            _schedule_kickoff(rows[0][2], "20261107"),
            datetime(2026, 11, 8, 0, 0),
        )
        self.assertEqual(
            _schedule_kickoff("1-1 00:00", "20261231"),
            datetime(2027, 1, 1, 0, 0),
        )


if __name__ == "__main__":
    unittest.main()
