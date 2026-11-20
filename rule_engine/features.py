"""Consensus snapshots for the multi-window Asian-handicap rules.

The rules in 《规则回测分析结果.docx》 do not look at a single company's latest
quote, nor at the full tick path.  They compare two *consensus snapshots*:

* one snapshot at the cut-off of the earlier window (e.g. T-30m);
* one snapshot at the decision window (e.g. T-10m).

Each snapshot is built as follows:

1. For every company take its latest quote whose ``change_time <= cutoff``.
2. The main line is the handicap quoted by the largest number of companies.
3. The home/away water is the average water of the companies sitting on that
   main line.

Only the net change between the two snapshots matters; whether a company moved
back and forth inside the window is deliberately ignored.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from statistics import median
from typing import Optional

from .models import AsianRow, CompanySeries

# A "consensus" needs at least two companies on the same main line.  The
# document's sample effectively used 6-8; this floor only guards against
# degenerate one-company snapshots.
MIN_SNAPSHOT_COMPANIES = 2
EPS = 1e-9


@dataclass(frozen=True)
class SnapshotQuote:
    company_id: int
    company_name: str
    change_time: datetime
    line: float
    home_water: float
    away_water: float


@dataclass
class ConsensusSnapshot:
    cutoff: datetime
    line: float
    home_water: float
    away_water: float
    companies: int  # companies quoting the main line
    total_companies: int  # companies with any quote at/before the cutoff
    quotes: list[SnapshotQuote] = field(default_factory=list)
    latest_change: Optional[datetime] = None

    @property
    def valid(self) -> bool:
        return self.companies >= MIN_SNAPSHOT_COMPANIES

    def to_dict(self) -> dict:
        return {
            "cutoff": self.cutoff.isoformat(sep=" "),
            "line": self.line,
            "home_water": self.home_water,
            "away_water": self.away_water,
            "companies": self.companies,
            "total_companies": self.total_companies,
            "latest_change": self.latest_change.isoformat(sep=" ")
            if self.latest_change
            else None,
            "quotes": [
                {
                    "company_id": q.company_id,
                    "company": q.company_name,
                    "change_time": q.change_time.isoformat(sep=" "),
                    "line": q.line,
                    "home_water": q.home_water,
                    "away_water": q.away_water,
                }
                for q in self.quotes
            ],
        }


@dataclass
class SnapshotDelta:
    prev: ConsensusSnapshot
    decision: ConsensusSnapshot
    d_line: float
    d_home_water: float
    d_away_water: float
    rel_water: float
    line_direction: str  # home / away / flat
    water_direction: str  # home / away / flat
    conflict: bool

    def to_dict(self) -> dict:
        return {
            "prev": self.prev.to_dict(),
            "decision": self.decision.to_dict(),
            "d_line": self.d_line,
            "d_home_water": self.d_home_water,
            "d_away_water": self.d_away_water,
            "rel_water": self.rel_water,
            "line_direction": self.line_direction,
            "water_direction": self.water_direction,
            "conflict": self.conflict,
        }


def _latest_quote_at_or_before(
    series: CompanySeries, cutoff: datetime
) -> Optional[AsianRow]:
    best: Optional[AsianRow] = None
    for row in series.rows:
        if row.change_time > cutoff:
            continue
        if best is None or row.change_time >= best.change_time:
            best = row
    return best


def _modal_line(lines: list[float]) -> float:
    """Most-quoted handicap; ties go to the line closest to the median."""
    rounded = [round(x, 4) for x in lines]
    counts = Counter(rounded)
    top = max(counts.values())
    candidates = sorted(line for line, n in counts.items() if n == top)
    if len(candidates) == 1:
        return candidates[0]
    med = float(median(rounded))
    return min(candidates, key=lambda x: (abs(x - med), x))


def build_consensus_snapshot(
    series_list: list[CompanySeries], cutoff: datetime
) -> Optional[ConsensusSnapshot]:
    """Build the company consensus visible at ``cutoff`` (inclusive)."""
    quotes: list[SnapshotQuote] = []
    for series in series_list:
        row = _latest_quote_at_or_before(series, cutoff)
        if row is None:
            continue
        quotes.append(
            SnapshotQuote(
                company_id=series.company_id,
                company_name=series.company_name,
                change_time=row.change_time,
                line=round(row.handicap, 4),
                home_water=round(row.home_water, 4),
                away_water=round(row.away_water, 4),
            )
        )
    if not quotes:
        return None

    line = _modal_line([q.line for q in quotes])
    on_line = [q for q in quotes if abs(q.line - line) <= EPS]
    home_water = round(sum(q.home_water for q in on_line) / len(on_line), 4)
    away_water = round(sum(q.away_water for q in on_line) / len(on_line), 4)

    return ConsensusSnapshot(
        cutoff=cutoff,
        line=line,
        home_water=home_water,
        away_water=away_water,
        companies=len(on_line),
        total_companies=len(quotes),
        quotes=on_line,
        latest_change=max(q.change_time for q in quotes),
    )


def build_snapshot_delta(
    series_list: list[CompanySeries],
    prev_cutoff: datetime,
    decision_cutoff: datetime,
) -> Optional[SnapshotDelta]:
    """Compare the consensus before and at the decision window."""
    prev = build_consensus_snapshot(series_list, prev_cutoff)
    decision = build_consensus_snapshot(series_list, decision_cutoff)
    if prev is None or decision is None:
        return None

    d_line = round(decision.line - prev.line, 4)
    d_home = round(decision.home_water - prev.home_water, 4)
    d_away = round(decision.away_water - prev.away_water, 4)
    rel_water = round(d_home - d_away, 4)

    # A more negative handicap means the home side gives more: "line moves to
    # home".  A positive relative water change means home water rose / away
    # water fell: "water moves to away".
    if d_line < -EPS:
        line_direction = "home"
    elif d_line > EPS:
        line_direction = "away"
    else:
        line_direction = "flat"

    if rel_water > EPS:
        water_direction = "away"
    elif rel_water < -EPS:
        water_direction = "home"
    else:
        water_direction = "flat"

    conflict = (
        line_direction in ("home", "away")
        and water_direction in ("home", "away")
        and line_direction != water_direction
    )

    return SnapshotDelta(
        prev=prev,
        decision=decision,
        d_line=d_line,
        d_home_water=d_home,
        d_away_water=d_away,
        rel_water=rel_water,
        line_direction=line_direction,
        water_direction=water_direction,
        conflict=conflict,
    )


def best_water(
    snapshot: ConsensusSnapshot, direction: str
) -> tuple[Optional[float], Optional[str]]:
    """Best available side water among companies on the main line."""
    if direction not in ("home", "away"):
        return None, None
    on_line = [q for q in snapshot.quotes if abs(q.line - snapshot.line) <= EPS]
    if not on_line:
        return None, None
    if direction == "home":
        quote = max(on_line, key=lambda q: q.home_water)
        return quote.home_water, quote.company_name
    quote = max(on_line, key=lambda q: q.away_water)
    return quote.away_water, quote.company_name


def side_water(snapshot: ConsensusSnapshot, direction: str) -> Optional[float]:
    if direction == "home":
        return snapshot.home_water
    if direction == "away":
        return snapshot.away_water
    return None
