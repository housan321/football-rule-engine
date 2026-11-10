# 盘口规则引擎 · 分析手册与回测纪要

> 版本：v1.1（2026-11-09）
> 项目：`D:\football-rule-engine`
> 数据截止：2026-11-09（数据库扩充历史比赛后重跑 Phase 2）
> 适用：Phase 1 规则引擎 + Phase 2 历史回测
> 说明：本文档记录**当前实现**，不是理论文档。规则变更时请同步更新本文档。

---

## 1. 项目定位

这是一个**独立项目**，不引用 `D:\football-handicap-analysis` 的任何代码模块，只共用同一个 MySQL `football` 数据库。

```
D:\football-handicap-analysis   →  导入 titan007 赔率/比赛（写库）
D:\football-rule-engine         →  只读数据库，跑规则引擎/回测
```

核心原则：

- 只用数据库中的**亚盘/欧赔变化轨迹**
- 不查新闻、不判断伤停/首发/战意
- 不调用 LLM
- 输出可复现的方向、置信度、规则审计
- 默认保守：大量场次为“仅观察”

---

## 2. 数据源与过滤

### 2.1 表

| 表 | 用途 |
|---|---|
| `match_list_all` | 比赛、开赛时间、最终比分 |
| `odds_live_all_raw` | 亚盘全量变盘（主盘 + 走地） |
| `odds_history_eu` | 欧赔逐笔变盘 |
| `modeling.team_params` | sup（多数联赛缺失） |

### 2.2 亚盘过滤

- 公司白名单：`2/3/7/9/11/22`
- 核心公司：`2/3/7/11`（Bet365 / 皇冠 / 澳门 / 韦德）
- 只取 `line_no=0`（主盘）
- 赛前：`phase IN ('initial','prematch')`
- 滚球：`phase='inplay'`
- 只取分析时点之前的记录（`change_the_time <= 分析时点`）
- `n=1` 且距开赛 >12 小时的公司 → 剔除，仅作初盘参考

### 2.3 欧赔过滤

- 公司：`2/3/5/7/9/22`（Bet365 / 皇冠 / 立博 / 澳门 / 威廉希尔 / 平博）
- 只取分析时点之前的记录
- 去水后计算主/平/客概率

### 2.4 sup

- 读 `modeling.team_params`，按 `comp_id + team_id` 取最新
- 回测使用 `fetch_sup_asof`，只取 `fit_end_date <= 分析日期`，避免未来函数
- 多数联赛缺失，缺失时置信度封顶 55

### 2.5 数据导入（独立爬虫）

新项目自带完整的 titan007 导入栈，不依赖 `D:\football-handicap-analysis`：

```bash
cd D:\football-rule-engine

# 单场/多场
python -m rule_engine import --ids 3010473
python -m rule_engine import --ids 3010473,3073201

# 按日期/联赛发现
python -m rule_engine import --dates 2026-11-05 --leagues 192

# 只抓取/解析，不写库
python -m rule_engine import --ids 3010473 --dry-run

# 查看参数
python -m rule_engine import --help
```

写入：

- `match_list_all`
- `odds_live_all`
- `odds_live_all_raw`
- `odds_history_eu`
- `data_import_log`

依赖：`pymysql`、系统 `openssl`、项目根目录 `.env`。

亚盘页偶发空盘口（`no main odds records`）时，导入器会按 `--retries`（默认 2）自动重试；
仍为空则不会写入 `match_list_all`，后续分析会报 “fixture not found”。

### 2.6 联赛白名单

白名单硬编码在 `rule_engine/leagues.py` 的 `LEAGUE_WHITELIST`（联赛名 → titan007 SclassID）。

默认过滤：

- `run --within-minutes N`：只用白名单
- `run --dates ...` / `import --dates ...`：不传 `--leagues` 时只用白名单
- `run --ids ...` / `analyze`：不过滤（显式指定）

覆盖：

```bash
python -m rule_engine run --within-minutes 60 --leagues 9,36
python -m rule_engine run --within-minutes 60 --all-leagues
python -m rule_engine import --dates 2026-11-08 --all-leagues
```

---

## 3. 特征

### 3.1 亚盘

- 开盘/最新盘口、开盘/最新水位
- `line_delta = 最新盘口 - 开盘盘口`
  - 正数 = 盘口向主队方向移动
  - 负数 = 向客队方向移动
- `home_water_delta = 最新主水 - 开盘主水`
  - 负数 = 主水下降 = 资金压主队
- 上盘（让球方）识别：
  - 最新盘口 `< 0` → 主队上盘
  - 最新盘口 `> 0` → 客队上盘
  - `= 0` → 无上盘
- `deepening = -upper_sign × line_delta`
  - 上盘升盘（让得更多）= 正值
- `retreat = -deepening`
  - 上盘退盘（让得更少）= 正值
- `upper_water_delta` / `lower_water_delta`
- 公司一致性、最新盘口极差、最新主水极差
- 早变比例：开赛前 3 小时以上的变盘次数 / 总变盘次数
- 波动次数：平均每家公司变盘次数

### 3.2 欧赔

- 每家开盘/最新赔率
- 去水概率（归一化）
- 主胜概率变化、客胜概率变化
- 返还率（overround）
- 欧赔热门方（主/客中概率更高的一方）
- 欧赔漂移方向：变化 ≥1 个百分点才算有方向

---

## 4. 规则与权重

### 4.1 水位规则

| 规则 | 触发 | 方向 | 权重 |
|---|---|---|---:|
| `WL_UPPER_ULTRA_LOW` | 上盘水位 ≤0.75 | 上盘 | 1.5 |
| `WL_UPPER_LOW` | 上盘 0.76–0.85 | 上盘 | 1.0 |
| `WL_UPPER_HIGH` | 上盘 ≥0.975 | 观察（T-03 待裁决） | 0 |
| `WL_UPPER_ULTRA_HIGH` | 上盘 ≥1.10 | 观察 | 0 |
| `WL_UPPER_099` | 上盘约 0.99 且各核心公司已维持 ≥2h | 下盘（T-04） | 1.0 |
| `WL_LOWER_ULTRA_LOW` | 下盘水位 ≤0.75 | 下盘 | 1.2 |

### 4.2 盘口运动规则

| 规则 | 触发 | 方向 | 权重 |
|---|---|---|---:|
| `T01_EARLY_DEEPEN_FAV` | 升盘 >0.20 + 上盘降水 + 早变比例 ≥0.5 + 上盘是欧赔热门 | 下盘（T-01） | 1.2 |
| `T01_EARLY_DEEPEN_DOG` | 同上，但上盘不是欧赔热门 | 上盘 | 1.2 |
| `MOV_DEEPEN_SUPPORT` | 升盘 >0.20 + 上盘降水（非早段/非热门场景） | 上盘 | 1.0 |
| `MOV_DEEPEN_WATER_UP` | 升盘 + 升水 | 观察 | 0 |
| `T02_RETREAT_LOW_WATER` | 退盘 >0.20 + 上盘降水 | 下盘（T-02 诱上） | 1.2 |
| `MOV_RETREAT_WATER_UP` | 退盘 + 升水 | 下盘（真实看衰上盘） | 1.0 |
| `MOV_WATER_ONLY` | 盘口变化 ≤0.05 + 主水变化 ≥0.05 | 水位方向 | 0.8 |

### 4.3 欧亚交叉

| 规则 | 触发 | 方向 | 权重 |
|---|---|---|---:|
| `EURO_ASIA_ALIGN` | 亚盘共识方向 = 欧赔漂移方向 | 同向 | 1.2 |
| `EURO_ASIA_DIVERGE` | 亚盘偏主、欧赔偏客（或相反） | 观察 + 封顶 40 | 0 |
| `EURO_FAV_DRIFT` | 欧赔热门去水概率上升 ≥2 个百分点 | 热门方 | 0.8 |

### 4.4 结构/公司分歧

| 规则 | 触发 | 处理 |
|---|---|---|
| `COMPANY_DIVERGENCE` | 最新盘口极差 ≥0.5 球 | 观察 |
| `WATER_DIVERGENCE` | 最新主水极差 ≥0.15 | 观察 + 封顶 50 |
| `MACAU_OPPOSITE` | 澳门变盘 ≥0.2 且与核心中位反向 | 观察 |

---

## 5. 评分、置信度与仓位

### 5.1 方向

```text
主队得分 = Σ 方向为主的规则权重
客队得分 = Σ 方向为客的规则权重
net = 主队得分 - 客队得分
冲突比 = min(主队得分, 客队得分) / max(主队得分, 客队得分)
```

方向成立条件：

- `|net| >= 1.0`
- `冲突比 < 0.6`

否则输出“观察”。

### 5.2 置信度

```text
base = 30 + 10 × |net| + 4 × 方向性命中规则数
confidence = min(base, 所有封顶值)
confidence = clamp(confidence, 20, cap)
```

封顶：

| 条件 | 封顶 |
|---|---:|
| 方向 = 观察 | 40 |
| 价格已干（实际水位 < 公平水位 × 0.85） | 40 |
| 欧亚背离 | 40 |
| 方向性规则冲突比 ≥0.6 | 40 |
| 亚盘数据陈旧（赛前 >6h / 滚球 >15min） | 40 |
| 核心亚盘公司 <2 家 | 45 |
| 公司水位分歧 ≥0.15 | 50 |
| 欧赔公司 <3 家 | 50 |
| 价格偏干（实际水位 < 公平水位 × 0.95） | 50 |
| sup 缺失 | 55 |
| 无基本面（Phase 1 固定） | 55 |

### 5.3 价格检查

方向确定后，用欧赔去水概率反推方向侧“公平水位”：

| 方向侧盘口 | 公平水位 |
|---|---|
| ≤ -0.5 | (平概率 + 客概率) / 主概率 |
| -0.5 ~ 0 | (客概率 + 0.5×平概率) / 主概率 |
| 平手 | 客概率 / 主概率 |
| 0 ~ +0.5 | (客概率 - 0.5×平概率) / 主概率 |
| ≥ +0.5 | 客概率 / (主概率 + 平概率) |

`ratio = 实际水位 / 公平水位`

- `<0.85`：价格已干 → 封顶 40
- `0.85–0.95`：价格偏干 → 封顶 50
- `>1.10`：价格有边际 → 仅记录审计，不加分

### 5.4 仓位

| 条件 | 操作 |
|---|---|
| 方向 = 观察 | 仅观察 |
| 方向有，但置信度 <45 | 仅观察 |
| 方向有，且置信度 ≥45 | **小注** |

当前实盘**没有**应用回测建议的“≥50”门槛。

---

## 6. 命令行

### 6.0 一键：导入 + 分析

```bash
cd D:\football-rule-engine

# 导入数据后立即分析（T-60）
python -m rule_engine run --ids 3010473 --decision-minutes 60 --out-dir out/t60

# 多场
python -m rule_engine run --ids 3010473,3000518 --decision-minutes 60

# 按日期 + 联赛ID 自动发现/导入/分析
python -m rule_engine run --dates 2026-11-05 --leagues 350 --decision-minutes 60 --out-dir out/t60
python -m rule_engine run --dates 2026-11-05,2026-11-06 --leagues 103,350 --max-fixtures 20
```

`--dates` / `--leagues` 与 `--ids` / `--fixture-id` 互斥。  
流程：先从 `bf.titan007.com` 赛程页发现 fixture；发现失败或为空时，回退到 `match_list_all` 按日期 + `competition_id` 查询。  
`--leagues` 使用 SclassID / `competition_id`（逗号分隔），`--max-fixtures` 限制数量。

`--within-minutes N`：扫描白名单联赛未来 N 分钟内开赛的比赛。
默认先从 `bf.titan007.com` 赛程页发现并入库这些比赛；**已入库的比赛也会重新抓取刷新**，
不只是新发现场次。赛程页日期不一定等于北京时间开赛日，扫描会同时读取**前一天**页面，
避免漏掉 00:xx / 01:00 的比赛。`--skip-import` 时只查数据库已有比赛；
`--leagues` 同时作用于赛程发现和数据库刷新；
亚盘核心公司 <2 或欧赔公司 <2 的场次标记 `[skip]`，不生成 HTML/JSON，也不写入 CSV。

`run` 等价于先执行 `python -m rule_engine import --ids ...`，再执行 `python -m rule_engine analyze --ids ...`，并默认把 `actionable=是` 的场次追加到 `tracking/paper_tracking.csv`。用 `--no-track` 关闭 CSV 跟踪，`--excel` 指定其他路径。

常用开关：

| 开关 | 作用 |
|---|---|
| `--skip-import` | 只分析 |
| `--skip-analysis` | 只导入 |
| `--import-dry-run` | 只抓取/解析，不写库，然后分析现有数据 |
| `--raise-on-import-error` | 导入失败则不分析 |
| `--eu-companies` / `--timeout` / `--retries` / `--no-save` | 原样传给 ingest |

### 6.1 单场/多场分析

```bash
cd D:\football-rule-engine

# 当前时点（自动判断赛前/滚球）
python -m rule_engine analyze --fixture-id 3068888

# 多场
python -m rule_engine analyze --ids 3068888,3069034

# 每场各自取开赛前 60 分钟（推荐，与回测口径一致）
python -m rule_engine analyze --ids 3010473 3000518 --decision-minutes 60 --out-dir out/t60

# 固定分析时点（所有场次共用）
python -m rule_engine analyze --fixture-id 3068888 --as-of "2026-11-05 20:00"
```

`--as-of` 与 `--decision-minutes` 互斥；`--decision-minutes` 隐含 `pre` 模式。

### 6.2 扫描

```bash
python -m rule_engine scan --date 2026-11-05 --limit 50
python -m rule_engine scan --date 2026-11-05 --decision-minutes 60 --write --out-dir out/t60_scan
python -m rule_engine scan --date 2026-11-05 --only-actionable
```

### 6.3 回测

```bash
python -m rule_engine backtest --from 2025-08-16 --to 2026-11-09 \
  --decision-minutes 60 --min-confidence 50 \
  --out out/backtest_conf50.csv --summary out/backtest_conf50.json
```

### 6.4 实验默认 gate（Phase 3 Step 1）

```bash
# 一键：让球方 + conf>=50 + T-60
python -m rule_engine analyze --ids 3010473 --experimental-default
python -m rule_engine run --ids 3010473 --experimental-default

# 分开传
python -m rule_engine analyze --ids 3010473 --decision-minutes 60 --only-giving --min-confidence 50
```

- `--experimental-default` = `--only-giving` + `--min-confidence 50`；未给 `--decision-minutes` / `--as-of` 时自动 T-60。
- 被过滤的场次保留方向，但操作变为“仅观察”。
- 输出新增：`side_line`、`is_giving`、`experimental_default`、`actionable`、`filter_notes`。
- 当前代码默认没有改变；必须显式传参才启用实验 gate。

### 6.5 纸面跟踪 CSV

`run` 默认会写 CSV；`track` 是只做跟踪、不写 HTML 的轻量命令。

```bash
# 一键：导入 + 分析全部 + 写 CSV（推荐）
python -m rule_engine run --ids 3010473 --experimental-default
python -m rule_engine run --dates 2026-11-07 --leagues 25 --experimental-default

# 只跟踪，不写 HTML
python -m rule_engine track --ids 3010473 --excel tracking/paper_tracking.csv
python -m rule_engine track --dates 2026-11-07 --leagues 25 --excel tracking/paper_tracking.csv

# 比赛结束后回填 result / profit / CLV
python -m rule_engine track-update --excel tracking/paper_tracking.csv
```

- 默认只记录 `actionable=是`；`--include-non-actionable` 可记录全部。
- 默认 `only-giving + conf>=50 + T-60`；`--all-sides` / `--min-confidence` / `--as-of` 可覆盖。
- 以 `(analysis_at, fixture_id)` 去重；重复运行只更新，已回填的 result/CLV 不覆盖。
- CSV 字段：分析时间、比赛、**口径（T-60/T-30）**、方向、side_line、让球方、置信度、实验默认、可下注、操作、引擎盘口、下注水位、result、profit、CLV盘口/水位/有利、备注、回填时间。
- CSV 使用 UTF-8 with BOM（`utf-8-sig`），Excel/WPS 可直接打开；不再依赖 `openpyxl`。

---

## 7. Phase 2 回测方法

- **决策时点**：`开赛时间 - decision_minutes`（默认 60）
- 只使用决策时点之前的亚盘/欧赔，**避免未来函数**
- 结算：用 `match_list_all.home_score / away_score`，按亚盘含半赢/半输/走盘
- 下注条件：`stake=小注` 且 `confidence >= min_confidence`
- 下注单位：1 单位平注
- 结算水位：优先 Bet365，其次皇冠/澳门/韦德，取分析时点最新报价
- CLV：对比同一公司开赛前最后报价，判断收盘价是否更有利
- `--limit` 默认 500 场；全量样本（2718 场）需要显式传 `--limit 5000`
- 输出：逐场 CSV + JSON 分桶（规则/联赛/置信度/盘口/月份）

### 7.1 结算规则

- 半/一球等整/半球线：全赢或全输
- 平半/半一（.25/.75）：拆成两个半注
  - 平半：一注平手 + 一注半球
  - 半一：一注半球 + 一注一球
- 走盘：本金退回
- 默认按 90 分钟比分结算，不处理加时/点球

---

## 8. 回测结果（2025-08-16 → 2026-11-09，2718 场）

样本：2718 场有最终比分且亚盘/欧赔齐全（2026-06/07 无数据）。2026-11-09 数据库扩充历史比赛后重跑；旧样本 1880 场的结论保留在 git 历史与本文件 2026-11-06 版本中。

### 8.1 分时期主结果

| 时期 | conf45 | conf50 | conf50_giving | conf50_receiving |
|---|---:|---:|---:|---:|
| 2025-08 ~ 2025-12 | 163 注 / -5.1% | 145 注 / -6.0% | 93 注 / +11.5% | 52 注 / -37.2% |
| 2026-01 ~ 2026-05 | 184 注 / -5.4% | 165 注 / -6.4% | 97 注 / +9.4% | 68 注 / -28.9% |
| 2026-08 ~ 2026-11 | 201 注 / +1.3% | 169 注 / +2.4% | 98 注 / +21.6% | 71 注 / -24.1% |

### 8.2 全期合并

| 变体 | 下注 | 命中率 | ROI | 利润 |
|---|---:|---:|---:|---:|
| `conf45_all` | 548 | 50.5% | -2.9% | -15.61 |
| `conf50_all` | 479 | 50.6% | -3.2% | -15.20 |
| `conf45_giving` | 315 | 61.6% | **+16.4%** | +51.60 |
| `conf50_giving` | 288 | 60.7% | **+14.2%** | +40.95 |
| `conf50_receiving` | 191 | 32.7% | **-29.4%** | -56.15 |

### 8.3 置信度桶（全期，未加盘口过滤）

| 桶 | 下注 | 命中率 | ROI | 利润 |
|---|---:|---:|---:|---:|
| 45–49 | 69 | 50.0% | -0.6% | -0.41 |
| 50–55 | 479 | 50.6% | -3.2% | -15.20 |

> 45–49 桶内部强烈分化：**让球方 27 注 / 70.4% / +39.4%**；受让方 42 注 / 35.1% / -26.4%。整体接近打平是两者抵消的结果。

### 8.4 决策时点敏感性（conf50）

| 决策时点 | 全部 | 让球方 | 受让方/PK |
|---|---:|---:|---:|
| T-30 | 477 注 / -3.2% | 290 注 / +16.3% | 187 注 / -33.6% |
| T-60 | 479 注 / -3.2% | 288 注 / +14.2% | 191 注 / -29.4% |
| T-120 | 504 注 / -0.7% | 311 注 / +12.1% | 193 注 / -21.3% |

> T-30 vs T-60 的详细对比见 `docs/T30_vs_T60_2026-11-09.md`：总体差 +2.1pp、p≈0.77；2025H2 段 T-60 +11.5% vs T-30 +0.2%；两者仅 182 场重叠，共同场次利润几乎相同。
>
> 同时下注 T-60 + T-30（各 1 单位）见 `docs/T30_T60_union_2026-11-09.md`：578 注 / +88.24 / ROI +15.3%，但 182 场重叠中 177 场结果相同，主要是加杠杆；最大回撤 -12.47（T-60 -4.28）。去重合并（每场一注、T-60 优先 + T-30 独有）为 396 注 / +62.51 / ROI +15.8% / 回撤 -6.28。

### 8.5 结论

1. **不加盘口过滤时，conf45/conf50 全期为负**：conf≥50 单独使用不是可盈利规则。
2. **“只做让球方”是唯一稳定为正的分组**：`conf50_giving` 288 注、60.7% 命中、+14.2% ROI；三个时期分别 +11.5% / +9.4% / +21.6%。
3. **受让方/PK 三个阶段全负**：-37.2% / -28.9% / -24.1%；全期 -29.4%。
4. **45–49 桶整体 -0.6%，但只在让球方内有效**：让球方增量 27 注 / 70.4% / +39.4%（95% CI +7%～+72%，p≈0.02，未校正多重比较）；受让方 42 注 / 35.1% / -26.4%。因此 45 只能和 `--only-giving` 一起用，单独降到 45 仍应剔除。
5. 2025 年段是独立于 2026 年发现过程的数据，让球方过滤在该段仍为正（+11.5%），说明不是纯粹的 2026 样本内拟合。
6. **决策时点不是主要变量**：T-30 / T-60 / T-120 的让球方 ROI 都在 +12%～+16%，T-60 继续作为默认。T-30 的 +16.3% 相对 T-60 的 +14.2% 不显著（p≈0.77），且 2025H2 仅 +0.2%（T-60 +11.5%）；两者只有 182 场重叠，T-30 波动更大。详见 `docs/T30_vs_T60_2026-11-09.md`。

---

## 9. 结论

### 9.1 当前回测支持的结论

1. **全期基线为负**：`conf50_all` 479 注、-3.2% ROI；不要再把“conf≥50 小注”本身当成正期望策略。
2. **真正稳定的 edge 在让球方**：`conf50_giving` 三个时期全部为正（+11.5% / +9.4% / +21.6%）；`conf50_receiving` 三个时期全部为负。
3. **T-60 仍是默认决策时点**；T-30/T-120 在扩展样本上已重跑，让球方 ROI 分别为 +16.3% / +12.1%，差异不改变结论。
4. **月度波动明显**：`conf50_giving` 的亏损月为 2025-09、2025-10、2026-04，不能把全期 ROI 当成每月稳定收益。
5. **候选变体仍是看数据后提出的**，但 2025 年段的独立验证显著增强了可信度。
6. **已实现**：`--only-giving` / `--min-confidence` / `--experimental-default` 已加到 `analyze` / `scan` / `run`；下一步纸面跟踪，再考虑设为默认。

### 9.2 当前实盘默认（尚未按回测修改）

| 项目 | 当前实盘 | 回测建议 |
|---|---|---|
| 决策时点 | 当前时间 / `--as-of` | 每场 T-60 |
| 最低置信度 | 45 | **50（保守默认）**；45 + 只做让球方可作为实验档 |
| 盘口方向 | 主客都做 | **只做让球方（side_line < 0）** |
| 仓位 | 小注 | 小注 |
| 强推 | 无 | 无 |

### 9.3 统计警告

- `conf50_giving` 288 注、60.7% 命中，95% 置信区间约 **55%–66%**。
- `conf45_giving` 315 注、61.6% 命中、ROI +16.4%；增量 45–49 让球方 27 注 / 70.4% / +39.4%（95% CI +7%～+72%，p≈0.02，未校正多重比较）。样本只有 27 注，**不能当作稳定 edge**。
- ROI +14.2% 的区间仍然很宽；2025 年段为 +11.5%，说明收益并不均匀但方向一致。
- 全期回测不是完全独立的样本外验证；2025 年段虽独立，但候选变体仍是事后提出。
- 无基本面、无交易量，结算按 90 分钟比分。

### 9.4 规则层面审计（2718 场，conf≥45 下注样本）

> 注意：这是“规则出现过的注单”统计，不是严格的规则消融。一场注单可能同时命中多条规则，因此各规则注数之和大于总注数；只有 `solo` 列接近独立贡献。

| 规则 | 出现注数 | 全期 ROI | 让球方子集 ROI | solo n / ROI | 2025 / 2026H1 / 2026H2 ROI | 结论 |
|---|---:|---:|---:|---|---|---|
| `EURO_FAV_DRIFT` | 217 | +5.2% | +7.6% | 0 / - | +1.7% / -3.8% / +14.3% | 全期为正的高频规则，但 2026H1 为负；保留、降权 |
| `WL_UPPER_LOW` | 135 | +5.1% | +5.9% | 0 / - | +16.0% / -16.4% / +10.3% | 弱正、不稳定；保留为辅助 |
| `EURO_ASIA_ALIGN` | 482 | -2.5% | +17.9% | 54 / +6.3% | -9.2% / -3.6% / +3.6% | 全期负；只可在让球方内作确认 |
| `MOV_WATER_ONLY` | 428 | -3.5% | +15.9% | 0 / - | -7.8% / -6.3% / +3.2% | 同上 |
| `WL_UPPER_099` | 19 | -23.0% | 3 注 | 0 / - | +12.9% / +23.3% / -76.2% | 隔离/停用 |
| `T02_RETREAT_LOW_WATER` | 12 | -29.9% | 0 注 | 12 / -29.9% | -51.4% / -20.0% / -1.0% | 隔离/停用 |
| `MOV_DEEPEN_SUPPORT` | 5 | -6.4% | -8.0% | 0 / - | - / -38.3% / +41.5% | 样本太小，隔离 |
| `MOV_RETREAT_WATER_UP` | 4 | -6.8% | 0 注 | 0 / - | +24.3% / - / -100.0% | 样本太小，隔离 |
| `WL_LOWER_ULTRA_LOW` | 2 | +37.5% | 0 注 | 2 / +37.5% | 2026H2 only | 无证据 |
| `T01_EARLY_DEEPEN_FAV` | 2 | -100.0% | 0 注 | 1 / -100.0% | 2026H2 only | 样本太小，隔离 |

补充：

- `WL_UPPER_ULTRA_LOW`、`T01_EARLY_DEEPEN_DOG` 等在本样本中没有形成下注，属于**未验证规则**，不能算“有用”。
- `WL_UPPER_HIGH`、`WL_UPPER_ULTRA_HIGH`、`EURO_ASIA_DIVERGE`、`COMPANY_DIVERGENCE`、`WATER_DIVERGENCE`、`MACAU_OPPOSITE` 是**风险/观察过滤器**，不应按 ROI 判断；它们的作用是压制下注。
- 正确做法是**规则消融**：逐条关闭规则重跑回测，或记录全部规则命中后离线重评分。当前 CSV 只能做“出现注单”统计，存在共线性。

**规则去留建议：**

1. **保留为主 gate**：只做让球方（meta-filter）+ conf≥50。
2. **保留但降权**：`EURO_FAV_DRIFT`、`WL_UPPER_LOW`。
3. **仅作确认、不可单独触发**：`EURO_ASIA_ALIGN`、`MOV_WATER_ONLY`。
4. **隔离/停用**：`T02_RETREAT_LOW_WATER`、`WL_UPPER_099`、`MOV_RETREAT_WATER_UP`、`T01_EARLY_DEEPEN_FAV`、`MOV_DEEPEN_SUPPORT`。
5. **保留为风险过滤器**：`WL_UPPER_HIGH`、`WL_UPPER_ULTRA_HIGH`、`EURO_ASIA_DIVERGE`、`COMPANY_DIVERGENCE`、`WATER_DIVERGENCE`、`MACAU_OPPOSITE`。
6. **未验证规则**：`WL_UPPER_ULTRA_LOW`、`T01_EARLY_DEEPEN_DOG` 等，暂不赋予方向权重。

### 9.5 规则消融（2026-11-09）

本次用**一次性本地脚本**关闭规则后重新评分（方向/置信度/仓位都会变），对 2718 场逐场重跑。当前 CLI 没有保留 `--disable-rule` 入口，复跑需要重新加消融参数。完整报告：`docs/RULE_ABLATION_2026-11-09.md`。

关键结果（T-60，`conf50_giving`）：

| 变体 | 下注 | 命中率 | ROI | P1 / P2 / P3 ROI |
|---|---:|---:|---:|---|
| baseline | 288 | 60.7% | +14.2% | +11.5% / +9.4% / +21.6% |
| 删 `T01_EARLY_DEEPEN_FAV` | 320 | 62.1% | +16.0% | +14.3% / +9.9% / +22.6% |
| 删已知坏规则（T02、WL_UPPER_099、MOV_RETREAT_WATER_UP、T01_FAV、MOV_DEEPEN_SUPPORT） | 316 | 62.2% | +16.3% | +14.3% / +11.4% / +22.1% |
| 只保留 4 条核心规则 | 316 | 62.2% | +16.3% | +14.3% / +11.4% / +22.1% |

结论：

1. 删 `EURO_ASIA_ALIGN` / `MOV_WATER_ONLY` 可以把**整体**命中率从 50.6% 提到 54.5% / 55.4%，但下注数从 479 降到 258 / 262，且让球方 ROI 从 +14.2% 掉到 +5.5% / +6.4%；这两条是让球方 edge 的主要来源。
2. 只做让球方时，删“已知坏规则”带来 +1.5pp 命中、+2.1pp ROI，三个时期都改善，但月度是 8 好 / 4 差 / 2 平，p 值均 >0.05，**统计上不显著**。
3. 风险/观察过滤器（DQ、WATER_DIVERGENCE、COMPANY_DIVERGENCE、MACAU_OPPOSITE、高水）对下注几乎无影响，建议保留。
4. 最小改动候选是删 `T01_EARLY_DEEPEN_FAV`；先纸面跟踪，不要直接改默认。当前代码没有保留消融开关，复跑需临时加参数。

## 10. 已知限制

1. **无基本面**：伤停、首发、战意、天气全部缺失，置信度硬封顶 55。
2. **无交易量**：只有价格，没有成交量；价量局限始终存在。
3. **规则是启发式**：T-01/T-03 等规则本身存在上下文依赖；Phase 1 用“观察 + 冲突提示”处理。
4. **sup 大多缺失**：理论盘口校验无法完成。
5. **结算假设 90 分钟**：不处理加时/点球。
6. **全期 ROI 仍是全样本**：Phase 3 已做月度 walk-forward，但候选变体是看过数据后提出的，仍有选择偏差。
7. **赛后 `scan` 不是赛前分析**：不带 `--decision-minutes` 时，已开赛比赛会进入 `inplay` 并读取整场轨迹。
8. **CLV 使用同一公司报价**：不同公司报价可能不同，CLV 只是近似。

---

## 11. Phase 3：Walk-forward 样本外验证（扩展样本）

### 11.1 方法

- 月份：2025-08 → 2026-11（2026-06/07 无数据），共 14 个月。
- 每个测试月只使用**之前月份**的数据选择变体。
- 候选：`conf50_all`（默认）、`conf50_giving`、`conf45_giving`。
- 控制组：`conf50_receiving`（受让方/PK，不参与选择）。

### 11.2 T-60 Walk-forward 结果（2026-11-09 重跑）

- 输入：548 注 conf≥45（2718 场扩展样本）
- 拼接结果：**312 注，61.2% 命中，利润 +49.63，ROI +15.9%**。
- 选择路径：
  - 2025-08：默认 `conf50_all`（-4.1%）
  - 2025-09 → 2025-12：`conf50_giving`（-0.6% / -14.6% / +19.8% / +38.8%）
  - 2026-01 → 2026-11：`conf45_giving` 连续被选中
- `conf50_giving` 月度：14 个月中 11 个月为正；负月份为 2025-09（-0.6%）、2025-10（-14.6%）、2026-04（-42.8%）。
- `conf50_receiving`：14 个月全部为负。

### 11.3 结论

1. **让球方过滤在 2025 年独立数据段仍然有效**：2025-08～12 的 `conf50_giving` 为 +11.5%，2026-01～05 为 +9.4%，2026-08～11 为 +21.6%。
2. **受让方/PK 是系统性亏损源**：全期 -29.4%，三个阶段全负。
3. **Walk-forward 拼接 +15.9%**，比只看 2026 年样本更可信，但仍不是完全独立的最终验证。
4. **T-120 已在扩展样本上重跑**：让球方 +12.1%，仍低于 T-60 的 +14.2%，当前继续以 T-60 为主。
5. **下一步**：
   - 已实现 `--only-giving` / `--min-confidence` / `--experimental-default`；
   - 用实验 gate 纸面跟踪 1–2 个月；
   - 再考虑把“只做让球方 + conf≥50”设为默认。

报告文件（2026-11-09 重跑）：

- `docs/PHASE2_2026-11-09.md`（Phase 2 全部分桶，入库）
- `out/phase2_v2_report.md`（同一份报告的本地副本）
- `out/phase2_v2_walkforward_t60.md`（Walk-forward）
- 旧版本：`out/phase3_walkforward_all.md`、`out/phase3_walkforward_t60.md`、`out/phase3_walkforward_t120.md`（812 场早期样本，保留作对比）

## 11.5 GitHub Actions 定时扫描与邮件通知

工作流：`.github/workflows/scan.yml`

- 定时由 **cron-job.org** 通过 GitHub `workflow_dispatch` API 触发，每 30 分钟一次
- 每次 job 内启动临时 **MySQL 8 service container**
- `python -m rule_engine.init_db` 初始化 `sql/schema.sql`
- 执行：
  ```bash
  python -m rule_engine.scheduler
  ```

调度器逻辑：

1. 读 `data/schedule_cache.json`（含 `league` 联赛名、`home` 主队、`away` 客队）；
2. 每天 11:00 / 16:00（北京时间）之后第一次运行刷新缓存，爬 D-1 / D / D+1 赛程页；
3. 刷新成功后顺带结算已结束场次，回填 `result` / `profit` / CLV 到 `paper_tracking.csv`，并同步 `actionable_*.json`；
4. 按开赛时间分两条 lane：
   - T-60 lane：距开赛 30–60 分钟，按 `kickoff - 60` 分析；
   - T-30 lane：距开赛 0–30 分钟，按 `kickoff - 30` 分析；
5. 只导入本轮 due 场次的赔率，统一按只做让球方 + conf≥50 分析；
   - **T-60 优先**：同一场如果 T-60 已 actionable，跳过它的 T-30；T-60 没下注时才允许 T-30；
6. 赔率不足的场次跳过；
7. 有 `actionable=是` 时写 CSV、`tracking/actionable_YYYY-MM-DD.json`，邮件只发本轮新增 actionable；
8. 更新 `data/scheduler_state.json` 去重；
9. 把 `data/` 与 `tracking/` 的结果提交回仓库（需要 `contents: write`）。

> `data/` 只存调度缓存/状态两个小 JSON；`out/` 和 `data/*.js` 在 Actions 里不生成。
>
> **T-60/T-30 参数（workflow 已设置）：**
>
> | 变量 | 默认 | 作用 |
> |---|---|---|
> | `SCHEDULE_ENABLE_T30` | `false`（workflow=`true`） | 开启 T-30 lane |
> | `SCHEDULE_WINDOW_MINUTES` | `60` | T-60 lane 上界 |
> | `SCHEDULE_T30_WINDOW_MINUTES` | `30` | T-30 lane 上界 |
> | `ANALYSIS_DECISION_MINUTES` | `60` | T-60 分析时点 |
> | `ANALYSIS_T30_MINUTES` | `30` | T-30 分析时点 |
>
> `data/scheduler_state.json` 记录 `analyzed`（T-60 已处理）、`t30_analyzed`（T-30 已处理）、`t60_actionable`（T-60 可下注）。启动时从 `paper_tracking.csv` 最近 2 小时记录恢复这三个集合，避免重启/跨午夜后重复分析或重复下注。`paper_tracking.csv` 和邮件正文新增 `口径` 列。
>
> 两档依赖约 30 分钟一次的触发：T-60 在 30–60 分钟窗口内补跑，T-30 在 0–30 分钟窗口内补跑；延迟超过 30 分钟可能漏档，进入 T-30 窗口时若 T-60 未跑过就只做 T-30。

需要配置的 GitHub Secrets：

| Secret | 用途 |
|---|---|
| `MYSQL_ROOT_PASSWORD` | 临时 MySQL root 密码 |
| `SMTP_HOST` / `SMTP_PORT` | SMTP 服务器 |
| `SMTP_USER` / `SMTP_PASSWORD` | SMTP 登录 |
| `SMTP_SSL` | 可选：465 端口设为 `true`（代码也会自动把 465 识别为 SSL） |
| `SMTP_TLS` | 可选：587 端口默认 `true`，一般不用填 |
| `MAIL_FROM` | 发件人 |
| `MAIL_TO` | 收件人，逗号分隔 |

> 端口与加密方式必须匹配：**465 = 隐式 SSL**（`SMTP_SSL=true`），**587 = 明文 + STARTTLS**。
> 日志出现 `email send failed: Connection unexpectedly closed` 时，先检查
> `SMTP_PORT` 是否 465 但 `SMTP_SSL` 未开启；`SMTP_SSL` 只接受 `true/1/yes/on`。

设置位置：GitHub 仓库 → **Settings → Secrets and variables → Actions → New repository secret**，Name 必须与上表完全一致。`MYSQL_ROOT_PASSWORD` 是临时 MySQL 容器的密码，不是本机数据库密码；SMTP 授权码不是邮箱登录密码。

### cron-job.org 配置

1. 创建 fine-grained PAT，仅授权本仓库，权限 **Actions: write**。
2. console.cron-job.org 新建 job：
   - URL：
     ```text
     https://api.github.com/repos/<OWNER>/<REPO>/actions/workflows/scan.yml/dispatches
     ```
   - Method：`POST`
   - Headers：
     ```text
     Accept: application/vnd.github+json
     Authorization: Bearer <PAT>
     User-Agent: cron-job.org
     X-GitHub-Api-Version: 2022-11-28
     Content-Type: application/json
     ```
   - Body：`{"ref":"main"}`（默认分支是 `master` 时改为 `master`）
   - 频率：每 30 分钟
3. Test run 成功返回 **HTTP 204**。
4. `scan.yml` 已移除 `schedule`，只保留 `workflow_dispatch`，避免重复触发。

> PAT 只存 cron-job.org，不要提交到仓库；`workflow_dispatch` 只触发默认分支上的 workflow。

注意：

- GitHub host runner 无法访问本机 `127.0.0.1` 的 MySQL；工作流使用 job 内临时 MySQL，所以不需要云数据库。
- 每次运行的 MySQL 是临时容器，但 `paper_tracking.csv` / `actionable_*.json` 会提交回仓库，所以可以跨运行累计；邮件附件只包含本轮新增场次，artifact 作为额外备份。
- cron-job.org 触发可能有少量延迟，GitHub Actions 本身也可能排队。
- 外部 `workflow_dispatch` 不受 GitHub 公共仓库“60 天无活动暂停 schedule”的限制；私有仓库仍会消耗 Actions 分钟数。
- 本地测试邮件：
  ```bash
  SMTP_HOST=... SMTP_PORT=587 SMTP_USER=... SMTP_PASSWORD=... \
  MAIL_FROM=... MAIL_TO=... \
  python -m rule_engine run --experimental-default --within-minutes 60
  ```
  `--no-email` 可关闭邮件。

## 12. 复现命令

```bash
cd D:\football-rule-engine

# 单元测试
python -m unittest discover -s tests -v

# T-60，conf≥45（全量样本需显式 --limit 5000）
python -m rule_engine backtest --from 2025-08-16 --to 2026-11-09 \
  --decision-minutes 60 --min-confidence 45 --limit 5000 \
  --out out/backtest_conf45.csv --summary out/backtest_conf45.json

# T-60，conf≥50
python -m rule_engine backtest --from 2025-08-16 --to 2026-11-09 \
  --decision-minutes 60 --min-confidence 50 --limit 5000 \
  --out out/backtest_conf50.csv --summary out/backtest_conf50.json

# T-30，conf≥50
python -m rule_engine backtest --from 2025-08-16 --to 2026-11-09 \
  --decision-minutes 30 --min-confidence 50 --limit 5000 \
  --out out/backtest_d30.csv --summary out/backtest_d30.json

# T-120，conf≥50
python -m rule_engine backtest --from 2025-08-16 --to 2026-11-09 \
  --decision-minutes 120 --min-confidence 50 --limit 5000 \
  --out out/backtest_d120.csv --summary out/backtest_d120.json

# Walk-forward（输入上面 conf≥45 的 CSV）
python -m rule_engine walkforward --input out/backtest_conf45.csv \
  --out out/phase3_walkforward.json --report out/phase3_walkforward.md

# 批量生成 T-60 HTML
python -m rule_engine scan --date 2026-11-05 --decision-minutes 60 --write --out-dir out/t60_scan
```

输出文件：

```
out/backtest_conf45.csv / .json
out/backtest_conf50.csv / .json
out/backtest_d30.csv / .json
out/backtest_d120.csv / .json
out/t60/<主队>VS<客队>_<fixture>.html / .json
out/t60_scan/...
```

---

## 13. 术语

| 术语 | 含义 |
|---|---|
| 上盘 | 让球方（强队/热门） |
| 下盘 | 受让方（弱队/冷门） |
| 升盘 | 让球方让得更多 |
| 退盘 | 让球方让得更少 |
| 低水 | 水位 ≤0.85 |
| 高水 | 水位 ≥0.975 |
| 走盘 | 结算结果为平局盘口 |
| 半赢/半输 | 平半/半一盘的拆分结算结果 |
| CLV | Closing Line Value，收盘价价值 |

---

## 14. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-11-06 | Phase 1 规则引擎、Phase 2 回测、T-60 参数、首轮回测纪要 |
| 2026-11-09 | 数据库扩充历史比赛（1880 → 2718 场）后重跑 Phase 2 / Phase 3；`backtest --limit` 默认改为不截断 |
