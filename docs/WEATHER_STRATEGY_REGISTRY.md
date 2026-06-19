# Weather 策略总账（我们到底试过哪些 · 灵感/规则 · 是否可行 · 血缘归属）

Status: `current-reference`
Updated: 2026-06-19 首版
Source of truth: 状态/结论以各 living doc 为准，本表只做汇总入口

这份是"我们一共研究过哪些策略"的单页总账。每条策略：**灵感/盈利规则 → 当前状态 → 是否可行 →
属于量化血缘哪一层**。状态/结论的权威来源是评估层 living docs（`docs/analysis/*.md`），本表汇总它们，
有冲突以 living doc 为准。

> 关联主轴：[0]–[6] 分层定义见 [WEATHER_ARCHITECTURE_SPINE.md](WEATHER_ARCHITECTURE_SPINE.md)；
> 一条策略跑完怎么复盘见 [WEATHER_STRATEGY_REVIEW_PIPELINE.md](WEATHER_STRATEGY_REVIEW_PIPELINE.md)。

## 状态图例

| 状态 | 含义 |
|---|---|
| `live` | 当前真实下单（实盘城市池/方向见 `WEATHER_CITY_POOL_DECISIONS.md` / `WEATHER_STRATEGY_ENTRYPOINT.md`） |
| `shadow` | 跑零 notional 影子遥测，不下单 |
| `paper` | 纸面/回放记账 |
| `research` | 仅离线研究，未达 shadow 标准 |
| `dormant` | 因方向切换暂时不用，**但未被永久证伪，保留备用**（如某分支日后跑通可复用），不归档不删 |
| `shelved` | 停用/被新实现取代，仅留历史；本项目默认仍保留文件，不主动删除 |

> 重要：本项目研究结论大多是 `inconclusive`（暂未确认），不是 `disproven`（已否定）。
> 一条策略"当前不在主线"≠"可以清掉"。Range RV / pre_predict 等方向可能回归，相关底表/脚本一律**保留备用**。

## 注意：策略会换代，执行/评估血缘不换

区分两层，别混：

- **策略 / 特征层 [0]–[1]（会流动）**：observed_max → reheat factory、哪些信号 live/shadow/dormant。
  本总账跟踪的就是这层；它换方向是正常的。
- **执行 / 评估血缘 [3]–[6]（永久基础设施，不随策略换代而重做）**：
  `order → fill → live/shadow 对比 → PnL → 关联 strategy_config 参数 → 看板`。
  这条链与具体策略无关，由 canonical 表（`orders` / `fills` / `fact_trades.strategy_instance` /
  `settlements` / `strategy_config`）和看板（`run_stack.sh` 的 `/weather/live`、`/weather/runs`）支撑，
  living docs 是 [live_performance.md](analysis/live_performance.md)([5][6]) 与
  [account_reconcile.md](analysis/account_reconcile.md)([5])。
  **换策略方向不动这条链；清理/重构也不碰它。**

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
| current_yes_fade_confirmed | 日内已回落后更稳健地买 current YES | **`live`（tiny-live $5/单·$5/城日）** | **当前 live 之一**（N100 `weather_theta_current_yes_tiny_live.py --entry-profile-mode fade_confirmed --live`，2026-06-19 起）；默认 timing head，但整支仍卡 execution freshness / fresh-ask 滑点 → 当作前向取证探针，按执行质量评估不按 PnL | [1]-[2] reheat_risk |
| current_yes_peak_forming_micro | 当前仍在高位时买 current YES（微仓） | **`live`（tiny-live $5）** | **当前 live 之一**（同脚本 `--entry-profile-mode peak_forming_micro --enable-peak-forming-live`）；注意：已从白皮书旧口径"shadow only"**升级为 micro live**（用户 2026-06-19 确认有意为之） | [1]-[2] reheat_risk |
| metar_cross_prev_no | 用实时 METAR 交叉前日 NO（latency/source basis） | **`live`（$10/单·$50/天）** | **当前也在 live**（N100 `weather_metar_cross_prev_no_shadow.py --live`）；6-17/18 新线，仓位上限比 current-YES 大，研究背书与回填证据待复盘补 | [0]-[2] reheat_risk |
| higher_no_carry | 买更高温档 NO（ladder carry） | `shadow`（telemetry only） | 没证明能稳定打赢同窗 current YES，仅 shadow 表达遥测 | [2] reheat_risk |
| low_price_yes_reheat_reversal | 需二次升温才命中的低价 YES，升级成 `forecast prior × reheat condition` | `research` | 凸性研究，小仓 shadow 候选，不直接 live；单独记 PnL | [1]-[2] reheat_risk |

## 共享 / 基础设施层（[0] 事实层）

| 组件 | 作用 | 状态 | 备注 |
|---|---|---|---|
| `reheat_feature_factory_v1` | 两分支共享事实物化：observed path + current YES/d1-d2 NO/target YES quotes + source-grain settlement | 在用 | **取代旧 observed_max 各自 materialize**；仍缺 forecast peak context（`forecast_peak_hour_local` 等 0% 覆盖，待 backfill） |
| observed_max 旧底表 | 早期日内最高温底表 | `dormant` | 当前主线改走 reheat factory 故暂不用，**但未证伪，保留备用**——若 Range RV / pre_predict 跑通可能复用；不归档不删 |
| forecast quality base | 共享可靠性标签层 | `shadow` | 见上 pre_predict 行 |

## 执行 / 组合 / 城市层（[3] 选择 / [4] 执行）

| 家族 | 灵感 / 规则 | 状态 | 是否可行 | 血缘层 |
|---|---|---|---|---|
| entry_timing | target-date lead time / forecast checkpoint / decision window 限制 | `shadow` | 部分 timing 限制 shadow，未确认广义 live 自动化 | [3] entry_timing |
| sizing / entry band | 替代统一 0.25–0.75 的入场区间与仓位 | `design-draft` | 当前 live sizing/band 仍由 entrypoint/config 定义 | [3] sizing_entry_band |
| execution_quality | maker 扣 spread/queue/逆选后是否仍有可成交 edge | `research` | inconclusive | [4] execution_quality |
| city_selection / city-day basket | city×side×instance 选择、篮子组合 | `shadow` | 篮子仅 shadow，live 城市池由 CITY_POOL_DECISIONS 治理 | [3] city_selection |
| blender / edge-engine | blender 字段作 shadow/paper/size signal | `shadow` | 不作 live hard gate | [1] blender_shadow |
| mid_price_core v1 / v2 / maker_queue | 早期中价核心策略 | `shelved` | **2026-06 因实盘亏损被用户停掉**（v2 6-06，其余 live_real 成交停在 6-11）；是停用决策，非证伪 | 历史 |

---

## 当前优先级（白皮书口径，2026-06-16）

1. 共享 reheat feature factory（A）已 v1：策略头默认消费它，不再各自 materialize。
2. current YES timing（B）已 v1。**更新（2026-06-19）：fade_confirmed 与 peak_forming_micro 现已双双 tiny-live（$5）**，
   白皮书旧口径"peak-forming 仅 shadow / 不改 live"已被取代。
3. **下一步 E**：current YES 已进 tiny-live，但核心未解的仍是 execution freshness / fresh-ask 滑点 → 复盘看执行存活，不看早期 PnL。
4. higher NO carry（C）已 v1：未稳定打赢 current YES，仅 shadow telemetry。
5. low-price YES reheat reversal（D）单独做凸性研究，不与 no-reheat 策略混 PnL。

> 早期"已知盈利模式"（5 月 BUY_NO/Warsaw/ECMWF/LA）是 near-binary 修复前口径，**已作废**，
> 见 `WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md §1.1`。**当前没有任何"已确认稳定盈利"的 live alpha**：
> 在跑的 current YES（fade_confirmed + peak_forming_micro）与 metar-cross 是 **tiny-live 前向取证**（$5–$10 微仓），
> 不是已证实策略；评估按执行质量/滑点，别按早期 PnL。旧 mid_price_core 已因亏损停用。
