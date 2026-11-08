from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from openpyxl import load_workbook

from .config import load_config
from .db import connect
from .ingest.all import main as ingest_main
from .scheduler import TRACKING_DIR, now_local
from .tracker import COLUMNS, SHEET, update_tracking_results

EXCEL_PATH = TRACKING_DIR / "paper_tracking.xlsx"
MIN_AGE_HOURS = 3
MAX_AGE_DAYS = 3


def _headers(ws) -> dict[str, int]:
    labels = {
        str(cell.value).strip(): cell.column
        for cell in ws[1]
        if cell.value is not None
    }
    return {key: labels[label] for key, label in COLUMNS if label in labels}


def pending_fixture_ids(
    path: Path,
    now: datetime,
    max_age_days: int = MAX_AGE_DAYS,
    max_fixtures: int = 20,
) -> list[int]:
    if not path.exists():
        return []
    wb = load_workbook(path, read_only=True)
    try:
        ws = wb[SHEET]
        headers = _headers(ws)
        need = {"kickoff", "fixture_id", "result"}
        if not need.issubset(headers):
            return []
        ids: list[int] = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            result = row[headers["result"] - 1]
            if result not in (None, ""):
                continue
            try:
                kickoff = datetime.strptime(
                    str(row[headers["kickoff"] - 1]), "%Y-%m-%d %H:%M"
                )
                fixture_id = int(row[headers["fixture_id"] - 1])
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
    finally:
        wb.close()


def sync_actionable_json(now: datetime) -> int:
    """Copy result/profit/CLV from Excel into actionable_*.json."""
    if not EXCEL_PATH.exists():
        return 0
    wb = load_workbook(EXCEL_PATH, read_only=True)
    try:
        ws = wb[SHEET]
        headers = _headers(ws)
        sheet_values = {}
        for row in ws.iter_rows(min_row=2, values_only=True):
            try:
                fixture_id = int(row[headers["fixture_id"] - 1])
                analysis_at = str(row[headers["analysis_at"] - 1])
            except (KeyError, TypeError, ValueError):
                continue
            sheet_values[(fixture_id, analysis_at)] = {
                "result": row[headers["result"] - 1],
                "profit": row[headers["profit"] - 1],
                "clv_line": row[headers["clv_line"] - 1],
                "clv_water": row[headers["clv_water"] - 1],
                "clv_favorable": row[headers["clv_favorable"] - 1],
                "updated_at": row[headers["updated_at"] - 1],
            }
    finally:
        wb.close()

    changed = 0
    for path in sorted(TRACKING_DIR.glob("actionable_*.json")):
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        dirty = False
        for entry in entries:
            key = (entry.get("fixture_id"), entry.get("analysis_at"))
            values = sheet_values.get(key)
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
    if not EXCEL_PATH.exists():
        print(f"no tracker file: {EXCEL_PATH}")
        return 0

    ids = pending_fixture_ids(EXCEL_PATH, now)
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
        updated, skipped, errors = update_tracking_results(conn, EXCEL_PATH)
    finally:
        conn.close()
    json_files = sync_actionable_json(now)
    print(
        f"settle: excel_updated={updated} skipped={skipped} errors={errors} "
        f"json_files_updated={json_files}"
    )
    return 1 if errors else 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
