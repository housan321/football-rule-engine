# 盘口-水位共识规则引擎

把《规则回测分析结果.docx》里的三条**多时间窗亚盘背离规则**脚本化：

| 策略 key | 名称 | 比较两个共识快照 | 下注时点 |
|---|---|---|---|
| `conflict_t10` | conflict T-10m | T-30m → T-10m | T-10m |
| `conflict_t120` | conflict T-120m | T-3h → T-2h | T-2h（T-120m） |
| `water_t60` | water T-60m | T-2h → T-1h | T-1h（T-60m） |

只读 MySQL 里的赛前亚盘主盘（`line_no=0`），不调用 LLM，不使用欧赔/基本面。
本项目**已移除**旧的 T-01/T-02/T-03 水位规则、置信度评分和 `--experimental-default` gate；
当前引擎只实现上述三条规则。

## 规则定义

### 1. 共识快照

每个时点不是取某一家公司，而是把所有有效公司合成一个快照：

1. 对每家公司，取 `change_time <= cutoff` 的最近一条报价（包含正好等于 cutoff 的报价）。
2. **主盘口** = 出现次数最多的盘口；次数相同时取最接近中位数、再取较小值。
3. **主水/客水** = 落在主盘口上的公司的平均水位。
4. 快照要求至少 2 家公司落在主盘口（`--min-asian-companies`，默认 2）。
   文档样本的亚盘共识中位数是 8 家、范围 4–8；如需更严格地贴近文档，
   可显式设 `--min-asian-companies 4`。

### 2. 净变化

```
Δline       = line(decision) - line(prev)
Δhome_water = home_water(decision) - home_water(prev)
Δaway_water = away_water(decision) - away_water(prev)
相对水位     = Δhome_water - Δaway_water
```

方向约定：

- `Δline < 0`（盘口更深，主队让得更多）→ **盘口朝主队**；`Δline > 0` → 朝客队。
- `相对水位 > 0`（主水升、客水降）→ **水位朝客队**；`< 0` → 朝主队。

### 3. 触发条件

**conflict**（`conflict_t10` / `conflict_t120`）：

```
|Δline| >= 0.25
|相对水位| >= 0.06
盘口方向与水位方向相反
→ 下注：买水位方向，在 decision 时点下注
```

**water**（`water_t60`）：

```
|Δline| < 0.25          # 盘口没有明显移动
|相对水位| >= 0.06      # 只有水位信号
→ 下注：买水位方向，在 T-60m 下注
```

只比较两个快照的净变化；中间的来回震荡、哪家公司先动都不影响判定。
快照之间的公司集合可以不同：某家公司在新窗口才出现，只贡献到新快照。

### 4. 下注与价格

- 下注方向 = 水位方向。
- 下注盘口 = decision 快照的主盘口；下注方视角：主队买 `line`，客队买 `-line`。
- **consensus 价** = decision 快照该方向平均水位（规则默认价）。
- **best 价** = 主盘口上该方向最高的公司水位（回测同时报告）。
- 结算按 `match_list_all` 最终比分，亚盘含半赢/半输/走盘。

### 文档示例

T-30m `-0.50 / 0.95 / 0.90` → T-10m `-0.75 / 1.02 / 0.82`：

```
Δline = -0.25   → 盘口朝主队
相对水位 = (1.02-0.95) - (0.82-0.90) = +0.15 → 水位朝客队
方向相反 → conflict，买客队，在 T-10m 下注
```

## 安装

```bash
cd F:\PycharmProjects\football-rule-engine
python -m pip install -r requirements.txt
copy .env.example .env
# 编辑 .env，填入 MySQL 密码
```

Python 3.10+。

## 使用

```bash
# 单场：默认跑三条策略
python -m rule_engine analyze --fixture-id 3010473

# 多场 + 指定策略 + 指定价格口径
python -m rule_engine analyze --ids 3010473,3000518 \
  --strategies conflict_t10 water_t60 --price consensus --out-dir out

# 只输出 JSON（多场时为数组）
python -m rule_engine analyze --ids 3010473 3000518 --json-only

# 扫描某一天
python -m rule_engine scan --date 2026-11-05 --strategies all
python -m rule_engine scan --date 2026-11-05 --only-actionable
python -m rule_engine scan --date 2026-11-05 --write --out-dir out/scan

# 一键：导入 + 分析 + 写 tracking CSV
python -m rule_engine run --ids 3010473
python -m rule_engine run --within-minutes 120
python -m rule_engine run --dates 2026-11-05 --leagues 350
```

`--strategies` 接受 `all`、逗号分隔或空格分隔的 key：

```text
conflict_t10   conflict_t120   water_t60
```

`--price` 只影响 `analyze/scan/run/track` 记录的下注价；回测始终同时报告 consensus 和 best。

`run` 常用开关：

| 开关 | 作用 |
|---|---|
| `--skip-import` | 只分析，不重新抓数据 |
| `--skip-analysis` | 只导入，不分析 |
| `--import-dry-run` | 只抓取/解析不写库，然后分析现有数据 |
| `--raise-on-import-error` | 导入失败时不继续分析 |
| `--excel` | 纸面跟踪 CSV 路径，默认 `tracking/paper_tracking.csv` |
| `--no-track` | 不写 CSV |
| `--no-report` | 不写 `out/` HTML/JSON |
| `--ci` | `--no-report` + `--no-save` |
| `--within-minutes N` | 扫描未来 N 分钟内开赛的比赛（建议 ≥120） |
| `--min-asian-companies N` | 快照主盘口最少公司数（默认 2） |

> `--within-minutes` 默认只用联赛白名单；`--all-leagues` 关闭白名单。
> 亚盘历史为空的场次标记 `[skip]`，不生成报告、不写入 CSV。

## 回测

回测对每场比赛跑所请求的策略，并按文档口径输出：

- 注数、命中率；
- ROI（consensus）、ROI（best）；
- 两个 ROI 的 95% bootstrap CI（2000 次，固定种子）；
- 反向下注（opposite）对照；
- 分联赛、分方向、分月、`abs(盘口)<=1.0` 稳健性；
- 逐场 CSV（包含 `prev_line/line/line_delta/rel_water/water_consensus/water_best` 等）。

```bash
python -m rule_engine backtest --from 2025-08-16 --to 2026-11-09 \
  --strategies all --limit 5000 \
  --out out/backtest.csv --summary out/backtest_summary.json
```

只跑单条规则：

```bash
python -m rule_engine backtest --from 2025-08-16 --to 2026-11-09 \
  --strategies conflict_t10 --limit 5000
```

> `--limit` 限制的是**比赛数**，不是注数；全量样本（约 1276 场）需要显式
> `--limit 5000`。每场比赛会为请求的每条策略各生成一行 CSV。

输出表头与文档一致：

```
策略                 注数   命中    ROI best (95% CI)          ROI cons    平均水位  best水位
conflict T-10m        97   76.3%   +37.95% (+23.29% ~ +52.16%)  +35.78%     0.83      0.86
conflict T-120m       60   64.3%   +14.26% (-6.79% ~ +33.92%)   +11.30%     ...
water T-60m          280   58.7%   +10.85% (+0.87% ~ +20.62%)   +4.20%      ...
```

> 文档里的数字来自原分析脚本的 1276 场样本；本仓库重跑得到的具体数值会随
> DB 历史范围、公司可用性、consensus/best 定义而变化。请把本仓库的输出视为同一规则的复现，而不是逐位复刻。

## 月度稳定性

```bash
python -m rule_engine walkforward --input out/backtest.csv \
  --out out/monthly.json --report out/monthly.md

python -m rule_engine walkforward --from 2025-08-16 --to 2026-11-09 \
  --strategies all --limit 5000
```

策略是预注册的，不做参数选择；这里只按自然月展示每条规则的注数/命中/ROI。

## 纸面跟踪

```bash
python -m rule_engine track --ids 3010473 --strategies all
python -m rule_engine track --dates 2026-11-07 --leagues 25

# 比赛结束后回填 result / profit / CLV
python -m rule_engine track-update --excel tracking/paper_tracking.csv
```

CSV 以 `(analysis_at, fixture_id, mode)` 去重；已回填的 result/CLV 不会被覆盖。
`stage` 列为 `T-10` / `T-120` / `T-60`，`mode` 列为策略名称。
CLV 使用开赛前最后一份共识快照（`kickoff - 1s`）。

调度器每触发一条 `(fixture, strategy)` 就追加一行 CSV；如果本轮有多条触发，
只发**一封**邮件，正文列出本轮全部触发行，附件是本轮的小 CSV。不再生成
`tracking/actionable_*.json`。

## 调度器

`python -m rule_engine.scheduler` 每轮：

1. 读/刷新 `data/schedule_cache.json`（每天 11:00、16:00 北京时间后首次运行抓 D-1/D/D+1 赛程）；
2. 对每场、每条策略检查是否落在决策窗口内（默认 grace 15 分钟，
   `SCHEDULE_GRACE_MINUTES`）：
   - `conflict_t120`：距开赛 105–120 分钟；
   - `water_t60`：距开赛 45–60 分钟；
   - `conflict_t10`：距开赛 0–10 分钟；
   早于窗口不分析，晚于窗口不补单（避免在错误时点下注）。
3. 只导入本轮 due 场次的赔率，逐 (fixture, strategy) 分析；
4. 快照覆盖不足时**不标记完成**，下一轮仍会重试；覆盖正常则写入 `data/scheduler_state.json` 的 `done` 集合；
5. 每条触发的 `(fixture, strategy)` 追加一行到 `tracking/paper_tracking.csv`，并发邮件；
   Actions 只保留 CSV，不生成 `actionable_*.json`；
6. 赛程刷新成功后回填已结束场次的 result/profit/CLV。

> 回填发生在 **11:00 / 16:00（北京时间）之后的第一次成功刷新**，且只处理
> 距开赛 ≥3 小时、≤3 天、`result` 仍为空的场次。因此 11:00 这一轮会回填
> 08:00 前开赛的比赛；08:00–11:00 开赛的比赛要等 16:00 那一轮。

> **触发频率**：T-10m 窗口只有 10 分钟宽，cron 必须至少每 ~10 分钟运行一次，
> 否则会漏掉 `conflict_t10`。T-120/T-60 的窗口是 15 分钟宽（默认 grace），
> cron 间隔也应 ≤15 分钟；错过窗口不会补单。

环境变量：

| 变量 | 默认 | 作用 |
|---|---|---|
| `SCHEDULE_GRACE_MINUTES` | `15` | 决策时点之后仍可触发的宽限（T-10m 实际为 0–10 分钟） |
| `SCHEDULE_MIN_ASIAN` | `2` | 快照主盘口最少公司数 |
| `SCHEDULE_PRICE` | `consensus` | 记录/下注价口径 |
| `APP_TIMEZONE` | `Asia/Shanghai` | 调度时区 |

`data/scheduler_state.json` 形如：

```json
{
  "date": "2026-11-20",
  "done": {
    "conflict_t10": [3010473],
    "conflict_t120": [3010473],
    "water_t60": [3010473]
  }
}
```

### 刷新 `schedule_cache.json`

调度器只在缓存“过期”时自动刷新：仅当 `fetched_at` 早于当天最近一次 11:00 / 16:00（北京时间）才会重新抓取 D-1 / D / D+1 赛程（`scheduler.py` 的 `load_or_refresh_schedule`）。手动刷新有两种方式：

**只抓赛程（不分析、不结算、不发邮件）：**

```bash
python -c "from rule_engine.scheduler import collect_schedule, now_local; collect_schedule(now_local())"
```

会重写 `data/schedule_cache.json`，不影响 `data/scheduler_state.json`；本机需要 OpenSSL 1.1.1（见「导入数据」一节的说明）。

**让 scheduler 走完整刷新流程：**

```bash
# 把 fetched_at 改旧（或备份后删除缓存文件），下一次运行就会重新抓取
python -c "import json; p='data/schedule_cache.json'; d=json.load(open(p,encoding='utf-8')); d['fetched_at']='2000-01-01T00:00:00'; json.dump(d,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=2)"
python -m rule_engine.scheduler
```

这样会先刷新赛程、结算已结束注单，再处理本轮 due 场次（可能写 tracker、发邮件）。抓取失败时 `collect_schedule` 在写文件前就会抛异常，旧缓存保持不变。

GitHub Actions 没有单独的手动刷新入口：要么在本地刷新后提交 `data/schedule_cache.json`，要么把 `fetched_at` 改旧并提交，再触发 `workflow_dispatch`（缓存过期时 scheduler 会自己刷新）。

## 导入数据

```bash
python -m rule_engine import --ids 3010473
python -m rule_engine import --ids 3010473,3073201
python -m rule_engine import --dates 2026-11-05 --leagues 192
python -m rule_engine import --ids 3010473 --dry-run
python -m rule_engine import --help
```

写入：`match_list_all`、`odds_live_all`、`odds_live_all_raw`、`odds_history_eu`、`data_import_log`。
要求 `pymysql`、系统 `openssl`（1.1.1；3.x 的 TLS 指纹会被 titan007 断开，或 `--openssl-bin` / `OPENSSL_BIN` 指向 1.1.1 客户端）、根目录 `.env`。

## 数据范围

- 亚盘公司（与文档一致）：`2/3/6/7/10/13/22/23`
  （Bet365、皇冠、明陞、澳门、易胜博、Interwetten、平博、香港马会；
  见 `rule_engine/db.py` 的 `ASIAN_WHITELIST`）
- 如果历史数据是用旧的 `2/3/7/9/11/22` 白名单导入的，需要重新导入
  `6/10/13/23`（或用 `--companies all` 重新抓取），否则快照只会用到部分公司
- 读取 `odds_live_all`（主盘 + 赛前），只取 `line_no=0`、`phase='prematch'`
- 快照 cutoff 为**包含**（`<=`），与文档的 `cutoff <= T-30m` 一致
- 欧赔表仍会导入，但当前三条规则**不使用**欧赔；文档的结论是欧赔没有提供额外确认

## 联赛白名单

白名单硬编码在 `rule_engine/leagues.py` 的 `LEAGUE_WHITELIST`。默认行为：

| 命令 | 默认联赛范围 |
|---|---|
| `run --within-minutes N` | 白名单 |
| `run --dates ...` | 白名单（不传 `--leagues` 时） |
| `import --dates ...` | 白名单（不传 `--leagues` 时） |
| `run --ids ...` / `analyze` | 不过滤（显式指定） |

覆盖方式：

```bash
python -m rule_engine run --within-minutes 120 --leagues 9,36
python -m rule_engine run --within-minutes 120 --all-leagues
```

## GitHub Actions

工作流：`.github/workflows/scan.yml`

- 由 cron-job.org 通过 `workflow_dispatch` 触发；**建议每 10 分钟一次**（T-10m 需要）
- job 内启动临时 MySQL 8 service container
- 抓取/调度在 `.github/docker/crawler.Dockerfile`（OpenSSL 1.1.1）容器里执行：
  titan007 按 TLS 指纹直接断开 OpenSSL 3.x 客户端（表现为 `unexpected eof`），
  `ubuntu-latest` 自带的 OpenSSL 3.x 会导致所有 VIP 页抓取失败
- `python -m rule_engine.init_db` 初始化 `sql/schema.sql`
- `python -m rule_engine.scheduler`
- 提交 `data/schedule_cache.json`、`data/scheduler_state.json`、`tracking/paper_tracking.csv`
- Actions 不生成 `data/*.js`、`out/*.html`、`out/*.json`、`tracking/actionable_*.json`；
  这些只在本机运行 `run` / `analyze` / `scan --write` 时生成

需要的 Secrets：`MYSQL_ROOT_PASSWORD`、`SMTP_HOST/PORT/USER/PASSWORD`、
`SMTP_SSL/SMTP_TLS`（可选）、`MAIL_FROM`、`MAIL_TO`。

> 端口与加密：**465 = 隐式 SSL（`SMTP_SSL=true`）**，**587 = STARTTLS**。
> 邮件正文只列本轮新增触发的注单；附件是只含本轮注单的小 CSV。

## 项目结构

```
football-rule-engine/
  rule_engine/
    config.py      # .env / 环境变量
    db.py          # 只读 MySQL：比赛、亚盘、欧赔、sup、回测比赛
    models.py      # 数据类
    features.py    # 共识快照 + 快照净变化
    rules.py       # 三条策略的定义、阈值、评估
    engine.py      # 单场单策略分析结果（方向/盘口/水位/审计）
    backtest.py    # 逐场回测 + best/consensus/opposite + bootstrap CI
    walkforward.py # 月度稳定性
    tracker.py     # 纸面跟踪 CSV（result/profit/CLV）
    scheduler.py   # 三窗口调度 + 结算 + 邮件
    settle.py      # 回填已结束场次的 result/profit/CLV
    notify.py      # SMTP 邮件
    report.py      # HTML + JSON 报告
    cli.py         # analyze / scan / run / track / backtest / walkforward
    settlement.py  # 亚盘结算（半赢/半输/走盘）
    leagues.py     # 联赛白名单
    init_db.py     # 用 sql/schema.sql 初始化 MySQL
    ingest/        # 独立导入栈
  sql/schema.sql
  .github/workflows/scan.yml
  tests/
  data/           # scheduler_state.json / schedule_cache.json
  tracking/       # paper_tracking.csv（Actions 只保留这个文件）
  out/            # 本地分析报告
```

## 测试

```bash
python -m unittest discover -s tests -v
```

测试不依赖数据库。

## 已知限制

1. **只有三条规则**：没有旧的水位区间、澳门反向、欧亚交叉等启发式规则，也没有置信度评分。
2. **样本与多重检验**：文档自己指出 97 个 T-10m 事件是在 9 个窗口 × 多个模式里扫描得到的，存在多重检验风险；本引擎不对此做统计校正。
3. **价格口径**：`best` 是同一主盘口上的最高公司水位，不是跨盘口最优价；实际限额、滑点和失效报价未建模。
4. **快照公司数**：DB 公司可用性随场次变化；少于 `--min-asian-companies` 时不触发。
5. **欧赔未接入规则**：当前三条规则是纯亚盘内部信号。
6. **调度频率**：T-10m 需要 ~10 分钟一次的 cron，否则会漏单。

## 下一步

- 只用 2024-25 全量 history 做一次真正的样本外确认（文档建议）；
- 纸面跟踪三条规则，重点看 `conflict_t10` 的 best/consensus 差距；
- 不要用 2025-26/26-27 调阈值；如需扩展，另建预注册规则。
