from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Settlement:
    profit: float
    outcome: str  # win / half_win / push / half_loss / loss
    adjusted: float | None = None


def _unit(line: float, side_margin: float, water: float, stake: float) -> tuple[float, str, float]:
    adjusted = side_margin + line
    if adjusted > 1e-9:
        return stake * water, "win", adjusted
    if adjusted < -1e-9:
        return -stake, "loss", adjusted
    return 0.0, "push", adjusted


def settle_asian(
    line: float,
    water: float,
    home_score: int,
    away_score: int,
    direction: str,
) -> Settlement:
    """Settle a 1-unit Asian handicap bet.

    line is from the bettor's perspective: negative = gives handicap.
    direction: home or away.
    water: Asian profit odds (e.g. 0.85).
    """
    if direction not in ("home", "away"):
        raise ValueError("direction must be home or away")

    margin = home_score - away_score
    side_margin = margin if direction == "home" else -margin

    # Quarter lines end in .25/.75 and must be split into two half bets.
    is_quarter = abs(line * 2 - round(line * 2)) > 1e-9
    if is_quarter:
        p1, o1, a1 = _unit(line - 0.25, side_margin, water, 0.5)
        p2, o2, a2 = _unit(line + 0.25, side_margin, water, 0.5)
        outcomes = {o1, o2}
        if outcomes == {"win"}:
            outcome = "win"
        elif outcomes == {"loss"}:
            outcome = "loss"
        elif outcomes == {"push"}:
            outcome = "push"
        elif "win" in outcomes and "push" in outcomes:
            outcome = "half_win"
        elif "loss" in outcomes and "push" in outcomes:
            outcome = "half_loss"
        else:  # pragma: no cover - adjacent quarter lines cannot be win+loss
            outcome = "split"
        return Settlement(round(p1 + p2, 4), outcome, round((a1 + a2) / 2, 4))

    p, o, a = _unit(line, side_margin, water, 1.0)
    return Settlement(round(p, 4), o, round(a, 4))
