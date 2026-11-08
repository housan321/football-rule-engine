from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .features import AsianFeatures, EuroFeatures
from .models import Match, SupInfo


@dataclass
class RuleHit:
    code: str
    name: str
    direction: str  # home / away / observe
    strength: float
    reason: str
    category: str
    evidence: dict = field(default_factory=dict)


@dataclass
class RuleReport:
    hits: list[RuleHit] = field(default_factory=list)
    caps: list[tuple[int, str]] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    data_quality: dict = field(default_factory=dict)


def _side_name(match: Match, side: str) -> str:
    if side == "home":
        return match.home
    if side == "away":
        return match.away
    return "无"


def _upper_name(match: Match, asian: AsianFeatures) -> str:
    return _side_name(match, asian.upper_side)


def _lower_name(match: Match, asian: AsianFeatures) -> str:
    if asian.upper_side == "home":
        return match.away
    if asian.upper_side == "away":
        return match.home
    return "无"


def evaluate(
    match: Match,
    mode: str,
    asian: AsianFeatures,
    euro: EuroFeatures,
    sup: SupInfo,
    analysis_time: datetime,
) -> RuleReport:
    report = RuleReport()

    def hit(
        code: str,
        name: str,
        direction: str,
        strength: float,
        reason: str,
        category: str,
        evidence: dict | None = None,
    ) -> None:
        report.hits.append(
            RuleHit(code, name, direction, strength, reason, category, evidence or {})
        )

    # ------------------------------------------------------------------
    # 数据质量
    # ------------------------------------------------------------------
    report.data_quality = {
        "asian_companies": asian.coverage,
        "asian_latest": asian.latest_time.isoformat(sep=" ") if asian.latest_time else None,
        "asian_age_minutes": round(asian.data_age_minutes, 1)
        if asian.data_age_minutes is not None
        else None,
        "euro_companies": euro.companies,
        "euro_latest": euro.latest_time.isoformat(sep=" ") if euro.latest_time else None,
        "sup_available": sup.available,
        "sup_detail": sup.detail,
        "low_coverage": asian.low_coverage,
        "stale": asian.stale,
        "excluded": [
            {
                "company": v.company_name,
                "reason": v.exclude_reason,
            }
            for v in asian.views
            if v.excluded
        ],
    }

    if asian.low_coverage:
        hit(
            "DQ_LOW_COVERAGE",
            "核心亚盘公司不足 2 家",
            "observe",
            0,
            f"仅 {asian.coverage} 家核心公司可用，无法形成公司共识",
            "data_quality",
        )
        report.caps.append((45, "核心公司覆盖率不足"))

    if asian.stale:
        hit(
            "DQ_STALE",
            "亚盘数据陈旧",
            "observe",
            0,
            f"亚盘最新 {asian.latest_time}，数据年龄 {asian.data_age_minutes:.0f} 分钟",
            "data_quality",
        )
        report.caps.append((40, "亚盘数据陈旧"))

    if euro.companies < 3:
        report.caps.append((50, "欧赔公司不足 3 家"))

    if not sup.available:
        report.caps.append((55, "sup 缺失，无法校验理论盘口"))

    # Phase 1 没有基本面输入，统一封顶
    report.caps.append((55, "Phase 1 无基本面（伤停/首发/战意）"))

    # ------------------------------------------------------------------
    # 结构特征
    # ------------------------------------------------------------------
    upper = _upper_name(match, asian)
    lower = _lower_name(match, asian)
    upper_is_favorite = (
        asian.upper_side != "none" and asian.upper_side == euro.favorite_side
    )

    # 公司分歧 / 澳门反向
    if asian.line_dispersion >= 0.5:
        report.conflicts.append(
            f"各公司最新盘口分歧达 {asian.line_dispersion:+.2f} 球"
        )
        hit(
            "COMPANY_DIVERGENCE",
            "公司盘口分歧过大",
            "observe",
            0,
            f"最新盘口极差 {asian.line_dispersion:+.2f}，市场未形成共识",
            "structure",
        )
    if asian.water_dispersion >= 0.15:
        report.conflicts.append(
            f"各公司主水极差达 {asian.water_dispersion:.2f}"
        )
        report.caps.append((50, "公司水位分歧过大"))
        hit(
            "WATER_DIVERGENCE",
            "公司水位分歧过大",
            "observe",
            0,
            f"核心公司最新主水极差 {asian.water_dispersion:.2f}，定价不统一",
            "structure",
        )
    try:
        macau = next(v for v in asian.core_views if v.company_id == 7)
    except StopIteration:
        macau = None
    if macau and abs(macau.line_delta) >= 0.2:
        target = 1 if asian.median_line_delta > 0 else -1
        macau_sign = 1 if macau.line_delta > 0 else -1
        if asian.median_line_delta != 0 and macau_sign != target:
            report.conflicts.append(
                f"澳门盘方向与多数公司相反（澳门 {macau.line_delta:+.2f}）"
            )
            hit(
                "MACAU_OPPOSITE",
                "澳门与多数公司反向",
                "observe",
                0,
                f"澳门变盘 {macau.line_delta:+.2f}，与核心中位 {asian.median_line_delta:+.2f} 相反；澳门为最高权威，方向未定",
                "structure",
            )

    # ------------------------------------------------------------------
    # 水位规则
    # ------------------------------------------------------------------
    if asian.upper_side != "none":
        if asian.upper_latest_water <= 0.75:
            hit(
                "WL_UPPER_ULTRA_LOW",
                "上盘超低水",
                asian.upper_side,
                1.5,
                f"{upper} 让球方水位 {asian.upper_latest_water:.2f} ≤0.75，庄家明显看好上盘",
                "water",
            )
        elif asian.upper_latest_water <= 0.85:
            hit(
                "WL_UPPER_LOW",
                "上盘低水",
                asian.upper_side,
                1.0,
                f"{upper} 让球方水位 {asian.upper_latest_water:.2f} 处于低水区，庄家看好上盘",
                "water",
            )
        elif asian.upper_latest_water >= 1.10:
            hit(
                "WL_UPPER_ULTRA_HIGH",
                "上盘超高水",
                "observe",
                0,
                f"{upper} 让球方水位 {asian.upper_latest_water:.2f} ≥1.10，超高水阻击，需基本面确认方向",
                "water",
            )
            report.conflicts.append("上盘超高水，方向需基本面裁决")
        elif asian.upper_latest_water >= 0.975:
            hit(
                "WL_UPPER_HIGH",
                "上盘高水（T-03）",
                "observe",
                0,
                f"{upper} 让球方水位 {asian.upper_latest_water:.2f} ≥0.975；高水阻上，但需判断上盘是否有真实软肋",
                "water",
            )
            report.conflicts.append("T-03 高水阻上：Phase 1 无基本面，无法裁决")

        if 0.985 <= asian.upper_latest_water <= 0.995:
            stable = (
                asian.latest_time is not None
                and asian.views
                and all(
                    (asian.latest_time - v.first_time).total_seconds() >= 7200
                    for v in asian.core_views
                )
            )
            if stable:
                hit(
                    "WL_UPPER_099",
                    "上盘 0.99 水（T-04）",
                    "away" if asian.upper_side == "home" else "home",
                    1.0,
                    f"{upper} 让球方长期维持约 0.99 水，隐蔽阻击上盘",
                    "water",
                )

        if asian.lower_latest_water <= 0.75:
            hit(
                "WL_LOWER_ULTRA_LOW",
                "下盘超低水",
                "away" if asian.upper_side == "home" else "home",
                1.2,
                f"{lower} 受让方水位 {asian.lower_latest_water:.2f} ≤0.75，资金明显偏向下盘",
                "water",
            )

    # ------------------------------------------------------------------
    # 盘口运动规则
    # ------------------------------------------------------------------
    if asian.upper_side != "none":
        if asian.deepening > 0.20 and abs(asian.upper_water_delta) >= 0.02:
            if asian.upper_water_delta <= -0.02:
                if (
                    asian.early_change_ratio >= 0.5
                    and euro.companies >= 2
                    and upper_is_favorite
                ):
                    hit(
                        "T01_EARLY_DEEPEN_FAV",
                        "早段升盘 + 上盘热门（T-01）",
                        "away" if asian.upper_side == "home" else "home",
                        1.2,
                        f"{upper} 升盘 {asian.deepening:+.2f} 且为欧赔热门，早段变动；T-01 视为赶下盘，庄家看好下盘",
                        "movement",
                    )
                elif (
                    asian.early_change_ratio >= 0.5
                    and euro.companies >= 2
                    and not upper_is_favorite
                ):
                    hit(
                        "T01_EARLY_DEEPEN_DOG",
                        "早段升盘 + 上盘冷门（T-01 反向）",
                        asian.upper_side,
                        1.2,
                        f"{upper} 升盘 {asian.deepening:+.2f} 但非欧赔热门，早段升盘赶冷门，庄家看好上盘",
                        "movement",
                    )
                else:
                    hit(
                        "MOV_DEEPEN_SUPPORT",
                        "升盘 + 降水",
                        asian.upper_side,
                        1.0,
                        f"{upper} 升盘 {asian.deepening:+.2f} 且水位下降 {asian.upper_water_delta:+.2f}，临场减少赔付/真实支持",
                        "movement",
                    )
            elif asian.upper_water_delta >= 0.02:
                hit(
                    "MOV_DEEPEN_WATER_UP",
                    "升盘 + 升水",
                    "observe",
                    0,
                    f"{upper} 升盘 {asian.deepening:+.2f} 但水位上升 {asian.upper_water_delta:+.2f}，阻上/赶盘并存，方向不明",
                    "movement",
                )
                report.conflicts.append("升盘 + 升水：阻上与赶盘无法区分")

        if asian.retreat > 0.20:
            if asian.upper_water_delta <= -0.02:
                hit(
                    "T02_RETREAT_LOW_WATER",
                    "退盘 + 低水（T-02）",
                    "away" if asian.upper_side == "home" else "home",
                    1.2,
                    f"{upper} 退盘 {asian.retreat:+.2f} 且水位走低 {asian.upper_water_delta:+.2f}，符合退盘低水诱上",
                    "movement",
                )
            elif asian.upper_water_delta >= 0.02:
                hit(
                    "MOV_RETREAT_WATER_UP",
                    "退盘 + 升水（真实看衰）",
                    "away" if asian.upper_side == "home" else "home",
                    1.0,
                    f"{upper} 退盘 {asian.retreat:+.2f} 且水位上升 {asian.upper_water_delta:+.2f}，庄家真实看衰上盘",
                    "movement",
                )

    if abs(asian.median_line_delta) <= 0.05 and abs(asian.median_home_water_delta) >= 0.05:
        direction = "home" if asian.median_home_water_delta < 0 else "away"
        hit(
            "MOV_WATER_ONLY",
            "盘口不变 + 水位单边",
            direction,
            0.8,
            f"盘口未动，主水 {asian.median_home_water_delta:+.2f}，资金单独压{'主队' if direction == 'home' else '客队'}",
            "movement",
        )

    # ------------------------------------------------------------------
    # 欧亚交叉
    # ------------------------------------------------------------------
    if asian.consensus_direction in ("home", "away"):
        if euro.movement == asian.consensus_direction:
            hit(
                "EURO_ASIA_ALIGN",
                "欧亚同向",
                asian.consensus_direction,
                1.2,
                f"亚盘 {asian.consensus_reason}，欧赔同向（主胜概率 {euro.home_prob_delta * 100:+.1f}%，客胜 {euro.away_prob_delta * 100:+.1f}%）",
                "cross",
            )
        elif euro.movement in ("home", "away"):
            report.conflicts.append(
                f"欧亚背离：亚盘偏 {asian.consensus_direction}，欧赔偏 {euro.movement}"
            )
            report.caps.append((40, "欧亚背离，方向需裁决"))
            hit(
                "EURO_ASIA_DIVERGE",
                "欧亚背离",
                "observe",
                0,
                f"亚盘偏{'主' if asian.consensus_direction == 'home' else '客'}，欧赔偏{'主' if euro.movement == 'home' else '客'}；需裁决谁更可信",
                "cross",
            )

    if euro.favorite_side != "none":
        if euro.favorite_side == "home" and euro.home_prob_delta >= 0.02:
            hit(
                "EURO_FAV_DRIFT",
                "欧赔热门方向漂移",
                "home",
                0.8,
                f"主胜去水概率上升 {euro.home_prob_delta * 100:+.1f} 个百分点",
                "cross",
            )
        elif euro.favorite_side == "away" and euro.away_prob_delta >= 0.02:
            hit(
                "EURO_FAV_DRIFT",
                "欧赔热门方向漂移",
                "away",
                0.8,
                f"客胜去水概率上升 {euro.away_prob_delta * 100:+.1f} 个百分点",
                "cross",
            )

    directional = [h for h in report.hits if h.direction in ("home", "away")]
    if not directional:
        hit(
            "NO_SIGNAL",
            "无有效方向信号",
            "observe",
            0,
            "Phase 1 规则未形成方向性共识",
            "structure",
        )

    return report
