# HeadA fresh-book 修复与 source/filter 深挖 v1

## 数据快照

| 项目 | 值 |
|---|---|
| 当前 raw | `runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1/{shadow_decisions,would_live_entries}.jsonl` |
| canonical | `runtime/weather.db`，`MAX(fact_built_at_utc)=2026-07-28T08:22:50Z` |
| 当前固定分母 | 2026-07-16..25，84 张 `dist>0`、已结算、first would-live ticket；8 个 target dates，0 unsettled / 0 missing |
| 历史外部校验分母 | 2026-05-06..06-30，333 张 HeadA hot-tail ticket；53 个 target dates |
| 执行主口径 | captured fresh best ask 做 taker，5 shares，Weather 官方 fee；这是保守反事实，不是 shadow fill |
| fresh-book 事故窗 | 当前进程 2026-07-25T06:26:55Z 启动后；7/26..28 共 49 个 unique candidate、565 次重复失败 |
| CLOB coverage gate | `false`，原因是另一条 Busan BUY_NO 的 1 行 unknown fee lineage；本报告不发布 `live_real` PnL |
| 产物 | `generated/heada_would_live_filter_diagnostic_v1/` |

## 结论与动作

1. **fresh-book 根因已修到代码并验证**：启动脚本在 loop-child 路径只赋值、未 export
   `LOW_PRICE_YES_LOTTERY_MARKET_PROXY`；Python 又只读这个私有变量，实际因此走直连。
   同一 token 现场复测：direct 4.01 秒 timeout，`127.0.0.1:7890` 0.47 秒 HTTP 200。
   修复后 Python 在仅有 canonical `WEATHER_DATA_FEED_MARKET_PROXY` 时成功解析 7890 并取到 book。
2. **当前 shadow 策略不是 taker 策略**：runtime 是 `maker_first_fraction=1.0`、`maker_only=true`；
   7/16 后是 zero-notional shadow，没有真实订单。本文 ROI 为了回答“当时真能买到吗”，故意采用
   taker-at-fresh-ask 保守估值。历史 raw submitted order 中 141/146 是 maker-only，只有早期 5/146 是
   guarded taker。
3. **当前 ECMWF/GFS 差异是真实的 in-window 差异，但不是稳定 source alpha**：
   ECMWF−GFS ROI 差为 **+144.0pp**，按 target_date block bootstrap 95% CI
   **[+57.9pp,+242.9pp]**；但历史 333 张的差仅 **+30.4pp，CI [-43.0pp,+103.2pp]**，
   且历史 GFS 本身仍为 **+21.3% ROI**。
4. **没有发现可称为“完全不会中”的 PIT hard filter**。当前最显眼的 `ask<=8c` 是 18/18 全输，
   train 13/13、forward 5/5 都全输；但历史同规则 130 张中 12 张，ROI +34.8%，直接否定“永远不中”。
   它只能作为预注册的 lottery-risk / no-size-up shadow tag，不能 hard block。
5. 不改 live selector、不禁 GFS。下一步应做 source-aware 概率重校准并继续 frozen shadow；
   当前 source/score telemetry 还暴露出 76/84 行 artifact 缺失后用默认特征的问题，不能拿这些默认值造筛选器。

```text
significance=PASS（仅当前 8 日 ECMWF-vs-GFS 差）;
baseline=FAIL（历史同分母不支持永久 GFS 负 alpha）;
forward=FAIL（仅 2 个 forward dates，GFS forward 已由 -100% 回到 +1.0%）;
conclusion=inconclusive
```

## fresh-book 链路：根因、影响和修复

### 根因证据

- 当前进程有 `WEATHER_DATA_FEED_MARKET_PROXY=http://127.0.0.1:7890`，没有
  `LOW_PRICE_YES_LOTTERY_MARKET_PROXY`。
- runner 修复前的 `market_proxy_url()` 只认后者，空值被解释为 direct。
- 所有 timeout 后的 failover 日志都写 `status=skipped`；其真实 reason 是
  `direct_connection`，旧日志没有打印 reason。
- 独立 data-feed failover 日志在同一时段持续显示 7890 HTTP 200，排除“本地代理整体一直死亡”。

### 影响半径

| target_date | unique candidate | 重复 fresh-book failure rows |
|---|---:|---:|
| 2026-07-26 | 25 | 274 |
| 2026-07-27 | 16 | 173 |
| 2026-07-28 | 8 | 118 |
| 合计 | **49** | **565** |

错误类型为 504 次 `ConnectTimeout`、50 次 connection reset、11 次 connection refused。
这 49 个只证明“未进入 fresh-book evidence funnel”，不能声称全是漏掉的 would-live 单：
修复后仍需过 fresh ask band/cushion、depth、fee edge 和 dedupe。
逐条清单在 `fresh_book_failure_unique_candidates.csv`。

### 修复

- runner 改用共享 market-proxy resolver：策略私有变量未设置时回落 canonical proxy；
  显式 `direct` 仍可关闭代理。
- launcher 显式 export 策略 proxy，并调用共享 shell normalizer。
- failover 日志增加 `reason/returncode/stderr`；blocked row 增加实际 `book_proxy_url`。
- 定向验证：shell syntax 通过；proxy/sizing tests **21 passed**；canonical-only 环境实取
  CLOB book 成功（38 asks）。

### shadow 部署验证

| 项目 | 结果 |
|---|---|
| develop commit | `bac46d01` |
| Mac shadow checkout commit | `f9e418be`（scoped cherry-pick） |
| tmux context | `weather-data-feed-jrs / low_price_yes_lottery_shadow_v1` |
| wrapper / runner PID | `53533 / 53551` |
| 进程代理环境 | `LOW_PRICE_YES_LOTTERY_MARKET_PROXY=7890` 且 canonical proxy=7890 |
| 最新 cycle | `2026-07-28T08:37:12Z`，planned=0 / blocked=0 / live=false |
| 真实下单变化 | 0；live journal 最后一行仍为 2026-07-15 |

重启时只停止并恢复 zero-notional HeadA shadow，没有启用 live、没有改变 sizing 或资金参数。
当前 cycle 没有 eligible candidate，因此 runtime 还没有产生一条新的 fresh-book decision row；
链路验证由同 checkout、同 resolver、同 7890 对 CLOB `/book` 的成功实取完成。

## Taker / maker 口径

| 层 | 实际含义 |
|---|---|
| 当前 84 张绩效 | **taker 反事实**：fresh ask + 官方 fee，保证不把虚构 maker fill 当收益 |
| 当前运行实例 | **100% maker-first**：`maker_first_fraction=1.0`，fixed 5 shares，shadow 不发单 |
| taker fallback | 当前配置分配为 0；maker lifecycle 在 shadow 关闭 |
| maker-limit ROI | 仅 price-only 上限；无 queue/fill/adverse-selection 证据，不作为主结论 |
| 历史 raw submitted | 141 maker-only / 5 guarded taker；这是 child-order rows，不等于 146 个独立 thesis |

所以“统计用 taker”与“策略执行 maker-first”不矛盾：前者是可执行保守评估，后者是实际下单设计。

## Source 深挖

### 当前 shadow 与历史同分母

| 窗口 | source | rows | wins | 胜率 | ROI |
|---|---|---:|---:|---:|---:|
| 当前 8 dates | ECMWF | 54 | 11 | 20.4% | +70.8% |
| 当前 8 dates | GFS | 30 | 1 | 3.3% | -73.2% |
| 历史 53 dates | ECMWF | 222 | 34 | 15.3% | +51.7% |
| 历史 53 dates | GFS | 111 | 16 | 14.4% | +21.3% |

当前 train（截至 7/23）GFS 22/0、ROI -100%；7/24..25 forward 为 8/1、ROI +1.0%。
方向已经明显回升，forward 只有 2 个 date block。

### 价格和距离不能解释全部差异

| PIT 特征均值 | ECMWF | GFS |
|---|---:|---:|
| fresh ask | 11.4c | 11.9c |
| ticket 高于 forecast 的 settlement-lattice steps | 0.72 | 0.90 |
| `model_p - ask` | 26.0c | 30.5c |
| fresh spread | 1.52c | 1.65c |

GFS 票略贵、略远、模型 edge 反而更高，但差距都不足以机械解释 11/54 vs 1/30；
而且 forecast source 是按城市固定路由，source 与城市、气候和市场关注度完全混杂，不是随机 A/B。

### 更有解释力的是“hot-tail 是否真的发生”

| settlement 相对原 forecast | ECMWF | GFS |
|---|---:|---:|
| winning bracket 高于 forecast | 35/54（其中 11 张命中） | 12/30（其中 1 张命中） |
| forecast 落在 winning bracket 内 | 4/54 | 5/30 |
| winning bracket 低于 forecast | 15/54 | 13/30 |

按所买 ticket 看，GFS 的 29 张输票中 **22 张最终落在 ticket 下方、7 张落在上方**；
ECMWF 的 43 张输票则为 **20 张下方、23 张上方**。这说明当前 GFS cohort 更多是“热尾根本没走到”，
ECMWF 则大量发生真实热尾、只是 exact bracket 又被 overshoot。机制上支持 source-aware calibration，
不支持把 `forecast_source=GFS` 本身当因果 hard gate。

## 候选筛选反事实

| 拟筛条件 | 当前 removed | 当前 wins | removed ROI | 保留后 ROI | 历史 removed / wins / ROI | 裁决 |
|---|---:|---:|---:|---:|---|---|
| GFS | 30 | 1 | -73.2% | +70.8% | 111 / 16 / +21.3% | 当前坏，历史不支持永久禁用 |
| ask <=8c | 18 | 0 | -100% | +34.8% | 130 / 12 / +34.8% | no-size-up shadow tag；不能 hard block |
| 距 forecast >1 lattice step | 20 | 3 | +28.0% | +15.0% | 109 / 17 / +51.5% | 明确不能筛 |
| 距 forecast >1.5 steps | 8 | 1 | +8.9% | +18.9% | 24 / 7 / +212.2% | 明确不能筛 |
| spread >2c | 10 | 1 | -12.4% | +21.9% | 128 / 17 / +21.5% | 不稳定 |
| model edge >=30c | 25 | 3 | -2.2% | +26.8% | 94 / 8 / -16.6% | 概率过度自信诊断；train/forward 翻号 |
| GFS 且 ask<=8c | 6 | 0 | -100% | +22.9% | 43 / 8 / +161.9% | 历史直接反证 hard filter |

`ask<=8c` 当前剔除后 ROI 提升 +16.8pp，date-block CI [+9.2pp,+24.7pp]；但 0/18 的
one-sided 95% 命中率上界仍为 15.3%，且历史已有 12 个赢家。当前结果能定义一个新的 frozen shadow
hypothesis，不能定义“完全不会中”。

本轮 K=9 个诊断候选，未做多重检验校正；因此不把任何点估升级成 live selector。

## Signal / evidence funnel

| 层 | grain | rows | dates | 说明 |
|---|---|---:|---:|---|
| captured would-live | first signal | 104 | 8 | 已拿到 fresh book |
| intended mechanism | `dist>0` ticket | 84 | 8 | 当前固定绩效分母 |
| invalid distance branch | ticket | 20 | 7 | active prod checkout 未含最新 geometry fix |
| post-restart book candidate | unique signal | 49 | 3 | 565 次失败，属于 coverage gap |
| settled/executable taker expression | ticket | 84 | 8 | settlement 100%，fresh ask 100% |
| actual fill | fill | 0 | 0 | zero-notional shadow |

## Frozen forward、三门与 8 环

- train：7/16..23，59 张、8 胜、ROI +12.1%。
- forward：7/24..25，25 张、4 胜、ROI +32.1%；只有 2 个 date block。
- 7/26..28 因 fresh-book 事故没有可比 would-live 分母，不能算策略 0 单或 0 胜。
- 三门：当前 source 差的显著性 PASS；历史基准与 frozen forward 均 FAIL，最终 `inconclusive`。
- 8 环：覆盖描述性绩效、date-block 推断、信号判别/概率质量（沿用 v2）、fresh-book 执行、
  target-date 相关性和历史同分母基准；未覆盖 maker fill/queue、容量、真实 fill PnL。

## Bloodline placement

- fresh-book 修复位于 `candidate -> plan` 前的 PIT quote evidence 层。
- 筛选诊断仍是 `fact_signal_candidates`/shadow opportunity grain，不创建平行 fact。
- `ask<=8c` 与 source/error-direction 只记录为 shadow diagnostic；不改 live eligibility。
- evaluator：
  `scripts/analysis/forecast_quality/research_heada_would_live_filter_diagnostic_v1.py`。
