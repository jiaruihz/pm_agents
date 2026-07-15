# Heat-Death Early Event Replay v1

Status: current-reference
Verdict: `inconclusive_tiny_live_probe_only`

## 结论

**上一版“最终只有 5 笔”的说法作废。5 是盘口档案缺口再叠加任意价格带后的可计算行数，不是策略信号数。**
这次审计把 signal funnel 与 quote/settlement evidence funnel 分开，价格只作为连续 EV 输入，不再作为 eligibility hard gate。
CLOB 分钟 price history 补回 151/152 个已结算 leg：base current YES fee ROI +0.3%，加 2c ask premium 后 -1.1%；base d1 NO 原价即 -1.3%。support>=2 两边分别 +0.6% / -1.4%，没有显示更强 edge。
Busan-like 0.80-0.90 只是事后诊断切片：base current YES 8 行 ROI -13.2%，d1 NO 10 行 -18.4%；support>=2 各只有 2 行，不能据此定策略阈值。
事件输入已覆盖 2026-07-07..2026-07-15，但 ROI 只使用 8 个已结算日（截至 2026-07-14）；H2 只维持 fixed-10-share tiny-live probe，不具备 size-up 证据。Busan 2026-07-14 anchor city-day 已按预注册原则从全部证据层剔除（定义形态的 in-sample 交易）。

## Signal funnel（这里才是策略漏斗）

- unique source reports: 12998
- local 13:00-17:00 event rows: 2226
- + decline >= 0.5: 827
- + running high age >= 60m: 644
- + flat/cooling path: 575
- market-aligned event rows: 551
- first base signal city-days: 168
- first support>=2 diagnostic city-days: 84
- anchor 剔除：Busan 2026-07-14 从证据层移除（定义形态的 in-sample 交易），证据分母为 base 167 / strong 83

## Evidence coverage（不是策略筛选）

- base direct quote coverage: current YES 22/167; d1 NO 21/167
- support>=2 direct quote coverage: current YES 9/83; d1 NO 8/83
- settled executable rows: base 43; support>=2 17
- settled indicative rows (not executable): base 303; support>=2 153

## 为什么 executable coverage 会塌缩

| Cohort | Expression | Signals | Indicative price | First-snapshot ask | Ask within 30m | Settled | Ask+settled | Indicative but no ask | First-snapshot book status |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| base | current_yes | 167 | 167 | 18 | 22 | 152 | 22 | 145 | `{"ok": 18, "orderbook_budget_exhausted": 146, "orderbook_scope_skipped": 3}` |
| base | d1_no | 167 | 166 | 18 | 21 | 151 | 21 | 145 | `{"missing_status": 1, "ok": 18, "orderbook_budget_exhausted": 145, "orderbook_scope_skipped": 3}` |
| strong_partial | current_yes | 83 | 83 | 7 | 9 | 77 | 9 | 74 | `{"ok": 8, "orderbook_budget_exhausted": 74, "orderbook_scope_skipped": 1}` |
| strong_partial | d1_no | 83 | 82 | 7 | 8 | 76 | 8 | 74 | `{"missing_status": 1, "ok": 8, "orderbook_budget_exhausted": 73, "orderbook_scope_skipped": 1}` |

历史 paper snapshot 不是全量盘口录制：默认每 10 分钟生成一次 snapshot，但 orderbook enrichment 使用 `strategy_live` 紧凑 scope、60 秒总预算和单 worker。天气状态与 indicative market price 大多保留，真实 YES/NO ask 则大量标为 `orderbook_budget_exhausted` 或 `orderbook_scope_skipped`。因此缺的是可执行价格证据，不是物理 signal 或 settlement 全部缺失。

这批数据仍有意义：indicative+settled 层可检验 selector 的方向、胜率和粗略定价残差；只有 direct ask+settled 层才能声称 executable ROI。前者不能冒充后者。

## CLOB minute price PIT proxy（同分母补回）

`/prices-history` 仍可取回 closed market 的分钟价格，因此可以在首次识别后取第一个 PIT price。它恢复了价格路径分母，但不含当时 ask、spread、size/depth；下面是 price proxy ROI，不是 guaranteed fill ROI。

| Cohort | Expression | Rows | Dates | Cities | Win rate | Avg proxy | Fee ROI | Date-bootstrap 95% CI |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| base | current_yes | 151 | 8 | 39 | +92.1% | 0.916 | +0.3% | [-2.8%, +3.1%] |
| base | d1_no | 150 | 8 | 39 | +94.7% | 0.957 | -1.3% | [-4.3%, +1.5%] |
| strong_partial | current_yes | 77 | 8 | 25 | +93.5% | 0.927 | +0.6% | [-4.1%, +4.3%] |
| strong_partial | d1_no | 76 | 8 | 25 | +94.7% | 0.959 | -1.4% | [-5.5%, +1.4%] |

### 固定加价敏感性（代理 ask = history price + 1/2/3c）

| Add-on | Cohort | Expression | Rows | Avg assumed ask | Fee ROI |
|---:|---|---|---:|---:|---:|
| +0.00 | base | current_yes | 151 | 0.916 | +0.3% |
| +0.00 | base | d1_no | 150 | 0.957 | -1.3% |
| +0.00 | strong_partial | current_yes | 77 | 0.927 | +0.6% |
| +0.00 | strong_partial | d1_no | 76 | 0.959 | -1.4% |
| +0.01 | base | current_yes | 151 | 0.924 | -0.5% |
| +0.01 | base | d1_no | 150 | 0.965 | -2.0% |
| +0.01 | strong_partial | current_yes | 77 | 0.935 | -0.2% |
| +0.01 | strong_partial | d1_no | 76 | 0.966 | -2.1% |
| +0.02 | base | current_yes | 151 | 0.930 | -1.1% |
| +0.02 | base | d1_no | 150 | 0.970 | -2.5% |
| +0.02 | strong_partial | current_yes | 77 | 0.941 | -0.8% |
| +0.02 | strong_partial | d1_no | 76 | 0.971 | -2.6% |
| +0.03 | base | current_yes | 151 | 0.934 | -1.6% |
| +0.03 | base | d1_no | 150 | 0.974 | -2.9% |
| +0.03 | strong_partial | current_yes | 77 | 0.945 | -1.2% |
| +0.03 | strong_partial | d1_no | 76 | 0.975 | -3.0% |

### 与已留存 direct ask 的重合校验

| Expression | Overlap | Mean ask-history | Median | P90 |
|---|---:|---:|---:|---:|
| current_yes | 22 | +0.016 | +0.005 | +0.015 |
| d1_no | 21 | +0.006 | +0.005 | +0.010 |

上表混合了 signal 后到 direct book 出现前的价格移动，不能纯解释为 spread。第一张 snapshot 同时有 ask 和 indicative price 的 16 个 leg 校验如下：

| Expression | Same-snapshot overlap | Mean ask-indicative | Median | P90 |
|---|---:|---:|---:|---:|
| current_yes | 18 | +0.025 | +0.008 | +0.064 |
| d1_no | 18 | +0.013 | +0.004 | +0.035 |

### Busan-like 0.80-0.90 价格形态（仅诊断，不作为门槛）

| Cohort | Expression | Rows | Dates | Cities | Win rate | Avg proxy | Fee ROI | Date-bootstrap 95% CI |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| base_anchor_price_band_0.80_0.90 | current_yes | 8 | 6 | 7 | +75.0% | 0.858 | -13.2% | [-61.2%, +15.8%] |
| base_anchor_price_band_0.80_0.90 | d1_no | 10 | 6 | 9 | +70.0% | 0.851 | -18.4% | [-50.8%, +6.0%] |
| strong_anchor_price_band_0.80_0.90 | current_yes | 2 | 2 | 2 | +50.0% | 0.854 | -41.9% | [-100.0%, +11.4%] |
| strong_anchor_price_band_0.80_0.90 | d1_no | 2 | 2 | 2 | +50.0% | 0.818 | -39.4% | [-100.0%, +19.5%] |

## current YES vs d1 NO（严格同一 signal 分母）

current YES 只有最终最高温正好停在当前档才赢；d1 NO 只要求最终最高温不是下一档。当前档已经打印后，停在当前档时两者都赢，只升一档时两者都输，只有升两档及以上时 d1 NO 额外赢。因此 d1 NO 是 `d2+ overshoot` 保险，是否值得取决于额外胜率能否覆盖价格溢价。

| Price layer | Cohort | Paired rows | Dates | Current YES win | d1 NO win | d1-current ask | Current YES ROI | d1 NO ROI | YES-d1 ROI | 95% CI |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct_executable_ask | base | 21 | 7 | +100.0% | +100.0% | +0.007 | +2.3% | +1.6% | +0.7% | [+0.0%, +2.1%] |
| direct_executable_ask | strong_partial | 8 | 7 | +100.0% | +100.0% | +0.015 | +4.3% | +2.7% | +1.5% | [-0.1%, +4.8%] |
| indicative_not_executable | base | 151 | 8 | +91.4% | +94.0% | +0.036 | +0.8% | -0.2% | +1.1% | [+0.4%, +1.7%] |
| indicative_not_executable | strong_partial | 76 | 8 | +93.4% | +94.7% | +0.030 | +0.8% | -1.0% | +1.8% | [+0.4%, +3.7%] |
| clob_price_history_proxy | base | 150 | 8 | +92.0% | +94.7% | +0.042 | +0.3% | -1.3% | +1.6% | [+1.0%, +2.4%] |
| clob_price_history_proxy | strong_partial | 76 | 8 | +93.4% | +94.7% | +0.033 | +0.6% | -1.4% | +2.0% | [+0.5%, +4.4%] |

## Direct executable ask result

| Cohort | Expression | Rows | Dates | Cities | Win rate | Avg ask | Fee ROI | Date-bootstrap 95% CI |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| base | current_yes | 22 | 7 | 8 | +100.0% | 0.976 | +2.3% | [+0.9%, +5.9%] |
| base | d1_no | 21 | 7 | 7 | +100.0% | 0.983 | +1.6% | [+0.6%, +3.8%] |
| strong_partial | current_yes | 9 | 7 | 4 | +100.0% | 0.960 | +4.0% | [+0.8%, +10.9%] |
| strong_partial | d1_no | 8 | 7 | 3 | +100.0% | 0.972 | +2.7% | [+0.6%, +6.9%] |

## Broad indicative-price diagnostic（不可当成成交回测）

| Cohort | Expression | Rows | Dates | Cities | Win rate | Avg price | Fee ROI | Date-bootstrap 95% CI |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| base | current_yes | 152 | 8 | 39 | +91.4% | 0.905 | +0.8% | [-1.8%, +3.1%] |
| base | d1_no | 151 | 8 | 39 | +94.0% | 0.941 | -0.2% | [-2.9%, +2.3%] |
| strong_partial | current_yes | 77 | 8 | 25 | +93.5% | 0.926 | +0.8% | [-3.2%, +4.2%] |
| strong_partial | d1_no | 76 | 8 | 25 | +94.7% | 0.955 | -1.0% | [-4.9%, +1.7%] |

## support count diagnostic（base cohort，非门槛）

| Support | Expression | Rows | Dates | Win rate | Avg price | Fee ROI | Date-bootstrap 95% CI |
|---|---|---:|---:|---:|---:|---:|---:|
| 0 | current_yes | 42 | 8 | +90.5% | 0.902 | +0.1% | [-5.8%, +4.3%] |
| 0 | d1_no | 42 | 8 | +95.2% | 0.942 | +0.9% | [-3.6%, +3.9%] |
| 1 | current_yes | 40 | 8 | +90.0% | 0.882 | +1.8% | [-4.6%, +5.8%] |
| 1 | d1_no | 40 | 8 | +92.5% | 0.918 | +0.5% | [-5.6%, +4.6%] |
| 2 | current_yes | 36 | 8 | +86.1% | 0.875 | -1.9% | [-8.7%, +4.5%] |
| 2 | d1_no | 36 | 8 | +88.9% | 0.933 | -5.0% | [-11.8%, +1.4%] |
| 3+ | current_yes | 34 | 7 | +100.0% | 0.967 | +3.2% | [+1.6%, +5.6%] |
| 3+ | d1_no | 33 | 7 | +100.0% | 0.973 | +2.6% | [+1.3%, +4.4%] |

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
conclusion=inconclusive_tiny_live_probe_only
```

完整逐事件行见 `generated/heat_death_early_event_replay_v1/`。
