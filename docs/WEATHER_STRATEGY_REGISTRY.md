# Weather 策略总账（我们到底试过哪些 · 灵感/规则 · 是否可行 · 血缘归属）

Status: `current-reference`
Updated: 2026-06-19 首版
Source of truth: 状态/结论以各 living doc 为准，本表只做汇总入口

这份是"我们一共研究过哪些策略"的单页总账。每条策略：**灵感/盈利规则 → 当前状态 → 是否可行 →
属于量化血缘哪一层**。状态/结论的权威来源是评估层 living docs（`docs/analysis/*.md`），本表汇总它们，
有冲突以 living doc 为准。

## 状态图例

| 状态 | 含义 |
|---|---|
| `live` | 当前真实下单（实盘城市池/方向见 `WEATHER_CITY_POOL_DECISIONS.md` / `WEATHER_STRATEGY_ENTRYPOINT.md`） |
| `shadow` | 跑零 notional 影子遥测，不下单 |
| `paper` | 纸面/回放记账 |
| `research` | 仅离线研究，未达 shadow 标准 |
| `shelved` | 已证伪或停用，仅留历史 |

## 血缘分支（白皮书口径）

```text
pre_predict   赛前/早盘：没看到日内路径时，预测最终最高温分布（给 prior）
reheat_risk   日内路径：已看到 running max 后，判断会不会再升温（给 conditional update）
两支共享一个事实层 reheat_feature_factory_v1
```

---

## 分支一：pre_predict（[1] 概率 / [2] 结构 / [3] 选择）

| 策略 / 家族 | 灵感 / 盈利规则 | 状态 | 是否可行（当前结论） | 血缘层 · 入口 doc |
|---|---|---|---|---|
| 普通单腿 YES/NO | forecast max + 历史误差 + 市场隐含，挑 mispriced bracket | `research` | baseline；裸 `model_p_yes - price` 不是确认 alpha | [1] model_vs_market |
| model×market 融合 overlay | `0.3*model + 0.7*market`，承认市场吃掉大部分公开天气信息 | `research` | 提升太小，未确认 alpha；global model alpha 为负 | [1] model_vs_market |
| forecast quality / reliability base | entropy/adjacent mass/city-model history 转可复用可靠性标签 | `shadow` | **只作共享可靠性层 / soft 标签**，非独立 live 策略 | [1] model_vs_market |
| forecast-bounded Range RV | forecast 锁定档位区间内做相对价值 | `shadow` | 三统计门过、但 live-standard/forward 不过；零 notional shadow | [2] market_structure_edge |
| adjacent / range basket | 相邻档/区间篮子的相对定价 | `research` | inconclusive，holdout/top5 不稳 | [2] market_structure_edge |
| all-YES underround（no-arb 篮子） | 互斥档 YES ask 之和 <1 的无套利结构 | `research` | **离线确认（+3.16% settled unit ROI）但散户 live 被否**（per-leg buffer~0.3¢、全腿成交/部分成交风险） | [2] market_structure_edge |
| side-band / BUY_NO side alpha | BUY_NO 历史胜率高、特定价带方向偏好 | `research` | **胜率 ≠ alpha**；clean 测试三门不过，仅作特征/标签 | [2] side_alpha |
| 低价 YES prior sleeve（lottery） | 低价高凸 longshot 档的 prior | `research` | 收益由少数日期/城市命中驱动，excess CI 跨 0，不 live | [1]-[2] pre_predict |
| station-basis（结算源 basis） | 官方结算站点 vs 市场所用站点的温差 basis | `shadow` | 当前主操作 shadow 线，`NOT_READY_ACCUMULATE_SHADOW`，有前向阻塞 | [0]-[2] 见 ENTRYPOINT |

## 分支二：reheat_risk（[0] 事实 / [1]-[2] 模型与表达）

| 策略 / 家族 | 灵感 / 盈利规则 | 状态 | 是否可行（当前结论） | 血缘层 · 入口 doc |
|---|---|---|---|---|
| current_yes_fade_confirmed | 日内已回落后更稳健地买 current YES | `shadow`（最接近 tiny-live） | 默认 current-YES timing head；卡在 execution freshness / fresh-ask 滑点 | [1]-[2] reheat_risk |
| current_yes_peak_forming | 当前仍在高位时买 current YES | `shadow`（narrow early sleeve） | 早买省价 vs 二次升温风险未净赚；只保留窄 shadow | [1]-[2] reheat_risk |
| higher_no_carry | 买更高温档 NO（ladder carry） | `shadow`（telemetry only） | 没证明能稳定打赢同窗 current YES，仅 shadow 表达遥测 | [2] reheat_risk |
| low_price_yes_reheat_reversal | 需二次升温才命中的低价 YES，升级成 `forecast prior × reheat condition` | `research` | 凸性研究，小仓 shadow 候选，不直接 live；单独记 PnL | [1]-[2] reheat_risk |

## 共享 / 基础设施层（[0] 事实层）

| 组件 | 作用 | 状态 | 备注 |
|---|---|---|---|
| `reheat_feature_factory_v1` | 两分支共享事实物化：observed path + current YES/d1-d2 NO/target YES quotes + source-grain settlement | 在用 | **取代旧 observed_max 各自 materialize**；仍缺 forecast peak context（`forecast_peak_hour_local` 等 0% 覆盖，待 backfill） |
| observed_max 旧底表 | 早期日内最高温底表 | `shelved` | 已被 factory 取代，旧代号仅留历史路径；数据整理片确认归档 |
| forecast quality base | 共享可靠性标签层 | `shadow` | 见上 pre_predict 行 |

## 执行 / 组合 / 城市层（[3] 选择 / [4] 执行）

| 家族 | 灵感 / 规则 | 状态 | 是否可行 | 血缘层 |
|---|---|---|---|---|
| entry_timing | target-date lead time / forecast checkpoint / decision window 限制 | `shadow` | 部分 timing 限制 shadow，未确认广义 live 自动化 | [3] entry_timing |
| sizing / entry band | 替代统一 0.25–0.75 的入场区间与仓位 | `design-draft` | 当前 live sizing/band 仍由 entrypoint/config 定义 | [3] sizing_entry_band |
| execution_quality | maker 扣 spread/queue/逆选后是否仍有可成交 edge | `research` | inconclusive | [4] execution_quality |
| city_selection / city-day basket | city×side×instance 选择、篮子组合 | `shadow` | 篮子仅 shadow，live 城市池由 CITY_POOL_DECISIONS 治理 | [3] city_selection |
| blender / edge-engine | blender 字段作 shadow/paper/size signal | `shadow` | 不作 live hard gate | [1] blender_shadow |
| mid_price_core v1 / v2 | 早期中价核心策略 | `shelved` | V2 于 2026-06-06 停 live | 历史 |

---

## 当前优先级（白皮书口径，2026-06-16）

1. 共享 reheat feature factory（A）已 v1：策略头默认消费它，不再各自 materialize。
2. current YES timing（B）已 v1：默认 fade-confirmed，peak-forming 仅 narrow early shadow，不改 live。
3. **下一步 E**：current YES 最接近 tiny-live，卡 execution freshness / fresh-ask 滑点。
4. higher NO carry（C）已 v1：未稳定打赢 current YES，仅 shadow telemetry。
5. low-price YES reheat reversal（D）单独做凸性研究，不与 no-reheat 策略混 PnL。

> 早期"已知盈利模式"（5 月 BUY_NO/Warsaw/ECMWF/LA）是 near-binary 修复前口径，**已作废**，
> 见 `WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md §1.1`。当前没有任何"已确认稳定盈利"的 live alpha；
> 最接近的是 current YES（卡执行）和离线确认但散户被否的 all-YES underround。
