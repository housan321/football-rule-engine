#!/usr/bin/env python3
"""Parse a saved titan007 AsianOdds page into normalized records.

Usage:
    python -I scripts/titan007_parser.py <html-file> <output-json>

The page has two relevant tables:
  - #odds       : one row per company (and alternate line), with 初/即时/终.
  - #oddsDetail : merged change history with timestamps for the main line.

Only the Python standard library is used.
"""
import json
import re
import sys
from datetime import datetime
from html.parser import HTMLParser

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

# Chinese handicap text -> magnitude in goals.
HANDICAP_MAP = {
    "平手": 0.0,
    "平手/半球": 0.25,
    "平/半": 0.25,
    "半球": 0.5,
    "半球/一球": 0.75,
    "半/一": 0.75,
    "一球": 1.0,
    "一球/球半": 1.25,
    "一/球半": 1.25,
    "球半": 1.5,
    "球半/两球": 1.75,
    "球半/二球": 1.75,
    "两球": 2.0,
    "二球": 2.0,
    "两球/两球半": 2.25,
    "两球半": 2.5,
    "两球半/三球": 2.75,
    "三球": 3.0,
}

# titan007 company id -> skill company id
TITAN_TO_SKILL = {
    1: 7,    # 澳门
    3: 3,    # 皇冠
    8: 2,    # Bet365
    12: 10,  # 易胜博
    14: 11,  # 韦德
    17: 6,   # 明陞
    22: 4,   # 10BET
    24: 14,  # 12bet
    31: 15,  # 利记
    35: 16,  # 盈禾
    42: 17,  # 18Bet
    47: 22,  # 平博 / Pinnacle
    9: 9,    # 威廉希尔
    19: 13,  # Interwetten
    48: 23,  # 香港马会（自定义）
    50: 24,  # 1xBet（skill 明确排除，仅存档）
}

TITAN_NAME = {
    1: "澳门", 3: "皇冠", 8: "Bet365", 12: "易胜博", 14: "韦德",
    17: "明陞", 22: "10BET", 24: "12bet", 31: "利记", 35: "盈禾",
    42: "18Bet", 47: "平博", 9: "威廉希尔", 19: "Interwetten",
    48: "香港马会", 50: "1xBet",
}

# The detail table header uses abbreviations that differ from the main table.
DETAIL_NAME_TO_TITAN = {
    "澳": 1,
    "Crow": 3,
    "36": 8,
    "易胜": 12,
    "伟": 14,
    "明": 17,
    "10": 22,
    "12": 24,
    "利": 31,
    "盈": 35,
    "18": 42,
}


class Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag="", attrs=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []

    def text(self) -> str:
        parts = []
        for child in self.children:
            if isinstance(child, str):
                parts.append(child)
            else:
                parts.append(child.text())
        return "".join(parts)

    def find_all(self, tag):
        out = []
        for child in self.children:
            if isinstance(child, Node):
                if child.tag == tag:
                    out.append(child)
                out.extend(child.find_all(tag))
        return out

    def direct_children(self, tag):
        return [c for c in self.children if isinstance(c, Node) and c.tag == tag]


class DomParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag == "br":
            self.stack[-1].children.append("\n")
        elif tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        tag = tag.lower()
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def normalize_company(name: str) -> str:
    return name.replace("封", "").replace("*", "").strip()


def parse_handicap(text: str, goals_attr=None):
    """Return signed handicap from the home team's perspective."""
    text = text.strip().replace("\xa0", " ")
    if not text or text == "-":
        return None
    received = text.startswith("受让")
    clean = text.replace("受让", "").strip()
    magnitude = HANDICAP_MAP.get(clean)
    if magnitude is None and goals_attr not in (None, ""):
        try:
            magnitude = float(goals_attr)
        except ValueError:
            magnitude = None
    if magnitude is None:
        return None
    if magnitude == 0:
        return 0.0
    return magnitude if received else -magnitude


def parse_water(text: str):
    text = text.strip().replace("\xa0", "")
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_detail_cell(text: str):
    """Parse '平手/半球 1.47 0.58' -> (handicap, home, away)."""
    text = " ".join(text.replace("\xa0", " ").split())
    if not text or text == "-":
        return None
    m = re.match(r"^(受让)?(.+?)\s+([\d.]+)\s+([\d.]+)$", text)
    if not m:
        return None
    magnitude = HANDICAP_MAP.get(m.group(2).strip())
    if magnitude is None:
        return None
    if magnitude == 0:
        signed = 0.0
    else:
        signed = magnitude if m.group(1) else -magnitude
    return signed, float(m.group(3)), float(m.group(4))


def infer_time(mmddhhmm: str, match_dt: datetime) -> datetime:
    dt = datetime.strptime("2000-" + mmddhhmm, "%Y-%m-%d %H:%M")
    year = match_dt.year
    if dt.month > match_dt.month:
        year -= 1
    return dt.replace(year=year)


_ENCODING_ALIASES = {
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "x-gbk": "gb18030",
    "gb_2312-80": "gb18030",
    "cp936": "gb18030",
}


def _normalize_encoding(name):
    if not name:
        return None
    name = str(name).strip().strip("\"'").lower()
    return _ENCODING_ALIASES.get(name, name)


def _sniff_meta_charset(raw: bytes):
    """Return the charset declared in the first 8 KB, normalized."""
    head = raw[:8192].decode("ascii", "ignore").lower()
    m = re.search(r'charset\s*=\s*["\']?([a-z0-9_:-]+)', head)
    return _normalize_encoding(m.group(1)) if m else None


def decode_html(raw: bytes, encoding=None) -> str:
    """Decode a titan007 page from bytes.

    titan007 sometimes serves GB2312 pages, and sometimes declares GB2312
    while actually sending UTF-8. Try strict UTF-8 before GB18030 when the
    declared charset is a GB variant, and fall back through caller hint,
    UTF-8, GB18030, then lossy UTF-8.
    """
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError("raw must be bytes")
    raw = bytes(raw)

    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", "replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace")

    meta = _sniff_meta_charset(raw)
    hint = _normalize_encoding(encoding)
    candidates = []
    if meta and (meta.startswith("gb") or meta.startswith("cp936")):
        # Mislabeled UTF-8 is common; a real GBK page fails strict UTF-8.
        candidates.extend(["utf-8", meta])
    elif meta:
        candidates.append(meta)
    if hint:
        candidates.append(hint)
    candidates.extend(["utf-8", "gb18030"])

    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            return raw.decode(candidate)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "ignore")


def read_html(html_path: str) -> str:
    with open(html_path, "rb") as fh:
        return decode_html(fh.read())


def parse_html(html_path: str) -> dict:
    """Parse a titan007 AsianOdds page from a file path."""
    return parse_html_text(read_html(html_path))


def parse_html_text(text: str, fixture_id=None) -> dict:
    """Parse a titan007 AsianOdds page from text.

    ``fixture_id`` should be passed by a crawler when the URL id is not
    embedded in the page body (live pages usually omit the saved-from comment).
    The requested id wins over an embedded id; a mismatch is reported.
    """
    if not isinstance(text, str):
        raise TypeError("text must be str; use decode_html() first")

    parser = DomParser()
    parser.feed(text)
    root = parser.root

    m = re.search(r"AsianOdds_n\.aspx\?id=(\d+)", text)
    embedded_fixture_id = m.group(1) if m else None
    requested_fixture_id = str(fixture_id) if fixture_id is not None else None
    effective_fixture_id = requested_fixture_id or embedded_fixture_id
    fixture_id_source = (
        "request" if requested_fixture_id
        else ("page" if embedded_fixture_id else None)
    )
    fixture_id_mismatch = bool(
        requested_fixture_id
        and embedded_fixture_id
        and requested_fixture_id != embedded_fixture_id
    )

    m = re.search(r'class="time">\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})', text)
    kickoff = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M") if m else None

    competition_name = None
    competition_id = None
    m = re.search(
        r'<a[^>]*href="([^"]*SclassID=(\d+)[^"]*)"[^>]*class="LName"[^>]*>([^<]+)</a>',
        text,
    )
    if m:
        competition_id = int(m.group(2))
        competition_name = m.group(3).strip()

    scores = re.findall(r'class="score(?: gt)?"[^>]*>([^<]*)<', text)
    home_score = int(scores[0]) if len(scores) >= 1 and scores[0].isdigit() else None
    away_score = int(scores[1]) if len(scores) >= 2 and scores[1].isdigit() else None

    title = None
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    if m:
        title = " ".join(m.group(1).split())
    home_team = away_team = None
    if title:
        m = re.match(r"(.+?)VS(.+?)\((.+?)\)", title)
        if m:
            home_team = m.group(1).strip()
            away_team = m.group(2).strip()

    m = re.search(r'teamid=(\d+)[^>]*title="历史盘口走势">主<', text)
    home_team_id = int(m.group(1)) if m else None
    m = re.search(r'teamid=(\d+)[^>]*title="历史盘口走势">客<', text)
    away_team_id = int(m.group(1)) if m else None

    tables = root.find_all("table")
    odds_table = next((t for t in tables if t.attrs.get("id") == "odds"), None)
    detail_table = next((t for t in tables if t.attrs.get("id") == "oddsDetail"), None)
    if odds_table is None:
        raise ValueError("table#odds not found")

    id_to_name = dict(TITAN_NAME)
    main_records = []
    last_name = None

    for tr in odds_table.find_all("tr"):
        tds = tr.direct_children("td")
        if len(tds) < 12:
            continue
        company_id = tr.attrs.get("companyid")
        if not company_id:
            for span in tr.find_all("span"):
                if span.attrs.get("companyid"):
                    company_id = span.attrs["companyid"]
                    break
        name_cell = tds[1].text().strip()
        if name_cell:
            last_name = normalize_company(name_cell)
        name = normalize_company(name_cell) if name_cell else last_name
        if company_id and company_id.isdigit():
            id_to_name[int(company_id)] = name
        if not name:
            continue

        line_label = tds[2].text().strip()
        line_no = 0
        if line_label.startswith("盘"):
            try:
                line_no = int(line_label[1:])
            except ValueError:
                line_no = 0

        groups = {
            "initial": tds[3:6],
            "current": tds[6:9],
            "final": tds[9:12],
        }
        for phase, group in groups.items():
            if len(group) != 3:
                continue
            home = parse_water(group[0].text())
            handicap = parse_handicap(group[1].text(), group[1].attrs.get("goals"))
            away = parse_water(group[2].text())
            if home is None or handicap is None or away is None:
                continue
            title_ts = group[0].attrs.get("title") or group[1].attrs.get("title")
            if phase == "initial":
                change_time = title_ts
            elif phase == "final":
                change_time = kickoff.strftime("%Y-%m-%d %H:%M") if kickoff else None
            else:
                change_time = None
            titan_id = int(company_id) if company_id and company_id.isdigit() else None
            main_records.append({
                "fixture_id": effective_fixture_id,
                "company_id_titan": titan_id,
                "company_id": TITAN_TO_SKILL.get(titan_id) if titan_id else None,
                "company": TITAN_NAME.get(titan_id, name),
                "line_no": line_no,
                "phase": phase,
                "change_time": change_time,
                "home_odds": home,
                "handicap": handicap,
                "away_odds": away,
                "oddstype": group[1].attrs.get("oddstype"),
            })

    detail_records = []
    if detail_table is not None:
        header = None
        for tr in detail_table.find_all("tr"):
            ths = tr.direct_children("th")
            if ths:
                header = [normalize_company(th.text()) for th in ths]
                continue
            tds = tr.direct_children("td")
            if header is None or len(tds) != len(header):
                continue
            time_text = tds[-1].text().strip()
            score = tds[-2].text().strip()
            # titan007 omits leading zeroes in months/days/hours for some
            # fixtures (e.g. "8-25 04:52"), so accept 1-2 digits.
            if not re.match(r"^\d{1,2}-\d{1,2} \d{1,2}:\d{2}$", time_text):
                continue
            change_dt = infer_time(time_text, kickoff) if kickoff else None
            for i, td in enumerate(tds[:-2]):
                parsed = parse_detail_cell(td.text())
                if parsed is None:
                    continue
                company_name = header[i] if i < len(header) else ""
                titan_id = DETAIL_NAME_TO_TITAN.get(company_name)
                if titan_id is None:
                    for tid, nm in id_to_name.items():
                        if normalize_company(nm) == company_name:
                            titan_id = tid
                            break
                detail_records.append({
                    "fixture_id": effective_fixture_id,
                    "company_titan_name": company_name,
                    "company_id_titan": titan_id,
                    "company_id": TITAN_TO_SKILL.get(titan_id) if titan_id else None,
                    "company": TITAN_NAME.get(titan_id, company_name),
                    "line_no": 0,
                    "change_time": change_dt.strftime("%Y-%m-%d %H:%M") if change_dt else None,
                    "home_odds": parsed[1],
                    "handicap": parsed[0],
                    "away_odds": parsed[2],
                    "score": score,
                    # At exactly kickoff the main table's 终盘 is authoritative;
                    # detail rows at kickoff are treated as in-play to avoid conflicts.
                    "phase": "prematch" if (kickoff and change_dt and change_dt < kickoff) else "inplay",
                })

    def dedupe(records):
        seen = set()
        out = []
        for r in records:
            key = tuple(sorted(r.items()))
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        return out

    main_records = dedupe(main_records)
    detail_records = dedupe(detail_records)
    detail_records.sort(key=lambda r: (r["change_time"] or "", r["company_id_titan"] or 0))

    return {
        "fixture_id": effective_fixture_id,
        "embedded_fixture_id": embedded_fixture_id,
        "fixture_id_source": fixture_id_source,
        "fixture_id_mismatch": fixture_id_mismatch,
        "kickoff": kickoff.strftime("%Y-%m-%d %H:%M") if kickoff else None,
        "competition_id": competition_id,
        "competition": competition_name,
        "home_team": home_team,
        "away_team": away_team,
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "home_score": home_score,
        "away_score": away_score,
        "main_records": main_records,
        "detail_records": detail_records,
        "counts": {
            "main": len(main_records),
            "detail": len(detail_records),
            "detail_prematch": sum(1 for r in detail_records if r["phase"] == "prematch"),
            "detail_inplay": sum(1 for r in detail_records if r["phase"] == "inplay"),
        },
    }


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: python -I scripts/titan007_parser.py <html-file> <output-json>")
    result = parse_html(sys.argv[1])
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print(
        f"fixture_id={result['fixture_id']} kickoff={result['kickoff']} "
        f"competition={result['competition']} "
        f"home={result['home_team']}({result['home_team_id']}) "
        f"away={result['away_team']}({result['away_team_id']}) "
        f"counts={result['counts']}"
    )


if __name__ == "__main__":
    main()
