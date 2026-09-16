# Current-YES core carry：loss 形态与 maker adverse-selection 审计

Status: `inconclusive / no live sizing change`

## 结论

- 冻结 136 个 city-day 是 taker-only；maker 不在 +4.67% ROI 中。
- 6 个 loss 全部是 current exact bracket 后续向上 overshoot，不是低于该档。
- maker adverse selection 在机制上成立：thesis 变差时卖单更可能击中 current-YES bid；但当前 live maker 只有少量 fill 且 0 个 canonical settled，尚不能估计条件成交率。
- 不增加 maker size；maker/taker 继续分账。6 个事后 loss 不足以增加 hard gate。

## Loss 特征（仅解释，不作 gate）

| slice | slice loss | complement loss |
|---|---:|---:|
| market_mid_lt_0.85 | 5/37 (13.5%) | 1/99 (1.0%) |
| minutes_since_running_max_lt_60 | 6/74 (8.1%) | 0/62 (0.0%) |
| forecast_peak_passed_1.5_to_2.5h | 4/30 (13.3%) | 2/106 (1.9%) |
| path_faded | 1/49 (2.0%) | 5/87 (5.7%) |

共同形态是价格较低、running high 尚年轻、forecast peak 只过去约 1–2 小时，并且大多没有形成 mature fade。具体逐笔见 generated `loss_cases.csv`。

## Maker 压力测试

使用 trigger midpoint 作为 maker 允许追到的最高成本；这是压力情景，不把未来 touch 当 fill。

| scenario | combined ROI | expected maker fills |
|---|---:|---:|
| taker only | 4.67% | 0 |
| outcome-neutral at observed fill rate | 5.37% | 56.7 |
| losses always fill, same overall fill rate | 3.52% | 56.7 |
| only losses fill | 0.67% | 6.0 |

## Evidence 边界

- signal funnel：冻结 136 city-days / 30 target dates / 6 losses。
- live evidence funnel：taker city-days 12；maker fill city-days 5；settled maker fills 0。
- CLOB fill coverage gate：`gate_pass=false`；当前失败来自一笔本策略外 Busan 旧 fill 的 fee lineage unknown，因此本报告不发布 live PnL。

三门：`significance=NA`、`baseline=NA`、`forward=FAIL`，`conclusion=inconclusive`。动作：不扩大 maker，继续独立采集真实 fill/markout/settlement。

## 2026-07-29 live execution update

当前 Mac raw runtime 覆盖 `2026-07-25T01:40:29Z..2026-07-29T09:31:24Z`：

- 24 个 entry signals，20 个 maker root 成功 post-only 挂出，3 个由 authenticated order lookup 确认为 full fill；按 accepted maker root 计 fill rate 为 `3/20 = 15%`。
- 20 个 accepted maker root 中，13 个首次报价已经碰到 trigger-time midpoint cap；同样有 13 个的 `ask - 1 tick` 高于 cap。此时继续 follow best bid 也不能提高报价。
- 首次盘口平均 spread 为 `3.34c`，maker 首次报价平均比 ask 低 `2.825c`。3 个 confirmed maker fills 均在首次报价成交、没有依赖 reprice，价格分别为 `0.979 / 0.91 / 0.94`。
- 因此当前低 fill 的主因是 `minimum(initial_mid, model_probability)` cap，而不是 static/chase lifecycle 选择。把 refresh 从 15 秒改得更快不会解决这 13 个 cap-bound roots。

### Profile recommendation

core-carry 继续使用 `chase + post-only + no taker fallback` 这一 profile family；不切 static maker，也不使用 maker-to-taker fallback。建议新增同 family challenger：

`split_taker_maker_edge_capped_no_fallback_v2`

- 初始仍报 `best bid + 1 tick`；
- 价格上限从固定 initial midpoint 改成可配置的 retained-edge cap，最高仍不超过 `ask - 1 tick`，并保留相对 taker 的最小价格改善；
- 在同一 observation epoch 内分阶段向上改善，不能只靠无限 refresh；
- 在预计下一次天气数据更新前留 buffer 撤单，而不是等新 observation 到达后才撤；
- maker 到期不转 taker，因为独立 taker leg 已负责保证基础仓位。

该 V2 只应先做同 signal 的 shadow/A-B。当前只有 3 个 maker fills，尚不足以证明提高 cap 后的 fill 增量能覆盖 adverse selection；因此本次不改 live profile、TTL 或 maker size。
