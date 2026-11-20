"""Monthly stability reports for the three strategies.

There is no parameter selection here: the strategies are pre-registered, so
the useful check is whether each one keeps its sign month by month.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Optional

from .rules import STRATEGIES


def load_csv(path: str | Path) -> list[dict]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [r for r in rows if str(r.get("bet", "0")) == "1"]


def _profit(row: dict) -> float:
    for key in ("profit_consensus", "profit"):
        value = row.get(key)
        if value not in (None, ""):
            return float(value)
    return 0.0


def _stats(rows: list[dict]) -> dict:
    outcomes = Counter(r.get("outcome", "") for r in rows)
    wins = outcomes["win"] + outcomes["half_win"]
    losses = outcomes["loss"] + outcomes["half_loss"]
    pushes = outcomes["push"]
    profit = sum(_profit(r) for r in rows)
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


def _strategy_of(row: dict) -> Optional[str]:
    value = str(row.get("strategy", "")).strip()
    return value or None


def monthly_variant_table(rows: list[dict]) -> dict:
    months = sorted({str(r.get("match_time", ""))[:7] for r in rows})
    keys = sorted({_strategy_of(r) for r in rows if _strategy_of(r)})
    table: dict[str, dict] = {}
    for key in keys:
        table[key] = {
            month: _stats(
                [
                    r
                    for r in rows
                    if str(r.get("match_time", ""))[:7] == month
                    and _strategy_of(r) == key
                ]
            )
            for month in months
        }
    return {"months": months, "variants": table}


def walk_forward(rows: list[dict]) -> dict:
    months = sorted({str(r.get("match_time", ""))[:7] for r in rows})
    keys = sorted({_strategy_of(r) for r in rows if _strategy_of(r)})
    steps = []
    for month in months:
        month_rows = [r for r in rows if str(r.get("match_time", ""))[:7] == month]
        steps.append(
            {
                "month": month,
                "strategies": {
                    key: _stats(
                        [r for r in month_rows if _strategy_of(r) == key]
                    )
                    for key in keys
                },
            }
        )
    stitched = {
        key: _stats([r for r in rows if _strategy_of(r) == key]) for key in keys
    }
    return {"months": months, "steps": steps, "stitched": stitched}


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
            "策略是预注册的，不做参数选择；这里只看月度方向是否稳定。",
            "ROI=利润/注数（1 单位平注），使用 consensus 价。",
            "平注、无滑点/交易量；结算按 90 分钟比分。",
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


def _label(key: str) -> str:
    spec = STRATEGIES.get(key)
    return spec.label if spec else key


def print_summary(summary: dict) -> None:
    print(f"Source: {summary['source']} | bets: {summary['total_bets']}")
    print("\nMonthly by strategy")
    for key, data in summary["monthly_variants"].items():
        parts = []
        for month in summary["months"]:
            s = data[month]
            parts.append(
                f"{month}: n={s['n']} hit={_rate(s['hit_rate'])} "
                f"roi={_pct(s['roi'])}"
            )
        print(f"  {_label(key):<18} " + " | ".join(parts))

    print("\nOverall")
    for key, stats in summary["walk_forward"]["stitched"].items():
        print(
            f"  {_label(key):<18} n={stats['n']} "
            f"hit={_rate(stats['hit_rate'])} "
            f"profit={stats['profit']:+.2f} roi={_pct(stats['roi'])}"
        )


def write_markdown(summary: dict, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    months = summary["months"]
    lines = [
        "# 三策略月度稳定性",
        "",
        f"- 输入：`{summary['source']}`",
        f"- 下注数：{summary['total_bets']}",
        f"- 月份：{'、'.join(months) or '-'}",
        "",
        "## 月度表现（consensus 价）",
        "",
        "| 策略 | " + " | ".join(months) + " |",
        "|---|" + "---|" * len(months),
    ]
    labels = {key: _label(key) for key in summary["monthly_variants"]}
    for key, data in summary["monthly_variants"].items():
        cells = []
        for month in months:
            s = data[month]
            cells.append(
                f"n={s['n']} 命中 {_rate(s['hit_rate'])} ROI {_pct(s['roi'])}"
            )
        lines.append(f"| {labels[key]} | " + " | ".join(cells) + " |")

    lines += ["", "## 全样本", "", "| 策略 | 注数 | 命中 | 利润 | ROI |", "|---|---:|---:|---:|---:|"]
    for key, stats in summary["walk_forward"]["stitched"].items():
        lines.append(
            f"| {_label(key)} | {stats['n']} | {_rate(stats['hit_rate'])} | "
            f"{stats['profit']:+.2f} | {_pct(stats['roi'])} |"
        )

    lines += ["", "## 说明", ""]
    for note in summary["notes"]:
        lines.append(f"- {note}")
    lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
