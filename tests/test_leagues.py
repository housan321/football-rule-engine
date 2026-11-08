from __future__ import annotations

import unittest

from rule_engine.leagues import LEAGUE_IDS, LEAGUE_NAMES, LEAGUE_WHITELIST, league_id


class LeagueWhitelistTests(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(league_id("德乙"), 9)
        self.assertEqual(league_id("日职联"), 25)
        self.assertEqual(league_id("土超"), 30)
        self.assertIsNone(league_id("不存在"))

    def test_ids_are_unique(self):
        self.assertEqual(len(LEAGUE_IDS), len(set(LEAGUE_WHITELIST.values())))
        self.assertGreaterEqual(len(LEAGUE_IDS), 40)
        self.assertIn(9, LEAGUE_IDS)
        self.assertIn(25, LEAGUE_IDS)

    def test_names(self):
        self.assertIn("英超", LEAGUE_NAMES)
        self.assertIn("巴西甲", LEAGUE_NAMES)


if __name__ == "__main__":
    unittest.main()
