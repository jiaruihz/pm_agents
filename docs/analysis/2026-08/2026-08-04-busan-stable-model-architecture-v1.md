# Busan 稳定概率模型架构

## 结论

可以做到，而且不应继续沿 `v9 → v10 → v11 → ...` 的方式推进。Busan 模型身份现固定为：

`busan_intraday_exact_no`

数字版本只允许出现在 state/schema contract；训练截止日、算法、正则参数和单次 hypothesis 改动只产生新的不可变 `run_id` 与 `artifact_id`，不产生新模型名字。现有 v9/v10/诊断 v11 已全部映射为同一模型下的历史 experiments，均未晋升。

当前只完成研究架构与训练合同，**没有部署概率 adapter、没有改变 CrossNO 或 WCIR 生产行为**；Busan 继续是 coverage-only。

## 一、完整概率表达

模型不再用一个 residual 同时解释“快源是否可信”和“后面还会不会升温”，而按信息到达顺序分成两个 head：

```text
p_confirm = P(next routine 确认已经发生的 AMOS 跨档 | source→routine basis state)

p_reheat = P(next routine 未确认后，最终仍离开当前 exact rung
             | remaining heat + path morphology + atmosphere)

pending:
P(final exact current rung NO)
  = p_confirm + (1 - p_confirm) × p_reheat

routine 已确认跨档:
P(NO) = 1

routine 已明确未确认:
P(NO) = p_reheat
```

这解决的是结构问题，不是今天这个 case 的局部修补：

- 8月4日：routine 未确认后直接切到 conditional reheat head，不再靠一个很小的线性系数慢慢下修。
- 7月20日：虽然同样未确认，但上午仍有 heating runway，`p_reheat` 可以保持较高。
- 8月1日 terminal false cross：AMOS persistence 只能提高 `p_confirm`，不能直接把最终 settlement NO 推到接近1。

## 二、固定 feature ontology

19个连续状态变量预先归入4个物理机制，不允许 case 自己发明 feature 或 hard threshold：

| head | mechanism | features | 数量 |
|---|---|---|---:|
| next routine confirmation | source→settlement basis | cross margin、peak margin、peak giveback、current cross retained、persistence、observation count、runway agreement、minutes to report | 8 |
| conditional reheat | remaining heat | hours to/post forecast peak、forecast ceiling margin | 3 |
| conditional reheat | path morphology | 15m/60m slope、official peak age | 3 |
| conditional reheat | atmospheric suppression | humidity、dewpoint depression、cloud、precipitation、wind | 5 |

方向约束写在 feature contract 中。例如 source cross margin 对 confirmation 单调增加，source peak giveback 单调降低；remaining heat 与 forecast ceiling 对 reheat 单调增加，post-peak age、cloud 和 precipitation 单调降低。wind 保持 unconstrained，避免强行假设海风方向。

## 三、case 应该怎样进入系统

```text
坏 case
  -> failure taxonomy
  -> PIT regression case（只写预期关系）
  -> 物理 mechanism hypothesis
  -> 固定 ontology 内的一次 experiment run
  -> 全 checkpoint / transition / state-entry 同分母 OOF
  -> frozen forward + market baseline
  -> 通过后生成新 artifact；否则保留失败 run
```

case 永远不能直接：

- 新增日期/城市命名的 feature；
- 新增只挡住该 case 的 threshold；
- 改 evaluation denominator；
- 直接成为 live gate。

首批 regression library 已冻结3类：8/1 terminal false cross、7/20 late reheat、8/4 late fade。它们只用于验证排序和状态切换，不进入训练标签或模型选择分母。

## 四、训练标签合同

训练 grain 分为**固定 anchor rung 的 cross episode transition**和**同一 current-rung 的15-minute pending state**；首次触发事件的总体确认率不能再拿去给触发后的连续 checkpoint 定价，也不能把“当前 routine rung 已经变化”的 row 混成同一目标。

每个训练 example 同时保存：

- `anchor_rung` 与 PIT checkpoint；
- `label_next_routine_confirms`；
- 仅在未确认分支定义的 `label_late_break_given_nonconfirmation`；
- `label_final_exact_anchor_no`；
- PIT provenance 与决策时钟。

代码已强制以下一致性：next routine 已确认时 conditional reheat label 必须为空且 final anchor-NO 必须为1；未确认时 final anchor-NO 必须严格等于 conditional late-break label。这样不会再把 next-METAR label 与 settlement label 混训。

## 五、experiment 晋升合同

一次实验只能改变一个物理 mechanism，并同时满足：

1. hypothesis 事前登记且所有 feature 属于固定 ontology；
2. rows、labels、quotes 完全相同；
3. development 至少20个独立 target dates 且 logloss 改善；
4. frozen 至少10个独立 dates 且不退化；
5. 相对同 rows market 的 target-date bootstrap CI 上界小于0；
6. regression cases 全部通过。

这是“进入 frozen artifact”的门，不是 live 授权。shadow/live 仍走独立证据与部署合同。

上一轮 giveback interaction 自动被拒绝：development仅7天、相对基线 logloss `+0.00072`、frozen仅1天、相对market CI `[-0.2211,+0.1399]` 跨0。它保留为失败 experiment，不再衍生 v12。

## 六、落地产物

- 稳定模型与两阶段概率合同：`src/strategies/weather_city_probability_shadow/busan_model.py`
- 通用 anti-patch governance：`weather_model_evaluation/model_governance.py`
- 架构 manifest：`docs/analysis/2026-08/generated/busan_stable_model_architecture/architecture_manifest.json`
- case regression library：`docs/analysis/2026-08/2026-08-04-busan-model-regression-cases.csv`
- 可复跑 audit：`scripts/analysis/reheat_risk/audit_busan_stable_model_architecture.py`
- tests：`tests/research_tests/test_busan_stable_model_architecture.py`

下一训练阶段只做一件事：按这个合同重建 anchored cross-episode transition table，然后分别训练 confirmation 与 conditional reheat 两个 head；不会从8月4日继续手调单一 residual。
