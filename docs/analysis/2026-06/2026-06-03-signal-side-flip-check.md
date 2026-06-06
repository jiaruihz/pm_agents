# 2026-06-03 Signal Side Flip Check

> 稳定结论已抽取到外层文档
> [WEATHER_PROBABILITY_MODEL_REVIEW.md](../../WEATHER_PROBABILITY_MODEL_REVIEW.md)。
> 本文件只保留当次 raw snapshot/lineage 证据。

## 数据快照

- 数据源：本机镜像 raw lineage，`runtime/weather_edge_v1/remote_pm_agent/signals/*.jsonl`。
- 同步状态：已执行 `scripts/ops/sync_weather_remote.sh`，但 rsync 末尾出现 `Broken pipe`，因此本次使用本地已镜像文件；最新 raw `live_cycle` 文件已到 `2026-06-03 00:11` 北京时间附近。
- DB 状态：`runtime/weather.db` 修改时间为 `2026-06-01 21:56:45 +0800`，`signals.max(snapshot_ts_utc)=2026-05-31T14:00:53Z`，未覆盖 6/1 之后 raw signal。
- 记录行数：raw `target_date IN (2026-05-31, 2026-06-01)` 共 2002 条 signal row，67 个 `target_date + city + condition_id/market_id + bracket` 分组。
- unsettled 占比 / missing_bracket：本报告只查 signal side flip，不评估 fill 结算层；PnL/settlement 未作为结论依据。

## 目标指标

`signal_side_flip` = 同一 `target_date + city + condition_id/market_id + bracket` 在多个 raw signal snapshot 中同时出现过 `BUY_YES` 和 `BUY_NO` eligible edge。

分母锁定为 raw signal lineage，不用 `fact_trades` 自算 PnL。

## 数据完整性自检

- 按 DB `signals` 查 `target_date=2026-05-31` 可见 4 个 flip group；`target_date=2026-06-01` 在 DB 中只有 15 条 signal，不足以判断。
- raw `remote_pm_agent/signals` 覆盖 2026-06-01 全天和 2026-06-02，因此 6/1 结论以 raw signal 为准。
- 新增检查脚本 `scripts/analysis/inspect_weather_signal_side_flips.py` 已通过 `py_compile`。

## 结果

raw signals 中 `target_date IN (2026-05-31, 2026-06-01)` 共发现 10 个 flip group：

| target_date | city | bracket | switches | first -> last | p_yes range | entry price range | edge range |
|---|---:|---:|---:|---|---:|---:|---:|
| 2026-05-31 | LA | 70-71 | 2 | NO -> NO | 0.0367-0.5592 | 0.3900-0.6700 | 0.1246-0.3633 |
| 2026-05-31 | LA | 72+ | 2 | YES -> YES | 0.3619-0.9483 | 0.4650-0.6600 | 0.1060-0.4033 |
| 2026-05-31 | Miami | 88-89 | 1 | NO -> YES | 0.1265-0.6354 | 0.2750-0.7000 | 0.1735-0.3604 |
| 2026-05-31 | Miami | 90-91 | 1 | YES -> NO | 0.1769-0.6163 | 0.4600-0.5400 | 0.1213-0.2881 |
| 2026-06-01 | LA | 70-71 | 2 | YES -> YES | 0.2286-0.6204 | 0.4050-0.5950 | 0.1031-0.2154 |
| 2026-06-01 | London | 21 | 1 | YES -> NO | 0.0732-0.5070 | 0.3700-0.7250 | 0.1170-0.3018 |
| 2026-06-01 | Lucknow | 34 | 1 | NO -> YES | 0.1643-0.4618 | 0.3250-0.6650 | 0.1218-0.1957 |
| 2026-06-01 | Lucknow | 35 | 1 | YES -> NO | 0.1615-0.3711 | 0.2600-0.7050 | 0.1111-0.1985 |
| 2026-06-01 | Miami | 90-91 | 1 | NO -> YES | 0.1986-0.6354 | 0.4100-0.5750 | 0.1332-0.2314 |
| 2026-06-01 | Miami | 92-93 | 1 | YES -> NO | 0.1265-0.6136 | 0.2350-0.7150 | 0.1181-0.3786 |

## 代码口径

`scripts/ops/weather_snapshot_signal_builder.py` 明确按 market 去重，而不是按 `market + side` 去重；注释说明同一 market 在 lookback window 中可以从 `BUY_NO` flip 到 `BUY_YES`，为了避免 live 自我对冲，保留最新 eligible snapshot。

edge 公式从样例看是自洽的：

- BUY_YES：`edge = model_probability_yes - entry_price`
- BUY_NO：`edge = 1 - model_probability_yes - entry_price`

例如 `2026-06-01 LA 70-71`：

- `01:30:53Z BUY_YES`: `p=0.6204, entry=0.4050, edge=0.2154`
- `02:00:53Z BUY_NO`: `p=0.2286, entry=0.5950, edge=0.1764`

这说明 side flip 是模型概率/盘口在不同 snapshot 中跨阈值后产生的，不像 YES/NO token 取反、edge 符号反了，或 ingest 把 side 写错。

## 结论和动作

交易动作：不建议因为出现 side flip 就直接判定 bug 或砍掉 live；当前证据显示这是预期内的快照漂移现象。

排查动作：后续应该加一个监控，把 `signal_side_flip` 单独计数，尤其标出 `switches >= 2`、`p_yes` 大幅跳变、以及同一 city-date 相邻 bracket 成对反向 flip 的情况。这类不是计算错误，但可能是 forecast 接近结算时不稳定，适合作为 size 降档或 shadow 观察因子。

## 追加排查：为什么模型概率跳这么快

新增 raw snapshot transition 检查脚本：

```bash
.venv/bin/python scripts/analysis/inspect_weather_snapshot_side_flip_transitions.py \
  --target-date 2026-05-31 --target-date 2026-06-01 \
  --min-abs-edge 0.10 --min-entry-price 0.25 --max-entry-price 0.75
```

强 flip 定义：相邻 snapshot 方向变化，且前后两侧都满足 `abs_edge >= 0.10`、entry price 在 `[0.25, 0.75)`。

结果：

- `transition_count=67`
- `median_abs_d_forecast=2.1°F`
- `mean_abs_d_forecast=2.2254°F`
- `median_abs_d_p=0.3932`
- `mean_abs_d_p=0.4022`
- `median_abs_d_yes_px=0.01`
- `mean_abs_d_yes_px=0.0291`
- `abs_d_forecast_ge_1f=64/67`
- `abs_d_forecast_ge_2f=36/67`
- `model_init_changed=5/67`

结论：强 flip 的主要驱动不是市场价格变化，而是 `forecast_max_f` 变化。价格中位变化只有 1 cent；模型概率中位变化接近 39 points。

生产概率链路：

- `paper_snapshot.py` 每次 snapshot 实时调用 Open-Meteo live GFS/ECMWF endpoint。
- `fetch_live_gfs()` / `fetch_live_ecmwf()` 只取 `hourly.temperature_2m` 的当天 `max(valid)`，没有固定 forecast data version。
- `compute_bracket_probs()` 用 `forecast_max_f + 历史误差样本` 后 round 到整数温度，再统计落入 bracket 的比例。

因此 1-3°F 的 daily max 变化会被窄 bracket 放大成很大的概率变化。特别是 `70-71`、`90-91` 这种 2°F 桶，如果 forecast 从桶中心一侧跨到另一侧，历史误差样本投票会整体迁移，`model_prob` 可以跳 30-60 points。

关键例子：

| target_date | city | bracket | from_ts | to_ts | side | forecast | p_yes | yes_px |
|---|---|---|---|---|---|---:|---:|---:|
| 2026-06-01 | LA | 70-71 | 2026-06-01T01:30:53Z | 2026-06-01T02:00:53Z | YES -> NO | 70.4 -> 69.1 | 0.6204 -> 0.2286 | 0.405 -> 0.405 |
| 2026-06-01 | Miami | 90-91 | 2026-06-01T01:30:53Z | 2026-06-01T02:00:53Z | NO -> YES | 92.2 -> 90.3 | 0.1986 -> 0.6354 | 0.425 -> 0.445 |
| 2026-05-31 | LA | 70-71 | 2026-05-31T04:30:53Z | 2026-05-31T05:00:53Z | YES -> NO | 71.5 -> 73.7 | 0.5592 -> 0.0367 | 0.390 -> 0.400 |

这更像是 probability/forecast layer 的版本不稳定问题，而不是 edge 公式 bug。`model_init_utc_estimated` 只有 5/67 次变化，说明字段名里的 model cycle 是估算值，不足以证明 Open-Meteo 返回的是同一底层 forecast version。

建议后续改造：

1. 在 snapshot 里落盘 forecast hourly vector 或至少 `forecast_max_hour_local` / `forecast_values_hash`，否则只能看到 max 变了，看不到是哪一个小时变了。
2. 对 `forecast_max_f` 加稳定性约束：同一 city-date 最近两次 max 变化超过 1°F，或者同一 bracket 出现 side flip，则降 size / shadow。
3. 对窄 bracket 的概率加时间平滑或 ensemble：不要让单一 live GFS/ECMWF endpoint 的半小时跳变直接满额触发反向交易。
