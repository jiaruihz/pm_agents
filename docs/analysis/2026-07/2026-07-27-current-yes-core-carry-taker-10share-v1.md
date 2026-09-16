# Current-YES core carry：10-share taker 容量重放

Status: `inconclusive / no live sizing change`

## 结论

- 5-share 冻结：136 city-days，胜率 95.59%，
  fee-adjusted ROI +4.67%。
- 10-share 全 ladder 重放：134 city-days，胜率
  95.52%，fee-adjusted ROI +4.48%，
  date-block 95% CI [+0.63%,
  +7.93%]。
- 原 136 个 entry 中，136 个有十股深度，
  133 个在十股成本后仍为正 EV。
- 但当前 forward settlement 与 maker adverse-selection 证据不足，不扩大 live。

## 风险

若 taker 与 maker 都成交，历史 loss 的平均美元损失从
`$8.37` 增至 `$12.64`，
放大到 1.51 倍。

按当前初步 maker fill rate 41.7%，平均每日现金成本从约
`$29.13`
增至约
`$49.15`。

## 双漏斗与证据边界

- signal funnel：相同 OOF state/model/checkpoint，只改变 taker quantity。
- evidence funnel：使用 PIT full ask ladder + 官方 fee；不是 actual historical fills。
- maker 未并入 10-share alpha；当前 maker fill rate 只用于现金压力情景。
- 生产 runtime contract 当前明确冻结 `5 taker + 5 maker`；daily cost
  预检也写死按 10 shares 估算，不能把参数直接改成 `10+5`。
- `significance=PASS`（历史 absolute ROI），`baseline` 沿用冻结报告，
  `forward=FAIL`，`conclusion=inconclusive_for_live_size_up`。

动作：维持 `5 taker + 5 maker`，先积累 settled forward，不部署 10 taker。
