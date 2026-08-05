# Current-YES 模型地图与版本主线

日期：2026-06-21
状态：`historical / superseded_for_decision_use`

> ⚠️ 本文是 6 月21日时点的历史地图，不再是当前人工决策入口。文中的 live 状态、
> v9/hazard 晋级判断和“下一步联合门”均不得直接恢复。其后已修正固定
> `CITY_MODEL`、forecast/PIT 血缘、city-day 权重、official fee、native settlement
> lattice、obs-age train/serve skew、strict-high/跨日时钟，并完成更长窗口复测。
> 当前策略状态读 `WEATHER_STRATEGY_REGISTRY.md`；hazard 当前结论读
> `analysis/reheat_risk.md` 顶部 2026-07-27 correction。

## 一句话主线

这条方向是 **current-YES / no-reheat**：当某城市当前 running max 所在温度 bracket 看起来能守到结算时，买这个 bracket 的 YES。

如果要看 current YES、higher NO carry、low-price YES reheat reversal 如何共用同一个 reheat-risk 数据/模型底座，读：
[2026-06-21-reheat-risk-yes-no-expression-map.md](2026-06-21-reheat-risk-yes-no-expression-map.md)。

现在的问题不是“有没有模型”，而是模型、规则、执行、LLM 审单混在了一起。正确拆法是：

```text
weather / market snapshot
  -> 状态头：fade-confirmed 或 peak-forming
  -> 概率模型 / 规则阈值
  -> 确定性执行 guard
  -> 可选 LLM/Codex 下单前审单
  -> CLOB order
```

核心区分：

- **概率/规则模型**：判断这笔 current-YES 是否有足够 EV。
- **执行 guard**：挡 stale observation、METAR 更新前后竞态、fresh book 变差、重复敞口。
- **LLM/Codex preflight**：最后一层人工式 reviewer；它不是基础概率模型，目前也没有启用 live blocking。

## 核心诊断（2026-06-21 整合：6/20 归零事件 + 校准证据）

成绩确实差：split live `2026-06-18..06-21` 总 ROI **-3.64%**，已结算亏损**全部**来自 `peak_forming_micro`，集中在 `2026-06-20`（15-6、-10.10%）。下面把"为什么"收敛成一条主线，再分三轴说差在哪。

### 一句话根因：买的是"见新高"，不是"确认今天不再升温"

current-YES 的隐含命题是 **`running max 所在档 = 当天最终档`**。这个命题**只在"今天升温窗口已经关闭"时才成立**。但现在两个 profile 的触发点恰恰发生在升温窗口**还开着**的时候：

- `peak_forming_micro`：旧 live 条件把"新高后 10–60 分钟仍在高位"当成可买形态——这正是日内最可能继续往上爬的时刻；10 分钟只能算微观冷却，**不能**算封顶证据。
- `fade_confirmed`：看到一次回落就买——但"中午回落 ≠ 全天高点结束"（Wuhan 11:00 回落、Singapore 早段 humid/cloudy dip 都是 false fade）。

6/20 的归零不是 6 个独立坏运气，而是**一次大面积 reheat 把全城池同方向的 YES 一起打穿**（$86 铺 20 城、全买 YES ≈ 一笔注）。所以修的方向不是调阈值，是把目标从**"赌新高守住"**换成**"确认升温条件已消失（no-reheat）"**。

### 决定性证据：市场近乎完美校准，live 模型比市场更差

在 `v8 full replay`（`contains_running=1` 子集，3239 rows）上，按市场 YES ask 分桶对真实结算：

| ask 桶 | n | 平均 ask | 真实胜率 | 差(真实−ask) |
|---|---:|---:|---:|---:|
| [0.5,0.6) | 122 | 0.547 | 0.459 | **−0.088** |
| [0.6,0.7) | 137 | 0.651 | 0.613 | −0.038 |
| [0.7,0.8) | 229 | 0.747 | 0.712 | −0.035 |
| [0.8,0.9) | 421 | 0.851 | 0.800 | **−0.050** |
| [0.9,1.0) | 1469 | 0.969 | 0.969 | 0.000 |
| **≥0.70 合计** | 2119 | 0.922 | 0.908 | −0.014 |

三点结论：

1. **市场 ask 本身校准得非常好**（与 v11 registry 的 holdout 一致：market-as-prob AUC 0.929 / logloss 0.304，直接打赢 live base v8/v9 的 0.925 / 0.313）。**市场是更准的概率，live 模型比它略差。**
2. **"model_p > market 才下单"是下单条件（selection），不是模型有偏的独立证据**——成交单里 model_p 必然高于价格是同义反复。但策略因此**专挑"自己（较差的）模型比市场（较准的）高"的地方下注**，那高出来的部分大概率是模型上偏误差，不是 alpha。6/20 Karachi 是教科书例：模型 0.898、市场 0.771、该桶真实 ~0.71–0.80。
3. **每个 ask 桶真实胜率都 < ask（差全为负）**——这一形态上按市价买 YES 本身就轻微 −EV（YES 端有 overround）。策略既在赌一个被高估的方向，又叠了自己的过度自信。**值得正式评估：这一形态真正的 +EV 边，临近收盘很可能是 NO（卖 YES），而不是买 YES。**

> 口径：以上用本机 `theta_yes_current_full_replay_v8/feature_rows.csv`（2026-05-19..06-14）+ raw live order JSONL；未绕过 `fact_trades` 自算 fill PnL。模型 vs market 的 holdout 排名引用 v11 registry。

### 三轴差距：模型 / 买点 / 时间，差在哪、往哪修

| 轴 | 现在怎么做 | 差在哪（本质） | 方向 |
|---|---|---|---|
| **模型** | 学"当前档是不是最终档"的中位数分类，线性 logistic，holdout 还输市场 | 没学"还能不能再升"。缺**升温燃料**特征：暖平流（风向×上游暖，现在只用风速没用方向）、云导数（转晴=还能升/堆云=封顶）、过没过太阳峰、露点天花板、边界层。线性表达不了"晴∧已过峰∧低露点→锁死"这种**合取**封顶条件 | 把预测目标从"最终档是它"换成 **"剩余升温潜力 ≤ 0 的概率（封顶/hazard 概率）"**；label 改成"running max 之后是否还创新高/升过档"。这正是 `hazard v2` 的形态，但还没过门 |
| **买点** | `model_p > 市价 + edge` 就买 | 拿"比市场更乐观"当买点 = 专挑自己高估处下注，且买在 YES 贵边（高 ask 真实胜率 < 价格） | 买点改成 **"封顶条件成立 ∧ 市场还没把封顶 price 进去"**。市场已 0.85 你也觉得封顶 → 没 edge；市场还在 0.6 怕 reheat、物理上已封顶 → 这才是 edge。高 ask 区目前无过门 edge → 先 shadow/砍 size，必要时反手 NO |
| **时间** | 13–15 点本地、新高后 10–60 分钟进；裸 hour 线性塞模型 | 进在日内峰值附近、最可能继续爬的窗口；裸 hour 表达不了日内驼峰；还有 stale_obs 时序问题（本分支在修） | 钟点换成 **"相对太阳过顶 / 相对预报峰"**；只在 **过了当地日峰 ∧ 最近连续 N 次观测不再创新高 ∧ 预报余下小时不再升** 之后才进 |

### 更好的策略：从"赌新高"到"确认封顶"的联合门

三轴其实是同一件事——**只在"今天升温窗口已关闭"的联合条件成立时才买**：

1. **模型层**：输出 `p_capped`（封顶概率），并把 `p_used` **锚到市场**——默认 `p_used = market_implied`，只在有**过门的 residual 模型确实打赢 market** 时才允许向上偏离；给 `model_p ≤ market + 小上限` 封顶，直接掐掉 +13pp 暴冲。（residual-calibrator 文档显示目前没有 residual 过门 → 等价于高 ask 区先不偏离市场。）
2. **时序门**：`已过当地日峰 ∧ 连续 N 次观测不创新高 ∧ forecast_remaining_max ≤ 0`，否则不进。把"见新高"硬改成"峰已过且不再升"。
3. **买点门**：要求相对**真实校准后概率**（不是 model_p）有正 edge；高 ask（≥0.7）无过门 edge 默认 shadow；认真回测**临近收盘买 NO** 的对侧 EV。
4. **组合层**：跨 profile 的 **same city/date/bracket proposition cap**——同一命题只允许一个主仓，避免 split 重复下注 + 同日全池同向的相关性爆仓（加同日 high-ask YES 总 notional 上限）。
5. **promotion gate 不变**：任何上面的改动要替换 live，必须 forward window ROI + date-bootstrap CI **同时打赢 raw market 和 base v9**，否则只能 shadow。

> 与下方「推荐主线」一致，这里只是把它收敛成一条可执行的"封顶联合门"。`peak_forming_micro` 在联合门补齐前不应按"状态已确认"处理；生产上应降为 shadow / 极小 probe，或直接暂停 real orders。

## 数据快照

本文件更新前已重新同步并重建：

- `scripts/ops/sync_weather_remote.sh`：完成，最新 market snapshot 到 `2026-06-21 22:30` 北京时间附近。
- `scripts/weather_dashboard/run_stack.sh`：完成，`runtime/weather.db` 已重建。
- `fact_trades`：4400 rows，`fact_built_at_utc=2026-06-21T14:54:43.916236+00:00`。
- `fact_signal_candidates`：34677 rows，event_date 覆盖 `2026-05-05`..`2026-06-23`。
- CLOB fill coverage gate：`gate_pass=true`，DB/cache fill cost 差异为 0。

注意：current-YES split 实例的 `strategy_instance` 不在 `fact_trades` 字段里；下面实盘分实例结果来自 raw live order JSONL，并用 `runtime/weather.db:settlement_outcomes` join 结算。

## 现在真正 live 的是什么

N100 当前跑的是 split live，两条 profile 分开记账：

| instance | entry profile | live 角色 | 概率来源 | 当前状态 |
|---|---|---|---|---|
| `theta_current_yes_fade_confirmed_tiny_live_v1` | `fade_confirmed` | 原始 current-YES live sleeve：看到 running max 后真实回落再买 | 默认 base v8/v9 logistic artifact | live tiny-size |
| `theta_current_yes_peak_forming_micro_tiny_live_v1` | `peak_forming_micro` | 仍在 running max 附近时尝试提前买 | 同一 base v8/v9 概率层 + peak-forming guard | live micro-probe / fragile |

N100 当前 caps（2026-06-21 22:56 北京时间附近远端读取）：

| 项 | fade-confirmed | peak-forming |
|---|---:|---:|
| max order notional | 1.5 | 1.5 |
| max city-day notional | 1.5 | 1.5 |
| max taker cushion | 0.02 | 0.02 |
| max obs age | 20 min | 20 min |
| pre-METAR update blackout | 6 min | 6 min |
| min forecast peak hour local | 12 | 12 |
| LLM preflight | disabled | disabled |
| LLM mode | advisory | advisory |
| prompt version if enabled | `current_yes_codex_v2_veto_loss_detector` | same |

当前实盘 LLM 没有启用。N100 也没有 Codex CLI，所以不能在 N100 上直接跑 Codex 审单。

## 最近实盘结果

窗口：split current-YES live 从 `2026-06-18` 到 `2026-06-21` 的 raw live orders。

官方已结算口径只统计 `settlement_outcomes` 已有 settled 的 matched orders；`2026-06-21` 的 12 笔 matched 目前在本机 settlement table 仍是 missing，不计入正式 win/ROI。

| profile | raw orders | matched | settled matched | W-L | settled PnL | settled cost | ROI |
|---|---:|---:|---:|---:|---:|---:|---:|
| fade-confirmed | 8 | 6 | 3 | 3-0 | +$1.972134 | $11.00 | +17.93% |
| peak-forming micro | 31 | 30 | 21 | 15-6 | -$5.685329 | $91.00 | -6.25% |
| total | 39 | 36 | 24 | 18-6 | -$3.713195 | $102.00 | -3.64% |

按 target date：

| target_date | matched | settled | W-L | PnL | ROI | unsettled |
|---|---:|---:|---:|---:|---:|---:|
| 2026-06-18 | 1 | 1 | 1-0 | +$1.747638 | +34.95% | 0 |
| 2026-06-19 | 2 | 2 | 2-0 | +$3.324456 | +33.24% | 0 |
| 2026-06-20 | 21 | 21 | 15-6 | -$8.785290 | -10.10% | 0 |
| 2026-06-21 | 12 | 0 | NA | NA | NA | 12 |

已结算亏损全部来自 `peak_forming_micro`，集中在 `2026-06-20`：

| time UTC | city | bracket | price | notional | loss |
|---|---|---:|---:|---:|---:|
| 2026-06-20 05:36 | Busan | 27 | 0.721 | 5.0 | -5.0 |
| 2026-06-20 06:03 | KualaLumpur | 31 | 0.631 | 5.0 | -5.0 |
| 2026-06-20 07:00 | Wuhan | 31 | 0.491 | 5.0 | -5.0 |
| 2026-06-20 08:30 | Lucknow | 40 | 0.741 | 5.0 | -5.0 |
| 2026-06-20 08:48 | Karachi | 34 | 0.771 | 5.0 | -5.0 |
| 2026-06-20 17:04 | BuenosAires | 15 | 0.481 | 3.0 | -3.0 |

`2026-06-21` 未结算 matched 中，Wuhan 两笔最关键：

| time UTC | profile | city | bracket | price | notional | 当前判断 |
|---|---|---|---:|---:|---:|---|
| 2026-06-21 03:03 | peak-forming | Wuhan | 28 | 0.711 | 3.0 | 观测上已被后续 29 打穿，官方结算本机暂未入表 |
| 2026-06-21 03:10 | fade-confirmed | Wuhan | 28 | 0.711 | 3.0 | 同上 |

这说明最近实盘的坏味道不只是“同一城市重复买”，而是：两个 profile 虽然信号形态不同，但最终押的是同一个 proposition：`28C` 是否守住。

## 已发现的问题：执行问题 vs 模型问题

### 执行 / 规则 / 架构问题

| 问题 | 例子 | 性质 | 当前处理 |
|---|---|---|---|
| observation stale race | Wuhan 2026-06-21 第一笔 peak-forming，下单时 obs 已接近/超过预期下一报文窗口 | 执行时序问题 | 已加 `snapshot_rule_peak_forming_stale_after_expected_obs` |
| forecast peak 过早 | Wuhan forecast peak 在 11:00 左右，和城市日内高温常识冲突 | 规则/特征解释问题 | 已加 `snapshot_rule_forecast_peak_too_early`，默认 `min_forecast_peak_hour_local=12` |
| split profile 重复押同一 city/date/bracket | Wuhan 2026-06-21 peak + fade 连续买同一 `28` | portfolio exposure / dedupe 问题 | 还没有彻底解决；当前 size 已降到 1.5，但需要跨 profile 的 shared cap / same proposition cap |
| peak-forming size 过大 | 2026-06-20 多笔 peak-forming 仍是 5 notional 级别，亏损集中 | sizing / rollout 问题 | 已降到 1.5/order 和 1.5 city-day |
| sync 默认不含 split runtime latest_summary | 本机 `remote_pm_agent` split summary 一度旧于 N100 | 运维/镜像问题 | 本文直接从 N100 读 current caps；后续应把 split dirs 加进 sync |
| N100 无 Codex CLI | 无法在 N100 直接跑 Codex preflight | 部署架构问题 | 不建议在 N100 登录 Codex；应做 Mac-side preflight queue |

### 模型 / 信号问题

| 问题 | 说明 | 当前处理 |
|---|---|---|
| peak-forming 与 fade-confirmed 是两个状态头，不能混成一个胜率 | peak-forming 更早、更便宜，但 reheat 风险大；fade-confirmed 更稳但也会遇到 false fade | 已 split live，但模型层还需要更明确的状态建模 |
| 早盘/中午回落不是“高温已结束” | Wuhan 11:00 回落、Singapore 早段 humid/cloudy dip 都说明 decline 本身不够 | `decline/fade modes v1` 已指出 mature fade vs false fade；还未进 live model |
| market 已经吃掉大部分明显信息 | v11 registry 显示 raw market ask 在 live-like slice 很强 | 后续模型必须打赢 market 和 market_iso，而不是只打赢旧 v9 |
| hazard v2 有方向但未过交易门 | peak-forming hazard v2 比 market-only 方向更好，但 ROI CI 仍跨 0 | 保留 research/shadow，不替换 live |
| LLM v3 能抓一部分坏单，但不是稳定模型 | v3 抓到 4/6 个已结算输单，但 Wuhan 第二笔 fade 仍放过 | 只能 advisory / reviewer，不能 live block |

## 版本家族

### A. Base current-YES replay / live gate

| version | 目的 | 数据 / holdout | 关键结果 | 决策 |
|---|---|---|---|---|
| `v8 full replay` | 建干净的 current-YES 全量机会分母 | 3239 rows，1527 train / 1712 holdout，2026-05-19..2026-06-14 | `weather_plus_price` 有判别力；best fixed rule 强，但 prefix walk-forward 弱 | 作为后续模型的 feature source |
| `v9 live gate` | 冻结一个可 tiny-live 的 fade-confirmed 规则 | 原始 live-like holdout 约 31 orders / 11 days | YES ROI 约 +16% 到 +18%，通过当时 tiny-live gate | 升 tiny-live，不放量 |
| `v11 model registry` | 停止临时命名，统一比较 market、market_iso、live v9、residual、HGB | 1712 holdout rows / 14 dates；live-like 278 rows / 13 dates | live-like 里 raw market ask 很强，v9 合理但没明确打赢 market | 作为模型治理层，不替换 live |

人话：**v9 是当前 live base，但 v11 规定以后不能只和旧 v9 比，必须打赢 market。**

### B. Forecast clock / peak timing

| version | 目的 | 关键结果 | 决策 |
|---|---|---|---|
| `v12 forecast clock model` | 把 forecast peak timing 作为模型特征 | 点估有变化，但 live-like 里没有稳定打赢 market ask / v9 | research only |
| `v13 observation/execution guard` | 测 obs age、METAR blackout、minutes since max | 半小时 replay 证明不了收益，但这些仍是事故防护 | 风控/telemetry guard，不是 alpha proof |
| `v14 forecast peak scorecard` | 比较 v9 fade、after-peak filter、early peak-forming | forecast clock 能解释风险形状，但过滤会缩样本；peak-forming 不稳 | forward telemetry required |
| `v15 live readiness` | 判断 forecast peak clock 能否推动 live upgrade | historical v9 过，forecast upgrade/forward telemetry 不过 | 保持 v9 tiny-live / telemetry |

人话：**forecast clock 是上下文和风险特征，还不是 promoted model。**

### C. Fade-confirmed branch

| version | 目的 | 关键结果 | 决策 |
|---|---|---|---|
| `fade_confirmed specialist v1` | 只在已回落样本上训练 specialist | base model holdout live-like ROI 约 +18.3%，specialist 约 +13.7% | specialist shadow only，live 默认 base |
| `decline/fade modes v1` | 区分 mature fade 和 false fade | `h15-21 + decline>=0.5C + minutes_since_running_max>=90` 更像真 fade；早段 humid/cloudy dip 是 trap | 加入研究/shadow 特征，不做 live hard rule |

人话：**fade-confirmed 仍是最干净的 live head，但不是所有 decline 都代表当天高点结束。**

### D. Peak-forming branch

Peak-forming 是“温度仍在 running max 附近”就买，比 fade-confirmed 更早，价格可能更好，但下午 reheat 风险更大。

| version | 目的 | 关键结果 | 决策 |
|---|---|---|---|
| `peak-forming v1` | 第一次拆 peak-forming vs post-decline | broad peak-forming 弱，`h13` 早段有点意思 | shadow/probe only |
| `peak-forming hazard v1` | 第一版 survival hazard | 已被 v2 supersede | research only |
| `peak-forming hazard v2` | 用 market、METAR、forecast clock、GFS/ECMWF gap、plateau 特征预测 current high 是否守住 | 判别力改善，但交易 ROI CI 仍跨 0 | shadow/research，不替换 live |

hazard v2 holdout 对比：

| 模型 / 规则 | holdout dates | orders | win | ROI | 95% date bootstrap |
|---|---:|---:|---:|---:|---:|
| market only | 17 | 559 | 78.4% | -5.2% | [-11.5%, +1.7%] |
| 当前 live base v9 peak rule | 17 | 234 | 79.5% | -1.0% | [-12.1%, +9.5%] |
| hazard v2 peak rule | 17 | 283 | 80.6% | -0.9% | [-8.9%, +7.5%] |
| hazard v2 stalled peak | 17 | 133 | 79.7% | -0.3% | [-12.1%, +10.5%] |
| train-selected h13 p>=0.75 edge>=0.08 | 17 | 121 | 77.7% | +1.3% | [-14.6%, +15.1%] |

命名澄清：

- 表里的“当前 live base v9 peak rule”不是原始 live v9 fade-confirmed 规则。
- 它是在 peak-forming holdout population 上，用当前 live base probability artifact 做 baseline。
- 原始 live v9 是 fade-confirmed 分支，分母更小、更干净。

人话：**hazard v2 比 market-only peak-forming 方向更好，但还不够替换 live。**

### E. Residual / weather feature challengers

| version | 目的 | 关键结果 | 决策 |
|---|---|---|---|
| `proper-form tail features v1` | 用正确 target form 测 tail weather features | 天气有信号，但 market/base 后 residual edge 变薄 | 不改 live |
| `residual calibrator + alti v1` | 测 `market + METAR core`、气压 `alti/d_alti_3h`、非线性模型 | 最佳 logistic 点估改善 logloss，但日期 bootstrap CI 跨 0；HGB 退化 | research only |

人话：**天气特征不是没用，难点是 market 已经价格化一部分显而易见的信息。**

## 分层架构与当前条件归属（团队共识词汇表）

后续讨论统一用这套分层。一笔单从信号到下单要穿过 5 层，**每层职责不同、该不该写死也不同**：

```text
weather / market snapshot
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│ ① 状态层  signal / state                                     │
│    问: 现在什么局? 峰过了吗? 今天还会不会再升温?            │
│    输出: 局类型(peak-forming / fade) + 封顶概率              │
│    纪律: 应"模型化/特征化"，不该写死阈值                     │
└─────────────────────────────────────────────────────────────┘
        │  (确认形态 + 给出 p_封顶)
        ▼
┌─────────────────────────────────────────────────────────────┐
│ ② 概率/EV层  model                                           │
│    问: 这个局值不值得买? 相对"真实校准概率"有没有 edge?      │
│    纪律: 概率锚市场; edge 必须打赢 market，不是打赢旧 v9     │
└─────────────────────────────────────────────────────────────┘
        │  (有 EV)
        ▼
┌─────────────────────────────────────────────────────────────┐
│ ③ 执行/风控层  guard                                         │
│    问: 能不能安全成交?(obs 新鲜 / METAR 竞态 / 盘口)         │
│    纪律: 永远写死的 hard guard，跟赚不赚钱无关               │
└─────────────────────────────────────────────────────────────┘
        │  (能安全成交)
        ▼
┌─────────────────────────────────────────────────────────────┐
│ ④ 组合层  portfolio                                          │
│    问: 整体敞口还允不允许再加这一注?                         │
│    纪律: 写死的上限——同命题去重 + 同日同向总额              │
└─────────────────────────────────────────────────────────────┘
        │  (敞口允许)
        ▼
┌─────────────────────────────────────────────────────────────┐
│ ⑤ LLM 复核  reviewer (advisory)                              │
│    问: 下单前最后人工式看一眼，有没有明显坏味道?            │
│    纪律: 只 advisory，不替代概率模型，不在 N100 跑 Codex     │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
     CLOB order
```

### 当前每条买入条件 → 应归属哪层（★ = 现在放错层了）

| 当前条件 | 现在被当成 | **应归属层** | 备注 |
|---|---|---|---|
| 本地 `13–15` 点窗口 | 执行硬规则 | **① 状态层** ★ | 钟点是在替模型猜"峰过没过"；应换成"相对太阳过顶/预报峰"由状态层判断 |
| `新高后 ≥10 分钟`（peak-forming） | 执行硬规则 | **① 状态层原始特征** ★ | 10 分钟不是封顶信号；30/60 分钟 METAR 城市至少要等完整观测周期后的新报文，确认没有更高值，才能进入 `stalled_high_candidate` |
| `decline_c ≥ 0.5`（fade） / `≤0.25`（peak） | 执行硬规则 | **① 状态层原始特征** ★ | `0.25C` 是旧代码容差，不是 METAR 状态单位；状态层应看官方读数/settlement bracket 是否仍在 running max，以及是否已有后续观测确认 |
| `预报峰 ≥12 点` veto | 执行硬规则 | **① 状态层** ★ | 应换成 `forecast_remaining_max` / `reheat_after_now` 连续特征 |
| `p_yes ≥ 0.5/0.6` | 模型阈值 | **② 概率层** | 概率本身要锚市场、重校准 |
| `edge ≥ 0.02/0.05` | 模型阈值 | **② 概率层** | ★口径：edge 要对"真实校准概率"算，不是"model_p − 价格" |
| `ask 0.5–0.97` 区间 | 模型阈值 | **② 概率层** | 高 ask（≥0.7）目前无过门 edge，应 shadow |
| `obs ≤ 20 分钟` | 执行硬规则 | **③ 执行层** ✓ | 放对了，永远写死 |
| `METAR 6 分钟黑窗` | 执行硬规则 | **③ 执行层** ✓ | 放对了 |
| `fresh ask ≤ 快照 + 0.02` | 执行硬规则 | **③ 执行层** ✓ | 放对了 |
| 同 city/date/bracket 重复下单 | （几乎没管） | **④ 组合层** ★缺 | split 两 profile 押同一命题、6/20 全池同向，必须补 |
| Codex preflight | code 已部署、live 关 | **⑤ LLM 层** ✓ | 维持 advisory |

### 读这张表的两句话

1. **现在最大的结构病：把"判断今天封顶没"（①状态层该干的事）写成了执行层的死阈值（13–15点、新高10分钟、回落0.5度）。** 死阈值表达不了"晴∧已过峰∧低露点→锁死"这种合取封顶条件，所以一遇 reheat 就穿。
2. **真正该写死的（③执行层 obs/盘口安全）已经放对了，别动；该补的（④组合层敞口上限）现在基本是空的。**

### 对 `peak_forming` 的修正定义

用户指出的问题成立：下面这个旧条件**不能**定义为状态层买入信号：

```text
latest obs maps to running-max settlement value / bracket
+ ask 0.50-0.97
+ model p >= 0.60
+ edge >= 0.02
+ minutes_since_running_max >= 10
```

它混了三件事：

- `decline <= 0.25C` 只是旧代码用连续摄氏度差表达"仍在 running max 附近"的容差；对整数/离散 METAR 读数来说没有独立物理含义，应改成"官方读数/round 后 settlement value / bracket 仍等于 running max"。
- `minutes_since_running_max` 是状态层原始特征，但 10 分钟远小于多数 METAR 城市的 30/60 分钟报文周期，不能证明"高点已形成"。
- `ask` 是执行/市场价格条件，不属于状态层。
- `model p` 和 `edge` 是概率/EV 层条件，也不属于状态层。

新的状态层应该先把形态拆开：

| state | 定义 | live 动作 |
|---|---|---|
| `fresh_high_unconfirmed` | 刚创新高，或距离最后一次 running max 尚未跨过一个完整官方观测周期 | 不买 YES；最多 telemetry |
| `same_bracket_unconfirmed` | 最新官方读数仍映射到 running max 的 settlement value/bracket，但还没有后续观测证明不再创新高 | 不买 YES；不能用 p/edge 强行放行 |
| `stalled_high_candidate` | **第一次**触到 running max 后至少又出现 1 个官方观测；新观测没有更高值，且最新读数仍映射到 running max bracket；同时预报余下小时没有明显 reheat | 才允许进入概率/EV 层 |
| `mature_fade_confirmed` | 高点后回落、时间已跨过日峰/预报峰，且后续观测/预报/云风湿条件共同支持 no-reheat | 才允许作为 fade 买点候选 |
| `false_fade_risk` | 早段回落、云层/湿度/风向/预报仍支持再升温，或还没过当地日峰 | veto 或 shadow |

因此，`peak_forming` 更准确的名字不是"刚创新高可以买"，而应是：

```text
stalled_high_candidate =
  latest obs still maps to running-max settlement value / bracket
  + post_first_high_obs_count >= 1  # 对 hourly METAR 城市尤其重要
  + higher_after_first_high_count == 0
  + same_running_max_obs_count >= 2 or elapsed_since_first_running_max >= one cadence
  + after_solar_or_forecast_peak
  + forecast_remaining_max <= small_buffer
  + no_reheat_fuel(weather regime)
```

注意：这里必须用 `first_running_max_obs_utc` / `post_first_high_obs_count` / `same_running_max_obs_count`。不能用旧 live 字段 `running_max_obs_utc` 直接推，因为旧字段记录的是**最后一次**等于 running max 的观测；如果同一最高读数连续报两次，它会刷新到第二次，把真实 plateau 误判成"刚到高点"。

云层、湿度、风向/风速、太阳高度、预报余下高点不能拆成单个硬阈值；它们应该作为**合取状态特征**进入 `p_capped` / hazard 模型。典型组合是：已过太阳峰 + 低云/增云 + 露点约束 + 风不支持暖平流 + GFS/ECMWF 余下小时不再升。反过来，早段转晴、湿热、暖风、预报仍上修，就算短暂回落也应该归为 `false_fade_risk`。

### 落地顺序（用分层说）

`先补 ④ 组合层（止血）` → `把 ① 状态层从死阈值搬成"峰已过且不再升"` → `训 ② 封顶概率模型（打赢 market 才上）` → `⑤ LLM advisory 垫底`。下面「哪些规则更适合进入模型层」是同一件事的细化清单。

## 现在有哪些规则判定

### 两个 profile 共用的执行/风控规则

- local live window：默认 `13-15`。
- max order notional：当前 N100 为 `1.5`。
- max city-day notional：当前 N100 为 `1.5`。
- max obs age：`20 min`。
- pre-METAR-update blackout：`6 min`。
- forecast peak 太早 veto：`forecast_peak_hour_local >= 12`。
- fresh CLOB：fresh ask 不能比 snapshot ask 贵超过 `0.02`。
- cross tick buffer：`0.001`。
- d1 sibling / market structure 必须可见，否则跳过。
- duplicate / city-day cap：当前是 profile 内约束；跨 profile 同 city/date/bracket proposition cap 仍需补。

### fade-confirmed 当前规则

- `decline_c >= 0.5C`。
- `yes ask >= 0.55`。
- `p_yes_win >= 0.5`。
- `edge >= 0.05`。
- model mode 默认 `base`，不是 specialist。

### peak-forming 当前规则

下面是**当前 live 现状记录**，不是推荐的新状态定义。尤其 `decline_c <= 0.25C` 只是旧代码容差，`minutes_since_running_max >= 10` 只说明没有在刚打印新高的瞬间追单；两者都不能说明今天高点已形成。

- `decline_c <= 0.25C`。
- `0.50 <= yes ask <= 0.97`。
- `p_yes_win >= 0.60`。
- `edge >= 0.02`。
- `minutes_since_running_max >= 10`。
- stale-after-expected-obs hard reject。
- forecast peak too early hard reject。

## 哪些规则更适合进入模型层

现在不少东西是“为了安全先用 hard rule 挡住”。长期看，其中一部分应该进入模型/状态层，而不是永远写死：

| 当前形态 | 更好的模型层表达 |
|---|---|
| `forecast_peak_hour_local >= 12` hard rule | `forecast_remaining_max`、`reheat_after_now`、peak hour delta、forecast model agreement 作为连续特征 |
| `decline_c >= 0.5` | mature fade vs early false fade 状态分类：local hour、minutes since max、decline depth、湿度/云/风 |
| `minutes_since_running_max >= 10` | 改为 cadence-aware：`post_high_obs_count`、`obs_cadence_min`、`first/last max touch age`、`plateau_obs_count`；10 分钟不单独放行 |
| obs age / next obs blackout | source freshness risk、cadence phase、minutes_to_next_obs、source profile reliability |
| peak-forming ask/edge threshold | hazard model 输出 `p_survive` 后做 expected value / sizing，而不是固定 one-size threshold |
| city-day cap | portfolio layer 的 same-proposition exposure：city/date/bracket across profiles |

仍然应该保留为 hard guard 的东西：

- fresh book 变差 / depth 不足；
- observation 明显 stale；
- METAR 更新前后竞态；
- 资金上限、重复下单、真实 CLOB 安全边界。

## LLM / Codex 怎么用

LLM preflight 不是和 hazard v2 竞争的概率模型。它比较的是真实 would-trade / matched order baseline，角色是“下单前 reviewer”。

它所在环节：

```text
candidate passed deterministic rules
  -> fresh CLOB quote accepted
  -> city-day cap accepted
  -> LLM preflight
  -> plan/order
```

当前 prompt 版本结果：

| prompt version | 角色 | 2026-06-18..2026-06-21 matched replay 结果 | 状态 |
|---|---|---|---|
| `current_yes_codex_v1_reheat_guard` | 保守 reheat reviewer | 保留 9-1 settled，ROI +16.2%，但 veto precision 只有 35.7%，误杀 9 个赢家 | 太激进 |
| `current_yes_codex_v2_veto_loss_detector` | 只 veto 明确输单 | 保留 12-4 settled，ROI -6.9%，只抓到 2/6 个输单 | 太弱 |
| `current_yes_codex_v3_price_aware_veto` | price/edge-aware reviewer | 保留 15-2 settled，ROI +15.3%，抓到 4/6 个输单，误杀 3 个赢家 | 最好但仍未 live-ready |

Wuhan 2026-06-21 诊断：

- v3 会 veto stale 的 `peak_forming_micro`。
- v3 仍会 allow 后一笔 fresh `fade_confirmed`。
- 说明 LLM 没有彻底学会“11:00 回落不等于全天高点结束”。

实盘建议：

- 不要在 N100 登录 Codex。
- 不要每条天气报文全量跑 LLM。
- 只对 would-trade candidate 跑：规则已过、fresh quote 已过、cap 已过，准备下单前。
- 做 Mac-side preflight queue：N100 写 pending candidate，Mac 本地 Codex 读、判断、写回 decision。
- 初期只 advisory/telemetry；只有当它能稳定抓住武汉这类 false-fade / stale-race，才讨论 `block_veto`。
- 缓存 key 应至少包含：`city/target_date/bracket/obs_ts/forecast_hash/price_bucket/profile`。

## 当前 promotion status

| component | status | 原因 |
|---|---|---|
| `fade_confirmed` base v9 tiny-live | live tiny-size | 原始 fixed rule 通过 tiny-live gate；最近 split live 仍小样本正收益 |
| `peak_forming_micro` | live micro-probe / fragile | 最近已结算亏损全部来自它；旧 `新高后10分钟`状态定义不成立，联合门补齐前应 shadow / 暂停 real orders / 只保留极小 telemetry probe |
| `fade specialist v1` | shadow only | holdout live-like ROI 输给 base |
| `forecast clock model v12` | research only | 没有稳定打赢 v9/market |
| `observation guard v13` | risk guard / telemetry | 是事故防护，不是 replay 已证明 alpha |
| `forecast peak scorecard v14` | scorecard / telemetry | 能解释风险，不是 promoted rule |
| `hazard v2 peak-forming` | research / shadow candidate | 模型形态更好，但 ROI CI 跨 0 |
| `residual + alti` | research only | 点估改善，CI 未过 |
| `Codex preflight v1-v3` | code deployed, live disabled | v3 有用，但样本太小且漏掉 Wuhan fade case |

## 推荐主线

1. 保留 split 记账/telemetry，但把风险讲清楚：
   - `fade_confirmed` 是当前最干净 sleeve。
   - `peak_forming_micro` 只是 micro-probe，不是可放量策略；旧状态定义修好前，real orders 应 shadow / 暂停 / 极小 telemetry probe。

2. 先补 portfolio exposure：
   - 跨 profile 的 same city/date/bracket cap。
   - 同一 proposition 只能有一个主仓位，不要因为 profile 分裂而重复下注。

3. 把 peak-forming 当作主研究前线，但先改状态定义：
   - `fresh_high_unconfirmed` 不下单。
   - `stalled_high_candidate` 必须 cadence-aware，用 first-touch + plateau-count 判断；至少等第一次触高后一个后续官方观测未创新高。
   - hazard v2 继续作为候选概率层。
   - promotion 必须等 forward window ROI 和 date-bootstrap CI 过门。

4. 把 mature fade / false fade 正式做成状态特征：
   - `local_hour`
   - `minutes_since_running_max`
   - `forecast_remaining_max`
   - `reheat_after_now`
   - humidity/cloud/wind regime
   - source freshness / cadence phase

5. LLM 只做 reviewer：
   - 下单前审单；
   - 不替代概率模型；
   - 不在 N100 上跑 Codex；
   - 不启 live block，先 advisory/telemetry。

6. 每个未来模型必须写清楚它改的是哪一层：
   - probability model；
   - state/rule head；
   - execution guard；
   - portfolio exposure；
   - LLM reviewer。

## Source reports

- `2026-06-16-theta-yes-current-full-replay-v8.md`
- `2026-06-16-theta-yes-current-live-gate-v9.md`
- `2026-06-17-theta-current-yes-model-registry-v11.md`
- `2026-06-17-theta-current-yes-forecast-clock-model-v12.md`
- `2026-06-18-theta-current-yes-observation-execution-guard-v13.md`
- `2026-06-18-theta-current-yes-forecast-peak-scorecard-v14.md`
- `2026-06-18-theta-current-yes-live-readiness-v15.md`
- `2026-06-18-current-yes-fade-confirmed-specialist-model-v1.md`
- `2026-06-20-current-yes-residual-calibrator-alti-v1.md`
- `2026-06-21-current-yes-peak-forming-hazard-v2.md`
- `2026-06-21-current-yes-decline-fade-modes-v1.md`
- `2026-06-21-current-yes-codex-prompt-version-comparison-v1.md`
