from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median
from typing import Optional

from .db import CORE_ASIAN
from .models import CompanySeries, EuroSeries, Match

EARLY_WINDOW = timedelta(hours=3)
N1_STALE = timedelta(hours=12)


@dataclass
class CompanyView:
    company_id: int
    company_name: str
    n_changes: int
    n_line_changes: int
    first_time: datetime
    last_time: datetime
    open_line: float
    latest_line: float
    open_home_water: float
    latest_home_water: float
    open_away_water: float
    latest_away_water: float
    line_delta: float
    home_water_delta: float
    away_water_delta: float
    early_line_changes: int
    late_line_changes: int
    excluded: bool = False
    exclude_reason: str = ""


@dataclass
class AsianFeatures:
    views: list[CompanyView] = field(default_factory=list)
    core_views: list[CompanyView] = field(default_factory=list)
    coverage: int = 0
    latest_time: Optional[datetime] = None
    data_age_minutes: Optional[float] = None
    median_line_delta: float = 0.0
    median_home_water_delta: float = 0.0
    median_away_water_delta: float = 0.0
    line_agreement: float = 0.0
    water_agreement: float = 0.0
    line_dispersion: float = 0.0
    latest_line: float = 0.0
    upper_side: str = "none"
    upper_sign: int = 0
    deepening: float = 0.0
    retreat: float = 0.0
    upper_water_delta: float = 0.0
    lower_water_delta: float = 0.0
    upper_latest_water: float = 0.0
    lower_latest_water: float = 0.0
    home_latest_water: float = 0.0
    away_latest_water: float = 0.0
    water_dispersion: float = 0.0
    early_change_ratio: float = 0.0
    volatility: float = 0.0
    consensus_direction: str = "observe"
    consensus_reason: str = ""
    low_coverage: bool = False
    stale: bool = False


@dataclass
class EuroFeatures:
    companies: int = 0
    latest_time: Optional[datetime] = None
    data_age_minutes: Optional[float] = None
    open_home: float = 0.0
    open_draw: float = 0.0
    open_away: float = 0.0
    latest_home: float = 0.0
    latest_draw: float = 0.0
    latest_away: float = 0.0
    home_prob_delta: float = 0.0
    away_prob_delta: float = 0.0
    overround: float = 0.0
    favorite_side: str = "none"
    movement: str = "observe"


def _pct(value: float) -> float:
    return round(value * 100, 2)


def build_company_views(
    series: list[CompanySeries], match_time: datetime
) -> list[CompanyView]:
    views: list[CompanyView] = []
    for item in series:
        rows = sorted(item.rows, key=lambda r: r.change_time)
        if not rows:
            continue
        first, last = rows[0], rows[-1]
        excluded = False
        reason = ""
        if len(rows) == 1 and (match_time - first.change_time) > N1_STALE:
            excluded = True
            reason = "n=1 且距开赛超过 12 小时，仅作初盘参考"

        line_changes = 0
        early_lines = 0
        late_lines = 0
        prev_line = first.handicap
        for row in rows[1:]:
            if abs(row.handicap - prev_line) > 1e-9:
                line_changes += 1
                if row.change_time <= match_time - EARLY_WINDOW:
                    early_lines += 1
                else:
                    late_lines += 1
                prev_line = row.handicap

        views.append(
            CompanyView(
                company_id=item.company_id,
                company_name=item.company_name,
                n_changes=len(rows),
                n_line_changes=line_changes,
                first_time=first.change_time,
                last_time=last.change_time,
                open_line=first.handicap,
                latest_line=last.handicap,
                open_home_water=first.home_water,
                latest_home_water=last.home_water,
                open_away_water=first.away_water,
                latest_away_water=last.away_water,
                line_delta=last.handicap - first.handicap,
                home_water_delta=last.home_water - first.home_water,
                away_water_delta=last.away_water - first.away_water,
                early_line_changes=early_lines,
                late_line_changes=late_lines,
                excluded=excluded,
                exclude_reason=reason,
            )
        )
    return views


def _median(values: list[float]) -> float:
    return float(median(values)) if values else 0.0


def build_asian_features(
    views: list[CompanyView],
    mode: str,
    analysis_time: datetime,
) -> AsianFeatures:
    core = [
        v
        for v in views
        if v.company_id in CORE_ASIAN and not v.excluded
    ]
    usable = [v for v in views if not v.excluded]
    latest_time = max((v.last_time for v in usable), default=None)
    age = (
        (analysis_time - latest_time).total_seconds() / 60 if latest_time else None
    )

    if not core:
        return AsianFeatures(
            views=views,
            coverage=0,
            latest_time=latest_time,
            data_age_minutes=age,
            low_coverage=True,
            stale=bool(age is not None and age > (15 if mode == "inplay" else 360)),
            consensus_reason="无可用核心公司数据",
        )

    line_delta = _median([v.line_delta for v in core])
    home_water_delta = _median([v.home_water_delta for v in core])
    away_water_delta = _median([v.away_water_delta for v in core])
    latest_line = _median([v.latest_line for v in core])

    if latest_line < -1e-9:
        upper_side, upper_sign = "home", 1
    elif latest_line > 1e-9:
        upper_side, upper_sign = "away", -1
    else:
        upper_side, upper_sign = "none", 0

    deepening = -upper_sign * line_delta if upper_sign else 0.0
    retreat = -deepening
    upper_water_delta = _median(
        [v.home_water_delta if upper_side == "home" else v.away_water_delta for v in core]
    ) if upper_sign else 0.0
    lower_water_delta = _median(
        [v.away_water_delta if upper_side == "home" else v.home_water_delta for v in core]
    ) if upper_sign else 0.0
    upper_latest_water = _median(
        [v.latest_home_water if upper_side == "home" else v.latest_away_water for v in core]
    ) if upper_sign else 0.0
    lower_latest_water = _median(
        [v.latest_away_water if upper_side == "home" else v.latest_home_water for v in core]
    ) if upper_sign else 0.0
    home_latest_water = _median([v.latest_home_water for v in core])
    away_latest_water = _median([v.latest_away_water for v in core])
    water_dispersion = round(
        max(v.latest_home_water for v in core) - min(v.latest_home_water for v in core), 4
    )

    def agreement(values: list[float], target: float) -> float:
        if not values or abs(target) < 1e-9:
            return 0.0
        sign = 1 if target > 0 else -1
        hits = sum(1 for x in values if (x > 0) - (x < 0) == sign)
        return round(hits / len(values), 3)

    total_line_changes = sum(v.n_line_changes for v in core)
    early_changes = sum(v.early_line_changes for v in core)
    early_ratio = early_changes / total_line_changes if total_line_changes else 0.0

    score_home = 0.0
    score_away = 0.0
    if line_delta > 0.05:
        score_home += 1.0
    elif line_delta < -0.05:
        score_away += 1.0
    if home_water_delta < -0.02:
        score_home += 1.0
    elif home_water_delta > 0.02:
        score_away += 1.0
    if score_home > score_away:
        consensus, reason = "home", "盘口/水位净向主队"
    elif score_away > score_home:
        consensus, reason = "away", "盘口/水位净向客队"
    else:
        consensus, reason = "observe", "盘口与水位方向不一致或变动不足"

    return AsianFeatures(
        views=views,
        core_views=core,
        coverage=len(core),
        latest_time=latest_time,
        data_age_minutes=age,
        median_line_delta=round(line_delta, 4),
        median_home_water_delta=round(home_water_delta, 4),
        median_away_water_delta=round(away_water_delta, 4),
        line_agreement=agreement([v.line_delta for v in core], line_delta),
        water_agreement=agreement([v.home_water_delta for v in core], home_water_delta),
        line_dispersion=round(
            max(v.latest_line for v in core) - min(v.latest_line for v in core), 4
        ),
        latest_line=round(latest_line, 4),
        upper_side=upper_side,
        upper_sign=upper_sign,
        deepening=round(deepening, 4),
        retreat=round(retreat, 4),
        upper_water_delta=round(upper_water_delta, 4),
        lower_water_delta=round(lower_water_delta, 4),
        upper_latest_water=round(upper_latest_water, 4),
        lower_latest_water=round(lower_latest_water, 4),
        home_latest_water=round(home_latest_water, 4),
        away_latest_water=round(away_latest_water, 4),
        water_dispersion=water_dispersion,
        early_change_ratio=round(early_ratio, 3),
        volatility=round(total_line_changes / max(1, len(core)), 2),
        consensus_direction=consensus,
        consensus_reason=reason,
        low_coverage=len(core) < 2,
        stale=bool(age is not None and age > (15 if mode == "inplay" else 360)),
    )


def _implied(home: float, draw: float, away: float) -> tuple[float, float, float, float]:
    raw = [1.0 / home, 1.0 / draw, 1.0 / away]
    total = sum(raw)
    return raw[0] / total, raw[1] / total, raw[2] / total, total - 1.0


def build_euro_features(
    series: list[EuroSeries], match_time: datetime, analysis_time: datetime
) -> EuroFeatures:
    usable = [s for s in series if s.rows]
    if not usable:
        return EuroFeatures(companies=0)

    open_probs: list[tuple[float, float, float]] = []
    latest_probs: list[tuple[float, float, float]] = []
    overrounds: list[float] = []
    latest_times: list[datetime] = []

    for item in usable:
        rows = sorted(item.rows, key=lambda r: r.change_time)
        first, last = rows[0], rows[-1]
        oh, od, oa, _ = _implied(first.home, first.draw, first.away)
        lh, ld, la, over = _implied(last.home, last.draw, last.away)
        open_probs.append((oh, od, oa))
        latest_probs.append((lh, ld, la))
        overrounds.append(over)
        latest_times.append(last.change_time)

    def avg(idx: int, bucket: list[tuple[float, float, float]]) -> float:
        return sum(x[idx] for x in bucket) / len(bucket)

    open_home, open_draw, open_away = (
        avg(0, open_probs),
        avg(1, open_probs),
        avg(2, open_probs),
    )
    latest_home, latest_draw, latest_away = (
        avg(0, latest_probs),
        avg(1, latest_probs),
        avg(2, latest_probs),
    )
    home_delta = latest_home - open_home
    away_delta = latest_away - open_away
    if home_delta > 0.01 and home_delta >= away_delta:
        movement = "home"
    elif away_delta > 0.01 and away_delta > home_delta:
        movement = "away"
    else:
        movement = "observe"

    return EuroFeatures(
        companies=len(usable),
        latest_time=max(latest_times),
        data_age_minutes=(analysis_time - max(latest_times)).total_seconds() / 60,
        open_home=round(open_home, 4),
        open_draw=round(open_draw, 4),
        open_away=round(open_away, 4),
        latest_home=round(latest_home, 4),
        latest_draw=round(latest_draw, 4),
        latest_away=round(latest_away, 4),
        home_prob_delta=round(home_delta, 4),
        away_prob_delta=round(away_delta, 4),
        overround=round(sum(overrounds) / len(overrounds), 4),
        favorite_side="home" if latest_home >= latest_away else "away",
        movement=movement,
    )


def features_snapshot(asian: AsianFeatures, euro: EuroFeatures) -> dict:
    return {
        "asian": {
            "coverage": asian.coverage,
            "latest_time": asian.latest_time.isoformat(sep=" ") if asian.latest_time else None,
            "data_age_minutes": round(asian.data_age_minutes, 1)
            if asian.data_age_minutes is not None
            else None,
            "latest_line": asian.latest_line,
            "upper_side": asian.upper_side,
            "median_line_delta": asian.median_line_delta,
            "median_home_water_delta": asian.median_home_water_delta,
            "deepening": asian.deepening,
            "retreat": asian.retreat,
            "upper_water_delta": asian.upper_water_delta,
            "upper_latest_water": asian.upper_latest_water,
            "lower_latest_water": asian.lower_latest_water,
            "home_latest_water": asian.home_latest_water,
            "away_latest_water": asian.away_latest_water,
            "water_dispersion": asian.water_dispersion,
            "line_agreement": asian.line_agreement,
            "water_agreement": asian.water_agreement,
            "line_dispersion": asian.line_dispersion,
            "early_change_ratio": asian.early_change_ratio,
            "volatility": asian.volatility,
            "consensus_direction": asian.consensus_direction,
            "consensus_reason": asian.consensus_reason,
            "low_coverage": asian.low_coverage,
            "stale": asian.stale,
            "companies": [
                {
                    "id": v.company_id,
                    "name": v.company_name,
                    "open_line": v.open_line,
                    "latest_line": v.latest_line,
                    "line_delta": round(v.line_delta, 4),
                    "open_home_water": v.open_home_water,
                    "latest_home_water": v.latest_home_water,
                    "home_water_delta": round(v.home_water_delta, 4),
                    "changes": v.n_changes,
                    "line_changes": v.n_line_changes,
                    "early_line_changes": v.early_line_changes,
                    "excluded": v.excluded,
                    "exclude_reason": v.exclude_reason,
                }
                for v in asian.views
            ],
        },
        "euro": {
            "companies": euro.companies,
            "latest_time": euro.latest_time.isoformat(sep=" ") if euro.latest_time else None,
            "data_age_minutes": round(euro.data_age_minutes, 1)
            if euro.data_age_minutes is not None
            else None,
            "open_probs_pct": [
                _pct(euro.open_home),
                _pct(euro.open_draw),
                _pct(euro.open_away),
            ],
            "latest_probs_pct": [
                _pct(euro.latest_home),
                _pct(euro.latest_draw),
                _pct(euro.latest_away),
            ],
            "home_prob_delta_pct": _pct(euro.home_prob_delta),
            "away_prob_delta_pct": _pct(euro.away_prob_delta),
            "overround_pct": _pct(euro.overround),
            "favorite_side": euro.favorite_side,
            "movement": euro.movement,
        },
    }
