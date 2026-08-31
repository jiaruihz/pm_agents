# Weather Edge Engine — PR2 开发日志

Status: `historical-development-log / not-current-state`
Current authority: `../WEATHER_DOCS_INDEX.md` and `../WEATHER_STRATEGY_REGISTRY.md`

> 关联设计：[WEATHER_CITY_BLEND_MODEL_IMPLEMENTATION_PLAN_2026-06-05.md](../WEATHER_CITY_BLEND_MODEL_IMPLEMENTATION_PLAN_2026-06-05.md) §4 Step 2 / §6 PR 2
> 范围：离线回放评估 + 周度 sklearn 重新校准脚本
> 当时状态：进行中（仅表示 2026-06-05 的开发截面）

## 0. PR2 范围

不改生产、不改 N100。只产出两个分析脚本和一份评估文档。

新增文件：

```text
scripts/analysis/city_selection/eval_city_day_basket.py
scripts/analysis/blender_shadow/recalibrate_blend.py
docs/analysis/city_selection.md
docs/dev_logs/2026-06-05-weather-edge-engine-pr2.md  ← 本文档
```

## 1. 数据源摸底（已完成）

`runtime/weather.db` 当前覆盖：

| 指标 | 值 |
|---|---|
| `fact_signal_candidates` 总行数 | 20,931 |
| `settlement_status='settled'` | 2,796 |
| 时间范围 | 2026-05-05 → 2026-06-05（31 天） |
| `eligible=1` | 6,597 |
| `paper_ordered=1` | 2,438 |
| `live_filled=1` | 211 |

### 1.1 关键字段映射

| 概念 | 字段 |
|---|---|
| `model_p_yes_raw` | `model_p_yes` |
| `market_implied_p_yes`（YES 端） | `market_yes_price` |
| YES ask（BUY_YES 入场） | `decision_entry_price`（side=BUY_YES 时） |
| NO ask（BUY_NO 入场） | `decision_entry_price`（side=BUY_NO 时） |
| 结算真值 | `final_yes`（bracket 维度，1.0 = 该 bracket 是赢家） |
| 分组键 | `(city, event_date, decision_snapshot_ts_utc)` |
| 黑/白名单 | `city`（与 blender config 对齐） |

### 1.2 已知缺口（写进评估报告）

1. **NO book = 1 − YES bid 近似**：当前 candidates 表没有独立 NO orderbook。BUY_NO 入场价是 `1 − yes_bid_proxy`，不是真实 NO ask。设计文档 §1.4 明确生产必须读独立 NO book；本 PR2 暂用 fallback，**评估文档显式标注「NO 入场价是 proxy」**。
2. **`decision_window_missing` ~44%**：决策时缺数据的 (city, target_date, bracket, side) 行的 `model_p_yes / market_yes_price / decision_entry_price` 均为 None。PR2 直接过滤这些行，只评估有完整决策数据的样本。
3. **每 (city, target_date, bracket) 只一个决策快照**：candidates 表已经聚合到 best-decision-snapshot 维度，没有逐 snapshot 历史。所以 basket 评估的「snapshot_ts」实际是 best-decision 时间点，不是逐分钟 replay。日内 forecast jump / side flip 的真实评估需要日志层面回放，留 PR3 shadow 双写时再做。
4. **`edge` 字段语义**：表里 `edge = model_p_yes - entry_price`，对 BUY_NO 是负数。脚本里必须重算 `edge_no = (1 - model_p_yes) - entry_no`。
5. **counterfactual_pnl / counterfactual_pnl_best 已经在表里**：可以作为 PR2 sanity check 对照（独立计算的 PnL 应当与表中已有 counterfactual 字段一致或可解释）。

## 2. 比较口径

三种规则同源同期 settled 候选上回放，给同一组 (city, event_date) 输入：

| 规则 | 信号概率 | 选腿方式 | sizing |
|---|---|---|---|
| **raw_single** | `model_p_yes`（旧生产） | 单腿独立 edge > 阈值 | 等额 $5 |
| **blended_single** | `0.3*raw + 0.7*market`（黑名单城市 0.1/0.9） | 单腿独立 edge > 阈值 | 等额 $5 |
| **basket** | 同 blended | `build_city_day_basket()` | basket 内 $3/$5 + cap $15 |

报告指标：
- fills、total_pnl_usd、roi、win_rate、worst_case_loss 分布
- **missed profit**：raw / blended-single 选了 + 赢了，basket 没选 → 错过的盈利
- **avoided loss**：raw / blended-single 选了 + 亏了，basket 没选 → 避开的亏损
- top-1 / top-5 移除后 ROI（尾部敏感性）

通过判据（设计文档 §4 Step 2 → Step 3）：
- basket 不能只靠减少交易数刷高 ROI。必须 missed_profit 显著小于 avoided_loss。
- top-5 移除后 basket ROI 仍 ≥ 0。

## 3. recalibrate_blend.py 口径

- 用 settled 候选去重到 (city, event_date, bracket)：每个 bracket 只算一次（YES 和 NO 行共享 `model_p_yes` 与 `market_yes_price`）。
- label = `final_yes`（0/1）。
- 计算 Brier：raw / market / blend at α ∈ {0.0, 0.1, ..., 1.0}。
- sklearn 重新校准 raw：`IsotonicRegression` + `LogisticRegression`（Platt）。
- 输出：best_alpha、Brier table、|Δα vs 0.30|、drift flag。

drift flag 阈值（与设计 §5、§9 一致）：
- `|best_alpha - 0.30| > 0.10` → flag = `alpha_drift`
- `Brier(best_alpha) < Brier(0.30) - 0.005`（>0.5% 绝对改进）→ flag = `brier_improvement_significant`

不自动改 config，只产 JSON 报告。

## 4. 开发顺序

1. ✅ 摸底数据源
2. ✅ 写 dev log
3. ✅ 写 `recalibrate_blend.py`
4. ✅ 跑 recalibrate（drift flags 全部触发）
5. ✅ 写 `eval_city_day_basket.py`
6. ✅ 跑 eval（1737 settled rows，三规则对比 + 归因）
7. ✅ 评估结论已在 2026-08-13 收口到 [city_selection living doc](../analysis/city_selection.md)
8. ✅ 收尾 dev log

## 5. 偏离设计文档的地方

- **basket 评估的「snapshot_ts」是聚合层**：candidates 表已经聚合到 best-decision snapshot 维度，所以 eval 的 basket 评估按 (city, target_date) 跑了一遍，不是逐 snapshot replay。设计文档 §2.3 是按 `(city, target_date, snapshot_ts)` 输入 basket builder。
  影响：eval 给出的 "basket 决策" 是 lower bound — 真正生产 N100 每 30 分钟一个 snapshot 都会跑一次 basket，决策更精细。本期 eval 把 basket 跑得偏保守。
  做法：在评估文档里显式标注；PR3 shadow 双写时 N100 自然会逐 snapshot 调用 basket。

- **eval 没有 forecast jump / side flip 字段输入**：candidates 表里没有 `forecast_jump_f` / `side_flip_count_today`。所以三规则比较里 basket 的稳定性门没生效（所有 candidate 默认稳定）。
  影响：basket 的 "shadow demotion" 行为本期未被评估。这部分要 PR3 shadow 双写后才能真实评估。

- **sklearn 的 isotonic / Platt 比 raw 还差**：drift report 显示 isotonic Brier=0.205、Platt 0.206，比 raw 0.223 好但比 market 0.167 差。这是预期之内——单调校准对一个本期已经严重欠拟合的模型无能为力。报告里照常输出，由人判断。

- **basket vs single-leg PnL 不在同一 sizing 基线上**：basket 用 $3/$5 mixed tier，single-leg 等额 $5。所以「basket 总 PnL 小」一部分来自 sizing 差。评估文档里把这点写明，并把「PR2b 上调 notional」作为下一步候选。

- **eval 用 `Path` import sys.path 自举**：脚本头部加了三行让 `python scripts/analysis/...` 可以直接跑，不需要 caller 设 `PYTHONPATH=.`。仓库其他脚本依赖 caller 设环境变量，这条偏离常规，但更友好。

## 6. 关键结论

evaluation doc 结论摘要：

- **Step 2 → Step 3 暂不通过**。basket 在 offline replay 上 missed_profit > avoided_loss。
- **blended_single 是当前最强规则**（ROI +15.3%）— 比 raw（+6.8%）显著好，说明 blender 本身有效。
- **basket BUY_NO 子段正向**（ROI +6.5%）— 与设计预期一致。
- **basket BUY_YES 当前亏钱**（−$55）— 49 腿 22% 胜率撑不住，候选调优方向：放宽 YES 端阈值。
- recalibration 最近 7 天 best α=0.00（纯 market），触发漂移告警，但工具按设计**不自动改 config**。

不退役旧策略、不开新 canary。

## 7. 下一步（PR2b 或 PR3）

PR2b（可选，先做 basket 调优）：
- 调大 single_leg_notional ($5/$8)
- 调小 edge_small_threshold (0.02)
- `prefer_no_over_yes=False` 试一轮
- 重跑 eval，验证 missed_profit ≤ avoided_loss

PR3（推荐并行）：
- N100 shadow 双写：raw / used / blend metadata / basket decision / 独立 NO book / forecast_jump / side_flip 字段
- 不下单。1 周后回放包含真实稳定性信号，能给 basket 一个更公平的评估。

## 8. 文件变更清单（PR2）

```text
A  scripts/analysis/blender_shadow/recalibrate_blend.py
A  scripts/analysis/city_selection/eval_city_day_basket.py
A  docs/analysis/city_selection.md（2026-08-13 吸收历史结论）
A  docs/dev_logs/2026-06-05-weather-edge-engine-pr2.md
```

原三份机器/渲染产物已由 JRS manifest `basket_blender_legacy_cleanup_20260813`
content-addressed 保存，并从工作树移除。

不修改现有文件。生产、N100、CI 行为不变。
