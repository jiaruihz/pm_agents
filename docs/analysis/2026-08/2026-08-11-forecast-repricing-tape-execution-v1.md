# Forecast Repricing：真实 tape / queue-conservative maker 复核

significance=FAIL（宽分母 passive maker 为负；唯一正切片仅 2 fills / 1 target date）

baseline=best-bid touch proxy 已升级为 exchange trade print + visible queue ahead 的保守成交反事实

forward=FAIL_LOW_SAMPLE（WS execution slice 只有 1 个 exit-scoreable target date，且不覆盖 D-1 candidate universe）

execution=single-leg passive expression rejected；full-ladder completion 继续 zero-notional abstain

production: live_action=none; orders_changed=0

## 结论

`0/48` 的准确含义是：旧 secondary holdout 的 48 个候选中，未来 60 分钟没有一次 observed ask 跌到原
best bid。因此旧 `+36.39%` 是“假设能在一个市场没有主动来找我们的价位成交”后的条件收益，不是 48 次真实
maker fills。它不能排除主动 SELL 直接打 bid，只能说明旧的五分钟 snapshot 没有 tape/queue 证据。

本轮已经用现有 public WS 的真实 `last_trade_price` 补了这层证据。固定每个订阅 YES token 同时最多一张
60 秒 best-bid 假想挂单；只有真实 SELL tape 在该价或更低的累计成交量达到 `visible queue ahead + 5 shares`
才记保守成交，成交后按下一分钟 5 股 executable bid、官方 Weather taker fee 退出。结果仍然否定单腿表达：

- 4,017 posts；44 个 queue-conservative possible fills；23 个有完整 60 秒退出证据。
- fixed 60s：PnL `-$2.7196`，ROI `-6.57%`；23 笔只有 1 笔为正。
- 每次 book 更新重算、首次可保本即退出：ROI `-6.93%`，没有修复亏损。
- tight spread、light queue、30s supportive tape 均为负，ROI `-6.11%..-7.18%`。
- “盘口先恶化则撤单”只剩 2 fills，fixed60 ROI `+6.72%`；其中 Helsinki `+21.64%`、Amsterdam
  `-8.83%`。它只有 1 个 target date，且与 D-1 forecast candidates 不同分母，不能冻结为盈利策略。

因此当前最合理的优化不是继续从这 23 笔追价格带/城市阈值，而是停止 single-leg passive expression；保留
full-ladder completion 的 zero-notional runner，让它在没有正 completion margin 时明确 abstain。

## 更新到 T-1（2026-08-10）

历史重建输入已从 `2026-05-21..2026-08-09` 增量到 `2026-05-21..2026-08-10`：

| funnel | 8/09 截止 | 8/10 截止 | delta |
|---|---:|---:|---:|
| snapshot files | 5,019/5,020 | 5,031 | +11/+12 |
| forecast update events | 6,003 | 6,120 | +117 |
| paired complete-ladder events | 6,003 | 6,074 | +71 |
| full-ladder rungs | 62,444 | 63,225 | +781 |
| D-1 events | 5,902 | 5,973 | +71 |
| D-1 rungs | 61,392 | 62,173 | +781 |
| D-1 target dates | 65 | 66 | +1 |
| 60m scoreable rungs | 45,451 | 45,968 | +517 |
| 60m trade-through proxies | 3,979 | 4,025 | +46 |

更新后的 entry challenger 相对 market-level M0，holdout MSE delta 为 `-0.000000487`，target-date
bootstrap 95% CI `[-0.000001851,+0.000000939]`，仍跨 0。时间切分前推一天后，旧 conditional selector
在新 holdout 为 44 quotes / 0 trade-through，anti-toxic selector 为 0 quotes；full-ladder completion 仍是
development 33 quotes / 1 negative proxy fill（ROI `-11.45%`），holdout 2 quotes / 0 fill。

## WS execution slice

- physical capture：`2026-08-10T00:00:00Z..18:24:00Z`。
- selector：policy-valid selective hot strip；4 cities，exit-scoreable target date 只有 `2026-08-10`。
- raw：2,589,272 frames、2,421 exchange trade prints，其中 588 SELL。
- 重建：deterministic WS book contract；1,627 个 parity/sequence reconstruction errors 显式阻断，未静默补 REST。
- 这不是 D-1 forecast-event 同分母：当前 subscription selector 服务 intraday hot strip，没有覆盖跨城市 D-1
  forecast candidate tokens。
- public `last_trade_price` 是 exchange match；没有 own order lifecycle，queue-conservative fill 仍是严格反事实，
  不是账户 actual fill。

## 可复跑产物

- T-1 base：`forecast_repricing/full_ladder_base_20260811_tminus1/forecast_event_rungs.csv`
  - SHA-256 `eb2fe47a82fa309f8d8908f709f5fa909c34a78fa0faa5bb2380e95affffdcb5`
- position：`forecast_repricing/full_ladder_position_20260811_tminus1/position_policy.joblib`
  - SHA-256 `c7074670a73eb26e461a94aa68e15cb502edbf8c60246515ca81067d12599c26`
- tape：`forecast_repricing/tape_passive_execution_20260810/{passive_orders.csv,policy_summary.csv,summary.json,report.md}`
- current smoke：`forecast_repricing/completion_probe_current_smoke_20260811`
  - 256 files、3,206 complete ladders、35,266 rungs、93 streams；left-censored baseline，orders=0。

```bash
.venv/bin/python -m weather_model_evaluation.cli forecast-repricing-tape \
  --ws-root /Volumes/jrs/weather_data_feed_service_runtime/market_books/ws_incremental \
  --start-utc 2026-08-10T00:00:00Z --end-utc 2026-08-10T18:24:00Z \
  --output-dir /Volumes/jrs-archive/pm_agents/research/artifact_store/active/forecast_repricing/tape_passive_execution_20260810
```

## 当前动作

状态保持 `runnable zero-notional completion probe / historical gate FAIL`。不把 `adverse_cancel` 的 2-fill
正 ROI 写成策略 gate，不改生产、不下单。要检验“D-1 revision + adverse cancel + full-ladder completion”这一
完整策略，下一份新证据必须来自 D-1 candidate token 的 policy-valid WS subscription 和 own
post/cancel/fill lifecycle；扩大生产 selector 前需走 `weather-strategy-deploy` 并显式确认。
