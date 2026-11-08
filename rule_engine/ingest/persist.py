#!/usr/bin/env python3
"""Import a saved titan007 AsianOdds page into MySQL.

Modes:
  1. Generate a .sql file (works without a MySQL driver):
       python -I scripts/import_titan007.py --html data/xxx.html --sql-out sql/load_2910948.sql
  2. Execute with explicit connection flags (requires pymysql):
       python -I scripts/import_titan007.py --html data/xxx.html --execute \
              --host 127.0.0.1 --user root --password *** --database football
  3. Execute using project .env (recommended):
       python scripts/import_titan007.py --html data/xxx.html --execute
     Reads MYSQL_HOST/MYSQL_PORT/MYSQL_USER/MYSQL_PASSWORD/MYSQL_DB
     from .env in the current working directory.

Rows written to odds_live_all:
  - main line only (line_no=0)
  - pre-match only (phase=prematch)
  - default company whitelist: 2,3,7,9,11,22

Rows written to odds_live_all_raw:
  - every line (main / 盘2 / 盘3 / ...)
  - both pre-match and in-play records
  - same company whitelist

Use --companies all to keep every company found on the page, or pass a custom
comma-separated id list.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from .parser import TITAN_NAME, TITAN_TO_SKILL, parse_html  # noqa: E402

# Asian-handicap companies kept in odds_live_all / odds_live_all_raw.
# Union of SKILL.md's Asian dictionary (2,3,7,9,22) and data_sources.md's
# core+auxiliary anchor (2,3,7,11,22); 11 = 韦德/Betway.
REQUIRED_COMPANIES = (2, 3, 7, 9, 11, 22)


def load_dotenv(path=".env"):
    """Minimal .env loader (avoids an extra dependency)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def sql_str(value):
    if value is None:
        return "NULL"
    text = str(value).replace("\\", "\\\\").replace("'", "''")
    return "'" + text + "'"


def sql_time(value):
    if not value:
        return "NULL"
    value = str(value)
    if len(value) == 16:  # YYYY-MM-DD HH:MM
        value += ":00"
    return sql_str(value)


def make_hash(*parts):
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def company_id_for(record):
    cid = record.get("company_id")
    if cid is not None:
        return int(cid)
    titan = record.get("company_id_titan")
    if titan is not None:
        return 1000 + int(titan)
    return 0


def parse_companies(value):
    """Parse '2,3,7,9,11,22' or 'all' into a tuple (None means no filter)."""
    if value is None or str(value).strip().lower() == "all":
        return None
    companies = []
    for part in str(value).split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise ValueError(f"invalid company id {part!r}")
        cid = int(part)
        if cid not in companies:
            companies.append(cid)
    return tuple(companies) if companies else None


def _allowed(company_id, companies):
    return companies is None or company_id in companies


def build_skill_rows(data, companies=REQUIRED_COMPANIES):
    """Main line + pre-match only, for odds_live_all."""
    rows = {}
    for r in data.get("main_records", []):
        if r.get("line_no") != 0:
            continue
        if r.get("phase") not in ("initial", "final"):
            continue
        if not r.get("change_time"):
            continue
        company_id = company_id_for(r)
        if not _allowed(company_id, companies):
            continue
        key = (
            company_id,
            r["change_time"],
            float(r["home_odds"]),
            float(r["handicap"]),
            float(r["away_odds"]),
        )
        rows[key] = {
            "fixture_id": int(data["fixture_id"]),
            "hometeam": data.get("home_team"),
            "awayteam": data.get("away_team"),
            "league_id": data.get("competition_id"),
            "league": data.get("competition"),
            "company_id": company_id,
            "company_name": r.get("company") or TITAN_NAME.get(r.get("company_id_titan")),
            "company_id_titan": r.get("company_id_titan"),
            "line_no": 0,
            "phase": "prematch",
            "change_the_time": r["change_time"],
            "home_odds": float(r["home_odds"]),
            "handicap": float(r["handicap"]),
            "away_odds": float(r["away_odds"]),
            "source": "titan007",
        }

    for r in data.get("detail_records", []):
        if r.get("phase") != "prematch":
            continue
        if not r.get("change_time"):
            continue
        company_id = company_id_for(r)
        if not _allowed(company_id, companies):
            continue
        key = (
            company_id,
            r["change_time"],
            float(r["home_odds"]),
            float(r["handicap"]),
            float(r["away_odds"]),
        )
        rows[key] = {
            "fixture_id": int(data["fixture_id"]),
            "hometeam": data.get("home_team"),
            "awayteam": data.get("away_team"),
            "league_id": data.get("competition_id"),
            "league": data.get("competition"),
            "company_id": company_id,
            "company_name": r.get("company") or TITAN_NAME.get(r.get("company_id_titan")),
            "company_id_titan": r.get("company_id_titan"),
            "line_no": 0,
            "phase": "prematch",
            "change_the_time": r["change_time"],
            "home_odds": float(r["home_odds"]),
            "handicap": float(r["handicap"]),
            "away_odds": float(r["away_odds"]),
            "source": "titan007",
        }

    return sorted(rows.values(), key=lambda x: (x["company_id"], x["change_the_time"]))


def build_raw_rows(data, companies=REQUIRED_COMPANIES):
    """All lines and phases, for odds_live_all_raw."""
    rows = {}
    for r in data.get("main_records", []):
        if not r.get("change_time"):
            continue
        if r.get("phase") not in ("initial", "final"):
            continue
        company_id = company_id_for(r)
        if not _allowed(company_id, companies):
            continue
        key = (
            company_id,
            r.get("line_no", 0),
            r.get("phase"),
            r["change_time"],
            float(r["home_odds"]),
            float(r["handicap"]),
            float(r["away_odds"]),
        )
        rows[key] = {
            "fixture_id": int(data["fixture_id"]),
            "hometeam": data.get("home_team"),
            "awayteam": data.get("away_team"),
            "league_id": data.get("competition_id"),
            "league": data.get("competition"),
            "company_id": company_id,
            "company_name": r.get("company") or TITAN_NAME.get(r.get("company_id_titan")),
            "company_id_titan": r.get("company_id_titan"),
            "line_no": int(r.get("line_no", 0)),
            "phase": r.get("phase"),
            "change_the_time": r["change_time"],
            "home_odds": float(r["home_odds"]),
            "handicap": float(r["handicap"]),
            "away_odds": float(r["away_odds"]),
            "score": None,
            "source": "titan007",
        }

    for r in data.get("detail_records", []):
        if not r.get("change_time"):
            continue
        company_id = company_id_for(r)
        if not _allowed(company_id, companies):
            continue
        key = (
            company_id,
            r.get("line_no", 0),
            r.get("phase"),
            r["change_time"],
            float(r["home_odds"]),
            float(r["handicap"]),
            float(r["away_odds"]),
        )
        rows[key] = {
            "fixture_id": int(data["fixture_id"]),
            "hometeam": data.get("home_team"),
            "awayteam": data.get("away_team"),
            "league_id": data.get("competition_id"),
            "league": data.get("competition"),
            "company_id": company_id,
            "company_name": r.get("company") or TITAN_NAME.get(r.get("company_id_titan")),
            "company_id_titan": r.get("company_id_titan"),
            "line_no": int(r.get("line_no", 0)),
            "phase": r.get("phase"),
            "change_the_time": r["change_time"],
            "home_odds": float(r["home_odds"]),
            "handicap": float(r["handicap"]),
            "away_odds": float(r["away_odds"]),
            "score": r.get("score") or None,
            "source": "titan007",
        }

    return sorted(
        rows.values(),
        key=lambda x: (x["company_id"], x["line_no"], x["phase"], x["change_the_time"]),
    )


def rows_to_sql(table, rows, extra_columns=None):
    """Build one multi-row INSERT with ON DUPLICATE KEY UPDATE."""
    if not rows:
        return None

    columns = [
        "fixture_id", "hometeam", "awayteam", "league_id", "league",
        "company_id", "company_name", "company_id_titan", "line_no", "phase",
        "change_the_time", "home_odds", "handicap", "away_odds",
    ]
    include_score = extra_columns and "score" in extra_columns
    if include_score:
        columns.append("score")
    columns.extend(["source", "row_hash"])

    values = []
    for r in rows:
        # Team names are denormalized attributes, not part of the identity:
        # keep row_hash unchanged so re-import remains idempotent.
        digest = make_hash(
            table, r["fixture_id"], r["company_id"], r["line_no"], r["phase"],
            r["change_the_time"], r["home_odds"], r["handicap"], r["away_odds"],
            r["source"],
        )
        vals = [
            str(r["fixture_id"]),
            sql_str(r.get("hometeam")),
            sql_str(r.get("awayteam")),
            "NULL" if r.get("league_id") is None else str(r["league_id"]),
            sql_str(r.get("league")),
            str(r["company_id"]),
            sql_str(r["company_name"]),
            "NULL" if r["company_id_titan"] is None else str(r["company_id_titan"]),
            str(r["line_no"]),
            sql_str(r["phase"]),
            sql_time(r["change_the_time"]),
            f"{r['home_odds']:.3f}",
            f"{r['handicap']:.2f}",
            f"{r['away_odds']:.3f}",
        ]
        if include_score:
            vals.append(sql_str(r.get("score")))
        vals.extend([sql_str(r["source"]), sql_str(digest)])
        values.append("(" + ", ".join(vals) + ")")

    update_cols = [
        "hometeam", "awayteam", "league_id", "league",
        "home_odds", "handicap", "away_odds",
        "company_name", "company_id_titan",
    ]
    update_sql = ", ".join(f"{c}=VALUES({c})" for c in update_cols)
    return (
        f"INSERT INTO {table}\n  ({', '.join(columns)})\nVALUES\n  "
        + ",\n  ".join(values)
        + f"\nON DUPLICATE KEY UPDATE {update_sql};"
    )


def build_statements(data, status_id=1, database="football", companies=REQUIRED_COMPANIES):
    statements = [f"USE `{database}`;", "SET NAMES utf8mb4;"]

    if data.get("fixture_id") is None:
        raise ValueError(
            "data['fixture_id'] is required; pass a fixture id when the page "
            "does not embed AsianOdds_n.aspx?id=..."
        )
    fixture_id = int(data["fixture_id"])
    competition_id = data.get("competition_id")
    competition = data.get("competition")
    home_team = data.get("home_team")
    away_team = data.get("away_team")
    home_team_id = data.get("home_team_id")
    away_team_id = data.get("away_team_id")
    kickoff = data.get("kickoff")
    home_score = data.get("home_score")
    away_score = data.get("away_score")

    if competition_id is not None and competition:
        statements.append(
            "INSERT INTO competition_list (competition_id, name_zh)\n"
            f"VALUES ({competition_id}, {sql_str(competition)})\n"
            "ON DUPLICATE KEY UPDATE name_zh=VALUES(name_zh);"
        )

    if home_team_id is not None and home_team:
        statements.append(
            "INSERT INTO team_list (team_id, name_zh)\n"
            f"VALUES ({home_team_id}, {sql_str(home_team)})\n"
            "ON DUPLICATE KEY UPDATE name_zh=VALUES(name_zh);"
        )
    if away_team_id is not None and away_team:
        statements.append(
            "INSERT INTO team_list (team_id, name_zh)\n"
            f"VALUES ({away_team_id}, {sql_str(away_team)})\n"
            "ON DUPLICATE KEY UPDATE name_zh=VALUES(name_zh);"
        )

    if competition_id is not None and home_team_id is not None and away_team_id is not None and kickoff:
        statements.append(
            "INSERT INTO match_list_all\n"
            "  (fixture_id, competition_id, home_team_id, away_team_id, match_time, "
            "status_id, home_score, away_score, source)\n"
            f"VALUES ({fixture_id}, {competition_id}, {home_team_id}, {away_team_id}, "
            f"{sql_time(kickoff)}, {status_id}, "
            f"{'NULL' if home_score is None else home_score}, "
            f"{'NULL' if away_score is None else away_score}, {sql_str('titan007')})\n"
            "ON DUPLICATE KEY UPDATE\n"
            "  match_time=VALUES(match_time), home_score=VALUES(home_score), "
            "away_score=VALUES(away_score), source=VALUES(source);"
        )

    skill_rows = build_skill_rows(data, companies)
    raw_rows = build_raw_rows(data, companies)

    sql = rows_to_sql("odds_live_all", skill_rows)
    if sql:
        statements.append(sql)
    sql = rows_to_sql("odds_live_all_raw", raw_rows, extra_columns=["score"])
    if sql:
        statements.append(sql)

    detail_prematch = sum(
        1 for r in data.get("detail_records", []) if r.get("phase") == "prematch"
    )
    statements.append(
        "INSERT INTO data_import_log\n"
        "  (source, fixture_id, rows_main, rows_detail_prematch, rows_inserted)\n"
        f"VALUES ('titan007', {fixture_id}, {len(data.get('main_records', []))}, "
        f"{detail_prematch}, {len(skill_rows)});"
    )

    return statements, skill_rows, raw_rows


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Import titan007 Asian odds into MySQL or SQL file")
    parser.add_argument("--html", help="saved titan007 HTML file")
    parser.add_argument("--json", help="pre-parsed JSON (skips HTML parsing)")
    parser.add_argument("--sql-out", help="write SQL file (default: sql/load_<fixture>.sql)")
    parser.add_argument("--execute", action="store_true", help="execute against MySQL (requires pymysql)")
    parser.add_argument("--host", default=os.environ.get("MYSQL_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MYSQL_PORT", "3306")))
    parser.add_argument("--user", default=os.environ.get("MYSQL_USER", "root"))
    parser.add_argument("--password", default=os.environ.get("MYSQL_PASSWORD", ""))
    parser.add_argument("--database", default=os.environ.get("MYSQL_DB", "football"))
    parser.add_argument("--status-id", type=int, default=1, help="match_list_all.status_id (default 1)")
    parser.add_argument(
        "--companies",
        default=",".join(str(c) for c in REQUIRED_COMPANIES),
        help="comma-separated company ids, or 'all' (default: 2,3,7,9,11,22)",
    )
    args = parser.parse_args()
    companies = parse_companies(args.companies)

    if args.json:
        with open(args.json, encoding="utf-8") as fh:
            data = json.load(fh)
    elif args.html:
        data = parse_html(args.html)
    else:
        parser.error("one of --html or --json is required")

    statements, skill_rows, raw_rows = build_statements(
        data, status_id=args.status_id, database=args.database, companies=companies
    )
    payload = "\n\n".join(statements) + "\n"

    sql_out = args.sql_out
    if not sql_out:
        sql_out = str(
            Path(__file__).resolve().parents[2] / "sql" / f"load_{data['fixture_id']}.sql"
        )
    with open(sql_out, "w", encoding="utf-8") as fh:
        fh.write(payload)

    print(f"fixture_id={data['fixture_id']} sql_out={sql_out}")
    print(
        f"companies={companies if companies is not None else 'all'} "
        f"odds_live_all rows={len(skill_rows)} odds_live_all_raw rows={len(raw_rows)}"
    )

    if args.execute:
        try:
            import pymysql  # type: ignore
        except ImportError:
            raise SystemExit(
                "pymysql is not installed. Run: python -m pip install pymysql"
            )
        conn = pymysql.connect(
            host=args.host,
            port=args.port,
            user=args.user,
            password=args.password,
            database=args.database,
            charset="utf8mb4",
            autocommit=False,
        )
        try:
            with conn.cursor() as cur:
                for stmt in statements:
                    cur.execute(stmt)
            conn.commit()
            print("executed against MySQL successfully")
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


if __name__ == "__main__":
    main()
