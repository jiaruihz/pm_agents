# Helsinki 模型可靠性全量审计 v1

## 人话结论

这个模型**有用，但“盘口表达已经可靠”还不能成立**。

- 天气/路径信息确实能修正一部分市场错误；全 checkpoint 上 Brier 从市场的
  `0.07641` 降到 `0.06125`，logloss 从 `0.24874` 降到 `0.19870`。
- 但 362 行不是 362 个独立案例，只来自 9 个 target dates；收敛到每天每个温度档
  第一次判断后只有 `31 rows / 9 dates`，相对市场的 Brier/logloss 改善 CI 全部跨 0。
- 更重要的是，改善集中在 **source 到达前的旧 full-ladder 盘口**。真正 post-source
  可执行的 active book 只有 `12 date-X rows / 4 dates`，模型 Brier/logloss
  `0.13196/0.47753`，反而差于市场 `0.09167/0.29274`。
- 因此当前合理状态是：**冻结 probability artifact 继续 clean forward，expression
  不冻结、不接 live**。下一版核心不是再加阈值，而是把训练 grain、盘口时钟和逐档
  独立二分类结构一起修掉。

## 固定分母

本审计没有挑案例，完整覆盖 artifact freeze 前全部 OOF：

| grain | rows | target dates | 日期 |
|---|---:|---:|---|
| 每 10 分钟 checkpoint | 362 | 9 | 2026-07-20..29 |
| path-state 首次 entry | 142 | 9 | 2026-07-20..29 |
| 每日每 X 首次 entry | 31 | 9 | 2026-07-20..29 |
| positive-edge 5-share intent | 28 | 8 | 2026-07-20..29 |

`2026-07-31+` clean forward 标签没有读取、没有调参。模型 artifact SHA256：
`398b92b295f43e04c7b4deab26ccdd2b1f40354474e2d187f453092dfa3a3ed9`。

### Signal funnel

`2,115 weather checkpoints / 14 settled dates → 598 unique market rows → 249 final-train
state entries → 362 OOF checkpoints / 9 dates → 31 date-X first entries → 28 intents`。

每 10 分钟都重估，但同一 date-X 有持仓后由 state machine 去重；所以 362 是概率评分
分母，不是 362 次下单。

### Evidence funnel

- OOF checkpoint：`231 active post-source rows / 4 dates`；`131 pre-source full-ladder
  rows / 9 dates`。
- date-X entry：`12 active rows / 4 dates`；`19 pre-source rows / 6 dates`。
- 28 个 intent：`11 active / 4 dates`；`17 pre-source / 5 dates`。
- upper-strip 同刻 executable coverage：`0/28`；actual fill：`0`。

旧盘口缺口只算 evidence gap，不算模型筛选。

## 概率可靠性

所有 proper scores 都先按日内取均值，再按 target_date 等权：

| grain | model | Brier | logloss | 0.5 accuracy |
|---|---|---:|---:|---:|
| checkpoint | market | 0.07641 | 0.24874 | 91.99% |
| checkpoint | weather v7 | 0.09757 | 0.27789 | 80.94% |
| checkpoint | offset-fade | **0.06125** | **0.19870** | 91.99% |
| date-X entry | market | 0.08180 | 0.26162 | 83.87% |
| date-X entry | weather v7 | 0.11124 | 0.31666 | 87.10% |
| date-X entry | offset-fade | **0.07185** | **0.25795** | **90.32%** |

点估改善，但 target-date block bootstrap 没确认胜过市场：

- checkpoint：Brier delta `-0.01516`，95% CI `[-0.05346,+0.01966]`；logloss
  delta `-0.05005`，CI `[-0.15524,+0.04330]`。
- date-X entry：Brier delta `-0.00995`，CI `[-0.06072,+0.05824]`；logloss
  delta `-0.00367`，CI `[-0.17642,+0.25672]`。

模型在 checkpoint 上修对了 16 个市场 0.5 分类错误，同时也把市场原本正确的 16 行
改错，总 accuracy 完全没变。29 个错误 checkpoint 实际只聚成 7 个 date-X episode，
再次说明不能把 10 分钟重复行当独立案例。

## 盘口时钟是当前最大结构问题

| date-X evidence | rows/dates | market Brier/logloss | model Brier/logloss | model-market Brier CI |
|---|---:|---:|---:|---:|
| source 前 full ladder | 19 / 6 | 0.06758 / 0.21437 | **0.02182 / 0.07747** | `[-0.07301,-0.02055]` |
| source 后 active book | 12 / 4 | **0.09167 / 0.29274** | 0.13196 / 0.47753 | `[-0.06270,+0.17093]` |

旧盘口上模型明显好，恰好说明它能发现“市场还没来得及更新”的差异；但这部分价格不是
信号触发后的可执行报价。active book 上点估反而退化，而且只有 4 天，不能宣布模型
已学会可执行 residual。

## 全案例发现的可靠与不可靠部分

### 合理的部分

- 31 个 date-X entry 中，没有出现“forecast ceiling 至少低 1°C，却把 break 概率推到
  50% 以上”或相反方向的硬语义冲突。
- 21 个 market/weather 都认为会 break 的 entry 最终 `21/21` break；模型平均概率
  `0.9733`，比市场 `0.9345` 更接近结果。
- forecast margin `(-1,0)°C` 的 4 个 entry 全部 break，模型 Brier `0.06486`，明显好于
  market `0.23137`；说明 forecast 天花板不能单独当硬边界，路径修正有真实价值。

### 不可靠的部分

- `10/31` entry 被模型相对市场修正超过 2 logits；这组模型 Brier `0.07842`，反而
  差于市场 `0.03639`。这不是要加“2 logits gate”，而是说明 correction 的收缩不足。
- active date-X 上模型 calibration bias 为 `+0.06278`，存在偏乐观；pre-source 则为
  `-0.05796`。把两个盘口时钟混在一起，会把相反偏差抵消成看似校准。
- 唯一高置信错误是 2026-07-26 X=19：模型 `97.00%`、市场 `44.0%`、forecast margin
  `+0.6°C`，最终没有 break。它是典型的 fresh-runway 过度修正。
- 2026-07-25 X=20 与 2026-07-29 X=24 的 forecast margin 分别 `-1.4/-1.5°C`，weather
  概率 `25.17%/12.73%`，模型仍抬到 `21.28%/36.30%` 并高于可执行成本，最终都输。
  后者尤其说明 residual 可以反向压过 weather 与 market 的共同弱信号。
- 2026-07-27 X=20 只有 `0.87pp` edge，且 leave-one-training-date-out 后 signal 判定稳定率
  只有 `50%`；这笔实际赢了，但模型把它选出来的稳定性不够。

完整 31 个 entry 与 362 个 checkpoint 均已落盘，不只保留上述异常例。

## 参数与样本敏感性

- 9 个 expanding folds 的相邻标准化系数 cosine 为平均 `0.969`、最小 `0.940`，整体
  方向没有乱跳。
- 19 个非截距 feature 中 `14` 个全 fold 同号；`weather_market_logit_gap`、
  `FMI-METAR basis`、`official report age`、`path_pullback`、`fade_reheat_120m` 会翻号。
- 对每个 OOF 日逐个删除一个历史训练日重拟合：26 个 date-X rows 的平均概率变化
  `2.11pp`，但单行最大变化的 p95 达 `20.28pp`。
- `11/26` 个 entry 在至少一个 leave-one-date-out 模型中改变是否高于成本；已有 signal
  中是 `10/22`，最差判定稳定率 `50%`。

结论是：系数向量表面稳定，但少数关键 entry 对某一天训练样本很敏感；9 个 market OOF
dates 不足以支撑精细 residual correction。

## 交易可靠性，不把 ROI 当概率确认

| cohort | signals/dates | wins | PnL | ROI | target-date 95% CI |
|---|---:|---:|---:|---:|---:|
| 全部 intent | 28 / 8 | 25 | +$15.67 | +14.33% | `[+5.14%,+21.82%]` |
| active post-source | 11 / 4 | 8 | +$4.34 | +12.16% | `[-12.57%,+35.09%]` |
| pre-source full ladder | 17 / 5 | 17 | +$11.33 | +15.38% | `[+11.33%,+17.41%]` |
| 每日最后一档 | 8 / 8 | 5 | +$9.18 | +58.04% | `[-20.91%,+126.96%]` |
| 每日较低档 | 20 / 8 | 20 | +$6.48 | +6.93% | `[+2.59%,+14.42%]` |

全体 25/28 看起来很高，主要因为同一天较低 X 档 `20/20` 天然容易 break。真正决定
方向风险的每日最后一档只有 `5/8`，三笔亏损全在 `fresh_runway + active post-source +
last rung`。大 edge 也没有更稳定：`>15pp` 为 `4/6`，active 子集仅 `1/3` 且 ROI
`-7.58%`。这些只是诊断，不转成新 edge/path threshold。

## 统一修复方向

下一版建议直接做结构升级，而不是继续补坏案例规则：

1. **训练 grain 对齐决策 grain**：primary objective 改为 post-source active book 的首次
   date-X decision，target_date 等权；checkpoint/state-entry 只作辅助 loss，不能再主导。
2. **盘口时钟显式入模**：pre-source stale residual 与 post-source executable residual
   分开建模/分头评分；旧盘口只能衡量 repricing lead，不能拿来证明 entry alpha。
3. **改成 joint ladder distribution**：直接预测 `Δmax={0,1,2,3+}`，让同一天各 X 的
   概率单调、互相一致，再推导 exact-bracket NO；把“每日最后一档”风险放回分布本身。
4. **结构性收缩 correction**：用层级/regularized residual 和 OOF calibration uncertainty
   控制小样本下的大 logit 推动；不是事后把 `>2 logits` 删掉。
5. **loss 体系继续扩充**：固定报告 checkpoint/transition/date-X 三 grain Brier、logloss、
   calibration、market-relative regret；expression 再单独报告 fee-adjusted utility 与
   active-book capacity。不能让 10 分钟重复行或低档 ladder 胜率替代真正的 entry 质量。

当前可继续资格：**weather probability artifact 可以继续 frozen forward；market
expression 只具备 zero-notional collector/research 资格，不具备 freeze/live 资格。**

## 执行证据

- production manifest：`status=warning`，但 canonical DB route `healthy`，repo 兼容入口与
  `/Volumes/jrs/pm_agents/runtime/weather.db` 为同 device/inode；本审计未读取 canonical
  live PnL。
- 审计脚本：`scripts/analysis/reheat_risk/research_helsinki_model_reliability_audit_v1.py`。
- 生成目录：`generated/helsinki_model_reliability_audit_v1/`，含 probability scores、
  calibration bins、target-date bootstrap、31-entry/362-checkpoint atlas、trade slices、
  coefficient stability 与 leave-one-date-out sensitivity。
- `2026-07-31+` final audit / clean forward：untouched。
- live change / real order / actual fill：`0 / 0 / 0`。
