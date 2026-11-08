#!/usr/bin/env python3
"""Import titan007 1x2 (European) odds into MySQL `odds_history_eu`.

Data source: https://1x2d.titan007.com/{fixture_id}.js

The JS file contains:
  var game=Array("...")        one record per bookmaker: company|odds_id|name|...
  var gameDetail=Array("...")  per-odds_id history: odds_id^home|draw|away|MM-DD HH:MM|...|YYYY;...

Default company whitelist (from the project docs):
  2=Bet365, 3=皇冠, 5=立博, 7=澳门, 9=威廉希尔, 22=平博(Pinnacle)

Examples:
    python scripts/import_titan007_eu.py --fixture-id 2910948 --dry-run
    python scripts/import_titan007_eu.py --fixture-id 2910948
    python scripts/import_titan007_eu.py --js data/2910948.js
    python scripts/import_titan007_eu.py --fixture-id 2910948,2910949
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

from .db import connect, get_config, print_rows  # noqa: E402

EU_SOURCE = "titan007_eu"
EU_ODDS_SOURCE = "titan007"
DEFAULT_COMPANIES = (2, 3, 5, 7, 9, 22)
URL_TEMPLATE = "https://1x2d.titan007.com/{fixture_id}.js"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://1x2.titan007.com/oddslist/{fixture_id}.htm",
    "Accept": "*/*",
}

# titan007 1x2 company id -> (skill company_id, canonical name)
TITAN_1X2_MAP = {
    "281": (2, "Bet365"),
    "545": (3, "皇冠"),
    "82": (5, "立博"),
    "80": (7, "澳门"),
    "115": (9, "威廉希尔"),
    "177": (22, "平博"),
}
# Fallback when the numeric id changes.
NAME_MAP = {
    "bet365": (2, "Bet365"),
    "crown": (3, "皇冠"),
    "ladbrokes": (5, "立博"),
    "macauslot": (7, "澳门"),
    "williamhill": (9, "威廉希尔"),
    "pinnacle": (22, "平博"),
}


def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def resolve_company(titan_id: str, english_name: str):
    if titan_id in TITAN_1X2_MAP:
        return TITAN_1X2_MAP[titan_id]
    return NAME_MAP.get(normalize_name(english_name))


def parse_companies(value):
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


def make_row_hash(fixture_id, company_id, change_time, home, draw, away):
    raw = "|".join(
        [
            "odds_history_eu",
            str(fixture_id),
            str(company_id),
            change_time,
            f"{home:.2f}",
            f"{draw:.2f}",
            f"{away:.2f}",
            EU_ODDS_SOURCE,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def download_js(fixture_id: int, timeout: int) -> bytes:
    url = URL_TEMPLATE.format(fixture_id=fixture_id)
    headers = {
        key: value.format(fixture_id=fixture_id) for key, value in REQUEST_HEADERS.items()
    }
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception as exc:
        # 1x2d sometimes presents a certificate chain urllib cannot verify.
        # Fall back to the OpenSSL CLI transport, which is already used for
        # vip.titan007.com and zq.titan007.com.
        try:
            from .crawl import fetch_url, find_openssl

            _, body = fetch_url(
                find_openssl(),
                "1x2d.titan007.com",
                f"/{fixture_id}.js",
                timeout=timeout,
                retries=1,
            )
            print(
                f"[eu] urllib failed ({type(exc).__name__}); "
                "used openssl fallback",
                file=sys.stderr,
            )
            return body
        except Exception:
            raise exc


def parse_js(text: str, fixture_id=None) -> dict:
    schedule = re.search(r"ScheduleID=(\d+)", text)
    if fixture_id is not None:
        fid = int(fixture_id)
    elif schedule:
        fid = int(schedule.group(1))
    else:
        raise ValueError("fixture id not found in JS and --fixture-id was not given")

    match_time = re.search(r'MatchTime="([^"]+)"', text)
    default_year = datetime.now().year
    if match_time:
        year_match = re.match(r"(\d{4})", match_time.group(1))
        if year_match:
            default_year = int(year_match.group(1))

    def js_var(name):
        found = re.search(rf'var {name}="([^"]*)"', text)
        return found.group(1).strip() if found else None

    home_team = js_var("hometeam_cn") or js_var("hometeam")
    away_team = js_var("guestteam_cn") or js_var("guestteam")
    league_name = js_var("matchname_cn") or js_var("matchname")
    sclass_match = re.search(r"var sclassId=(\d+)", text)
    league_id = int(sclass_match.group(1)) if sclass_match else None

    game_match = re.search(r'var game=Array\("(.*?)"\);', text, re.S)
    detail_match = re.search(r'var gameDetail=Array\("(.*?)"\);', text, re.S)
    if not game_match or not detail_match:
        raise ValueError("var game / var gameDetail arrays not found")

    odds_to_company = {}
    for record in game_match.group(1).split('\",\"'):
        fields = record.split("|")
        if len(fields) < 3:
            continue
        company = resolve_company(fields[0], fields[2])
        if company is not None:
            odds_to_company[fields[1]] = company

    rows = []
    seen = set()
    raw_entries = 0
    skipped = 0
    for record in detail_match.group(1).split('\",\"'):
        if "^" not in record:
            continue
        odds_id, body = record.split("^", 1)
        company = odds_to_company.get(odds_id)
        if company is None:
            continue
        company_id, company_name = company
        for entry in body.split(";"):
            entry = entry.strip()
            if not entry:
                continue
            raw_entries += 1
            fields = entry.split("|")
            if len(fields) < 8:
                skipped += 1
                continue
            try:
                home = float(fields[0])
                draw = float(fields[1])
                away = float(fields[2])
                year = fields[7] if fields[7].isdigit() and len(fields[7]) == 4 else str(default_year)
                change_dt = datetime.strptime(f"{year}-{fields[3]}", "%Y-%m-%d %H:%M")
            except (ValueError, IndexError):
                skipped += 1
                continue
            change_time = change_dt.strftime("%Y-%m-%d %H:%M:%S")
            digest = make_row_hash(
                fid, company_id, change_time, home, draw, away
            )
            if digest in seen:
                continue
            seen.add(digest)
            rows.append(
                {
                    "fixture_id": fid,
                    "hometeam": home_team,
                    "awayteam": away_team,
                    "league_id": league_id,
                    "league": league_name,
                    "company_id": company_id,
                    "company_name": company_name,
                    "change_the_time": change_time,
                    "home_win": home,
                    "draw": draw,
                    "away_win": away,
                    "source": EU_ODDS_SOURCE,
                    "row_hash": digest,
                }
            )

    rows.sort(key=lambda row: (row["company_id"], row["change_the_time"]))
    return {
        "fixture_id": fid,
        "hometeam": home_team,
        "awayteam": away_team,
        "league_id": league_id,
        "league": league_name,
        "rows": rows,
        "raw_entries": raw_entries,
        "skipped": skipped,
    }


def filter_rows(rows, companies):
    if companies is None:
        return rows
    return [row for row in rows if row["company_id"] in companies]


def summarize(rows):
    counts = Counter((row["company_id"], row["company_name"]) for row in rows)
    return [
        (company_id, name, count)
        for (company_id, name), count in sorted(counts.items())
    ]


def import_fixture(conn, parsed, companies):
    rows = filter_rows(parsed["rows"], companies)
    fixture_id = parsed["fixture_id"]
    if not rows:
        return 0, 0, parsed["raw_entries"]

    sql = (
        "INSERT INTO odds_history_eu\n"
        "  (fixture_id, hometeam, awayteam, league_id, league, company_id, "
        "   company_name, change_the_time, home_win, draw, away_win, source, row_hash)\n"
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)\n"
        "ON DUPLICATE KEY UPDATE\n"
        "  hometeam=VALUES(hometeam), awayteam=VALUES(awayteam),\n"
        "  league_id=VALUES(league_id), league=VALUES(league),\n"
        "  home_win=VALUES(home_win), draw=VALUES(draw), away_win=VALUES(away_win),\n"
        "  company_name=VALUES(company_name)"
    )
    params = [
        (
            row["fixture_id"],
            row["hometeam"],
            row["awayteam"],
            row["league_id"],
            row["league"],
            row["company_id"],
            row["company_name"],
            row["change_the_time"],
            row["home_win"],
            row["draw"],
            row["away_win"],
            row["source"],
            row["row_hash"],
        )
        for row in rows
    ]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM odds_history_eu WHERE fixture_id=%s", (fixture_id,)
        )
        before = int(cur.fetchone()[0])
        for start in range(0, len(params), 500):
            cur.executemany(sql, params[start:start + 500])
        cur.execute(
            "SELECT COUNT(*) FROM odds_history_eu WHERE fixture_id=%s", (fixture_id,)
        )
        after = int(cur.fetchone()[0])
        inserted = max(0, after - before)
        cur.execute(
            "INSERT INTO data_import_log\n"
            "  (source, fixture_id, rows_main, rows_detail_prematch, rows_inserted)\n"
            "VALUES (%s, %s, %s, 0, %s)",
            (EU_SOURCE, fixture_id, len(rows), inserted),
        )
    conn.commit()
    return len(rows), inserted, parsed["raw_entries"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import titan007 European odds JS into odds_history_eu"
    )
    parser.add_argument("--js", nargs="+", help="saved JS file(s)")
    parser.add_argument("--fixture-id", help="comma-separated fixture ids to download")
    parser.add_argument(
        "--companies",
        default=",".join(str(c) for c in DEFAULT_COMPANIES),
        help="comma-separated company ids, or 'all' (default: 2,3,5,7,9,22)",
    )
    parser.add_argument("--dry-run", action="store_true", help="parse only, do not write")
    parser.add_argument("--save-js", default="data", help="directory for downloaded JS")
    parser.add_argument("--no-save", action="store_true", help="do not save downloaded JS")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--database", help="override MYSQL_DB")
    args = parser.parse_args()

    if not args.js and not args.fixture_id:
        parser.error("one of --js or --fixture-id is required")

    try:
        companies = parse_companies(args.companies)
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    jobs = []
    if args.js:
        for raw in args.js:
            path = Path(raw)
            if not path.is_absolute():
                path = (PROJECT_ROOT / path).resolve()
            if not path.exists():
                print(f"[error] JS file not found: {path}", file=sys.stderr)
                return 2
            jobs.append((str(path), path.read_bytes()))
    if args.fixture_id:
        for part in args.fixture_id.split(","):
            part = part.strip()
            if not part:
                continue
            if not part.isdigit():
                print(f"[error] invalid fixture id {part!r}", file=sys.stderr)
                return 2
            fixture_id = int(part)
            try:
                payload = download_js(fixture_id, args.timeout)
            except Exception as exc:
                print(f"[error] download {fixture_id} failed: {exc}", file=sys.stderr)
                return 1
            source = f"download {fixture_id}"
            if not args.no_save:
                save_dir = Path(args.save_js)
                if not save_dir.is_absolute():
                    save_dir = PROJECT_ROOT / save_dir
                save_dir.mkdir(parents=True, exist_ok=True)
                save_path = save_dir / f"{fixture_id}.js"
                save_path.write_bytes(payload)
                source = str(save_path)
            jobs.append((source, payload))

    conn = None
    if not args.dry_run:
        try:
            cfg = get_config()
            conn = connect(
                cfg, database=args.database or cfg["database"], autocommit=False
            )
        except Exception as exc:
            print(f"[db] {exc}", file=sys.stderr)
            return 2

    failures = 0
    try:
        for source, payload in jobs:
            try:
                text = payload.decode("utf-8-sig")
                parsed = parse_js(text)
            except Exception as exc:
                failures += 1
                print(f"[{source}] parse failed: {exc}", file=sys.stderr)
                continue
            rows = filter_rows(parsed["rows"], companies)
            print(
                f"\n[{source}] fixture={parsed['fixture_id']} "
                f"raw_entries={parsed['raw_entries']} selected={len(rows)} "
                f"skipped={parsed['skipped']}"
            )
            if rows:
                print_rows(summarize(rows), ["company", "name", "entries"], "table")
            if args.dry_run:
                continue
            try:
                processed, inserted, raw_entries = import_fixture(conn, parsed, companies)
                print(
                    f"fixture {parsed['fixture_id']}: processed {processed} row(s), "
                    f"new {inserted} (raw entries={raw_entries})"
                )
            except Exception as exc:
                conn.rollback()
                failures += 1
                print(f"[{source}] DB write failed: {exc}", file=sys.stderr)
    finally:
        if conn is not None:
            conn.close()

    if failures:
        print(f"\n{failures} failure(s)", file=sys.stderr)
        return 1
    print("\ndone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
