from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .cli import InsufficientOdds, _analyze_one
from .config import load_config
from .db import connect
from .ingest import crawl
from .ingest.all import main as ingest_main
from .leagues import LEAGUE_IDS
from .notify import email_config_from_env, send_actionable_email
from .tracker import append_rows, build_actionable_workbook, result_to_tracker_row

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TRACKING_DIR = ROOT / "tracking"
CACHE_PATH = DATA_DIR / "schedule_cache.json"
STATE_PATH = DATA_DIR / "scheduler_state.json"
EXCEL_PATH = TRACKING_DIR / "paper_tracking.xlsx"

TZ = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Shanghai"))
REFRESH_HOURS = (11, 16)  # Beijing time: refresh the schedule cache after 11:00 / 16:00
WINDOW_MINUTES = int(os.environ.get("SCHEDULE_WINDOW_MINUTES", "60"))
ANALYSIS_MINUTES = int(os.environ.get("ANALYSIS_DECISION_MINUTES", "60"))
MIN_CONFIDENCE = int(os.environ.get("SCHEDULE_MIN_CONFIDENCE", "50"))
MIN_ASIAN = int(os.environ.get("SCHEDULE_MIN_ASIAN", "2"))
MIN_EU = int(os.environ.get("SCHEDULE_MIN_EU", "2"))


def now_local() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def last_refresh_point(now: datetime) -> datetime:
    for hour in sorted(REFRESH_HOURS, reverse=True):
        point = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if point <= now:
            return point
    yesterday = now - timedelta(days=1)
    return yesterday.replace(
        hour=max(REFRESH_HOURS), minute=0, second=0, microsecond=0
    )


def _parse_cached_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(TZ).replace(tzinfo=None)
    return parsed


def load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def cache_is_valid(cache: dict, now: datetime) -> bool:
    fetched = _parse_cached_time(cache.get("fetched_at"))
    return bool(fetched and fetched >= last_refresh_point(now))


def collect_schedule(now: datetime) -> list[dict]:
    """Fetch today/tomorrow fixtures from the schedule pages and cache them."""
    openssl = crawl.find_openssl(None)
    # D-1 is required for fixtures that kick off just after midnight.
    dates = [
        (now.date() - timedelta(days=1)).strftime("%Y%m%d"),
        now.date().strftime("%Y%m%d"),
        (now.date() + timedelta(days=1)).strftime("%Y%m%d"),
    ]
    matches: dict[int, dict] = {}
    for yyyymmdd in dates:
        headers, body = crawl.fetch_url(
            openssl,
            crawl.SCHEDULE_HOST,
            crawl.SCHEDULE_PATH.format(yyyymmdd=yyyymmdd),
            timeout=60,
        )
        text = crawl.decode_html(body)
        rows = list(crawl._iter_schedule_rows_full(text))
        print(f"  schedule {yyyymmdd}: {len(rows)} row(s)")
        for sid, league_raw, row_text in rows:
            if not sid.isdigit():
                continue
            prefix = league_raw.split(",", 1)[0].strip()
            league_id = int(prefix) if prefix.isdigit() else None
            if league_id not in LEAGUE_IDS:
                continue
            kickoff = crawl._schedule_kickoff(row_text, yyyymmdd)
            if kickoff is None:
                continue
            # Keep today + tomorrow; drop long-past rows.
            if kickoff < now - timedelta(hours=2):
                continue
            if kickoff > now + timedelta(hours=40):
                continue
            fixture_id = int(sid)
            matches[fixture_id] = {
                "fixture_id": fixture_id,
                "league_id": league_id,
                "kickoff": _iso(kickoff),
                "schedule_page": yyyymmdd,
            }
    result = sorted(matches.values(), key=lambda m: m["kickoff"])
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(
            {"fetched_at": _iso(now), "matches": result},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"  schedule cache refreshed: {len(result)} fixture(s)")
    return result


def load_or_refresh_schedule(now: datetime) -> tuple[list[dict], bool]:
    """Return ``(matches, refreshed)``.

    ``refreshed`` is True only when the cache was stale and a crawl succeeded,
    i.e. the first run after 11:00 / 16:00.
    """
    cache = load_cache()
    if cache_is_valid(cache, now):
        matches = cache.get("matches", [])
        print(
            f"  schedule cache: {len(matches)} fixture(s), "
            f"fetched_at={cache.get('fetched_at')}"
        )
        return matches, False
    print("  schedule cache stale/missing -> refresh")
    try:
        return collect_schedule(now), True
    except Exception as exc:  # noqa: BLE001 - keep serving the stale cache
        print(f"  [warn] schedule refresh failed: {exc}")
        cached = cache.get("matches", [])
        if cached:
            print(f"  using stale cache: {len(cached)} fixture(s)")
        return cached, False


def settle_tracker(now: datetime) -> None:
    """Backfill result/profit/CLV whenever the schedule cache is refreshed.

    Settlement is intentionally tied to the 11:00 / 16:00 refresh runs instead
    of every 30-minute poll, so the live DB import cost is paid twice a day.
    """
    from .settle import run as settle_run

    print("  schedule refreshed -> settle finished tracker rows")
    try:
        settle_run(now)
    except Exception as exc:  # noqa: BLE001 - settlement must not stop the scan
        print(f"  [warn] settlement failed: {exc}")


def load_state(now: datetime) -> dict:
    today = now.strftime("%Y-%m-%d")
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            state = {}
        if state.get("date") == today:
            return state
    return {"date": today, "analyzed": []}


def save_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def due_matches(matches: list[dict], done: set[int], now: datetime) -> list[dict]:
    due = []
    for match in matches:
        try:
            kickoff = datetime.fromisoformat(match["kickoff"])
        except (KeyError, ValueError):
            continue
        minutes = (kickoff - now).total_seconds() / 60
        if 0 <= minutes <= WINDOW_MINUTES and match["fixture_id"] not in done:
            due.append({**match, "minutes_to_kickoff": minutes})
    return due


def append_actionable_json(results, now: datetime) -> Path:
    TRACKING_DIR.mkdir(parents=True, exist_ok=True)
    path = TRACKING_DIR / f"actionable_{now.strftime('%Y-%m-%d')}.json"
    existing = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = []
    keys = {
        (item.get("fixture_id"), item.get("analysis_at"))
        for item in existing
    }
    for result in results:
        row = result_to_tracker_row(result)
        key = (row["fixture_id"], row["analysis_at"])
        if key not in keys:
            existing.append(row)
            keys.add(key)
    path.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def run_once(now: datetime) -> int:
    state = load_state(now)
    done = {int(x) for x in state.get("analyzed", [])}
    matches, refreshed = load_or_refresh_schedule(now)
    if refreshed:
        settle_tracker(now)
    due = due_matches(matches, done, now)
    if not due:
        print(
            f"[{now:%Y-%m-%d %H:%M}] no due fixtures "
            f"(cache={len(matches)}, today analyzed={len(done)})"
        )
        save_state(state)
        return 0

    print(f"[{now:%Y-%m-%d %H:%M}] due {len(due)} fixture(s):")
    for match in due:
        print(
            f"  {match['fixture_id']}  league={match['league_id']}  "
            f"kickoff={match['kickoff']}  "
            f"T-{match['minutes_to_kickoff']:.0f}min"
        )

    ids = [str(match["fixture_id"]) for match in due]
    import_rc = ingest_main(
        ["--ids", ",".join(ids), "--no-save", "--log-level", "WARNING"]
    )
    if import_rc != 0:
        print(f"  [warn] import exited with code {import_rc}")

    cfg = load_config()
    conn = connect(cfg)
    results = []
    analyzed_ids = set()
    skipped = 0
    try:
        for match in due:
            fixture_id = int(match["fixture_id"])
            try:
                result, _ = _analyze_one(
                    conn,
                    fixture_id,
                    None,
                    ANALYSIS_MINUTES,
                    "auto",
                    "out",
                    write=False,
                    only_giving=True,
                    min_confidence=MIN_CONFIDENCE,
                    min_asian_companies=MIN_ASIAN,
                    min_eu_companies=MIN_EU,
                )
                results.append(result)
                analyzed_ids.add(fixture_id)
                print(
                    f"  {fixture_id} -> {result.direction_name} "
                    f"conf={result.confidence} {result.stake} "
                    f"actionable={'yes' if result.actionable else 'no'}"
                )
            except InsufficientOdds as exc:
                skipped += 1
                analyzed_ids.add(fixture_id)
                print(f"  {fixture_id} -> skip: {exc}")
            except Exception as exc:  # noqa: BLE001 - retry next run
                print(f"  {fixture_id} -> error: {exc}")
    finally:
        conn.close()

    actionable = [result for result in results if result.actionable]
    if actionable:
        rows = [result_to_tracker_row(result) for result in actionable]
        try:
            added, updated = append_rows(EXCEL_PATH, rows)
            print(f"  tracker: added={added} updated={updated} -> {EXCEL_PATH}")
        except OSError as exc:
            print(f"  [warn] tracker write failed: {exc}")
        json_path = append_actionable_json(actionable, now)
        print(f"  actionable json -> {json_path}")

        email_cfg = email_config_from_env()
        if email_cfg is None:
            print("  [warn] email skipped: SMTP_HOST / MAIL_FROM / MAIL_TO not configured")
        else:
            try:
                attachment = (
                    f"actionable_{now.strftime('%Y-%m-%d_%H%M')}.xlsx",
                    build_actionable_workbook(rows),
                )
                sent = send_actionable_email(
                    results, email_cfg, attachment=attachment
                )
                print(
                    f"  [email] sent {sent} fixture(s) to "
                    f"{', '.join(email_cfg.mail_to)}"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  [warn] email send failed: {exc}")

    state["analyzed"] = sorted(done | analyzed_ids)
    save_state(state)
    print(
        f"  done: analyzed={len(analyzed_ids)} skipped={skipped} "
        f"actionable={len(actionable)} today_total={len(state['analyzed'])}"
    )
    return len(analyzed_ids)


def main() -> int:
    now = now_local()
    print(f"=== scheduler {now:%Y-%m-%d %H:%M:%S} Asia/Shanghai ===")
    run_once(now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
