"""Hard-coded league whitelist used for crawling/scanning.

IDs are titan007 SclassID values, which match match_list_all.competition_id
for the supported competitions.
"""
from __future__ import annotations

LEAGUE_WHITELIST: dict[str, int] = {
    "英超": 36,
    "西甲": 31,
    "意甲": 34,
    "德甲": 8,
    "法甲": 11,
    "葡超": 23,
    "荷甲": 16,
    "苏超": 29,
    "瑞典超": 26,
    "俄超": 10,
    "挪超": 22,
    "比甲": 5,
    "芬超": 13,
    "希腊超": 32,
    "英冠": 37,
    "德乙": 9,
    "法乙": 12,
    "荷乙": 17,
    "西乙": 33,
    "意乙": 40,
    "瑞典甲": 122,
    "罗甲": 124,
    "瑞士超": 27,
    "土超": 30,
    "以超": 118,
    "波兰超": 6,
    "埃及超": 303,
    "伊朗超": 279,
    "沙地联": 292,
    "日职联": 25,
    "韩K联": 15,
    "日职乙": 284,
    "中超": 60,
    "澳洲甲": 273,
    "巴西甲": 4,
    "奥甲": 3,
    "丹麦超": 7,
    "葡甲": 157,
    "越南联": 766,
    "俄甲": 235,
    "塞尔超": 129,
    "泰超": 700,
    "南非超": 308,
    "瑞士甲": 121,
    "捷甲": 137,
    "阿甲": 2,
    "美职业": 21,
    "马来超": 316,
    "阿联酋超": 301,
    "印尼超": 1122,
}

LEAGUE_IDS: frozenset[int] = frozenset(LEAGUE_WHITELIST.values())
LEAGUE_NAMES: frozenset[str] = frozenset(LEAGUE_WHITELIST)


def league_id(name: str) -> int | None:
    return LEAGUE_WHITELIST.get(name.strip())
