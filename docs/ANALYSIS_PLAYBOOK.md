# 盘口规则引擎 · 分析手册与回测纪要

> 版本：v1.0（2026-11-06）
> 项目：`D:\football-rule-engine`
> 数据截止：2026-11-06
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
亚盘核心公司 <2 或欧赔公司 <2 的场次标记 `[skip]`，不生成 HTML/JSON，也不写入 Excel。

`run` 等价于先执行 `python -m rule_engine import --ids ...`，再执行 `python -m rule_engine analyze --ids ...`，并默认把 `actionable=是` 的场次追加到 `tracking/paper_tracking.xlsx`。用 `--no-track` 关闭 Excel 跟踪，`--excel` 指定其他路径。

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
python -m rule_engine backtest --from 2026-10-01 --to 2026-11-06 \
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

### 6.5 纸面跟踪 Excel

`run` 默认会写 Excel；`track` 是只做跟踪、不写 HTML 的轻量命令。

```bash
# 一键：导入 + 分析全部 + 写 Excel（推荐）
python -m rule_engine run --ids 3010473 --experimental-default
python -m rule_engine run --dates 2026-11-07 --leagues 25 --experimental-default

# 只跟踪，不写 HTML
python -m rule_engine track --ids 3010473 --excel tracking/paper_tracking.xlsx
python -m rule_engine track --dates 2026-11-07 --leagues 25 --excel tracking/paper_tracking.xlsx

# 比赛结束后回填 result / profit / CLV
python -m rule_engine track-update --excel tracking/paper_tracking.xlsx
```

- 默认只记录 `actionable=是`；`--include-non-actionable` 可记录全部。
- 默认 `only-giving + conf>=50 + T-60`；`--all-sides` / `--min-confidence` / `--as-of` 可覆盖。
- 以 `(analysis_at, fixture_id)` 去重；重复运行只更新，已回填的 result/CLV 不覆盖。
- Excel 字段：分析时间、比赛、方向、side_line、让球方、置信度、实验默认、可下注、操作、引擎盘口、下注水位、result、profit、CLV盘口/水位/有利、备注、回填时间。
- 依赖 `openpyxl`。

---

## 7. Phase 2 回测方法

- **决策时点**：`开赛时间 - decision_minutes`（默认 60）
- 只使用决策时点之前的亚盘/欧赔，**避免未来函数**
- 结算：用 `match_list_all.home_score / away_score`，按亚盘含半赢/半输/走盘
- 下注条件：`stake=小注` 且 `confidence >= min_confidence`
- 下注单位：1 单位平注
- 结算水位：优先 Bet365，其次皇冠/澳门/韦德，取分析时点最新报价
- CLV：对比同一公司开赛前最后报价，判断收盘价是否更有利
- 输出：逐场 CSV + JSON 分桶（规则/联赛/置信度/盘口/月份）

### 7.1 结算规则

- 半/一球等整/半球线：全赢或全输
- 平半/半一（.25/.75）：拆成两个半注
  - 平半：一注平手 + 一注半球
  - 半一：一注半球 + 一注一球
- 走盘：本金退回
- 默认按 90 分钟比分结算，不处理加时/点球

---

## 8. 回测结果（2025-08-16 → 2026-11-07，1880 场）

样本：1880 场有最终比分；分三段回测后合并（2026-06/07 无数据）。

### 8.1 分时期主结果

| 时期 | conf45 | conf50 | conf50_giving | conf50_receiving |
|---|---:|---:|---:|---:|
| 2025-08 ~ 2025-12 | 101 注 / -16.4% | 89 注 / -17.6% | 53 注 / +1.9% | 36 注 / -46.2% |
| 2026-01 ~ 2026-05 | 114 注 / +3.2% | 104 注 / +1.6% | 63 注 / +15.6% | 41 注 / -20.0% |
| 2026-08 ~ 2026-11 | 161 注 / -0.9% | 135 注 / +2.0% | 77 注 / +20.6% | 58 注 / -22.8% |

### 8.2 全期合并

| 变体 | 下注 | 命中率 | ROI | 利润 |
|---|---:|---:|---:|---:|
| `conf45_all` | 376 | 49.9% | -3.8% | -14.21 |
| `conf50_all` | 328 | 50.5% | -3.5% | -11.34 |
| `conf45_giving` | 211 | 61.2% | **+15.8%** | +33.26 |
| `conf50_giving` | 193 | 60.6% | **+13.9%** | +26.73 |
| `conf50_receiving` | 135 | 33.3% | **-28.2%** | -38.06 |

### 8.3 置信度桶（全期，未加盘口过滤）

| 桶 | 下注 | 命中率 | ROI | 利润 |
|---|---:|---:|---:|---:|
| 45–49 | 48 | 45.5% | -6.0% | -2.88 |
| 50–55 | 328 | 50.5% | -3.5% | -11.34 |

### 8.4 结论

1. **不加盘口过滤时，conf45/conf50 全期为负**：conf≥50 单独使用不是可盈利规则。
2. **“只做让球方”是唯一稳定为正的分组**：`conf50_giving` 193 注、60.6% 命中、+13.9% ROI；三个时期分别 +1.9% / +15.6% / +20.6%。
3. **受让方/PK 三个阶段全负**：-46.2% / -20.0% / -22.8%；全期 -28.2%。
4. **45–49 桶全期 -6.0%**，仍应剔除。
5. 2025 年段是独立于 2026 年发现过程的数据，让球方过滤在该段仍为正，说明不是纯粹的 2026 样本内拟合。

---

## 9. 结论

### 9.1 当前回测支持的结论

1. **全期基线为负**：`conf50_all` 328 注、-3.5% ROI；不要再把“conf≥50 小注”本身当成正期望策略。
2. **真正稳定的 edge 在让球方**：`conf50_giving` 三个时期全部为正；`conf50_receiving` 三个时期全部为负。
3. **T-60 仍是默认决策时点**；T-120 在扩展样本上尚未重跑。
4. **月度波动明显**：让球方也有亏损月（2025-10、2026-04），不能把全期 ROI 当成每月稳定收益。
5. **候选变体仍是看数据后提出的**，但 2025 年段的独立验证显著增强了可信度。
6. **已实现**：`--only-giving` / `--min-confidence` / `--experimental-default` 已加到 `analyze` / `scan` / `run`；下一步纸面跟踪，再考虑设为默认。

### 9.2 当前实盘默认（尚未按回测修改）

| 项目 | 当前实盘 | 回测建议 |
|---|---|---|
| 决策时点 | 当前时间 / `--as-of` | 每场 T-60 |
| 最低置信度 | 45 | 50 |
| 盘口方向 | 主客都做 | **只做让球方（side_line < 0）** |
| 仓位 | 小注 | 小注 |
| 强推 | 无 | 无 |

### 9.3 统计警告

- `conf50_giving` 193 注、60.6% 命中，95% 置信区间约 **53%–68%**。
- ROI +13.9% 的区间仍然很宽；2025 年段仅 +1.9%，说明收益并不均匀。
- 全期回测不是完全独立的样本外验证；2025 年段虽独立，但候选变体仍是事后提出。
- 无基本面、无交易量，结算按 90 分钟比分。

### 9.4 规则层面审计（1880 场，conf≥45 下注样本）

> 注意：这是“规则出现过的注单”统计，不是严格的规则消融。一场注单可能同时命中多条规则，因此各规则注数之和大于总注数；只有 `solo` 列接近独立贡献。

| 规则 | 出现注数 | 全期 ROI | 让球方子集 ROI | solo n / ROI | 2025 / 2026H1 / 2026H2 ROI | 结论 |
|---|---:|---:|---:|---|---|---|
| `EURO_FAV_DRIFT` | 152 | +5.0% | +5.7% | 0 / - | -11.9% / +4.2% / +13.3% | 唯一全期为正的高频规则，但 2025 为负；保留、降权 |
| `WL_UPPER_LOW` | 90 | +1.6% | +1.6% | 0 / - | +4.1% / -13.4% / +9.4% | 弱正、不稳定；保留为辅助 |
| `EURO_ASIA_ALIGN` | 324 | -4.3% | +15.7% | 35 / +2.0% | -24.4% / +5.6% / +0.5% | 全期负、2025 差；只可在让球方内作确认 |
| `MOV_WATER_ONLY` | 293 | -3.4% | +16.0% | 0 / - | -20.0% / +2.8% / +2.3% | 同上 |
| `T02_RETREAT_LOW_WATER` | 11 | -23.6% | 0 注 | 11 / -23.6% | -51.4% / 0.0% / -1.0% | 隔离/停用 |
| `WL_UPPER_099` | 12 | -32.7% | 2 注 | 0 / - | +11.3% / -100% / -72% | 隔离/停用 |
| `MOV_RETREAT_WATER_UP` | 3 | -37.3% | 0 注 | 0 / - | 2026H2 only | 样本太小，隔离 |
| `T01_EARLY_DEEPEN_FAV` | 1 | -100% | 0 注 | 1 / -100% | 2026H2 only | 样本太小，隔离 |
| `MOV_DEEPEN_SUPPORT` | 5 | -6.4% | -8.0% | 0 / - | 2026 only | 样本太小，隔离 |
| `WL_LOWER_ULTRA_LOW` | 1 | 0.0% | 0 注 | 1 / 0.0% | 2026H2 only | 无证据 |

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

## 10. 已知限制

1. **无基本面**：伤停、首发、战意、天气全部缺失，置信度硬封顶 55。
2. **无交易量**：只有价格，没有成交量；价量局限始终存在。
3. **规则是启发式**：T-01/T-03 等规则本身存在上下文依赖；Phase 1 用“观察 + 冲突提示”处理。
4. **sup 大多缺失**：理论盘口校验无法完成。
5. **结算假设 90 分钟**：不处理加时/点球。
6. **回测为全样本**：未做滚动样本外验证。
7. **赛后 `scan` 不是赛前分析**：不带 `--decision-minutes` 时，已开赛比赛会进入 `inplay` 并读取整场轨迹。
8. **CLV 使用同一公司报价**：不同公司报价可能不同，CLV 只是近似。

---

## 11. Phase 3：Walk-forward 样本外验证（扩展样本）

### 11.1 方法

- 月份：2025-08 → 2026-11（2026-06/07 无数据），共 14 个月。
- 每个测试月只使用**之前月份**的数据选择变体。
- 候选：`conf50_all`（默认）、`conf50_giving`、`conf45_giving`。
- 控制组：`conf50_receiving`（受让方/PK，不参与选择）。

### 11.2 T-60 Walk-forward 结果

- 拼接结果：**220 注，59.8% 命中，利润 +29.14，ROI +13.2%**。
- 选择路径：
  - 2025-08 / 2025-09：默认 `conf50_all`（分别 -7.8% / -20.9%）
  - 2025-10：`conf50_giving`（-35.8%，12 注）
  - 2025-11 起：主要选择 `conf45_giving` 或 `conf50_giving`
  - 2026-01 → 2026-11：`conf45_giving` 连续被选中
- `conf50_giving` 月度：14 个月中 11 个月为正；负月份为 2025-08（-75%，仅 2 注）、2025-10（-35.8%，12 注）、2026-04（-37.3%，3 注）。
- `conf50_receiving`：14 个月中 13 个月为负，仅 2025-08 为正（+37%，3 注）。

### 11.3 结论

1. **让球方过滤在 2025 年独立数据段仍然有效**：2025-08～12 的 `conf50_giving` 为 +1.9%，2026-01～05 为 +15.6%，2026-08～11 为 +20.6%。
2. **受让方/PK 是系统性亏损源**：全期 -28.2%，三个阶段全负。
3. **Walk-forward 拼接 +13.2%**，比只看 2026 年样本更可信，但仍不是完全独立的最终验证。
4. **T-120 未在扩展样本上重跑**；当前以 T-60 为主。
5. **下一步**：
   - 已实现 `--only-giving` / `--min-confidence` / `--experimental-default`；
   - 用实验 gate 纸面跟踪 1–2 个月；
   - 再考虑把“只做让球方 + conf≥50”设为默认。

报告文件：

- `out/phase3_walkforward_all.md`
- `out/phase3_walkforward_t60.md`
- `out/phase3_walkforward_t120.md`（早期 812 场样本）

## 11.5 GitHub Actions 定时扫描与邮件通知

工作流：`.github/workflows/scan.yml`

- 定时由 **cron-job.org** 通过 GitHub `workflow_dispatch` API 触发，每 30 分钟一次
- 每次 job 内启动临时 **MySQL 8 service container**
- `python -m rule_engine.init_db` 初始化 `sql/schema.sql`
- 执行：
  ```bash
  python -m rule_engine run --ci --experimental-default --within-minutes 60
  ```
  `--ci` = 不写 `out/` HTML/JSON + 不保存 `data/` JS，只保留 `tracking/paper_tracking.xlsx` 和邮件。
- 有 `actionable=是` 时：
  - 写入 `tracking/paper_tracking.xlsx`
  - 通过 SMTP 发送邮件，附 Excel
- 上传 `paper_tracking.xlsx` 作为 Actions artifact

需要配置的 GitHub Secrets：

| Secret | 用途 |
|---|---|
| `MYSQL_ROOT_PASSWORD` | 临时 MySQL root 密码 |
| `SMTP_HOST` / `SMTP_PORT` | SMTP 服务器 |
| `SMTP_USER` / `SMTP_PASSWORD` | SMTP 登录 |
| `MAIL_FROM` | 发件人 |
| `MAIL_TO` | 收件人，逗号分隔 |

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
- 每次运行是独立容器/文件，`paper_tracking.xlsx` **不会跨运行累计**；当前方案是每次邮件附件 + artifact。
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

# T-60，conf≥45
python -m rule_engine backtest --from 2026-10-01 --to 2026-11-06 \
  --decision-minutes 60 --min-confidence 45 \
  --out out/backtest_conf45.csv --summary out/backtest_conf45.json

# T-60，conf≥50
python -m rule_engine backtest --from 2026-10-01 --to 2026-11-06 \
  --decision-minutes 60 --min-confidence 50 \
  --out out/backtest_conf50.csv --summary out/backtest_conf50.json

# T-30，conf≥50
python -m rule_engine backtest --from 2026-10-01 --to 2026-11-06 \
  --decision-minutes 30 --min-confidence 50 \
  --out out/backtest_d30.csv --summary out/backtest_d30.json

# T-120，conf≥50
python -m rule_engine backtest --from 2026-10-01 --to 2026-11-06 \
  --decision-minutes 120 --min-confidence 50 \
  --out out/backtest_d120.csv --summary out/backtest_d120.json

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
