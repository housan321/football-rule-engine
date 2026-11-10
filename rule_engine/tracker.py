from __future__ import annotations

import csv
import io
import os
from datetime import datetime
from pathlib import Path

from .db import fetch_asian, fetch_match
from .engine import AnalysisResult
from .features import build_asian_features, build_company_views
from .settlement import settle_asian

DEFAULT_TRACKER_PATH = "tracking/paper_tracking.csv"
# Write a BOM so Excel/WPS on Windows detect UTF-8 and show Chinese correctly.
CSV_ENCODING = "utf-8-sig"

COLUMNS: list[tuple[str, str]] = [
    ("analysis_at", "分析时间"),
    ("fixture_id", "fixture_id"),
    ("kickoff", "开赛"),
    ("stage", "口径"),
    ("league", "联赛"),
    ("home", "主队"),
    ("away", "客队"),
    ("mode", "模式"),
    ("direction", "方向"),
    ("direction_name", "方向名称"),
    ("side_line", "side_line"),
    ("is_giving", "让球方"),
    ("confidence", "置信度"),
    ("confidence_label", "置信度标签"),
    ("experimental_default", "实验默认"),
    ("actionable", "可下注"),
    ("stake", "操作"),
    ("engine_line", "引擎盘口"),
    ("water", "下注水位"),
    ("result", "结果"),
    ("profit", "利润"),
    ("clv_line", "CLV盘口"),
    ("clv_water", "CLV水位"),
    ("clv_favorable", "CLV有利"),
    ("notes", "备注"),
    ("updated_at", "回填时间"),
]
FIELD_NAMES = [key for key, _label in COLUMNS]
HEADERS = [label for _key, label in COLUMNS]
LABEL_BY_KEY = {key: label for key, label in COLUMNS}
KEY_BY_LABEL = {label: key for key, label in COLUMNS}

PRESERVE_ON_UPDATE = {
    "result",
    "profit",
    "clv_line",
    "clv_water",
    "clv_favorable",
    "updated_at",
}


def _cell(value) -> str:
    return "" if value is None else str(value)


def _row_to_values(row: dict) -> list[str]:
    return [_cell(row.get(key, "")) for key in FIELD_NAMES]


def _rows_to_text(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(HEADERS)
    for row in rows:
        writer.writerow(_row_to_values(row))
    return buf.getvalue()


def _read_rows(path: Path) -> list[dict]:
    """Read tracker rows; tolerate BOM, plain UTF-8 and GBK-edited files."""
    try:
        text = path.read_text(encoding=CSV_ENCODING)
    except UnicodeDecodeError:
        text = path.read_text(encoding="gb18030")
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        return []
    positions: dict[str, int] = {}
    for idx, name in enumerate(header):
        name = name.strip()
        key = KEY_BY_LABEL.get(name, name)
        if key in FIELD_NAMES:
            positions[key] = idx
    rows: list[dict] = []
    for values in reader:
        if not any(value.strip() for value in values):
            continue
        rows.append(
            {
                key: (values[idx] if idx < len(values) else "")
                for key, idx in positions.items()
            }
        )
    return rows


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(_rows_to_text(rows), encoding=CSV_ENCODING, newline="")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def read_tracker_rows(path: str | Path) -> list[dict]:
    """Return all tracker rows as dicts keyed by COLUMNS field names."""
    out = Path(path)
    if not out.exists():
        return []
    return _read_rows(out)


def result_to_tracker_row(result: AnalysisResult) -> dict:
    asian = result.features.get("asian", {})
    water = None
    if result.direction == "home":
        water = asian.get("home_latest_water")
    elif result.direction == "away":
        water = asian.get("away_latest_water")
    delta_minutes = round(
        (result.match.match_time - result.analysis_time).total_seconds() / 60
    )
    stage = f"T-{delta_minutes}" if delta_minutes > 0 else ""
    return {
        "analysis_at": result.analysis_time.strftime("%Y-%m-%d %H:%M:%S"),
        "fixture_id": result.match.fixture_id,
        "kickoff": result.match.match_time.strftime("%Y-%m-%d %H:%M"),
        "stage": stage,
        "league": result.match.league or "",
        "home": result.match.home,
        "away": result.match.away,
        "mode": result.mode,
        "direction": result.direction,
        "direction_name": result.direction_name,
        "side_line": result.side_line,
        "is_giving": "是" if result.is_giving else "否",
        "confidence": result.confidence,
        "confidence_label": result.confidence_label,
        "experimental_default": "是" if result.experimental_default else "否",
        "actionable": "是" if result.actionable else "否",
        "stake": result.stake,
        "engine_line": asian.get("latest_line"),
        "water": water,
        "result": "",
        "profit": "",
        "clv_line": "",
        "clv_water": "",
        "clv_favorable": "",
        "notes": "；".join(result.filter_notes),
        "updated_at": "",
    }


def build_actionable_csv(rows: list[dict]) -> bytes:
    """Build an in-memory CSV containing only the given actionable rows.

    Used for the per-run email attachment so the email never carries the whole
    cumulative paper_tracking.csv history.
    """
    return _rows_to_text(rows).encode(CSV_ENCODING)


def append_rows(path: str | Path, rows: list[dict]) -> tuple[int, int]:
    """Append actionable rows; update the same (analysis_at, fixture_id) in place."""
    out = Path(path)
    existing_rows = _read_rows(out) if out.exists() else []

    index: dict[tuple[str, int], int] = {}
    for row_idx, row in enumerate(existing_rows):
        at = str(row.get("analysis_at", "")).strip()
        fid = str(row.get("fixture_id", "")).strip()
        if at and fid:
            try:
                index[(at, int(fid))] = row_idx
            except ValueError:
                continue

    added = 0
    updated = 0
    for row in rows:
        key = (str(row["analysis_at"]), int(row["fixture_id"]))
        if key in index:
            target = existing_rows[index[key]]
            for field in FIELD_NAMES:
                if field in PRESERVE_ON_UPDATE and _cell(target.get(field, "")).strip():
                    continue
                target[field] = _cell(row.get(field, ""))
            updated += 1
        else:
            existing_rows.append(
                {field: _cell(row.get(field, "")) for field in FIELD_NAMES}
            )
            index[key] = len(existing_rows) - 1
            added += 1

    _write_rows(out, existing_rows)
    return added, updated


def _closing_quote(conn, match, direction: str):
    series = fetch_asian(conn, match.fixture_id, "pre", match.match_time)
    if not series:
        return None, None
    views = build_company_views(series, match.match_time)
    features = build_asian_features(views, "pre", match.match_time)
    if features.latest_line is None:
        return None, None
    close_line = (
        features.latest_line if direction == "home" else -features.latest_line
    )
    close_water = (
        features.home_latest_water
        if direction == "home"
        else features.away_latest_water
    )
    return close_line, close_water


def update_tracking_results(conn, path: str | Path) -> tuple[int, int, int]:
    """Fill result/profit/CLV for rows that do not have a result yet."""
    out = Path(path)
    if not out.exists():
        raise FileNotFoundError(f"tracker file not found: {out}")
    rows = _read_rows(out)

    updated = 0
    skipped = 0
    errors = 0
    for row in rows:
        if _cell(row.get("result", "")).strip():
            continue
        try:
            fixture_id_raw = _cell(row.get("fixture_id", "")).strip()
            direction = _cell(row.get("direction", ""))
            bet_line_raw = _cell(row.get("side_line", "")).strip()
            water_raw = _cell(row.get("water", "")).strip()
            if (
                direction not in ("home", "away")
                or not fixture_id_raw
                or not bet_line_raw
                or not water_raw
            ):
                skipped += 1
                continue
            fixture_id = int(fixture_id_raw)
            bet_line = float(bet_line_raw)
            water = float(water_raw)
            match = fetch_match(conn, fixture_id)
            if not match or match.home_score is None or match.away_score is None:
                skipped += 1
                continue
            settlement = settle_asian(
                bet_line,
                water,
                int(match.home_score),
                int(match.away_score),
                direction,
            )
            close_line, close_water = _closing_quote(conn, match, direction)
            clv_line = (
                round(bet_line - close_line, 4) if close_line is not None else None
            )
            clv_water = (
                round(water - close_water, 4) if close_water is not None else None
            )
            favorable = None
            if clv_line is not None or clv_water is not None:
                favorable = bool(
                    (clv_line is not None and clv_line > 1e-9)
                    or (clv_water is not None and clv_water > 1e-9)
                )
            row["result"] = settlement.outcome
            row["profit"] = settlement.profit
            row["clv_line"] = clv_line if clv_line is not None else ""
            row["clv_water"] = clv_water if clv_water is not None else ""
            row["clv_favorable"] = (
                "" if favorable is None else ("是" if favorable else "否")
            )
            row["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            updated += 1
        except Exception:
            errors += 1

    _write_rows(out, rows)
    return updated, skipped, errors
