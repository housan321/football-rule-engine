#!/usr/bin/env python3
"""Sync one or more titan007 fixtures: Asian handicap + European odds.

Reuses:
  scripts/crawl_titan007_openssl.py : openssl transport, schedule discovery,
                                       Titan007MySQLPipeline
  scripts/import_titan007_eu.py     : 1x2d JS download/parse/import

For each fixture both sources are attempted independently:
  - Asian page -> odds_live_all / odds_live_all_raw
  - 1x2d JS    -> odds_history_eu

A failure in one source does not prevent the other from being written.

Examples:
    python scripts/import_titan007_all.py --ids 2910948
    python scripts/import_titan007_all.py --ids 2910948 --dry-run
    python scripts/import_titan007_all.py --dates 2026-10-28 --leagues 4 --max-fixtures 1
    python scripts/import_titan007_all.py --ids 2910948,2910949
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

from ..leagues import LEAGUE_IDS  # noqa: E402
from . import crawl as asian  # noqa: E402
from . import eu  # noqa: E402
from .db import connect, get_config, print_rows  # noqa: E402
from .parser import decode_html, parse_html_text  # noqa: E402

logger = logging.getLogger("titan007.all")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import titan007 Asian handicap + European odds for the same fixture(s)"
    )
    parser.add_argument("--ids", help="comma-separated fixture ids")
    parser.add_argument("--dates", help="schedule dates, e.g. 2026-10-28,2026-10-29")
    parser.add_argument("--leagues", help="comma-separated SclassID values")
    parser.add_argument(
        "--all-leagues",
        action="store_true",
        help="disable the hard-coded league whitelist",
    )
    parser.add_argument("--max-fixtures", type=int)
    parser.add_argument(
        "--eu-companies",
        default=",".join(str(c) for c in eu.DEFAULT_COMPANIES),
        help="EU company ids, or 'all' (default: 2,3,5,7,9,22)",
    )
    parser.add_argument("--dry-run", action="store_true", help="fetch/parse only")
    parser.add_argument("--save-js", default="data", help="directory for downloaded 1x2d JS")
    parser.add_argument("--no-save", action="store_true", help="do not save downloaded JS")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=2, help="Asian fetch retries")
    parser.add_argument("--database", help="override MYSQL_DB")
    parser.add_argument("--status-id", type=int, default=1)
    parser.add_argument("--openssl-bin", help="path to the openssl executable")
    parser.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )
    return parser.parse_args(argv)


def save_js(payload: bytes, fixture_id: int, args) -> str | None:
    if args.no_save:
        return None
    save_dir = Path(args.save_js)
    if not save_dir.is_absolute():
        save_dir = PROJECT_ROOT / save_dir
    save_dir.mkdir(parents=True, exist_ok=True)
    path = save_dir / f"{fixture_id}.js"
    path.write_bytes(payload)
    return str(path)


def fetch_eu(fixture_id: int, args):
    payload = eu.download_js(fixture_id, args.timeout)
    saved = save_js(payload, fixture_id, args)
    text = payload.decode("utf-8-sig")
    embedded = re.search(r"ScheduleID=(\d+)", text)
    if embedded and int(embedded.group(1)) != fixture_id:
        raise ValueError(
            f"EU JS fixture mismatch: page has {embedded.group(1)}, requested {fixture_id}"
        )
    parsed = eu.parse_js(text, fixture_id=fixture_id)
    parsed["source"] = saved or f"https://1x2d.titan007.com/{fixture_id}.js"
    return parsed


def ensure_fixture_ids(args) -> list[int]:
    fixture_ids = asian.parse_ids(args.ids)
    if fixture_ids:
        if args.max_fixtures:
            fixture_ids = fixture_ids[: args.max_fixtures]
        return fixture_ids

    openssl = asian.find_openssl(args.openssl_bin)
    leagues = asian.parse_leagues(args.leagues) if args.leagues else None
    if leagues is None and not getattr(args, "all_leagues", False):
        leagues = LEAGUE_IDS
    return asian.discover_fixtures(
        openssl,
        asian.parse_dates(args.dates),
        leagues,
        args.max_fixtures,
        args.timeout,
    )


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asian.FETCH_RETRIES = args.retries

    try:
        eu_companies = eu.parse_companies(args.eu_companies)
        openssl = asian.find_openssl(args.openssl_bin)
        fixture_ids = ensure_fixture_ids(args)
    except Exception as exc:
        logger.error("%s", exc)
        return 2
    if not fixture_ids:
        logger.error("no fixture ids found")
        return 2

    logger.info(
        "fixtures=%s eu_companies=%s dry_run=%s",
        ",".join(str(x) for x in fixture_ids),
        eu_companies if eu_companies is not None else "all",
        args.dry_run,
    )

    if args.dry_run:
        return dry_run(args, openssl, fixture_ids)

    pipeline = asian.Titan007MySQLPipeline(
        database=args.database, status_id=args.status_id
    )
    pipeline.stats = asian.SimpleStats()
    eu_conn = None
    try:
        pipeline.open_spider()
        cfg = get_config()
        eu_conn = connect(
            cfg, database=args.database or cfg["database"], autocommit=False
        )
    except Exception as exc:
        logger.error("database connection failed: %s", exc)
        pipeline.close_spider()
        return 2

    results = []
    failures = 0
    try:
        for index, fixture_id in enumerate(fixture_ids, 1):
            asian_status = "-"
            eu_status = "-"

            # ---- Asian -------------------------------------------------
            attempts = max(1, args.retries + 1)
            for attempt in range(1, attempts + 1):
                try:
                    _, body = asian.fetch_odds(openssl, fixture_id, args.timeout)
                    before = dict(pipeline.stats.values)
                    pipeline.process_item(
                        {
                            "fixture_id": fixture_id,
                            "url": f"https://vip.titan007.com/AsianOdds_n.aspx?id={fixture_id}",
                            "body": body,
                            "encoding": None,
                            "fetched_at": datetime.now().isoformat(timespec="seconds"),
                            "status": 200,
                        }
                    )
                    main_rows = pipeline.stats.values.get(
                        "titan007/rows_main", 0
                    ) - before.get("titan007/rows_main", 0)
                    raw_rows = pipeline.stats.values.get(
                        "titan007/rows_raw", 0
                    ) - before.get("titan007/rows_raw", 0)
                    asian_status = f"main={main_rows} raw={raw_rows}"
                    break
                except Exception as exc:
                    empty_odds = "no main odds records" in str(exc)
                    if empty_odds and attempt < attempts:
                        logger.warning(
                            "fixture %s Asian empty odds, retry %d/%d",
                            fixture_id,
                            attempt,
                            attempts - 1,
                        )
                        asian.time.sleep(1.5)
                        continue
                    failures += 1
                    asian_status = f"FAIL: {str(exc)[:160]}"
                    logger.error("fixture %s Asian: %s", fixture_id, exc)
                    break

            # ---- European ----------------------------------------------
            try:
                parsed = fetch_eu(fixture_id, args)
                processed, inserted, raw_entries = eu.import_fixture(
                    eu_conn, parsed, eu_companies
                )
                eu_status = f"processed={processed} new={inserted} raw={raw_entries}"
                logger.info("fixture %s EU: %s", fixture_id, eu_status)
            except Exception as exc:
                try:
                    eu_conn.rollback()
                except Exception:
                    pass
                failures += 1
                eu_status = f"FAIL: {str(exc)[:160]}"
                logger.error("fixture %s EU: %s", fixture_id, exc)

            results.append((fixture_id, asian_status, eu_status))
            if index < len(fixture_ids):
                asian.time.sleep(1.5)
    finally:
        pipeline.close_spider()
        if eu_conn is not None:
            eu_conn.close()

    print_rows(results, ["fixture", "asian", "european"], "table")
    print(
        f"\nfailures={failures} "
        f"asian_stats={pipeline.stats.values}"
    )
    return 1 if failures else 0


def dry_run(args, openssl, fixture_ids: list[int]) -> int:
    results = []
    failures = 0
    for index, fixture_id in enumerate(fixture_ids, 1):
        asian_status = "-"
        eu_status = "-"
        try:
            _, body = asian.fetch_odds(openssl, fixture_id, args.timeout)
            data = parse_html_text(decode_html(body), fixture_id=fixture_id)
            asian_status = (
                f"main={data['counts']['main']} detail={data['counts']['detail']}"
            )
        except Exception as exc:
            failures += 1
            asian_status = f"FAIL: {str(exc)[:160]}"
            logger.error("fixture %s Asian: %s", fixture_id, exc)

        try:
            parsed = fetch_eu(fixture_id, args)
            rows = eu.filter_rows(parsed["rows"], eu.parse_companies(args.eu_companies))
            eu_status = f"entries={len(rows)} raw={parsed['raw_entries']}"
        except Exception as exc:
            failures += 1
            eu_status = f"FAIL: {str(exc)[:160]}"
            logger.error("fixture %s EU: %s", fixture_id, exc)

        results.append((fixture_id, asian_status, eu_status))
        if index < len(fixture_ids):
            asian.time.sleep(1.5)

    print_rows(results, ["fixture", "asian", "european"], "table")
    print(f"\nfailures={failures} (dry-run, nothing written)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
