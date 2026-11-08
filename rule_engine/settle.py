from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from .config import load_config
from .db import connect
from .ingest.all import main as ingest_main
from .scheduler import TRACKING_DIR, now_local
from .tracker import read_tracker_rows, update_tracking_results

TRACKER_PATH = TRACKING_DIR / "paper_tracking.csv"
MIN_AGE_HOURS = 3
MAX_AGE_DAYS = 3


def _number(value):
    """Return a float for numeric CSV cells, otherwise the raw string."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return float(text)
    except ValueError:
        return text


def pending_fixture_ids(
    path: Path,
    now: datetime,
    max_age_days: int = MAX_AGE_DAYS,
    max_fixtures: int = 20,
) -> list[int]:
    if not path.exists():
        return []
    ids: list[int] = []
    for row in read_tracker_rows(path):
        if str(row.get("result", "")).strip():
            continue
        try:
            kickoff = datetime.strptime(
                str(row.get("kickoff", "")).strip(), "%Y-%m-%d %H:%M"
            )
            fixture_id = int(str(row.get("fixture_id", "")).strip())
        except (TypeError, ValueError):
            continue
        if kickoff > now - timedelta(hours=MIN_AGE_HOURS):
            continue
        if kickoff < now - timedelta(days=max_age_days):
            continue
        if fixture_id not in ids:
            ids.append(fixture_id)
        if len(ids) >= max_fixtures:
            break
    return ids


def sync_actionable_json(now: datetime) -> int:
    """Copy result/profit/CLV from the CSV tracker into actionable_*.json."""
    if not TRACKER_PATH.exists():
        return 0
    tracker_values = {}
    for row in read_tracker_rows(TRACKER_PATH):
        try:
            fixture_id = int(str(row.get("fixture_id", "")).strip())
            analysis_at = str(row.get("analysis_at", "")).strip()
        except (TypeError, ValueError):
            continue
        tracker_values[(fixture_id, analysis_at)] = {
            "result": row.get("result", ""),
            "profit": _number(row.get("profit")),
            "clv_line": _number(row.get("clv_line")),
            "clv_water": _number(row.get("clv_water")),
            "clv_favorable": row.get("clv_favorable", ""),
            "updated_at": row.get("updated_at", ""),
        }

    changed = 0
    for path in sorted(TRACKING_DIR.glob("actionable_*.json")):
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        dirty = False
        for entry in entries:
            key = (entry.get("fixture_id"), entry.get("analysis_at"))
            values = tracker_values.get(key)
            if not values or entry.get("result") not in (None, ""):
                continue
            entry.update(values)
            dirty = True
        if dirty:
            path.write_text(
                json.dumps(entries, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            changed += 1
    return changed


def run(now: datetime | None = None) -> int:
    now = now or now_local()
    print(f"=== settle {now:%Y-%m-%d %H:%M:%S} Asia/Shanghai ===")
    if not TRACKER_PATH.exists():
        print(f"no tracker file: {TRACKER_PATH}")
        return 0

    ids = pending_fixture_ids(TRACKER_PATH, now)
    if ids:
        print(f"pending settled fixtures: {len(ids)}")
        rc = ingest_main(
            ["--ids", ",".join(str(x) for x in ids), "--no-save", "--log-level", "WARNING"]
        )
        if rc != 0:
            print(f"[warn] import exited with code {rc}")
    else:
        print("no pending settled fixtures")

    cfg = load_config()
    conn = connect(cfg)
    try:
        updated, skipped, errors = update_tracking_results(conn, TRACKER_PATH)
    finally:
        conn.close()
    json_files = sync_actionable_json(now)
    print(
        f"settle: tracker_updated={updated} skipped={skipped} errors={errors} "
        f"json_files_updated={json_files}"
    )
    return 1 if errors else 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
