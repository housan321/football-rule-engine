from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .db import fetch_asian, fetch_match
from .engine import AnalysisResult
from .features import build_asian_features, build_company_views
from .settlement import settle_asian

SHEET = "paper_tracking"
LEGACY_INFO_SHEET = "说明"
DEFAULT_TRACKER_PATH = "tracking/paper_tracking.xlsx"

COLUMNS: list[tuple[str, str]] = [
    ("analysis_at", "分析时间"),
    ("fixture_id", "fixture_id"),
    ("kickoff", "开赛"),
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
LABEL_BY_KEY = {key: label for key, label in COLUMNS}
KEY_BY_LABEL = {label: key for key, label in COLUMNS}

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_ALIGN = Alignment(horizontal="center", vertical="center")
WIDTHS = {
    "分析时间": 19,
    "fixture_id": 12,
    "开赛": 17,
    "联赛": 12,
    "主队": 18,
    "客队": 18,
    "模式": 8,
    "方向": 8,
    "方向名称": 18,
    "side_line": 10,
    "让球方": 9,
    "置信度": 9,
    "置信度标签": 11,
    "实验默认": 9,
    "可下注": 8,
    "操作": 9,
    "引擎盘口": 10,
    "下注水位": 10,
    "结果": 10,
    "利润": 9,
    "CLV盘口": 10,
    "CLV水位": 10,
    "CLV有利": 9,
    "备注": 34,
    "回填时间": 19,
}


def result_to_tracker_row(result: AnalysisResult) -> dict:
    asian = result.features.get("asian", {})
    water = None
    if result.direction == "home":
        water = asian.get("home_latest_water")
    elif result.direction == "away":
        water = asian.get("away_latest_water")
    return {
        "analysis_at": result.analysis_time.strftime("%Y-%m-%d %H:%M:%S"),
        "fixture_id": result.match.fixture_id,
        "kickoff": result.match.match_time.strftime("%Y-%m-%d %H:%M"),
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


def _ensure_headers(ws) -> dict[str, int]:
    existing = {
        str(ws.cell(1, col).value).strip(): col
        for col in range(1, ws.max_column + 1)
        if ws.cell(1, col).value is not None
    }
    mapping: dict[str, int] = {}
    has_headers = any(
        ws.cell(1, col).value is not None for col in range(1, ws.max_column + 1)
    )
    next_col = ws.max_column + 1 if has_headers else 1
    for key, label in COLUMNS:
        if label in existing:
            mapping[key] = existing[label]
        else:
            col = next_col
            next_col += 1
            cell = ws.cell(1, col, label)
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = HEADER_ALIGN
            mapping[key] = col
    for key, label in COLUMNS:
        col = mapping[key]
        if ws.column_dimensions[get_column_letter(col)].width is None:
            ws.column_dimensions[get_column_letter(col)].width = WIDTHS.get(
                label, 14
            )
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    return mapping


def _open_tracker(path: Path):
    if path.exists():
        wb = load_workbook(path)
        if SHEET in wb.sheetnames:
            ws = wb[SHEET]
        else:
            ws = wb.create_sheet(SHEET, 0)
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = SHEET
    if LEGACY_INFO_SHEET in wb.sheetnames:
        del wb[LEGACY_INFO_SHEET]
    mapping = _ensure_headers(ws)
    return wb, ws, mapping


def append_rows(path: str | Path, rows: list[dict]) -> tuple[int, int]:
    """Append actionable rows; update the same (analysis_at, fixture_id) in place."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb, ws, mapping = _open_tracker(out)

    existing: dict[tuple[str, int], int] = {}
    for row_idx in range(2, ws.max_row + 1):
        at = ws.cell(row_idx, mapping["analysis_at"]).value
        fid = ws.cell(row_idx, mapping["fixture_id"]).value
        if at and fid is not None:
            existing[(str(at), int(fid))] = row_idx

    added = 0
    updated = 0
    preserve = {"result", "profit", "clv_line", "clv_water", "clv_favorable", "updated_at"}
    for row in rows:
        key = (str(row["analysis_at"]), int(row["fixture_id"]))
        if key in existing:
            row_idx = existing[key]
            for field, label in COLUMNS:
                col = mapping[field]
                if field in preserve and ws.cell(row_idx, col).value not in (None, ""):
                    continue
                ws.cell(row_idx, col, row.get(field, ""))
            updated += 1
        else:
            row_idx = ws.max_row + 1
            for field, label in COLUMNS:
                ws.cell(row_idx, mapping[field], row.get(field, ""))
            existing[key] = row_idx
            added += 1

    # Basic number formats
    for row_idx in range(2, ws.max_row + 1):
        for field in ("side_line", "engine_line", "water", "clv_line", "clv_water"):
            ws.cell(row_idx, mapping[field]).number_format = "+0.00;-0.00;0.00"
        ws.cell(row_idx, mapping["profit"]).number_format = "+0.00;-0.00;0.00"
        ws.cell(row_idx, mapping["confidence"]).number_format = "0"

    wb.save(out)
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
    wb = load_workbook(out)
    if SHEET not in wb.sheetnames:
        raise ValueError(f"sheet {SHEET!r} not found in {out}")
    ws = wb[SHEET]
    mapping = _ensure_headers(ws)

    updated = 0
    skipped = 0
    errors = 0
    for row_idx in range(2, ws.max_row + 1):
        if ws.cell(row_idx, mapping["result"]).value not in (None, ""):
            continue
        try:
            fixture_id = int(ws.cell(row_idx, mapping["fixture_id"]).value)
            direction = str(ws.cell(row_idx, mapping["direction"]).value or "")
            bet_line = ws.cell(row_idx, mapping["side_line"]).value
            water = ws.cell(row_idx, mapping["water"]).value
            if direction not in ("home", "away") or bet_line is None or water is None:
                skipped += 1
                continue
            match = fetch_match(conn, fixture_id)
            if not match or match.home_score is None or match.away_score is None:
                skipped += 1
                continue
            bet_line = float(bet_line)
            water = float(water)
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
            ws.cell(row_idx, mapping["result"], settlement.outcome)
            ws.cell(row_idx, mapping["profit"], settlement.profit)
            ws.cell(row_idx, mapping["clv_line"], clv_line if clv_line is not None else "")
            ws.cell(row_idx, mapping["clv_water"], clv_water if clv_water is not None else "")
            ws.cell(
                row_idx,
                mapping["clv_favorable"],
                "" if favorable is None else ("是" if favorable else "否"),
            )
            ws.cell(
                row_idx,
                mapping["updated_at"],
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            updated += 1
        except Exception:
            errors += 1
    wb.save(out)
    return updated, skipped, errors
