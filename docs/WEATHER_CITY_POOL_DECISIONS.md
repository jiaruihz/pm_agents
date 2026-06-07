# 天气策略城市池决策记录

更新时间：2026-06-08

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

当前版本：v4  
决策日期：2026-06-06  
T1 城市数：22

### 当前 T1 城市

| 区域/用途 | 城市 |
|---|---|
| 美洲 | Boston, LA, Miami, NYC, Phoenix, Seattle |
| 欧洲 | Ankara, Istanbul, London, Madrid, Moscow, Munich, Warsaw |
| 亚洲 / 中东 | Chengdu, Guangzhou, Jeddah, Karachi, Lucknow, Manila, Shanghai, Singapore, Tokyo |

完整列表：

```text
Ankara, Boston, Chengdu, Guangzhou, Istanbul, Jeddah, Karachi, LA,
London, Lucknow, Madrid, Manila, Miami, Moscow, Munich, NYC, Phoenix,
Seattle, Shanghai, Singapore, Tokyo, Warsaw
```

### 当前不在 T1 的重点城市

| 城市 | 当前状态 | 原因 |
|---|---|---|
| Paris | T2 / research only | 2026-05-29 降级：city×side 质量不稳定，保留 research only |
| Beijing | T2 / research only | live ROI -7.6%，不继续扩 size |
| Chicago | T2 / research only | live ROI -18.5%，样本虽小但表现弱 |
| Austin | T2 / research only | v2 时已移出，赔率结构不利 |
| Amsterdam | T2 / research only | 2026-06-06 降级：三策略实例 all-history live PnL -30.43 / ROI -82.0%，V1-only ROI -61.9% |
| BuenosAires | T2 / research only | 2026-06-06 降级：三策略实例 all-history live PnL -28.11 / ROI -49.6%，V1-only ROI -22.2% |

### pm_agent live allowlist 覆盖层（2026-06-08）

这里不是修改 `weather-predict/city_pools.py` 的全局 T1/T2 source of truth，
而是 `pm_agent` live 启动脚本里的实例级 allowlist。目的是先把近期退化的
`mid_price_core_v1_25_75` 分支降风险，同时不影响天气采集、settlement、
paper/research 和 side-band 影子观察。

| strategy_instance | live allowed cities | 说明 |
|---|---|---|
| `mid_price_core_v1_25_75` | Boston, Chengdu, Guangzhou, Istanbul, LA, London, Lucknow, Madrid, Manila, Miami, NYC, Phoenix, Seattle, Shanghai, Singapore, Tokyo, Warsaw | 从该实例 live allowlist 移除 Ankara, Jeddah, Karachi, Moscow, Munich；BuenosAires 本来不在该 allowlist |
| `mid_price_core_v1_side_band` | Boston, LA, London, Miami, NYC, Phoenix, Shanghai, Tokyo, Warsaw | 保持 legacy core 城市池；本来就不包含 BuenosAires/Munich/Jeddah/Karachi/Moscow/Ankara |

共同 side gate 仍为 `NYC:BUY_YES`，所以 `NYC` 仍在两个实例城市池里，但只允许符合条件的 `BUY_NO`。

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

### 2026-06-08：收缩 v1_25_75 live allowlist，弱近期 ECMWF 城市先退出该实例

动作：

- `pm_agent` N100 部署到 commit `4149a13`。
- `mid_price_core_v1_25_75` live allowlist 从 v4 T1 22 城收缩到 17 城。
- 从该实例 live allowlist 移除：`Ankara`, `Jeddah`, `Karachi`, `Moscow`, `Munich`。
- `BuenosAires` 已在 2026-06-06 降级为 T2 / research only，本来不在该实例 allowlist。
- `mid_price_core_v1_side_band` 城市池不变，仍为 legacy core 9 城。

依据：

- `docs/analysis/2026-06/2026-06-07-mid-price-core-v1-city-model-downgrade.md`
- `docs/analysis/2026-06/2026-06-07-v1-ecmwf-blocked-side-band-overlay.md`

历史 overlay 结论：

| slice | baseline v1_25_75 | side-band kept subset | 结论 |
|---|---:|---:|---|
| 2026-06-01 以来，6 城市 ECMWF 历史 fills | 95 fills / PnL -99.14 / ROI -39.8% | 35 fills / PnL -9.85 / ROI -10.2% | side-band 明显减亏但仍未证明正 alpha |

交易动作：

- 这些城市不再进入 `mid_price_core_v1_25_75` live。
- 不为此新增 city×model block 参数；保持简单的实例级城市 allowlist。
- side-band 对这些城市不直接 live，因为它本来不含这些城市；如要研究，走 shadow/paper。

部署验证：

- N100 commit：`4149a13`
- 新 live loop pid：`mid_price_core_v1_25_75=387632`, `mid_price_core_v1_side_band=387645`
- 首轮运行：2026-06-08 00:10/00:11 北京时间，两个实例 `contract_alerts=[]`
- 首轮配置确认：
  - v1_25_75 `allowed_cities=Boston,Chengdu,Guangzhou,Istanbul,LA,London,Lucknow,Madrid,Manila,Miami,NYC,Phoenix,Seattle,Shanghai,Singapore,Tokyo,Warsaw`
  - side-band `allowed_cities=Boston,LA,London,Miami,NYC,Phoenix,Shanghai,Tokyo,Warsaw`

### 2026-06-06：停 V2 live，Amsterdam / BuenosAires 降级到 T2

动作：

- `mid_price_core_v2_25_75` 从 live 默认启动集中移除；生产已停止 V2 branch loop。
- `Amsterdam` 降级到 `RESEARCH_T2_CITIES`。
- `BuenosAires` 降级到 `RESEARCH_T2_CITIES`。
- `NYC` 保留在 T1，但 pm_agent live signal builder 暂时 hard-block `BUY_YES`；`BUY_NO` 继续允许。
- 两城仍保留在 `FULL_CITY_CONFIGS`，继续收集、结算、paper/research，不删除历史或天气配置。

三实例共同窗口（`target_date >= 2026-06-01`, `trade_class='live_real'`）：

| strategy_instance | settled_cost | realized_pnl | ROI | open_cost | mid MTM |
|---|---:|---:|---:|---:|---:|
| `mid_price_core_v1_side_band` | 134.70 | +25.27 | +18.76% | 34.02 | -1.99 |
| `mid_price_core_v1_25_75` | 431.02 | +8.85 | +2.05% | 140.20 | -16.40 |
| `mid_price_core_v2_25_75` | 252.31 | -20.07 | -7.96% | 44.42 | -4.86 |

V2 结论：

- V2 主动抢单本身不是主要问题：V2 BUY_YES 的 `fill PnL - plan PnL = +11.63`，实际成交价比 plan 更好。
- 问题在 `0.25-0.75` YES 信号负 alpha：共同窗口 V2 BUY_YES `-22.64 / ROI -19.45%`，V1 BUY_YES `-8.50 / ROI -8.40%`。
- V2 把负 alpha YES 样本更积极地成交出来，因此不再作为 live 默认实例；后续只允许显式 shadow/实验启动。

城市降级证据：

| city | 三实例 all-history settled_cost | PnL | ROI | V1-only PnL / ROI | 结论 |
|---|---:|---:|---:|---:|---|
| Amsterdam | 37.11 | -30.43 | -82.0% | -10.85 / -61.9% | 三实例、两侧几乎全负，降级 T2 |
| BuenosAires | 56.63 | -28.11 | -49.6% | -8.12 / -22.2% | V2 YES 满亏 cluster，候选反事实也偏负，降级 T2 |

NYC side-block 证据：

| slice | BUY_NO | BUY_YES | 结论 |
|---|---:|---:|---|
| 2026-05-31..2026-06-06 live_real settled | +0.68 / ROI +1.7% | -31.00 / ROI -75.4% | YES 近期集中亏损，NO 仍未坏 |
| all-history live_real settled | +15.48 / ROI +23.3% | -42.72 / ROI -68.7% | 亏损边界在 side，不是整城 |
| 机会层 recent 反事实 | +10.50 | -17.65 | YES 的机会层也转负 |

处理：先用 pm_agent-side hard gate block `NYC:BUY_YES`，避免 90 分钟 lookback 的旧 snapshot 继续漏出 YES 信号；后续若 weather-predict `city_pools.py` 同步 git 化，再把 NYC 写入 `CITY_ALLOWED_SIDES`。

回滚条件：

- V2 仅在有新的 YES 过滤/城市禁入逻辑后 shadow 重跑；不得直接恢复 live 默认启动。
- Amsterdam / BuenosAires 若要回到交易池，至少需要新的 paper/research 样本证明 city×side 正 alpha，并先 shadow 观察。

部署记录：

- `pm_agent` git-first 部署到 N100 commit `2458696`：V2 默认不启动，`mid_price_core_v1_25_75` 显式使用 v4 T1 allowlist。
- `weather-predict` N100 目录当前不是 git worktree；2026-06-06 走 fallback 热修 `city_pools.py`，备份为 `/home/jiarui/projects/weather-predict/city_pools.py.bak.codex_20260606_v2_stop_city_demotion`。
- 当前 Codex workspace 不允许写 `/home/rui/projects/weather-predict`，所以本机 weather-predict 开发副本需后续手动同步同一城市池改动，避免下次从本机 weather-predict 部署时覆盖 N100 热修。

### 2026-05-29：Madrid NO-only 重新进 T1，Paris 降级

动作：

- `Madrid` 重新进入 `TRADING_T1_CITIES`，但只允许 `BUY_NO`。
- `Paris` 从 `TRADING_T1_CITIES` 移到 `RESEARCH_T2_CITIES`。
- `Shanghai` 保留 T1，但只允许 `BUY_NO`。
- 其余 T1 城市恢复默认双侧，旧的中等置信 BUY_YES block 不再作为主配置。
- 生产配置改为 `CITY_TRADING_CONFIG` 嵌套结构：`TRADING_T1_CITIES` / `RESEARCH_T2_CITIES` / `CITY_ALLOWED_SIDES`。

依据：

| 层 | Madrid BUY_NO | Madrid BUY_YES |
|---|---:|---:|
| 反事实机会 | +10.0, n=12, win 66.7% | -1.85, n=8, win 25% |
| live_real settled | +2.38, n=6, ROI 8.3%, win 50% | -9.9, n=2, ROI -100%, win 0% |
| paper | +11.53, n=18, ROI 10.6%, win 70.6% | -4.95, n=10, ROI -19.8%, win 20% |

结论：

- Madrid 的亏损不是整城问题，而是 YES 侧黑洞；整城踢出会误杀 NO 正腿。
- Madrid 不是高置信加仓城市，按 NO-only re-entry 观察；累积 5-10 笔 settled NO 后复核。
- 若 Madrid NO realized PnL <= -5 或 ROI < -20%，回退到 T2。

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
