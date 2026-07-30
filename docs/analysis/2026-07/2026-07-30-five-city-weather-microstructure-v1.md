# 五城天气盘口微结构 v1：Tokyo / Busan / Seoul / Amsterdam / Helsinki

## 结论

这五城值得研究，但暂时不能把“maker 空间大”直接翻译成 maker 有正 EV。

- Tokyo、Busan、Seoul 的主动重定价窗口主要在当地 `10–14`；Amsterdam、
  Helsinki 更偏 `12–16`。
- 亚洲三城到 `16–18` 后通常明显变深、变紧，更适合 taker 完成缺腿；欧洲两城
  午后仍可能保持较宽 spread。
- maker 相对立即 taker 的可见报价改善中位约 `1.9–2.9c`，但“下一张 archived
  snapshot 的 ask 已穿过 maker 价”的样本，后续 markout 中位为
  `-3.1c..-7.6c`，81.6%–100% 为负。这不是实际 fill rate，但说明静态挂单会
  集中暴露于 adverse selection。
- complete ladder 的静态 all-YES ask underround 只占 `1.4%–2.9%` snapshots，
  常见边际仅 `0.4–3.1c`，且各腿不能原子成交、容量也未验证；不是稳定套利底座。
- Busan/Seoul 的 AMOS 不能直接用于排除档位。Busan 2026-07-27 AMOS 最高
  `35.7°C`，按整数会指向 36，但市场最终把 35 档定到接近 1；Seoul
  2026-07-29 也出现 AMOS `30.5°C` 对市场 30 档。快源适合做 path evidence，
  未经 settlement-basis 校准不能作为“这条腿已不可能”的证明。

当前最值得借鉴外部多腿账户的不是“永远挂 maker”，而是：

```text
完整 ladder / common-base 风险
→ 按城市和当地时段判断盘口状态
→ 稳定窗口被动收中心腿
→ 新 source epoch 到达立即撤改
→ 晚段 spread/depth 改善后用 taker 补齐
→ source 与 settlement basis 冲突时不删腿、不追所谓套利
```

## 数据与 grain

- 盘口：2026-07-15..29，611 个 full-ladder archive files。
- 五城 raw token-book rows：110,296。
- `event_slug × archived snapshot` states：5,321。
- 主样本：target day 当地 `06–18`、完整 ladder，共 1,024 states；不完整 ladder
  324 states 已从主汇总剔除。
- 每城主样本覆盖 12–13 个 target dates。
- 快源 event study：621 states；Tokyo/JMA、Busan/AMOS、Seoul/AMOS、
  Helsinki/FMI 各 8 dates，Amsterdam/KNMI 只有 2 dates。
- 所有 bracket 在同一 event snapshot 合并后计算；没有把同城同日的多档当成
  多个独立表达。

盘口是 archived PIT；observation 只允许 `first_seen_at <= book_ts`。displayed
depth 不叫 volume；后续 quote crossing 不叫 maker fill。

## 五城全貌

以下 spread、price move、ladder TV 均为概率点的百分数；depth 是 favorite top
ask 的可见美元 notional。

| city | states | favorite spread | favorite depth | favorite 切换率 | full-ladder TV | 同档价格变化 | maker quote-cross proxy |
|---|---:|---:|---:|---:|---:|---:|---:|
| Amsterdam | 178 | 3.0c | $9.99 | 5.1% | 4.2% | 2.0c | 18.0% |
| Busan | 223 | 2.0c | $11.85 | 13.5% | 4.5% | 1.5c | 17.0% |
| Helsinki | 183 | 3.0c | $10.80 | 8.7% | 4.1% | 2.0c | 14.2% |
| Seoul | 220 | 2.0c | $14.58 | 15.9% | 5.8% | 2.5c | 23.2% |
| Tokyo | 220 | 2.0c | $10.10 | 11.8% | 4.8% | 1.8c | 15.9% |

Seoul 是五城里全日 favorite migration 与 quote-cross proxy 最高的；它不是
“盘口更好做”，而是对 source/path 更新更敏感。

### Tokyo

- `12–14`：spread 4c、depth $7.61、favorite 切换 31.3%、ladder TV 14.1%、
  同档变化 11.25c。这里不适合静态 maker，也不适合看到一条腿便宜就追。
- `16–18`：spread 0.4c、depth $33.93、favorite 切换 5.1%、同档变化 0.3c。
  更适合完成篮子的 taker leg。
- JMA AMeDAS 是 10 分钟快源；8-date 快源样本可以用于撤改时钟，但还不足以形成
  城市 live gate。

### Busan

- `12–14`：spread 7.5c、favorite 切换 35.3%、ladder TV 13.1%、同档变化
  8c，是五城最典型的“maker room 最大、legging/toxicity 也最大”的窗口。
- `16–18`：spread 0.4c、depth $73.95、同档变化 0.55c，适合完成缺腿。
- AMOS 分钟级 path 很有价值，但 runway decimal maximum 与 WU settlement
  integer bracket 存在 basis；不能用 `round(AMOS max)` 直接删掉 35 或 36 档。

### Seoul

- `10–12`：spread 3c、depth $6.69、favorite 切换 24.3%、quote-cross proxy
  40.5%。
- `12–14`：ladder TV 13.25%、同档变化 8c；source-implied feasible strip
  经常 `<1`，但主要是 AMOS 30.5→31 与市场 30 档的 basis 假象，不能称套利。
- `16–18`：spread 1c、depth $52.92、favorite 切换 4.9%，明显更适合 taker
  completion。
- 仁川确实最适合做 source-event execution 研究，但必须同时保存
  AMOS、routine METAR、WU/settlement-facing value，不能只保存一个 running max。

### Amsterdam

- `06–10`：spread 2c、depth $24.15、同档变化 1c，相对稳定。
- `12–16`：spread 6–6.5c、depth约 $7–9、同档变化 2.5–4.75c；maker savings
  可见，但需要 observation-epoch cancel/reprice。
- `16–18` 同档变化中位 9.5c，但只有 12 states，不能据此下规则。
- KNMI first-seen 目前只有 2 个 target dates，与其他四城不能按相同置信度比较。

### Helsinki

- `06–10`：spread 2c、depth $20.87、同档变化 0.5c，是较干净的执行窗口。
- `14–16`：spread 5c、depth $7.14、favorite 切换 20.8%、同档变化 7.5c。
  这里的 wide spread 不是免费 maker 收益。
- `16–18` 仍有 5c spread，且只有 11 states；不能照搬亚洲城市的 late-day
  taker rule。

## maker 质量：可见收益与 toxicity

以 favorite YES 为例，假设在 spread 内比 best bid 提高 1 tick；若 spread 只有
1 tick则排 best bid。这个 hypothetical quote 相对立即 taker 的价格改善中位为：

| city | maker 改善 | next-snapshot quote-cross | cross 后 markout 中位 | cross 后负 markout |
|---|---:|---:|---:|---:|
| Amsterdam | 2.9c | 18.0% | -3.1c | 93.8% |
| Busan | 1.9c | 17.0% | -5.1c | 81.6% |
| Helsinki | 2.9c | 14.2% | -7.6c | 96.2% |
| Seoul | 1.9c | 23.2% | -3.6c | 100.0% |
| Tokyo | 1.9c | 15.9% | -4.85c | 91.4% |

这个表不能估 maker EV：

- archived snapshot 间隔较宽；
- 看不到 queue ahead、cancel、部分成交和未成交分母；
- ask 穿过报价只是一类明显 toxic 的 observable path，不等于所有 maker fills。

它能否定的只是“spread 大就一直挂着一定赚”。正确实验必须在同一 signal 分母记录
真实 order lifecycle、market-wide prints、queue depletion 和 1/5/15m markout。

## 静态 underround 与多腿组合

完整 ladder 的 all-YES top asks 合计 `<1` 的 snapshot 比例：

| city | rate | underround edge 中位 |
|---|---:|---:|
| Amsterdam | 1.69% | 1.9c |
| Busan | 2.94% | 3.05c |
| Helsinki | 2.22% | 2.1c |
| Seoul | 1.44% | 0.4c |
| Tokyo | 1.52% | 1.4c |

这些只是同时刻 top-of-book 的静态机会：

- 每条腿的可成交 shares 不同；
- 抓取和成交不是原子的；
- 最薄中心腿常只有几美元可见 depth；
- 盘口变化期间很容易先成交坏腿、好腿不成交。

所以它可以作为 basket scanner，但不能按 `1-sum(asks)` 直接记收益。外部账户真正
可借鉴的仍是跨时间 maker 收中心腿、taker 补尾腿和 target-share reconcile。

## 初步 execution route

这是 research routing，不是 live rule：

| 状态 | route | 原因 |
|---|---|---|
| 亚洲三城 `16–18`、spread≤1c、中心 depth 充足、无新 source conflict | `taker completion candidate` | spread/depth/同档变化同时改善 |
| Tokyo/Busan/Seoul `10–14` 或 Amsterdam/Helsinki `12–16`，但 signal edge 足以覆盖报价与 adverse-selection buffer | `short-lived maker candidate` | 有 spread room，但必须按 source epoch 撤改 |
| 新快源刚到、favorite/center 正迁移、spread 宽且 source 与 settlement-facing observation 未确认 | `skip / cancel / reprice` | 最容易把 maker 成交集中在坏方向 |
| source-implied strip `<1` 但 basis 未通过 | `skip as arbitrage` | 这是 source disagreement，不是无风险组合 |

当前不据此改变 live。下一阶段的关键不是再切更多 city-hour，而是补齐真实 prints 和
order lifecycle，再对相同 `signal_id × target shares` 比较：

```text
taker_now full-depth VWAP
vs maker_wait actual fill + markout + incomplete basket
vs skip
```

## 产物

- 脚本：
  - `scripts/analysis/execution_quality/research_five_city_weather_microstructure_v1.py`
  - `scripts/analysis/execution_quality/research_five_city_fast_source_repricing_v1.py`
- JRS：
  - `/Volumes/jrs/weather_data_feed_service_runtime/research/`
    `five_city_weather_microstructure/v1/snapshot=20260730T120000Z`
- 核心文件：
  - `state_rows.csv.gz`
  - `city_summary.csv`
  - `city_hour_summary.csv`
  - `city_weather_summary.csv`
  - `fast_source_state_rows.csv.gz`
  - `fast_source_city_weather_summary.csv`
  - `coverage.json`
