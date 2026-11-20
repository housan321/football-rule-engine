"""The three pre-registered multi-window strategies.

All timestamps are cut-offs relative to kickoff.  The *decision* window is when
the bet is placed; the *prev* window is only the baseline for the move.

============================================================  =========  ==========
strategy                                                        compares   bet at
============================================================  =========  ==========
conflict T-10m                                                  T-30m→T-10m  T-10m
conflict T-120m                                                 T-3h→T-2h    T-2h
water T-60m                                                     T-2h→T-1h    T-1h
============================================================  =========  ==========

Definitions (from 《规则回测分析结果.docx》):

* Δline = line(decision) - line(prev)
* relative water change = Δhome_water - Δaway_water
* line direction = sign(Δline): negative → home, positive → away
* water direction = sign(relative water change): positive → away, negative → home

conflict: |Δline| >= 0.25 and |relative water| >= 0.06 and the two point in
          opposite directions.  Bet the water direction.
water:    |Δline| < 0.25 and |relative water| >= 0.06.  Bet the water
          direction.  (No line signal at all.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .features import SnapshotDelta, best_water, side_water

MIN_LINE_MOVE = 0.25
MIN_REL_WATER_MOVE = 0.06
EPS = 1e-9


@dataclass(frozen=True)
class StrategySpec:
    key: str
    label: str
    prev_minutes: int
    decision_minutes: int
    kind: str  # "conflict" or "water"

    @property
    def prev_label(self) -> str:
        return _window_label(self.prev_minutes)

    @property
    def decision_label(self) -> str:
        return _window_label(self.decision_minutes)


def _window_label(minutes: int) -> str:
    if minutes >= 120 and minutes % 60 == 0:
        return f"T-{minutes // 60}h"
    return f"T-{minutes}m"


STRATEGIES: dict[str, StrategySpec] = {
    "conflict_t10": StrategySpec(
        key="conflict_t10",
        label="conflict T-10m",
        prev_minutes=30,
        decision_minutes=10,
        kind="conflict",
    ),
    "conflict_t120": StrategySpec(
        key="conflict_t120",
        label="conflict T-120m",
        prev_minutes=180,
        decision_minutes=120,
        kind="conflict",
    ),
    "water_t60": StrategySpec(
        key="water_t60",
        label="water T-60m",
        prev_minutes=120,
        decision_minutes=60,
        kind="water",
    ),
}

DEFAULT_STRATEGY_KEYS: tuple[str, ...] = (
    "conflict_t10",
    "conflict_t120",
    "water_t60",
)

PRICE_MODES = ("consensus", "best")


def get_strategy(key: str) -> StrategySpec:
    try:
        return STRATEGIES[key]
    except KeyError as exc:
        raise ValueError(
            f"未知策略 {key!r}；可选：{', '.join(STRATEGIES)}"
        ) from exc


def resolve_strategies(values) -> list[StrategySpec]:
    """Expand ``all`` / aliases / a list of keys into strategy specs."""
    if values is None:
        values = list(DEFAULT_STRATEGY_KEYS)
    if isinstance(values, str):
        values = [values]
    keys: list[str] = []
    for value in values:
        for part in str(value).split(","):
            part = part.strip()
            if not part:
                continue
            if part.lower() == "all":
                keys.extend(DEFAULT_STRATEGY_KEYS)
            else:
                keys.append(part)
    if not keys:
        keys = list(DEFAULT_STRATEGY_KEYS)
    seen: set[str] = set()
    out: list[StrategySpec] = []
    for key in keys:
        spec = get_strategy(key)
        if spec.key not in seen:
            seen.add(spec.key)
            out.append(spec)
    return out


@dataclass
class StrategySignal:
    spec: StrategySpec
    triggered: bool
    direction: str  # home / away / observe
    reason: str
    line_ok: bool
    water_ok: bool
    direction_opposite: bool
    bet_line: Optional[float] = None
    consensus_water: Optional[float] = None
    best_water: Optional[float] = None
    best_company: Optional[str] = None
    conditions: dict = field(default_factory=dict)

    @property
    def strength(self) -> float:
        return 1.0 if self.triggered else 0.0


def _side_cn(direction: str) -> str:
    return {"home": "主队", "away": "客队"}.get(direction, "无")


def _delta_text(delta: SnapshotDelta) -> str:
    prev, decision = delta.prev, delta.decision
    return (
        f"{prev.cutoff:%H:%M} {prev.line:+.2f}/{prev.home_water:.2f}/"
        f"{prev.away_water:.2f} → {decision.cutoff:%H:%M} "
        f"{decision.line:+.2f}/{decision.home_water:.2f}/"
        f"{decision.away_water:.2f}"
    )


def evaluate_strategy(delta: Optional[SnapshotDelta], spec: StrategySpec) -> StrategySignal:
    conditions = {
        "min_line_move": MIN_LINE_MOVE,
        "min_rel_water_move": MIN_REL_WATER_MOVE,
    }
    if delta is None:
        return StrategySignal(
            spec=spec,
            triggered=False,
            direction="observe",
            reason=f"{spec.prev_label} 或 {spec.decision_label} 没有可用的公司共识快照",
            line_ok=False,
            water_ok=False,
            direction_opposite=False,
            conditions=conditions,
        )

    line_ok = abs(delta.d_line) >= MIN_LINE_MOVE - EPS
    water_ok = abs(delta.rel_water) >= MIN_REL_WATER_MOVE - EPS

    if spec.kind == "conflict":
        triggered = bool(line_ok and water_ok and delta.conflict)
    else:  # water-only: the line must NOT have moved
        triggered = bool((not line_ok) and water_ok)

    direction = delta.water_direction if triggered else "observe"

    line_desc = (
        f"Δ盘口 {delta.d_line:+.2f}（朝{_side_cn(delta.line_direction)}）"
        if delta.line_direction != "flat"
        else f"Δ盘口 {delta.d_line:+.2f}（未动）"
    )
    water_desc = (
        f"相对水位 {delta.rel_water:+.2f}（朝{_side_cn(delta.water_direction)}）"
        if delta.water_direction != "flat"
        else f"相对水位 {delta.rel_water:+.2f}（未动）"
    )
    head = f"{_delta_text(delta)}；{line_desc}，{water_desc}"

    if triggered:
        if spec.kind == "conflict":
            reason = (
                f"{head}；盘口与水位方向相反 → 买{_side_cn(direction)}"
                f"（水位方向），在 {spec.decision_label} 下注"
            )
        else:
            reason = (
                f"{head}；盘口无明显移动（|Δ|<{MIN_LINE_MOVE:.2f}），"
                f"仅水位单边 → 买{_side_cn(direction)}，在 {spec.decision_label} 下注"
            )
    else:
        if spec.kind == "conflict":
            if not line_ok and not water_ok:
                why = "盘口与水位变动均未达阈值"
            elif not line_ok:
                why = f"盘口变动 {delta.d_line:+.2f} 未达 ±{MIN_LINE_MOVE:.2f}"
            elif not water_ok:
                why = f"相对水位 {delta.rel_water:+.2f} 未达 ±{MIN_REL_WATER_MOVE:.2f}"
            else:
                why = "盘口与水位方向一致，不是背离"
            reason = f"{head}；不触发：{why}"
        else:
            if line_ok:
                why = f"盘口变动 {delta.d_line:+.2f} 达到 ±{MIN_LINE_MOVE:.2f}，不属于水位单边"
            elif not water_ok:
                why = f"相对水位 {delta.rel_water:+.2f} 未达 ±{MIN_REL_WATER_MOVE:.2f}"
            else:
                why = "未满足条件"
            reason = f"{head}；不触发：{why}"

    if not triggered:
        return StrategySignal(
            spec=spec,
            triggered=False,
            direction="observe",
            reason=reason,
            line_ok=line_ok,
            water_ok=water_ok,
            direction_opposite=delta.conflict,
            conditions=conditions,
        )

    decision = delta.decision
    consensus = side_water(decision, direction)
    best, best_company = best_water(decision, direction)
    return StrategySignal(
        spec=spec,
        triggered=True,
        direction=direction,
        reason=reason,
        line_ok=line_ok,
        water_ok=water_ok,
        direction_opposite=delta.conflict,
        bet_line=decision.line,
        consensus_water=consensus,
        best_water=best,
        best_company=best_company,
        conditions=conditions,
    )
