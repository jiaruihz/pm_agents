# 天气低价 YES 彩票仓研究 - 2026-05-19

这是第一版低价 YES 彩票仓研究文档，重点分析 `5c-20c` 低价 YES 在持有到结算口径下是否有筛选价值。本报告使用已经结算的历史 snapshot 代理价格，不使用可成交 ask/bid；后续 snapshot 已经开始记录 CLOB orderbook 字段，等数据自然积累后再切换到强回测口径。

## 样本定义

原始样本单位是一条时间点机会：`snapshot_time + city + event_date + bracket + BUY_YES`。

进入本报告前的过滤条件：

- 只看 `BUY_YES`
- 代理入场价在 `5c` 到 `20c`
- `model_prob / entry_price >= 1.5`
- `model_prob - entry_price >= 3pt`
- 已经有最终结算结果

同一个城市、同一天、同一个温度 bin 会在不同 snapshot 里反复出现，所以策略评估不用 8118 条原始行直接算，而是按入场窗口去重：每个 `city + event_date + bracket + entry_window` 只保留最早一次符合条件的入场。

## 数据覆盖

- 原始时间点机会：`8118`
- 如果每个 market 只取最早一次，唯一 market 数：`611`
- 日期范围：`2026-05-05` 到 `2026-05-15`
- 覆盖 `11` 个已结算 event date，`48` 个城市。

## 核心结论

- 低价 YES 全池不能直接交易；原始 8118 行主要是时间点扫描结果，不是 8118 个独立下注机会。
- 按入场窗口去重后，这批数据里最值得继续看的窗口是 `T-20..24h` 和 `T-24..30h`。
- 表现较好的规则基本都要求高模型 edge，例如 `edge >= 20pt`，或者 `model_prob >= 30%` 且 `prob / price >= 2`。
- 这个结果只能作为研究线索，不能直接当实盘规则：样本只有 11 个结算日，并且当前价格还是 snapshot 代理价格，不是可成交 ask。

按窗口去重后的整体表现：

| Window | n | ROI | Hit rate | Cost | PnL | Dates | Cities |
|---|---:|---:|---:|---:|---:|---:|---:|
| T-30..40h | 221 | 5.1% | 12.2% | 25.69 | 1.31 | 10 | 44 |
| T-24..30h | 230 | -1.2% | 11.3% | 26.33 | -0.33 | 10 | 44 |
| T-20..24h | 229 | 0.4% | 10.9% | 24.90 | 0.10 | 10 | 45 |
| T-16..20h | 216 | -18.5% | 9.3% | 24.54 | -4.54 | 10 | 42 |
| T-12..16h | 244 | 12.8% | 12.7% | 27.48 | 3.52 | 11 | 42 |
| T-8..12h | 272 | 9.0% | 11.8% | 29.35 | 2.65 | 11 | 48 |
| T-4..8h | 127 | 5.2% | 11.0% | 13.31 | 0.69 | 11 | 43 |

## 候选规则

完整 JSON 里保留 `85` 条通过初筛的候选规则；Markdown 只展示排名靠前的 `8` 条，避免把文档变成参数表。候选规则要求 test ROI 为正，且 test 至少覆盖 2 个日期、4 个城市。

- train: `2026-05-05` 到 `2026-05-11`
- test: `2026-05-12` 到 `2026-05-15`

### Candidate 1: T-20..24h / price=05-20c, prob>=30%, ratio>=2, edge>=5%

- Overall: n=85, ROI=53.5%, hit=18.8%, cost=10.42, pnl=5.58, dates=10, cities=30
- Train: n=41, ROI=3.9%, hit=12.2%, cost=4.81, pnl=0.19, dates=6, cities=13
- Test: n=44, ROI=96.1%, hit=25.0%, cost=5.61, pnl=5.39, dates=4, cities=28
- 全样本最大单日亏损：`-1.04`，日期 `2026-05-08`

主要正贡献城市：

- `Madrid`: n=6, ROI=205.3%, hit=50.0%, cost=0.98, pnl=2.02, dates=6, cities=1
- `Busan`: n=2, ROI=667.8%, hit=100.0%, cost=0.26, pnl=1.74, dates=2, cities=1
- `Warsaw`: n=5, ROI=210.1%, hit=40.0%, cost=0.65, pnl=1.35, dates=4, cities=1

主要负贡献城市：

- `London`: n=8, ROI=-100.0%, hit=0.0%, cost=1.05, pnl=-1.05, dates=7, cities=1
- `Seoul`: n=6, ROI=-100.0%, hit=0.0%, cost=0.82, pnl=-0.82, dates=6, cities=1
- `Miami`: n=3, ROI=-100.0%, hit=0.0%, cost=0.35, pnl=-0.35, dates=3, cities=1

### Candidate 2: T-20..24h / price=05-20c, prob>=15%, ratio>=1.5, edge>=20%

- Overall: n=83, ROI=68.4%, hit=19.3%, cost=9.50, pnl=6.50, dates=10, cities=27
- Train: n=42, ROI=36.3%, hit=14.3%, cost=4.40, pnl=1.60, dates=6, cities=13
- Test: n=41, ROI=96.0%, hit=24.4%, cost=5.10, pnl=4.90, dates=4, cities=25
- 全样本最大单日亏损：`-0.61`，日期 `2026-05-06`

主要正贡献城市：

- `Madrid`: n=5, ROI=278.6%, hit=60.0%, cost=0.79, pnl=2.21, dates=5, cities=1
- `Busan`: n=2, ROI=667.8%, hit=100.0%, cost=0.26, pnl=1.74, dates=2, cities=1
- `Shanghai`: n=4, ROI=490.0%, hit=50.0%, cost=0.34, pnl=1.66, dates=4, cities=1

主要负贡献城市：

- `London`: n=9, ROI=-100.0%, hit=0.0%, cost=1.10, pnl=-1.10, dates=7, cities=1
- `Seoul`: n=4, ROI=-100.0%, hit=0.0%, cost=0.48, pnl=-0.48, dates=4, cities=1
- `Tokyo`: n=6, ROI=-100.0%, hit=0.0%, cost=0.39, pnl=-0.39, dates=6, cities=1

### Candidate 3: T-24..30h / price=05-20c, prob>=15%, ratio>=1.5, edge>=20%

- Overall: n=85, ROI=62.7%, hit=18.8%, cost=9.84, pnl=6.16, dates=10, cities=32
- Train: n=39, ROI=31.1%, hit=15.4%, cost=4.58, pnl=1.42, dates=6, cities=13
- Test: n=46, ROI=90.2%, hit=21.7%, cost=5.26, pnl=4.74, dates=4, cities=30
- 全样本最大单日亏损：`-0.73`，日期 `2026-05-06`

主要正贡献城市：

- `LA`: n=7, ROI=215.7%, hit=42.9%, cost=0.95, pnl=2.05, dates=6, cities=1
- `Miami`: n=5, ROI=275.6%, hit=40.0%, cost=0.53, pnl=1.47, dates=4, cities=1
- `Madrid`: n=6, ROI=144.5%, hit=33.3%, cost=0.82, pnl=1.18, dates=5, cities=1

主要负贡献城市：

- `London`: n=8, ROI=-100.0%, hit=0.0%, cost=1.08, pnl=-1.08, dates=6, cities=1
- `Tokyo`: n=4, ROI=-100.0%, hit=0.0%, cost=0.41, pnl=-0.41, dates=4, cities=1
- `Seoul`: n=3, ROI=-100.0%, hit=0.0%, cost=0.33, pnl=-0.33, dates=3, cities=1

### Candidate 4: T-20..24h / price=05-20c, prob>=20%, ratio>=2, edge>=5%

- Overall: n=119, ROI=50.2%, hit=16.8%, cost=13.31, pnl=6.69, dates=10, cities=39
- Train: n=53, ROI=4.0%, hit=11.3%, cost=5.77, pnl=0.23, dates=6, cities=13
- Test: n=66, ROI=85.6%, hit=21.2%, cost=7.54, pnl=6.46, dates=4, cities=37
- 全样本最大单日亏损：`-0.85`，日期 `2026-05-06`

主要正贡献城市：

- `Madrid`: n=6, ROI=205.3%, hit=50.0%, cost=0.98, pnl=2.02, dates=6, cities=1
- `Busan`: n=2, ROI=667.8%, hit=100.0%, cost=0.26, pnl=1.74, dates=2, cities=1
- `Warsaw`: n=6, ROI=187.8%, hit=33.3%, cost=0.69, pnl=1.30, dates=4, cities=1

主要负贡献城市：

- `London`: n=9, ROI=-100.0%, hit=0.0%, cost=1.10, pnl=-1.10, dates=7, cities=1
- `Seoul`: n=7, ROI=-100.0%, hit=0.0%, cost=0.88, pnl=-0.88, dates=6, cities=1
- `Beijing`: n=8, ROI=-100.0%, hit=0.0%, cost=0.67, pnl=-0.67, dates=8, cities=1

### Candidate 5: T-20..24h / price=05-20c, prob>=15%, ratio>=2, edge>=10%

- Overall: n=132, ROI=41.3%, hit=15.2%, cost=14.15, pnl=5.85, dates=10, cities=40
- Train: n=59, ROI=-2.9%, hit=10.2%, cost=6.18, pnl=-0.18, dates=6, cities=13
- Test: n=73, ROI=75.6%, hit=19.2%, cost=7.97, pnl=6.03, dates=4, cities=38
- 全样本最大单日亏损：`-0.85`，日期 `2026-05-06`

主要正贡献城市：

- `Madrid`: n=6, ROI=205.3%, hit=50.0%, cost=0.98, pnl=2.02, dates=6, cities=1
- `Busan`: n=3, ROI=530.9%, hit=66.7%, cost=0.32, pnl=1.68, dates=2, cities=1
- `Warsaw`: n=6, ROI=187.8%, hit=33.3%, cost=0.69, pnl=1.30, dates=4, cities=1

主要负贡献城市：

- `London`: n=9, ROI=-100.0%, hit=0.0%, cost=1.10, pnl=-1.10, dates=7, cities=1
- `Seoul`: n=7, ROI=-100.0%, hit=0.0%, cost=0.88, pnl=-0.88, dates=6, cities=1
- `Beijing`: n=9, ROI=-100.0%, hit=0.0%, cost=0.73, pnl=-0.73, dates=9, cities=1

### Candidate 6: T-20..24h / price=05-20c, prob>=25%, ratio>=2, edge>=5%

- Overall: n=102, ROI=42.4%, hit=16.7%, cost=11.94, pnl=5.07, dates=10, cities=32
- Train: n=51, ROI=6.0%, hit=11.8%, cost=5.66, pnl=0.34, dates=6, cities=13
- Test: n=51, ROI=75.4%, hit=21.6%, cost=6.27, pnl=4.73, dates=4, cities=30
- 全样本最大单日亏损：`-0.85`，日期 `2026-05-06`

主要正贡献城市：

- `Madrid`: n=6, ROI=205.3%, hit=50.0%, cost=0.98, pnl=2.02, dates=6, cities=1
- `Busan`: n=2, ROI=667.8%, hit=100.0%, cost=0.26, pnl=1.74, dates=2, cities=1
- `Shanghai`: n=6, ROI=218.0%, hit=33.3%, cost=0.63, pnl=1.37, dates=5, cities=1

主要负贡献城市：

- `London`: n=9, ROI=-100.0%, hit=0.0%, cost=1.10, pnl=-1.10, dates=7, cities=1
- `Seoul`: n=6, ROI=-100.0%, hit=0.0%, cost=0.82, pnl=-0.82, dates=6, cities=1
- `Beijing`: n=8, ROI=-100.0%, hit=0.0%, cost=0.67, pnl=-0.67, dates=8, cities=1

### Candidate 7: T-20..24h / price=05-20c, prob>=15%, ratio>=2, edge>=5%

- Overall: n=137, ROI=37.9%, hit=14.6%, cost=14.51, pnl=5.49, dates=10, cities=41
- Train: n=59, ROI=-2.9%, hit=10.2%, cost=6.18, pnl=-0.18, dates=6, cities=13
- Test: n=78, ROI=68.1%, hit=17.9%, cost=8.33, pnl=5.67, dates=4, cities=39
- 全样本最大单日亏损：`-0.85`，日期 `2026-05-06`

主要正贡献城市：

- `Madrid`: n=6, ROI=205.3%, hit=50.0%, cost=0.98, pnl=2.02, dates=6, cities=1
- `Busan`: n=3, ROI=530.9%, hit=66.7%, cost=0.32, pnl=1.68, dates=2, cities=1
- `Warsaw`: n=6, ROI=187.8%, hit=33.3%, cost=0.69, pnl=1.30, dates=4, cities=1

主要负贡献城市：

- `London`: n=9, ROI=-100.0%, hit=0.0%, cost=1.10, pnl=-1.10, dates=7, cities=1
- `Seoul`: n=7, ROI=-100.0%, hit=0.0%, cost=0.88, pnl=-0.88, dates=6, cities=1
- `Beijing`: n=9, ROI=-100.0%, hit=0.0%, cost=0.73, pnl=-0.73, dates=9, cities=1

### Candidate 8: T-24..30h / price=05-20c, prob>=20%, ratio>=2, edge>=5%

- Overall: n=118, ROI=49.4%, hit=17.0%, cost=13.39, pnl=6.61, dates=10, cities=38
- Train: n=50, ROI=23.9%, hit=14.0%, cost=5.65, pnl=1.35, dates=6, cities=13
- Test: n=68, ROI=68.0%, hit=19.1%, cost=7.74, pnl=5.26, dates=4, cities=37
- 全样本最大单日亏损：`-0.95`，日期 `2026-05-06`

主要正贡献城市：

- `LA`: n=8, ROI=177.7%, hit=37.5%, cost=1.08, pnl=1.92, dates=6, cities=1
- `Miami`: n=6, ROI=240.4%, hit=33.3%, cost=0.59, pnl=1.41, dates=5, cities=1
- `Madrid`: n=6, ROI=144.5%, hit=33.3%, cost=0.82, pnl=1.18, dates=5, cities=1

主要负贡献城市：

- `London`: n=9, ROI=-100.0%, hit=0.0%, cost=1.21, pnl=-1.21, dates=7, cities=1
- `Seoul`: n=5, ROI=-100.0%, hit=0.0%, cost=0.54, pnl=-0.54, dates=5, cities=1
- `Tokyo`: n=5, ROI=-100.0%, hit=0.0%, cost=0.51, pnl=-0.51, dates=5, cities=1

## 解释

这批结果说明：低价 YES 本身不是策略，真正可能有价值的是“特定入场窗口 + 高模型概率/高价格比 + 高 edge”的子集。

## 城市集中度检查

这里单独做一层 city group by。原因是低价彩票仓很容易被少数城市、少数命中票撑出漂亮 ROI；如果剔除头部城市后收益消失，就不能把它理解成全局天气策略。

### 规则 A：T-20..24h / edge >= 20pt

条件：`5c <= price <= 20c`，`model_prob >= 15%`，`prob / price >= 1.5`，`edge >= 20pt`。

- 整体：n=83，city_count=27，ROI=68.4%，hit=19.3%，PnL=+6.50
- 正收益城市 11 个，负收益城市 16 个

| City | n | PnL | ROI | Hit |
|---|---:|---:|---:|---:|
| Madrid | 5 | +2.21 | 278.6% | 60.0% |
| Busan | 2 | +1.74 | 667.8% | 100.0% |
| Shanghai | 4 | +1.66 | 490.0% | 50.0% |
| Warsaw | 5 | +1.36 | 210.1% | 40.0% |
| London | 9 | -1.10 | -100.0% | 0.0% |
| Seoul | 4 | -0.49 | -100.0% | 0.0% |
| Tokyo | 6 | -0.39 | -100.0% | 0.0% |

集中度压力测试：

| Slice | n | ROI | PnL |
|---|---:|---:|---:|
| 原规则 | 83 | 68.4% | +6.50 |
| 去掉 top 1 城市 | 78 | 49.2% | +4.29 |
| 去掉 top 3 城市 | 72 | 11.0% | +0.89 |
| 去掉 top 5 城市 | 66 | -18.3% | -1.34 |
| 剔除 London/Seoul/Tokyo/Beijing | 60 | 121.2% | +8.77 |

结论：规则 A 有信号，但不是全城市通用。它更像“城市白名单 + 黑名单”的策略。Madrid、Busan、Shanghai、Warsaw 值得继续观察；London、Seoul、Tokyo、Beijing 暂时应该降权或禁入。

### 规则 B：T-24..30h / edge >= 20pt

条件同规则 A，只是入场窗口提前到 `T-24..30h`。

- 整体：n=85，city_count=32，ROI=62.7%，hit=18.8%，PnL=+6.16
- 正收益城市 12 个，负收益城市 20 个

| City | n | PnL | ROI | Hit |
|---|---:|---:|---:|---:|
| LA | 7 | +2.05 | 215.7% | 42.9% |
| Miami | 5 | +1.47 | 275.6% | 40.0% |
| Madrid | 6 | +1.18 | 144.5% | 33.3% |
| Jeddah | 2 | +0.82 | 469.8% | 50.0% |
| London | 8 | -1.08 | -100.0% | 0.0% |
| Tokyo | 4 | -0.41 | -100.0% | 0.0% |
| Seoul | 3 | -0.33 | -100.0% | 0.0% |

集中度压力测试：

| Slice | n | ROI | PnL |
|---|---:|---:|---:|
| 原规则 | 85 | 62.7% | +6.16 |
| 去掉 top 1 城市 | 78 | 46.3% | +4.11 |
| 去掉 top 3 城市 | 67 | 19.4% | +1.47 |
| 去掉 top 5 城市 | 64 | -2.4% | -0.17 |
| 剔除 London/Seoul/Tokyo/Beijing | 68 | 102.2% | +8.09 |

结论：规则 B 比规则 A 的城市覆盖稍宽，但仍然不是全池策略。LA/Miami/Madrid 是这一窗口的主要贡献；London/Tokyo/Seoul 继续是硬伤。

### 规则 C：T-20..24h / model_prob >= 30% / ratio >= 2

条件：`5c <= price <= 20c`，`model_prob >= 30%`，`prob / price >= 2`，`edge >= 5pt`。

- 整体：n=85，city_count=30，ROI=53.5%，hit=18.8%，PnL=+5.58
- 正收益城市 11 个，负收益城市 19 个

| Slice | n | ROI | PnL |
|---|---:|---:|---:|
| 原规则 | 85 | 53.5% | +5.58 |
| 去掉 top 1 城市 | 79 | 37.7% | +3.56 |
| 去掉 top 3 城市 | 72 | 5.5% | +0.47 |
| 去掉 top 5 城市 | 64 | -19.8% | -1.48 |
| 剔除 London/Seoul/Tokyo/Beijing | 64 | 98.7% | +7.95 |

结论：规则 C 的收益高度依赖 Madrid、Busan、Warsaw、NYC 等头部城市。它适合做白名单研究，不适合做全城市规则。

### 城市层面的临时判断

| Tier | Cities | 用法 |
|---|---|---|
| 观察白名单 | Madrid, LA, Miami, Warsaw, Shanghai, Busan, NYC | 可以进入下一轮 paper，但要限制单城市仓位 |
| 小样本正贡献 | Jeddah, Milan, Amsterdam, BuenosAires, Seattle, Chicago | 样本太少，只能观察，不能放大 |
| 临时黑名单 | London, Seoul, Tokyo, Beijing | 多个规则里反复 -100%，下一轮应先排除或大幅降权 |
| 不稳定/冲突 | Miami, Shanghai, LA | 不同窗口表现不同，要按窗口拆开看，不能简单城市全开或全关 |

更稳妥的下一版规则不是“全城市跑 Candidate 1/2”，而是：

```text
T-20..30h
5c <= executable ask <= 20c
edge >= 20pt
prob / ask >= 1.5
排除临时黑名单城市
每个 city + date + bracket 只允许一次入场
单城市每日成本上限
```

下一步最重要的验证不是补历史 CLOB，因为这部分现在拿不到足够可靠的历史 orderbook。正确做法是让未来定时 snapshot 自然记录 `yes_best_ask` / `yes_best_bid`，等盘口恢复且样本积累后，用可成交价格重跑同一套窗口研究。

## 下一步

1. 先把本版本作为研究 baseline，不直接变成实盘规则。
2. 等 orderbook-enriched snapshot 积累数日后，用 `entry = yes_best_ask` 重跑。
3. 加入去重持仓规则：同一个 `city + event_date + bracket` 只允许一笔打开中的彩票仓。
4. 在任何 paper/live sleeve 前加风控：每日最大票数、单城市最大成本、单 event date 最大成本。

## 当前成果记录

截至 2026-05-19，这轮研究已经得到的有效成果是：

1. 明确了样本单位：原始 `8118` 行不是独立交易，而是时间点机会。策略评估必须按 `city + event_date + bracket + entry_window` 去重。
2. 排除了“全低价 YES 池直接交易”的想法。全池结果会被重复 snapshot 和少数城市贡献误导。
3. 找到了值得继续验证的候选形态：`T-20..30h`、`5c-20c`、高 edge、`prob / price >= 1.5` 的低价 YES 彩票仓。
4. 发现城市维度是核心变量。Madrid、LA、Miami、Warsaw、Shanghai、Busan、NYC 暂时进入观察白名单；London、Seoul、Tokyo、Beijing 暂时进入黑名单或强降权。
5. 明确了当前最大红旗：去掉 top 5 正贡献城市后，多个候选规则 ROI 会转负，所以现在还不能证明这是稳定 alpha。
6. CLOB 历史不做强行补全。未来定时 snapshot 已经改为记录 top-of-book 和深度字段，等盘口恢复后自然积累可成交价格数据。

## 偶然性验证计划

下一版研究要回答“这是偶然，还是有效因子”。优先级如下：

1. `leave-one-city-out` / `leave-one-date-out`：逐个剔除城市和日期，检查 ROI 是否被单城市或单日命中撑起来。
2. 城市内 fixed-effect：在同一个城市内部比较高 edge 与低 edge，而不是只比较城市之间谁赚钱。
3. 参数邻域稳定性：检查 `edge >= 15/20/25pt`、`T-16..24h / T-20..30h / T-24..30h`、不同价格段是否是一片区域有效，而不是单点参数有效。
4. placebo 随机对照：同价格区间随机选 YES、同日内打乱 model_prob/edge，比较真实规则在随机分布中的百分位。
5. 用未来 CLOB 数据重跑：入场用 ask，退出/估值用 bid，确认 proxy price 结果没有被 spread 和流动性吃掉。
