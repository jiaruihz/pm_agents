# Helsinki market expression v3 structural repair

Status: `historical FAIL / 2026-07-31 regression FAIL / keep existing zero-notional shadow / no live change`

## 结论

这次不能换 shadow 模型。

把原来分开的 forecast ceiling 与 current innovation 合成
`bias-corrected future peak margin`，再加入 `time since bracket transition`，
在历史 date-X 上有很小的点估改善，但没有解决真正的 active-book 问题；在
2026-07-31 的 26-NO 反例上还把错误概率推得更高。因此 3 个新候选全部不合格，
现有 incumbent/challenger 保持不动，不新增第三个 shadow model。

这说明 26-NO 错误不是“少两个特征”这么简单。更核心的问题是 expression residual
只有 4 个 active target dates，模型在“刚跨入当天最后一档”时会从历史高 break base
rate 学到过强的正修正。下一版应扩大 active date-X 分母并约束 market correction，
不能围绕 7/31 再加阈值。

## 固定设计

- 训练/选择只读 2026-07-15..29 的既有 PIT market denominator；前 5 日只训练，
  expanding OOF 为 2026-07-20..29。
- A0：现有 `p_v2_compact_r16`。
- A1：移除分开的 raw ceiling/innovation，改为二者相加后的单一 bias-corrected margin。
- A2：A1 + `log1p(minutes_since_bracket_transition)`。
- A3：与 A2 相同特征的 shallow HGB。
- checkpoint/state-entry/date-X 各 1/3、日期等权；fold 内 median/standardization。
- 选择只看 Brier/logloss：integrated 与 date-X 不差于 v2，同时 active post-source
  date-X 不差于 market。ROI 不参与选模。
- 2026-07-31 在历史选模完成后才作为 regression diagnostic：保留 21–25 的原始信号，
  且不能再触发错误 26-NO。它不进入训练或阈值选择。

## 数据和漏斗

Signal funnel：

- 2,115 个 weather checkpoints；
- 598 个去重 PIT market rows / 14 target dates；
- 362 个 expanding OOF checkpoints / 9 dates；
- 142 个 state entries、31 个 date-X entries；
- active post-source date-X 只有 12 rows / 4 dates；
- 3 个结构候选，historical pass 0，7/31 regression pass 0，shadow eligible 0。

Evidence funnel：

- full-ladder 有 534 rows，active collector 匹配 231 rows；合并去重后 598 rows；
- 7/31 full-day diagnostic 有 39 个 two-sided scored checkpoints；one-sided rows仍保留在
  原 shadow coverage denominator，但 market-offset model 无 midpoint，不能用于本次概率回归；
- actual fills=0，orders submitted=0。

## 同分母概率结果

| grain / evidence | model | rows / dates | accuracy | Brier | logloss |
|---|---|---:|---:|---:|---:|
| checkpoint / all | market | 362 / 9 | 91.99% | 0.07641 | 0.24874 |
| checkpoint / all | v2 compact r16 | 362 / 9 | 94.48% | **0.05625** | 0.20072 |
| checkpoint / all | A2 structural r16 | 362 / 9 | 95.03% | 0.05711 | 0.20321 |
| date-X / all | market | 31 / 9 | 83.87% | 0.08180 | 0.26162 |
| date-X / all | v2 compact r16 | 31 / 9 | 90.32% | 0.06669 | 0.21921 |
| date-X / all | A2 structural r16 | 31 / 9 | **96.77%** | **0.06389** | **0.21360** |
| active date-X | market | 12 / 4 | 83.33% | **0.09167** | **0.29274** |
| active date-X | v2 compact r16 | 12 / 4 | 83.33% | 0.10888 | 0.33639 |
| active date-X | A2 structural r16 | 12 / 4 | 91.67% | 0.10655 | 0.33034 |

A2 对 v2 的 date-X Brier/logloss delta 为 `-0.00281/-0.00561`，但 target-date
bootstrap CI 分别 `[-0.00793,+0.00211]`、`[-0.01823,+0.00736]`，均跨 0。
在 primary active date-X 上，A2 仍比 market 差 `+0.01488 Brier / +0.03759 logloss`，
CI 也跨 0。A3 HGB 的 date-X Brier/logloss 为 `0.08323/0.26510`，明显退化。

Accuracy 的切片也不能掩盖 proper loss：A2 在 fresh-runway 为 95.10%，pullback
96.63%，plateau 100%，fade 90%；但 fade Brier/logloss `0.15303/0.44150` 仍差于
market 的 `0.12873/0.37350`。模型对少数错误概率给得太重，正是需要修的结构问题。

## 7/31 异常日回归

13:01 Helsinki time 后约 1.9 分钟的首个 26-NO checkpoint：market midpoint 32%，
5-share fee-adjusted cost 38.17%。

| model | P(26-NO) | 是否仍触发 |
|---|---:|---:|
| v2 compact r16 | 46.73% | 是 |
| A1 corrected-margin r16 | 50.30% | 是 |
| A2 corrected-margin + transition r16 | 52.48% | 是 |
| A3 shallow HGB | 73.38% | 是 |

A1/A2 仍保留了 21–25 的信号，但同样保留错误 26-NO；A3 连 21/22 尘埃信号都不再
产生，却仍产生 26-NO。结构修复方向没有通过反例回归，不能发布到 shadow。

## 交易映射仅作参考

不加额外 price/edge threshold、固定 first-positive、5 shares：v2 为 19 笔、16胜3负，
ROI 26.86%；A2 为 21 笔、18胜3负，ROI 20.27%。真正 active 的 A2 只有 11 笔/4日，
8胜3负、ROI 12.35%，target-date bootstrap CI `[-13.05%,+36.11%]`。新增信号扩大了
投入，但没有减少错误或提高 ROI，所以不能用总胜率包装成升级。

## 动作与下一步资格

- 已生成三个研究 artifact，但全部标记 `shadow_eligible=false`；它们只用于复现，
  不进入 config。
- 未修改 Helsinki shadow config，未重启 shadow，未修改 live，未下单。
- 当前具备继续研究资格，但不具备换 shadow / freeze 新表达的资格。
- 下一阶段目标应改为：补足 active post-source date-X 的独立 target dates，并研究
  受约束的 market correction / hierarchical shrinkage；weather-head 与 market baseline
  均保留同 rows。7/31 只作为固定 regression，不再围绕它调阈值。

Reproducible entrypoint:
`scripts/analysis/reheat_risk/research_helsinki_market_expression_v3_structural_repair.py`

Historical artifacts are recoverable through
`/Volumes/jrs-archive/pm_agents/research/artifact_store/manifests/cleanup-20260813-generated-unambiguous-batch1.json`.
New runs require a stable `--run-id` and write an immutable directory beneath
the configured JRS research artifact root.
