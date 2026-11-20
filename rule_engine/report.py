from __future__ import annotations

import html
import json
from pathlib import Path

from .engine import AnalysisResult

CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--surface-2:#f4f3ef;--ink:#0b0b0b;--ink-2:#52514e;--muted:#898781;--border:rgba(11,11,11,.10);--home:#eb6834;--away:#2a78d6;--warning:#fab219;--critical:#d03b3b;--good:#0ca30c;--shadow:0 1px 2px rgba(11,11,11,.04),0 14px 34px rgba(11,11,11,.05)}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--surface-2:#222220;--ink:#fff;--ink-2:#c3c2b7;--muted:#898781;--border:rgba(255,255,255,.10)}}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;font-size:15px;line-height:1.65}
.page{max-width:1100px;margin:0 auto;padding:26px 22px 60px}.topbar{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:20px;color:var(--ink-2);font-size:13px}
.brand{font-weight:650;letter-spacing:-.02em;color:var(--ink)}.chip{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--border);border-radius:999px;padding:5px 12px;font-size:12.5px;background:var(--surface);color:var(--ink-2)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--warning);flex:none}.dot.on{background:var(--good)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:18px;box-shadow:var(--shadow);padding:26px;margin-bottom:18px}
.hero{display:grid;grid-template-columns:1.25fr .95fr;gap:26px}@media(max-width:900px){.hero{grid-template-columns:1fr}}
h1{font-size:clamp(24px,4vw,36px);line-height:1.2;letter-spacing:-.03em;margin:0 0 8px}.meta{color:var(--ink-2);font-size:13.5px}
.concl{border-left:3px solid var(--home);padding-left:16px}.concl .label{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.concl .value{font-size:22px;font-weight:700;letter-spacing:-.02em;margin:2px 0 8px}
h2{font-size:17px;letter-spacing:-.02em;margin:0 0 14px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px 26px}@media(max-width:900px){.grid2{grid-template-columns:1fr}}
.field{padding:14px 16px;background:var(--surface-2);border-radius:12px}.field .k{font-size:12px;letter-spacing:.08em;color:var(--muted);text-transform:uppercase;margin-bottom:4px}.field .v{font-size:14.5px;color:var(--ink)}
.callout{border-left:3px solid var(--home);background:var(--surface-2);border-radius:10px;padding:12px 16px;color:var(--ink-2);font-size:14px}
.risk{list-style:none;margin:0;padding:0;counter-reset:r}.risk li{position:relative;padding-left:34px;margin-bottom:9px;color:var(--ink-2)}
.risk li::before{counter-increment:r;content:counter(r);position:absolute;left:0;top:1px;width:22px;height:22px;border-radius:50%;background:var(--surface-2);border:1px solid var(--border);display:flex;align-items:center;justify-content:center;font-size:12px;color:var(--ink-2)}
table{width:100%;border-collapse:collapse;font-size:12.8px}th,td{padding:8px 10px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}th{color:var(--muted);font-weight:600;font-size:11.5px;letter-spacing:.06em;text-transform:uppercase}
.tag{display:inline-block;font-size:11.5px;padding:2px 8px;border-radius:999px;border:1px solid var(--border);color:var(--muted);margin-left:6px}
footer{margin-top:22px;color:var(--muted);font-size:12.3px;line-height:1.7}
"""


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def _yaml_value(value) -> str:
    if value is None:
        return "null"
    text = str(value).replace("\n", " ")
    if any(ch in text for ch in (":", "#", '"', "'")):
        return '"' + text.replace('"', "'") + '"'
    return text


def _side_label(direction: str) -> str:
    return {"home": "主队", "away": "客队"}.get(direction, "观察")


def _move_label(direction: str) -> str:
    return {"home": "主队", "away": "客队"}.get(direction, "未动")


def _fmt(value, digits: int = 2, signed: bool = False) -> str:
    if value is None:
        return "-"
    return f"{value:+.{digits}f}" if signed else f"{value:.{digits}f}"


def _snapshot_row(snapshot: dict | None, label: str) -> str:
    if not snapshot:
        return (
            f"<tr><td>{_esc(label)}</td><td colspan='5' style='color:var(--critical)'>"
            "无可用快照</td></tr>"
        )
    return (
        "<tr>"
        f"<td>{_esc(label)}</td>"
        f"<td>{_esc(snapshot['cutoff'][11:16])}</td>"
        f"<td>{_fmt(snapshot['line'], signed=True)}</td>"
        f"<td>{_fmt(snapshot['home_water'], 2)}</td>"
        f"<td>{_fmt(snapshot['away_water'], 2)}</td>"
        f"<td>{snapshot['companies']} / {snapshot['total_companies']}</td>"
        "</tr>"
    )


def render_html(result: AnalysisResult) -> str:
    m = result.match
    feat = result.features
    asian = feat.get("asian", {})
    dq = result.data_quality
    prev = asian.get("prev")
    decision = asian.get("decision")

    line = result.line
    if result.direction == "home" and line is not None:
        direction_text = f"{m.home} {line:+.2f}"
    elif result.direction == "away" and line is not None:
        direction_text = f"{m.away} {-line:+.2f}"
    else:
        direction_text = "观察（未触发）"

    side_text = f"{result.side_line:+.2f}" if result.side_line is not None else "-"
    side_kind = (
        "让球方"
        if result.is_giving
        else ("受让/PK" if result.side_line is not None else "-")
    )
    water_text = _fmt(result.water, 3)
    best_text = (
        f"{result.best_water:.3f} @ {result.best_company}"
        if result.best_water is not None
        else "-"
    )

    quote_rows = []
    for quote in (decision or {}).get("quotes", []):
        quote_rows.append(
            "<tr>"
            f"<td>{_esc(quote['company'])}</td>"
            f"<td>{_esc(quote['change_time'][11:19])}</td>"
            f"<td>{_fmt(quote['line'], signed=True)}</td>"
            f"<td>{_fmt(quote['home_water'], 3)}</td>"
            f"<td>{_fmt(quote['away_water'], 3)}</td>"
            "</tr>"
        )
    quote_table = (
        "<table><thead><tr><th>公司</th><th>报价时间</th><th>盘口</th>"
        "<th>主水</th><th>客水</th></tr></thead><tbody>"
        + "".join(quote_rows)
        + "</tbody></table>"
    )

    hit = result.rule_hits[0] if result.rule_hits else None
    audit = (
        "<table><thead><tr><th>规则</th><th>名称</th><th>方向</th><th>依据</th>"
        "</tr></thead><tbody>"
        f"<tr><td>{_esc(hit.code)}</td><td>{_esc(hit.name)}</td>"
        f"<td>{_esc(_side_label(hit.direction))}</td>"
        f"<td>{_esc(hit.reason)}</td></tr>"
        "</tbody></table>"
        if hit
        else "<p>无规则记录。</p>"
    )
    invalidation = "<ul class='risk'>" + "".join(
        f"<li>{_esc(x)}</li>" for x in result.invalidation
    ) + "</ul>"

    return f"""<!DOCTYPE html>
<!--
---
fixture_id: {m.fixture_id}
league: {_yaml_value(m.league)}
home: {_yaml_value(m.home)}
away: {_yaml_value(m.away)}
kickoff: {_yaml_value(m.match_time.strftime("%Y-%m-%d %H:%M"))}
strategy: {_yaml_value(result.strategy)}
strategy_label: {_yaml_value(result.strategy_label)}
prev_at: {_yaml_value(result.prev_time.strftime("%Y-%m-%d %H:%M:%S"))}
decision_at: {_yaml_value(result.analysis_time.strftime("%Y-%m-%d %H:%M:%S"))}
triggered: {_yaml_value(result.triggered)}
direction: {_yaml_value(result.direction)}
side_line: {_yaml_value(result.side_line)}
water: {_yaml_value(result.water)}
---
-->
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(m.home)} VS {_esc(m.away)} · {_esc(result.strategy_label)} · {m.fixture_id}</title>
<style>{CSS}</style>
</head>
<body>
<div class="page">
  <header class="topbar">
    <div class="brand">盘口-水位背离引擎 · {_esc(result.strategy_label)}</div>
    <div><span class="chip"><span class="dot {'on' if result.triggered else ''}"></span>
      {'触发 · 1 单位' if result.triggered else '未触发 · 仅观察'}</span></div>
  </header>

  <section class="card hero">
    <div>
      <h1>{_esc(m.home)} <span style="color:var(--muted);font-weight:400">vs</span> {_esc(m.away)}</h1>
      <div class="meta">
        {_esc(m.league or "未知联赛")} · {m.match_time.strftime("%Y-%m-%d %H:%M")}（北京时间）· fixture {m.fixture_id}<br>
        比较 {_esc(result.prev_time.strftime("%H:%M"))} → 决策 {_esc(result.analysis_time.strftime("%H:%M"))}
        · 价格口径 {_esc(result.price_mode)}
      </div>
    </div>
    <div class="concl">
      <div class="label">结论</div>
      <div class="value">{_esc(direction_text)}</div>
      <div class="meta">{_esc(result.strategy_label)} · {_esc(result.stake)}</div>
    </div>
  </section>

  <section class="card">
    <h2>共识快照</h2>
    <table>
      <thead><tr><th>时点</th><th>时间</th><th>主盘口</th><th>主水</th><th>客水</th>
      <th>主盘口公司 / 有报价公司</th></tr></thead>
      <tbody>
        {_snapshot_row(prev, "基准")}
        {_snapshot_row(decision, "决策")}
      </tbody>
    </table>
    <div class="grid2" style="margin-top:18px">
      <div class="field"><div class="k">Δ盘口</div><div class="v">{_fmt(result.line_delta, 2, True)} · 朝{_move_label(result.line_direction)}</div></div>
      <div class="field"><div class="k">Δ主水 / Δ客水</div><div class="v">{_fmt(result.home_water_delta, 2, True)} / {_fmt(result.away_water_delta, 2, True)}</div></div>
      <div class="field"><div class="k">相对水位（Δ主水-Δ客水）</div><div class="v">{_fmt(result.rel_water, 2, True)} · 朝{_move_label(result.water_direction)}</div></div>
      <div class="field"><div class="k">方向关系</div><div class="v">{'盘口与水位相反（conflict）' if asian.get('conflict') else '同向或未动'}</div></div>
      <div class="field"><div class="k">下注方向</div><div class="v">{_esc(direction_text)}</div></div>
      <div class="field"><div class="k">下注盘口 / 水位</div><div class="v">{side_text}（{side_kind}） / {water_text}</div></div>
      <div class="field"><div class="k">best 价</div><div class="v">{_esc(best_text)}</div></div>
      <div class="field"><div class="k">可下注</div><div class="v">{'是 · 1 单位' if result.actionable else '否'}</div></div>
    </div>
    <div class="callout" style="margin-top:18px">{_esc(result.reason)}</div>
  </section>

  <section class="card">
    <h2>决策快照公司明细</h2>
    {quote_table}
    <p class="meta">只列出落在主盘口上的公司；水位为这些公司的简单平均。</p>
  </section>

  <section class="card">
    <h2>规则审计</h2>
    {audit}
    <h2 style="margin-top:24px">失效条件</h2>
    {invalidation}
  </section>

  <footer>
    规则来源：《规则回测分析结果.docx》 — conflict T-10m（T-30m→T-10m）、
    conflict T-120m（T-3h→T-2h）、water T-60m（T-2h→T-1h）。<br>
    阈值：|Δ盘口| ≥ 0.25，|相对水位| ≥ 0.06；conflict 要求方向相反，water 要求盘口未动。<br>
    数据：odds_live_all，line_no=0，亚盘公司 2/3/6/7/10/13/22/23。非投注建议。
  </footer>
</div>
</body>
</html>
"""


def write_outputs(result: AnalysisResult, out_dir: str | Path) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base = (
        f"{result.match.home}VS{result.match.away}_"
        f"{result.match.fixture_id}_{result.strategy}"
    )
    html_path = out / f"{base}.html"
    json_path = out / f"{base}.json"
    html_path.write_text(render_html(result), encoding="utf-8")
    json_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return html_path, json_path
