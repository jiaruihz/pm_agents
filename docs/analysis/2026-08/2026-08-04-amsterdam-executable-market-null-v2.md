# Amsterdam executable market null v2

Status: `captured-book taker replay / 14 D1 target dates / inconclusive / no-live-change`

## 结论

用生产 collector 实际保存的 full-ladder order book 重算后，v1 的 sampled-price proxy 确实低估了 taker 摩擦。主 D1 同分母中，按市场概率随机选 current-bracket YES/NO 的 5-share net ROI 为 `-2.06%`（95% CI `[-6.71%, +1.23%]`），而 v1 proxy 是 `-0.25%`。真实 ask、spread 与 fee 合计使点估再差约 `1.81pp`。

当前 market-prior candidate 在同一 captured book 上，proper score 只得到点估改善，CI 仍跨 0；按每个 target date 的首次正 edge 执行共 10 笔、8 胜，5-share net ROI `+10.96%`，但同 rows 的纯 market direction 是 10 笔、9 胜、`+22.46%`，candidate 超额 ROI `-11.50pp`（95% CI `[-38.54pp, 0.00pp]`）。因此它没有打败真实市场执行基准，不能 frozen 或升 live。

## 冻结目标与分母

```text
在 2026-07-15..2026-07-28 的 Amsterdam 10-minute D1 checkpoints 上，
只连接 observed_at 之前最近且不超过 45 分钟的 production full-book snapshot，
比较 market、V7 weather-only 与 fixed market-prior posterior；
主指标为同 rows 的 Brier/logloss，以及 5/10-share ask-VWAP + Weather fee 的 net ROI。
```

- probability/checkpoint unit：10-minute state；按 target_date 等权。
- execution unit：captured-book counterfactual，不是 submitted order 或 actual fill。
- settlement：canonical `settlement_outcomes / pm_history`。
- exact bracket：当前档 YES 代表最终正好停在当前档；当前档 NO 代表最终离开该档。
- market-prior 参数沿用既有 prototype：intercept `-0.0170352`、weather disagreement beta `0.0641052`；posterior 使用本轮同一 captured-book `p_market` 重新计算，没有沿用更鲜的 price-reference posterior。

## 数据快照与完整性

- production manifest：2026-08-04 12:54 UTC；`db_route=healthy`。仓库入口是 symlink，解引用后与 `/Volumes/jrs/pm_agents/runtime/weather.db` 同 device/inode；manifest warning 仅为另一运行进程 checkout SHA drift。
- canonical fact 快照：`fact_trades` 4,898 rows，`MAX(fact_built_at_utc)=2026-08-04T11:43:46.231439+00:00`；4,860 settled、38 未标 settlement。本文不发布 `live_real` PnL，因此不使用 fill 表作为回放分母。
- full-book raw：741 gzip files、1,237,406 rows scanned；Amsterdam 26,564 rows，1,288 `event×snapshot` groups、19 event dates。
- canonical winner：16 target dates，覆盖 2026-07-15..2026-08-01。
- full-ladder executable：1,064 snapshots / 16 dates；182 snapshots settlement 未覆盖、31 winner bracket 未在该 book、10 ladder incomplete、1 snapshot 5/10-share favorite depth 不足。
- D1 input：1,536 eligible checkpoints；490 没有 45 分钟内的 prior book、164 current bracket 缺失、5-share depth blocker 4，最终 878 rows / 14 dates。book age median `982s`、p90 `1,770s`、max `2,658s`。
- 10-share 最终 875 rows；相对 5-share 只多损失 3 个 depth rows，当前规模下 depth 不是主要区别。

### 双漏斗

```text
signal funnel (checkpoint grain):
1,536 eligible D1 checkpoints
  -> 878 PIT book-matched executable checkpoints
  -> market-prior positive-edge 158 rows / 10 dates
  -> first positive per target date 10 counterfactual trades

evidence funnel (snapshot/checkpoint grain):
1,288 Amsterdam captured snapshot groups / 19 dates
  -> canonical settlement 16 dates
  -> full-ladder executable 1,064 snapshots / 16 dates
  -> D1 same-checkpoint executable 878 rows / 14 dates
  -> actual submitted/fill evidence: 0（本报告未声称 actual fills）
```

## Probability 层：同 captured book

5-share 与 10-share 的 probability rows 相同；下表按 target_date 等权。

| Candidate | Brier | Logloss | Direction accuracy |
|---|---:|---:|---:|
| market | 0.06668 | 0.21373 | 89.02% |
| standalone V7 weather | 0.08045 | 0.25625 | 88.64% |
| fixed market-prior | 0.06503 | 0.20946 | 89.36% |

相对 market：

- standalone weather：ΔBrier `+0.01376`，95% CI `[-0.02726,+0.05699]`；Δlogloss `+0.04252`，CI `[-0.06339,+0.15761]`。
- market-prior：ΔBrier `-0.00166`，95% CI `[-0.00502,+0.00138]`；Δlogloss `-0.00428`，CI `[-0.01178,+0.00251]`。

market-prior 点估方向正确，但 proper-score CI 均跨 0；probability baseline gate 未通过。

## 真实 ask/depth taker replay

### 全 checkpoint，5 shares

| Policy | Rows / dates | Win / expected-win rate | Net ROI | 95% CI |
|---|---:|---:|---:|---:|
| market direction | 878 / 14 | 84.97% | -5.34% | [-12.63%, +1.38%] |
| market-probability randomized | 878 / 14 | 83.09% | -2.06% | [-6.71%, +1.23%] |
| standalone weather direction | 878 / 14 | 88.04% | +3.07% | [-2.57%, +8.03%] |
| fixed market-prior direction | 878 / 14 | 85.76% | -4.33% | [-12.19%, +2.92%] |

这是假设每 10 分钟重复建立 5-share 新仓的诊断分母，不是可直接执行的持仓策略。所有 CI 都跨 0。

### 首次正 edge / target date，5 shares

| Policy | Signals / dates | Correct | Net ROI | 95% CI |
|---|---:|---:|---:|---:|
| market-prior candidate | 10 / 10 | 8/10 | +10.96% | [-20.21%, +39.52%] |
| market direction，same rows | 10 / 10 | 9/10 | +22.46% | [-5.57%, +48.82%] |
| market randomized，same rows | 10 / 10 | expected 71.13% | +3.97% | [-0.38%, +7.95%] |

market-prior 相对 market-direction 的 paired ROI delta 为 `-11.50pp`，95% CI `[-38.54pp, 0.00pp]`。10-share 结论同号：candidate ROI `+10.81%`，market same rows `+22.34%`。

逐笔归因解释了为什么 candidate 的绝对 ROI 为正却没有模型 alpha：10 笔中 market-prior 与 market direction 有 9 笔选择同一侧；其中 8 笔共同正确、1 笔共同错误。唯一一次不同方向是 7/20：market 的 `P(cross)=45.38%`，选择当前 20 YES；market-prior 将其推到 `50.63%`，翻成当前 20 NO，最终 winner 仍是 20。5-share 下 market 该笔净赚 `$1.94`，candidate 净亏 `$2.36`，单笔造成 `$-4.30` paired PnL delta；这正好等于全部 10 笔 candidate 相对 market 的 PnL缺口。故 `8/10、+10.96%` 主要是 same-row market selection 的收益，不是 weather correction 的收益。

进一步的纯盘口 ablation 也不支持把 selection 收益归因给天气：只用同一 normalized full-ladder market probability 与可执行 ask 定义正 edge，可产生66个 checkpoints / 8 dates；8个日期全部包含在 market-prior 选中的10日内。纯盘口首次正 edge/date 为8笔、5胜，ROI点估 `+25.41%`，但95% CI `[-32.88%,+104.64%]`。market-prior 只额外加入7/20和7/22：前者是上述错误翻向，后者正确，未形成独有的稳定增量。另有4个未被market-prior选择的日期，market first-checkpoint实际4/4全对，但都是约0.98–0.999的高成本小盈利；剔除它们会机械提高选中组ROI。因此这里的高ROI混有price-band/capital-efficiency选择和小样本运气，并非“天气挑出了市场会判断对的日期”。纯盘口正edge自身也使用book内归一化，仍是research ablation，不是已确认套利。

把这10个A组时点的market direction probability作为零假设，市场预期命中数为`7.15/10`，实际为`9/10`；Poisson-binomial下出现至少9胜的概率为`14.85%`。这不是足以拒绝市场概率零假设的罕见结果，且A与market-prior参数均来自历史posthoc研究，未计入选择/调参自由度后的真实显著性只会更弱。因此A组盈利是selection-alpha候选证据，但当前应归类为“运气无法排除”，不能称为已识别alpha。

旧 standalone `p_weather-cost` 表达在 first-positive/date 上为 14 笔、10 胜、ROI `+2.77%`；同 rows market direction 14/14、ROI `+2.14%`，paired delta `+0.63pp`。这个小幅 selected-trade 改善不能推翻其 probability proper score 未胜 market，而且该表达已被确认不应把 weather-only probability 当 fair price，因此只保留 lineage diagnostic。

### 完整 exact ladder，5 shares

| Market-only policy | Snapshots / dates | Net ROI | 95% CI |
|---|---:|---:|---:|
| buy exact-bracket favorite | 1,064 / 16 | -20.69% | [-53.88%, +12.19%] |
| randomized by normalized ladder probability | 1,064 / 16 | -7.39% | [-19.33%, +3.44%] |

两者 CI 均跨 0；favorite 的日期间方差极大。这一 grain 与 D1 current-bracket binary 不混用。

## 对 v1 proxy 的修正

v1 的 randomized fee-proxy ROI 为 `-0.247%`；本轮 D1 captured-book 5-share randomized ROI 为 `-2.056%`，相差 `-1.81pp`。差额来自实际 side ask/spread、可见深度 VWAP与 fee，而不是天气模型。由此可见 maker 若能稳定改善 1–2c，经济上确实可能重要；但本报告没有真实 maker order lifecycle，不能把 future touch 当 fill，也不估 maker ROI。

## 三门与动作

- significance：`FAIL`。market-prior proper-score CI 跨 0；首次交易 ROI 与超额 ROI 未显著为正。
- baseline：`FAIL`。candidate 没有打败同 rows market direction；old weather-only proper score 也未胜 market。
- forward：`NA`。这是 7/15–7/28 historical captured-book replay，market-prior 参数为既有 posthoc prototype，不是本报告之后的 untouched forward。
- conclusion：`inconclusive`。
- action：保持 zero-notional；把本 v2 captured-book baseline 固定为下一版 event-innovation / market-prior 模型的执行基准，不改 live。

8 环：已覆盖描述性绩效、target-date bootstrap、binary probability、同分母 market baseline、captured ask/depth 和 5/10-share 容量；缺真实 maker queue/fill、实际订单、组合相关性和 untouched frozen forward。

## 可复跑产物

- `scripts/analysis/forecast_quality/research_amsterdam_executable_market_null_v2.py`
- `docs/analysis/2026-08/generated/amsterdam_executable_market_null_v2/summary.json`
- `docs/analysis/2026-08/generated/amsterdam_executable_market_null_v2/d1_checkpoint_rows_{5,10}.csv.gz`
- `docs/analysis/2026-08/generated/amsterdam_executable_market_null_v2/full_ladder_rows_{5,10}.csv.gz`
- `docs/analysis/2026-08/generated/amsterdam_executable_market_null_v2/d1_daily_summary_{5,10}.csv`
- `tests/research_tests/test_amsterdam_executable_market_null_v2.py`
