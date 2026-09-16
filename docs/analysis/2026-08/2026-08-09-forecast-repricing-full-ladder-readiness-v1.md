# Forecast Repricing full-ladder / readiness v1

significance=`FAIL_weather_incremental_CI_crosses_zero`

market_baseline=`PASS_same_denominator_compared`

execution=`TAKER_REJECTED / CONDITIONAL_MAKER_POINT_ONLY / MAKER_THEN_TAKER_NOT_READY`

forward=`NA_no_post_freeze_settled_full_ladder_cohort`

production=`live_action:none / orders_changed:0 / collector_change:not_deployed`

## 1. 结论与动作

当前不能把 Forecast Repricing 称为独立 alpha，也没有已确认的可交易执行形态。60m 是最有希望的 horizon，conditional maker 是唯一值得继续采证的表达，但其 `+6.62%` 只是 4 个日期、无真实 fill 的条件点估；taker 在真实 ask/depth/Weather fee 下显著为负，maker-then-taker 也没有足够证据启用 fallback。

下一步唯一动作：在取得显式部署确认后，扩展共享 `weather_market_books` 的 D-2/D-1 forecast-event full-ladder burst，并启动冻结 60m scorer 的 zero-notional posted→queue→partial-fill/expire→markout ledger。此动作只采集和虚拟评分，不产生 `TradeIntent`、order 或 fill，不改 Core Carry 或任何 live 参数。

## 2. Readiness card

| 项目 | 状态 | 证据 / blocker |
|---|---|---|
| canonical identity | PASS | 2026-08-09 manifest exit 0；DB route healthy；兼容入口与 `/Volumes/jrs/pm_agents/runtime/weather.db` 同 device `16777247` / inode `54444`。唯一 warning 为另一 production checkout head drift，与本次只读 artifact 无关 |
| fixed full-ladder denominator | PASS_reconstructed | 2,767 events / 28,038 rungs；selected 2,767、unselected 25,271 |
| D-1 coverage | PARTIAL | 2,666 events / 40 dates；30m 30 dates、60m 31 dates |
| D-2 coverage | FAIL_FOR_CLAIM | 101 events / 11 dates；远小于 D-1，secondary holdout 的可交易候选全部为 D-1 |
| 5/15m historical price coverage | FAIL | 5m=0；15m=5,427 rungs / 517 events / 4 dates，属于 coverage gap，不是策略筛除 |
| exact forecast clocks | PARTIAL | v3 journal 有 issue/run time、collector run/content first-seen、source fetched、available clocks；provider availability first-seen 未观测。legacy archive 只有 estimated issue 与 earliest-observed collector clock |
| current exact-run feed | PARTIAL | v3 101,728 rows、5,078 unique city/model/target/run keys、4 D-1 与4 D-2 target dates；latest 06Z request因 provider run unavailable/429 blocked，fallback=false |
| REST full-ladder books | PASS_CURRENT_NOT_EVENT_JOINED | `weather_market_books` current REST batch healthy；仍未形成 forecast-event t0/+5/+15/+30/+60 的正式 joined cohort |
| D-2/D-1 WS dynamics | FAIL | 当前 `tiered_hot_strip_v4` 只覆盖4个 target-day 城市/热点 brackets，不是 D-2/D-1 complete ladder；截至 07:40Z 当日 payload 已 1.296GB，不能未经预算验证盲目扩订阅 |
| settlement probability head | FAIL_SEPARATE | legacy model Brier/logloss 均显著输 market；不进入 repricing target |
| chronological secondary holdout | PASS_RECONSTRUCTED_ONLY | model/horizon/threshold 只用 development expanding OOF 固定，最后9个 target dates 才打开；60m 实际可比为7 dates |
| formal frozen forward | FAIL | 没有冻结后、collector-exact、已结算且完整 event-ladder 的评分 cohort |
| real maker fill lifecycle | FAIL | actual fills=0；queue/partial/expire/adverse selection 未观测；future touch 未当 fill |

## 3. 分母、时钟与漏斗

历史可核验 universe 是 3,913 snapshot files、3,139,991 raw rows、309,345 ladder groups；N100 物理 I/O 故障造成的未恢复文件仍是已知缺口，因此这里的“全部”仅指该 recovered union。

| funnel | stage | count | unit |
|---|---|---:|---|
| signal | complete D-2/D-1 ladders | 19,673 | ladder state |
| signal | raw forecast update events | 2,805 | event |
| signal | complete paired forecast events | 2,767 | `city × target_date × forecast-event` |
| signal | full ladder rungs | 28,038 | rung；selected 2,767 / unselected 25,271 |
| evidence | 5m markout | 0 | rung |
| evidence | 15m markout | 5,427 / 517 / 4 | rung / event / target_date |
| evidence | 30m markout | 19,252 / 1,890 / 30 | rung / event / target_date |
| evidence | 60m markout | 18,856 / 1,857 / 31 | rung / event / target_date |
| execution | holdout threshold signals | 36 / 4 active dates | decision；其中28个有60m可评分 markout |
| execution | actual maker fills | 0 | fill |

时钟合同：

| clock | reconstructed archive | formal forward |
|---|---|---|
| forecast issue | `model_init_utc_estimated` | `forecast_run_at_utc`，只表示 issue/run，不冒充 availability |
| provider first-seen | missing | 若 provider API 不给 availability，保持 missing 并写 blocker |
| collector first-seen | earliest observed，非 exact | `run_first_seen_at_utc` + `content_first_seen_at_utc`，status=`collector_exact` |
| market snapshot | `snapshot_ts_utc` | book event time、collector received time、`available_at_utc` 分列 |
| execution | same-snapshot replay assumption | virtual decision time、posted time、ack/proxy time、expire/fallback time分列 |

## 4. 固定模型与向量 markout

权重按 target_date→forecast event→rung 三层等权。development 为前32个 dates；严格 expanding-date OOF。最后9个 dates 是 secondary holdout，60m 有7个可比 dates。Ridge alpha=10、horizon 候选与 threshold 均未看 holdout 后重调；5/15m 因 coverage 不足未参与选择。

| model（60m holdout） | vector MSE | MAE | direction | MSE delta vs market-level [95% CI] |
|---|---:|---:|---:|---:|
| market level only | 0.000224 | 0.006387 | 23.56% | baseline |
| weather innovation only | 0.000223 | 0.006248 | 25.73% | -0.0000005 `[-0.0000034,+0.0000017]` |
| weather + market level | 0.000222 | 0.006456 | 25.35% | -0.0000020 `[-0.0000058,+0.0000009]` |
| market + static microstructure control | 0.000221 | 0.006695 | 27.19% | -0.0000030 `[-0.0000060,+0.0000000]` |
| weather + market + static microstructure | 0.000219 | 0.006725 | 27.58% | -0.0000045 `[-0.0000081,-0.0000010]` |

最后一行相对 market-level 显著，但不能归因给 weather：对匹配的 `market + static microstructure` control，weather 增量是 `-0.0000017`，CI `[-0.0000051,+0.0000010]`，跨0。development 上 weather+market 60m 的增量也跨0。因此 market baseline 已比较，但独立 weather innovation 尚未通过显著性。

terminal settlement head 与此处严格分开：legacy model Brier `0.082117` vs market `0.067375`，delta `+0.014742` CI `[+0.012486,+0.016914]`；logloss delta `+1.125800` CI `[+0.882304,+1.413073]`。它失败的是最终 winner probability，不是短期 markout label。

## 5. 执行、频率、容量与尾部风险

| expression（60m holdout） | signals / dates | fee-adjusted ROI | target-date 95% CI | 判定 |
|---|---:|---:|---:|---|
| taker ask→future bid | 28 / 4 | **-31.96%** | `[-58.23%,-17.74%]` | reject |
| market-level taker baseline | 17 / 4 | -28.45% | `[-54.76%,+0.65%]` | negative control |
| conditional maker bid→future bid | 28 / 4 | **+6.62%** | `[-18.56%,+13.46%]` | point only；fill-unverified |
| maker-then-taker | 0 real lifecycles | NA | NA | not ready；历史 taker 负，不允许无条件 fallback |

development OOF 产生30个 signals/10个 covered dates（3.0/date，D-1 26、D-2 4）；secondary holdout 产生36个 virtual decisions/9个 scheduled target dates（4.0/date，但集中在4个 active dates），其中28个有60m markout且全部D-1。这个频率不支持外推 D-2。

28个可评分 holdout signals 的 visible min(entry ask depth, future bid depth) 中位34.33 shares、P25 19.26；raw top-level entry cost 合计仅 `$9.84`，现有 capped replay cost `$0.51`。这只能说明小额 top-of-book 可见，不是 maker queue capacity，也不能线性外推资金容量。

主要尾部风险：forecast first-seen/availability clock 错位；D-2 极稀疏；同 target-date 的城市与事件相关；provider exact run unavailable/429；spread+双边 fee 完全吞掉小 repricing；maker 不成交、部分成交与成交后 adverse selection；5m event-time book 缺失；WS 预算耗尽；完整 ladder 中错误 rung 映射或 stale book。现有 target-date bootstrap 只有7个 holdout dates，无法覆盖这些尾部。

## 6. Zero-notional forward 设计（未部署）

血缘：`forecast_run_row_v3 → ForecastEvent → pre/t0/+5/+15/+30/+60 full ladder → frozen score → zero-notional QuoteIntent → virtual posted/queue/partial/expire/fallback → markout`。不得生成真实 `TradeIntent`。

- universe：34个 v3 forecast cities，D-2/D-1；不按城市、价格带或事后表现过滤。event identity 固定为 city、target_date、forecast run、pre/post consensus content identity。
- capture owner：只扩展共享 `weather_market_books` selector；消费者不得重新请求盘口。event 到来时记录最后一份 `available_at<=collector_first_seen` 的 pre book，并让 owner 对该 event 的完整 ladder执行 t0 burst及+5/+15/+30/+60 定时 snapshot。
- WS scope：只在 event 后65分钟订阅该 city 的完整 D-2/D-1 ladder；先 dry-run 量 traffic。当前 collector 日预算3GB且07:40Z已用1.296GB；新增 arm 预设 stop=`projected total >2.7GB/day` 或任一 producer freshness失败。目标增量预算先限`<=0.5GB/day`，超限只保留REST checkpoints。保留30天热层，随后按 production artifact contract 迁至 archive；不删除 raw。
- frozen primary：60m `weather_plus_market_level` Ridge、threshold=0、date/event/rung等权；5/15/30m只做 secondary coverage，不重选模型或 threshold。
- taker ledger：虚拟 entry ask、当时可见 ask depth、双边 Weather fee；future bid/depth只作 markout，不假设可回到过去成交。
- conditional maker ledger：t0 best bid posted、queue-ahead、trade/cancel events、conservative partial fills、TTL=5m、expire reason与filled/unfilled两分母；touch-only另列，永不算 fill。
- maker-then-taker arm：TTL=5m 后，仅当冻结模型用新鲜 book 重算的剩余55m taker net markout仍>0才记录 virtual fallback；否则 expire。该 arm只是 forward 对照，历史证据不支持真实 fallback。
- minimum review：至少30个 settled target dates；先验收5/15/30/60各自 coverage、增量 weather MSE相对匹配 baseline、taker fee ROI、maker fill/expire/adverse-selection；target-date block bootstrap CI 过门后再开 untouched forward。任何 tiny live 仍需另行显式确认。

## 7. 可复跑产物与验证

- full-ladder base：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/forecast_repricing/full_ladder_base_20260809`
- evaluation：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/forecast_repricing/full_ladder_evaluation_20260809`
- `forecast_event_rungs.csv` SHA-256：`8be4d7c0a0f209f692df7935f6cb99ba9ad8914ef55f518818b20dd62e5f3900`
- reconstructed scorer SHA-256：`e6f83778841a864e6770d2a6c9b174c08e82b7bacecc2071be8dbf51a04859ad`
- 定向 tests：8 passed。没有修改 live strategy、city pool、sizing、execution policy 或订单。
