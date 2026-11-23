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
    "荷甲": 16,
    "英冠": 37,
    "德乙": 9,
    "法乙": 12,
    "荷乙": 17,
    "西乙": 33,
    "意乙": 40,
    "葡超": 23,
    "苏超": 29,
    "瑞典超": 26,
    "俄超": 10,
    "挪超": 22,
    "比甲": 5,
    "芬超": 13,
    "希腊超": 32,
    "奥甲": 3,
    "丹麦超": 7,
    "罗甲": 124,
    "瑞士超": 27,
    "土超": 30,
    "以超": 118,
    "波兰超": 6,
    "塞尔超": 129,
    "捷甲": 137,

    "埃及超": 303,
    "伊朗超": 279,
    "沙地联": 292,
    "卡塔尔联": 313,
    "阿联酋超": 301,

    "日职联": 25,
    "韩K联": 15,
    "日职乙": 284,
    "中超": 60,
    "澳洲甲": 273,
    "越南联": 766,
    "泰超": 700,
    "马来超": 316,
    "印尼超": 1122,

    "阿甲": 2,
    "巴西甲": 4,
    "哥伦甲秋": 250,
    "墨西联秋":140,
    "美职业": 21,

    "亚冠精英":192,
    "亚冠联2":350,
    "欧冠":103,
    "欧罗巴":113,
    "欧协联":2187,


}

LEAGUE_IDS: frozenset[int] = frozenset(LEAGUE_WHITELIST.values())
LEAGUE_NAMES: frozenset[str] = frozenset(LEAGUE_WHITELIST)


def league_id(name: str) -> int | None:
    return LEAGUE_WHITELIST.get(name.strip())
