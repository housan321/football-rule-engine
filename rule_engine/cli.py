from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

from .backtest import print_summary, run_backtest, summarize, write_csv, write_summary
from .walkforward import (
    build_summary as build_walkforward_summary,
    load_csv as load_walkforward_csv,
    print_summary as print_walkforward_summary,
    write_json as write_walkforward_json,
    write_markdown as write_walkforward_markdown,
)
from .config import load_config
from .db import (
    CORE_ASIAN,
    connect,
    fetch_asian,
    fetch_eu,
    fetch_match,
    fetch_sup,
    list_fixtures,
    list_upcoming_matches,
)
from .engine import analyze, apply_action_filters, determine_mode
from .leagues import LEAGUE_IDS
from .notify import email_config_from_env, send_actionable_email
from .report import write_outputs
from .tracker import (
    DEFAULT_TRACKER_PATH,
    append_rows as append_tracker_rows,
    build_actionable_csv,
    result_to_tracker_row,
    update_tracking_results,
)


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def _parse_ids(values) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for value in values or []:
        for part in str(value).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                fixture_id = int(part)
            except ValueError as exc:
                raise ValueError(f"无效 fixture id: {part!r}") from exc
            if fixture_id not in seen:
                seen.add(fixture_id)
                ids.append(fixture_id)
    if not ids:
        raise ValueError("至少需要一个 fixture id")
    return ids


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError("时间格式应为 'YYYY-MM-DD HH:MM[:SS]'")


class InsufficientOdds(Exception):
    """Raised when an imminent fixture does not have enough odds to analyze."""


def _resolve_analysis_time(
    match,
    base_time: datetime | None,
    decision_minutes: int | None,
) -> datetime:
    """Per-match decision time: kickoff - N minutes, or a fixed timestamp, or now."""
    if decision_minutes is not None:
        return match.match_time - timedelta(minutes=decision_minutes)
    return base_time or datetime.now()


def _action_filters(args) -> tuple[bool, int | None]:
    only_giving = bool(
        getattr(args, "only_giving", False)
        or getattr(args, "experimental_default", False)
    )
    min_conf = getattr(args, "min_confidence", None)
    if getattr(args, "experimental_default", False) and min_conf is None:
        min_conf = 50
    return only_giving, min_conf


def _decision_minutes_for(args) -> int | None:
    if (
        getattr(args, "experimental_default", False)
        and getattr(args, "decision_minutes", None) is None
        and getattr(args, "as_of", None) is None
    ):
        return 60
    return getattr(args, "decision_minutes", None)


def _print_result(result) -> None:
    m = result.match
    print(
        f"{m.fixture_id}  {m.league or '-':<8}  {m.home} vs {m.away}  "
        f"[{result.mode}]  -> {result.direction_name}  "
        f"conf={result.confidence}({result.confidence_label})  {result.stake}"
    )
    print(f"    {result.summary}")
    side = f"{result.side_line:+.2f}" if result.side_line is not None else "-"
    kind = (
        "让球方"
        if result.is_giving
        else ("受让/PK" if result.side_line is not None else "-")
    )
    print(
        f"    side_line={side} ({kind}) | "
        f"实验默认={'是' if result.experimental_default else '否'} | "
        f"可下注={'是' if result.actionable else '否'}"
    )
    if result.filter_notes:
        print(f"    过滤: {' | '.join(result.filter_notes)}")
    if result.conflicts:
        print(f"    conflicts: {' | '.join(result.conflicts[:3])}")


def _analyze_one(
    conn,
    fixture_id: int,
    base_time: datetime | None,
    decision_minutes: int | None,
    mode: str,
    out_dir: str,
    write: bool,
    only_giving: bool = False,
    min_confidence: int | None = None,
    min_asian_companies: int | None = None,
    min_eu_companies: int | None = None,
):
    match = fetch_match(conn, fixture_id)
    if not match:
        raise ValueError(f"fixture {fixture_id} not found")
    analysis_time = _resolve_analysis_time(match, base_time, decision_minutes)
    resolved_mode = (
        "pre"
        if decision_minutes is not None
        else determine_mode(match, analysis_time, mode)
    )
    asian = fetch_asian(conn, fixture_id, resolved_mode, analysis_time)
    euro = fetch_eu(conn, fixture_id, analysis_time)
    if min_asian_companies is not None or min_eu_companies is not None:
        core = len(
            [
                s
                for s in asian
                if s.company_id in CORE_ASIAN and s.rows
            ]
        )
        eu_count = len([s for s in euro if s.rows])
        if min_asian_companies is not None and core < min_asian_companies:
            raise InsufficientOdds(
                f"亚盘核心公司 {core} < {min_asian_companies}"
            )
        if min_eu_companies is not None and eu_count < min_eu_companies:
            raise InsufficientOdds(
                f"欧赔公司 {eu_count} < {min_eu_companies}"
            )
    sup = fetch_sup(conn, match)
    result = analyze(match, resolved_mode, analysis_time, asian, euro, sup)
    apply_action_filters(
        result, only_giving=only_giving, min_confidence=min_confidence
    )
    paths = write_outputs(result, out_dir) if write else (None, None)
    return result, paths


def cmd_analyze(args) -> int:
    try:
        fixture_ids = _parse_ids(args.ids) if args.ids else [args.fixture_id]
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    cfg = load_config(database=args.database)
    conn = connect(cfg)
    base_time = _parse_time(args.as_of)
    only_giving, min_conf = _action_filters(args)
    decision_minutes = _decision_minutes_for(args)
    results = []
    failures: list[tuple[int, str]] = []
    try:
        for fixture_id in fixture_ids:
            try:
                result, (html_path, json_path) = _analyze_one(
                    conn,
                    fixture_id,
                    base_time,
                    decision_minutes,
                    args.mode,
                    args.out_dir,
                    write=not args.json_only,
                    only_giving=only_giving,
                    min_confidence=min_conf,
                )
                results.append(result)
                if not args.json_only:
                    _print_result(result)
                    print(f"    html: {html_path}")
                    print(f"    json: {json_path}")
            except Exception as exc:  # noqa: BLE001 - one fixture must not stop the batch
                failures.append((fixture_id, str(exc)))
                print(f"[error] fixture {fixture_id}: {exc}", file=sys.stderr)
    finally:
        conn.close()

    if args.json_only:
        import json

        payload = (
            results[0].to_dict()
            if len(results) == 1
            else [r.to_dict() for r in results]
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"\nanalyzed {len(results)}/{len(fixture_ids)}"
            + (f", failures={len(failures)}" if failures else "")
        )
    return 1 if failures else 0


def cmd_scan(args) -> int:
    cfg = load_config(database=args.database)
    conn = connect(cfg)
    try:
        fixtures = list_fixtures(conn, args.date, limit=args.limit)
        if not fixtures:
            print(f"no fixtures on {args.date}")
            return 0
        base_time = _parse_time(args.as_of)
        only_giving, min_conf = _action_filters(args)
        decision_minutes = _decision_minutes_for(args)
        rows = []
        for match in fixtures:
            analysis_time = _resolve_analysis_time(
                match, base_time, decision_minutes
            )
            mode = (
                "pre"
                if decision_minutes is not None
                else determine_mode(match, analysis_time, args.mode)
            )
            asian = fetch_asian(conn, match.fixture_id, mode, analysis_time)
            euro = fetch_eu(conn, match.fixture_id, analysis_time)
            sup = fetch_sup(conn, match)
            result = analyze(match, mode, analysis_time, asian, euro, sup)
            apply_action_filters(
                result, only_giving=only_giving, min_confidence=min_conf
            )
            if args.only_actionable and not result.actionable:
                continue
            rows.append(result)
            if args.write:
                write_outputs(result, args.out_dir)
        for result in rows:
            _print_result(result)
        print(f"\n{len(rows)} / {len(fixtures)} fixtures shown")
        return 0
    finally:
        conn.close()


def _build_ingest_argv(args, fixture_ids: list[int]) -> list[str]:
    argv = ["--ids", ",".join(str(x) for x in fixture_ids)]
    if getattr(args, "import_dry_run", False):
        argv.append("--dry-run")
    if getattr(args, "database", None):
        argv += ["--database", args.database]
    argv += ["--eu-companies", args.eu_companies]
    argv += ["--timeout", str(args.timeout)]
    argv += ["--retries", str(args.retries)]
    argv += ["--log-level", args.log_level]
    if getattr(args, "no_save", False) or getattr(args, "ci", False):
        argv.append("--no-save")
    if getattr(args, "save_js", None):
        argv += ["--save-js", args.save_js]
    if getattr(args, "status_id", None) is not None:
        argv += ["--status-id", str(args.status_id)]
    if getattr(args, "openssl_bin", None):
        argv += ["--openssl-bin", args.openssl_bin]
    return argv


def _fixture_ids_from_db(
    database: str | None, date_values: list[str], league_ids: list[int] | None
) -> list[int]:
    """DB fallback when schedule discovery is unavailable (e.g. past dates)."""
    cfg = load_config(database=database)
    conn = connect(cfg)
    try:
        placeholders = ",".join(["%s"] * len(date_values))
        sql = (
            "SELECT fixture_id FROM match_list_all "
            f"WHERE DATE(match_time) IN ({placeholders})"
        )
        params: list = list(date_values)
        if league_ids:
            league_placeholders = ",".join(["%s"] * len(league_ids))
            sql += f" AND competition_id IN ({league_placeholders})"
            params.extend(league_ids)
        sql += " ORDER BY match_time"
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [int(row["fixture_id"]) for row in cur.fetchall()]
    finally:
        conn.close()


def _upcoming_fixture_ids(
    database: str | None,
    within_minutes: int,
    max_fixtures: int | None,
    league_ids=None,
) -> list[int]:
    cfg = load_config(database=database)
    conn = connect(cfg)
    try:
        matches = list_upcoming_matches(
            conn,
            within_minutes,
            limit=max_fixtures or 200,
            league_ids=league_ids,
        )
        return [m.fixture_id for m in matches]
    finally:
        conn.close()


def _discover_within_ids(args, within_minutes: int, league_ids=None) -> list[int]:
    """Crawl schedule pages for fixtures kicking off in the next N minutes."""
    from .ingest import crawl

    openssl = crawl.find_openssl(args.openssl_bin)
    return crawl.discover_upcoming_fixtures(
        openssl,
        datetime.now(),
        within_minutes,
        args.timeout,
        args.max_fixtures,
        league_ids=league_ids,
    )


def _discover_fixture_ids(args) -> list[int]:
    """Resolve --dates/--leagues to fixture ids: schedule discovery, then DB fallback."""
    from .ingest import crawl

    dates = crawl.parse_dates(args.dates)
    leagues = crawl.parse_leagues(args.leagues) if args.leagues else None
    if leagues is None and not getattr(args, "all_leagues", False):
        leagues = LEAGUE_IDS
    date_values = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates]
    try:
        openssl = crawl.find_openssl(args.openssl_bin)
        ids = crawl.discover_fixtures(
            openssl, dates, leagues, args.max_fixtures, args.timeout
        )
        if ids:
            print(f"discovered {len(ids)} fixture(s) from schedule")
            return ids
        print("[warn] schedule discovery returned no fixtures; falling back to DB")
    except Exception as exc:  # noqa: BLE001 - fall back to DB
        print(f"[warn] schedule discovery failed: {exc}", file=sys.stderr)
    try:
        ids = _fixture_ids_from_db(
            args.database, date_values, sorted(leagues) if leagues else None
        )
        if args.max_fixtures:
            ids = ids[: args.max_fixtures]
        print(f"DB fallback: {len(ids)} fixture(s)")
        return ids
    except Exception as exc:  # noqa: BLE001
        print(f"[error] DB fallback failed: {exc}", file=sys.stderr)
        return []


def cmd_run(args) -> int:
    """One command: import titan007 odds, then run the rule engine."""
    if args.within_minutes is not None:
        from .ingest import crawl

        if args.all_leagues:
            league_ids = None
        elif args.leagues:
            league_ids = crawl.parse_leagues(args.leagues)
        else:
            league_ids = LEAGUE_IDS
        db_ids = _upcoming_fixture_ids(
            args.database,
            args.within_minutes,
            args.max_fixtures,
            league_ids=league_ids,
        )
        schedule_ids: list[int] = []
        if not args.skip_import:
            try:
                schedule_ids = _discover_within_ids(
                    args, args.within_minutes, league_ids=league_ids
                )
                if schedule_ids:
                    print(
                        f"schedule discovered {len(schedule_ids)} fixture(s) "
                        f"within {args.within_minutes} min"
                    )
            except Exception as exc:  # noqa: BLE001 - DB fallback remains
                print(
                    f"[warn] schedule discovery failed: {exc}",
                    file=sys.stderr,
                )
        fixture_ids = []
        seen: set[int] = set()
        for fixture_id in db_ids + schedule_ids:
            if fixture_id not in seen:
                seen.add(fixture_id)
                fixture_ids.append(fixture_id)
        if args.max_fixtures:
            fixture_ids = fixture_ids[: args.max_fixtures]
        if not fixture_ids:
            print(f"no fixtures kicking off within {args.within_minutes} minutes")
            return 0
        league_label = "all" if league_ids is None else str(len(league_ids))
        print(
            f"within {args.within_minutes} min: {len(fixture_ids)} fixture(s) "
            f"(db={len(db_ids)}, schedule={len(schedule_ids)}, leagues={league_label})"
            + (" -> refresh all" if not args.skip_import else " -> db only")
        )
    elif args.leagues and not args.dates:
        print("[error] --leagues requires --dates", file=sys.stderr)
        return 2
    elif args.dates:
        fixture_ids = _discover_fixture_ids(args)
        if not fixture_ids:
            print("[error] no fixtures found for the given dates/leagues", file=sys.stderr)
            return 2
    else:
        try:
            fixture_ids = _parse_ids(args.ids) if args.ids else [args.fixture_id]
        except ValueError as exc:
            print(f"[error] {exc}", file=sys.stderr)
            return 2

    import_rc = 0
    if not args.skip_import:
        from .ingest.all import main as ingest_main

        import_rc = ingest_main(_build_ingest_argv(args, fixture_ids))
        if import_rc != 0:
            print(f"[warn] import exited with code {import_rc}", file=sys.stderr)
            if args.raise_on_import_error:
                return import_rc

    if args.within_minutes is not None and not args.skip_import:
        refreshed = _upcoming_fixture_ids(
            args.database,
            args.within_minutes,
            args.max_fixtures,
            league_ids=league_ids,
        )
        if refreshed:
            fixture_ids = refreshed

    if args.skip_analysis:
        return 0 if import_rc == 0 else 1

    cfg = load_config(database=args.database)
    conn = connect(cfg)
    base_time = _parse_time(args.as_of)
    only_giving, min_conf = _action_filters(args)
    decision_minutes = _decision_minutes_for(args)
    min_asian = args.min_asian_companies if args.within_minutes is not None else None
    min_eu = args.min_eu_companies if args.within_minutes is not None else None
    write_reports = not (args.json_only or args.no_report or args.ci)
    results = []
    failures: list[tuple[int, str]] = []
    skipped = 0
    try:
        for fixture_id in fixture_ids:
            try:
                result, (html_path, json_path) = _analyze_one(
                    conn,
                    fixture_id,
                    base_time,
                    decision_minutes,
                    args.mode,
                    args.out_dir,
                    write=write_reports,
                    only_giving=only_giving,
                    min_confidence=min_conf,
                    min_asian_companies=min_asian,
                    min_eu_companies=min_eu,
                )
                results.append(result)
                if not args.json_only:
                    _print_result(result)
                    if html_path:
                        print(f"    html: {html_path}")
                        print(f"    json: {json_path}")
            except InsufficientOdds as exc:
                skipped += 1
                print(f"[skip] fixture {fixture_id}: {exc}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001 - one fixture must not stop the batch
                failures.append((fixture_id, str(exc)))
                print(f"[error] fixture {fixture_id}: {exc}", file=sys.stderr)
    finally:
        conn.close()

    actionable_results = [r for r in results if r.actionable]
    rows = [result_to_tracker_row(r) for r in actionable_results]
    tracked_rows = len(actionable_results)
    tracked_added = 0
    tracked_updated = 0
    if not args.no_track:
        try:
            tracked_added, tracked_updated = append_tracker_rows(args.excel, rows)
        except OSError as exc:
            print(
                f"[warn] CSV 写入失败（可能文件被占用）: {exc}",
                file=sys.stderr,
            )

    if not args.no_email and actionable_results:
        email_cfg = email_config_from_env(args)
        email_out = sys.stderr if args.json_only else sys.stdout
        if email_cfg is None:
            print(
                "[warn] email skipped: SMTP_HOST / MAIL_FROM / MAIL_TO not configured",
                file=sys.stderr,
            )
        else:
            try:
                attachment = (
                    f"actionable_{datetime.now():%Y-%m-%d_%H%M}.csv",
                    build_actionable_csv(rows),
                )
                sent = send_actionable_email(
                    results, email_cfg, attachment=attachment
                )
                print(
                    f"[email] sent {sent} actionable fixture(s) to "
                    f"{', '.join(email_cfg.mail_to)}",
                    file=email_out,
                )
            except Exception as exc:  # noqa: BLE001 - email must not kill the scan
                print(f"[warn] email send failed: {exc}", file=sys.stderr)

    if args.json_only:
        import json

        payload = (
            results[0].to_dict()
            if len(results) == 1
            else [r.to_dict() for r in results]
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if not args.no_track:
            print(
                f"[track] actionable={tracked_rows} added={tracked_added} "
                f"updated={tracked_updated} | {args.excel}",
                file=sys.stderr,
            )
    else:
        print(
            f"\nanalyzed {len(results)}/{len(fixture_ids)}"
            + (f", skipped={skipped}" if skipped else "")
            + (f", failures={len(failures)}" if failures else "")
        )
        if not args.no_track:
            print(
                f"tracked: actionable={tracked_rows} added={tracked_added} "
                f"updated={tracked_updated} | {args.excel}"
            )
    return (
        1
        if failures or (import_rc != 0 and args.within_minutes is None)
        else 0
    )


def cmd_track(args) -> int:
    """Analyze fixtures and append actionable gate bets to a CSV tracker."""
    if args.leagues and not args.dates:
        print("[error] --leagues requires --dates", file=sys.stderr)
        return 2
    if args.dates:
        if not args.leagues:
            print("[error] --dates requires --leagues", file=sys.stderr)
            return 2
        fixture_ids = _discover_fixture_ids(args)
        if not fixture_ids:
            print("[error] no fixtures found for the given dates/leagues", file=sys.stderr)
            return 2
    else:
        try:
            fixture_ids = _parse_ids(args.ids) if args.ids else [args.fixture_id]
        except ValueError as exc:
            print(f"[error] {exc}", file=sys.stderr)
            return 2

    cfg = load_config(database=args.database)
    conn = connect(cfg)
    base_time = _parse_time(args.as_of)
    decision_minutes = None if base_time is not None else args.decision_minutes
    only_giving = not args.all_sides
    rows = []
    failures = 0
    actionable = 0
    try:
        for fixture_id in fixture_ids:
            try:
                result, _ = _analyze_one(
                    conn,
                    fixture_id,
                    base_time,
                    decision_minutes,
                    "auto",
                    args.excel,
                    write=False,
                    only_giving=only_giving,
                    min_confidence=args.min_confidence,
                )
                if result.actionable:
                    actionable += 1
                if result.actionable or args.include_non_actionable:
                    rows.append(result_to_tracker_row(result))
                    _print_result(result)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"[error] fixture {fixture_id}: {exc}", file=sys.stderr)
    finally:
        conn.close()

    try:
        added, updated = append_tracker_rows(args.excel, rows)
    except OSError as exc:
        added = updated = 0
        print(
            f"[warn] CSV 写入失败（可能文件被占用）: {exc}",
            file=sys.stderr,
        )
    print(
        f"\nanalyzed {len(fixture_ids)} | actionable={actionable} | "
        f"tracker added={added} updated={updated} | {args.excel}"
    )
    return 1 if failures else 0


def cmd_track_update(args) -> int:
    cfg = load_config(database=args.database)
    conn = connect(cfg)
    try:
        updated, skipped, errors = update_tracking_results(conn, args.excel)
    finally:
        conn.close()
    print(
        f"track-update: updated={updated} skipped={skipped} errors={errors} | {args.excel}"
    )
    return 1 if errors else 0


def cmd_backtest(args) -> int:
    start = args.start or (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    end = args.end or datetime.now().strftime("%Y-%m-%d")
    fixture_ids = _parse_ids(args.fixture_ids) if args.fixture_ids else None
    cfg = load_config(database=args.database)
    conn = connect(cfg)
    try:
        records = run_backtest(
            conn,
            start_date=start,
            end_date=end,
            decision_minutes=args.decision_minutes,
            min_age_hours=args.min_age_hours,
            limit=args.limit,
            league=args.league,
            fixture_ids=fixture_ids,
            min_confidence=args.min_confidence,
            progress=lambda i, n: print(f"  analyzed {i}/{n}...", flush=True),
        )
        summary = summarize(
            records,
            period={"from": start, "to": end},
            decision_minutes=args.decision_minutes,
            min_age_hours=args.min_age_hours,
            min_confidence=args.min_confidence,
        )
        csv_path = write_csv(records, args.out)
        summary_path = write_summary(summary, args.summary)
        print_summary(summary)
        print(f"\nCSV: {csv_path}\nSummary: {summary_path}")
        return 0 if summary["errors"] == 0 else 1
    finally:
        conn.close()


def cmd_walkforward(args) -> int:
    if args.input:
        rows = load_walkforward_csv(args.input)
        source = str(args.input)
    else:
        start = args.start or (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        end = args.end or datetime.now().strftime("%Y-%m-%d")
        cfg = load_config(database=args.database)
        conn = connect(cfg)
        try:
            records = run_backtest(
                conn,
                start_date=start,
                end_date=end,
                decision_minutes=args.decision_minutes,
                min_age_hours=args.min_age_hours,
                limit=args.limit,
                min_confidence=45,
                progress=lambda i, n: print(f"  analyzed {i}/{n}...", flush=True),
            )
        finally:
            conn.close()
        rows = [r.to_row() for r in records if r.bet == 1]
        source = f"{start}..{end} T-{args.decision_minutes}"

    summary = build_walkforward_summary(rows, source=source)
    print_walkforward_summary(summary)
    json_path = write_walkforward_json(summary, args.out)
    report_path = write_walkforward_markdown(summary, args.report)
    print(f"\nJSON: {json_path}\nReport: {report_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 1 deterministic odds-trajectory rule engine",
        epilog=(
            "Import odds: python -m rule_engine import --ids 3010473\n"
            "Ingest help: python -m rule_engine.ingest --help"
        ),
    )
    parser.add_argument("--database", help="override MYSQL_DB")
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="analyze one or more fixtures")
    id_group = p_analyze.add_mutually_exclusive_group(required=True)
    id_group.add_argument("--fixture-id", type=int, help="single fixture id")
    id_group.add_argument(
        "--ids",
        nargs="+",
        help="multiple fixture ids: '--ids 3068888,3069034' or '--ids 3068888 3069034'",
    )
    p_analyze.add_argument("--mode", choices=("auto", "pre", "inplay"), default="auto")
    p_analyze.add_argument(
        "--only-giving",
        action="store_true",
        help="only bet the team giving the handicap (side_line < 0)",
    )
    p_analyze.add_argument(
        "--min-confidence", type=int, help="suppress bets below this confidence"
    )
    p_analyze.add_argument(
        "--experimental-default",
        action="store_true",
        help="Phase 3 gate: --only-giving + --min-confidence 50 + T-60 when no time is given",
    )
    analyze_time = p_analyze.add_mutually_exclusive_group()
    analyze_time.add_argument("--as-of", help="fixed analysis time, YYYY-MM-DD HH:MM[:SS]")
    analyze_time.add_argument(
        "--decision-minutes",
        type=int,
        help="per-match analysis time = kickoff - N minutes; implies pre mode",
    )
    p_analyze.add_argument("--out-dir", default="out")
    p_analyze.add_argument("--json-only", action="store_true")
    p_analyze.set_defaults(func=cmd_analyze)

    p_scan = sub.add_parser("scan", help="scan all fixtures on a date")
    p_scan.add_argument("--date", required=True, help="YYYY-MM-DD")
    p_scan.add_argument("--limit", type=int, default=200)
    p_scan.add_argument("--mode", choices=("auto", "pre", "inplay"), default="auto")
    p_scan.add_argument(
        "--only-giving",
        action="store_true",
        help="only bet the team giving the handicap (side_line < 0)",
    )
    p_scan.add_argument(
        "--min-confidence", type=int, help="suppress bets below this confidence"
    )
    p_scan.add_argument(
        "--experimental-default",
        action="store_true",
        help="Phase 3 gate: --only-giving + --min-confidence 50 + T-60 when no time is given",
    )
    scan_time = p_scan.add_mutually_exclusive_group()
    scan_time.add_argument("--as-of", help="fixed analysis time, YYYY-MM-DD HH:MM[:SS]")
    scan_time.add_argument(
        "--decision-minutes",
        type=int,
        help="per-match analysis time = kickoff - N minutes; implies pre mode",
    )
    p_scan.add_argument("--only-actionable", action="store_true")
    p_scan.add_argument("--write", action="store_true", help="write HTML/JSON per fixture")
    p_scan.add_argument("--out-dir", default="out")
    p_scan.set_defaults(func=cmd_scan)

    p_run = sub.add_parser(
        "run",
        help="import odds, analyze all fixtures, and append actionable bets to CSV",
    )
    run_ids = p_run.add_mutually_exclusive_group(required=True)
    run_ids.add_argument("--fixture-id", type=int, help="single fixture id")
    run_ids.add_argument(
        "--ids",
        nargs="+",
        help="multiple fixture ids: '--ids 3010473,3073201' or '--ids 3010473 3073201'",
    )
    run_ids.add_argument(
        "--dates",
        help="schedule dates, comma-separated: 2026-11-05,2026-11-06 (requires --leagues)",
    )
    run_ids.add_argument(
        "--within-minutes",
        type=int,
        help="all leagues: matches kicking off within N minutes; insufficient odds are skipped",
    )
    p_run.add_argument(
        "--leagues",
        help="league/SclassID values, comma-separated: 192,103 (used with --dates/--within-minutes)",
    )
    p_run.add_argument(
        "--all-leagues",
        action="store_true",
        help="disable the hard-coded league whitelist",
    )
    p_run.add_argument("--max-fixtures", type=int)
    p_run.add_argument(
        "--min-asian-companies",
        type=int,
        default=2,
        help="within-minutes mode: skip if core Asian companies < N (default 2)",
    )
    p_run.add_argument(
        "--min-eu-companies",
        type=int,
        default=2,
        help="within-minutes mode: skip if EU companies < N (default 2)",
    )
    p_run.add_argument("--mode", choices=("auto", "pre", "inplay"), default="auto")
    p_run.add_argument(
        "--only-giving",
        action="store_true",
        help="only bet the team giving the handicap (side_line < 0)",
    )
    p_run.add_argument(
        "--min-confidence", type=int, help="suppress bets below this confidence"
    )
    p_run.add_argument(
        "--experimental-default",
        action="store_true",
        help="Phase 3 gate: --only-giving + --min-confidence 50 + T-60 when no time is given",
    )
    run_time = p_run.add_mutually_exclusive_group()
    run_time.add_argument("--as-of", help="fixed analysis time, YYYY-MM-DD HH:MM[:SS]")
    run_time.add_argument(
        "--decision-minutes",
        type=int,
        help="per-match analysis time = kickoff - N minutes; implies pre mode",
    )
    p_run.add_argument("--out-dir", default="out")
    p_run.add_argument("--json-only", action="store_true")
    p_run.add_argument(
        "--excel", default=DEFAULT_TRACKER_PATH,
        help=f"CSV paper-tracking file (default: {DEFAULT_TRACKER_PATH})",
    )
    p_run.add_argument(
        "--no-track", action="store_true",
        help="do not append actionable bets to the CSV tracker",
    )
    p_run.add_argument(
        "--no-report", action="store_true",
        help="do not write out/ HTML/JSON reports",
    )
    p_run.add_argument(
        "--ci", action="store_true",
        help="CI mode: --no-report + --no-save (no out/ or data/ files)",
    )
    p_run.add_argument(
        "--email-to", help="comma-separated recipients (default: env MAIL_TO)"
    )
    p_run.add_argument(
        "--mail-from", help="sender address (default: env MAIL_FROM or SMTP_USER)"
    )
    p_run.add_argument("--smtp-host", help="SMTP host (default: env SMTP_HOST)")
    p_run.add_argument(
        "--smtp-port", type=int, help="SMTP port (default: env SMTP_PORT or 587)"
    )
    p_run.add_argument("--smtp-user", help="SMTP user (default: env SMTP_USER)")
    p_run.add_argument(
        "--smtp-password",
        help="SMTP password (default: env SMTP_PASSWORD; prefer env in CI)",
    )
    p_run.add_argument(
        "--smtp-ssl", action="store_true", help="use SMTP_SSL (usually port 465)"
    )
    p_run.add_argument(
        "--smtp-no-tls", action="store_true", help="disable STARTTLS"
    )
    p_run.add_argument("--no-email", action="store_true", help="do not send email")
    p_run.add_argument("--skip-import", action="store_true")
    p_run.add_argument("--skip-analysis", action="store_true")
    p_run.add_argument("--raise-on-import-error", action="store_true")
    p_run.add_argument("--import-dry-run", action="store_true")
    p_run.add_argument("--database", help="override MYSQL_DB")
    p_run.add_argument("--eu-companies", default="2,3,5,7,9,22")
    p_run.add_argument("--timeout", type=int, default=60)
    p_run.add_argument("--retries", type=int, default=2)
    p_run.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    p_run.add_argument("--status-id", type=int, default=1)
    p_run.add_argument("--openssl-bin")
    p_run.add_argument("--no-save", action="store_true")
    p_run.add_argument("--save-js", default="data")
    p_run.set_defaults(func=cmd_run)

    p_bt = sub.add_parser("backtest", help="backtest rules against historical scores")
    p_bt.add_argument("--from", dest="start", help="start date, YYYY-MM-DD")
    p_bt.add_argument("--to", dest="end", help="end date, YYYY-MM-DD")
    p_bt.add_argument(
        "--decision-minutes",
        type=int,
        default=60,
        help="analysis time = kickoff - N minutes (default: 60)",
    )
    p_bt.add_argument(
        "--min-age-hours",
        type=int,
        default=3,
        help="only matches kicked off at least N hours ago (default: 3)",
    )
    p_bt.add_argument("--limit", type=int, default=500)
    p_bt.add_argument(
        "--min-confidence",
        type=int,
        default=45,
        help="only bet when rule confidence >= N (default: 45)",
    )
    p_bt.add_argument("--league", help="league name substring filter")
    p_bt.add_argument("--fixture-ids", nargs="+", help="optional fixture id filter")
    p_bt.add_argument("--out", default="out/backtest.csv")
    p_bt.add_argument("--summary", default="out/backtest_summary.json")
    p_bt.set_defaults(func=cmd_backtest)

    p_track = sub.add_parser(
        "track", help="paper-track actionable experimental-gate bets into CSV"
    )
    track_ids = p_track.add_mutually_exclusive_group(required=True)
    track_ids.add_argument("--fixture-id", type=int)
    track_ids.add_argument("--ids", nargs="+")
    track_ids.add_argument(
        "--dates", help="schedule dates, comma-separated; requires --leagues"
    )
    p_track.add_argument("--leagues")
    p_track.add_argument("--all-leagues", action="store_true")
    p_track.add_argument("--max-fixtures", type=int)
    p_track.add_argument("--excel", default=DEFAULT_TRACKER_PATH)
    track_time = p_track.add_mutually_exclusive_group()
    track_time.add_argument("--as-of", help="fixed analysis time")
    track_time.add_argument(
        "--decision-minutes", type=int, default=60, help="T-N decision time (default 60)"
    )
    p_track.add_argument("--min-confidence", type=int, default=50)
    p_track.add_argument(
        "--all-sides", action="store_true", help="disable the only-giving gate"
    )
    p_track.add_argument("--include-non-actionable", action="store_true")
    p_track.add_argument("--database")
    p_track.add_argument("--openssl-bin")
    p_track.add_argument("--timeout", type=int, default=60)
    p_track.set_defaults(func=cmd_track)

    p_upd = sub.add_parser(
        "track-update", help="fill result/profit/CLV in the CSV tracker"
    )
    p_upd.add_argument("--excel", default=DEFAULT_TRACKER_PATH)
    p_upd.add_argument("--database")
    p_upd.set_defaults(func=cmd_track_update)

    p_wf = sub.add_parser(
        "walkforward",
        help="Phase 3: monthly walk-forward validation of rule variants",
    )
    p_wf.add_argument("--input", help="existing backtest CSV (conf>=45 bets)")
    p_wf.add_argument("--from", dest="start", help="start date, YYYY-MM-DD")
    p_wf.add_argument("--to", dest="end", help="end date, YYYY-MM-DD")
    p_wf.add_argument("--decision-minutes", type=int, default=60)
    p_wf.add_argument("--min-age-hours", type=int, default=3)
    p_wf.add_argument("--limit", type=int, default=2000)
    p_wf.add_argument("--database", help="override MYSQL_DB")
    p_wf.add_argument("--out", default="out/phase3_walkforward.json")
    p_wf.add_argument("--report", default="out/phase3_walkforward.md")
    p_wf.set_defaults(func=cmd_walkforward)

    # Help-only stub; `main()` intercepts `import` and forwards raw args
    # to rule_engine.ingest without invoking this subparser.
    sub.add_parser(
        "import",
        help="import titan007 odds (use: python -m rule_engine import --ids ...)",
    )
    return parser


def main() -> int:
    _utf8_stdout()
    # `import` forwards raw args to the self-contained ingest CLI.
    if len(sys.argv) >= 2 and sys.argv[1] == "import":
        from .ingest.all import main as ingest_main

        return ingest_main(sys.argv[2:])
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
