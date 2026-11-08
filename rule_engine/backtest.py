from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from .db import (
    fetch_asian,
    fetch_backtest_matches,
    fetch_eu,
    fetch_sup_asof,
)
from .engine import analyze
from .models import Match
from .settlement import settle_asian

PREFERRED_COMPANIES = (2, 3, 7, 11)


@dataclass
class BetRecord:
    fixture_id: int
    match_time: datetime
    league: str
    home: str
    away: str
    home_score: int
    away_score: int
    direction: str
    confidence: int
    confidence_label: str
    bet: int
    engine_line: float
    bet_line: Optional[float]
    side_line: Optional[float]
    water: Optional[float]
    company: Optional[str]
    profit: float
    outcome: str
    rule_codes: str
    conflicts: str
    closing_line: Optional[float]
    closing_water: Optional[float]
    clv_favorable: Optional[bool]
    error: str = ""

    def to_row(self) -> dict:
        data = asdict(self)
        data["match_time"] = self.match_time.isoformat(sep=" ")
        return data


def _last_row(series, at: datetime):
    rows = [r for r in series.rows if r.change_time <= at]
    return rows[-1] if rows else None


def select_quote(series_list, direction: str, at: datetime) -> Optional[dict]:
    if direction not in ("home", "away"):
        return None
    for company_id in PREFERRED_COMPANIES:
        series = next((s for s in series_list if s.company_id == company_id), None)
        if not series:
            continue
        row = _last_row(series, at)
        if not row:
            continue
        return {
            "company_id": company_id,
            "company": series.company_name,
            "line": row.handicap,
            "water": row.home_water if direction == "home" else row.away_water,
        }
    return None


def _clv_favorable(
    direction: str,
    bet_line: float,
    bet_water: float,
    close_line: Optional[float],
    close_water: Optional[float],
) -> Optional[bool]:
    if close_line is None or close_water is None:
        return None
    if direction == "home":
        return (close_line < bet_line - 1e-9) or (close_water < bet_water - 1e-9)
    return (close_line > bet_line + 1e-9) or (close_water < bet_water - 1e-9)


def run_backtest(
    conn,
    start_date: str,
    end_date: str,
    decision_minutes: int = 60,
    min_age_hours: int = 3,
    limit: int = 500,
    league: Optional[str] = None,
    fixture_ids: Optional[list[int]] = None,
    min_confidence: int = 45,
    progress: Optional[Callable[[int, int], None]] = None,
) -> list[BetRecord]:
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
        analysis_time = match.match_time - timedelta(minutes=decision_minutes)
        try:
            asian = fetch_asian(conn, match.fixture_id, "pre", analysis_time)
            euro = fetch_eu(conn, match.fixture_id, analysis_time)
            sup = fetch_sup_asof(conn, match, analysis_time)
            result = analyze(match, "pre", analysis_time, asian, euro, sup)

            engine_line = float(result.features["asian"]["latest_line"])
            rule_codes = "|".join(
                sorted(
                    {
                        h.code
                        for h in result.rule_hits
                        if h.direction in ("home", "away") and h.category != "price"
                    }
                )
            )
            conflicts = " | ".join(result.conflicts[:3])
            bet = (
                result.stake == "小注"
                and result.confidence >= min_confidence
                and result.direction in ("home", "away")
            )
            quote = select_quote(asian, result.direction, analysis_time) if bet else None

            if bet and quote:
                settlement = settle_asian(
                    quote["line"],
                    quote["water"],
                    int(match.home_score),
                    int(match.away_score),
                    result.direction,
                )
                # The decision series is truncated at T-N; fetch a separate
                # series up to kickoff so CLV can see closing prices.
                asian_close = fetch_asian(
                    conn, match.fixture_id, "pre", match.match_time
                )
                close_same_company = None
                series = next(
                    (s for s in asian_close if s.company_id == quote["company_id"]),
                    None,
                )
                if series:
                    row = _last_row(
                        series, match.match_time - timedelta(seconds=1)
                    )
                    if row:
                        close_same_company = {
                            "line": row.handicap,
                            "water": row.home_water
                            if result.direction == "home"
                            else row.away_water,
                        }
                clv = _clv_favorable(
                    result.direction,
                    quote["line"],
                    quote["water"],
                    close_same_company["line"] if close_same_company else None,
                    close_same_company["water"] if close_same_company else None,
                )
                side_line = (
                    quote["line"]
                    if result.direction == "home"
                    else -quote["line"]
                )
                record = BetRecord(
                    fixture_id=match.fixture_id,
                    match_time=match.match_time,
                    league=match.league or "",
                    home=match.home,
                    away=match.away,
                    home_score=int(match.home_score),
                    away_score=int(match.away_score),
                    direction=result.direction,
                    confidence=result.confidence,
                    confidence_label=result.confidence_label,
                    bet=1,
                    engine_line=engine_line,
                    bet_line=quote["line"],
                    side_line=side_line,
                    water=quote["water"],
                    company=quote["company"],
                    profit=settlement.profit,
                    outcome=settlement.outcome,
                    rule_codes=rule_codes,
                    conflicts=conflicts,
                    closing_line=close_same_company["line"] if close_same_company else None,
                    closing_water=close_same_company["water"] if close_same_company else None,
                    clv_favorable=clv,
                )
            else:
                record = BetRecord(
                    fixture_id=match.fixture_id,
                    match_time=match.match_time,
                    league=match.league or "",
                    home=match.home,
                    away=match.away,
                    home_score=int(match.home_score),
                    away_score=int(match.away_score),
                    direction=result.direction,
                    confidence=result.confidence,
                    confidence_label=result.confidence_label,
                    bet=0,
                    engine_line=engine_line,
                    bet_line=None,
                    side_line=None,
                    water=None,
                    company=None,
                    profit=0.0,
                    outcome="no_quote" if bet else "no_bet",
                    rule_codes=rule_codes,
                    conflicts=conflicts,
                    closing_line=None,
                    closing_water=None,
                    clv_favorable=None,
                )
            records.append(record)
        except Exception as exc:  # noqa: BLE001 - one bad fixture must not stop the run
            records.append(
                BetRecord(
                    fixture_id=match.fixture_id,
                    match_time=match.match_time,
                    league=match.league or "",
                    home=match.home,
                    away=match.away,
                    home_score=int(match.home_score or 0),
                    away_score=int(match.away_score or 0),
                    direction="observe",
                    confidence=0,
                    confidence_label="",
                    bet=0,
                    engine_line=0.0,
                    bet_line=None,
                    side_line=None,
                    water=None,
                    company=None,
                    profit=0.0,
                    outcome="error",
                    rule_codes="",
                    conflicts="",
                    closing_line=None,
                    closing_water=None,
                    clv_favorable=None,
                    error=str(exc)[:300],
                )
            )
        if progress and index % 20 == 0:
            progress(index, len(matches))
    return records


def _group_stats(records: list[BetRecord]) -> dict:
    outcomes = Counter(r.outcome for r in records)
    wins = outcomes["win"] + outcomes["half_win"]
    losses = outcomes["loss"] + outcomes["half_loss"]
    pushes = outcomes["push"]
    profit = sum(r.profit for r in records)
    n = len(records)
    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "hit_rate": round(wins / (wins + losses), 4) if wins + losses else None,
        "profit": round(profit, 4),
        "roi": round(profit / n, 4) if n else None,
    }


def _confidence_bucket(confidence: int) -> str:
    if confidence < 45:
        return "40-44"
    if confidence < 50:
        return "45-49"
    return "50-55"


def _line_bucket(side_line: Optional[float]) -> str:
    if side_line is None:
        return "unknown"
    if side_line <= -1.0:
        return "让一球或更深"
    if side_line < 0:
        return "让平半到半一"
    if abs(side_line) < 1e-9:
        return "平手"
    if side_line < 1.0:
        return "受让平半到半一"
    return "受让一球或更深"


def summarize(
    records: list[BetRecord],
    period: dict,
    decision_minutes: int,
    min_age_hours: int,
    min_confidence: int = 45,
) -> dict:
    bets = [r for r in records if r.bet == 1]
    outcomes = Counter(r.outcome for r in bets)
    wins = outcomes["win"] + outcomes["half_win"]
    losses = outcomes["loss"] + outcomes["half_loss"]
    pushes = outcomes["push"]
    staked = len(bets)
    profit = round(sum(r.profit for r in bets), 4)
    clv_values = [r.clv_favorable for r in bets if r.clv_favorable is not None]

    by_confidence: dict[str, list[BetRecord]] = defaultdict(list)
    by_rule: dict[str, list[BetRecord]] = defaultdict(list)
    by_league: dict[str, list[BetRecord]] = defaultdict(list)
    by_line: dict[str, list[BetRecord]] = defaultdict(list)
    by_direction: dict[str, list[BetRecord]] = defaultdict(list)
    by_month: dict[str, list[BetRecord]] = defaultdict(list)

    for record in bets:
        by_confidence[_confidence_bucket(record.confidence)].append(record)
        for code in filter(None, record.rule_codes.split("|")):
            by_rule[code].append(record)
        by_league[record.league or "-"].append(record)
        by_line[_line_bucket(record.side_line)].append(record)
        by_direction[record.direction].append(record)
        by_month[record.match_time.strftime("%Y-%m")].append(record)

    def group(d: dict[str, list[BetRecord]]) -> dict:
        return {key: _group_stats(items) for key, items in sorted(d.items())}

    errors = [r for r in records if r.outcome == "error"]
    notes = [
        "决策时点 = 开赛前 decision_minutes；只用该时点之前的赔率，避免未来函数。",
        "结算使用 match_list_all 最终比分，按亚盘含半赢/半输规则。",
        "Phase 1 无基本面，置信度最高 55；小注为 1 单位平注。",
        "全样本回测不等于样本外验证；请按月/按联赛观察稳定性。",
        "未计算交易量/滑点；未处理加时赛（按 90 分钟比分假设）。",
    ]

    return {
        "period": period,
        "decision_minutes": decision_minutes,
        "min_age_hours": min_age_hours,
        "min_confidence": min_confidence,
        "matches": len(records),
        "bets": len(bets),
        "no_bets": len(records) - len(bets) - len(errors),
        "errors": len(errors),
        "staked": staked,
        "profit": profit,
        "roi": round(profit / staked, 4) if staked else None,
        "hit_rate": round(wins / (wins + losses), 4) if wins + losses else None,
        "outcomes": {
            "win": outcomes["win"],
            "half_win": outcomes["half_win"],
            "push": pushes,
            "half_loss": outcomes["half_loss"],
            "loss": outcomes["loss"],
        },
        "clv_samples": len(clv_values),
        "clv_favorable_rate": round(sum(1 for x in clv_values if x) / len(clv_values), 4)
        if clv_values
        else None,
        "by_confidence": group(by_confidence),
        "by_rule": group(by_rule),
        "by_league": group(by_league),
        "by_line": group(by_line),
        "by_direction": group(by_direction),
        "by_month": group(by_month),
        "notes": notes,
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
    return "-" if value is None else f"{value * 100:+.1f}%"


def _rate(value: Optional[float]) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def print_summary(summary: dict) -> None:
    print(
        f"Period: {summary['period']['from']} → {summary['period']['to']} | "
        f"decision = kickoff - {summary['decision_minutes']} min"
    )
    print(
        f"Matches: {summary['matches']} | Bets: {summary['bets']} | "
        f"No bet: {summary['no_bets']} | Errors: {summary['errors']}"
    )
    o = summary["outcomes"]
    print(
        f"Record: W {o['win']} | HW {o['half_win']} | P {o['push']} | "
        f"HL {o['half_loss']} | L {o['loss']}"
    )
    print(
        f"Hit rate: {_rate(summary['hit_rate'])} | Staked: {summary['staked']:.1f} | "
        f"Profit: {summary['profit']:+.2f} | ROI: {_pct(summary['roi'])} | "
        f"CLV favorable: {_rate(summary['clv_favorable_rate'])}"
    )

    def table(title: str, data: dict, limit: int = 8) -> None:
        print(f"\n{title}")
        for key, stats in list(data.items())[:limit]:
            print(
                f"  {key:<16} n={stats['n']:<4} "
                f"hit={_rate(stats['hit_rate']):<7} "
                f"roi={_pct(stats['roi']):<8} profit={stats['profit']:+.2f}"
            )

    table("By confidence", summary["by_confidence"])
    table("By rule", summary["by_rule"], limit=12)
    table("By league", summary["by_league"])
    table("By line", summary["by_line"])
    table("By month", summary["by_month"])
    print("\nNotes:")
    for note in summary["notes"]:
        print(f"  - {note}")
