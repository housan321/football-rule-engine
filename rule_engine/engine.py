"""Rule engine for the three consensus-snapshot strategies.

The engine is deliberately thin: it builds the two snapshots, evaluates one
strategy, and exposes the result (direction, bet line, bet water, audit trail).
There is no score aggregation, no confidence tuning and no other rule set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .features import (
    MIN_SNAPSHOT_COMPANIES,
    SnapshotDelta,
    build_consensus_snapshot,
    build_snapshot_delta,
)
from .models import CompanySeries, Match, SupInfo
from .rules import PRICE_MODES, StrategySignal, StrategySpec, evaluate_strategy, get_strategy


@dataclass
class RuleHit:
    code: str
    name: str
    direction: str
    strength: float
    reason: str
    category: str
    evidence: dict = field(default_factory=dict)


@dataclass
class AnalysisResult:
    match: Match
    strategy: str
    strategy_label: str
    analysis_time: datetime  # decision cut-off
    prev_time: datetime  # baseline cut-off
    direction: str  # home / away / observe
    triggered: bool
    confidence: int
    confidence_label: str
    stake: str
    summary: str
    reason: str
    prev_line: Optional[float] = None
    line: Optional[float] = None
    line_delta: Optional[float] = None
    home_water_delta: Optional[float] = None
    away_water_delta: Optional[float] = None
    rel_water: Optional[float] = None
    line_direction: str = "flat"
    water_direction: str = "flat"
    price_mode: str = "consensus"
    water: Optional[float] = None
    consensus_water: Optional[float] = None
    best_water: Optional[float] = None
    best_company: Optional[str] = None
    companies_prev: int = 0
    companies_decision: int = 0
    rule_hits: list[RuleHit] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    invalidation: list[str] = field(default_factory=list)
    data_quality: dict = field(default_factory=dict)
    features: dict = field(default_factory=dict)
    sup: Optional[SupInfo] = None
    side_line: Optional[float] = None
    is_giving: bool = False
    actionable: bool = False
    experimental_default: bool = False
    filter_notes: list[str] = field(default_factory=list)

    @property
    def mode(self) -> str:
        """Alias kept for output compatibility: the strategy key."""
        return self.strategy

    @property
    def direction_name(self) -> str:
        if self.direction == "home":
            return self.match.home
        if self.direction == "away":
            return self.match.away
        return "观察"

    @property
    def decision_minutes(self) -> int:
        return get_strategy(self.strategy).decision_minutes

    def to_dict(self) -> dict:
        return {
            "fixture_id": self.match.fixture_id,
            "league": self.match.league,
            "home": self.match.home,
            "away": self.match.away,
            "kickoff": self.match.match_time.isoformat(sep=" "),
            "strategy": self.strategy,
            "strategy_label": self.strategy_label,
            "mode": self.mode,
            "analysis_at": self.analysis_time.isoformat(sep=" "),
            "prev_at": self.prev_time.isoformat(sep=" "),
            "direction": self.direction,
            "direction_name": self.direction_name,
            "triggered": self.triggered,
            "actionable": self.actionable,
            "stake": self.stake,
            "side_line": self.side_line,
            "is_giving": self.is_giving,
            "price_mode": self.price_mode,
            "water": self.water,
            "consensus_water": self.consensus_water,
            "best_water": self.best_water,
            "best_company": self.best_company,
            "prev_line": self.prev_line,
            "line": self.line,
            "line_delta": self.line_delta,
            "home_water_delta": self.home_water_delta,
            "away_water_delta": self.away_water_delta,
            "rel_water": self.rel_water,
            "line_direction": self.line_direction,
            "water_direction": self.water_direction,
            "companies_prev": self.companies_prev,
            "companies_decision": self.companies_decision,
            "confidence": self.confidence,
            "confidence_label": self.confidence_label,
            "summary": self.summary,
            "reason": self.reason,
            "conflicts": self.conflicts,
            "invalidation": self.invalidation,
            "data_quality": self.data_quality,
            "features": self.features,
            "sup": {
                "available": self.sup.available,
                "sup": self.sup.sup,
                "detail": self.sup.detail,
            }
            if self.sup
            else None,
            "rule_hits": [
                {
                    "code": h.code,
                    "name": h.name,
                    "direction": h.direction,
                    "strength": h.strength,
                    "category": h.category,
                    "reason": h.reason,
                    "evidence": h.evidence,
                }
                for h in self.rule_hits
            ],
        }


def _hit_code(spec: StrategySpec) -> str:
    return spec.key.upper()


def _make_invalidation(spec: StrategySpec) -> list[str]:
    return [
        f"本规则只在 {spec.decision_label} 决策，其他时点不适用",
        f"{spec.prev_label} / {spec.decision_label} 任一快照少于 "
        f"{MIN_SNAPSHOT_COMPANIES} 家同盘口公司 → 不视为共识，不触发",
        "决策后再出现盘口/水位反向变化 → 原信号已过期，不追注",
        "只比较两个快照的净变化；中间的来回震荡不影响判定",
    ]


def _features(
    spec: StrategySpec,
    prev_time: datetime,
    decision_time: datetime,
    delta: Optional[SnapshotDelta],
    signal: StrategySignal,
    price_mode: str,
) -> dict:
    decision = delta.decision if delta else None
    prev = delta.prev if delta else None
    asian: dict = {
        "strategy": spec.key,
        "prev_time": prev_time.isoformat(sep=" "),
        "decision_time": decision_time.isoformat(sep=" "),
        "latest_line": decision.line if decision else None,
        "home_latest_water": decision.home_water if decision else None,
        "away_latest_water": decision.away_water if decision else None,
        "line_delta": delta.d_line if delta else None,
        "rel_water": delta.rel_water if delta else None,
        "line_direction": delta.line_direction if delta else "flat",
        "water_direction": delta.water_direction if delta else "flat",
        "conflict": delta.conflict if delta else False,
        "prev": prev.to_dict() if prev else None,
        "decision": decision.to_dict() if decision else None,
    }
    return {
        "strategy": spec.key,
        "strategy_label": spec.label,
        "price_mode": price_mode,
        "conditions": signal.conditions,
        "triggered": signal.triggered,
        "direction": signal.direction,
        "bet_line": signal.bet_line,
        "consensus_water": signal.consensus_water,
        "best_water": signal.best_water,
        "best_company": signal.best_company,
        "asian": asian,
    }


def analyze(
    match: Match,
    strategy: str,
    asian_series: list[CompanySeries],
    price: str = "consensus",
    min_companies: int = MIN_SNAPSHOT_COMPANIES,
) -> AnalysisResult:
    """Evaluate one strategy for one fixture.

    ``asian_series`` may contain rows after the decision cut-off; snapshots
    filter by ``change_time <= cutoff``, so no future information is used.
    """
    if price not in PRICE_MODES:
        raise ValueError(f"price 必须是 {PRICE_MODES} 之一，收到 {price!r}")
    spec = get_strategy(strategy)
    decision_time = match.match_time - timedelta(minutes=spec.decision_minutes)
    prev_time = match.match_time - timedelta(minutes=spec.prev_minutes)

    delta = build_snapshot_delta(asian_series, prev_time, decision_time)
    prev = delta.prev if delta else build_consensus_snapshot(asian_series, prev_time)
    decision = (
        delta.decision
        if delta
        else build_consensus_snapshot(asian_series, decision_time)
    )
    coverage_ok = bool(
        prev
        and decision
        and prev.companies >= min_companies
        and decision.companies >= min_companies
    )

    if not coverage_ok:
        signal = StrategySignal(
            spec=spec,
            triggered=False,
            direction="observe",
            reason=(
                f"共识公司不足：{spec.prev_label} "
                f"{prev.companies if prev else 0} 家、{spec.decision_label} "
                f"{decision.companies if decision else 0} 家"
                f"（要求 ≥{min_companies} 家同盘口）"
            ),
            line_ok=False,
            water_ok=False,
            direction_opposite=bool(delta.conflict) if delta else False,
            conditions={
                "min_line_move": None,
                "min_rel_water_move": None,
                "min_companies": min_companies,
            },
        )
    else:
        signal = evaluate_strategy(delta, spec)

    triggered = bool(signal.triggered)
    direction = signal.direction if triggered else "observe"
    side_line = None
    if triggered and signal.bet_line is not None:
        side_line = (
            signal.bet_line if direction == "home" else -signal.bet_line
        )
    selected_water = (
        signal.consensus_water if price == "consensus" else signal.best_water
    )
    if triggered and selected_water is None:
        selected_water = signal.consensus_water

    confidence = 100 if triggered else 0
    result = AnalysisResult(
        match=match,
        strategy=spec.key,
        strategy_label=spec.label,
        analysis_time=decision_time,
        prev_time=prev_time,
        direction=direction,
        triggered=triggered,
        confidence=confidence,
        confidence_label="触发" if triggered else "未触发",
        stake="1单位" if triggered else "仅观察",
        summary=signal.reason,
        reason=signal.reason,
        prev_line=delta.prev.line if delta else (prev.line if prev else None),
        line=delta.decision.line if delta else (decision.line if decision else None),
        line_delta=delta.d_line if delta else None,
        home_water_delta=delta.d_home_water if delta else None,
        away_water_delta=delta.d_away_water if delta else None,
        rel_water=delta.rel_water if delta else None,
        line_direction=delta.line_direction if delta else "flat",
        water_direction=delta.water_direction if delta else "flat",
        price_mode=price,
        water=selected_water,
        consensus_water=signal.consensus_water,
        best_water=signal.best_water,
        best_company=signal.best_company,
        companies_prev=prev.companies if prev else 0,
        companies_decision=decision.companies if decision else 0,
        rule_hits=[
            RuleHit(
                code=_hit_code(spec),
                name=spec.label,
                direction=direction,
                strength=signal.strength,
                reason=signal.reason,
                category=spec.kind,
                evidence={
                    "line_delta": delta.d_line if delta else None,
                    "rel_water": delta.rel_water if delta else None,
                    "line_direction": delta.line_direction if delta else None,
                    "water_direction": delta.water_direction if delta else None,
                },
            )
        ],
        conflicts=[],
        invalidation=_make_invalidation(spec),
        data_quality={
            "companies_prev": prev.companies if prev else 0,
            "companies_decision": decision.companies if decision else 0,
            "total_companies_prev": prev.total_companies if prev else 0,
            "total_companies_decision": decision.total_companies if decision else 0,
            "min_companies": min_companies,
            "coverage_ok": coverage_ok,
            "snapshot_prev": prev.to_dict() if prev else None,
            "snapshot_decision": decision.to_dict() if decision else None,
        },
        features=_features(spec, prev_time, decision_time, delta, signal, price),
        side_line=side_line,
        is_giving=bool(side_line is not None and side_line < 0),
        actionable=triggered,
        experimental_default=triggered,
    )
    if not triggered:
        result.stake = "仅观察"
    return result


def apply_action_filters(result: AnalysisResult, *args, **kwargs) -> AnalysisResult:
    """Compatibility shim.

    The new rules are binary and pre-registered: there is no confidence gate and
    no only-giving filter.  Any legacy arguments are accepted and ignored so old
    call sites do not silently change the signal.
    """
    result.actionable = result.triggered
    result.experimental_default = result.triggered
    if not result.actionable:
        result.stake = "仅观察"
    return result
