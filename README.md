# Phase 1 · 盘口规则引擎

一个**独立项目**：把足球盘口分析中的确定性规则脚本化，只读取数据库里的亚盘/欧赔变化轨迹，不调用 LLM，不依赖原项目的任何代码模块。

> 它与 `D:\football-handicap-analysis` 的关系仅限于**共用同一个 MySQL 数据库**；代码、配置、输出目录完全独立。

> **完整分析手册与回测纪要：** `docs/ANALYSIS_PLAYBOOK.md`
> （当前实盘规则、T-60 口径、回测结果、已知限制、复现命令）

## 目标

- 对单场比赛输出可复现的方向、置信度、规则审计日志
- 对未来某一天的比赛批量扫描，筛出“值得进一步分析”的场次
- 为后续 Phase 2（回测）和 Phase 3（接 LLM）提供稳定的 evidence 接口

## 不做什么

- 不查新闻、不判断伤停/首发/战意
- 不预测比分
- 不做“强推”
- 不替代 LLM 的深度判断

## 安装

```bash
cd D:\football-rule-engine
python -m pip install -r requirements.txt
copy .env.example .env
# 编辑 .env，填入 MySQL 密码
```

Python 3.10+。

## 使用

```bash
# 一键：导入 titan007 数据 + 分析（推荐）
python -m rule_engine run --ids 3010473 --decision-minutes 60 --out-dir out/t60
python -m rule_engine run --ids 3010473,3000518 --decision-minutes 60

# 一键：导入 + 分析全部 + 把 actionable 写入 CSV（默认 tracking/paper_tracking.csv）
python -m rule_engine run --ids 3010473 --experimental-default
python -m rule_engine run --leagues 9 --dates 2026-11-07 --experimental-default

# 扫描未来 N 分钟内开赛的所有联赛；赔率数据不足的场次自动跳过
python -m rule_engine run --within-minutes 60 --experimental-default
python -m rule_engine run --within-minutes 60 --experimental-default --skip-import --no-track

# 自定义 CSV 路径 / 关闭 CSV 跟踪
python -m rule_engine run --ids 3010473 --experimental-default --excel out/my_tracking.csv
python -m rule_engine run --ids 3010473 --experimental-default --no-track

# 只分析、不写 CSV
python -m rule_engine analyze --ids 3010473,3000518 --experimental-default --out-dir out/exp

# 也可以分开传
python -m rule_engine analyze --ids 3010473 --decision-minutes 60 --only-giving --min-confidence 50

# 按日期 + 联赛ID 自动发现/导入/分析（联赛ID = SclassID / competition_id）
python -m rule_engine run --dates 2026-11-05 --leagues 350 --decision-minutes 60 --out-dir out/t60
python -m rule_engine run --dates 2026-11-05,2026-11-06 --leagues 103,350 --max-fixtures 20

# 单场分析（自动判断赛前/滚球）
python -m rule_engine analyze --fixture-id 3068888

# 多场分析（逗号或空格分隔均可；自动去重，单场失败不影响其他场）
python -m rule_engine analyze --ids 3068888,3069034
python -m rule_engine analyze --ids 3068888 3069034

# 指定模式与输出目录
python -m rule_engine analyze --fixture-id 3068888 --mode pre --out-dir out

# 按每场各自开赛前 60 分钟分析（多场各自取 T-60，隐含 pre 模式）
python -m rule_engine analyze --ids 3010473 3000518 --decision-minutes 60 --out-dir out/t60

# 扫描某天，但每场都取各自开赛前 60 分钟
python -m rule_engine scan --date 2026-11-05 --decision-minutes 60 --write --out-dir out/t60_scan

# 固定分析时点（所有场次共用同一个时间戳）
python -m rule_engine analyze --fixture-id 3068888 --as-of "2026-11-05 20:00"

# 只输出 JSON（审计日志；多场时输出 JSON 数组）
python -m rule_engine analyze --fixture-id 3068888 --json-only
python -m rule_engine analyze --ids 3068888,3069034 --json-only

# 回放：指定固定分析时点（所有场次共用）
python -m rule_engine analyze --fixture-id 3068888 --as-of "2026-11-05 20:05"

# 回放：每场各自开赛前 N 分钟（推荐，与回测口径一致）
python -m rule_engine analyze --fixture-id 3068888 --decision-minutes 60

# 扫描某一天，只显示有方向的场次
python -m rule_engine scan --date 2026-11-05 --only-actionable

# 扫描并写入每场 HTML/JSON
python -m rule_engine scan --date 2026-11-05 --write --out-dir out

# Phase 2：历史回测（T-60 决策，置信度 >=50 才下注）
python -m rule_engine backtest --from 2025-08-16 --to 2026-11-09 \
  --decision-minutes 60 --min-confidence 50 \
  --out out/backtest.csv --summary out/backtest_summary.json
```

> `--dates` 与 `--leagues` 互斥于 `--ids`/`--fixture-id`。
> 流程：先从 `bf.titan007.com` 赛程页发现 fixture；发现失败或为空时，
> 回退到 `match_list_all` 按日期 + `competition_id` 查询。
> `--leagues` 使用 SclassID / `competition_id`（逗号分隔），`--max-fixtures` 限制数量。

`run` 的常用开关：

| 开关 | 作用 |
|---|---|
| `--skip-import` | 只分析，不重新抓数据 |
| `--skip-analysis` | 只导入，不分析 |
| `--import-dry-run` | 只抓取/解析不写库，然后分析现有数据 |
| `--raise-on-import-error` | 导入失败时不继续分析 |
| `--eu-companies` / `--timeout` / `--retries` / `--no-save` | 原样传给 ingest |
| `--decision-minutes 60` | 每场各自取开赛前 60 分钟分析 |
| `--experimental-default` | 让球方 + conf≥50 + T-60 |
| `--only-giving` / `--min-confidence N` | 单独控制方向/置信度过滤 |
| `--excel` | 纸面跟踪 CSV 路径，默认 `tracking/paper_tracking.csv` |
| `--no-track` | 不写 CSV，只分析 |
| `--no-report` | 不写 `out/` 下的 HTML/JSON |
| `--ci` | CI 模式：`--no-report` + `--no-save`，不生成 `out/`、`data/` |
| `--within-minutes N` | 扫描所有联赛未来 N 分钟内开赛的比赛 |
| `--min-asian-companies` / `--min-eu-companies` | 赔率不足时跳过的阈值，默认 2/2 |

> `--within-minutes` 默认只用**联赛白名单**（`rule_engine/leagues.py`）。
> 先从 `bf.titan007.com` 赛程页**爬取**未来 N 分钟内开赛的比赛并入库，再分析；
> **已入库的比赛也会重新抓取刷新**，不只是新发现场次。
> 赛程页日期不一定等于北京时间开赛日，扫描会同时读取前一天页面，
> 避免漏掉 00:xx / 01:00 的比赛。
> `--skip-import` 才只扫描数据库已有比赛。`--max-fixtures` 限制发现/分析的场次。
> `--leagues` 同时作用于赛程发现和数据库刷新；`--all-leagues` 关闭白名单。
> 亚盘核心公司 < 阈值或欧赔公司 < 阈值时标记 `[skip]`，不生成 HTML/JSON，也不写入 CSV。

> `--as-of` 是**所有场次共用的固定时间戳**，适合复现某一场或某一天同一时刻的决策。
> `--decision-minutes` 是**逐场计算** `开赛时间 - N 分钟`，适合批量生成真正的 T-60 赛前分析；
> 它隐含 `pre` 模式，不能与 `--as-of` 同时使用。
>
> 如果赛后用 `scan --date` 而不带 `--decision-minutes`，分析时点会是“现在”，
> 已开赛的比赛会进入 `inplay` 模式并读取整场滚球轨迹，这不是赛前 T-60 口径。

输出：

- `out/<主队>VS<客队>_<fixture>.html`：仅结论段 HTML
- `out/<主队>VS<客队>_<fixture>.json`：完整审计日志（特征、规则、冲突、数据质量）

## 规则范围（Phase 1）

### 数据质量

- 亚盘白名单 `2/3/7/9/11/22`，核心 `2/3/7/11`
- 欧赔白名单 `2/3/5/7/9/22`
- 只取 `line_no=0`
- 剔除“n=1 且距开赛 >12 小时”的陈旧公司
- 亚盘数据年龄：滚球 >15 分钟、赛前 >6 小时 → 置信度封顶 40
- 核心公司 <2 家 → 封顶 45
- 欧赔公司 <3 家 → 封顶 50
- sup 缺失 / Phase 1 无基本面 → 封顶 55

### 特征

- 开盘/最新盘口、开盘/最新水位
- 线路径（升盘/退盘）、水路径（升水/降水）
- 公司一致性、最新盘口极差、澳门是否反向
- 早变比例（开赛前 3 小时以上）
- 欧赔主/平/客去水概率、方向漂移、返还率
- 上盘/下盘识别（让球方 = 上盘）

### 规则

| 代码 | 含义 |
|---|---|
| `WL_UPPER_ULTRA_LOW` | 上盘 ≤0.75 超低水 |
| `WL_UPPER_LOW` | 上盘 0.76–0.85 低水 |
| `WL_UPPER_HIGH` | 上盘 ≥0.975 高水（T-03，方向不明时观察） |
| `WL_UPPER_ULTRA_HIGH` | 上盘 ≥1.10 超高水 |
| `WL_UPPER_099` | 上盘长期 0.99 水（T-04） |
| `WL_LOWER_ULTRA_LOW` | 下盘 ≤0.75 超低水 |
| `T01_EARLY_DEEPEN_FAV` | 早段升盘 + 上盘热门（T-01） |
| `T01_EARLY_DEEPEN_DOG` | 早段升盘 + 上盘冷门（T-01 反向） |
| `MOV_DEEPEN_SUPPORT` | 升盘 + 降水（临场真实支持） |
| `MOV_DEEPEN_WATER_UP` | 升盘 + 升水（阻上/赶盘并存，观察） |
| `T02_RETREAT_LOW_WATER` | 退盘 + 低水（T-02 诱上） |
| `MOV_RETREAT_WATER_UP` | 退盘 + 升水（真实看衰上盘） |
| `MOV_WATER_ONLY` | 盘口不变 + 水位单边 |
| `EURO_ASIA_ALIGN` | 欧亚同向 |
| `EURO_ASIA_DIVERGE` | 欧亚背离（观察） |
| `EURO_FAV_DRIFT` | 欧赔热门方向漂移 |
| `COMPANY_DIVERGENCE` | 公司盘口分歧过大（观察） |
| `MACAU_OPPOSITE` | 澳门与多数公司反向（观察） |

### 评分与置信度

- 每条方向性规则有 0–1.5 的权重
- 主/客得分相减得到净方向
- 方向性规则冲突比 ≥0.6 → 强制观察，置信度封顶 40
- 净得分绝对值 <1.0 → 观察
- 置信度最高 55；≥45 且净得分 ≥1.0 才允许“小注”，否则“仅观察”

## 导入数据（独立爬虫）

新项目自带完整的 titan007 导入栈，**不依赖原项目**：

```bash
cd D:\football-rule-engine

# 单场：亚盘 + 欧赔
python -m rule_engine import --ids 3010473

# 多场
python -m rule_engine import --ids 3010473,3073201

# 按赛程日期/联赛批量发现
python -m rule_engine import --dates 2026-11-05 --leagues 192

# 只抓取/解析，不写库
python -m rule_engine import --ids 3010473 --dry-run

# 查看导入参数
python -m rule_engine import --help
# 或
python -m rule_engine.ingest --help
```

写入的表：

- `match_list_all`
- `odds_live_all`
- `odds_live_all_raw`
- `odds_history_eu`
- `data_import_log`

要求：

- `pymysql`（已在 `requirements.txt`）
- 系统 `openssl` 在 PATH（找不到时用 `--openssl-bin` 或 `OPENSSL_BIN`）
- 项目根目录 `.env` 指向目标 MySQL

> `python -m rule_engine import ...` 会把后面的参数原样转发给 `rule_engine.ingest`；
> 独立运行也可以直接使用 `python -m rule_engine.ingest ...`。
>
> 亚盘页偶尔会返回空盘口（`no main odds records`）。导入器会自动重试
> `--retries` 次（默认 2，即最多 3 次）；仍为空时该场不会写入 `match_list_all`，
> 后续分析会报 “fixture not found”。可加大 `--retries` 或稍后重跑。

## Phase 2：历史回测

`backtest` 命令用 `match_list_all` 的最终比分结算规则信号：

- 决策时点：`开赛时间 - decision_minutes`（默认 60 分钟）
- 只使用决策时点之前的亚盘/欧赔，避免未来函数
- 用 `match_list_all.home_score / away_score` 结算亚盘，支持半赢/半输/走盘
- 只对 `stake=小注` 且 `confidence >= min_confidence` 的信号下注（默认 45）
- `--limit` 默认 500 场；全量样本（2718 场）需要显式传 `--limit 5000`
- 输出逐场 CSV 和按规则/联赛/置信度/盘口/月份分桶的 JSON 汇总

```bash
python -m rule_engine backtest --from 2025-08-16 --to 2026-11-09 \
  --decision-minutes 60 --min-confidence 50 --limit 5000 \
  --out out/backtest.csv --summary out/backtest_summary.json
```

本次样本（2025-08-16 → 2026-11-09，2718 场有比分且亚盘/欧赔齐全；2026-11-09 数据库扩充历史数据后重跑）：

| 时期 | conf45 | conf50 | conf50_giving | conf50_receiving |
|---|---:|---:|---:|---:|
| 2025-08 ~ 2025-12 | 163 注 / -5.1% | 145 注 / -6.0% | 93 注 / +11.5% | 52 注 / -37.2% |
| 2026-01 ~ 2026-05 | 184 注 / -5.4% | 165 注 / -6.4% | 97 注 / +9.4% | 68 注 / -28.9% |
| 2026-08 ~ 2026-11 | 201 注 / +1.3% | 169 注 / +2.4% | 98 注 / +21.6% | 71 注 / -24.1% |

全期合并：

| 变体 | 下注 | 命中率 | ROI | 利润 |
|---|---:|---:|---:|---:|
| `conf45_all` | 548 | 50.5% | -2.9% | -15.61 |
| `conf50_all` | 479 | 50.6% | -3.2% | -15.20 |
| `conf45_giving` | 315 | 61.6% | **+16.4%** | +51.60 |
| `conf50_giving` | 288 | 60.7% | **+14.2%** | +40.95 |
| `conf50_receiving` | 191 | 32.7% | **-29.4%** | -56.15 |

初步观察：

- **不加盘口过滤时，conf45/conf50 全期为负**；`conf≥50` 单独使用不是可盈利规则。
- **“只做让球方”是唯一稳定为正的分组**：conf50_giving 288 注、60.7% 命中、+14.2% ROI，三个时期分别 +11.5% / +9.4% / +21.6%。
- **受让方/PK 三个阶段全负**：-37.2% / -28.9% / -24.1%；全期 -29.4%。
- **45–49 桶整体 -0.6%，但结构分化**：让球方 27 注 / 70.4% 命中 / **+39.4%** ROI；受让方 42 注 / 35.1% / -26.4%。所以 45–49 只能和“只做让球方”一起用，单独把门槛降到 45 没有优势。
- **让球方口径下 conf45 略优于 conf50**：315 注 / +16.4% vs 288 注 / +14.2%；增量 45–49 让球方 27 注 / +39.4%（95% CI +7%～+72%，p≈0.02，未校正多重比较）。样本仍小，建议作为实验档而不是直接改默认。
- 决策时点敏感性：T-30 giving +16.3% / T-60 +14.2% / T-120 +12.1%。T-30 的总体优势**不显著**（p≈0.77），且独立的 2025H2 段仅 +0.2%（T-60 +11.5%）；两者只有 182 场重叠，T-30 波动更大。默认仍用 T-60，详见 `docs/T30_vs_T60_2026-11-09.md`。
- 同时下注 T-60 + T-30（各 1 单位）：578 注 / +88.24 / ROI +15.3%，但 182 场重叠中 177 场结果相同，基本是**加杠杆而非分散**；最大回撤 -12.47（T-60 -4.28）。更好的合并是“每场一注、T-60 优先 + T-30 独有”：396 注 / +62.51 / ROI +15.8% / 回撤 -6.28。详见 `docs/T30_T60_union_2026-11-09.md`。
- 新扩充的 2025 年段让球方过滤仍为正（+11.5%），说明不是纯 2026 样本内拟合。

⚠️ 这是**全样本、样本内分组**结果。Phase 3 已用 walk-forward 做了月度验证；“只做让球方”通过后才建议增加实验开关，而不是直接改默认。

详细分桶（置信度/盘口/规则/联赛/月份）见 `docs/PHASE2_2026-11-09.md`。

规则消融（关闭规则后重新评分）的结论见 `docs/RULE_ABLATION_2026-11-09.md`。该分析用一次性本地脚本完成，当前 CLI **没有**保留 `--disable-rule` 入口；如需复跑，需要重新加消融参数。

## Phase 3：Walk-forward 样本外验证（扩展样本）

```bash
cd D:\football-rule-engine

# 用全期合并的 conf>=45 CSV 做 walk-forward（推荐，秒级）
python -m rule_engine walkforward --input out/backtest_conf45.csv \
  --out out/phase3_walkforward_all.json \
  --report out/phase3_walkforward_all.md

# 也可以直接重跑回测再验证（较慢）
python -m rule_engine walkforward --from 2025-08-16 --to 2026-11-09 \
  --decision-minutes 60 --limit 5000 --out out/phase3_walkforward.json
```

方法：

- 按自然月滚动，测试月只用之前月份的数据选择变体
- 候选：`conf50_all`（默认）、`conf50_giving`、`conf45_giving`
- 控制组：`conf50_receiving`（受让方/PK）

扩展样本 T-60 结果（2026-11-09 重跑，输入 548 注 conf≥45）：

- 月份：2025-08 → 2026-11（2026-06/07 无数据），共 14 个月
- **Walk-forward 拼接：312 注，61.2% 命中，+49.63 单位，ROI +15.9%**
- `conf50_giving`：14 个月中 11 个月为正；负月份为 2025-09（-0.6%）、2025-10（-14.6%）、2026-04（-42.8%）
- `conf50_receiving`：14 个月全部为负
- 选择路径：2025-08 默认 `conf50_all`；2025-09～12 选择 `conf50_giving`；2026-01 起持续选择 `conf45_giving`

⚠️ 候选变体是在看到数据后提出的，仍有选择偏差；但 2025 年段是独立窗口，让球方过滤在该段仍为正（conf50_giving +11.5%），比只看 2026 年更强。目前**不改变实盘默认**，下一步是增加 `--only-giving` 实验开关并纸面跟踪。

## 实验默认 gate（Phase 3 Step 1）

```bash
# 等价于 --only-giving + --min-confidence 50；未给时间时自动 T-60
python -m rule_engine analyze --ids 3010473 --experimental-default
python -m rule_engine run --ids 3010473 --experimental-default
```

规则：

- 只做**让球方**（`side_line < 0`，主队让/客队让都算）
- 置信度 ≥50
- 45–49 桶在让球方内为正（27 注 / +39.4%），可显式用 `--only-giving --min-confidence 45` 做更激进的实验档；默认仍用 50
- 未显式传 `--decision-minutes` / `--as-of` 时，自动用 T-60
- 受让方/PK 或置信度不足的场次**保留方向**，但操作变为“仅观察”，不会消失

JSON/HTML 新增字段：

| 字段 | 含义 |
|---|---|
| `side_line` | 下注方视角盘口，负数=让球 |
| `is_giving` | 是否让球方 |
| `experimental_default` | 是否符合“让球方 + conf≥50” |
| `actionable` | 经过 `--only-giving` / `--min-confidence` 过滤后是否可下注 |
| `filter_notes` | 被过滤的原因 |

> 当前默认行为没有改变：不加参数时仍是 conf≥45、主客都做。实验 gate 需要显式 `--experimental-default` 或两个过滤参数。

## 纸面跟踪（CSV）

`run` 现在会**自动**把 `actionable=是` 的场次写入 CSV（默认 `tracking/paper_tracking.csv`）；`track` 是只做跟踪、不写 HTML 的轻量命令。`track-update` 再回填结果和 CLV。

```bash
# 分析并写入 CSV（默认：T-60、只做让球方、conf>=50）
python -m rule_engine track --ids 3010473 --excel tracking/paper_tracking.csv
python -m rule_engine track --dates 2026-11-07 --leagues 25 --excel tracking/paper_tracking.csv

# 回填 result / profit / CLV（比赛结束后运行）
python -m rule_engine track-update --excel tracking/paper_tracking.csv
```

行为：

- 默认只记录 `actionable=是` 的场次；`--include-non-actionable` 可记录全部。
- 默认只做让球方 + conf≥50；`--all-sides` / `--min-confidence` 可覆盖。
- 默认 T-60；`--as-of` 可指定固定分析时点。
- 以 `(analysis_at, fixture_id)` 去重：同一快照重复运行只更新，不新增行；已回填的 result/CLV 不会被覆盖。
- CSV 包含：分析时间、比赛、**口径（T-60/T-30）**、方向、side_line、让球方、置信度、实验默认、可下注、操作、引擎盘口、下注水位、result、profit、CLV盘口/水位/有利、备注、回填时间。
- CSV 使用 UTF-8 with BOM（`utf-8-sig`），Excel/WPS 可直接打开且中文不乱码；不再依赖 `openpyxl`。

> `track` 只分析数据库里已有的比赛；数据不新时先运行 `python -m rule_engine run ...` 或 `import`。

## 联赛白名单（爬取过滤）

白名单硬编码在 `rule_engine/leagues.py` 的 `LEAGUE_WHITELIST`，包含英超、西甲、意甲、德甲、法甲、葡超、荷甲、苏超、瑞典超、俄超、挪超、比甲、芬超、希腊超、英冠、德乙、法乙、荷乙、西乙、意乙、瑞典甲、罗甲、瑞士超、土超、以超、波兰超、埃及超、伊朗超、沙地联、日职联、韩K联、日职乙、澳洲甲、巴西甲、奥甲、丹麦超、葡甲、越南联、俄甲、塞尔超、泰超、南非超、瑞士甲、捷甲、阿甲、美职业、马来超、阿联酋超、印尼超。

默认行为：

| 命令 | 默认联赛范围 |
|---|---|
| `run --within-minutes N` | 白名单 |
| `run --dates ...` | 白名单（不传 `--leagues` 时） |
| `import --dates ...` | 白名单（不传 `--leagues` 时） |
| `run --ids ...` / `analyze` | 不过滤（显式指定） |

覆盖方式：

```bash
# 只跑指定联赛
python -m rule_engine run --within-minutes 60 --leagues 9,36

# 关闭白名单，扫描所有联赛
python -m rule_engine run --within-minutes 60 --all-leagues

# 导入时关闭白名单
python -m rule_engine import --dates 2026-11-08 --all-leagues
```

> 修改白名单直接改 `rule_engine/leagues.py`；ID 使用 titan007 SclassID / `match_list_all.competition_id`。

## GitHub Actions 定时扫描与邮件

工作流：`.github/workflows/scan.yml`

- 定时由 **cron-job.org**（console.cron-job.org）通过 GitHub `workflow_dispatch` API 触发，每 30 分钟一次
- job 内启动临时 **MySQL 8 service container**，不需云数据库
- `python -m rule_engine.init_db` 初始化 `sql/schema.sql`
- 运行调度器：
  ```bash
  python -m rule_engine.scheduler
  ```

调度器逻辑（参考 `euro_asian_task-main`）：

1. 读 `data/schedule_cache.json`（含 `league` 联赛名、`home` 主队、`away` 客队）；
2. 每天 **11:00 / 16:00（北京时间）之后第一次运行**自动爬 D-1 / D / D+1 赛程页，刷新缓存；
3. 按开赛时间分两条 lane：
   - **T-60 lane**：距开赛 **30–60 分钟**，按 `kickoff - 60` 分析；
   - **T-30 lane**：距开赛 **0–30 分钟**，按 `kickoff - 30` 分析；
4. 只导入本轮 due 场次的赔率，统一按 **只做让球方 + conf≥50** 分析；
   - **T-60 优先**：同一场如果 T-60 已经 actionable，就跳过它的 T-30；T-60 没有下注时才允许 T-30；
5. 赔率不足的场次跳过；
6. 有 `actionable=是` 时：
   - 写 `tracking/paper_tracking.csv`
   - 追加 `tracking/actionable_YYYY-MM-DD.json`
   - 通过 SMTP 发邮件：正文只列**本轮新增** actionable；附件是只含本轮新增场次的小 CSV，不再附完整的 `paper_tracking.csv`
7. 更新 `data/scheduler_state.json` 去重；
8. **赛程刷新成功后**自动结算已结束场次（只在 11:00 / 16:00 之后的第一次运行触发，不是每 30 分钟都跑）：
   - 从 `paper_tracking.csv` 中挑出开赛超过 3 小时、仍未回填 `result` 的场次；
   - 重新导入这些场次的赔率/比分，回填 `result`、`profit`、CLV 盘口/水位；
   - 把回填结果同步到对应的 `tracking/actionable_YYYY-MM-DD.json`；
9. 把 `data/` 与 `tracking/` 的结果提交回仓库。

> `data/schedule_cache.json` 每场比赛包含 `fixture_id`、`league_id`、`league`（联赛名）、`home`、`away`、`kickoff`、`schedule_page`；`data/` 只存调度缓存/状态两个小 JSON，不再生成比赛 HTML/JSON；`out/` 在 Actions 里不使用。
>
> **两条 lane 的开关与参数：**
>
> | 变量 | 默认 | 作用 |
> |---|---|---|
> | `SCHEDULE_ENABLE_T30` | `false`（workflow 设为 `true`） | 开启 T-30 lane |
> | `SCHEDULE_WINDOW_MINUTES` | `60` | T-60 lane 上界 |
> | `SCHEDULE_T30_WINDOW_MINUTES` | `30` | T-30 lane 上界 |
> | `ANALYSIS_DECISION_MINUTES` | `60` | T-60 分析时点 |
> | `ANALYSIS_T30_MINUTES` | `30` | T-30 分析时点 |
>
> `data/scheduler_state.json` 记录 `analyzed`（T-60 已处理）、`t30_analyzed`（T-30 已处理）、`t60_actionable`（T-60 可下注，用于 T-30 优先判断）。调度器每次启动会从 `paper_tracking.csv` 最近 2 小时的记录恢复这三个集合，避免重启或跨午夜后重复分析/重复下注。
>
> `paper_tracking.csv` 和邮件正文新增 `口径` 列（`T-60` / `T-30`），用来区分两档注单。
>
> 两档依赖约 **30 分钟一次**的触发：T-60 在 30–60 分钟窗口内补跑，T-30 在 0–30 分钟窗口内补跑。如果 cron-job.org 延迟超过 30 分钟，可能漏掉某一档；某场进入 T-30 窗口时如果 T-60 还没跑过，就只做 T-30。
>
> workflow 需要 `permissions: contents: write`，每次运行把
> `data/schedule_cache.json`、`data/scheduler_state.json`、
> `tracking/paper_tracking.csv`、`tracking/actionable_*.json` 提交回仓库；
> `concurrency` 保证同一时间只跑一个调度器。

需要配置的 GitHub Secrets：

| Secret | 用途 |
|---|---|
| `MYSQL_ROOT_PASSWORD` | 临时 MySQL root 密码 |
| `SMTP_HOST` / `SMTP_PORT` | SMTP 服务器 |
| `SMTP_USER` / `SMTP_PASSWORD` | SMTP 登录 |
| `SMTP_SSL` | 可选：465 端口时设为 `true`（代码也会自动把 465 识别为 SSL） |
| `SMTP_TLS` | 可选：587 端口默认 `true`，一般不用填 |
| `MAIL_FROM` | 发件人 |
| `MAIL_TO` | 收件人，逗号分隔 |

> 端口与加密方式必须匹配：**465 = 隐式 SSL**（`SMTP_SSL=true`），**587 = 明文 + STARTTLS**。
> 若日志出现 `email send failed: Connection unexpectedly closed`，优先检查
> `SMTP_PORT` 是否为 465 而 `SMTP_SSL` 未开启；`SMTP_SSL` 只接受
> `true/1/yes/on`（不区分大小写）。

### 在哪里设置这些 Secrets

1. 打开 GitHub 仓库页面。
2. 进入 **Settings**（仓库设置，不是个人账号设置）。
3. 左侧选择 **Secrets and variables → Actions**。
4. 点 **New repository secret**。
5. 按上面的名称逐个添加：
   - **Name**：例如 `MYSQL_ROOT_PASSWORD`（必须完全一致）
   - **Secret**：对应的值
6. 点 **Add secret**，重复添加其余 Secret。

说明：

- `MYSQL_ROOT_PASSWORD` 是 **Actions 临时 MySQL 容器**的 root 密码，不是本机 MySQL 密码；随便生成一个强随机字符串即可。
- SMTP 授权码不是邮箱登录密码。QQ 邮箱在“设置 → 账户 → POP3/SMTP 服务”里生成授权码；Gmail 需要开启两步验证后生成 App Password。
- Secret 保存后不会再次显示，日志里会自动打码为 `***`。
- cron-job.org 用的 GitHub PAT **不是** GitHub Secret，而是填在 cron-job.org 的请求头里。

### cron-job.org 配置

1. 在 GitHub 创建 **fine-grained PAT**，只授权本仓库，权限选 **Actions: write**。
2. 在 console.cron-job.org 新建 job：
   - URL：
     ```text
     https://api.github.com/repos/<OWNER>/<REPO>/actions/workflows/scan.yml/dispatches
     ```
   - Method：`POST`
   - Headers：
     ```text
     Accept: application/vnd.github+json
     Authorization: Bearer <你的 PAT>
     User-Agent: cron-job.org
     X-GitHub-Api-Version: 2022-11-28
     Content-Type: application/json
     ```
   - Body：
     ```json
     {"ref":"main"}
     ```
     如果仓库默认分支是 `master`，把 `main` 改成 `master`。
   - 执行频率：每 30 分钟
3. 点击 Test run，成功应返回 **HTTP 204**。
4. 删除 GitHub workflow 自带的 `schedule` 后，就不会重复触发；当前 `scan.yml` 只保留 `workflow_dispatch`。

> PAT 只保存在 cron-job.org，不要提交到仓库；建议定期轮换。
> `workflow_dispatch` 只触发默认分支上的 workflow，确保 `scan.yml` 在默认分支。

本地测试邮件：

```bash
SMTP_HOST=... SMTP_PORT=587 SMTP_USER=... SMTP_PASSWORD=... \
MAIL_FROM=... MAIL_TO=... \
python -m rule_engine run --experimental-default --within-minutes 60
```

`--no-email` 可关闭邮件。每次运行的 MySQL 是临时容器，但
`tracking/paper_tracking.csv` 和 `tracking/actionable_*.json` 会提交回仓库，
因此历史和结算结果可以跨运行累计。邮件只包含本轮新增的 actionable（正文 + 本轮小 CSV），artifact 作为额外备份。

## 项目结构

```
football-rule-engine/
  rule_engine/
    config.py     # .env / 环境变量
    db.py         # 只读 MySQL：比赛、亚盘、欧赔、sup、回测比赛
    models.py     # 数据类
    features.py   # 轨迹 -> 特征
    rules.py      # 规则命中 + 数据质量
    engine.py     # 冲突消解 + 置信度 + 价格检查
    leagues.py    # 硬编码联赛白名单（爬取过滤）
    settlement.py # 亚盘结算（半赢/半输/走盘）
    backtest.py   # Phase 2 回测 + 分桶统计
    walkforward.py # Phase 3 月度滚动样本外验证
    tracker.py    # 纸面跟踪 CSV（actionable 场次、result、CLV）
    scheduler.py  # 赛程缓存 + 30 分钟轮询（T-60/T-30 两档）+ 11/16 点结算回填 + 邮件 + 提交结果
    settle.py     # 回填已结束场次的 result/profit/CLV，并同步 actionable JSON
    notify.py     # SMTP 邮件通知（仅本轮新增 actionable + 小 CSV）
    init_db.py    # 用 sql/schema.sql 初始化 MySQL
    report.py     # 仅结论段 HTML + JSON
    cli.py        # analyze / scan / backtest / import / run / track
    ingest/       # 独立导入栈（不依赖原项目）
      all.py      # 亚盘 + 欧赔一键导入
      crawl.py    # openssl 抓取 + 赛程发现
      eu.py       # 1x2d 欧赔
      parser.py   # 亚盘 HTML 解析
      persist.py  # SQL 构造
      pipeline.py # 事务入库
      db.py       # 独立 .env/连接
  sql/
    schema.sql    # MySQL 表结构（Actions service container / 本地初始化）
  .github/
    workflows/
      scan.yml    # 每 30 分钟调度 + 邮件 + 提交结果
  tests/
    test_features.py
    test_rules.py
    test_engine.py
    test_cli.py
    test_settlement.py
    test_backtest.py
    test_walkforward.py
    test_tracker.py
    test_scheduler.py
    test_settle.py
    test_notify.py
    test_init_db.py
    test_ingest_imports.py
  data/           # scheduler_state.json / schedule_cache.json（提交回仓库）
  tracking/       # paper_tracking.csv / actionable_YYYY-MM-DD.json
  out/            # 本地分析 HTML/JSON（Actions 不使用）
```

## 测试

```bash
python -m unittest discover -s tests -v
```

测试不依赖数据库；数据库只在 CLI 运行时读取。

## 已知限制

1. **没有基本面**：伤停、首发、战意、天气全部缺失，所以 Phase 1 的置信度硬封顶 55，且大量场次会输出“仅观察”。
2. **规则是启发式**：T-01/T-03 等规则本身存在上下文依赖，Phase 1 用“观察 + 冲突提示”处理，而不是强行给方向。
3. **sup 大多缺失**：当前库只覆盖部分联赛，理论盘口校验无法完成。
4. **已回测、未调参**：Phase 2 已用 2718 场历史比分回测；规则权重和阈值尚未按结果调整，当前只在实验 gate 使用“只做让球方 + conf≥50”。
5. **不接受交易量**：只有价格，没有成交量；`价量局限` 始终存在。

## 下一步

- 纸面跟踪实验 gate（`--experimental-default`）1–2 个月
- 做规则消融（逐条关闭规则重跑），而不是只看“规则出现注单”的共线性统计
- 通过后再考虑把“只做让球方 + conf≥50”设为默认
- 最后再接 LLM：把 `analysis.json` 作为 evidence 输入，而不是直接给 LLM 结论
