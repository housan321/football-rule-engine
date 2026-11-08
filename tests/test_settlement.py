from __future__ import annotations

import unittest

from rule_engine.settlement import settle_asian


class SettlementTests(unittest.TestCase):
    def test_half_ball_win_loss(self):
        win = settle_asian(-0.5, 0.90, 1, 0, "home")
        self.assertEqual(win.outcome, "win")
        self.assertAlmostEqual(win.profit, 0.90)

        loss = settle_asian(-0.5, 0.90, 0, 0, "home")
        self.assertEqual(loss.outcome, "loss")
        self.assertAlmostEqual(loss.profit, -1.0)

    def test_quarter_ball_half_loss(self):
        result = settle_asian(-0.25, 0.90, 0, 0, "home")
        self.assertEqual(result.outcome, "half_loss")
        self.assertAlmostEqual(result.profit, -0.5)

    def test_quarter_ball_half_win(self):
        result = settle_asian(0.25, 0.90, 0, 0, "home")
        self.assertEqual(result.outcome, "half_win")
        self.assertAlmostEqual(result.profit, 0.45)

    def test_three_quarter_half_win(self):
        result = settle_asian(-0.75, 0.90, 1, 0, "home")
        self.assertEqual(result.outcome, "half_win")
        self.assertAlmostEqual(result.profit, 0.45)

    def test_three_quarter_loss(self):
        result = settle_asian(-0.75, 0.90, 0, 0, "home")
        self.assertEqual(result.outcome, "loss")
        self.assertAlmostEqual(result.profit, -1.0)

    def test_pk_push(self):
        result = settle_asian(0.0, 0.90, 1, 1, "home")
        self.assertEqual(result.outcome, "push")
        self.assertAlmostEqual(result.profit, 0.0)

    def test_away_receiving_half_ball(self):
        result = settle_asian(0.5, 0.95, 1, 1, "away")
        self.assertEqual(result.outcome, "win")
        self.assertAlmostEqual(result.profit, 0.95)


if __name__ == "__main__":
    unittest.main()
