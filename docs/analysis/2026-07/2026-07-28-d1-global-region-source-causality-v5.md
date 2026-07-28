# D1 distance-2 NO：全球区域 × forecast source 成因实验 v5

## 结论与动作

**上一版关于参与者偏好、模板报价的说法没有直接证据，本报告撤回其策略解释权。实验能确认的是：source availability 不是原因；source accuracy 解释部分地域差异，但欧洲的 market residual 在控制标准化tail safety 与成本后仍存在。具体剩余原因尚不可识别。**

- 修正 v4：把“欧洲档位相对 forecast/error distribution 更安全”称为主因过强。它能解释 outcome risk，却没有形成跨区域单调 alpha，因而只保留为风险特征，不作为已识别的错价成因。
- 动作：保持 research / frozen shadow；不根据 Europe、Asia 或任何 region/source 建 hard gate，不改 live。
- 下一步只注册可检验变量：source-specific standardized distance、market calibration residual、settlement-station basis、tail side、lead time；不注册“注意力/偏好/定价者”标签。

## Target 与数据快照

```text
在固定 D1 distance=2 paired opportunity 分母上，检验区域收益是否可由
source coverage/quality、标准化 tail distance、成本与 market calibration
解释，并比较 all5/single-source 与同 rows market/mechanical baseline。
```

- 数据：immutable v2 artifacts；2026-06-17..2026-07-07，1,190 baskets / 20 target dates / 43 cities / 2,380 candidate rows。
- grain=`research replay basket/expression`；settled=100%；unsettled=0；missing bracket=0；actual fill=0。
- 每个 candidate 都有 ECMWF IFS/AIFS、GFS、ICON、JMA 五源概率；所有区域 source coverage=100%。

## 实验 1：所有区域，而非欧美二分

| region | baskets/dates/cities | actual PNO | market PNO | cost | all5 ROI [CI] | mechanical | model-mechanical |
|---|---:|---:|---:|---:|---:|---:|---:|
| EU | 295/19/10 | 0.956 | 0.893 | 0.899 | +6.35% [+2.61%, +9.84%] | +3.44% | +2.91% |
| AS | 354/20/13 | 0.898 | 0.886 | 0.893 | +0.55% [-4.39%, +5.21%] | -1.01% | +1.56% |
| ME | 72/19/3 | 0.903 | 0.843 | 0.851 | +6.14% [-2.99%, +14.26%] | +0.28% | +5.86% |
| OC | 23/13/1 | 1.000 | 0.905 | 0.911 | +9.78% [+2.58%, +20.00%] | +6.05% | +3.73% |
| SA | 105/17/4 | 0.924 | 0.942 | 0.948 | -2.54% [-7.89%, +2.42%] | -1.68% | -0.86% |
| AF | 34/18/1 | 0.882 | 0.928 | 0.936 | -5.71% [-23.19%, +7.49%] | -1.40% | -4.32% |
| US | 307/16/11 | 0.912 | 0.923 | 0.931 | -2.00% [-5.92%, +1.77%] | -1.99% | -0.01% |

- **EU 是唯一多城市大样本中 absolute ROI CI 明确为正的区域。**
- **Asia 并没有复现 Europe**：all5 ROI +0.55%，CI [-4.39%, +5.21%]；market residual 只有 +0.013。
- ME 点估 +6.14%，但只有 72 baskets/3 cities，CI 跨 0；OC 是 Wellington 单城 23 baskets，不能称区域机制。
- SA/AF/US 均为负点估；因此不是“所有 C 市场都好”。

## 实验 2：是否由 source 分布或某个更好模型导致

### 2.1 Coverage

五个 source 在每个区域的 candidate coverage 都是 100%，所以不存在“欧洲恰好多了 ICON、亚洲少了 ECMWF”这种 source mix 差异。

### 2.2 训练期 source MAE

| region | best source / MAE(F) | all5 city-mean MAE(F) |
|---|---:|---:|
| EU | ICON / 1.25 | 2.33 |
| AS | ICON / 2.36 | 2.83 |
| ME | ECMWF AIFS / 2.01 | 3.25 |
| OC | ECMWF IFS / 1.49 | 1.90 |
| SA | ECMWF IFS / 2.34 | 2.78 |
| AF | ECMWF IFS / 1.68 | 2.43 |
| US | ICON / 3.27 | 3.94 |

- Europe 的 forecast 确实更准，尤其 ICON/GFS；US 五源误差整体更大。这是 underlying tail risk 差异的一部分。
- 但 source quality 不能单独解释收益：ME 的平均 MAE 3.25F 仍有正 ROI 点估；SA MAE 2.78F 却为负。

### 2.3 同分母 single-source 交易与 proper score

| region | all5 ROI | five single-source ROI range | all5 Brier Δmarket | any source Brier beats market after BH? |
|---|---:|---:|---:|---:|
| EU | +6.35% | +3.28%..+6.20% | +0.00034 | NO |
| AS | +0.55% | -0.91%..+2.22% | +0.00134 | NO |
| ME | +6.14% | +1.50%..+5.37% | -0.00222 | NO |
| OC | +9.78% | +2.40%..+11.98% | -0.00302 | NO |
| SA | -2.54% | -4.73%..-1.48% | +0.00241 | NO |
| AF | -5.71% | -5.69%..+4.60% | +0.00374 | NO |
| US | -2.00% | -4.21%..-0.85% | +0.00728 | NO |

- Europe 五个 single-source trade policy 都为正，US 五个都为负；这说明结果不由某一个 source 独占。
- 但 Europe-all 的 all5 与每个 single source 在全 candidate proper score 上都没有打赢 market；全 42 个 region×source/all5 检验经 BH 后没有确认的 Brier 改善。
- 因此 source accuracy 可以解释“天气尾部本来有多危险”，尚不能证明 source probability 自身提供稳定 market alpha。

## 实验 3：标准化 tail safety 是否解释区域残差

| safety quintile | baskets | mean safety | actual PNO | market PNO | market residual | ROI |
|---|---:|---:|---:|---:|---:|---:|
| Q1_low | 238 | 0.42 | 0.828 | 0.772 | +0.055 | +5.53% |
| Q2 | 238 | 1.16 | 0.891 | 0.867 | +0.024 | +1.75% |
| Q3 | 238 | 1.76 | 0.937 | 0.911 | +0.026 | +2.11% |
| Q4 | 238 | 2.52 | 0.962 | 0.968 | -0.006 | -1.21% |
| Q5_high | 238 | 3.99 | 0.983 | 0.986 | -0.003 | -0.63% |

- safety score 对 actual P(NO) 呈清楚单调关系，说明它是有效风险变量；但 ROI/market residual 不单调，市场已定价掉高 safety 的大部分信息。
- 为避免 US=F-unit 完全共线，固定效应实验只在 C-unit 内进行，并以 Asia 为 reference；控制 safety quintile 与 cost quintile 后：
  - EU residual 仍比 Asia 高 +5.67%，target-date bootstrap CI [+1.53%, +9.78%]，p=0.0067。
- 改成每个 source 自己的 `distance / train MAE` 后，EU−Asia 系数范围仍为 +5.32%..+5.84%；五种定义的 95% CI 下界范围为 +1.33%..+1.66%。
- 所以 Europe 结果**不能被 source error distribution + entry cost 完全解释**。剩余项可能是未建模的 ladder geometry、city/station settlement basis、区域 weather regime 或定价过程；当前数据不能在这些机制之间识别。

## 已证伪 / 未证实 / 保留

- **已证伪：source availability/mix 是欧洲结果主因。**五源覆盖完全相同。
- **不支持：某一个 source 产生 Europe alpha。**所有单源同方向，proper score 均未确认胜 market。
- **部分支持：source accuracy 影响 underlying risk。**Europe MAE 更低、US 更高，但控制后 Europe residual 仍存在。
- **保留待验：settlement-station basis / ladder placement / regional weather regime。**需要新增可观测特征，不能用 region 名称代理。
- **未识别：参与者、注意力、高温偏好、模板化做市。**没有 order-flow/wallet evidence，禁止用于策略规则。

## Signal / evidence funnel、完整性与三门

- signal：1,338 raw full-ladder baskets → 1,298 paired distance2 → 1,190 all-five/market-covered → 1,190 selections。
- evidence：2,380 candidate rows，五源 finite=11,900/11,900；settlement/executable quote 完整；actual fill=0。
- 已覆盖：宽分母、region/source 同分母 A/B、proper score、fee-adjusted expression、target-date bootstrap、BH、多变量控制。
- 缺失：station-basis 连续特征、participant identity/order-flow、真实 fill/queue/capacity、新日期 frozen forward。
- significance=EU historical trade PASS；baseline=trade PASS / probability FAIL；forward=NA；conclusion=`shadow_candidate`，动作=保持 research/frozen shadow，不改 live。
