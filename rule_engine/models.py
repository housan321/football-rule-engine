from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Match:
    fixture_id: int
    match_time: datetime
    status_id: Optional[int] = None
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    competition_id: Optional[int] = None
    league: Optional[str] = None
    home: str = "主队"
    away: str = "客队"
    home_id: Optional[int] = None
    away_id: Optional[int] = None


@dataclass
class AsianRow:
    company_id: int
    company_name: str
    change_time: datetime
    home_water: float
    handicap: float
    away_water: float
    score: Optional[str] = None
    phase: str = "prematch"


@dataclass
class EuroRow:
    company_id: int
    company_name: str
    change_time: datetime
    home: float
    draw: float
    away: float


@dataclass
class CompanySeries:
    company_id: int
    company_name: str
    rows: list[AsianRow] = field(default_factory=list)


@dataclass
class EuroSeries:
    company_id: int
    company_name: str
    rows: list[EuroRow] = field(default_factory=list)


@dataclass
class SupInfo:
    available: bool
    sup: Optional[float] = None
    home_name: Optional[str] = None
    away_name: Optional[str] = None
    detail: str = ""
