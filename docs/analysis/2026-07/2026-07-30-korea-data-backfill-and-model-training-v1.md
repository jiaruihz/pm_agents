# Korea 数据补全与模型训练 v1

## 结论

数据补全和两层模型训练已完成，但当前不应照此上线：

- 长历史物理模型通过冻结集的 physical-prior baseline，说明温度路径、湿度、风和云
  对“当天还会升几档”确实有预测信息。
- 同刻 AMOS + 可成交盘口的 residual 模型在冻结集显著输给 raw market，尚未证明
  market alpha。
- 交易 replay 的正 ROI 切片置信区间均跨 0；完整 21 日分布为负。因此保持
  `research / zero-notional`，不改 live。

## 数据补全

### 长历史物理层

- source：IEM ASOS archive，Seoul/RKSI、Busan/RKPK。
- 原始窗口：`2026-05-01..2026-07-30`，每城 `91` 天。
- 标准化观测：`6,819` 行；Seoul `4,322`，Busan `2,497`。
- 温度、露点、RH、风覆盖均为 `100%`；sky 有效覆盖 Seoul `50.44%`、
  Busan `70.64%`。
- lineage：historical observation-time proxy，first-seen unknown；不冒充
  AMOS collector-exact PIT。
- 数据和 raw 均落在
  `/Volumes/jrs/pm_agents/research/korea_iem_history/v1/`。

canonical `settlement_outcomes` 已覆盖训练标签，production manifest 的 DB route 为
`healthy`，所以没有对 canonical weather.db 做不必要的 rebuild。

### 短期盘口层

- 窗口：`2026-07-08..2026-07-28`。
- AMOS first-seen state：`35,469` 行。
- Korea target-day book state：`885` 行。
- 严格 as-of joined/executable opportunity：`483` 行，`21` 个 target dates，
  `34` 个 city-days。
- 盘口证据：full-ladder atlas `297`、archived L2 `158`、direct paper snapshot
  `28` 行。
- 缺失：Seoul `07-08..07-14` 和 `07-28` 共 `8` 个 city-days。抽查 paper
  snapshot 是 `orderbook_budget_exhausted` 且 bid/ask 为空，属于 coverage gap，
  不能伪装为策略过滤或可成交样本。

## 模型 1：remaining-heat 物理分布

Target 是每个城市当地 09–17 点，最终 winning bracket 相对当时 routine running
max 的 offset：`negative / zero / +1 / +2 / +3以上`。

- usable window：`2026-05-12..2026-07-28`，`78` target dates、
  `156` city-days、`1,404` hourly states。
- train：`64` dates、`128` city-days、`1,152` states。
- frozen holdout：`14` dates、`28` city-days、`252` states。
- 特征：city、local hour、current/running max、距高点时间、1h/3h 温变、RH、
  露点差、风速风向、云底/sky、能见度、季节项。

| frozen holdout | logloss | multiclass Brier | accuracy |
|---|---:|---:|---:|
| train class prior | 1.234490 | 0.631178 | 54.76% |
| weather model | 0.930365 | 0.480672 | 60.32% |

Model − prior logloss 为 `-0.304125`，target-date block 95% CI
`[-0.375167, -0.211007]`。这是可靠的 physical-prior 改善，不是对 market
baseline 的胜利。

## 模型 2：AMOS + market residual

Target 是 `P(PIT favorite exact bracket 最终获胜)`；market logit 作锚，再加入
AMOS 温度路径、湿度、风、云和降水状态。

- train：`07-08..07-20`，`309` rows、`13` dates、`19` city-days。
- frozen holdout：`07-21..07-28`，`174` rows、`8` dates、`15` city-days。

| frozen holdout | Brier | logloss |
|---|---:|---:|
| raw market | 0.158985 | 0.448042 |
| market-only calibrator | 0.170081 | 0.524248 |
| market + weather | 0.201869 | 0.581507 |

Candidate − raw market：Brier `+0.042884`
（95% CI `[+0.022042, +0.074965]`），logloss `+0.133465`
（95% CI `[+0.074670, +0.229759]`）。正值表示更差，因此 market baseline gate
明确失败。

### 迭代账（已合并）

早期 runner 曾按数据覆盖扩展分别生成 `baseline-v1`、`expanded-v2/v3/v4`
四份日期报告；它们不是四个策略，也不应作为四个当前入口继续保留：

| run | 窗口 / opportunity | holdout model vs market | 结论 |
|---|---|---|---|
| baseline-v1 | 07-15..27 / 297 | Brier `+0.017779`；logloss `+0.072484` | market gate FAIL |
| expanded-v2 | 07-08..28 / 483 | Brier `+0.042884`；logloss `+0.133465` | market gate FAIL |
| expanded-v3 | 同窗口 / 803（混入更宽 book states） | Brier `+0.051831`；logloss `+0.157991` | 更差，未晋升 |
| expanded-v4 | 回到可审计 483-row evidence contract | 与 expanded-v2 相同 | 作为最终 historical run identity |

这些 run 的耐久结论、分母与 coverage gap 以本报告为准；详细机器产物仍由
`/Volumes/jrs/pm_agents/research/korea_intraday_residual/v4/` 保存和复跑，不再为同一
runner 的窗口扩展保留平行 Markdown。

## 信号、订单与 ROI

完整 `21` 日、`34` city-day 分布：

- 所有 `483` opportunity 都机械按 5 shares：win rate `68.53%`，
  fee-adjusted PnL `$-24.0550`，ROI `-1.46%`，
  date-block 95% CI `[-14.62%, +11.25%]`。
- 每 city-day 仅首单：`34` orders，`15` wins，win rate `44.12%`，
  PnL `$-4.4047`，ROI `-5.55%`，CI `[-43.23%, +32.79%]`。
- frozen holdout 全 opportunity 虽为 ROI `+16.27%`，CI
  `[-1.29%, +33.39%]`；每 city-day 首单 ROI `+24.74%`，CI
  `[-43.34%, +87.59%]`，都未通过显著性。
- edge `>=2c` selector 只有 `8` 单、`5` 胜、ROI `+26.25%`，CI
  `[-52.81%, +107.81%]`，只作附录诊断，不能替代完整分布。

## 决策

物理问题已经抽象清楚：先预测 remaining-heat bracket distribution，再与同刻盘口
做 residual 和 fee-adjusted expression。该缺口已由 stacked v2 完成：长历史
remaining-heat prior 已真实接入 AMOS residual，并同时测试 settlement-facing
routine-transfer prior。historical holdout 上 primary 相对 raw market 的 Brier
delta `+0.087861`（95% CI `[+0.032741,+0.139633]`）、logloss delta
`+0.261617`（95% CI `[+0.094451,+0.406495]`），明确更差；不加 live gate、
不部署、不真实下单。详见
`docs/analysis/2026-07/2026-07-30-korea-stacked-remaining-heat-residual-v2.md`。

## 产物

- 长历史 manifest：
  `/Volumes/jrs/pm_agents/research/korea_iem_history/v1/manifest.json`
- 物理模型与预测：
  `/Volumes/jrs/pm_agents/research/korea_iem_remaining_heat/v1/`
- 盘口 residual 全量 replay：
  `/Volumes/jrs/pm_agents/research/korea_intraday_residual/v4/`
- 物理模型细节：
  `docs/analysis/2026-07/2026-07-30-korea-iem-remaining-heat-model-v1.md`
