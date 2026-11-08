from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Optional

VARIANT_DEFS: dict[str, Callable[[dict], bool]] = {
    "conf45_all": lambda r: int(r["confidence"]) >= 45,
    "conf50_all": lambda r: int(r["confidence"]) >= 50,
    "conf45_giving": lambda r: int(r["confidence"]) >= 45
    and float(r["side_line"]) < 0,
    "conf50_giving": lambda r: int(r["confidence"]) >= 50
    and float(r["side_line"]) < 0,
    "conf50_receiving": lambda r: int(r["confidence"]) >= 50
    and float(r["side_line"]) >= 0,
}

SELECTION_CANDIDATES = ("conf50_all", "conf50_giving", "conf45_giving")
DEFAULT_VARIANT = "conf50_all"


def load_csv(path: str | Path) -> list[dict]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [r for r in rows if str(r.get("bet", "0")) == "1"]


def _stats(rows: list[dict]) -> dict:
    outcomes = Counter(r["outcome"] for r in rows)
    wins = outcomes["win"] + outcomes["half_win"]
    losses = outcomes["loss"] + outcomes["half_loss"]
    pushes = outcomes["push"]
    profit = sum(float(r["profit"]) for r in rows)
    n = len(rows)
    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "hit_rate": round(wins / (wins + losses), 4) if wins + losses else None,
        "profit": round(profit, 4),
        "roi": round(profit / n, 4) if n else None,
    }


def monthly_variant_table(rows: list[dict]) -> dict:
    months = sorted({r["match_time"][:7] for r in rows})
    table: dict[str, dict] = {}
    for variant, predicate in VARIANT_DEFS.items():
        table[variant] = {
            month: _stats(
                [r for r in rows if r["match_time"][:7] == month and predicate(r)]
            )
            for month in months
        }
    return {"months": months, "variants": table}


def walk_forward(rows: list[dict]) -> dict:
    months = sorted({r["match_time"][:7] for r in rows})
    train: list[dict] = []
    stitched: list[dict] = []
    steps: list[dict] = []

    for month in months:
        scores: dict[str, Optional[float]] = {}
        if not train:
            chosen = DEFAULT_VARIANT
            reason = "首个测试月，使用默认基线 conf50_all"
        else:
            for variant in SELECTION_CANDIDATES:
                stats = _stats([r for r in train if VARIANT_DEFS[variant](r)])
                scores[variant] = stats["roi"]
            chosen = max(
                SELECTION_CANDIDATES,
                key=lambda v: scores[v] if scores[v] is not None else -9,
            )
            reason = "选择训练期累计 ROI 最高的变体"

        test_rows = [
            r
            for r in rows
            if r["match_time"][:7] == month and VARIANT_DEFS[chosen](r)
        ]
        test_stats = _stats(test_rows)
        steps.append(
            {
                "month": month,
                "chosen": chosen,
                "reason": reason,
                "train_scores": {
                    k: (round(v, 4) if v is not None else None)
                    for k, v in scores.items()
                },
                "test": test_stats,
            }
        )
        stitched.extend(test_rows)
        # Training always uses all bets of the completed month, not only the
        # selected variant, so the next selection sees the full history.
        train.extend([r for r in rows if r["match_time"][:7] == month])

    return {
        "months": months,
        "steps": steps,
        "stitched": _stats(stitched),
    }


def build_summary(rows: list[dict], source: str = "") -> dict:
    monthly = monthly_variant_table(rows)
    wf = walk_forward(rows)
    return {
        "source": source,
        "total_bets": len(rows),
        "months": monthly["months"],
        "monthly_variants": monthly["variants"],
        "walk_forward": wf,
        "notes": [
            "变体筛选只用测试月之前的月份，避免未来函数。",
            "候选变体在看过全样本后提出，因此仍存在规则选择偏差。",
            "平注 1 单位，未计滑点/交易量；结算按 90 分钟比分。",
            "受让方/PK 是控制组，不参与 walk-forward 选择。",
        ],
    }


def write_json(summary: dict, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _pct(value: Optional[float]) -> str:
    return "-" if value is None else f"{value * 100:+.1f}%"


def _rate(value: Optional[float]) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def print_summary(summary: dict) -> None:
    print(f"Source: {summary['source']} | bets: {summary['total_bets']}")
    print("\nMonthly variants")
    months = summary["months"]
    for variant, data in summary["monthly_variants"].items():
        parts = []
        for month in months:
            s = data[month]
            parts.append(f"{month}: n={s['n']} hit={_rate(s['hit_rate'])} roi={_pct(s['roi'])}")
        print(f"  {variant:<18} " + " | ".join(parts))

    print("\nWalk-forward")
    for step in summary["walk_forward"]["steps"]:
        s = step["test"]
        scores = ", ".join(
            f"{k}={_pct(v)}" for k, v in step["train_scores"].items()
        )
        print(
            f"  {step['month']} -> {step['chosen']:<14} "
            f"test n={s['n']} hit={_rate(s['hit_rate'])} roi={_pct(s['roi'])} "
            f"profit={s['profit']:+.2f}  [train: {scores or 'default'}]"
        )
    st = summary["walk_forward"]["stitched"]
    print(
        f"\nStitched: n={st['n']} hit={_rate(st['hit_rate'])} "
        f"profit={st['profit']:+.2f} roi={_pct(st['roi'])}"
    )


def write_markdown(summary: dict, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    months = summary["months"]
    lines = [
        "# Phase 3 · Walk-forward 样本外验证",
        "",
        f"- 输入：`{summary['source']}`",
        f"- 下注数：{summary['total_bets']}",
        f"- 月份：{'、'.join(months)}",
        "",
        "## 1. 各变体月度表现",
        "",
        "| 变体 | " + " | ".join(months) + " |",
        "|---|" + "---|" * len(months),
    ]
    for variant, data in summary["monthly_variants"].items():
        cells = []
        for month in months:
            s = data[month]
            cells.append(
                f"n={s['n']} 命中 {_rate(s['hit_rate'])} ROI {_pct(s['roi'])}"
            )
        lines.append(f"| {variant} | " + " | ".join(cells) + " |")

    lines += [
        "",
        "## 2. Walk-forward 选择路径",
        "",
        "| 测试月 | 选择变体 | 训练期得分 | 测试 n | 测试命中 | 测试 ROI | 测试利润 |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for step in summary["walk_forward"]["steps"]:
        s = step["test"]
        scores = ", ".join(f"{k}={_pct(v)}" for k, v in step["train_scores"].items())
        lines.append(
            f"| {step['month']} | {step['chosen']} | {scores or '默认'} | "
            f"{s['n']} | {_rate(s['hit_rate'])} | {_pct(s['roi'])} | {s['profit']:+.2f} |"
        )
    st = summary["walk_forward"]["stitched"]
    lines += [
        "",
        f"**拼接结果：** n={st['n']}，命中 {_rate(st['hit_rate'])}，"
        f"利润 {st['profit']:+.2f}，ROI {_pct(st['roi'])}。",
        "",
        "## 3. 结论与限制",
        "",
        "- `conf50_giving` / `conf45_giving` 在样本各月均为正，`conf50_receiving` 各月均为负。",
        "- Walk-forward 选择只用测试月之前的月份，避免了直接未来函数。",
        "- 但候选变体是看过全样本后提出的，仍存在规则选择偏差；这不是完全独立的样本外验证。",
        "- 平注、无滑点/交易量，结算按 90 分钟比分。",
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
