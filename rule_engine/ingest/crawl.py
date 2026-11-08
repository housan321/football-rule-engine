#!/usr/bin/env python3
"""Fetch titan007 pages through `openssl s_client` and import them to MySQL.

Why this exists: from some networks the server accepts the OpenSSL CLI client
but resets connections from Scrapy/curl/Python-ssl (TLS fingerprinting). This
script keeps the same parser and MySQL pipeline, and only swaps the transport.

Examples:
    python scripts/crawl_titan007_openssl.py --ids 2910948 --dry-run
    python scripts/crawl_titan007_openssl.py --ids 2910948
    python scripts/crawl_titan007_openssl.py --dates 2026-10-28 --leagues 4 --max-fixtures 1
    python scripts/crawl_titan007_openssl.py --dates 2026-10-28,2026-10-29 --dry-run

Requires the `openssl` executable on PATH (or set OPENSSL_BIN).
"""
from __future__ import annotations

import argparse
import gzip
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import zlib
from datetime import date, datetime, timedelta
from pathlib import Path

from .parser import decode_html, parse_html_text  # noqa: E402
from .pipeline import Titan007MySQLPipeline  # noqa: E402

ASIAN_HOST = "vip.titan007.com"
SCHEDULE_HOST = "bf.titan007.com"
ASIAN_PATH = "/AsianOdds_n.aspx?id={fixture_id}"
SCHEDULE_PATH = "/football/Next_{yyyymmdd}.htm"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "close",
}

logger = logging.getLogger("titan007.openssl")
FETCH_RETRIES = 2


class FetchError(RuntimeError):
    pass


class SimpleStats:
    def __init__(self):
        self.values = {}

    def inc_value(self, key, count=1, start=0):
        self.values[key] = self.values.get(key, start) + count


# ---- HTTP over openssl s_client ------------------------------------


def find_openssl(explicit=None):
    candidate = explicit or os.environ.get("OPENSSL_BIN") or shutil.which("openssl")
    if not candidate:
        raise FetchError(
            "openssl not found; install OpenSSL or set OPENSSL_BIN, "
            "or use the browser-export + scripts/import_titan007.py fallback"
        )
    return candidate


def _dechunk(data: bytes) -> bytes:
    out = bytearray()
    pos = 0
    while True:
        end = data.find(b"\r\n", pos)
        if end < 0:
            break
        line = data[pos:end].split(b";", 1)[0].strip()
        try:
            size = int(line, 16)
        except ValueError:
            break
        pos = end + 2
        if size == 0:
            break
        out += data[pos:pos + size]
        pos += size + 2
    return bytes(out)


def _decompress(body: bytes, headers: dict) -> bytes:
    encoding = (headers.get("content-encoding") or "").lower()
    if "gzip" in encoding:
        return gzip.decompress(body)
    if "deflate" in encoding:
        try:
            return zlib.decompress(body)
        except zlib.error:
            return zlib.decompress(body, -zlib.MAX_WBITS)
    return body


def _parse_response(raw: bytes):
    raw = raw.lstrip(b"\r\n")
    head, sep, body = raw.partition(b"\r\n\r\n")
    if not sep:
        raise FetchError(f"no HTTP header in response ({len(raw)} bytes)")
    lines = head.split(b"\r\n")
    status_line = lines[0].decode("latin1", "replace")
    match = re.match(r"HTTP/\d(?:\.\d)?\s+(\d{3})", status_line)
    if not match:
        raise FetchError(f"bad status line: {status_line[:80]!r}")
    status = int(match.group(1))
    headers = {}
    for line in lines[1:]:
        if b":" not in line:
            continue
        key, value = line.split(b":", 1)
        headers[key.decode("latin1").strip().lower()] = value.decode("latin1").strip()
    if "chunked" in (headers.get("transfer-encoding") or "").lower():
        body = _dechunk(body)
    body = _decompress(body, headers)
    return status, headers, body


def _openssl_once(openssl, host, path, timeout, use_alpn=True):
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        + "".join(f"{key}: {value}\r\n" for key, value in BASE_HEADERS.items())
        + "\r\n"
    ).encode("latin1")
    cmd = [
        openssl, "s_client",
        "-connect", f"{host}:443",
        "-servername", host,
        "-quiet",
    ]
    if use_alpn:
        cmd += ["-alpn", "http/1.1"]
    try:
        proc = subprocess.run(
            cmd,
            input=request,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise FetchError(f"openssl timed out after {timeout}s") from exc
    if not proc.stdout:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        if use_alpn and "unknown option" in stderr.lower() and "alpn" in stderr.lower():
            return _openssl_once(openssl, host, path, timeout, use_alpn=False)
        raise FetchError(f"empty response (rc={proc.returncode}): {stderr[:300]}")
    return _parse_response(proc.stdout)


def fetch_url(openssl, host, path, timeout=60, retries=None):
    retries = FETCH_RETRIES if retries is None else retries
    last_error = None
    for attempt in range(retries + 1):
        try:
            status, headers, body = _openssl_once(openssl, host, path, timeout)
            if status != 200:
                raise FetchError(f"HTTP {status} for https://{host}{path}")
            return headers, body
        except FetchError as exc:
            last_error = exc
            if attempt < retries:
                delay = 1.5 * (attempt + 1)
                logger.warning(
                    "fetch attempt %d/%d failed for %s%s: %s; retrying in %.1fs",
                    attempt + 1, retries + 1, host, path, exc, delay,
                )
                time.sleep(delay)
    raise FetchError(f"failed to fetch https://{host}{path}: {last_error}")


# ---- discovery ------------------------------------------------------


def parse_dates(raw):
    if not raw:
        return [date.today().strftime("%Y%m%d")]
    out = []
    for part in str(raw).split(","):
        cleaned = part.strip().replace("-", "").replace("/", "")
        if not cleaned:
            continue
        if len(cleaned) != 8 or not cleaned.isdigit():
            raise ValueError(f"invalid date {part!r}; use YYYY-MM-DD or YYYYMMDD")
        datetime.strptime(cleaned, "%Y%m%d")
        if cleaned not in out:
            out.append(cleaned)
    return out or [date.today().strftime("%Y%m%d")]


def parse_leagues(raw):
    if raw is None or str(raw).strip() == "":
        return None
    out = set()
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise ValueError(f"invalid league id {part!r}")
        out.add(int(part))
    return out or None


def parse_ids(raw):
    if raw is None or str(raw).strip() == "":
        return []
    out = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit() or int(part) <= 0:
            raise ValueError(f"invalid fixture id {part!r}")
        if int(part) not in out:
            out.append(int(part))
    return out


def _iter_schedule_rows(text):
    """Yield (sid, name-attribute) from the schedule table, with a no-parsel fallback."""
    try:
        from parsel import Selector
    except ImportError:
        Selector = None
    if Selector is not None:
        for tr in Selector(text=text).xpath("//table[@id='table_live']//tr[@sid]"):
            yield (tr.attrib.get("sid") or "").strip(), (tr.attrib.get("name") or "").strip()
        return
    for tag in re.findall(r"<tr[^>]*>", text, re.I | re.S):
        sid_match = re.search(r"""sid\s*=\s*["']?(\d+)["']?""", tag)
        name_match = re.search(r"""name\s*=\s*["']([^"']*)["']""", tag)
        if sid_match:
            yield sid_match.group(1), name_match.group(1) if name_match else ""


def discover_fixtures(openssl, dates, leagues, max_fixtures, timeout):
    fixtures = []
    seen = set()
    for yyyymmdd in dates:
        headers, body = fetch_url(
            openssl, SCHEDULE_HOST, SCHEDULE_PATH.format(yyyymmdd=yyyymmdd), timeout=timeout
        )
        text = decode_html(body)
        rows = list(_iter_schedule_rows(text))
        logger.info("schedule %s: %d fixture row(s)", yyyymmdd, len(rows))
        for sid, league_raw in rows:
            if max_fixtures is not None and len(fixtures) >= max_fixtures:
                return fixtures
            if not sid.isdigit() or int(sid) in seen:
                continue
            prefix = league_raw.split(",", 1)[0].strip()
            league_id = int(prefix) if prefix.isdigit() else None
            if leagues is not None and league_id not in leagues:
                continue
            seen.add(int(sid))
            fixtures.append(int(sid))
    return fixtures


_SCHEDULE_TIME_RE = re.compile(r"(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})")


def _schedule_kickoff(row_text, page_yyyymmdd):
    """Parse '11-7 11:00' from a schedule row into a datetime."""
    match = _SCHEDULE_TIME_RE.search(row_text)
    if not match:
        return None
    month, day, hour, minute = (int(x) for x in match.groups())
    year = int(page_yyyymmdd[:4])
    page_month = int(page_yyyymmdd[4:6])
    if month == 12 and page_month == 1:
        year -= 1
    elif month == 1 and page_month == 12:
        year += 1
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def _iter_schedule_rows_full(text):
    """Yield (sid, name-attribute, row-text) from the schedule table."""
    for block in re.findall(r"<tr\b[^>]*>.*?</tr>", text, re.I | re.S):
        sid_match = re.search(r"""\bsid\s*=\s*["']?(\d+)""", block, re.I)
        if not sid_match:
            continue
        name_match = re.search(r"""\bname\s*=\s*["']([^"']*)["']""", block, re.I)
        row_text = re.sub(r"<[^>]+>", " ", block)
        yield (
            sid_match.group(1),
            name_match.group(1) if name_match else "",
            row_text,
        )


def _schedule_dates(now, within_minutes):
    """Schedule-page dates to fetch. Include D-1: post-midnight matches can
    appear on the previous day's page (赛程页日期不一定等于北京时间开赛日)."""
    cutoff = now + timedelta(minutes=within_minutes)
    dates = []
    cursor = now.date() - timedelta(days=1)
    while cursor <= cutoff.date():
        dates.append(cursor.strftime("%Y%m%d"))
        cursor += timedelta(days=1)
    return dates


def discover_upcoming_fixtures(
    openssl,
    now,
    within_minutes,
    timeout,
    max_fixtures=None,
    league_ids=None,
):
    """Discover fixtures kicking off within the window, optional league filter."""
    cutoff = now + timedelta(minutes=within_minutes)
    dates = _schedule_dates(now, within_minutes)

    fixtures = []
    seen = set()
    for yyyymmdd in dates:
        headers, body = fetch_url(
            openssl,
            SCHEDULE_HOST,
            SCHEDULE_PATH.format(yyyymmdd=yyyymmdd),
            timeout=timeout,
        )
        text = decode_html(body)
        rows = list(_iter_schedule_rows_full(text))
        logger.info("schedule %s: %d fixture row(s)", yyyymmdd, len(rows))
        for sid, league_raw, row_text in rows:
            if max_fixtures is not None and len(fixtures) >= max_fixtures:
                return fixtures
            if not sid.isdigit() or int(sid) in seen:
                continue
            prefix = league_raw.split(",", 1)[0].strip()
            league_id = int(prefix) if prefix.isdigit() else None
            if league_ids is not None and league_id not in league_ids:
                continue
            kickoff = _schedule_kickoff(row_text, yyyymmdd)
            if kickoff is None or not (now <= kickoff <= cutoff):
                continue
            seen.add(int(sid))
            fixtures.append(int(sid))
    return fixtures


# ---- main -----------------------------------------------------------


def fetch_odds(openssl, fixture_id, timeout):
    return fetch_url(
        openssl,
        ASIAN_HOST,
        ASIAN_PATH.format(fixture_id=fixture_id),
        timeout=timeout,
    )


def dry_run(openssl, fixture_ids, timeout):
    failures = 0
    for index, fixture_id in enumerate(fixture_ids, 1):
        try:
            headers, body = fetch_odds(openssl, fixture_id, timeout)
            data = parse_html_text(decode_html(body), fixture_id=fixture_id)
            logger.info(
                "fixture %s: %s vs %s, kickoff=%s, main=%d detail=%d bytes=%d",
                fixture_id,
                data.get("home_team") or "?",
                data.get("away_team") or "?",
                data.get("kickoff"),
                data["counts"]["main"],
                data["counts"]["detail"],
                len(body),
            )
            if data["counts"]["main"] == 0:
                failures += 1
        except Exception as exc:
            failures += 1
            logger.error("fixture %s: %s", fixture_id, exc)
        if index < len(fixture_ids):
            time.sleep(1.5)
    return 1 if failures else 0


def import_to_db(openssl, fixture_ids, timeout, database, status_id):
    pipeline = Titan007MySQLPipeline(database=database, status_id=status_id)
    pipeline.stats = SimpleStats()
    pipeline.open_spider()
    failures = 0
    try:
        for index, fixture_id in enumerate(fixture_ids, 1):
            try:
                headers, body = fetch_odds(openssl, fixture_id, timeout)
                pipeline.process_item(
                    {
                        "fixture_id": fixture_id,
                        "url": f"https://{ASIAN_HOST}/AsianOdds_n.aspx?id={fixture_id}",
                        "body": body,
                        "encoding": None,
                        "fetched_at": datetime.now().isoformat(timespec="seconds"),
                        "status": 200,
                    }
                )
            except Exception as exc:
                failures += 1
                logger.error("fixture %s: %s", fixture_id, exc)
            if index < len(fixture_ids):
                time.sleep(1.5)
    finally:
        pipeline.close_spider()
    logger.info("stats: %s", pipeline.stats.values)
    return 1 if failures else 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Fetch titan007 pages via openssl s_client and import to MySQL"
    )
    parser.add_argument("--ids", help="comma-separated fixture ids")
    parser.add_argument("--dates", help="schedule dates, e.g. 2026-10-28,2026-10-29")
    parser.add_argument("--leagues", help="comma-separated SclassID values")
    parser.add_argument("--max-fixtures", type=int)
    parser.add_argument("--dry-run", action="store_true", help="fetch and parse, do not write")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--database", help="override MYSQL_DB")
    parser.add_argument("--status-id", type=int, default=1)
    parser.add_argument("--openssl-bin", help="path to the openssl executable")
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    return parser


def main():
    global FETCH_RETRIES
    args = build_parser().parse_args()
    FETCH_RETRIES = args.retries
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        openssl = find_openssl(args.openssl_bin)
        fixture_ids = parse_ids(args.ids)
        if not fixture_ids:
            fixture_ids = discover_fixtures(
                openssl,
                parse_dates(args.dates),
                parse_leagues(args.leagues),
                args.max_fixtures,
                args.timeout,
            )
        elif args.max_fixtures:
            fixture_ids = fixture_ids[: args.max_fixtures]
        if not fixture_ids:
            logger.error("no fixture ids found")
            return 2
        logger.info("fixtures: %s", ",".join(str(x) for x in fixture_ids))
        if args.dry_run:
            return dry_run(openssl, fixture_ids, args.timeout)
        return import_to_db(openssl, fixture_ids, args.timeout, args.database, args.status_id)
    except (FetchError, ValueError) as exc:
        logger.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
