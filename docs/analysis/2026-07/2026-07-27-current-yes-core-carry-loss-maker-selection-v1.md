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
