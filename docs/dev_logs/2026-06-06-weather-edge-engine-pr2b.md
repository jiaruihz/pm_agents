# Weather Edge Engine — PR2b 开发日志

> 关联设计：[WEATHER_CITY_BLEND_MODEL_IMPLEMENTATION_PLAN_2026-06-05.md](../WEATHER_CITY_BLEND_MODEL_IMPLEMENTATION_PLAN_2026-06-05.md) §4 Step 2  
> 范围：basket 参数调优与离线 gate 复核  
> 状态：完成；不改 N100、不改生产配置、不开 canary

## 0. 背景

PR2 的问题不是 blend，而是 basket v1 太保守：

- `prefer_no_over_yes=True`
- `max_no_legs_per_city_day=3`
- `single_leg_notional_small/normal=$3/$5`
- `edge_small/normal=0.03/0.06`

PR2 原结论是 basket `missed_profit > avoided_loss`，不能进 Step 3。

## 1. 本次代码改动

新增 / 修改：

```text
M scripts/analysis/eval_city_day_basket.py
A scripts/analysis/tune_city_day_basket.py
A docs/analysis/2026-06/2026-06-06-city-day-basket-pr2b-sweep.json
A docs/analysis/2026-06/2026-06-06-city-day-basket-pr2b-sweep.md
A docs/analysis/2026-06/2026-06-06-city-day-basket-eval.json
```

`eval_city_day_basket.py` 新增 basket 参数 CLI 覆盖：

```text
--basket-small-notional
--basket-normal-notional
--basket-city-day-cap
--basket-max-no-legs
--basket-edge-small-threshold
--basket-edge-normal-threshold
--basket-prefer-no-over-yes / --basket-no-prefer-no-over-yes
```

这是必要修正：PR2 文档里写的“调 basket 参数”不能只靠原来的 `--leg-notional`
和 `--edge-threshold`，因为那两个参数只影响 raw/blended single-leg，不影响
`BasketConfig()`。

`tune_city_day_basket.py` 固定 PR2 baseline：

- raw_single
- market_only
- blended_single

然后只扫 basket 参数，输出 PR2b sweep JSON/Markdown。

## 2. 数据快照

```text
DB: runtime/weather.db
fact_signal_candidates settled complete rows: 1,878
date range: 2026-05-06 → 2026-06-04
```

注意：这比 PR2 报告多到 2026-06-04，因为本机 DB 已更新。

## 3. Sweep 结果

共扫 192 组参数，30 组通过离线 gate。

Gate 定义：

```text
missed_profit <= avoided_loss
basket ROI >= blended_single ROI * 0.8
basket ROI excl top-5 >= 0
BUY_NO basket ROI >= 0
```

Baseline：

| rule | n_legs | cost | PnL | ROI | ROI excl top5 |
|---|---:|---:|---:|---:|---:|
| raw_single | 1,696 | $8,480 | +$550 | +6.48% | -6.14% |
| market_only | 0 | $0 | $0 | 0.00% | 0.00% |
| blended_single | 1,200 | $6,000 | +$869 | +14.48% | -2.36% |

排名最高的稳健配置：

```text
small/normal notional = $3/$8
city_day_cap = $15
max_legs = 4
edge_small/normal = 0.03/0.06
prefer_no_over_yes = False
```

完整 eval：

| rule | n_legs | win_rate | cost | PnL | ROI | ROI excl top5 |
|---|---:|---:|---:|---:|---:|---:|
| blended_single | 1,200 | 51.7% | $6,000 | +$869 | +14.48% | -2.36% |
| basket PR2b | 792 | 45.2% | $3,944 | +$1,111 | +28.16% | +3.44% |

Basket vs blended_single attribution：

```text
missed_profit = +$784
avoided_loss  = +$825
net           = +$41
```

## 4. 结论

PR2b 离线 gate 已被部分参数反转。

关键变化不是单纯调大 sizing，而是：

1. `prefer_no_over_yes=False`：YES/NO 按 edge 一起竞争，Miami 这类低价 YES 尾部腿不再被系统性砍掉。
2. `max_legs=4`：比 v1 的 3 条腿稍放宽，减少 missed profit。
3. `$3/$8`：保留低 edge 小仓位，但高 edge 腿给足体量。

这仍然不是生产批准：

- eval 还是基于 `fact_signal_candidates` 的决策窗代表 snapshot，不是 N100 逐 snapshot replay。
- BUY_NO book 仍有 `1 - yes_bid` proxy 缺口。
- 不含真实 `forecast_jump_f` / `side_flip_count_today` 稳定性字段。
- 参数是在同一全样本上 sweep 出来的，存在明显 overfit 风险。

## 4.1 Robustness 复核

新增复核脚本：

```text
scripts/analysis/validate_city_day_basket_robustness.py
docs/analysis/2026-06/2026-06-06-city-day-basket-pr2b-robustness.md
docs/analysis/2026-06/2026-06-06-city-day-basket-pr2b-robustness.json
```

固定 PR2b 最佳配置，不重新调参，按 full / train / holdout / recent / live_filled 子集评估：

| slice | raw ROI | blended ROI | basket ROI | basket top5 ROI | gates |
|---|---:|---:|---:|---:|---:|
| full | +6.48% | +14.48% | +28.16% | +3.44% | 4/4 |
| train_pre_2026_05_26 | +14.47% | +24.52% | +40.53% | +7.44% | 3/4 |
| holdout_from_2026_05_26 | -10.76% | -5.91% | +4.23% | -29.31% | 2/4 |
| recent_from_2026_06_01 | +1.54% | +4.27% | +8.04% | -33.60% | 1/4 |
| live_filled_only | +2.52% | +5.56% | +11.67% | -15.35% | 1/4 |

量化解读：

- raw-only → blended 在 full / live_filled_only 都有改善，说明概率层目前仍有研究价值。
- basket PR2b 全样本明显好，但 holdout / recent / live_filled 子集去掉 top-5 后均为负，说明收益仍依赖少数尾部腿。
- `live_filled_only` 是机会子集反事实，不是钱包真实 PnL，也不是 PR2b live 实盘结果；新策略还没有上线。
- 因此 PR2b 参数只能作为 PR3 shadow 候选，不应直接 canary。

## 5. 下一步

推荐顺序：

1. 用 PR2b 参数作为 shadow 候选配置，不直接 live。
2. 做 PR3 shadow 双写，记录 blend metadata、basket decision、独立 NO book、forecast jump、side flip。
3. 等 7 天真实逐 snapshot shadow 数据后，再复核 PR2b gate。

不退役旧策略、不开新 canary、不改 N100 live 配置。
