from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from rule_engine.features import ConsensusSnapshot, SnapshotDelta, SnapshotQuote
from rule_engine.rules import (
    MIN_LINE_MOVE,
    MIN_REL_WATER_MOVE,
    evaluate_strategy,
    get_strategy,
    resolve_strategies,
)

NOW = datetime(2026, 11, 5, 20, 5)


def snapshot(line, home_water, away_water, quotes=None):
    quotes = quotes or [
        SnapshotQuote(2, "Bet365", NOW, line, home_water, away_water),
        SnapshotQuote(3, "皇冠", NOW, line, home_water, away_water),
    ]
    return ConsensusSnapshot(
        cutoff=NOW,
        line=line,
        home_water=home_water,
        away_water=away_water,
        companies=len(quotes),
        total_companies=len(quotes),
        quotes=quotes,
        latest_change=NOW,
    )


def delta(d_line, d_home, d_away, prev=None, decision=None):
    rel = round(d_home - d_away, 4)
    if d_line < 0:
        line_direction = "home"
    elif d_line > 0:
        line_direction = "away"
    else:
        line_direction = "flat"
    if rel > 0:
        water_direction = "away"
    elif rel < 0:
        water_direction = "home"
    else:
        water_direction = "flat"
    return SnapshotDelta(
        prev=prev or snapshot(-0.5, 0.95, 0.90),
        decision=decision or snapshot(-0.75, 1.02, 0.82),
        d_line=d_line,
        d_home_water=d_home,
        d_away_water=d_away,
        rel_water=rel,
        line_direction=line_direction,
        water_direction=water_direction,
        conflict=(
            line_direction in ("home", "away")
            and water_direction in ("home", "away")
            and line_direction != water_direction
        ),
    )


class RulesTests(unittest.TestCase):
    def test_conflict_t10_triggers_away(self):
        spec = get_strategy("conflict_t10")
        signal = evaluate_strategy(delta(-0.25, 0.07, -0.08), spec)
        self.assertTrue(signal.triggered)
        self.assertEqual(signal.direction, "away")
        self.assertAlmostEqual(signal.consensus_water, 0.82)
        self.assertAlmostEqual(signal.best_water, 0.82)
        self.assertAlmostEqual(signal.bet_line, -0.75)

    def test_conflict_requires_opposite_directions(self):
        spec = get_strategy("conflict_t10")
        # line to home, water also to home (rel negative)
        signal = evaluate_strategy(delta(-0.25, -0.07, 0.08), spec)
        self.assertFalse(signal.triggered)
        self.assertIn("方向一致", signal.reason)

    def test_conflict_requires_both_thresholds(self):
        spec = get_strategy("conflict_t10")
        small_line = evaluate_strategy(delta(-0.20, 0.07, -0.08), spec)
        small_water = evaluate_strategy(delta(-0.25, 0.03, -0.02), spec)
        self.assertFalse(small_line.triggered)
        self.assertFalse(small_water.triggered)
        self.assertIn(f"{MIN_LINE_MOVE:.2f}", small_line.reason)
        self.assertIn(f"{MIN_REL_WATER_MOVE:.2f}", small_water.reason)

    def test_water_t60_triggers_with_flat_line(self):
        spec = get_strategy("water_t60")
        d = delta(
            0.0,
            0.06,
            -0.06,
            prev=snapshot(-0.5, 0.95, 0.90),
            decision=snapshot(-0.5, 1.01, 0.84),
        )
        signal = evaluate_strategy(d, spec)
        self.assertTrue(signal.triggered)
        self.assertEqual(signal.direction, "away")
        self.assertAlmostEqual(d.rel_water, 0.12)

    def test_water_t60_rejects_line_move(self):
        spec = get_strategy("water_t60")
        signal = evaluate_strategy(delta(-0.25, 0.07, -0.08), spec)
        self.assertFalse(signal.triggered)
        self.assertIn("不属于水位单边", signal.reason)

    def test_best_water_uses_best_company_on_main_line(self):
        spec = get_strategy("conflict_t10")
        decision = snapshot(
            -0.75,
            1.02,
            0.82,
            quotes=[
                SnapshotQuote(2, "Bet365", NOW, -0.75, 1.02, 0.82),
                SnapshotQuote(3, "皇冠", NOW, -0.75, 1.00, 0.84),
                SnapshotQuote(7, "澳门", NOW, -0.75, 1.01, 0.80),
                SnapshotQuote(11, "韦德", NOW, -0.50, 0.70, 1.10),
            ],
        )
        signal = evaluate_strategy(delta(-0.25, 0.07, -0.08, decision=decision), spec)
        self.assertTrue(signal.triggered)
        self.assertAlmostEqual(signal.consensus_water, 0.82)
        self.assertAlmostEqual(signal.best_water, 0.84)
        self.assertEqual(signal.best_company, "皇冠")

    def test_strategy_windows_match_document(self):
        t10 = get_strategy("conflict_t10")
        self.assertEqual((t10.prev_minutes, t10.decision_minutes), (30, 10))
        self.assertEqual(t10.kind, "conflict")
        t120 = get_strategy("conflict_t120")
        self.assertEqual((t120.prev_minutes, t120.decision_minutes), (180, 120))
        self.assertEqual(t120.kind, "conflict")
        t60 = get_strategy("water_t60")
        self.assertEqual((t60.prev_minutes, t60.decision_minutes), (120, 60))
        self.assertEqual(t60.kind, "water")

    def test_resolve_strategies(self):
        keys = [spec.key for spec in resolve_strategies(["all"])]
        self.assertEqual(
            keys, ["conflict_t10", "conflict_t120", "water_t60"]
        )
        keys = [spec.key for spec in resolve_strategies(["water_t60,conflict_t10"])]
        self.assertEqual(keys, ["water_t60", "conflict_t10"])

    def test_missing_delta_is_untriggered(self):
        signal = evaluate_strategy(None, get_strategy("conflict_t10"))
        self.assertFalse(signal.triggered)
        self.assertEqual(signal.direction, "observe")


if __name__ == "__main__":
    unittest.main()
