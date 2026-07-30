# 天气盘口微结构专题 v1：城市 × 当地时间 × 天气路径

## 数据快照

- 盘口窗口：2026-07-15..2026-07-29 UTC。
- raw full-ladder orderbook files：611。
- raw token-book rows：984,296。
- 标准 grain：47,508 个 `event_slug × archived snapshot` states。
- 覆盖：754 events、47 城市、17 个 target dates。
- target-day states：19,853；target-day observation coverage 62.53%。
- 2026-07-18 后 target-day observation coverage 75.45%。
- 可对齐天气状态的日间主样本：4,179 states、40 城市、11 个 target dates。
- JRS：
  `/Volumes/jrs/pm_agents/research/weather_book_microstructure_atlas/v1/`
  `snapshot=20260729T161422Z`
- 状态：`research / descriptive atlas / no live rule`。

## 目标与边界

目标不是从城市切片寻找最高 ROI，而是建立同分母执行路由：

```text
同一 PIT signal / p_win
→ taker_now 的 full-depth + fee 成本
→ maker_wait 的 queue / fill / adverse-selection 成本
→ incomplete basket / legging risk
→ maker / taker / skip
```

v1 使用所有抓到的 active complete ladders，不限于本账户或外部钱包交易过的市场。
盘口是严格 archived PIT；天气 observation 只按 `fetched_at_utc <= book_ts` 对齐。

当前 archive 有真实 bid/ask、top size、full depth 与 snapshot-to-snapshot repricing；
没有 market-wide trade prints。 displayed depth 不是成交量，v1 不用盘口深度冒充
volume，也不推断 maker fill。

## 首版事实

### 当地时间

| local time | favorite spread 中位 | favorite top ask depth | favorite 切换率 |
|---|---:|---:|---:|
| 00–06 | 1¢ | $29.51 | 3.68% |
| 06–10 | 2¢ | $23.06 | 4.49% |
| 10–12 | 2¢ | $16.31 | 6.70% |
| 12–14 | 2¢ | $18.60 | 9.05% |
| 14–16 | 2¢ | $30.32 | 8.35% |
| 16–18 | 1¢ | $44.64 | 6.71% |
| 18–24 | 1¢ | $43.85 | 3.59% |

中午不是单纯“流动性差”：它同时是 spread 较宽、favorite 更易切换、可见中心深度
较薄的主动重定价窗口。直接 taker 成本高；maker 有 spread 收益，但 queue 与
adverse selection 风险也最高。16 点后盘口普遍更深、更紧、更稳定，taker
完成腿的成本相对更可控。

### 天气状态（target day 10–18，当时可见 observation）

| weather state | states | favorite spread | favorite top ask depth | favorite 切换率 | favorite price 变化中位 |
|---|---:|---:|---:|---:|---:|
| fresh runway | 1,597 | 4.0¢ | $8.00 | 14.28% | 3.5¢ |
| warming | 190 | 4.0¢ | $6.69 | 14.21% | 5.0¢ |
| plateau | 1,033 | 4.0¢ | $9.57 | 17.62% | 4.5¢ |
| pullback | 1,359 | 1.8¢ | $35.15 | 10.82% | 2.1¢ |

因此 execution 不能只按城市配置。fresh-runway / warming / plateau 下：

- crossing spread 最贵；
- top depth 最薄；
- favorite repricing 最快；
- maker 可能节省最多，但也最容易成交在坏方向。

pullback 下 book 更深、更紧，适合完成缺腿；但这只是执行质量，不代表该天气表达
本身有正 alpha。

### 城市例子（target day 10–18，至少 14–15 dates）

| city | favorite spread | favorite top ask depth | favorite 切换率 | favorite price 变化中位 | 描述 |
|---|---:|---:|---:|---:|---|
| Chengdu | 4.0¢ | $7.72 | 21.21% | 5.5¢ | 最典型的薄、宽、快重定价；不宜盲目 taker，maker 必须按 observation epoch 撤改 |
| Shanghai | 2.0¢ | $23.69 | 14.36% | 4.75¢ | 容量好于成都，但中心档迁移仍快；适合 target-share basket，不适合单腿追价 |
| Guangzhou | 2.0¢ | $36.69 | 12.31% | 3.5¢ | 中心腿容量较好，taker completion 相对可行 |
| Busan | 3.0¢ | $11.85 | 18.32% | 3.5¢ | 日间重定价高，不能因全日深度看起来好就忽略午间 legging risk |
| Madrid | 3.0¢ | $14.11 | 6.55% | 2.5¢ | favorite 相对稳定；是否有 alpha 仍需同信号概率比较 |

这些是微结构描述，不是城市 allowlist，也没有做 ROI 排名。

## 对 43cb 风格策略的意义

上海案例的机制不是静态 ask underround，而是：

```text
天气路径改变
→ 35/36 中心概率迁移
→ 贵腿 maker 累积
→ 便宜尾腿 taker
→ target shares 动态补齐
```

atlas 支持这个机制在市场层面是可发生的：日间 warming/plateau 的 favorite spread
约 4¢、切换率 14–18%、top ask depth只有约 `$7–10`。这提供 maker 收中心腿和
跨时间补篮子的空间，也同时证明不能把最终完整篮子当作可原子复制。

## 下一阶段

1. **P0 已完成：标准化 microstructure frame**
   - 事件快照、当地时间、完整 ladder、spread/depth、favorite migration、
     as-of weather state 已写入 JRS。
2. **P1：补真实成交量**
   - forward 采集 market-wide trade prints / last-trade stream；
   - 保存 condition、token、price、shares、aggressor side、event time；
   - Gamma cumulative volume 只作一致性校验，不替代逐笔 prints。
3. **P2：observation epoch repricing**
   - 每次新 observation 后固定 1/5/15/30m 窗；
   - 记录 center-ladder mid、spread、depth、favorite change 与成交 prints；
   - Atlanta 2026-07-17 terminal false cross 作为 source negative control。
4. **P3：同 signal execution A/B**
   - `taker_now`：真实 full-depth VWAP + official fee；
   - `maker_wait`：queue-ahead、level depletion/trade-through、partial fill、
     cancel/reprice、1/5/15m markout；
   - future touch 不算 maker fill，未成交 opportunity 仍留在分母。
5. **P4：basket completion shadow**
   - common-base / modal-overweight 分账；
   - 每腿 target shares、完成率、完成时间、fee-inclusive basket cost；
   - incomplete basket 的逐 outcome PnL 与 legging loss；
   - 输出 `maker / taker / skip`，不直接改 live。

## 数据缺口

- full-ladder archive 只有 15 天，独立 target dates 仍不足以定城市执行 policy。
- market-wide trade prints 未归档，真实 volume、aggressor flow、maker fill
  probability 暂不可估。
- orderbook 是分钟级 snapshots，不是连续 L2 feed；短时 queue 变化会漏。
- target-day observation coverage 已达 62.53%，7/18 后为 75.45%，但
  Seoul/HongKong/TelAviv 等城市键/source coverage 仍缺，需要统一 city identity。
- 本专题只覆盖 execution quality；任何 alpha 结论仍须同 rows 的
  `p_win-market` proper score 与 fee-adjusted forward。

## 产物

- 脚本：
  `scripts/analysis/execution_quality/research_weather_book_microstructure_atlas_v1.py`
- JRS：
  `/Volumes/jrs/pm_agents/research/weather_book_microstructure_atlas/v1/`
  `snapshot=20260729T161422Z`
- 文件：
  `state_rows.csv.gz`、`city_hour_summary.csv`、
  `weather_state_summary.csv`、`city_weather_summary.csv`、`coverage.json`
