# HeadA market-anchored lifecycle v1

Generated: 2026-07-28T12:41:36+00:00

## 结论与交易动作

这轮把“升温路径有预测增量”推进到了同刻市场与可执行动作，不再停在天气模型自身分数。
主检验固定为：`market probability + 1.0 × weather probability delta`，动作阈值固定 `2c`；
其他 gamma/threshold 只作敏感性，不据此挑最漂亮的结果。

| checkpoint_hour_local | rows | dates | market_brier | candidate_brier | brier_delta | brier_delta_ci_low | brier_delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 9.00000 | 41.00000 | 8.00000 | 0.07470 | 0.07096 | -0.00374 | -0.01608 | 0.00550 |
| 12.00000 | 40.00000 | 8.00000 | 0.04757 | 0.04444 | -0.00313 | -0.00836 | 0.00114 |

| action | checkpoint_hour_local | opportunity_rows | dates | wins | action_rows | unconditional_exit_all_pnl | policy_pnl | pnl_delta_vs_hold | pnl_delta_vs_exit_all | date_mean_delta_ci_low | date_mean_delta_ci_high | policy_roi_on_entry_cost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| sell_or_hold_direct_bid | 9 | 16 | 5 | 1 | 5 | -0.52519 | -0.79315 | 0.22207 | -0.26796 | 0.00914 | 0.07874 | -0.39358 |
| add_one_share_direct_ask | 9 | 17 | 5 | 1 | 0 | NA | 0.00000 | 0.00000 | NA | NA | NA | NA |
| sell_or_hold_direct_bid | 12 | 7 | 4 | 0 | 1 | -0.18598 | -0.83664 | 0.00190 | -0.65067 | 0.00000 | 0.00143 | -0.99773 |
| add_one_share_direct_ask | 12 | 9 | 5 | 0 | 2 | NA | -0.20655 | -0.20655 | NA | NA | NA | -1.00000 |

**当前裁决：没有可执行升级。** 09:00/12:00 的 market-anchored terminal Brier 点估均改善，但
主 gamma=1 的 target-date CI 都跨 0；12:00 的 weather delta 对未来 180 分钟 repricing
显著反向，提示市场可能先过冲再均值回归，不能把 terminal-score 点估直接当短线加仓信号。
direct-bid 子集只有 09:00 的 1 个 winner、12:00 仍为 0，因此“退出比持有少亏”不能归因于
weather selector，必须同时看
`unconditional_exit_all_pnl`。12:00 的 add 信号最终都输掉。price-history 是 midpoint-like public
proxy，不含 bid/ask/depth，不能包装成 guaranteed fill ROI。

## 研究对象与时钟

- estimand：已经在 D-1/target-day 09:00 前进入的 HeadA exact ticket，在当地 09:00 或 12:00
  收到 `actual warming - forecast warming` 后，是否应 sell / hold / add。
- weather increment：同 rows OOF `p_innovation - p_rolling_bias`，sigma=3.0F。
- market anchor：checkpoint 之前最后一条 CLOB `/prices-history`，最大 age=90m。
- execution：sell 用 direct YES bid；add 用 direct YES ask；Weather fee
  `0.05*p*(1-p)`；edge buffer=0.02。
- 不把当天 observation 回填到原 entry selector。

## 双漏斗

### Signal funnel

- frozen current HeadA：84 tickets / 8 target dates。
- 与 canonical 09/12 checkpoint 相交：

| checkpoint_hour_local | overlap_rows | dates | proxy_price_rows | direct_bid_rows | direct_ask_rows |
| --- | --- | --- | --- | --- | --- |
| 9 | 41 | 8 | 41 | 16 | 17 |
| 12 | 40 | 8 | 40 | 7 | 9 |

### Evidence funnel

- exact token identity：来自同一个 `ladder_snapshot_id + condition_id` canonical rung。
- public price proxy：只恢复历史 repricing path，不证明当时可成交。
- direct bid/ask 缺失原因主要是 `orderbook_budget_exhausted` / `orderbook_scope_skipped`，
  是 coverage gap，不是策略筛除。
- settlement：current HeadA 固定 84/84。
- actual fills：0；本报告是 research replay。

### Fresh-book 污染窗口

7/27 起 runner 记录了 223
次 `fresh_book_fetch_failed`，对应 18 个 unique
`city/date/bracket/token`；HeadA 是 zero-notional shadow，所以受影响真实 order/fill/notional 均为 0，
但 would-live/fresh-book evidence 被污染。

| city | event_date | bracket | failed_attempts | first_failure_utc | last_failure_utc | snapshot_ask | model_p_yes | edge | book_error |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Atlanta | 2026-07-27 | 94-95 | 3 | 2026-07-27T02:38:27Z | 2026-07-27T02:49:49Z | 0.15500 | 0.64970 | 0.49470 | ConnectTimeout: timed out |
| Atlanta | 2026-07-27 | 96-97 | 11 | 2026-07-27T02:55:29Z | 2026-07-27T04:13:15Z | 0.07000 | 0.64970 | 0.57970 | ConnectTimeout: timed out |
| Austin | 2026-07-27 | 100-101 | 14 | 2026-07-27T03:07:32Z | 2026-07-27T04:58:32Z | 0.11500 | 0.38860 | 0.27360 | ConnectTimeout: timed out |
| Chicago | 2026-07-27 | 92-93 | 6 | 2026-07-27T03:08:12Z | 2026-07-27T03:50:06Z | 0.13000 | 0.59920 | 0.46920 | ConnectTimeout: timed out |
| Chicago | 2026-07-27 | 94-95 | 5 | 2026-07-27T04:06:11Z | 2026-07-27T04:41:47Z | 0.07000 | 0.28260 | 0.21260 | ConnectTimeout: timed out |
| Dallas | 2026-07-27 | 104-105 | 15 | 2026-07-27T03:08:53Z | 2026-07-27T05:06:55Z | 0.05000 | 0.32770 | 0.27770 | ConnectTimeout: timed out |
| Denver | 2026-07-27 | 96-97 | 16 | 2026-07-27T04:06:53Z | 2026-07-27T05:59:27Z | 0.14000 | 0.58690 | 0.44690 | ConnectTimeout: timed out |
| Denver | 2026-07-27 | 98-99 | 2 | 2026-07-27T06:05:48Z | 2026-07-27T06:12:10Z | 0.05000 | 0.25640 | 0.20640 | ConnectTimeout: timed out |
| Houston | 2026-07-27 | 98-99 | 15 | 2026-07-27T03:09:33Z | 2026-07-27T05:08:16Z | 0.09000 | 0.35880 | 0.26880 | ConnectTimeout: timed out |
| MexicoCity | 2026-07-27 | 26 | 18 | 2026-07-27T04:07:34Z | 2026-07-27T06:12:50Z | 0.15000 | 0.35030 | 0.20030 | ConnectTimeout: timed out |
| CapeTown | 2026-07-28 | 18 | 16 | 2026-07-27T20:06:49Z | 2026-07-27T22:01:56Z | 0.10000 | 0.39270 | 0.29270 | ConnectTimeout: timed out |
| Chongqing | 2026-07-28 | 35 | 21 | 2026-07-27T14:13:59Z | 2026-07-27T16:16:16Z | 0.05500 | 0.30230 | 0.24730 | ConnectTimeout: timed out |
| Helsinki | 2026-07-28 | 18 | 21 | 2026-07-27T19:03:36Z | 2026-07-27T21:05:24Z | 0.07000 | 0.30790 | 0.23790 | ConnectTimeout: timed out |
| HongKong | 2026-07-28 | 31 | 5 | 2026-07-27T14:44:33Z | 2026-07-27T16:16:56Z | 0.08500 | 0.28530 | 0.20030 | ConnectTimeout: timed out |
| Milan | 2026-07-28 | 35 | 14 | 2026-07-27T20:08:09Z | 2026-07-27T22:02:36Z | 0.05750 | 0.33900 | 0.28150 | ConnectTimeout: timed out |
| Moscow | 2026-07-28 | 25 | 3 | 2026-07-27T19:04:16Z | 2026-07-27T19:16:59Z | 0.05500 | 0.25990 | 0.20490 | ConnectTimeout: timed out |
| Shanghai | 2026-07-28 | 32 | 15 | 2026-07-27T14:14:40Z | 2026-07-27T16:17:37Z | 0.11000 | 0.37770 | 0.26770 | ConnectTimeout: timed out |
| Wellington | 2026-07-28 | 13 | 23 | 2026-07-27T10:03:55Z | 2026-07-27T12:08:05Z | 0.12500 | 0.36720 | 0.24220 | ConnectTimeout: timed out |

## Market residual / repricing lead-lag

如果 weather delta 真是市场尚未吸收的信息，它应同向预测未来 60/180 分钟价格变化：

| checkpoint_hour_local | horizon_min | rows | dates | correlation | slope_price_move_per_1p_weather_delta | mean_signed_move | signed_move_ci_low | signed_move_ci_high | directional_accuracy |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 9.00000 | 60.00000 | 41.00000 | 8.00000 | 0.17908 | 0.32742 | 0.00588 | -0.01065 | 0.02781 | 0.48780 |
| 9.00000 | 180.00000 | 39.00000 | 8.00000 | 0.15822 | 0.53606 | 0.01647 | -0.01487 | 0.05468 | 0.58974 |
| 12.00000 | 60.00000 | 40.00000 | 8.00000 | 0.25489 | 0.30001 | 0.01534 | -0.01672 | 0.02794 | 0.45000 |
| 12.00000 | 180.00000 | 40.00000 | 8.00000 | -0.20629 | -0.51280 | -0.02698 | -0.04650 | -0.00326 | 0.50000 |

## Market-anchored probability sensitivity

gamma=0 是同 rows market proxy；负 delta 才是改善：

| checkpoint_hour_local | gamma | rows | dates | brier_delta | brier_delta_ci_low | brier_delta_ci_high | logloss_delta | logloss_delta_ci_low | logloss_delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 9.00000 | 0.00000 | 41.00000 | 8.00000 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.00000 |
| 9.00000 | 0.25000 | 41.00000 | 8.00000 | -0.00123 | -0.00453 | 0.00119 | -0.00785 | -0.01808 | 0.00056 |
| 9.00000 | 0.50000 | 41.00000 | 8.00000 | -0.00223 | -0.00869 | 0.00253 | -0.01318 | -0.03264 | 0.00248 |
| 9.00000 | 1.00000 | 41.00000 | 8.00000 | -0.00374 | -0.01608 | 0.00550 | -0.01938 | -0.05386 | 0.00804 |
| 12.00000 | 0.00000 | 40.00000 | 8.00000 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.00000 |
| 12.00000 | 0.25000 | 40.00000 | 8.00000 | -0.00110 | -0.00268 | 0.00019 | -0.00379 | -0.00802 | -0.00023 |
| 12.00000 | 0.50000 | 40.00000 | 8.00000 | -0.00197 | -0.00495 | 0.00046 | -0.00613 | -0.01393 | 0.00005 |
| 12.00000 | 1.00000 | 40.00000 | 8.00000 | -0.00313 | -0.00836 | 0.00114 | -0.00859 | -0.02186 | 0.00185 |

## Fee-adjusted lifecycle sensitivity

| action | checkpoint_hour_local | threshold | opportunity_rows | dates | wins | action_rows | baseline_hold_pnl | unconditional_exit_all_pnl | policy_pnl | pnl_delta_vs_hold | pnl_delta_vs_exit_all | date_mean_delta_ci_low | date_mean_delta_ci_high | policy_roi_on_entry_cost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| sell_or_hold_direct_bid | 9 | 0.01000 | 16 | 5 | 1 | 6 | -1.01522 | -0.52519 | -0.73597 | 0.27925 | -0.21078 | 0.00914 | 0.10579 | -0.36521 |
| sell_or_hold_direct_bid | 9 | 0.02000 | 16 | 5 | 1 | 5 | -1.01522 | -0.52519 | -0.79315 | 0.22207 | -0.26796 | 0.00914 | 0.07874 | -0.39358 |
| sell_or_hold_direct_bid | 9 | 0.05000 | 16 | 5 | 1 | 3 | -1.01522 | -0.52519 | -0.93622 | 0.07900 | -0.41102 | 0.00190 | 0.03219 | -0.46457 |
| add_one_share_direct_ask | 9 | 0.01000 | 17 | 5 | 1 | 0 | 0.00000 | NA | 0.00000 | 0.00000 | NA | NA | NA | NA |
| add_one_share_direct_ask | 9 | 0.02000 | 17 | 5 | 1 | 0 | 0.00000 | NA | 0.00000 | 0.00000 | NA | NA | NA | NA |
| add_one_share_direct_ask | 9 | 0.05000 | 17 | 5 | 1 | 0 | 0.00000 | NA | 0.00000 | 0.00000 | NA | NA | NA | NA |
| sell_or_hold_direct_bid | 12 | 0.01000 | 7 | 4 | 0 | 1 | -0.83854 | -0.18598 | -0.83664 | 0.00190 | -0.65067 | 0.00000 | 0.00143 | -0.99773 |
| sell_or_hold_direct_bid | 12 | 0.02000 | 7 | 4 | 0 | 1 | -0.83854 | -0.18598 | -0.83664 | 0.00190 | -0.65067 | 0.00000 | 0.00143 | -0.99773 |
| sell_or_hold_direct_bid | 12 | 0.05000 | 7 | 4 | 0 | 0 | -0.83854 | -0.18598 | -0.83854 | 0.00000 | -0.65257 | 0.00000 | 0.00000 | -1.00000 |
| add_one_share_direct_ask | 12 | 0.01000 | 9 | 5 | 0 | 2 | 0.00000 | NA | -0.20655 | -0.20655 | NA | NA | NA | -1.00000 |
| add_one_share_direct_ask | 12 | 0.02000 | 9 | 5 | 0 | 2 | 0.00000 | NA | -0.20655 | -0.20655 | NA | NA | NA | -1.00000 |
| add_one_share_direct_ask | 12 | 0.05000 | 9 | 5 | 0 | 2 | 0.00000 | NA | -0.20655 | -0.20655 | NA | NA | NA | -1.00000 |

## 八环复核

1. hypothesis：当天 warming innovation 可能先于 exact-ticket repricing。
2. universe：固定 current HeadA 84，不按城市/source/天气类型事后换分母。
3. time：entry 与 target-day checkpoint 分层；forecast/observation 均按 canonical available clock。
4. model：只用 OOF rolling-bias 与 innovation 差；market 是同刻 anchor。
5. probability：报告 Brier/logloss paired delta 与 target-date block CI。
6. execution：direct bid/ask 与 public proxy 分层；fee、spread、top size 不混。
7. robustness：gamma 0/0.25/0.5/1 与阈值 1c/2c/5c；主值预先固定 1、2c。
8. deployment：本报告不改 live；未通过三门只保留 zero-notional telemetry。

## 数据快照与治理

```json
{
  "db": "/Users/deepsleep/projects/pm_agents/runtime/weather.db",
  "tmax_state_target_date_min": "2026-07-04",
  "tmax_state_target_date_max": "2026-07-28",
  "tmax_state_rows": 281269,
  "settlement_target_date_max": "2026-07-27",
  "current_input": "docs/analysis/2026-07/generated/heada_multisource_city_regime_v1/current_enriched.csv",
  "checkpoint_input": "docs/analysis/2026-07/generated/forecast_innovation_morning_v1/checkpoint_rows.csv",
  "oof_input": "docs/analysis/2026-07/generated/forecast_innovation_morning_v1/oof_predictions.csv",
  "price_history_endpoint": "https://clob.polymarket.com/prices-history",
  "price_history_tokens": 44,
  "price_history_rows": 16279
}
```

已识别的工程缺口：

- HeadA tail telemetry 在 clean production checkout 依赖未跟踪的 generated CSV，导致整批
  `tail_telemetry_status=error`；本轮已改为 versioned deployable calibration bundle，并加 contract test。
- canonical direct book 在 12:00 严重缺失，根因是 snapshot orderbook 全局 budget/scoping。
  正确补采不是把缺失行筛掉，而是在固定 08:45–12:15 本地 checkpoint 对 HeadA exact token +
  相邻两档优先抓 direct bid/ask/top size/depth，保持所有 candidate/blocked rows。

## 三门

- significance/probability：见主 gamma=1 paired CI。
- fee-adjusted execution：见 2c direct-book lifecycle。
- fresh frozen forward：尚未满 15 个 settled target dates，FAIL/NA。

状态只能是 `shadow_candidate` 或 `inconclusive`；不得据少量当前日期直接升 live。
