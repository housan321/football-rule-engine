from __future__ import annotations

import unittest

from rule_engine.init_db import split_sql


class InitDbTests(unittest.TestCase):
    def test_split_sql_ignores_comments(self):
        sql = """
-- comment; with semicolon
CREATE TABLE a (x INT);
USE football;

CREATE TABLE b (y INT);
"""
        statements = split_sql(sql)
        self.assertEqual(len(statements), 3)
        self.assertTrue(statements[0].startswith("CREATE TABLE a"))
        self.assertEqual(statements[1], "USE football")
        self.assertTrue(statements[2].startswith("CREATE TABLE b"))


if __name__ == "__main__":
    unittest.main()
