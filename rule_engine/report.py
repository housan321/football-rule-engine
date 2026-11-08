from __future__ import annotations

import html
import json
from pathlib import Path

from .engine import AnalysisResult

CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--surface-2:#f4f3ef;--ink:#0b0b0b;--ink-2:#52514e;--muted:#898781;--border:rgba(11,11,11,.10);--home:#eb6834;--away:#2a78d6;--warning:#fab219;--critical:#d03b3b;--good:#0ca30c;--shadow:0 1px 2px rgba(11,11,11,.04),0 14px 34px rgba(11,11,11,.05)}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--surface-2:#222220;--ink:#fff;--ink-2:#c3c2b7;--muted:#898781;--border:rgba(255,255,255,.10)}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--surface-2:#222220;--ink:#fff;--ink-2:#c3c2b7;--muted:#898781;--border:rgba(255,255,255,.10)}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;font-size:15px;line-height:1.65}
.page{max-width:1180px;margin:0 auto;padding:26px 22px 60px}.topbar{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:20px;color:var(--ink-2);font-size:13px}
.brand{font-weight:650;letter-spacing:-.02em;color:var(--ink)}.chip{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--border);border-radius:999px;padding:5px 12px;font-size:12.5px;background:var(--surface);color:var(--ink-2)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--warning);flex:none}
.card{background:var(--surface);border:1px solid var(--border);border-radius:18px;box-shadow:var(--shadow);padding:26px}
.hero{display:grid;grid-template-columns:1.25fr .95fr;gap:26px;margin-bottom:18px}@media(max-width:900px){.hero{grid-template-columns:1fr}}
h1{font-size:clamp(26px,4vw,40px);line-height:1.2;letter-spacing:-.03em;margin:0 0 8px}.meta{color:var(--ink-2);font-size:13.5px}
.concl{border-left:3px solid var(--home);padding-left:16px}.concl .label{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.concl .value{font-size:22px;font-weight:700;letter-spacing:-.02em;margin:2px 0 8px}
h2{font-size:18px;letter-spacing:-.02em;margin:0 0 14px}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px 26px}@media(max-width:900px){.grid2{grid-template-columns:1fr}}
.field{padding:14px 16px;background:var(--surface-2);border-radius:12px}.field .k{font-size:12px;letter-spacing:.08em;color:var(--muted);text-transform:uppercase;margin-bottom:4px}.field .v{font-size:14.5px;color:var(--ink)}
.risk{list-style:none;margin:0;padding:0;counter-reset:r}.risk li{position:relative;padding-left:34px;margin-bottom:9px;color:var(--ink-2)}
.risk li::before{counter-increment:r;content:counter(r);position:absolute;left:0;top:1px;width:22px;height:22px;border-radius:50%;background:var(--surface-2);border:1px solid var(--border);display:flex;align-items:center;justify-content:center;font-size:12px;color:var(--ink-2)}
.callout{border-left:3px solid var(--home);background:var(--surface-2);border-radius:10px;padding:12px 16px;color:var(--ink-2);font-size:14px}.callout.warn{border-left-color:var(--warning)}.callout.info{border-left-color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:12.8px}th,td{padding:8px 10px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}th{color:var(--muted);font-weight:600;font-size:11.5px;letter-spacing:.06em;text-transform:uppercase}
.tag{display:inline-block;font-size:11.5px;padding:2px 8px;border-radius:999px;border:1px solid var(--border);color:var(--muted);margin-left:6px}
footer{margin-top:22px;color:var(--muted);font-size:12.3px;line-height:1.7}@media (prefers-reduced-motion:reduce){*{transition:none!important}}@media print{.card{break-inside:avoid}body{background:#fff}}
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


def _direction_label(direction: str) -> str:
    return {"home": "主队", "away": "客队"}.get(direction, "观察")


def render_html(result: AnalysisResult) -> str:
    m = result.match
    feat = result.features
    asian = feat.get("asian", {})
    euro = feat.get("euro", {})
    dq = result.data_quality
    score = (
        f"{m.home_score}-{m.away_score}"
        if result.mode == "inplay"
        and m.home_score is not None
        and m.away_score is not None
        else None
    )
    sup_value = result.sup.sup if result.sup and result.sup.available else None

    meta = f"""<!--
---
fixture_id: {m.fixture_id}
competition_id: {m.competition_id}
league: {_yaml_value(m.league)}
home: {_yaml_value(m.home)}
away: {_yaml_value(m.away)}
kickoff: {_yaml_value(m.match_time.strftime("%Y-%m-%d %H:%M"))}
mode: {_yaml_value(result.mode)}
analysis_at: {_yaml_value(result.analysis_time.strftime("%Y-%m-%d %H:%M:%S"))}
score_at_generation: {_yaml_value(score)}
data_asian_latest: {_yaml_value(asian.get("latest_time"))}
data_eu_latest: {_yaml_value(euro.get("latest_time"))}
sup: {_yaml_value(sup_value)}
---
-->"""

    line = float(asian.get("latest_line", 0) or 0)
    if result.direction == "home":
        direction_text = f"{m.home} {line:+.2f}"
    elif result.direction == "away":
        direction_text = f"{m.away} {-line:+.2f}"
    else:
        direction_text = "观察（无方向）"

    rows = []
    for hit in result.rule_hits:
        rows.append(
            "<tr>"
            f"<td>{_esc(hit.code)}</td>"
            f"<td>{_esc(hit.name)}</td>"
            f"<td>{_esc(_direction_label(hit.direction))}</td>"
            f"<td>{hit.strength:g}</td>"
            f"<td>{_esc(hit.reason)}</td>"
            "</tr>"
        )
    rule_table = (
        "<table><thead><tr><th>规则</th><th>名称</th><th>方向</th><th>权重</th><th>依据</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )

    conflicts = (
        "<ul class='risk'>"
        + "".join(f"<li>{_esc(x)}</li>" for x in result.conflicts)
        + "</ul>"
        if result.conflicts
        else "<p style='margin:0;color:var(--ink-2)'>无规则冲突。</p>"
    )
    invalidation = "<ul class='risk'>" + "".join(
        f"<li>{_esc(x)}</li>" for x in result.invalidation
    ) + "</ul>"

    left_side = m.home if result.direction == "home" else m.away if result.direction == "away" else "无"
    lower_side = m.away if result.direction == "home" else m.home if result.direction == "away" else "无"
    side_text = (
        f"{result.side_line:+.2f}" if result.side_line is not None else "-"
    )
    side_kind = (
        "让球方"
        if result.is_giving
        else ("受让/PK" if result.side_line is not None else "-")
    )
    filter_html = ""
    if result.filter_notes:
        filter_html = (
            "<div class='callout info' style='margin-top:16px'><strong>实验过滤：</strong>"
            + "；".join(_esc(x) for x in result.filter_notes)
            + "</div>"
        )

    return f"""<!DOCTYPE html>
{meta}
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(m.home)} VS {_esc(m.away)} · Phase 1 规则引擎 · {m.fixture_id}</title>
<style>{CSS}</style>
</head>
<body>
<div class="page">
  <header class="topbar">
    <div class="brand">Phase 1 · 盘口规则引擎（无 LLM）</div>
    <div><span class="chip"><span class="dot"></span>{_esc(result.stake)} · 置信度 {_esc(result.confidence_label)} · {'实验默认' if result.experimental_default else '非实验默认'}</span></div>
  </header>

  <section class="card hero">
    <div>
      <h1>{_esc(m.home)} <span style="color:var(--muted);font-weight:400">vs</span> {_esc(m.away)}</h1>
      <div class="meta">
        {_esc(m.league or "未知联赛")} · {m.match_time.strftime("%Y-%m-%d %H:%M")}（北京时间）· fixture {m.fixture_id}<br>
        模式 {_esc(result.mode)} · 亚盘最新 {_esc(asian.get("latest_time") or "-")} · 欧赔最新 {_esc(euro.get("latest_time") or "-")} · 分析 {result.analysis_time.strftime("%Y-%m-%d %H:%M:%S")}
      </div>
    </div>
    <div class="concl">
      <div class="label">规则引擎结论</div>
      <div class="value">{_esc(direction_text)}</div>
      <div class="meta">置信度 {result.confidence}/100（{_esc(result.confidence_label)}）· {_esc(result.stake)}</div>
    </div>
  </section>

  <section class="card" id="conclusion">
    <h2>结论</h2>
    <div class="grid2">
      <div class="field"><div class="k">方向</div><div class="v">{_esc(direction_text)}<span class="tag">Phase 1 规则</span></div></div>
      <div class="field"><div class="k">操作级别</div><div class="v">{_esc(result.stake)}（最高仅小注）</div></div>
      <div class="field"><div class="k">规则得分</div><div class="v">主 {result.score_home:g} / 客 {result.score_away:g} · 冲突比 {result.conflict_ratio:.2f}</div></div>
      <div class="field"><div class="k">一句话</div><div class="v">{_esc(result.summary)}</div></div>
      <div class="field"><div class="k">亚盘数据</div><div class="v">{dq.get("asian_companies", 0)} 家核心公司 · 年龄 {dq.get("asian_age_minutes")} 分钟 · 上盘 {'主队' if asian.get("upper_side") == 'home' else '客队' if asian.get("upper_side") == 'away' else '无'} · 线 {float(asian.get("latest_line", 0) or 0):+.2f}</div></div>
      <div class="field"><div class="k">欧赔数据</div><div class="v">{euro.get("companies", 0)} 家 · 主胜 {euro.get("latest_probs_pct", [0,0,0])[0]}% / 平 {euro.get("latest_probs_pct", [0,0,0])[1]}% / 客 {euro.get("latest_probs_pct", [0,0,0])[2]}%</div></div>
      <div class="field"><div class="k">实验默认（让球方 + conf≥50）</div><div class="v">{'符合' if result.experimental_default else '不符合'} · side_line {side_text}（{side_kind}）</div></div>
      <div class="field"><div class="k">可下注</div><div class="v">{'是' if result.actionable else '否'} · 操作 {_esc(result.stake)}</div></div>
    </div>
    {filter_html}

    <h2 style="margin-top:24px">规则审计</h2>
    {rule_table}

    <h2 style="margin-top:24px">规则冲突</h2>
    {conflicts}

    <h2 style="margin-top:24px">失效条件</h2>
    {invalidation}

    <h2 style="margin-top:24px">数据质量</h2>
    <div class="callout info">
      核心亚盘公司 {dq.get("asian_companies", 0)} 家；欧赔 {dq.get("euro_companies", 0)} 家；
      sup { "可用" if dq.get("sup_available") else "缺失" }（{_esc(dq.get("sup_detail", ""))}）。
      { "被剔除：" + "、".join(x["company"] + "（" + x["reason"] + "）" for x in dq.get("excluded", [])) if dq.get("excluded") else "无剔除公司。"}
    </div>
  </section>

  <footer>
    数据截止：亚盘 {_esc(asian.get("latest_time") or "-")} / 欧赔 {_esc(euro.get("latest_time") or "-")}。
    来源：odds_live_all_raw（公司 2/3/7/11，line_no=0）、odds_history_eu（公司 2/3/5/7/9/22）。
    本报告由 Phase 1 确定性规则引擎生成，不含 LLM、不含基本面（伤停/首发/战意），置信度最高 55。
    规则依据见 SKILL.md / manipulation_tactics.md / yapai_framework.md。非投注建议 / 价量局限。
  </footer>
</div>
</body>
</html>
"""


def write_outputs(result: AnalysisResult, out_dir: str | Path) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base = f"{result.match.home}VS{result.match.away}_{result.match.fixture_id}"
    html_path = out / f"{base}.html"
    json_path = out / f"{base}.json"
    html_path.write_text(render_html(result), encoding="utf-8")
    json_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return html_path, json_path
