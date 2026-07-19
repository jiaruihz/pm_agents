# Hong Kong Source → Market Speed Audit v1

Status: `research_snapshot`; no live authorization

## Action

- **停止把 public HKO official-lock 当可成交 latency expression**：保留 HKO 作为 settlement truth / label，当前 public feed 到达时 `T-1 NO` 已封死。
- **Co-WIN 继续作概率特征，不作锁定事实**：它能提前约 20 分钟，但 source basis 不稳定；需要估计 `P(HKO floor cross | Co-WIN path/time/bias)` 后与 same-time market 比 residual。
- **补一条 HKO since-midnight max/min collector shadow**：当前只抓 latest 1-minute mean，可能漏掉 10-minute publication interval 内先升后回落的 1-minute peak。新 endpoint 同样每 10 分钟更新，只改善 settlement alignment，不改善速度。

## “1 minute”到底是什么

HKO `latest_1min_temperature.csv` 的 `1-minute` 指温度的 averaging window，不是每分钟发布。HKO / DATA.GOV.HK 均标注该 dataset **Every 10 Minutes**。本地 raw 也一致：distinct HKO observations 主 cadence 10 分钟，first-seen 中位约 8.1 分钟、p90 约 8.9 分钟。

Co-WIN 6087 才是实际 1-minute observations：本地 first-seen 中位 2.2 分钟、p90 2.4 分钟；但它是 HKU secondary sensor，不是 HKO Observatory settlement station。

## HKO official-lock lineage

Window: 2026-07-11..2026-07-16；grain = first new HKO floor per target date/bracket。

| layer | count | result |
|---|---:|---|
| HKO official lock candidates | 24 | 6 target dates |
| live-enabled candidates | 18 | runner 实际带 live flags，不是“完全没触发” |
| fresh book `ok` | 22 | 1 次 fetch failed；1 个早期 event 缺 market mapping |
| fresh book 有 NO ask | 6 | 六次全部 `0.999` |
| fresh book 无 NO ask | 16 | ask side 已空 |
| ask ≤ 0.93 | 0 | 无可执行 entry |
| orders / canonical fills | 0 / 0 | 没有真实下单或成交 |

所以用户看到的“从未触发”实际是：**signal 触发 24 次，但 plan/order 全部被已经封死的 fresh book 阻断**。

## 盘口速度：2026-07-17 clean episode

Co-WIN 与 HKO 都跨到 floor 30，最终 HKO realtime max 为 30.2C、floor 30。

| UTC | event | 29 NO fresh ask |
|---|---|---:|
| 08:04:00 | Co-WIN observation 30.0C | — |
| 08:06:20 | 我们 first-seen Co-WIN | — |
| 08:06:36 | first quote | 0.67 × 10 |
| 08:07:36 | +1 minute | 0.79 |
| 08:08:36 | +2 minutes | 0.93 |
| 08:09:36 | +3 minutes | 0.94 |
| 08:20:00 | HKO official observation 30.0C | — |
| 08:28:01 | 我们 first-seen HKO | official-lock 已晚约 21.7m |

这个 episode 说明两件事：

1. 市场在 public HKO first-seen 前约 19 分钟已经把 29 NO 推到 0.94，至少有人/机器人在消费更快 proxy 或路径预测；无法从盘口识别具体玩家或数据源。
2. 我们的 Co-WIN first-seen 并不算慢：第一次看到后仍有约 2 分钟从 0.67 到 0.93 的窗口。当前没有成交不是纯网络速度问题，而是策略没有把 Co-WIN 当可下单事实——这是正确的 basis 边界。

## 为什么不能直接交易 Co-WIN

2026-07-11 反例：Co-WIN 02:29 first-seen 35.1C 时，34 NO 仍约 0.51–0.54；HKO 最终只有 34.2C，34 bracket 获胜，买 34 NO 会输。完整 completed-day 对齐中，Co-WIN bias-adjusted persistent HKO confirmation 只有 14/21（66.7%），same-minute MAE 0.71C，daily max basis 为 -0.6C..+2.9C。

已有 7/11..13 first Co-WIN floor-cross + fresh-book 分母中：8 个 events、7 个有 ask；按当时 runner cap（0.92/0.94）和 10-share depth，只有 3 个可执行，2 win / 1 loss，按每 event 1 share 计 cost 2.10、payout 2.00，**fee 前已为 -0.10**。把 cap 事后放宽到 0.97 也只是 5 events、4 win / 1 loss、cost 4.024、payout 4.00，fee 前仍略负；样本太小且不构成 live policy。

## Opportunity assessment

- `HKO official T-1 NO lock`：`rejected_for_expression`。数据准确，但 public publication cadence 决定了到达时没有价格空间；更快轮询同一 CSV 只能省几十秒，解决不了上游 10-minute update。
- `Co-WIN predictive cross`：`inconclusive / feature-shadow`。物理上有 2–3 分钟 repricing window，但目前准确度与 same-time price 不支持裸买 previous NO。
- 真正需要验证的 alpha 不是“谁先报到整数”，而是连续 residual：`p_hko_cross(Co-WIN path, intraday bias, remaining heat) - executable NO cost`。必须保留未触发/错误事件和全 ladder quotes，不能只记录成功跨档。

## Funnels

- Signal funnel（event）：HKO/Co-WIN raw → distinct first-seen → new floor / probabilistic cross → first city-day expression。
- Evidence funnel（event/expression）：fresh direct book → official HKO floor/Daily Extract → executable depth → order → canonical fill。HKO 为 24 → 22 → 0 → 0 → 0；Co-WIN 旧 runner 为 8 → 7 books → 3 historical-cap executable，未下单。

## Sources and snapshot

- Local raw: `high_frequency_observations.jsonl`, `hko_official_tminus1_no_live/events.jsonl`, `fast_source_prev_no_trial/events.jsonl`, `fast_source_stale_book/quote_snapshots.jsonl` under `/Volumes/jrs/weather_data_feed_service_runtime/output/`.
- HKO official open-data catalog: `https://www.hko.gov.hk/en/abouthko/opendata_intro.htm`.
- Latest 1-minute mean dataset: `https://data.weather.gov.hk/weatherAPI/hko_data/regional-weather/latest_1min_temperature.csv` (official update frequency: every 10 minutes).
- Since-midnight max/min dataset: `https://data.gov.hk/en-data/dataset/hk-hko-rss-max-and-min-air-temp-since-midnight` (official update frequency: every 10 minutes).

## Contract

significance=NA; baseline=same-time fresh market; forward=FAIL; conclusion=official-lock no entry edge, Co-WIN feature-shadow only
