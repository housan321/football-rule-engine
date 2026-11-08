from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .features import (
    AsianFeatures,
    EuroFeatures,
    build_asian_features,
    build_company_views,
    build_euro_features,
    features_snapshot,
)
from .models import Match, SupInfo
from .rules import RuleHit, RuleReport, evaluate


@dataclass
class AnalysisResult:
    match: Match
    mode: str
    analysis_time: datetime
    direction: str  # home / away / observe
    confidence: int
    confidence_label: str
    stake: str
    score_home: float
    score_away: float
    conflict_ratio: float
    summary: str
    rule_hits: list[RuleHit] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    invalidation: list[str] = field(default_factory=list)
    data_quality: dict = field(default_factory=dict)
    features: dict = field(default_factory=dict)
    sup: Optional[SupInfo] = None
    side_line: Optional[float] = None
    is_giving: bool = False
    experimental_default: bool = False
    actionable: bool = False
    filter_notes: list[str] = field(default_factory=list)

    @property
    def direction_name(self) -> str:
        if self.direction == "home":
            return self.match.home
        if self.direction == "away":
            return self.match.away
        return "观察"

    def to_dict(self) -> dict:
        return {
            "fixture_id": self.match.fixture_id,
            "league": self.match.league,
            "home": self.match.home,
            "away": self.match.away,
            "kickoff": self.match.match_time.isoformat(sep=" "),
            "mode": self.mode,
            "analysis_at": self.analysis_time.isoformat(sep=" "),
            "direction": self.direction,
            "direction_name": self.direction_name,
            "confidence": self.confidence,
            "confidence_label": self.confidence_label,
            "stake": self.stake,
            "side_line": self.side_line,
            "is_giving": self.is_giving,
            "experimental_default": self.experimental_default,
            "actionable": self.actionable,
            "filter_notes": self.filter_notes,
            "score_home": round(self.score_home, 2),
            "score_away": round(self.score_away, 2),
            "conflict_ratio": round(self.conflict_ratio, 2),
            "summary": self.summary,
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


def _fair_water(line: float, p_home: float, p_draw: float, p_away: float) -> float:
    """Approximate fair Asian water for a side, from de-margined euro probabilities.

    line is from the bettor side's perspective: negative = gives handicap.
    """
    eps = 1e-9
    if line <= -0.5:
        return (p_draw + p_away) / max(p_home, eps)
    if line < -eps:
        return (p_away + 0.5 * p_draw) / max(p_home, eps)
    if abs(line) <= eps:
        return p_away / max(p_home, eps)
    if line < 0.5:
        return max((p_away - 0.5 * p_draw) / max(p_home, eps), 0.0)
    return p_away / max(p_home + p_draw, eps)


def _confidence_label(confidence: int) -> str:
    if confidence < 40:
        return "低"
    if confidence < 50:
        return "低-中"
    return "中低"


def side_line_for(result: AnalysisResult) -> Optional[float]:
    """Handicap from the bettor side's perspective: negative = giving."""
    line = float(result.features.get("asian", {}).get("latest_line", 0) or 0)
    if result.direction == "home":
        return line
    if result.direction == "away":
        return -line
    return None


def apply_action_filters(
    result: AnalysisResult,
    only_giving: bool = False,
    min_confidence: Optional[int] = None,
) -> AnalysisResult:
    """Apply the Phase 3 experimental gate without changing direction/confidence.

    - experimental_default is always computed: giving side + conf >= 50.
    - only_giving suppresses receiving/PK bets.
    - min_confidence suppresses bets below the threshold.
    Suppressed bets keep their direction but become "仅观察".
    """
    side = side_line_for(result)
    result.side_line = side
    result.is_giving = bool(side is not None and side < 0)
    result.experimental_default = bool(
        result.direction != "observe"
        and result.is_giving
        and result.confidence >= 50
    )

    actionable = result.direction != "observe" and result.stake == "小注"
    notes: list[str] = []
    if actionable and only_giving and not result.is_giving:
        actionable = False
        notes.append("非让球方（受让/PK），不符合实验默认")
    if (
        actionable
        and min_confidence is not None
        and result.confidence < min_confidence
    ):
        actionable = False
        notes.append(f"置信度 {result.confidence} < {min_confidence}")

    result.actionable = actionable
    result.filter_notes = notes
    if not actionable and result.stake == "小注":
        result.stake = "仅观察"
    return result


def _make_invalidation(result: AnalysisResult) -> list[str]:
    items: list[str] = []
    if result.direction == "home":
        items.append(f"若盘口反向退升且{result.match.home}水位回升至 ≥0.95 → 原方向失效")
        items.append(f"{result.match.away}先进球 → 离场")
    elif result.direction == "away":
        items.append(f"若盘口反向退升且{result.match.away}水位回升至 ≥0.95 → 原方向失效")
        items.append(f"{result.match.home}先进球 → 离场")
    else:
        items.append("当前无方向：待盘口/水位形成一致信号后再评估")
    items.append("数据超过 15 分钟未更新（滚球）→ 失效，需刷新")
    items.append("澳门与多数公司重新同向且方向反转 → 重新评估")
    return items


def analyze(
    match: Match,
    mode: str,
    analysis_time: datetime,
    asian_series,
    euro_series,
    sup: SupInfo,
) -> AnalysisResult:
    views = build_company_views(asian_series, match.match_time)
    asian = build_asian_features(views, mode, analysis_time)
    euro = build_euro_features(euro_series, match.match_time, analysis_time)
    report: RuleReport = evaluate(match, mode, asian, euro, sup, analysis_time)

    hits = report.hits
    home_score = round(sum(h.strength for h in hits if h.direction == "home"), 3)
    away_score = round(sum(h.strength for h in hits if h.direction == "away"), 3)
    aligned = len(
        [
            h
            for h in hits
            if h.direction in ("home", "away") and h.category != "price"
        ]
    )
    net = home_score - away_score
    conflict = min(home_score, away_score)
    conflict_ratio = conflict / max(home_score, away_score, 0.1)

    direction = "observe"
    if abs(net) >= 1.0 and conflict_ratio < 0.6:
        direction = "home" if net > 0 else "away"

    # 价格检查：方向侧水位 vs 欧赔去水概率推出的公平水位
    price_check = None
    if direction != "observe" and euro.companies >= 2:
        if direction == "home":
            side_line = asian.latest_line
            side_water = asian.home_latest_water
            fair = _fair_water(
                side_line, euro.latest_home, euro.latest_draw, euro.latest_away
            )
        else:
            side_line = -asian.latest_line
            side_water = asian.away_latest_water
            fair = _fair_water(
                side_line, euro.latest_away, euro.latest_draw, euro.latest_home
            )
        if fair > 0:
            ratio = side_water / fair
            price_check = {
                "side_line": round(side_line, 4),
                "side_water": round(side_water, 4),
                "fair_water": round(fair, 4),
                "ratio": round(ratio, 3),
            }
            if ratio < 0.85:
                report.caps.append((40, "价格已干：方向侧水位显著低于公平水位"))
                report.conflicts.append(
                    f"价格已干：水位 {side_water:.2f} vs 公平 {fair:.2f}（{ratio:.0%}）"
                )
                hits.append(
                    RuleHit(
                        "PRICE_DRY",
                        "价格检查：水位已干",
                        direction,
                        0,
                        f"方向侧水位 {side_water:.2f}，按欧赔去水公平水位约 {fair:.2f}，追价无边际",
                        "price",
                    )
                )
            elif ratio < 0.95:
                report.caps.append((50, "价格偏干：方向侧水位低于公平水位"))
                hits.append(
                    RuleHit(
                        "PRICE_THIN",
                        "价格检查：水位偏干",
                        direction,
                        0,
                        f"方向侧水位 {side_water:.2f}，公平水位约 {fair:.2f}，边际偏薄",
                        "price",
                    )
                )
            elif ratio > 1.10:
                hits.append(
                    RuleHit(
                        "PRICE_VALUE",
                        "价格检查：水位有边际",
                        direction,
                        0,
                        f"方向侧水位 {side_water:.2f}，公平水位约 {fair:.2f}，价格有利",
                        "price",
                    )
                )
    report.data_quality["price_check"] = price_check

    confidence = 30 + 10 * abs(net) + 4 * aligned
    cap = 55
    for value, _reason in report.caps:
        cap = min(cap, value)
    if conflict_ratio >= 0.6 and max(home_score, away_score) > 0:
        cap = min(cap, 40)
        report.conflicts.append("方向性规则冲突，置信度封顶 40")
    if direction == "observe":
        confidence = min(confidence, 40)
    confidence = int(max(20, min(confidence, cap)))
    stake = "仅观察" if direction == "observe" or confidence < 45 else "小注"

    top = sorted(
        [h for h in hits if h.direction != "observe"],
        key=lambda h: h.strength,
        reverse=True,
    )[:3]
    if top:
        summary = "；".join(h.name for h in top)
    elif report.conflicts:
        summary = "规则冲突：" + "；".join(report.conflicts[:2])
    else:
        summary = "无有效方向信号，仅观察"

    result = AnalysisResult(
        match=match,
        mode=mode,
        analysis_time=analysis_time,
        direction=direction,
        confidence=confidence,
        confidence_label=_confidence_label(confidence),
        stake=stake,
        score_home=home_score,
        score_away=away_score,
        conflict_ratio=conflict_ratio,
        summary=summary,
        rule_hits=hits,
        conflicts=report.conflicts,
        data_quality=report.data_quality,
        features=features_snapshot(asian, euro),
        sup=sup,
    )
    result.invalidation = _make_invalidation(result)
    apply_action_filters(result)
    return result


def determine_mode(match: Match, analysis_time: datetime, requested: str = "auto") -> str:
    if requested in ("pre", "inplay"):
        return requested
    return "pre" if analysis_time < match.match_time else "inplay"
