# 天气策略 × 概率模型 复盘 — 2026-06-05

Status: `snapshot`。本文保留 2026-06-05 时间点证据，不定义当前生产口径；当前入口见 [WEATHER_STRATEGY_ENTRYPOINT.md](WEATHER_STRATEGY_ENTRYPOINT.md)，edge engine 当前状态见 [WEATHER_EDGE_ENGINE_CURRENT_STATE_2026-06-06.md](WEATHER_EDGE_ENGINE_CURRENT_STATE_2026-06-06.md)。

> 整合一个月 live_real 实盘 + 概率校准实测 + 当前数据管道审计。
> 数据源：`runtime/weather.db`（`fact_trades` / `fact_signal_candidates`），口径见
> [WEATHER_ANALYSIS_CONTRACT.md](WEATHER_ANALYSIS_CONTRACT.md) 与
> [WEATHER_DATA_CANONICAL_SOURCES.md](WEATHER_DATA_CANONICAL_SOURCES.md)。
> 概率校准实测见 [docs/analysis/2026-06/2026-06-05-probability-calibration.md](analysis/2026-06/2026-06-05-probability-calibration.md)。

---

## 0. TL;DR

**实盘 9 天 67 fills，+$31.72，ROI +11%**（窗口 2026-05-24 → 2026-06-01，notional 总额 $288.3）。但**全部利润几乎集中在 BUY_NO（38 fills $19.97 / 73.7% 胜率）**。BUY_YES 实盘 13 fills 仍亏少胜小（38.5% 胜率，$11.75 PnL 主要来自 1 单 Miami +$14.23 的运气）。

**概率模型仍输给市场**：raw `model_p_yes` 在 time-split 上 Brier 0.2226 vs 市场 0.1715（**+29.8% 相对劣化**），LOO 上 +16.1%。但 **30% 模型 + 70% 市场凸组合**在两种 holdout 下都赢市场（time-split −0.9%, LOO −3.9% relative Brier）。这是**今天就能上的免费增益**。

**3 个立刻可执行的动作**：
1. ⭐**SHIP**：把策略的下单概率从 `model_p_yes` 切到 `0.30 * model_p_yes + 0.70 * market_implied`（边际成本：零；Brier-validated；BUY_YES 系列直接受益）。
2. ⭐**RAW-MODEL 城市黑名单**：Milan / Lucknow / Austin / Beijing 在 LOO 上 raw model >9% Brier 劣化，**应禁用纯 raw model 信号**，只走 ensemble。
3. ⭐**策略实例瘦身**：`mid_price_core_v2`（−$7.30 / 11 fills）和 `maker_queue_v2`（+$11.93 / 18 fills，PnL/fill < v1）在 1 个月样本下没显著好于 `maker_queue_v1`（+$25.06 / 16 fills），考虑暂停 v2 系列腾出 notional 预算。

---

## 1. 实盘 1 个月战绩（live_real, settled）

### 1.1 总览

| 指标 | 值 |
|---|---:|
| 窗口 | 2026-05-24 → 2026-06-01（9 天） |
| 总 fills | 67 |
| 总 notional | $288.3 |
| 已结算 PnL | **+$31.72** |
| ROI | +11.0% |

口径：`fact_trades.trade_class='live_real'` AND `settlement_status='settled'`，PnL 取 `pnl_usd_at_fill`。

### 1.2 BUY_NO 才是利润来源

| side | n | PnL | notional | win_rate |
|---|---:|---:|---:|---:|
| BUY_NO | 38 | **+$19.97** | $180.35 | **73.7%** |
| BUY_YES | 13 | +$11.75 | $52.5 | 38.5% |
| (sim 中间状态) | 16 | n/a | n/a | n/a |

BUY_NO 73.7% live 胜率与既知的 paper-side 76% 高度一致，**实盘验证了 BUY_NO 边际**。BUY_YES 38.5% 比早期 paper-side 12% 大幅好转，但小样本（13 单）+ 主要利润来自 Miami 1 单 +$14.23（90-91 bracket NO→YES side-flip 之后命中），**不要据此宣告 BUY_YES 已通**。

### 1.3 策略实例对比

| strategy_name | n | PnL | PnL/fill |
|---|---:|---:|---:|
| `maker_queue_v1` (entry 0.25-0.75, mqe 0.03) | 16 | +$25.06 | **+$1.57** |
| `maker_queue_v2` (entry 0.25-0.75) | 18 | +$11.93 | +$0.66 |
| `mid_price_core_v1` (entry 0.35-0.65) | 2 | +$4.39 | +$2.19 |
| `mid_price_core_v1` (entry 0.25-0.75) | 4 | −$2.36 | −$0.59 |
| `mid_price_core_v2` (entry 0.25-0.75) | 11 | **−$7.30** | −$0.66 |

观察：
- `maker_queue_v1` 单 fill 收益领先，应保留为主力。
- `mid_price_core_v2` 11 fills 都亏，初步信号是负的——下一步要么改参数要么暂停。
- 窄入场带（0.35-0.65）样本 2 单不够下结论，但 per-fill +$2.19 远好于宽带 −$0.59，**值得加注做验证**。

### 1.4 城市维度（live_real, settled, top + bottom）

**top 5（city × side）**：

| city | side | n | PnL |
|---|---|---:|---:|
| Miami | BUY_YES | 1 | +$14.23（单点运气，bracket 90-91） |
| Istanbul | BUY_YES | 2 | +$11.07 |
| Warsaw | BUY_YES | 1 | +$6.90 |
| Paris | BUY_NO | 4 | +$6.58 |
| Karachi | BUY_NO | 2 | +$6.32 |

**bottom（亏 $5+）**：London / Manila / Munich / NYC / Shanghai / Singapore / Warsaw（BUY_NO 这一单与上面 BUY_YES 那单不是同城同 side）各亏 $5（说明这些是单 fill 全 cost loss，多半 final NO 命中）。

### 1.5 候选 → paper → live 漏斗

| city_pool | 候选数 | eligible | paper_ordered | live_filled |
|---|---:|---:|---:|---:|
| `t1_trading` | 6597 | 6597 | 911（13.8%） | **60（0.91%）** |
| `t2_research` | 12781 | 0 | 1345（10.5%） | 1 |
| （未分类） | 1553 | n/a | 182 | 0 |

**关键观察：t1 candidates 6597 中只成交 60 fills（0.91%）**。漏斗形态符合 paper→live 的过滤设计（paper 全打、live 严格 gate），但绝对量小，单月数据**还不足以做城市级 ROI 排序**——绝大多数城市 live_real 只有 1–4 fills。

---

## 2. 概率模型 vs 市场 — 实测

### 2.1 校准结果速读

数据：`signals` join `settlements`（settled）按 `(target_date, city, bracket)` 去重取最近决策 snapshot，n=1577 行 / 49 城 / 2026-05-06 → 2026-06-01。详细见
[2026-06-05-probability-calibration.md](analysis/2026-06/2026-06-05-probability-calibration.md)。

**Time-split (80/20 by date)**：

| 方法 | Brier | Δ vs market |
|---|---:|---:|
| `raw_model` | 0.22261 | **+0.0511 (+29.8%)** |
| `market_baseline` | 0.17154 | — |
| `isotonic_model` | 0.20078 | +0.0292 |
| `platt_model` | 0.20217 | +0.0306 |
| **`ensemble_fixed_w=0.30`** | **0.17000** | **−0.0015 (−0.9%)** ⭐ |
| `ensemble_best_w=0.35` | 0.17115 | −0.0004 |

**Leave-one-city-out (24 城, 单城 ≥30 holdout)**：

| 方法 | Avg Brier | Δ vs market |
|---|---:|---:|
| `raw_model` | 0.20833 | **+0.0289 (+16.1%)** |
| `market_baseline` | 0.17944 | — |
| `isotonic_model` | 0.18976 | +0.0103 |
| `platt_model` | 0.18973 | +0.0103 |
| **`ensemble_fixed_w=0.30`** | **0.17241** | **−0.0070 (−3.9%)** ⭐ |
| `ensemble_best_w` | 0.17256 | −0.0069 |

### 2.2 三条结论

1. **raw `model_p_yes` 单独用没有 alpha 反而扣分**。Time-split 上 Brier 高 30%，LOO 上高 16%。生产端单 raw model 信号下单的策略系（mid_price_core_v2 等）在实盘也亏（§1.3 印证）。
2. **Isotonic / Platt 单独校准不够**——只把 raw model 拉到与市场接近但不超过。
3. **30% 模型 + 70% 市场凸组合稳定击败市场**。fixed_w=0.30 在两种 holdout 下都是最优或接近最优（time-split −0.9%, LOO −3.9% relative Brier），不需要 in-sample tune。这是 **alpha 信号确实存在但 SNR 很低**——市场已经吃掉了大部分信息，模型只剩 30% 的补充权重。

### 2.3 哪些城市 raw model 最差（LOO）

| city | n_test | raw Brier | market Brier | raw−market |
|---|---:|---:|---:|---:|
| Milan | 30 | 0.3384 | 0.2185 | **+0.1200** |
| Lucknow | 30 | 0.2780 | 0.1761 | **+0.1019** |
| Austin | 32 | 0.2624 | 0.1639 | **+0.0985** |
| Beijing | 58 | 0.2143 | 0.1204 | **+0.0939** |
| LA | 44 | 0.2267 | 0.1654 | +0.0613 |
| London | 68 | 0.2321 | 0.1760 | +0.0562 |
| Seoul | 61 | 0.2066 | 0.1515 | +0.0551 |

**Milan / Lucknow / Austin / Beijing** 是 raw model 持续输给市场 ≥9% Brier 的城市，应**禁用纯 raw model 信号**，只走 ensemble；或在生产里设 per-city raw_model 黑名单。

---

## 3. 行动项

### A. 立即可执行（本周）

**A1. SHIP：上线 0.3-model + 0.7-market 概率 ensemble**

- 修改点：N100 `weather-predict` 的 paper_snapshot.py（或下游 signal 生成处）增加 `model_p_yes_ensemble = 0.30 * model_p_yes + 0.70 * market_implied_p_yes`。
- 策略下单时 `edge = model_p_yes_ensemble - market_price` 替代原 `model_p_yes - market_price`。
- 落地必须**双写两个字段**（`model_p_yes_raw` + `model_p_yes_ensemble`）保留诊断；策略可一段时间内同时跑两路 paper 验证再切 live。
- 部署流程走 `weather-strategy-deploy` skill（git-first，不绕过 N100 deploy 脚本）。
- 风险：ensemble 把 alpha 从 raw model 的高/低偏离往市场拉，会让某些以前明显的 edge 变小→ fill 数可能下降；接受这个 trade-off（Brier 改善是事实）。

**A2. RAW-MODEL 城市黑名单**

- Milan / Lucknow / Austin / Beijing 在 LOO 上 raw model 比市场差 ≥9% Brier，**这 4 个城市的纯 raw_model 信号停止下单**。
- 改 N100 `city_pools` 或在 signal 层加 filter：这 4 城只允许 ensemble-edge ≥ threshold 的信号通过。
- 注意：与 A1 配合——A1 上线后这 4 城自然落到 ensemble，无需单独黑名单；如果 A1 暂时无法上，A2 是最小成本止血。

**A3. 策略实例瘦身**

- 暂停 `mid_price_core_v2` 实盘（11 fills −$7.30，且依赖纯 raw model）。
- `maker_queue_v2` 保持观察但不加 notional（PnL/fill < v1 50%+）。
- 主力倾向 `maker_queue_v1`；窄入场带 `mid_price_core_v1 0.35-0.65` 样本太小但单 fill 收益高，可**单独开窄带 paper 验证 2 周**再决定是否加 live notional。
- 部署走 `weather-strategy-deploy` skill。

### B. 验证 / 观察（2-4 周）

**B1.** A1 上线后 4 周内对比 live_real BUY_YES 胜率是否从 38.5% 进一步提升。若提升 → 模型 vs 市场 ensemble 的边际确认；若停滞或下降 → 重新审视 ensemble 权重或某些城市是否需要 per-city 不同权重。

**B2.** 累计 live_real 到 ≥300 fills 后做城市级 ROI 排序（当前 67 fills 不足）。

**B3.** raw model 改造同步进行（与 ensemble 正交）：
- 季节条件化 误差分布（已实测 +9% Brier，见 `WEATHER_PROBABILITY_MODEL_REVIEW.md` §5）。
- WU 数据采集修复 → 激活 v2 条件 k-NN（详见 review §4）。
- forecast version/hash 落盘 + side-flip 风控（review C7）。

### C. 数据管道补漏（下面 §4 详细）

**C1.** N100 `backup_data.sh` systemd timer / cron — 当前最后一次备份是 2026-05-12，已 24 天没新备份，灾备暴露。
**C2.** N100 1.6 GB `runtime/runtime/weather_edge_v1/` 影子目录 — 需文件级 diff 确认可删。

---

## 4. 数据管道当前状态（2026-06-05 审计）

### 4.1 已完成（本次会话内修复）

- ✅ 删除两个 legacy SQLite DB：`runtime/_legacy/weather_v2.db`、`runtime/_legacy/weather_edge_v1_weather.db`（已退役，但 README 还在引用，今天清理）。
- ✅ `sync_weather_remote.sh` 新增 7 个 weather model cache 同步：`gfs_v4 / gfs_daily / ecmwf_v4 / jma_v5 / hrrr_v5 / icon_eu_v5 / arome_v5`（每个 4KB-18MB，全部已 mirror 到本机 `runtime/weather_edge_v1/market_data/cache/`）。
- ✅ `sync_weather_remote.sh` 新增 `output/logs/` 和 pm_agent `runtime/logs/` 同步（debug 用）。
- ✅ 新建 `scripts/ops/sync_n100_backups.sh` 拉取 N100 tar 备份到本机 `runtime/_backups_n100/`（sha256 校验，本机首次拉到 5.5MB + 8.3MB 共 14MB）。
- ✅ `CLAUDE.md` / `AGENTS.md` / `WEATHER_DATA_CANONICAL_SOURCES.md` 同步更新「N100 没有活跃 SQLite」「N100 上有两个 repo（weather-predict + pm_agent runtime）」的拓扑事实。
- ✅ 概率校准脚本 `scripts/analysis/calibrate_weather_probability.py` 落库；产出 JSON+MD 报告。

### 4.2 已知缺口（按优先级）

**P0 — 灾备暴露（必须 1 周内修）**

- N100 `backup_data.sh` 最后一次跑是 2026-05-12 16:41，**24 天没新备份**。生产数据 cache 仍在持续增长但没在备份里。
- 修复：N100 上加 systemd timer（每周一次 `scripts/ops/backup_data.sh`），或在 pm_agent 的 dashboard refresh cron 里串一次。
- 本机 `sync_n100_backups.sh` 已跑通，新备份生成后会自动 mirror。

**P1 — 影子目录残留（影响 N100 磁盘）**

- N100 上 `~/projects/pm_agent/runtime/runtime/weather_edge_v1/` 1.6 GB 影子目录（5-16 ~ 5-31，疑似 cwd bug 产物），未 mirror，未删。
- 下一步：ssh 进 N100 做 `diff -r` 与正确路径对比，确认无独有文件后删除。

**P2 — 数据缺口（影响分析口径）**

- `fact_trades.live_real` 当前 67 fills，**单城样本最大 4 fills**，城市级 ROI 排序不可信，需累计到 ≥300 fills（约 2-3 个月）。
- `live_real` 行数曾经为 0（clob_fill_sync 网络问题已修，但若再次失败 silently fall back 到 0，需要 alert）。
- `decision_window_missing` ~44%——`fact_signal_candidates` 里近一半机会的决策 snapshot 缺失（详见 `WEATHER_DATA_CANONICAL_SOURCES.md` §4.1），反事实分析覆盖不全。
- `gfs_365d_*` cache 文件名标 365 天但实际 ~735 天（已知，不改名，文档已注）。
- WU 观测 `dewpt`/`wspd`/`wx_phrase` 全空（IEM ASOS METAR fallback），导致 v2 条件模型不能 backfill—— roadmap 项，非紧急。

### 4.3 本机 weather.db 表清单（参考）

```
fact_trades                 3940 行（live_real 67 / live_sim 952 / paper 2285 / replay 636）
fact_signal_candidates     20931 行（t1 6597 / t2 12781 / 未分类 1553）
signals                      1577 行（settled, 49 城, 2026-05-06 → 2026-06-01）
orders / fills / settlements / runs / config_versions / strategy_versions / ...
```

---

## 5. 文档更新清单

| 文档 | 改动 |
|---|---|
| 本文（NEW） | 1 月战绩 + 校准 + 数据审计整合 |
| [WEATHER_PROBABILITY_MODEL_REVIEW.md](WEATHER_PROBABILITY_MODEL_REVIEW.md) | TL;DR 前置 "30% model + 70% market beats market by 0.9-3.9% Brier" 校准结论，原 §1-7 保留 |
| [WEATHER_DATA_PIPELINE.md](WEATHER_DATA_PIPELINE.md) | 加 §同步覆盖更新（7 cache 家族、logs、backup sync）+ 删除 legacy DB 段 |
| [WEATHER_DATA_CANONICAL_SOURCES.md](WEATHER_DATA_CANONICAL_SOURCES.md) | 已在本次会话更新（删 legacy DB、加 cache 家族、加 backup 备注） |
| [CLAUDE.md](../CLAUDE.md) | 已加本文档到 "研究与分析报告" 索引（下一步） |

---

## 附：复现命令

```bash
# 1 月战绩
cd /home/rui/projects/pm_agent
.venv/bin/python -c "
import sqlite3; c = sqlite3.connect('runtime/weather.db')
for r in c.execute(\"SELECT side, COUNT(*), ROUND(SUM(pnl_usd_at_fill),2), ROUND(AVG(CASE WHEN pnl_usd_at_fill>0 THEN 1.0 ELSE 0 END),3) FROM fact_trades WHERE trade_class='live_real' AND settlement_status='settled' GROUP BY side\"):
    print(r)
"

# 校准重跑
.venv/bin/python scripts/analysis/calibrate_weather_probability.py \
  --db-path runtime/weather.db \
  --out-json docs/analysis/2026-06/2026-06-05-probability-calibration.json \
  --out-md docs/analysis/2026-06/2026-06-05-probability-calibration.md

# 完整同步 + 备份 + 重建
scripts/ops/sync_weather_remote.sh
scripts/ops/sync_n100_backups.sh
scripts/ops/weather_dashboard_refresh.sh
```
