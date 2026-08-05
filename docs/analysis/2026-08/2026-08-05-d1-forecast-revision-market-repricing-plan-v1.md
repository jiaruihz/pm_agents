# D-1 forecast revision × market repricing 研究计划与首轮审计 v1

weather-only:
significance=not_run_no_settlement_complete_forward_dates
calibration=W0_reference_unchanged_W1_training_pending
pooled_baseline=retained_negative_control
forward=collector_accumulating

market residual:
baseline=same-event complete normalized full ladder
forward=coverage_only_no_eligible_complete_forward_revision_yet
execution=not_run_no_probability_gate

production:
live_action=none
orders_changed=0

## 数据快照

- forecast rows=16660，raw batches=3808，material batches=621。
- collector observed window=2026-08-05T07:15:57.703070Z..2026-08-05T15:29:37.759673Z。
- market checkpoints=864；complete=246。
- settlement-complete revision events=0；其余保持 unsettled coverage，missing_bracket=0。

## 先修的 lineage 根因

旧 state 产生 backward previous-run rows=5425；另外 forward transition delivery rows=10880，折叠后 unique transition keys=977，重复=9903。
根因是每轮从旧 run 重新请求，而 state 只保存最后轮询 run。修复后按 model×city×target×run 保存历史，旧 run 重抓只比较同 run content，不再引用未来 run；原始 payload/run/value 不删除，污染仅限派生 revision lineage。

## 固定研究问题

在 D-1 complete exact-run material event 的 first available clock 上，比较事件前最后一份完整 ladder、事件后第一份完整 ladder和 5/10/30/60/90m markout；短窗用于识别薄盘/stale quote，长窗用于判断市场是否持续吸收同一 run。先检验 revision 是否带来方向一致的 market repricing，再在结算后比较 weather posterior 与各 checkpoint 的 M0 market proper score。

静态 forecast level、revision event 和 market residual 分三层：weather-only challenger 不读取市场；revision 只做连续 feature；M2/M3 只在同 rows、同 labels、同 feature-book 时钟下与 M0 比。
正式 weather score 的主 checkpoint 固定为当地 target 前一日 18:00–24:00 的首个 complete batch（`D-1_18_24`）；12:00–18:00 只作 secondary。revision markout 可保留全部 D-1 events，但不得替代主 checkpoint proper score。

## 首轮双漏斗

| signal funnel | count |
|---|---:|
| raw_forecast_batches | 3808 |
| material_forecast_batches | 621 |
| duplicate_poll_batches_collapsed | 3187 |
| complete_material_batches | 210 |
| monitor_start_utc | 2026-08-05T07:15:57Z |
| bootstrap_end_utc | 2026-08-05T07:45:57Z |
| d1_complete_run_transition_events | 68 |
| primary_d1_18_24_transition_events | 4 |
| forward_new_complete_run_events | 0 |
| settlement_complete_events | 0 |
| probability_scoreable_events | 0 |

| evidence funnel | count |
|---|---:|
| market_checkpoints | 864 |
| complete_market_checkpoints | 246 |
| revision_events_with_pre_book | 65 |
| revision_events_with_post_book | 68 |
| revision_events_immediate_scoreable | 14 |
| revision_events_5m_markout_scoreable | 17 |
| revision_events_10m_markout_scoreable | 17 |
| revision_events_30m_markout_scoreable | 15 |
| revision_events_60m_markout_scoreable | 15 |
| revision_events_90m_markout_scoreable | 15 |
| executable | 0 |
| actual_fills | 0 |

event classes：`{"bootstrap_existing_run": 34, "complete_batch_after_partial": 34}`。只有 `forward_new_complete_run` 可进入正式 revision forward；bootstrap 与 partial→complete 继续保留，但不混入 alpha 分母。

## 下一阶段与冻结规则

1. collector 修复部署后，从全新 state schema 开始积累 chronological revision；不重写旧 JSONL。
2. 先累计 complete D-1 run events、完整 pre/post ladders与 settlement；第一段 clean rows 明确作为 development，不冒充 forward。
3. W0 只作锁定 legacy reference；W1 在 clean development 的 inner train/validation 中选择 revision/spread/lead-age、层级收缩和 tail，先跑出 weather-only 结果再决定是否冻结。
4. W1 评审后，在同一 development rows 上比较 M0/M1/M2/M3并选择 residual 正则；两条线都出结果后才生成 freeze artifact。只有 freeze timestamp 之后的新日期进入 untouched forward，且 M2/M3 必须在其 target-date block bootstrap 的 logloss/RPS/calibration 上优于 M0，才进入 ask/fee/depth EV。
5. 当前不做 ROI、maker、selected price band、城市名单或 live 动作。

## 8 环与结论

本轮覆盖 lineage、signal/evidence coverage、market checkpoint contract；统计推断因 0 个 eligible settled forward events 未启动，执行、容量、fills、组合相关性均未覆盖。

结论：`inconclusive / collector-and-lineage-repair`。研究已经启动，但旧 revision 字段不能使用；修复代码需经生产确认后部署，随后才开始干净 forward clock。
