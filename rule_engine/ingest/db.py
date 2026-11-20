#!/usr/bin/env python3
"""Shared helpers for the self-hosted football MySQL toolchain.

Every script in tools/ reads connection settings from the project .env:

    MYSQL_HOST=127.0.0.1
    MYSQL_PORT=3306
    MYSQL_USER=root
    MYSQL_PASSWORD=******
    MYSQL_DB=football

Only pymysql is required at runtime. Real environment variables take
precedence over .env, so CI/cron can override credentials without editing
the file.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

# Asian-handicap companies kept in odds_live_all / odds_live_all_raw.
# The eight used by 《规则回测分析结果.docx》: Bet365(2), 皇冠(3),
# 明陞(6), 澳门(7), 易胜博(10), Interwetten(13), 平博(22), 香港马会(23).
# Must match REQUIRED_COMPANIES in rule_engine/ingest/persist.py.
DEFAULT_COMPANIES: tuple[int, ...] = (2, 3, 6, 7, 10, 13, 22, 23)

READONLY_KEYWORDS = ("SELECT", "SHOW", "DESC", "DESCRIBE", "EXPLAIN", "WITH")


def configure_stdio() -> None:
    """Use UTF-8 output in Git Bash / PyCharm on Windows.

    cmd.exe keeps its active code page so Chinese still renders there.
    """
    if os.name != "nt" or not (os.environ.get("MSYSTEM") or os.environ.get("PYCHARM_HOSTED")):
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


configure_stdio()


class ConfigError(RuntimeError):
    """Raised when .env is missing required values."""


class DbUnavailable(RuntimeError):
    """Raised when MySQL cannot be reached."""


def load_env(path: str | Path | None = None) -> Path:
    """Load KEY=VALUE pairs from .env without overriding the process env."""
    env_path = Path(path) if path else ENV_FILE
    if not env_path.exists():
        return env_path
    for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)
    return env_path


def get_config(require_database: bool = True) -> dict[str, Any]:
    """Return MySQL connection settings resolved from env/.env."""
    env_path = load_env()
    cfg = {
        "host": os.environ.get("MYSQL_HOST", "127.0.0.1").strip(),
        "port": int(os.environ.get("MYSQL_PORT", "3306") or 3306),
        "user": os.environ.get("MYSQL_USER", "root").strip(),
        "password": os.environ.get("MYSQL_PASSWORD", ""),
        "database": os.environ.get("MYSQL_DB", "football").strip(),
        "charset": "utf8mb4",
    }
    if not env_path.exists():
        print(f"[warn] {env_path} not found; using process environment", file=sys.stderr)
    if require_database and not cfg["database"]:
        raise ConfigError(f"MYSQL_DB is empty. Fill it in {env_path}.")
    return cfg


def connect(
    cfg: dict[str, Any] | None = None,
    database: str | None | object = ...,
    autocommit: bool = True,
    multi: bool = False,
):
    """Open a pymysql connection with a readable error message.

    Pass ``database=None`` to connect to the server without selecting a
    schema (needed by db_init.py).
    """
    try:
        import pymysql
        from pymysql.constants import CLIENT
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise DbUnavailable(
            "pymysql is not installed. Run: python -m pip install -r requirements.txt"
        ) from exc

    cfg = cfg or get_config()
    db_name = cfg["database"] if database is ... else database
    flags = CLIENT.MULTI_STATEMENTS if multi else 0
    try:
        return pymysql.connect(
            host=cfg["host"],
            port=cfg["port"],
            user=cfg["user"],
            password=cfg["password"],
            database=db_name,
            charset=cfg["charset"],
            autocommit=autocommit,
            client_flag=flags,
        )
    except pymysql.err.OperationalError as exc:
        code = exc.args[0] if exc.args else None
        hint = ""
        if code == 1045:
            hint = (
                f" Check MYSQL_USER/MYSQL_PASSWORD in {ENV_FILE}."
                " The current MYSQL_PASSWORD is empty."
                if not cfg["password"]
                else f" Check MYSQL_USER/MYSQL_PASSWORD in {ENV_FILE}."
            )
        elif code == 1049:
            hint = " The database does not exist yet; run: python tools/db_init.py"
        elif code in (2002, 2003):
            hint = f" Is MySQL running on {cfg['host']}:{cfg['port']}?"
        raise DbUnavailable(
            f"cannot connect to MySQL at {cfg['host']}:{cfg['port']} "
            f"as {cfg['user']!r}: {exc}.{hint}"
        ) from exc
    except pymysql.MySQLError as exc:
        raise DbUnavailable(f"MySQL error: {exc}") from exc


def quote_ident(name: str) -> str:
    """Quote a MySQL identifier after rejecting obviously unsafe input."""
    if not re.fullmatch(r"[A-Za-z0-9_$\-]+", name or ""):
        raise ValueError(f"unsafe identifier: {name!r}")
    return "`" + name.replace("`", "``") + "`"


def cell(value: Any) -> str:
    """Render one value for table/CSV output."""
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def display_width(text: str) -> int:
    """Terminal width, counting CJK characters as two columns."""
    width = 0
    for ch in str(text):
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def format_table(rows: Iterable[Sequence[Any]], columns: Sequence[str]) -> str:
    """Return a plain-text table that stays aligned with Chinese text."""
    data = [[cell(v) for v in row] for row in rows]
    widths = []
    for idx, name in enumerate(columns):
        width = display_width(name)
        for row in data:
            width = max(width, display_width(row[idx]))
        widths.append(width)
    lines = ["  ".join(_pad(name, widths[i]) for i, name in enumerate(columns))]
    lines.append("  ".join("-" * width for width in widths))
    for row in data:
        lines.append("  ".join(_pad(value, widths[i]) for i, value in enumerate(row)))
    return "\n".join(lines)


def print_rows(
    rows: Iterable[Sequence[Any]],
    columns: Sequence[str],
    fmt: str = "table",
    *,
    stream=None,
) -> None:
    """Print rows as table / json / csv."""
    stream = stream or sys.stdout
    data = [list(row) for row in rows]
    fmt = (fmt or "table").lower()
    if fmt == "table":
        print(format_table(data, columns), file=stream)
    elif fmt == "json":
        payload = [dict(zip(columns, row)) for row in data]
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str), file=stream)
    elif fmt == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(data)
        print(buffer.getvalue().rstrip("\n"), file=stream)
    else:
        raise ValueError(f"unsupported format: {fmt}")


def parse_int_list(value: str | None) -> tuple[int, ...] | None:
    """Parse '7,3,2' or 'all' into a company-id tuple (None means no filter)."""
    if value is None or value.strip() == "":
        return DEFAULT_COMPANIES
    value = value.strip()
    if value.lower() == "all":
        return None
    try:
        return tuple(int(part) for part in value.split(",") if part.strip())
    except ValueError as exc:
        raise ValueError(f"invalid company list: {value!r}") from exc


def ensure_readonly_sql(sql: str) -> str:
    """Reject anything that is not a read-only statement."""
    stripped = re.sub(r"^(?:\s|--[^\n]*\n|/\*.*?\*/)*", "", sql, flags=re.S)
    keyword = stripped.split(None, 1)[0].upper() if stripped else ""
    if keyword not in READONLY_KEYWORDS:
        raise ValueError(
            f"only read-only queries are allowed (SELECT/SHOW/DESC/EXPLAIN/WITH), got {keyword or 'empty'}"
        )
    return sql


def table_exists(conn, schema: str, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema=%s AND table_name=%s LIMIT 1",
            (schema, table),
        )
        return cur.fetchone() is not None


def human_int(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value:,}"
