# Market-Implied Tail Residual Incremental Review v1

> observed_at_utc: 2026-08-09T06:32Z
> verdict: `inconclusive`; zero-notional shadow unchanged; no live change.

## 结论

没有找到可以直接加进 hotter-tail selector 的稳定模式。新增结算窗进一步反证 broad cheap YES：
7/24..7/28 的 ask<=20c exact-bracket YES 为 23,674 行 / 5 target dates / 47 城，胜率 3.09%，
avg ask 4.48c，Weather taker fee 后 ROI -34.03%，target-date bootstrap CI [-45.76%, -24.68%]。

唯一值得继续的机制是完整 ladder 的局部凹陷：`cold_1 + discount` 在旧窗 154 行 / 9 dates 为
+9.42%（CI 跨 0），新增窗 56 行 / 5 dates 为 +56.70%（CI [+9.79%, +103.83%]）。方向一致，
但它是本轮看过多个切片后识别的 post-hoc 候选，而且落在盘口众数下一格，不是 hotter-tail。
它应作为独立 market-structure residual 连续特征进入下一轮 clean forward，不应成为 HeadA/tail hard gate。

## 数据快照

- canonical DB: `/Volumes/jrs/pm_agents/runtime/weather.db`; manifest `db_route=healthy`，无 manifest finding。
- canonical build: `fact_signal_candidates` built through `2026-08-08T08:57:10Z`。
- settlement: canonical 只到 target date `2026-08-07`。
- ladder canonical: target dates `2026-07-04..2026-07-28`；本轮固定评估窗为 `2026-07-11..2026-07-28`。
- shadow raw: `2026-08-07T17:43:27Z..2026-08-09T06:03:19Z`，19,470 rows / 4 target dates / 47 城；
  12,996 direct two-sided、8,019 full-ladder>=80%、11,416 有 prior-checkpoint price path。
- shadow 的 8/08 以后尚无完整 settled forward date，因此本轮没有 untouched forward 成绩。
- 全局 controller health 为 `CRITICAL`。06:44 UTC 的 `market_books` 批次曾 degraded（1,738 tokens 中
  仅 238 books 成功）；06:51 下一批恢复 `status=ok`、418/418 books 成功，但只发现 19 events，另有
  81 个 `event_unavailable` discovery failures。因此当前 fresh telemetry 仍须按 quote/event coverage 分层，
  不能把 heartbeat 正常等同于完整 ladder universe。
- canonical refresh 的 LaunchAgent 最后一次退出为 1：CLOB order-status 请求超时，三次 authenticated fill sync
  未完整成功。DB route 与本轮已固定历史 build 健康，这不改变 7/11..7/28 replay 数字，但生产全链不能称为正常。

## 固定分母

grain 为每个 `city-target_date-exact_bracket-local_2h_bin` 的首个 direct executable YES quote；
entry 为 direct YES ask，持有到 exact-bracket settlement，fee=`0.05*p*(1-p)`。同 snapshot 至少 80% rung
有 direct two-sided quote时，才计算 normalized ladder geometry。

signal funnel: complete PIT ladder -> fixed 2h checkpoint -> mechanism diagnostic slice。

evidence funnel: direct quote -> settled exact bracket -> executable taker expression；actual fill=0。

## 宽分母结果

| slice | rows | dates | cities | win | avg ask | fee ROI | date CI | losing days | <=-50% days | max daily PnL* |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 7/11..7/23 all exact YES | 57,600 | 13 | 47 | 12.18% | 13.20c | -10.55% | [-10.91%, -10.01%] | 13 | 0 | n/a |
| 7/24..7/28 all exact YES | 32,301 | 5 | 47 | 12.22% | 13.36c | -11.36% | [-11.98%, -10.57%] | 5 | 0 | -141.84 |
| 7/11..7/23 ask<=20c | 42,560 | 13 | 47 | 3.56% | 4.27c | -20.03% | [-30.27%, -8.17%] | 13 | n/a | n/a |
| 7/24..7/28 ask<=20c | 23,674 | 5 | 47 | 3.09% | 4.48c | -34.03% | [-45.76%, -24.68%] | 5 | 1 | -122.11 |

`*` PnL 是每个机会机械买 1 share 的研究账本，不是实际组合美元损益。

## 模式复核

| hypothesis | 7/11..7/23 | 7/24..7/28 | 判断 |
|---|---|---|---|
| D-1 late，mid 先跌>=2c 后买 YES | -23.31%，CI 全负 | +4.79%，CI 跨 0 | 前后反号，不成立 |
| D0 00-06，mid 先跌>=2c 后买 YES | -12.28%，CI 全负 | +6.57%，CI 跨 0 | 前后反号，不成立 |
| hot_1 + local discount | -1.91%，CI 很宽 | +76.06%，CI 很宽 | hotter 方向不稳定 |
| cold_1 + local discount | +9.42%，CI 跨 0 | +56.70%，CI 正 | 机制候选，但 post-hoc、仅 5 新 dates |
| D0 18-24 高价 rising/mode | 约 +1%，CI 窄 | 约 +1%，CI 窄 | 近结算尘埃 carry，不是 tail lottery，容量/收益太小 |

本轮查看了 lifecycle、ask bucket、mode distance、price path、ladder kink 多组 cells，未做多重检验校正；
因此即使 `cold_1 + discount` 新窗 CI 为正，也不能称为 confirmed 或 clean forward。

## 可优化方向

不要加 `cold_1 AND discount` hard gate。下一版只增加连续的局部 ladder residual：

```text
kink_log_residual_i = log(mid_i)
                      - 0.5 * (log(mid_{i-1}) + log(mid_{i+1}))
```

它衡量某 exact bracket 相对相邻两档是否异常凹陷，并与 mode distance、lifecycle、hotter-tail mass、
spread/depth、prior-checkpoint change 一起进入 market-logit offset。固定同 rows 比较：

1. baseline = normalized raw market midpoint；
2. candidate = expanding-date OOF market-only residual；
3. probability primary = Brier/logloss delta；
4. trade secondary = positive residual 对应 exact-bracket YES 的 fee-adjusted ROI；
5. clean forward 从 2026-08-08 shadow deployment 后的完整 settled dates 开始，不再读取后调系数。

若该特征最终有效，alpha 归类为 ladder microstructure / market residual，而不是 forecast-bias 或 hotter-tail lottery。

## Gate

```text
significance: broad hotter-tail FAIL; cold_1-discount diagnostic only
baseline:     market-only candidate尚未完成 OOF proper-score A/B
forward:      NA（8/08+ 尚无完整 settled date）
conclusion:   inconclusive
action:       继续 zero-notional；不改 live；不增加 hard selector
```

Artifacts:

- `/Volumes/jrs-archive/pm_agents/research/artifact_store/market_implied_tail_residual_v1/baseline_20260711_23_recheck/`
- `/Volumes/jrs-archive/pm_agents/research/artifact_store/market_implied_tail_residual_v1/incremental_20260724_28c/`
