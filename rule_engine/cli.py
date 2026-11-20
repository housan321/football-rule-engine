from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

from .backtest import print_summary, run_backtest, summarize, write_csv, write_summary
from .config import load_config
from .db import (
    connect,
    fetch_asian,
    fetch_match,
    list_fixtures,
    list_upcoming_matches,
)
from .engine import AnalysisResult, analyze
from .ingest import crawl
from .leagues import LEAGUE_IDS
from .notify import email_config_from_env, send_actionable_email
from .report import write_outputs
from .rules import (
    DEFAULT_STRATEGY_KEYS,
    PRICE_MODES,
    STRATEGIES,
    StrategySpec,
    resolve_strategies,
)
from .tracker import (
    DEFAULT_TRACKER_PATH,
    append_rows as append_tracker_rows,
    build_actionable_csv,
    result_to_tracker_row,
    update_tracking_results,
)
from .walkforward import (
    build_summary as build_walkforward_summary,
    load_csv as load_walkforward_csv,
    print_summary as print_walkforward_summary,
    write_json as write_walkforward_json,
    write_markdown as write_walkforward_markdown,
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


class InsufficientOdds(Exception):
    """Raised when an imminent fixture has no Asian odds history at all."""


def _resolve_price(value: str | None) -> str:
    price = (value or "consensus").strip().lower()
    if price not in PRICE_MODES:
        raise ValueError(f"--price 必须是 {PRICE_MODES} 之一")
    return price


def _resolve_specs(values) -> list[StrategySpec]:
    return resolve_strategies(values or list(DEFAULT_STRATEGY_KEYS))


def _reached_specs(
    match, strategies, now: datetime | None = None
) -> tuple[list[StrategySpec], list[StrategySpec]]:
    """Split requested strategies into (decision cut-off reached, future).

    A live one-shot analysis must not pretend that a T-10m snapshot exists
    while the match is still two hours away.
    """
    now = now or datetime.now()
    reached: list[StrategySpec] = []
    pending: list[StrategySpec] = []
    for spec in _resolve_specs(strategies):
        cutoff = match.match_time - timedelta(minutes=spec.decision_minutes)
        (reached if cutoff <= now else pending).append(spec)
    return reached, pending


def _analyze_one(
    conn,
    fixture_id: int,
    strategies,
    out_dir: str,
    write: bool,
    price: str = "consensus",
    min_asian_companies: int | None = None,
) -> tuple[list[AnalysisResult], list[tuple]]:
    match = fetch_match(conn, fixture_id)
    if not match:
        raise ValueError(
            f"fixture {fixture_id} 不在 match_list_all（数据库尚未导入）。"
            f"先运行: python -m rule_engine import --ids {fixture_id}"
        )

    asian = fetch_asian(conn, fixture_id, match.match_time)
    if not asian:
        raise InsufficientOdds(f"fixture {fixture_id} 没有亚盘历史")

    min_companies = min_asian_companies or 2
    specs, pending = _reached_specs(match, strategies)
    if pending:
        print(
            f"[pending] fixture {fixture_id}: "
            + "、".join(spec.label for spec in pending)
            + " 决策时点未到，跳过",
            file=sys.stderr,
        )
    results: list[AnalysisResult] = []
    paths: list[tuple] = []
    for spec in specs:
        result = analyze(
            match,
            spec.key,
            asian,
            price=price,
            min_companies=min_companies,
        )
        results.append(result)
        if write:
            paths.append(write_outputs(result, out_dir))
        else:
            paths.append((None, None))
    return results, paths


def _print_result(result: AnalysisResult) -> None:
    m = result.match
    signal = "触发" if result.triggered else "未触发"
    side = f"{result.side_line:+.2f}" if result.side_line is not None else "-"
    water = f"{result.water:.3f}" if result.water is not None else "-"
    print(
        f"{m.fixture_id}  {m.league or '-':<8}  {m.home} vs {m.away}  "
        f"[{result.strategy_label}] {result.analysis_time:%m-%d %H:%M}  "
        f"-> {result.direction_name}  {signal}  side_line={side} water={water}"
    )
    print(f"    {result.reason}")
    if result.triggered:
        best = (
            f"{result.best_water:.3f} @ {result.best_company}"
            if result.best_water is not None
            else "-"
        )
        print(
            f"    下注: {result.direction_name}  盘口 {side}  水位 {water} "
            f"({result.price_mode}) | best {best}"
        )


def _collect_results(
    conn,
    fixture_ids: list[int],
    strategies,
    out_dir: str,
    write: bool,
    price: str,
    min_asian_companies: int | None,
    print_results: bool,
) -> tuple[list[AnalysisResult], list[tuple[int, str]], int]:
    results: list[AnalysisResult] = []
    failures: list[tuple[int, str]] = []
    skipped = 0
    for fixture_id in fixture_ids:
        try:
            fixture_results, paths = _analyze_one(
                conn,
                fixture_id,
                strategies,
                out_dir,
                write=write,
                price=price,
                min_asian_companies=min_asian_companies,
            )
            results.extend(fixture_results)
            if print_results:
                for result, (html_path, json_path) in zip(fixture_results, paths):
                    _print_result(result)
                    if html_path:
                        print(f"    html: {html_path}")
                        print(f"    json: {json_path}")
        except InsufficientOdds as exc:
            skipped += 1
            print(f"[skip] {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - one fixture must not stop the batch
            failures.append((fixture_id, str(exc)))
            print(f"[error] fixture {fixture_id}: {exc}", file=sys.stderr)
    return results, failures, skipped


def cmd_analyze(args) -> int:
    try:
        fixture_ids = _parse_ids(args.ids) if args.ids else [args.fixture_id]
        price = _resolve_price(args.price)
        _resolve_specs(args.strategies)
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    cfg = load_config(database=args.database)
    conn = connect(cfg)
    try:
        results, failures, skipped = _collect_results(
            conn,
            fixture_ids,
            args.strategies,
            args.out_dir,
            write=not args.json_only,
            price=price,
            min_asian_companies=args.min_asian_companies,
            print_results=not args.json_only,
        )
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
            f"\nanalyzed {len(fixture_ids)} fixture(s), "
            f"{len(results)} strategy result(s)"
            + (f", skipped={skipped}" if skipped else "")
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
        price = _resolve_price(args.price)
        rows: list[AnalysisResult] = []
        for match in fixtures:
            try:
                asian = fetch_asian(conn, match.fixture_id, match.match_time)
            except Exception as exc:  # noqa: BLE001
                print(f"[error] fixture {match.fixture_id}: {exc}", file=sys.stderr)
                continue
            if not asian:
                continue
            specs, pending = _reached_specs(match, args.strategies)
            if pending:
                print(
                    f"[pending] fixture {match.fixture_id}: "
                    + "、".join(spec.label for spec in pending)
                    + " 决策时点未到，跳过",
                    file=sys.stderr,
                )
            for spec in specs:
                result = analyze(
                    match,
                    spec.key,
                    asian,
                    price=price,
                    min_companies=args.min_asian_companies,
                )
                if args.only_actionable and not result.actionable:
                    continue
                rows.append(result)
                if args.write:
                    write_outputs(result, args.out_dir)
        for result in rows:
            _print_result(result)
        print(f"\n{len(rows)} result(s) from {len(fixtures)} fixture(s)")
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
    try:
        price = _resolve_price(args.price)
        _resolve_specs(args.strategies)
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    league_ids = None
    if args.within_minutes is not None:
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
    write_reports = not (args.json_only or args.no_report or args.ci)
    try:
        results, failures, skipped = _collect_results(
            conn,
            fixture_ids,
            args.strategies,
            args.out_dir,
            write=write_reports,
            price=price,
            min_asian_companies=args.min_asian_companies,
            print_results=not args.json_only,
        )
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
            f"\nanalyzed {len(fixture_ids)} fixture(s), "
            f"{len(results)} strategy result(s)"
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
    """Analyze fixtures and append triggered bets to a CSV tracker."""
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
    rows = []
    failures = 0
    actionable = 0
    try:
        for fixture_id in fixture_ids:
            try:
                results, _ = _analyze_one(
                    conn,
                    fixture_id,
                    args.strategies,
                    args.excel,
                    write=False,
                    price=args.price,
                    min_asian_companies=args.min_asian_companies,
                )
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"[error] fixture {fixture_id}: {exc}", file=sys.stderr)
                continue
            for result in results:
                if result.actionable:
                    actionable += 1
                if result.actionable or args.include_non_actionable:
                    rows.append(result_to_tracker_row(result))
                    _print_result(result)
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
        f"\nanalyzed {len(fixture_ids)} fixture(s) | actionable={actionable} | "
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
    try:
        specs = _resolve_specs(args.strategies)
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2
    cfg = load_config(database=args.database)
    conn = connect(cfg)
    try:
        records = run_backtest(
            conn,
            start_date=start,
            end_date=end,
            strategies=[spec.key for spec in specs],
            min_age_hours=args.min_age_hours,
            limit=args.limit,
            league=args.league,
            fixture_ids=fixture_ids,
            min_companies=args.min_asian_companies,
            progress=lambda i, n: print(f"  analyzed {i}/{n}...", flush=True),
        )
        summary = summarize(
            records,
            period={"from": start, "to": end},
            strategies=[spec.key for spec in specs],
            min_age_hours=args.min_age_hours,
        )
        csv_path = write_csv(records, args.out)
        summary_path = write_summary(summary, args.summary)
        print_summary(summary)
        print(f"\nCSV: {csv_path}\nSummary: {summary_path}")
        errors = sum(
            data["errors"] for data in summary["by_strategy"].values()
        )
        return 0 if errors == 0 else 1
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
                strategies=args.strategies,
                min_age_hours=args.min_age_hours,
                limit=args.limit,
                min_companies=args.min_asian_companies,
            )
        finally:
            conn.close()
        rows = [r.to_row() for r in records if r.bet == 1]
        source = f"{start}..{end}"

    summary = build_walkforward_summary(rows, source=source)
    print_walkforward_summary(summary)
    json_path = write_walkforward_json(summary, args.out)
    report_path = write_walkforward_markdown(summary, args.report)
    print(f"\nJSON: {json_path}\nReport: {report_path}")
    return 0


def _strategy_help() -> str:
    return "、".join(f"{key}={spec.label}" for key, spec in STRATEGIES.items())


def _add_analysis_args(
    parser: argparse.ArgumentParser, with_price: bool = True
) -> None:
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=["all"],
        help=f"策略：all 或 {_strategy_help()}",
    )
    if with_price:
        parser.add_argument(
            "--price",
            choices=PRICE_MODES,
            default="consensus",
            help="下注价：consensus=决策快照平均水位，best=主盘口最高水位（默认 consensus）",
        )
    parser.add_argument(
        "--min-asian-companies",
        type=int,
        default=2,
        help="每个快照主盘口至少需要的公司数（默认 2）",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Multi-window Asian-handicap consensus rule engine",
        epilog=(
            "Import odds: python -m rule_engine import --ids 3010473\n"
            "Ingest help: python -m rule_engine.ingest --help\n"
            f"Strategies: {_strategy_help()}"
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
    _add_analysis_args(p_analyze)
    p_analyze.add_argument("--out-dir", default="out")
    p_analyze.add_argument("--json-only", action="store_true")
    p_analyze.set_defaults(func=cmd_analyze)

    p_scan = sub.add_parser("scan", help="scan all fixtures on a date")
    p_scan.add_argument("--date", required=True, help="YYYY-MM-DD")
    p_scan.add_argument("--limit", type=int, default=200)
    _add_analysis_args(p_scan)
    p_scan.add_argument("--only-actionable", action="store_true")
    p_scan.add_argument("--write", action="store_true", help="write HTML/JSON per fixture")
    p_scan.add_argument("--out-dir", default="out")
    p_scan.set_defaults(func=cmd_scan)

    p_run = sub.add_parser(
        "run",
        help="import odds, analyze all fixtures, and append triggered bets to CSV",
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
        help="all leagues: matches kicking off within N minutes",
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
    _add_analysis_args(p_run)
    p_run.add_argument("--out-dir", default="out")
    p_run.add_argument("--json-only", action="store_true")
    p_run.add_argument(
        "--excel", default=DEFAULT_TRACKER_PATH,
        help=f"CSV paper-tracking file (default: {DEFAULT_TRACKER_PATH})",
    )
    p_run.add_argument(
        "--no-track", action="store_true",
        help="do not append triggered bets to the CSV tracker",
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

    p_bt = sub.add_parser("backtest", help="backtest the three strategies")
    p_bt.add_argument("--from", dest="start", help="start date, YYYY-MM-DD")
    p_bt.add_argument("--to", dest="end", help="end date, YYYY-MM-DD")
    p_bt.add_argument(
        "--min-age-hours",
        type=int,
        default=3,
        help="only matches kicked off at least N hours ago (default: 3)",
    )
    p_bt.add_argument("--limit", type=int, default=500)
    p_bt.add_argument("--league", help="league name substring filter")
    p_bt.add_argument("--fixture-ids", nargs="+", help="optional fixture id filter")
    _add_analysis_args(p_bt, with_price=False)
    p_bt.add_argument("--out", default="out/backtest.csv")
    p_bt.add_argument("--summary", default="out/backtest_summary.json")
    p_bt.set_defaults(func=cmd_backtest)

    p_track = sub.add_parser(
        "track", help="paper-track triggered bets into CSV"
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
    _add_analysis_args(p_track)
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
        help="monthly stability of each strategy",
    )
    p_wf.add_argument("--input", help="existing backtest CSV (bet=1 rows)")
    p_wf.add_argument("--from", dest="start", help="start date, YYYY-MM-DD")
    p_wf.add_argument("--to", dest="end", help="end date, YYYY-MM-DD")
    p_wf.add_argument("--min-age-hours", type=int, default=3)
    p_wf.add_argument("--limit", type=int, default=2000)
    p_wf.add_argument("--database", help="override MYSQL_DB")
    _add_analysis_args(p_wf, with_price=False)
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
