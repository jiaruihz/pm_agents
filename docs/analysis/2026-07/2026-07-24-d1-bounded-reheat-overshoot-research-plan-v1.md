# D1 Bounded-Reheat / Overshoot Hazard 研究计划 v1

Status: `preregistered research work order / zero-notional / no live change`

## 1. 研究目标

下一轮不直接优化 `d1 YES mid>=0.80`，也不寻找新的 regime filter。目标固定为：

> 在完整 PIT market ladder 的同分母上，分别估计“是否到达 d1”和“到达 d1 后是否
> overshoot 到 d2+”，验证 compact physics residual 能否稳定打赢 market；只有两段
> hazard 合成的 `P(exact d1)` 同样胜出，才评估 D1 YES 的 fee-adjusted 交易表达。

研究对象是两个 sibling probability heads：

```text
h_reach = P(final reaches d1)
h_over  = P(final reaches d2+ | final reaches d1)

p_current   = 1 - h_reach
p_exact_d1  = h_reach * (1 - h_over)
p_d2plus    = h_reach * h_over
```

本研究优先裁决 `h_over`，最终交易候选仍先使用 direct D1 YES。D2 exact、D1 NO 和
D2+ basket 只作 expression diagnostic，不混入主策略收益。

## 2. 冻结分母、grain 与标签

### Probability universe

- grain：一个 `(city, target_date, decision_snapshot_ts_utc, current bracket)` PIT state；
- universe：所有 bounded current/d1 语义有效、决策时 settlement 尚未知的 states；
- probability score 不设 d1 price floor，不按 regime、城市、source 或事后结果筛选；
- market baseline 必须有同 snapshot 的 current、d1 与全部 d2+/tail ladder；
- 历史已知 2026-07-02..05 forecast fallback 污染窗口从 physics fit 排除并单列影响，
  不能静默混入或用旧 forecast 字段回填；
- settlement label 按 settlement-source native unit/lattice 生成，Celsius 使用
  half-up 边界；禁止把转换后的连续摄氏/华氏距离直接当市场档位距离。

### 三态 label

```text
stall_current   : final winning bracket = current
exact_d1        : final winning bracket = d1
overshoot_d2plus: final winning bracket ∈ d2+
```

无法确认 current/d1 adjacency、`X+` 语义或 native lattice 的行属于 data-invalid，
不属于模型筛除。final 低于已观测 running max 等矛盾行进入 lineage audit，不自动修标签。

### Market baseline

同 snapshot 将完整 ladder midpoint 映射并归一化为：

```text
p_mkt_current
p_mkt_exact_d1
p_mkt_d2plus

h_mkt_reach = p_mkt_exact_d1 + p_mkt_d2plus
h_mkt_over  = p_mkt_d2plus / (p_mkt_exact_d1 + p_mkt_d2plus)
```

概率层使用 midpoint；交易层另用 selected-side executable ask ladder、depth 和官方 fee。
两者不能混用。

## 3. 数据准备与 parity

第一步复用并重新校验现有三态资产：

- bounded labels：历史 13,545 state rows；
- physics-eligible：历史 11,132 rows / 45 dates（剔除已知污染）；
- complete-ladder：历史 6,333 rows；
-旧 expanding OOF paired denominator：3,938 rows / 18 dates。

这些数字只作本 work order 的起始审计目标；正式 run 必须先记录当前 source hash、DB build
time、日期覆盖和变化原因，不能假设文件未变化。

需要补齐的 forward/parity 字段：

- 完整 current/d1/d2+/tail midpoint 与 5/10-share ask ladder；
- forecast issue/run/first-seen/hash/age 与固定 `CITY_MODEL`；
- raw native observation、settlement native unit 与每道 lattice boundary distance；
- forecast peak clock、forecast curve 到 d1/d2 的 margin；
- strict-new-high timestamp，不允许 equal high 重置；
- solar remaining window；
-温度 slope 与 acceleration；
- dewpoint depression、wind/mixing、cloud/precip transition；
- source cadence、source-to-settlement basis 与 terminal false-cross 标记。

Atlanta 2026-07-17 必须作为 source negative control 单列，不能让快源 cross 直接成为
`reach d1` 或 `overshoot` 的 settlement label。

## 4. 特征组与物理假设

每次只增加一个 feature group，固定 rows 配对消融：

| 组 | 主要字段 | 物理问题 |
| --- | --- | --- |
| market prior | `h_mkt_reach`, `h_mkt_over` | 市场已经定价多少 |
| lattice geometry | current→d1、d1→d2 native distance，forecast ceiling margins | 离两道真实结算边界多远 |
| remaining heat | peak delta、solar minutes、forecast curve slope/integral | 还剩多少可用加热能量 |
| path dynamics | strict-new-high age、1h/3h slope、acceleration、plateau/pullback | 当前路径是在继续突破还是衰减 |
| atmosphere | dewpoint depression、wind/mixing、cloud、precip transition | 加热、蒸发和边界层混合是否支持 overshoot |
| source reliability | cadence、age、source basis、terminal false risk | 当前观测能否代表 settlement lattice |

regime 只用于报告切片或预注册的低维 interaction；不加入 categorical regime
笛卡尔积，不把单个坏例子翻译成 AND gate。

## 5. 模型打擂

所有 candidate 都以 market hazard 为 prior，不允许只用 weather-only 模型打败弱基准：

1. `M0 market_raw`：完整 ladder 原始三态概率；
2. `M1 market_calibrated`：只校准 market 的两段 hazard；
3. `M2 L2 logistic residual`：compact physics residual；
4. `M3 elastic-net logistic residual`：检查稀疏性和系数稳定性；
5. `M4 spline/GAM residual`：检查 remaining heat、lattice margin 的非线性 band；
6. `M5 shallow HGB residual`：只作非线性 challenger，深度/正则在 train 内嵌套选择；
7. `M6 city-family hierarchical/shrinkage challenger`：只在前述模型胜出后运行，禁止
   city one-hot 记忆 settlement。

主模型优先选择最简单、跨日期稳定的 specification，不按最高历史 ROI 选模型。

## 6. 训练与统计验证

- 至少 12 个 target dates warm-up；
- 逐 target date expanding OOF，测试日永不参与 fit、imputation、scaling 或调参；
- 每个 city-target_date 总训练权重为 1；
- 超参数只能在当时可见 train dates 内 nested selection；
- paired delta 按 city-day 等权、target-date block bootstrap；
- 同时报 front/back、leave-one-date-out、去掉最大两日贡献、城市/source/unit 切片；
- 本轮模型数和 feature ablation 数必须进入多重检验记录。

### Primary metrics

先看 conditional overshoot head：

```text
logloss(h_over) candidate - market
Brier(h_over) candidate - market
```

再看完整三态：

```text
multiclass logloss(p_current, p_exact_d1, p_d2plus)
multiclass Brier
```

最后单列：

```text
binary proper score for p_exact_d1
calibration slope/intercept and reliability bins
```

AUC 只作 rank 诊断，不作为 promotion 主指标。

## 7. 概率层晋升门

必须按顺序通过：

### Gate A：overshoot head

- `h_over` 相对 `h_mkt_over` 的 Brier 和 logloss delta 均 `<0`；
- 两项 target-date bootstrap 95% CI 上界都 `<0`；
- front/back 同方向，leave-one-date-out 不由单日翻转。

### Gate B：三态一致性

- 完整三态 logloss/Brier 不劣于 raw market；
- `p_exact_d1` 两项 proper score 都显著优于对应 market probability；
- reach head 与 overshoot head 的概率能合成且三态和为 1，禁止另训一个不一致的
  D1 selector probability。

若只通过 Gate A、未通过 Gate B：保留 overshoot risk telemetry，不交易 D1。

## 8. 交易表达测试

只有 Gate A+B 通过后才运行。

### Primary：D1 YES

```text
edge_d1 = p_exact_d1 - five_share_d1_yes_effective_cost
```

- direct bounded D1 YES；
- first positive-EV city-day signal；
- 5-share 与 10-share完整 ask ladder；
- 官方 fee；
- 不加 `mid>=0.80`、regime、faded 或 overshoot cutoff 作为新增 gate；
- old high-mid policy 只作 frozen baseline，不参与重新选阈值。

### Secondary diagnostics

- D1 NO：单列 stall 与 overshoot 的 PnL 贡献，不能称纯 overshoot；
- exact D2 YES：明确它仍是 exact landing，不代表 d2+；
- D2+ basket：报告腿数、ask sum、fee、全腿可成交率和 non-atomic risk；
- maker：必须建 fill/queue/adverse selection，不能把 future touch 当成交。

交易晋升要求 5-share fee-adjusted ROI 的 target-date CI 下界 `>0`，并在去掉最大两日、
最大两笔 tail loss后仍保持正方向。

## 9. Frozen forward

历史通过只允许启动 zero-notional forward：

```text
全部 eligible state
→ market/physics score
→ selected/blocked reason
→ 5/10-share executable ladder
→ settlement label
```

collector 必须保存全部 score，不只保存正 EV。模型 artifact、features、checkpoint、
hash 和 entry rule 冻结；forward 不调参数。

第一次正式裁决至少需要：

- 10 个独立 target dates；
- 30 个 settled D1 strategy signals；
- 完整 probability denominator 和 coverage report。

若 frozen forward 的 proper score 未胜 market，即使 selected ROI 为正也保持
`inconclusive`。任何 tiny-live 都需要另行用户确认和 deploy 流程，本 work order
不修改生产。

## 10. 双漏斗交付

### Signal funnel

```text
bounded PIT states
→ complete market-ladder states
→ h_reach / h_over scoreable
→ first positive-EV city-day D1 signal
```

### Evidence funnel

```text
PIT forecast/observation parity
→ fresh full ladder
→ 5/10-share executable expression
→ canonical settlement
→ shadow/order/fill（分层）
```

每层必须报告 rows、city-days、target dates 和 coverage gap。盘口或 settlement 缺失
不得包装成策略过滤。

## 11. 停止条件与结论分支

| 结果 | 结论 | 动作 |
| --- | --- | --- |
| 所有 residual 模型均不胜 market | `rejected_for_current_expression` | D1 保持 dormant，不再追加 regime/filter |
| overshoot 胜、exact D1 不胜 | `overshoot_telemetry_only` | 保留 h_over 风险分，不交易 |
| 历史 proper score 与 D1 YES ROI 都过门 | `shadow_candidate` | 启动 frozen zero-notional forward |
| historical 过门、forward 未过 | `inconclusive` | 不 live、不调 forward |
| historical + independent forward + execution 都过门 | `promotion_candidate` | 另行复盘并请求 tiny-live 决策，不自动部署 |

## 12. 预期产物

- `scripts/analysis/market_structure_edge/research_d1_bounded_reheat_overshoot_v2.py`
- data-integrity / feature-coverage JSON
- OOF state predictions
- paired model score table
- feature ablation and coefficient/shape stability
- D1 YES 5/10-share replay
- frozen forward artifact and zero-notional runner contract
- final Markdown report、registry 与 docs index 更新

本计划复用旧三态 hazard 的正确概率结构，但不复用其失败的 path/regime gate。
