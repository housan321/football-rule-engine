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
from .tracker import (
    append_rows,
    build_actionable_csv,
    read_tracker_rows,
    result_to_tracker_row,
)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TRACKING_DIR = ROOT / "tracking"
CACHE_PATH = DATA_DIR / "schedule_cache.json"
STATE_PATH = DATA_DIR / "scheduler_state.json"
TRACKER_PATH = TRACKING_DIR / "paper_tracking.csv"

TZ = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Shanghai"))
REFRESH_HOURS = (11, 16)  # Beijing time: refresh the schedule cache after 11:00 / 16:00
WINDOW_MINUTES = int(os.environ.get("SCHEDULE_WINDOW_MINUTES", "60"))
ANALYSIS_MINUTES = int(os.environ.get("ANALYSIS_DECISION_MINUTES", "60"))
# Second lane: 0..30 minutes before kickoff, analysed at kickoff - 30.
T30_WINDOW_MINUTES = int(os.environ.get("SCHEDULE_T30_WINDOW_MINUTES", "30"))
T30_ANALYSIS_MINUTES = int(os.environ.get("ANALYSIS_T30_MINUTES", "30"))
ENABLE_T30 = os.environ.get("SCHEDULE_ENABLE_T30", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
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
        for sid, league_raw, row_text, cells in rows:
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
            league = cells[0] if len(cells) > 0 else ""
            home = crawl.clean_team_cell(cells[3]) if len(cells) > 3 else ""
            away = crawl.clean_team_cell(cells[5]) if len(cells) > 5 else ""
            matches[fixture_id] = {
                "fixture_id": fixture_id,
                "league_id": league_id,
                "league": league,
                "home": home,
                "away": away,
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


def t30_enabled() -> bool:
    """T-30 is opt-in and only valid when its window/decision time are smaller."""
    return bool(
        ENABLE_T30
        and 0 < T30_WINDOW_MINUTES < WINDOW_MINUTES
        and 0 < T30_ANALYSIS_MINUTES < ANALYSIS_MINUTES
    )


def _seed_recent_tracker_state(state: dict, now: datetime) -> None:
    """Recover restarts and midnight crossings from the tracker CSV.

    Tracker rows are actionable bets, so a recent T-60 row can also enforce
    T-60 priority after the per-day state file has rolled over at midnight.
    """
    if not TRACKER_PATH.exists():
        return
    try:
        rows = read_tracker_rows(TRACKER_PATH)
    except Exception:
        return
    cutoff = now - timedelta(hours=2)
    t60_done = {int(x) for x in state.get("analyzed", [])}
    t30_done = {int(x) for x in state.get("t30_analyzed", [])}
    t60_actionable = {int(x) for x in state.get("t60_actionable", [])}
    for row in rows:
        try:
            kickoff = datetime.strptime(
                str(row.get("kickoff", "")).strip(), "%Y-%m-%d %H:%M"
            )
            analysis_at = datetime.strptime(
                str(row.get("analysis_at", "")).strip(), "%Y-%m-%d %H:%M:%S"
            )
            fixture_id = int(str(row.get("fixture_id", "")).strip())
        except (TypeError, ValueError):
            continue
        if analysis_at < cutoff:
            continue
        delta = (kickoff - analysis_at).total_seconds() / 60
        if abs(delta - ANALYSIS_MINUTES) <= 1:
            t60_done.add(fixture_id)
            t60_actionable.add(fixture_id)
        elif abs(delta - T30_ANALYSIS_MINUTES) <= 1:
            t30_done.add(fixture_id)
    state["analyzed"] = sorted(t60_done)
    state["t30_analyzed"] = sorted(t30_done)
    state["t60_actionable"] = sorted(t60_actionable)


def load_state(now: datetime) -> dict:
    today = now.strftime("%Y-%m-%d")
    state: dict = {"date": today, "analyzed": []}
    if STATE_PATH.exists():
        try:
            loaded = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            loaded = {}
        if loaded.get("date") == today:
            state = loaded
    state.setdefault("analyzed", [])
    state.setdefault("t30_analyzed", [])
    state.setdefault("t60_actionable", [])
    _seed_recent_tracker_state(state, now)
    return state


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


def due_lanes(
    matches: list[dict], state: dict, now: datetime
) -> tuple[list[dict], list[dict], list[dict]]:
    """Split due fixtures into T-60, T-30, and T-30 blocked by T-60 priority.

    T-60 lane: 30..60 minutes before kickoff (or 0..60 when T-30 is disabled).
    T-30 lane: 0..30 minutes before kickoff.
    A fixture with a T-60 actionable bet never gets a T-30 bet in the same day.
    """
    t60_done = {int(x) for x in state.get("analyzed", [])}
    t30_done = {int(x) for x in state.get("t30_analyzed", [])}
    t60_actionable = {int(x) for x in state.get("t60_actionable", [])}
    t60_due: list[dict] = []
    t30_due: list[dict] = []
    t30_blocked: list[dict] = []
    use_t30 = t30_enabled()
    for match in matches:
        try:
            kickoff = datetime.fromisoformat(match["kickoff"])
            fixture_id = int(match["fixture_id"])
        except (KeyError, TypeError, ValueError):
            continue
        minutes = (kickoff - now).total_seconds() / 60
        if not 0 <= minutes <= WINDOW_MINUTES:
            continue
        if use_t30 and minutes <= T30_WINDOW_MINUTES:
            if fixture_id in t30_done:
                continue
            item = {
                **match,
                "minutes_to_kickoff": minutes,
                "stage": "T-30",
                "decision_minutes": T30_ANALYSIS_MINUTES,
            }
            if fixture_id in t60_actionable:
                t30_blocked.append(item)
            else:
                t30_due.append(item)
        else:
            if fixture_id in t60_done:
                continue
            t60_due.append(
                {
                    **match,
                    "minutes_to_kickoff": minutes,
                    "stage": "T-60",
                    "decision_minutes": ANALYSIS_MINUTES,
                }
            )
    return t60_due, t30_due, t30_blocked


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
    t60_done = {int(x) for x in state.get("analyzed", [])}
    t30_done = {int(x) for x in state.get("t30_analyzed", [])}
    t60_actionable = {int(x) for x in state.get("t60_actionable", [])}
    matches, refreshed = load_or_refresh_schedule(now)
    if refreshed:
        settle_tracker(now)
    t60_due, t30_due, t30_blocked = due_lanes(matches, state, now)
    due = t60_due + t30_due
    if not due and not t30_blocked:
        print(
            f"[{now:%Y-%m-%d %H:%M}] no due fixtures "
            f"(cache={len(matches)}, today T-60={len(t60_done)}, "
            f"T-30={len(t30_done)})"
        )
        save_state(state)
        return 0

    if due:
        print(f"[{now:%Y-%m-%d %H:%M}] due {len(due)} fixture(s):")
        for match in due:
            print(
                f"  [{match['stage']}] {match['fixture_id']}  "
                f"league={match['league_id']}  kickoff={match['kickoff']}  "
                f"{match['minutes_to_kickoff']:.0f}min"
            )
    for match in t30_blocked:
        print(
            f"  [T-30] {match['fixture_id']} -> blocked: "
            f"T-60 already actionable"
        )

    t60_analyzed_ids: set[int] = set()
    t30_analyzed_ids: set[int] = set()
    t30_blocked_ids: set[int] = set()
    t60_actionable_ids: set[int] = set()
    results = []
    skipped = 0

    if due:
        ids = [str(match["fixture_id"]) for match in due]
        import_rc = ingest_main(
            ["--ids", ",".join(ids), "--no-save", "--log-level", "WARNING"]
        )
        if import_rc != 0:
            print(f"  [warn] import exited with code {import_rc}")

        cfg = load_config()
        conn = connect(cfg)
        try:
            for match in due:
                fixture_id = int(match["fixture_id"])
                stage = match["stage"]
                try:
                    result, _ = _analyze_one(
                        conn,
                        fixture_id,
                        None,
                        match["decision_minutes"],
                        "auto",
                        "out",
                        write=False,
                        only_giving=True,
                        min_confidence=MIN_CONFIDENCE,
                        min_asian_companies=MIN_ASIAN,
                        min_eu_companies=MIN_EU,
                    )
                    results.append(result)
                    if stage == "T-60":
                        t60_analyzed_ids.add(fixture_id)
                        if result.actionable:
                            t60_actionable_ids.add(fixture_id)
                    else:
                        t30_analyzed_ids.add(fixture_id)
                    print(
                        f"  [{stage}] {fixture_id} -> {result.direction_name} "
                        f"conf={result.confidence} {result.stake} "
                        f"actionable={'yes' if result.actionable else 'no'}"
                    )
                except InsufficientOdds as exc:
                    skipped += 1
                    if stage == "T-60":
                        t60_analyzed_ids.add(fixture_id)
                    else:
                        t30_analyzed_ids.add(fixture_id)
                    print(f"  [{stage}] {fixture_id} -> skip: {exc}")
                except Exception as exc:  # noqa: BLE001 - retry next run
                    print(f"  [{stage}] {fixture_id} -> error: {exc}")
        finally:
            conn.close()

    # Blocked T-30 fixtures are not analysed; remember them so they do not
    # re-enter the T-30 lane on every 30-minute poll.
    for match in t30_blocked:
        t30_blocked_ids.add(int(match["fixture_id"]))

    actionable = [result for result in results if result.actionable]
    if actionable:
        rows = [result_to_tracker_row(result) for result in actionable]
        try:
            added, updated = append_rows(TRACKER_PATH, rows)
            print(f"  tracker: added={added} updated={updated} -> {TRACKER_PATH}")
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
                    f"actionable_{now.strftime('%Y-%m-%d_%H%M')}.csv",
                    build_actionable_csv(rows),
                )
                print(
                    f"  [email] smtp {email_cfg.host}:{email_cfg.port} "
                    f"ssl={email_cfg.use_ssl} tls={email_cfg.use_tls}"
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

    state["analyzed"] = sorted(t60_done | t60_analyzed_ids)
    state["t30_analyzed"] = sorted(
        t30_done | t30_analyzed_ids | t30_blocked_ids
    )
    state["t60_actionable"] = sorted(t60_actionable | t60_actionable_ids)
    save_state(state)
    print(
        f"  done: T-60 analyzed={len(t60_analyzed_ids)} "
        f"T-30 analyzed={len(t30_analyzed_ids)} "
        f"T-30 blocked={len(t30_blocked_ids)} skipped={skipped} "
        f"actionable={len(actionable)} "
        f"today T-60={len(state['analyzed'])} T-30={len(state['t30_analyzed'])}"
    )
    return len(t60_analyzed_ids | t30_analyzed_ids)


def main() -> int:
    now = now_local()
    print(f"=== scheduler {now:%Y-%m-%d %H:%M:%S} Asia/Shanghai ===")
    run_once(now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
