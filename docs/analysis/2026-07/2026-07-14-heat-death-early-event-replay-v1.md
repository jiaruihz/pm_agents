# Heat-Death Early Event Replay v1

Status: current-reference
Verdict: `inconclusive_zero_notional_only`

## 结论

**上一版“最终只有 5 笔”的说法作废。5 是盘口档案缺口再叠加任意价格带后的可计算行数，不是策略信号数。**
这次审计把 signal funnel 与 quote/settlement evidence funnel 分开，价格只作为连续 EV 输入，不再作为 eligibility hard gate。
CLOB 分钟 price history 已补回 110/111 个已结算 leg：base current YES fee ROI +1.0%，加 2c ask premium 后 -0.5%；base d1 NO 原价即 -0.3%。support>=2 两边分别 -0.7% / -1.7%，没有显示更强 edge。
Busan-like 0.80-0.90 只是事后诊断切片：base current YES 7 行 ROI -0.6%，d1 NO 9 行 -9.1%；support>=2 各只有 2 行，不能据此定策略阈值。
历史事件档案只覆盖 2026-07-07..2026-07-13 的已结算日，因此仍不足以确认策略；forward runner 继续是 zero-notional。

## Signal funnel（这里才是策略漏斗）

- unique source reports: 10176
- local 13:00-17:00 event rows: 1761
- + decline >= 0.5: 632
- + running high age >= 60m: 485
- + flat/cooling path: 432
- market-aligned event rows: 410
- first base signal city-days: 130
- first support>=2 diagnostic city-days: 67

## Evidence coverage（不是策略筛选）

- base direct quote coverage: current YES 19/130; d1 NO 18/130
- support>=2 direct quote coverage: current YES 8/67; d1 NO 7/67
- settled executable rows: base 37; support>=2 15
- settled indicative rows (not executable): base 222; support>=2 110

## 为什么 executable coverage 会塌缩

| Cohort | Expression | Signals | Indicative price | First-snapshot ask | Ask within 30m | Settled | Ask+settled | Indicative but no ask | First-snapshot book status |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| base | current_yes | 130 | 130 | 16 | 19 | 111 | 19 | 111 | `{"ok": 16, "orderbook_budget_exhausted": 113, "orderbook_scope_skipped": 1}` |
| base | d1_no | 130 | 130 | 16 | 18 | 111 | 18 | 112 | `{"ok": 16, "orderbook_budget_exhausted": 113, "orderbook_scope_skipped": 1}` |
| strong_partial | current_yes | 67 | 67 | 7 | 8 | 55 | 8 | 59 | `{"ok": 8, "orderbook_budget_exhausted": 59}` |
| strong_partial | d1_no | 67 | 67 | 7 | 7 | 55 | 7 | 60 | `{"ok": 8, "orderbook_budget_exhausted": 59}` |

历史 paper snapshot 不是全量盘口录制：默认每 10 分钟生成一次 snapshot，但 orderbook enrichment 使用 `strategy_live` 紧凑 scope、60 秒总预算和单 worker。天气状态与 indicative market price 大多保留，真实 YES/NO ask 则大量标为 `orderbook_budget_exhausted` 或 `orderbook_scope_skipped`。因此缺的是可执行价格证据，不是物理 signal 或 settlement 全部缺失。

这批数据仍有意义：indicative+settled 层可检验 selector 的方向、胜率和粗略定价残差；只有 direct ask+settled 层才能声称 executable ROI。前者不能冒充后者。

## CLOB minute price PIT proxy（同分母补回）

`/prices-history` 仍可取回 closed market 的分钟价格，因此可以在首次识别后取第一个 PIT price。它恢复了价格路径分母，但不含当时 ask、spread、size/depth；下面是 price proxy ROI，不是 guaranteed fill ROI。

| Cohort | Expression | Rows | Dates | Cities | Win rate | Avg proxy | Fee ROI | Date-bootstrap 95% CI |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| base | current_yes | 110 | 6 | 36 | +94.5% | 0.934 | +1.0% | [-2.2%, +3.9%] |
| base | d1_no | 110 | 6 | 36 | +95.5% | 0.956 | -0.3% | [-3.5%, +2.7%] |
| strong_partial | current_yes | 55 | 6 | 21 | +94.5% | 0.950 | -0.7% | [-5.9%, +2.8%] |
| strong_partial | d1_no | 55 | 6 | 21 | +94.5% | 0.960 | -1.7% | [-7.3%, +2.2%] |

### 固定加价敏感性（代理 ask = history price + 1/2/3c）

| Add-on | Cohort | Expression | Rows | Avg assumed ask | Fee ROI |
|---:|---|---|---:|---:|---:|
| +0.00 | base | current_yes | 110 | 0.934 | +1.0% |
| +0.00 | base | d1_no | 110 | 0.956 | -0.3% |
| +0.00 | strong_partial | current_yes | 55 | 0.950 | -0.7% |
| +0.00 | strong_partial | d1_no | 55 | 0.960 | -1.7% |
| +0.01 | base | current_yes | 110 | 0.943 | +0.1% |
| +0.01 | base | d1_no | 110 | 0.964 | -1.1% |
| +0.01 | strong_partial | current_yes | 55 | 0.958 | -1.5% |
| +0.01 | strong_partial | d1_no | 55 | 0.968 | -2.5% |
| +0.02 | base | current_yes | 110 | 0.949 | -0.5% |
| +0.02 | base | d1_no | 110 | 0.969 | -1.6% |
| +0.02 | strong_partial | current_yes | 55 | 0.965 | -2.1% |
| +0.02 | strong_partial | d1_no | 55 | 0.974 | -3.0% |
| +0.03 | base | current_yes | 110 | 0.953 | -0.9% |
| +0.03 | base | d1_no | 110 | 0.973 | -2.0% |
| +0.03 | strong_partial | current_yes | 55 | 0.969 | -2.6% |
| +0.03 | strong_partial | d1_no | 55 | 0.979 | -3.5% |

### 与已留存 direct ask 的重合校验

| Expression | Overlap | Mean ask-history | Median | P90 |
|---|---:|---:|---:|---:|
| current_yes | 19 | +0.018 | +0.006 | +0.023 |
| d1_no | 18 | +0.007 | +0.005 | +0.020 |

上表混合了 signal 后到 direct book 出现前的价格移动，不能纯解释为 spread。第一张 snapshot 同时有 ask 和 indicative price 的 16 个 leg 校验如下：

| Expression | Same-snapshot overlap | Mean ask-indicative | Median | P90 |
|---|---:|---:|---:|---:|
| current_yes | 16 | +0.024 | +0.008 | +0.025 |
| d1_no | 16 | +0.009 | +0.004 | +0.025 |

### Busan-like 0.80-0.90 价格形态（仅诊断，不作为门槛）

| Cohort | Expression | Rows | Dates | Cities | Win rate | Avg proxy | Fee ROI | Date-bootstrap 95% CI |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| base_anchor_price_band_0.80_0.90 | current_yes | 7 | 5 | 6 | +85.7% | 0.856 | -0.6% | [-52.2%, +17.6%] |
| base_anchor_price_band_0.80_0.90 | d1_no | 9 | 5 | 8 | +77.8% | 0.849 | -9.1% | [-34.0%, +15.3%] |
| strong_anchor_price_band_0.80_0.90 | current_yes | 2 | 2 | 2 | +50.0% | 0.854 | -41.9% | [-100.0%, +11.4%] |
| strong_anchor_price_band_0.80_0.90 | d1_no | 2 | 2 | 2 | +50.0% | 0.818 | -39.4% | [-100.0%, +19.5%] |

## Direct executable ask result

| Cohort | Expression | Rows | Dates | Cities | Win rate | Avg ask | Fee ROI | Date-bootstrap 95% CI |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| base | current_yes | 19 | 6 | 8 | +100.0% | 0.974 | +2.5% | [+0.9%, +6.8%] |
| base | d1_no | 18 | 6 | 7 | +100.0% | 0.981 | +1.9% | [+0.7%, +4.6%] |
| strong_partial | current_yes | 8 | 6 | 4 | +100.0% | 0.955 | +4.5% | [+1.0%, +12.5%] |
| strong_partial | d1_no | 7 | 6 | 3 | +100.0% | 0.968 | +3.1% | [+0.7%, +8.1%] |

## Broad indicative-price diagnostic（不可当成成交回测）

| Cohort | Expression | Rows | Dates | Cities | Win rate | Avg price | Fee ROI | Date-bootstrap 95% CI |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| base | current_yes | 111 | 6 | 36 | +93.7% | 0.924 | +1.2% | [-1.7%, +3.8%] |
| base | d1_no | 111 | 6 | 36 | +94.6% | 0.942 | +0.2% | [-3.0%, +3.3%] |
| strong_partial | current_yes | 55 | 6 | 21 | +94.5% | 0.950 | -0.6% | [-5.0%, +2.5%] |
| strong_partial | d1_no | 55 | 6 | 21 | +94.5% | 0.960 | -1.7% | [-6.9%, +2.0%] |

## support count diagnostic（base cohort，非门槛）

| Support | Expression | Rows | Dates | Win rate | Avg price | Fee ROI | Date-bootstrap 95% CI |
|---|---|---:|---:|---:|---:|---:|---:|
| 0 | current_yes | 32 | 6 | +93.8% | 0.901 | +3.9% | [+2.5%, +5.1%] |
| 0 | d1_no | 32 | 6 | +96.9% | 0.938 | +3.0% | [+1.3%, +4.7%] |
| 1 | current_yes | 31 | 6 | +93.5% | 0.916 | +1.9% | [-6.1%, +6.6%] |
| 1 | d1_no | 31 | 6 | +93.5% | 0.922 | +1.2% | [-7.0%, +5.3%] |
| 2 | current_yes | 26 | 6 | +88.5% | 0.919 | -4.0% | [-11.4%, +3.3%] |
| 2 | d1_no | 26 | 6 | +88.5% | 0.938 | -6.0% | [-14.5%, +2.7%] |
| 3+ | current_yes | 22 | 5 | +100.0% | 0.975 | +2.4% | [+1.8%, +3.1%] |
| 3+ | d1_no | 22 | 5 | +100.0% | 0.979 | +2.0% | [+1.4%, +2.7%] |

## Busan executed anchor case

Busan 2026-07-14 在 2026-07-14T04:08:34Z 首次 strong-partial：30 YES ask=0.84，31 NO ask=0.89。该日未纳入上面的已结算 ROI。

用户确认这是实际人工成交的 anchor trade；它定义了本研究要寻找的 early-dislocation 形态，不是普通 sanity case。

- selector 对齐：replay 在 2026-07-14T04:08:34Z 首次选中 strong，价格正是 `30 YES=0.84 / 31 NO=0.89`。
- 当时 runner 尚未开发、人工成交未进 canonical，都不是回测缺陷；回测本来就是事后重建。
- 真正缺口是历史 first-signal direct ask 覆盖稀疏，能进入 executable 统计的行偏向市场已经 repriced 的晚期高价盘口。
- 分钟 price proxy 已把同分母历史方向补回，但结果没有显示稳定正 edge；由于缺当时 ask/depth，`0.84/0.89` 这类早期错价交易头的 executable ROI 仍未充分验证。

## Feature coverage boundary

可 PIT 重建：METAR/SPECI 雨、云层、风向/风速、温度路径、forecast peak clock、首个后续直接盘口。历史仍缺 remaining-3h forecast weather 与带坐标 solar geometry，因此这里叫 `strong_partial`，不能假装是完整 weather_state_v2 回测。

## Three gates

```text
significance=FAIL_LOW_SAMPLE
baseline=NA_short_event_archive
forward=FAIL_THIN
conclusion=inconclusive_zero_notional_only
```

完整逐事件行见 `generated/heat_death_early_event_replay_v1/`。
