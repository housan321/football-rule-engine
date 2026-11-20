from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from .config import DBConfig
from .models import (
    AsianRow,
    CompanySeries,
    EuroRow,
    EuroSeries,
    Match,
    SupInfo,
)

# The eight Asian companies used by 《规则回测分析结果.docx》:
# Bet365(2), 皇冠(3), 明陞(6), 澳门(7), 易胜博(10), Interwetten(13),
# 平博(22), 香港马会(23).  The first four are the dense ones.
ASIAN_WHITELIST = (2, 3, 6, 7, 10, 13, 22, 23)
EU_WHITELIST = (2, 3, 5, 7, 9, 22)
CORE_ASIAN = (2, 3, 6, 10)


def connect(cfg: DBConfig):
    try:
        import pymysql
        from pymysql.cursors import DictCursor
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "pymysql is required. Install with: pip install -r requirements.txt"
        ) from exc
    return pymysql.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        database=cfg.database,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=True,
    )


def fetch_match(conn, fixture_id: int) -> Optional[Match]:
    sql = """
SELECT b.fixture_id, b.match_time, b.status_id, b.home_score, b.away_score,
       b.competition_id, c.name_zh AS league,
       COALESCE(h.name_zh, CONCAT('TEAM-', b.home_team_id)) AS home,
       COALESCE(a.name_zh, CONCAT('TEAM-', b.away_team_id)) AS away,
       b.home_team_id, b.away_team_id
FROM match_list_all b
LEFT JOIN competition_list c ON c.competition_id = b.competition_id
LEFT JOIN team_list h ON h.team_id = b.home_team_id
LEFT JOIN team_list a ON a.team_id = b.away_team_id
WHERE b.fixture_id = %s
"""
    with conn.cursor() as cur:
        cur.execute(sql, (fixture_id,))
        row = cur.fetchone()
    if not row:
        return None
    return Match(
        fixture_id=int(row["fixture_id"]),
        match_time=row["match_time"],
        status_id=row["status_id"],
        home_score=row["home_score"],
        away_score=row["away_score"],
        competition_id=row["competition_id"],
        league=row["league"],
        home=row["home"],
        away=row["away"],
        home_id=row["home_team_id"],
        away_id=row["away_team_id"],
    )


def fetch_asian(
    conn, fixture_id: int, cutoff: datetime
) -> list[CompanySeries]:
    """All pre-match main-line quotes with ``change_time <= cutoff``.

    The cut-off is inclusive: a snapshot at exactly T-30m must see quotes
    stamped T-30m, matching the document's ``cutoff <= T-30m`` definition.
    """
    placeholders = ",".join(["%s"] * len(ASIAN_WHITELIST))
    sql = f"""
SELECT id, company_id, company_name, change_the_time, home_odds, handicap,
       away_odds, NULL AS score, phase
FROM odds_live_all
WHERE fixture_id=%s AND phase='prematch' AND line_no=0
  AND company_id IN ({placeholders}) AND change_the_time <= %s
ORDER BY company_id, change_the_time, id
"""
    params = (fixture_id, *ASIAN_WHITELIST, cutoff)

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    grouped: dict[int, CompanySeries] = {}
    for row in rows:
        company_id = int(row["company_id"])
        series = grouped.setdefault(
            company_id, CompanySeries(company_id, row["company_name"])
        )
        series.rows.append(
            AsianRow(
                company_id=company_id,
                company_name=row["company_name"],
                change_time=row["change_the_time"],
                home_water=float(row["home_odds"]),
                handicap=float(row["handicap"]),
                away_water=float(row["away_odds"]),
                score=row["score"],
                phase=row["phase"],
            )
        )
    return [grouped[k] for k in sorted(grouped)]


def fetch_eu(conn, fixture_id: int, analysis_time: datetime) -> list[EuroSeries]:
    placeholders = ",".join(["%s"] * len(EU_WHITELIST))
    sql = f"""
SELECT company_id, company_name, change_the_time, home_win, draw, away_win
FROM odds_history_eu
WHERE fixture_id=%s AND company_id IN ({placeholders})
  AND change_the_time <= %s
ORDER BY company_id, change_the_time
"""
    with conn.cursor() as cur:
        cur.execute(sql, (fixture_id, *EU_WHITELIST, analysis_time))
        rows = cur.fetchall()

    grouped: dict[int, EuroSeries] = {}
    for row in rows:
        company_id = int(row["company_id"])
        series = grouped.setdefault(
            company_id, EuroSeries(company_id, row["company_name"])
        )
        series.rows.append(
            EuroRow(
                company_id=company_id,
                company_name=row["company_name"],
                change_time=row["change_the_time"],
                home=float(row["home_win"]),
                draw=float(row["draw"]),
                away=float(row["away_win"]),
            )
        )
    return [grouped[k] for k in sorted(grouped)]


def fetch_sup(conn, match: Match) -> SupInfo:
    if match.home_id is None or match.away_id is None:
        return SupInfo(False, detail="缺少球队 ID")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT team_id, teamname, ave_sup, fit_end_date "
                "FROM modeling.team_params "
                "WHERE team_id IN (%s, %s) AND comp_id = %s "
                "ORDER BY team_id, fit_end_date DESC",
                (match.home_id, match.away_id, match.competition_id),
            )
            rows = cur.fetchall()
    except Exception as exc:
        return SupInfo(False, detail=f"读取 team_params 失败: {exc}")

    latest: dict[int, dict] = {}
    for row in rows:
        latest.setdefault(int(row["team_id"]), row)
    home = latest.get(int(match.home_id))
    away = latest.get(int(match.away_id))
    if not home or not away:
        return SupInfo(False, detail="该联赛/球队缺少 modeling.team_params")
    return SupInfo(
        True,
        sup=float(home["ave_sup"]) - float(away["ave_sup"]),
        home_name=home.get("teamname"),
        away_name=away.get("teamname"),
        detail=(
            f"home={float(home['ave_sup']):+.2f} "
            f"away={float(away['ave_sup']):+.2f} "
            f"fit={max(home['fit_end_date'], away['fit_end_date'])}"
        ),
    )


def fetch_sup_asof(conn, match: Match, as_of: datetime) -> SupInfo:
    """Point-in-time sup: only team_params with fit_end_date <= analysis date."""
    if match.home_id is None or match.away_id is None:
        return SupInfo(False, detail="缺少球队 ID")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT team_id, teamname, ave_sup, fit_end_date "
                "FROM modeling.team_params "
                "WHERE team_id IN (%s, %s) AND comp_id = %s "
                "  AND fit_end_date <= %s "
                "ORDER BY team_id, fit_end_date DESC",
                (match.home_id, match.away_id, match.competition_id, as_of.date()),
            )
            rows = cur.fetchall()
    except Exception as exc:
        return SupInfo(False, detail=f"读取 team_params 失败: {exc}")

    latest: dict[int, dict] = {}
    for row in rows:
        latest.setdefault(int(row["team_id"]), row)
    home = latest.get(int(match.home_id))
    away = latest.get(int(match.away_id))
    if not home or not away:
        return SupInfo(False, detail="as-of 时点没有可用的 team_params")
    return SupInfo(
        True,
        sup=float(home["ave_sup"]) - float(away["ave_sup"]),
        home_name=home.get("teamname"),
        away_name=away.get("teamname"),
        detail=(
            f"home={float(home['ave_sup']):+.2f} "
            f"away={float(away['ave_sup']):+.2f} "
            f"fit={max(home['fit_end_date'], away['fit_end_date'])}"
        ),
    )


def fetch_backtest_matches(
    conn,
    start_date: str,
    end_date: str,
    min_age_hours: int = 3,
    limit: int = 500,
    league_like: str | None = None,
    fixture_ids: list[int] | None = None,
) -> list[Match]:
    cutoff = datetime.now() - timedelta(hours=min_age_hours)
    conditions = [
        "DATE(b.match_time) BETWEEN %s AND %s",
        "b.home_score IS NOT NULL",
        "b.away_score IS NOT NULL",
        "b.match_time < %s",
    ]
    params: list = [start_date, end_date, cutoff]
    if league_like:
        conditions.append("c.name_zh LIKE %s")
        params.append(f"%{league_like}%")
    if fixture_ids:
        placeholders = ",".join(["%s"] * len(fixture_ids))
        conditions.append(f"b.fixture_id IN ({placeholders})")
        params.extend(fixture_ids)
    params.append(limit)

    sql = f"""
SELECT b.fixture_id, b.match_time, b.status_id, b.home_score, b.away_score,
       b.competition_id, c.name_zh AS league,
       COALESCE(h.name_zh, CONCAT('TEAM-', b.home_team_id)) AS home,
       COALESCE(a.name_zh, CONCAT('TEAM-', b.away_team_id)) AS away,
       b.home_team_id, b.away_team_id
FROM match_list_all b
LEFT JOIN competition_list c ON c.competition_id = b.competition_id
LEFT JOIN team_list h ON h.team_id = b.home_team_id
LEFT JOIN team_list a ON a.team_id = b.away_team_id
WHERE {' AND '.join(conditions)}
ORDER BY b.match_time
LIMIT %s
"""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        Match(
            fixture_id=int(row["fixture_id"]),
            match_time=row["match_time"],
            status_id=row["status_id"],
            home_score=row["home_score"],
            away_score=row["away_score"],
            competition_id=row["competition_id"],
            league=row["league"],
            home=row["home"],
            away=row["away"],
            home_id=row["home_team_id"],
            away_id=row["away_team_id"],
        )
        for row in rows
    ]


def list_upcoming_matches(
    conn,
    within_minutes: int,
    limit: int = 200,
    league_ids: set[int] | frozenset[int] | None = None,
) -> list[Match]:
    """Matches kicking off between now and now + N minutes, optional league filter."""
    now = datetime.now()
    cutoff = now + timedelta(minutes=within_minutes)
    sql = """
SELECT b.fixture_id, b.match_time, b.status_id, b.home_score, b.away_score,
       b.competition_id, c.name_zh AS league,
       COALESCE(h.name_zh, CONCAT('TEAM-', b.home_team_id)) AS home,
       COALESCE(a.name_zh, CONCAT('TEAM-', b.away_team_id)) AS away,
       b.home_team_id, b.away_team_id
FROM match_list_all b
LEFT JOIN competition_list c ON c.competition_id = b.competition_id
LEFT JOIN team_list h ON h.team_id = b.home_team_id
LEFT JOIN team_list a ON a.team_id = b.away_team_id
WHERE b.match_time >= %s AND b.match_time <= %s
"""
    params: list = [now, cutoff]
    if league_ids:
        placeholders = ",".join(["%s"] * len(league_ids))
        sql += f" AND b.competition_id IN ({placeholders})"
        params.extend(sorted(league_ids))
    sql += " ORDER BY b.match_time LIMIT %s"
    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        Match(
            fixture_id=int(row["fixture_id"]),
            match_time=row["match_time"],
            status_id=row["status_id"],
            home_score=row["home_score"],
            away_score=row["away_score"],
            competition_id=row["competition_id"],
            league=row["league"],
            home=row["home"],
            away=row["away"],
            home_id=row["home_team_id"],
            away_id=row["away_team_id"],
        )
        for row in rows
    ]


def list_fixtures(conn, date: str, limit: int = 200) -> list[Match]:
    sql = """
SELECT b.fixture_id, b.match_time, b.status_id, b.home_score, b.away_score,
       b.competition_id, c.name_zh AS league,
       COALESCE(h.name_zh, CONCAT('TEAM-', b.home_team_id)) AS home,
       COALESCE(a.name_zh, CONCAT('TEAM-', b.away_team_id)) AS away,
       b.home_team_id, b.away_team_id
FROM match_list_all b
LEFT JOIN competition_list c ON c.competition_id = b.competition_id
LEFT JOIN team_list h ON h.team_id = b.home_team_id
LEFT JOIN team_list a ON a.team_id = b.away_team_id
WHERE DATE(b.match_time) = %s
ORDER BY b.match_time
LIMIT %s
"""
    with conn.cursor() as cur:
        cur.execute(sql, (date, limit))
        rows = cur.fetchall()
    return [
        Match(
            fixture_id=int(row["fixture_id"]),
            match_time=row["match_time"],
            status_id=row["status_id"],
            home_score=row["home_score"],
            away_score=row["away_score"],
            competition_id=row["competition_id"],
            league=row["league"],
            home=row["home"],
            away=row["away"],
            home_id=row["home_team_id"],
            away_id=row["away_team_id"],
        )
        for row in rows
    ]
