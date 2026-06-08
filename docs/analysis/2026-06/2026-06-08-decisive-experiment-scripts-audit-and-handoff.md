# 天气策略合理性报告审计版

> 生成于 2026-06-08。本文是对同目录
> `2026-06-08-decisive-experiment-scripts-audit-and-handoff-draft.md` 的本机核验版。
> 原始草稿已保存，不再直接当作最终结论引用。

## 结论

当前可发布的判断是：

1. **不要加注、不要放大 sizing、不要继续沿 H_A 全局模型 alpha 路线调参。**
2. **H_A 的“全局概率质量能打赢市场”证据不成立**：现有文档显示 raw model 在 OOS Brier 上输给市场，0.3 model + 0.7 market 的微弱改善不足以当显著 alpha。
3. **H_B（market-free 结构偏差）和 H_C（GFS/城市条件子池）仍是未完成检验，不是已证赢家。**
4. **原草稿的方向大体对，但证据表达过硬**：它把若干历史快照、不可本机复核的脚本审计、以及当前 DB 数字混在一起了。最终版本必须把这些拆开。

最关键的操作结论：

- live 侧：保持保守，不因同窗后验切片加仓。
- 研究侧：先把两个决定性实验脚本落库、修口径、跑 coverage/CI/baseline/forward，再谈 H_B/H_C。
- 文档侧：不要再内联会漂移的 live_real 行数，统一引用 coverage gate 和当前 DB 快照。

## 数据快照

本轮没有重新跑 `scripts/ops/sync_weather_remote.sh`，原因是 2026-06-07 的 fill recovery 报告明确提醒“不要盲目 sync，以免覆盖 rebuilt local clob_fills”。本轮使用当前本机 WSL DB 快照：

| 项 | 当前值 |
|---|---:|
| DB | `runtime/weather.db` |
| DB mtime | `2026-06-08 09:32:53 +0800` |
| `MAX(fact_built_at_utc)` | `2026-06-08T01:32:33.618777+00:00` |
| CLOB coverage gate | `PASS` |
| `fact_trades live_real rows` | `1333` |
| `fact_trades live_real fill_ids` | `1333` |
| `clob_fills.jsonl rows` | `1333` |
| `missing_order_rows` / `over_order_keys` | `0` / `0` |
| `db_fill_cost_minus_fact_cost` | `0.0` |

强制 5 行自检：

| 检查 | 结果 |
|---|---:|
| `trade_class=live_real` | `1333` |
| `trade_class=live_simulated` | `1104` |
| `trade_class=paper` | `2285` |
| `trade_class=snapshot_replay` | `636` |
| `settlement_status=settled` | `5033` |
| `settlement_status IS NULL` | `325` |
| `fact_signal_candidates` | `23893` rows, `7841` eligible, `2886` paper_ordered, `520` live_filled |
| CLOB submitted orders | `1562` orders, `1333` with fill |
| CLOB error orders | `151` orders, `0` with fill |

## 当前可复算绩效

以下只读 `fact_trades`，筛选 `trade_class='live_real'` 且 `settlement_status='settled'`。

### 月度窗口

| target_date 窗口 | fills | cost | PnL | ROI | win_rate |
|---|---:|---:|---:|---:|---:|
| 2026-05-16..2026-05-31 | 649 | `$1806.60` | `+$56.14` | `+3.1%` | `55.3%` |
| 2026-06-01..2026-06-05 | 472 | `$1209.08` | `-$99.47` | `-8.2%` | `43.2%` |

按方向：

| 窗口 | side | fills | cost | PnL | ROI | win_rate |
|---|---|---:|---:|---:|---:|---:|
| 2026-05-16..2026-05-31 | BUY_NO | 448 | `$1367.06` | `+$2.90` | `+0.2%` | `62.7%` |
| 2026-05-16..2026-05-31 | BUY_YES | 201 | `$439.54` | `+$53.24` | `+12.1%` | `38.8%` |
| 2026-06-01..2026-06-05 | BUY_NO | 268 | `$798.10` | `-$22.85` | `-2.9%` | `58.2%` |
| 2026-06-01..2026-06-05 | BUY_YES | 204 | `$410.98` | `-$76.62` | `-18.6%` | `23.5%` |

解读：6 月转负、BUY_NO 高胜率但 ROI 可为负，这两个方向性判断成立。但这些仍是点估计，未过三门检验，不能单独作为 keep/cut 依据。

### 2026-05-31..2026-06-06 实例窗口

当前 DB 已比 2026-06-07 历史报告多了成交/结算。按 `producer_run_id` 后缀 + legacy bucket 合并后：

| instance_total | fills | settled_cost | settled_pnl | settled_roi | open_cost |
|---|---:|---:|---:|---:|---:|
| `mid_price_core_v1_25_75_total` | 404 | `$1041.44` | `-$166.40` | `-16.0%` | `$0.00` |
| `mid_price_core_v1_side_band_total` | 116 | `$281.71` | `+$22.15` | `+7.9%` | `$0.00` |
| `mid_price_core_v2_25_75_total` | 130 | `$323.08` | `-$96.99` | `-30.0%` | `$0.00` |
| `maker_queue_v1 legacy` | 3 | `$9.38` | `+$7.61` | `+81.1%` | `$0.00` |

解读：主力 v1 25-75 与已停 v2 仍明显为负；side_band 仍为正但样本短，最多标 `shadow_candidate`，不能标 `confirmed`。

## 原草稿哪些是对的

| 草稿判断 | 审计结论 |
|---|---|
| 67 fills 旧口径作废 | 对。当前 gate 下 `live_real=1333`，旧 67 笔不能作为 live 证据。 |
| coverage gate 是发布 live_real PnL 的硬门 | 对。当前 gate 通过，所以 DB 内部策略 PnL 可用。 |
| H_A 全局模型 alpha 证据不成立 | 基本对。Brier 文档支持 raw model 输市场；但“彻底证伪模型所有用途”过强，因为 IC/排序能力未测。 |
| H_B/H_C 没有干净测过 | 对。它们目前不是 no-go，也不是 go。 |
| 不能按 5 月赢家/近窗赢家直接切城市或加仓 | 对。属于同窗后验选择，有明显过拟合风险。 |
| 三门框架：显著性、基准、前瞻 | 对。应作为后续策略动作门槛。 |
| BUY_NO 高胜率不等于 edge | 对。当前 6 月 BUY_NO win_rate `58.2%` 但 ROI `-2.9%`。 |
| 季节条件化、IC、C2 口径核对值得补 | 对。现有文档支持它们是便宜且可能关键的研究项。 |

## 原草稿哪些是错的或需要降级

| 草稿内容 | 问题 | 最终修正 |
|---|---|---|
| `live_real=1246~1302` | 已过时。当前 DB/gate 是 `1333`。 | 写成“行数会漂移，以 coverage gate 和当前 DB 为准”。 |
| 近 7 天表直接引用 `-$232.74 / -16.0%` | 这是 2026-06-07 历史快照。当前 DB 合并实例后数值已变。 | 保留为历史证据时必须标日期；当前版使用 2026-06-08 复算表。 |
| `docs/analysis/model_vs_market.md` | 当前仓库未找到该路径。 | 不能作为当前可点击来源；月度数值改由当前 DB 复算。 |
| `research_market_structural_edge.py` / `research_executable_edge.py` 已写已自测 | 本文初审时 `scripts/analysis/` 没有这两个文件。 | 初审结论是“RELIABILITY 文档声称已写，但本机工作树不可复核”；2026-06-08 后续已补齐本机版本，见下方“后续落地记录”。 |
| “15 条发现全部 confirmed，0 条驳回” | 本机没有被审计脚本，也没有对应审计产物可复核。 | 降级为“外部/草稿审计声称”，不能作为最终事实。 |
| “所有代码行号引用均来自验证者实读源码” | 当前最终版无法验证源码行号。 | 删除此类绝对表述。 |
| “样本够大，因此统计显著” | 样本量大不等于已过 CI/baseline/forward。 | 改为“点估计方向不利，足以支持不加注；未完成三门，不支持永久砍规则”。 |
| “H_A 已死” | 对全局 Brier 成立；对排序 IC、条件子池、sizing 信号不完整。 | 改为“H_A 全局概率 alpha 已失败；模型残余用途未完成检验”。 |
| “右半边 2/5/8 环代码已写好” | 初审时 5/8 对应脚本未落库；2 环只有部分脚本可见。 | 改为“设计方向正确，但必须以本机落库脚本和三门产物为准”。 |
| “这台 Mac 没有 DB” | 与当前环境不符。当前 WSL 本机有 `runtime/weather.db`。 | 删除。 |

## 最终可引用版本

这套天气策略当前不能按“已找到可加仓 edge”处理。最强证据不是某个城市或某个子池亏了，而是三件事同时成立：

1. 当前 `live_real` fill 链路已经通过 coverage gate，可以用 `fact_trades` 看真实成交后绩效；
2. 当前 DB 复算显示 6 月窗口整体转负，主力 `mid_price_core_v1_25_75_total` 在 2026-05-31..2026-06-06 的 settled ROI 为 `-16.0%`；
3. 支撑继续翻案的 H_B/H_C 在初审时没有落库脚本；2026-06-08 已补齐 Step1/Step2 脚本并生成三门产物，但结果仍是 `inconclusive`，不支持 live 加仓。

因此，当前动作不是“砍一批近窗亏损城市”，也不是“加注 GFS 表现好的城市”，而是：

1. **冻结 live 风险**：不加仓、不放大 sizing；只允许已设定的小规模 shadow / live 观察。
2. **继续复核可复核脚本**：`research_market_structural_edge.py` 和 `research_executable_edge.py` 已落到 `scripts/analysis/`，并修掉日期切分、future orderbook、join fanout、cluster bootstrap、baseline 和多重检验的第一版硬伤；后续只按它们的三门标签引用结论。
3. **把 H_B/H_C 分开判**：
   - H_B：同价位无脑 BUY_NO baseline 之外是否有超额。
   - H_C：GFS/城市子池必须 train 选池、freeze、holdout 复核。
4. **报告只用三门标签发结论**：`significance`、`baseline`、`forward` 任一缺失，最多 `inconclusive` 或 `shadow_candidate`，不得写 `confirmed`。

## 后续落地记录

2026-06-08 已完成优先级 1/2：

| 项 | 当前状态 |
|---|---|
| 草稿废弃标记 | 已在 `2026-06-08-decisive-experiment-scripts-audit-and-handoff-draft.md` 顶部加“废弃提示”。 |
| Step1 H_B 脚本 | 已新增 `scripts/analysis/research_market_structural_edge.py`。默认产物：`2026-06-08-market-structural-edge.{json,md}`。当前 gates：`significance=FAIL`、`baseline=FAIL`、`forward=PASS`、`verdict=inconclusive`。 |
| Step2 执行脚本 | 已新增 `scripts/analysis/research_executable_edge.py`。默认产物：`2026-06-08-executable-edge.{json,md}`。当前 gates：`significance=FAIL`、`baseline=FAIL`、`forward=NA`、`verdict=inconclusive`。 |
| raw orderbook 2B | 当前脚本 fail-closed；在实现 `snapshot_ts <= decision_snapshot_ts_utc` 和 market 去重前，不发布 raw orderbook executable edge。 |

## 文件状态

- 原始草稿：`docs/analysis/2026-06/2026-06-08-decisive-experiment-scripts-audit-and-handoff-draft.md`
- 本审计最终版：`docs/analysis/2026-06/2026-06-08-decisive-experiment-scripts-audit-and-handoff.md`
- H_B 结构检验产物：`docs/analysis/2026-06/2026-06-08-market-structural-edge.md`
- Step2 执行检验产物：`docs/analysis/2026-06/2026-06-08-executable-edge.md`
- 关键历史来源：`docs/analysis/2026-06/2026-06-07-fill-recovery-and-performance-recalc.md`
- 口径来源：`docs/WEATHER_ANALYSIS_CONTRACT.md`
