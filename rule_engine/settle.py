from __future__ import annotations

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
    print(
        f"settle: tracker_updated={updated} skipped={skipped} errors={errors}"
    )
    return 1 if errors else 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
