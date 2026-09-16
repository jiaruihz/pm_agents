# Core Carry transition-confirmation challenger v1

**结论：`rejected`；没有修改 live。** 这是单一、预注册的 Core-offset residual，不是新增 gate。

## 固定口径

- 分母：1,349 checkpoints、801 个 city-date-bracket state entries、36 城、31 个 target dates（2026-06-02–2026-07-08）。
- 开发窗：15 个 expanding OOF dates；历史 forward：8 dates（2026-06-26–2026-07-08）。
- 训练权重：target_date 等权 → date 内 state entry 等权 → state 内 checkpoint 等权；主评测同样按 target_date 等权。
- 模型：frozen Core logit offset + 10 个连续物理/语义特征 + L2=0.5；K=1，无 forward 选型。
- market 并未缺席：frozen Core 本身以 market logit 为 prior；本版只检验新增天气语义是否能在同一 market anchor 上提供增量，避免再拟合第二个盘口系数掩盖物理特征效果。

## 加入的表达

- settlement-native 上沿距离与 forecast 越档 margin；
- fresh high、dip→rebound、forecast-exit 与短时 pullback 冲突；
- 剩余 daylight/solar、forecast peak clock、forecast/observed reheat；
- plateau confirmation；
- Core 原有 wind logit boost × transition-risk，用连续 residual 学习是否应减弱无条件风贡献。

## 历史 forward 结果（主口径：date-equal state entry）

| 模型 | Brier | Logloss | 平均概率 | 实际 hold rate |
|---|---:|---:|---:|---:|
| challenger | 0.075179 | 0.266087 | 0.918 | 0.913 |
| frozen Core | 0.075182 | 0.265677 | 0.913 | 0.913 |
| same-row market | 0.076743 | 0.278244 | 0.925 | 0.913 |

- Challenger − Core Brier：-0.000003（95% CI [-0.001151, +0.001128]）
- Challenger − Core logloss：+0.000410（95% CI [-0.003645, +0.004540]）
- Challenger − market Brier：-0.001563（95% CI [-0.007297, +0.003580]）
- Challenger − market logloss：-0.012157（95% CI [-0.033075, +0.007545]）

开发窗也没有改善：challenger Brier/logloss 为 `0.065706/0.238535`，
Core 为 `0.065311/0.237705`。因此不是只在最后 8 天偶然翻负。

## 学到的 residual 是否符合物理语义

wind boost × transition risk 的系数为负（`-0.0041`），方向上确实会轻微
抑制“仅凭强风抬高 hold”的表达；forecast 越档 margin 与 peak-ahead heat
也是负向。但整体修正非常小，而且 dip→rebound 为正、plateau confirmation
为负，和预期风险方向相反。它说明这些由旧 ledger 拼出的交互 proxy 高度
相关、缺少真实转折时钟，模型没有稳定分离“维持混合”与“暖/冷输送”。

所以这次失败不是证明 cloud/rain、dewpoint 或风向语义没用；它证明在没有
first-seen transition、dewpoint trend、wind direction × terrain 原始字段时，
不能用现有摘要变量替代后就声称完成了特征升级。

## 结论边界

- 这次修正了旧 challenger 把同一 city-day 的不同 bracket 合并的问题；主 grain 现在是 city × target_date × current_bracket。
- cloud/rain 首见转折、dewpoint trend、wind direction × terrain 历史字段在固定 parent 中不存在，本版明确不伪造。
- 历史 forward 即使通过，也只能成为新 frozen-forward challenger；需要用 2026-08-07 之后新样本验证，不能直接替换 Core 或改 live。

## 复现

```bash
.venv/bin/python scripts/analysis/reheat_risk/research_current_yes_core_carry_taker_10share_v1.py --transition-confirmation-challenger --challenger-output-dir /tmp/core-carry-transition-confirmation-v1
```

大文件产物：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/current_yes_core_carry_transition_confirmation_challenger_v1/historical_parent_1349_state_entry_offset_ridge_20260807`
