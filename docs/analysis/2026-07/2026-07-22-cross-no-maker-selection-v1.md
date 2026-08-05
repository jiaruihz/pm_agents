# Cross-NO Maker 反向选择研究 v1

## 直接结论

- 不建议把 Helsinki `+0.6` 的 first-seen taker 全量换成 maker。历史盘口显示 maker 能在回撤里拿到更便宜的筹码，但真实 cross maker 小样本已出现方向性反向选择。
- Helsinki 同分母：collector 后 `33` 个 `+0.6` 信号，只有 `14` 个有 first-priced direct book。严格 first-seen taker 10 股成交 `4` 笔，PnL `$3.5309`。
- maker replay 使用当前价格逻辑：`min(first ask - 1 tick, 0.97)`；只有 first ask `≤0.97` 才按当前 policy 挂单，maker-only 不要求 first ask depth。成交模型要求归档 ask **严格穿过** limit 且当时 ask depth `≥10`，真实 Helsinki fill 可覆盖归档漏采。

| maker 窗口 | 可挂单 | touch/actual 事件 | 保守模型成交/股 | principal | PnL | ROI |
|---:|---:|---:|---:|---:|---:|---:|
| 45s | 5 | 2 | 1/5.0 | $4.79 | $0.21 | 0.043841 |
| 225s | 5 | 3 | 3/30.0 | $24.76 | $5.24 | 0.211632 |
| 300s | 5 | 4 | 3/30.0 | $24.76 | $5.24 | 0.211632 |

- 45 秒窗口有两个 touch/actual 事件，但保守口径只认 Helsinki 7/17 的真实 `5 @0.958` fill（38.5 秒，PnL `$0.21`）。7/18 在 7 秒后 ask 穿到 `0.96`，当时只显示 8 股，不能冒充 10 股 full fill。
- 225 秒模型看起来优于 taker，主要因为 7/20 previous-20 NO：first ask `0.55` 但仅 `0.28` 股，180 秒后 ask 穿到 `0.54` 且显示 10 股，于是 maker 模型按 `10 @0.549` 计入。它是 queue/cross-through 反事实，不是 actual fill，不能当 live 回测成交。
- first ask 高于 `0.97` 时直接在 `0.97` 挂 225 秒，没有增加任何 strict model fill；当前样本不支持靠 cap maker 捡高价盘口回撤。

## 各城当前规则的全采集窗口

| 城市/当前规则 | 信号/日期 | settled 正确 | priced/price eligible | taker成交/PnL | 220s maker队列模型/PnL |
|---|---:|---:|---:|---:|---:|
| Atlanta / `persistent_candidate_margin_v5` | 12/7 | 11/12 | 5/2 | 1 / $-8.75655 | 1 / $-8.69 |
| Busan / `persistent_candidate_margin_v5` | 26/7 | 20/20 | 22/7 | 5 / $8.56415 | 1 / $1.51 |
| Helsinki / `single_candidate_margin_0p6` | 33/9 | 33/33 | 14/5 | 4 / $3.5309 | 3 / $5.24 |
| Singapore / `persistent_candidate_margin_v5` | 5/5 | 4/4 | 4/2 | 1 / $0.3808 | 0 / $0 |
| Tokyo / `arithmetic_round_v1` | 48/10 | 41/43 | 25/9 | 7 / $-0.760963 | 1 / $0.34 |

- 这张表使用各城**当前** signal policy：Busan/Singapore 双观测 `0.5+0.7`、Tokyo 单次 arithmetic cross、Helsinki 单次 `+0.6`；Atlanta 作为 `terminal_false_cross` negative control。旧规则事件没有混入。
- Busan 当前规则 20/20 settled signal 正确，first-seen taker 已结算 5 笔 PnL `+$8.5642`（另 1 笔为 7/22 未结算）；220 秒 maker queue model 只识别 1 笔，说明 archive 对被动成交的覆盖很弱，不能据此说 Busan maker 不成交。
- Tokyo 43 个 settled signal 中 41 正确，但 first-seen taker 可执行子集 PnL `-$0.7610`；信号高准确率并不自动等于高价 NO 有正 EV。maker 模型同样不能绕过这层市场定价。
- Singapore 只有 4 个 settled current-policy signals；Helsinki direct-book 也只有 14 个 priced signals。两城均未达到城市级 10 active days / 30 settled fills 门槛。

## 真实 maker 证据：所有 cross 城市

- 共挂出 `15` 张 maker，canonical order→fill 回连确认 `8` 张成交；已结算/人工治理确认 `13` 个信号，其中正确 `11`、错误 `2`。
- 错误信号 maker 成交 `2/2`；正确信号成交 `6/11`。也就是错误 fill rate `100%`，正确 fill rate `54.5%`。样本很小，不构成统计确认，但方向是 adverse selection，不是 maker 自动避错。
- 已成交 maker（5 股腿）实际/治理修正 PnL `$-5.50541`；同一批信号若按配对 taker 缩成 5 股是 `$-5.919765`，maker 省价/fee 改善 `$0.414355`。
- 但 maker 没成交的 5 个已结算信号全部正确，错失 taker 5 股等价 PnL `$2.199935`；全 13 个已结算信号 taker 5 股等价 PnL `$-3.71983`，优于 maker-only 的 `$-5.50541`。
- 两个错误 fill 必须单列：Atlanta 7/17 `terminal_false_cross` 与 Tokyo 7/21。Atlanta 因 bracket upper `89` 与 settlement bracket `88-89` 的 canonical join 缺口，按项目治理记录修正为 maker `-$4.30`，没有把它伪装成未结算。

### 分城 actual order→fill

| 城市 | maker挂出/成交 | settled正确/错误 | 正确成交/错误成交 | maker实际PnL | 全信号taker 5股等价PnL |
|---|---:|---:|---:|---:|---:|
| Atlanta | 1/1 | 0/1 | 0/1 | $-4.3 | $-4.378275 |
| Busan | 6/3 | 5/0 | 3/0 | $1.91952 | $2.64216 |
| Helsinki | 1/1 | 1/0 | 1/0 | $0.21 | $0.195175 |
| Singapore | 2/1 | 1/0 | 1/0 | $0.19517 | $0.1904 |
| Tokyo | 5/2 | 4/1 | 1/1 | $-3.5301 | $-2.36929 |

- Busan/Helsinki/Singapore 当前真实 maker fill 都是 winner（合计 5 笔），说明 maker **可以**在这些城市的波动中拿到筹码；不能从 pooled 亏损推出“所有城市不要 maker”。
- Tokyo 的选择最差：4 个正确信号只 fill 1 个 maker，唯一错误信号也 fill；maker `-$3.5301`，全 5 个信号 taker 5 股等价 `-$2.3693`。但只有 5 个信号，仍是 `low_sample/inconclusive`，不能当作已确认 city gate。
- Busan maker 3 个 winner PnL `+$1.9195`，但没 fill 的 2 个 resolved signal 也都是 winner；它改善已成交腿的价格，却牺牲 coverage。Helsinki/Singapore 都只有 1 个 resolved maker fill。

## Markout 与生命周期

- 只有 exact fill timestamp 且 archive 可对齐的 3 笔可算短期 ask markout：30 秒中位数 `0.0`，5 分钟中位数 `0.005`。盘口 spread 很宽，bid/mid markout 明显更差；样本不足以证明短期回撤一定继续。
- 参数名写 `effective_lifetime_sec=45`，但订单 expiration 实际中位为下单后 `219.536` 秒，因为实现额外加了 180 秒 security threshold，且没有 45 秒主动 cancel。当前“45 秒 maker”事实上约 225 秒。
- 生产 stale-book observer 当前进程没有启用 `--continuous-active-brackets`，所以 60 秒采样会漏掉 45 秒内 touch/queue；未来应以真实 order lifecycle/fill 为主、连续 direct-book archive 为辅。

## 动作建议

不是“全部不要 maker”。结论分两层：

1. `maker-only 替代 first-seen taker`：各城都不支持，保持 taker 为 core execution。
2. `taker 后额外 5 股 maker probe`：Busan/Helsinki/Singapore 有正面 fill，Tokyo/Atlanta 有明显 adverse-selection negative controls；pooled 和所有 city slice 都未过样本/前瞻门，所以不扩量、不把 maker PnL 当策略 alpha，也不基于本轮小样本做城市 live keep/cut。

先把 45 秒语义修成显式 cancel、恢复 active-bracket 连续 archive，再做按城市冻结的 `taker-only` vs `taker+maker5` forward。三门状态：`significance=FAIL(low sample)`、`baseline=FAIL/NA(queue coverage)`、`forward=FAIL`、`conclusion=inconclusive`。

## 口径边界

- signal funnel：collector 后 `+0.6` 首信号 → first-priced book。
- evidence funnel：first-priced book → 当前 price gate → archive strict cross/depth 或 actual fill → settlement。
- archive touch 不是 actual fill；strict cross/depth 仍不知道 queue ahead，因此列为模型成交。PnL 不含 maker rebate，真实 canonical fee按 fill evidence保留。
- 逐事件见 `helsinki_maker_replay.csv`、`current_policy_city_replay.csv` 与 `actual_maker_order_ledger.csv`；分城汇总见 `current_policy_city_summary.csv`、`actual_maker_city_summary.csv`；markout 见 `actual_maker_fill_markouts.csv`。

## 数据完整性自检

- canonical DB：`max_fact_built_at_utc=2026-07-22T04:05:05.526752+00:00`，`max_fill_ts_utc=2026-07-22T01:51:30.085506+00:00`，覆盖 target date 到 `2026-07-22`；本轮未同步、未全量重建。
- `weather_clob_fill_coverage_gate.py` 已在报告生成后复核：`gate_pass=true`、DB/cache fill id 双向差异均为 0。
- 8 环：①描述性绩效=覆盖；②统计推断=未通过（城市样本不足）；③信号判别=覆盖；④概率校准=本题不涉及；⑤执行微结构=部分覆盖（真实 fill + 稀疏 archive，缺 queue）；⑥容量=仅 top-depth/5股 actual，未通过；⑦组合相关性=按 target date 列示但样本不足；⑧基准=同信号 taker 5股等价已覆盖。
