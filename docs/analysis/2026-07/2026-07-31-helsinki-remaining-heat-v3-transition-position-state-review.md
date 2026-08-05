# Helsinki remaining-heat v3 峰值转换与通用持仓状态审阅

## 结论

- `市场整体概率更准` 与 `模型交易 ROI 为正` 不矛盾：前者在全体同盘口 checkpoint 上按
  target date 等权评分，后者只累加模型挑出的少量分歧交易并按现金投入计权。
- 峰值转换 challenger 的方向正确、点估小幅改善，但 date-block CI 跨 0，不能替换 frozen v2。
- 通用 city-day 持仓状态机已实现并完成研究回放；它不决定 signal，只管理目标仓位、去重、
  加仓、锁盈、结算和风险状态。本轮没有增加 edge 阈值、没有接 live、没有真实订单。

## 为什么市场分数更好，策略仍能赚钱

全体同 rows 534 checkpoint 上：

| probability | Brier | logloss |
|---|---:|---:|
| v2 weather | 0.08975 | 0.27035 |
| market mid | 0.06252 | 0.21789 |

所以，若问题是“随机抽一个有盘口的时刻，谁的概率更可信”，答案是市场。

策略回答的是另一个问题：只在 `weather p_break > executable X-NO cost` 时买。35 笔里有
34 笔同时存在 two-sided market mid，这 34 笔全部是模型概率高于市场：

| selected 34 trades | model | market mid | executable cost |
|---|---:|---:|---:|
| mean probability/cost | 0.7879 | 0.6202 | 0.6530 |
| trade-weighted Brier | 0.0984 | 0.1138 | 0.1058 |
| target-date-weighted Brier | 0.1474 | 0.1107 | 0.1077 |
| target-date-weighted logloss | 0.4443 | 0.3453 | 0.3342 |

这说明：

1. 在重复交易较多的那些好日期上，模型选出的分歧点确实碰巧优于市场，所以 trade-weighted
   Brier 和现金 ROI 可以为正。
2. 每个日期等权后结论反转，模型仍输市场；单笔坏日期不能被同一好日期的多笔交易稀释。
3. ROI 还受买价影响。成本 0.8–1.0 的 17 笔全部赢，贡献 `$4.05`；成本 0.2–0.4
   的 7 笔贡献 `$9.05`。不同于 proper score，ROI 是 payoff/cash 加权。

因此当前正 ROI 是值得继续跟踪的 selected residual，不是“模型整体已经比市场聪明”的证明。

## 峰值转换 v3

### 结构修正

旧 market replay 的 `minutes_to_future_peak` 范围实际为 0–730 分钟；它没有负值，不能严谨地
称作“峰值前后 ±60m”。v3 改用 signed day-peak clock，并加入连续机制特征：

- 距离日峰值还有多久、峰值已过去多久；
- 剩余 future peak 相对已发生 day peak 的折损；
- 当前 settlement boundary 的首次跨越和最后维持时间；
- 未来 1h/2h 升温斜率、正斜率时长和 reheat strength；
- future heat 的真实时间积分、boundary 上方持续时间和峰后回落。

exact forecast 缺失时显式沿用 frozen v2 概率，不让一组全 missing 的 transition features
静默改变模型。

### 固定分母结果

2025 expanding OOF 固定为 51,451 checkpoints / 365 target dates：

| model | Brier | logloss | AUC | accuracy |
|---|---:|---:|---:|---:|
| frozen v2 | 0.051368 | 0.171644 | 0.982545 | 93.08% |
| transition HGB | 0.051091 | 0.170976 | 0.982709 | 93.03% |
| transition where available, else v2 | **0.051068** | **0.170877** | **0.982730** | 93.04% |
| transition logistic | 0.058149 | 0.191104 | 0.979203 | 91.98% |

相对 v2 的 target-date bootstrap：

| metric | delta | 95% CI |
|---|---:|---:|
| Brier | -0.000301 | [-0.001231, +0.000654] |
| logloss | -0.000766 | [-0.003240, +0.002024] |

正确 signed `±60m` day-peak 切片有 4,059 rows / 335 dates：

| metric | v2 | transition HGB |
|---|---:|---:|
| Brier | 0.130239 | 0.129146 |
| logloss | 0.408958 | 0.405628 |

点估均改善，但总体和 peak slice 的改善很小，CI 没有通过；transition logistic 明显更差。
所以 v3 artifact 只冻结为 challenger，primary 仍是 v2。

## 通用 city-day 持仓状态机

状态机放在纯能力层，不读取盘口、不算模型、不下单。上游传入 `eligible` 和目标 shares 后，它负责：

```text
NOOP -> OPEN -> HOLD / ADD -> LOCKED_WIN / LOCKED_LOSS -> SETTLED
```

同一 signal id 幂等；同一 instrument 按目标 inventory 只补增量；city-day cash budget 是可选风险边界，
不是 signal threshold。

Helsinki adapter 沿用原来的唯一规则 `edge > 0`，没有增加价格带或 edge 门。560 个有 5-share
direct-X-NO 深度的 checkpoint 重放结果：

| action | count |
|---|---:|
| OPEN | 35 |
| HOLD | 352 |
| NOOP | 173 |

- 35 个 `(target_date, current X)` 与旧 primary 完全一致；
- cost `$111.0153`、payout `$125`、PnL `+$13.9847`、ROI `+12.60%`，数值误差 `<1e-12`；
- 最大 city-day 在险 cash `$4.9905`；最大已部署 cash `$21.0512`。旧档 NO 在温度跨过该档后
  会成为 locked win，因此已部署资金与仍在险资金不应混报。

这是能力和账本语义升级，不是新 alpha，也没有改变历史策略收益。

## 双漏斗

### Signal funnel

| step | unit | rows | dates |
|---|---|---:|---:|
| weather fixed OOF | checkpoint | 51,451 | 365 |
| market-window scoreable FMI | checkpoint | 2,115 | 15 |
| state-machine executable intents | checkpoint | 560 | 14 |
| upstream first-positive opens | date-X position | 35 | 14 |

晚上或已无升温的 checkpoint 仍在 2,115 signal 分母，没有用峰值状态过滤。

### Evidence funnel

| step | unit | rows | dates |
|---|---|---:|---:|
| actual FMI first-seen | checkpoint | 1,268 | 14 |
| prior full ladder ≤25m | checkpoint | 693 | 14 |
| same-row direct NO midpoint | checkpoint | 534 | 14 |
| 5-share executable expression | checkpoint | 560 | 14 |
| actual fill | fill | 0 | 0 |

`2026-07-28` 仍是 PIT first-seen gap，不是策略过滤。

## 资格与失败项

- peak-transition v3：`FAIL to replace v2`，原因是 Brier/logloss CI 跨 0。
- selected-trade ROI：仍为 exploratory；14 个 PIT 日期、35 positions，ROI CI 跨 0。
- same-row market baseline：整体仍优于 weather v2。
- actual fill：0，不能推断 queue、latency 或 adverse selection。
- 2026 final audit 未读；v2/v3 train end 都是 2025-12-31；2026-07-31+ forward untouched。

动作：保留 v2 primary、v3 challenger 和通用状态机；继续 zero-notional collector/shadow，不改 live。

## 产物

- v3：`scripts/analysis/reheat_risk/research_helsinki_remaining_heat_probability_v3.py`
- state replay：`scripts/analysis/reheat_risk/research_helsinki_position_state_machine_v1.py`
- generic state：`src/strategies/weather_edge_v1/tools/city_day_position_state.py`
- v3 generated：`docs/analysis/2026-07/generated/helsinki_remaining_heat_probability_v3/`
- state generated：`docs/analysis/2026-07/generated/helsinki_position_state_machine_v1/`
