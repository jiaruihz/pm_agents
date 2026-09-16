# Tmax Distribution Research Synthesis v1

> generated_at: 2026-07-03  
> Scope: P0-P5 research synthesis; no live runner/order behavior changed.

## 一句话主线

现在这条线不再是“识别某个 regime 然后固定买某一档”，而是：

**用盘口 + 天气路径 + bracket 边界 + regime/context 估计 Tmax 最终落在哪个 bucket，然后只在模型胜率相对真实 ask 有正 edge 时选择表达。**

表达不是固定 current-NO。每个 city-date-hour 可以在四个表达里选一个：

- `current YES`
- `current NO`
- `d1 NO`
- `d2 NO`

选择标准是：

```text
model_edge = P(expression wins | current state) - ask
```

这就是当前主线的策略本体。

## 为什么要从 regime-route 改成 distribution-first

旧思路的问题是：  
`day_open_runway / day_marginal_runway / day_forecast_capped` 这类 regime 只是在描述天气形态，但交易真正结算的是 **Tmax 最后落在哪个 bracket**。

所以 regime 不能直接等于买/不买。更合理的位置是：

```text
regime/context = 概率模型特征
ask/expression = 执行层选择
settlement bucket = 训练/验证 label
```

这能避免两个老问题：

1. 看起来“天气形态对”，但买错 bracket 或买贵了。
2. 为了修坏 case 不断加 hard gate，最后样本被切碎。

## P0-P5 做了什么

| 阶段 | 问题 | 结论 |
|---|---|---|
| P0 | 市场、forecast anchor、running-max anchor 谁更像真实分布？ | 朴素 forecast/running anchor 明显不如 market；不能靠单锚替代盘口。 |
| P1 | market + weather/path/regime fusion 能不能赢 market proper scoring？ | 能，在 6/21+ forward 上 logloss 优于 market，但仍是短 forward。 |
| P2 | 概率优势接到真实 ask 后还有没有 EV？ | 有正 EV 迹象；但只有 6 天，且未完整处理 depth/fill/sizing。 |
| P3 | 到底是什么特征贡献了效果？ | `boundary/path + regime/context` 更干净；纯 city/source 容易噪声，不能单独当 alpha。 |
| P4 | 6/27+ 没完整 settlement，能不能用 observed max 做压力测试？ | 可以作为 research-only 压力测试；方向没坏，但不能当正式 PnL。 |
| P5 | 如果按 walk-forward 执行 replay，真实 ask 下是否稳定？ | verified 很好，extension 变薄；结论仍是 `inconclusive_positive_signal`，不 live。 |
| P6 | 怎么把 P5 输出变成可长期验证的 shadow 事件？ | 已定义 zero-notional telemetry contract，selected/blocked 都记录；不下单。 |

## 当前核心结果

### Verified Forward: 2026-06-21..2026-06-26

这是 official settlement 口径。

| 版本 | rows | ROI | CI | 备注 |
|---|---:|---:|---:|---|
| `loo_no_city_source / mkt_regime`, edge>=0.02 | 446 | +11.5% | [+0.9%, +21.7%] | 机制更干净，少用 city/source。 |
| `mkt_city_source`, edge>=0.02 | 561 | +11.2% | [+5.3%, +17.1%] | 行数更多，6 天日块全正，但 city/source 可能更噪。 |
| `loo_no_city_source / mkt_regime`, edge>=0.10 | 130 | +36.4% | [+9.0%, +57.8%] | dev-CV 也偏好高 edge，但样本更少。 |

### Extension Pressure Test: 2026-06-27..2026-06-29

这是 observed-max-derived label，不是 canonical settlement。

| 版本 | rows | ROI | CI | 备注 |
|---|---:|---:|---:|---|
| `mkt_city_source`, edge>=0.02 | 155 | +8.4% | [-15.4%, +36.1%] | 点估仍正，但 CI 宽。 |
| `loo_no_city_source / mkt_regime`, edge>=0.02 | 135 | +3.4% | [-44.5%, +51.3%] | 方向没坏，但不稳定。 |
| `loo_no_city_source / mkt_regime`, edge>=0.10 | 63 | +2.8% | [-100.0%, +128.1%] | 高 edge 在 recent extension 明显变薄。 |

## 现在该怎么理解这个策略

### 不是

```text
看到 runway -> 买 current NO
看到 capped -> 买 d2 NO
看到 pullback -> 买 current YES
```

这类 rule 太容易变成 case-by-case gate。

### 而是

```text
1. 盘口给出 local distribution baseline
2. 天气路径 / boundary / meteo / regime 修正这个分布
3. 模型输出 P(current), P(d1), P(d2), P(tail)
4. 对每个可买表达计算 P(win) - ask
5. 同一 city-date-hour 只选最高 edge 表达
6. 先 zero-notional shadow，等 official settlement / depth / fill 通过后再讨论 live
```

## 已经比较清楚的机制

### 1. Regime 有用，但不是交易规则

`day_regime / intraday_state / running_max_state / solar_window / wind_regime` 等字段适合作为概率模型输入。  
它们帮助模型判断最终 Tmax 分布，但不能直接写成固定买哪一档。

### 2. Bracket boundary 很关键

同样是 84F，靠近 bracket 上沿还是下沿，对打穿概率完全不同。  
P3 里 boundary/fractional-position 是核心机制层之一。

### 3. City/source 有信息，但容易噪

`mkt_city_source` 在 verified forward 很好，extension 也点估最好；但它可能吃到短期城市/source 记忆。  
所以当前更稳妥的表达是并行看：

- `mkt_city_source`：更强点估、更多交易
- `loo_no_city_source / mkt_regime`：更干净机制、少一点城市记忆

### 4. 高 edge 排序有信号，但不能直接当 live 参数

dev-CV 最喜欢 `edge>=0.10`，verified forward 也强。  
但 6/27-6/29 extension 变薄，说明 recent regime / label / 执行压力还没过。

## 当前结论等级

```text
significance = PARTIAL / THIN
baseline = PARTIAL_PASS
forward = FAIL / THIN
conclusion = inconclusive_positive_signal
live_action = no
shadow_telemetry = contract_ready
```

人话：

**这条线值得继续，而且比原来 regime-routed current-NO 更像一个正经量化策略；但现在还没到能实盘扩大的程度。**

## 下一步

## P6 已完成：zero-notional shadow telemetry

P6 把 P5 的表达选择结果整理成未来 shadow runner 应该写出的事件格式。  
核心原则是：**selected 和 blocked 都要记**。

这避免以后只能看到“触发了什么”，却看不到“为什么没触发，以及没触发是否反而更好”。

当前 backfill 结果：

| config | role | verified selected | verified ROI | extension selected | extension ROI |
|---|---|---:|---:|---:|---:|
| `tmax_dist_clean_edge02` | clean mechanism primary | 446 | +11.5% | 135 | +3.4% |
| `tmax_dist_city_source_edge02` | capacity / city-source comparison | 561 | +11.2% | 155 | +8.4% |
| `tmax_dist_clean_edge10` | high-edge pressure test | 130 | +36.4% | 63 | +2.8% |

P6 event row 记录：

- model method
- chosen expression
- ask
- model edge
- selected / blocked status
- selected / blocked reason
- city/source/regime/context
- later settlement

产物：

- P6 report: `docs/analysis/2026-07/2026-07-03-tmax-distribution-p6-shadow-telemetry-v1.md`
- P6 script: `scripts/analysis/reheat_risk/research_tmax_distribution_p6_shadow_telemetry_v1.py`
- Shadow events: `docs/analysis/2026-07/generated/tmax_distribution_p6_shadow_telemetry_v1/shadow_events.csv`

## 下一步

### P7: 接真实 zero-notional shadow runner

现在 P6 只是从 P5 historical opportunities backfill 出 telemetry。  
下一步是让实时/准实时 runner 每轮写同样 schema：

```text
state snapshot -> model probabilities -> best expression -> selected/blocked shadow event
```

仍然不下单。

### P8: official settlement / depth replay

等 6/27+ official settlement 和 orderbook depth 补齐后重跑：

- verified-only P5
- observed-derived vs official label diff
- depth-aware fill feasibility
- daily drawdown / capacity

### P9: bad-day model error attribution

重点拆：

- 6/28 extension 为什么拖累
- current YES 为什么在 extension 里明显差
- 高 edge 为什么 verified 强、extension 变薄
- 是 label/source 问题，还是 recent weather regime shift，还是模型过度自信

## 给外部审阅的重点问题

如果让 Fable 审，应该让它审实验设计，而不是重新发散找策略：

```text
请审阅 P0-P5 Tmax distribution-first 研究链路，重点检查：
1. 是否存在 label leakage / selection bias / denominator drift；
2. P4 observed-max-derived label 作为 6/27+ 压力测试是否边界清楚；
3. P5 dev-CV threshold ranking 到 verified/extension 的对比是否足以说明过拟合风险；
4. 当前从 proper scoring 到 executable EV 的转换是否有遗漏的 execution/microstructure 假设；
5. P6 zero-notional shadow telemetry 应该记录哪些字段，才能最小成本验证 live-readiness。

不要给 live 建议；只指出实验设计漏洞、过拟合风险和下一步最小充分验证。
```

## Artifact Map

- P3: `docs/analysis/2026-07/2026-07-02-tmax-distribution-p3-feature-ablation-v1.md`
- P4: `docs/analysis/2026-07/2026-07-03-tmax-distribution-p4-observed-label-extension-v1.md`
- P5: `docs/analysis/2026-07/2026-07-03-tmax-distribution-p5-walk-forward-execution-replay-v1.md`
- P5 script: `scripts/analysis/reheat_risk/research_tmax_distribution_p5_walk_forward_execution_replay_v1.py`
- P6: `docs/analysis/2026-07/2026-07-03-tmax-distribution-p6-shadow-telemetry-v1.md`
- P6 script: `scripts/analysis/reheat_risk/research_tmax_distribution_p6_shadow_telemetry_v1.py`
