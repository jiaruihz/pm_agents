# Blender Research State And Next Plan — 2026-06-08

## 结论先行

`blender` 当前不应作为 live 硬 gate 直接替换原策略。它的有效定位是：

1. **shadow/paper 数据链路**：把 `raw / market / blended` 三套概率和 edge 写进 lineage，积累逐 snapshot 证据。
2. **二级风险信号**：当 raw 模型过度偏离市场时，blend 可以提示降 size 或进入人工/策略复核。
3. **不是独立 alpha**：在剔除近期弱 ECMWF 城市并 ban `T>28` 后，blender 对新 base 的边际收益为负。

当前更确定的收益来源不是 blender，而是：

- 移除近期弱 ECMWF 城市：`BuenosAires / Munich / Jeddah / Karachi / Moscow / Ankara`
- 禁止过早下单：`hours_to_settle > 28`
- 保留城市/时间层风控后，再研究是否用 blend 做降 size 或 shadow 排序

## 当前策略身份

### Shadow/paper 候选

```text
strategy_id = weather_edge_engine_blended_filter_v0
strategy_family = weather_edge_engine
source_strategy_instance = mid_price_core_v1_25_75
decision_mode = single_leg_filter_shadow
execution_mode = shadow/paper
```

代码位置：

```text
weather_dashboard/blend/blender.py
weather_dashboard/blend/city_blend_config.json
weather_dashboard/strategy_specs/weather_edge_engine_blended_single_v0.json
scripts/ops/weather_blended_shadow_paper.py
scripts/ops/weather_blended_shadow_paper_loop.sh
scripts/ops/start_weather_blended_shadow_paper_loop.sh
```

研究脚本：

```text
scripts/analysis/backtest_weather_edge_engine_blended_single.py
scripts/analysis/backtest_weather_edge_engine_blended_entry_bands.py
scripts/analysis/backtest_blended_paper_fill_estimate.py
scripts/analysis/weather_blended_live_instance_overlay.py
scripts/analysis/research_v1_raw_regime_filter_walkforward.py
scripts/analysis/research_v1_removed_ecmwf_t28_blender_overlay.py
```

核心公式：

```text
p_yes_used = alpha * model_p_yes_raw + (1 - alpha) * market_implied_p_yes
```

当前默认：

```text
alpha = 0.30
beta = 0.70
raw_model_blacklist = Milan / Lucknow / Austin / Beijing
blacklist alpha = 0.10
```

例子：

```text
raw = 30%
market = 20%
p_yes_used = 0.3 * 30% + 0.7 * 20% = 23%
```

这会把概率向市场收缩，因此 edge 通常变小。它不是为了扩大交易面，而是为了约束 raw 模型过度自信。

## 已完成研究

### 1. 机会集回测：blend 有信号，但不能直接上线

报告：

```text
docs/analysis/2026-06/2026-06-06-blended-single-v0-backtest.md
docs/analysis/2026-06/2026-06-06-blended-entry-band-backtest.md
docs/analysis/2026-06/2026-06-06-blended-paper-fill-estimate.md
```

早期机会集结论：

| slice | raw_single ROI | blended_single ROI | 读法 |
|---|---:|---:|---|
| full | +6.48% | +14.48% | 全样本改善 |
| holdout_from_2026_05_26 | -10.76% | -5.91% | 仍亏，只是少亏 |
| recent_from_2026_06_01 | +1.54% | +4.27% | 样本薄 |
| live_filled_only | +2.52% | +5.56% | 机会子集，不是钱包 PnL |

问题：

- 2026-06-06 旧报告中部分 settled 口径受 near-binary 勘误影响，不能单独作为最终 live 决策。
- 即使 headline ROI 改善，holdout/recent 不够稳。
- `live_filled_only` 是反事实机会集，不等于真实可成交策略表现。

### 2. Live fill overlay：blend 更像近期漂移过滤器

报告：

```text
docs/analysis/2026-06/2026-06-07-blended-live-instance-overlay.md
docs/analysis/2026-06/2026-06-07-v1-raw-regime-filter-walkforward.md
```

对原 `mid_price_core_v1_25_75` settled `live_real` fills 做控制变量 overlay：

```text
成交价固定
size 固定
结算结果固定
只改变 gate 是否保留这笔 fill
```

关键结论：

| gate | pre delta | post delta | 读法 |
|---|---:|---:|---|
| `blended_edge_ge_0.10` | -$67.63 | +$86.56 | 6 月后有帮助，但 6 月前伤害收益 |
| `market_confirm_or_raw_edge_gt_0.25` | -$39.44 | +$96.08 | 高 raw edge 例外更温和 |
| `pre_profitable_cities_only` | +$104.56 | +$117.08 | 城市层风控更强 |
| `exclude_pre_weak_cities` | +$84.25 | +$106.17 | 城市层风控更强 |

解释：

- blender 的收益主要来自过滤“raw 模型激进、市场不确认”的单。
- 但历史上它也误杀过盈利单。
- 更强的非后验信号是城市/数据源 regime，而不是全局 blended threshold。

### 3. 剔除弱 ECMWF 城市 + T28 后：blender 边际变弱

报告：

```text
docs/analysis/2026-06/2026-06-08-v1-removed-ecmwf-t28-blender-overlay.md
```

口径：

- 当前 DB：`runtime/weather.db`
- CLOB coverage gate：`gate_pass=true`
- 样本：`mid_price_core_v1_25_75` settled `live_real`
- target_date：`2026-05-16 -> 2026-06-05`
- post：`target_date >= 2026-06-01`

控制变量结果：

| 规则 | post fills | post PnL | 相对原始 delta |
|---|---:|---:|---:|
| 原 v1_25_75 | 277 | -$89.06 | $0.00 |
| 只剔除 6 个 ECMWF 城市 | 182 | +$10.08 | +$99.14 |
| 只 ban `T>28` | 111 | +$3.51 | +$92.57 |
| 剔除 6 城 + `T<=28` | 82 | +$39.53 | +$128.58 |
| 新 base + `blended_edge>=0.10` | 23 | +$20.19 | +$109.25 |
| 新 base + `blended 或 raw_edge>0.25` | 31 | +$22.24 | +$111.30 |

在新 base 内的边际增量：

| 规则 | post kept PnL | 相对新 base delta |
|---|---:|---:|
| 剔除 6 城 + `T<=28` | +$39.53 | $0.00 |
| 再加 `blended_edge>=0.10` | +$20.19 | -$19.33 |
| 再加 `blended 或 raw_edge>0.25` | +$22.24 | -$17.28 |

交易解释：

- 6 月后的主要亏损已经被“城市 + 时间”规则解释掉。
- 在这个新 base 上，blender 继续硬过滤会错过更多净盈利。
- 因此 blender 不应作为当前 live 硬 gate；更适合作为降 size、排序、shadow 记录或 drift 警报。

## 当前工程改动

已经具备：

- `weather_dashboard/blend/`：概率 blend 纯函数与配置。
- `weather_dashboard/strategy_specs/weather_edge_engine_blended_single_v0.json`：策略身份说明。
- `scripts/ops/weather_blended_shadow_paper.py`：本机 shadow/paper runner。
- `scripts/ops/weather_blended_shadow_paper_loop.sh` / `start_weather_blended_shadow_paper_loop.sh`：循环入口。
- 多个 analysis 脚本：回测、paper fill 估算、live fill overlay、regime 研究。
- `blended_25_75_e05` / `blended_filter_25_75_v0` 的默认 shadow 城市池已对齐当前 operational base：移除 `Ankara / Jeddah / Karachi / Moscow / Munich`；`BuenosAires` 原本不在该 shadow T1 profile 中。
- shadow/paper runner 默认时间窗为 `22 <= hours_to_settle <= 28`，与 `T>28` ban 对齐。

未改变：

- 未改 N100 production live 下单配置。
- 未退役原策略。
- 未把 blender 作为真实 live gate。
- 未把 basket optimizer 推进 canary。

## 下一步研究策略

### 方向 A：先让新 live base 稳定跑

目标：

```text
验证“剔除 6 个弱 ECMWF 城市 + T<=28”是不是前瞻有效，而不是后验修补。
```

观察指标：

- `live_real` settled PnL / ROI，按 `target_date`、`fill_date_bj` 都拆。
- missed opportunity：被移除城市是否继续亏，还是后续反弹。
- `T>28` 被 ban 的机会是否继续净亏，特别是 Warsaw/Tokyo/Madrid 这种历史上被 T28 ban 误杀过盈利的城市。
- per-city hit rate、side split、model_version split。

验收门槛：

```text
至少 7 天 shadow/paper + live observation
post-change avoided_loss >= missed_profit
日期中位 delta 非负
不是单一城市或单一日期贡献全部收益
```

### 方向 B：blender 从 hard gate 改成 size/risk signal

当前不推荐：

```text
if blended_edge < 0.10: skip
```

更合理的候选：

```text
if operational_base_pass:
    size = base_size
    if blended_edge < 0.00:
        size = 0
    elif blended_edge < 0.05:
        size = 0.25 * base_size
    elif blended_edge < 0.10 and raw_edge <= 0.25:
        size = 0.5 * base_size
    else:
        size = base_size
```

研究重点：

- `blended_edge` 用于 sizing 是否比 skip 更稳。
- `raw_edge > 0.25` 的例外是否保留真实 alpha，还是保留 tail risk。
- 按城市校准 alpha，而不是全局 `0.30/0.70`。
- 逐 snapshot 记录 forecast jump / side flip 后，再决定是否引入动态 alpha。

### 方向 C：继续研究 raw 模型 6 月后失效

已知线索：

- 弱城市集中在近期 ECMWF 城市。
- `T>28` 过早信号有明显拖累。
- raw-normalized city-day 分布在 holdout/recent 变差。
- market-normalized 分布更稳。

下一步：

- 按 `city x model_version x hours_to_settle x side` 拆 raw calibration。
- 对每个城市估计 rolling Brier/logloss/drift。
- 检查 forecast 更新源、forecast jump、snapshot side flip。
- 区分“模型错”和“市场已经提前反映”的情况。

### 方向 D：basket 研究继续，但不和 blender 硬绑

basket 当前应独立研究：

- city-day 组合 objective
- market-normalized temperature distribution
- legacy per-bucket baseline
- top-k sensitivity
- walk-forward 选择器

blend 在 basket 里最多先作为候选特征，不应直接作为 optimizer 的主概率目标。

## 当前推荐动作

短期：

```text
1. live 保持用户已做的城市移除和 T28 ban。
2. blender 只跑 shadow/paper，不进入 live hard gate。
3. 新增报告默认用“相对原始”和“相对新 base”两个 delta，避免误读。
```

中期：

```text
1. 把 blend 字段双写进 lineage。
2. 设计 blended sizing overlay，而不是 blended skip gate。
3. 7 天后用前瞻数据复核新 base 和 blended sizing。
```

长期：

```text
1. 按城市/模型/lead-time 做 raw calibration。
2. 用 market-normalized 分布继续 basket optimizer。
3. 只有 walk-forward 和 live shadow 同时通过，才考虑 canary。
```
