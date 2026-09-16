# 天气概率模型 · 专家评估与实测（v2 · 2026-06-05）

Status: current-reference
Updated: 2026-06-09 metadata pass; preserve content dates below
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md; reference only, not production source of truth


> 时间点快照评估。回答「我们现在算 `model_p_yes` 的模型，算法、缺陷、好处，以及今天该上什么」。
> 证据基于直读 N100 生产代码 + 离线 Brier 实测（time-split + leave-one-city-out 两种 holdout）。
> 代码位置均在 N100 `jiarui@192.168.0.200:~/projects/weather-predict`。
> 口径来源：[WEATHER_ANALYSIS_CONTRACT.md](WEATHER_ANALYSIS_CONTRACT.md)、[WEATHER_SYSTEM_CONTRACT.md](WEATHER_SYSTEM_CONTRACT.md)。
> 综合策略 × 模型复盘：[WEATHER_STRATEGY_AND_MODEL_REVIEW_2026-06-05.md](WEATHER_STRATEGY_AND_MODEL_REVIEW_2026-06-05.md)。
> 分阶段改造方案见：[WEATHER_PROBABILITY_MODEL_ROADMAP.md](WEATHER_PROBABILITY_MODEL_ROADMAP.md)。
> 完整校准报告：[2026-06-05-probability-calibration.md](analysis/2026-06/2026-06-05-probability-calibration.md)。

## 0. TL;DR（先读这段）

### 0.1 ⭐ 最重要的结论（v2 新增，2026-06-05 实测）

**`model_p_yes` 单独用输给市场，但 `0.30 * model + 0.70 * market` 凸组合稳定击败市场。**

| holdout | raw model Brier | market Brier | ensemble(0.3) Brier | 模型相对市场 | ensemble 相对市场 |
|---|---:|---:|---:|---:|---:|
| time-split (80/20 by date) | 0.22261 | 0.17154 | **0.17000** | +29.8%（劣化） | **−0.9%（改善）** ⭐ |
| leave-one-city-out (24 城) | 0.20833 | 0.17944 | **0.17241** | +16.1%（劣化） | **−3.9%（改善）** ⭐ |

- **意义**：raw model 自己用是"反 alpha"，但**对市场带有 30% 权重的信息补充**——市场已经吃掉绝大部分公开信息，模型只能贡献最后 30% 的修正。
- **今天就能上**：把策略的下单 edge 从 `model_p_yes - market_price` 换成 `(0.30*model_p_yes + 0.70*market_price) - market_price`（数学上等价于把 raw edge 缩放为 0.30 倍）。零数据成本，纯运算。
- **per-city 黑名单**（LOO 上 raw model 比市场差 ≥9% Brier）：Milan / Lucknow / Austin / Beijing —— 这 4 城建议**禁用纯 raw model 信号**，只走 ensemble。
- 详细行动项（含部署流程、风险、验证窗口）见 [WEATHER_STRATEGY_AND_MODEL_REVIEW_2026-06-05.md](WEATHER_STRATEGY_AND_MODEL_REVIEW_2026-06-05.md) §3.

### 0.2 原有诊断（仍然成立）

1. **生产端只跑最简单的 baseline 模型**：单点预报 + 全年全局经验误差 bootstrap（`pm_edge_compare.compute_bracket_probs`）。
2. **更聪明的 v2 条件模型（k-NN 按云量/风/露点找相似日）和 multi-model ensemble 全在离线，没上线**。原始原因是**早期实测效果不及 baseline 单点+全年误差 bootstrap**（v2 先跑了一版初始版，eval 没赢过 baseline，于是回退用 baseline 跑通生产）。后来 WU 数据采集退化（`dewpt`/`wspd`/`wx_phrase` 三个特征 0% 覆盖，已退化成 `IEM_ASOS_METAR_FALLBACK` 只带温度，预报缓存也只有 `temperature_2m`），即便现在想复活 v2 也跑不起来（实测见 §4），但这是**第二层次的原因，不是原始原因**。
3. **「连贯分布」在 snapshot 里已经存在**：生产对一个 city-day 一次性算完所有 bracket，逐挡位概率天然 Σ≈1。连贯性是被下游逐腿过滤 + 等额 sizing 抹掉的（见 [WEATHER_CITY_DAY_PORTFOLIO_OPTIMIZER_DESIGN.md](WEATHER_CITY_DAY_PORTFOLIO_OPTIMIZER_DESIGN.md) 顶部复核说明）。
4. **零数据成本增益（实测）**：只把误差分布**按季节条件化**（k-NN by day-of-year），LOO Brier **平均 +9.02% / 中位 +7.82% / 52 城 44 城改善**。强季节性大陆城市收益最大（Seoul +49.7%、Beijing +33.7%、Boston +14.5%、Warsaw +8.7%），热带/海洋性城市几乎无改善（Jakarta/Karachi/LA/Phoenix），符合物理直觉。**与 0.1 的 ensemble 正交**——季节条件化改 raw model 本身，ensemble 是 model×market 的事后融合，两者可叠加。
5. **live forecast 版本/稳定性是当前新风险**：强 side flip 的主要驱动不是盘口跳，而是 Open-Meteo live forecast max 半小时内移动 1-3°F；窄 bracket 经 bootstrap+rounding 后会把 1-3°F 放大成 30-60 个概率点。
6. **新优先级（v2 修正）**：
   1. **⭐ 上线 0.3-model + 0.7-market ensemble**（今天即可，Brier 已 validated）
   2. 上线季节条件化误差分布（零数据成本、已实测）
   3. 核对 PM 结算口径 vs WU 训练源（排雷）
   4. 落盘 forecast version/hash 并治理半小时跳变
   5. 修 WU 特征采集以激活完整 v2
   6. 接双模型 spread
   7. 最后才谈组合优化器

---

## 1. 生产模型现状（实际在跑的）

入口：`paper_snapshot.py`，每个 city-day 每半小时 snapshot 调一次。

```
① 每城固定最优模型 all_models.get(city, 'gfs')（ECMWF 或 GFS），ECMWF 失败 fallback GFS
② errors = WU实测日最高(整数°F) − 模型预报日最高   ← **实际 ~735 天历史**（cache 文件名写 `gfs_365d_*` 但实际包含 ~2 年），要求 ≥30 天
③ simulated = 今日预报 + [每个历史 error]，四舍五入到整数挡位
④ P(bracket) = simulated 落入该挡位的比例
⑤ edge = P(bracket) − market_yes_price
```

- 非参数经验误差分布的 **bootstrap**，不是正态 CDF。
- **预报与误差严格配对**（`fetch_live_ecmwf`↔`compute_ecmwf_error_distribution`，`fetch_live_gfs`↔`compute_error_distribution`），代码显式防「GFS误差套ECMWF预报」错配。
- `compute_bracket_probs(fcst, errors, 整个market的brackets, unit)` **一次算完一个 city-day 所有挡位** → 同一 snapshot 逐挡位概率 Σ≈1（连贯分布天然存在）。
- °F→°C 转换做过代数等价性验证（Tokyo 326 天 0 分歧）。

离线但**未上线**（被 `roi_compare_branches.py` / `scripts/archive_exploration/` / `lib/*_eval.py` 引用）：
`lib/conditional_error_model.py`（v2 条件 k-NN）、`lib/multi_model_ensemble.py`（多模型 spread）、`lib/bias_corrector.py`、`lib/uncertainty_filter.py`、`calibration_backtest.py` + `calibration_results_v5.json`。

---

## 2. 做得好的地方（不要推翻重做）

1. **经验分布 > 正态 CDF**：bootstrap 自动吸收偏度/肥尾/系统性偏差。模型系统性偏冷 X°F，误差分布均值就是 +X，**自动去偏**。
2. **每城选最优模型，且实证选的**：`calibration_results_v5.json` 里 NYC GFS `corr=0.997 / rmse=1.54°F` vs ECMWF `rmse=2.98°F` —— 确实该给 NYC 选 GFS。点预报本身极准。
3. **预报-误差严格配对**，避免错配（代码专门处理）。
4. **有留一法 Brier 回测框架**，具备科学评估能力。

---

## 3. 关键缺陷（按影响排序）

### C1 ⭐ 生产用无条件全局 bootstrap，把全年所有季节/天气型误差当 i.i.d. 混在一起
夏天误差和冬天误差、晴天和雨天混成一锅 → 分布「被污染地变宽」→ 每个具体挡位概率被摊薄 → 峰值挡位 `P(YES)` 系统性偏低。**与已知「BUY_YES 胜率仅 12%」高度吻合**：不是 YES 方向不行，是模型不够 sharp。
→ §5 实测：**只按季节条件化即 +9% Brier**，证实此诊断。

### C2 ⭐ 训练口径 vs 结算口径是否同源（隐藏漏损）
误差锚在 `WU 站点日最高(整数°F)`。但 Polymarket 结算用哪个站/哪个时窗的 max？若 PM 结算源 ≠ WU 训练源，整个误差分布系在错的锚上。`corr=0.997` 说明点预报近乎完美，**剩下能输的钱几乎全在这个口径对齐上**。必须专门核对一次。

### C3 误差全样本等权，无时间衰减/无模型版本对齐
~735 天里一年前误差与昨天等权；GFS/ECMWF 每年升级 cycle，老误差可能来自旧模型版本，漂移未处理。建议时间衰减加权或滚动窗（可与 C1 季节条件化叠加）。

### C4 双模型 spread 信息被丢了
生产每城只用单模型。ECMWF 与 GFS 的**分歧度本身就是不确定性信号**（一致→分布更窄可加仓；分歧大→更宽降仓）。`multi_model_ensemble.py` 已算 spread 却没接进生产概率，是免费信息浪费。

### C5 概率没做事后校准
有 `calibration_results` 但仅诊断；生产概率直接用 bootstrap 频率，无 isotonic/Platt/温度缩放。Brier 回测应顺带产出校准曲线，过/欠自信用一个标量参数即可修。

### C6 阈值偏薄
`≥30 天`出分布、k-NN `k=30`，对季节性强的城市偏薄；同季同天气型时有效近邻可能不足。

### C7 ⭐ live forecast 输入没有版本锁定，窄 bracket 会放大半小时跳变
生产 `paper_snapshot.py` 每半小时实时调用 Open-Meteo live GFS/ECMWF endpoint，
`fetch_live_gfs()` / `fetch_live_ecmwf()` 只取当天 hourly `temperature_2m`
的 `max(valid)`。snapshot 里只记录 `forecast_max_f` 和估算的
`model_init_utc_estimated`，没有记录真实 forecast data version、hourly
vector hash、max 所在小时。

2026-05-31/2026-06-01 强 side flip 复盘显示：

- 过滤条件：相邻 snapshot 方向变化，前后均 `abs_edge >= 0.10`，entry price 在 `[0.25, 0.75)`。
- `transition_count=67`
- `median_abs_d_forecast=2.1°F`，`mean_abs_d_forecast=2.2254°F`
- `median_abs_d_p=0.3932`，`mean_abs_d_p=0.4022`
- `median_abs_d_yes_px=0.01`，`mean_abs_d_yes_px=0.0291`
- `abs_d_forecast_ge_1f=64/67`，`abs_d_forecast_ge_2f=36/67`
- `model_init_changed=5/67`

结论：强 flip 主要由 `forecast_max_f` 跳变驱动，不是市场价格变化。
由于 `compute_bracket_probs()` 把 `forecast_max_f + 历史误差样本` round 到
整数温度再统计落入 bracket 的比例，`70-71`、`90-91` 这种 2°F 窄桶会把
1-3°F 的 forecast max 变化放大成 30-60 个概率点。

典型例子：

| target_date | city | bracket | from_ts | to_ts | side | forecast | p_yes | yes_px |
|---|---|---|---|---|---|---:|---:|---:|
| 2026-06-01 | LA | 70-71 | 2026-06-01T01:30:53Z | 2026-06-01T02:00:53Z | YES → NO | 70.4 → 69.1 | 0.6204 → 0.2286 | 0.405 → 0.405 |
| 2026-06-01 | Miami | 90-91 | 2026-06-01T01:30:53Z | 2026-06-01T02:00:53Z | NO → YES | 92.2 → 90.3 | 0.1986 → 0.6354 | 0.425 → 0.445 |

治理方向：在 snapshot 中落盘 `forecast_max_hour_local`、`forecast_values_hash`
或完整 hourly vector；如果同一 city-date 最近两次 `forecast_max_f` 变化超过
1°F，或同一 bracket 当日出现 YES/NO flip，则降 size / shadow。

---

## 4. v2 条件模型为何没上线

### 4.0 原始原因（早期 eval 输给 baseline）

**v2 不是因为没机会跑而没上线**。早期跑过一版初始 v2（按云量/风/露点找相似日 k-NN），实测 PnL/Brier 没赢过 baseline 单点+全年误差 bootstrap，于是回退用 baseline 跑通生产，腾出精力先调其他链路（sizing、入场带、城市池）。这是历史决策。

### 4.1 当前再启用 v2 跑不起来：数据缺口（实测）

后来数据采集退化（见下），即便现在想复活 v2 也跑不起来。这是**第二层原因**，不是原始原因。

v2 `compute_bracket_probs_conditional` 用 5 个特征做 k-NN：`cloud_score`（来自 `wx_phrase`）、`mean_wspd`、`mean_dewpt`、`season_sin`、`season_cos`，**且要求 5 特征全齐才保留样本**。

实测当前 N100 数据（`cache/wu_obs/wu_obs_*.csv`，52 城全扫）：

| 特征 | 来源列 | 当前覆盖 |
|---|---|---|
| `mean_dewpt` | `dewpt` | **0 / 全部行**（空） |
| `mean_wspd` | `wspd` | **0 / 全部行**（空） |
| `cloud_score` | `wx_phrase` | 列非空但**全是占位符 `IEM_ASOS_METAR_FALLBACK`** → `_wx_to_cloud` 返回 None |
| `season_sin/cos` | 日期推导 | 100% |

结果：v2 在每城丢弃 ~99.4–100% 历史天（「特征缺失」），样本 <60 全部 skip，**无法产出任何概率**。

根因：WU 观测采集已退化为 IEM ASOS METAR fallback，只回温度，丢了露点/风/天气现象。预报缓存（`gfs_v4_*` / `ecmwf_v4_*`）的 `hourly` 也只有 `time` + `temperature_2m`，**两个数据源都拿不到云/风/露点**。

→ 要激活完整 v2，必须先**修复 WU 多字段采集**（或改用 open-meteo 重新拉 cloudcover/windspeed/dewpoint 作为前瞻条件特征——后者反而是决策时真正可见的量）。

---

## 5. 实测：季节条件化（零新数据，现在可上线）

既然 v2 的天气特征拿不到，但**季节可从日期算出来**，故单独测「按季节 k-NN 条件化 vs 全年全局 bootstrap」，直接检验 C1。

- 方法：留一法（LOO），baseline 同样 LOO 排除当天（公平）。threshold offset −5..+5，k=30，按 day-of-year 圆周距离选近邻。
- 数据：N100 `cache/`（gfs_v4 误差 + WU 温度），52 城，n≥60。
- 复跑：N100 `/tmp/season_brier.py`（驱动脚本，未入库；逻辑见本节）。

**结果：平均 Brier +9.02% / 中位 +7.82% / 52 城 44 城改善（>0）。**

强季节性城市收益最大，热带/海洋性城市几乎无改善（符合物理）：

| 收益梯队 | 城市（Δ Brier） |
|---|---|
| 大赢（>15%） | Seoul +49.7%、Beijing +33.7%、Lucknow +24.3%、Istanbul +21.3%、Guangzhou +20.6%、PanamaCity +19.1%、TelAviv +18.3%、BuenosAires +17.4%、Chongqing +15.2% |
| 中等（5–15%） | Boston +14.5%、Houston +13.9%、Munich +13.6%、Busan +13.0%、Moscow +12.7%、Chengdu +12.7%、Dallas +11.4%、Seattle +11.1%、MexicoCity +11.1%、KualaLumpur +10.7%、Singapore +9.9%、Minneapolis +9.6%、Taipei +9.6%、Lagos +9.5%、Warsaw +8.7%、Paris +8.7%、Shanghai +7.9%、SanFrancisco +7.7%、CapeTown +7.2%、Ankara +6.2%、Helsinki +5.9%、Milan +5.9%、HongKong +5.9% |
| 微弱/持平（0–5%） | SaoPaulo +4.2%、Atlanta +3.7%、Miami +3.6%、Shenzhen +2.9%、Manila +2.9%、Madrid +2.9%、Wuhan +1.7%、NYC +1.7%、Jeddah +1.4%、Tokyo +1.0%、London +0.4% |
| 略负（<0） | Karachi −0.05%、LA −0.3%、Jakarta −1.0%、Phoenix −1.2%、Wellington −1.4%、Amsterdam −1.8%、Chicago −2.3%、Denver −2.4% |

**读法与注意**：
- 这是 LOO 校准回测，不是前瞻 live 检验；**Brier 改善 ≠ 必然 PnL 改善**，落地前需验证它确实 sharpen 了「该赢的那一侧」（尤其改善弱 BUY_YES）。
- 略负的多是低季节性城市（热带/海洋性）；**应做成 per-city 开关**：强季节城市上季节条件化，弱季节城市保留全局 bootstrap。
- 季节条件化与未来恢复的完整 v2（加云/风/露点）正交，可叠加。

---

## 6. 与上层「整组决策」的关系（先后顺序）

「修模型」与前面对话讨论的「city-day 整组决策」是**两个正交改进，但有先后**：

> 若分布本身欠 sharp（C1，YES 12% 胜率根因），在烂分布上做再精的组合优化器也是垃圾进垃圾出。

**正确顺序：先把分布修准（C2 排雷 → C1 季节条件化），再上组合软塑形。** 后者解决「别浪费已有分布」，前者解决「让分布值得用」。

---

## 7. 建议优先级

| 优先级 | 动作 | 性质 | 成本 | 依据 |
|---|---|---|---|---|
| 1 | 核对 PM 结算口径 vs WU 训练源（站点/时窗/max 定义） | 排雷 | 低 | C2 |
| 2 | 上线**季节条件化**误差分布（per-city 开关，强季节城开） | 增益，已实测 | 低（纯日期） | §5 |
| 3 | 落盘 forecast version/hash/max_hour，并对半小时 forecast jump / side flip 降 size 或 shadow | 风控 | 低-中 | C7 |
| 4 | 修复 WU `dewpt`/`wspd`/`wx_phrase` 采集（或改 open-meteo 拉云/风/露点）→ 激活完整 v2 | 增益 | 中 | §4 |
| 5 | 接双模型 spread 做不确定性缩放 | 免费信息 | 中 | C4 |
| 6 | 误差加时间衰减/滚动窗 + 概率事后校准（isotonic/温度缩放） | 稳健性 | 中 | C3/C5 |
| 7 | city-day 组合软塑形（砍同挡冲突/薄edge/高 sum_p_hit + 总额上限） | 资金效率 | 中 | optimizer 文档 |

---

## 附：复现命令

```bash
# 直读生产模型
ssh jiarui@192.168.0.200 'cd ~/projects/weather-predict && sed -n "1,140p" pm_edge_compare.py'

# v2 条件模型 / 留一 Brier 评估入口
ssh jiarui@192.168.0.200 'cd ~/projects/weather-predict && python3 lib/conditional_error_model.py --city NYC'
#   注意：当前数据下因 dewpt/wspd/wx 缺失会全城 skip（见 §4）

# 季节条件化 Brier 实测（本评估用脚本，存于 N100 /tmp/season_brier.py）
ssh jiarui@192.168.0.200 'cd ~/projects/weather-predict && .venv/bin/python3 /tmp/season_brier.py'
```
