from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import load_config
from .db import connect, fetch_asian, fetch_match
from .engine import analyze
from .ingest import crawl
from .ingest.all import main as ingest_main
from .leagues import LEAGUE_IDS
from .notify import email_config_from_env, send_actionable_email
from .rules import STRATEGIES, StrategySpec
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
MAX_DECISION_MINUTES = max(spec.decision_minutes for spec in STRATEGIES.values())
# How late a strategy may still fire after its exact decision cut-off.  The
# T-10m window is inherently 0..10 minutes; the wider windows tolerate a little
# polling jitter (e.g. conflict T-120m: 105..120 minutes).
GRACE_MINUTES = int(os.environ.get("SCHEDULE_GRACE_MINUTES", "15"))
MIN_ASIAN = int(os.environ.get("SCHEDULE_MIN_ASIAN", "2"))
PRICE = os.environ.get("SCHEDULE_PRICE", "consensus").strip().lower()
if PRICE not in ("consensus", "best"):
    PRICE = "consensus"


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
    """Backfill result/profit/CLV whenever the schedule cache is refreshed."""
    from .settle import run as settle_run

    print("  schedule refreshed -> settle finished tracker rows")
    try:
        settle_run(now)
    except Exception as exc:  # noqa: BLE001 - settlement must not stop the scan
        print(f"  [warn] settlement failed: {exc}")


def _seed_recent_tracker_state(state: dict, now: datetime) -> None:
    """Recover state after a restart or midnight rollover.

    The tracker only contains triggered bets, so this prevents re-betting a
    fixture whose signal was already recorded.  Non-triggered strategies are
    not seeded; they can be re-evaluated safely within their window.
    """
    if not TRACKER_PATH.exists():
        return
    try:
        rows = read_tracker_rows(TRACKER_PATH)
    except Exception:
        return
    cutoff = now - timedelta(hours=2)
    done = state.setdefault("done", {})
    for spec in STRATEGIES.values():
        done.setdefault(spec.key, [])
    by_stage = {f"T-{spec.decision_minutes}": spec.key for spec in STRATEGIES.values()}
    for row in rows:
        try:
            analysis_at = datetime.strptime(
                str(row.get("analysis_at", "")).strip(), "%Y-%m-%d %H:%M:%S"
            )
            fixture_id = int(str(row.get("fixture_id", "")).strip())
        except (TypeError, ValueError):
            continue
        if analysis_at < cutoff:
            continue
        strategy = by_stage.get(str(row.get("stage", "")).strip())
        if strategy:
            done[strategy].append(fixture_id)
    for key, values in done.items():
        done[key] = sorted(set(int(v) for v in values))


def load_state(now: datetime) -> dict:
    today = now.strftime("%Y-%m-%d")
    loaded_done: dict = {}
    if STATE_PATH.exists():
        try:
            loaded = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            loaded = {}
        if loaded.get("date") == today and isinstance(loaded.get("done"), dict):
            loaded_done = loaded["done"]
    # Rebuild from scratch instead of reusing the loaded dict: this drops
    # legacy keys (analyzed / t30_analyzed / t60_actionable) and strategies
    # that no longer exist.
    state: dict = {"date": today, "done": {}}
    for spec in STRATEGIES.values():
        state["done"][spec.key] = sorted(
            set(int(v) for v in loaded_done.get(spec.key, []))
        )
    _seed_recent_tracker_state(state, now)
    return state


def save_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def due_pairs(
    matches: list[dict],
    state: dict,
    now: datetime,
    strategies: list[StrategySpec] | None = None,
) -> list[dict]:
    """(fixture, strategy) pairs whose decision window has been reached."""
    specs = strategies or list(STRATEGIES.values())
    done = state.get("done", {})
    out: list[dict] = []
    for match in matches:
        try:
            kickoff = datetime.fromisoformat(match["kickoff"])
            fixture_id = int(match["fixture_id"])
        except (KeyError, TypeError, ValueError):
            continue
        minutes = (kickoff - now).total_seconds() / 60
        if minutes <= 0 or minutes > MAX_DECISION_MINUTES:
            continue
        for spec in specs:
            # Only fire inside [decision - grace, decision]; never early.
            lower = max(0.0, spec.decision_minutes - GRACE_MINUTES)
            if minutes < lower or minutes > spec.decision_minutes:
                continue
            if fixture_id in set(done.get(spec.key, [])):
                continue
            out.append(
                {
                    **match,
                    "strategy": spec.key,
                    "strategy_label": spec.label,
                    "decision_minutes": spec.decision_minutes,
                    "minutes_to_kickoff": minutes,
                }
            )
    return out


def run_once(now: datetime) -> int:
    state = load_state(now)
    matches, refreshed = load_or_refresh_schedule(now)
    if refreshed:
        settle_tracker(now)

    due = due_pairs(matches, state, now)
    if not due:
        print(f"[{now:%Y-%m-%d %H:%M}] no due fixture/strategy pairs")
        save_state(state)
        return 0

    print(f"[{now:%Y-%m-%d %H:%M}] due {len(due)} pair(s):")
    for item in due:
        print(
            f"  [{item['strategy']}] {item['fixture_id']}  "
            f"league={item['league_id']}  kickoff={item['kickoff']}  "
            f"{item['minutes_to_kickoff']:.0f}min"
        )

    fixture_ids = sorted({int(item["fixture_id"]) for item in due})
    import_rc = ingest_main(
        ["--ids", ",".join(str(x) for x in fixture_ids), "--no-save", "--log-level", "WARNING"]
    )
    if import_rc != 0:
        print(f"  [warn] import exited with code {import_rc}")

    cfg = load_config()
    conn = connect(cfg)
    results = []
    skipped = 0
    try:
        for item in due:
            fixture_id = int(item["fixture_id"])
            strategy = item["strategy"]
            try:
                match = fetch_match(conn, fixture_id)
                if not match:
                    skipped += 1
                    continue
                asian = fetch_asian(conn, fixture_id, match.match_time)
                if not asian:
                    skipped += 1
                    continue
                result = analyze(
                    match,
                    strategy,
                    asian,
                    price=PRICE,
                    min_companies=MIN_ASIAN,
                )
                if result.data_quality.get("coverage_ok"):
                    state["done"].setdefault(strategy, []).append(fixture_id)
                else:
                    skipped += 1
                results.append(result)
                print(
                    f"  [{item['strategy_label']}] {fixture_id} -> "
                    f"{result.direction_name} "
                    f"{'触发' if result.triggered else '未触发'} "
                    f"{result.stake}"
                )
            except Exception as exc:  # noqa: BLE001 - retry next run
                print(f"  [{item['strategy_label']}] {fixture_id} -> error: {exc}")
    finally:
        conn.close()

    actionable = [r for r in results if r.actionable]
    if actionable:
        rows = [result_to_tracker_row(r) for r in actionable]
        try:
            added, updated = append_rows(TRACKER_PATH, rows)
            print(f"  tracker: added={added} updated={updated} -> {TRACKER_PATH}")
        except OSError as exc:
            print(f"  [warn] tracker write failed: {exc}")

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

    for key, values in state["done"].items():
        state["done"][key] = sorted(set(int(v) for v in values))
    save_state(state)
    print(
        f"  done: pairs={len(results)} skipped={skipped} "
        f"actionable={len(actionable)}"
    )
    return len(results)


def main() -> int:
    now = now_local()
    print(f"=== scheduler {now:%Y-%m-%d %H:%M:%S} Asia/Shanghai ===")
    run_once(now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
