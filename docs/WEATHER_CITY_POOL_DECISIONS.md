# 天气策略城市池决策记录

更新时间：2026-05-27

这份文档专门记录天气策略城市池怎么变、为什么变、当前哪些城市可以实盘。

代码真相：

| 环境 | 文件 |
|---|---|
| 本机开发副本 | `/home/rui/projects/weather-predict/city_pools.py` |
| N100 生产端 | `/home/jiarui/projects/weather-predict/city_pools.py` |

口径说明：

| 名称 | 含义 |
|---|---|
| `TRADING_T1_CITIES` | 当前允许 paper/live 执行的交易城市池 |
| `RESEARCH_T2_CITIES` | 研究池，自动等于 `FULL_CITY_CONFIGS - TRADING_T1_CITIES` |
| `FULL_CITY_CONFIGS` | 所有已配置城市，保留天气、结算、研究配置 |

所以，把一个城市从 T1 移出，不代表删除它。它仍然保留在 `FULL_CITY_CONFIGS`，后续继续作为 T2 做 paper / research / settlement。

## 当前结论

当前版本：v3  
决策日期：2026-05-27  
T1 城市数：24

### 当前 T1 城市

| 区域/用途 | 城市 |
|---|---|
| 美洲 | Boston, LA, Miami, NYC, Phoenix, Seattle, BuenosAires |
| 欧洲 | Amsterdam, Ankara, Istanbul, London, Moscow, Munich, Paris, Warsaw |
| 亚洲 / 中东 | Chengdu, Guangzhou, Jeddah, Karachi, Lucknow, Manila, Shanghai, Singapore, Tokyo |

完整列表：

```text
Amsterdam, Ankara, Boston, BuenosAires, Chengdu, Guangzhou, Istanbul,
Jeddah, Karachi, LA, London, Lucknow, Manila, Miami, Moscow, Munich,
NYC, Paris, Phoenix, Seattle, Shanghai, Singapore, Tokyo, Warsaw
```

### 当前不在 T1 的重点城市

| 城市 | 当前状态 | 原因 |
|---|---|---|
| Madrid | T2 / research only | live 亏损最明显，ROI -60.2% |
| Beijing | T2 / research only | live ROI -7.6%，不继续扩 size |
| Chicago | T2 / research only | live ROI -18.5%，样本虽小但表现弱 |
| Austin | T2 / research only | v2 时已移出，赔率结构不利 |

## 这次 v3 怎么改

代码提交：

```text
0637da1 strategy: update T1 city pool after paper comparison
```

变更：

| 动作 | 城市 |
|---|---|
| 移出 T1 | Chicago |
| 确认继续不在 T1 | Beijing, Madrid |
| 加入 T1 | BuenosAires, Amsterdam, Manila, Munich, Singapore, Chengdu |

### 为什么移出

| 城市 | live 样本 | 结论 |
|---|---:|---|
| Madrid | 15 fills / 3 wins / ROI -60.2% | 明确移出；多次出现在 market-level loser |
| Beijing | 29 fills / ROI -7.6% | 不继续给 live 预算 |
| Chicago | 8 fills / ROI -18.5% | 样本不大，但方向不值得继续放 T1 |

### 为什么加入

这次不是只看 T2 排名，而是把“昨天刚加的 8 城”和“当前还在 T2 的候选”放到同一张 paper ledger 表里比较。

筛选标准：

| 指标 | 阈值 |
|---|---:|
| active_days | >= 4 |
| fills | >= 10 |
| ROI | >= 10% |
| positive_day_rate | >= 60% |

解释：

- `active_days`：这个城市满足入场条件的天数，不只看交易笔数。
- `positive_day_rate`：有多少交易日是赚钱的，用来判断是不是只靠某一天运气。
- ROI 为正但 `positive_day_rate` 低，先低 size 或 shadow，不直接加权。

被选入 T1 的 6 个 T2 城市：

| 城市 | fills | active_days | positive_day_rate | pnl_usd | ROI | 判断 |
|---|---:|---:|---:|---:|---:|---|
| BuenosAires | 11 | 7 | 85.7% | 39.46 | 65.2% | 第一优先级 |
| Amsterdam | 14 | 7 | 71.4% | 33.49 | 50.4% | 稳定 |
| Manila | 12 | 6 | 83.3% | 24.76 | 44.8% | 稳定 |
| Munich | 13 | 7 | 71.4% | 23.70 | 35.7% | 稳定 |
| Singapore | 13 | 8 | 75.0% | 25.50 | 34.2% | 稳定，补亚洲池 |
| Chengdu | 13 | 5 | 100.0% | 21.39 | 31.2% | 稳定，补亚洲池 |

完整分析报告：

- `docs/analysis/2026-05/2026-05-27-performance-live-full-research.md`

## 昨天新增 8 城怎么处理

2026-05-26 新增的 8 城：

```text
Ankara, Guangzhou, Istanbul, Jeddah, Karachi, Lucknow, Moscow, Seattle
```

2026-05-27 同池复查后的分级：

| 分级 | 城市 | 动作 |
|---|---|---|
| 保留 / 可小幅加权 | Jeddah, Guangzhou, Moscow, Seattle | 日稳定性和 ROI 都过关 |
| 低 size 观察 | Lucknow, Istanbul, Ankara, Karachi | 总 ROI 为正，但赚钱天数比例不够稳 |

## 历史记录

### 2026-05-27：Madrid 单独降级

代码提交：

```text
294f7e3 strategy: remove Madrid from T1 trading pool
```

动作：

- `Madrid` 从 `TRADING_T1_CITIES` 移出。
- `Madrid` 继续留在 `FULL_CITY_CONFIGS`，所以变成 T2 / research only。

原因：

- live 已结算结果：15 fills，3 wins，ROI -60.2%。
- Madrid 有多个 market-level loser，不适合继续放 T1。

### 2026-05-26：v2 扩池

代码提交：

```text
b4902bc feat: city pool v2 — expand T1 to 20 cities, dual-policy A/B paper orders
```

动作：

| 动作 | 城市 |
|---|---|
| 加入 T1 | Ankara, Guangzhou, Istanbul, Jeddah, Karachi, Lucknow, Moscow, Seattle |
| 移出 T1 | Austin, Beijing |

当时依据：

- 基于 2026-05-08 到 2026-05-24 的 paper ledger。
- 939 笔已结算交易。
- 新增城市主要来自 BUY_NO 表现，ROI 大多在 +20% 到 +55% 区间。

## 后续改城市池的流程

每次改城市池，按这个顺序做：

1. 先跑 paper/live 分析，明确分母和时间窗。
2. 把新加城市和当前 T2 候选放在同一张表里比较。
3. 更新 `/home/rui/projects/weather-predict/city_pools.py`。
4. 更新这份文档。
5. 更新 `docs/WEATHER_STRATEGY_ENTRYPOINT.md`。
6. 如果新增或删除 docs 文件，同步更新 `AGENTS.md` 和 `CLAUDE.md` 的文档索引。
7. 部署 N100 前先本机 `py_compile`。
8. 部署到 N100 后跑 `scripts/ops/doctor_restart.sh` 和 smoke check。
