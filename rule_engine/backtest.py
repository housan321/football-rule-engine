"""Backtest the three consensus-snapshot strategies.

Each match is evaluated for every requested strategy.  For a triggered signal
we settle two prices:

* ``consensus``: the average side water of the decision snapshot (the rule's
  own price);
* ``best``: the best side water among companies on the main line.

The document reports both, plus a 95% bootstrap CI on ROI.  We also settle the
opposite side as a control, matching the "opposite 方向明显为负" check.
"""

from __future__ import annotations

import csv
import json
import random
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .db import fetch_asian, fetch_backtest_matches
from .engine import analyze
from .models import Match
from .rules import DEFAULT_STRATEGY_KEYS, StrategySpec, resolve_strategies
from .settlement import settle_asian

BOOTSTRAP_ITERATIONS = 2000
BOOTSTRAP_SEED = 42
LARGE_LINE = 1.0


@dataclass
class BetRecord:
    fixture_id: int
    match_time: datetime
    league: str
    home: str
    away: str
    home_score: int
    away_score: int
    strategy: str
    direction: str
    bet: int
    prev_line: Optional[float]
    line: Optional[float]
    line_delta: Optional[float]
    home_water_delta: Optional[float]
    away_water_delta: Optional[float]
    rel_water: Optional[float]
    companies_prev: int
    companies_decision: int
    bet_line: Optional[float]
    water_consensus: Optional[float]
    water_best: Optional[float]
    best_company: Optional[str]
    outcome: str
    profit_consensus: float
    outcome_best: str
    profit_best: float
    outcome_opposite: str
    profit_opposite: float
    note: str = ""

    def to_row(self) -> dict:
        data = asdict(self)
        data["match_time"] = self.match_time.isoformat(sep=" ")
        return data


def _side_line(decision_line: float, direction: str) -> float:
    return decision_line if direction == "home" else -decision_line


def _settle(
    line: float,
    water: Optional[float],
    match: Match,
    direction: str,
):
    if water is None:
        return "no_price", 0.0
    settlement = settle_asian(
        line,
        water,
        int(match.home_score),
        int(match.away_score),
        direction,
    )
    return settlement.outcome, settlement.profit


def _record(
    match: Match,
    spec: StrategySpec,
    result,
) -> BetRecord:
    decision = result.features.get("asian", {}).get("decision") or {}
    base = dict(
        fixture_id=match.fixture_id,
        match_time=match.match_time,
        league=match.league or "",
        home=match.home,
        away=match.away,
        home_score=int(match.home_score or 0),
        away_score=int(match.away_score or 0),
        strategy=spec.key,
        direction=result.direction,
        prev_line=result.prev_line,
        line=result.line,
        line_delta=result.line_delta,
        home_water_delta=result.home_water_delta,
        away_water_delta=result.away_water_delta,
        rel_water=result.rel_water,
        companies_prev=result.companies_prev,
        companies_decision=result.companies_decision,
    )

    if not result.triggered or result.side_line is None:
        note = "" if result.companies_decision else "无可用共识快照"
        return BetRecord(
            **base,
            bet=0,
            bet_line=None,
            water_consensus=None,
            water_best=None,
            best_company=None,
            outcome="no_bet",
            profit_consensus=0.0,
            outcome_best="no_bet",
            profit_best=0.0,
            outcome_opposite="no_bet",
            profit_opposite=0.0,
            note=note,
        )

    bet_line = result.side_line
    consensus = result.consensus_water
    best = result.best_water if result.best_water is not None else consensus
    outcome, profit = _settle(bet_line, consensus, match, result.direction)
    outcome_best, profit_best = _settle(bet_line, best, match, result.direction)

    opposite = "away" if result.direction == "home" else "home"
    opposite_water = (
        decision.get("home_water")
        if opposite == "home"
        else decision.get("away_water")
    )
    outcome_opp, profit_opp = _settle(
        -bet_line, opposite_water, match, opposite
    )

    return BetRecord(
        **base,
        bet=1,
        bet_line=bet_line,
        water_consensus=consensus,
        water_best=best,
        best_company=result.best_company,
        outcome=outcome,
        profit_consensus=profit,
        outcome_best=outcome_best,
        profit_best=profit_best,
        outcome_opposite=outcome_opp,
        profit_opposite=profit_opp,
        note="",
    )


def run_backtest(
    conn,
    start_date: str,
    end_date: str,
    strategies: Optional[list[str]] = None,
    min_age_hours: int = 3,
    limit: int = 500,
    league: Optional[str] = None,
    fixture_ids: Optional[list[int]] = None,
    min_companies: int = 2,
    progress: Optional[Callable[[int, int], None]] = None,
) -> list[BetRecord]:
    specs = resolve_strategies(strategies or list(DEFAULT_STRATEGY_KEYS))
    matches = fetch_backtest_matches(
        conn,
        start_date,
        end_date,
        min_age_hours=min_age_hours,
        limit=limit,
        league_like=league,
        fixture_ids=fixture_ids,
    )
    records: list[BetRecord] = []

    for index, match in enumerate(matches, 1):
        try:
            # One query per match; snapshots discard anything after their
            # respective cut-offs, so the decision remains point-in-time.
            asian = fetch_asian(conn, match.fixture_id, match.match_time)
            for spec in specs:
                result = analyze(
                    match, spec.key, asian, min_companies=min_companies
                )
                records.append(_record(match, spec, result))
        except Exception as exc:  # noqa: BLE001 - one bad fixture must not stop the run
            for spec in specs:
                records.append(
                    BetRecord(
                        fixture_id=match.fixture_id,
                        match_time=match.match_time,
                        league=match.league or "",
                        home=match.home,
                        away=match.away,
                        home_score=int(match.home_score or 0),
                        away_score=int(match.away_score or 0),
                        strategy=spec.key,
                        direction="observe",
                        bet=0,
                        prev_line=None,
                        line=None,
                        line_delta=None,
                        home_water_delta=None,
                        away_water_delta=None,
                        rel_water=None,
                        companies_prev=0,
                        companies_decision=0,
                        bet_line=None,
                        water_consensus=None,
                        water_best=None,
                        best_company=None,
                        outcome="error",
                        profit_consensus=0.0,
                        outcome_best="error",
                        profit_best=0.0,
                        outcome_opposite="error",
                        profit_opposite=0.0,
                        note=str(exc)[:300],
                    )
                )
        if progress and index % 20 == 0:
            progress(index, len(matches))
    return records


def _wins_losses(records: list[BetRecord], prefix: str = "") -> tuple[int, int, int]:
    outcome_attr = f"outcome{prefix}"
    outcomes = Counter(getattr(r, outcome_attr) for r in records)
    wins = outcomes["win"] + outcomes["half_win"]
    losses = outcomes["loss"] + outcomes["half_loss"]
    pushes = outcomes["push"]
    return wins, losses, pushes


def _bootstrap_ci(
    profits: list[float],
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
) -> list[Optional[float]]:
    if not profits:
        return [None, None]
    rng = random.Random(seed)
    n = len(profits)
    means: list[float] = []
    for _ in range(iterations):
        total = sum(profits[rng.randrange(n)] for _ in range(n))
        means.append(total / n)
    means.sort()
    lo = means[max(0, int(0.025 * iterations))]
    hi = means[min(iterations - 1, int(0.975 * iterations))]
    return [round(lo, 4), round(hi, 4)]


def _group_stats(records: list[BetRecord]) -> dict:
    wins, losses, pushes = _wins_losses(records, "")
    opp_wins, opp_losses, _ = _wins_losses(records, "_opposite")
    n = len(records)
    profit_c = round(sum(r.profit_consensus for r in records), 4)
    profit_b = round(sum(r.profit_best for r in records), 4)
    profit_o = round(sum(r.profit_opposite for r in records), 4)
    water_c = [r.water_consensus for r in records if r.water_consensus is not None]
    water_b = [r.water_best for r in records if r.water_best is not None]
    profits_c = [r.profit_consensus for r in records]
    profits_b = [r.profit_best for r in records]
    profits_o = [r.profit_opposite for r in records]
    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "hit_rate": round(wins / (wins + losses), 4) if wins + losses else None,
        "profit_consensus": profit_c,
        "roi_consensus": round(profit_c / n, 4) if n else None,
        "profit_best": profit_b,
        "roi_best": round(profit_b / n, 4) if n else None,
        "ci95_consensus": _bootstrap_ci(profits_c),
        "ci95_best": _bootstrap_ci(profits_b),
        "avg_water": round(sum(water_c) / len(water_c), 4) if water_c else None,
        "avg_best_water": round(sum(water_b) / len(water_b), 4) if water_b else None,
        "opposite": {
            "n": n,
            "wins": opp_wins,
            "losses": opp_losses,
            "hit_rate": round(opp_wins / (opp_wins + opp_losses), 4)
            if opp_wins + opp_losses
            else None,
            "profit_consensus": profit_o,
            "roi_consensus": round(profit_o / n, 4) if n else None,
            "ci95_consensus": _bootstrap_ci(profits_o),
        },
    }


def _abs_line_bucket(side_line: Optional[float]) -> str:
    if side_line is None:
        return "unknown"
    return "abs<=1.0" if abs(side_line) <= LARGE_LINE + 1e-9 else "abs>1.0"


def _league_bucket(league: str) -> str:
    return league or "-"


def _season_label(moment: datetime) -> str:
    """European season: Aug-May belongs to year/year+1."""
    if moment.month >= 8:
        return f"{moment.year}-{str(moment.year + 1)[-2:]}"
    return f"{moment.year - 1}-{str(moment.year)[-2:]}"


def summarize(
    records: list[BetRecord],
    period: dict,
    strategies: Optional[list[str]] = None,
    min_age_hours: int = 3,
) -> dict:
    specs = resolve_strategies(strategies or list(DEFAULT_STRATEGY_KEYS))
    by_strategy: dict[str, dict] = {}
    for spec in specs:
        rows = [r for r in records if r.strategy == spec.key]
        bets = [r for r in rows if r.bet == 1]
        errors = [r for r in rows if r.outcome == "error"]

        by_league: dict[str, list[BetRecord]] = defaultdict(list)
        by_direction: dict[str, list[BetRecord]] = defaultdict(list)
        by_month: dict[str, list[BetRecord]] = defaultdict(list)
        by_season: dict[str, list[BetRecord]] = defaultdict(list)
        by_abs_line: dict[str, list[BetRecord]] = defaultdict(list)
        for record in bets:
            by_league[_league_bucket(record.league)].append(record)
            by_direction[record.direction].append(record)
            by_month[record.match_time.strftime("%Y-%m")].append(record)
            by_season[_season_label(record.match_time)].append(record)
            by_abs_line[_abs_line_bucket(record.bet_line)].append(record)

        stats = _group_stats(bets)
        stats.update(
            {
                "label": spec.label,
                "prev_window": spec.prev_label,
                "decision_window": spec.decision_label,
                "prev_minutes": spec.prev_minutes,
                "decision_minutes": spec.decision_minutes,
                "matches": len(rows),
                "bets": len(bets),
                "no_bets": len(rows) - len(bets) - len(errors),
                "errors": len(errors),
                "by_league": {
                    key: _group_stats(items)
                    for key, items in sorted(by_league.items())
                },
                "by_direction": {
                    key: _group_stats(items)
                    for key, items in sorted(by_direction.items())
                },
                "by_month": {
                    key: _group_stats(items)
                    for key, items in sorted(by_month.items())
                },
                "by_season": {
                    key: _group_stats(items)
                    for key, items in sorted(by_season.items())
                },
                "by_abs_line": {
                    key: _group_stats(items)
                    for key, items in sorted(by_abs_line.items())
                },
            }
        )
        by_strategy[spec.key] = stats

    return {
        "period": period,
        "min_age_hours": min_age_hours,
        "matches": len({r.fixture_id for r in records}),
        "strategies": [spec.key for spec in specs],
        "by_strategy": by_strategy,
        "notes": [
            "决策时点由策略决定：conflict T-10m=T-10m，conflict T-120m=T-120m，water T-60m=T-60m。",
            "快照只使用 cutoff <= 窗口时点的报价，避免未来函数。",
            "主盘口=公司出现次数最多的盘口；水位=该盘口上各公司的平均水位。",
            "conflict：|Δ盘口|>=0.25 且 |相对水位|>=0.06 且方向相反；water：|Δ盘口|<0.25 且 |相对水位|>=0.06。",
            "下注方向=水位方向；相对水位=Δ主水-Δ客水，正值朝客队、负值朝主队。",
            "consensus 价=决策快照的平均水位；best 价=主盘口上该方向最高水位。",
            "ROI=利润/注数（1 单位平注），95% CI 为 2000 次 bootstrap 百分位区间。",
            "opposite 为同一事件反向下注的对照；结算按 90 分钟比分，含半赢/半输。",
        ],
    }


def write_csv(records: list[BetRecord], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = [r.to_row() for r in records]
    with out.open("w", newline="", encoding="utf-8-sig") as handle:
        if not rows:
            handle.write("fixture_id\n")
            return out
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return out


def write_summary(summary: dict, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _pct(value: Optional[float]) -> str:
    return "-" if value is None else f"{value * 100:+.2f}%"


def _rate(value: Optional[float]) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _ci_span(ci: list[Optional[float]]) -> str:
    if not ci or ci[0] is None or ci[1] is None:
        return "-"
    return f"{ci[0] * 100:+.2f}% ~ {ci[1] * 100:+.2f}%"


def print_summary(summary: dict) -> None:
    print(
        f"Period: {summary['period']['from']} → {summary['period']['to']} | "
        f"matches: {summary['matches']}"
    )
    print(
        "\n策略                 注数   命中    ROI best (95% CI)                 "
        "ROI cons    平均水位  best水位"
    )
    print("-" * 118)
    for key, data in summary["by_strategy"].items():
        best_ci = _ci_span(data["ci95_best"])
        best_ci_text = f"({best_ci})" if best_ci != "-" else "-"
        print(
            f"{data['label']:<18} {data['bets']:>5}  {_rate(data['hit_rate']):>6}  "
            f"{_pct(data['roi_best']):>8} {best_ci_text:<29}  "
            f"{_pct(data['roi_consensus']):>8}  "
            f"{data['avg_water'] if data['avg_water'] is not None else '-':>7}  "
            f"{data['avg_best_water'] if data['avg_best_water'] is not None else '-':>7}"
        )
        opp = data["opposite"]
        print(
            f"  opposite: n={opp['n']:<5} hit={_rate(opp['hit_rate']):<7} "
            f"ROI={_pct(opp['roi_consensus']):<9} "
            f"CI={_ci_span(opp['ci95_consensus'])}"
        )
        for label, group in data["by_league"].items():
            if not group["n"]:
                continue
            print(
                f"    league {label:<18} n={group['n']:<4} "
                f"hit={_rate(group['hit_rate']):<7} "
                f"ROI best={_pct(group['roi_best']):<9} "
                f"cons={_pct(group['roi_consensus'])}"
            )
        for label, group in data["by_abs_line"].items():
            if not group["n"]:
                continue
            print(
                f"    {label:<20} n={group['n']:<4} "
                f"hit={_rate(group['hit_rate']):<7} "
                f"ROI cons={_pct(group['roi_consensus'])}"
            )
        for label, group in data["by_season"].items():
            if not group["n"]:
                continue
            print(
                f"    season {label:<13} n={group['n']:<4} "
                f"hit={_rate(group['hit_rate']):<7} "
                f"ROI best={_pct(group['roi_best']):<9} "
                f"cons={_pct(group['roi_consensus'])}"
            )
        print()

    print("Notes:")
    for note in summary["notes"]:
        print(f"  - {note}")
