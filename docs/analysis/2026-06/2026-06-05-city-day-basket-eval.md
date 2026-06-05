# City-Day Basket Replay & Blend Recalibration — 2026-06-05

> 关联：[实施计划](../../WEATHER_CITY_BLEND_MODEL_IMPLEMENTATION_PLAN_2026-06-05.md) §4 Step 2
> PR2 dev log：[../../dev_logs/2026-06-05-weather-edge-engine-pr2.md](../../dev_logs/2026-06-05-weather-edge-engine-pr2.md)
> 数据：`runtime/weather.db` / `fact_signal_candidates`，settled=2,796 行，时间窗 2026-05-06 → 2026-06-01。
> 工具：`scripts/analysis/eval_city_day_basket.py`、`scripts/analysis/recalibrate_blend.py`

## 0. TL;DR

| 决策 | 结论 |
|---|---|
| Step 2 → Step 3 gate | **暂不通过**（offline replay 上 basket 未跑赢 blended single-leg） |
| 主要原因 | basket 规则 missed_profit ($3,007–$3,852) > avoided_loss ($2,170–$3,330) |
| 但 | basket win_rate 65% vs single 51%，shared_profit 与 single-leg 基本一致 |
| 行动 | 调 basket 参数（notional 上调、YES 端放宽）后重跑；同步推进 PR3 shadow 双写（不影响 live） |
| Recalibration | 最近 7 天 holdout 最优 alpha = **0.00**（纯 market），触发 `alpha_drift` + `brier_improvement_significant` |

不退役任何旧策略，不部署新策略。

---

## 1. 数据范围

```text
input rows:           1,737  (settled & decision_window_missing=0)
date range:           2026-05-06 → 2026-06-01
cities:               25+
brackets sampled:     ~1.7k 行（每行一个 city × target_date × bracket × side）
```

已知缺口：
- BUY_NO 入场价 `decision_entry_price` 是 `1 − yes_bid_proxy`，不是独立 NO book。
- 不含 intra-day forecast jump / side flip（candidates 表已聚合到 best-decision snapshot）。
- 未做 orderbook 深度约束。

---

## 2. 三规则口径

| 规则 | 概率源 | 选腿 | sizing |
|---|---|---|---|
| **raw_single** | `model_p_yes` | 单腿 edge > 0.03（side-aware） | 等额 $5 |
| **blended_single** | `0.3*raw + 0.7*market`（黑名单 0.1/0.9） | 单腿 edge > 0.03 | 等额 $5 |
| **basket** | 同 blended | `build_city_day_basket()` v1 默认参数 | $3/$5 + city-day cap $15 |

---

## 3. 总分结果

```text
                  n_legs  win_rate    cost     pnl      ROI
raw_single         1572     0.506    $7860   +$537    +6.84%
blended_single     1108     0.512    $5540   +$849   +15.33%
basket              317     0.650    $1265    +$16    +1.24%
```

尾部敏感性（去掉单笔/前 5 笔最大盈利后 ROI）：

```text
                  ROI excl top-1   ROI excl top-5
raw_single          +0.21%          -6.78%
blended_single      +5.93%          -2.92%
basket              -0.05%          -3.22%
```

按方向：

```text
raw_single:      BUY_YES  n=577  win=17.9%  pnl=+$459     BUY_NO  n=995  win=69.6%  pnl=+$78
blended_single:  BUY_YES  n=386  win=19.2%  pnl=+$754     BUY_NO  n=722  win=68.3%  pnl=+$95
basket:          BUY_YES  n= 49  win=22.4%  pnl= -$55     BUY_NO  n=268  win=72.8%  pnl=+$71
```

观察：

1. **blended_single 是当前最强规则**（ROI +15.3%，尾部去除后仍正）。这说明 v1 的 blender（0.30/0.70 + 4 城黑名单）确实把信号纯度提升了。
2. **raw_single 的 BUY_YES 几乎完全由低价彩票腿支撑**：win_rate 仅 17.9%，但平均赢一次 PnL 极大。同样形态在 blended_single 里减弱（少了 191 条不合算的 YES）。
3. **basket 大幅压缩交易量**：1572 → 317 条。Win_rate 提升到 65%，但绝对 PnL 只有 $16，远低于 blended_single 的 $849。
4. **basket BUY_YES 亏钱**（−$55）：49 条 YES 腿胜率 22%，单腿小 notional 撑不住低胜率的尾部。BUY_NO 部分仍正（+$71）。

---

## 4. 归因：basket 对比 single-leg

```text
basket vs raw_single:
  missed_profit  = +$3,852  (raw 选了&赢了，basket 没选)
  avoided_loss   = +$3,330  (raw 选了&亏了，basket 没选)
  shared_profit  =   +$565
  shared_loss    =   −$550
  net basket - raw =  −$522

basket vs blended_single:
  missed_profit  = +$3,007
  avoided_loss   = +$2,170
  shared_profit  =   +$547
  shared_loss    =   −$535
  net basket - blended = −$837
```

读法：

- **shared 行接近持平**：raw $565 / -$550, blended $547 / -$535。这说明 basket 抓住了 single-leg 的「日常」alpha。
- **missed_profit 总额由少数尾部大赢主导**（Miami 单城在 blended_single 里 +$577，basket 在 Miami 只拿到 +$11）。basket 按风险 sizing，没有 plunge 进低价 YES 彩票腿。
- 在「不要尾部支撑」的口径下（excl top-5），三个规则全部为负，说明当前样本期间没有任何规则是真正稳态正收益。

---

## 5. 城市切片摘要（top-10 |PnL|）

basket（保守，体量小）:

```text
  Warsaw      +$28  ROI +47.9%  win 0.73
  Amsterdam   +$14  ROI +45.4%  win 0.88
  London      +$12  ROI +12.1%  win 0.67
  Chongqing   −$12  ROI −100%   win 0.00   ← 3 legs 全输
  NYC         −$12  ROI −16.4%  win 0.59
  Chicago     −$11  ROI −22.6%  win 0.55
  Miami       +$11  ROI +16.8%  win 0.80
  Dallas      −$10  ROI −55.9%  win 0.25
  Madrid       +$9  ROI +19.4%  win 0.75
  Beijing      −$8  ROI −61.5%  win 0.33    ← 黑名单生效但仍亏
```

blended_single:

```text
  Miami      +$577  ROI +268%  win 0.56   ← 由若干低价 YES 彩票腿主导
  Singapore  +$159  ROI +159%
  Madrid     +$120  ROI +86%
  Guangzhou  +$120  ROI +160%
  Paris       −$76  ROI −35%   ← 可能候选降级
  Seoul       −$74  ROI −30%   ← 可能候选降级
```

---

## 6. Step 2 → Step 3 通过判据复核

设计文档 §4 Step 2 验收：

> - basket 规则不能只靠减少交易数提高 ROI，必须报告 missed profit 和 avoided loss。 ✅ 已报告
> - 若 orderbook 缺口导致结论不稳，显式列出缺口，不允许静默 fallback。 ✅ 已列出（§1）

复盘判据：

| 判据 | 结论 |
|---|---|
| basket missed_profit ≤ avoided_loss | ❌ 不满足（$3,007 > $2,170 vs blended） |
| basket ROI ≥ blended_single × 0.8 | ❌ 不满足（1.24% vs 15.33%） |
| basket 尾部去除后仍正 | ❌ 全部规则去尾后转负 |
| BUY_NO basket 30d ROI ≥ 0 | ✅ basket BUY_NO ROI +6.5%（$71/$1,092） |

**结论：暂不进 Step 3 canary 部署**。需要回到 basket 参数调优。

---

## 7. Basket 参数候选调整方向

下面的方向准备进 PR2b（也可以并入 PR3 shadow 一起观察）：

1. **single_leg_notional**：当前 $3/$5，相对 single-leg 等额 $5 显得吃亏。试 $5/$8 看体量能否撑起绝对 PnL。
2. **`prefer_no_over_yes` 放开**：basket BUY_YES 只 49 腿、亏钱；改成「按 EV 排序」可能让 Miami 这类城市的低价 YES 进入。
3. **edge thresholds**：当前 0.03/0.06。blended_single 的 BUY_YES 部分赢钱主要靠低价高赔率，调低 YES 端的阈值（或对低价 YES 用独立阈值）。
4. **possible_final_temps 真实化**：当前 eval 用 candidate 自带 bracket 集作为 final temp 池，缺失「比这些 bracket 都更低/更高」的可能。引入 `pm_history` 已知的实际 bucket 边界后，EV 估计会更准。
5. **黑名单复盘**：Paris、Seoul 在 blended_single 里持续亏，下一轮可能要进黑名单/重权（当前只 Milan/Lucknow/Austin/Beijing）。Beijing 在黑名单里仍亏 $8，说明 0.10 还不够保守。

---

## 8. Recalibration 结果（recalibrate_blend.py）

最近 7 天 holdout（2026-05-26 → 2026-06-01）：

```text
n_train   = 1,153    train window 2026-05-06 → 2026-05-25
n_holdout =   415    holdout window 2026-05-26 → 2026-06-01

Brier(raw)            = 0.22294
Brier(market)         = 0.16656
Brier(deployed α=0.30) = 0.17399
Brier(best α=0.00)    = 0.16656
Brier(isotonic)       = 0.20511
Brier(Platt)          = 0.20551

DRIFT FLAGS: alpha_drift, brier_improvement_significant
```

读法：

- 全窗口（前份分析 2026-06-05-probability-calibration.md）里 α=0.30 是最优。
- 但**最近 7 天里 raw 模型显著退化**：raw Brier 0.223 vs market 0.167。
- isotonic / Platt 对 raw 单独做单调校准也救不回（0.205）→ 表明问题不在校准，是 raw 模型当前期间预测力下滑。
- 工具按设计 §5 触发漂移告警。**不自动改 config**，由人判断。

**短期处置**：
- 不调整生产 α（仍是 0.30）。这只是单 7 天窗口告警，可能下周回弹。
- 若 4 周内 best_alpha 持续 < 0.20，再考虑下调 global α。
- 同时记入 PR3 shadow 双写时增加「raw model Brier 滚动 7 天」字段。

---

## 9. 复现

```bash
cd /home/rui/projects/pm_agent

# 重新校准
.venv/bin/python scripts/analysis/recalibrate_blend.py
# → JSON 输出在 docs/analysis/<YYYY-MM>/<YYYY-MM-DD>-recalibrate-blend.json

# 离线回放
.venv/bin/python scripts/analysis/eval_city_day_basket.py
# → JSON 输出在 docs/analysis/<YYYY-MM>/<YYYY-MM-DD>-city-day-basket-eval.json

# 自定义阈值/notional
.venv/bin/python scripts/analysis/eval_city_day_basket.py \
    --edge-threshold 0.02 --leg-notional 8
```

---

## 10. 下一步

1. **PR2b（推荐）**：basket 参数调优（§7 列表），重跑 eval，把 missed_profit 控制到 ≤ avoided_loss。
2. **PR3（与 PR2b 并行）**：N100 shadow 双写。即便 basket 当前不达通过判据，shadow 双写本身不开 live，可以收集独立 NO book、真实 intra-day jump/flip 等当前 eval 缺失的字段，下一轮回放会更准。
3. **不变更生产**：三个旧策略实例继续 live；不开 weather_edge_engine canary。
